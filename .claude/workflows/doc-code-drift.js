export const meta = {
  name: 'doc-code-drift',
  description: 'Ищет расхождения между тем, что документация обещает, и тем, что код делает - те классы, которые grep и тесты закрыть не могут',
  phases: [
    { title: 'Audit', detail: 'Одна зона на агента: сверка утверждений документа с кодом' },
    { title: 'Verify', detail: 'Состязательная перепроверка находок против репозитория' },
    { title: 'Synthesize', detail: 'Сведение в один отчёт с приоритетом' },
  ],
}

// Зоны заданы в скрипте, а не выясняются агентом: список меняется редко,
// а лишний агент на инвентаризацию стоит денег и добавляет случайность.
const AREAS = [
  {
    key: 'readme-en',
    doc: 'README.md',
    against: 'hooks/hooks.json, hooks/*.py, .claude-plugin/plugin.json, skills/',
    focus: 'таблица «What turns on when», раздел Quick start, обещания про то, что работает сразу после установки',
  },
  {
    key: 'readme-parity',
    doc: 'README.md и README.ru.md',
    against: 'друг друга',
    focus: 'расхождение по фактам между английской и русской версией: числа, шаги установки, что чем включается. Разделы, которых в английской версии нет намеренно (благодарности, «Когда команда нужна»), расхождением не считаются',
  },
  {
    key: 'hooks-readme',
    doc: 'hooks/README.md',
    against: 'hooks/guard.py, hooks/verify.py, hooks/retry_guard.py, hooks/approval_log.py, hooks/session_start.py, hooks/messages.py',
    focus: 'что именно блокируется, какие роли в матрице, формат записи журнала, порядок выбора языка, к каким событиям подключён каждый хук',
  },
  {
    key: 'install-docs',
    doc: 'docs/install.md и docs/quick-start.md',
    against: 'hooks/settings.example.json, hooks/hooks.json, состав каталога hooks/',
    focus: 'копируется ли всё, на что ссылается конфигурация; совпадают ли команды проверки с реальным поведением',
  },
  {
    key: 'permissions',
    doc: 'checklists/permission-checklist.md и templates/README.md',
    against: 'templates/*.md (frontmatter) и MEMORY_MATRIX в hooks/guard.py',
    focus: 'совпадают ли инструменты, permissionMode, maxTurns, effort и memory у каждой роли в трёх местах сразу',
  },
  {
    key: 'principles',
    doc: 'principles/*.md',
    against: 'друг друга и hooks/*.py',
    focus: 'противоречия между принципами и между принципом и хуком, который его исполняет: пороги, числа, названия правил, обещания вроде «оставляет след» или «останавливается после N попыток»',
  },
  {
    key: 'skills',
    doc: 'skills/*/SKILL.md',
    against: 'checklists/*.md и templates/',
    focus: 'ссылается ли скилл на существующие файлы и правила; совпадают ли шаги проверки внутри скилла с тем, что реально проверяемо',
  },
]

const FINDING_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'reality', 'where', 'severity'],
        properties: {
          claim: { type: 'string', description: 'что утверждает документация, дословно' },
          reality: { type: 'string', description: 'что на самом деле делает код' },
          where: { type: 'string', description: 'файл:строка документа и файл:строка кода' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
      },
    },
  },
}

phase('Audit')

const audited = await pipeline(AREAS, (area) =>
  agent(
    [
      'Ты сверяешь документацию с кодом в репозитории agents-best-teams.',
      '',
      `Документ: ${area.doc}`,
      `Сверять с: ${area.against}`,
      `На что смотреть: ${area.focus}`,
      '',
      'Ищи ТОЛЬКО смысловые расхождения: документ обещает одно, код делает другое.',
      'Битые ссылки, опечатки, форматирование и несовпадение чисел с диском уже',
      'закрыты автотестами - на них не отвлекайся.',
      '',
      'Каждую находку подтверждай чтением обоих файлов. Не нашёл расхождений -',
      'верни пустой список: это нормальный и ожидаемый результат.',
      'Не больше трёх находок, самых весомых.',
    ].join('\n'),
    { label: `audit:${area.key}`, phase: 'Audit', schema: FINDING_SCHEMA },
  ),
)

const findings = audited
  .filter(Boolean)
  .flatMap((r, i) => (r.findings || []).map((f) => ({ ...f, area: AREAS[i].key })))

if (findings.length === 0) {
  return { verdict: 'расхождений не найдено', findings: [] }
}

phase('Verify')

// Один состязательный проверяющий на все находки, а не по агенту на каждую:
// первый прогон должен быть дешёвым. Если находок станет много - резать
// на группы, форма скрипта это позволяет.
const verified = await agent(
  [
    'Ты проверяешь чужие находки о расхождении документации и кода, и твоя задача',
    'их опровергнуть, а не подтвердить. Для каждой открой оба файла сам.',
    '',
    'Находка неверна, если: документ говорит о другом контексте; расхождение',
    'намеренное и объяснено рядом; описано поведение другой версии; проверяющий',
    'домыслил то, чего в документе нет.',
    '',
    'Верни каждую находку с полем verdict: confirmed или rejected, и одной строкой',
    'почему. Отвергнутые не выбрасывай - они показывают, где документ читается',
    'неоднозначно.',
    '',
    JSON.stringify(findings, null, 2),
  ].join('\n'),
  {
    label: 'verify:all',
    phase: 'Verify',
    schema: {
      type: 'object',
      required: ['checked'],
      properties: {
        checked: {
          type: 'array',
          items: {
            type: 'object',
            required: ['claim', 'verdict', 'why'],
            properties: {
              claim: { type: 'string' },
              where: { type: 'string' },
              severity: { type: 'string' },
              verdict: { type: 'string', enum: ['confirmed', 'rejected'] },
              why: { type: 'string' },
            },
          },
        },
      },
    },
  },
)

phase('Synthesize')

const report = await agent(
  [
    'Сведи проверенные находки в короткий отчёт на русском языке.',
    '',
    'Порядок: сначала подтверждённые по убыванию весомости, потом отвергнутые',
    'одним абзацем. Для каждой подтверждённой скажи, что править - документ или код.',
    'Чаще правится документ, но не всегда: иногда обещание верное, а код отстал.',
    '',
    'Без вступлений и без пересказа задачи. Если подтверждённых нет - так и скажи',
    'одной строкой.',
    '',
    JSON.stringify(verified?.checked || [], null, 2),
  ].join('\n'),
  { label: 'synthesize', phase: 'Synthesize' },
)

return { report, confirmed: (verified?.checked || []).filter((c) => c.verdict === 'confirmed') }
