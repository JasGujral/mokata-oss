---
name: frontend-perf
description: mokata · Frontend performance for React web — measure, identify, fix, verify (keep or revert), guard; audit first, fix on request.
when_to_use: Engage when mokata's per-turn routing or `spec show` names `frontend-perf` for this run, when the spec's `domains` carry both `frontend-react` and `performance`, when an acceptance criterion states a bundle-size, render-count or load-time budget for a React web page, or when a React web change is made in the name of speed. Do NOT engage for a backend latency problem, for a React Native app, or to optimize something nobody has measured yet — measure first, do not engage to guess.
---

> **mokata Agent Skill.** This is mokata's `frontend-perf` domain knowledge. mokata LOADS it when
> the approved spec routes both a React web surface and a performance surface — never on a prompt
> keyword. It specialises the `performance` skill for the browser and feeds the same measure-first
> instrument. Durable writes are **human-gated**; no change is kept without a before/after number.

⛭ mokata frontend-perf active — gate: no optimization is kept without a before/after measurement proving the win

# mokata · Frontend performance (React web)

**Audit first, fix on request.** The default output is a measured finding, not a code change. Change
code only when the spec asks for the improvement (an AC with a budget) or the human says so.

## The loop — every step leaves evidence

1. **Measure** — pick ONE metric that matches the complaint and record it under stated conditions:
   bytes of the built output per entry (gzip and raw), render count or render duration of the
   component under a scripted interaction, or a lab load metric (LCP, CLS, TBT). Record the tool,
   the build mode (production!), the device/network profile and the run count.
2. **Identify** — find the cause in the measurement, not in your intuition: the dependency that
   owns the bytes (diff two builds), the component that renders most (a profiler run or a
   render-count test), the long task on the main thread.
3. **Fix** — the smallest change aimed at the identified cause.
4. **Verify — keep or revert** — re-measure under the same conditions, several runs. Keep the change
   only if the improvement exceeds the run-to-run noise; otherwise revert it and say so.
5. **Guard** — leave something that fails if it regresses: a byte budget the project's checker
   enforces, a render-count assertion in a component test, or a recorded baseline.

Report every kept change in a before/after box:

```
metric:      initial JS for /checkout (gzip)      conditions: prod build, same commit base
before:      212.4 kB  (3 runs, ±0.0)              after: 168.9 kB (3 runs, ±0.0)
change:      −43.5 kB (−20.5%)                     kept: yes — exceeds noise; budget guard added
```

## What a lab can and cannot tell you
- **Interaction latency in the field (INP) is a field metric** — CI cannot measure it; TBT and a
  scripted interaction's long tasks are lab proxies. Say which one you measured.
- **Profiling a development build measures the wrong thing** — dev React is much slower and
  differently shaped. Measure a production build; a profiling build only when you need component
  timings. Live in-browser profiling is a human DevTools activity; mokata prepares for it and records
  the result, it does not perform it.
- **A re-render is a symptom, not a verdict** — it matters when it is measured as slow. With the
  React Compiler on, check whether the component compiled before adding memoization
  (`frontend-react` → `compiler-hunt-bailouts`).
Sources: https://web.dev/articles/vitals · https://web.dev/articles/inp · https://react.dev/reference/react/Profiler .
Specific thresholds (what counts as a "good" LCP or INP) are **UNVERIFIED** here — quote them only
after reading the cited page.

## Red flags — stop and re-measure
- a win reported from one run, or from a development build
- "it feels faster" with no number
- a new dependency added to make the page faster, with no byte measurement of the dependency itself
- memoization added everywhere in one change

## If this appears stuck

This guidance runs inside a pipeline phase. If that phase appears stuck, or the harness stops it mid-run: run `mokata resume` (where the run stands and the gate that still applies) and `mokata approve --list` (anything waiting on a human). Nothing is lost — run state, approvals and the audit ledger are on disk — so re-invoke the phase to continue; `mokata doctor` names a hung MCP server or a hook that is not firing.

## Rationalizations — stop if you catch yourself thinking any of these

| Rationalization | Reality |
|---|---|
| "This is obviously the slow part." | Obvious and measured disagree often. Measure, then identify from the numbers. |
| "One run is enough, the difference is big." | Builds and browsers are noisy; a single run cannot separate a win from variance. |
| "I'll keep it, it can't hurt." | A change without a measured win is complexity with a cost. Revert it and record why. |
| "Dev mode shows the same picture." | Development React adds checks that dominate timings. Measure production. |
| "Lighthouse in CI tells us the real INP." | INP is field-only. CI gives a lab proxy; label it as one. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] one named metric, measured before the change under stated conditions (prod build, run count)
- [ ] the cause was identified from the measurement, and the fix aims at it
- [ ] the after-measurement used the same conditions; the change was kept only above the noise
- [ ] a guard (budget, render-count test, or recorded baseline) now fails on regression
- [ ] the before/after box was recorded to memory + the ledger through the write gate

## References — pulled in just-in-time (not loaded inline)

- `references/measure-identify-fix-verify.md` — the loop in detail: choosing the metric, stating conditions, noise and run counts, and the guard options
- `references/bundle-and-render-evidence.md` — how to attribute bytes to a dependency and renders to a component without bundler-specific tooling

## Contract

**CAN**
- measure one metric under stated conditions and identify the cause from the numbers
- make the smallest fix on request, then keep it only when the after-measurement clears the noise
- record the before/after box as a typed `context` entry to memory + the ledger (human-gated)

**MUST NOT**
- persist a performance decision without the write gate (gate: write-gate)
- change an approved spec's budget or scope beyond approval (gate: deviation)
- keep a change without a before/after measurement, or call a lab proxy a field metric (advisory)

**DEPENDS ON**
- the spec carrying `frontend-perf` (routed when `frontend-react` and `performance` are both in play) (advisory)
- a production build and a measuring tool the project has — degrades to stating what could not be measured (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
