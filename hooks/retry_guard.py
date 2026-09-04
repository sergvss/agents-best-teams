#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry_guard.py — механическая реализация правила 1 из principles/07-stop-rules.md:
после трёх неудачных попыток одной операции — остановка.

Работает в двух режимах на двух событиях:

    --record   на PostToolUseFailure — считает неудачи
    (без флага) на PreToolUse       — блокирует четвёртую слепую попытку

Ключевая тонкость. Наивный счётчик неудач запрещал бы нормальную работу:
прогон тестов, падающий три раза, пока агент чинит код между прогонами, — это
не петля, а ровно тот цикл, который методология и поощряет. Поэтому счётчик
сбрасывается, если между попытками рабочая копия менялась. Блокируется только
повтор **вслепую** — та же команда при том же состоянии файлов.

Вход:  JSON хука на stdin.
Выход: на PreToolUse — пусто (разрешить) или JSON с deny.
       На PostToolUseFailure — всегда пусто, это событие ничего не решает.

Зависимости: только стандартная библиотека Python 3.8+.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import msg, use_project

DEFAULT_LIMIT = 3
STATE_TTL_S = 3600


def state_path(session_id):
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), "abt-retry-{}.json".format(digest))


def load_state(path):
    try:
        if time.time() - os.path.getmtime(path) > STATE_TTL_S:
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass  # Счётчик — удобство, а не корректность.


def command_key(command):
    """Ключ команды: пробелы схлопнуты, чтобы переформатирование не считалось новой попыткой."""
    return hashlib.sha256(re.sub(r"\s+", " ", command).strip().encode("utf-8")).hexdigest()[:16]


def workspace_fingerprint(cwd):
    """
    Отпечаток рабочей копии. Меняется, когда меняются файлы, — этого достаточно,
    чтобы отличить «чиню и пробую снова» от «повторяю вслепую».

    Считается только когда счётчик уже подошёл к пределу, чтобы не платить
    запуском git за каждый вызов инструмента.
    """
    parts = []
    # porcelain ловит появление, удаление и неотслеживаемые файлы, но не
    # содержимое: у уже изменённого файла строка « M path» остаётся той же
    # после любой следующей правки. Одного его недостаточно — агент, который
    # чинит тот же файл между попытками, выглядел бы повторяющим вслепую.
    # diff добавляет содержимое отслеживаемых правок.
    for command in (["git", "status", "--porcelain"], ["git", "diff", "HEAD"]):
        try:
            result = subprocess.run(
                command, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        # git diff HEAD падает в репозитории без единого коммита — тогда
        # обходимся porcelain: он там и так покажет всё как неотслеживаемое.
        if result.returncode == 0:
            parts.append(result.stdout)
        elif command[1] == "status":
            return ""
    # Неотслеживаемые файлы не попадают ни в porcelain по содержимому
    # (там только «?? path»), ни в diff — а правят их не реже прочих.
    # Читать их целиком незачем: размер и время правки меняются вместе с
    # содержимым и стоят один stat.
    if parts:
        for line in parts[0].decode("utf-8", "replace").splitlines():
            if not line.startswith("??"):
                continue
            target = os.path.join(cwd, line[2:].strip().strip('"'))
            try:
                stat = os.stat(target)
            except OSError:
                continue
            parts.append("{}:{}:{}".format(target, stat.st_size,
                                           stat.st_mtime_ns).encode("utf-8"))

    if not parts:
        return ""
    return hashlib.sha256(b"\n".join(parts)).hexdigest()[:16]


def read_input():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=msg("cli.retry_description"))
    parser.add_argument("--record", action="store_true",
                        help=msg("cli.retry_record"))
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=msg("cli.retry_limit"))
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        if exc.code not in (0, None):
            sys.stderr.write(msg("cli.retry_bad_args"))
            return 0
        raise

    data = read_input()
    if data is None or (data.get("tool_name") or "") != "Bash":
        return 0

    command = (data.get("tool_input") or {}).get("command") or ""
    if not command.strip():
        return 0

    session = str(data.get("session_id") or "no-session")
    cwd = data.get("cwd") or os.getcwd()
    use_project(cwd)
    path = state_path(session)
    state = load_state(path)
    key = command_key(command)

    if args.record:
        entry = state.get(key) or {"count": 0}
        entry["count"] += 1
        entry["fingerprint"] = workspace_fingerprint(cwd) if entry["count"] >= args.limit - 1 else ""
        state[key] = entry
        save_state(path, state)
        return 0

    entry = state.get(key)
    if not entry or entry.get("count", 0) < args.limit:
        return 0

    # Между попытками что-то менялось — это не слепой повтор, счётчик обнуляем.
    if entry.get("fingerprint") and workspace_fingerprint(cwd) != entry["fingerprint"]:
        state.pop(key, None)
        save_state(path, state)
        return 0

    state.pop(key, None)   # Блокируем один раз: решение принимает человек, а не счётчик.
    save_state(path, state)

    reason = msg(
        "retry.blocked",
        n=entry.get("count", args.limit),
        cmd=re.sub(r"\s+", " ", command)[:200],
    )

    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
