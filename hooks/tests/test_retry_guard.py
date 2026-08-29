#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты правила трёх попыток.

Половина тестов проверяет, что хук НЕ срабатывает: запрет нормального цикла
«починил — прогнал снова» был бы хуже, чем отсутствие правила вовсе.

Запуск:
    python -X utf8 hooks/tests/test_retry_guard.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# Язык сообщений закреплён явно, см. пояснение в test_guard.py.
os.environ.setdefault("ABT_LANG", "en")

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "retry_guard.py")


class RetryGuardTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        self.env = env
        for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(cmd, cwd=self.cwd, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.session = "s-{}-{}".format(os.getpid(), id(self))
        sys.path.insert(0, os.path.dirname(os.path.abspath(HOOK)))
        import retry_guard
        self.module = retry_guard

    def tearDown(self):
        try:
            os.remove(self.module.state_path(self.session))
        except OSError:
            pass
        self._tmp.cleanup()

    def call(self, command, record=False, limit=None):
        argv = [sys.executable, "-X", "utf8", HOOK]
        if record:
            argv.append("--record")
        if limit is not None:
            argv += ["--limit", str(limit)]
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "session_id": self.session, "cwd": self.cwd}
        proc = subprocess.run(argv, input=json.dumps(payload).encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, "хук не имеет права падать")
        out = proc.stdout.decode("utf-8").strip()
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None

    def touch(self, name="changed.txt", text="x"):
        with open(os.path.join(self.cwd, name), "w", encoding="utf-8") as fh:
            fh.write(text)


class TestBlindRetryIsStopped(RetryGuardTestCase):
    def test_fourth_identical_attempt_without_changes_is_blocked(self):
        cmd = "pytest -q"
        for _ in range(3):
            self.call(cmd, record=True)
        reason = self.call(cmd)
        self.assertIsNotNone(reason, "слепой повтор обязан блокироваться")
        self.assertIn("three-attempts rule", reason)
        self.assertIn(cmd, reason)

    def test_whitespace_does_not_create_a_new_attempt(self):
        # Переформатирование той же команды — не новая стратегия.
        for cmd in ["npm  test", "npm test", "npm   test"]:
            self.call(cmd, record=True)
        self.assertIsNotNone(self.call("npm test"))

    def test_block_happens_once_then_releases(self):
        # Блокировать бесконечно нельзя: решение принимает человек, а не счётчик.
        cmd = "make build"
        for _ in range(3):
            self.call(cmd, record=True)
        self.assertIsNotNone(self.call(cmd))
        self.assertIsNone(self.call(cmd), "после остановки решение за пользователем")


class TestNormalWorkIsNotBlocked(RetryGuardTestCase):
    def test_fix_and_retry_cycle_is_allowed(self):
        # Главный случай: тесты падают, агент чинит код, прогоняет снова.
        # Запретить это значит запретить работу.
        cmd = "pytest -q"
        for i in range(5):
            self.call(cmd, record=True)
            self.touch("fix{}.py".format(i))
            self.assertIsNone(self.call(cmd), "цикл починки заблокирован на шаге {}".format(i))

    def test_below_limit_passes(self):
        cmd = "pytest -q"
        for _ in range(2):
            self.call(cmd, record=True)
        self.assertIsNone(self.call(cmd))

    def test_different_commands_counted_separately(self):
        for _ in range(3):
            self.call("pytest -q", record=True)
        self.assertIsNone(self.call("npm test"), "чужой счётчик не должен мешать")

    def test_successful_command_never_counted(self):
        # Учёт идёт только на событии неудачи; проверок здесь нет вовсе.
        for _ in range(5):
            self.assertIsNone(self.call("git status"))

    def test_non_bash_tools_are_ignored(self):
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "a.py"},
                   "session_id": self.session, "cwd": self.cwd}
        proc = subprocess.run([sys.executable, "-X", "utf8", HOOK],
                              input=json.dumps(payload).encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "")


class TestContract(RetryGuardTestCase):
    def test_malformed_input_does_not_break_anything(self):
        for raw in [b"", b"   ", b"not json"]:
            with self.subTest(raw=raw):
                proc = subprocess.run([sys.executable, "-X", "utf8", HOOK],
                                      input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout.decode("utf-8").strip(), "")

    def test_bad_arguments_do_not_block_work(self):
        proc = subprocess.run([sys.executable, "-X", "utf8", HOOK, "--limit", "не-число"],
                              input=b"{}", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
