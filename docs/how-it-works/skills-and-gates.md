# The skills layer & gate map

mokata's capabilities are **skills**. This page explains what a skill is under the hood, how its
Contract maps to a **real gate** (never prose), how the **gate-guard hook** enforces the run-state
gates on your *native* edits, and how the one activation surface keeps every channel in agreement.
For the operational catalog, see [Skills reference](../reference/skills.md); for the phased flow,
see [Pipeline & gates](../concepts/pipeline.md).

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
| `develop` | `no-code-without-failing-test` + `spec-persisted` + `spec-scope` | a saved spec + a RED test precede implementation, and the write stays inside the scope the spec authorized |
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

## The gate-guard — the gates enforced on *native* edits

A Contract binds the skill. It cannot bind the **native `Write`/`Edit`** Claude Code reaches for
when no skill is active — and a gate that only fires inside mokata's own tools is a seatbelt with an
unlocked door. So mokata ships a **`PreToolUse` hook** that runs on the harness's own file-mutation
tools, decides from state on disk, and **blocks with exit code 2**. There are two, and they are
different in kind:

| Hook | Runs on | Kind |
|---|---|---|
| `mokata-hook secret-guard` | `Write` · `Edit` · `MultiEdit` · **`Bash`** | **security** — never overridable |
| `mokata-hook gate-guard` | `Write` · `Edit` · `MultiEdit` · `NotebookEdit` | **methodology** — overridable, explicitly and on the ledger |

The gate-guard enforces **three run-state gates** — the same ids the in-tool gates use, at a new
enforcement point (a net *under* them, never a second opinion):

| Gate | Blocks a native write to an implementation file when… |
|---|---|
| `spec-persisted` | an approach is approved for this run but **no spec is emitted** |
| `no-code-without-failing-test` | the spec is emitted but **no failing test is on record** |
| `spec-scope` | the write is **outside the spec's authorized surface**, spells an item the spec explicitly **deferred**, or a **spec amend is in progress** |

A violation is one stderr line and exit 2 — it names the file, says what to do, and offers the
override:

```text
BLOCKED [no-code-without-failing-test] no failing test is on record for this run — auth.py is
implementation. Write the failing test first and watch it fail (/mokata:test), or override:
mokata gate override no-code-without-failing-test --reason "<why>"
```

### What `spec-scope` checks

When the model emits the spec, it records the **scope you approved**: the paths the change is
*authorized* to touch, and the items you explicitly agreed **not** to build — the **deferred** ones,
each carrying the literal **marker** it would spell in code (a token like `batch_update`). You never
hand-author that JSON; you agree to it in the spec.

The marker is why scope is checkable at all. A deferred feature usually lands *inside a perfectly
authorized file* — the batch endpoint goes in the same module as the single-item ones — so the path
alone can't see it. The content can: the hook matches the marker as a literal substring of the text
about to be written, and blocks. **A user's instruction to build something the spec deferred is
authorization to ASK, not to build.** Your three levers when it fires:

- `mokata spec amend` — re-gate the scope (the new criteria re-earn completeness and owe their own
  failing tests). An amend in progress blocks development writes until it lands.
- `mokata spec amend --abort` — abandon it; the run returns to its existing spec and writes unblock.
- `mokata gate override spec-scope --reason "<why>"` — take responsibility, on the ledger.

Every undeclared case fails **open**: a spec with no scope section, an unreadable one, or one that
draws no map is never a block.

### What it will *not* do (the honest limits)

- **Test files are always writable.** RED is the *permission* to implement, not the prohibition —
  a gate that blocked the failing test would block the fix.
- **Gates fire only inside an active mokata run** (an approach approved and/or a spec emitted).
  Hand-editing a repo outside a run is never policed: guardrails on the pipeline, not house arrest.
- **Ambiguity fails OPEN.** If two runs have state in one repo and none is pinned (two Claude Code
  windows on one tree), mokata will not guess which run your edits belong to — every run-state gate
  turns **off** for that window, and says so once. Pin one with `MOKATA_SESSION_ID`, or give the
  window its own tree with `mokata worktree create` (see `mokata windows`).
- **A known hole, stated plainly:** the gate-guard does **not** match `Bash`, so a shell edit
  (`sed -i …`) is not policed by it. The *secret*-guard does match `Bash`.
- **Only Claude Code wires it.** It is the one harness that declares the `hooks` capability; on
  Cursor, Gemini, Windsurf, Codex, Aider and Cowork the gate-guard is never wired and **the
  run-state gates enforce nothing** there. `mokata setup claude` wires both hooks by default
  (`--no-hooks` opts out cleanly).

### Overriding a run-state gate

These are *methodology* gates, so they are overridable — under one discipline:

```bash
mokata gate status                                    # read-only: what is enforced / overridden here
mokata gate override spec-scope --reason "hotfix"     # ONE gate, THIS session — re-confirmed + ledgered
mokata gate clear                                     # drop this session's overrides
```

`--reason` is required, the override is re-confirmed interactively, scoped to the session (a new
session re-enables enforcement — nothing to remember to turn off), and appended to the audit ledger.
There is deliberately **no env-var kill switch**, **no MCP tool**, and **no slash command**: an
env var is a side door any process can open silently, and a model-invocable override would let the
model clear the very constraint it is under. The secret-guard has no override at all.

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
alone still refuses to implement without a failing test. And the one door a Contract *couldn't*
close — reaching for the harness's native `Write`/`Edit` with no skill active — is closed by the
[gate-guard hook](#the-gate-guard-the-gates-enforced-on-native-edits) above: the run-state gates
hold at the harness boundary, not just inside mokata's own tools.

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
