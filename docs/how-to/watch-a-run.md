# How-to: watch a run (parallel lanes + clickable dashboard)

See — at a glance and in depth — **what mokata is doing right now**, especially when it runs
subagents in parallel. Two tiers, and you choose which:

```bash
mokata config set settings.ux.progress terminal     # default — terminal only
mokata config set settings.ux.progress dashboard     # add the HTML dashboard
mokata config set settings.ux.progress both          # both
```

Both tiers are **read-only**: they only reflect run-state + the audit ledger. They never write
durable state, never gate, and never mutate a run — and nothing leaves your machine.

## Tier 1 — parallel-aware terminal lanes (always on)

```bash
mokata progress --lanes
```

One line per concurrent lane with its state, under the `[done/total]` phase header:

```text
mokata · run [3/7 done] · develop
  lanes (3 concurrent):
  ✓ auth-task            done
  ▶ billing-task         running
  ✗ search-task          blocked  (review failed)
```

A sequential run renders as a single lane (the familiar feel). With no active run it prints a
friendly message; with no audit ledger it shows a single lane — it degrades, never errors.
`--ascii` swaps the glyphs for `[x]/[>]/[!]/[~]`.

## Tier 2 — the clickable local HTML dashboard

```bash
mokata watch --once          # write a single snapshot
mokata watch --open          # write + open it in your browser, then live-refresh
mokata watch                 # live: rewrites the file every 2s (Ctrl-C to stop)
```

`mokata watch` writes a **self-contained** HTML file (inline CSS, no external assets, no
network, no server — clickable via native `<details>`) to gitignored
`.mokata/temp_local/watch.html`. It shows:

- the **parallel lanes** — click a lane card to drill into its ledger rows;
- the **7-phase pipeline** (done / current / pending);
- a **bounded tail** of the **gate & decision feed** from the audit ledger.

It's **frugal** — only the active run's state and a bounded ledger tail (never the full history),
and it costs the model no tokens (it's a human-facing file, not injected context). It **degrades
clean**: no run → a friendly empty state; no ledger → lanes only.

> `mokata watch` respects `settings.ux.progress`: with the default `terminal` it writes **no**
> HTML (and tells you how to enable the dashboard). The HTML lives in gitignored `temp_local/`,
> so it is never committed or auto-shared — it may contain run details, and it's yours alone.

See the [pipeline concept](../concepts/pipeline.md#run-progress-tracker).
