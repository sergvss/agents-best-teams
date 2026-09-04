#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты документации: ссылки, якоря и числа, которые в ней названы.

Почему это лежит рядом с тестами хуков, а не отдельно: сюда смотрит
`unittest discover`, то есть и локальный прогон, и CI. Отдельный шаг в
CI пришлось бы не забыть добавить, а забытая проверка — это ровно то,
против чего написан принцип 09.

Проверяется то, что ломается тихо. Битая ссылка не роняет сборку и не
видна автору, который её и поставил: он-то знает, куда хотел сослаться.
Число, названное в README, устаревает молча и через выпуск начинает
врать — за одну сессию так разошлись счётчик тестов и число ролей.

Запуск:
    python -X utf8 hooks/tests/test_docs.py
"""

import io
import os
import re
import subprocess
import unittest

REPO = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
)

# Ссылка на markdown-файл или каталог: то, что можно проверить на диске.
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRS = {".git", "node_modules", ".maintainer"}


def markdown_files():
    for base, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.endswith(".md"):
                yield os.path.join(base, name)


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def anchor(title):
    """Якорь GitHub: нижний регистр, пунктуация выброшена, пробелы в дефисы."""
    slug = re.sub(r"[^\w\s-]", "", title.strip().lower(), flags=re.U)
    return re.sub(r"\s+", "-", slug)


class TestLinks(unittest.TestCase):
    def test_local_links_point_at_existing_files(self):
        for path in markdown_files():
            base = os.path.dirname(path)
            for target in LINK.findall(read(path)):
                target = target.split("#")[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                with self.subTest(doc=os.path.relpath(path, REPO), link=target):
                    self.assertTrue(
                        os.path.exists(os.path.join(base, target)),
                        "ссылка ведёт в никуда",
                    )


class TestReadmeAnchors(unittest.TestCase):
    """
    Якоря навигации в шапке. Ломаются при переименовании раздела, и это
    единственное место, где переименование не оставляет других следов.
    """

    def test_navigation_anchors_resolve(self):
        for name in ("README.md", "README.ru.md"):
            text = read(os.path.join(REPO, name))
            headings = {anchor(t) for t in re.findall(r"^#{2,4}\s+(.+)$", text, re.M)}
            links = set(re.findall(r'href="#([^"]+)"', text))
            links |= set(re.findall(r"\]\(#([^)]+)\)", text))
            for link in sorted(links):
                with self.subTest(readme=name, anchor=link):
                    self.assertIn(link, headings, "нет раздела с таким якорем")


class TestNothingPrivateLeaked(unittest.TestCase):
    """
    Репозиторий публичный, и утечка в нём необратима: коммит остаётся в
    истории и в форках даже после правки. Поэтому проверяется не «нет ли
    сейчас», а «не появилось ли» — на каждом прогоне.

    Проверяются только отслеживаемые файлы: то, что лежит рядом, но не в git,
    в публичный доступ не попадает.
    """

    def setUp(self):
        out = subprocess.run(["git", "ls-files"], cwd=REPO, stdout=subprocess.PIPE,
                             text=True, encoding="utf-8").stdout
        self.tracked = [f for f in out.split("\n") if f.strip()]
        self.assertTrue(self.tracked, "git ls-files ничего не вернул")

    def _lines(self):
        for name in self.tracked:
            path = os.path.join(REPO, name)
            if not os.path.exists(path):
                continue
            try:
                # errors="replace", а не strict: в репозитории есть бинарные
                # файлы (иконка), и падать на них проверка не должна —
                # незапустившаяся проверка хуже отсутствующей.
                with io.open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if "\x00" in text[:4096]:
                continue   # бинарный файл, искать в нём строки бессмысленно
            for number, line in enumerate(text.splitlines(), 1):
                yield name, number, line

    def test_no_credentials(self):
        secret = re.compile(
            r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
        for name, number, line in self._lines():
            with self.subTest(where="{}:{}".format(name, number)):
                self.assertIsNone(secret.search(line), "похоже на ключ или токен")

    def test_no_personal_paths(self):
        # Обобщённые имена из примеров — это документация, а не утечка.
        placeholder = {"user", "you", "username", "youruser", "someone", "name", "me", "dev"}
        home = re.compile(r"(?:C:[\\/]{1,2}Users[\\/]{1,2}|/home/|/Users/)([A-Za-z0-9_.-]+)")
        for name, number, line in self._lines():
            for match in home.finditer(line):
                if match.group(1).lower() in placeholder:
                    continue
                self.fail("личный путь в {}:{} — {}".format(name, number, match.group(0)))


class TestCountsMatchReality(unittest.TestCase):
    """
    Числа, названные в README, против того, что лежит на диске.

    Называть точные числа полезно — они говорят о масштабе. Опасно другое:
    что число названо один раз и живёт своей жизнью. Здесь оно перестаёт
    жить своей жизнью.
    """

    def setUp(self):
        self.actual = {
            "принципы": len([n for n in os.listdir(os.path.join(REPO, "principles"))
                             if n.endswith(".md")]),
            "шаблоны": len([n for n in os.listdir(os.path.join(REPO, "templates"))
                            if n.endswith(".md") and n != "README.md"]),
            "чек-листы": len([n for n in os.listdir(os.path.join(REPO, "checklists"))
                              if n.endswith(".md")]),
        }

    def test_readme_numbers(self):
        cases = [
            ("README.md", r"(\d+)\s+principles", "принципы"),
            ("README.md", r"(\d+)\s+role templates", "шаблоны"),
            ("README.md", r"(\d+)\s+checklists", "чек-листы"),
            ("README.ru.md", r"(\d+)\s+принцип", "принципы"),
            ("README.ru.md", r"(\d+)\s+шаблонов ролей", "шаблоны"),
            ("README.ru.md", r"(\d+)\s+чек-листов", "чек-листы"),
        ]
        for name, pattern, key in cases:
            text = read(os.path.join(REPO, name))
            found = {int(m) for m in re.findall(pattern, text)}
            with self.subTest(readme=name, what=key):
                self.assertTrue(found, "число не найдено — изменилась формулировка?")
                self.assertEqual(
                    found, {self.actual[key]},
                    "в тексте {}, на диске {}".format(sorted(found), self.actual[key]),
                )

    def test_role_index_lists_every_template(self):
        index = read(os.path.join(REPO, "templates", "README.md"))
        for name in sorted(os.listdir(os.path.join(REPO, "templates"))):
            if not name.endswith(".md") or name == "README.md":
                continue
            with self.subTest(role=name):
                self.assertIn(name, index, "роли нет в индексе templates/README.md")

class TestOnlyRolesGetInstalledAsRoles(unittest.TestCase):
    """
    В templates/ лежат не только роли, и инструкция установки обязана это знать.

    Маска cp -r templates/* клала в .claude/agents/ ещё и external-llm-reviewer -
    паттерн вызова внешней модели без единого инструмента, но с полем name.
    Он регистрировался как роль, которую оркестратору некуда позвать.
    Тест ловит следующий такой файл, а не этот: этот уже исправлен.
    """

    # Файлы каталога, которые ролями не являются. Список закрытый: новый
    # не-роль обязан попасть и сюда, и в инструкцию установки.
    NOT_ROLES = {"README.md", "external-llm-reviewer.md"}

    def templates(self):
        base = os.path.join(REPO, "templates")
        return [n for n in sorted(os.listdir(base)) if n.endswith(".md")]

    def test_every_role_declares_tools(self):
        # Опознавательный признак роли - поле tools. Без него агент получает
        # инструменты по умолчанию, то есть матрица разрешений не действует.
        for name in self.templates():
            if name in self.NOT_ROLES:
                continue
            with self.subTest(template=name):
                text = read(os.path.join(REPO, "templates", name))
                self.assertRegex(text, r"(?m)^tools:",
                                 "шаблон без поля tools - это не роль")

    def test_non_roles_are_excluded_from_the_install_command(self):
        install = read(os.path.join(REPO, "docs", "install.md"))
        for name in self.NOT_ROLES:
            with self.subTest(template=name):
                self.assertIn(name, install,
                              "не-роль обязана быть исключена из установки явно")

    def test_the_list_of_non_roles_is_current(self):
        # Обратная сторона: файл из списка мог стать ролью или исчезнуть,
        # и тогда исключение в инструкции установки лишнее.
        present = set(self.templates())
        for name in self.NOT_ROLES:
            with self.subTest(template=name):
                self.assertIn(name, present, "файла нет - исключение устарело")
                if name == "README.md":
                    continue
                text = read(os.path.join(REPO, "templates", name))
                self.assertNotRegex(text, r"(?m)^tools:",
                                    "у файла появились tools - он стал ролью")


if __name__ == "__main__":
    unittest.main(verbosity=2)
