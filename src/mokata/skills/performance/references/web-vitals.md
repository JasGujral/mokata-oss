# Performance optimization — primary sources (JIT detail)

Pulled in just-in-time when the Performance skill is engaged and the heavier detail is needed.
Authored clean-room in mokata's own words; every external claim is anchored to a web.dev / Core
Web Vitals primary source below. Where a specific threshold, percentile, or tooling behaviour could
not be verified against the live source at authoring time, it is marked **UNVERIFIED** and must be
confirmed at the cited URL for the version in use before it is relied on.

## The core principle — measure, don't guess

Source: https://web.dev/articles/vitals

Performance is optimized against measured data, not intuition. A change intended to improve speed is
a hypothesis until a before/after measurement — taken under the same conditions on both sides —
confirms the metric moved by a margin that clears measurement noise. This is the mokata
measure-first discipline: a baseline BEFORE the change, a number AFTER, and the change kept only if
the number improved.

## Core Web Vitals — the user-centric metrics

Source: https://web.dev/articles/vitals

Google's Core Web Vitals are a small set of user-centric metrics, each measuring a distinct
dimension of the page experience:

### LCP — Largest Contentful Paint (loading)
When the largest content element in the viewport is rendered. Measures perceived load speed.
Source: https://web.dev/articles/lcp

### INP — Interaction to Next Paint (responsiveness)
The latency of a page's interactions across its lifetime — how quickly the page responds to user
input. Measures responsiveness.
Source: https://web.dev/articles/inp

### CLS — Cumulative Layout Shift (visual stability)
The amount of unexpected layout movement of visible content during the page's lifetime. Measures
visual stability.
Source: https://web.dev/articles/cls

### Thresholds
web.dev publishes "good / needs-improvement / poor" bands for each metric, commonly cited as
LCP ≤ 2.5 s, INP ≤ 200 ms, and CLS ≤ 0.1, assessed at the 75th percentile of page loads. The exact
current threshold values and the assessment percentile are **UNVERIFIED** here — the definitions
are revised over time, so confirm the current numbers at the cited metric pages before quoting a
value as a pass/fail bar.

## Lab vs field data — two different measurements

Source: https://web.dev/articles/vitals

- **Lab data** — a measurement in a controlled, reproducible environment (a synthetic run). Good
  for the deterministic before/after of a specific change, because you can hold conditions constant
  and diff the two runs.
- **Field data** — real-user measurement in the wild, capturing the device, network, and usage
  variance a lab run cannot. Good for knowing whether a change actually mattered to real users.

They answer different questions and are not interchangeable: do not compare a lab number on one side
to a field number on the other and call the difference a delta. Measure the same way on both sides
of a change.

## Premature optimization (context, applied with care)

The widely-quoted caution that premature optimization is the root of much wasted effort is used in
mokata narrowly: do not spend complexity optimizing code before you have measured that it is on the
hot path. Find where the time goes first (profile / measure / query the graph), then optimize the
change with the largest measured effect. The precise wording and attribution of that maxim are
**UNVERIFIED** provenance here — rely on your own measurement, not the aphorism.

## How this becomes a recorded result in mokata

A performance change carries a before/after measurement plus a decision (what was optimized, the
metric, the delta, the conditions). mokata records these as a typed `context` memory item through
the human-gated write path and writes the decision to the audit ledger under the `domain` kind, so
the win (or its absence) is walkable later and the next change can see the baseline. The perf budget
is **advisory** in this release — measurement + ledger, not a hard gate that blocks a change.
