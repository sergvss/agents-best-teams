#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты защитных хуков. Прогоняют guard.py как настоящий процесс — через stdin
и stdout, ровно так же, как его вызывает Claude Code.

Запуск:
    python -X utf8 hooks/tests/test_guard.py

Половина тестов проверяет, что хук НЕ срабатывает. Это не формальность:
хук с ложными срабатываниями пользователь отключает в первый же день, и защиты
не остаётся вовсе. Обоснование — principles/10-eval-suite.md.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "guard.py")

# Язык сообщений закреплён явно: иначе тесты сверяли бы текст с умолчанием,
# и смена умолчания ломала бы их, ничего не сломав по существу.
os.environ.setdefault("ABT_LANG", "en")


def run_hook(payload, rules=None):
    """Вызывает guard.py и возвращает решение: None = разрешено, строка = причина отказа."""
    cmd = [sys.executable, "-X", "utf8", GUARD]
    if rules is not None:
        # Именно is not None: пустая строка — валидный тестовый случай.
        cmd += ["--rules", rules]
    proc = subprocess.run(
        cmd,
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout.decode("utf-8").strip()
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def bash(command, agent=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent:
        payload["agent_type"] = agent
    return payload


def edit(file_path, tool="Edit", agent=None):
    payload = {"tool_name": tool, "tool_input": {"file_path": file_path}}
    if agent:
        payload["agent_type"] = agent
    return payload


class GuardTestCase(unittest.TestCase):
    def assertBlocked(self, payload, marker=None):
        reason = run_hook(payload)
        self.assertIsNotNone(reason, "ожидалась блокировка, команда прошла: {}".format(payload))
        if marker:
            self.assertIn(marker, reason)

    def assertAllowed(self, payload):
        reason = run_hook(payload)
        self.assertIsNone(reason, "ожидалось разрешение, но заблокировано:\n{}".format(reason))


class TestFilesystem(GuardTestCase):
    def test_blocks_dangerous_targets(self):
        for command in [
            "rm -rf /",
            "rm -rf .",
            "rm -rf ..",
            "rm -rf ~",
            "rm -rf *",
            "rm -rf ./",
            "rm -fr /",
            "rm -Rf /*",
            "sudo rm -rf /",
            "rm --recursive --force /",
            "cd /tmp && rm -rf .",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_sees_through_command_wrappers(self):
        # Обёртка со своими флагами не должна прятать команду от правила.
        for command in [
            "sudo rm -rf /",
            "sudo -u root rm -rf /",
            "sudo -i rm -rf /",
            "env FOO=bar rm -rf /",
            "env FOO=bar BAZ=qux rm -rf .",
            "nohup rm -rf ~",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_wrappers_do_not_break_safe_commands(self):
        for command in ["sudo ls -la", "env FOO=bar npm test", "sudo -u postgres psql -c 'SELECT 1'"]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))

    def test_blocks_variable_in_target(self):
        for command in ['rm -rf "$BUILD_DIR/"', "rm -rf $HOME", "rm -rf %TEMP%"]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "variable")

    def test_allows_specific_paths(self):
        for command in [
            "rm -rf ./build",
            "rm -rf node_modules",
            "rm -rf /tmp/agents-test-dir",
            "rm -f single-file.txt",
            "rm -r some-dir",
            "ls -la",
            'echo "rm -rf /"',
            "grep -rn 'rm -rf' docs/",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))


class TestGit(GuardTestCase):
    def test_blocks_destructive(self):
        for command, marker in [
            ("git push --force origin main", "force"),
            ("git push -f", "force"),
            ("git -C /repo push --force", "force"),
            ("git reset --hard HEAD~1", "reset --hard"),
            ("git clean -fdx", "git clean"),
            ("git clean -fd", "git clean"),
            ("git checkout -- .", "checkout"),
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), marker)

    def test_blocks_force_by_alternative_syntax(self):
        # Плюс перед рефспеком — тот же force, только другим синтаксисом,
        # а --mirror ещё и удаляет на сервере ветки, которых нет локально.
        for command in [
            "git push origin +main",
            "git push origin +refs/heads/main:refs/heads/main",
            "git push --mirror origin",
            "sudo git push --force",
            # Удаление ветки на сервере — класс P по principles/03.
            "git push origin --delete feature",
            "git push origin :feature",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_allows_safe(self):
        for command in [
            "git push --force-with-lease origin main",
            "git push origin main",
            "git push origin main:main",
            "git push --tags",
            "git push origin HEAD:refs/heads/feature",
            "git push -u origin feature",
            "git push --follow-tags",
            "git reset --soft HEAD~1",
            "git reset HEAD file.txt",
            "git clean -n",
            "git clean -ndx",
            "git clean -i",
            "git checkout -- src/file.js",
            "git checkout main",
            "git status",
            "git diff --stat",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))


class TestSql(GuardTestCase):
    def test_blocks_unsafe(self):
        for command in [
            'psql -c "DELETE FROM users"',
            'mysql -e "UPDATE users SET active = 0"',
            'psql -c "TRUNCATE TABLE logs"',
            'sqlite3 app.db "DROP TABLE users"',
            'psql -c "DROP DATABASE prod"',
            # Клиент по абсолютному пути и с расширением .exe — тот же клиент.
            '/usr/bin/psql -c "DELETE FROM users"',
            'C:/tools/psql.exe -c "DELETE FROM users"',
            '"C:\\tools\\psql.exe" -c "DELETE FROM users"',
            'sudo -u postgres psql -c "DELETE FROM users"',
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_allows_safe(self):
        for command in [
            'psql -c "DELETE FROM users WHERE id = 1"',
            'mysql -e "UPDATE users SET active = 0 WHERE id = 5"',
            'psql -c "SELECT * FROM users"',
            'sqlite3 app.db "SELECT count(*) FROM users"',
        ]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))

    def test_does_not_fire_outside_db_clients(self):
        # Ключевая проверка на ложное срабатывание: слово DELETE в тексте
        # не должно блокировать поиск по коду или правку документации.
        for command in [
            'grep -rn "DELETE FROM users" src/',
            'echo "UPDATE users SET x=1"',
            "rg 'TRUNCATE TABLE' migrations/",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))


class TestEnv(GuardTestCase):
    def test_blocks_writes(self):
        for path, tool in [
            (".env", "Edit"),
            ("/home/user/proj/.env", "Write"),
            (".env.production", "Edit"),
            ("C:\\projects\\app\\.env.local", "Edit"),
        ]:
            with self.subTest(path=path):
                self.assertBlocked(edit(path, tool), "P/Privileged")

    def test_allows_examples_and_normal_files(self):
        for path in [".env.example", ".env.sample", "src/config.py", "docs/env.md", "environment.yml"]:
            with self.subTest(path=path):
                self.assertAllowed(edit(path))

    def test_read_is_not_touched(self):
        # Хук висит на Edit и Write; чтение .env остаётся разрешённым.
        self.assertAllowed(edit(".env", tool="Read"))

    def test_blocks_writes_through_shell(self):
        # Без этой половины правило бесполезно: перенаправление вывода пишет
        # в файл мимо инструментов Edit и Write.
        for command in [
            "echo API_KEY=hacked > .env",
            "echo X=1 >> .env",
            "cat /dev/null > .env",
            "printf '' > config/.env.local",
            "sed -i s/a/b/ .env",
            "rm .env",
            "rm -f .env.production",
            "cp .env.example .env",
            "mv other.txt .env",
            "tee .env",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_shell_reads_and_examples_pass(self):
        for command in [
            "cat .env",
            "grep API_KEY .env",
            "source .env",
            "echo TEMPLATE=1 > .env.example",
            "cp .env.example .env.sample",
            "rm build/output.txt",
            "sed -i s/a/b/ src/app.py",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))


class TestAgentMemory(GuardTestCase):
    def test_blocks_roles_without_write_rights(self):
        for path, tool, agent in [
            ("src/app.py", "Edit", "code-reviewer"),
            ("notes.md", "Write", "code-reviewer"),
            ("src/app.py", "Edit", "pm-orchestrator"),
            ("newfile.txt", "Write", "devops"),
            ("newfile.txt", "Write", "local-sysops"),
            ("src/app.js", "Edit", "browser-tester"),
            ("src/app.js", "Write", "browser-tester"),
            ("report.md", "Write", "finops-engineer"),
            ("src/app.py", "Edit", "unit-economics-analyst"),
            ("metrics.md", "Write", "investment-analyst"),
            ("package.json", "Edit", "vendor-auditor"),
        ]:
            with self.subTest(agent=agent, tool=tool, path=path):
                self.assertBlocked(edit(path, tool, agent), agent)

    def test_allows_own_memory_directory(self):
        for agent in ["code-reviewer", "pm-orchestrator", "devops", "local-sysops", "browser-tester",
                      "finops-engineer", "unit-economics-analyst", "investment-analyst",
                      "vendor-auditor"]:
            path = ".claude/agent-memory/{}/MEMORY.md".format(agent)
            with self.subTest(agent=agent):
                self.assertAllowed(edit(path, "Write", agent))
                self.assertAllowed(edit(path, "Edit", agent))

    def test_does_not_allow_foreign_memory_directory(self):
        # Роль не должна писать в память чужой роли.
        self.assertBlocked(
            edit(".claude/agent-memory/dev-backend/MEMORY.md", "Edit", "code-reviewer"),
            "code-reviewer",
        )

    def test_allows_role_specific_zones(self):
        # devops и local-sysops правят существующие файлы — Edit им положен.
        self.assertAllowed(edit("CHANGELOG.md", "Edit", "devops"))
        self.assertAllowed(edit("docker-compose.yml", "Edit", "local-sysops"))
        # browser-tester пишет тест-артефакты.
        self.assertAllowed(edit("tests/e2e/login.spec.js", "Write", "browser-tester"))

    def test_ignores_agents_outside_matrix(self):
        self.assertAllowed(edit("src/app.py", "Edit", "dev-backend"))
        self.assertAllowed(edit("src/app.py", "Edit", "qa-tester"))

    def test_blocks_writes_through_shell(self):
        # Найдено на живом прогоне: инструмент Write у роли заблокирован, а
        # `cat > файл` пишет тот же файл мимо проверки. Платформа после
        # блокировки Write сама предлагает агенту перейти на Bash.
        for command, agent in [
            ("cat > CHANGELOG.md <<EOF\ntext\nEOF", "devops"),
            ("echo x > VERSION", "devops"),
            ("touch newfile.txt", "local-sysops"),
            ("cp src/a.py src/b.py", "code-reviewer"),
            ("sed -i s/a/b/ src/app.py", "pm-orchestrator"),
            ("rm src/app.js", "browser-tester"),
        ]:
            with self.subTest(agent=agent, command=command):
                self.assertBlocked(bash(command, agent), agent)

    def test_shell_writes_inside_own_zone_pass(self):
        for command, agent in [
            ("echo x > .claude/agent-memory/devops/MEMORY.md", "devops"),
            ("echo x > ./.claude/agent-memory/devops/notes.md", "devops"),
            ("echo x > .claude/agent-memory/code-reviewer/gotcha.md", "code-reviewer"),
            ("echo x > tests/e2e/shot.png", "browser-tester"),
            # Чтение и обычные команды роли не ограничены.
            ("git status", "devops"),
            ("cat CHANGELOG.md", "devops"),
            ("python -m unittest discover", "devops"),
            # Роль вне матрицы правило не трогает.
            ("echo x > server/app.py", "dev-backend"),
        ]:
            with self.subTest(agent=agent, command=command):
                self.assertAllowed(bash(command, agent))

    def test_ignores_main_thread(self):
        # Вне субагента поле agent_type отсутствует — правила ролей не применяются.
        self.assertAllowed(edit("src/app.py", "Edit"))


class TestContract(GuardTestCase):
    def test_empty_and_malformed_input_do_not_crash(self):
        for raw in [b"", b"   ", b"not json"]:
            with self.subTest(raw=raw):
                proc = subprocess.run(
                    [sys.executable, "-X", "utf8", GUARD],
                    input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout.decode("utf-8").strip(), "")

    def test_rules_can_be_disabled_selectively(self):
        # С выключенным правилом git та же команда должна проходить.
        self.assertIsNotNone(run_hook(bash("git push --force"), rules="fs,git,sql"))
        self.assertIsNone(run_hook(bash("git push --force"), rules="fs,sql"))

    def test_misconfiguration_fails_closed(self):
        # Опечатка и пустой список — два способа молча остаться без защиты.
        # Хук обязан отказать в обоих случаях, а не пропустить команду.
        for rules, marker in [("fs,gti", "do not exist"), ("", "empty"), ("   ", "empty")]:
            with self.subTest(rules=repr(rules)):
                reason = run_hook(bash("ls -la"), rules=rules)
                self.assertIsNotNone(reason, "конфигурация принята молча")
                self.assertIn(marker, reason)

    def test_deny_payload_shape(self):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", GUARD],
            input=json.dumps(bash("rm -rf /")).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, "хук обязан выходить с кодом 0")
        out = json.loads(proc.stdout.decode("utf-8"))
        block = out["hookSpecificOutput"]
        self.assertEqual(block["hookEventName"], "PreToolUse")
        self.assertEqual(block["permissionDecision"], "deny")
        self.assertTrue(block["permissionDecisionReason"].startswith("BLOCKED ["))


class TestShellParsingBypasses(GuardTestCase):
    """
    Обходы, найденные триангулированным ревью. Общий корень у всех один:
    наивный разбор команды. Каждый случай проверялся вручную и подтверждался
    до того, как попасть сюда.
    """

    def test_command_separators(self):
        for command in [
            "echo ok\nrm -rf /",          # перевод строки — тоже разделитель
            "echo ok\r\nrm -rf /",
            "true & rm -rf /",            # одиночный & запускает вторую команду
            "cd /tmp; rm -rf .",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_nested_shell_invocation(self):
        for command in [
            "sh -c 'rm -rf /'",
            'bash -c "rm -rf /"',
            "sh -c 'sh -c \"rm -rf /\"'",
            "sudo sh -c 'git push --force'",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command))

    def test_absolute_and_prefixed_command_paths(self):
        for command in [
            "/bin/rm -rf /",
            "/usr/bin/git reset --hard",
            "LC_ALL=C rm -rf /",          # присваивание без env перед командой
            "CI=true git reset --hard",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command))

    def test_git_forms_missed_before(self):
        for command in [
            "git clean --force --dir",    # длинные флаги
            "git checkout -- . README",   # второй путь снимал блокировку
            "git restore .",              # современная замена checkout --
            "git restore --worktree .",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "W/Write")

    def test_sql_statement_level_checks(self):
        for command in [
            # WHERE в подзапросе не защищает внешний UPDATE
            'psql -c "UPDATE t SET x = (SELECT y FROM s WHERE s.id = 1)"',
            # WHERE в первом операторе не оправдывает второй
            'psql -c "DELETE FROM a WHERE id = 1; DELETE FROM b"',
            # WHERE в комментарии — не WHERE
            'psql -c "DELETE FROM t /* WHERE id = 1 */"',
            'mysql -e "UPDATE `orders` SET paid = 1"',
        ]:
            with self.subTest(command=command):
                self.assertBlocked(bash(command), "P/Privileged")

    def test_quotes_do_not_cause_false_alarms(self):
        # Символ внутри кавычек — не оператор оболочки и не команда.
        for command in [
            "echo 'пример: > .env'",
            'git commit -m "чиним; тесты"',
            "psql -c \"SELECT 'DELETE FROM users'\"",
            "psql -c \"SELECT 'rm -rf /'\"",
            "echo 'sh -c rm -rf /'",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(bash(command))

    def test_path_traversal_out_of_allowed_zone(self):
        # Разрешённая зона проверяется по нормализованному пути.
        for path, tool, agent in [
            (".claude/agent-memory/code-reviewer/../../../src/app.py", "Edit", "code-reviewer"),
            ("tests/../src/app.js", "Write", "browser-tester"),
        ]:
            with self.subTest(path=path, agent=agent):
                self.assertBlocked(edit(path, tool, agent), agent)

    def test_rule_list_has_no_duplicates(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
        import guard  # noqa: E402
        self.assertEqual(
            len(guard.ALL_RULES), len(set(guard.ALL_RULES)),
            "правило env зарегистрировано в двух наборах и попадает в список дважды",
        )

    def test_deny_messages_do_not_suggest_blocked_alternatives(self):
        # Самопротиворечивость из principles/09: предложенная альтернатива
        # не должна сама блокироваться этим же хуком.
        reason = run_hook(bash('rm -rf "$BUILD_DIR"'))
        self.assertIsNotNone(reason)
        self.assertNotIn("${VAR:?", reason)


class TestPackaging(unittest.TestCase):
    """Упаковка плагина: манифесты, скиллы и конфигурации хуков не должны врать."""

    def setUp(self):
        self.root = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
        )
        sys.path.insert(0, os.path.join(self.root, "hooks"))
        import guard  # noqa: E402
        self.all_rules = set(guard.ALL_RULES)

    def _json(self, *parts):
        with open(os.path.join(self.root, *parts), encoding="utf-8") as fh:
            return json.load(fh)

    def test_manifests_are_valid_and_agree(self):
        plugin = self._json(".claude-plugin", "plugin.json")
        market = self._json(".claude-plugin", "marketplace.json")
        self.assertEqual(plugin["name"], "agents-best-teams")
        listed = [p["name"] for p in market["plugins"]]
        self.assertIn(plugin["name"], listed, "плагин не объявлен в маркетплейсе")
        # Путь к хукам из манифеста обязан существовать.
        hooks_path = plugin["hooks"].lstrip("./")
        self.assertTrue(os.path.exists(os.path.join(self.root, hooks_path)))

    def test_hook_configs_use_only_known_rules(self):
        # Опечатка здесь молча отключила бы защиту — ровно этот случай
        # уже был с правилом env в settings.example.json.
        for config in (("hooks", "hooks.json"), ("hooks", "settings.example.json")):
            data = self._json(*config)
            for entry in data["hooks"]["PreToolUse"]:
                for hook in entry["hooks"]:
                    args = hook.get("args", [])
                    if "--rules" in args:
                        rules = set(args[args.index("--rules") + 1].split(","))
                        with self.subTest(config=config[-1], rules=sorted(rules)):
                            self.assertTrue(
                                rules <= self.all_rules,
                                "неизвестные правила: {}".format(sorted(rules - self.all_rules)),
                            )

    def test_every_script_in_the_configs_exists(self):
        """
        Конфигурация обязана ссылаться только на существующие файлы.

        Отсутствующий скрипт не даёт ошибки: хук просто не запускается, а
        выглядит настроенным — то есть худший вид поломки из возможных.
        Проверка заодно держит в узде инструкцию установки: если в конфигурации
        появится новый хук, забыть про него в docs/install.md будет нельзя,
        потому что копируется вся маска hooks/*.py.
        """
        hooks_dir = os.path.normpath(os.path.dirname(GUARD))
        for config in ("hooks.json", "settings.example.json"):
            raw = json.dumps(self._json("hooks", config))
            for script in sorted(set(re.findall(r"([a-z_]+\.py)", raw))):
                with self.subTest(config=config, script=script):
                    self.assertTrue(
                        os.path.exists(os.path.join(hooks_dir, script)),
                        "конфигурация {} ссылается на несуществующий {}".format(config, script),
                    )

    def test_bash_matcher_enables_shell_side_env_rule(self):
        # Защита .env двусторонняя: без правила env на матчере Bash
        # запись через перенаправление вывода проходит насквозь.
        for config in (("hooks", "hooks.json"), ("hooks", "settings.example.json")):
            data = self._json(*config)
            for entry in data["hooks"]["PreToolUse"]:
                if entry["matcher"] == "Bash":
                    args = entry["hooks"][0]["args"]
                    rules = args[args.index("--rules") + 1].split(",")
                    with self.subTest(config=config[-1]):
                        self.assertIn("env", rules)

    def test_every_skill_is_well_formed(self):
        skills_dir = os.path.join(self.root, "skills")
        names = sorted(os.listdir(skills_dir))
        self.assertTrue(names, "нет ни одного скилла")
        for name in names:
            path = os.path.join(skills_dir, name, "SKILL.md")
            with self.subTest(skill=name):
                self.assertTrue(os.path.exists(path), "нет SKILL.md у скилла " + name)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                head = text.split("\n---", 2)[0]
                # re.M обязателен: frontmatter начинается с ---, и без него
                # ^ совпадал бы только с началом всего текста.
                self.assertTrue(
                    re.search(r"^name:\s*" + re.escape(name) + r"\s*$", head, re.M),
                    "имя в frontmatter не совпадает с каталогом",
                )
                self.assertTrue(
                    re.search(r"^description:\s*\S", head, re.M), "у скилла нет описания"
                )

    def test_checklist_skills_point_at_existing_files(self):
        # Скиллы намеренно тонкие: содержимое живёт в checklists/.
        # Ссылка на несуществующий файл сделала бы скилл пустым.
        skills_dir = os.path.join(self.root, "skills")
        for name in sorted(os.listdir(skills_dir)):
            with open(os.path.join(skills_dir, name, "SKILL.md"), encoding="utf-8") as fh:
                text = fh.read()
            for ref in re.findall(r"checklists/([\w-]+\.md)", text):
                with self.subTest(skill=name, ref=ref):
                    self.assertTrue(
                        os.path.exists(os.path.join(self.root, "checklists", ref)),
                        "скилл ссылается на несуществующий чек-лист " + ref,
                    )


class TestMatrixMatchesDocs(unittest.TestCase):
    """
    MEMORY_MATRIX в guard.py и таблица в permission-checklist описывают одно и то же.
    Разойтись они могут молча — этот тест не даёт.
    """

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(GUARD)))
        import guard  # noqa: E402
        self.matrix = guard.MEMORY_MATRIX
        checklist = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir,
            "checklists", "permission-checklist.md",
        )
        with open(checklist, encoding="utf-8") as fh:
            self.doc = fh.read()

    def test_agents_needing_hook_match(self):
        documented = set()
        for line in self.doc.splitlines():
            if line.strip().startswith("|") and "project + хук" in line:
                documented.add(line.strip().strip("|").split("|")[0].strip())
        self.assertEqual(
            documented,
            set(self.matrix),
            "роли, помеченные в permission-checklist как требующие хука, "
            "разошлись с MEMORY_MATRIX в guard.py",
        )

    def test_modes_table_matches_templates(self):
        """Таблица режимов в чек-листе не должна расходиться с frontmatter шаблонов."""
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
        templates_dir = os.path.join(root, "templates")

        declared = {}
        for line in self.doc.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 5 and os.path.exists(os.path.join(templates_dir, cells[0] + ".md")):
                declared[cells[0]] = cells[1:]

        self.assertTrue(declared, "не удалось разобрать таблицу режимов")

        for agent, (mode, turns, effort, memory) in declared.items():
            with open(os.path.join(templates_dir, agent + ".md"), encoding="utf-8") as fh:
                head = fh.read().split("\n---", 2)[0]
            # Разбираем скалярные поля построчно: так тест остаётся без зависимостей.
            fields = dict(
                re.findall(r"^(permissionMode|maxTurns|effort|memory):\s*(\S+)", head, re.M)
            )
            expected_memory = "project + хук" if agent in self.matrix else fields.get("memory", "—")
            with self.subTest(agent=agent):
                self.assertEqual(fields.get("permissionMode", "—"), mode)
                self.assertEqual(fields.get("maxTurns", "—"), turns)
                self.assertEqual(fields.get("effort", "—"), effort)
                self.assertEqual(expected_memory, memory)

    def test_every_matrix_agent_has_a_template(self):
        templates = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "templates",
        )
        for agent in self.matrix:
            path = os.path.join(templates, agent + ".md")
            with self.subTest(agent=agent):
                self.assertTrue(os.path.exists(path), "нет шаблона для роли " + agent)
                with open(path, encoding="utf-8") as fh:
                    head = fh.read().split("\n---", 2)[0]
                # Роль под правилом хука обязана иметь включённую память,
                # иначе правило охраняет то, чего нет.
                self.assertIn("memory: project", head, "у роли " + agent + " не включена память")

    def test_every_read_only_template_with_memory_is_in_matrix(self):
        """Обратная сверка: шаблон → матрица.

        Поле memory включает Read/Write/Edit в обход списка tools. Роль без
        Edit/Write в tools, но с памятью и без записи в матрице, молча получает
        право писать куда угодно — ровно та дыра, ради которой правило и есть.
        Проверка обратного направления нужна потому, что забыть строку в
        матрице при добавлении роли не мешает ничему и никак не проявляется.
        """
        templates = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "templates",
        )
        for name in sorted(os.listdir(templates)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(templates, name), encoding="utf-8") as fh:
                head = fh.read().split("\n---", 2)[0]
            if "memory: project" not in head:
                continue
            tools = re.search(r"^tools:(.*)$", head, re.M)
            tools = tools.group(1) if tools else ""
            if "Edit" in tools or "Write" in tools:
                continue  # роли с правом записи по списку tools правило не ограничивает
            agent = name[: -len(".md")]
            with self.subTest(agent=agent):
                self.assertIn(
                    agent, self.matrix,
                    "роль " + agent + " читающая, но с памятью — её нет в MEMORY_MATRIX, "
                    "поле memory выдаст ей Write/Edit в обход tools",
                )


class TestMessageCatalogue(unittest.TestCase):
    """
    Инвариант двуязычия. Каталог существует ровно затем, чтобы языки не
    разошлись, — а разойтись они могут только одним способом: кто-то добавит
    сообщение на одном языке и забудет про второй. Проверяем это, а не текст.
    """

    def setUp(self):
        sys.path.insert(0, os.path.normpath(os.path.dirname(GUARD)))
        import messages  # noqa: E402
        self.messages = messages

    def test_every_key_exists_in_every_language(self):
        for key, entry in self.messages.MESSAGES.items():
            for language in self.messages.SUPPORTED:
                with self.subTest(key=key, lang=language):
                    self.assertIn(language, entry, "нет перевода на " + language)
                    self.assertTrue(entry[language].strip(), "пустой текст")

    def test_placeholders_match_between_languages(self):
        # Разный набор подстановок — это TypeError в момент блокировки,
        # то есть отказ защиты ровно тогда, когда она нужна.
        for key, entry in self.messages.MESSAGES.items():
            fields = {
                language: set(re.findall(r"\{(\w+)\}", text))
                for language, text in entry.items()
            }
            with self.subTest(key=key):
                self.assertEqual(
                    len(set(map(frozenset, fields.values()))), 1,
                    "у ключа {} расходятся подстановки: {}".format(key, fields),
                )

    def test_hooks_use_only_existing_keys(self):
        # Опечатка в ключе — KeyError вместо блокировки. Проверяем все хуки.
        hooks_dir = os.path.normpath(os.path.dirname(GUARD))
        used = set()
        for name in os.listdir(hooks_dir):
            if not name.endswith(".py") or name == "messages.py":
                continue
            with open(os.path.join(hooks_dir, name), encoding="utf-8") as fh:
                used |= set(re.findall(r"msg\(\s*[\"']([\w.]+)[\"']", fh.read()))
        self.assertTrue(used, "в хуках не найдено ни одного вызова msg()")
        for key in sorted(used):
            with self.subTest(key=key):
                self.assertIn(key, self.messages.MESSAGES)

    def test_language_switches_the_block_text(self):
        env = dict(os.environ, ABT_LANG="ru")
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", GUARD],
            input=json.dumps(bash("rm -rf /")).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        russian = json.loads(proc.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecisionReason"]
        english = run_hook(bash("rm -rf /"))
        self.assertIn("Причина блокировки", russian)
        self.assertIn("Why this is blocked", english)
        # Правило одно и то же на обоих языках: блокируется в любом случае.
        self.assertIn("BLOCKED [P/Privileged]", russian)
        self.assertIn("BLOCKED [P/Privileged]", english)

    def test_project_file_selects_the_language(self):
        # Файл в проекте — это то, что пишет setup-agent-team.
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, ".claude"))
            with open(os.path.join(project, ".claude", ".abt-lang"), "w", encoding="utf-8") as fh:
                fh.write("ru\n")
            payload = dict(bash("rm -rf /"), cwd=project)
            env = {k: v for k, v in os.environ.items() if k != "ABT_LANG"}
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", GUARD],
                input=json.dumps(payload).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )
            reason = json.loads(proc.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("Причина блокировки", reason)

    def test_no_user_facing_text_left_in_hook_code(self):
        """
        Ни одной строки для человека вне каталога.

        Проверяется через AST, а не грепом: докстринги и комментарии — это
        пояснения для того, кто правит код, они остаются русскими намеренно.
        Ищется другое — строковый литерал с кириллицей, который может доехать
        до пользователя. Именно так нашлась подстановка «git push origin
        +<ветка>», уезжавшая внутрь английского сообщения.
        """
        import ast
        hooks_dir = os.path.normpath(os.path.dirname(GUARD))
        cyrillic = re.compile(r"[А-Яа-яЁё]")
        for name in sorted(os.listdir(hooks_dir)):
            if not name.endswith(".py") or name == "messages.py":
                continue
            with open(os.path.join(hooks_dir, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        docs.add(doc)
            leaked = [
                (node.lineno, node.value[:60])
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and cyrillic.search(node.value)
                and node.value not in docs
            ]
            with self.subTest(hook=name):
                self.assertFalse(
                    leaked,
                    "текст мимо каталога в {}: {}".format(name, leaked[:3]),
                )

    def test_guard_and_messages_are_self_sufficient_together(self):
        # Форма ручной установки: два файла в .claude/hooks/ и больше ничего.
        # Забыть messages.py при копировании — значит получить хук, который
        # не запускается вовсе; проверяем, что пары достаточно.
        hooks_dir = os.path.normpath(os.path.dirname(GUARD))
        with tempfile.TemporaryDirectory() as target:
            for name in ("guard.py", "messages.py"):
                shutil.copy(os.path.join(hooks_dir, name), target)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", os.path.join(target, "guard.py")],
                input=json.dumps(bash("rm -rf /")).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            self.assertIn("BLOCKED", proc.stdout.decode("utf-8"))

    def test_unknown_language_falls_back_instead_of_failing(self):
        env = dict(os.environ, ABT_LANG="de")
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", GUARD],
            input=json.dumps(bash("rm -rf /")).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.assertEqual(proc.returncode, 0)
        reason = json.loads(proc.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("BLOCKED", reason, "незнакомый язык не должен снимать защиту")


if __name__ == "__main__":
    unittest.main(verbosity=2)
