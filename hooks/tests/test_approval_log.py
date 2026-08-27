#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты PostToolUse-хука журнала привилегированных действий.

Запуск:
    python -X utf8 hooks/tests/test_approval_log.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "approval_log.py")


def run_hook(payload, cwd):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", HOOK],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
    )
    return proc


def entries(cwd):
    path = os.path.join(cwd, ".claude", "approval-log.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class ApprovalLogTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def log(self, tool, payload, agent=""):
        data = {"hook_event_name": "PostToolUse", "tool_name": tool,
                "tool_input": payload, "cwd": self.cwd, "session_id": "abcdef1234"}
        if agent:
            data["agent_type"] = agent
        proc = run_hook(data, self.cwd)
        self.assertEqual(proc.returncode, 0, "PostToolUse-хук не имеет права падать")
        return entries(self.cwd)


class TestWhatGetsLogged(ApprovalLogTestCase):
    def test_privileged_bash_actions_are_recorded(self):
        for command, risk in [
            ("git commit -m 'fix'", "W"),
            ("git push origin main", "W"),
            ("alembic upgrade head", "W"),
            ("pip install requests", "W"),
            ("psql -c 'SELECT 1'", "P"),
            ("chmod 600 key.pem", "P"),
            ("cat .env", "P"),
        ]:
            with self.subTest(command=command):
                self.setUp()
                found = self.log("Bash", {"command": command})
                self.assertEqual(len(found), 1, "действие не попало в журнал: " + command)
                self.assertEqual(found[0]["risk"], risk)
                self.assertIn(command.split()[0], found[0]["detail"])

    def test_sensitive_file_edits_are_recorded(self):
        found = self.log("Write", {"file_path": "config/.env"})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["risk"], "P")

    def test_role_is_recorded_when_action_came_from_one(self):
        found = self.log("Bash", {"command": "git commit -m x"}, agent="devops")
        self.assertEqual(found[0]["agent"], "devops")
        # Пустая роль означает главный поток, а не потерянные данные.
        self.setUp()
        found = self.log("Bash", {"command": "git commit -m x"})
        self.assertEqual(found[0]["agent"], "")


class TestWhatStaysOut(ApprovalLogTestCase):
    def test_routine_actions_do_not_pollute_the_log(self):
        # Журнал, куда пишется всё подряд, читать никто не станет.
        for command in [
            "ls -la", "git status", "git diff", "cat README.md",
            "python -m unittest discover", "grep -rn TODO src/", "npm test",
        ]:
            with self.subTest(command=command):
                self.setUp()
                self.assertEqual(self.log("Bash", {"command": command}), [])

    def test_ordinary_file_edits_stay_out(self):
        for path in ["src/app.py", "README.md", "client/game/step.js"]:
            with self.subTest(path=path):
                self.setUp()
                self.assertEqual(self.log("Edit", {"file_path": path}), [])

    def test_reads_are_not_actions(self):
        self.assertEqual(self.log("Read", {"file_path": "src/app.py"}), [])


class TestContract(ApprovalLogTestCase):
    def test_appends_rather_than_overwrites(self):
        self.log("Bash", {"command": "git commit -m first"})
        self.log("Bash", {"command": "git push"})
        self.assertEqual(len(entries(self.cwd)), 2, "журнал обязан дописываться")

    def test_malformed_input_does_not_break_anything(self):
        for raw in [b"", b"   ", b"not json"]:
            with self.subTest(raw=raw):
                proc = subprocess.run(
                    [sys.executable, "-X", "utf8", HOOK],
                    input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.cwd,
                )
                self.assertEqual(proc.returncode, 0)

    def test_unwritable_log_does_not_break_the_session(self):
        # Журнал — удобство, а не корректность: сломать работу он не вправе.
        data = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}, "cwd": self.cwd}
        # Файл на месте каталога: makedirs упрётся в него и бросит OSError.
        blocker = os.path.join(self.cwd, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("не каталог")
        env = dict(os.environ)
        env["AGENTS_APPROVAL_LOG"] = os.path.join(blocker, "log.jsonl")
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", HOOK],
            input=json.dumps(data).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.cwd, env=env,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
