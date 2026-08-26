<!-- Замени icon.png на свою иконку 200x200, или удали строку — README работает и без неё. -->
<p align="center">
  <img src="./icon.png" alt="agents-best-teams" width="160" />
</p>

<h1 align="center">agents-best-teams</h1>

<p align="center"><em>«Один суперагент перестаёт справляться задолго до того, как ты успеваешь это заметить. Команда специализированных агентов с явными ролями, маршрутизацией и hooks вместо уговоров — масштабируется.»</em></p>

<p align="center">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <img alt="Claude Code compatible" src="https://img.shields.io/badge/Claude_Code-compatible-9333ea" />
  <img alt="Codex compatible" src="https://img.shields.io/badge/Codex-compatible-10b981" />
  <img alt="Cursor compatible" src="https://img.shields.io/badge/Cursor-compatible-0ea5e9" />
  <img alt="Language: Russian" src="https://img.shields.io/badge/lang-RU-red" />
</p>

<p align="center">
  <a href="#use-cases">Use cases</a> ·
  <a href="#что-внутри">Что внутри</a> ·
  <a href="#когда-команда-нужна">Когда нужна</a> ·
  <a href="#quick-install">Quick Install</a> ·
  <a href="#настройка-primary-и-side-моделей">Настройка моделей</a> ·
  <a href="#структура">Структура</a> ·
  <a href="#принципы-одной-строкой">Принципы</a> ·
  <a href="#благодарности">Благодарности</a>
</p>

---

Методология построения **команды AI-агентов** для разработки программного обеспечения. Для тех, кто использует Claude Code, Codex, Cursor или любой другой agent-CLI и хочет перейти от «одного суперагента на всё» к специализированной команде с явными ролями, маршрутизацией и защитой от регрессий.

---

## Use cases

### 1. Новый продуктовый проект — настроить команду агентов с нуля

Стартуешь проект с 1-2 разработчиками + AI-агентами. Берёшь шаблоны из `templates/`, настраиваешь под свой стек (5 мин на шаблон), подключаешь оркестратор. Через 30 минут — рабочая команда из 9-12 ролей, которая параллелит независимые задачи и сама блокирует деструктив через hooks.

### 2. Существующая команда агентов выросла до хаоса

У тебя уже 10+ агентов, но каждый агент частично знает всё, регулярно лезет в чужую зону, нет единой маршрутизации. Берёшь `principles/02-tier-decomposition.md` и `templates/pm-orchestrator.md`, перенастраиваешь маршрутизацию по tier'ам, добавляешь Stop rules — хаос превращается в pipeline.

### 3. Агент сломал прод — нужна страховка от повторов

После инцидента (агент применил миграцию без бэкапа / закоммитил секрет / удалил нужные файлы) — добавляешь Risk classes из `principles/03-risk-classes.md`, защитные hooks из `principles/09-mechanical-invariants.md`, и регрессионный eval-сценарий из `principles/10-eval-suite.md`. Та же ошибка больше не повторится — она кодифицирована.

### 4. Команда работает с агентами, но критичные правки идут наугад

Биллинг, миграции, security — агент сделал, ты проверил «глазами», ок. Через месяц вылез баг. Внедряешь **триангуляцию** из `principles/05-triangulated-review.md`: три модели от разных вендоров (твой PRIMARY + две SIDE-модели — например, GPT + Gemini, если PRIMARY = Claude) параллельно ревьюят критичный diff. Правило 2-из-3 = блокер. Стоимость — копейки, защита — реальная.

### 5. Аудит существующей agent-инфраструктуры

Используешь `checklists/` как чек-листы аудита: Tool / Permission / Context / Planning. Проходишь по своим агентам, отмечаешь gap'ы, заводишь задачи на закрытие. Получаешь конкретный todo-лист улучшений вместо «надо как-то улучшить».

---

## Что внутри

Методология фокусируется на **командной работе** агентов, а не на одиночных:

- **Tier-декомпозиция задач** — pipeline зависит от размера задачи, а не наоборот
- **Изоляция зон ответственности** — каждый агент знает свою зону и не лезет в чужую
- **Триангулированное ревью** — критичные решения проверяют 3 модели параллельно (правило 2-из-3 = блокер)
- **Persistent memory per-agent** — институциональное знание накапливается между сессиями
- **Human-in-the-loop** для привилегированных операций (например, admin-логин в браузере)
- **Eval-suite** для агентного слоя — те же тесты что для продуктового кода
- **Mechanical invariants через hooks** — повторяющиеся ошибки превращаются в код, не в новые правила промпта

---

## Когда команда нужна

**Нужна**, если:
- В проекте 5+ ролей: backend, frontend, QA, DevOps, документация
- Есть критичные зоны с разными правами (прод, биллинг, RBAC)
- Агент регулярно «залезает» в чужую зону и ломает что-то
- Хочется параллельного выполнения независимых задач

**Не нужна**, если:
- Pet-project на одного разработчика
- Задачи однородные (только backend или только frontend)
- Нет времени настраивать — один опытный агент быстрее

---

## Quick Install

### Claude Code — плагином

```
/plugin marketplace add sergvss/agents-best-teams
/plugin install agents-best-teams@sergvss
```

Что заработает сразу после установки:

- **Защитные хуки** — без единой строки настройки. Блокируют деструктив файловой системы, force-push, запись в `.env`, SQL без WHERE.
- **Чек-листы как скиллы** — `tool-checklist`, `permission-checklist`, `planning-checklist`, `context-checklist`, `implementation-path`. Claude подтягивает их сам, когда они уместны, или вызывай через `/`.
- **`/agents-best-teams:setup-agent-team`** — соберёт команду ролей под твой проект: предложит состав по стеку, скопирует шаблоны, адаптирует плейсхолдеры, проверит результат.

Шаблоны агентов плагин намеренно **не** устанавливает сам. В них плейсхолдеры под конкретный проект, и подставить их осмысленно может только человек или скилл сборки — плагин, который положил бы в `.claude/agents/` роли с `<your-project>` внутри, сделал бы хуже, чем ничего. Поэтому после установки запусти `setup-agent-team`.

### Claude Code — вручную

Если плагины не подходят: склонируй репозиторий и скопируй нужное.

```bash
git clone https://github.com/sergvss/agents-best-teams.git
cd agents-best-teams

# Шаблоны агентов — адаптировать под проект после копирования
cp -r templates/* /path/to/your-project/.claude/agents/

# Защитные хуки
mkdir -p /path/to/your-project/.claude/hooks
cp hooks/guard.py /path/to/your-project/.claude/hooks/
cp hooks/settings.example.json /path/to/your-project/.claude/settings.json

# Чек-листы как скиллы
cp -r skills/* /path/to/your-project/.claude/skills/
```

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

Положи `templates/` в директорию, где твоя платформа подгружает определения агентов (обычно `<project>/.agents/`, `<platform>/agents/` или эквивалент). `principles/` — как общий контекст или CLAUDE.md-like файл.

> **Заметка про пути в шаблонах:** в `templates/*.md` пути persistent memory указаны как `.claude/agent-memory/<agent>/` — это конвенция Claude Code. Для других платформ замени `.claude/` на эквивалентную директорию (Cursor → `.cursor/`, Codex → `~/.codex/`, и т.д.). Сама механика memory (что хранить, как обновлять) не зависит от платформы — см. `principles/06-memory-hygiene.md`.

---

## Настройка PRIMARY и SIDE-моделей

Методология **провайдер-нейтральна**. Тебе нужно один раз решить:

| Роль | Что это | Примеры моделей |
|---|---|---|
| **PRIMARY** | Главный агент в твоём IDE/CLI — знает проект, имеет memory. **Кто PRIMARY — определяется твоей платформой**, не выбором модели. | Claude (если ты в Claude Code) / GPT (если ты в Codex) / любая модель в Cursor / Cline / Aider |
| **SIDE-A** | Первый внешний ревьюер для триангуляции — vendor **отличается** от PRIMARY | Любая модель от другого vendor'а |
| **SIDE-B** | Второй внешний ревьюер — vendor **отличается** от PRIMARY **И** SIDE-A | Третья компания |

**Важно:** одна и та же модель может быть PRIMARY у одного пользователя и SIDE у другого — роль определяется **относительно твоей платформы**, не свойствами модели. Если ты в Claude Code → твой PRIMARY = Claude (Anthropic); SIDE-A может быть GPT (OpenAI), SIDE-B — Gemini (Google). Если ты в Codex → PRIMARY = GPT (OpenAI); SIDE-A может быть Claude (Anthropic), SIDE-B — Gemini (Google). И так далее.

Правило: **три разных vendor'а = настоящая независимость взглядов**. Vendor — компания, обучившая модель; не путать с gateway (через какой API ты её зовёшь — OpenRouter, прямой и т.д.). Подробнее — `principles/01-philosophy.md` (раздел «Глоссарий») и `principles/05-triangulated-review.md`.

### Шаг 1. Скопируй конфиг агентов

В этом репо лежит пример конфига для maintainer-агента — используй его как шаблон:

```bash
# В своём проекте создай свой agents.config (имя/формат — на твой выбор)
cp agents-best-teams/agents.config.example /path/to/your-project/agents.config
```

Шаблон описывает три секции:
- `[primary]` — твой основной агент (имя, иногда платформа)
- `[side_a]` / `[side_b]` — внешние модели (провайдер, model id, env-var с ключом)
- `[budget]` — лимиты на платные API-вызовы (per-review, per-tier3, monthly)

### Шаг 2. Заполни ключи

API-ключи SIDE-моделей — в `.env` (никогда не в `agents.config`, никогда не в git):

```bash
# .env (в .gitignore!)
OPENROUTER_API_KEY=sk-or-...    # самый простой вариант — один ключ ко всем моделям
# или прямые ключи провайдеров:
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AI...
```

Самый чистый способ — **OpenRouter**: один ключ → доступ к GPT, Claude, Gemini, Grok, Mistral одновременно. Если используешь его — в `agents.config` укажи `gateway = "openrouter"` для обеих SIDE-моделей, но **разные `vendor`** (правило независимости проверяется по vendor — компании-обучателю модели, а не по gateway).

> **Важно про выбор моделей:** модель для ревью выбирается по **измеримому порогу**, а не по названию тира. Порог — `coding_index ≥ 74` (Artificial Analysis, виден в каталоге OpenRouter при сортировке `coding-high-to-low`); для точек высокого риска (прод-биллинг, прод-RBAC, DDL прода) — `≥ 77` и effort `high`. Слова `mini` / `flash` / `pro` в слаге модели больше ничего не гарантируют: на август 2026 модель Google с `flash` в имени обходит вчерашний флагман OpenAI по коду и стоит в 13 раз дешевле. Экономить нужно уровнем `effort`, а не моделью ниже порога — она не углубится в diff в принципе. Обоснование и эмпирика — `principles/04-orchestration-budgets.md`, раздел «Выбор модели для ревью».

### Шаг 3. Настрой бюджет

Открой `agents.config` и поправь `[budget]` под свой кошелёк. Типовые значения:

```toml
[budget]
per_review_usd = 0.50    # одна триангуляция diff'а
per_tier3_usd  = 2.00    # полный Tier-3 цикл (план + триангуляция + итерации)
monthly_usd    = 50.00   # общий месячный потолок (hard limit — не используй "warn")
on_overflow    = "ask_user"   # ask_user | block | warn
```

> **Hard vs soft limits:** для `monthly_usd` рекомендуется `ask_user` или `block` — это потолок, не warning. Использовать `warn` имеет смысл только для предупреждающих под-порогов (например, 80% от лимита).

Подробности про бюджет и ориентиры стоимости — `principles/04-orchestration-budgets.md`.

---

## Migration: для тех, кто уже использовал шаблоны

Если ты применил `templates/*` до этой правки — **ничего не сломалось**:
- Шаблоны агентов работают как раньше, без `agents.config`
- Persistent memory (`.claude/agent-memory/` и аналоги) — без изменений
- Что добавилось: **возможность** триангуляции и бюджет, **необязательно** для T1/T2

Чтобы подключить новое:
1. Создай `agents.config` в проекте (см. «Настройка PRIMARY и SIDE-моделей» выше)
2. Положи ключи в `.env`
3. На Tier-3 ревью агент сможет прогнать SIDE-A + SIDE-B — по паттерну из `templates/external-llm-reviewer.md` (готовой CLI-обёртки в репо нет, реализация — на твоей стороне, шаблон описывает structure prompt'а, JSON-схему ответа и агрегацию по правилу 2-из-3)

Старая терминология «GPT-консультант» и «внешние ревьюеры» теперь — `reasoning-consultant` (для разовой консультации до Tier 3) и SIDE-A / SIDE-B (для триангулированного ревью после).

---

## Quick Start — применить за 30 минут

> Установил плагином? Шаги 1-3 делает `/agents-best-teams:setup-agent-team`, шаг 4 уже выполнен. Ниже — что происходит под капотом и как сделать то же руками.

1. **Скопируй шаблоны** в свой проект (см. Quick Install выше) и **создай `agents.config`** (см. «Настройка PRIMARY и SIDE-моделей»)
2. **Адаптируй плейсхолдеры** в каждом шаблоне:
   - `<your-project>` — название проекта
   - `<backend-framework>` — FastAPI / Django / Express / Rails / ...
   - `<frontend-framework>` — Vue / React / Svelte / ...
   - `<порт>`, `<ваш-бэк-порт>`, `<ваш-фронт-порт>` — порты твоих dev-серверов
   - `<ваш>` / `<ваша>` в строках описания стека — оставь один вариант из списка рядом, остальные удали

   После замены проверь что **все типы плейсхолдеров** заменены:
   ```bash
   # Подставь свою agents-директорию: .claude/agents/ для Claude Code,
   # .cursor/rules/ для Cursor, ~/.codex/skills/.../ для Codex, и т.д.
   AGENTS_DIR=".claude/agents"   # ← поменяй под свою платформу
   grep -rEn '<your-[a-z-]+>|<ваш[а-и]?>|<ваш-[а-яa-z-]+>|<backend-framework>|<frontend-framework>|<порт>|<reasoning-LLM[^>]*>|<быстрый[^>]*>|<мощный[^>]*>' "$AGENTS_DIR"
   # должно вернуть пусто — все плейсхолдеры заменены
   ```

   > Проверка ищет только **установочные** плейсхолдеры. Разметка вида `<что сделать>`, `<step>`, `<вердикт>` и `<PORT>` в командах `lsof -ti :<PORT>` — это места, которые агент заполняет по ходу задачи. Их заменять не нужно, и в проверку они намеренно не входят.
3. **Настрой `pm-orchestrator.md`** — таблицу маршрутизации под свои реальные роли. Минимально: dev-агент + qa-тестер + оркестратор
4. **Поставь защитные hooks** — они уже реализованы, писать ничего не нужно:
   ```bash
   cp hooks/guard.py <проект>/.claude/hooks/
   cp hooks/settings.example.json <проект>/.claude/settings.json
   ```
   Закрывают деструктив ФС, force-push, правку `.env`, SQL без WHERE и возвращают ролям ограничения, которые снимает поле `memory`. Только Python, без зависимостей. Подробности — `hooks/README.md`
5. **Прогони eval-suite** из `principles/10-eval-suite.md` — 5 базовых сценариев, 20-30 минут. Покажет дыры до того, как они выстрелят в проде

---

## Структура

```
principles/          # 10 принципов методологии
├── 01-philosophy.md           # Манифест: команда > суперагент
├── 02-tier-decomposition.md   # Tier 1/2/3: выбор pipeline по сложности
├── 03-risk-classes.md         # Классы риска R/D/W/P и подтверждения
├── 04-orchestration-budgets.md# Бюджеты делегаций, чекпоинты
├── 05-triangulated-review.md  # 3 ревьюера параллельно
├── 06-memory-hygiene.md       # Persistent memory: что хранить и как
├── 07-stop-rules.md           # 6 правил остановки для агентов
├── 08-approval-log.md         # Журнал привилегированных действий
├── 09-mechanical-invariants.md# Hooks вместо промпт-уговоров
└── 10-eval-suite.md           # Eval-сценарии для агентного слоя

templates/           # Готовые шаблоны промптов агентов
├── pm-orchestrator.md         # Оркестратор: декомпозиция + координация
├── code-reviewer.md           # Ревьюер: инварианты + триангуляция
├── dev-backend.md             # Backend-разработчик
├── dev-frontend.md            # Frontend-разработчик
├── qa-tester.md               # QA-инженер: тесты + регрессии
├── browser-tester.md          # E2E-тестировщик (Playwright MCP)
├── devops.md                  # DevOps: релизы, версии, инфраструктура
├── docs-writer.md             # Технический писатель
├── external-llm-reviewer.md   # Паттерн внешнего LLM-ревьюера
├── dev-database.md            # DBA: схема БД, миграции, индексы, оптимизация
├── i18n-keeper.md             # Мультиязычность: локали, переводы, аудит
└── local-sysops.md            # SysOps локальной среды: процессы, порты, логи

checklists/          # Чек-листы перед действием агента
├── tool-checklist.md          # Что проверить перед вызовом инструмента
├── permission-checklist.md    # Матрица разрешений агентов
├── context-checklist.md       # Управление контекстом в команде
├── planning-checklist.md      # Чек-лист декомпозиции задачи
└── implementation-path.md     # Путь от задачи до коммита

hooks/               # Рабочие защитные хуки (слой Claude Code)
├── guard.py                   # Все правила: fs, git, sql, env, memory
├── hooks.json                 # Автоподключение при установке плагином
├── settings.example.json      # Ручное подключение без плагина
├── README.md                  # Установка, настройка, границы применимости
└── tests/test_guard.py        # 28 тестов, половина — на ложные срабатывания

skills/              # Чек-листы как вызываемые скиллы (слой Claude Code)
├── setup-agent-team/          # Сборка команды ролей под проект
├── tool-checklist/            # Перед вызовом инструмента
├── permission-checklist/      # Матрица разрешений роли
├── planning-checklist/        # Декомпозиция задачи
├── context-checklist/         # Передача работы и управление контекстом
└── implementation-path/       # Путь от задачи до коммита

.claude-plugin/      # Манифесты для установки плагином
├── plugin.json
└── marketplace.json

EXAMPLES.md          # Каждый принцип в виде «плохо → хорошо»
```

> `hooks/` — единственная папка репозитория, привязанная к Claude Code. Всё остальное провайдер-нейтрально. Для других платформ механика описана в `hooks/README.md`, идея переносится без изменений.

---

## Принципы одной строкой

1. **Команда агентов > суперагент** — специализация снижает ошибки зон ответственности
2. **Tier-декомпозиция** — размер задачи определяет pipeline, не наоборот
3. **R/D/W/P классы риска** — подтверждение только там, где оно нужно
4. **Бюджеты делегаций** — чекпоинт после N шагов, не бесконечная цепочка
5. **Триангуляция** — критичные решения проверяют 3 модели параллельно
6. **Persistent memory** — институциональное знание агента между сессиями
7. **Stop rules** — агент знает когда остановиться, не застревает в петлях
8. **Approval log** — привилегированные действия оставляют след
9. **Mechanical invariants** — повторяющиеся ошибки → hooks, не уговоры
10. **Eval-suite** — агентный слой тестируется так же, как продуктовый код

---

## Лицензия

MIT — см. [`LICENSE`](LICENSE).

---

## Благодарности

Эта методология выросла из практики построения команды агентов в реальных продуктовых проектах. Толчком и источником вдохновения стал репозиторий [DenisSergeevitch/agents-best-practices](https://github.com/DenisSergeevitch/agents-best-practices) — спасибо автору за то, что собрал и сформулировал базовые принципы работы с агентами в одном месте. Часть концепций (классы риска, чек-листы инструментов и разрешений, идея mechanical invariants) развита здесь применительно к **команде** агентов.

Освоены и интегрированы практики ещё двух источников:

- **[garrytan/gstack](https://github.com/garrytan/gstack)** — набор Гарри Тана (Y Combinator) на 23 инструмента для Claude Code как виртуальной инженерной команды. Подход «структурированный спринт Think → Plan → Build → Review → Test → Ship → Reflect» и идея параллельных независимых пайплайнов лежат в основе нашего `pm-orchestrator` и Tier-декомпозиции.
- **[multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)** — коллекция дисциплинарных skill'ов по наблюдениям Андрея Карпатого о типичных ошибках LLM при кодировании. Принципы **Think Before Coding** (обязательная консультация SIDE-модели перед Tier 3 — `reasoning-consultant`), **Simplicity First** (запрет premature abstraction в шаблонах dev-агентов), **Surgical Changes** (раздел «хирургические правки» в каждом dev-template), **Goal-Driven Execution** (поле `Done condition` в шаблоне плана Tier 3) — встроены в нашу методологию напрямую.

---

<p align="center"><em>Если применяешь методологию и нашёл что улучшить — PR приветствуется.</em></p>
