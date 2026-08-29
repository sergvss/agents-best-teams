#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
approval_log.py — PostToolUse-хук: журнал привилегированных действий.

Реализация принципа 08. Дополняет guard.py, а не дублирует его: guard
блокирует то, чего делать нельзя, а этот хук записывает то, что делать можно,
но нужно помнить — коммиты, миграции, установки, обращения к базе.

Смысл журнала не в наказании, а в том, что разбор инцидента занимает минуту
вместо часа: видно, кто, когда и что именно выполнил.

Вход:  JSON хука на stdin.
Выход: ничего. Хук на PostToolUse не может и не должен ничего блокировать,
       поэтому код возврата всегда 0, даже при собственной ошибке.

Зависимости: только стандартная библиотека Python 3.8+.

Формат записи — JSONL: строка на действие, дописывание безопасно при
параллельной работе нескольких ролей. Человекочитаемый вид — командой из
hooks/README.md.
"""

import json
import os
import re
import sys
import time

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import msg, use_project

DEFAULT_LOG = os.path.join(".claude", "approval-log.jsonl")

# Что попадает в журнал. Ключ — класс риска по principles/03, значение —
# распознаватели. Список намеренно короткий: журнал, куда пишется всё подряд,
# читать никто не станет, и он перестанет быть журналом.
#
# Метки — устойчивые идентификаторы, а не текст для человека, и потому не
# переводятся. Журнал это запись, а не сообщение: если метки зависели бы от
# языка, один файл после смены ABT_LANG содержал бы записи на двух языках,
# и по нему нельзя было бы ни искать, ни считать.
BASH_PATTERNS = [
    ("W", "git-commit", r"\bgit\b.*\bcommit\b"),
    ("W", "git-push", r"\bgit\b.*\bpush\b"),
    ("W", "git-tag", r"\bgit\b.*\btag\b"),
    ("W", "git-merge", r"\bgit\b.*\b(merge|rebase|cherry-pick)\b"),
    ("W", "db-migration", r"\b(alembic|flyway|knex|liquibase)\b|\bmanage\.py\s+migrate\b|\bmigrate\s+(up|deploy|latest)\b"),
    ("W", "package-install", r"\b(pip|pip3|npm|yarn|pnpm|poetry|uv)\b.*\b(install|add|remove|uninstall)\b"),
    ("P", "db-access", r"\b(psql|mysql|mariadb|sqlite3|mongosh|clickhouse-client)\b"),
    ("P", "permissions", r"\b(chmod|chown|icacls)\b"),
    ("P", "service-control", r"\b(systemctl|service|sc\.exe)\b"),
    ("P", "secret-read", r"\b(cat|less|more|head|tail|type)\b[^|;&]*\.env\b"),
]

SENSITIVE_PATH = re.compile(r"(^|/)(\.env|\.git/|\.claude/settings|id_rsa|\.pem$)", re.I)


def classify(data):
    """Возвращает (класс, что произошло, деталь) или None, если писать нечего."""
    tool = data.get("tool_name") or ""
    payload = data.get("tool_input") or {}

    if tool == "Bash":
        command = re.sub(r"\s+", " ", (payload.get("command") or "")).strip()
        if not command:
            return None
        for risk, what, pattern in BASH_PATTERNS:
            if re.search(pattern, command, re.I):
                return risk, what, command[:300]
        return None

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = payload.get("file_path") or payload.get("notebook_path") or ""
        if path and SENSITIVE_PATH.search(path.replace("\\", "/")):
            return "P", "sensitive-file-edit", path
        return None

    return None


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return 0
    try:
        data = json.loads(raw)
    except ValueError:
        return 0

    verdict = classify(data)
    if verdict is None:
        return 0
    risk, what, detail = verdict

    log_path = os.environ.get("AGENTS_APPROVAL_LOG") or DEFAULT_LOG
    if not os.path.isabs(log_path):
        log_path = os.path.join(data.get("cwd") or os.getcwd(), log_path)
    use_project(data.get("cwd") or "")

    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "risk": risk,
        "what": what,
        "detail": detail,
        # Пусто — значит действие выполнено в главном потоке, а не ролью.
        "agent": data.get("agent_type") or "",
        "tool": data.get("tool_name") or "",
        "session": (data.get("session_id") or "")[:8],
    }

    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Журнал не должен ломать работу: сообщаем и уходим без ошибки.
        sys.stderr.write(msg("cli.approval_log_write_failed", error=exc))

    return 0


if __name__ == "__main__":
    sys.exit(main())
