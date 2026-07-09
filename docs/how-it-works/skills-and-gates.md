# The skills layer & gate map

mokata's capabilities are **skills**. This page explains what a skill is under the hood, how its
Contract maps to a **real gate** (never prose), and how the one activation surface keeps every
channel in agreement. For the operational catalog, see [Skills reference](../reference/skills.md);
for the phased flow, see [Pipeline & gates](../concepts/pipeline.md).

## Two surfaces, one source

Claude Code exposes two ways to invoke a capability: a **slash command** (`/mokata:<name>`) and an
**Agent Skill** (a `SKILL.md` file Claude auto-engages from its `description`). mokata renders both
from the **same** `templates/commands/<name>.md` source — the skill's trigger text is the
template's own `description`, and its body is the template's protocol verbatim, behind a fixed
banner. Nothing is hand-copied, so the two surfaces cannot drift; a drift-guard test re-renders and
compares.

Run `mokata skills` for the live list, `mokata skills <name>` to reveal a skill's full prompt and
gate, and `mokata run <name>` to run one standalone (each keeps only its own gate).

## The two groups — 26 skills

- **16 pipeline/capability skills** — the curated set Claude may engage on its own: the pipeline
  gates (`brainstorm`, `spec`, `test`, `develop`, `review`, `refine`, `debug`, `bug`, `optimize`,
  `ship`) plus the knowledge/governance/portability capabilities (`onboard`, `govern`, `session`,
  `playbook`), the docs↔code capability (`docsync`), and harness repair (`mcp-repair`).
- **10 domain skills** — API, security, performance, frontend-a11y, browser-testing, CI/CD, git,
  deprecation, docs/ADR, shipping — see [the domain-skills layer](domain-skills.md).

That's **26 skills in total**. The counts are read from the registry, not hand-kept — `mokata
skills` is the single source, and [docsync](docsync.md) fails the build if any doc claims a number
the registry doesn't report.

## What a mokata skill carries

A prompt tells the model what to do. A mokata skill also **binds** it:

### 1. A Contract mapped to a real gate

Each skill declares a **Contract** — what it CAN do, what it MUST NOT do, and what it DEPENDS ON.
Crucially, every hard boundary in a Contract maps to an actual enforcement point in the engine, not
a sentence the model is trusted to honour:

| Skill | Its gate | The real check behind it |
|---|---|---|
| `brainstorm` | `approach-approval` | no spec until one approach is explicitly approved (human) |
| `spec` | `completeness` | every acceptance criterion maps to a test, or `emit` is refused |
| `test` | `red-before-green` | a failing test must exist first |
| `develop` | `no-code-without-failing-test` + `spec-persisted` | a saved spec + a RED test precede implementation |
| `review` | `spec-then-quality` | two passes — against the spec, then quality (human) |
| `ship` | `finish-is-human-landed` | green tests + met ACs + a passed review, then a human-chosen land |
| `govern` / memory / config edits | WriteGate | secret-scan → human approval → audit ledger |

If a skill's prose promised a boundary the engine didn't back, that would be a bug the skill-lint
catches — Contracts are grounded in a boundary→gate map, so a Contract never claims enforcement
that isn't there.

### 2. One activation surface (the `⛭` line)

Every skill renders a single-sourced activation line — for example, on `develop`:

> **⛭ mokata develop active** — gate: no implementation lands without a failing test that pins
> the change.

That line is constructed in exactly one place (`progress.active_skill_line`) and reused by the
statusline badge, the in-chat banner, and `mokata progress` — so all three always agree on which
skill is active and which gate it holds. You never have to guess whether mokata "stepped in": the
banner says so, and the boundary it will hold is stated up front.

### 3. Anti-rationalization + a verification gate

Each skill carries an **anti-rationalization** table (excuse → reality — e.g. develop's "I'll just
clean this up while I'm here") and a **verification** checkbox the skill must satisfy before it
claims done. This is the discipline that stops a skill from talking itself past its own gate: the
gate is the hard stop, the verification is the evidence, and the anti-rationalization is the
pre-empt.

## Standalone, composable, gated

Skills don't require the full pipeline. Each runs on its own and applies **only its own gate**:

```bash
mokata skills                 # the live catalog
mokata skills review          # reveal review's full prompt + gate
mokata run review             # run one skill standalone
mokata chain spec test        # a manual chain — each step keeps its gate
mokata enter completeness_gate  # run just one pipeline phase's gate
```

Because the gate travels with the skill, there's no "fast path" that skips it — running `develop`
alone still refuses to implement without a failing test.

## How skills auto-engage

Skills are **model-invocable**: Claude Code can activate one from its `description` when the moment
fits (weighing options → `brainstorm`; a contract change → the `api` domain skill), announcing
itself with the `⛭` banner. Auto-engagement only *starts* the capability — the gate still holds,
and it won't hijack a direct command or mid-implementation work. Where a skill and a same-named
command collide, the **skill takes precedence**, which is why every body carries the full protocol
inline rather than telling Claude to "go run the command."

## See also

- [The domain-skills layer](domain-skills.md) — how technology knowledge attaches to phases + gates.
- [Skills reference](../reference/skills.md) — the operational catalog + gate kinds.
- [Pipeline & gates](../concepts/pipeline.md) — the phased flow the gates live in.
- [Governance & audit](../concepts/governance.md) — the WriteGate every durable write rides.
