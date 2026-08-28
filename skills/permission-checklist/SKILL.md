---
name: permission-checklist
description: Permission matrix for an agent team - which tools, permissionMode, maxTurns, effort and memory each role is entitled to. Use when adding a new role, when reviewing an existing one, and after an incident where an agent stepped outside its area.
---

# Матрица разрешений агентов

Принцип: каждая роль получает **только те инструменты, которые нужны для её работы**. Не «на всякий случай», не «может пригодиться». Ограниченный набор инструментов сам по себе документирует роль.

Проверяй при добавлении роли:

1. Список `tools` — только необходимое, и явно описано, чего у роли НЕТ и почему.
2. `permissionMode` — `acceptEdits` только если обычные правки роли относятся к классу D.
3. `maxTurns` — осознанный бюджет шагов, а не «сколько не жалко».
4. `effort` — `high` только там, где цена ошибки выше цены прогона.
5. `memory` — если включаешь, проверь, что Write и Edit роли разрешены. Иначе сначала защитный хук.

**Важно про два ограничения, которых конфигурация не даёт:**

- `permissionMode` управляет только правками файлов и файловыми командами. Класс W (миграции, `git commit`) и класс P (force-push, DDL, `.env`) им не закрываются — для них нужны правила разрешений и хуки.
- Поле `memory` выдаёт роли Read, Write и Edit в обход списка `tools`. Для роли, лишённой их намеренно, это снимает главное ограничение — работает только в паре с защитным хуком.

Полная матрица по всем ролям, разбор конфликта `memory` и чек-листы ревизии:
`${CLAUDE_PLUGIN_ROOT}/checklists/permission-checklist.md`

Если методология установлена копированием — файл лежит в `checklists/permission-checklist.md` репозитория.
