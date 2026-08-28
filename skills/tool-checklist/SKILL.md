---
name: tool-checklist
description: Check before calling a tool - is it needed at all, is it the right one, how large is the output, what is the risk class. Use before Bash with destructive potential, before bulk edits, before database queries, and whenever a tool's output could turn out to be enormous.
---

# Чек-лист вызова инструмента

Применяй **до** вызова, а не после разбора последствий.

Быстрая версия из четырёх вопросов:

1. **Нужен ли вызов?** Ответ уже может быть в контексте или в памяти агента.
2. **Тот ли инструмент?** Специализированный лучше универсального: Grep вместо `grep` через Bash, Read вместо `cat`.
3. **Какой объём вернётся?** Вывод на тысячи строк вытесняет из контекста то, ради чего работа и начиналась. Ограничивай заранее.
4. **Какой класс риска?** R и D выполняй сразу, W и P требуют подтверждения — см. классы риска в методологии.

Полный текст с таблицами лимитов вывода и разбором антипаттернов:
`${CLAUDE_PLUGIN_ROOT}/checklists/tool-checklist.md`

Если методология установлена не плагином, а копированием — тот же файл лежит в `checklists/tool-checklist.md` репозитория. Прочитай его, прежде чем следовать этому чек-листу в спорном случае.
