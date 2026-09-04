# Установка на разных платформах

Установка плагином для Claude Code — в [README](../README.ru.md#установка). Здесь всё остальное: ручное копирование и другие платформы.

---

### Claude Code — вручную

> ⚠ **Способ установки ровно один.** Если плагин уже стоит, копировать хуки в проект нельзя, и наоборот. При двух установках **каждый хук срабатывает дважды**, и это не безобидно: счётчик правила трёх попыток растёт вдвое быстрее, поэтому повтор блокируется после второй попытки вместо четвёртой, а каждая запись журнала задваивается и считать по нему становится нельзя. Обе установки при этом по отдельности исправны, и никаких других признаков поломка не подаёт — замерено на живой сборке.
>
> Начиная с версии 1.11.0 `session_start.py` обнаруживает такое сочетание и говорит о нём в начале сессии. Убрать: либо `.claude/hooks/` вместе с блоком `hooks` из `.claude/settings.json`, либо сам плагин — `/plugin uninstall agents-best-teams@sergvss`.

Если плагины не подходят: склонируй репозиторий и скопируй нужное.

```bash
git clone https://github.com/sergvss/agents-best-teams.git
cd agents-best-teams

PROJECT=/path/to/your-project
mkdir -p "$PROJECT/.claude/agents" "$PROJECT/.claude/skills" "$PROJECT/.claude/hooks"

# Шаблоны агентов — адаптировать под проект после копирования.
# Маской "все .md" здесь копировать нельзя: в каталоге лежат два файла,
# которые ролями не являются. README.md — оглавление каталога, а
# external-llm-reviewer.md — паттерн вызова внешней модели, у него нет
# поля tools, зато есть name: попав в .claude/agents/, он зарегистрируется
# как роль без единого инструмента, которую оркестратору некуда позвать.
for f in templates/*.md; do
  case "$(basename "$f")" in README.md|external-llm-reviewer.md) continue ;; esac
  cp "$f" "$PROJECT/.claude/agents/"
done

# Принципы и чек-листы: роли ссылаются на них относительным путём
# ../principles/..., и после копирования в .claude/agents/ этот путь
# указывает на .claude/principles/. Без них ссылки внутри ролей ведут в никуда.
cp -r principles checklists "$PROJECT/.claude/"

# Чек-листы как скиллы
cp -r skills/* "$PROJECT/.claude/skills/"

# Защитные хуки — копировать все .py разом, а не выборочно.
# settings.example.json объявляет четыре хука, и каждому нужен свой файл;
# messages.py нужен всем — в нём текст сообщений, без него ничего не стартует.
cp hooks/*.py "$PROJECT/.claude/hooks/"
```

Маска `*.py` здесь не лень, а защита от самой частой ошибки установки: скопировать `guard.py` и решить, что готово. Конфигурация ссылается на `guard.py`, `approval_log.py`, `retry_guard.py` и `session_start.py`; недостающий файл останавливает работу: `guard.py` без `messages.py` отвечает отказом сразу на обоих языках и выходит с кодом 2, отсутствующий скрипт даёт «can't open file» и тот же код 2. Для `PreToolUse` код 2 — блокирующая ошибка, то есть работа встанет. Так и задумано: раньше в первом из этих двух случаев был код 1, а он хук роняет, но вызов пропускает — защита снималась тихо, и об этом никто не узнавал. Громкий отказ хуже незаметно снятой защиты только на вид.

Пятый, `verify.py`, в конфигурацию намеренно не включён: без команды тестов ему нечего запускать. Подключение — [hooks/README.md](../hooks/README.md).

Конфигурацию хуков переноси вручную:

```bash
# Если .claude/settings.json ещё нет
cp hooks/settings.example.json "$PROJECT/.claude/settings.json"
```

⚠ Если `settings.json` уже существует — **не копируй поверх**, иначе потеряешь свои настройки. Открой оба файла и перенеси в свой только блок `"hooks"`, оставив остальные ключи как были.

Язык сообщений хуков по умолчанию английский. Русский:

```bash
printf 'ru\n' > "$PROJECT/.claude/.abt-lang"
```

После установки обязательно проверь, что хуки действительно работают — [hooks/README.md](../hooks/README.md#проверь-что-защита-встала--это-обязательный-шаг). Незапустившийся хук неотличим от отсутствующего.

### Codex

```bash
git clone https://github.com/sergvss/agents-best-teams.git
cd agents-best-teams
mkdir -p ~/.codex/skills/agents-best-teams
cp -r templates principles checklists ~/.codex/skills/agents-best-teams/
```

### Cursor

```bash
# Cursor читает .cursor/rules/ — копируй принципы туда как контекст-правила
cp principles/*.md /path/to/your-project/.cursor/rules/
# Шаблоны агентов используй через @-mention при разговоре или сохрани в docs/agents/
cp -r templates /path/to/your-project/docs/agents/
```

### Любая другая agent-платформа

Положи `../templates/` в директорию, где твоя платформа подгружает определения агентов (обычно `<project>/.agents/`, `<platform>/agents/` или эквивалент). `../principles/` — как общий контекст или CLAUDE.md-like файл.

> **Заметка про пути в шаблонах:** в `../templates/*.md` пути persistent memory указаны как `.claude/agent-memory/<agent>/` — это конвенция Claude Code. Для других платформ замени `.claude/` на эквивалентную директорию (Cursor → `.cursor/`, Codex → `~/.codex/`, и т.д.). Сама механика memory (что хранить, как обновлять) не зависит от платформы — см. `../principles/06-memory-hygiene.md`.
