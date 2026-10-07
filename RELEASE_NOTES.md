mokata **0.0.21**. Upgrade with `mokata upgrade` (or `pip install -U mokata`, then
`mokata upgrade`). Requires **Python ≥ 3.10**.

---

## 0.0.21 — "Decisions, on the record."

0.0.21 was planned as the release in which a mokata run tells the truth about itself, end to end:
what it decided, what it cost, and that it is one run from registration to ship. **It ships the
first two of those and not the third.** Every governance decision now emits a typed event into an
append-only store on your machine, a team can opt in to publishing that stream to its shared
PostgreSQL store, and mokata has measured its own token estimate against real counts for the first
time. One identity per run, a conversational run the phase gate binds, and review as a measurable
gate are not here — *Re-scheduled* and *Known limitations* name each one rather than calling the
programme "partially met".

Alongside it: the code graph notices an edit rather than only a commit, a broken graph adoption is
no longer told to adopt again, and commands stop crashing on a narrow stdout.

---

## Every governance decision is now a typed event

mokata records governance in an append-only SQLite store at `.mokata/temp_local/events/events.db`.
It is local-only in every profile — never committed, never synced — and the vocabulary is closed at
seven types: `GateDecision`, `ToolCall`, `TokenSpend`, `MemoryOp`, `PhaseTransition`,
`ApprovalDecision` and `SecretScanHit`, each in a versioned envelope.

The events are **derived**, not hand-written at call sites: every audit-ledger entry projects into
the stream as it is appended — 51 of the ledger's 65 kinds map to an event, and the other 14 are
declared unmapped with a reason, held total by a sweep rather than a hand-kept list. MCP dispatch,
secret-scan hits, phase transitions, review verdicts, token spend and memory reads emit their own.

Two rules hold across all of it. **Absence is never written as zero** — `duration_ms` is carried
where something was actually timed (today, MCP dispatch) and is absent elsewhere, because a `0`
would say the decision took no time. And **an event carries identifiers, verdicts and counts, never
a subject, a value, a diff, a reason's prose or a scanned secret.** `settings.events.enabled`
(default on) reaches every producer; an unreadable manifest resolves to *on*, because a toggle
nobody can read must not silently disable an audit surface.

A clean resume now clears the replaced session's state before loading, and reports what it
cleared — including an approval written by a mokata that predates per-session state.

## Sharing the stream with your team

`mokata events` tells you where the stream stands: how many events the local store holds, whether
publishing is on, what a publish would send, and **whether a live notification can be received at
all over your configured DSN**. `mokata events --share` — or the `events_share` MCP tool, which
proposes and waits for `mokata approve <id>` — publishes new local events to the team's shared
store.

Publishing is **opt-in and off by default**:

    mokata config set settings.events.shared true

and it is treated as data leaving your machine. Every publish goes through the write gate, where the
secret scan is a hard block an approval cannot override — measured: an event carrying an AWS-shaped
key in a payload field is refused with findings and zero rows written, while the same path with a
clean payload commits.

After rows land, mokata sends a PostgreSQL `NOTIFY`: a doorbell carrying identifiers and counts, not
the events, so a missed one costs nothing. **Nothing in mokata listens for it yet.** The cache it was
designed to invalidate was removed in an earlier release, so a subscriber would have had nothing to
do. And because `LISTEN`/`NOTIFY` does not survive a transaction-mode connection pooler — and a
publisher cannot tell *"nobody is listening"* from *"nothing can listen"* — `mokata events` prints
which of three states your DSN is in: deliverable, cannot be received, or unknown.

Sharing requires team schema **v6**; see *Upgrading*. This release also ran mokata's full
live-database suite against a real PostgreSQL 16.13 — 131 tests, green.

## mokata measures its own token estimate

Every budget mokata enforces — the 2,000-token bootstrap budget, the handback caps, the savings
ledger, the parallel cost estimates — rests on a `chars/4` estimate whose margin had never been
measured: the calibration record and its `doctor` finding have existed since 0.0.16, and the one
production caller logged the estimate with no real number beside it.

mokata now reads real output-token counts from Claude Code's transcript through a read-only adapter
— **counts only, never text** — on the post-tool-use hook, and `mokata doctor` reports the running
ratio whether or not it is in trouble. The scope is declared: **output** side only, on responses
whose content is entirely text (thinking tokens subtracted); a response that also carries a tool
call is excluded, never half-counted, and a session that never edits a file is never sampled. An
exact-count adapter calling a token-counting API was deliberately **not** built: the mokata process
never calls a model. The first measurement is under *Known limitations*.

## The code graph notices an edit, not just a commit

Before every graph query mokata unions four invalidation signals, and the detection logic was
correct in all four — **three were wired to nothing.** In-harness edits were recorded under Claude
Code's session id and read under a fresh random one, so no in-harness edit ever drove a rebuild, and
the working-tree signal ran only when `HEAD` moved — only when you committed. Both are fixed; the
working-tree check now runs on every query at **9.2 ms**, against the **1615 ms** walk the obvious
alternative costs.

Four more holes are closed with it:

- **A brand-new file is seen before you `git add` it** — one bounded `git ls-files --others`,
  **30 ms**.
- **An out-of-band revert, a delete-then-recreate, and a restore that keeps a file's timestamp are
  caught before the answer.** mokata's index had stored each file's mtime and size since it was
  written, and nothing compared them. A stat pass over the stored baseline costs about **7 ms** over
  2,423 paths, against **843 ms** to hash them all, and only drifted paths are hashed. On Linux and
  macOS it also compares `st_ctime`, which userspace cannot set — so a `tar -xp` or `cp -p` restore
  that keeps mtime and size, and that git's own stat cache therefore reports as clean, is caught.
- **Edits to files whose names git quotes** — an accented character, a `"`, a `\`, a control
  character — were dropped and read as fresh. The same blindness let `mokata ci-check --base` pass
  a PR green when it touched such a path. Both read git's NUL-separated output now.
- **The post-answer recheck works after a session's first query**, not only on it.

**`graph.required` stops telling you to adopt a graph you already adopted.** The refusal was
rendered from one boolean standing for several situations, so a graph that was adopted and broken —
the tool uninstalled, a wrong venv, a `PATH` that lost it — got the same *"adopt a real code graph"*
as a target that does not exist. The reason is now derived and the advice follows it: adopt,
repair (`mokata doctor`), or check the target — in the refusal, its roads out, and the MCP hint
alike. The reason stays off the wire, so a model cannot write one into a session to explain away its
own refusal. **Which situations refuse is unchanged**, deliberately: that is a product decision, and
it is not taken in this release.

What freshness still cannot see is under *Known limitations*, with its numbers.

## Also fixed

**Commands crashed on a narrow stdout.** On a cp1252 stdout — what a GitHub Windows runner hands
Python — or an ASCII one (`LC_ALL=C`), `mokata tour` and `mokata release-notes-check` died with
`UnicodeEncodeError` the moment they printed a `−` or a `⚠`. `release-notes-check` is a release
gate, so the gate crashed while printing its verdict and had none. Every command now degrades the
glyph **visibly** with `backslashreplace`: you see `−`, which names the character that was
dropped, where a `?` would be indistinguishable from a real question mark.

**A parallel run that lost its subagent mid-batch reported spending nothing.** The token trail was
cleared on `SubagentUnavailable`, so `actual_cost` read **$0.0000** for a run that had spent real
money, and `within_budget` returned true for a batch that may have blown its budget before it fell
over. The trail is kept now and marked partial (`accounting_is_partial`), so an honest zero and an
erased one are no longer the same three characters.

**The `audit_share` MCP tool said "blocked" when it had nothing new to publish** — the same word the
secret-scan refusal uses, with `committed: true` beside it. Status is now derived by one function
from the publisher's own reasons, for audit and events alike.

**A retrieval wrote every identifier it returned into the audit ledger, unbounded** — about 20 KB
for a 500-item retrieval. It now names at most eight and says how many it dropped.

**`mokata release-notes-check` now catches a promise that names no version.** One pointing at
whichever release comes after the current one re-targets itself at every cut, so it can never be
found false. It is now its own red result, `UNRESOLVABLE`: name a release, or delete the promise.
Every run also prints the best exit code that invocation could reach.

## For contributors

None of this changes the published package. A release retry reads the PR's head with its state in
one call, so the commit CI was gated on and the commit the merge publishes cannot differ. The public
mirror is verified before the sync, and cheap release gates run to completion and report once. An
interpreter that cannot run the suite is refused with exit 2 instead of producing 146 red results
(`CONTRIBUTING.md` documents provisioning); the suite can no longer hang on a consent prompt at a
terminal, or silently run everything when a computed test list is empty. A hostile-arrangement leg
runs the CLI under cp1252 and C-locale stdout on every push — it found the crash above — and
`scripts/shuffle-order.py` reports how a seeded, replayable order differs from the alphabetical one.

---

## Re-scheduled

One commitment published in the v0.0.20 notes named **0.0.21** and is not in it.

- **The sub-10-minute PR gate.** Published against **0.0.21** in the v0.0.20 notes and not built in
  it — the fourth release in a row. **These notes name no new release for it:** a target nobody has
  booked work against is not a schedule, and printing one would make it a promise.
  Contributor-facing; the number is under *Known limitations*.

## Not in this release

Three pieces of planned work did not ship. None was promised in published notes, so they are
listed here rather than as re-scheduled commitments — but each still names the release it is
owed in, and that release is checked against the plan before this one could be tagged.

- **A run that holds one identity from registration through ship, and a conversational run the
  phase gate binds.** Re-scheduled to **0.0.22** (tracked as `B-LIFE-FU`). The first depends on the event schema, which only
  arrived in this release; the second's structural half collides with the gate's rule that it acts
  only on a positive trigger, and is not small.
- **Review as a thin gate with a measurable caught-at-review rate**, with the status-line work.
  Re-scheduled to **0.0.22** (tracked as `REVIEW-LIB-SWEEP`).
- **The internal-tooling suite's recorded local gate, and the CI ceiling re-derived from the public
  mirror.** Re-scheduled to **0.0.22** (tracked as `INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER`). Contributor-facing; it does not affect the published package.

  Why these three are absent, stated rather than implied: building them on the way out meant writing
  three stages of code into a release cut the same day, with no way to run a test against them.

---

## Known limitations

Everything below is shipped as-is, with the cost stated.

**Graph freshness: two classes of edit are still not detected before the answer.** Since 0.0.16
mokata has published that *"every graph query front-runs a freshness check; a known-stale graph
rebuilds before it answers (never serves stale structure)."* Re-measured in this release, that is
**still not fully true** — less untrue than before, and for a different reason.

1. **A source file in no baseline that git will not name.** A file the cold-start index never covered
   (it indexes `.py` only) that is *also* excluded by `.gitignore`, so git skips it too — a `.ts`
   file under an ignored directory is the shape. On every platform. Closing it means indexing the
   full polyglot set at cold start or walking ignored trees, and both cost the repository-size walk
   this check exists to avoid. It is a trade, not an oversight.
2. **On Windows: a same-size content write that restores the mtime.** `tar -xp`, `cp -p`,
   `rsync --times`, `touch -r` — every vendor drop and every restore — move neither mtime nor size,
   and git's own stat cache trusts exactly those two fields, so both pre-answer signals are blind
   together: a real edit to a tracked, indexed `.py` reads `fresh=True` with an **empty note**, the
   same thing a genuinely clean tree says. On Linux and macOS this is closed by `st_ctime`, measured
   on an `os.utime` restore and a real `tar -xp` (mtime no, size no, ctime yes). On Windows
   `st_ctime` is the *creation* time and does not move on a write, so the class survives there.
   **On Windows, rebuild the code graph yourself after a restore or a vendor drop.**

**Two bounds are disclosed rather than left unbounded**, both costed notes on the answer and never a
block. At most **500** drifted paths are hashed per query, and the rest are *rebuilt* without being
confirmed — a redundant rebuild, never a missed edit. At most **5000** deletion records are kept;
the oldest are dropped and **named** when that happens, and a byte-identical recreate of a named path
is then outside every signal.

**The cold-start walk seeds the baseline; it is not a detector.** It reports only after a query
persisted the index without its state — one cancelled mid-rebuild (Ctrl-C, a cancelled MCP call) or
two running at once. Plan around three signals plus the drift pass, not four detectors. **And
in-harness edit records are not seen before a run is registered:** the hook has Claude Code's
session id and the query process cannot see it (a Claude Code harness gap, #25642). The working-tree
and code-anchor signals still cover those edits, and the pass says so rather than reporting a clean
tree.

*Quantitatively: **2 classes** undetected before the answer — non-`.py` source under a gitignored
path on every platform, and a same-size mtime-preserving write on **Windows only**. In-harness edit
records blind until a run is registered. The warm path costs ~39 ms of bounded git plus a ~7 ms stat
pass over 2,423 indexed paths, against 843 ms for a content-hash walk. Bounds: ≤500 drifted paths
hashed per query, ≤5000 deletion records retained.*

**This release's exit criteria are not all met, and the ones that are not are listed here rather than
rounded up.**

**1 · CI did not run on the development repository during this release, and that is not a backlog
item — the event that would have closed it was removed by decision.** The criterion that the
development repository runs its own suite on a push is **not met and not meetable**: its Actions
minutes were not restored. **What remains true, in the same breath:** the public mirror's CI runs
on **every push and every PR**, so the public suite and the Windows legs at the cut are unaffected.
What is gone is CI on the development repository *during* the build — which is why Windows failures
arrive late, and why the hostile-arrangement leg exists to buy back two of their causes.

**2 · The events programme's first criterion was amended during this release, and the original is
kept beside it.** It read *"every governance decision emits a typed event carrying duration and real
token usage."* Measured, the first clause was met and the second was not. The amended form — a typed
event per governance decision; `duration_ms` where it was **measured** and absent, never zero, where
nothing was timed; token spend as its own typed event with estimate and measurement kept apart — is
what shipped, and every clause of it is tested. A per-event usage field was declined: a slot almost
no producer can fill teaches a reader to treat absence as noise. The precedent for amending by
recording rather than quietly rewriting is 0.0.18's PR-gate criterion, which closed by publishing
the number it missed by.

**3 · Three planned stages did not ship, and the events programme stands at one of its four
criteria.** The three missing ones, by name, because *"partially met"* is the collapse this
programme exists to prevent:

- **A run does not yet hold one identity from registration through ship.** There is no
  ship-completion event; entering the ship stage is recorded only by the CLI, with no MCP path; and
  the stage-pass event is **defined and never written**. A run finished outside that one CLI command
  never records its completion time.
- **A conversational protocol run still does not reliably arm the phase gate.** Registration is
  instructed by skill prose, pinned so the instruction cannot silently vanish; it is not structural,
  and the fail-open survives. Skip the instruction and the gate correctly reports *"no active mokata
  run — not policed"*.
- **Review is not yet a thin gate with a measurable caught-at-review rate.**

**What did ship, and should not be understated:** the typed event stream; the ledger-kind register,
held total over the codebase by a sweep rather than by a list; the `events.enabled` toggle, which
now actually reaches every producer; and the first real-PostgreSQL measurement of the shared event
log.

**4 · The suite's order-independence was searched over nine arrangements against a target of ten, and
none of the nine found an order-dependent test.**
`scripts/shuffle-order.py` is built, tested, replayable by seed, and runs the alphabetical baseline
alongside the shuffle so the report is the **difference** both ways — a test that fails only
alphabetically is a residue the alphabet *creates*, and a shuffle-only tool would never see it.
**The instrument is not the search.**
Seeds 23, 37, 41, 59, 67, 73, 89, 97 and 101 each ran the whole suite twice (about 8,480 tests,
~23 minutes a seed) and failed the same set in both orders. The tenth seed's result was overwritten
by a re-run rather than failed, so it is not counted. Nine samples are a search, not a proof.

**The token estimate has been measured once.** One live Claude Code session: **415** API responses,
**5** of them calibratable — almost every response in an agentic session carries a tool call — with
real output tokens at **1.552×** the `chars/4` estimate. If that holds, `chars/4` under-counts
mokata's dense prose by about 55%, and every budget enforced against it is enforced against a floor
it does not hold. **The budgets are unchanged in this release.** Five responses from one session is
not a distribution, and it is not presented as one.

**The event publisher infers where it left off from the team's table, not from its own record** —
the highest sequence its actor name has published. A second machine under the same `$MOKATA_ACTOR`,
or a rebuilt local `events.db`, is therefore **refused by name** (`pointer-ahead`, `pointer-stuck`,
`local-unreadable`) rather than reporting *"nothing new"* while publishing nothing. The refusals are
not the fix; a high-water mark kept locally is, and it is not built.

**Carried from 0.0.20, unchanged unless noted.** TypeScript is an optional extra
(`pip install 'mokata[graph-ts]'`); without it the graph is quietly incomplete on a TypeScript
repository, and **plain JavaScript is not covered at all**. The PostgreSQL floor is enforced and one
dimension of its evidence is manual: no live leg has run against a below-floor server, and this
release's run against PostgreSQL 16.13 is still not a PostgreSQL 14 — nothing here should be read as
"enforced and verified". Adding the embedder still costs ordering (**−0.81pp** MRR@10 at 100,000
items; the recall half is closed). The Windows notification sound is called, not verified, and the
Windows toast does not exist. On Linux, an MCP gated write on a machine with no audio player raises
the banner and makes no sound (`settings.ux.notify_audio false` stops it asking). And #28 closes on
"you can tell", not on "the gate blocks" — the same fail-open as the second missing criterion above;
`mokata init --yes` wires the hook that makes it refuse.

**A release can still proceed on a branch-protection read it could not fully corroborate.** Restoring
full verification was carried into 0.0.21 and was not built in it either; the exemption is now four
releases old, and the refusal now names 0.0.22 as its expiry. Maintainer-facing; it does not affect
the published package.

**The PR gate still takes 26–31 minutes against a target of under 10, and was not measured again.**
The figures stand from the **0.0.18** cut, with the unit suite at **91%** of the binding leg. This
release added tests again — one new guard alone costs about 18 s per suite run — so the number has
not fallen. Contributor-facing only. See *Re-scheduled*.

---

## Upgrading

    pip install -U mokata
    mokata upgrade

`mokata upgrade` is the part that matters: it refreshes the commands, skills and hooks in your
checkout to match the installed version. `mokata doctor` will tell you if anything is out of step.

**If you use team mode**, nothing changes until you turn event sharing on. Memory, sessions and the
shared audit log keep working on team schema v3 and above — the global floor did not move. Event
sharing needs schema **v6**: re-run `mokata team init`, and until you do, `mokata events --share`
refuses and names that command. The v6 migration reshapes the shared events table, which no mokata
build had ever written to; if yours somehow holds rows, the migration **refuses by name and changes
nothing** in that table rather than guessing. A refused migration can leave the other team tables
upgraded and the events table on its old shape — the refusal message names the recovery.
