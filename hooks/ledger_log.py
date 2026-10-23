#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger_log.py — PostToolUse-хук: журнал вызовов ролей рядом с ledger длинной задачи.

Ledger (.agent-ledger/<задача>.md, шаблон pm-orchestrator) переживает обрыв
сессии, только если строки в нём есть. Правило «строка после каждой делегации»
держалось на памяти оркестратора: оборвись сессия между вызовом роли и
записью — и продолжающий не узнает, что роль уже отработала. Этот хук пишет
факт вызова сам: время, роль, поручение, исход. Результат и выводы по-прежнему
пишет оркестратор в «Шаги» — хук их не знает и не придумывает.

Пишет в отдельный файл .agent-ledger/calls.md и только дописывает в конец.
Первая версия добавляла раздел в сам ledger, и живой прогон показал: оркестратор
переписывает ledger целиком, и строки хука пропадают. В свой файл, который
никто не переписывает, - надёжно, и одновременное завершение двух ролей не
теряет строку.

Идея — из obra/superpowers: там строку ledger пишет скрипт task-done, а не
память агента.

Вход:  JSON хука на stdin (PostToolUse или PostToolUseFailure инструмента Agent).
Выход: ничего. Нет ledger в .agent-ledger/ — хук молчит: ledger заводит
       оркестратор, хук его не создаёт. Код возврата всегда 0.

Зависимости: только стандартная библиотека Python 3.8+.
"""

import json
import os
import sys
import time

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import msg, use_project

LEDGER_DIR = ".agent-ledger"
CALLS = "calls.md"


def has_ledger(project):
    """Есть ли ledger задачи - любой .md в каталоге, кроме самого журнала вызовов."""
    # listdir, а не glob: путь проекта с [ или ] glob понял бы как шаблон (триангуляция 1.32.0)
    try:
        names = os.listdir(os.path.join(project, LEDGER_DIR))
    except OSError:
        return False
    return any(name.endswith(".md") and name != CALLS for name in names)


def status_of(data):
    """Исход вызова словами: завершено, сбой или запущено в фоне."""
    if data.get("hook_event_name") == "PostToolUseFailure":
        return msg("ledger.failed")
    response = data.get("tool_response")
    response = response if isinstance(response, dict) else {}
    status = response.get("status") or ""
    if status == "completed":
        text = msg("ledger.done")
        duration = response.get("totalDurationMs")
        if isinstance(duration, (int, float)):
            text += msg("ledger.duration", seconds=int(duration / 1000))
        return text
    if (data.get("tool_input") or {}).get("run_in_background"):
        return msg("ledger.background")
    return status or msg("ledger.done")


def entry(data):
    """Строка журнала: дата и время, роль, поручение, исход."""
    tool_input = data.get("tool_input") or {}
    role = tool_input.get("subagent_type") or "general-purpose"
    # Поручение - в одну строку и коротко: полный текст лежит в транскрипте
    task = " ".join((tool_input.get("description") or "").split())[:120]
    return "- {} {} — {} — {}\n".format(time.strftime("%Y-%m-%d %H:%M"), role, task, status_of(data))


def append(project, line):
    """Дописывает строку в журнал вызовов, создавая его с заголовком при первом вызове."""
    path = os.path.join(project, LEDGER_DIR, CALLS)
    new = not os.path.exists(path)
    # Режим "a": дописывание без чтения - две роли, завершившиеся разом, строк не теряют
    # Заголовок и строка - одной записью, чтобы не перемешаться с соседним процессом
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write((msg("ledger.header") + "\n\n" if new else "") + line)


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    if data.get("tool_name") not in ("Agent", "Task"):
        return 0
    # Корень проекта, а не текущий каталог - как в guard.py и approval_log.py
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    use_project(project)
    if not has_ledger(project):
        return 0
    append(project, entry(data))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - журнал не имеет права ломать работу
        sys.stderr.write("ledger_log.py: {}: {}\n".format(type(error).__name__, error))
        sys.exit(0)
