#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guard.py — защитные PreToolUse-хуки для команды агентов.

Механические инварианты: то, что нельзя доверять промпту, проверяется кодом.
Обоснование — principles/09-mechanical-invariants.md, классы риска — principles/03.

Вход:  JSON хука на stdin.
Выход: пусто = разрешить; JSON с permissionDecision=deny = заблокировать;
       JSON с permissionDecision=ask = спросить человека в окне Claude Code.
       Код возврата всегда 0 — решение передаётся через JSON, а не через exit 2.

Зависимости: только стандартная библиотека Python 3.8+.
Работает одинаково на Windows и POSIX — намеренно одна реализация вместо
двух шелловых, которые пришлось бы держать в синхроне.

Запуск:
    python guard.py --rules fs,git,sql,env,memory
    python guard.py                # то же самое: без --rules включены все
"""

import argparse
import fnmatch
import json
import posixpath
import re
import shlex
import sys
import tempfile

# Тексты для человека живут в каталоге, а не здесь: язык выбирается
# пользователем, и правило не должно зависеть от того, какой он выбрал.
try:
    from messages import msg, use_project
except ImportError:
    # Отказ, а не падение. Код 1 хук роняет, но вызов пропускает - защита
    # снимается тихо, и об этом никто не узнаёт. Код 2 блокирует, поэтому
    # неполная установка видна с первой же команды, как и обещает install.md.
    # Текст здесь на обоих языках: каталога сообщений как раз и нет.
    sys.stderr.write(
        "BLOCKED: messages.py is missing next to guard.py. "
        "Copy every hooks/*.py file, not guard.py alone.\n"
        "BLOCKED: рядом с guard.py нет messages.py. "
        "Копировать нужно все файлы hooks/*.py, а не один guard.py.\n"
    )
    sys.exit(2)

# Инструменты, которые пишут в файлы. Read сюда намеренно не входит.
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Из них те, что создают файл, а не правят существующий. Набор задан отдельно,
# чтобы матрица ниже не перечисляла инструменты поимённо: именно перечисление
# оставило NotebookEdit вне запретов и открыло ролям запись тетрадей куда угодно.
CREATE_TOOLS = {"Write", "NotebookEdit"}

# Цели, по которым rm -rf недопустим ни при каких обстоятельствах.
DANGEROUS_RM_TARGETS = {
    "/", "/*", "~", "~/", "~/*", ".", "./", "./*", "..", "../", "*",
}

# SQL проверяется только у известных клиентов БД. Иначе хук срабатывал бы на
# любом grep по слову DELETE и был бы отключён пользователем в первый же день.
DB_CLIENTS = {
    "psql", "mysql", "mariadb", "sqlite3", "clickhouse-client",
    "mongosh", "pgcli", "mycli", "cockroach",
}

# Обёртки, которые стоят перед настоящей командой и не меняют её сути.
COMMAND_WRAPPERS = {"sudo", "env", "time", "nohup", "nice", "doas", "command", "xargs"}

# Флаги обёрток, забирающие значение отдельным токеном: sudo -u root rm ...
WRAPPER_FLAGS_WITH_VALUE = {"-u", "--user", "-g", "--group", "-C", "--chdir"}

# Оболочки, запускающие вложенную команду строкой: sh -c "rm -rf /".
SHELL_WRAPPERS = {"sh", "bash", "zsh", "dash", "ksh", "ash"}

# Операторы, разделяющие самостоятельные команды. Перенаправления (> и >>)
# сюда не входят намеренно: они нужны правилу env внутри сегмента.
# Обратная кавычка здесь потому, что `rm -rf /` — такой же запуск команды,
# как $(rm -rf /), только вторая форма попадала под скобки, а первая ни подо
# что: echo `rm -rf /` проходил мимо всех четырёх правил.
SEGMENT_SEPARATORS = {";", "|", "||", "&&", "&", "|&", ";;", "(", ")", "`"}

# То же множество для shlex. По умолчанию punctuation_chars=True даёт
# ();<>|& — обратной кавычки там нет, и без неё она приклеивалась к слову.
PUNCTUATION_CHARS = "();<>|&`"

# Глубина раскрытия вложенных sh -c. Дальше начинается не ошибка агента,
# а намеренная обфускация, которую эти хуки закрывать не берутся.
MAX_NESTING = 3

# Примеры конфигов коммитятся намеренно и секретов не содержат.
ENV_ALLOWED = {".env.example", ".env.sample", ".env.template", ".env.dist"}

# Матрица из checklists/permission-checklist.md, выраженная кодом.
# Для каждой роли: какие инструменты записи ей НЕ положены и куда всё же можно.
# Ключ роли — значение agent_type в JSON хука.
MEMORY_MATRIX = {
    "pm-orchestrator": WRITE_TOOLS,
    "code-reviewer": WRITE_TOOLS,
    # Роли-аналитики: смотрят и ставят диагноз, но не правят. Память им нужна,
    # поэтому поле memory включено — и поэтому же нужен этот возврат ограничений.
    "security-reviewer": WRITE_TOOLS,
    "investigator": WRITE_TOOLS,
    "design-reviewer": WRITE_TOOLS,
    "scope-challenger": WRITE_TOOLS,
    # Финансовые роли: тоже смотрят и считают, ничего не правят.
    "finops-engineer": WRITE_TOOLS,
    "unit-economics-analyst": WRITE_TOOLS,
    "investment-analyst": WRITE_TOOLS,
    "vendor-auditor": WRITE_TOOLS,
    # browser-tester правит тест-артефакты: в зоне E2E ему открыты все
    # инструменты записи, за её пределами — ни один.
    "browser-tester": WRITE_TOOLS,
    # devops и local-sysops правят существующие файлы, но не создают новые.
    # NotebookEdit здесь вместе с Write не по формальности: тетрадь — такой же
    # файл, и роль, которой нельзя создать .py, не должна создавать .ipynb.
    # Перечисление инструментов поимённо уже один раз оставило дыру.
    "devops": CREATE_TOOLS,
    "local-sysops": CREATE_TOOLS,
    # qa-tester пишет законно - Edit и Write у неё в tools, - но только тесты.
    # Граница «только тесты» жила в одном тексте промпта, и роль дважды её
    # переходила: при мутационной проверке правила продуктовый config.py и,
    # упёршись в лимит ходов, оставляла его сломанным (задача #2). Как и у
    # browser-tester, инструменты записи отняты целиком, а зона их возвращает.
    "qa-tester": WRITE_TOOLS,
}

# Зона записи browser-tester: каталог E2E, как бы он ни лежал — `e2e/` в корне
# или `tests/e2e/`. Сравнение посегментное, а не по подстроке, и это важно:
# подстрока «/tests/» пускала роль в `backend/tests/conftest.py`, то есть в
# юнит-тесты, которыми владеет qa-tester, и при этом не пускала в `e2e/specs/` —
# раскладку, где E2E лежит в корне. Правило было неверно в обе стороны сразу.
#
# Каталог с другим именем (`cypress/`, `playwright/`) сюда не попадает
# намеренно: лучше заблокировать и объяснить, чем угадывать. Это константа
# под проект, как MEMORY_MATRIX и DB_CLIENTS.
BROWSER_TESTER_WRITE_SEGMENTS = ("e2e",)

# Зона записи qa-tester - тестовые файлы, где бы они ни лежали. Одного каталога
# tests/ мало: в JS и TS тесты часто стоят рядом с кодом (Button.test.tsx,
# __tests__/), и хук, не пускающий туда QA, отключили бы в первый же день.
# Поэтому признака два: каталог по сегменту пути и имя тестового файла.
# Константы под проект, как BROWSER_TESTER_WRITE_SEGMENTS.
QA_TESTER_WRITE_SEGMENTS = ("tests", "test", "__tests__", "spec")
QA_TESTER_FILE_PATTERNS = (
    "test_*.py", "*_test.py", "conftest.py", "*_test.go", "*.test.*", "*.spec.*",
)


def in_own_memory(path, agent):
    """
    Своя папка памяти роли — при любом значении поля `memory`.

    Вариант `local` кладёт её в `.claude/agent-memory-local/`, и хук, знавший
    только `agent-memory`, блокировал роль в её собственной памяти. Значение
    описано в `principles/06-memory-hygiene.md`, то есть пользователь имел
    полное право его выбрать.
    """
    return any(
        "/.claude/{}/{}/".format(directory, agent) in path
        for directory in ("agent-memory", "agent-memory-local")
    )


def in_browser_tester_zone(path):
    """True, если путь лежит внутри каталога E2E-тестов."""
    return any(part.lower() in BROWSER_TESTER_WRITE_SEGMENTS
               for part in path.split("/"))


def comparable(path):
    """
    Путь для сравнения каталогов: прямые слеши, нижний регистр, диск без двоеточия.

    C:/Temp и /c/Temp - один каталог: первое пишет Claude Code, второе Git Bash.
    Без приведения временный каталог узнавался бы через раз.
    """
    path = "/" + posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    return re.sub(r"^/([a-z]):/", r"/\1/", path.lower())


def in_temp_dir(path):
    """True, если путь внутри системного временного каталога."""
    here = comparable(path)
    roots = {comparable(tempfile.gettempdir()), "/tmp"}
    return any(here == root or here.startswith(root.rstrip("/") + "/") for root in roots)


def in_qa_tester_zone(path):
    """
    True, если qa-tester может сюда писать: тестовый файл или временный каталог.

    Временный каталог в зоне ради безопасной мутационной проверки: копия модуля
    ломается там, и обрыв на лимите ходов следов в рабочей копии не оставляет.
    Не пусти хук роль во временный каталог - безопасный способ стал бы
    невозможен, и остался бы только опасный.

    Каталог E2E в зону не входит, даже внутри tests/ и с именем *.spec.*:
    у каталога ровно один хозяин, и это browser-tester.
    """
    if in_temp_dir(path):
        return True
    if in_browser_tester_zone(path):
        return False
    parts = [part.lower() for part in path.split("/") if part]
    if not parts:
        return False
    if any(part in QA_TESTER_WRITE_SEGMENTS for part in parts[:-1]):
        return True
    return any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in QA_TESTER_FILE_PATTERNS)


# Роли, у которых помимо папки памяти есть своя зона записи по путям.
ROLE_WRITE_ZONES = {
    "browser-tester": in_browser_tester_zone,
    "qa-tester": in_qa_tester_zone,
}


def in_role_zone(path, agent):
    """True, если путь внутри собственной зоны записи роли."""
    zone = ROLE_WRITE_ZONES.get(agent)
    return bool(zone and zone(path))


def role_zone_hint(agent):
    """Хвост строки «Разрешено: ...» в блокировке - описание зоны роли."""
    if agent == "browser-tester":
        return msg("memory.extra_browser_tester")
    if agent == "qa-tester":
        return msg("memory.extra_qa_tester",
                   segments=", ".join(QA_TESTER_WRITE_SEGMENTS),
                   patterns=", ".join(QA_TESTER_FILE_PATTERNS))
    return ""


def deny(reason, ask=True):
    """
    Печатает решение об отказе и завершает работу.

    К блокировкам правил дописывается общий хвост: спроси пользователя, а не
    ищи обход. Место выбрано намеренно — это единственная точка, через которую
    проходят все семнадцать сообщений, и агент читает её в тот момент, когда
    упёрся. Ошибкам конфигурации хвост не нужен: там чинят настройку, а не
    решают, как поступить с задачей.
    """
    if ask:
        reason += msg("guard.ask_do_not_work_around")
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    # ensure_ascii=True: кириллица уезжает в \uXXXX и не зависит от кодировки консоли.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.exit(0)


# Просьбы подтвердить, собранные за одну проверку. Отказ завершает работу сразу,
# а подтверждение откладывается до конца разбора: иначе в `git push --force &&
# rm -rf /` первое правило открыло бы окно, и после «да» вторая команда
# выполнилась бы непроверенной. Так отказ всегда сильнее подтверждения.
PENDING_CONFIRMATIONS = []


def confirm(reason):
    """
    Просит человека подтвердить вызов в окне Claude Code вместо отказа.

    Для операций, у которых есть законные случаи: force-push, сброс рабочей
    копии, DROP. Отказ ничем не снимается, и агенту, упёршись, оставалось
    только сказать «выполните сами». Окно ставит решение туда, где оно и должно
    быть, причём и в auto mode: классификатор не может одобрить такой вызов
    молча, а агент не может подделать нажатие.
    """
    PENDING_CONFIRMATIONS.append(reason)


def emit_confirmation():
    """Открывает одно окно на все накопленные просьбы, если отказа не случилось."""
    if not PENDING_CONFIRMATIONS:
        return
    # Одно и то же правило в нескольких сегментах команды - один текст, а не повтор.
    text = "\n\n".join(dict.fromkeys(PENDING_CONFIRMATIONS))
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            # Этот текст Claude Code показывает только человеку в окне.
            "permissionDecisionReason": text,
            # А этот - только агенту, рядом с результатом вызова.
            "additionalContext": msg("guard.confirm_context", details=text),
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))


HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def heredoc_delimiters(line):
    """
    Ограничители heredoc, объявленные в строке, — и только вне кавычек.

    Внутри кавычек `<<EOF` heredoc'ом не является, а если считать его таковым,
    появится способ спрятать следующую строку от проверки.
    """
    found = []
    quote = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
        elif char in "'\"":
            quote = char
            index += 1
        elif char == "<" and line.startswith("<<", index):
            match = HEREDOC_START.match(line, index)
            if not match:
                index += 2
                continue
            found.append(match.group(2))
            index = match.end()
        else:
            index += 1
    return found


def strip_heredoc_bodies(command):
    """
    Убирает тела heredoc: это данные, а не команды, выполнены они не будут.

    Разбирать их как команды — значит блокировать запись любого текста
    с примерами: документации, теста, скрипта. Хук, мешающий обычной работе,
    отключают в первый же день.

    Сама строка с перенаправлением остаётся, поэтому `cat > .env <<EOF`
    по-прежнему виден правилу env: решает перенаправление, а не текст внутри.
    """
    if "<<" not in command:
        return command
    lines = command.split("\n")
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for delimiter in heredoc_delimiters(line):
            while index < len(lines) and lines[index].strip() != delimiter:
                index += 1
            index += 1   # строка-ограничитель тоже не команда
    return "\n".join(kept)


def newlines_to_separators(command):
    """
    Перевод строки вне кавычек — такой же разделитель команд, как точка с запятой.

    Без этого `echo ok\\nrm -rf /` выглядит одной командой echo, и все правила
    молча пропускают вторую строку.
    """
    out = []
    quote = None
    for char in command:
        if quote:
            out.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            out.append(char)
        else:
            out.append(";" if char in "\n\r" else char)
    return "".join(out)


def lex(command):
    """
    Разбирает команду на токены, где операторы оболочки — отдельные токены,
    а содержимое кавычек не режется.

    punctuation_chars=True — то, ради чего берётся shlex вместо регулярок:
    он не спутает разделитель команд с тем же символом внутри строки.
    """
    lexer = shlex.shlex(newlines_to_separators(command), posix=True,
                        punctuation_chars=PUNCTUATION_CHARS)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        # Незакрытая кавычка — разбираем грубо, лучше чем не проверить вовсе.
        return command.split()


def split_segments(command):
    """Режет составную команду на сегменты-списки токенов по операторам оболочки."""
    segments, current = [], []
    for token in lex(command):
        if token in SEGMENT_SEPARATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def strip_wrappers(tokens):
    """
    Отбрасывает sudo/env и подобное, чтобы добраться до настоящей команды.

    Снимать нужно не только имя обёртки, но и её флаги с присваиваниями:
    без этого `sudo -u root rm -rf /` и `env FOO=bar rm -rf /` проходят мимо
    всех правил, потому что первым токеном оказывается не rm.
    """
    i = 0
    saw_wrapper = False
    while i < len(tokens):
        token = tokens[i]
        if basename(token) in COMMAND_WRAPPERS:
            saw_wrapper = True
            i += 1
            continue
        # LC_ALL=C rm -rf / — присваивание перед командой работает и без env,
        # поэтому снимается всегда, а не только после обёртки.
        if "=" in token and not token.startswith("=") and not token.startswith("-"):
            i += 1
            continue
        if saw_wrapper and token.startswith("-"):
            i += 1
            if token in WRAPPER_FLAGS_WITH_VALUE and i < len(tokens):
                i += 1
            continue
        break
    return tokens[i:]


# Присваивание целиком: NAME=value. Значение может быть пустым - это важный
# случай: `D=; rm -rf "$D/"` схлопывается в корень.
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)

# Обращение к переменной: $NAME или ${NAME}.
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def expand_assigned(tokens, assigned):
    """
    Подставляет переменные, заданные раньше в этой же команде.

    Их значение хук видит дословно, в отличие от переменных окружения. Без
    подстановки `SC=<временный каталог>; cp x "$SC/y"` читалось как запись в
    непонятный "$SC/y" - и роль, которой временный каталог открыт, получала
    отказ. Незнакомые переменные остаются как есть и проверяются строго.
    """
    if not assigned:
        return tokens

    def substitute(match):
        name = match.group(1) or match.group(2)
        return assigned[name] if name in assigned else match.group(0)

    return [VARIABLE.sub(substitute, token) for token in tokens]


def remember_assignments(tokens, assigned):
    """
    True, если сегмент состоит из одних присваиваний; их значения запоминаются.

    Только отдельный сегмент: в `X=1 cmd` переменная живёт лишь для cmd и
    следующим командам не видна. Значение, которое вычисляется при выполнении
    (`$(...)`, другая переменная), хук не знает - такая переменная забывается,
    и путь с ней проверяется строго.
    """
    body = tokens[1:] if tokens and tokens[0] == "export" else tokens
    if not body or not all(ASSIGNMENT.match(token) for token in body):
        return False
    for token in body:
        name, value = ASSIGNMENT.match(token).groups()
        if any(char in value for char in "$`%"):
            assigned.pop(name, None)
        else:
            assigned[name] = value
    return True


def is_absolute(path):
    """Абсолютный путь POSIX или Windows: /x, C:/x, C:\\x."""
    path = path.replace("\\", "/")
    return path.startswith("/") or re.match(r"^[A-Za-z]:/", path) is not None


def next_workdir(workdir, tokens):
    """
    Каталог после `cd` в этой же команде.

    None означает каталог проекта или неизвестный каталог - тогда относительные
    пути проверяются как пути проекта, то есть строго, как и раньше. Неизвестным
    каталог становится после `cd -`, `cd ~` и `cd` по переменной, которой в
    команде не присваивали значения.
    """
    args = [arg for arg in tokens[1:] if not arg.startswith("-")]
    if not args:
        return None
    target = args[0].replace("\\", "/")
    if target.startswith("~") or any(char in target for char in "$`%"):
        return None
    if is_absolute(target) or workdir is None:
        return target
    return posixpath.join(workdir, target)


def basename(path):
    """Имя исполняемого файла без директории и расширения .exe."""
    name = path_basename(path)
    return name[:-4] if name.lower().endswith(".exe") else name


def path_basename(path):
    """Последний элемент пути; разделители обеих ОС считаются одинаковыми."""
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def is_protected_env(path):
    """Файл с секретами, запись в который запрещена. Примеры конфигов исключены."""
    base = path_basename(path.strip("\"'"))
    if base in ENV_ALLOWED:
        return False
    return base == ".env" or base.startswith(".env.")


# ---------------------------------------------------------------------------
# Правило fs — деструктив файловой системы
# ---------------------------------------------------------------------------
def is_null_device(target):
    """Устройство-пустышка: запись туда ничего не сохраняет."""
    return target.strip('"\'').lower().lstrip("/") in ("dev/null", "nul")


def sed_file_arguments(args):
    """
    Файловые аргументы `sed -i` без самого скрипта замены.

    Скрипт — первый позиционный аргумент, если он не пришёл через -e или -f.
    Отличить его от имени файла надёжнее всего по этому правилу, а не по виду:
    имя файла тоже может содержать слэши.
    """
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return []
    # -e/-f задают скрипт отдельно, тогда все позиционные — файлы.
    script_given_separately = any(
        a.startswith("-e") or a.startswith("-f") or a.startswith("--expression")
        or a.startswith("--file")
        for a in args
    )
    return positional if script_given_separately else positional[1:]


def written_paths(tokens):
    """
    Пути, в которые сегмент команды пишет: цели перенаправления и аргументы
    команд, создающих или меняющих файлы. Чтение сюда не попадает.
    """
    targets = []
    for index, token in enumerate(tokens):
        if token in (">", ">>", ">|", "&>", ">&") and index + 1 < len(tokens):
            target = tokens[index + 1]
            # `2>&1` — дублирование дескриптора, а не файл, и `> /dev/null`
            # ничего не сохраняет. Считать их записью значило блокировать
            # `npm test 2>&1` и `ls > /dev/null` у тринадцати ролей матрицы,
            # то есть мешать обычной работе. Хук, который мешает, отключают.
            #
            # Разбор режет `2>&1` на `2`, `>&`, `1`: амперсанд уходит в
            # оператор, и целью остаётся голый номер дескриптора.
            if is_null_device(target):
                continue
            if target.startswith("&"):
                continue
            if token in (">&", "&>") and target.isdigit():
                continue
            targets.append(target)

    if not tokens:
        return targets
    command = basename(tokens[0])
    args = [t for t in tokens[1:] if t not in (">", ">>", ">|", "&>", ">&", "<")]

    if command == "sed" and any(a.startswith("-i") for a in args):
        # Первый непозиционный аргумент sed — сам скрипт замены, а не файл:
        # без этого `sed -i s/a/b/ file` читалось как «пишет в s/a/b/», и роль
        # не могла править даже собственную папку памяти.
        targets += sed_file_arguments(args)
    elif command in ("rm", "tee", "truncate", "shred", "unlink", "touch", "mkdir"):
        targets += [a for a in args if not a.startswith("-")]
    elif command in ("mv", "cp", "install"):
        positional = [a for a in args if not a.startswith("-")]
        targets += positional[-1:] if len(positional) > 1 else []
    return targets


def modifies_existing(tokens, target):
    """
    Правит ли команда существующий файл, а не создаёт новый.

    Различие нужно ровно там, где его делает матрица: devops и local-sysops
    правят существующее, но не создают. `sed -i` и `tee -a` — правка;
    `>` и `tee` без `-a` создают или обнуляют, то есть равны созданию.
    """
    if not tokens:
        return False

    # Дописывание через оператор: `>> файл`. Форма равнозначна `tee -a`,
    # и обе разбираются одинаково — иначе роль, которой можно дописать в
    # CHANGELOG одним способом, не может тем же действием другим.
    for index, token in enumerate(tokens[:-1]):
        if token == ">>" and tokens[index + 1] == target:
            return True

    command = basename(tokens[0])
    if command == "sed":
        return any(a.startswith("-i") for a in tokens[1:])
    if command == "tee":
        return any(a in ("-a", "--append") for a in tokens[1:])

    # Остальное — создание или обнуление. Если дописывают в несуществующий
    # файл, он появится: отличить это хук не может, потому что смотрит на
    # команду, а не на диск. Послабление касается двух ролей, чья работа и
    # состоит в правке существующих релизных файлов.
    return False


def check_memory_bash(tokens, agent, workdir=None):
    """
    Та же защита зоны роли, но со стороны Bash.

    Без этой половины правило обходится тривиально: инструмент Write
    заблокирован, а `cat > файл` пишет тот же файл мимо проверки. Хуже того,
    платформа после блокировки Write сама предлагает агенту перейти на Bash.
    """
    denied = MEMORY_MATRIX.get(agent)
    if not agent or denied is None:
        return

    # Роль, которой запрещено только создание файлов (devops, local-sysops),
    # правит существующие — и через оболочку тоже. Без этой проверки половины
    # правила расходились: `Edit CHANGELOG.md` проходил, а `sed -i` по тому же
    # файлу блокировался, хотя это одно и то же действие разными руками.
    edits_existing_files_allowed = not (denied & {"Edit", "MultiEdit"})

    for target in written_paths(tokens):
        raw = target.strip("\"'").replace("\\", "/")
        # После `cd <каталог>` в той же команде относительный путь считается от
        # него: `cd <временный каталог> && cp a b` пишет туда, а не в проект.
        if workdir and not is_absolute(raw):
            raw = posixpath.join(workdir.replace("\\", "/"), raw)
        # lstrip("./") здесь недопустим: он снимает не префикс, а любые символы
        # из набора, и съедает точку у .claude, ломая проверку своей же зоны.
        path = "/" + posixpath.normpath(raw).lstrip("/")
        if in_own_memory(path, agent):
            continue
        if in_role_zone(path, agent):
            continue
        if edits_existing_files_allowed and modifies_existing(tokens, target):
            continue
        # Путь с переменной, которой в команде не присваивали значения, хук
        # проверить не может. Роль из живого прогона приписала такой отказ
        # классификатору разрешений - текст обязан назвать настоящую причину.
        unresolved = msg("memory.unresolved_variable") if any(c in target for c in "$`%") else ""
        deny(msg(
            "memory.shell_write",
            agent=agent,
            target=target,
            extra=role_zone_hint(agent),
        ) + unresolved)


def check_fs(tokens):
    # basename, а не точное имя: /bin/rm — та же команда, что и rm.
    if not tokens or basename(tokens[0]) != "rm":
        return

    recursive = force = False
    targets = []
    for tok in tokens[1:]:
        if tok == "--recursive":
            recursive = True
        elif tok == "--force":
            force = True
        elif tok.startswith("--"):
            continue
        elif tok.startswith("-") and len(tok) > 1:
            # Короткие флаги слипаются: -rf, -fr, -Rf — смотрим посимвольно.
            if "r" in tok or "R" in tok:
                recursive = True
            if "f" in tok:
                force = True
        else:
            targets.append(tok)

    if not (recursive and force):
        return

    for target in targets:
        # Переменная в пути опасна сама по себе: при пустом значении путь
        # схлопывается в корень. Проверить её значение хук не может.
        if "$" in target or "%" in target:
            deny(msg("fs.rm_rf_variable", target=target))

        normalized = target.replace("\\", "/")
        if normalized in DANGEROUS_RM_TARGETS or normalized.rstrip("/") in ("", "~", ".", ".."):
            deny(msg("fs.rm_rf_dangerous", target=target))


# ---------------------------------------------------------------------------
# Правило git — деструктивные операции с историей и рабочей копией
# ---------------------------------------------------------------------------
def check_git(tokens):
    if not tokens or basename(tokens[0]) != "git":
        return
    rest = tokens[1:]
    # Пропускаем глобальные флаги вида -C <путь>, чтобы добраться до подкоманды.
    idx = 0
    while idx < len(rest) and rest[idx].startswith("-"):
        idx += 2 if rest[idx] in ("-C", "-c") else 1
    if idx >= len(rest):
        return
    subcommand = rest[idx]
    args = rest[idx + 1:]

    if subcommand == "push":
        if any(a == "--mirror" for a in args):
            confirm(msg("git.push_mirror"))

        # Удаление ветки на сервере: --delete или рефспек, начинающийся с двоеточия.
        if any(a in ("--delete", "-d") for a in args) or any(a.startswith(":") for a in args):
            confirm(msg("git.push_delete"))

        forced = any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in args)
        # Рефспек, начинающийся с плюса, — тот же force, только другим синтаксисом.
        plus_refspec = any(a.startswith("+") for a in args)
        if (forced or plus_refspec) and not any(a.startswith("--force-with-lease") for a in args):
            syntax = msg("git.syntax_plus_refspec") if plus_refspec and not forced \
                else msg("git.syntax_force")
            confirm(msg("git.push_force", syntax=syntax))

    if subcommand == "reset" and "--hard" in args:
        confirm(msg("git.reset_hard"))

    if subcommand == "clean":
        # -n и --dry-run ничего не удаляют, а показывают список — это безопасно
        # и как раз то, что хук предлагает в качестве альтернативы. Интерактивный
        # режим тоже спрашивает пользователя, поэтому пропускается.
        def has_flag(short, long_name):
            return any(
                a == long_name
                or (a.startswith("-") and not a.startswith("--") and short in a)
                for a in args
            )

        safe = has_flag("n", "--dry-run") or has_flag("i", "--interactive")
        # Проверяем не набор букв, а сам факт вызова: без -f git clean и так
        # откажется работать, поэтому любой недry-run вызов — намерение удалять.
        if not safe:
            confirm(msg("git.clean"))

    # git restore делает то же, что checkout --, и в справке git предлагается
    # как современная замена, поэтому правило обязано покрывать обе формы.
    if subcommand in ("checkout", "restore"):
        tail = args[args.index("--") + 1:] if "--" in args else [a for a in args if not a.startswith("-")]
        # any, а не all: `git checkout -- . README` откатывает всё точно так же,
        # а наличие второго пути раньше снимало блокировку.
        if any(t in (".", "./", "*", "./*", ":/") for t in tail):
            confirm(msg("git.checkout_all", subcommand=subcommand))


# ---------------------------------------------------------------------------
# Правило sql — запросы без WHERE и деструктив схемы
# ---------------------------------------------------------------------------
def sql_skeleton(text):
    """
    Оставляет от запроса только структуру: комментарии убирает, строковые
    литералы и закавыченные идентификаторы заменяет заглушками.

    Без этого `SELECT 'DELETE FROM users'` выглядит удалением, а
    `UPDATE "users" SET ...` не опознаётся из-за кавычек вокруг имени таблицы.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    text = re.sub(r"'(?:[^']|'')*'", " 'lit' ", text)
    text = re.sub(r'"[^"]*"', " ident ", text)
    text = re.sub(r"`[^`]*`", " ident ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_parenthesised(text):
    """Убирает содержимое скобок: WHERE из подзапроса не защищает внешний запрос."""
    out, depth = [], 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def check_sql(tokens):
    # Клиент может быть вызван по абсолютному пути или с расширением .exe.
    if not tokens or basename(tokens[0]) not in DB_CLIENTS:
        return

    # SQL приезжает отдельным аргументом после -c/-e, кавычки уже сняты лексером.
    skeleton = sql_skeleton(" ".join(tokens[1:]))

    # Каждый оператор проверяется отдельно: WHERE в первом не оправдывает второй.
    for statement in skeleton.split(";"):
        _check_sql_statement(statement)


def _check_sql_statement(flat):
    if not flat.strip():
        return
    has_where = re.search(r"\bwhere\b", strip_parenthesised(flat), re.I) is not None

    if re.search(r"\bdelete\s+from\b", flat, re.I) and not has_where:
        deny(msg("sql.delete_no_where"))

    if re.search(r"\bupdate\s+[a-z_][\w.]*\s+set\b", flat, re.I) and not has_where:
        deny(msg("sql.update_no_where"))

    # DROP любого объекта, а не перечисленных видов: MATERIALIZED VIEW,
    # FUNCTION, TRIGGER, ROLE, EXTENSION уничтожают не меньше таблицы.
    # Закрытый список дважды оказывался уже обещания в документации — сначала
    # на трёх формах, потом на девяти. Перечисление проигрывает здесь по той
    # же причине, что и в матрице разрешений: список забывают дополнить.
    if re.search(
        r"\b(drop\s+(?:if\s+exists\s+)?(?:materialized\s+)?[a-z_]+"
        r"|alter\s+table\b[^;]*\bdrop\b"
        r"|truncate\b)",
        flat, re.I,
    ):
        confirm(msg("sql.drop_truncate"))


# ---------------------------------------------------------------------------
# Правило env — защита файлов с секретами
# ---------------------------------------------------------------------------
def check_env(tool_name, file_path, _agent):
    if tool_name not in WRITE_TOOLS or not file_path:
        return
    if is_protected_env(file_path):
        deny(msg("env.write", path=file_path))


# ---------------------------------------------------------------------------
# Правило memory — возврат ограничений, снятых полем memory
# ---------------------------------------------------------------------------
def check_memory(tool_name, file_path, agent):
    """
    Поле memory включает агенту Read/Write/Edit в обход списка tools и тем самым
    снимает ограничения роли. Это правило возвращает их обратно: запись разрешена
    только туда, где она роли положена. Разбор — checklists/permission-checklist.md.
    """
    if not agent or tool_name not in WRITE_TOOLS or not file_path:
        return
    denied = MEMORY_MATRIX.get(agent)
    if denied is None:
        return

    # normpath обязателен: без него путь вида
    # .claude/agent-memory/code-reviewer/../../../src/app.py считается
    # собственной папкой памяти и выпускает запись за пределы зоны.
    path = "/" + posixpath.normpath(file_path.replace("\\", "/")).lstrip("/")

    # Собственная папка памяти — всегда разрешена, ради неё правило и существует.
    if in_own_memory(path, agent):
        return

    # browser-tester пишет тест-артефакты: спеки, скриншоты, отчёты.
    #
    # Внутри своей зоны разрешены и Write, и Edit. Раньше Edit был закрыт
    # везде, включая E2E, — и это заставляло роль делать худшее из двух:
    # чтобы поправить одну строку в спеке, приходилось переписывать файл
    # целиком через Write. Граница зон проводится по файлам, а не по
    # инструментам: в своей зоне роль работает обычным способом, в чужую
    # не заходит вовсе.
    # qa-tester - тестовые файлы и временный каталог, см. in_qa_tester_zone.
    if in_role_zone(path, agent):
        return

    if tool_name not in denied:
        return

    deny(msg(
        "memory.tool_write",
        agent=agent,
        path=file_path,
        tool=tool_name,
        extra=role_zone_hint(agent),
    ))


def check_env_bash(tokens):
    """
    Та же защита .env, но со стороны Bash.

    Без этой половины правило бесполезно: `echo KEY=... > .env` пишет в файл
    мимо инструментов Edit и Write, то есть мимо проверки по имени файла.
    """
    def blocked(path, how):
        deny(msg("env.shell", path=path, how=how))

    # Перенаправление вывода в файл: > .env, >> .env.local.
    # Ищем по токенам, а не по тексту: символ > внутри кавычек оператором
    # не является, и `echo 'пример: > .env'` блокировать нельзя.
    for index, token in enumerate(tokens):
        if token in (">", ">>", ">|", "&>", ">&") and index + 1 < len(tokens):
            target = tokens[index + 1]
            if is_protected_env(target):
                blocked(target, msg("env.how_redirect"))

    if not tokens:
        return
    command = basename(tokens[0])
    args = [t for t in tokens[1:] if t not in (">", ">>", ">|", "&>", ">&", "<")]

    # Правка на месте, удаление, запись через tee и подобное.
    if command == "sed" and any(a.startswith("-i") for a in args):
        targets = [a for a in args if not a.startswith("-")]
    elif command in ("rm", "tee", "truncate", "shred", "unlink"):
        targets = [a for a in args if not a.startswith("-")]
    elif command in ("mv", "cp", "install"):
        # У копирования и перемещения опасен только адресат — последний аргумент.
        positional = [a for a in args if not a.startswith("-")]
        targets = positional[-1:] if len(positional) > 1 else []
    else:
        return

    for target in targets:
        if is_protected_env(target):
            blocked(target, msg("env.how_command", command=command))


BASH_RULES = {"fs": check_fs, "git": check_git, "sql": check_sql, "env": check_env_bash}
PATH_RULES = {"env": check_env, "memory": check_memory}
# env зарегистрировано в обоих наборах, поэтому без дедупликации оно попадает
# в список дважды и печатается пользователю как «fs, git, sql, env, env, memory».
ALL_RULES = list(BASH_RULES) + [name for name in PATH_RULES if name not in BASH_RULES]


def analyze_bash(command, enabled, agent="", depth=0):
    """
    Проверяет команду посегментно, раскрывая вложенные sh -c.

    Без раскрытия `sh -c 'rm -rf /'` выглядит вызовом sh: имя опасной команды
    спрятано внутри строкового аргумента, и ни одно правило до него не доходит.
    """
    if depth > MAX_NESTING:
        return
    command = strip_heredoc_bodies(command)
    # Контекст одной команды: переменные, присвоенные в ней, и каталог после cd.
    # Каждый сегмент по отдельности этого не знает, а живой прогон показал, что
    # оболочкой пишут именно так: сначала SC=<каталог> или cd, потом запись.
    assigned = {}
    workdir = None
    for tokens in split_segments(command):
        tokens = expand_assigned(tokens, assigned)
        if remember_assignments(tokens, assigned):
            continue
        tokens = strip_wrappers(tokens)
        if not tokens:
            continue
        if basename(tokens[0]) == "cd":
            workdir = next_workdir(workdir, tokens)
            continue
        if basename(tokens[0]) in SHELL_WRAPPERS:
            for index in range(1, len(tokens) - 1):
                if tokens[index] in ("-c", "--command"):
                    analyze_bash(tokens[index + 1], enabled, agent, depth + 1)
            continue
        for name, rule in BASH_RULES.items():
            if name in enabled:
                rule(tokens)
        # Правило зоны роли требует agent_type, поэтому вызывается отдельно.
        if "memory" in enabled:
            check_memory_bash(tokens, agent, workdir)


def main():
    parser = argparse.ArgumentParser(description=msg("cli.guard_description"))
    parser.add_argument(
        "--rules",
        default=",".join(ALL_RULES),
        help=msg("cli.guard_rules", rules=", ".join(ALL_RULES)),
    )
    args = parser.parse_args()
    enabled = {r.strip() for r in args.rules.split(",") if r.strip()}

    # Пустой список правил отключает защиту целиком и так же молча, как опечатка.
    # Выключать хук нужно, убирая его из конфигурации, а не обнуляя --rules.
    if not enabled:
        deny(msg("config.empty_rules", rules=", ".join(ALL_RULES)), ask=False)

    # Опечатка в имени правила не должна тихо отключать защиту: молчаливо
    # неработающий хук хуже отсутствующего, потому что создаёт уверенность.
    unknown = sorted(enabled - set(ALL_RULES))
    if unknown:
        deny(msg("config.unknown_rules",
                 unknown=", ".join(unknown), rules=", ".join(ALL_RULES)), ask=False)

    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        # Неразбираемый вход — не наше дело блокировать, но и молчать нельзя.
        sys.stderr.write(msg("config.bad_json"))
        return

    # Язык сообщений может быть задан файлом в проекте, а не только окружением.
    use_project(data.get("cwd") or "")

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    agent = data.get("agent_type") or ""

    if tool_name == "Bash":
        analyze_bash(tool_input.get("command") or "", enabled, agent)
        emit_confirmation()
        return

    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    for name, rule in PATH_RULES.items():
        if name in enabled:
            rule(tool_name, file_path, agent)
    emit_confirmation()


if __name__ == "__main__":
    main()
