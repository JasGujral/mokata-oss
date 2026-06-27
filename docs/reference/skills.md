# Reference: skills catalog

Run `mokata skills` for the live catalog (progressive disclosure — `mokata skills <name>`
reveals the full prompt + gate). Every skill runs standalone (`mokata run <name>`) with no
full-pipeline prerequisite and applies **only its own gate**. The shipped `/<name>` slash
commands under `templates/commands/` are generated from this same registry, so the command
and the CLI never drift.

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
