mokata **0.0.20**. Upgrade with `mokata upgrade` (or `pip install -U mokata`, then
`mokata upgrade`). Requires **Python ≥ 3.10**.

---

## 0.0.20 — "The floor you can measure."

Four releases ago mokata published a measurement against itself: the SQLite FTS5/BM25 lexical tier
ranked *worse* than the keyword floor it had replaced, and the repair was scheduled for the next
release. It was not in that release, or the two after it. **It is in this one, and it is retired on
a re-run of the same benchmark rather than on a claim** — which matters, because retiring it on a
claim is how it came to be published in the first place.

Alongside it: the code graph learns TypeScript, stops answering from your dependencies' source, and
`mokata doctor` gains two things to say that it previously could not.

---

## The ranking repair — measured, not asserted

`normalize_lexical_scores` scaled each engine's scores against the best score *in its own result
set*, which flattened exactly the gap that would have ranked a mid-pack answer above a poor one.
The repair separates the two jobs the tier was conflating: **FTS5 selects the candidates, Jaccard
scores them.**

On the **100,000**-item benchmark — two independently seeded corpora, the same probes, the same
code, only the ranking changed:

    arm                  before            after            Δrecall    ΔMRR
    A: jaccard (floor)   0.5000 / 0.8334   0.5000 / 0.8334    0.00      0.00
    B: fts               0.4444 / 0.7258   0.5000 / 0.8528   +5.56    +12.70
    C: +vector           0.4306 / 0.7235   0.5000 / 0.8253   +6.94    +10.18
    D: +expansion        0.7639 / 0.7235   0.8333 / 0.8253   +6.94    +10.18

The lexical tier now **meets the keyword floor on recall and beats it on ordering**. The published
regression — −5.6pp recall and −10.8pp MRR@10 at 100k — is measured closed, and the repair applies
to both lexical paths, not just the one the benchmark happens to exercise.

What is **not** closed is below, under *Known limitations*, with its own numbers.

---

## The code graph reads TypeScript

`mokata defs`, `callers`, `imports` and the blast radius now answer from `.ts`, `.tsx`, `.mts` and
`.cts` files — real call and import edges, in the same index as your Python, not a grep over text.

It is an **optional extra**:

    pip install 'mokata[graph-ts]'

mokata auto-detects it. And because an optional parser that fails quietly is worse than no parser
at all, `mokata doctor` now tells you which state you are in: a repository with TypeScript files and
no grammar installed is told that its graph answers are **incomplete**, and given the one command
that fixes it. A repository with no TypeScript is told nothing.

**Plain JavaScript is not covered.** See *Known limitations*.

## The graph stopped answering from your dependencies

On any JavaScript or TypeScript repository, mokata's graph floor walked into `node_modules` — it
holds the same file extensions mokata indexes — so `defs` and `callers` named files you do not
maintain, and a symbol defined *only* inside an installed package was answered as though it were
yours. That is fixed, from one declaration that now reaches every walker in the codebase.

Go's `vendor/` and Rust's `target/` are deliberately **not** pruned: `vendor/` is hand-maintained
source in some repositories, and pruning it would delete your own code from your own graph — the
same harm, in the direction nobody notices.

## `mokata doctor` says two more things

**Your code-graph chain may predate the provider that should be in it.** A `code_graph` capability
chain written before mokata's embedded AST provider existed names external tools and the grep
floor. Nothing ever re-offered the AST provider to it, so a repository whose chain read
`neo4j → ripgrep → grep` lost Neo4j in 0.0.18 and landed on **grep**, while the embedded floor — no
install, real edges — sat unwired beside it. A new `graph-floor-unwired` warning names the gap and
the single command that closes it. It never edits your manifest: doctor proposes,
`mokata reconfigure --add` previews and asks.

**An unreadable PostgreSQL version is now its own answer.** The supported-version floor has four
outcomes rather than three — below the floor, at it, and *"mokata could not read this server's
version, so the floor was not checked on this connection."* Previously an unknown version and a
checked-and-fine version shared one silence.

## Also fixed

**On macOS, mokata was trading away a durability guarantee it had written down and never set.**
SQLite's `synchronous` pragma has *two* compile-time defaults: one for a rollback journal, and a
separate one that takes over once the database is in WAL mode. mokata puts your local store into
WAL — that is what stops two windows blocking each other — and on a build whose WAL default is
`NORMAL`, that switch is itself what moved the store off `FULL`. The module carried a paragraph
explaining why it refuses `NORMAL` (it can lose the last committed transaction on power loss, and
mokata's writes are ones a human approved) and then relied on the default instead of writing the
pragma. Measured on two machines running the identical commit: `FULL` on Debian's sqlite 3.37,
`NORMAL` on a pyenv Python 3.13 linking sqlite 3.51. It is now set explicitly at the one place
connections are opened, so the value is mokata's on every build rather than that of whoever
compiled your libsqlite3. If you run mokata on a Mac, this one is worth the upgrade on its own.

**And a smaller correction to something the docs told you about your own store.** mokata's code
said that closing the last connection removes SQLite's `-wal` and `-shm` sidecar files, so a store
at rest is a single file. Whether those files are *unlinked* is a property of your SQLite build,
not of mokata, and on some builds they persist. Nothing is at risk and nothing changed in how your
data is written: the last close still **checkpoints**, so `memory.db` on its own carries every
committed row and copying that one file loses nothing. Only the claim about the file count was
wrong, and it is now stated correctly.

**A failed checkpoint told you to check your disk, whatever had gone wrong.** When mokata could not
write a crash-safety checkpoint it printed *"retry a save once the disk/permissions recover"* — a
diagnosis it had never made. The exception was caught and discarded, so one sentence covered both a
real permission error and a bug in mokata's own code. It now names the failure's type and says which
of the two it is: a filesystem refusal still points at permissions and disk; anything else says that
nothing on your machine needs fixing, and points at the issue tracker. The exception's *text* is
still never printed — it routinely carries a path — so you get the type and, for an I/O error, its
`errno` symbol. The notice stays once per moment; the count does not, and `mokata doctor` reports
how many times a checkpoint failed this session.

**`mokata spec amend` could hang forever; on the MCP surface it timed out at 60 seconds.** When an
amendment widens scope, mokata re-runs the blast-radius lens over the newly-authorized targets — and
that lens had no clock. It queries the code graph once per target, and with no structural graph each
query walks the corpus: measured at **7.1 s for one target, 14.6 s for three** on a 3,000-file
checkout. A widening amendment on a real repository therefore ran past the MCP budget, and on the
command line, which bounded nothing, never returned at all. The slow path is the *degraded* path —
the lexical floor is what you are on when the graph is unreachable — which is why it bit hardest
when it was hardest to diagnose. The lens now has a 20-second budget, a third of the MCP surface's,
so mokata's answer arrives first. It still fails closed: an unknown blast radius on a widening
amendment is refused rather than waved through. What changed is that the refusal arrives, says the
lens ran out of time rather than broke, and names the way out — widen less, or override
deliberately. On the lexical floor it also names the provider to install.

**A prompt nobody could answer said "aborted", as though someone had.** With no terminal — CI, a
hook, an agent harness — `mokata gate override`, `mokata approve`, `mokata spec amend`,
`mokata upgrade` and the degraded-evidence prompt declined in the words of a human decision and
named no flag. They now distinguish *you declined*, *nobody was asked* (naming `--yes` inline) and
*stdin closed with the question open*. The audit ledger recorded the fabrication too — a
no-terminal run was written down as a human decline, and rule-learning read it as a human pushing
back — so those rows are now `unanswered` and learning ignores them. `mokata spec amend` prints
both ways out, because its decline leaves the run regressed with writes blocked.

**`mokata test` and `mokata review` shared one blind spot.** A test could prove the producer of a
value populated it while the consumer read a different field, and the reviewer, re-running that same
suite, inherited the gap. `test` now requires each criterion to be asserted where the value is read,
and fed the boundary of its own inputs — empty, zero and other falsy-but-present values, absent
versus present-and-empty, a hostile key. `review` now treats the suite as the builder's claim in
executable form: open a real consumer of each changed contract, and name one input class the tests
never feed. Inventing a criterion is still forbidden; the scope rule was narrowed, not removed.

**`mokata reconfigure --remove` could not unwire a provider it had itself wired.** `--add pgvector`
put it in your memory chain; `--remove pgvector` answered *"no changes — your setup already
matches"* and left it there, because the two commands drew their candidates from different lists.
They now share one rule, and the always-present local floors are read off the profile rather than
kept in a list beside it. `--remove grep` is unchanged.

**The gate that refuses an edit on a degraded code graph now measures the degradation** at the
moment of refusal, instead of trusting a flag written into the session by the model driving it.
When the two disagree, the refusal says so.

**A removed channel is no longer answered as a typo.** `mokata migrate obsidian` exits 1 with the
removal notice and tells you where your data is; a genuine misspelling still exits 2 with
argparse's *"invalid choice"*. Existed-and-gone and never-existed now differ in wording **and** in
exit code.

---

## Re-scheduled

One commitment published in the v0.0.19 notes named this release and is not in it. It is named here
with what it was promised for, where it lands, and why.

- **The sub-10-minute PR gate.** First promised at **0.0.18** and carried by two releases since
  without being built; the second of those moves is what put it in this one, and it is absent from
  this one too:
  **the sub-10-minute PR gate is re-scheduled to 0.0.21**.
  That is the third move of the same item, so the
  reason is stated rather than implied — the target cannot be reached by trimming around the edges,
  because one step, the unit suite, is **91%** of the binding leg, and this release again added
  tests rather than removing them. Reaching it means cutting inside the suite, which is a piece of
  work in its own right and has never been booked as one. Contributor-facing; it does not affect
  the published package.

⛔ **The ranking repair is deliberately not in this section.** It was published against this
release and it shipped in it. A section that listed it would be disclosing a slip that did not
happen, which fails in the same way as hiding one.

---

## Known limitations

Everything below is shipped as-is, with the cost stated.

**TypeScript support is an optional extra, and without it the graph is quietly incomplete on a
TypeScript repository.** The base install carries no TypeScript grammar; `pip install
'mokata[graph-ts]'` adds two packages, roughly **5.1 MB** installed. Without them mokata indexes
your Python and skips your TypeScript — `mokata doctor` says so, and that notice is the only thing
between you and an answer that looks complete. **Plain JavaScript is not covered at all**, extra or
no extra: `.js`, `.jsx`, `.mjs` and `.cjs` need a different grammar this release does not ship, and
handing them to the TypeScript parser to widen the headline would have meant reporting whatever came
out. Resolution is by name — a call to `x.y()` resolves to `y` — the same model, and the same
limitation, as mokata's Python graph.

**The PostgreSQL floor is enforced, and one dimension of its evidence is manual.** Unchanged from
**0.0.19**, and the reason is now stronger rather than weaker: the CI legs written to retire this
sentence — a real below-floor server on a runner — have still never executed. The WARN and REFUSE
arms, the date arithmetic and the version detection are covered by the automated suite; what CI
cannot do is run them against a database, so the live legs were executed by hand against
**PostgreSQL 16.14**, and a genuinely below-floor server — a real PostgreSQL 14 — was never used.
⛔ Nothing here should be read as "enforced and verified", and retiring it because those legs are
now *present* would be the defect this release exists to close.

**Adding the embedder still costs ordering, and only the recall half of that disclosure is closed.**
Against the Jaccard keyword floor on the **100,000**-item benchmark, the vector tier now costs
**0.00pp** recall — it cost **6.94pp** — and **−0.81pp** MRR@10, where it cost **−6.3pp**. The
recall half is closed. The ordering half stands, and is not being folded into the section above.

**A release can still proceed on a protection read it could not fully corroborate.** When
GitHub's branch-protection endpoint is unreadable, mokata's release path has a third state that
passes only on positive corroboration, prints the assurances it did **not** obtain, and exits 3 so
an untaught caller reads it as a refusal. Restoring full verification was assigned to this release,
and neither candidate mechanism was built, so the constant naming the release now names the next
one. Nothing has been released on a degraded verdict yet — the exemption is armed, not spent.
Maintainer-facing; it does not affect the published package.

**The Windows notification sound is called, not verified.** CI grades that `winsound.MessageBeep`
is invoked with the right flag on Windows. No human has heard the result, and no headless runner
can. The visual toast on Windows does not exist at all. Treat the arm as wired and unproven.

**On Linux, the notification's sound needs a sound stack.** mokata tries `canberra-gtk-play`
(`libcanberra-gtk3-bin`), then `paplay` (`pulseaudio-utils`); in a terminal it falls back to the
bell, which needs nothing. The uncovered case is an MCP gated write on a machine with no audio
player — an MCP server has no TTY, so there is no bell to fall back to. mokata raises the banner,
names the degrade once, and tells you which package to install; it does not pretend to have made a
sound. `settings.ux.notify_audio false` stops it asking. macOS is unaffected.

**The PR gate still takes 26–31 minutes against a target of under 10, and this release did not
measure it again.** The figures stand from the **0.0.18** cut: **1556 s**, **1791 s** and
**1872 s** wall clock over three mirror runs, with a single step — the unit suite — at **91%** of
the binding leg. This release added tests, so the number has not fallen. Contributor-facing only.
See *Re-scheduled*.

**#28 closes on "you can tell", not on "the gate blocks".** An unregistered run is still allowed
through the phase gate; what changed in **0.0.19** is that the allow no longer reads as an
approval. You are told, once per session, that the gate is not enforcing. If you need it to refuse,
wire the hook — `mokata init --yes` does that for you, and `mokata doctor` reports the state.

---

## Upgrading

    pip install -U mokata
    mokata upgrade

`mokata upgrade` is the part that matters: it refreshes the commands, skills and hooks in your
checkout to match the installed version. `mokata doctor` will tell you if anything is out of step.
