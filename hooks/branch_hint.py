#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
branch_hint.py — PreToolUse-хук: подсказка «ты правишь основную ветку».

Процесс веток (checklists/branch-workflow.md) держался только на описании скилла
start-branch: «используй, когда меняешь код на основной ветке». Но на какой
ветке сессия, модель не знает, пока не проверит, - и не проверяет. Живой прогон
1.34.0: при политике auto агент дважды правил прямо в main, не открыв ни одного
скилла. Факт, которого модель не видит, должен сообщать код.

При первой правке файла на основной ветке хук вкладывает агенту подсказку с
фактами: какая ветка и какая политика проекта (git config abt.branches). Это не
окно и не блокировка - человек ничего не видит, правка проходит. Подсказка
приходит вместе с результатом правки, а незакоммиченная правка переезжает в
новую ветку при `git switch -c`, так что основная остаётся чистой.

Один раз за сессию: вторая подсказка ничего не добавляет, а приучает её не читать.
Политика off - молчит: пользователь решил работать в основной.

Вход:  JSON хука на stdin (PreToolUse инструментов правки файлов и Bash).
Выход: JSON с additionalContext или ничего. Код возврата всегда 0.

Зависимости: только стандартная библиотека Python 3.8+.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import msg, use_project
# Тот же разбор записи, что у guard.py: перенаправления, sed -i, tee, cp, тела heredoc
from guard import split_segments, strip_heredoc_bodies, strip_wrappers, written_paths

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DEFAULT_BRANCHES = ("main", "master")
# Свои рабочие файлы агентов - память и ledger - к коду задачи не относятся
SKIPPED_PARTS = (".claude", ".agent-ledger")
STATE_DIR = os.path.join(tempfile.gettempdir(), "agents-best-teams", "branch-hint")


def git(project, *args):
    """Вывод git или пустая строка: нет git, не репозиторий - подсказки нет."""
    try:
        proc = subprocess.run(["git", "-C", project] + list(args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def default_branch(project):
    """Основная ветка: куда указывает origin/HEAD, иначе main или master."""
    remote = git(project, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote.startswith("origin/"):
        return remote[len("origin/"):]
    return None


def is_skipped(path):
    parts = re.split(r"[\\/]+", path)
    return any(part in SKIPPED_PARTS for part in parts)


def edited_paths(data):
    """
    Файлы, которые меняет вызов. Bash - тоже: живой прогон 1.34.0 показал, что в
    bypassPermissions агенты пишут файлы через cat > и sed -i, а не через Edit,
    и хук только на инструментах правки не сработал ни разу.
    """
    tool_input = data.get("tool_input") or {}
    if data.get("tool_name") in WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return [path] if path else []
    if data.get("tool_name") != "Bash":
        return []
    paths = []
    for tokens in split_segments(strip_heredoc_bodies(tool_input.get("command") or "")):
        paths.extend(t.strip("\"'") for t in written_paths(strip_wrappers(tokens)))
    return paths


def inside(project, path):
    """Путь внутри проекта: относительный - от корня проекта, абсолютный - по префиксу."""
    full = os.path.normcase(os.path.normpath(os.path.join(project, path)))
    base = os.path.normcase(os.path.normpath(project))
    return full == base or full.startswith(base.rstrip(os.sep) + os.sep)


def first_time(session):
    """True ровно один раз за сессию: маркер в каталоге состояния."""
    if not session:
        return True
    os.makedirs(STATE_DIR, exist_ok=True)
    marker = os.path.join(STATE_DIR, re.sub(r"[^\w-]", "_", session))
    if os.path.exists(marker):
        return False
    open(marker, "w").close()
    return True


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    use_project(project)
    # Только код задачи внутри проекта: свои файлы агентов и черновики снаружи
    # (временный каталог, соседние репозитории) подсказку не вызывают
    if not any(inside(project, p) and not is_skipped(p) for p in edited_paths(data) if p):
        return 0

    branch = git(project, "branch", "--show-current")
    if not branch:
        return 0
    default = default_branch(project)
    if branch != default and not (default is None and branch in DEFAULT_BRANCHES):
        return 0
    policy = git(project, "config", "--get", "abt.branches").lower()
    if policy == "off":
        return 0
    if not first_time(data.get("session_id") or ""):
        return 0

    key = "branch.hint_auto" if policy == "auto" else "branch.hint_ask"
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg(key, branch=branch)},
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - подсказка не имеет права ломать работу
        sys.stderr.write("branch_hint.py: {}: {}\n".format(type(error).__name__, error))
        sys.exit(0)
