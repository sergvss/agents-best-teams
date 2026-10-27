<p align="center">
  <img src="./icon.png" alt="agents-best-teams" width="160" />
</p>

<h1 align="center">agents-best-teams</h1>

<p align="center">
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/lang-en-blue.svg" /></a>
  <a href="README.ru.md"><img alt="Русский" src="https://img.shields.io/badge/lang-ru-lightgrey.svg" /></a>
</p>

<p align="center"><em>"A single super-agent stops coping long before you notice. A team of specialised agents with explicit roles, routing and hooks instead of pleading — that scales."</em></p>

<p align="center">
  <a href="CHANGELOG.md"><img alt="Version" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fsergvss%2Fagents-best-teams%2Fmain%2F.claude-plugin%2Fplugin.json&query=%24.version&label=version&color=blue" /></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <img alt="Claude Code compatible" src="https://img.shields.io/badge/Claude_Code-plugin-9333ea" />
  <img alt="Codex compatible" src="https://img.shields.io/badge/Codex-compatible-10b981" />
  <img alt="Cursor compatible" src="https://img.shields.io/badge/Cursor-compatible-0ea5e9" />
  <img alt="Content language: Russian" src="https://img.shields.io/badge/content-RU-red" />
</p>

<p align="center">
  <a href="#why">Why</a> ·
  <a href="#the-development-process">Process</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#install">Install</a> ·
  <a href="#updating">Updating</a> ·
  <a href="#whats-inside">What's inside</a> ·
  <a href="#when-you-need-a-team">When you need it</a> ·
  <a href="#the-language-situation">Language</a> ·
  <a href="#credits">Credits</a>
</p>

---

A methodology and a Claude Code plugin for building software with a **team of AI agents**. Three layers in one plugin: a **development process** from request to merged branch — clarify the task, plan, branch, test first, debug from the root cause, review; **protective hooks** that stop destruction mechanically instead of asking nicely; and a **team of roles** with explicit areas and routing. For people using Claude Code, Codex, Cursor or any other agent CLI who want to move from "one super-agent for everything" to a team that works through a process.

> **Read this before you install.** This README, the [concepts page](docs/concepts.en.md) and everything the hooks say to you are in English. The methodology's prose — the principles, the role templates, the checklists — is written in Russian, deliberately rather than by oversight. [The language situation](#the-language-situation) explains what that does and does not cost you.

---

## Why

| Situation | What you take |
|---|---|
| Starting a project and want a team of roles from day one, not a single agent | the `setup-agent-team` skill assembles a roster for your stack |
| You already have a dozen agents, each wandering into someone else's area | `principles/02` and the orchestrator template — routing by Tier |
| An agent destroyed something and it must not happen again | `hooks/` — working protective hooks, installed with the plugin |
| Critical changes ship on gut feeling | `principles/05` — three models from different vendors, 2-of-3 rule |
| You need an audit of what is already configured | `checklists/` — permission, context and planning matrices |
| You want the agent to work through a process instead of improvising | the process skills — `clarify-task`, `start-branch`, `test-first`, `debug-root-cause`, `finish-branch` |

## The development process

The plugin covers the whole path from a request to a merged branch. Each step is a skill, opened before the first code edit; one line in the session context names them, because on simple tasks the model skips skills it is not reminded of.

```
request ──► clarify-task: look at the project first, then one message —
            what I understood, 2-3 approaches with a recommendation,
            at most three questions that change the result
        ──► Tier
   Tier 1 ─► edit on the current branch
   Tier 2 ─► start-branch ─► test-first ─► review if risky ─► finish-branch
   Tier 3 ─► plan: files, interfaces, Run/Expected, Review Focus
           ─► a fresh reader checks the plan ─► you approve it
           ─► start-branch ─► tasks one by one, risky ones reviewed at once
           ─► final review of the branch ─► finish-branch
bug ─────► debug-root-cause ─► test-first
```

- **Branches.** Tier 2 and above leaves the default branch; the project policy lives in `git config abt.branches`. At the end, a menu — merge, pull request or keep — and you choose. Deleting unmerged work takes your own word: the hook asks before `git branch -D`.
- **Test first.** A red test before the code, and it must fail for the right reason. Code written before the test is not deleted: the test has to go red when the behaviour is broken on purpose.
- **Plan execution, Tier 3.** Each role gets its own task, not the whole plan. After the plan is approved the orchestrator settles small questions itself and writes each decision down; it stops only for something irreversible, security, anything outside the branch, or a broken plan.

Measured, not assumed: a red test before the code — 0 of 2 runs without the skills, 3 of 3 with them; a vague request handled in one round — 0 of 3 before, 2 of 3 after; a Tier 3 task went from plan to merged branch, surviving a broken session on the way. Idea sources and differences — [docs/superpowers-comparison.md](docs/superpowers-comparison.md).

---

## Install

### Quick start

Six steps from nothing to a working team. Each one is explained below — if something goes wrong, the answer is there.

| | What to do |
|---|---|
| 1 | `/plugin marketplace add sergvss/agents-best-teams` |
| 2 | `/plugin install agents-best-teams@sergvss` |
| 3 | `/reload-plugins` — **only if** Claude Code printed `Run /reload-plugins to activate.` Until then nothing works |
| 4 | Hook messages are in English. For Russian: `mkdir -p .claude && printf 'ru\n' > .claude/.abt-lang` |
| 5 | Paste into the chat: `Разверни команду агентов в этом проекте через скилл agents-best-teams:setup-agent-team` |
| 6 | Verify: ask the agent to run `echo check > .env.check`. It must be blocked |

Steps 1-3 take a minute; step 5 takes a few minutes and a couple of decisions from you. Do not skip the sixth: a hook that never started is indistinguishable from one that is absent, and you want to find that out now rather than on the day it was needed.

On macOS and Linux step 6 will most likely surface a problem — see below.

> **It is not a typo that `sergvss` means two different things.** On the first line it is the **GitHub repository address**; on the second it is the **marketplace name** defined inside that repository.
>
> If you add the marketplace through **Manage Plugins → Marketplaces → Add**, that field wants the address: `sergvss/agents-best-teams`. The bare word `sergvss` there produces `Invalid marketplace source format`.

### What turns on when

Not everything starts working the moment you install. The precise picture:

| | When | What it is |
|---|---|---|
| **Protective hooks** | immediately | Block filesystem destruction, writes to `.env` and SQL without `WHERE` in database clients (`psql`, `mysql`, `sqlite3` and others); before force-push, `reset --hard` or `DROP` they ask you in the Claude Code permission prompt, and so does a code commit that `code-reviewer` has not approved (once the team has `code-reviewer` and you turn the gate on with an empty `.claude/.review-gate`); log privileged actions and stop blind retries of a failing command |
| **After-denial rule** | next start | When auto mode denies an action, the agent asks you through the question menu instead of telling you to run it yourself. A role hands the request to the main conversation: subagents cannot ask |
| **Development process** | immediately; the context line from next start | The skills `clarify-task`, `start-branch`, `test-first`, `debug-root-cause`, `finish-branch`; one line in the session context names them from the next session start; a hook tells the agent when it is editing the default branch |
| **Checklists as skills** | immediately | Claude pulls them in when relevant, or invoke them with `/` |
| **Hook diagnosis** | immediately | Ask where a permission prompt or a block came from: the `diagnose-hooks` skill reads the session transcript, cites `file:line` for every hook decision and says whether the current version would still do it |
| **Team setup prompt** | next start | It is a `SessionStart` hook: the event already fired if you installed mid-session. `/clear` is enough — no need to restart entirely — but `/reload-plugins` will not do it |
| **Team roles** | manual only | `setup-agent-team`, see below |
| **`verify.py` Stop hook** | one line of config | Wired by the plugin but silent until you name your test command in your user file `~/.claude/agents-best-teams/verify.json`: `{"C:/projects/my-app": "npm test"}`. Outside the repository on purpose, so a cloned repo cannot plant a command. Wiring — [hooks/README.md](hooks/README.md) |
| **Triangulation** | manual only | Needs `agents.config` and external model keys — [docs/models.md](docs/models.md) |

**Activation comes first.** After installing, Claude Code prints either `Plugin is now active.` or `Run /reload-plugins to activate.` In the second case nothing works until you run that command.

**On macOS and Linux the hooks will most likely stay silent.** The configuration names `python`, while those systems name the binary `python3` — the hook never starts, and nothing tells you. Every occurrence needs changing, not just the first: there are nineteen of them per configuration file, and fixing one block leaves the log, the retry counter, the review gate and the startup prompt silent. The command to do it is in [hooks/README.md](hooks/README.md).

### The roles need a separate step

Hooks and skills work immediately, but **the plugin deliberately does not install the roles**: the templates carry placeholders for a specific project, and roles with `<your-project>` inside them would be worse than no roles at all. Plugin files are also overwritten on update, so your edits to the roles would keep disappearing.

On the first session in a project without a team, Claude offers to assemble one. If you would rather not wait, paste this into the chat:

```
Разверни команду агентов в этом проекте через скилл agents-best-teams:setup-agent-team
```

The skill reads your stack, proposes a roster, copies the templates and adapts them to the project. To silence the offer permanently, create an empty `.claude/.no-team-setup-prompt` file.

> **Check that the hooks actually work:** ask the agent to run `echo check > .env.check`. It must be blocked, with an explanation. If the file appears instead, the hooks are not wired up — see [hooks/README.md](hooks/README.md).
>
> Do not test the protection with something genuinely destructive like `rm -rf .`: if the hook is not working — which is the very thing you are testing — the command runs.

**The role catalogue — who does what** — [templates/README.md](templates/README.md).
**The concepts you will meet — Tier, risk classes, 2-of-3, agent memory** — [docs/concepts.en.md](docs/concepts.en.md).
**Other platforms and manual installation** — [docs/install.md](docs/install.md).
**First run, step by step** — [docs/quick-start.md](docs/quick-start.md).
**Subagents, agent teams, workflows and skills: which to pick** — [docs/claude-code-mechanisms.md](docs/claude-code-mechanisms.md).
**How to check that all of this actually works** — [docs/verification.md](docs/verification.md).

> **About the name.** "Agent team" here means a way of organising work: roles, areas, routing. Claude Code has a feature with a similar name, **agent teams**, and it is a different thing — one of four execution mechanisms. The `templates/` roles work with all of them, but some frontmatter fields are ignored for teammates.

---

## Updating

```
/plugin marketplace update sergvss
/reload-plugins
```

Two reasons an update "does not arrive" even though you did everything right:

- **Third-party marketplaces do not update themselves.** Auto-update is on by default only for Anthropic official marketplaces. Turn it on: `/plugin` → **Marketplaces** tab → `sergvss` → **Enable auto-update**.
- **A new version is only visible once `version` is bumped in the manifest.** If the author pushed changes without bumping it, they never reach installed plugins.

### Never overwrite these on a manual update

| Never | Why |
|---|---|
| `.claude/agent-memory/` | Accumulated knowledge of your roles; not recoverable |
| `.claude/approval-log.jsonl` | The privileged-action log |
| `.claude/settings.json` | Your settings — port new hook blocks in by hand |
| `.claude/.abt-lang` | The language you chose for hook messages |
| `.claude/agents/` | The roles are adapted to your project |

`hooks/*.py` and `skills/` are safe to overwrite. Project-specific settings — your own roles, database clients, test layouts — live in `.claude/agents-best-teams.json`, which updates never touch. If an older version had you edit constants in `guard.py`, move them into that file: [hooks/README.md](hooks/README.md#файл-настроек-проекта).

That last table row is genuinely awkward, and it is honest to say so: merging template changes into an already-adapted role is a job only a human can do. Advice — commit `.claude/` right after installing, so it stays visible which parts you changed and which are still stock.

**In detail, including the path for anyone who installed before 1.0.0** — [docs/update.md](docs/update.md).

---

## What's inside

The methodology is about agents working **as a team**, not about single agents:

- **Tier decomposition** — the pipeline follows the size of the task, not the other way round
- **Isolated areas of responsibility** — every agent knows its area and stays out of the others
- **Triangulated review** — critical decisions are checked by 3 models from different vendors; 2-of-3 means blocker
- **Per-agent persistent memory** — institutional knowledge accumulates between sessions
- **Human in the loop** for privileged operations
- **An eval suite** for the agent layer — the same testing discipline as for product code
- **Mechanical invariants** — a recurring mistake becomes code, not another line of prompt
- **Untrusted input** — text from tickets, pages and API responses is data, not instructions

Mechanical invariants here are working code rather than a promise: `hooks/` are covered by tests, and half of those tests check for the **absence** of false positives. A hook that gets in the way is switched off on day one — and then there is no protection at all.

---

## When you need a team

**You do**, if:
- The project has 5+ roles: backend, frontend, QA, DevOps, documentation
- There are critical areas with different rights (production, billing, RBAC)
- Agents keep wandering into each other's areas and breaking things
- You want independent tasks to run in parallel

**You do not**, if:
- It is a one-developer pet project
- The tasks are homogeneous (backend only, or frontend only)
- There is no time to set it up — one capable agent is faster

---

## Structure

```
principles/    11 principles: philosophy, Tier decomposition, risk classes,
               budgets and caching, triangulation, memory, stop rules,
               approval log, mechanical invariants, eval suite,
               untrusted input

templates/     20 role templates
               they build:  backend, frontend, database, QA, DevOps,
                            documentation, i18n, local sysops
               they look:   code reviewer, security, design,
                            investigator, scope challenger
               they count:  running cost, unit economics,
                            investor reporting, subscriptions and licences
               other:       orchestrator, browser tester,
                            external LLM reviewer

checklists/    9 checklists: tools, permissions, context,
               planning, the path from task to commit, branches,
               test first, debugging from the root cause,
               clarifying a task without an interview

hooks/         Working protective hooks — the Claude Code layer
               guard.py — blocks destruction before it runs
               verify.py — do not finish with failing tests
               approval_log.py — privileged-action log
               ledger_log.py — role calls into the task ledger
               branch_hint.py — tells the agent it is editing the default branch
               retry_guard.py — the three-attempts rule
               review_gate.py — review gate for code commits (opt-in)
               session_start.py — start-of-session prompts
               denial_guidance.py — what to do after a denial
               messages.py — every message, in English and Russian
               configuration and tests

skills/        The development process, checklists as invocable skills,
               team assembly for a project

docs/          Install, update, model configuration, first run,
               choosing an execution mechanism, verification,
               concepts.en.md — the terms, in English

.claude-plugin/  Plugin and marketplace manifests

EXAMPLES.md    Every principle as a "bad → good" pair
CHANGELOG.md   Release history
VERSION        Current version, checked against the manifest in CI
```

> Only `hooks/`, `skills/` and `.claude-plugin/` are tied to Claude Code. Everything else is provider-neutral.

---

## The principles in one line each

1. **A team beats a super-agent** — specialisation reduces area-of-responsibility errors
2. **Tier decomposition** — task size determines the pipeline, not the reverse
3. **R/D/W/P risk classes** — confirmation only where it is actually needed
4. **Budgets and caching** — checkpoint after N steps; stable content first in the prompt, volatile content last
5. **Triangulation** — critical decisions are reviewed by 3 models in parallel
6. **Persistent memory** — an agent's institutional knowledge between sessions
7. **Stop rules** — the agent knows when to stop and does not get stuck in loops
8. **Approval log** — privileged actions leave a trace
9. **Mechanical invariants** — recurring mistakes become hooks, not pleading
10. **Eval suite** — the agent layer is tested like product code
11. **Untrusted input** — external text is data, not instructions

---

## The language situation

Worth being precise about, because "the docs are in Russian" and "you cannot use it" are not the same statement.

**In English:** this README, [the concepts page](docs/concepts.en.md), the plugin's metadata, and **everything the hooks say to you** — every block reason and every suggested alternative.

**In Russian:** the 11 principles, the 20 role templates, the 9 checklists, the bodies of the skills, the rest of `docs/`, and the Russian README at [README.ru.md](README.ru.md).

**Choosing the language.** Hook messages exist in both, and the rules are identical either way — language changes what you read, never what is blocked:

```bash
ABT_LANG=ru claude                                    # one session
mkdir -p .claude && printf 'ru\n' > .claude/.abt-lang # for the project
```

English is the default. If no language is chosen, the plugin asks at the start of each session until you choose. `setup-agent-team` asks too when it deploys a team, writes the answer to `.claude/.abt-lang` and can produce the roles themselves in your language. An unrecognised value falls back to English instead of disabling anything.

**Does the rest still work for you?** Yes, and not by luck. The roles and skills are prompts the model reads, and the model is multilingual: it reads a Russian role definition and answers in the language you write in. When `setup-agent-team` deploys a team it can write the roles in your language as it adapts them to your stack — so the roles you actually work with are yours to read.

**Why the prose is not translated.** Several thousand lines of it, changing every release. A second copy would drift from the first, and a role template that quietly disagrees with its Russian original is more dangerous than a missing one — you would be running rules you cannot verify. That is also why role translation happens at deployment rather than in this repository: one canonical copy here, nothing to drift.

If you want to help maintain a fuller English version, open an issue.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Credits

This methodology grew out of building agent teams in real product projects. The push and the original inspiration came from [DenisSergeevitch/agents-best-practices](https://github.com/DenisSergeevitch/agents-best-practices) — thanks to its author for collecting and articulating the base principles of working with agents in one place. Several concepts (risk classes, tool and permission checklists, the mechanical-invariants idea) are developed here for the case of a **team** of agents.

Practices from two more sources are absorbed and integrated:

- **[garrytan/gstack](https://github.com/garrytan/gstack)** — Garry Tan's (Y Combinator) kit for Claude Code as a virtual engineering team. The structured Think → Plan → Build → Review → Test → Ship → Reflect sprint, and the idea of parallel independent pipelines, underpin our `pm-orchestrator` and Tier decomposition.
- **[obra/superpowers](https://github.com/obra/superpowers)** — Jesse Vincent's skills framework and development methodology for coding agents. Its habit of testing agent behaviour, not just code, with headless `claude -p` runs shaped our live checks and the corpus of real agent commands the hooks are tested against; its two-stage review (spec first, quality second), the discipline of verifying review feedback before acting on it, session diagnosis from transcripts with file:line evidence, and the task ledger that lets a long task survive a broken session are now part of this methodology too — and so is most of its development process: branches with an integration menu, test first, debugging from the root cause, clarifying a task before building, a plan format with interfaces and Review Focus, and plan execution task by task. Every piece is rewritten for our confirmation rules and measured before and after; the differences — no long interviews, nothing deleted without your word, review only where a mistake is expensive — are in [docs/superpowers-comparison.md](docs/superpowers-comparison.md). Thank you for showing how far a methodology can be pushed when its rules are tested like code.
- **[multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)** — a collection of disciplinary skills based on Andrej Karpathy's observations about typical LLM coding failures. The **Think Before Coding**, **Simplicity First**, **Surgical Changes** and **Goal-Driven Execution** principles are built into the methodology directly.

---

<p align="center"><em>If you use the methodology and find something to improve — PRs welcome.</em></p>
