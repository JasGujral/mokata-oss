# How-to: refine existing code

`refine` is mokata's front-end for code you **already have**. Point it at a file or
component; it reviews the code in depth, proposes concrete refinements, and — once you
approve a scoped set — hands off to the **existing** `spec → test → develop → review` flow.
It's the counterpart to `brainstorm` (which is for *new* problems).

> `refine` reviews *your code* and proposes changes; [`review`](../reference/skills.md)
> verifies a *diff against its spec*. Different ends of the pipeline.

## Run it

```bash
# inside Claude Code (plugin): /mokata:refine focus auth + security
# CLI (engine view):
mokata run refine            # shows the protocol + what it can ground in right now
mokata enter refine          # enter the pipeline at the refine front-end
```

Pass free-form **scope** guidance to include / exclude / focus — e.g. *"focus on the auth
module and security"*, *"exclude performance"*, *"only the public API surface"*. With no
guidance it does the full in-depth review and tells you up front what's in and out of scope.

## What it does

1. **Grounds** in the real code — uses the codebase graph (callers / callees / imports /
   blast radius) and memory (prior decisions), reading **only** the target. It pulls related
   context through the graph + memory rather than pasting the repo (frugal by design).
2. **Reviews deeply** across all dimensions by default — architecture & boundaries, design
   patterns & anti-patterns, CS best practices, quality, testability, coupling & cohesion,
   error handling, **security**, and **performance** — honoring your scope.
3. **Proposes a prioritized list** — each refinement with its rationale, the principle it
   serves, the tradeoff, and whether it's **behavior-preserving** or **behavior-changing**.
4. **Offers 2–3 coherent directions** where they genuinely differ, so you choose *scope*,
   not just yes/no.

## The hard gate, then the existing flow

`refine` will **not** produce a spec until you **explicitly approve a scoped set** of
refinements. The approved set is persisted (`approved_refinements`) and read by the
completeness gate — then the unchanged pipeline runs:

```text
refine  → approve a scoped set        (HARD-GATE)
spec    → acceptance criteria, incl. "behavior preserved" criteria
test    → RED: characterization tests pin current behavior BEFORE any change
develop → GREEN: the minimum change to pass
review  → spec-compliance, then quality
```

`refine` **doesn't write the spec itself** — it hands the approved plan to the `spec` skill
(maximum reuse). **Behavior-preserving by default:** structural/refactor changes are pinned
by characterization tests written *before* the change, so behavior can't silently drift.

See [the pipeline](../concepts/pipeline.md) and [the skills catalog](../reference/skills.md).
