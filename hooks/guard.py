#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guard.py — защитные PreToolUse-хуки для команды агентов.

Механические инварианты: то, что нельзя доверять промпту, проверяется кодом.
Обоснование — principles/09-mechanical-invariants.md, классы риска — principles/03.

Вход:  JSON хука на stdin.
Выход: пусто = разрешить; JSON с permissionDecision=deny = заблокировать.
       Код возврата всегда 0 — решение передаётся через JSON, а не через exit 2.

Зависимости: только стандартная библиотека Python 3.8+.
Работает одинаково на Windows и POSIX — намеренно одна реализация вместо
двух шелловых, которые пришлось бы держать в синхроне.

Запуск:
    python guard.py --rules fs,git,sql,env,memory
    python guard.py                # то же самое: без --rules включены все
"""

import argparse
import json
import posixpath
import re
import shlex
import sys

# Инструменты, которые пишут в файлы. Read сюда намеренно не входит.
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

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
SEGMENT_SEPARATORS = {";", "|", "||", "&&", "&", "|&", ";;", "(", ")"}

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
    # browser-tester пишет тест-артефакты: Write ограничен зоной tests/ ниже,
    # а Edit продуктового кода ему не положен вовсе.
    "browser-tester": {"Edit", "MultiEdit", "Write"},
    "devops": {"Write"},
    "local-sysops": {"Write"},
}

BROWSER_TESTER_WRITE_DIRS = ("/tests/", "/test/")


def deny(reason):
    """Печатает решение об отказе и завершает работу."""
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
    lexer = shlex.shlex(newlines_to_separators(command), posix=True, punctuation_chars=True)
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
def written_paths(tokens):
    """
    Пути, в которые сегмент команды пишет: цели перенаправления и аргументы
    команд, создающих или меняющих файлы. Чтение сюда не попадает.
    """
    targets = []
    for index, token in enumerate(tokens):
        if token in (">", ">>", ">|", "&>", ">&") and index + 1 < len(tokens):
            targets.append(tokens[index + 1])

    if not tokens:
        return targets
    command = basename(tokens[0])
    args = [t for t in tokens[1:] if t not in (">", ">>", ">|", "&>", ">&", "<")]

    if command == "sed" and any(a.startswith("-i") for a in args):
        targets += [a for a in args if not a.startswith("-")]
    elif command in ("rm", "tee", "truncate", "shred", "unlink", "touch", "mkdir"):
        targets += [a for a in args if not a.startswith("-")]
    elif command in ("mv", "cp", "install"):
        positional = [a for a in args if not a.startswith("-")]
        targets += positional[-1:] if len(positional) > 1 else []
    return targets


def check_memory_bash(tokens, agent):
    """
    Та же защита зоны роли, но со стороны Bash.

    Без этой половины правило обходится тривиально: инструмент Write
    заблокирован, а `cat > файл` пишет тот же файл мимо проверки. Хуже того,
    платформа после блокировки Write сама предлагает агенту перейти на Bash.
    """
    if not agent or agent not in MEMORY_MATRIX:
        return

    for target in written_paths(tokens):
        # lstrip("./") здесь недопустим: он снимает не префикс, а любые символы
        # из набора, и съедает точку у .claude, ломая проверку своей же зоны.
        path = "/" + posixpath.normpath(target.strip("\"'").replace("\\", "/")).lstrip("/")
        if "/.claude/agent-memory/{}/".format(agent) in path:
            continue
        if agent == "browser-tester" and any(d in path for d in BROWSER_TESTER_WRITE_DIRS):
            continue
        deny(
            "BLOCKED [W/Write]: {agent} пытается записать {target} командой оболочки.\n\n"
            "Причина блокировки: этой роли запись вне своей зоны не положена по матрице "
            "разрешений. Инструмент Write у неё уже ограничен — запись через оболочку "
            "закрывается тем же правилом, иначе ограничение обходилось бы одной строкой.\n\n"
            "Разрешено: .claude/agent-memory/{agent}/{extra}\n\n"
            "Альтернативы:\n"
            "  1. Нужен файл вне зоны — верни задачу оркестратору, её сделает профильный агент\n"
            "  2. Заметка на будущее — пиши в свою папку памяти\n"
            "  3. Ограничение мешает по делу — меняй матрицу осознанно, а не в обход".format(
                agent=agent,
                target=target,
                extra=" и тест-артефакты в tests/" if agent == "browser-tester" else "",
            )
        )


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
            deny(
                "BLOCKED [P/Privileged]: rm -rf по цели «{}» — путь содержит переменную.\n\n"
                "Причина блокировки: если переменная окажется пустой, путь схлопнется "
                "в корень и команда снесёт систему. Проверить её значение хук не может.\n\n"
                "Альтернативы:\n"
                "  1. Подставь путь буквально, без переменной — это снимет блокировку\n"
                "  2. Сначала выведи цель отдельной командой и убедись, что она не пустая\n"
                "  3. Если путь известен только во время выполнения — выполни удаление сам, "
                "вне агента"
                .format(target)
            )

        normalized = target.replace("\\", "/")
        if normalized in DANGEROUS_RM_TARGETS or normalized.rstrip("/") in ("", "~", ".", ".."):
            deny(
                "BLOCKED [P/Privileged]: rm -rf по цели «{}» — удаление по корню, "
                "домашней директории, текущему каталогу или маске.\n\n"
                "Причина блокировки: операция необратима и затрагивает файлы за пределами "
                "задачи. Именно так теряются рабочие копии и данные, которых нет в git.\n\n"
                "Альтернативы:\n"
                "  1. Укажи конкретный подкаталог: rm -rf ./build\n"
                "  2. Если файлы под контролем версий — git clean -n сначала покажет список\n"
                "  3. Если нужен именно этот путь — выполни команду сам, вне агента"
                .format(target)
            )


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
            deny(
                "BLOCKED [P/Privileged]: git push --mirror — приведение удалённого репозитория "
                "к точной копии локального.\n\n"
                "Причина блокировки: перезаписываются все ветки и теги, а ветки, которых нет "
                "локально, удаляются на сервере. Одна команда затрагивает работу всей команды.\n\n"
                "Альтернативы:\n"
                "  1. git push origin <ветка> — отправить конкретную ветку\n"
                "  2. git push --tags — если нужны именно теги\n"
                "  3. Если зеркалирование действительно нужно — выполни команду сам, вне агента"
            )

        # Удаление ветки на сервере: --delete или рефспек, начинающийся с двоеточия.
        if any(a in ("--delete", "-d") for a in args) or any(a.startswith(":") for a in args):
            deny(
                "BLOCKED [P/Privileged]: git push --delete — удаление ветки на сервере.\n\n"
                "Причина блокировки: ветка исчезает у всех, кто с ней работает. "
                "Класс P по principles/03 — подтверждать каждый раз, даже если разрешали раньше.\n\n"
                "Альтернативы:\n"
                "  1. Убедись, что ветка влита, и удали её сам через интерфейс хостинга\n"
                "  2. Локальную копию можно удалить безопасно: git branch -d <ветка>\n"
                "  3. Если ветка нужна как архив — поставь на неё тег перед удалением"
            )

        forced = any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in args)
        # Рефспек, начинающийся с плюса, — тот же force, только другим синтаксисом.
        plus_refspec = any(a.startswith("+") for a in args)
        if (forced or plus_refspec) and not any(a.startswith("--force-with-lease") for a in args):
            syntax = "git push origin +<ветка>" if plus_refspec and not forced else "git push --force"
            deny(
                "BLOCKED [P/Privileged]: {} — принудительная перезапись истории.\n\n"
                "Причина блокировки: теряются коммиты, которые уже видят другие разработчики. "
                "Восстановить их можно только из чужих локальных копий.\n"
                "Рефспек с плюсом впереди (+main) означает ровно то же, что и --force.\n\n"
                "Альтернативы:\n"
                "  1. git push --force-with-lease — блокируется, если кто-то запушил после тебя\n"
                "  2. git push без флага и без плюса — если конфликта нет\n"
                "  3. git revert вместо перезаписи — история остаётся целой".format(syntax)
            )

    if subcommand == "reset" and "--hard" in args:
        deny(
            "BLOCKED [W/Write]: git reset --hard — сброс рабочей копии без возможности отката.\n\n"
            "Причина блокировки: незакоммиченные изменения исчезают безвозвратно, "
            "git их нигде не сохраняет.\n\n"
            "Альтернативы:\n"
            "  1. git stash — спрятать изменения с возможностью вернуть\n"
            "  2. git checkout <файл> — откатить точечно, а не всё сразу\n"
            "  3. Если сброс действительно нужен — сделай git stash перед ним"
        )

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
            deny(
                    "BLOCKED [W/Write]: git clean — удаление неотслеживаемых файлов.\n\n"
                    "Причина блокировки: под удаление попадают .env, локальные конфиги "
                    "и всё, что намеренно не в git.\n\n"
                    "Альтернативы:\n"
                    "  1. git clean -n — сухой прогон, покажет список без удаления\n"
                    "  2. git clean -i — интерактивный режим с выбором\n"
                    "  3. Удали конкретные файлы вручную"
                )

    # git restore делает то же, что checkout --, и в справке git предлагается
    # как современная замена, поэтому правило обязано покрывать обе формы.
    if subcommand in ("checkout", "restore"):
        tail = args[args.index("--") + 1:] if "--" in args else [a for a in args if not a.startswith("-")]
        # any, а не all: `git checkout -- . README` откатывает всё точно так же,
        # а наличие второго пути раньше снимало блокировку.
        if any(t in (".", "./", "*", "./*", ":/") for t in tail):
            deny(
                "BLOCKED [W/Write]: git {} по всей рабочей копии — откат всех изменений.\n\n"
                "Причина блокировки: правки во всех файлах пропадают разом, включая те, "
                "которых задача не касалась.\n\n"
                "Альтернативы:\n"
                "  1. git {} -- <конкретный-файл>\n"
                "  2. git stash — сохранить, а потом решить\n"
                "  3. git diff — сначала посмотри, что именно потеряется".format(
                    subcommand, subcommand
                )
            )


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
        deny(
            "BLOCKED [P/Privileged]: DELETE FROM без WHERE — удаление всех строк таблицы.\n\n"
            "Причина блокировки: запрос без WHERE почти всегда означает опечатку или "
            "недодуманное условие. Откатить его нечем.\n\n"
            "Альтернативы:\n"
            "  1. Добавь WHERE с явным условием\n"
            "  2. Сначала оцени объём: SELECT COUNT(*) с тем же WHERE\n"
            "  3. Если нужно очистить таблицу целиком — сделай дамп и выполни команду сам"
        )

    if re.search(r"\bupdate\s+[a-z_][\w.]*\s+set\b", flat, re.I) and not has_where:
        deny(
            "BLOCKED [P/Privileged]: UPDATE без WHERE — изменение всех строк таблицы.\n\n"
            "Причина блокировки: прежние значения нигде не сохраняются, восстановить их "
            "можно только из бэкапа.\n\n"
            "Альтернативы:\n"
            "  1. Добавь WHERE с явным условием\n"
            "  2. Сначала оцени объём: SELECT COUNT(*) с тем же WHERE\n"
            "  3. Если массовое обновление нужно — сделай дамп таблицы заранее"
        )

    if re.search(r"\b(drop\s+(database|schema|table)|truncate\b)", flat, re.I):
        deny(
            "BLOCKED [P/Privileged]: DROP или TRUNCATE — удаление структуры или всех данных.\n\n"
            "Причина блокировки: операция необратима и не журналируется как обычные изменения.\n\n"
            "Альтернативы:\n"
            "  1. Сделай дамп затронутых таблиц и подтверди операцию вручную\n"
            "  2. На боевой БД — только со свежим бэкапом и подтверждением владельца\n"
            "  3. Если это миграция — оформи её файлом миграции, а не разовой командой"
        )


# ---------------------------------------------------------------------------
# Правило env — защита файлов с секретами
# ---------------------------------------------------------------------------
def check_env(tool_name, file_path, _agent):
    if tool_name not in WRITE_TOOLS or not file_path:
        return
    if is_protected_env(file_path):
        deny(
            "BLOCKED [P/Privileged]: запись в {} — правка файла с секретами.\n\n"
            "Причина блокировки: .env содержит ключи и пароли. Изменение вслепую ломает "
            "окружение, а случайная запись секрета не в тот файл может утечь в git.\n\n"
            "Альтернативы:\n"
            "  1. Прочитать .env можно — хук блокирует только запись\n"
            "  2. Нужную переменную добавь в .env.example, значение перенесёт владелец\n"
            "  3. Если правка действительно нужна — сделай её сам, вне агента"
            .format(file_path)
        )


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
    if "/.claude/agent-memory/{}/".format(agent) in path:
        return

    # browser-tester пишет тест-артефакты: спеки, скриншоты, отчёты.
    if agent == "browser-tester" and tool_name == "Write":
        if any(d in path for d in BROWSER_TESTER_WRITE_DIRS):
            return

    if tool_name not in denied:
        return

    deny(
        "BLOCKED [W/Write]: {agent} пытается изменить {path} инструментом {tool}.\n\n"
        "Причина блокировки: этот инструмент роли не положен по матрице разрешений. "
        "Он появился у неё только потому, что включено поле memory — оно выдаёт "
        "Read/Write/Edit в обход списка tools.\n\n"
        "Разрешено: .claude/agent-memory/{agent}/{extra}\n\n"
        "Альтернативы:\n"
        "  1. Нужна правка вне зоны — верни задачу оркестратору, её сделает профильный агент\n"
        "  2. Заметка на будущее — пиши в свою папку памяти\n"
        "  3. Ограничение мешает по делу — меняй матрицу осознанно, а не в обход".format(
            agent=agent,
            path=file_path,
            tool=tool_name,
            extra=" и тест-артефакты в tests/" if agent == "browser-tester" else "",
        )
    )


def check_env_bash(tokens):
    """
    Та же защита .env, но со стороны Bash.

    Без этой половины правило бесполезно: `echo KEY=... > .env` пишет в файл
    мимо инструментов Edit и Write, то есть мимо проверки по имени файла.
    """
    def blocked(path, how):
        deny(
            "BLOCKED [P/Privileged]: {} — {}.\n\n"
            "Причина блокировки: .env содержит ключи и пароли. Перезапись через "
            "оболочку так же необратима, как через редактор, и так же ломает окружение.\n\n"
            "Альтернативы:\n"
            "  1. Чтение не ограничено: cat .env, grep KEY .env работают\n"
            "  2. Нужную переменную добавь в .env.example, значение перенесёт владелец\n"
            "  3. Если правка действительно нужна — сделай её сам, вне агента".format(path, how)
        )

    # Перенаправление вывода в файл: > .env, >> .env.local.
    # Ищем по токенам, а не по тексту: символ > внутри кавычек оператором
    # не является, и `echo 'пример: > .env'` блокировать нельзя.
    for index, token in enumerate(tokens):
        if token in (">", ">>", ">|", "&>", ">&") and index + 1 < len(tokens):
            target = tokens[index + 1]
            if is_protected_env(target):
                blocked(target, "перезапись через перенаправление вывода")

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
            blocked(target, "изменение или удаление командой " + command)


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
    for tokens in split_segments(command):
        tokens = strip_wrappers(tokens)
        if not tokens:
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
            check_memory_bash(tokens, agent)


def main():
    parser = argparse.ArgumentParser(description="Защитные PreToolUse-хуки")
    parser.add_argument(
        "--rules",
        default=",".join(ALL_RULES),
        help="Список правил через запятую: " + ", ".join(ALL_RULES),
    )
    args = parser.parse_args()
    enabled = {r.strip() for r in args.rules.split(",") if r.strip()}

    # Пустой список правил отключает защиту целиком и так же молча, как опечатка.
    # Выключать хук нужно, убирая его из конфигурации, а не обнуляя --rules.
    if not enabled:
        deny(
            "guard.py: список правил пуст — защита не выполняется.\n\n"
            "Причина блокировки: пустой --rules отключает все проверки, но выглядит "
            "как рабочая конфигурация. Хук отказывает, пока это не исправлено.\n\n"
            "Допустимые правила: {}.\n"
            "Если хук нужно отключить — убери его из конфигурации, а не обнуляй "
            "список правил.".format(", ".join(ALL_RULES))
        )

    # Опечатка в имени правила не должна тихо отключать защиту: молчаливо
    # неработающий хук хуже отсутствующего, потому что создаёт уверенность.
    unknown = sorted(enabled - set(ALL_RULES))
    if unknown:
        deny(
            "guard.py: в конфигурации указаны неизвестные правила: {}.\n\n"
            "Причина блокировки: опечатка в имени правила молча отключила бы защиту. "
            "Хук отказывает, пока конфигурация не исправлена.\n\n"
            "Допустимые правила: {}.\n"
            "Где править: значение --rules в hooks.json или settings.json.".format(
                ", ".join(unknown), ", ".join(ALL_RULES)
            )
        )

    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        # Неразбираемый вход — не наше дело блокировать, но и молчать нельзя.
        sys.stderr.write("guard.py: не удалось разобрать JSON хука\n")
        return

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    agent = data.get("agent_type") or ""

    if tool_name == "Bash":
        analyze_bash(tool_input.get("command") or "", enabled, agent)
        return

    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    for name, rule in PATH_RULES.items():
        if name in enabled:
            rule(tool_name, file_path, agent)


if __name__ == "__main__":
    main()
