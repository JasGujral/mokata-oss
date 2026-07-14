# Tutorial: run a story end-to-end

A guided, learn-by-doing walkthrough of one story through the whole pipeline. By the end
you'll have seen the HARD-GATE, the completeness gate blocking and passing, RED-before-GREEN,
the two-stage review, the run-state gates on the agent's own file writes, the human-minted
approval, and the audit trail.

> This tutorial drives the **CLI** so each gate is visible step by step. For real day-to-day
> work the primary path is Claude Code — [`mokata setup claude`](../how-to/use-without-plugin.md)
> (see [Getting started](../getting-started.md)) — where Claude runs these same phases for you.
> The CLI here is the engine's mechanics, shown to make the flow concrete.

## 1. Set up

```bash
pip install mokata               # from PyPI, no clone; MCP server included on Python ≥ 3.10
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
persisted to `.mokata/temp_local/state/approved_approach__<run>.json` (run-scoped, so two
windows on one repo never clobber each other). Check it:

```bash
mokata brainstorm --status
```

From this moment the run is **active**, and inside Claude Code the run-state gates start
enforcing on the agent's *native* `Write`/`Edit` too — see step 6.

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

## 6. See the seatbelt that isn't in the pipeline

Everything above is mokata's *own* engine gating its *own* tools. Two guarantees sit outside it,
and they're the ones you'll actually feel in Claude Code:

```bash
mokata gate status   # the run-state gates enforcing on the agent's NATIVE Write/Edit
mokata approve       # every durable write waiting for a human-minted approval
```

- **The run-state gates** (`spec-persisted`, `no-code-without-failing-test`, `spec-scope`) are
  enforced by a `PreToolUse` hook, so the agent can't skip mokata's tools and just edit the file:
  the write is refused with `BLOCKED [<gate>] <reason>` and exit code 2. Test files are always
  writable, and they only fire inside an active run. It's a *methodology* block, so you can lift
  one on the record: `mokata gate override <gate> --reason "<why>"` (session-scoped, ledgered),
  and `mokata gate clear` to enforce again.
- **Approvals are minted by you, not the model.** A write tool hands back a `proposal_id` and
  writes nothing; you run `mokata approve <id>` in your own terminal; the model then re-calls the
  same tool with that id and it commits **once**. `approve=true` on a tool call commits nothing —
  it never was consent. (A secret is still hard-blocked even in an approved write.)

## 7. Inspect everything

```bash
mokata audit         # every gate decision, approval, override + tool call, in order
mokata budget        # token savings recorded this run
mokata memory        # any decisions captured + pending self-healing proposals
```

## 8. Enter mid-pipeline (advanced)

You don't have to run the whole thing. To run only the completeness gate against a
hand-written spec, or to start at the strawman:

```bash
mokata enter completeness_gate
mokata enter strawman --to probes
```

Only the run phases' gates apply; skipped upstream phases are reported explicitly.

Next: the [how-to guides](../how-to/configure-a-profile.md) and the
[concepts](../concepts/pipeline.md).
