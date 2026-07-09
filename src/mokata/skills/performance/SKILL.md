---
name: performance
description: mokata · Performance optimization — measure-first, before/after to the ledger, no guessing.
when_to_use: Engage when an approach touches a hot path, a database query, a cache, or a batch/loop over data, when the spec's `domains` constraint names `performance`, when an acceptance criterion states a latency/throughput/size budget, or when a change is made in the name of speed. Do NOT engage for a correctness-only change with no performance claim or budget, or for a change whose cost you have not first measured — measure before you optimize, do not engage to guess.
---

> **mokata Agent Skill.** This is mokata's `performance` domain knowledge, attached to the pipeline
> so Claude engages it automatically when an approach touches a hot path or a change is made for
> speed. It is NOT a parallel advisory: it enriches optimize/review, feeds the instrument that
> already runs there (measure-first), and records the before/after to memory + the audit ledger.
> mokata's non-negotiables still hold — durable writes are **human-gated**, and no optimization is
> kept without a measurement.

⛭ mokata performance active — gate: no optimization is kept without a before/after measurement proving the win

# mokata · Performance optimization

Performance is a **measured** property, not a felt one. A change that "should be faster" is a
hypothesis until a number confirms it; a change kept on a hunch is as likely to have added
complexity for nothing — or a regression — as to have helped. This skill makes mokata treat
optimization as an experiment: state the metric and its budget, **measure the baseline first**,
make the change, measure again, and record the before/after so the win (or its absence) is on the
ledger and the next change can see it.

## Measure first: a baseline before a change, a number after
Never optimize without a measurement. Take a BEFORE measurement of the metric you intend to move,
make the change, take an AFTER measurement under the same conditions, and keep the change only if
the number improved by a margin that clears the noise. "It should be faster" is not evidence — a
before/after is. This is the discipline the measure-first instrument enforces, and it is why a perf
change in mokata carries a recorded baseline, not just a claim. web.dev states the same principle
for the web — you optimize against measured data, not intuition
(https://web.dev/articles/vitals). The exact tooling and measurement procedure vary by stack;
treat any specific tool's numbers as **UNVERIFIED** until reproduced under your own conditions.

## Core Web Vitals: the user-centric metrics (web.dev)
For user-facing web work, Google's **Core Web Vitals** are the primary, user-centric performance
metrics — each measures a distinct dimension of the loading/interaction experience
(https://web.dev/articles/vitals):
- **LCP — Largest Contentful Paint** measures loading: when the largest content element in the
  viewport is rendered (https://web.dev/articles/lcp).
- **INP — Interaction to Next Paint** measures responsiveness: the latency of a page's interactions
  (https://web.dev/articles/inp).
- **CLS — Cumulative Layout Shift** measures visual stability: unexpected layout movement during
  the page's lifetime (https://web.dev/articles/cls).

web.dev publishes "good / needs-improvement / poor" thresholds for each (commonly cited as
LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1, assessed at the 75th percentile of page loads). The exact
current threshold values and the assessment percentile are **UNVERIFIED** here — confirm them at
the cited web.dev pages before quoting a number as a pass/fail bar, because the definitions are
revised over time.

## Lab vs field: two kinds of measurement, not interchangeable
Distinguish **lab** data (a reproducible measurement in a controlled environment — a synthetic run
you can diff before/after) from **field** data (real-user measurement in the wild, which captures
device/network variance a lab run cannot). Both are legitimate; they answer different questions.
Use a lab measurement for the deterministic before/after of a specific change; use field data to
know whether the change mattered to real users (https://web.dev/articles/vitals). Do not compare a
lab number to a field number and call it a delta — measure the same way on both sides.

## Optimize the hot path, and only after measuring it
Effort spent speeding up code that is not on the hot path buys nothing and adds complexity. Find
where the time actually goes — profile, measure, or query the graph for the hot path — before
choosing what to change, and prefer the change with the largest measured effect. The oft-cited
caution against *premature* optimization is exactly this: optimizing before you have measured where
the cost is spends complexity you may never recover. Treat the specific attributions of that maxim
as **UNVERIFIED** provenance and rely on your own measurement, not the aphorism.

## Attachment to the flow (what mokata does that a doc cannot)
- **brainstorm** — when the chosen approach's graph surface touches a hot path / cache / query, or
  an AC states a latency/throughput/size budget, the domain classifier puts `performance` in the
  spec's `domains`, so the budget is a first-class, human-approved constraint, not an afterthought.
- **optimize** — before changing anything for speed, take the BEFORE measurement of the target
  metric; design the change against where the time is actually spent (the measured hot path).
- **review** — the **Performance** axis activates: check that the change carries a before/after and
  that the measured win clears the noise, so no optimization is kept on a hunch.
- **memory + ledger** — the before/after measurement and the decision (what was optimized, the
  metric, the delta, the conditions) are recorded as a typed `context` entry through the human gate
  and written to the audit ledger, so the win is walkable later (P7) and a future change can see the
  baseline. Record it with mokata's domain-decision path — never as loose prose. The perf budget is
  **advisory** in this release (measurement + ledger, not a hard gate).

## Gate (instrument)
This skill feeds **measure-first** (an SK.S1/S2 instrument, not a hard gate): an optimization is
kept only against a before/after measurement proving the win. It is advisory — it informs and
records the change, it does not block it — but the *durable write* recording the measurement is
human-gated (write-gate), and any change to an approved spec's perf budget/scope routes through the
deviation gate. The perf budget itself ships **advisory** this release.

## Grounding discipline
Decide from the measurement and the metric definition, not from a feeling. Before asserting a change
is faster, a path is hot, or a budget is met, MEASURE it — take the baseline, run the change, take
the after under the same conditions — and for a web metric read the cited web.dev definition for the
version in use and CITE the URL. Prefer the primary source (web.dev / Core Web Vitals) and your own
reproduced numbers over memory or a blog. Flag anything you could not measure or verify as
**UNVERIFIED** rather than stating it as fact.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "This is obviously faster — no need to measure." | "Obviously" is a hypothesis; keep the change only against a before/after that clears the noise (measure-first). |
| "I'll optimize this loop while I'm here." | If it is not on the measured hot path, you are adding complexity for no measured win — profile first, then optimize what matters. |
| "The lab number improved, so real users are faster." | Lab ≠ field; a lab delta is a controlled diff, not proof it reached real users (web.dev lab-vs-field). |
| "I'll record the win later." | The before/after IS the evidence; an optimization without a recorded baseline is a claim, not a result — write it to the ledger. |
| "LCP under 2.5 s is the rule." | The exact thresholds/percentile are revised over time — confirm the current bar at the cited web.dev page before treating a number as pass/fail (UNVERIFIED). |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] a BEFORE measurement of the target metric was taken under stated conditions
- [ ] the change was made, then an AFTER measurement taken the SAME way; the delta clears the noise
- [ ] the metric matches the claim (a web metric grounded in its cited web.dev definition, URL cited)
- [ ] lab and field data were not conflated; any real-user claim is backed by field data
- [ ] the before/after + decision were recorded to memory + the ledger (human-gated), not left as prose

## References — pulled in just-in-time (not loaded inline)

- `references/web-vitals.md` — Core Web Vitals (LCP / INP / CLS), the measure-first discipline, and lab-vs-field measurement in full, each with its primary-source URL

## Contract

**CAN**
- classify the hot-path/budget surface and take a before/after measurement of the target metric
- design the change against the measured hot path, keeping only a win that clears the noise
- record the before/after + decision as a typed `context` entry to memory + the ledger (human-gated)

**MUST NOT**
- persist a performance decision without the write gate (gate: write-gate)
- change an approved spec's perf budget/scope beyond approval (gate: deviation)
- keep an optimization without a before/after measurement, or state an unmeasured speed claim as fact (advisory)

**DEPENDS ON**
- the spec carrying the `performance` domain (classified at brainstorm; amend-in if reached late) (advisory)
- a measurement of the target metric — degrades to a stated-conditions lab run, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
