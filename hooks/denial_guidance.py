#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
denial_guidance.py — что делать агенту, когда нужное действие отклонено.

SessionStart и SubagentStart: вкладывает в контекст короткое правило поведения
после отказа.

Зачем. Текст отказа классификатора auto mode велит модели «STOP and explain to
the user», и агент заканчивает словами «выполните сами». По транскриптам вышло
иначе: если спросить через AskUserQuestion и получить «да», повтор того же
действия проходит. У субагентов AskUserQuestion нет вовсе, поэтому роль может
только остановиться — и правило учит её вернуть главному диалогу точную
просьбу, а не совет пользователю.

Почему отдельный хук, а не session_start.py. У того контракт — молчать в
настроенном проекте. Это правило нужно всегда, а ещё после сжатия контекста,
которое session_start.py пропускает намеренно, и в каждом субагенте.

Вход:  JSON хука на stdin.
Выход: JSON с additionalContext для SessionStart или SubagentStart.

Зависимости: только стандартная библиотека Python 3.8+.
"""

import json
import os
import sys

# Тексты для агента — в каталоге сообщений, см. messages.py.
from messages import msg, use_project

# Событие -> ключ текста. Другие события хук не обслуживает и молчит: ответ
# с чужим hookEventName Claude Code не примет.
GUIDANCE = {
    "SessionStart": "denial.main",
    "SubagentStart": "denial.subagent",
}


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        # Подсказка, а не защита: неразбираемый вход не повод ломать старт сессии.
        return 0

    event = data.get("hook_event_name") or ""
    if event not in GUIDANCE:
        return 0

    # Язык текста — как у остальных хуков: окружение, затем файл проекта.
    use_project(data.get("cwd") or os.getcwd())

    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": msg(GUIDANCE[event]),
        }
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
