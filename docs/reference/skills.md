# Reference: skills catalog

Run `mokata skills` for the live catalog (progressive disclosure — `mokata skills <name>`
reveals the full prompt + gate). Every skill runs standalone (`mokata run <name>`) with no
full-pipeline prerequisite and applies **only its own gate**. The **37** shipped `/<name>` slash
commands under `templates/commands/` are generated from this same registry, so the command
and the CLI never drift.

**The count: 26 skills** — the **16** curated skills below plus **10** domain-knowledge skills.
That is what `mokata setup claude` writes into `.claude/skills/`, and what
[`mokata doctor`](cli.md#mokata-doctor-matrix) checks for when it tells you whether your skills
are visible in this root.

## The skills

| Skill | Gate id | Kind | What it does |
|---|---|---|---|
| `brainstorm` | `approach-approval` | human | Socratic pre-spec exploration (for *new* problems); HARD-GATE: no spec until one approach is explicitly approved |
| `refine` | `refinement-approval` | human | deep, user-steerable review of *existing* code → prioritized refinements; HARD-GATE: no spec until a scoped set is approved, then hands off to `spec` |
| `onboard` | `typed-capture-human-gated` | human | guided capture of the project's rules/guardrails/conventions/context/docs into TYPED, human-gated memory the skills reference |
| `spec` | `completeness` | human | turn the problem into testable acceptance criteria, each mapped to a test |
| `test` | `red-before-green` | check | write failing tests first (RED); no implementation here |
| `develop` | `no-code-without-failing-test` | check | implement the minimum to turn a failing test green |
| `review` | `spec-then-quality` | human | two-pass review — against the spec, then quality |
| `debug` | `repro-first` | check | reproduce first, find the root cause (N-strikes escalation), then fix |
| `optimize` | `measure-first` | check | measure before/after; keep only proven, behavior-preserving wins |
| `bug` | `reproducer-required` | check | start from a reproducer + failing test, then fix; labels reported→reproduced→fixing→verified |
| `ship` | `finish-is-human-landed` | human | verify it's truly done (green tests + met ACs + a passed review), then YOU choose how to land it — mokata never merges/PRs/deletes without explicit confirmation |
| `version` | `version-display` | check | show the installed version + how to update (offline; the update check is opt-in, the upgrade human-gated) |

## The full curated catalog (16 skills)

The table above lists the **runnable registry skills** (`mokata run <name>`). The full curated
catalog Claude can auto-engage is **16 skills**: the pipeline skills above (minus `version`,
which is a CLI utility) plus five standalone/auto-firing skills:

| Skill | What it does |
|---|---|
| `govern` | the governance surface — budgets, trust dials, ledger views |
| `session` | portable, secret-scanned, human-gated session push/pull |
| `playbook` | dense orchestration of a full pipeline run |
| `docsync` | docs↔code reconciler — audits every claim against the code and offers **human-gated** fixes; auto-fires when a change touches a documented symbol |
| `mcp-repair` | auto-engages when the mokata MCP server/tools aren't connecting |

Since 0.0.12 every skill carries a `## Contract` — what it CAN do, what it MUST NOT, and the
real gate backing each boundary — plus an active-skill banner shared with the statusline and
`mokata progress`. There are also **10 domain-knowledge skills** that attach to pipeline phases;
see [Domain skills](../how-it-works/domain-skills.md).

## `refine` vs `review`

These sound similar but sit at opposite ends of the pipeline:

- **`refine`** = *review my existing code and propose changes.* It's a **front-end** (like
  `brainstorm`, but for code you already have): deep review → prioritized refinements →
  approve a scoped set → hand off to `spec`. It has no spec to check against yet.
- **`review`** = *verify a diff against its spec.* It's the **back-end** check after
  `develop`: does the change do exactly what the spec said (no more), then quality.

See [how-to: refine existing code](../how-to/refine-existing-code.md).

## Gate kinds

- **human** — requires explicit approval (it surfaces, you decide).
- **check** — a verifiable condition (e.g. a failing test must exist before implementation).

## The run-state gates (enforced beyond the skills)

Four gates don't only live inside a skill: the **`gate-guard` PreToolUse hook** enforces them on
Claude Code's *native* `Write`/`Edit`/`MultiEdit`/`NotebookEdit` too, blocking with **exit code 2**
so they hold even when no mokata skill is driving.

| Gate | Blocks an implementation write when… |
|---|---|
| `approach-approval` | the run is registered but still in brainstorm — no approach approved, no spec emitted |
| `spec-persisted` | an approach is approved for this run but no spec is emitted |
| `no-code-without-failing-test` | the spec is emitted but no failing test is on record |
| `spec-scope` | the write is outside the spec's authorized surface, spells a **deferred** item's marker, or a `spec amend` is in progress |

Test files are always writable, the gates fire only inside an active run, and an ambiguous run
(two windows, none pinned) fails **open**. They are methodology gates, so they are overridable —
`mokata gate status | override <gate> --reason "<why>" | clear`: session-scoped, re-confirmed and
ledgered, with deliberately no MCP tool and no slash command. The `secret-guard` hook is a
*security* block and has no override. Full explainer:
[the gate-guard](../how-it-works/skills-and-gates.md#the-gate-guard-the-gates-enforced-on-native-edits).

## Invocation

```bash
mokata skills                 # list (names + one-line summaries)
mokata skills test            # reveal test's full prompt + gate
mokata run review             # run a skill standalone
mokata chain spec test        # manual chain — each step keeps its gate
```

## Pipeline phases vs. skills

The 7 pipeline phases (`brainstorm`, `analysis`, `strawman`, `pre_mortem`, `probes`,
`completeness_gate`, `emit`) carry their own gates (`approach-approval`, `completeness`,
`emit-approval`) and are entered with `mokata enter <phase>`. Skills are the standalone
command surface; the two compose (see [the pipeline](../concepts/pipeline.md)).

## Authoring a skill (G6)

Skills are authored test-first (RED-GREEN-REFACTOR-for-docs): declare doc requirements,
watch them fail, write the content until they pass, then promote to a registry `Skill`.
See [how-to: write a skill](../how-to/write-a-skill.md).
