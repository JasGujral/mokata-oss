# Concept: the 7-phase pipeline & gates

mokata's engine is a 7-phase pipeline. Each phase consumes the prior phase's handoff, and
several phases carry **gates** — checks that must hold before the run proceeds.

```
brainstorm → analysis → strawman → pre_mortem → probes → completeness_gate → emit
```

These are the canonical `PIPELINE_PHASES`. You can run the whole thing
(`mokata playbook`) or enter at any phase (`mokata enter <phase>`).

**Front-ends.** Two phases sit *in front of* the pipeline and hand a gated direction into
it: `brainstorm` (for a *new* problem) and `refine` (for *existing* code — review → approve
a scoped set → hand off to `spec`). Both HARD-GATE the spec; the completeness gate reads
whichever ran. See [refine existing code](../how-to/refine-existing-code.md).

**mokata engages brainstorm when you're exploring.** You don't have to remember to type
`/mokata:brainstorm` — the skill is *model-invocable*, so Claude Code can auto-activate it
when you're weighing options or describing a new problem before any code. You'll know it
stepped in by the banner `mokata · brainstorm (engaged)`. It's proactive, **not** intrusive:
it only *starts* the conversation — the HARD-GATE still holds (no spec/code until you approve
an approach), and it won't hijack a direct command or mid-implementation work. Turn it off or
make it ask first with `settings.brainstorm.auto` (`on` | `off` | `ask`, default `on`):

```bash
mokata config set settings.brainstorm.auto off    # never auto-engage
mokata config set settings.brainstorm.auto ask     # offer first, don't dive in
```

## The phases

1. **brainstorm** — Socratic, *one question at a time*; surfaces 2–3 real approaches with
   tradeoffs; **HARD-GATE**: no spec until exactly one approach is explicitly approved.
   The approved approach is persisted to `.mokata/temp_local/state/approved_approach.json` and becomes
   a downstream constraint.
2. **analysis** — grounds the approved approach in the codebase (structural facts from the
   knowledge layer) and the answered questions; produces components/notes.
3. **strawman** — a first-cut design mapping the approach to each acceptance criterion.
4. **pre_mortem** — derives adversarial *risk probes* from the approved approach (each
   declared downside becomes a probe, plus standard failure/scale/rollback angles).
5. **probes** — checks the spec addresses each probe.
6. **completeness_gate** — the **provable-completeness blocker**: `emit` is refused until
   every acceptance criterion maps to a test (RED-before-GREEN traceability). It reads the
   brainstorm handoff so the approved approach is in view.
7. **emit** — produces durable output; the write is **human-gated**.

## Gates

Three phases carry a gate (the rest are advisory):

| Phase | Gate id | Kind | Blocks on |
|---|---|---|---|
| brainstorm | `approach-approval` | human | no approved approach |
| completeness_gate | `completeness` | check | any acceptance criterion with no mapped test (or an empty spec) |
| emit | `emit-approval` | human | un-approved durable output |

The completeness gate **never silently passes**: an empty spec and any unmapped AC both
block. See [AC traceability](knowledge.md) and the [governance model](governance.md).

**Spec before code, enforced.** Implementation entry points (`/mokata:develop`,
`/mokata:test`, and `mokata run develop`/`test`) carry a **`spec-persisted`** precondition
that fires *ahead of* the test gate: it blocks unless a saved spec with ≥1 acceptance
criterion exists (`emitted_spec.json`, written by the human-gated `emit` only after the
completeness gate passes). Jump straight to `develop` with no saved spec and mokata stops
with a clear next step — *"no saved spec — draft and emit it first (`/mokata:spec`)"* — and
logs the decision. So "spec written **and** saved before implementation" is enforced, not
merely implied.

**Don't break a saved spec by mistake.** As part of grounding, `spec`/`refine`/`develop` run a
**spec-awareness** check (Stage 37): a change is cross-checked against your **saved specs** and
**recorded decisions**, and if it would affect one, mokata surfaces it and routes it through the
deviation gate — confirm (amend/supersede) or re-plan, never a silent break. It's frugal (only
the touch-set, graph-expanded) and degrades cleanly (no corpus ⇒ no-op; no graph ⇒ a
lexical/file check that says so). See [governance](governance.md) and `mokata spec-check`.

**Start green, finish verified.** Before implementing, confirm a clean baseline —
`mokata baseline` reports the test suite green/red so a *new* failure is attributable to your
change (it degrades cleanly when no test command is configured — mokata never guesses one).
And the flow now **ends with `/mokata:ship`**: it verifies the work is *actually* done
(evidence over claims — green tests + every AC met + review passed; otherwise it blocks with
what's missing), summarizes what shipped, and **lets you choose how to land it** — merge, open
a PR, keep the branch, or discard. mokata may prepare a commit/branch or a PR description, but
runs a git action **only on your explicit confirmation**; it never merges, PRs, or deletes on
its own, and the finish decision is recorded in the audit ledger.

## Run-progress tracker

A multi-phase run is legible, not opaque: mokata shows a **read-only** progress tracker
derived from the persisted run-state (`pipeline_run__<id>` checkpoints) — so it can't drift
from what the engine actually did. Each phase is marked **done / current / pending** with a
`[done/total]` count and what's next:

```text
mokata · run  [3/7 done]
  ✓ brainstorm        approach-approval passed
  ✓ analysis
  ✓ strawman
  ▶ pre_mortem        ← you are here
  ○ probes
  ○ completeness_gate
  ○ emit
next: probes     ·     pending: 4/7
```

Ask for it anytime with **`mokata progress`** (or the `progress` MCP tool — `--ascii` for
plain glyphs). The pipeline skills also print it at the start and end of each phase, plus a
one-line banner — `mokata · develop (running)` → `mokata · develop (done)` — so you always
know **which** part of mokata is running. With no active run it says so cleanly (never an
error). It's local and read-only — pure surfacing, no telemetry.

### Parallel-aware lanes + the clickable dashboard (Stage 40)

When mokata runs subagents in parallel, the progress view becomes **parallel-aware**.
`mokata progress --lanes` shows one line per concurrent lane with its state
(`running`/`done`/`blocked`/`degraded`), under the `[done/total]` phase header; a sequential
run renders as a single lane (the familiar feel). It's **derived** from the run-state plus the
execmode records the orchestrator already writes to the audit ledger — nothing new is persisted.

For a richer view, **`mokata watch`** writes a **self-contained, clickable local HTML
dashboard** (no external assets, no network, no server — pure stdlib) under gitignored
`.mokata/temp_local/`: the parallel lanes (click a lane to drill into its ledger rows), the
7-phase pipeline, and a **bounded tail** of the gate/decision feed. Choose your tier with
`mokata config set settings.ux.progress {terminal|dashboard|both}` (default `terminal`).

Both tiers are **read-only and non-authoritative** — they only *reflect* run-state + the ledger;
they never write durable state, never gate, never mutate a run. The dashboard is **frugal**
(only the active run + a bounded ledger tail, no model-token cost — it's a human-facing file) and
**degrade-clean** (no run → a friendly empty state; ledger absent → lanes only). See
[watch a run](../how-to/watch-a-run.md).

## Mid-pipeline entry (L2)

`mokata enter <phase> [--to <phase>]` runs a slice. The gates of the phases you run still
apply; upstream phases are not forced, and the skip is reported explicitly (never silent).

```bash
mokata enter completeness_gate        # run just the gate on a hand-written spec
mokata enter strawman --to probes     # run a slice
```

## Dry-run preview (E7)

`mokata preview` lists the planned actions, the gate at each phase, and the files each
phase *would* touch — with **zero side effects** (no writes, no ledger entries).

```bash
mokata preview                 # whole pipeline
mokata preview --start pre_mortem --to completeness_gate
```

## Worked example

```bash
mokata init --profile standard
mokata brainstorm                 # approve one approach (HARD-GATE)
mokata preview                    # see what will run + what it touches
mokata playbook                   # brainstorm → … → completeness gate → emit
mokata audit                      # every gate decision + tool call, in order
```

If a criterion has no test, the completeness gate blocks `emit` and the audit ledger
records the block — fix the mapping (write the test) and re-run.
