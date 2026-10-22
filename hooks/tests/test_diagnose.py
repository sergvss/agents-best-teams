#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты скилла diagnose-hooks: разбор решений хуков по транскрипту.

Транскрипты синтетические, в формате Claude Code: вызовы в message.content
(tool_use / tool_result), решения хуков во вложениях hook_success. Скрипт
запускается настоящим процессом с --session, как его вызывает агент.

Запуск:
    python -X utf8 -m unittest hooks.tests.test_diagnose
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir,
                      "skills", "diagnose-hooks", "diagnose.py")

os.environ.setdefault("ABT_LANG", "en")


def tool_use(uid, command, cwd):
    return {"cwd": cwd, "timestamp": "2026-10-22T08:36:59.000Z", "message": {"content": [
        {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": command}}]}}


def hook(uid, command_field, stdout="", stderr="", exit_code=0, event="PreToolUse", name="PreToolUse:Bash"):
    return {"timestamp": "2026-10-22T08:37:00.000Z", "attachment": {
        "type": "hook_success", "hookName": name, "hookEvent": event, "toolUseID": uid,
        "command": command_field, "stdout": stdout, "stderr": stderr, "exitCode": exit_code}}


def decision(kind, reason):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": kind,
                                              "permissionDecisionReason": reason}})


def result(uid, text, is_error=False):
    return {"message": {"content": [{"type": "tool_result", "tool_use_id": uid,
                                     "content": text, "is_error": is_error}]}}


class DiagnoseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="abt-diag-")
        self.session = os.path.join(self.tmp, "1111-session.jsonl")
        cwd = self.tmp.replace("\\", "/")
        lines = [
            {"type": "queue-operation"},                                                      # 1
            tool_use("t1", "git add app.py && git commit -m fix", cwd),                       # 2
            hook("t1", "python ${CLAUDE_PLUGIN_ROOT}/hooks/review_gate.py",
                 decision("ask", "CONFIRM [W/Write]: committing code without a review - 1: app.py")),  # 3
            result("t1", "[main abc123] fix"),                                                 # 4
            tool_use("t2", "rm -rf /", cwd),                                                   # 5
            hook("t2", "Checking the command against the protective hooks",
                 decision("deny", "BLOCKED [P/Privileged]: rm -rf on /")),                     # 6
            result("t2", "BLOCKED [P/Privileged]: rm -rf on /", is_error=True),                # 7
            tool_use("t3", "ls", cwd),                                                         # 8
            hook("t3", "C:/tools/other-hook.exe claude", "{}"),                                # 9 чужой хук
            hook("t3", "python ${CLAUDE_PLUGIN_ROOT}/hooks/guard.py", ""),                     # 10 пустой ответ
            hook("t4", "python ${CLAUDE_PLUGIN_ROOT}/hooks/verify.py", stderr="NOT FINISHED: 2 tests failed",
                 exit_code=2, event="Stop", name="Stop"),                                      # 11
            tool_use("t5", "git commit -am x", cwd),                                           # 12
            hook("t5", "python ${CLAUDE_PLUGIN_ROOT}/hooks/review_gate.py",
                 decision("ask", "CONFIRM [W/Write]: a commit the review gate cannot match")),  # 13
            result("t5", "The user doesn't want to proceed with this tool use.", is_error=True),  # 14
        ]
        with open(self.session, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "--session", self.session] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8")

    def test_every_decision_is_cited_with_file_and_line(self):
        out = self.run_script()
        self.assertIn("решений хуков: 4", out)
        for line in (3, 6, 11, 13):
            self.assertIn("1111-session.jsonl:{}".format(line), out)

    def test_foreign_hooks_and_empty_answers_are_not_findings(self):
        out = self.run_script()
        self.assertNotIn(":9\n", out)
        self.assertNotIn(":10\n", out)
        self.assertNotIn("other-hook", out)

    def test_decisions_hooks_and_outcomes(self):
        out = self.run_script()
        self.assertIn("ОКНО - шлюз ревью (review_gate.py)", out)
        self.assertIn("ОТКАЗ - защитные правила (guard.py)", out)          # узнан по строке статуса
        self.assertIn("БЛОКИРОВКА - проверка тестов при завершении (verify.py)", out)
        self.assertIn("исход:   выполнено", out)
        self.assertIn("исход:   человек отклонил", out)
        self.assertIn("2 tests failed", out)

    def test_grep_narrows_to_matching_calls(self):
        out = self.run_script("--grep", "rm -rf")
        self.assertIn("решений хуков: 1", out)
        self.assertIn("ОТКАЗ", out)

    def test_replay_runs_the_current_hooks(self):
        out = self.run_script("--grep", "rm -rf", "--replay")
        self.assertIn("сейчас: отказ", out)
        # Шлюз ревью без включателя в проекте сейчас молчит.
        out = self.run_script("--grep", "git add app.py", "--replay")
        self.assertIn("сейчас: окна нет", out)

    def test_subagent_transcripts_are_included(self):
        sub = os.path.join(self.session[:-len(".jsonl")], "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-1.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(tool_use("s1", "git push --force", self.tmp)) + "\n")
            fh.write(json.dumps(hook("s1", "python ${CLAUDE_PLUGIN_ROOT}/hooks/guard.py",
                                     decision("ask", "CONFIRM [P/Privileged]: force push"))) + "\n")
        out = self.run_script()
        self.assertIn("решений хуков: 5", out)
        self.assertIn("agent-1.jsonl:2", out)

    def test_missing_project_is_reported_not_crashed(self):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "--project", os.path.join(self.tmp, "nowhere")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Транскриптов не найдено", proc.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
