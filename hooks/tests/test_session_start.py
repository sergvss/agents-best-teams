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

# Язык закреплён явно и заодно снимает вопрос о выборе языка: с заданным
# ABT_LANG хук про язык не спрашивает, и тесты проверяют именно подсказку
# про сборку команды. Отдельный тест ниже снимает переменную намеренно.
os.environ.setdefault("ABT_LANG", "en")

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
    # Внутри сессии Claude Code её CLAUDE_PROJECT_DIR указал бы хуку этот
    # репозиторий вместо временного каталога теста.
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", HOOK],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
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
        self.assertIn("hooks", prompt)

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


class TestDuplicateInstallation(SessionStartTestCase):
    """
    Плагин и ручная копия одновременно.

    Замерено на живой установке: одна команда даёт две записи в журнале, одно
    падение поднимает счётчик повторов на два — то есть правило трёх попыток
    превращается в правило двух. Обе установки по отдельности исправны, и
    никаких других признаков поломка не подаёт, поэтому предупреждение должно
    быть громким и повторяться, пока дубль не убран.
    """

    def add_project_copy(self):
        hooks = os.path.join(self.cwd, ".claude", "hooks")
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(hooks, "guard.py"), "w", encoding="utf-8") as fh:
            fh.write("# копия хука в проекте\n")

    def test_warns_when_both_installations_present(self):
        self.add_project_copy()
        prompt = run_hook(self.cwd)
        self.assertIsNotNone(prompt)
        self.assertIn("agents-best-teams", prompt)
        self.assertIn(".claude/hooks/", prompt)

    def test_warning_survives_the_opt_out(self):
        # Опт-аут гасит подсказки, но не сообщение об испорченном поведении:
        # выключать предупреждение флагом от другой темы неправильно.
        self.add_project_copy()
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        with open(os.path.join(self.cwd, ".claude", ".no-team-setup-prompt"), "w") as fh:
            fh.write("")
        prompt = run_hook(self.cwd)
        self.assertIsNotNone(prompt, "предупреждение о дубле погашено опт-аутом")
        self.assertNotIn("setup-agent-team", prompt, "подсказки опт-аут гасить обязан")

    def test_no_warning_for_a_plain_manual_install(self):
        # Ручная установка — это и есть копия в проекте. Дубля нет.
        import shutil
        hooks = os.path.join(self.cwd, ".claude", "hooks")
        os.makedirs(hooks, exist_ok=True)
        src = os.path.normpath(os.path.join(os.path.dirname(HOOK)))
        for name in ("session_start.py", "messages.py", "guard.py"):
            shutil.copy(os.path.join(src, name), hooks)
        payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": self.cwd}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.join(hooks, "session_start.py")],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out = proc.stdout.decode("utf-8").strip()
        prompt = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
        self.assertNotIn("установлена дважды", prompt)
        self.assertNotIn("installed twice", prompt)

    def test_no_warning_without_a_project_copy(self):
        prompt = run_hook(self.cwd) or ""
        self.assertNotIn("installed twice", prompt)


class TestLanguagePrompt(SessionStartTestCase):
    """
    Вопрос о языке. Он нужен ровно один раз и не должен превращаться
    в ещё одну подсказку, которую учатся пролистывать.
    """

    def run_without_lang(self, cwd, lang_file=None):
        """Прогон с намеренно снятым ABT_LANG — иначе язык уже задан."""
        if lang_file is not None:
            os.makedirs(os.path.join(cwd, ".claude"), exist_ok=True)
            with open(os.path.join(cwd, ".claude", ".abt-lang"), "w", encoding="utf-8") as fh:
                fh.write(lang_file)
        payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": cwd}
        env = {k: v for k, v in os.environ.items() if k != "ABT_LANG"}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", HOOK],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8").strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None

    def test_asks_when_language_is_not_chosen(self):
        prompt = self.run_without_lang(self.cwd)
        self.assertIsNotNone(prompt)
        self.assertIn(".abt-lang", prompt)

    def test_silent_about_language_once_the_file_exists(self):
        # Команда развёрнута и язык выбран — сказать нечего.
        self.add_role("dev-backend")
        self.assertIsNone(self.run_without_lang(self.cwd, lang_file="ru\n"))

    def test_garbage_in_the_file_counts_as_not_chosen(self):
        # Файл с мусором хуже отсутствующего: он выглядит настроенным.
        self.add_role("dev-backend")
        prompt = self.run_without_lang(self.cwd, lang_file="deutsch\n")
        self.assertIsNotNone(prompt)
        self.assertIn(".abt-lang", prompt)

    def test_language_question_does_not_replace_the_team_offer(self):
        # В новом проекте поводов два, и оба должны дойти до агента.
        prompt = self.run_without_lang(self.cwd)
        self.assertIn(".abt-lang", prompt)
        self.assertIn("setup-agent-team", prompt)


class TestOptOutSilencesOnlyWhatItPromises(SessionStartTestCase):
    """
    Файл называется .no-team-setup-prompt и обещает выключить предложение
    собрать команду. Он же гасил заодно вопрос о языке - ранний return стоял
    выше языкового блока, - и человек оставался на умолчании, которого не
    выбирал, не узнав, что выбор вообще был.
    """

    def run_without_lang(self, cwd):
        """Прогон с намеренно снятым ABT_LANG - иначе язык уже задан."""
        payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": cwd}
        env = {k: v for k, v in os.environ.items() if k != "ABT_LANG"}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", HOOK],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8").strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None

    def opt_out(self):
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        open(os.path.join(self.cwd, ".claude", ".no-team-setup-prompt"), "w").close()

    def test_language_question_survives_the_opt_out(self):
        self.opt_out()
        prompt = self.run_without_lang(self.cwd)
        self.assertIsNotNone(prompt, "опт-аут не должен гасить вопрос о языке")
        self.assertIn(".abt-lang", prompt)

    def test_team_prompt_is_the_one_that_goes_silent(self):
        self.opt_out()
        prompt = self.run_without_lang(self.cwd) or ""
        self.assertNotIn("setup-agent-team", prompt)

    def test_opt_out_is_fully_silent_once_language_is_chosen(self):
        # Оба повода одноразовые: когда язык задан, опт-аут молчит целиком.
        self.opt_out()
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        with open(os.path.join(self.cwd, ".claude", ".abt-lang"), "w", encoding="utf-8") as fh:
            fh.write("ru")
        self.assertIsNone(self.run_without_lang(self.cwd))


class TestProjectConfigProblems(SessionStartTestCase):
    """
    Ошибки в .claude/agents-best-teams.json.

    Хук при ошибке молча откатывается на умолчания, чтобы не запереть работу.
    Молча - только для хука: человек обязан узнать, что его настройка не
    действует, иначе он будет считать зону расширенной, а она нет.
    """

    def configure(self, content):
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        with open(os.path.join(self.cwd, ".claude", "agents-best-teams.json"), "w",
                  encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))

    def test_problems_are_reported_at_session_start(self):
        self.configure({"memory_matrix": {"code-reviewer": "create"}, "qa_tester_dirs": ["x"]})
        prompt = run_hook(self.cwd) or ""
        self.assertIn("agents-best-teams.json", prompt)
        self.assertIn("code-reviewer", prompt)
        self.assertIn("qa_tester_dirs", prompt)

    def test_unreadable_file_is_reported(self):
        self.configure("{not json")
        self.assertIn("agents-best-teams.json", run_hook(self.cwd) or "")

    def test_valid_file_adds_nothing_to_say(self):
        self.configure({"qa_tester_write_segments": ["integration"]})
        self.assertNotIn("agents-best-teams.json", run_hook(self.cwd) or "")


class TestVerifyReminder(SessionStartTestCase):
    """
    Напоминание включить verify.py, когда тесты появились (задача #6).

    Домашний каталог подменяется временным: иначе результат зависел бы от того,
    включён ли verify.py в настройках человека, запустившего тесты.
    """

    def setUp(self):
        super().setUp()
        self._home = tempfile.TemporaryDirectory()
        self._saved = {key: os.environ.get(key)
                       for key in ("HOME", "USERPROFILE", "CLAUDE_PROJECT_DIR")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self._home.name
        # Внутри сессии Claude Code эта переменная указала бы хуку чужой проект.
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.add_role("code-reviewer")

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._home.cleanup()
        super().tearDown()

    def touch(self, *parts, content=""):
        path = os.path.join(self.cwd, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_reminds_when_tests_exist_without_verify(self):
        self.touch("tests", "test_app.py")
        prompt = run_hook(self.cwd) or ""
        self.assertIn("verify.py", prompt)
        self.assertIn(".claude/.no-verify-prompt", prompt)

    def test_tests_dir_on_the_second_level_counts(self):
        # Живой прогон: в песочнице тесты в client/tests/ и server/tests/, и
        # напоминание не срабатывало - на втором уровне искались только файлы.
        self.touch("server", "tests", "test_app.py")
        self.assertIn("verify.py", run_hook(self.cwd) or "")

    def test_test_file_next_to_code_counts(self):
        self.touch("src", "Button.test.tsx")
        self.assertIn("verify.py", run_hook(self.cwd) or "")

    def test_silent_without_tests(self):
        self.touch("src", "app.py")
        self.assertNotIn("verify.py", run_hook(self.cwd) or "")

    def test_dependencies_do_not_count_as_tests(self):
        self.touch("node_modules", "pkg.test.js")
        self.assertNotIn("verify.py", run_hook(self.cwd) or "")

    def test_silent_when_verify_is_configured_in_the_project(self):
        self.touch("tests", "test_app.py")
        self.touch(".claude", "settings.json", content='{"hooks": {"Stop": [{"hooks": '
                   '[{"args": [".claude/hooks/verify.py", "--command", "pytest"]}]}]}}')
        self.assertNotIn("verify.py", run_hook(self.cwd) or "")

    def test_silent_when_verify_is_configured_for_the_user(self):
        self.touch("tests", "test_app.py")
        os.makedirs(os.path.join(self._home.name, ".claude"))
        with open(os.path.join(self._home.name, ".claude", "settings.json"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"hooks": {"Stop": [{"hooks": [{"args": ["verify.py"]}]}]}}')
        self.assertNotIn("verify.py", run_hook(self.cwd) or "")

    def store(self, mapping):
        directory = os.path.join(self._home.name, ".claude", "agents-best-teams")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "verify.json"), "w", encoding="utf-8") as fh:
            json.dump(mapping, fh)

    def test_silent_when_the_user_file_has_a_command(self):
        # Путь плагина: команда в файле пользователя, хук подключён плагином.
        self.touch("tests", "test_app.py")
        self.store({self.cwd: "npm test"})
        self.assertNotIn("verify.py", run_hook(self.cwd) or "")

    def test_repository_file_does_not_count_as_enabled(self):
        # Ключ в файле проекта verify.py не читает - и напоминание не должно
        # считать хук включённым.
        self.touch("tests", "test_app.py")
        self.touch(".claude", "agents-best-teams.json", content='{"verify_command": "npm test"}')
        self.assertIn("verify.py", run_hook(self.cwd) or "")

    def test_reminder_names_the_user_file_and_the_project(self):
        self.touch("tests", "test_app.py")
        prompt = run_hook(self.cwd) or ""
        self.assertIn("agents-best-teams/verify.json", prompt)
        self.assertIn(os.path.basename(self.cwd).lower(), prompt.lower())

    def test_own_opt_out_silences_only_this_reminder(self):
        self.touch("tests", "test_app.py")
        self.touch(".claude", ".no-verify-prompt")
        self.assertNotIn("verify.py", run_hook(self.cwd) or "")

    def test_team_opt_out_does_not_silence_it(self):
        self.touch("tests", "test_app.py")
        self.touch(".claude", ".no-team-setup-prompt")
        self.assertIn("verify.py", run_hook(self.cwd) or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
