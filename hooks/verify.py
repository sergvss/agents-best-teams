#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py — Stop-хук: не даёт завершить работу с падающими тестами.

Механическая реализация условия готовности из principles/10-eval-suite.md и
принципа «цель проверяема» из checklists/implementation-path.md. Пока условие
живёт только в тексте промпта, оно соблюдается по настроению; хук делает его
обязательным.

Вход:  JSON хука на stdin (session_id, cwd).
Выход: exit 0 — можно завершать; exit 2 — нельзя, stderr уходит агенту как
       объяснение; exit 1 — ошибка конфигурации, завершению не мешает.

Зависимости: только стандартная библиотека Python 3.8+.

Пример подключения — hooks/settings.example.json, секция Stop.

Важное ограничение: хук не может заставить тесты пройти. Его задача в том,
чтобы «закончил с падающими тестами» нельзя было сделать молча.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import msg, use_project

DEFAULT_MAX_BLOCKS = 2
DEFAULT_TIMEOUT_S = 300
COUNTER_TTL_S = 3600


def state_path(session_id):
    """Счётчик блокировок этой сессии. Файл, а не память: хук живёт один вызов."""
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), "abt-verify-{}.count".format(digest))


def read_blocks(path):
    """
    Счётчик протухает через час. Возобновлённая сессия сохраняет прежний
    session_id, и без срока годности залежавшийся счётчик навсегда отключил бы
    блокировку в ней.
    """
    try:
        if time.time() - os.path.getmtime(path) > COUNTER_TTL_S:
            return 0
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_blocks(path, value):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(value))
    except OSError:
        pass  # Счётчик — удобство, а не корректность: не смогли, значит не смогли.


def has_uncommitted_changes(cwd):
    """
    True, если в рабочей копии есть изменения. Если это не git-репозиторий
    или git недоступен — True: лучше лишний раз прогнать тесты, чем пропустить.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=msg("cli.verify_description"))
    parser.add_argument("--command", help=msg("cli.verify_command"))
    parser.add_argument("--max-blocks", type=int, default=DEFAULT_MAX_BLOCKS,
                        help=msg("cli.verify_max_blocks"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                        help=msg("cli.verify_timeout"))
    parser.add_argument("--changed-only", action="store_true",
                        help=msg("cli.verify_changed_only"))
    # По умолчанию argparse завершает процесс кодом 2, а для Stop-хука это
    # «блокировать». Опечатка в конфигурации заперла бы сессию навсегда.
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        if exc.code not in (0, None):
            sys.stderr.write(msg("verify.bad_args"))
            return 1
        raise

    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    data = {}
    if raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            pass

    # Ошибка конфигурации не должна запирать сессию: для Stop-хука блокировка
    # дороже пропуска, поэтому здесь fail-open, в отличие от guard.py.
    if not args.command:
        sys.stderr.write(msg("verify.no_command"))
        return 1

    cwd = data.get("cwd") or os.getcwd()
    use_project(cwd)
    session_id = str(data.get("session_id") or "no-session")

    # Пропуск при чистом дереве — опция, а не поведение по умолчанию.
    # Штатный процесс заканчивается коммитом, после которого дерево чистое:
    # с пропуском по умолчанию гейт отключался бы в самом обычном сценарии.
    if args.changed_only and not has_uncommitted_changes(cwd):
        return 0

    counter = state_path(session_id)
    timed_out = False

    try:
        result = subprocess.run(
            args.command, cwd=cwd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=args.timeout,
        )
        output = result.stdout.decode("utf-8", "replace").strip()
        failed = result.returncode != 0
    except subprocess.TimeoutExpired:
        # Таймаут — это непройденная проверка, а не ошибка конфигурации.
        # Разрешать завершение здесь означало бы, что зависшие тесты снимают гейт.
        timed_out = True
        failed = True
        output = msg("verify.timeout", timeout=args.timeout)
    except OSError as exc:
        sys.stderr.write(msg("verify.launch_failed", error=exc))
        return 1

    if not failed:
        write_blocks(counter, 0)
        return 0

    tail = "\n".join(output.splitlines()[-25:])
    if timed_out:
        tail += msg("verify.slow_hint")
    blocks = read_blocks(counter) + 1
    write_blocks(counter, blocks)

    if blocks > args.max_blocks:
        # Бесконечно блокировать нельзя: агент может быть не в состоянии починить,
        # и сессия окажется заперта. Но уйти молча тоже нельзя.
        sys.stderr.write(msg("verify.giving_up", blocks=blocks - 1, tail=tail))
        return 0

    sys.stderr.write(msg("verify.not_done", command=args.command, tail=tail))
    return 2


if __name__ == "__main__":
    sys.exit(main())
