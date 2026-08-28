#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты SessionStart-хука, предлагающего развернуть команду.

Главное, что здесь проверяется, — молчание. Подсказка, повторяющаяся каждую
сессию, тратит контекст и приучает себя игнорировать, поэтому «не сработал»
здесь такой же полноценный результат, как «сработал».

Запуск:
    python -X utf8 hooks/tests/test_session_start.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "session_start.py")

ROLE = """---
name: {name}
description: роль
tools: Read, Edit
---

# Роль
"""


def run_hook(cwd, source="startup"):
    """Возвращает текст подсказки или None, если хук промолчал."""
    payload = {"hook_event_name": "SessionStart", "source": source, "cwd": cwd}
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", HOOK],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = proc.stdout.decode("utf-8").strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


class SessionStartTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def add_role(self, name, filename=None):
        agents = os.path.join(self.cwd, ".claude", "agents")
        os.makedirs(agents, exist_ok=True)
        with open(os.path.join(agents, filename or (name + ".md")), "w", encoding="utf-8") as fh:
            fh.write(ROLE.format(name=name))


class TestPromptAppears(SessionStartTestCase):
    def test_offers_setup_in_a_project_without_team(self):
        prompt = run_hook(self.cwd)
        self.assertIsNotNone(prompt, "в проекте без ролей подсказка обязана появиться")
        self.assertIn("setup-agent-team", prompt)
        # Подсказка должна называть и то, что уже работает, иначе выглядит
        # так, будто не заработало ничего.
        self.assertIn("хуки", prompt)

    def test_offers_setup_when_agents_dir_has_foreign_roles_only(self):
        # Чужие роли в проекте — не наша команда.
        self.add_role("my-custom-helper")
        self.assertIsNotNone(run_hook(self.cwd))

    def test_prompt_tells_how_to_turn_itself_off(self):
        prompt = run_hook(self.cwd)
        self.assertIn(".no-team-setup-prompt", prompt)


class TestSilence(SessionStartTestCase):
    def test_silent_when_team_is_set_up(self):
        self.add_role("pm-orchestrator")
        self.assertIsNone(run_hook(self.cwd), "в настроенном проекте хук обязан молчать")

    def test_one_role_is_enough_to_consider_team_present(self):
        for role in ("dev-backend", "code-reviewer", "qa-tester"):
            with self.subTest(role=role):
                # tearDown перед setUp: иначе прошлый временный каталог течёт.
                self.tearDown()
                self.setUp()
                self.add_role(role)
                self.assertIsNone(run_hook(self.cwd))

    def test_role_recognised_by_frontmatter_not_filename(self):
        # Файл могли переименовать при адаптации; зовут роль по полю name.
        self.add_role("devops", filename="release-manager.md")
        self.assertIsNone(run_hook(self.cwd))

    def test_opt_out_marker_silences_the_prompt(self):
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        open(os.path.join(self.cwd, ".claude", ".no-team-setup-prompt"), "w").close()
        self.assertIsNone(run_hook(self.cwd))


class TestContract(SessionStartTestCase):
    def test_malformed_input_does_not_break_the_session(self):
        for raw in [b"", b"   ", b"not json"]:
            with self.subTest(raw=raw):
                proc = subprocess.run(
                    [sys.executable, "-X", "utf8", HOOK],
                    input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.cwd,
                )
                self.assertEqual(proc.returncode, 0)

    def test_unreadable_agents_dir_does_not_break_the_session(self):
        # На месте каталога ролей оказался файл — не повод падать.
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        with open(os.path.join(self.cwd, ".claude", "agents"), "w", encoding="utf-8") as fh:
            fh.write("не каталог")
        self.assertIsNotNone(run_hook(self.cwd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
