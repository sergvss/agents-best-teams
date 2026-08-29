#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session_start.py — SessionStart-хук: предложить развернуть команду ролей
и выбрать язык сообщений, если он ещё не выбран.

Плагин ставит хуки и скиллы, но не ставит роли: в шаблонах плейсхолдеры под
конкретный проект, и подставить их может только человек или скилл сборки.
Из-за этого после установки легко решить, что «ничего не включилось».

Этот хук закрывает разрыв: при первом запуске в проекте, где команды ещё нет,
он вкладывает в контекст короткую подсказку, и Claude сам предлагает сборку.

Вход:  JSON хука на stdin.
Выход: пусто, если команда уже развёрнута или подсказка отключена;
       JSON с additionalContext, если стоит предложить сборку.

Молчание в настроенном проекте — не оптимизация, а требование: подсказка,
которая повторяется каждую сессию, тратит контекст и приучает её игнорировать.

Зависимости: только стандартная библиотека Python 3.8+.
"""

import json
import os
import re
import sys

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import LANG_FILE, SUPPORTED, msg, use_project

# Файл-маркер: пользователь решил, что подсказка не нужна.
OPT_OUT = os.path.join(".claude", ".no-team-setup-prompt")

# Имена ролей методологии. Наличие любой из них означает, что команда уже есть.
ROLE_NAMES = {
    "pm-orchestrator", "code-reviewer", "dev-backend", "dev-frontend",
    "dev-database", "qa-tester", "browser-tester", "devops",
    "docs-writer", "i18n-keeper", "local-sysops",
    "security-reviewer", "investigator", "design-reviewer", "scope-challenger",
    "finops-engineer", "unit-economics-analyst", "investment-analyst",
    "vendor-auditor",
}

def language_is_chosen(cwd):
    """
    True, если язык сообщений уже задан явно.

    Спрашивать нужно один раз: вопрос, который повторяется каждую сессию,
    приучают игнорировать ровно так же, как и подсказку про сборку команды.
    """
    if (os.environ.get("ABT_LANG") or "").strip().lower()[:2] in SUPPORTED:
        return True
    try:
        with open(os.path.join(cwd, LANG_FILE), encoding="utf-8") as fh:
            return fh.read().strip().lower()[:2] in SUPPORTED
    except OSError:
        return False


def team_is_set_up(cwd):
    """
    True, если в проекте уже есть роли методологии.

    Проверяется имя во frontmatter, а не имя файла: роль могли переименовать
    при адаптации, а поле name остаётся тем, по которому её зовут.
    """
    agents_dir = os.path.join(cwd, ".claude", "agents")
    if not os.path.isdir(agents_dir):
        return False
    try:
        names = os.listdir(agents_dir)
    except OSError:
        return False

    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(agents_dir, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(2048)
        except OSError:
            continue
        match = re.search(r"^name:\s*([\w-]+)\s*$", head, re.M)
        if match and match.group(1) in ROLE_NAMES:
            return True
    return False


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    data = {}
    if raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            return 0

    cwd = data.get("cwd") or os.getcwd()
    use_project(cwd)

    if os.path.exists(os.path.join(cwd, OPT_OUT)):
        return 0

    # Два повода заговорить, и оба одноразовые. Язык идёт первым: пока он не
    # выбран, всё остальное человек читает на умолчании, которое ему могли
    # и не подобрать.
    parts = []
    if not language_is_chosen(cwd):
        parts.append(msg("session.language_prompt"))
    if not team_is_set_up(cwd):
        parts.append(msg("session.team_setup"))

    if not parts:
        return 0

    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n---\n\n".join(parts),
        }
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
