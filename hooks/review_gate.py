#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_gate.py — шлюз ревью для коммитов кода.

Шаг «Ревью» жил только в тексте: checklists/implementation-path.md и роль
pm-orchestrator. Главный агент, который вёл задачу сам, сделал десяток коммитов
кода без единого ревью, и регрессия дошла до владельца (задача #6). Принцип 09:
повторяющаяся ошибка закрывается кодом, а не ещё одной строкой в промпте.

Режим зависит от события, на котором стоит хук:

  PreToolUse Bash        `git commit` с изменениями кода без одобрения именно
                         этого состояния — окно подтверждения; маркер одобрения
                         не от code-reviewer — отказ; запись в файл одобрений —
                         отказ.
  PreToolUse Edit/Write  запись в файл одобрений — отказ.
  PostToolUse Bash       (--record) code-reviewer выполнил маркер — одобрение
                         текущего состояния записывается.

Маркер — `echo abt-review-approve "<что проверено>"`. Отдельный скрипт одобрения
роль не нашла бы: путь к плагину (CLAUDE_PLUGIN_ROOT) есть только в
конфигурации хуков, а не в Bash. Поэтому одобрение пишет сам хук и только если
команду выполнила роль code-reviewer: agent_type приходит от Claude Code, и
подделать его агент не может.

Одобрение привязано к содержимому, а не к тексту diff. Отпечаток — набор пар
«путь, хэш содержимого» для всего, что отличается от HEAD. Так одинаково
считаются индекс, рабочая копия при `commit -a` и `git add -A && git commit`
одной командой — на момент проверки индекс там ещё пуст, и сверка по
`git diff --cached` пропустила бы коммит. Любая правка после одобрения меняет
отпечаток, и нужно новое ревью.

Шлюз включается, только если в проекте есть роль code-reviewer: без неё
одобрение получить нельзя, и окно открывалось бы на каждый коммит.

Граница: хук ловит пропуск ревью по забывчивости, а не умысел. Запись в файл
одобрений через `python -c` он не видит — как и остальные правила, код внутри
интерпретатора он не разбирает.

Зависимости: только стандартная библиотека Python 3.8+ и git.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

# Тексты для человека — в каталоге сообщений, см. messages.py.
from messages import msg, use_project
# Разбор команды общий с guard.py: cd, sh -c и обёртки понимаются одинаково.
from guard import (
    GIT_GLOBAL_WITH_VALUE, MAX_NESTING, SHELL_WRAPPERS, WRITE_TOOLS, basename, comparable,
    confirm, deny, emit_confirmation, expand_assigned, is_absolute, next_workdir, note_platform_write,
    path_basename, project_root, remember_assignments, split_segments,
    strip_heredoc_bodies, strip_wrappers, written_paths,
)

REVIEWER = "code-reviewer"
MARKER = "abt-review-approve"
# Лежит в каталоге git, а не в рабочей копии: в коммит не попадает, у каждого
# worktree свой (git rev-parse --git-path).
APPROVALS_FILE = "abt-review-approvals.json"
MAX_APPROVALS = 50
# Шлюз включается файлом в проекте, по умолчанию выключен. Окно на каждый
# коммит без ревью раздражало пользователя во всех проектах с code-reviewer,
# а не только там, где он этого хотел (решение пользователя, 1.22.0). Удаление
# или перезапись файла - через окно: иначе агент выключил бы шлюз сам.
SWITCH = os.path.join(".claude", ".review-gate")
GIT_TIMEOUT_S = 8


# Коммит одной документации ревью не требует. *.txt сюда намеренно не входит:
# requirements.txt — зависимости, то есть код.
# VERSION - номер выпуска, а не код: коммит одной версии давал окно (окно пользователя).
DOC_PATTERNS = ("*.md", "*.mdx", "*.rst", "*.adoc", "LICENSE", "LICENSE.*", "VERSION")

# Широта того, что уйдёт в коммит: индекс, плюс изменения отслеживаемых файлов,
# плюс новые файлы. "unknown" — хук не может сказать, что именно уйдёт.
SCOPES = ("index", "tracked", "all")

# Ключи git commit, которые забирают значение.
COMMIT_SHORT_WITH_VALUE = set("mFCct")
# Короткие ключи с необязательным значением, приклеенным к ним: -uno, -S<key>.
COMMIT_SHORT_ATTACHED = set("uS")
COMMIT_LONG_WITH_VALUE = {
    "--message", "--file", "--reuse-message", "--reedit-message", "--template",
    "--author", "--date", "--fixup", "--squash", "--cleanup", "--trailer",
}

# Операторы перенаправления: разборщик режет `2>&1` на `2`, `>&`, `1`, и без
# фильтра номер дескриптора выглядел бы путём коммита.
# `<<` здесь же: в `git commit -F - <<'EOF'` ограничитель EOF иначе читался
# путём, и коммит с одобрением получал окно «не могу сверить» (живой прогон).
REDIRECTS = {">", ">>", ">|", "&>", ">&", "<", "<<", "<<-", "<<<"}

# Команды, которые ничего не меняют в рабочей копии. Любая другая перед коммитом
# в той же команде делает состояние несверяемым: отпечаток считается до запуска,
# и `echo x > new.py && git add -A && git commit` прошёл бы по старому состоянию.
READ_ONLY_COMMANDS = {
    "echo", "printf", "ls", "cat", "pwd", "true", "test", "[", "head", "tail",
    "grep", "wc", "which", "type", "date", "sleep", "tr", "cut", "sort", "uniq",
    "chcp", "pytest", "py.test",
}

# Запуски тестов файлы репозитория не меняют. Замер на 4507 настоящих
# коммитах агентов: 21% шли как `python -m pytest ... && git add ... && git
# commit`, и шлюз считал их несверяемыми. Ключи обновления снимков - запись,
# с ними прогон остаётся «неизвестной командой».
PYTHON_TEST_MODULES = {"pytest", "unittest", "pyflakes", "py_compile", "mypy", "flake8"}
NPM_TEST_SCRIPTS = {"test", "t"}
JS_TEST_TOOLS = {"vitest", "jest", "mocha"}
SNAPSHOT_UPDATE = {"-u", "--update", "--updateSnapshot", "--update-snapshots", "--snapshot-update",
                   "--fix", "--write"}
GIT_READ_ONLY = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "blame", "grep",
    "branch", "tag", "remote", "describe", "shortlog", "fetch",
}

# С --git-dir и --work-tree хук не знает, какой репозиторий коммитится, и
# считает коммит несверяемым. Ключи со значением - GIT_GLOBAL_WITH_VALUE из guard.py.
GIT_LOCATION_OPTIONS = {"--git-dir", "--work-tree"}

# Переменные окружения, которые подменяют репозиторий или индекс: с ними хук
# проверил бы один репозиторий, а git закоммитил бы другой.
GIT_ENV = re.compile(r"\bGIT_(?:DIR|WORK_TREE|INDEX_FILE|OBJECT_DIRECTORY|COMMON_DIR)=")

# Встроенные подкоманды git. Остальное может оказаться алиасом, в том числе на
# commit: `git ci` при alias.ci = commit прошёл бы мимо шлюза.
GIT_BUILTINS = GIT_READ_ONLY | {
    "add", "stage", "commit", "commit-tree", "rm", "mv", "restore", "reset", "apply",
    "update-index", "read-tree", "checkout", "switch", "stash", "cherry-pick", "merge",
    "revert", "am", "pull", "push", "clone", "init", "config", "gc", "fsck", "worktree",
    "submodule", "bisect", "notes", "archive", "clean", "rebase", "reflog", "show-ref",
    "for-each-ref", "cat-file", "hash-object", "ls-tree", "count-objects", "help",
    "version", "lfs", "mergetool", "difftool", "format-patch", "sparse-checkout",
    "maintenance", "range-diff", "rerere", "prune", "repack", "bundle", "update-ref",
    "symbolic-ref", "write-tree", "mktree", "check-ignore", "check-attr", "name-rev",
    "cherry", "whatchanged", "verify-commit", "var",
}


class GitStateError(Exception):
    """Репозиторий есть, но git не смог описать его состояние."""


class Unverifiable(Exception):
    """Что уйдёт в коммит, по команде не вычислить: маска, путь вне репозитория."""



# ---------------------------------------------------------------------------
# Разбор команды
# ---------------------------------------------------------------------------
def native(path):
    """
    Путь Git Bash `/c/x` -> `C:/x` на Windows.

    Хук зовёт git без оболочки, и такой git пути `/c/...` не понимает: для
    `cd /c/проект && git commit` шлюз считал каталог «не репозиторием» и молча
    пропускал коммит без ревью (найдено разбором окна пользователя, 1.21.2).
    """
    if os.name == "nt":
        match = re.match(r"^/([A-Za-z])(/.*)?$", path.replace("\\", "/"))
        if match:
            return match.group(1).upper() + ":" + (match.group(2) or "/")
    return path


def resolve(base, path):
    """Каталог относительно base; None означает сам base."""
    if not path:
        return native(base)
    return native(path) if is_absolute(path) else os.path.join(native(base), path)


def segments(command, base, depth=0):
    """
    (токены, каталог, workdir) каждой простой команды — как в guard.analyze_bash.

    workdir — каталог после cd в виде, как его ведёт guard.py: None, относительный
    или абсолютный. Он нужен там, где абсолютный каталог сессии исказил бы смысл.
    """
    if depth > MAX_NESTING:
        return
    assigned = {}
    workdir = None
    for tokens in split_segments(strip_heredoc_bodies(command)):
        tokens = expand_assigned(tokens, assigned)
        if remember_assignments(tokens, assigned):
            continue
        tokens = strip_wrappers(tokens)
        if not tokens:
            continue
        if basename(tokens[0]) == "cd":
            workdir = next_workdir(workdir, tokens)
            continue
        if basename(tokens[0]) in SHELL_WRAPPERS:
            for index in range(1, len(tokens) - 1):
                if tokens[index] in ("-c", "--command"):
                    for item in segments(tokens[index + 1], resolve(base, workdir), depth + 1):
                        yield item
            continue
        yield tokens, resolve(base, workdir), workdir


def git_call(tokens, cwd):
    """(подкоманда, аргументы, каталог, репозиторий задан явно) для git или None."""
    if basename(tokens[0]) != "git":
        return None
    rest = tokens[1:]
    idx = 0
    relocated = False
    while idx < len(rest) and rest[idx].startswith("-"):
        option = rest[idx]
        name = option.split("=", 1)[0]
        if name in GIT_LOCATION_OPTIONS:
            relocated = True
        if option == "-C" and idx + 1 < len(rest):
            cwd = resolve(cwd, rest[idx + 1])
        elif option.startswith("-C") and len(option) > 2:
            # Слитная форма: -C<путь>.
            cwd = resolve(cwd, option[2:])
        idx += 2 if option in GIT_GLOBAL_WITH_VALUE else 1
    if idx >= len(rest):
        return None
    return rest[idx], rest[idx + 1:], cwd, relocated


def writes_inside_repo(targets, cwd, tops):
    """
    True, если хоть одна запись может попасть в репозиторий, где идёт коммит.

    Запись вне репозитория содержимое коммита не меняет. Агенты так готовят
    сообщение коммита: `printf '...' > /tmp/msg && git commit -F /tmp/msg`, и
    шлюз спрашивал «не могу сверить» на каждом таком коммите (окно пользователя).
    Путь с переменной или каталог вне git считаются «внутри» - строго.
    """
    if cwd not in tops:
        top = git(cwd, "rev-parse", "--show-toplevel")
        tops[cwd] = comparable(top.strip()) if top else None
    top = tops[cwd]
    for target in targets:
        raw = target.strip("\"'")
        if top is None or any(char in raw for char in "$`%~"):
            return True
        full = comparable(raw if is_absolute(raw) else os.path.join(cwd, raw))
        if full == top or full.startswith(top.rstrip("/") + "/"):
            return True
    return False


def git_alias(repo, name):
    """Значение alias.<name> в этом репозитории или None."""
    value = git(repo, "config", "--get", "alias." + name)
    return value.strip() if value else None


def is_read_only(tokens):
    """True, если команда не меняет файлы репозитория: чтение или прогон тестов."""
    name = basename(tokens[0]).lower()
    args = tokens[1:]
    if name == "timeout" and len(tokens) > 2:
        # timeout 600 <команда>: важна сама команда.
        return is_read_only(tokens[2:])
    if any(arg in SNAPSHOT_UPDATE for arg in args):
        return False
    if name in READ_ONLY_COMMANDS:
        return True
    if name in ("python", "python3", "py") and "-m" in args:
        module = args[args.index("-m") + 1] if args.index("-m") + 1 < len(args) else ""
        return module in PYTHON_TEST_MODULES
    if name == "npm":
        rest = [a for a in args if not a.startswith("-")]
        return bool(rest) and (rest[0] in NPM_TEST_SCRIPTS or
                               (rest[0] in ("run", "run-script") and len(rest) > 1
                                and rest[1].split(":")[0] == "test"))
    if name == "npx":
        rest = [a for a in args if not a.startswith("-")]
        return bool(rest) and rest[0] in JS_TEST_TOOLS
    if name == "node":
        return "--test" in args
    return False


def is_marker(tokens):
    return basename(tokens[0]) == "echo" and len(tokens) > 1 and tokens[1] == MARKER


def is_switch(target, workdir=None):
    """True, если путь - включатель шлюза .claude/.review-gate (два последних сегмента)."""
    raw = target.strip("\"'").replace("\\", "/")
    if workdir and not is_absolute(raw):
        raw = workdir.replace("\\", "/").rstrip("/") + "/" + raw
    parts = [part.lower() for part in raw.split("/") if part and part != "."]
    return parts[-2:] == [".claude", ".review-gate"]


def switch_exists(target, cwd):
    """
    True, если включатель по этому пути уже есть.

    Записать включатель, которого нет, - только включить шлюз: проверка
    становится строже, и окно на это не нужно. Живой случай 1.27.0: агент при
    настройке включал шлюз по просьбе пользователя, в bypassPermissions окно
    не работает, хук отказал - и включить шлюз пришлось руками в терминале.
    Выключение - удаление или перезапись существующего - по-прежнему через окно.
    """
    raw = native(target.strip("\"'"))
    path = raw if os.path.isabs(raw) else os.path.join(native(cwd), raw)
    return os.path.exists(path)


def is_approvals_file(path):
    return path_basename(path.strip("\"'")) == APPROVALS_FILE


def strip_redirects(args):
    """Аргументы без перенаправлений: `-m x 2>&1` -> `-m x`."""
    out = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in REDIRECTS:
            skip = True
            # Номер дескриптора перед оператором: 2 в `2>&1`, `2> log`.
            if out and out[-1].isdigit():
                out.pop()
            continue
        out.append(arg)
    return out


def add_scope(args):
    """
    (широта, пути) для git add: всё, отслеживаемые, отдельные пути или неизвестно что.

    `git add <файлы> && git commit` - самая частая форма коммита у агентов.
    Пока она считалась несверяемой, окно открывалось почти на каждом коммите,
    даже на коммите одного CHANGELOG.
    """
    args = strip_redirects(args)
    if any(a in ("-A", "--all", ".", ":/", "--no-ignore-removal") for a in args):
        return "all", []
    if any(a in ("-u", "--update") for a in args):
        return "tracked", []
    paths = []
    for arg in args:
        if arg in ("--", "-v", "--verbose", "-f", "--force"):
            continue
        if arg.startswith("-"):
            # -p, -i, -N, --chmod и прочее: результат по тексту не вычислить.
            return "unknown", []
        paths.append(arg)
    return ("paths", paths) if paths else ("index", [])


def commit_scope(args):
    """Что возьмёт git commit: индекс, ещё и отслеживаемые (-a) или неизвестно что."""
    args = strip_redirects(args)
    scope = "index"
    paths = []
    include = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            paths += args[index + 1:]
            break
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if name == "--all":
                scope = "tracked"
            elif name == "--include":
                include = True
            elif name in ("--interactive", "--patch", "--pathspec-from-file"):
                return "unknown", []
            elif name in COMMIT_LONG_WITH_VALUE and "=" not in arg:
                index += 1
        elif arg.startswith("-") and len(arg) > 1:
            # Короткие ключи слипаются: -am "текст", -sm"текст".
            for position, char in enumerate(arg[1:], 1):
                if char == "a":
                    scope = "tracked"
                elif char == "i":
                    include = True
                elif char in "p":
                    return "unknown", []
                elif char in COMMIT_SHORT_ATTACHED:
                    break
                elif char in COMMIT_SHORT_WITH_VALUE:
                    if position == len(arg) - 1:
                        index += 1
                    break
        else:
            paths.append(arg)
        index += 1
    if not paths:
        return scope, []
    # С путями без --include git коммитит только их содержимое из рабочей
    # копии, а остальное проиндексированное откладывает (--only).
    return ("index" if include else "only"), paths


def widest(first, second):
    if "unknown" in (first, second):
        return "unknown"
    return max(first, second, key=SCOPES.index)


def hash_paths(top, paths, run):
    """Хэши содержимого файлов рабочей копии - без записи в репозиторий."""
    present = [p for p in paths if os.path.isfile(os.path.join(top, p))]
    if any("\n" in p for p in present):
        # --stdin-paths делит по строкам: такой путь хэш посчитать не даст.
        raise GitStateError("newline in path")
    if not present:
        return {}
    hashes = run("hash-object", "--stdin-paths", stdin="\n".join(present).encode("utf-8")).split()
    if len(hashes) != len(present):
        raise GitStateError("hash-object")
    return dict(zip(present, hashes))


def spec_paths(top, specs, run, base="HEAD"):
    """Пути репозитория, которые изменят `git add <пути>` или `git commit <пути>`."""
    out = set()
    for cwd, items in specs:
        for item in items:
            if item.startswith(":") or any(char in item for char in "*?["):
                raise Unverifiable(item)
            full = os.path.normpath(os.path.join(cwd, item))
            try:
                rel = os.path.relpath(full, top).replace("\\", "/")
            except ValueError:
                raise Unverifiable(item)
            if rel.startswith(".."):
                raise Unverifiable(item)
            if os.path.isdir(full):
                scope = ["--", rel] if rel != "." else []
                out |= set(nul_list(run("ls-files", "-m", "-o", "-d", "--exclude-standard", "-z", *scope)))
                # И то, что уже проиндексировано в том же виде, что в рабочей
                # копии: `git add src/foo.py` и затем `git commit src/` коммитит
                # foo.py, а ls-files -m его не покажет (внешнее ревью 1.21.1).
                out |= set(nul_list(run("diff", "--cached", "--name-only", "--no-renames", "-z",
                                        base, *scope)))
            elif os.path.isfile(full) or nul_list(run("ls-files", "-z", "--", rel)):
                # Файл есть - его содержимое; файла нет, но он отслеживается - удаление.
                out.add(rel)
            else:
                raise Unverifiable(item)
    return out


def apply_specs(top, base, blobs, paths, run):
    """Накладывает на состояние содержимое путей из рабочей копии, как сделал бы git."""
    if not paths:
        return
    new = hash_paths(top, sorted(paths), run)
    listing = run("ls-tree", "-r", "-z", base) if len(paths) > 200 else \
        run("ls-tree", "-r", "-z", base, "--", *sorted(paths))
    base_blobs = {}
    for entry in nul_list(listing):
        meta, _, item = entry.partition("\t")
        base_blobs[item] = meta.split()[2]
    for item in paths:
        blob = new.get(item, "-")
        if blob == base_blobs.get(item, "-"):
            blobs.pop(item, None)  # содержимое вернулось к HEAD - не изменение
        else:
            blobs[item] = blob


# ---------------------------------------------------------------------------
# Состояние репозитория
# ---------------------------------------------------------------------------
def git(repo, *args, **kwargs):
    """Вывод git или None: не репозиторий, нет git, ошибка, таймаут."""
    try:
        proc = subprocess.run(
            ["git", "-C", native(repo)] + list(args), input=kwargs.get("stdin"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def nul_list(text):
    return [item for item in (text or "").split("\0") if item]


def commit_state(repo, scope, specs=()):
    """
    (пары «путь -> хэш», корень репозитория) для того, что уйдёт в коммит.

    None — только если это не репозиторий: такой коммит не пройдёт и без хука.
    Сбой git внутри репозитория — GitStateError, а не None: молчаливый пропуск
    сделал бы из нечитаемого файла способ закоммитить код без ревью.

    Пары — пути, отличающиеся от HEAD, и хэш их содержимого; удалённый путь
    даёт «-». Хэши рабочей копии считает `git hash-object` без -w: с теми же
    фильтрами, что и `git add`, а в репозиторий хук ничего не пишет.
    """
    top = git(repo, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    top = top.strip()

    def run(*args, **kwargs):
        out = git(top, *args, **kwargs)
        if out is None:
            raise GitStateError(args[0])
        return out

    if git(top, "rev-parse", "--verify", "-q", "HEAD") is not None:
        base = "HEAD"
    else:
        # Репозиторий без коммитов: база — пустое дерево в формате этого
        # репозитория (sha1 или sha256), вычисленное без записи.
        base = run("hash-object", "-t", "tree", "--stdin", stdin=b"").strip()

    blobs = {}
    if scope == "index":
        paths = nul_list(run("diff", "--cached", "--name-only", "--no-renames", "-z", base))
        if paths:
            # Весь индекс одним вызовом, без путей в аргументах: длинный список
            # упёрся бы в предел командной строки. «режим хэш стадия\tпуть».
            wanted = set(paths)
            for entry in nul_list(run("ls-files", "-s", "-z")):
                meta, _, item = entry.partition("\t")
                if item in wanted:
                    blobs[item] = meta.split()[1]
            for item in paths:
                blobs.setdefault(item, "-")
    elif scope in ("tracked", "all"):
        paths = nul_list(run("diff", "--name-only", "--no-renames", "-z", base))
        if scope == "all":
            untracked = nul_list(run("ls-files", "--others", "--exclude-standard", "-z"))
            paths = sorted(set(paths) | set(untracked))
        hashed = hash_paths(top, paths, run)
        blobs = {item: hashed.get(item, "-") for item in paths}
    # scope "only": коммит берёт только пути из команды - начинаем с пустого.
    apply_specs(top, base, blobs, spec_paths(top, specs, run, base), run)
    return blobs, top


def digest_of(blobs):
    pairs = sorted("{}\0{}".format(item, blob) for item, blob in blobs.items())
    return hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()


def fingerprint(repo, scope):
    """(отпечаток, пути, корень) — для одобрений, записанных до 1.21.1."""
    state = commit_state(repo, scope)
    if state is None:
        return None
    blobs, top = state
    return digest_of(blobs), sorted(blobs), top


def approvals_path(top):
    relative = git(top, "rev-parse", "--git-path", APPROVALS_FILE)
    return os.path.join(top, relative.strip()) if relative else None


def read_approvals(top):
    path = approvals_path(top)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("approvals") or []
    except (OSError, TypeError, ValueError, AttributeError):
        return []


def is_doc(path):
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in DOC_PATTERNS)


def team_has_reviewer(root):
    """
    True, если в проекте есть роль code-reviewer.

    Роль узнаётся по имени во frontmatter, а не по имени файла: при адаптации
    файл могли переименовать — так же, как в session_start.py.
    """
    agents_dir = os.path.join(root, ".claude", "agents")
    try:
        names = os.listdir(agents_dir)
    except OSError:
        return False
    for name in names:
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(agents_dir, name), encoding="utf-8", errors="replace") as fh:
                head = fh.read(2048)
        except OSError:
            continue
        match = re.search(r"^name:\s*([\w-]+)\s*$", head, re.M)
        if match and match.group(1) == REVIEWER:
            return True
    return False


# ---------------------------------------------------------------------------
# Режимы
# ---------------------------------------------------------------------------
def check_commit(repo, scope, specs=()):
    """Открывает окно, если коммит кода не одобрен; иначе молчит."""
    if scope == "unknown":
        confirm(msg("review.gate_unverifiable"), msg("review.gate_unverifiable_details"))
        return
    try:
        state = commit_state(repo, scope, specs)
    except Unverifiable:
        confirm(msg("review.gate_unverifiable"), msg("review.gate_unverifiable_details"))
        return
    if state is None:
        # Не репозиторий: такой коммит не пройдёт и без нас.
        return
    blobs, top = state
    code = sorted(item for item in blobs if not is_doc(item))
    if not code:
        return
    digest = digest_of(blobs)
    wanted = {"{}\0{}".format(item, blobs[item]) for item in code}
    for entry in read_approvals(top):
        # Точное совпадение состояния - формат одобрений до 1.21.1.
        if digest in (entry.get("fingerprints") or []):
            return
        # Каждый файл кода в коммите - ровно в том виде, что видел ревьюер.
        # Частичный коммит проверенного проходит; файл, которого ревьюер не
        # видел или который правили после него, - нет.
        if wanted <= set(entry.get("pairs") or []):
            return
    shown = ", ".join(code[:3]) + (", ..." if len(code) > 3 else "")
    confirm(msg("review.gate_missing", count=len(code), files=shown),
            msg("review.gate_missing_details"))


def pre_bash(data):
    agent = data.get("agent_type") or ""
    command = (data.get("tool_input") or {}).get("command") or ""
    base = data.get("cwd") or os.getcwd()

    # Шлюз выключен без роли ревьюера, и тогда незачем разбирать git и звать
    # git config для алиасов. Защита маркера и файла одобрений действует всегда.
    root = project_root(data)
    gated = os.path.exists(os.path.join(root, SWITCH)) and team_has_reviewer(root)
    git_env = GIT_ENV.search(command) is not None
    alias_defined = False
    staging = "index"
    specs = []
    commits = []
    tops = {}
    for tokens, cwd, workdir in segments(command, base):
        if is_marker(tokens) and agent != REVIEWER:
            deny(msg("review.approve_not_reviewer", who=agent or msg("review.who_main")))
        writes = written_paths(tokens)
        for target in writes:
            if is_approvals_file(target):
                deny(msg("review.approvals_protected", path=target))
            # workdir в виде guard.py, а не абсолютный cwd: сессия из worktree в
            # .claude/worktrees/ иначе считала бы каждую запись записью в .claude.
            note_platform_write(target, workdir)
            if is_switch(target, workdir) and switch_exists(target, cwd):
                confirm(msg("review.switch_write", path=target))
        call = git_call(tokens, cwd)
        if call is None:
            # Запись или неизвестная команда до коммита меняет то, что уйдёт
            # в коммит, уже после проверки: отпечаток считается до запуска.
            if not is_read_only(tokens) or \
                    (writes and (not gated or writes_inside_repo(writes, cwd, tops))):
                staging = "unknown"
            continue
        subcommand, args, repo, relocated = call
        if not gated:
            continue
        if subcommand == "config" and any(a.startswith("alias.") for a in args):
            # Алиас, заведённый в этой же команде, проверить по конфигурации нельзя.
            alias_defined = True
            staging = "unknown"
            continue
        if subcommand not in GIT_BUILTINS:
            alias = git_alias(repo, subcommand)
            if alias is None:
                if alias_defined:
                    commits.append((repo, "unknown", []))
                else:
                    staging = "unknown"
                continue
            if alias.startswith("!"):
                # Алиас-команда оболочки: что она сделает, по тексту не видно.
                commits.append((repo, "unknown", []))
                continue
            try:
                expanded = shlex.split(alias)
            except ValueError:
                commits.append((repo, "unknown", []))
                continue
            if not expanded:
                continue
            subcommand, args = expanded[0], expanded[1:] + list(args)
        if subcommand in ("add", "stage"):
            scope, paths = add_scope(args)
            if scope == "paths":
                specs.append((cwd, paths))
            else:
                staging = widest(staging, scope)
        elif subcommand == "commit":
            scope, paths = ("unknown", []) if relocated or git_env else commit_scope(args)
            if staging == "unknown" or scope == "unknown":
                commits.append((repo, "unknown", []))
            elif scope == "only":
                # Коммит путей: только их содержимое, индекс и git add до него не важны.
                commits.append((repo, "only", [(cwd, paths)]))
            else:
                commits.append((repo, widest(staging, scope), specs + [(cwd, paths)]))
        elif subcommand == "commit-tree":
            # Низкоуровневое создание коммита: сверять отпечаток не с чем.
            commits.append((repo, "unknown", []))
        elif subcommand not in GIT_READ_ONLY or writes:
            staging = "unknown"

    if commits:
        for repo, scope, commit_specs in commits:
            try:
                check_commit(repo, scope, commit_specs)
            except Exception as error:  # noqa: BLE001 - сбой хука не должен пропускать молча
                confirm(msg("review.gate_error", error=type(error).__name__))
    emit_confirmation(data.get("permission_mode") or "")


def pre_path(data):
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if data.get("tool_name") in WRITE_TOOLS and path and is_approvals_file(path):
        deny(msg("review.approvals_protected", path=path))
    if data.get("tool_name") in WRITE_TOOLS and path and is_switch(path) and \
            switch_exists(path, data.get("cwd") or os.getcwd()):
        # Запись в .claude/: в bypassPermissions окно на неё Claude Code одобряет сам,
        # и emit_confirmation по этой пометке отвечает отказом (аудит 1.33.0)
        note_platform_write(path)
        confirm(msg("review.switch_write", path=path))
        emit_confirmation(data.get("permission_mode") or "")


def post_context(text):
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text},
    }, ensure_ascii=True))


def record(data):
    """code-reviewer выполнил маркер — записывает одобрение текущего состояния."""
    if (data.get("agent_type") or "") != REVIEWER:
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    base = data.get("cwd") or os.getcwd()
    found = next(((tokens, cwd) for tokens, cwd, _ in segments(command, base) if is_marker(tokens)), None)
    if found is None:
        return
    tokens, repo = found

    # Все три широты сразу: ревьюер смотрел рабочую копию, а коммит может
    # взять индекс, -a или git add -A — любое из них совпадёт с одобренным.
    try:
        states = [state for state in (commit_state(repo, scope) for scope in SCOPES) if state]
    except GitStateError:
        states = []
    if not states:
        post_context(msg("review.record_failed", path=repo))
        return
    top = states[0][1]
    prints = sorted({digest_of(blobs) for blobs, _ in states if blobs})
    # Пары «путь, хэш» всех трёх широт: коммит одобрен, если каждый его файл
    # кода есть здесь в том же виде. Индекс и рабочая копия ревьюер видел оба.
    pairs = sorted({"{}\0{}".format(item, blob) for blobs, _ in states for item, blob in blobs.items()})
    if not prints:
        post_context(msg("review.nothing_to_approve"))
        return

    path = approvals_path(top)
    if path is None:
        post_context(msg("review.record_failed", path=repo))
        return
    approvals = read_approvals(top)
    approvals.append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": " ".join(tokens[2:])[:300],
        "fingerprints": prints,
        "pairs": pairs,
    })
    # Через временный файл: оборванная запись не должна стереть прежние одобрения.
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"approvals": approvals[-MAX_APPROVALS:]}, fh, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    post_context(msg("review.recorded", count=max(len(blobs) for blobs, _ in states)))


def main():
    parser = argparse.ArgumentParser(description=msg("cli.review_gate_description"))
    parser.add_argument("--record", action="store_true", help=msg("cli.review_gate_record"))
    args = parser.parse_args()

    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        sys.stderr.write(msg("config.bad_json"))
        return

    use_project(data.get("cwd") or "")
    tool_name = data.get("tool_name") or ""
    if args.record:
        if tool_name == "Bash":
            record(data)
    elif tool_name == "Bash":
        pre_bash(data)
    elif tool_name in WRITE_TOOLS:
        pre_path(data)


if __name__ == "__main__":
    main()
