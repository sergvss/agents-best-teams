#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты ledger_log.py: хук сам дописывает факт вызова роли в .agent-ledger/calls.md.

Запуск:
    python -X utf8 hooks/tests/test_ledger_log.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOKS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
HOOK = os.path.join(HOOKS, "ledger_log.py")
LEDGER = "# Ledger: задача\n\n## Шаги\n- [ ] dev-backend — поручено\n"


def run_hook(payload, cwd, project_dir=None, lang="ru"):
    # Тесты могут идти внутри сессии Claude Code: её CLAUDE_PROJECT_DIR увёл бы
    # запись в настоящий ledger репозитория
    env = dict(os.environ, ABT_LANG=lang)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project_dir:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    return subprocess.run([sys.executable, "-X", "utf8", HOOK], input=json.dumps(payload).encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env)


def agent_call(project, role="dev-backend", task="Эндпоинт /api/daily", event="PostToolUse",
               status="completed", background=False):
    return {"hook_event_name": event, "tool_name": "Agent", "cwd": project,
            "tool_input": {"subagent_type": role, "description": task, "prompt": "…",
                           "run_in_background": background},
            "tool_response": {"status": status, "totalDurationMs": 95400}}


class LedgerLogTest(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()
        self.ledger_dir = os.path.join(self.project, ".agent-ledger")
        self.calls = os.path.join(self.ledger_dir, "calls.md")

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def make_ledger(self, name="daily.md"):
        os.makedirs(self.ledger_dir, exist_ok=True)
        path = os.path.join(self.ledger_dir, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(LEDGER)
        return path

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_without_ledger_the_hook_is_silent_and_creates_nothing(self):
        # Ledger заводит оркестратор, а не хук: Tier 1 и Tier 2 без ledger не должны его получить
        proc = run_hook(agent_call(self.project), self.project)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(self.ledger_dir))

    def test_journal_alone_does_not_count_as_a_ledger(self):
        # Задача закрыта, ledger удалён, а calls.md забыли - новых строк быть не должно
        os.makedirs(self.ledger_dir)
        with open(self.calls, "w", encoding="utf-8") as fh:
            fh.write("старое\n")
        run_hook(agent_call(self.project), self.project)
        self.assertEqual(self.read(self.calls), "старое\n")

    def test_call_goes_to_its_own_file_and_the_ledger_is_untouched(self):
        # Живой прогон 1.32.0: раздел внутри ledger пропадал, когда оркестратор
        # переписывал ledger целиком. Свой файл этого не боится.
        ledger = self.make_ledger()
        run_hook(agent_call(self.project), self.project)
        self.assertEqual(self.read(ledger), LEDGER)
        text = self.read(self.calls)
        self.assertTrue(text.startswith("# Журнал вызовов ролей"))
        self.assertRegex(text, r"- \d{4}-\d\d-\d\d \d\d:\d\d dev-backend — Эндпоинт /api/daily — завершено за 95 с")

    def test_calls_are_appended_in_order_under_one_header(self):
        self.make_ledger()
        run_hook(agent_call(self.project), self.project)
        run_hook(agent_call(self.project, role="code-reviewer", task="Ревью с ТЗ"), self.project)
        text = self.read(self.calls)
        self.assertEqual(text.count("# Журнал вызовов ролей"), 1)
        self.assertLess(text.index("dev-backend — Эндпоинт"), text.index("code-reviewer — Ревью с ТЗ"))

    def test_rewritten_ledger_does_not_lose_calls(self):
        ledger = self.make_ledger()
        run_hook(agent_call(self.project), self.project)
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write("# Ledger переписан целиком\n")
        run_hook(agent_call(self.project, role="qa-tester", task="Тесты"), self.project)
        text = self.read(self.calls)
        self.assertIn("dev-backend — Эндпоинт", text)
        self.assertIn("qa-tester — Тесты", text)

    def test_failure_and_background_are_named(self):
        self.make_ledger()
        run_hook(agent_call(self.project, event="PostToolUseFailure", status=""), self.project)
        run_hook(agent_call(self.project, role="qa-tester", status="async_launched", background=True), self.project)
        text = self.read(self.calls)
        self.assertIn("dev-backend — Эндпоинт /api/daily — сбой", text)
        self.assertIn("qa-tester — Эндпоинт /api/daily — запущено в фоне", text)

    def test_project_root_wins_over_a_subfolder_cwd(self):
        # Агент сделал cd в подкаталог - журнал всё равно в корне проекта
        self.make_ledger()
        sub = os.path.join(self.project, "server")
        os.makedirs(sub)
        payload = agent_call(self.project)
        payload["cwd"] = sub
        run_hook(payload, sub, project_dir=self.project)
        self.assertIn("dev-backend — Эндпоинт", self.read(self.calls))
        self.assertFalse(os.path.exists(os.path.join(sub, ".agent-ledger")))

    def test_project_path_with_brackets(self):
        # Триангуляция 1.32.0: glob понял бы [v2] как шаблон и не нашёл ledger
        self.project = os.path.join(self.project, "app [v2]")
        self.ledger_dir = os.path.join(self.project, ".agent-ledger")
        self.calls = os.path.join(self.ledger_dir, "calls.md")
        self.make_ledger()
        run_hook(agent_call(self.project), self.project)
        self.assertIn("dev-backend — Эндпоинт", self.read(self.calls))

    def test_english_messages(self):
        self.make_ledger()
        run_hook(agent_call(self.project), self.project, lang="en")
        text = self.read(self.calls)
        self.assertTrue(text.startswith("# Role call log"))
        self.assertIn("completed in 95 s", text)

    def test_other_tools_and_broken_input_change_nothing(self):
        self.make_ledger()
        payload = agent_call(self.project)
        payload["tool_name"] = "Bash"
        for raw in (json.dumps(payload).encode("utf-8"), b"{not json", b""):
            with self.subTest(raw=raw[:20]):
                proc = subprocess.run([sys.executable, "-X", "utf8", HOOK], input=raw,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.project)
                self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(self.calls))

    def test_wired_for_both_events_in_both_configs(self):
        for name in ("hooks.json", "settings.example.json"):
            with open(os.path.join(HOOKS, name), encoding="utf-8") as fh:
                config = json.load(fh)["hooks"]
            for event in ("PostToolUse", "PostToolUseFailure"):
                with self.subTest(config=name, event=event):
                    entries = [e for e in config[event]
                               if any("ledger_log.py" in " ".join(h.get("args", [])) for h in e["hooks"])]
                    self.assertEqual(len(entries), 1)
                    self.assertIn("Agent", entries[0]["matcher"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
