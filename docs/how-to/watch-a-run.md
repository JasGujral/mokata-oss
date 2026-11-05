# How-to: watch a run (terminal lanes + the IDE's Sessions tab)

See — at a glance and in depth — **what mokata is doing right now**, especially when it runs
subagents in parallel. Two places, and you choose which `mokata watch` uses:

```bash
mokata config set settings.ux.progress terminal     # default — the terminal lanes
mokata config set settings.ux.progress ide          # the IDE's Sessions tab
mokata config set settings.ux.progress both         # both
```

`mokata watch --open` opens the IDE on whichever tier you are on. Any other value is refused —
including `dashboard` from earlier releases: `mokata watch` stops and names
`mokata config set settings.ux.progress ide` as the fix. Both are **read-only**: they
only reflect run-state, the sessions and the audit ledger. They never write durable state, never
gate, and never mutate a run — and nothing leaves your machine.

## `mokata progress` — the enriched, at-a-glance view

`mokata progress` (or **`/progress`** inside Claude Code) is the max-detail read of the
active run. Alongside the 7-phase pipeline tracker it now surfaces:

- the **five user-stage arc** — `brainstorm → spec → develop → review → ship`, each marked
  done / current / pending;
- the **develop task counter** — `develop [<done>/<total>]` — shown when a real decomposition
  batch is on record;
- a **"pending this session: …"** line naming the stages still ahead.

All of it is **derived read-only** from the same run-state and progress log the always-on
[stage badge](../concepts/pipeline.md#the-always-on-stage-badge-stage-54b) reads — never
fabricated. With no active run it prints a friendly empty view instead of an error.

## In the terminal — parallel-aware lanes (always on)

```bash
mokata watch                 # on the default `terminal` tier
mokata progress --lanes      # the same lanes, any tier
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

## In the browser — the IDE's Sessions tab

```bash
mokata watch --open              # open the IDE on Sessions for the current run
mokata watch --open --run <id>   # … for a named run
mokata watch --open --no-browser # print the link instead of launching a browser
```

There is **one** browser window: `mokata watch --open` (and `/watch` inside Claude Code)
opens the [`mokata ide`](review-agent-work.md) window on its **Sessions** tab. If a window is
already serving this repository it is **re-used** — re-focused on this run's session — and no
second server is started. The Sessions tab shows the run's session — its card (files, writes, the
gates it fired or that blocked it, its spend) over its **Timeline**; **Review changes** opens its
files in Review. Anything waiting on you is in the one waiting bar under the top bar, and the run's
pipeline position is under **Sessions › Plan & spec**.

The link lands on Sessions **once**, and only in the tab that followed it: other open tabs keep
their screen, and a later reload keeps whatever screen you moved to. On a box with no display (an ssh shell, a headless Linux machine) no browser is launched — a
console browser would take your terminal over — and the link is printed for you to open; the same
happens when the browser refuses. The command never waits on a browser.

> The self-contained `watch.html` dashboard of earlier releases is gone (0.0.27): it showed a
> subset of what Sessions shows, in a second window. Its parallel lanes are the terminal lanes
> above (`mokata watch`, `mokata progress --lanes`, the `lanes` MCP tool); its bounded audit-ledger
> feed is `mokata audit`.

See the [pipeline concept](../concepts/pipeline.md#run-progress-tracker).
