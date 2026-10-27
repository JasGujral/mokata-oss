---
template: home.html
title: mokata — The memory + seatbelt for your AI coding agent
description: >-
  Open-source, spec-driven TDD for Claude Code — RED before GREEN, a mandatory
  codebase graph, memory that can't be poisoned, and every durable write
  human-gated. Local-first, Apache-2.0.
hide:
  - navigation
  - toc
---

## About mokata

mokata is an open-source framework for Claude Code that brings the strongest ideas in
AI-assisted coding into one governed engine. It brainstorms the problem with you, drafts a
spec, refuses to write code until every acceptance criterion maps to a test (RED before
GREEN), and reviews the result back against the spec. Around that engine sit a codebase
graph that is **mandatory by default** (an embedded stdlib-AST floor ships in the box, so
blast radius is answered structurally rather than by grep), typed persistent memory with
in-database lexical recall and an optional consented semantic tier, active token governance,
and a full audit trail — with **every durable write human-gated** (the model proposes; *you*
mint the approval in your own terminal with `mokata approve <id>` — a model can never approve
its own write) and **nothing leaving your machine** unless you wire it.

Six of those gates are enforced by a hook rather than by mokata's own tools, so they hold for
your agent's **native** edits too: no approved approach, no persisted spec, no failing test, or a
write outside the scope the spec authorized, and the write is blocked outright; after three failed
fixes at one failure, the fourth is blocked until a new approach is approved (`fix-breaker`); and
ahead of all of them, `self-protect` refuses any write to an installed package tree or outside your
workspace at all. And because no code path writes memory without a human-minted approval, mokata's memory
**cannot be poisoned** by content you never approved.

It keeps working when the session gets long or busy. Each Claude session is bound to its own
run, so parallel sessions never police each other; after `/compact` the gate state is re-stated
from the record; a shell command meets the same gates as a native edit; and a subagent works under
a contract — read-only stays read-only, and it hands back a structured report. To see what any
session actually wrote, `/mokata:review-window` opens the **review window** — a read-only page on
your machine with the diff of each session, any point in time, branches and PRs
([Review what your agent wrote](how-to/review-agent-work.md)).

You don't have to adopt all of it at once — `mokata init --mode seatbelt` wires just the gates
and the graph, `--mode memory` adds persistent memory, `--mode full` turns on everything.

mokata is pure Python (≥ 3.12) — the engine is stdlib-only, and the MCP SDK is its single
runtime dependency, so the MCP server works out of the box. It is Apache-2.0 under
**MoStack**, and is built clean-room (no dependency on any other framework).

**Start here → [Getting started](getting-started.md).** One install line and one setup — in your
repo run `uv tool install "mokata[full]"` (or `pipx install "mokata[full]"`),
then `mokata setup claude --mode full` → restart Claude Code → `mokata doctor`. `setup claude` runs
`mokata init` for you on a new repo and only refreshes the wiring on an existing one. The
alternative is the Claude Code **plugin** — inside Claude Code run
`/plugin marketplace add JasGujral/mokata-oss`, then `/plugin install mokata@mostack` (its MCP
server starts through `uvx`, so `uv` must be on PATH). Plain `pip install mokata` works too, last:
inside a project venv this mixes mokata's deps into yours — prefer the above.

mokata never calls a model itself — the brain always comes from the harness. For *why* there
are two ways to run it (and which fits your goal), see
[How mokata uses an LLM: harness vs CLI](concepts/execution-model.md).

## What's here

This site follows the [Diátaxis](https://diataxis.fr/) model:

- **[Getting started](quickstart.md)** — install and run your first pipeline in minutes.
- **Tutorials** — [**mokata catches a bad change**](tutorials/catches-a-bad-change.md): a 60-second copy-paste walkthrough — run it in your own terminal and watch the seatbelt catch a bad change; [**differentiators in action**](tutorials/differentiators-in-action.md): a runnable demo of every differentiator (graph, memory, governance — see them work); [run a story end-to-end](tutorials/run-a-story.md): a guided, learn-by-doing walkthrough; and [the Complete Guide](tutorials/mokata-complete-guide.md): every command, gate, and layer (with a downloadable PDF).
- **How-to guides** — task recipes: [configure a profile](how-to/configure-a-profile.md),
  [set the execution mode](how-to/set-execution-mode.md),
  [use & heal memory](how-to/use-memory.md),
  [write a skill](how-to/write-a-skill.md),
  [review what your agent wrote](how-to/review-agent-work.md),
  [integrate other tools](how-to/integrate-other-ai-tools.md),
  [share a stack](how-to/share-a-stack.md),
  [run mokata as a team (setup & operations)](how-to/team-setup.md),
  [install the Claude plugin](how-to/install-plugin.md),
  [use mokata without the plugin](how-to/use-without-plugin.md).
- **Concepts** — how each layer works: the [pipeline & gates](concepts/pipeline.md),
  [knowledge layer](concepts/knowledge.md), [memory](concepts/memory.md),
  [token governance](concepts/token-governance.md),
  [execution modes](concepts/execution-modes.md),
  [governance & audit](concepts/governance.md).
- **Reference** — complete specs: [CLI](reference/cli.md),
  [manifest & configuration](reference/manifest.md), [skills catalog](reference/skills.md).
- **[Developer guide](developer-guide.md)** — architecture, dev setup, testing, contributing.

## The feature set at a glance

| Part | Area | Highlights |
|---|---|---|
| A | Spine | manifest, capability router, detection + graceful degradation, bootstrap, `init` (incl. the `--mode seatbelt\|memory\|full` on-ramp); `upgrade` refreshes the harness wiring through setup's own preview-diff gate, and **stale wiring is visible on three channels** (`doctor --wiring` exits non-zero, and works before `init`) |
| B | Knowledge | codebase graph **mandatory by default** — embedded stdlib-AST floor in the box, adopted graphs layered on top, grep beneath; a degraded blast radius is refused as decision input unless a ledgered escape is accepted; typed queries, incremental index + staleness, drift anchors |
| C | Memory | typed persistent / decision / episodic memory, self-healing (surfacing), **no auto-writes — the poisoning defense**; in-database lexical recall (SQLite FTS5 + bm25, Postgres tsvector + ts_rank) with an optional consented semantic tier, `doctor` reporting which is live; **it ages** — usage signals + bi-temporal validity windows, and an over-budget scope gets a *proposed* archival sweep that closes a window rather than deleting a row; **it stays fast as it grows** — each tier nominates its shortlist in the database; **two people writing the same fact produce a proposal, not a lost fact**; summaries are *drafted* by the agent you are already talking to (no model embedded, no API key) and still gated; `memory export`/`import` backs it up to `.mokata/backups/` |
| D | Engine | brainstorm → spec → test → develop → review → ship, each gated (brainstorm alone runs 7 gated phases); provable completeness gate, AC-mapper, pre-mortem, prior-art step, spec-compliance, dry-run; a deferred item needs a re-gated `spec amend` before it can be built |
| E | TDD & execution | RED-before-GREEN, model routing, bug/debug/optimize engines, execution-mode selector; a run can take **its own worktree and branch** — always offered, never automatic — and `worktree list` joins your worktrees against their sessions with a staleness verdict per row; `worktree clean` and `worktree remove` tidy them — previewed, human-gated, and never touching a live session |
| F | Token governance | tracker, JIT retrieval, handback caps, output density, budget, cache-stable prefixes |
| G | Rules & governance | 4-tier rules, taxonomy, sync/async hooks, Karpathy gates, rule-learning, skill authoring |
| I | Safety & audit | secret protection, human-minted single-use approvals, **15 backed gates (six of them frontend/backend domain gates run by `mokata gate check`) — and the hook stops 6 things on your agent's native writes** (`self-protect`, the four run-state gates and the `fix-breaker`), audit ledger, lethal-trifecta gate, revert, resume |
| J | Distribution | cross-harness boundary, shareable stack manifests, portable sessions (transport derived from the repo's mode); `release-notes-check` **refuses the cut** when the notes announce a different version than the tag, or quietly drop a fact the changelog declared as a known limitation |
| K | Config | per-layer/tool toggles, profiles, local-first, committed config, trust dial, doctor (incl. the DSN deep-check and retrieval-stack line), reset |
| L | Composability | standalone commands, mid-pipeline entry, direct skills, catalog, chaining, suggestions |
| M | MCP surface | 70 tools (46 read · 23 write · 1 opt-in approve), every call answers within its budget (a long one says `running` and a same-argument re-call joins it — never run twice), typed annotations, structured `response_format`, cursor pagination, and a loud `AWAITING APPROVAL` head so waiting-on-a-human never reads as a hang |
| + | Sessions & subagents | each Claude session bound to its own run (parallel-safe); the gate state re-anchored after compaction; one context budget; subagent contracts (read-only, depth, a structured hand-back); shell writes meet the same gates as native edits; session notes drafted from the record and kept only on approval |
| + | Review window | `mokata ide` + `/mokata:review-window` — a read-only, localhost-only page: what each session wrote, the diff at any point in time from mokata's own write snapshots, the live tree, branch compare and GitHub PRs by URL; re-focuses on the asking session; `--demo` sample data ([guide](how-to/review-agent-work.md)) |

Counts in the box today: **30 Agent Skills** (16 curated + 14 domain) · **39 slash commands** ·
**71 CLI subcommands** · **70 MCP tools** · **15 backed gates** · **1 runtime dependency**.

Published docs: <https://mokata.ai/> · Source & issues:
<https://github.com/JasGujral/mokata-oss>.
