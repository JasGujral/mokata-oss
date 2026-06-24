# mokata

**Spec-driven TDD for Claude Code — knowledge-aware, self-healing memory, human-gated, local-first.**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/JasGujral/mokata-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/JasGujral/mokata-oss/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-informational.svg)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.9%E2%80%933.12-blue.svg)](pyproject.toml)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://jasgujral.github.io/mokata-oss/)
[![local-first](https://img.shields.io/badge/local--first-no%20telemetry-success.svg)](docs/concepts/governance.md)
[![Docs](https://img.shields.io/badge/docs-pages-blue.svg)](https://jasgujral.github.io/mokata-oss/)

📖 **Full documentation:** **<https://jasgujral.github.io/mokata-oss/>** — quickstart, tutorials, concepts, and the complete CLI + plugin reference.

---

**mokata** is an open-source framework for Claude Code that brings the best ideas in AI-assisted coding into one governed, knowledge-aware engine. At its core is a spec-driven TDD engine that starts by **brainstorming the problem with you** — one question at a time, weighing two or three real approaches grounded in your actual codebase before committing to anything. Only then does it draft a spec, and no code is written until that specification passes a *provable completeness gate*, every acceptance criterion is statically mapped to a test (RED before GREEN), and the finished code is reviewed back against the spec. Around that engine, mokata builds in the layers a single tool usually leaves out — a **persistent codebase knowledge graph** so the agent navigates by structure instead of guessing, **persistent, self-healing memory** (decisions, conventions, and past conversations that survive across sessions and surface their own contradictions instead of silently rotting), and **active token-and-cost governance** that retrieves just what's needed rather than dumping files into context. **Memory and its self-healing ship as part of the framework, on by default — not an add-on you wire up.** It can also orchestrate the external tools you already trust — like code graphs — under one set of gates with a full audit trail, and every durable write, whether to code, memory, or config, is **human-gated**: nothing silent, nothing autonomous. And it's **fully configurable and composable** — switch any layer or tool on or off, pick a profile, or reach for a single capability on its own: generate tests, debug a failure, or review a diff as a standalone command or directly-invoked skill, entering the pipeline wherever you need rather than running the whole thing. mokata is local-first, phones home nothing by default, is Apache-2.0 licensed, and is built so you can review every decision it makes.

> **Naming:** **mokata** = the framework · **MoStack** = the brand it ships under.

## Why mokata

- **Spec-driven, provable completeness.** No code ships until every acceptance criterion maps to a test (RED before GREEN). The completeness gate *blocks* emit — it never silently passes.
- **Knowledge + memory built in.** A codebase knowledge graph (adopted, with a grep floor) and persistent, self-healing memory (on by default) — the agent navigates by structure and remembers decisions across sessions.
- **Governed by default.** Every durable write (code, memory, config) is human-gated; sync hooks block only for security (exit 2), async hooks observe; every gate decision and tool call lands in an append-only audit ledger.
- **Local-first, no telemetry.** Nothing leaves your machine unless you explicitly wire an external service. The `minimal` profile performs zero network egress.
- **Configurable & composable.** Toggle any layer/tool, pick a profile, run any capability standalone, and enter the pipeline from any phase.

## Install

**As a Claude Code plugin (primary):**

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

**As a Python package (secondary):**

```bash
pip install -e .                 # from a checkout (Python ≥ 3.9, no required deps)
pip install -e ".[schema]"       # optional: richer manifest validation via jsonschema
```

## Quickstart

**In Claude Code (primary)** — after installing the plugin, drive the workflow with slash commands:

```text
/brainstorm        # Socratic pre-spec exploration (HARD-GATE before any spec)
/spec              # draft the spec (blocked until every acceptance criterion maps to a test)
/test  /develop    # RED-before-GREEN
/review            # spec-compliance, then quality
```

**Or via the CLI** (outside Claude Code):

```bash
mokata init                         # scaffold .mokata/ (default profile: standard); human-gated
mokata brainstorm                   # Socratic pre-spec exploration (HARD-GATE before spec)
mokata playbook                     # drive the full story end-to-end through the pipeline
```

Full walkthrough: [`docs/quickstart.md`](docs/quickstart.md) · published docs: <https://jasgujral.github.io/mokata-oss/>

## Core concepts

- **7-phase pipeline + gates** — brainstorm → analysis → strawman → pre-mortem → probes → completeness gate → emit; enter at any phase, each phase's gates still apply.
- **Knowledge layer** — structural queries (callers/callees/imports/blast-radius) via an adopted graph, grep floor when absent; staleness is surfaced, never served silently.
- **Memory** — persistent / decision / episodic, on by default, self-healing by *surfacing* old→new diffs for your approval (never a silent rewrite).
- **Execution modes** — sequential gated flow (default, lowest-cost) or parallel subagents (fresh-context isolation + two-stage review, concurrent fan-out), chosen per run, degrade-safe.
- **Governance & audit** — 4-tier rules, Karpathy gates, 4-layer secret protection, reversible writes, resume-from-last-gate, full audit ledger.

## Profiles & configuration

| Profile | Layers | Capabilities | Network |
|---|---|---|---|
| `minimal` | engine, governance | none (engine only) | **zero egress** |
| `standard` **(default)** | + knowledge, memory | code graph (ripgrep→grep) + memory (SQLite) | local-only |
| `full` | all | every known graph + memory provider (degrade to floors) | only present tools, all gated |

Per-layer / per-tool toggles, per-adapter trust dials, and profiles are all in the committed manifest. See [`docs/profiles.md`](docs/profiles.md).

## Commands & skills

`mokata skills` lists the catalog. Highlights: `/brainstorm`, `/spec`, `/test`, `/develop`, `/review`, `/debug`, `/optimize`, `/bug`. CLI also exposes `init`, `bootstrap`, `status`, `query`, `memory`, `enter`, `rules`, `audit`, `budget`, `index`, `lat-check`, `coverage`, `mcp`, `doctor`, `reset`, `suggest`, `chain`, `export`, `import`, `harness`, `playbook`.

## Contributing · Security · License

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the clean-room rule, tests in both jsonschema states.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability (privately).
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant.
- [`LICENSE`](LICENSE) — Apache-2.0, © MoStack. mokata is built clean-room (no dependency on any other framework).

## Links

- **Documentation: <https://jasgujral.github.io/mokata-oss/>** (the published docs site; source in `docs/`, built with MkDocs)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)
- Issues & discussions: the repository's Issues / Discussions tabs
