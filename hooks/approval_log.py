#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
approval_log.py — PostToolUse-хук: журнал привилегированных действий.

Машинная половина принципа 08. Человеческая — запись в CHANGELOG: что сделали,
почему и как откатить. Она отвечает на «почему», но появляется только если о
ней не забыли. Этот хук отвечает на «что и когда», зато не забывает никогда.
Разбор различия — в самом принципе, раздел «И отдельно — машинный след».

Дополняет guard.py, а не дублирует его: guard блокирует то, чего делать нельзя,
а этот хук записывает то, что делать можно, но нужно помнить — коммиты,
миграции, установки, обращения к базе.

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
from guard import basename, split_segments, strip_heredoc_bodies, strip_wrappers

DEFAULT_LOG = os.path.join(".claude", "approval-log.jsonl")

# Что попадает в журнал. Ключ — класс риска по principles/03, значение —
# распознаватели. Список намеренно короткий: журнал, куда пишется всё подряд,
# читать никто не станет, и он перестанет быть журналом.
#
# Метки — устойчивые идентификаторы, а не текст для человека, и потому не
# переводятся. Журнал это запись, а не сообщение: если метки зависели бы от
# языка, один файл после смены ABT_LANG содержал бы записи на двух языках,
# и по нему нельзя было бы ни искать, ни считать.
#
# Порядок значим: пишется первое совпадение. Более опасная форма команды
# обязана стоять выше общей, иначе запишется её классом — и журнал будет
# занижать риск ровно там, где точность и нужна.
DB_CLIENTS = r"(psql|mysql|mariadb|sqlite3|mongosh|clickhouse-client)"

BASH_PATTERNS = [
    ("W", "git-commit", r"\bgit\b.*\bcommit\b"),
    # Push, переписывающий или удаляющий чужую историю: P по principles/03,
    # а не W. Заблокированная попытка тоже попадает в журнал через
    # PostToolUseFailure — то есть без этой строки самое опасное действие
    # записывалось бы самым мягким классом.
    #
    # `--force-with-lease` сюда намеренно не входит: хук сам предлагает его
    # как безопасную замену и пропускает. Метить рекомендованную альтернативу
    # тем же классом, что и запрещённое действие, — та самая
    # самопротиворечивость, против которой написан principles/09.
    #
    # Формы перечислены не по написанию, а по смыслу: `+ветка` — тот же force,
    # `:ветка`, `--delete` и `-d` — удаление ветки на сервере. Короткие флаги
    # слипаются, как в guard.py: `-fu` и `-uf` — тоже force (аудит 1.26.0).
    ("P", "git-push-destructive",
     r"\bgit\b.*\bpush\b.*(--force(?!-with-lease)|--mirror|--delete|\s-d\b|\s-[A-Za-z]*f[A-Za-z]*\b|\s\+\S|\s:\S)"),
    ("W", "git-push", r"\bgit\b.*\bpush\b"),
    ("W", "git-tag", r"\bgit\b.*\btag\b"),
    ("W", "git-merge", r"\bgit\b.*\b(merge|rebase|cherry-pick)\b"),
    ("W", "db-migration", r"\b(alembic|flyway|knex|liquibase)\b|\bmanage\.py\s+migrate\b|\bmigrate\s+(up|deploy|latest)\b"),
    ("W", "package-install", r"\b(pip|pip3|npm|yarn|pnpm|poetry|uv)\b.*\b(install|add|remove|uninstall)\b"),
    # Класс даёт не запуск клиента, а команда внутри него: SELECT — это R.
    ("P", "db-write", r"\b" + DB_CLIENTS + r"\b[^|;&]*\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b"),
    ("R", "db-access", r"\b" + DB_CLIENTS + r"\b"),
    ("P", "permissions", r"\b(chmod|chown|icacls)\b"),
    ("P", "service-control", r"\b(systemctl|service|sc\.exe)\b"),
    ("P", "secret-read", r"\b(cat|less|more|head|tail|type)\b[^|;&]*\.env\b"),
]

# Команды, которые только ищут и печатают текст. Слова в их аргументах - образцы
# поиска и пути, а не действия: `grep -v alembic` не миграция, `grep "git push"` -
# не push. Без этого списка журнал метил такие команды в пяти проектах (1.27.0).
# find, xargs и awk сюда не входят: через -exec, xargs и system() они запускают
# другие команды. sed остаётся, хотя у GNU sed есть команда e: она редкость,
# а `sed -n` по файлу с alembic в пути - самый частый источник ложных записей.
TEXT_TOOLS = {"grep", "egrep", "fgrep", "rg", "ls", "echo", "printf", "sed",
              "wc", "sort", "uniq", "cut", "tr", "diff"}


def action_segments(command):
    """
    Сегменты команды, которые что-то делают, - строками.

    Разбор тот же, что в guard.py: тела heredoc - данные, их не выполнят;
    обёртки вроде sudo и env снимаются; сегменты TEXT_TOOLS пропускаются.
    """
    segments = []
    for tokens in split_segments(strip_heredoc_bodies(command)):
        tokens = strip_wrappers(tokens)
        if tokens and basename(tokens[0]) not in TEXT_TOOLS:
            segments.append(" ".join(tokens))
    return segments


# .claude/agents-best-teams.json - настройки защиты: правка расширяет зоны ролей,
# и хук открывает на неё окно подтверждения. Что хук останавливает, то журнал пишет.
SENSITIVE_PATH = re.compile(
    r"(^|/)(\.env|\.git/|\.claude/settings|\.claude/agents-best-teams\.json|id_rsa|\.pem$)", re.I)


def classify(data):
    """Возвращает (класс, что произошло, деталь) или None, если писать нечего."""
    tool = data.get("tool_name") or ""
    payload = data.get("tool_input") or {}

    if tool == "Bash":
        raw = payload.get("command") or ""
        command = re.sub(r"\s+", " ", raw).strip()
        if not command:
            return None
        segments = action_segments(raw)
        # Шаблоны снаружи, сегменты внутри: порядок шаблонов задаёт приоритет,
        # и `git push && git push --force` пишется классом P, а не W.
        for risk, what, pattern in BASH_PATTERNS:
            if any(re.search(pattern, segment, re.I) for segment in segments):
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

    # Корень проекта, а не текущий каталог: после `cd .claude/agents` журнал
    # появлялся в .claude/agents/.claude/. Порядок тот же, что в guard.py.
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    log_path = os.environ.get("AGENTS_APPROVAL_LOG") or DEFAULT_LOG
    if not os.path.isabs(log_path):
        log_path = os.path.join(project, log_path)
    use_project(project)

    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "risk": risk,
        "what": what,
        "detail": detail,
        # Удалось ли действие. Хук подключён к двум событиям: PostToolUse
        # приходит на успех, PostToolUseFailure — на провал. Без второго
        # журнал видел бы только удавшееся, а для аудита «попытался и не
        # смог» часто важнее, чем «сделал»: заблокированный force-push и
        # упавшая на середине миграция не оставляли бы следа вообще.
        "ok": data.get("hook_event_name") != "PostToolUseFailure",
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
