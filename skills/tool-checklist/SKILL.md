---
name: tool-checklist
description: Use right before running a command that deletes or changes data or files in bulk - SQL DELETE, UPDATE or DROP, rm -r, find -delete, a mass sed, a migration - and before a command whose output could be huge. Not for ordinary reads or a single-file edit.
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
