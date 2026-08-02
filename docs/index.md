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

Four of those gates are enforced by a hook rather than by mokata's own tools, so they hold for
your agent's **native** edits too: no approved approach, no persisted spec, or no failing test,
and the write is blocked outright. And because no code path writes memory without a
human-minted approval, mokata's memory **cannot be poisoned** by content you never approved.

You don't have to adopt all of it at once — `mokata init --mode seatbelt` wires just the gates
and the graph, `--mode memory` adds persistent memory, `--mode full` turns on everything.

mokata is pure Python (≥ 3.10) — the engine is stdlib-only, and the MCP SDK is its single
runtime dependency, so the MCP server works out of the box. It is Apache-2.0 under
**MoStack**, and is built clean-room (no dependency on any other framework).

**Start here → [Getting started](getting-started.md).** The canonical, pip-first path:
`pip install mokata` → `mokata setup claude` → restart Claude Code → `mokata mcp status`
(CONNECTED ✓). That one command wires the full workflow (slash commands + Agent Skills + MCP
server + status line) into Claude Code. Prefer the terminal or another AI tool? The same
`pip install mokata` gives you the **CLI** (Gemini, Codex, scripts, CI). A one-click Claude
Code **plugin** is planned but not yet available.

mokata never calls a model itself — the brain always comes from the harness. For *why* there
are two ways to run it (and which fits your goal), see
[How mokata uses an LLM: harness vs CLI](concepts/execution-model.md).

## What's here

This site follows the [Diátaxis](https://diataxis.fr/) model:

- **[Getting started](quickstart.md)** — install and run your first pipeline in minutes.
- **Tutorials** — [**mokata catches a bad change**](tutorials/catches-a-bad-change.md): the 60-second wow demo (copy-paste it and watch the seatbelt catch a bad change); [**differentiators in action**](tutorials/differentiators-in-action.md): a runnable demo of every differentiator (graph, memory, governance — see them work); [run a story end-to-end](tutorials/run-a-story.md): a guided, learn-by-doing walkthrough; and [the Complete Guide](tutorials/mokata-complete-guide.md): every command, gate, and layer (with a downloadable PDF).
- **How-to guides** — task recipes: [configure a profile](how-to/configure-a-profile.md),
  [set the execution mode](how-to/set-execution-mode.md),
  [use & heal memory](how-to/use-memory.md),
  [write a skill](how-to/write-a-skill.md),
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
| A | Spine | manifest, capability router, detection + graceful degradation, bootstrap, `init` (incl. the `--mode seatbelt\|memory\|full` on-ramp) |
| B | Knowledge | codebase graph **mandatory by default** — embedded stdlib-AST floor in the box, adopted graphs layered on top, grep beneath; a degraded blast radius is refused as decision input unless a ledgered escape is accepted; typed queries, incremental index + staleness, drift anchors |
| C | Memory | typed persistent / decision / episodic memory, self-healing (surfacing), **no auto-writes — the poisoning defense**; in-database lexical recall (SQLite FTS5 + bm25, Postgres tsvector + ts_rank) with an optional consented semantic tier, `doctor` reporting which is live; `memory export`/`import` backs it up to `.mokata/backups/` |
| D | Engine | brainstorm → spec → test → develop → review → ship, each gated (brainstorm alone runs 7 gated phases); provable completeness gate, AC-mapper, pre-mortem, prior-art step, spec-compliance, dry-run; a deferred item needs a re-gated `spec amend` before it can be built |
| E | TDD & execution | RED-before-GREEN, model routing, bug/debug/optimize engines, execution-mode selector |
| F | Token governance | tracker, JIT retrieval, handback caps, output density, budget, cache-stable prefixes |
| G | Rules & governance | 4-tier rules, taxonomy, sync/async hooks, Karpathy gates, rule-learning, skill authoring |
| I | Safety & audit | secret protection, human-minted single-use approvals, **10 backed gates (5 enforced on your agent's native writes by a hook)**, audit ledger, lethal-trifecta gate, revert, resume |
| J | Distribution | cross-harness boundary, shareable stack manifests, portable sessions (transport derived from the repo's mode) |
| K | Config | per-layer/tool toggles, profiles, local-first, committed config, trust dial, doctor (incl. the DSN deep-check and retrieval-stack line), reset |
| L | Composability | standalone commands, mid-pipeline entry, direct skills, catalog, chaining, suggestions |
| M | MCP surface | 61 tools (40 read · 20 write · 1 opt-in approve), every call bounded with a `timed_out` status that names the operation, typed annotations, structured `response_format`, cursor pagination, and a loud `AWAITING APPROVAL` head so waiting-on-a-human never reads as a hang |

Counts in the box today: **26 Agent Skills** (16 curated + 10 domain) · **37 slash commands** ·
**69 CLI subcommands** · **61 MCP tools** · **10 backed gates** · **1 runtime dependency**.

Published docs: <https://mokata.ai/> · Source & issues:
<https://github.com/JasGujral/mokata-oss>.
