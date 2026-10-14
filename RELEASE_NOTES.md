mokata **0.0.22**. Upgrade with `mokata upgrade` (or `pip install -U mokata`, then
`mokata upgrade`). Requires **Python ≥ 3.10**, and for the Claude Code integration
**Claude Code ≥ 2.1.0**.

---

## 0.0.22 — "One run, and the gates hold wherever the agent writes."

0.0.21 shipped the record of every governance decision and named what it did not ship: one identity
per run from registration through ship, a conversational run the phase gate binds, and review as a
measurable gate. **0.0.22 ships those three.** Building them showed how many ways an agent could
write around a gate, so this release also closes them: a shell write now meets the same run-state
gates as a `Write`, a gate's position is re-stated after compaction, a subagent works under a
contract, and an agent can no longer delete or forge mokata's own run record.

It also adds `mokata ide`, a read-only review window for what your sessions changed. An independent
verification pass ran against the built release, and what it found is fixed. **Several exit
criteria are not met, or not yet demonstrated live** — *Known limitations* names each one rather
than rounding it up.

Two refusals are new and you may meet them: a review recorded as passed while it raised a Blocking
finding is refused, and in a session running a pipeline a shell command mokata cannot read is
refused.

---

## One run, from registration to ship

**A run now has a finish.** `run_finished` is the ship-completion event, recorded by
`mokata progress finish --landing merged|pr|kept|discarded` or the `run_finish` MCP tool, and the
ship skill records it. It stamps `completed_at`, retires the run exactly as entering the ship stage
does, and lands in the typed stream as `PhaseTransition(ship, finish)`. It is **never recorded
run-less**: with no run, or with several and none identifiably yours, it is refused
(`FinishRefused`, CLI exit 1) and nothing is written. The new `progress_mark` MCP tool is the twin
of `mokata progress mark`. The old `STAGE_PASS` event, defined since 0.0.16 and written by nothing,
is deleted.

**A conversational run registers, and the gate waits for it.** When a session asks for
`/mokata:brainstorm` — typed, or through the model's own `Skill` call — mokata records a claim for
that Claude Code session. Until the run registers, an implementation write in that session is
blocked as `run-registration`, and the message names the way out. A new PostToolUse hook,
`run-bind`, then binds the session to the run id the MCP server itself returned, so there is one
identity, not two. `run-registration` is not overridable.

**Parallel runs each get their own gate.** A bound session is policed by exactly its own run,
whatever else the repository holds. Measured: two sessions bound to two runs in one repository got
two different verdicts (`spec-persisted` and `no-code-without-failing-test`); before this release
the gate failed open for both. A session that never asked for a pipeline has no claim, and
hand-editing stays unpoliced, as before.

## Review, measured

Every run-state gate block is now recorded as a `gate_block` event against the run whose state
blocked — and as `GateDecision(block)` in the typed stream, where no gate block had ever been
evented. A retried write counts once. The review verdict carries its Blocking-finding count
(`mokata progress record-review --blocking N`, or `blocking=` on MCP), and

    mokata progress review-status --caught

prints what review caught against what the gates caught in the loop, for the run and across runs
(`caught` on the `review_status` MCP tool). **A verdict without its count reads *not measured*,
never 0** — and "no review verdict", "nothing caught" and a percentage are three different lines.
The figure is informational: it never changes ship's exit code, and without `--caught` the output is
byte-identical.

Two rules tighten what a verdict can say. **A verdict cannot contradict its own Blocking count:** a
PASS that raised a Blocking finding and a FAIL that names none are refused, on the library, the CLI
and MCP alike — the one practice adopted from a survey of three review guides (Conventional
Comments, Google's engineering practices, and Anthropic's `code-review` plugin, the last for ideas
only because it carries no licence statement). And **an unobserved "independent" review is credited
as inline.** `independent=true` used to be a claim nothing checked; ship now derives `observed` — a
subagent of the run finished at or before the verdict — and a claim nobody observed ships with
*"review passed (inline — independence claimed, but no subagent of this run was observed)"*.

## The gates hold where the agent writes

**A shell write meets the same gates as a `Write`.** The run-state gates used to return before
reading a Bash command, so `sed -i … src/app.py` passed where a `Write` to the same file was
blocked. Every destination mokata's parser sees now gets the same verdict and the same message —
bare file names and a literal `cd` included — and `touch` counts as a write.

**What mokata cannot read is refused while a run is in progress.** In a session with an open or
bound claim, a command that may write somewhere mokata cannot place is refused as `bash-unreadable`,
with the reason and the way out: use Write/Edit, or name the file literally. The refusal counts as
an in-loop catch. Refused: the eight parser blind spots, plus `find -delete`, `| sh`, `awk` writing
and `cd "$D"`. **Reads are never refused.** A session in no run sees nothing new.

**mokata's own run record is not agent-writable, in any session.** An agent tool call that would
write, delete or move `.mokata/temp_local/state/` is blocked as `own-record` — `rm -rf .mokata`,
`git clean -fdx`, an appended event, and the same through a variable, a glob, braces or another
interpreter. Deleting that state removed the run and opened every run-state gate; appending a
`subagent_finished` event could forge an observed independent review. Reading it stays allowed.

**Containment is on for every gated write the agent makes.** With no workspace supplied, the write
gate uses its own two — the process's working directory and the repository its ledger lives in —
and refuses a write outside both. A destination **you** typed at the CLI is not contained, so
`--out ~/elsewhere.json` still works. One named allowance covers writes to a harness's
configuration, and nothing else. `mokata setup`'s own config writes (`.mcp.json`, `settings.json`,
`~/.claude.json`) now pass SELF-PROTECT and containment first; a refusal raises `ConfigWriteRefused`
and writes nothing.

## Subagents work under a contract

New SubagentStart and SubagentStop hooks give each subagent started in a pipeline session a
contract: its session's own run, its type, whether it is read-only, and a depth limit.

    settings.subagents.read_only_types       default ["Explore", "Plan"]
    settings.subagents.max_recursion_depth   default 1, range 0..3 (0 = no subagents in a run)

The gate-guard enforces the contract on the subagent's own calls — a read-only subagent's write is
blocked, a delete or a tree-rewriting git command counts as a write, and so does a spawn past the
depth — and every refusal is ledgered as `subagent_contract_unhonored`. A subagent is asked to end
with a **`HANDBACK` report** (status · summary · files · tests · open). With no readable report it is
sent back **once**, then let go with the gap ledgered — never a loop. A Claude Code that omits the
final message has that recorded as the harness's own gap. A session in no run sees none of this.

## Context that survives compaction, on one budget

**A gate's position survives compaction.** The gates were never at risk — every one reads on-disk
state — but the model was: after a summary it was not told which run it drives or what would decide
its next write. A `PreCompact` hook now writes an anchor of the session's run position, and on
SessionStart `compact` or `resume` the briefing carries a re-anchor block for **this session**: the
run, the last stage, whether an approach, a spec and a failing test are on record, **the verdict the
gate gives your next implementation write**, the overrides in force, and the rule to decide from the
record, not the summary. It says whether anything moved since the anchor, or that no anchor was
recorded.

**The briefing shows the repository now.** A *"Repo now (live)"* block carries the branch,
ahead/behind and changed files from one bounded `git status` (1.5 s timeout), the configured test
command — or *"no test command set — mokata will not guess"* — and which of `CLAUDE.md`, `AGENTS.md`
and `.claude/rules/` exist. At most 6 lines, and over budget it is dropped first, with a notice. A
**working set** per run derives every reference from its owner and persists only the files the
session touched and its notes; read it with `mokata progress working-set` or the `working_set` MCP
tool.

**One context budget: `settings.context`.** `briefing_tokens` (default 2000), `turn_tokens` (300),
`briefing_rules_lines` (12), `turn_rules_lines` (4), `turn_top_k` (5) and `live_lines` (6). The
defaults are the constants that shipped before. A bad value is refused, not clamped: the default is
used and the briefing names the key and the fix. The re-anchor and the MCP reachability warning are
now counted **inside** the budget; they used to be appended after it was checked. **Output density
is on by default** (`settings.governance.output_density: false` turns it off).

**Session notes, accepted by a person.** Stop, PreCompact and SessionEnd each rewrite one draft note
per pipeline session from mokata's record — the stage reached, files touched, in-loop catches,
handbacks, the review verdict, and the model's last message, truncated — bounded to 900 characters,
with no model called inside a hook. **A draft is not a memory.** `mokata memory harvest` lists the
drafts; `--approve <id>` saves one through the human gate (it asks, scans for secrets and ledgers
the write) and `--reject <id>` drops it. A decided draft is not proposed again unless the session
did more.

## `mokata ide`: a read-only review window

    pip install "mokata[ide]"
    mokata ide            # or /mokata:review-window from inside a Claude Code session

It shows the **live diff** of the working tree against `HEAD`, uncommitted and untracked files
included; the diff at **any point in time**, from per-write snapshots mokata keeps under
`.mokata/temp_local/snapshots/` (never in your `.git`); what each **session** changed, one row per
session with its run; a **branch compare**; and a **GitHub PR by URL**, which refuses a foreign
`origin` before any fetch and fetches with your own git login, storing no token. `/mokata:review-window` opens
the window, or re-focuses the running one on the session that asked — one server per repository.
Started from a session, the server exits after 30 idle minutes; `--foreground` keeps it in the
terminal.

Every section has an empty state, and `mokata ide --demo` serves a SAMPLE dataset — every payload
carries `sample: true` and the page shows a SAMPLE banner — so you can review the UI without the
sample ever passing for real data.

It only reads, and it is still built as though it could leak: **loopback only**, with no bind
option; a per-launch token exchanged at `/pair` for an HttpOnly, SameSite=Strict cookie; Host checked
on every request and Origin on every POST; a fixed static file set under a strict CSP that forbids
framing; realpath containment on every read, with `.mokata/` excluded; and repository text inserted
with `textContent` only. The `[ide]` extra declares `starlette`, `uvicorn` and `sse-starlette`, which
already arrive with mokata's `mcp` dependency.

## Claude Code 2.1.0 is the minimum

Every enforcement mokata runs inside Claude Code rides a hook event, and the newest it depends on —
tool hooks firing inside subagents — arrived in **2.1.0**. Below it nothing fails loudly: the hook
that would have fired simply does not. So `mokata doctor` raises `claude-code-too-old` and names each
feature that is off, and stays silent when Claude Code is absent or new enough.

## Also fixed

- **One subagent is one finish, even when mokata's hooks are wired twice.** With the plugin
  installed and `mokata init`'s project hooks also in place, Claude Code runs each hook twice; a
  subagent's finish was recorded twice (measured in a live headless session). It is now recorded
  once. Gate blocks were already counted once per blocked write.

- **Slash commands that only existed as commands were invisible.** In current Claude Code, a plugin
  command file named `skill.md` stops the plugin's other command files from registering, so
  `/mokata:version`, `/mokata:tour`, `/mokata:menu` and the rest answered *Unknown command*. The
  authoring command is now **`/mokata:skill-author`**, and the review window's is
  **`/mokata:review-window`** (it can no longer meet Claude Code's own `/ide`).

**What the independent verification found.** In two passes, 21 and then 23 shell shapes wrote a
source file while a `Write` to it was blocked — grouping, functions, command and process
substitution, wrappers such as `timeout` and `bash -c`, `git checkout` and its relatives, `patch`,
`sort -o`, `curl -o`, inline Python, Perl and Deno — and 24 read-only commands were refused during a
run. All are fixed, each pinned by the verifier's own shape. The verification also produced the
`own-record` guard above; made the re-anchor's next-write line the gate's own verdict (a hand-built
ladder had an open spec amendment, an override and a finished run wrong); kept the re-anchor whole
at `briefing_tokens` 200 to 700, where it had been cut away; stopped crediting a block in an
unclaimed session to another run's in-loop count; and kept outside paths out of the working set. In
`mokata ide` it closed a symlink out of the repository or to a device, a swapped-in symlink on a
point-in-time diff, a FIFO that could hang the reader, a substring match on the PR `origin`
(`acme/widgets-evil`, `github.com.evil.com`), a ref that could be read as a git option, an
unauthenticated launcher call, and a page that could be framed; on Linux and macOS a launch now takes
a per-repository lock, and switching between `--demo` and real data leaves one server. And the
session harvest stopped re-proposing a decided draft after a chat-only turn.

**The event publisher keeps its own high-water mark.** It used to infer where it left off from the
team's table. Now the mark is this machine's own, per (namespace, DSN), keyed by a SHA-256 of the
pair so **the DSN never reaches disk**, and anchored so a rebuilt `events.db` starts over. A missing
or unreadable mark resumes from the start and says so. ⚠ **The first publish after upgrading
re-sends from the start**; the team table de-duplicates it, so nothing is skipped or doubled.

**On Windows**, MCP `duration_ms` was measured with a ~15.6 ms clock and now uses
`time.perf_counter`, and git and gh output is decoded as UTF-8 rather than with the locale.

## For contributors

None of this changes the published package. **A release re-run converges from the remotes:**
`release.sh` records a `mokata/release-preflight` commit status once the preflights pass at a pushed
`HEAD`, and a re-entry at that commit skips them and every confirm whose outcome is already published
— with no skip flag and no override. A closed PR's reason is read from GitHub rather than inferred,
the mirror sync fast-forwards behind a typed yes instead of offering only a branch delete, and
`mokata branch-protection-check` needs a fresh cached document to pass degraded (exits 4–7 are new
refusals). A blocking `macos-latest` leg runs on the public mirror; `scripts/internal_suite.py`
records the internal-tooling suite's local gate (62 classes, 291 tests), refusing on any failure or
skip; a weekly seed job searches test order on the mirror; one shared comment-stripping reader
replaces ten copies; and the mutant drivers refuse a malformed window, an existing snapshot, an
over-claiming header and a label that runs a command.

---

## Re-scheduled

The v0.0.21 notes published three commitments naming **0.0.22**, each with a tracking key.
`B-LIFE-FU` and `REVIEW-LIB-SWEEP` shipped. The third shipped in part:

- **The CI ceiling re-derived from the public mirror.** The v0.0.21 notes said it was, with the
  internal-tooling suite's recorded local gate, re-scheduled to **0.0.22** (tracked as `INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER`).
  The gate shipped; no stage of this release built the ceiling half. **These notes name no new
  release for it.** Contributor-facing.

## Not in this release

Planned work that did not ship. None was promised in published notes. **These notes name no release
for any of them**: each is tracked, and none has work booked against a release yet.

- **A cross-turn read cache** (`ReadCache`). Not built, on evidence: it saves tokens only if a
  PostToolUse hook can replace what the model sees, and Claude Code is reported to ignore a hook's
  replacement output for its built-in tools.
- **The editing half of `mokata ide`.** This release's slice is read-only by decision. The editor,
  its go/no-go spikes, the approval-channel threat model and the question to Anthropic about
  `claude -p` move with the editing work.
- **Two Windows-only test skips** that need a Windows host to grade. Contributor-facing.

---

## Known limitations

Everything below is shipped as-is, with the cost stated.

**After a run ships, its spec scope keeps applying.** A session bound to a finished run is still
policed by that run — and in a repository holding a single run, every session is — so a write
outside the shipped spec's scope is refused as `spec-scope`. **Start a new `/mokata:brainstorm` for
further work**: it opens a new claim and binds the session to the new run. This is deliberate:
letting a finished run release the gates would let an agent end a run to escape them.

**Read-only subagents are not judged on MCP tool calls.** The gate-guard matcher covers
`Write|Edit|MultiEdit|NotebookEdit|Bash|Skill|Agent|Task`, so a third-party MCP server's write tool
can write from an `Explore` subagent.

**The shell lane reads commands; it does not run them.** A program mokata runs by design —
`python script.py`, `make`, `npm test` — is not inspected, because policing it would mean refusing
to run the tests; inline code is in scope, because there the agent is the author. A command mokata
cannot read is refused while a run is in progress, so a false refusal costs one rephrase. The parser
remains a heuristic over a Turing-complete language.

**An agent id outside `^[A-Za-z0-9_-]{1,80}$` gets no subagent contract**, so that subagent is not
policed as read-only and has no depth limit.

**A conversational run is bound only when a pipeline was asked for by name.** A brainstorm protocol
followed in chat with no `/mokata:brainstorm` and no `Skill` call opens no claim, and the gate still
reports *"no active mokata run — not policed"*. The `Skill` tool's input field name is not
documented; mokata reads `skill`, `command` and `name`, and an unreadable shape records no intent —
the old fail-open, never a block on a guess. **Edits made before any run is registered** are still
invisible to graph freshness's in-harness signal: the query process cannot see Claude Code's
session id (harness gap #25642), and closing it on mokata's side would mean reversing per-session
partitioning.

**`ReadCache` is blocked by the harness**, so per-turn reads are not de-duplicated (see *Not in this
release*).

**`mokata ide`'s bounds.** On Windows there is no launch lock (no `flock`), so two simultaneous
`/mokata:review-window` can start two servers. The snapshot store's first-write index is not locked under
concurrent writes, and pruning — once the timeline passes 22,000 entries it keeps the newest
20,000 — can drop a session's BEFORE. Shell writes are not snapshotted, though the live diff still
shows them. The window has not yet been run on a real Mac.

**The new hook surfaces have not been checked in a live Claude Code session.** The re-anchor after a
real `/compact`, the subagent contract with a real subagent, the shell lane, the session harvest and
`/mokata:review-window` are tested by driving the real hook entry points with the documented payloads. That
is not the same evidence as a session.

**This release's exit criteria are not all met, and the ones that are not are listed here rather than
rounded up.**

1. **A release re-run converging live is not yet demonstrated.** The mechanism is built and graded
   against real git, local bare remotes and a stand-in for `gh`; four GitHub behaviours it relies on
   are owed to the first real run, and this cut is that run.
2. **The events programme's last three clauses are built; one has a residue.** A ship-completion
   event exists, and review has a caught-at-review rate. A conversational run registers so the phase
   gate binds — when the pipeline was asked for by name (above).
3. **The `macos-latest` leg exists and blocks; it has not yet been seen to red** when the two
   host-dependent fixes of 2026-10-04 are reverted. That reading needs a push to the mirror.
4. **Order-independence still stands at nine seeds of ten.** The tenth was run and its result could
   not be read: the tool takes the first test-count line and sums every failure line, so a failing
   test that embeds a whole inner run in its message pollutes the parse. Filed, not fixed. The
   weekly seed job meets the same defect whenever a nested-suite test fails.
5. **Context discipline: two of four.** One context budget, with no second channel, is met and
   tested. A gate surviving compaction is built and not yet checked in a live session. **Per-turn
   context growth against a 0.0.21 baseline was not measured**, and no pass checked that every
   degraded state names its remedy.
6. **The IDE criteria were written for an editing IDE and are not met as written.** The slice was
   re-scoped to a read-only window on 2026-10-11. Its server-stack decision is recorded and not
   signed; no performance medians were measured; the spikes moved with the editing work; no
   400,000-line file was opened; there is no bundle to reproduce, because the page is hand-written
   static files. The security cases are tested.
7. **CI did not run on the development repository during this release**, by the same decision as
   0.0.21: its Actions minutes were not restored, so that criterion is **not meetable**. The public
   mirror's CI runs on every push and every PR.

**Graph freshness: two classes of edit are still not detected before the answer**, unchanged from
0.0.21. A source file in no baseline that git will not name — a `.ts` file under an ignored
directory is the shape — on every platform; and, on Windows only, a same-size content write that
restores the mtime (`tar -xp`, `cp -p`, `rsync --times`, `touch -r`), because `st_ctime` there is the
creation time. **On Windows, rebuild the code graph yourself after a restore or a vendor drop.** The
Windows class is now pinned by a test that reds if it ever closes. The bounds are unchanged: at most
**500** drifted paths hashed per query, at most **5000** deletion records kept.

**The token estimate has still been measured once.** One live session: **415** API responses, **5**
calibratable, real output tokens at **1.552×** the `chars/4` estimate — an under-count of about 55%
if it holds. The budgets are unchanged. Five responses from one session is not a distribution.

**Carried, unchanged.** TypeScript is an optional extra (`pip install 'mokata[graph-ts]'`), and
**plain JavaScript is not covered at all**. The PostgreSQL floor is enforced and one dimension of
its evidence is manual — no live leg has run against a below-floor server, and PostgreSQL 16.13 is
not a PostgreSQL 14. The embedder still costs ordering (**−0.81pp** MRR@10 at 100,000 items). The
Windows notification sound is called, not verified, and the Windows toast does not exist. On Linux,
an MCP gated write with no audio player makes no sound (`settings.ux.notify_audio false` stops it
asking). `LISTEN`/`NOTIFY` does not survive a transaction-mode pooler; `mokata events` prints which
state your DSN is in.

**The branch-protection degraded pass is bounded, not closed.** Inside its 24 h window, a protection
detail weakened while protection stays on is invisible — only an administrator can make that
change, and the window is printed. And a fresh machine cannot cut during a protection-endpoint
outage until one full read has succeeded. Maintainer-facing; it does not affect the published
package.

**The PR gate still takes 26–31 minutes against a target of under 10, and was not measured again.**
The figures stand from the **0.0.18** cut, with the unit suite at **91%** of the binding leg. This
release added tests again, so the number has not fallen. Contributor-facing only.

---

## Upgrading

    pip install -U mokata
    mokata upgrade

`mokata upgrade` is the part that matters: it refreshes the commands, skills and hooks in your
checkout to match the installed version — and this release wires new ones (`PreCompact`,
`SubagentStart`, `SubagentStop`, `Stop`, `SessionEnd`, and the PostToolUse `run-bind`). `mokata
doctor` will tell you if anything is out of step, including a Claude Code older than **2.1.0**
(`claude update`).

**For the review window**, install the extra: `pip install "mokata[ide]"`. Without it, `mokata ide`
prints that install line.

**If you use team mode**, the first `mokata events --share` after upgrading re-sends your local
events from the start, because the publisher now keeps its own mark; the team table de-duplicates
them. Nothing else in team mode changes.

**If you run a pipeline**, two refusals are new: a passed review that raised a Blocking finding,
and, during a run, a shell command mokata cannot read. Each names its way out.
