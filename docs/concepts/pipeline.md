# Concept: the 7-phase pipeline & gates

mokata's engine is a 7-phase pipeline. Each phase consumes the prior phase's handoff, and
several phases carry **gates** — checks that must hold before the run proceeds.

```
brainstorm → analysis → strawman → pre_mortem → probes → completeness_gate → emit
```

These are the canonical `PIPELINE_PHASES`. You can run the whole thing
(`mokata playbook`) or enter at any phase (`mokata enter <phase>`).

**Front-ends.** Two phases sit *in front of* the pipeline and hand a gated direction into
it: `brainstorm` (for a *new* problem) and `refine` (for *existing* code — review → approve
a scoped set → hand off to `spec`). Both HARD-GATE the spec; the completeness gate reads
whichever ran. See [refine existing code](../how-to/refine-existing-code.md).

**mokata engages brainstorm when you're exploring.** You don't have to remember to type
`/mokata:brainstorm` — the skill is *model-invocable*, so Claude Code can auto-activate it
when you're weighing options or describing a new problem before any code. You'll know it
stepped in by the banner `mokata · brainstorm (engaged)`. It's proactive, **not** intrusive:
it only *starts* the conversation — the HARD-GATE still holds (no spec/code until you approve
an approach), and it won't hijack a direct command or mid-implementation work. Turn it off or
make it ask first with `settings.brainstorm.auto` (`on` | `off` | `ask`, default `on`):

```bash
mokata config set settings.brainstorm.auto off    # never auto-engage
mokata config set settings.brainstorm.auto ask     # offer first, don't dive in
```

**A long brainstorm stays on-thread (anti-drift anchor).** On longer explorations the
original ask can scroll out of view and replies start to wander. mokata holds the thread down
with two pieces of session state, re-surfaced every turn:

- an **immutable anchor** — the user's original ask/goal, captured *once* at the start. It is
  set-once and can never be rewritten by a later turn or by a resume, so the thing you came to
  explore is always the reference point.
- a **compact running synthesis** — the goal, the constraints decided so far, the approaches
  on the table, and the current open question. It is updated each turn and **bounded** (a few
  short lines, hard-capped): a running state, never a transcript dump.

Each turn mokata restates the anchor + synthesis and runs a light **drift-check** — *"we're
exploring &lt;the anchor&gt; — does this still serve that?"* — and re-grounds to the anchor
when a turn strays. Resuming an in-progress brainstorm (`mokata brainstorm`) reprints this
anchor brief first, so you pick up grounded to the original goal. It's frugal/JIT (bounded, no
context dump), degrade-clean (no synthesis yet ⇒ just the anchor; no explicit anchor ⇒ the
topic), and the **HARD-GATE is unaffected** — the anchor is grounding, not approval; no spec
until an approach is explicitly approved, including after a restore.

## The phases

1. **brainstorm** — Socratic, *one question at a time*; surfaces 2–3 real approaches with
   tradeoffs; **HARD-GATE**: no spec until exactly one approach is explicitly approved.
   The approved approach is persisted to `.mokata/temp_local/state/approved_approach.json` and becomes
   a downstream constraint.
2. **analysis** — grounds the approved approach in the codebase (structural facts from the
   knowledge layer) and the answered questions; produces components/notes.
3. **strawman** — a first-cut design mapping the approach to each acceptance criterion.
4. **pre_mortem** — derives adversarial *risk probes* from the approved approach (each
   declared downside becomes a probe, plus standard failure/scale/rollback angles).
5. **probes** — checks the spec addresses each probe.
6. **completeness_gate** — the **provable-completeness blocker**: `emit` is refused until
   every acceptance criterion maps to a test (RED-before-GREEN traceability). It reads the
   brainstorm handoff so the approved approach is in view.
7. **emit** — produces durable output; the write is **human-gated**.

## Gates

Three phases carry a gate (the rest are advisory):

| Phase | Gate id | Kind | Blocks on |
|---|---|---|---|
| brainstorm | `approach-approval` | human | no approved approach |
| completeness_gate | `completeness` | check | any acceptance criterion with no mapped test (or an empty spec) |
| emit | `emit-approval` | human | un-approved durable output |

The completeness gate **never silently passes**: an empty spec and any unmapped AC both
block. See [AC traceability](knowledge.md) and the [governance model](governance.md).

**Spec before code, enforced.** Implementation entry points (`/mokata:develop`,
`/mokata:test`, and `mokata run develop`/`test`) carry a **`spec-persisted`** precondition
that fires *ahead of* the test gate: it blocks unless a saved spec with ≥1 acceptance
criterion exists (`emitted_spec.json`, written by the human-gated `emit` only after the
completeness gate passes). Jump straight to `develop` with no saved spec and mokata stops
with a clear next step — *"no saved spec — draft and emit it first (`/mokata:spec`)"* — and
logs the decision. So "spec written **and** saved before implementation" is enforced, not
merely implied.

**Don't break a saved spec by mistake.** As part of grounding, `spec`/`refine`/`develop` run a
**spec-awareness** check (Stage 37): a change is cross-checked against your **saved specs** and
**recorded decisions**, and if it would affect one, mokata surfaces it and routes it through the
deviation gate — confirm (amend/supersede) or re-plan, never a silent break. It's frugal (only
the touch-set, graph-expanded) and degrades cleanly (no corpus ⇒ no-op; no graph ⇒ a
lexical/file check that says so). See [governance](governance.md) and `mokata spec-check`.

**Start green, finish verified.** Before implementing, confirm a clean baseline —
`mokata baseline` reports the test suite green/red so a *new* failure is attributable to your
change (it degrades cleanly when no test command is configured — mokata never guesses one).
And the flow now **ends with `/mokata:ship`**: it verifies the work is *actually* done
(evidence over claims — green tests + every AC met + review passed; otherwise it blocks with
what's missing), summarizes what shipped, and **lets you choose how to land it** — merge, open
a PR, keep the branch, or discard. mokata may prepare a commit/branch or a PR description, but
runs a git action **only on your explicit confirmation**; it never merges, PRs, or deletes on
its own, and the finish decision is recorded in the audit ledger.

## Progress & visibility — one model, many channels

<a id="run-progress-tracker"></a>

**There is ONE progress model, surfaced on whichever channel you're looking at.** A multi-phase
run's state is computed once — `progress.build_progress()` derives a **`RunProgress`** (the
ordered phases marked done / current / pending, the `[done/total]` count, and what's next) from
the persisted run-state (`pipeline_run__<id>` checkpoints), so it **can't drift** from what the
engine actually did. Everything below is a **read-only renderer** over that single source — no
second progress system, no duplicated logic:

| Channel | What it is | Ask for it |
|---|---|---|
| **run-progress block** | the compact `done/current/pending` text tracker | `mokata progress` / `progress` MCP tool |
| **native to-do widget** | the *same* run-progress in the harness's own to-do list (summary + steps) | the agent renders it; derived from `build_todo_items` |
| **stage badge** | the always-on one-line mode badge in mokata's statusLine | on by default after `mokata setup` |
| **lanes + watch dashboard** | parallel-aware lanes + a clickable local HTML dashboard | `mokata progress --lanes` / `mokata watch` |
| **govern "what changed"** | the read-only audit recap of what changed and why | `mokata govern` / `audit --why` |
| **resume line** | the SessionStart one-liner for where to pick up | shown automatically on reopen |

All of them are **local, read-only, and non-authoritative** — pure surfacing, no telemetry, they
never write durable state or gate a run. This section is the single description of that model;
other docs point here rather than re-describing it.

### The run-progress block

The compact tracker: each phase marked **done / current / pending** with a `[done/total]` count
and what's next.

```text
mokata · run  [3/7 done]
  ✓ brainstorm        approach-approval passed
  ✓ analysis
  ✓ strawman
  ▶ pre_mortem        ← you are here
  ○ probes
  ○ completeness_gate
  ○ emit
next: probes     ·     pending: 4/7
```

Ask for it anytime with **`mokata progress`** (or the `progress` MCP tool — `--ascii` for
plain glyphs). The pipeline skills also print it at the start and end of each phase, plus a
one-line banner — `mokata · develop (running)` → `mokata · develop (done)` — so you always
know **which** part of mokata is running. With no active run it says so cleanly (never an
error). It's local and read-only — pure surfacing, no telemetry.

### The native to-do widget (Stage 70c)

Where your harness has a **native to-do list** — a top summary line plus steps you can mark
done / in-progress / pending, like Claude Code's own to-do widget — mokata renders **the same
run-progress there**: a summary line (`mokata · run [3/7 done] — current: pre_mortem`) plus one
item per phase, and it stays in sync as each gate passes. It is **not** a separate progress
system — `progress.build_todo_items()` is a **thin projection of the same `RunProgress`** (the
items map one-for-one onto the tracker's phases: `current` → `in_progress`), so it can never
drift from the block, the badge, or the lanes.

**Honest mechanism:** mokata can't call the harness's to-do tool itself — the **agent** renders
the widget, driven by mokata's single progress instruction, and mokata supplies the derived
items (via `build_todo_items` / `mokata progress`) so the steps are never invented. Where there's
**no** native to-do surface, the same instruction falls back to printing the run-progress block
above. Degrade-clean: no active run → an empty summary and no items.

### Proactive resume surfacing (SessionStart)

You never lose your place when you step away. When you reopen a repo, mokata's **SessionStart
briefing** (the sub-2k-token context injection) leads off with **one line** (max two) when
there's something to pick up:

```text
▸ Resume: pipeline at 'strawman' (last passed 'analysis') — run `mokata resume`
▸ Resume: in-progress brainstorm 'auth-refactor' — run `/brainstorm` (or `mokata brainstorm`)
```

It's **derived read-only** from the same run-state — `progress.list_sessions` (the active,
incomplete run with a passed gate) plus `brainstorm.restore_brainstorm_progress` (a
mid-stream, not-yet-approved exploration). When there's nothing to resume the line is simply
**absent** — no noise. It costs no extra reads (no stat/counter bumps), is deterministic, and
**degrades clean** (no sessions, no brainstorm, or a corrupt checkpoint → no line, never an
error). It stays within the briefing's token budget and never displaces the always-on rules.

### Flow legibility — verdicts, the next step, and counters (Stage 54c)

Every run reads like a guided flow: you always know what just happened, why a gate fired, the
one thing to do next, and how far along you are. These are all **read-only/derived** — mokata
formats values the gates and run-state already produce; it never re-derives a verdict or bumps
a counter.

- **Gate-verdict legibility.** Every gate decision gets the SAME one-liner —
  `✓ <gate> passed (<detail>)` on a pass, `✗ <gate> blocked — <reason>` on a block — applied
  consistently across the completeness, spec-persisted, deviation, and write gates (a pass
  gets a one-liner too, not only a block).
- **Why-blocked + how-to-unblock.** Every block names the **single next action** that clears
  it, e.g.

  ```text
  ✗ completeness blocked — 1 acceptance criterion unmapped to any test
    → to unblock: write a test for each unmapped acceptance criterion (`/mokata:test`), then re-run the gate
  ```
- **Stage recap + next-step nudge.** When a stage finishes, mokata prints a recap and names
  the one next command: `✓ spec done — 5 ACs written. Next: \`/mokata:develop\``. **Honest
  mechanism:** Claude Code has no plugin API to pre-fill the prompt box or rebind Tab, so the
  nudge just *names* the next `/mokata:` command — it reaches you through the `/` command
  **autocomplete** (click-to-fill) and **model-invoked continuation** (Claude offers to run
  the next step so you just confirm). mokata never claims to type for you or rebind a key.
- **In-stage progress counters.** The `[done/total]` count (e.g. `[3/7 ACs]`) is surfaced in
  the stage/gate output, derived from the run-progress view.

### The always-on stage badge (Stage 54b)

Like glancing at Claude Code's own "plan mode on" indicator, you always know **which stage
mokata is in** — without asking. mokata ships **its own Claude Code statusLine**, wired **on
by default** during `mokata setup`, that renders a one-line **mode badge** of the five
user-facing stages with the active one highlighted:

```text
mokata ▸ [brainstorm · spec · ›develop‹ · review · ship]
mokata ▸ auth-refactor · [brainstorm · ›spec‹ · develop · review · ship] · 3/7
mokata ▸ [brainstorm · spec · ›develop‹ · review · ship] · 2 running · 1 blocked
```

During a parallel fan-out the badge appends a compact **agents summary** (`2 running · 1
blocked`), derived from the same lane view (Stage 54d) and omitted when the run is sequential
or has no parallel batch.

The active stage is **derived read-only** from the same run-state as the progress tracker (an
in-progress brainstorm, or the pipeline checkpoint) — `brainstorm` while exploring, `spec`
while the 7-phase engine builds the spec (with its phase counter), `develop` once the spec is
emitted. The session name (shown when present) comes straight from Claude Code's statusLine
payload. It's **deterministic**, costs the model **no tokens** (it's a status command, not
injected context), and **degrades clean**: no run → a minimal `mokata`; in a non-mokata repo
→ nothing at all; it **always exits 0** and never blocks the harness.

It's **opt-out**, never opt-in. Turn it off any time and the line goes quiet (your own
statusline, if any, keeps working):

```bash
mokata config set settings.ux.statusline false    # silence the badge
mokata unsetup claude                              # remove the statusLine wiring entirely
```

**Merge-safe.** If you already have a custom statusLine, `setup` **composes** over it (runs
yours, then appends mokata's) and stashes the original so `unsetup` restores it verbatim — it
never clobbers your line. Because Claude Code reads `statusLine` from your **settings.json**
(not from a plugin), the default-on wiring is delivered by **`mokata setup`**; pure
plugin-marketplace installs enable it by running `mokata setup` (or adding the one-line
`statusLine` themselves). The CLI equivalent for terminal use is `mokata progress`.

### Parallel-aware lanes + the clickable dashboard (Stage 40)

When mokata runs subagents in parallel, the progress view becomes **parallel-aware**.
`mokata progress --lanes` shows one line per concurrent lane with its state
(`running`/`done`/`blocked`/`degraded`), under the `[done/total]` phase header; a sequential
run renders as a single lane (the familiar feel). It's **derived** from the run-state plus the
execmode records the orchestrator already writes to the audit ledger — nothing new is persisted.

For a richer view, **`mokata watch`** writes a **self-contained, clickable local HTML
dashboard** (no external assets, no network, no server — pure stdlib) under gitignored
`.mokata/temp_local/`: a **"Running _N_ agents" panel**, the 7-phase pipeline, and a
**bounded tail** of the gate/decision feed. Choose your tier with
`mokata config set settings.ux.progress {terminal|dashboard|both}` (default `terminal`).

The agents panel is a **responsive card grid** (Stage 54h) — a header counting the agents
currently running over one **card per subagent**, each with the agent's title, a **status +
activity pill** (the lane's state plus a short activity phrase derived from its latest ledger
row, e.g. `running`, `review passed`, `blocked`), and a live **running/idle dot**. Click a card
to drill into that lane's ledger rows — no detail is lost. A sequential run renders as a single
card; no run shows the friendly empty state. This rich card grid is the **browser** `watch`
view only: the Claude Code **terminal** keeps the text lanes and the [stage badge](#the-always-on-stage-badge-stage-54b)
keeps its inline `N running · N done · N blocked` (a TUI can't draw the grid).

Both tiers are **read-only and non-authoritative** — they only *reflect* run-state + the ledger;
they never write durable state, never gate, never mutate a run. The dashboard is **frugal**
(only the active run + a bounded ledger tail, no model-token cost — it's a human-facing file) and
**degrade-clean** (no run → a friendly empty state; ledger absent → lanes only). See
[watch a run](../how-to/watch-a-run.md).

**Inside Claude Code (Stage 54d).** The same read-only engines are reachable *without leaving
the harness*: the **`lanes`**, **`watch`**, and **`govern`** MCP tools (all declared `read`) and
the **`/mokata:progress`** (incl. the parallel lanes), **`/mokata:watch`**, and
**`/mokata:govern`** slash commands. They reuse `build_run_lanes` / `write_dashboard` /
`build_governance_view` — no new engine, no gating, no durable writes (the `watch`/`govern` HTML
is the existing self-contained artifact under gitignored `temp_local/`). And during a fan-out the
[stage badge](#the-always-on-stage-badge-stage-54b) appends a compact agents summary — e.g.
`· 2 running · 1 blocked` — derived from the same lane view, omitted when the run is sequential
or has no parallel batch.

## Mid-pipeline entry (L2)

`mokata enter <phase> [--to <phase>]` runs a slice. The gates of the phases you run still
apply; upstream phases are not forced, and the skip is reported explicitly (never silent).

```bash
mokata enter completeness_gate        # run just the gate on a hand-written spec
mokata enter strawman --to probes     # run a slice
```

## Dry-run preview (E7)

`mokata preview` lists the planned actions, the gate at each phase, and the files each
phase *would* touch — with **zero side effects** (no writes, no ledger entries).

```bash
mokata preview                 # whole pipeline
mokata preview --start pre_mortem --to completeness_gate
```

## Worked example

```bash
mokata init --profile standard
mokata brainstorm                 # approve one approach (HARD-GATE)
mokata preview                    # see what will run + what it touches
mokata playbook                   # brainstorm → … → completeness gate → emit
mokata audit                      # every gate decision + tool call, in order
```

If a criterion has no test, the completeness gate blocks `emit` and the audit ledger
records the block — fix the mapping (write the test) and re-run.
