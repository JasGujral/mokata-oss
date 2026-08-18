mokata **0.0.18**. Upgrade with `mokata upgrade` (or `pip install -U mokata`, then
`mokata upgrade`). Requires **Python ≥ 3.10**.

---

## 🔴 If you use mokata's MCP server, read this before anything else

**Every `pip install mokata` since 2026-07-28 — including v0.0.17 — has produced an MCP server that
cannot start.** `mokata-mcp` fails on launch and **all 61 tools are absent** from Claude Code. The
mokata **CLI is not affected**; only the MCP server is.

**The cause.** mokata declared `mcp>=1.2` with no upper bound. `mcp` **2.0.0** was published on
**2026-07-28** and removed `mcp.server.fastmcp`, the module mokata's server is built on, so from
that date the dependency resolved to an SDK mokata cannot use. **v0.0.17 was published on
2026-08-08 — eleven days into that window** — and, until 0.0.18, it is still what a fresh install
gets. We found and fixed it on **2026-08-12, fifteen days after the window opened**; it went
unnoticed that long because every MCP test is gated behind `skipUnless(<the SDK imports>)`, so **a
server that could not start reported as `OK (skipped=…)` and CI was green throughout.**

**If your MCP server is broken right now, this is the command — it works without upgrading mokata:**

```
pip install 'mcp<2'      # resolves to mcp 1.29.0, the last release that has mcp.server.fastmcp
```

Then start `mokata-mcp` again.

⚠ **On 0.0.17, `pip install -U mokata` on its own does nothing for this** — 0.0.17's own unbounded
`mcp>=1.2` re-resolves straight back to 2.0.0 and reproduces the failure. Once 0.0.18 is published,
upgrading *does* fix it, because 0.0.18 pins `mcp>=1.2,<2` and pip will bring the SDK back down for
you. We are stating the direct pin anyway: **a pin change only ever reaches people who install
after it ships, and this notice has to reach the people who already did.**

0.0.18 bounds the SDK, and — so that this cannot recur silently — teaches the server to tell an
*incompatible* SDK apart from a *missing* one and to name a remedy that actually works. See
*What was fixed*.

---

## 0.0.18 — "The removal release."

⚠ **This release removes things.** If your manifest still names a removed memory backend and you
have data behind it, mokata will **refuse and tell you what to do** rather than answer with an
empty store. Read *What was removed* before upgrading. Nothing you own is deleted by this release.

**This release does not meet all of its own exit criteria.** Three of the eight are short, and
they are stated below with their measurements rather than left for you to find. That section is
not an appendix — it is the point of shipping the release honestly.

---

## What was removed

**Five deprecated channels are gone.** The deprecated set is now empty. The removed set is
`obsidian`, `native-memory`, `memory-share`, `vault` (the session-*transport* kind) and `neo4j` —
and each of them says something different to you, because the data behind them is three different
kinds of thing.

- **`obsidian` and `native-memory`** were memory *backends*. Their data sits behind a store this
  release can no longer open. If your `memory_store` chain still names one **and there is data
  behind it**, mokata now **refuses**: nothing read, nothing written, nothing deleted, and the
  message names your vault path and the one route across. **This is a fix, not a new obstacle.**
  Before it, the chain still resolved — the removed backend read as merely *absent*, the router
  degraded past it, and the SQLite floor answered with an **empty store, no error and exit 0**.
  Memory that read as erased by an upgrade.
- **`memory-share` and `vault`** are files *you own*, and this release **still reads them**.
  `mokata memory import` and `mokata session list` open them directly, so the notice tells you the
  path and the command — and deliberately does *not* tell you to downgrade. Sending you back to an
  older mokata for something the release in your hands already does would be a false refusal.
- **`neo4j`** is derived data, so there is nothing to bring across at all: the canonical graph
  re-derives from your code. Your Neo4j database is on your own server at `$NEO4J_URI` and this
  release has not touched it.

⚠ **`mokata vault push / list / search / pull` and `team join --vault` are unaffected.** The thing
that was removed is the session *transport* that shared the word and the directory. The
design-artifact vault is live and supported.

**The Neo4j code-graph backend is removed** — 153 lines across 10 modules, including the public
export surface, the graph-tool entry and the backend-resolution branches. mokata only ever
*queried* a graph your team populated. The embedded AST floor (or an adopted external code-graph
provider) is the supported path, and it rebuilds the graph from your code.

## What changed

**The PostgreSQL floor is now a number: ≥ 15, target 17.** It is declared once in code and derived
everywhere else — including the setup guidance that used to state a digit in prose and now
interpolates the declaration — with a drift test grading the whole tracked corpus against it. The
reason is a date rather than a preference: **PostgreSQL 14 reaches upstream end-of-life on
2026-11-12**, and mokata was still telling people to stand up a database that dies that day.
**This release declares the floor and does not yet enforce it** — see the known limitations.

**The declared Python 3.10 floor is tested by three CI legs instead of one.** It now also runs on
`ubuntu · 3.10 · jsonschema=absent` and on `windows · 3.10 · jsonschema=present`, a combination
that had been configured in no workflow at all. `scripts/floor-python.sh` provisions that same
floor locally in one command, reading the version from `requires-python` every time so nothing in
the script can go stale.

## What was added

**mokata tells you when it is your move.** A desktop notification and a sound when mokata has
stopped and is waiting on a human — a gated write, a CLI prompt, an approval nobody has answered.

- Both are **on by default**, with separate switches: `ux.notify` and `ux.notify_audio`.
- `ux.notify_level` has three levels — `harness-silent` (only the waits nothing else announces),
  `all-waits`, and **`all-prompts`, the default and the widest**.
- `MOKATA_NOTIFY` is an environment kill-switch that needs no manifest.
- **Rate-limited to one notification per 60 seconds.** mokata stages gated writes in batches;
  twenty banners for one wait is not twenty times the signal, it is none, plus a user who turns the
  feature off. This limit exists because the first build of it did not have one and raised a few
  hundred banners on a developer's desktop in a single test run.
- It never fires from a test process, never fires off-TTY, in CI, or under `--yes`, and never
  raises. Every OS call is an argv list under a hard timeout carrying no content of yours.
- macOS and Linux have both arms. **Windows has neither, and says so once** instead of failing
  quietly.

**The statusline leads with the thing that has stopped.** When mokata is waiting on you, that
segment is now **first** — `⏳ awaiting approval p-… (+4 more)  local · mokata` — instead of last,
behind a strip that grows, on a line every terminal truncates from the right. A wait outranks the
mode and the stage: those say where the work is, this one says the work has halted and is waiting
on the reader.

**A bare status badge no longer means two things.** `mokata` used to be the answer both when your
session is bound to no run (a real, healthy answer) and when the surface could not be read at all.
The healthy answer is unchanged; the unreadable one now reads `mokata ▸ ⚠ unreadable`.

---

## What was fixed

**The MCP SDK is bounded, and a dead server can no longer read as green.** The pin is
`mcp>=1.2,<2` — the top of this page is the part that matters to you if you are affected today.
The rest is why it will not happen again quietly: the server now tells **four states** apart where
the old code had one wrong branch for two — SDK *absent* (skip), *incompatible* (fail loudly),
*broken*, meaning installed but missing one of its own dependencies (skip, and name that
dependency), and *supported* (run) — and **each state names a remedy that actually works.** The
old message told a user whose SDK was installed that it was not, and sent them to
`pip install -U mokata`, which re-resolved to the same broken SDK and looped. A
`MOKATA_REQUIRE_MCP_SDK=1` CI leg is now the positive control: with it set, "the MCP tests skipped"
is a failure rather than a green. **Supporting `mcp` 2.x is a port, not a version bump; it is
scheduled separately.**

**No settings-sourced command reaches a shell unreviewed.** mokata's statusline wrapper runs a
command taken from `settings.json` through a shell. The code carried a security suppression
justified as *"this is the user's own pre-existing command"* — **and that sentence was false from
0.0.5 and stayed false, unchanged, for four releases.** Setup defaults to project scope, so the
command is read from `<root>/.claude/settings.json`: a file in your checkout, which can arrive by
clone, pull, or a merged pull request rather than by anything you typed. The shell call stays —
it is the feature, and mokata grants no execution the harness does not already have — but the
command's **origin is now recorded alongside it**, a foreign origin is **refused**, and the exact
command is **shown to you at setup approval**. Three approval paths were wired, not one: the setup
wizard and `reconfigure` had both been discarding the plan that was meant to show it to you.

**A release can no longer publish an incomplete asset set and call it a success.** **v0.0.17's
GitHub Release carries three `.sigstore.json` bundles, no `.sig` and no `.pem`** — they were never
created, not lost on the way. `cosign` v3.0.6 defaults to the new bundle format, under which
`--output-signature` and `--output-certificate` are accepted, warned about, and **ignored** (the
bundle already contains both). Two separate safety checks were disarmed and both are fixed: the
build's own `ls -l dist/*.sig dist/*.pem` **exited 0 because `shopt -s nullglob` was set three
lines above it**, and the publishing action's `fail_on_unmatched_files` defaults to false, so it
printed `🤔 Pattern 'dist/*.sig' does not match any files` and published the short list regardless.
The asset set is now **declared once and asserted three times against exact paths** — never a glob
handed to an existence check — so a missing signature fails the cut rather than shipping.
**Your verification is unaffected:** the `.sigstore.json` bundles v0.0.17 does carry are complete
and verifiable on their own; `cosign verify-blob --bundle` is the supported route.

---

## Exit criteria — three of eight are not fully met

This release was cut against eight criteria, each meant to be falsifiable by a command. **Five are
met. Three are not, and here is exactly how they stand.** They are recorded decisions with dated
reasoning, not omissions discovered later.

**Criterion 3 — "the PR gate runs under 10 minutes" — NOT MET, and measured.** The gate runs
**26–31 minutes**: three mirror runs at **1556 s**, **1791 s** and **1872 s** wall clock. The
gate's duration is one leg's duration — `windows-latest · py3.12 · jsonschema=present` was the
slowest in all three and the run's wall clock matched it to within four seconds — and inside that
leg **a single step, the unit suite, is 91% of it** (1413 s of 1552 s; 1627 s of 1787 s; 1708 s of
1868 s). ⚠ **Read 26–31 minutes as exact, not as an estimate or a lower bound:** all three runs
concluded `success` with 10 of 10 jobs green, and each wall clock is the run's own
`run_duration_ms` taken from the API rather than a span of job timestamps added up by hand. The
target was unmeetable as written: no rearrangement of the remaining steps reaches ten
minutes. It is re-homed to **0.0.19**, where cutting inside the unit suite is the only thing that
can move the number. **This affects contributors, not users of the released package.**

**Criterion 4 — "the declared floor runs on Windows and with `jsonschema` absent, and one command
provisions it" — CONFIGURED, AND ONE HALF HAS NEVER RUN.** The provisioner is built and has been
exercised. The two new floor legs are declared and correct on inspection, and they carry **zero
executions between them**. **This release's own mirror run is their first execution.** So the
criterion is *configured and unproven*, not proven — and if the Windows leg goes red on its first
run, that is the leg working on its first day, on a combination nothing tested before now.

**Criterion 8 — "the PostgreSQL floor is a number the code can enforce" — HALF MET, BY RECORDED
DECISION.** The floor is declared, derivable from one constant, drift-guarded across every tracked
path, and corrected on every public surface. **The enforcement is not built**, and is ruled to
**0.0.19**: **WARN before 2026-11-12, REFUSE after it.** The trade was taken deliberately —
PostgreSQL 14 stays upstream-supported until that date, nothing regresses for anyone today, and
holding the release for a window of zero exposure was judged the wrong call. It is recorded here
rather than reported as satisfied.

---

## Known limitations

- **The PostgreSQL ≥ 15 floor is declared but not enforced.** Point mokata at PostgreSQL 14 and it
  will connect and work. Enforcement — warn before **2026-11-12**, refuse after — lands in
  **0.0.19**, and that date is PostgreSQL 14's own upstream end-of-life. If you run PostgreSQL 14,
  plan your upgrade against that date rather than against mokata.
- **The PR gate takes 26–31 minutes against a target of under 10.** Measured: **1556 s**,
  **1791 s**, **1872 s**. **91%** of the binding leg is one step — **1413 s** of **1552 s**,
  **1627 s** of **1787 s**, **1708 s** of **1868 s**. ⚠ **Exact, not a lower bound** — three runs
  that concluded `success`, 10 of 10 jobs green, each figure the run's own `run_duration_ms`.
  Contributor-facing only; re-homed to **0.0.19**.
- **On Linux, the notification's *sound* needs a sound stack — and if there is none, and no terminal
  to ring, you get the banner and no audio.** mokata tries `canberra-gtk-play`
  (`libcanberra-gtk3-bin`), then `paplay` (`pulseaudio-utils`); on a terminal it falls back to the
  bell, which needs nothing. The uncovered case is an **MCP gated write on a box with no audio
  player**: an MCP server has no TTY, so there is no bell to fall back to. mokata raises the
  banner, **names the degrade once** and tells you which package to install — it does not pretend
  to have made a sound. `settings.ux.notify_audio false` stops it asking. macOS is unaffected
  (`afplay` and the system sound are always there); Windows has no arm at all.
- **The new Windows Python 3.10 CI leg HAS now executed — and it went red on its first run, along
  with the other six unit legs.** ✎ *Updated 2026-08-18; it read "never executed, zero runs", which
  was true when written.* Its first run was this release's mirror CI, and it found **twenty
  failures — all of them defects in mokata's own tests, none in mokata.** Nineteen were two causes
  (a bare `"bash"` argv, which on Windows resolves through System32 to the WSL launcher rather than
  to Git Bash; and repo-relative paths built with the OS separator then compared against
  `/`-spelled names); the twentieth was the floor provisioner's dry-run assertion. All fixed, with
  guards, before this release publishes. The leg working on its first day is the leg doing its job.
- **The SQLite FTS5/BM25 lexical tier still ranks *worse* than the keyword floor it replaced —
  the third release in a row this has been true.** **0.0.16** disclosed it and scheduled the repair
  for **0.0.17**; **0.0.17** shipped no ranking work; **0.0.18** shipped none either, so both the
  measurement and the defect stand unchanged. `normalize_lexical_scores` scales each engine's
  scores against the best score *in its own result set*, flattening exactly the gap that would have
  ranked a mid-pack answer. On the **100,000**-item benchmark, against the Jaccard keyword floor on
  the same probes and the same code — only the corpus size differs — the FTS tier measures
  **−5.6pp recall (0.5000 → 0.4444)** and **−10.8pp MRR@10 (0.8334 → 0.7258)**. At **5,000** items
  the same comparison loses no recall at all and only **−3.3pp** MRR, so a small corpus hides more
  than half of it. Now scheduled for **0.0.19**, with the rest of the ranking work, because the
  correct repair is rank-preserving normalization rather than a constant to tune. If you run a
  large store and your lexical results look mis-ordered, this is why.

---

Clean-room throughout; no dependency on, or text copied from, any other framework.
Apache-2.0, under MoStack.
