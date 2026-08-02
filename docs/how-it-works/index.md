# How mokata works (architecture)

This is the developer's map of mokata: what the moving parts are, how they fit together, and
where the guarantees come from. If the [concepts](../concepts/pipeline.md) section explains each
subsystem on its own, this section explains the **whole machine** — and it goes deep on the two
layers that make mokata more than a prompt pack: the [skills layer](skills-and-gates.md) and the
[domain-skills layer](domain-skills.md) that attaches technology knowledge to the pipeline.

Everything here describes what actually ships and runs today. Nothing on this page is roadmap.

## The one idea

A code assistant *advises*. mokata **governs, remembers, and audits** — it wraps the advice in a
pipeline that won't skip its own gates, a memory that outlives the session, and a ledger that
records every decision.

That single idea shows up in every subsystem below: the pipeline won't emit a spec without an
approved approach, won't implement without a failing test, won't land without a passing review;
memory won't inject a stale or conflicting fact silently; and no durable write — code, memory,
config, or a git action — happens without an explicit human approval.

## The layers

```
        you  +  Claude Code (the harness)
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│  SKILLS  — 16 pipeline/capability skills + 10 domain skills    │
│  each carries a Contract and a ⛭ activation line; each maps    │
│  to a REAL gate below (not prose)                              │
├───────────────────────────────────────────────────────────────┤
│  PIPELINE — brainstorm → spec → test → develop → review → ship │
│  backed gates: approach-approval · completeness · spec-persisted│
│  · no-code-without-failing-test · deviation · hard-rule ·      │
│  ship-readiness · write-gate · secret-guard                    │
├───────────────────────────────────────────────────────────────┤
│  KNOWLEDGE graph   │  MEMORY engine (local + team scopes)      │
│  structural facts  │  typed, human-gated, precedence-resolved  │
├───────────────────────────────────────────────────────────────┤
│  GOVERN — WriteGate (secret-scan → human gate → audit ledger)  │
├───────────────────────────────────────────────────────────────┤
│  HOOKS — SessionStart briefing · secret-guard (security, hard) │
│  · gate-guard (run-state gates, P14-overridable) + MCP tools   │
└───────────────────────────────────────────────────────────────┘
```

Each box is a link into the deep-dive. The rest of this page walks them in order.

## 1. The pipeline & its gates

mokata's engine is a phased pipeline with **gates that must hold before a run proceeds**. The
developer-facing arc is `brainstorm → spec → test → develop → review → ship`; under `spec`, a
7-phase spec engine (`brainstorm → analysis → strawman → pre_mortem → probes → completeness_gate
→ emit`) turns an approved approach into testable acceptance criteria.

The gates are real code, not advice. **Nine are *backed*** — each names the module that enforces
it (`skill_contracts.GATES`):

| Gate | Where | Blocks on |
|---|---|---|
| `approach-approval` | brainstorm | no approach explicitly approved |
| `completeness` | spec | any acceptance criterion with no mapped test (or an empty spec) |
| `spec-persisted` | before develop/test | no saved spec with ≥1 acceptance criterion |
| `no-code-without-failing-test` | test → develop | implementing before a recorded RED test exists |
| `spec-scope` | develop | a write outside the spec's authorized surface, or one spelling a deferred item's marker |
| `deviation` | spec/refine/develop | a change that would break a saved spec or recorded decision |
| `hard-rule` | any phase | an in-scope hard governance rule (fail-closed, no runtime override) |
| `write-gate` + `secret-guard` | emit, ship, memory, config | an un-approved durable write; a secret in the payload |
| `ship-readiness` | ship | landing before green tests + met ACs + a passed review |

Four of them — `approach-approval`, `spec-persisted`, `no-code-without-failing-test` and
`spec-scope` — are also enforced **outside** mokata's own tools, on the harness's native
`Write`/`Edit`, by the
[gate-guard hook](skills-and-gates.md#the-gate-guard-the-gates-enforced-on-native-edits) (§7). That
is what makes them structural rather than advisory. Everything else a skill states as its headline
(`red-before-green`, `spec-then-quality`, `measure-first`, …) is an **advisory protocol boundary**,
labelled as such in the code rather than dressed up as enforcement.

You can run the whole thing (`mokata playbook`), enter a slice (`mokata enter <phase>`), or run
one skill standalone (`mokata run <skill>`) — the gates apply either way. Full detail:
[Pipeline & gates](../concepts/pipeline.md).

## 2. The skills layer

Every mokata capability is a **skill**: a `SKILL.md` Claude Code can auto-engage from its
description, backed by the identical protocol the `/<name>` command runs. There are two
groups — the **16 pipeline/capability skills** (the registry `mokata skills` prints) and the
**10 domain skills** below, for **26 in total**.

What makes a mokata skill more than a prompt:

- a **Contract** (CAN / MUST NOT / DEPENDS ON) whose boundaries map to a real gate in the table
  above — no Contract claims enforcement the code doesn't back;
- a single-sourced **`⛭ mokata <skill> active — gate: …`** activation line, so the statusline,
  the in-chat banner, and `mokata progress` always agree on which skill is running;
- **anti-rationalization** and a **verification** checkbox gate, so a skill can't talk itself past
  its own discipline.

Deep-dive: [The skills layer & gate map](skills-and-gates.md).

## 3. The domain-skills layer

Ten **domain skills** — API, security, performance, frontend-a11y, browser-testing, CI/CD, git,
deprecation, docs/ADR, and shipping — carry the technology knowledge the pipeline skills don't.
The point is *native integration*: each domain **attaches to the phase where it applies and feeds
the gate that already runs there**, rather than sitting beside the pipeline as advice.

A domain is selected from the **graph surface an approach touches** (not keyword-matched from the
prompt), persisted into the spec as a first-class constraint, and then engaged by exactly the
phases that constraint names. This is the layer that turns "the repo advises" into "mokata
governs + remembers + audits."

Deep-dive: [The domain-skills layer](domain-skills.md).

## 4. The knowledge graph

The knowledge layer gives every phase **structural facts about the codebase** — what calls what,
what a change's blast-radius is, where a symbol is defined. Brainstorm grounds an approach in it,
develop pulls it JIT for the symbols in play, review reads it for the architecture axis, and the
domain classifier derives the domains-in-play from it. The layer answers structurally from an
**embedded, zero-dependency stdlib-AST floor** out of the box, and lets you adopt an external
graph (`code-review-graph` / `serena`) for cross-language precision. (The Neo4j backend still
works but is **deprecated**, scheduled for removal in 0.0.17 — the graph is derived data, so
re-index with a supported backend rather than migrating.) `graph.required` is on
by default: mokata refuses to present a *degraded* (grep-floor) blast radius as decision input
unless you accept it (`--allow-degraded`, ledgered). See
[Knowledge layer](../concepts/knowledge.md).

## 5. The memory engine & scopes

Memory is mokata's **persistent, typed store** of decisions, guardrails, conventions, and context
— captured through the guided `mokata onboard` and human-gated on every write. It has two scopes:

- **local** (the default): byte-identical, uncached, no scope filtering — everything stays on your
  machine;
- **team**: point a whole team's mokata at one Postgres DSN and everyone reads/writes the same
  store live, with access policy enforced and a **precedence engine** that resolves conflicting
  scoped items to a single winner per subject (so two teammates' contradictory rules never both
  inject).

Local mode is untouched by any team feature. See [Memory](../concepts/memory.md) and
[Team mode](#8-team-mode).

## 6. Govern — the human-gated write spine

Every durable write in mokata — an emitted spec, a memory item, a config change, a git action, a
docsync reconcile — goes through **one WriteGate**: secret-scan → explicit human approval → append
to the **audit ledger**. For a write driven from inside the harness, that approval is **minted by
you out-of-band** (`mokata approve <proposal-id>`, in your own terminal) and merely *referenced* by
the model — never a flag the model types for itself. Nothing is silent and nothing is autonomous;
`mokata govern` and `mokata audit` replay exactly what changed and why. This is the P2 guarantee
that the whole stack rests on. See [Governance & audit](../concepts/governance.md).

## 7. Hooks & MCP — the harness surface

mokata integrates with Claude Code through two harness surfaces:

- **Hooks.** A **SessionStart** hook injects a sub-2k-token briefing (the always-on rules + a
  one-line resume pointer) when you open a repo. Two **PreToolUse** hooks then run synchronously on
  the harness's own file-mutation tools and **block on exit code 2**: the **secret-guard** (a
  *security* block — `Write`/`Edit`/`MultiEdit`/`Bash`, never overridable) and the **gate-guard**
  (a *methodology* block — `Write`/`Edit`/`MultiEdit`/`NotebookEdit`, holding the run-state gates
  `approach-approval` · `spec-persisted` · `no-code-without-failing-test` · `spec-scope`,
  overridable only by an
  explicit, re-confirmed, ledgered `mokata gate override`). Sync hooks block; async hooks only
  observe. `mokata setup` writes the hook wiring with an absolute entry-point path so it resolves
  even under a GUI-launched minimal PATH, and `--no-hooks` skips it cleanly. **Claude Code is the
  only harness that declares the `hooks` capability** — elsewhere the gate-guard is never wired and
  the run-state gates enforce nothing. Deep-dive:
  [the gate-guard](skills-and-gates.md#the-gate-guard-the-gates-enforced-on-native-edits).
- **MCP.** mokata's `mokata-mcp` server exposes **61 tools** — **40 read** (`progress`, `lanes`,
  `watch`, `govern`, `query`, …), **20 write**, and **1 approve** — so the pipeline's state and the
  graph are reachable from inside the harness without leaving the chat. Every *write* tool is
  **propose-only**: it returns a `proposal_id` and commits nothing until a human mints the approval
  out-of-band with `mokata approve <id>` (bare `mokata approve` lists what is awaiting one). See
  [Command surfaces (CLI ↔ slash ↔ MCP)](../reference/command-surfaces.md).

## 8. Team mode

Team mode is memory + audit made **shared and enforcing**: one Postgres-backed store, a real
run-identity stamped on every write, an access policy derived from `settings.access.grants`, a
precedence engine that resolves conflicts, and a shared activity log. It is **opt-in and
local-first** — nothing connects until you wire a DSN, and local mode stays byte-identical.
Operational guides: [Team setup](../how-to/team-setup.md) and
[Team audit](../how-to/team-audit.md).

## 9. docsync — keeping the docs true to the code

The docs you're reading are themselves governed. **docsync** (`mokata docsync`) audits the public
docs against the live code — command names, skill counts, install path, version examples, symbols
— and can **reconcile** drift through the same human-gated WriteGate. It auto-fires when a change
touches a symbol a doc references, and it backs brainstorm's doc-freshness check. Deep-dive:
[docsync](docsync.md).

## Where to go next

- Ground yourself in the flow: [Pipeline & gates](../concepts/pipeline.md).
- Understand the two skills layers: [Skills & gate map](skills-and-gates.md) →
  [Domain skills](domain-skills.md).
- See the guarantees: [Governance & audit](../concepts/governance.md).
- Get it running: [Get started](../getting-started.md) (`pip install mokata` →
  `mokata setup claude`).
