#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnose.py — что решили хуки agents-best-teams в сессии, по транскрипту.

Отвечает на вопрос «откуда это окно / почему заблокировало» с доказательством:
каждое решение хука - с цитатой файл:строка транскрипта, командой, причиной и
исходом. С --replay та же команда прогоняется через хуки текущей версии плагина:
повторится ли окно сейчас.

Появился после недели, когда разбор окон пользователя делался руками - разовыми
скриптами по транскриптам. Так нашлись три ложных окна шлюза ревью и дыра с путями
Git Bash /c/... Идея скилла - из obra/superpowers (diagnosing-superpowers):
без цитаты нет находки.

Запуск:
    python diagnose.py                          # последняя сессия текущего проекта
    python diagnose.py --hours 24               # все сессии проекта за сутки
    python diagnose.py --session <id или путь>  # конкретная сессия
    python diagnose.py --grep "git commit"      # только вызовы с этим текстом
    python diagnose.py --replay                 # и что решили бы хуки сейчас
    python diagnose.py --project C:/projects/x  # другой проект

Только стандартная библиотека Python 3.8+. Транскрипты не меняет.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Каталог плагина: skills/diagnose-hooks/diagnose.py -> корень на два уровня выше.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# Хуки плагина узнаются по имени скрипта в команде хука или по строке статуса,
# которую Claude Code пишет вместо команды.
OUR_HOOKS = {
    "guard.py": "защитные правила (guard.py)",
    "review_gate.py": "шлюз ревью (review_gate.py)",
    "retry_guard.py": "правило трёх попыток (retry_guard.py)",
    "verify.py": "проверка тестов при завершении (verify.py)",
    "session_start.py": "подсказки при старте (session_start.py)",
    "denial_guidance.py": "правило после отказа (denial_guidance.py)",
    "approval_log.py": "журнал (approval_log.py)",
}
STATUS_HINTS = {
    "protective hooks": "guard.py",
    "review gate": "review_gate.py",
    "three-attempts": "retry_guard.py",
}


def project_dirs(project):
    """Каталоги транскриптов проекта: путь, где каждый не буквенно-цифровой символ - «-»."""
    encoded = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project)).lower()
    try:
        names = os.listdir(PROJECTS)
    except OSError:
        return []
    return [os.path.join(PROJECTS, n) for n in names if n.lower() == encoded]


def transcripts(dirs, session, hours):
    """Файлы сессий: главная и её субагенты."""
    if session and os.path.isfile(session):
        main = [session]
    else:
        main = []
        for d in dirs:
            main += glob.glob(os.path.join(d, "*.jsonl"))
        if session:
            main = [p for p in main if os.path.basename(p).startswith(session)]
        elif hours:
            since = time.time() - hours * 3600
            main = [p for p in main if os.path.getmtime(p) >= since]
        else:
            main = sorted(main, key=os.path.getmtime)[-1:]
    files = []
    for path in sorted(main, key=os.path.getmtime):
        files.append(path)
        files += sorted(glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "*.jsonl")))
    return files


def which_hook(attachment):
    text = (attachment.get("command") or "").lower()
    for script in OUR_HOOKS:
        if script in text:
            return script
    for hint, script in STATUS_HINTS.items():
        if hint in text:
            return script
    return None


def decision_of(attachment):
    """(решение, причина) из вывода хука: ask / deny / block (Stop) или None."""
    out = (attachment.get("stdout") or "").strip()
    if out:
        for line in reversed(out.splitlines()):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            block = data.get("hookSpecificOutput") or {}
            decision = block.get("permissionDecision")
            if decision in ("ask", "deny"):
                return decision, block.get("permissionDecisionReason") or ""
    err = (attachment.get("stderr") or "").strip()
    if attachment.get("exitCode") == 2:
        return "block", err
    if (attachment.get("hookEvent") or "").startswith("Stop") and err:
        return "warn", err
    return None, ""


def outcome_of(result):
    """Что стало с вызовом: по tool_result после решения хука."""
    if result is None:
        return "нет данных"
    text = result.get("text", "")
    if "doesn't want to proceed" in text or "rejected" in text.lower():
        return "человек отклонил"
    if result.get("is_error"):
        return "не выполнено (отказ или ошибка)"
    return "выполнено"


def scan(path):
    """Решения хуков в одном файле транскрипта, с номерами строк."""
    calls, results, found, cwd = {}, {}, [], None
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        cwd = entry.get("cwd") or cwd
        for item in (entry.get("message") or {}).get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                calls[item.get("id")] = (item.get("name"), item.get("input") or {}, cwd)
            elif item.get("type") == "tool_result":
                content = item.get("content")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                results[item.get("tool_use_id")] = {"text": str(content or ""), "is_error": item.get("is_error")}
        attachment = entry.get("attachment") or {}
        if not attachment.get("type", "").startswith("hook"):
            continue
        hook = which_hook(attachment)
        if not hook:
            continue
        decision, reason = decision_of(attachment)
        if not decision:
            continue
        found.append({
            "where": "{}:{}".format(path, number), "time": (entry.get("timestamp") or "")[:19],
            "event": attachment.get("hookName") or attachment.get("hookEvent") or "?",
            "hook": hook, "decision": decision, "reason": reason,
            "tool_use_id": attachment.get("toolUseID"),
        })
    for item in found:
        tool, tool_input, call_cwd = calls.get(item["tool_use_id"], (None, {}, cwd))
        item.update(tool=tool, input=tool_input, cwd=call_cwd,
                    outcome=outcome_of(results.get(item["tool_use_id"])))
    return found


def replay(item):
    """Решение хуков текущей версии на тот же вызов: окна нет / окно / отказ."""
    if item["hook"] not in ("guard.py", "review_gate.py") or not item.get("tool"):
        return "не воспроизводится этим скриптом"
    script = os.path.join(PLUGIN_ROOT, "hooks", item["hook"])
    if not os.path.isfile(script):
        return "нет {} рядом со скиллом".format(item["hook"])
    payload = {"tool_name": item["tool"], "tool_input": item["input"], "cwd": item["cwd"] or os.getcwd()}
    proc = subprocess.run([sys.executable, "-X", "utf8", script], input=json.dumps(payload).encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = proc.stdout.decode("utf-8", "replace").strip()
    if not out:
        return "сейчас: окна нет"
    block = json.loads(out).get("hookSpecificOutput") or {}
    first = (block.get("permissionDecisionReason") or "").splitlines()[0][:120]
    return "сейчас: {} - {}".format({"ask": "окно", "deny": "отказ"}.get(block.get("permissionDecision"), "?"), first)


NAMES = {"ask": "ОКНО", "deny": "ОТКАЗ", "block": "БЛОКИРОВКА", "warn": "ПРЕДУПРЕЖДЕНИЕ"}


def main():
    parser = argparse.ArgumentParser(description="Решения хуков agents-best-teams в сессии, по транскрипту")
    parser.add_argument("--project", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    parser.add_argument("--session", help="id сессии (начало имени файла) или путь к .jsonl")
    parser.add_argument("--hours", type=float, help="все сессии проекта за последние N часов")
    parser.add_argument("--grep", help="только вызовы, где команда или путь содержит этот текст")
    parser.add_argument("--replay", action="store_true", help="прогнать вызов через хуки текущей версии")
    args = parser.parse_args()

    files = transcripts(project_dirs(args.project), args.session, args.hours)
    if not files:
        print("Транскриптов не найдено для проекта {} в {}".format(args.project, PROJECTS))
        return 1
    items = []
    for path in files:
        items += scan(path)
    if args.grep:
        items = [i for i in items if args.grep in json.dumps(i["input"], ensure_ascii=False)]

    print("Файлов транскрипта: {}, решений хуков: {}".format(len(files), len(items)))
    counts = {}
    for item in items:
        key = (item["hook"], item["decision"])
        counts[key] = counts.get(key, 0) + 1
    for (hook, decision), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("  {:>3} x {} - {}".format(n, NAMES[decision], OUR_HOOKS[hook]))
    for index, item in enumerate(items, 1):
        command = item["input"].get("command") or item["input"].get("file_path") or json.dumps(item["input"])[:200]
        print("\n[{}] {}".format(index, item["where"]))
        print("    {} {} - {} - {}".format(item["time"], item["event"], NAMES[item["decision"]], OUR_HOOKS[item["hook"]]))
        print("    вызов:   {}".format(" ".join(str(command).split())[:220]))
        print("    причина: {}".format(" ".join(item["reason"].split())[:300]))
        print("    исход:   {}".format(item["outcome"]))
        if args.replay:
            print("    {}".format(replay(item)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
