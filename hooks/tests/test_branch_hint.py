#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты branch_hint.py: подсказка при первой правке файла на основной ветке.

Запуск:
    python -X utf8 hooks/tests/test_branch_hint.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HOOKS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
HOOK = os.path.join(HOOKS, "branch_hint.py")


def git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


class BranchHintTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "app.py"), "w") as fh:
            fh.write("x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        git(self.repo, "branch", "-M", "main")
        # Своя сессия на тест: подсказка одна за сессию
        self.session = "test-" + uuid.uuid4().hex

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def hint(self, path="app.py", tool="Edit", session=None, lang="ru"):
        payload = {"tool_name": tool, "tool_input": {"file_path": os.path.join(self.repo, path)},
                   "cwd": self.repo, "session_id": session or self.session}
        env = dict(os.environ, ABT_LANG=lang)
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run([sys.executable, "-X", "utf8", HOOK], input=json.dumps(payload).encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.decode("utf-8").strip()
        if not out:
            return None
        block = json.loads(out)["hookSpecificOutput"]
        # Только подсказка: ни окна, ни отказа
        self.assertNotIn("permissionDecision", block)
        return block["additionalContext"]

    def test_policy_auto_tells_to_branch_now(self):
        git(self.repo, "config", "abt.branches", "auto")
        text = self.hint()
        self.assertIn("main", text)
        self.assertIn("git switch -c", text)

    def test_no_policy_tells_to_ask_the_user(self):
        text = self.hint()
        # Ветка сразу, вопрос - без ожидания: безголовая сессия ответа не дождётся
        self.assertIn("git switch -c", text)
        self.assertIn("спроси пользователя", text)
        self.assertIn("НУЖНО РАЗРЕШЕНИЕ", text)

    def test_policy_off_is_silent(self):
        git(self.repo, "config", "abt.branches", "off")
        self.assertIsNone(self.hint())

    def test_feature_branch_is_silent(self):
        git(self.repo, "switch", "-q", "-c", "feat/x")
        self.assertIsNone(self.hint())

    def test_once_per_session(self):
        self.assertIsNotNone(self.hint())
        self.assertIsNone(self.hint(path="other.py"))
        # Новая сессия - новая подсказка
        self.assertIsNotNone(self.hint(session=self.session + "-2"))

    def test_agent_working_files_do_not_count(self):
        # Память и ledger агент пишет и на основной ветке - это не код задачи
        for path in (".claude/agent-memory/dev/MEMORY.md", ".agent-ledger/task.md"):
            with self.subTest(path=path):
                self.assertIsNone(self.hint(path=path))
        self.assertIsNotNone(self.hint())

    def test_master_and_origin_head_are_default_branches(self):
        git(self.repo, "branch", "-M", "master")
        self.assertIsNotNone(self.hint())
        # origin/HEAD указывает на develop - тогда основная develop, а master уже нет
        git(self.repo, "switch", "-q", "-c", "develop")
        git(self.repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
        self.assertIsNotNone(self.hint(session=self.session + "-dev"))
        git(self.repo, "switch", "-q", "master")
        self.assertIsNone(self.hint(session=self.session + "-master"))

    def test_outside_git_and_other_tools_are_silent(self):
        outside = tempfile.mkdtemp()
        try:
            payload = {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(outside, "a.py")},
                       "cwd": outside, "session_id": self.session}
            env = dict(os.environ)
            env.pop("CLAUDE_PROJECT_DIR", None)
            proc = subprocess.run([sys.executable, "-X", "utf8", HOOK], input=json.dumps(payload).encode("utf-8"),
                                  stdout=subprocess.PIPE, env=env)
            self.assertEqual(proc.stdout.strip(), b"")
        finally:
            shutil.rmtree(outside, ignore_errors=True)
        self.assertIsNone(self.hint(tool="Read"))

    def bash_hint(self, command, session):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.repo, "session_id": session}
        env = dict(os.environ, ABT_LANG="ru")
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run([sys.executable, "-X", "utf8", HOOK], input=json.dumps(payload).encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return proc.stdout.decode("utf-8").strip() or None

    def test_writing_through_the_shell_counts(self):
        # Живой прогон 1.34.0: в bypass агенты пишут файлы оболочкой, а не Edit
        for i, command in enumerate(["cat > app.py <<'EOF'\nx = 2\nEOF", "sed -i 's/1/2/' app.py",
                                     "echo note >> README.md", "cd sub && printf 'x' > a.py"]):
            with self.subTest(command=command):
                self.assertIsNotNone(self.bash_hint(command, "{}-sh{}".format(self.session, i)))

    def test_shell_reads_and_agent_files_are_silent(self):
        for i, command in enumerate(["git status", "cat app.py", "python -m unittest 2>&1 | tail -3",
                                     "ls > /dev/null", "echo '- [x] шаг' >> .agent-ledger/task.md",
                                     # Черновик вне проекта - не код задачи
                                     "echo x > " + os.path.join(os.path.dirname(self.repo), "scratch.txt").replace("\\", "/")]):
            with self.subTest(command=command):
                self.assertIsNone(self.bash_hint(command, "{}-ro{}".format(self.session, i)))

    def test_english(self):
        git(self.repo, "config", "abt.branches", "auto")
        self.assertIn("create a branch now", self.hint(lang="en"))

    def test_wired_in_both_configs(self):
        for name in ("hooks.json", "settings.example.json"):
            with open(os.path.join(HOOKS, name), encoding="utf-8") as fh:
                config = json.load(fh)["hooks"]
            with self.subTest(config=name):
                entries = [e for e in config["PreToolUse"]
                           if any("branch_hint.py" in " ".join(h.get("args", [])) for h in e["hooks"])]
                self.assertEqual(len(entries), 1)
                for tool in ("Bash", "Edit", "Write"):
                    self.assertIn(tool, entries[0]["matcher"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
