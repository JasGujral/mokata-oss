# Concept: token governance

mokata measures and reduces context spend in-loop. Everything builds on one token view
(the `TokenTracker`); there is no parallel token machinery.

## Token / cost tracker (F1)

A conservative, dependency-free estimator (~4 chars/token). `TokenTracker.add(...)`
accumulates input/output tokens per call; `cost()` applies per-1k rates (illustrative,
configurable); `report()` prints the totals. This is for in-loop governance — "are we
spending more than the work is worth" — not billing.

## JIT graph-backed retrieval (F2)

Instead of dumping whole files into context, `jit_retrieve(layer, identifiers)` pulls the
relevant **reference snippets** for a set of identifiers via the knowledge layer and
reports the reduction vs the file-dump baseline (`tokens_retrieved` vs
`tokens_if_dumped`, `saved`, `saved_pct`). On a small sample this is routinely a 60%+
reduction.

## Sub-agent handback cap (F3)

Heavy sub-agent work returns a compact **summary**, not raw context. `cap_summary(text,
cap_tokens)` bounds the handback to a token cap; wired into the parallel execution path via
`run_tasks(..., handback_cap=N)` so a parent context never absorbs a sub-agent's full
working set.

## Output-density mode (F4)

An **optional**, toggleable terse-output compressor (`settings.governance.output_density`,
default off): collapses blank-line runs and duplicate lines, squeezes whitespace, and can
cap noisy tool output. Default behavior is unchanged unless enabled.

## Savings budget + statusline (F5)

`SavingsTracker.record(label, baseline, actual)` logs each governance win to the audit
ledger; `mokata budget` aggregates them into a report + a one-line statusline
(`mokata · saved N tok (P%)`).

```bash
mokata budget          # live savings readout (from the ledger) + statusline
```

## Prompt-cache awareness (F6)

`stable_prefix_for(surface)` composes a deterministic prefix from slow-changing sources
(manifest identity + always-on rules + constitution), excluding volatile per-run content,
so the prefix stays byte-identical across runs and keeps hitting a prompt cache.
`is_cache_stable(a, b)` verifies stability by fingerprint.
