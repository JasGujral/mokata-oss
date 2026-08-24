mokata **0.0.19**. Upgrade with `mokata upgrade` (or `pip install -U mokata`, then
`mokata upgrade`). Requires **Python ≥ 3.10**.

---

## ⚠ If you script `mokata init`, read this first

**`mokata init --yes` now wires your Claude Code harness.** Before this release it wrote a
`.mokata/` store and left your harness alone. It now also writes:

- `.claude/commands/` and `.claude/skills/`
- **`.claude/settings.json`** — the hook registration that makes the gate enforcing
- **`.mcp.json`**
- and it **spawns a `mokata-mcp --version` subprocess**, for the MCP handshake and the
  version-parity probe.

If you run `init --yes` in CI, in a sandbox, under a timeout, or with the network or subprocess
spawning restricted, that is new work inside a command you may have measured before. Nothing is
written without `--yes`; a plain `mokata init` wires nothing and **says so**.

**To undo it:** `mokata unsetup claude --scope project`.

**To see it first:** `mokata init --preview --yes` now renders the whole plan — this release fixed
a dry run that described three files of the eighty-five the real run writes.

---

## 0.0.19 — "The release that grades its own promises."

0.0.18 went to PyPI carrying five commitments against a release whose scope had been replaced, and
every check in the repository was green. They were green because they all asked the same question:
is the promise still *printed*? None of them asked whether it was still *true*.

This release builds the check that asks the second question. It keeps the one commitment that had a
real date attached to it — the PostgreSQL floor — and it publishes a **Re-scheduled** section for
the two that moved, instead of renumbering them quietly. Alongside that, three decisions mokata
used to make in silence now say what they are: an init that wired nothing, a gate that let an
unregistered run through, and a transport read that could hang forever without raising anything.

---

## What was added

**The PostgreSQL ≥ 15 floor is enforced.** mokata reads the server's major version off the
connection it already opens and **warns before 2026-11-12, refuses after it** — that date is
PostgreSQL 14's own upstream end-of-life, and it is the date the v0.0.18 notes committed to. The
two arms differ in what they *do*, not in how loudly they say it: warn connects and carries the
notice; refuse does not connect, falls back to your local SQLite store, and tells you why. There
are four outcomes rather than two — below the floor, at it, and *version unknown*, which is neutral
on every date, because an unreadable version is not an old one. The failure it raises points at the
one remedy that works: upgrade the server.

**A published schedule now has to resolve.** `mokata release-notes-check` grades every release
promise printed in a shipped file against the plan that owns the release it names. It has three
answers, not two: the notes are right; a promise no longer resolves; or *this cannot be checked
from here* — the honest answer for the published package, which does not contain the maintainers'
planning tree. The third is not a pass and nothing in the release path treats it as one.

**The two repositories' tag sets are checked against each other before a cut can start.** The dev
tag for v0.0.18 was never created — PyPI had the release, the mirror had its tag, the GitHub
Release had its signed assets, and this repository's tags stopped one short for four weeks, with
every check green. The check now runs in the next cut's preflight whatever the previous run did,
and again after the tag step. Its first run found a tag missing since v0.0.4 and stopped the cut
until it was reconciled. There is no waiver.

**`release.sh` can be run twice.** A failed cut is retried rather than finished by hand. Seven
steps were non-idempotent, and the decisive one was never the push: the preflight tag guard refused
at step zero, so any run that had ever tagged made every later run impossible. The release branch
is reused, and no force appears anywhere — forcing republishes a byte-identical tree under a new
SHA and throws away the CI result the merge is gated on.

**Windows gets a notification sound.** Standard library, no new dependency. Read the known
limitation below before you rely on it.

**Every CI job has a ceiling.** Thirteen of nineteen jobs across ten workflows had no timeout,
including every job in the release workflow — where a wedged job holds the logs of the build that
is publishing the artefact.

---

## What changed

**An unregistered run no longer passes the gate in silence.** It is still allowed: that floor is a
deliberate design decision and it did not move — not one of the twenty decision-table verdicts
changed in this release. What changed is that you are told, once per session, that the gate is not
enforcing. And mokata no longer states its own condition as a fact it does not have: a checkpoint
it merely could not read is reported as *unverifiable*, not as absent.

**Seventeen documentation pages were corrected against the code.** The changelog page in the
published navigation had 0.0.14 as its newest entry while 0.0.18 was shipping — four releases
missing, including 0.0.18's "your MCP server cannot start" notice. Nine sentences across seven
files still said `mokata init` does not touch Claude Code, which `--yes` now does.

---

## What was fixed

**A code-review-graph server that goes quiet no longer wedges the MCP server.** The stdio transport
wrote a request and then read with nothing bounding it. The bounded read now has one implementation
shared by both stdio clients, with four named outcomes, and a timed-out session is terminated,
reaped and dropped — the request went out and the reply never came back, so the process is
desynchronised and must not be reused. "It broke" and "it is hanging" are separate failures and
can no longer collapse into one.

**A decline with no terminal leaves something to approve.** The confirmation helper returned the
same `False` for two facts with opposite remedies — *a human was asked and said no*, and *no human
was ever asked*. Off a terminal, `mokata spec emit` declined, staged nothing, and parked the run
with nothing for `mokata approve` to redeem. The refusal now says which of the two it was.

**A worktree's `.git` was not excluded from the public mirror at all.** The one control on the
public/open-source boundary excluded `.git/` — and rsync reads that trailing slash as *directories
only*, while in a linked worktree `.git` is a regular file holding an absolute path into the
maintainer's own tree.

---

## Re-scheduled

Two commitments published in the v0.0.18 notes named **0.0.19** and are not in it. Both are stated
here with what they were promised for, where they land, and why — the alternative is renumbering a
published promise, which is the exact move this release built a check to forbid.

- **The FTS/BM25 ranking repair.** Disclosed at **0.0.16** and scheduled for **0.0.17**; absent
  from 0.0.17, 0.0.18 and 0.0.19 alike.
  **The rank-preserving repair is re-scheduled to 0.0.20.**
  Why: 0.0.19's scope was replaced wholesale on 2026-08-19 by the release-tooling and
  gate-visibility work above, and the correct repair is rank-preserving normalization rather than a
  constant to tune — a ranking stage, not a patch. The measurement is unchanged and restated below.
- **The sub-10-minute PR gate.** Promised for **0.0.18**, re-homed there to 0.0.19, and not built
  in it: **the sub-10-minute PR gate is re-scheduled to 0.0.20**. Why: the target was unmeetable as
  written — one step, the unit suite, is **91%** of the binding leg, so only cutting inside the
  suite can move the number, and this release added tests rather than removing them.
  Contributor-facing; it does not affect the published package.

⛔ **The PostgreSQL floor is deliberately not in this section.** It was published against
**2026-11-12** — PostgreSQL 14's upstream end-of-life, which is not ours to move — and it shipped
in this release. A slot is ours to re-schedule; a deadline is not.

---

## Known limitations

- **The SQLite FTS5/BM25 lexical tier still ranks *worse* than the keyword floor it replaced — the
  fourth release running.** **0.0.16** disclosed it and scheduled the repair for **0.0.17**;
  0.0.17, 0.0.18 and 0.0.19 each contain no ranking work, so both the measurement and the defect
  stand unchanged. `normalize_lexical_scores` scales each engine's scores against the best score
  *in its own result set*, flattening exactly the gap that would have ranked a mid-pack answer. On
  the **100,000**-item benchmark, against the Jaccard keyword floor on the same probes and the same
  code — only the corpus size differs — the FTS tier measures **−5.6pp recall
  (0.5000 → 0.4444)** and **−10.8pp MRR@10 (0.8334 → 0.7258)**. At **5,000** items the same
  comparison loses no recall at all and only **−3.3pp** MRR, so a small corpus hides more than half
  of it. See *Re-scheduled*. If you run a large store and your lexical results look mis-ordered,
  this is why.
- **The PostgreSQL floor is enforced, and one dimension of its evidence is manual.** The warn and
  refuse arms, the date arithmetic and the version detection are covered by the automated suite and
  by fifteen mutants. What CI cannot do is run them against a server: the hosted runners have no
  PostgreSQL. The live legs were executed by hand against PostgreSQL 16.14, and a genuinely
  below-floor server — a real PostgreSQL 14 — was never used, so the below-floor arm is proven
  against a simulated version report rather than against the database it describes. ⛔ Nothing here
  should be read as "enforced and verified".
- **The Windows notification sound is called, not verified.** CI grades that the call is made with
  the right flag on Windows. No human has heard the result, and no headless runner can. The visual
  toast on Windows does not exist at all. Treat the arm as wired and unproven.
- **On Linux, the notification's *sound* needs a sound stack — and if there is none, and no terminal
  to ring, you get the banner and no audio.** mokata tries `canberra-gtk-play`
  (`libcanberra-gtk3-bin`), then `paplay` (`pulseaudio-utils`); on a terminal it falls back to the
  bell, which needs nothing. The uncovered case is an **MCP gated write on a box with no audio
  player**: an MCP server has no TTY, so there is no bell to fall back to. mokata raises the banner,
  names the degrade once, and tells you which package to install — it does not pretend to have made
  a sound. `settings.ux.notify_audio false` stops it asking. macOS is unaffected.
- **The PR gate still takes 26–31 minutes against a target of under 10, and this release did not
  measure it again.** The figures stand from the 0.0.18 cut: **1556 s**, **1791 s** and **1872 s**
  wall clock across three mirror runs, with one step — the unit suite — at **91%** of the binding
  leg. This release added tests, so the number has not fallen. Contributor-facing only. See
  *Re-scheduled*.
- **#28 closes on "you can tell", not on "the gate blocks".** An unregistered run is still allowed
  through the phase gate. What this release fixes is that the allow no longer reads as an approval:
  you are told, once per session, that the gate is not enforcing. If you want it to refuse, wire the
  hook — `mokata init --yes` does that for you, and `mokata doctor` reports the state.

---

Clean-room throughout; no dependency on, or text copied from, any other framework.
Apache-2.0, under MoStack.
