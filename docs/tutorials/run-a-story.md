# Tutorial: run a story end-to-end

A guided, learn-by-doing walkthrough of one story through the whole pipeline. By the end
you'll have seen the HARD-GATE, the completeness gate blocking and passing, RED-before-GREEN,
the two-stage review, and the audit trail.

## 1. Set up

```bash
pip install -e ".[schema]"
mkdir demo && cd demo
mokata init --profile standard --yes
mokata status
```

`status` shows the live stack — on `standard`, `code_graph` resolves to `grep` (the floor)
and `memory_store` to `sqlite` unless richer tools are installed.

## 2. See the plan before doing anything (dry-run)

```bash
mokata preview
```

This lists all 7 phases, the gate at each, and the files each would touch — with **no side
effects**. Note that `completeness_gate` and `emit` are where things can block.

## 3. Brainstorm and approve an approach (HARD-GATE)

```bash
mokata brainstorm
```

The brainstorm protocol drives a one-question-at-a-time exploration and **refuses to let a
spec proceed until you explicitly approve one of 2–3 approaches**. The approved approach is
persisted to `.mokata/state/approved_approach.json`. Check it:

```bash
mokata brainstorm --status
```

## 4. Drive the whole pipeline

```bash
mokata playbook
```

The playbook runs the real flow and prints PASS/FAIL per checkpoint:

```
brainstorm_approved … gate_blocked_initially … gate_passed_after_tests …
red_before_green … review_passed … memory_written … RESULT: PASS
```

What happened under the hood:

- **completeness gate** first **blocked** emit (no tests mapped), then **passed** once every
  acceptance criterion mapped to a test — this is the provable-completeness guarantee.
- **RED-before-GREEN** was enforced: implementing a test that hadn't failed first is blocked.
- the **two-stage review** ran; on `standard`/`full`, memory recorded the decision.

## 5. Try the parallel path

```bash
mokata playbook --parallel
```

Without a subagent harness this **degrades to the sequential flow** (and says so) — never a
crash. With a harness it isolates each task's context and runs the two-stage review.

## 6. Inspect everything

```bash
mokata audit         # every gate decision + tool call, in order
mokata budget        # token savings recorded this run
mokata memory        # any decisions captured + pending self-healing proposals
```

## 7. Enter mid-pipeline (advanced)

You don't have to run the whole thing. To run only the completeness gate against a
hand-written spec, or to start at the strawman:

```bash
mokata enter completeness_gate
mokata enter strawman --to probes
```

Only the run phases' gates apply; skipped upstream phases are reported explicitly.

Next: the [how-to guides](../how-to/configure-a-profile.md) and the
[concepts](../concepts/pipeline.md).
