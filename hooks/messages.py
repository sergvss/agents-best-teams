#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Каталог сообщений хуков — единственное место, где живёт текст для человека.

Зачем отдельный файл. Сообщение блокировки читают в худший момент: когда что-то
уже пошло не так. Пока строки лежали в коде хуков, язык был прибит гвоздями, и
поменять его можно было только правкой самих правил.

Как выбирается язык:
  1. переменная окружения ABT_LANG (ru / en) — побеждает всегда;
  2. файл .claude/.abt-lang в каталоге проекта — это пишет setup-agent-team;
  3. английский по умолчанию.

Русский не «второй сорт»: методология написана по-русски, и ABT_LANG=ru
возвращает весь человеческий текст к ней. Английский стоит умолчанием потому,
что плагин публичный, а неизвестный язык блокировки хуже понятного.

Инвариант: каждый ключ существует на обоих языках, а в коде хуков не остаётся
ни одной строки для пользователя. И то и другое проверяется тестами — иначе
языки разойдутся ровно так же, как расходятся две копии документации.
"""

import os

DEFAULT_LANG = "en"
SUPPORTED = ("en", "ru")
LANG_FILE = os.path.join(".claude", ".abt-lang")

_project_dir = ""
_lang_cache = None


def use_project(cwd):
    """Запоминает каталог проекта: язык может быть задан файлом внутри него."""
    global _project_dir, _lang_cache
    _project_dir = cwd or ""
    _lang_cache = None


def _read_lang_file(base):
    try:
        with open(os.path.join(base, LANG_FILE), encoding="utf-8") as fh:
            return fh.read().strip().lower()[:2]
    except OSError:
        return ""


def _resolve():
    value = (os.environ.get("ABT_LANG") or "").strip().lower()[:2]
    if value in SUPPORTED:
        return value
    # Хук запускается с рабочим каталогом проекта, но payload может назвать его
    # явно — тогда верим payload, он точнее.
    value = _read_lang_file(_project_dir or os.getcwd())
    if value in SUPPORTED:
        return value
    return DEFAULT_LANG


def lang():
    global _lang_cache
    if _lang_cache is None:
        _lang_cache = _resolve()
    return _lang_cache


def msg(key, **fields):
    """Текст сообщения на выбранном языке. Неизвестный ключ — ошибка, не заглушка."""
    entry = MESSAGES[key]
    text = entry.get(lang()) or entry[DEFAULT_LANG]
    return text.format(**fields) if fields else text


MESSAGES = {
    # -- общий хвост всех блокировок правил ----------------------------------
    # Дописывается в deny() к каждому сообщению правила, а не повторяется в
    # семнадцати текстах. Смысл в моменте: агент читает это ровно тогда, когда
    # упёрся, — не в промпте роли, который к этому времени далеко позади.
    "guard.ask_do_not_work_around": {
        "ru": "\n\nНи одна альтернатива не подходит — спроси пользователя, а не подбирай обход. "
              "В автоматическом режиме это важнее всего: там за каждым шагом никто не смотрит, "
              "и найденный обход останется незамеченным.",
        "en": "\n\nIf none of the alternatives fits, ask the user — do not go looking for a way "
              "around. This matters most in automatic mode: nobody is watching each step there, "
              "and a workaround you find will go unnoticed.",
    },

    # -- правило memory ------------------------------------------------------
    "memory.extra_browser_tester": {
        "ru": " и каталог E2E целиком",
        "en": " and the whole E2E directory",
    },
    "memory.shell_write": {
        "ru": "BLOCKED [W/Write]: {agent} пытается записать {target} командой оболочки.\n\n"
              "Причина блокировки: этой роли запись вне своей зоны не положена по матрице "
              "разрешений. Инструмент Write у неё уже ограничен — запись через оболочку "
              "закрывается тем же правилом, иначе ограничение обходилось бы одной строкой.\n\n"
              "Разрешено: .claude/agent-memory/{agent}/{extra}\n\n"
              "Альтернативы:\n"
              "  1. Нужен файл вне зоны — верни задачу оркестратору, её сделает профильный агент\n"
              "  2. Заметка на будущее — пиши в свою папку памяти\n"
              "  3. Ограничение мешает по делу — меняй матрицу осознанно, а не в обход",
        "en": "BLOCKED [W/Write]: {agent} is trying to write {target} through the shell.\n\n"
              "Why this is blocked: the permission matrix does not allow this role to write "
              "outside its own area. Its Write tool is already restricted — writing through "
              "the shell is closed by the same rule, otherwise the restriction could be "
              "sidestepped with a single line.\n\n"
              "Allowed: .claude/agent-memory/{agent}/{extra}\n\n"
              "Alternatives:\n"
              "  1. Need a file outside your area — hand the task back to the orchestrator, "
              "the right agent will do it\n"
              "  2. A note for later — write it into your own memory directory\n"
              "  3. The restriction genuinely gets in the way — change the matrix "
              "deliberately, do not work around it",
    },
    "memory.tool_write": {
        "ru": "BLOCKED [W/Write]: {agent} пытается изменить {path} инструментом {tool}.\n\n"
              "Причина блокировки: этот инструмент роли не положен по матрице разрешений. "
              "Он появился у неё только потому, что включено поле memory — оно выдаёт "
              "Read/Write/Edit в обход списка tools.\n\n"
              "Разрешено: .claude/agent-memory/{agent}/{extra}\n\n"
              "Альтернативы:\n"
              "  1. Нужна правка вне зоны — верни задачу оркестратору, её сделает профильный агент\n"
              "  2. Заметка на будущее — пиши в свою папку памяти\n"
              "  3. Ограничение мешает по делу — меняй матрицу осознанно, а не в обход",
        "en": "BLOCKED [W/Write]: {agent} is trying to modify {path} with the {tool} tool.\n\n"
              "Why this is blocked: the permission matrix does not grant this tool to this "
              "role. It only appeared because the memory field is enabled — that field hands "
              "out Read/Write/Edit bypassing the tools list.\n\n"
              "Allowed: .claude/agent-memory/{agent}/{extra}\n\n"
              "Alternatives:\n"
              "  1. An edit outside your area is needed — hand the task back to the "
              "orchestrator, the right agent will do it\n"
              "  2. A note for later — write it into your own memory directory\n"
              "  3. The restriction genuinely gets in the way — change the matrix "
              "deliberately, do not work around it",
    },

    # -- правило fs ----------------------------------------------------------
    "fs.rm_rf_variable": {
        "ru": "BLOCKED [P/Privileged]: rm -rf по цели «{target}» — путь содержит переменную.\n\n"
              "Причина блокировки: если переменная окажется пустой, путь схлопнется "
              "в корень и команда снесёт систему. Проверить её значение хук не может.\n\n"
              "Альтернативы:\n"
              "  1. Подставь путь буквально, без переменной — это снимет блокировку\n"
              "  2. Сначала выведи цель отдельной командой и убедись, что она не пустая\n"
              "  3. Если путь известен только во время выполнения — выполни удаление сам, "
              "вне агента",
        "en": "BLOCKED [P/Privileged]: rm -rf on target \"{target}\" — the path contains a "
              "variable.\n\n"
              "Why this is blocked: if the variable turns out to be empty, the path collapses "
              "to the root and the command wipes the system. The hook cannot check its value.\n\n"
              "Alternatives:\n"
              "  1. Write the path out literally, without the variable — that lifts the block\n"
              "  2. Print the target with a separate command first and confirm it is not empty\n"
              "  3. If the path is only known at runtime — run the deletion yourself, outside "
              "the agent",
    },
    "fs.rm_rf_dangerous": {
        "ru": "BLOCKED [P/Privileged]: rm -rf по цели «{target}» — удаление по корню, "
              "домашней директории, текущему каталогу или маске.\n\n"
              "Причина блокировки: операция необратима и затрагивает файлы за пределами "
              "задачи. Именно так теряются рабочие копии и данные, которых нет в git.\n\n"
              "Альтернативы:\n"
              "  1. Укажи конкретный подкаталог: rm -rf ./build\n"
              "  2. Если файлы под контролем версий — git clean -n сначала покажет список\n"
              "  3. Если нужен именно этот путь — выполни команду сам, вне агента",
        "en": "BLOCKED [P/Privileged]: rm -rf on target \"{target}\" — deleting by root, home "
              "directory, current directory or wildcard.\n\n"
              "Why this is blocked: the operation is irreversible and reaches files outside "
              "the task. This is exactly how working copies and data that never reached git "
              "get lost.\n\n"
              "Alternatives:\n"
              "  1. Name a specific subdirectory: rm -rf ./build\n"
              "  2. If the files are under version control, git clean -n lists them first\n"
              "  3. If this really is the path you want — run the command yourself, outside "
              "the agent",
    },

    # -- правило git ---------------------------------------------------------
    "git.push_mirror": {
        "ru": "BLOCKED [P/Privileged]: git push --mirror — приведение удалённого репозитория "
              "к точной копии локального.\n\n"
              "Причина блокировки: перезаписываются все ветки и теги, а ветки, которых нет "
              "локально, удаляются на сервере. Одна команда затрагивает работу всей команды.\n\n"
              "Альтернативы:\n"
              "  1. git push origin <ветка> — отправить конкретную ветку\n"
              "  2. git push --tags — если нужны именно теги\n"
              "  3. Если зеркалирование действительно нужно — выполни команду сам, вне агента",
        "en": "BLOCKED [P/Privileged]: git push --mirror — forcing the remote repository into "
              "an exact copy of the local one.\n\n"
              "Why this is blocked: every branch and tag is overwritten, and branches that do "
              "not exist locally are deleted on the server. One command reaches everyone's "
              "work.\n\n"
              "Alternatives:\n"
              "  1. git push origin <branch> — push one specific branch\n"
              "  2. git push --tags — if tags are what you actually need\n"
              "  3. If mirroring really is required — run the command yourself, outside the "
              "agent",
    },
    "git.push_delete": {
        "ru": "BLOCKED [P/Privileged]: git push --delete — удаление ветки на сервере.\n\n"
              "Причина блокировки: ветка исчезает у всех, кто с ней работает. "
              "Класс P по principles/03 — подтверждать каждый раз, даже если разрешали раньше.\n\n"
              "Альтернативы:\n"
              "  1. Убедись, что ветка влита, и удали её сам через интерфейс хостинга\n"
              "  2. Локальную копию можно удалить безопасно: git branch -d <ветка>\n"
              "  3. Если ветка нужна как архив — поставь на неё тег перед удалением",
        "en": "BLOCKED [P/Privileged]: git push --delete — deleting a branch on the server.\n\n"
              "Why this is blocked: the branch disappears for everyone working on it. Risk "
              "class P in principles/03 — confirm every single time, even if it was allowed "
              "before.\n\n"
              "Alternatives:\n"
              "  1. Check the branch is merged and delete it yourself through the hosting UI\n"
              "  2. The local copy can be removed safely: git branch -d <branch>\n"
              "  3. If the branch is worth keeping as an archive — tag it before deleting",
    },
    "git.push_force": {
        "ru": "BLOCKED [P/Privileged]: {syntax} — принудительная перезапись истории.\n\n"
              "Причина блокировки: теряются коммиты, которые уже видят другие разработчики. "
              "Восстановить их можно только из чужих локальных копий.\n"
              "Рефспек с плюсом впереди (+main) означает ровно то же, что и --force.\n\n"
              "Альтернативы:\n"
              "  1. git push --force-with-lease — блокируется, если кто-то запушил после тебя\n"
              "  2. git push без флага и без плюса — если конфликта нет\n"
              "  3. git revert вместо перезаписи — история остаётся целой",
        "en": "BLOCKED [P/Privileged]: {syntax} — force-overwriting history.\n\n"
              "Why this is blocked: commits other developers can already see are lost. The "
              "only way back is somebody else's local copy.\n"
              "A refspec with a leading plus (+main) means exactly the same thing as --force.\n\n"
              "Alternatives:\n"
              "  1. git push --force-with-lease — refuses if someone pushed after you\n"
              "  2. git push with no flag and no plus — if there is no conflict\n"
              "  3. git revert instead of rewriting — the history stays intact",
    },
    # Подставляется внутрь git.push_force — значит, тоже обязано быть на языке
    # сообщения. Иначе английский текст получает русское «<ветка>» внутри.
    "git.syntax_plus_refspec": {
        "ru": "git push origin +<ветка>",
        "en": "git push origin +<branch>",
    },
    "git.syntax_force": {
        "ru": "git push --force",
        "en": "git push --force",
    },
    "git.reset_hard": {
        "ru": "BLOCKED [W/Write]: git reset --hard — сброс рабочей копии без возможности отката.\n\n"
              "Причина блокировки: незакоммиченные изменения исчезают безвозвратно, "
              "git их нигде не сохраняет.\n\n"
              "Альтернативы:\n"
              "  1. git stash — спрятать изменения с возможностью вернуть\n"
              "  2. git checkout <файл> — откатить точечно, а не всё сразу\n"
              "  3. Если сброс действительно нужен — сделай git stash перед ним",
        "en": "BLOCKED [W/Write]: git reset --hard — resetting the working copy with no way "
              "back.\n\n"
              "Why this is blocked: uncommitted changes are gone for good; git keeps no copy "
              "of them anywhere.\n\n"
              "Alternatives:\n"
              "  1. git stash — put the changes aside so they can be restored\n"
              "  2. git checkout <file> — revert one file instead of everything\n"
              "  3. If the reset really is needed — run git stash first",
    },
    "git.clean": {
        "ru": "BLOCKED [W/Write]: git clean — удаление неотслеживаемых файлов.\n\n"
              "Причина блокировки: под удаление попадают .env, локальные конфиги "
              "и всё, что намеренно не в git.\n\n"
              "Альтернативы:\n"
              "  1. git clean -n — сухой прогон, покажет список без удаления\n"
              "  2. git clean -i — интерактивный режим с выбором\n"
              "  3. Удали конкретные файлы вручную",
        "en": "BLOCKED [W/Write]: git clean — deleting untracked files.\n\n"
              "Why this is blocked: .env, local configuration and everything deliberately "
              "kept out of git are in the blast radius.\n\n"
              "Alternatives:\n"
              "  1. git clean -n — dry run, lists the files without deleting\n"
              "  2. git clean -i — interactive mode, pick what goes\n"
              "  3. Delete the specific files by hand",
    },
    "git.checkout_all": {
        "ru": "BLOCKED [W/Write]: git {subcommand} по всей рабочей копии — откат всех изменений.\n\n"
              "Причина блокировки: правки во всех файлах пропадают разом, включая те, "
              "которых задача не касалась.\n\n"
              "Альтернативы:\n"
              "  1. git {subcommand} -- <конкретный-файл>\n"
              "  2. git stash — сохранить, а потом решить\n"
              "  3. git diff — сначала посмотри, что именно потеряется",
        "en": "BLOCKED [W/Write]: git {subcommand} across the whole working copy — reverting "
              "every change.\n\n"
              "Why this is blocked: edits in every file go at once, including files the task "
              "never touched.\n\n"
              "Alternatives:\n"
              "  1. git {subcommand} -- <specific-file>\n"
              "  2. git stash — keep them, decide later\n"
              "  3. git diff — look at what would be lost first",
    },

    # -- правило sql ---------------------------------------------------------
    "sql.delete_no_where": {
        "ru": "BLOCKED [P/Privileged]: DELETE FROM без WHERE — удаление всех строк таблицы.\n\n"
              "Причина блокировки: запрос без WHERE почти всегда означает опечатку или "
              "недодуманное условие. Откатить его нечем.\n\n"
              "Альтернативы:\n"
              "  1. Добавь WHERE с явным условием\n"
              "  2. Сначала оцени объём: SELECT COUNT(*) с тем же WHERE\n"
              "  3. Если нужно очистить таблицу целиком — сделай дамп и выполни команду сам",
        "en": "BLOCKED [P/Privileged]: DELETE FROM without WHERE — removing every row in the "
              "table.\n\n"
              "Why this is blocked: a query without WHERE almost always means a typo or a "
              "condition that was never finished. There is nothing to roll it back with.\n\n"
              "Alternatives:\n"
              "  1. Add a WHERE with an explicit condition\n"
              "  2. Size it up first: SELECT COUNT(*) with the same WHERE\n"
              "  3. If the table really must be emptied — take a dump and run the command "
              "yourself",
    },
    "sql.update_no_where": {
        "ru": "BLOCKED [P/Privileged]: UPDATE без WHERE — изменение всех строк таблицы.\n\n"
              "Причина блокировки: прежние значения нигде не сохраняются, восстановить их "
              "можно только из бэкапа.\n\n"
              "Альтернативы:\n"
              "  1. Добавь WHERE с явным условием\n"
              "  2. Сначала оцени объём: SELECT COUNT(*) с тем же WHERE\n"
              "  3. Если массовое обновление нужно — сделай дамп таблицы заранее",
        "en": "BLOCKED [P/Privileged]: UPDATE without WHERE — changing every row in the "
              "table.\n\n"
              "Why this is blocked: the previous values are kept nowhere; a backup is the "
              "only way to get them back.\n\n"
              "Alternatives:\n"
              "  1. Add a WHERE with an explicit condition\n"
              "  2. Size it up first: SELECT COUNT(*) with the same WHERE\n"
              "  3. If the bulk update is genuinely needed — dump the table beforehand",
    },
    "sql.drop_truncate": {
        "ru": "BLOCKED [P/Privileged]: DROP или TRUNCATE — удаление структуры или всех данных.\n\n"
              "Причина блокировки: операция необратима и не журналируется как обычные изменения.\n\n"
              "Альтернативы:\n"
              "  1. Сделай дамп затронутых таблиц и подтверди операцию вручную\n"
              "  2. На боевой БД — только со свежим бэкапом и подтверждением владельца\n"
              "  3. Если это миграция — оформи её файлом миграции, а не разовой командой",
        "en": "BLOCKED [P/Privileged]: DROP or TRUNCATE — removing structure or all data.\n\n"
              "Why this is blocked: the operation is irreversible and is not journalled the "
              "way ordinary changes are.\n\n"
              "Alternatives:\n"
              "  1. Dump the affected tables and confirm the operation by hand\n"
              "  2. On a production database — only with a fresh backup and the owner's "
              "confirmation\n"
              "  3. If this is a migration — write it as a migration file, not a one-off "
              "command",
    },

    # -- правило env ---------------------------------------------------------
    "env.write": {
        "ru": "BLOCKED [P/Privileged]: запись в {path} — правка файла с секретами.\n\n"
              "Причина блокировки: .env содержит ключи и пароли. Изменение вслепую ломает "
              "окружение, а случайная запись секрета не в тот файл может утечь в git.\n\n"
              "Альтернативы:\n"
              "  1. Прочитать .env можно — хук блокирует только запись\n"
              "  2. Нужную переменную добавь в .env.example, значение перенесёт владелец\n"
              "  3. Если правка действительно нужна — сделай её сам, вне агента",
        "en": "BLOCKED [P/Privileged]: writing to {path} — editing a file that holds secrets.\n\n"
              "Why this is blocked: .env holds keys and passwords. Editing it blind breaks "
              "the environment, and a secret accidentally written into the wrong file can "
              "leak into git.\n\n"
              "Alternatives:\n"
              "  1. Reading .env is fine — the hook blocks writes only\n"
              "  2. Add the variable you need to .env.example; the owner will fill in the value\n"
              "  3. If the edit really is needed — make it yourself, outside the agent",
    },
    "env.shell": {
        "ru": "BLOCKED [P/Privileged]: {path} — {how}.\n\n"
              "Причина блокировки: .env содержит ключи и пароли. Перезапись через "
              "оболочку так же необратима, как через редактор, и так же ломает окружение.\n\n"
              "Альтернативы:\n"
              "  1. Чтение не ограничено: cat .env, grep KEY .env работают\n"
              "  2. Нужную переменную добавь в .env.example, значение перенесёт владелец\n"
              "  3. Если правка действительно нужна — сделай её сам, вне агента",
        "en": "BLOCKED [P/Privileged]: {path} — {how}.\n\n"
              "Why this is blocked: .env holds keys and passwords. Overwriting it through the "
              "shell is just as irreversible as through an editor, and breaks the environment "
              "just the same.\n\n"
              "Alternatives:\n"
              "  1. Reading is not restricted: cat .env, grep KEY .env both work\n"
              "  2. Add the variable you need to .env.example; the owner will fill in the value\n"
              "  3. If the edit really is needed — make it yourself, outside the agent",
    },
    "env.how_redirect": {
        "ru": "перезапись через перенаправление вывода",
        "en": "overwrite through output redirection",
    },
    "env.how_command": {
        "ru": "изменение или удаление командой {command}",
        "en": "modification or deletion by the {command} command",
    },

    # -- конфигурация самого хука -------------------------------------------
    "config.empty_rules": {
        "ru": "guard.py: список правил пуст — защита не выполняется.\n\n"
              "Причина блокировки: пустой --rules отключает все проверки, но выглядит "
              "как рабочая конфигурация. Хук отказывает, пока это не исправлено.\n\n"
              "Допустимые правила: {rules}.\n"
              "Если хук нужно отключить — убери его из конфигурации, а не обнуляй "
              "список правил.",
        "en": "guard.py: the rule list is empty — no protection is running.\n\n"
              "Why this is blocked: an empty --rules switches every check off while still "
              "looking like a working configuration. The hook refuses until this is fixed.\n\n"
              "Valid rules: {rules}.\n"
              "If you want the hook off — remove it from the configuration instead of "
              "emptying the rule list.",
    },
    "config.unknown_rules": {
        "ru": "guard.py: в конфигурации указаны неизвестные правила: {unknown}.\n\n"
              "Причина блокировки: опечатка в имени правила молча отключила бы защиту. "
              "Хук отказывает, пока конфигурация не исправлена.\n\n"
              "Допустимые правила: {rules}.\n"
              "Где править: значение --rules в hooks.json или settings.json.",
        "en": "guard.py: the configuration names rules that do not exist: {unknown}.\n\n"
              "Why this is blocked: a typo in a rule name would silently switch protection "
              "off. The hook refuses until the configuration is fixed.\n\n"
              "Valid rules: {rules}.\n"
              "Where to fix it: the --rules value in hooks.json or settings.json.",
    },
    "config.bad_json": {
        "ru": "guard.py: не удалось разобрать JSON хука\n",
        "en": "guard.py: could not parse the hook JSON\n",
    },

    # -- retry_guard ---------------------------------------------------------
    "retry.blocked": {
        "ru": "STOP [правило 3 попыток]: эта команда уже падала {n} раза подряд, "
              "и с прошлой попытки в рабочей копии ничего не изменилось.\n\n"
              "Команда: {cmd}\n\n"
              "Причина остановки: повтор без единой правки между попытками не даст "
              "нового результата. Если три раза не сработало — дело не в случайности.\n\n"
              "Что сделать вместо четвёртой попытки:\n"
              "  1. Сообщить пользователю последнюю ошибку целиком и предложить 2-3 разных подхода\n"
              "  2. Если менял что-то вне файлов — окружение, службу, внешний сервис — скажи "
              "об этом прямо, тогда повтор осмыслен\n"
              "  3. Сменить стратегию, а не флаги у той же команды\n\n"
              "Следующий вызов этой команды блокировку не встретит: счётчик сброшен, "
              "решение за тобой и пользователем.",
        "en": "STOP [three-attempts rule]: this command has already failed {n} times in a row, "
              "and nothing in the working copy has changed since the last attempt.\n\n"
              "Command: {cmd}\n\n"
              "Why this stops here: repeating with not a single edit in between will not "
              "produce a different result. If it failed three times, this is not bad luck.\n\n"
              "What to do instead of a fourth attempt:\n"
              "  1. Give the user the last error in full and offer 2-3 different approaches\n"
              "  2. If you changed something outside the files — the environment, a service, "
              "an external API — say so plainly; then a retry makes sense\n"
              "  3. Change the strategy, not the flags on the same command\n\n"
              "The next call of this command will not hit the block: the counter is reset, "
              "the decision is yours and the user's.",
    },

    # -- verify --------------------------------------------------------------
    "verify.bad_args": {
        "ru": "verify.py: ошибка в аргументах хука, проверка не выполнена.\n",
        "en": "verify.py: bad hook arguments, the check did not run.\n",
    },
    "verify.no_command": {
        "ru": "verify.py: не задан --command, проверять нечего. "
              "Укажи команду тестов в конфигурации хука или убери хук.\n",
        "en": "verify.py: no --command given, there is nothing to check. "
              "Name your test command in the hook configuration, or remove the hook.\n",
    },
    "verify.timeout": {
        "ru": "Проверка не уложилась в {timeout} с и была прервана.",
        "en": "The check did not finish within {timeout}s and was interrupted.",
    },
    "verify.launch_failed": {
        "ru": "verify.py: не удалось запустить проверку: {error}\n",
        "en": "verify.py: could not start the check: {error}\n",
    },
    "verify.slow_hint": {
        "ru": "\n\nЕсли проверка законно долгая — возьми более узкую команду или подними --timeout.",
        "en": "\n\nIf the check is legitimately slow — use a narrower command or raise "
              "--timeout.",
    },
    "verify.giving_up": {
        "ru": "verify.py: проверка всё ещё падает, но блокировать больше не буду "
              "({blocks} раз подряд). Задача НЕ доведена до готовности — скажи об этом "
              "пользователю прямо, не выдавай работу за законченную.\n\n{tail}\n",
        "en": "verify.py: the check still fails, but I will stop blocking "
              "({blocks} times in a row). The task is NOT finished — tell the user so "
              "plainly, do not pass the work off as done.\n\n{tail}\n",
    },
    "verify.not_done": {
        "ru": "НЕ ЗАВЕРШЕНО: проверка «{command}» падает.\n\n"
              "Условие готовности не выполнено, поэтому работа не закончена. "
              "Почини причину, а не симптом: подавлять тест, помечать его skip или "
              "ослаблять проверку — это не починка.\n\n"
              "Вывод команды (последние строки):\n{tail}\n",
        "en": "NOT FINISHED: the check \"{command}\" is failing.\n\n"
              "The done condition is not met, so the work is not finished. Fix the cause, "
              "not the symptom: silencing a test, marking it skip or weakening the assertion "
              "is not a fix.\n\n"
              "Command output (last lines):\n{tail}\n",
    },

    # -- тексты --help -------------------------------------------------------
    # Их видит тот, кто настраивает хуки руками. Читают редко, но правило одно
    # на все строки: текст для человека живёт здесь, а не в коде правила.
    "cli.guard_description": {
        "ru": "Защитные PreToolUse-хуки",
        "en": "Protective PreToolUse hooks",
    },
    "cli.guard_rules": {
        "ru": "Список правил через запятую: {rules}",
        "en": "Comma-separated list of rules: {rules}",
    },
    "cli.retry_bad_args": {
        "ru": "retry_guard: ошибка в аргументах хука.\n",
        "en": "retry_guard: bad hook arguments.\n",
    },
    "cli.approval_log_write_failed": {
        "ru": "approval_log: не удалось записать журнал: {error}\n",
        "en": "approval_log: could not write the log: {error}\n",
    },
    "cli.retry_description": {
        "ru": "Правило трёх попыток",
        "en": "The three-attempts rule",
    },
    "cli.retry_record": {
        "ru": "режим учёта неудач, для события PostToolUseFailure",
        "en": "failure-counting mode, for the PostToolUseFailure event",
    },
    "cli.retry_limit": {
        "ru": "сколько неудач подряд допустимо",
        "en": "how many consecutive failures are allowed",
    },
    "cli.verify_description": {
        "ru": "Stop-хук: проверка перед завершением",
        "en": "Stop hook: run a check before finishing",
    },
    "cli.verify_command": {
        "ru": "команда проверки, например: pytest -q",
        "en": "the check command, for example: pytest -q",
    },
    "cli.verify_max_blocks": {
        "ru": "сколько раз подряд блокировать завершение в одной сессии",
        "en": "how many times in a row to block finishing within one session",
    },
    "cli.verify_timeout": {
        "ru": "предел выполнения команды проверки, секунд",
        "en": "time limit for the check command, in seconds",
    },
    "cli.verify_changed_only": {
        "ru": "пропускать проверку, если в рабочей копии нет изменений",
        "en": "skip the check when the working copy has no changes",
    },

    # -- session_start -------------------------------------------------------
    "session.team_setup": {
        "ru": "Плагин agents-best-teams установлен, но команда ролей в этом проекте ещё не "
              "развёрнута: в `.claude/agents/` нет ни одной роли методологии.\n\n"
              "Уже работает без настройки: защитные хуки и чек-листы как скиллы.\n"
              "Пока не работает: разделение работы по ролям с изолированными зонами.\n\n"
              "Предложи пользователю развернуть команду прямо сейчас — это скилл "
              "`setup-agent-team`: он прочитает стек проекта, предложит состав ролей, "
              "скопирует шаблоны и адаптирует их под проект. Займёт несколько минут и "
              "потребует пары решений от пользователя.\n\n"
              "Предложи это одной короткой фразой в конце своего первого ответа, не прерывая "
              "текущую задачу пользователя. Если он откажется или промолчит — больше не "
              "возвращайся к теме в этой сессии. Отключить подсказку насовсем: создать пустой "
              "файл `.claude/.no-team-setup-prompt`.",
        "en": "The agents-best-teams plugin is installed, but no role team has been deployed "
              "in this project: `.claude/agents/` contains none of the methodology roles.\n\n"
              "Already working with no setup: the protective hooks and the checklists as "
              "skills.\n"
              "Not working yet: splitting work across roles with isolated areas.\n\n"
              "Offer to assemble the team now — that is the `setup-agent-team` skill: it "
              "reads the project stack, proposes a roster, copies the templates and adapts "
              "them to the project. It takes a few minutes and a couple of decisions from "
              "the user.\n\n"
              "Offer it in one short sentence at the end of your first reply, without "
              "interrupting the user's current task. If they decline or say nothing — do not "
              "raise it again this session. To silence the prompt for good: create an empty "
              "`.claude/.no-team-setup-prompt` file.",
    },
    "session.duplicate_install": {
        "ru": "ВНИМАНИЕ: методология agents-best-teams установлена дважды — плагином и "
              "копированием в `.claude/hooks/`. Каждый хук срабатывает по два раза.\n\n"
              "Это не просто шум. Счётчик правила трёх попыток растёт вдвое быстрее, то есть "
              "повтор блокируется после второй попытки вместо четвёртой; в журнале "
              "привилегированных действий каждая запись задваивается, и считать по нему "
              "нельзя. Никаких признаков, кроме этих, поломка не подаёт.\n\n"
              "Скажи пользователю выбрать одну установку из двух:\n"
              "  - оставить плагин — удалить `.claude/hooks/` и блок `hooks` из "
              "`.claude/settings.json`;\n"
              "  - оставить ручную — снять плагин: `/plugin uninstall agents-best-teams@sergvss`.\n\n"
              "Сообщи об этом до всего остального: пока дубль не убран, поведение хуков "
              "отличается от описанного в документации.",
        "en": "WARNING: the agents-best-teams methodology is installed twice — as a plugin and "
              "as a copy in `.claude/hooks/`. Every hook fires twice.\n\n"
              "This is not merely noise. The three-attempts counter grows twice as fast, so a "
              "retry is blocked after the second attempt instead of the fourth; every entry in "
              "the privileged-action log is duplicated, making it useless for counting. The "
              "breakage gives no other sign.\n\n"
              "Tell the user to pick one of the two installations:\n"
              "  - keep the plugin — delete `.claude/hooks/` and the `hooks` block from "
              "`.claude/settings.json`;\n"
              "  - keep the manual copy — remove the plugin: "
              "`/plugin uninstall agents-best-teams@sergvss`.\n\n"
              "Say this before anything else: until the duplicate is gone, the hooks behave "
              "differently from what the documentation describes.",
    },
    "session.language_prompt": {
        "ru": "Язык методологии agents-best-teams в этом проекте не выбран: файла "
              "`.claude/.abt-lang` нет, поэтому сообщения защитных хуков сейчас английские "
              "(умолчание).\n\n"
              "Спроси пользователя одной фразой, на каком языке он хочет их видеть — русском "
              "или английском, — и запиши ответ в `.claude/.abt-lang` одним словом: `ru` или "
              "`en`. Это влияет только на текст для человека; правила и блокировки одинаковы "
              "на обоих языках.\n\n"
              "Запись в `.claude/` Claude Code подтверждает отдельно, и это нормально: если "
              "разрешения нет, не считай это провалом — отдай пользователю готовую строку "
              "`printf 'ru\\n' > .claude/.abt-lang` и на этом закончи.\n\n"
              "Спрашивай один раз и не настаивай: если пользователь промолчал, останется "
              "английский, и вернуться к вопросу можно будет в любой момент.",
        "en": "The agents-best-teams language for this project has not been chosen: there is "
              "no `.claude/.abt-lang` file, so the protective hooks currently speak English "
              "(the default).\n\n"
              "Ask the user in one sentence which language they want for those messages — "
              "Russian or English — and write the answer into `.claude/.abt-lang` as a single "
              "word: `ru` or `en`. This only affects text meant for humans; the rules and the "
              "blocks are identical in both languages.\n\n"
              "Claude Code confirms writes into `.claude/` separately, and that is expected: "
              "if permission is not given, do not treat it as a failure — hand the user the "
              "ready line `printf 'ru\\n' > .claude/.abt-lang` and stop there.\n\n"
              "Ask once and do not push: if the user says nothing, English stays, and the "
              "question can be revisited at any time.",
    },
}
