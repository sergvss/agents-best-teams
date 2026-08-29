# Установка на разных платформах

Установка плагином для Claude Code — в [README](../README.ru.md#установка). Здесь всё остальное: ручное копирование и другие платформы.

---

### Claude Code — вручную

Если плагины не подходят: склонируй репозиторий и скопируй нужное.

```bash
git clone https://github.com/sergvss/agents-best-teams.git
cd agents-best-teams

PROJECT=/path/to/your-project
mkdir -p "$PROJECT/.claude/agents" "$PROJECT/.claude/skills" "$PROJECT/.claude/hooks"

# Шаблоны агентов — адаптировать под проект после копирования
cp -r templates/* "$PROJECT/.claude/agents/"

# Чек-листы как скиллы
cp -r skills/* "$PROJECT/.claude/skills/"

# Защитные хуки — копировать все .py разом, а не выборочно.
# settings.example.json объявляет четыре хука, и каждому нужен свой файл;
# messages.py нужен всем — в нём текст сообщений, без него ничего не стартует.
cp hooks/*.py "$PROJECT/.claude/hooks/"
```

Маска `*.py` здесь не лень, а защита от самой частой ошибки установки: скопировать `guard.py` и решить, что готово. Конфигурация ссылается на `guard.py`, `approval_log.py`, `retry_guard.py` и `session_start.py`; недостающий файл не даёт ошибки — хук просто не запускается, а выглядит настроенным.

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
