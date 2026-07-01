# Performance / latency budget

mokata treats **wall-clock latency as a measured, gated constraint** — the hot paths that run
every turn or session must feel instant, so each has a budget and the test suite asserts it stays
under that budget on a realistic fixture.

> This is **wall-clock latency** — distinct from the **token** budget (`mokata budget`, the F5
> savings readout), which is about tokens, not milliseconds.

## The budget table

Per-operation **wall-clock ceilings** (milliseconds). These are deliberately generous — typical
timings are **sub-millisecond**, so each budget carries ≥100× headroom and asserts a real upper
bound without flaking on a slow runner.

| Operation | Budget | What it is | Runs |
|---|---:|---|---|
| `statusline` | 50 ms | the stage badge (`build_stage_badge`) | every statusline refresh |
| `briefing` | 150 ms | the SessionStart briefing (`build_bootstrap`) | every session start |
| `secret_scan` | 100 ms | the secret scan (`govern/secrets.scan`) | every PreToolUse tool call |
| `grep_query` | 150 ms | the grep-floor structural query | every `mokata query` without a graph |
| `recall` | 100 ms | JIT memory recall (`jit_recall`) | every retrieval |
| `status` | 150 ms | capability resolution (`mokata status`) | on demand |

The every-turn paths (`statusline`, `briefing`, `secret_scan`) are the ones that most need to be
snappy — they're on the critical path of every interaction.

## Run the benchmark

```bash
mokata bench              # measure each hot path vs its budget (read-only)
mokata bench --repeat 15  # more samples (the median is reported)
```

Sample output:

```text
mokata bench — wall-clock latency vs budget (median of 7)
  ✓ statusline   median   0.005 ms  (min 0.005 / max 0.006)  ≤ 50 ms  OK
  ✓ briefing     median   0.137 ms  (min 0.134 / max 0.156)  ≤ 150 ms  OK
  ✓ secret_scan  median   0.286 ms  (min 0.286 / max 0.325)  ≤ 100 ms  OK
  ...
  all hot paths within budget.
```

`mokata bench` is **read-only** and **dependency-free** (it uses `time.perf_counter` and the
stdlib median — no benchmarking library). It exits non-zero only if a path is **over** budget.

## How the assertions stay robust (never flaky)

The perf tests use a **warmup + median-of-N** (the median absorbs the occasional GC/scheduler
spike), **generous ceilings**, and two relax controls so a noisy CI runner never false-fails:

- `MOKATA_PERF_RELAX=<float>` — multiply every budget (e.g. `MOKATA_PERF_RELAX=4`).
- On CI (`CI` env set) the budgets **auto-relax ×4**.
- `MOKATA_PERF_SKIP=1` — skip the timing assertions entirely (the bench helper + behaviour tests
  still run).

## Optimizing a path over budget

If a path ever exceeds its budget, fix it with a small, targeted change that keeps behaviour
**identical** (the hot ops are covered by behaviour-stability regression tests, so an optimization
can't silently change output). At this stage every hot path measured **well under** budget, so
nothing needed optimizing — the rule is *don't micro-optimize what's already fast*.
