---
name: context-checklist
description: Context management in an agent team - what to pass into a subtask, what to return from it, what to write to memory and what never to write. Use when handing work to another agent, when preparing a result report, and when the context is approaching its limit.
---

# Чек-лист управления контекстом

Контекст — расходуемый ресурс. Агент, который тащит в него всё подряд, теряет способность работать раньше, чем закончит задачу.

**Что передавать в подзадачу:**

1. Задачу и критерий готовности — обязательно.
2. Ограничения зоны: что трогать нельзя.
3. Только те факты, которых нет в коде. Всё, что исполнитель может прочитать сам, передавать не нужно.

**Что возвращать из подзадачи:**

1. Результат и статус проверки.
2. Что не получилось и почему.
3. Не возвращать простыни вывода инструментов — только выводы.

**Что писать в память:** то, что дорого было узнать и глупо узнавать снова. Не то, что есть в коде, в `git log` или в README.

Полный текст с шаблоном передачи работы, признаками переполнения контекста и правилами сжатия:
`${CLAUDE_PLUGIN_ROOT}/checklists/context-checklist.md`

Если методология установлена копированием — файл лежит в `checklists/context-checklist.md` репозитория.
