<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/mokata-wordmark-dark.svg">
    <img src="assets/brand/mokata-wordmark-light.svg" alt="mokata" width="340">
  </picture>
</p>

**The memory + seatbelt for your AI coding agent.** Spec-driven TDD for Claude Code —
knowledge-aware, self-healing memory, human-gated, local-first. It remembers your project and
stops the agent shipping the wrong thing.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/JasGujral/mokata-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/JasGujral/mokata-oss/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/release/JasGujral/mokata-oss?label=version)](https://github.com/JasGujral/mokata-oss/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg)](pyproject.toml)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://mokata.ai/)
[![local-first](https://img.shields.io/badge/local--first-no%20telemetry-success.svg)](docs/concepts/governance.md)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/JasGujral/mokata-oss/badge)](https://securityscorecards.dev/viewer/?uri=github.com/JasGujral/mokata-oss)

📖 **Full documentation:** **<https://mokata.ai/>** — quickstart, tutorials, concepts, and the complete CLI + plugin reference.

---

## See it catch a bad change (30 seconds)

An AI agent, mid-task, tries two bad changes — ship code with no spec, and stash an AWS key while
waving its own write through. mokata stops both. (Real output; run it yourself with
`mokata init --profile standard --yes` in a scratch repo.)

```console
$ mokata run develop          # the agent tries to write code with no spec...
[BLOCKED] spec-persisted — no saved spec — draft and emit it first (/mokata:spec); the completeness gate must pass before implementation.

$ # ...then tries to stash a secret, approving its own write with approve=True:
status: proposed   committed: False
`approve`/`confirm` no longer commit: an approval is MINTED BY A HUMAN out-of-band (`mokata approve <id>`) and can only be REFERENCED here by id. Typing approve=true is not consent — it never was.

$ mokata audit                # the gate decision is on the ledger
audit ledger — 1 entry:
  #1   gate        gate=spec-persisted phase=develop decision=blocked reason=no saved spec ...
```

**A model cannot approve its own writes at all.** It can only *propose* one and hand you an id; the
approval is minted by *you*, out-of-band, in a process the model isn't driving — and it is
single-use, content-hashed, and expires. And if you *do* approve that write, the secret-guard still
refuses it: approval is a methodology gate, never a security override.

There is a third block the agent can't route around either — reaching past mokata's tools for the
editor's own `Write` trips a `PreToolUse` hook (exit 2) and the file is never touched. It's in the
full demo.

**The full copy-paste demo + a 60-second screencast script:** [mokata catches a bad change](docs/tutorials/catches-a-bad-change.md). See *every* differentiator run in [differentiators in action](docs/tutorials/differentiators-in-action.md).

---

## See mokata in action

A quick tour of the workflow and skills — brainstorm → spec → test → develop → review → ship, with governance, memory, and the always-on status badge.

https://github.com/user-attachments/assets/a6940119-1edb-4017-b935-cd48b80b4ea9

> ▶ Video not playing inline? [Watch it here](https://github.com/user-attachments/assets/a6940119-1edb-4017-b935-cd48b80b4ea9).

---

**mokata** is an open-source framework for Claude Code that brings the best ideas in AI-assisted coding into one governed, knowledge-aware engine. At its core is a spec-driven TDD engine that starts by **brainstorming the problem with you** — one question at a time, weighing two or three real approaches grounded in your actual codebase before committing to anything. Only then does it draft a spec, and no code is written until that specification passes a *provable completeness gate*, every acceptance criterion is statically mapped to a test (RED before GREEN), and the finished code is reviewed back against the spec. Around that engine, mokata builds in the layers a single tool usually leaves out — a **persistent codebase knowledge graph** so the agent navigates by structure instead of guessing, **persistent, self-healing memory** (decisions, conventions, and past conversations that survive across sessions and surface their own contradictions instead of silently rotting), and **active token-and-cost governance** that retrieves just what's needed rather than dumping files into context. **Memory and its self-healing ship as part of the framework, on by default — not an add-on you wire up.** It can also orchestrate the external tools you already trust — like code graphs — under one set of gates with a full audit trail, and every durable write, whether to code, memory, or config, is **human-gated**: nothing silent, nothing autonomous. And it's **fully configurable and composable** — switch any layer or tool on or off, pick a profile, or reach for a single capability on its own: generate tests, debug a failure, or review a diff as a standalone command or directly-invoked skill, entering the pipeline wherever you need rather than running the whole thing. mokata is local-first, phones home nothing by default, is Apache-2.0 licensed, and is built so you can review every decision it makes.

> **Naming:** **mokata** = the framework · **MoStack** = the brand it ships under.

## Why mokata

- **Spec-driven, provable completeness.** No code ships until every acceptance criterion maps to a test (RED before GREEN). The completeness gate *blocks* emit — it never silently passes.
- **Knowledge + memory built in.** A codebase knowledge graph (adopted, with a grep floor) and persistent, self-healing memory (on by default) — the agent navigates by structure and remembers decisions across sessions.
- **Governed by default.** Every durable write (code, memory, config) is human-gated — the model proposes, *you* mint the approval out-of-band (`mokata approve <id>`; single-use, content-hashed). Sync hooks block (exit 2), async hooks observe: two sync blocks ship and they differ in kind — the **secret-guard** (security; **never** overridable) and the **gate-guard** (run-state/methodology; overridable with a reason, on the ledger). Every gate decision and tool call lands in an append-only audit ledger.
- **Local-first, no telemetry.** Nothing leaves your machine unless you explicitly wire an external service. The `minimal` profile performs zero network egress.
- **Configurable & composable.** Toggle any layer/tool, pick a profile, run any capability standalone, and enter the pipeline from any phase.
- **Meets you where you work, ships trustworthy.** Runs under Claude Code, Cursor, Copilot, Windsurf, Codex, Gemini CLI, and Aider (a VS Code extension is planned, not yet available); sessions travel between machines; teams share one governed backend safely; and releases carry an SBOM + Sigstore provenance — no telemetry, ever.

## Install

**Start here → [Getting started](docs/getting-started.md).** The canonical, pip-first path.

**In Claude Code (recommended)** — one install, then one command wires the full workflow
(slash commands + Agent Skills + MCP server + status line), on your existing Claude Code
sign-in — no API key:

```bash
pip install mokata               # MCP server works out of the box (the SDK is a default dep)
mokata setup claude              # wires commands + skills + MCP + statusline — human-gated
# restart Claude Code, then:
mokata mcp status                # expect: mokata-mcp: CONNECTED ✓
```

> Requires **Python ≥ 3.10**.

**As a CLI, with any AI tool** — harness-agnostic (Gemini, Codex, scripts, CI). The CLI is the
engine's mechanics (no LLM of its own); wire it into any shell- or MCP-capable assistant:

```bash
pip install mokata
mokata init
```

See [Integrate with other AI tools](docs/how-to/integrate-other-ai-tools.md).

<!-- mokata:directory-listing:start -->
> ⏳ **Pending Claude plugin-directory approval.** A one-click Claude Code **plugin** is
> **planned, not yet available** — mokata isn't registered on any Claude Code marketplace.
> The supported way to run mokata inside Claude Code today is the pip-first path:
> `pip install mokata` → `mokata setup claude`
> (see [Getting started](https://mokata.ai/getting-started/)).
> _(This notice auto-flips once the listing is approved — single source:
> `scripts/directory_listing.py`.)_
<!-- mokata:directory-listing:end -->

> **Contributing (developers).** Only clone if you're working on mokata itself — end users never
> need to: `git clone https://github.com/JasGujral/mokata-oss.git && cd mokata-oss && pip install -e .`
> (on Python ≥ 3.10 this also pulls the MCP SDK). See [CONTRIBUTING.md](CONTRIBUTING.md).

## Quickstart

**In Claude Code (primary)** — after `mokata setup claude`, drive the workflow with slash commands:

```text
/mokata:brainstorm        # Socratic pre-spec exploration (HARD-GATE before any spec)
/mokata:spec              # draft the spec (blocked until every acceptance criterion maps to a test)
/mokata:test  /mokata:develop    # RED-before-GREEN
/mokata:review            # spec-compliance, then quality
```

**Or via the CLI** (outside Claude Code):

```bash
mokata init                         # scaffold .mokata/ (default profile: standard); human-gated
mokata brainstorm                   # Socratic pre-spec exploration (HARD-GATE before spec)
mokata playbook                     # drive the full story end-to-end through the pipeline
```

> A `pip` CLI install is **terminal-only** — it runs the deterministic engine with no LLM
> attached. It does **not** put mokata inside Claude Code; for that, install the plugin or run
> `mokata setup claude`. ([Why two ways](https://mokata.ai/concepts/execution-model/).)

Full walkthrough: [`docs/quickstart.md`](docs/quickstart.md) · full hands-on guide → [the Complete Guide](https://mokata.ai/tutorials/mokata-complete-guide/) (with a downloadable PDF) · published docs: <https://mokata.ai/>

## Core concepts

- **7-phase pipeline + gates** — brainstorm → analysis → strawman → pre-mortem → probes → completeness gate → emit; enter at any phase, each phase's gates still apply.
- **Knowledge layer** — structural queries (callers/callees/imports/blast-radius) via an adopted graph, grep floor when absent; staleness is surfaced, never served silently.
- **Memory** — persistent / decision / episodic, on by default, self-healing by *surfacing* old→new diffs for your approval (never a silent rewrite).
- **Execution modes** — sequential gated flow (default, lowest-cost) or parallel subagents (fresh-context isolation + two-stage review, concurrent fan-out), chosen per run, degrade-safe; optional git-worktree isolation keeps concurrent/paused work off the main tree.
- **Governance & audit** — 4-tier rules, Karpathy gates, 4-layer secret protection, per-task model routing, reversible writes, full audit ledger. Inside an active run, three **run-state gates** stop the agent's own file writes in Claude Code — `spec-persisted` (no spec yet), `no-code-without-failing-test` (no RED on record), and `spec-scope` (outside the spec's authorized surface, or a feature you agreed *not* to build). Test files stay writable; hand-editing outside a run is never policed; `mokata gate status` shows what's enforced and `mokata gate override <gate> --reason "…"` lifts one, on the ledger. See the governed state at a glance with `mokata govern` (a read-only dashboard) and the *what-it-did-and-why* timeline with `mokata audit --why`.
- **Session lifecycle & portable sessions** — list runs (`mokata sessions`), resume from the last passed gate (`mokata resume`), pause/resume a mid-brainstorm (the HARD-GATE still holds), snapshot the in-flight session with `mokata session save` (local, ungated), and **carry it across machines or hand it to a teammate**: `mokata session push <tag>` / `pull <tag>` packages checkpoints + approach + in-progress brainstorm + relevant memory into a machine-path-free, versioned, **secret-scanned + human-gated** bundle (`--save-first` to snapshot and bundle in one step; sharing unfinished thinking needs an explicit `--allow-in-progress`, or `--requirements-only` to hand over just the distilled requirements). **An approach approval never crosses machines** — `pull` strips it, so you re-approve here (the emitted spec travels intact). Sessions carry a human-friendly name you can `session name`.
- **Progress & visibility, one model** — one `RunProgress` drives every surface: an always-on **stage badge** (statusline, on by default, merge-safe), the native **to-do widget** where the harness has one, the printed run-progress block, and the per-subagent **lanes** in `mokata watch` / `progress --lanes`. No duplicated progress logic — channel-specific renderers over one source of truth.
- **Runs under every agent, shows up in your editor** — the engine runs behind a thin boundary (`mokata harness` shows the capability matrix) with in-harness surfaces for **Claude Code, Cursor, GitHub Copilot, Windsurf, Codex, Gemini CLI, and Aider**, each degrading clearly where it lacks a capability. A **VS Code extension** (`editors/vscode`) with a read-only Copilot Chat `@mokata` participant — governance, memory, and run-progress in your editor — is **planned (not yet available)**.
- **Team & sharing** — one guided `mokata team join` chains adopt → shared memory (BYO Postgres) → vault pull → onboard → doctor (each human-gated, secret-scanned, reversible); publish/adopt governed per-framework **community stacks** (`mokata stacks`); and the team's **audit/activity logs** can live shared or local, conflict-free — **no telemetry, nothing phoned home**. One shared backend safely hosts **many projects**: every shared row is scoped by a stable project key, so review defaults to your project (`--all` / `--project` to span or pick).
- **Supply-chain trust** — releases ship a reproducible sdist+wheel, a **CycloneDX SBOM**, and a **Sigstore build-provenance attestation** generated at tag-time in CI (the repo ships no pre-signed artifacts); all five CI workflows are least-privilege and SHA-pinned.

## Profiles & configuration

| Profile | Layers | Capabilities | Network |
|---|---|---|---|
| `minimal` | engine, governance | none (engine only) | **zero egress** |
| `standard` **(default)** | + knowledge, memory | code graph (ripgrep→grep) + memory (SQLite) | local-only |
| `full` | all | every known graph + memory provider (degrade to floors) | only present tools, all gated |

Per-layer / per-tool toggles, the trust dial (per write surface or per tool — enforced today on the MCP write surface), and profiles are all in the committed manifest. See [`docs/profiles.md`](docs/profiles.md).

## Commands & skills

`mokata skills` lists the catalog — **16 curated skills** Claude can auto-engage (each also a `/mokata:*` command): the pipeline (`brainstorm`, `refine`, `onboard`, `spec`, `test`, `develop`, `review`, `debug`, `optimize`, `bug`, `ship`) plus standalone/auto-firing `govern`, `session`, `playbook`, `docsync` (docs↔code reconciler, human-gated fixes), and `mcp-repair` — and **10 domain-knowledge skills** (API design, security, performance, frontend/accessibility, browser testing, CI/CD, git workflow, deprecation, docs/ADRs, shipping) that attach to the pipeline phase where they apply. Utilities like `version` stay CLI-only.

The CLI exposes 65 subcommands, including:

- **Pipeline & skills** — `brainstorm`, `spec` (`show`/`amend`), `test`, `develop`, `review`, `ship`, `refine`, `debug`, `bug`, `optimize`, `onboard`, plus `run`/`enter`/`exec`/`playbook` (`--dense`)/`preview`/`chain` and `skill author`.
- **Approvals & gates (the seatbelt)** — `approve [<id>]` (mint the approval for a proposed write in *your* terminal; bare `approve` lists what's pending — there is deliberately no in-harness approve tool) and `gate` (`status`/`override`/`clear` — the run-state gates; an override needs a reason and is ledgered).
- **Inspection (read-only)** — `status`, `query`, `rules`, `audit` (`--why`), `budget`, `coverage`, `lat-check`, `index`, `doctor`, `baseline`, `harness`, `progress` (`--lanes`), `watch`, `govern`, `sessions`, `windows` (concurrent Claude Code windows on one repo).
- **Memory & knowledge** — `memory` (`edit`/`export`/`import`/`migrate`/`consolidate`), `govern`.
- **Session lifecycle & portability** — `sessions`, `resume`, `enter`, `session` (`save`/`push`/`pull`/`list`/`name`), `worktree create` (give a window its own working tree).
- **Team, stacks & sharing** — `team` (`init`/`join`/`adopt`/`connect`/`disconnect`), `mode`, `sync`, `stacks` (`list`/`search`/`show`/`install`), `vault`.
- **Setup, config & distribution** — `init`, `setup`/`unsetup`, `reconfigure`, `tour`, `config`, `bootstrap`, `validate`, `route`, `detect`, `reset`, `suggest`, `mcp`, `ci-check`, `export`/`import`, `version`/`upgrade`, `release-check`.

Full list with flags: the [CLI reference](https://mokata.ai/reference/cli/).

## Contributing · Security · License

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the clean-room rule, tests in both jsonschema states.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability (privately).
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant.
- [`LICENSE`](LICENSE) — Apache-2.0, © MoStack. mokata is built clean-room (no dependency on any other framework).

## Links

- **Documentation: <https://mokata.ai/>** (the published docs site; source in `docs/`, built with MkDocs)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)
- Issues & discussions: the repository's Issues / Discussions tabs
