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

    def log(self, tool, payload, agent="", event="PostToolUse"):
        data = {"hook_event_name": event, "tool_name": tool,
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
            # Класс даёт команда внутри клиента, а не запуск клиента:
            # чтение это R, изменение данных - P. Раньше журнал метил P
            # и обычный SELECT, то есть завышал там и занижал в другом месте.
            ("psql -c 'SELECT 1'", "R"),
            ("psql -c 'DELETE FROM runs'", "P"),
            ("sqlite3 db.sqlite 'DROP TABLE runs'", "P"),
            # Force-push переписывает историю: P, а не W как обычный push.
            ("git push --force origin main", "P"),
            ("git push origin +main", "P"),
            ("git push origin --delete feature", "P"),
            ("git push origin :feature", "P"),
            # Хук сам предлагает --force-with-lease как безопасную замену
            # и пропускает её. Метить рекомендованную альтернативу классом
            # запрещённого действия - самопротиворечивость из principles/09.
            ("git push --force-with-lease origin main", "W"),
            ("git push origin main:main", "W"),
            ("chmod 600 key.pem", "P"),
            ("cat .env", "P"),
        ]:
            with self.subTest(command=command):
                self.tearDown()   # иначе прошлый временный каталог течёт
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
        self.tearDown()
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
                self.tearDown()   # иначе прошлый временный каталог течёт
                self.setUp()
                self.assertEqual(self.log("Bash", {"command": command}), [])

    def test_ordinary_file_edits_stay_out(self):
        for path in ["src/app.py", "README.md", "client/game/step.js"]:
            with self.subTest(path=path):
                self.tearDown()
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


class TestFailedActionsAreRecordedToo(ApprovalLogTestCase):
    """
    Неудавшееся привилегированное действие обязано оставлять след.

    Проверено на живой установке: падающая команда порождает
    `PostToolUseFailure`, а `PostToolUse` при этом **не приходит**. Пока хук
    висел только на успехе, журнал видел половину картины — заблокированный
    force-push и упавшая на середине миграция не оставляли следа вообще,
    хотя для аудита «попытался и не смог» обычно важнее, чем «сделал».
    """

    def test_success_is_marked_ok(self):
        found = self.log("Bash", {"command": "git push origin main"})
        self.assertEqual(len(found), 1)
        self.assertIs(found[0]["ok"], True)

    def test_failure_is_recorded_and_marked(self):
        found = self.log("Bash", {"command": "git push --force origin main"},
                         event="PostToolUseFailure")
        self.assertEqual(len(found), 1, "неудавшееся действие не попало в журнал")
        self.assertIs(found[0]["ok"], False)
        # Заблокированная попытка попадает в журнал именно этим путём, и
        # записать её мягким классом значит занизить риск там, где точность
        # и нужна: в журнале того, что агент пытался сделать.
        self.assertEqual(found[0]["what"], "git-push-destructive")
        self.assertEqual(found[0]["risk"], "P")

    def test_routine_stays_out_even_when_it_fails(self):
        # Фильтр рутины не должен зависеть от исхода: журнал, куда пишется
        # всё подряд, читать никто не станет.
        found = self.log("Bash", {"command": "ls -la"}, event="PostToolUseFailure")
        self.assertEqual(found, [])


class TestJournalAgreesWithTheHook(unittest.TestCase):
    """
    По формам git push журнал и хук обязаны говорить одно и то же.

    Расходились они дважды и в обе стороны: сначала журнал метил
    force-push мягким W, потом - после починки - метил жёстким P и
    `--force-with-lease`, который хук сам предлагает как безопасную замену.
    Инвариант проверяет обе стороны сразу, а не ту, что вспомнилась.
    """

    GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "guard.py")

    # Что хук блокирует, то журнал обязан писать классом P; что пропускает -
    # классом ниже. Формы взяты по смыслу: +ветка это force, :ветка - удаление.
    FORMS = [
        "git push --force origin main",
        "git push -f",
        "git push origin +main",
        "git push --mirror origin",
        "git push origin --delete feature",
        "git push origin :feature",
        "git push --force-with-lease origin main",
        "git push origin main",
        "git push origin main:main",
        "git push -u origin feature",
        "git push --follow-tags",
    ]

    def hook_blocks(self, command):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "."}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", self.GUARD],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.returncode == 2:
            return True
        out = proc.stdout.decode("utf-8").strip()
        if not out:
            return False
        decision = json.loads(out)["hookSpecificOutput"].get("permissionDecision")
        return decision == "deny"

    def journal_risk(self, command):
        sys.path.insert(0, os.path.normpath(os.path.dirname(self.GUARD)))
        import approval_log
        found = approval_log.classify({"tool_name": "Bash",
                                       "tool_input": {"command": command}})
        return found[0] if found else None

    def test_blocked_pushes_are_privileged_and_allowed_ones_are_not(self):
        for command in self.FORMS:
            blocked = self.hook_blocks(command)
            risk = self.journal_risk(command)
            with self.subTest(command=command, blocked=blocked):
                if blocked:
                    self.assertEqual(risk, "P",
                                     "хук блокирует, журнал обязан писать P")
                else:
                    self.assertNotEqual(risk, "P",
                                        "хук пропускает - P завышает риск и "
                                        "обесценивает метку там, где она нужна")


if __name__ == "__main__":
    unittest.main(verbosity=2)
