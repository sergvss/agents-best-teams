#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты правила «что делать после отказа».

Запуск:
    python -X utf8 hooks/tests/test_denial_guidance.py
"""

import json
import os
import subprocess
import sys
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "denial_guidance.py")
HOOKS_DIR = os.path.normpath(os.path.dirname(HOOK))


def run(payload, lang="en"):
    """Прогон хука как процесса; возвращает (код возврата, разобранный вывод или None)."""
    env = dict(os.environ, ABT_LANG=lang)
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", HOOK], input=raw,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out = proc.stdout.decode("utf-8").strip()
    return proc.returncode, (json.loads(out) if out else None)


def context(event, source=None, lang="en"):
    payload = {"hook_event_name": event, "cwd": "."}
    if source:
        payload["source"] = source
    code, out = run(payload, lang)
    assert code == 0
    return out["hookSpecificOutput"] if out else None


class TestMainConversation(unittest.TestCase):
    def test_every_way_a_session_begins_gets_the_rule(self):
        # compact здесь важнее прочих: сжатие контекста может выбросить правило,
        # вложенное при старте, а отказ классификатора случается посреди работы.
        for source in ("startup", "resume", "clear", "compact", "fork"):
            with self.subTest(source=source):
                block = context("SessionStart", source)
                self.assertEqual(block["hookEventName"], "SessionStart")
                self.assertIn("AskUserQuestion", block["additionalContext"])

    def test_rule_names_the_way_out_and_the_limit(self):
        text = context("SessionStart", "startup")["additionalContext"]
        # Узнаваемый признак отказа - дословно, иначе агент его не сопоставит.
        self.assertIn("denied by the Claude Code auto mode classifier", text)
        # Повтор один раз, а не цикл уговоров классификатора.
        self.assertIn("once", text)
        # Документированный ручной путь, если повтор снова отклонён.
        self.assertIn("Recently denied", text)

    def test_hook_blocks_are_not_something_to_ask_about(self):
        # Отказ нашего хука согласием не снимается: спросить «разрешите?» и
        # повторить - значит получить тот же отказ и потерять доверие к вопросам.
        text = context("SessionStart", "startup")["additionalContext"]
        self.assertIn("BLOCKED", text)
        self.assertIn("not lifted by approval", text)


class TestSubagent(unittest.TestCase):
    def test_subagent_gets_its_own_rule(self):
        block = context("SubagentStart")
        self.assertEqual(block["hookEventName"], "SubagentStart")
        text = block["additionalContext"]
        # Роль обязана знать, что спросить не может, - иначе она ищет, как.
        self.assertIn("AskUserQuestion is not available", text)
        self.assertIn("PERMISSION NEEDED", text)

    def test_the_request_marker_matches_in_both_texts_and_languages(self):
        # Главный диалог узнаёт просьбу роли по маркеру. Разойдись маркер
        # хотя бы в одном языке - просьба дойдёт, но не будет узнана.
        for lang in ("en", "ru"):
            sub = context("SubagentStart", lang=lang)["additionalContext"]
            main = context("SessionStart", "startup", lang=lang)["additionalContext"]
            marker = [line for line in sub.splitlines() if line.isupper()][0]
            with self.subTest(lang=lang, marker=marker):
                self.assertIn(marker, main)


class TestContract(unittest.TestCase):
    def test_other_events_are_left_alone(self):
        # Ответ с чужим hookEventName Claude Code не примет.
        for event in ("PreToolUse", "Stop", ""):
            with self.subTest(event=event):
                code, out = run({"hook_event_name": event, "cwd": "."})
                self.assertEqual(code, 0)
                self.assertIsNone(out)

    def test_malformed_input_does_not_break_the_session(self):
        for raw in (b"", b"   ", b"not json"):
            with self.subTest(raw=raw):
                code, out = run(raw)
                self.assertEqual(code, 0)
                self.assertIsNone(out)


class TestWiring(unittest.TestCase):
    """Правило, которое не подключено, не существует - даже с зелёными тестами выше."""

    def entries(self, config, event):
        with open(os.path.join(HOOKS_DIR, config), encoding="utf-8") as fh:
            data = json.load(fh)
        found = []
        for entry in data["hooks"].get(event, []):
            for hook in entry.get("hooks", []):
                if any(a.endswith("denial_guidance.py") for a in hook.get("args", [])):
                    found.append(entry)
        return found

    def test_wired_for_both_events_in_both_configs(self):
        for config in ("hooks.json", "settings.example.json"):
            for event in ("SessionStart", "SubagentStart"):
                with self.subTest(config=config, event=event):
                    entries = self.entries(config, event)
                    self.assertEqual(len(entries), 1, "ровно одно подключение")
                    # Без матчера - на любой источник старта и на любую роль.
                    # Перечисление матчеров здесь потеряло бы compact или
                    # роль, добавленную позже.
                    self.assertIn(entries[0].get("matcher", ""), ("", "*"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
