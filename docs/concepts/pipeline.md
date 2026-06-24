# Concept: the 7-phase pipeline & gates

mokata's engine is a 7-phase pipeline. Each phase consumes the prior phase's handoff, and
several phases carry **gates** — checks that must hold before the run proceeds.

```
brainstorm → analysis → strawman → pre_mortem → probes → completeness_gate → emit
```

These are the canonical `PIPELINE_PHASES`. You can run the whole thing
(`mokata playbook`) or enter at any phase (`mokata enter <phase>`).

## The phases

1. **brainstorm** — Socratic, *one question at a time*; surfaces 2–3 real approaches with
   tradeoffs; **HARD-GATE**: no spec until exactly one approach is explicitly approved.
   The approved approach is persisted to `.mokata/state/approved_approach.json` and becomes
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
