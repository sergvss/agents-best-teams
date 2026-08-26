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

# Примеры конфигов коммитятся намеренно и секретов не содержат.
ENV_ALLOWED = {".env.example", ".env.sample", ".env.template", ".env.dist"}

# Матрица из checklists/permission-checklist.md, выраженная кодом.
# Для каждой роли: какие инструменты записи ей НЕ положены и куда всё же можно.
# Ключ роли — значение agent_type в JSON хука.
MEMORY_MATRIX = {
    "pm-orchestrator": WRITE_TOOLS,
    "code-reviewer": WRITE_TOOLS,
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


def split_segments(command):
    """Режет составную команду на самостоятельные сегменты по ; && || |."""
    return [s for s in re.split(r"&&|\|\||[;|]", command) if s.strip()]


def tokenize(segment):
    """Разбирает сегмент на токены с учётом кавычек. Кавычки снимаются."""
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        # Незакрытая кавычка — разбираем грубо, лучше чем не проверить вовсе.
        return segment.split()


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
        if token in COMMAND_WRAPPERS:
            saw_wrapper = True
            i += 1
            continue
        if saw_wrapper:
            if token.startswith("-"):
                i += 1
                if token in WRAPPER_FLAGS_WITH_VALUE and i < len(tokens):
                    i += 1
                continue
            # env FOO=bar <команда> — присваивание перед именем команды.
            if "=" in token and not token.startswith("="):
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
def check_fs(tokens, _segment):
    if not tokens or tokens[0] != "rm":
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
                "  1. Подставь путь буквально, без переменной\n"
                "  2. Сначала выведи цель и убедись, что она не пустая\n"
                "  3. Добавь защиту: ${{VAR:?переменная пуста}} — оболочка прервётся сама"
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
def check_git(tokens, _segment):
    if not tokens or tokens[0] != "git":
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
        # и как раз то, что хук предлагает в качестве альтернативы.
        dry_run = any(a == "--dry-run" or (a.startswith("-") and not a.startswith("--") and "n" in a) for a in args)
        for a in args:
            if dry_run:
                break
            if a.startswith("-") and not a.startswith("--") and any(c in a for c in "dfx"):
                deny(
                    "BLOCKED [W/Write]: git clean — удаление неотслеживаемых файлов.\n\n"
                    "Причина блокировки: под удаление попадают .env, локальные конфиги "
                    "и всё, что намеренно не в git.\n\n"
                    "Альтернативы:\n"
                    "  1. git clean -n — сухой прогон, покажет список без удаления\n"
                    "  2. git clean -i — интерактивный режим с выбором\n"
                    "  3. Удали конкретные файлы вручную"
                )

    if subcommand == "checkout" and "--" in args:
        tail = args[args.index("--") + 1:]
        if tail and all(t in (".", "./", "*") for t in tail):
            deny(
                "BLOCKED [W/Write]: git checkout -- . — откат всех изменений рабочей копии.\n\n"
                "Причина блокировки: правки во всех файлах пропадают разом, включая те, "
                "которых задача не касалась.\n\n"
                "Альтернативы:\n"
                "  1. git checkout -- <конкретный-файл>\n"
                "  2. git stash — сохранить, а потом решить\n"
                "  3. git diff — сначала посмотри, что именно потеряется"
            )


# ---------------------------------------------------------------------------
# Правило sql — запросы без WHERE и деструктив схемы
# ---------------------------------------------------------------------------
def check_sql(tokens, _segment):
    # Клиент может быть вызван по абсолютному пути или с расширением .exe.
    if not tokens or basename(tokens[0]) not in DB_CLIENTS:
        return

    # SQL приезжает отдельным аргументом после -c/-e, кавычки уже сняты shlex.
    body = " ".join(tokens[1:])
    flat = re.sub(r"\s+", " ", body)

    has_where = re.search(r"\bwhere\b", flat, re.I) is not None

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

    path = file_path.replace("\\", "/")

    # Собственная папка памяти — всегда разрешена, ради неё правило и существует.
    if "/.claude/agent-memory/{}/".format(agent) in "/" + path.lstrip("/"):
        return

    # browser-tester пишет тест-артефакты: спеки, скриншоты, отчёты.
    if agent == "browser-tester" and tool_name == "Write":
        if any(d in "/" + path.lstrip("/") for d in BROWSER_TESTER_WRITE_DIRS):
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


def check_env_bash(tokens, segment):
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

    # Перенаправление вывода в файл: > .env, >> .env.local
    for match in re.finditer(r">>?\s*([^\s;|&<>]+)", segment):
        if is_protected_env(match.group(1)):
            blocked(match.group(1).strip("\"'"), "перезапись через перенаправление вывода")

    if not tokens:
        return
    command = basename(tokens[0])
    args = tokens[1:]

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
ALL_RULES = list(BASH_RULES) + list(PATH_RULES)


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
        command = tool_input.get("command") or ""
        for segment in split_segments(command):
            tokens = strip_wrappers(tokenize(segment))
            for name, rule in BASH_RULES.items():
                if name in enabled:
                    rule(tokens, segment)
        return

    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    for name, rule in PATH_RULES.items():
        if name in enabled:
            rule(tool_name, file_path, agent)


if __name__ == "__main__":
    main()
