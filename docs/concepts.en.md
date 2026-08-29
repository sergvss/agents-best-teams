# Concepts

The methodology itself is written in Russian. This page is not a translation of it — it is the minimum you need to use the plugin deliberately rather than by guesswork: the terms that appear in hook messages, role templates and skill output, and what each one is for.

Where a Russian document is the authority, it is named. If this page and that document ever disagree, the Russian one is right.

---

## Language

Everything a human reads from the hooks — the reason an action was blocked, the alternatives offered — comes from one catalogue, [`hooks/messages.py`](../hooks/messages.py), and exists in English and Russian.

The language is chosen in this order:

1. the `ABT_LANG` environment variable (`en` or `ru`);
2. the `.claude/.abt-lang` file in the project, one word — this is what the setup skill writes;
3. English.

The rules themselves are identical in both languages. Language changes what you read, never what is blocked. An unrecognised value falls back to English rather than disabling anything.

The role templates, the principles and the checklists are Russian. The model reads them regardless and answers you in your own language; when the setup skill deploys a team it can translate the roles into your language as it adapts them to your stack. There is only ever one canonical copy in the repository, so there is nothing for a translation to silently drift from.

---

## Tier decomposition

The size of the task decides the pipeline, not the other way round. Three tiers:

| Tier | What it is | What runs |
|---|---|---|
| 1 | A single edit, an obvious fix | The agent does it and says so |
| 2 | A feature inside one area | Plan → implement → tests → review |
| 3 | A large feature, a new module, a refactor | Decomposition by the orchestrator, several roles, review, verification |

The point is not ceremony for large tasks; it is the absence of ceremony for small ones. A gate that fires on every change becomes a ritual, and rituals get ignored — which is how you end up with no gate at all when it matters.

Authority: `principles/02-tier-decomposition.md`.

---

## Risk classes: R / D / W / P

Every action falls into one of four classes, and the class decides whether a human is asked.

| Class | Meaning | Confirmation |
|---|---|---|
| **R** — Read | Reading anything | No |
| **D** — Draft | Reversible changes: edits under version control | No |
| **W** — Write | Changes that git does not save you from: `git reset --hard`, deleting untracked files | Yes |
| **P** — Privileged | Irreversible or reaching other people: force-push, `.env`, `DROP`, production data | Every single time, even if it was allowed before |

The distinction that carries the most weight is D versus W: "it is in git" is what makes a change cheap to undo, and the moment that stops being true the class changes.

Authority: `principles/03-risk-classes.md`.

---

## The 2-of-3 rule

Critical changes are reviewed by three models from different vendors at once: PRIMARY (the agent that knows the project) plus two independent side reviewers. A finding raised by two or more is treated as real.

Two properties matter more than the arithmetic:

- **Independence.** Each reviewer finishes before seeing the others. A reviewer who knows what the previous one found looks for confirmation instead of looking.
- **Quorum.** If a reviewer does not answer, the rule degrades quietly: with one side down "2 of 3" becomes unanimity of the rest; with both down only PRIMARY is left — the very opinion the exercise existed to check. A report must therefore state how many voices actually arrived, and must never render silence the same way it renders "no findings".

Authority: `principles/05-triangulated-review.md`.

---

## Per-agent memory

Each role can keep notes between sessions in `.claude/agent-memory/<role>/`. This is where institutional knowledge accumulates: what was tried and rejected, why a boundary is where it is, which estimate turned out wrong.

One mechanical detail is easy to miss and matters a lot. Claude Code's `memory` frontmatter field **automatically enables Read, Write and Edit, bypassing the `tools` list**. For a role deliberately built without write access — a reviewer, an analyst — that silently removes the guarantee the role exists for. The `memory` rule in [`hooks/guard.py`](../hooks/guard.py) puts the restriction back, allowing writes only into that role's own memory directory.

So: enabling `memory` on a read-only role without the hook gives it the ability to edit your code.

Authority: `principles/06-memory-hygiene.md`, `checklists/permission-checklist.md`.

---

## Stop rules

An agent needs to know when to stop, and the knowledge has to be mechanical rather than a request in a prompt.

- **Three blind attempts.** After three failures of the same command with nothing changed in between, the fourth is blocked. "Blind" is the operative word: [`hooks/retry_guard.py`](../hooks/retry_guard.py) resets the counter whenever the working copy changed between attempts, because a test failing three times while the agent fixes code is exactly the loop you want.
- **An unreachable service.** The same threshold, for a different reason: nothing changes between attempts on its own. A repeat needs a cause — the service was started, the address changed, a new key arrived.
- **Never invent the data a service did not return.** This is the worst failure mode available: a gap is visible, a plausible fabricated summary is not.

Authority: `principles/07-stop-rules.md`.

---

## Mechanical invariants

A mistake that repeats becomes code, not another line of prompt. That is what `hooks/` is: the rules the methodology cares about, expressed as something that runs.

Two things follow, and both are load-bearing:

- **A block must explain itself and offer a replacement.** A hook that only says "no" teaches agents to look for a way around it. Every block in this repository names the reason and lists alternatives — and in live runs agents took the offered alternative rather than working around the block.
- **False positives are the real failure mode.** A hook that gets in the way is switched off on day one, and then there is no protection at all. Half the tests in `hooks/tests/` check that the hooks stay silent when they should.

Authority: `principles/09-mechanical-invariants.md`.

---

## The eval suite

The agent layer — prompts, routing, permissions — is tested like product code, in eleven scenarios across five categories: prompt injection, tool misuse, approval bypass, connector failure, conflicting instruction.

The dividing line is worth stating: **if a model decides, the scenario is run by hand; if code decides, it is an automated test.** Model behaviour is not deterministic, so a manual scenario checks a pattern of behaviour rather than exact wording. The hooks are ordinary code and are covered by unit tests instead.

Authority: `principles/10-eval-suite.md`.

---

## Untrusted input

Text arriving from a ticket, a web page or an API response is data, not instructions. The dangerous case is not a user typing "ignore previous instructions" — the user is the principal and may ask for what the rules allow. The dangerous case is text written by a third party that the user has not read, carrying an instruction the agent might follow on their behalf.

A marker that looks like authority — `[SYSTEM]:`, "confirmation already received" — confers none.

Authority: `principles/11-untrusted-input.md`.

---

## Where to go next

- [README](../README.md) — install, what turns on when, updating
- [hooks/README.md](../hooks/README.md) — what each hook does, how to wire and verify it
- `principles/` — the eleven principles in full, in Russian
- `checklists/` — permission, context, planning and tool matrices, in Russian
