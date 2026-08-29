#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты Stop-хука verify.py. Как и с guard.py, хук вызывается настоящим
процессом — через stdin и коды возврата, ровно так же, как его зовёт Claude Code.

Запуск:
    python -X utf8 hooks/tests/test_verify.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

VERIFY = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "verify.py")

# Язык сообщений закреплён явно, см. пояснение в test_guard.py.
os.environ.setdefault("ABT_LANG", "en")
REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

PASSING = "{} -c \"import sys; sys.exit(0)\"".format(sys.executable)
FAILING = "{} -c \"print('2 tests failed'); import sys; sys.exit(1)\"".format(sys.executable)


def run_verify(session, command=None, cwd=REPO, extra=None):
    """Возвращает (код возврата, stderr)."""
    argv = [sys.executable, "-X", "utf8", VERIFY]
    if command is not None:
        argv += ["--command", command]
    argv += extra or []
    payload = {"hook_event_name": "Stop", "session_id": session, "cwd": cwd}
    proc = subprocess.run(
        argv, input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return proc.returncode, proc.stderr.decode("utf-8", "replace")


class TestVerifyGate(unittest.TestCase):
    def setUp(self):
        # Счётчик блокировок живёт в файле и переживает процесс, поэтому
        # каждому тесту нужен свой session_id и уборка за собой — иначе
        # прогоны начинают влиять друг на друга.
        sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(VERIFY))))
        import verify  # noqa: E402
        self.verify = verify
        self._sessions = []

    def tearDown(self):
        for session in self._sessions:
            try:
                os.remove(self.verify.state_path(session))
            except OSError:
                pass

    def session(self, name):
        unique = "{}-{}-{}".format(name, os.getpid(), id(self))
        self._sessions.append(unique)
        return unique

    def test_missing_command_does_not_lock_the_session(self):
        # Для Stop-хука блокировка дороже пропуска: ошибка конфигурации
        # обязана быть заметной, но не запирать сессию.
        code, err = run_verify(self.session("cfg"), command=None)
        self.assertEqual(code, 1)
        self.assertIn("--command", err)

    def test_passing_check_allows_stop(self):
        code, err = run_verify(self.session("pass-1"), PASSING)
        self.assertEqual(code, 0, err)
        self.assertEqual(err.strip(), "")

    def test_failing_check_blocks_stop(self):
        code, err = run_verify(self.session("fail-1"), FAILING)
        self.assertEqual(code, 2, "падающая проверка обязана блокировать завершение")
        self.assertIn("NOT FINISHED", err)
        # Вывод команды должен попасть агенту, иначе чинить нечего.
        self.assertIn("2 tests failed", err)

    def test_block_message_forbids_faking_success(self):
        _, err = run_verify(self.session("fail-2"), FAILING)
        self.assertIn("skip", err.lower())

    def test_gives_up_after_max_blocks_but_stays_loud(self):
        session = self.session("loop-guard")
        codes = [run_verify(session, FAILING, extra=["--max-blocks", "2"])[0]
                 for _ in range(4)]
        self.assertEqual(codes[:2], [2, 2], "первые попытки должны блокировать")
        self.assertEqual(codes[2:], [0, 0], "бесконечная блокировка заперла бы сессию")
        code, err = run_verify(session, FAILING, extra=["--max-blocks", "2"])
        self.assertEqual(code, 0)
        self.assertIn("NOT finished", err)

    def test_success_resets_the_counter(self):
        session = self.session("reset")
        run_verify(session, FAILING, extra=["--max-blocks", "1"])
        run_verify(session, PASSING)
        code, _ = run_verify(session, FAILING, extra=["--max-blocks", "1"])
        self.assertEqual(code, 2, "после успешного прогона счётчик должен обнуляться")

    def test_clean_worktree_skips_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            env.update(os.environ)
            for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
                subprocess.run(cmd, cwd=tmp, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Проверка падающая, но менять нечего — запускать её незачем.
            code, err = run_verify(self.session("clean"), FAILING, cwd=tmp, extra=["--changed-only"])
            self.assertEqual(code, 0, err)

    def test_timeout_blocks_instead_of_allowing_stop(self):
        # Зависшая проверка — непройденная проверка. Разрешать завершение
        # здесь означало бы, что достаточно подвесить тесты, чтобы снять гейт.
        hang = "{} -c \"import time; time.sleep(30)\"".format(sys.executable)
        code, err = run_verify(self.session("hang"), hang, extra=["--timeout", "2"])
        self.assertEqual(code, 2, "таймаут обязан блокировать завершение")
        self.assertIn("NOT FINISHED", err)
        self.assertIn("interrupted", err)

    def test_bad_arguments_do_not_lock_the_session(self):
        # argparse по умолчанию выходит с кодом 2, а для Stop-хука это
        # «блокировать»: опечатка в конфигурации заперла бы сессию навсегда.
        code, err = run_verify(self.session("badargs"), PASSING, extra=["--max-blocks", "не-число"])
        self.assertEqual(code, 1, "ошибка конфигурации не должна блокировать завершение")
        self.assertIn("argument", err.lower())

    def test_check_runs_by_default_on_clean_worktree(self):
        # Штатный процесс заканчивается коммитом, после которого дерево чистое.
        # Пропуск по умолчанию отключал бы гейт в самом обычном сценарии.
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
                subprocess.run(cmd, cwd=tmp, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            code, _ = run_verify(self.session("clean-default"), FAILING, cwd=tmp)
            self.assertEqual(code, 2, "без --changed-only проверка обязана выполняться")

    def test_non_git_directory_still_runs_the_check(self):
        # Не смогли определить наличие изменений — проверяем на всякий случай.
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = run_verify(self.session("nogit"), FAILING, cwd=tmp, extra=["--changed-only"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
