#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты шлюза ревью. Каждый тест — во временном git-репозитории, хук запускается
настоящим процессом через stdin, как его вызывает Claude Code.

Запуск:
    python -X utf8 -m unittest hooks.tests.test_review_gate

Ложное срабатывание здесь такой же баг, как пропуск: шлюз, открывающий окно на
коммит документации или на чужой проект без роли ревьюера, отключат в первый
же день — поэтому половина тестов проверяет, что он молчит.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "review_gate.py")

os.environ.setdefault("ABT_LANG", "en")

REVIEWER_ROLE = "---\nname: code-reviewer\ndescription: test\ntools: Read, Bash\n---\n"


def remove_tree(path):
    """
    Удаляет временный репозиторий целиком.

    На Windows объекты git доступны только для чтения, и rmtree с ignore_errors
    молча оставлял их: за десяток прогонов набежали тысячи каталогов.
    """
    if not os.path.exists(path):
        return

    def make_writable_and_retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    # onerror устарел с Python 3.12, onexc появился только в нём.
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=make_writable_and_retry)


def hook_env():
    # Тесты могут идти внутри сессии Claude Code: её CLAUDE_PROJECT_DIR указал бы
    # хуку на этот репозиторий вместо временного.
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="abt-gate-")
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "core.autocrlf", "false")
        self.write("app.py", "print(1)\n")
        self.write("README.md", "# readme\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.add_reviewer()

    def tearDown(self):
        remove_tree(self.repo)

    # -- окружение -----------------------------------------------------------
    def git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, name, text):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def add_reviewer(self, filename="code-reviewer.md"):
        self.write(os.path.join(".claude", "agents", filename), REVIEWER_ROLE)
        # Роль в репозитории не коммитится: каталог .claude не участвует в тестах шлюза.
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
            fh.write(".claude/\n")

    def run_gate(self, payload, *args):
        payload.setdefault("cwd", self.repo)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", GATE] + list(args),
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=hook_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8").strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def decide(self, command, agent=None):
        """(None | "ask" | "deny", текст) для PreToolUse Bash."""
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        if agent:
            payload["agent_type"] = agent
        block = self.run_gate(payload)
        if block is None:
            return None, None
        return block["permissionDecision"], block["permissionDecisionReason"]

    def approve(self, agent="code-reviewer", note="checked"):
        payload = {"tool_name": "Bash", "agent_type": agent,
                   "tool_input": {"command": 'echo abt-review-approve "{}"'.format(note)}}
        block = self.run_gate(payload, "--record")
        return block["additionalContext"] if block else None

    def approvals_file(self):
        return os.path.join(self.repo, ".git", "abt-review-approvals.json")


class CommitGate(GateTestCase):
    def context(self, command):
        """Текст для агента рядом с окном."""
        block = self.run_gate({"tool_name": "Bash", "tool_input": {"command": command}})
        return block.get("additionalContext", "") if block else ""

    def test_asks_for_unreviewed_code_commit(self):
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        decision, text = self.decide('git commit -m "fix"')
        self.assertEqual(decision, "ask")
        self.assertIn("app.py", text)
        # Как получить одобрение - агенту, а не человеку в окне.
        self.assertIn("abt-review-approve", self.context('git commit -m "fix"'))

    def test_window_text_is_short(self):
        # Скриншоты пользователя: окно на шесть абзацев справки на каждом коммите.
        # Человеку - одна строка, подробности - агенту.
        self.write("app.py", "print(2)\n")
        for command in ('git commit -am "fix"', "git add '*.py' && git commit -m x"):
            with self.subTest(command=command):
                decision, text = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertLess(len(text), 220, text)
                self.assertNotIn("\n", text)

    def test_approved_state_goes_through(self):
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.assertIn("approval recorded", self.approve())
        self.assertEqual(self.decide('git commit -m "fix"'), (None, None))

    def test_edit_after_approval_needs_a_new_review(self):
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.approve()
        self.write("app.py", "print(3)\n")
        self.git("add", "app.py")
        self.assertEqual(self.decide('git commit -m "fix"')[0], "ask")

    def test_approval_of_working_copy_covers_add_all_and_commit(self):
        # Ревьюер смотрел рабочую копию, коммит идёт одной командой с git add -A:
        # на момент проверки индекс пуст, но в коммит уйдёт одобренное.
        self.write("app.py", "print(2)\n")
        self.write("new_module.py", "x = 1\n")
        self.approve()
        self.assertEqual(self.decide('git add -A && git commit -m "fix"'), (None, None))

    def test_add_all_and_commit_without_approval_asks(self):
        # Дыра, ради которой отпечаток считается по содержимому: сверка по
        # git diff --cached увидела бы пустой индекс и пропустила коммит.
        self.write("app.py", "print(2)\n")
        self.assertEqual(self.decide('git add -A && git commit -m "fix"')[0], "ask")

    def test_commit_all_flag_matches_tracked_changes(self):
        self.write("app.py", "print(2)\n")
        self.assertEqual(self.decide('git commit -am "fix"')[0], "ask")
        self.approve()
        self.assertEqual(self.decide('git commit -am "fix"'), (None, None))

    def test_working_copy_edit_after_approval_needs_a_new_review(self):
        # Тот же набор путей, другое содержимое: отпечаток обязан смотреть в файл,
        # а не только в список изменённых путей.
        self.write("app.py", "print(2)\n")
        self.approve()
        self.write("app.py", "print(3)\n")
        self.assertEqual(self.decide('git commit -am "fix"')[0], "ask")
        self.assertEqual(self.decide('git add -A && git commit -m "fix"')[0], "ask")

    def test_untracked_file_added_after_approval_is_not_covered(self):
        self.write("app.py", "print(2)\n")
        self.approve()
        self.write("sneaky.py", "import os\n")
        self.assertEqual(self.decide('git add -A && git commit -m "fix"')[0], "ask")

    def test_docs_only_commit_is_silent(self):
        self.write("README.md", "# changed\n")
        self.write("docs/guide.md", "text\n")
        self.git("add", "-A")
        self.assertEqual(self.decide('git commit -m "docs"'), (None, None))

    def test_requirements_txt_counts_as_code(self):
        self.write("requirements.txt", "requests==2.0\n")
        self.git("add", "-A")
        self.assertEqual(self.decide('git commit -m "deps"')[0], "ask")

    def test_nothing_staged_is_silent(self):
        self.assertEqual(self.decide("git commit --amend --no-edit"), (None, None))

    def test_add_files_and_commit_is_checked_not_guessed(self):
        # Самая частая форма у агентов. Раньше - всегда «не могу сверить».
        self.write("app.py", "print(2)\n")
        decision, text = self.decide('git add app.py && git commit -m "fix"')
        self.assertEqual(decision, "ask")
        self.assertNotIn("cannot match", text)
        self.approve()
        for command in ('git add app.py && git commit -m "fix"', 'git commit -m "fix" -- app.py',
                        "git commit app.py -m fix"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_docs_only_commit_with_specific_files_is_silent(self):
        # Скриншот: окно на коммите одного CHANGELOG, пока код рядом не готов.
        self.write("app.py", "print(2)\n")
        self.write("CHANGELOG.md", "# changes\n")
        for command in ("git add CHANGELOG.md && git commit -m notes",
                        "git commit CHANGELOG.md -m notes"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_partial_commit_of_reviewed_content_is_silent(self):
        self.write("app.py", "print(2)\n")
        self.write("other.py", "x = 1\n")
        self.approve()
        self.assertEqual(self.decide("git add app.py && git commit -m part"), (None, None))
        # Файл правили после ревью - этого содержимого ревьюер не видел.
        self.write("app.py", "print(3)\n")
        self.assertEqual(self.decide("git add app.py && git commit -m part")[0], "ask")

    def test_file_reverted_to_head_is_not_a_change(self):
        # app.py проиндексирован изменённым, потом в рабочей копии возвращён к
        # HEAD: git add его «отменяет», и в коммите он не меняется вовсе.
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.write("app.py", "print(1)\n")
        self.write("other.py", "x = 1\n")
        self.approve()
        self.assertEqual(self.decide("git add app.py other.py && git commit -m x"), (None, None))

    def test_unreviewed_file_in_a_partial_commit_asks(self):
        self.write("app.py", "print(2)\n")
        self.approve()
        self.write("late.py", "y = 2\n")
        self.assertEqual(self.decide("git add app.py late.py && git commit -m x")[0], "ask")

    def test_add_directory_is_computed(self):
        self.write("pkg/a.py", "a = 1\n")
        self.write("pkg/b.py", "b = 1\n")
        self.assertEqual(self.decide("git add pkg && git commit -m x")[0], "ask")
        self.approve()
        self.assertEqual(self.decide("git add pkg/ && git commit -m x"), (None, None))

    def test_commit_directory_includes_already_staged_files(self):
        # Внешнее ревью 1.21.1: foo.py проиндексирован в том же виде, что в
        # рабочей копии, и `git commit pkg/` его коммитит - а вычисление его теряло.
        self.write("pkg/foo.py", "foo = 1\n")
        self.git("add", "pkg/foo.py")
        self.assertEqual(self.decide("git commit pkg/ -m x")[0], "ask")
        self.assertEqual(self.decide("git add pkg && git commit -m x")[0], "ask")

    def test_opt_out_name_elsewhere_does_not_ask(self):
        self.assertEqual(self.decide("touch src/.no-review-gate"), (None, None))

    def test_staged_deletion_by_path(self):
        os.remove(os.path.join(self.repo, "app.py"))
        self.assertEqual(self.decide("git add app.py && git commit -m rm")[0], "ask")
        self.approve()
        self.assertEqual(self.decide("git add app.py && git commit -m rm"), (None, None))

    def test_globs_and_patch_mode_stay_unverifiable(self):
        self.write("app.py", "print(2)\n")
        self.approve()
        for command in ("git add '*.py' && git commit -m x", "git add -p && git commit -m x",
                        "git commit --patch -m x"):
            with self.subTest(command=command):
                decision, text = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("cannot match", text)

    def test_opt_out_file_silences_the_gate(self):
        self.write("app.py", "print(2)\n")
        self.write(".claude/.no-review-gate", "")
        self.assertEqual(self.decide('git commit -am "fix"'), (None, None))

    def test_writing_the_opt_out_file_needs_a_human(self):
        decision, text = self.decide("touch .claude/.no-review-gate")
        self.assertEqual(decision, "ask")
        self.assertIn("review gate", text)
        block = self.run_gate({"tool_name": "Write", "tool_input": {
            "file_path": os.path.join(self.repo, ".claude", ".no-review-gate")}})
        self.assertEqual(block["permissionDecision"], "ask")

    def test_message_value_is_not_taken_for_a_path(self):
        # Значение -m и слипшихся ключей - не путь: иначе каждый коммит стал бы
        # «несверяемым» и окно открывалось бы с неверной причиной.
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.approve()
        for command in ('git commit -m "fix app.py"', 'git commit -sm "fix"',
                        "git commit --message fix", "git commit --author 'a <b@c>' -m x",
                        "git commit -m fix -uno"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_redirects_are_not_taken_for_paths(self):
        # Живой прогон: `git commit -m x 2>&1` дал «несверяемый коммит» -
        # разборщик режет 2>&1 на 2, >&, 1, и номер дескриптора выглядел путём.
        self.write("app.py", "print(2)\n")
        self.approve()
        for command in ('git add -A && git commit -m x 2>&1; echo "EXIT=$?"',
                        "git commit -am x > /dev/null", "git commit -am x 2> err.log",
                        "git add -A 2>/dev/null && git commit -m x"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_commit_message_from_heredoc(self):
        # Живой прогон в песочнице: `git commit -F - <<'EOF'` после одобрения
        # трижды получил окно - ограничитель EOF читался путём коммита.
        # Вторая форма - та, которой коммитит сам Claude Code.
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.approve()
        for command in ("git commit -q -F - <<'EOF'\nfix\n\nbody\nEOF\ngit log --oneline -1",
                        "git commit -m \"$(cat <<'EOF'\nfix\n\nCo-Authored-By: x <y@z>\nEOF\n)\"",
                        "git commit -F - <<-EOF\n\tfix\n\tEOF"):
            with self.subTest(command=command.splitlines()[0]):
                self.assertEqual(self.decide(command), (None, None))

    def test_bypass_mode_with_git_dir_write_blocks_instead_of_asking(self):
        # Живой прогон: агент дописал .git/info/exclude в той же команде, что и
        # коммит, и в bypassPermissions Claude Code пропустил коммит мимо окна.
        self.write("app.py", "print(2)\n")
        payload = {"tool_name": "Bash", "permission_mode": "bypassPermissions", "tool_input": {
            "command": "printf 'SESSIONS.md\\n' >> .git/info/exclude && git add -A && git commit -m x"}}
        block = self.run_gate(payload)
        self.assertEqual(block["permissionDecision"], "deny")
        payload["permission_mode"] = "default"
        self.assertEqual(self.run_gate(payload)["permissionDecision"], "ask")

    def test_write_before_commit_in_one_command_is_unverifiable(self):
        # Внешнее ревью: отпечаток считается до запуска команды, и файл,
        # созданный в ней же, ушёл бы в коммит по «чистому» состоянию.
        for command in ("echo 'import os' > new.py && git add -A && git commit -m x",
                        "sed -i s/1/2/ app.py && git commit -am x",
                        "npm run format && git commit -am x",
                        "python gen.py; git add -A; git commit -m x"):
            with self.subTest(command=command):
                decision, text = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("cannot match", text)

    def test_read_only_commands_before_commit_keep_the_check(self):
        self.write("app.py", "print(2)\n")
        self.approve()
        for command in ("git status && git add -A && git commit -m x",
                        "git diff --stat; git commit -am x",
                        "echo committing && git commit -am x"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_git_global_options_do_not_hide_the_commit(self):
        self.write("app.py", "print(2)\n")
        repo = self.repo.replace("\\", "/")
        for command in ("git --work-tree . commit -am x", "git --git-dir=.git commit -am x",
                        "git -C{} commit -am x".format(repo),
                        "git -c user.name=x commit -am x", "git --no-pager commit -am x"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command)[0], "ask")

    def test_alias_to_commit_is_gated(self):
        # Внешнее ревью, оба ревьюера: `git ci` при alias.ci = commit проходил мимо.
        self.git("config", "alias.ci", "commit")
        self.write("app.py", "print(2)\n")
        self.assertEqual(self.decide("git ci -am x")[0], "ask")
        self.approve()
        self.assertEqual(self.decide("git ci -am x"), (None, None))

    def test_shell_alias_and_alias_defined_in_the_command_are_unverifiable(self):
        self.git("config", "alias.save", "!git add -A && git commit -m wip")
        self.write("app.py", "print(2)\n")
        self.approve()
        for command in ("git save", "git config alias.c commit && git c -am x"):
            with self.subTest(command=command):
                decision, text = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("cannot match", text)

    def test_harmless_aliases_and_extensions_are_silent(self):
        self.git("config", "alias.st", "status")
        self.write("app.py", "print(2)\n")
        for command in ("git st", "git lfs ls-files", "git worktree list"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_git_environment_overrides_are_unverifiable(self):
        # Внешнее ревью: с GIT_DIR или GIT_INDEX_FILE git коммитит не то, что
        # проверил хук.
        self.write("app.py", "print(2)\n")
        self.approve()
        for command in ("GIT_INDEX_FILE=/tmp/other git commit -am x",
                        "env GIT_DIR=/tmp/x/.git git commit -am x",
                        "export GIT_WORK_TREE=/tmp/x; git commit -am x"):
            with self.subTest(command=command):
                decision, text = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("cannot match", text)

    def test_commit_tree_is_unverifiable(self):
        self.assertEqual(self.decide("git commit-tree HEAD^{tree} -m x")[0], "ask")

    def test_explicit_work_tree_is_unverifiable_even_when_approved(self):
        # С --work-tree или --git-dir коммитится не обязательно этот репозиторий:
        # одобрение текущего здесь ничего не доказывает.
        self.write("app.py", "print(2)\n")
        self.approve()
        for command in ("git --work-tree . commit -am x", "git --git-dir=.git commit -am x"):
            with self.subTest(command=command):
                decision, text = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("cannot match", text)

    def test_git_failure_inside_a_repository_is_not_silent(self):
        # Внешнее ревью: сбой git (например, нечитаемый файл) давал None, и шлюз
        # молча пропускал коммит. Внутри репозитория сбой - исключение, а
        # pre_bash превращает его в окно «шлюз не смог проверить».
        hooks_dir = os.path.normpath(os.path.dirname(GATE))
        sys.path.insert(0, hooks_dir)
        try:
            import review_gate
        finally:
            sys.path.remove(hooks_dir)
        self.write("app.py", "print(2)\n")
        real_git = review_gate.git
        # diff - худший случай: молчаливый сбой дал бы пустой список путей, то
        # есть «коммит без кода», и шлюз пропустил бы его без окна.
        for failing in ("diff", "hash-object"):
            review_gate.git = lambda repo, *args, **kw: None if args[0] == failing \
                else real_git(repo, *args, **kw)
            try:
                with self.subTest(failing=failing), self.assertRaises(review_gate.GitStateError):
                    review_gate.fingerprint(self.repo, "tracked")
            finally:
                review_gate.git = real_git

        # И целиком: pre_bash обязан превратить сбой в окно, а не промолчать.
        import contextlib
        import io
        import guard
        saved_dir = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        review_gate.git = lambda repo, *args, **kw: None if args[0] == "diff" \
            else real_git(repo, *args, **kw)
        guard.PENDING_CONFIRMATIONS.clear()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                review_gate.pre_bash({"tool_name": "Bash", "cwd": self.repo,
                                      "tool_input": {"command": "git commit -am x"}})
        finally:
            review_gate.git = real_git
            guard.PENDING_CONFIRMATIONS.clear()
            if saved_dir is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = saved_dir
        block = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(block["permissionDecision"], "ask")
        self.assertIn("could not check", block["permissionDecisionReason"])

    def test_index_changing_command_before_commit_is_unverifiable(self):
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.approve()
        self.assertEqual(self.decide("git rm -q README.md && git commit -m x")[0], "ask")

    def test_nested_shell_and_directory_forms(self):
        self.write("app.py", "print(2)\n")
        os.makedirs(os.path.join(self.repo, "sub"))
        for command in ("sh -c 'git add -A && git commit -m x'",
                        "cd sub && git add -A && git commit -m x",
                        "git -C {} commit -am x".format(self.repo.replace("\\", "/"))):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command)[0], "ask")

    def test_silent_without_a_reviewer_role(self):
        remove_tree(os.path.join(self.repo, ".claude"))
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.assertEqual(self.decide('git commit -m "fix"'), (None, None))

    def test_renamed_role_file_is_recognised_by_frontmatter(self):
        remove_tree(os.path.join(self.repo, ".claude"))
        self.add_reviewer("reviewer-for-backend.md")
        self.write("app.py", "print(2)\n")
        self.git("add", "app.py")
        self.assertEqual(self.decide('git commit -m "fix"')[0], "ask")

    def test_other_commands_are_silent(self):
        self.write("app.py", "print(2)\n")
        for command in ("git status", "git log --oneline -3", "git diff --cached",
                        "echo git commit", "git add -A"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), (None, None))

    def test_outside_a_repository_is_silent(self):
        plain = tempfile.mkdtemp(prefix="abt-plain-")
        try:
            os.makedirs(os.path.join(plain, ".claude", "agents"))
            with open(os.path.join(plain, ".claude", "agents", "code-reviewer.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(REVIEWER_ROLE)
            block = self.run_gate({"tool_name": "Bash", "cwd": plain,
                                   "tool_input": {"command": "git commit -m x"}})
            self.assertIsNone(block)
        finally:
            remove_tree(plain)


class FirstCommit(GateTestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="abt-gate-")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.add_reviewer()

    def test_first_commit_is_gated_and_approvable(self):
        self.write("main.py", "print(1)\n")
        self.git("add", "main.py")
        self.assertEqual(self.decide("git commit -m init")[0], "ask")
        self.approve()
        self.assertEqual(self.decide("git commit -m init"), (None, None))

    def test_sha256_repository_first_commit(self):
        # Хэш пустого дерева у sha256-репозитория другой: захардкоженный sha1
        # уронил бы проверку первого коммита.
        sha = tempfile.mkdtemp(prefix="abt-sha256-")
        try:
            proc = subprocess.run(["git", "-C", sha, "init", "-q", "--object-format=sha256"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if proc.returncode != 0:
                self.skipTest("git без поддержки sha256")
            remove_tree(self.repo)
            self.repo = sha
            self.git("config", "core.autocrlf", "false")
            self.add_reviewer()
            self.write("main.py", "print(1)\n")
            self.git("add", "main.py")
            self.assertEqual(self.decide("git commit -m init")[0], "ask")
            self.approve()
            self.assertEqual(self.decide("git commit -m init"), (None, None))
        finally:
            remove_tree(sha)


class Approval(GateTestCase):
    def test_marker_from_the_main_session_is_blocked(self):
        decision, text = self.decide('echo abt-review-approve "looks fine"')
        self.assertEqual(decision, "deny")
        self.assertIn("the main session", text)

    def test_marker_from_another_role_is_blocked(self):
        decision, text = self.decide("echo abt-review-approve", agent="dev-backend")
        self.assertEqual(decision, "deny")
        self.assertIn("dev-backend", text)

    def test_marker_from_the_reviewer_passes(self):
        self.assertEqual(self.decide("echo abt-review-approve ok", agent="code-reviewer"),
                         (None, None))

    def test_record_ignores_other_roles(self):
        self.write("app.py", "print(2)\n")
        self.assertIsNone(self.approve(agent="dev-backend"))
        self.assertIsNone(self.approve(agent=""))
        self.assertFalse(os.path.exists(self.approvals_file()))

    def test_record_ignores_ordinary_commands(self):
        self.write("app.py", "print(2)\n")
        payload = {"tool_name": "Bash", "agent_type": "code-reviewer",
                   "tool_input": {"command": "git diff"}}
        self.assertIsNone(self.run_gate(payload, "--record"))
        self.assertFalse(os.path.exists(self.approvals_file()))

    def test_nothing_to_approve_on_a_clean_tree(self):
        self.assertIn("nothing to approve", self.approve())
        self.assertFalse(os.path.exists(self.approvals_file()))

    def test_approvals_keep_a_bounded_history(self):
        for number in range(55):
            self.write("app.py", "print({})\n".format(number))
            self.approve(note=str(number))
        with open(self.approvals_file(), encoding="utf-8") as fh:
            approvals = json.load(fh)["approvals"]
        self.assertEqual(len(approvals), 50)
        self.assertEqual(approvals[-1]["note"], "54")

    def test_shell_write_to_the_approvals_file_is_blocked(self):
        for command in ("echo {} > .git/abt-review-approvals.json",
                        "cp /tmp/x .git/abt-review-approvals.json",
                        "cd .git && tee abt-review-approvals.json"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command.format("{}"))[0], "deny")

    def test_tool_write_to_the_approvals_file_is_blocked(self):
        payload = {"tool_name": "Write", "tool_input": {
            "file_path": os.path.join(self.repo, ".git", "abt-review-approvals.json")}}
        block = self.run_gate(payload)
        self.assertEqual(block["permissionDecision"], "deny")

    def test_reading_the_approvals_file_is_allowed(self):
        self.assertEqual(self.decide("cat .git/abt-review-approvals.json"), (None, None))


if __name__ == "__main__":
    unittest.main()
