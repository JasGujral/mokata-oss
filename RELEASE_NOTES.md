mokata **0.0.23**. Set up or upgrade with the same instruction, in your repo:
`uv tool install --upgrade mokata` (or `pipx install --force mokata`), then `mokata setup claude`. Requires **Python ≥ 3.10**, and for the Claude Code integration
**Claude Code ≥ 2.1.0**. The plugin route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.23 — "Install it in two commands, read your code and not your venv, and navigate what changed."

0.0.22 made a run keep one identity and its gates hold wherever the agent writes. **0.0.23 is about
what a stranger meets first.** The plugin now stands alone: its MCP server starts through `uvx` with
no pip step, each hook fires once when the plugin and `mokata init` are both wired, and no project
command is shadowed by a Claude Code built-in. A Python repository is read as your code — not your
virtualenv's — and its tests run in its own Python. `mokata ide` grows from a review window into a
place to navigate: changed files, symbols, search by word or by meaning, the call graph, and the
plan and spec behind a run.

It also builds the machinery the "Proof" programme needs — a gate enforcement ladder, code anchors
on acceptance criteria, a spec-quality battery, typed gate evidence, and an eval harness kept outside
the product. **None of the proof measurements has been run**: the harness exists, its numbers do
not. **Several exit criteria are not met, or not yet demonstrated live** — *Known limitations* names
each one rather than rounding it up.

Three things you may meet:

- **Five project-scope commands are renamed** to `/mokata-review`, `/mokata-init`, `/mokata-bug`,
  `/mokata-resume` and `/mokata-upgrade`, because Claude Code's own `/review`, `/init`, `/bug`,
  `/resume` and `/upgrade` shadowed them. The plugin's `/mokata:review` and the rest are unchanged.
- **The plugin's MCP server needs `uv`.** It is launched as `uvx --from mokata==0.0.23 mokata-mcp`;
  without `uv` it does not start, and `mokata doctor` says so (`plugin-mcp-unresolvable`).
- **Spec emit has a new refusal, and doctor has new warnings.** `mokata spec emit` and `spec_emit`
  refuse a spec the new spec-quality battery finds structurally broken or untestable; its
  approach-coherence findings are advisory in this release. `mokata doctor` warns about an
  in-project or shadowed mokata install and about stale worktrees; none changes its exit code.

---

## Install it in two commands

Inside Claude Code:

    /plugin marketplace add JasGujral/mokata-oss
    /plugin install mokata@mostack

That is the whole install. The plugin starts its MCP server as
`uvx --from mokata==<plugin version> mokata-mcp`, so there is **no prior pip step** — you only need
`uv` on your PATH. The pin is a guarded version field (`plugin.json:mcp-pin`): `release-check`
refuses a plugin that would start a different mokata than its own version.

**Each hook fires once.** With the plugin installed and `mokata init`'s project hooks also in place,
Claude Code ran every hook twice. Every plugin hook now carries `--via-plugin` and stands down when
`settings.json` wires the same hook *and that hook resolves* — a dead settings hook never silences
the plugin's, because a gate that silently does not run is worse than a duplicate.

**No command is shadowed.** Project-scope commands that collide with Claude Code built-ins are
written as `mokata-<name>.md`. Re-running `mokata setup claude` (which `mokata upgrade` runs) removes
mokata's own renamed or retired command files — those marked `description: mokata ·` — and never a
file of yours.

**The install docs lead with one instruction that both sets mokata up and updates it** —
`uv tool install --upgrade mokata` (or `pipx install --force mokata`), then `mokata setup claude`,
which runs `mokata init` for you on a new repo and only refreshes the wiring on an existing one.
The plugin is the documented alternative, and plain `pip` comes last, with the reason: inside a project's virtualenv it mixes mokata's dependencies into yours. `mokata doctor` and
`mokata setup claude` now warn when mokata is installed inside the project's own virtualenv
(`install-in-project-env`) or when another `mokata` on PATH is a different version
(`install-shadowed`). A source checkout of mokata itself does not warn.

## Your code, not your virtualenv

**Virtualenvs were read as source.** Only `node_modules` was pruned, so `venv/`, `env/`, any-named
venvs and in-repo conda environments filled the graph, the index, the IDE tree and search with
third-party code, and `defs` and `callers` named it. Environments are now pruned by **what they are,
not what they are called** — any directory carrying `pyvenv.cfg` or `conda-meta/`, plus
`__pypackages__` and `site-packages`. A real package named `env/` with no marker is still read.

**That fixes the blast-radius timeouts that broke `spec amend`.** The grep floor re-read the whole
tree for every symbol at every hop; on a repository whose virtualenv was walked as source, the amend
impact lens refused every widening as `blast-radius-timeout`. The corpus is now read **once per
query**, with an identical answer, and an acceptance criterion handed to the lens costs no walk.
**Measured:** 300 source files beside a 15,000-file un-dotted virtualenv — 0.0.22 did not answer in
300 s; 0.0.23 answers in 0.1 s.

**Tests run in the project's Python, activated or not.** The baseline and TDD gates used to inherit
mokata's own environment — from the MCP server, a hook or the IDE, whatever Claude Code was launched
with — so `pytest` was missing or ran without your dependencies, and the baseline read a false RED.
The environment is now resolved in order:

    settings.python  >  $VIRTUAL_ENV / $CONDA_PREFIX  >  an in-repo env found by its marker
                     >  poetry / pipenv  >  the system Python

**and the one used is named**, in the baseline report and in a `mokata doctor` info line
(`project-python`).

## `mokata ide`: navigate, search, see the graph

    pip install "mokata[ide]"
    mokata ide            # or /mokata:review-window from inside a Claude Code session

- **Navigate.** A changed-files navigator — `]` / `[` for the next or previous file, `j` / `k` for
  the next or previous hunk, unseen-since-last-look marks — ⌘P over changed files, files and
  symbols, an outline, and defs / callers / callees / refs / implementers / blast radius / history
  for any symbol, each with a `basis` badge. **Every answer is the graph's own:** the IDE returns
  the same bytes as `mokata query … --json`, which is new. ⌘P ranks with `rapidfuzz` when it is
  installed and with a pure-Python floor otherwise, and says which.
- **Search** by word or regex (`rg` when present, a labelled pure-Python floor otherwise), and by
  **Meaning**: semantic code search on mokata's own local embeddings
  (`pip install 'mokata[embeddings]'`) — no code-review-graph, no API key. Without the extra it
  says "unavailable" and shows nothing. The model is never downloaded on its own; one **Fetch**
  button is the one network call you start.
- **Graph.** A blast-radius ripple, a symbol focus view, a call-path tracer and an untested-path
  finder, on **vendored** Cytoscape.js (MIT, licences and a sha256 per file shipped) — no CDN, it
  renders offline. Every edge shows its `basis`; a lexical edge is drawn dashed and never claims
  coverage. Sub-graphs are capped at **2,000** nodes, past which the answer says `truncated`.
- **Plan.** The approved approach and the spec for a run, every archived spec version, an open
  amendment, and a lifecycle strip from brainstorm to ship with gate blocks and amendments on their
  stage. In team mode it reads the team store; an unreachable store says so and never shows local
  data in its place.
- Every chooser is a searchable, keyboard-driven combobox, each column scrolls on its own, and the
  view you left — mode, panel, file, line — comes back per repository.

**0.0.22's window bounds are closed:** the launch takes a per-repository lock on Windows too, so two
launches start one server; stopping a server no longer probes it with `os.kill(pid, 0)`; the
first-write snapshot index is locked; and pruning keeps every BEFORE a surviving session diff needs.

## The machinery for proof

**A gate enforcement ladder.** A gate declares `enforcement`: `advisory`, `soft` or `hard`. An
advisory gate never blocks and ledgers what it would have blocked (`gate_advisory`); soft blocks
with a ledgered override; hard refuses. **All 9 backed gates are hard**, and an omitted level
defaults to hard, so a missing field can never be how a gate stops blocking. Promotion is
proposal-only, through the human gate: an advisory gate with a clean streak (default **5**
evaluations that would not have blocked) is proposed, never promoted on its own; an approved
promotion lands on **soft** (it blocks, and a human override gets through and is ledgered), never
straight to hard. The four Karpathy gates, which never blocked, are labelled `advisory`.

**A spec-quality battery before consent.** `mokata spec emit` and `spec_emit` judge the spec last,
after every earlier refusal: structure, untestable criteria, consistency with the approved approach
and its deferred decisions, overlap with saved specs and decisions, unresolved anchors and
failure-mode coverage. Revise is counted per run; after **3** cycles it escalates. Approach
coherence, overlap, anchors and failure-mode coverage are advisory in this release, so nothing is
refused or sent back to brainstorm on them; switched on, an approach-rooted finding regresses the
run (`spec_regress`, ledgered).

**Acceptance criteria carry code anchors** — up to **5** each, from the approach's decisions, its
impact lens and the spec scope. An anchor that does not resolve is a warning, never a block, and a
spec without anchors is byte-identical. **Spec history is readable:** `mokata spec show --versions`
and `--version N` (MCP `spec_show(version=, list_versions=)`), and every answer says which version it
is and whether it is superseded. The spec skill now asks one question — refine first, or
standalone? — before drafting with no approved direction on record.

**Develop turns carry live evidence.** In a bound run's develop stage, each turn re-checks the
approach's consumer list against a live blast radius (MISSING / STALE), names decisions recorded on
the files you touched, and lists guard clauses in touched Python files — inside the 300-token turn
budget, ahead of recalled memory. A blast radius answered from the grep floor now **says it is
degraded**, with the remedy (`mokata query` prints a `⚠` line; MCP `query` adds `impact_degraded`).

**Typed evidence per gate outcome.** A gate decision carries `artifacts[]` and `tested[]`; the
completeness gate records one check per acceptance criterion under
`.mokata/temp_local/evidence/<run>/`. It sits beside the verdict and never changes it.

**The eval harness lives outside the product.** `bench/` is excluded from the wheel, and a new
`NO-API-KEY-GUARD` test sweeps `src/mokata/` for LLM SDK imports, API-key names, credential reads,
`claude -p` spawns and provider hosts — allow-list empty, baseline 0 findings. mokata itself still
never needs an API key.

## Also new

- **Intentions: memory that comes due.** `kind=intention` records a commitment with a due date, a
  keyword or a path, checked at SessionStart and in the healing sweep — no daemon — and the briefing
  says how many are due. Approve one to archive it; defer to let it resurface. Create one through MCP
  `remember` (`due_at`, `when_keyword`, `when_path`), behind the same human gate. No schema change.
- **`mokata suggest "<intent>"`** recommends pipeline skills from plain words ("flaky test" →
  debug) with a fixed, whole-word table; an unmatched intent returns nothing rather than a guess.
- **Worktree housekeeping.** `mokata worktree clean [--yes]` previews, then removes worktrees that
  are merged, clean and idle, and prunes vanished ones; `mokata worktree remove <path|label|branch>
  [--force]` refuses the main checkout, a live session and a locked worktree, and dirty or unmerged
  work without `--force`. Branches are never deleted. MCP `worktree_clean` / `worktree_remove` are
  propose-only. The briefing names new worktrees once, and `mokata doctor` raises
  `worktree-prunable`, `worktree-merged`, `worktree-registry` and an info `worktree-idle`.
- **`mokata` on its own is a home screen** — version, mode, profile, the active run and stage,
  approvals waiting on you, whether the gate hook resolves, and what to run next.
- **A cache-prefix notice:** when mokata's stable briefing prefix changes mid-session, one line says
  the next briefing will miss the prompt cache once, and names the segment.
- **MCP failures carry `remedyCommand`**, list reads carry a `count`, an empty list says why, and
  every curated skill has an *If this appears stuck* section with a runnable command.
- **A certification rig for `mokata-mcp`** on Cursor, Codex CLI, Gemini CLI and Copilot CLI — six
  criteria, a stdio contract suite, an owner checklist and a matrix. It certifies nothing yet.

## Also fixed

- **`spec_amend` in a resumed session picked the wrong run.** With two or more pipelines in one
  repository, the MCP server could not tell which Claude session it served, fell back to its own id
  and refused with `spec-persisted` — the symptom after `/resume`. The hooks now record each session
  against its Claude process, and the server walks its own process ancestry to find the session it
  serves, refusing a recycled pid, a shared ancestor (tmux, an IDE host) or a stale writer.
- **Hooks fire once, and never fewer.** A plugin hook stands down only for a `settings.json` hook on
  the same event whose tool matcher covers the plugin's and that resolves — an older, narrower
  wiring never switches a gate off.
- **On Windows the session registry's liveness probe no longer ends the process it asks about.**
- **An un-ignored venv no longer slows every graph query** (git's untracked list is filtered too).
- **Events carry the run's real stage.** `gate_block` and `subagent_finished` said `develop`
  whatever the stage.
- **An unknown subcommand's error prints to stdout**, with the remedy.

## For contributors

None of this changes the published package. **The release waits are three-state:** a dropped CI
watch reads the run's conclusion, so a network reset is UNREADABLE (exit 4, retried) and never a
red, and a release held at the `pypi` approval is PENDING (exit 3, with the approve URL) — re-running
the same command finishes the tail. **precut runs the whole integration suite**, as `release.sh`
does. A lint makes **new tests meet Windows first** (`os.kill(x, 0)`, unguarded `mkfifo`, `HOME`
without `USERPROFILE`). CONTRIBUTING explains the plugin cache (`claude --plugin-dir <checkout>`).
The G2 harness (`python -m bench.g2`: plan, run, validate, study, page, continuity, lean, audit) and
the LOCOMO harness (`python -m bench.locomo`) live in `bench/`; the only key either reads is
`MOKATA_G2_JUDGE_API_KEY`, for an opt-in API judge.

---

## Re-scheduled

No published commitment named **0.0.23**, so nothing slipped from this release. One item from an
earlier release's notes is still open, and is restated so it is not lost:

- **The CI ceiling re-derived from the public mirror.** The v0.0.21 notes said it was, with the
  internal-tooling suite's recorded local gate, re-scheduled to **0.0.22** (tracked as `INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER`).
  The gate shipped at 0.0.22; no stage of 0.0.22 or 0.0.23 built the ceiling half. **These notes
  name no new release for it.** Contributor-facing.

## Not in this release

Planned work that did not ship. None was promised in published notes. **These notes name no release
for any of them**: each is tracked, and none has work booked against a release yet.

- **Any real run of the proof engine** — no G2 run, no proof page, no audit report, no LOCOMO
  number. Each needs paid runs on the owner's account.
- **A lean MCP surface.** `settings.surface.lean` is an unadopted stub in `bench/`; nothing in the
  product reads it.
- **`mokata setup` writing MCP config for Codex CLI and Copilot CLI.**
- **A `mokata search` CLI**, retired by ruling.
- **The rest of the IDE's px0 parity rows:** a file-system watcher, explorer/`.gitignore` parity,
  and a crash-safe restart beyond the existing stale-record handling.
- **The editing half of `mokata ide`** and **a cross-turn read cache** (`ReadCache`), as at 0.0.22.
- **The community-marketplace and awesome-claude-code submissions**, which need the owner's
  credentials.
- **Two Windows-only test skips** that need a Windows host to grade. Contributor-facing.

---

## Known limitations

Everything below is shipped as-is, with the cost stated.

**The proof machinery is built and has not been run, so there are no proof numbers.** The G2 eval
harness, the public proof page, context continuity (CTX-CONT), the lean-MCP comparison (AXI-LEAN)
and PIPE-TOKEN-AUDIT exist and are tested against fixtures; **no real G2 run has been made, and every
counter is unmeasured.** The proof page refuses to render without a complete real run; the audit
renders only a template marked "FIXTURE RUN — NOT THE AUDIT"; continuity will report *UNTESTED* until
a seeded task long enough to compact exists; lean adopts nothing even on a win. The `claude` CLI
flags and stream-json shapes the harness relies on were written from the documented interface and
not exercised against a live binary; headless runs leave mokata's human-gated writes unanswered, so
G2 measures mokata with its gates waiting; and the four tasks are starter tasks the owner has not
ratified.

**`mokata-mcp` is certified on no client.** The rig passes all six criteria locally for Cursor and
Gemini, and C2–C6 for Codex and Copilot — the server half only; no real client was run and every
cell of the matrix is `untested`. C1 cannot pass for Codex CLI or Copilot CLI, because `mokata setup`
writes no MCP config for them; the checklist gives the snippets to verify by hand. The server's
`initialize` reports the MCP SDK's version (1.30.0) rather than mokata's, and `mokata setup` writes a
bare `mokata-mcp` that depends on PATH.

**The LOCOMO benchmark cannot publish.** Its stop condition refuses publication while a ranker it
measures is disclosed as regressed — arm D's MRR divergence (**−2.75pp** at 100,000 items) is open,
and the vector tier's **−0.81pp** was retired unfixed — and the field figure it would be compared
against is recorded as unverified. No run has been made.

**Spec-quality failure-mode coverage and approach coherence are advisory in this release, by
decision.** Enforcing failure-mode coverage refused
about 60 existing emit paths across 7 suites, so it ships computed, returned and ledgered on every
verdict, behind `ENFORCE_FAILURE_MODES` = off. The "rules" member is overlap with saved specs and
decisions only, and the battery does not run on `run_pipeline`'s emit.

**An earned gate promotion lands on `soft`, not `hard`.** A promoted gate can still be overridden
by a human, ledgered; hard remains a declared level only. No gate
ships `advisory` except the Karpathy four, so nothing has been promoted. `advisory_gate_report` is
not yet wired into `mokata govern`. The gate hook's gates are not routed through the ladder and are
pinned hard by a test; MCP `spec_emit` and spec amend reach the completeness gate without a ledger,
so they leave no `rule_block` entry and persist no evidence. In 26 shipped SKILL.md files
`(advisory)` still means *prose-only*, not the enforcement level.

**Binding a resumed session to its run** needs the resumed session to keep its session id: if
Claude Code gives it a new one, nothing is bound and resolution falls back exactly as before. It has
**not been run on real Windows**; the first turn after the MCP server starts or restarts falls back
until the next prompt refreshes the record; on macOS each resolution costs one `ps` call, roughly
20–50 ms, uncached; and when a sibling Claude under the same tmux or IDE ancestor wrote and exited
while yours has no mokata hooks, the shared ancestor can be accepted.

**`mokata ide`'s new bounds.** **Meaning** search has not ranked with the real model here — the model
host was unreachable from the build sandbox — only with a deterministic stand-in. The **team-mode**
plan viewer was tested with a fake transport carrying a real bundle, never a live PostgreSQL, and a
team bundle carries the current spec only. ⌘P over 100,000 files measured a median per-query p50 of
**38.3 ms** with `rapidfuzz` and **41.5 ms** on the floor, against a target of under 30 ms; the walk
took **555 ms**. Windows and macOS were not executed; parity is by construction and the mirror's
legs. There is no file-system watcher — an edit outside a session shows on the next git or snapshot
event, or a reload — the explorer does not read `.gitignore`, shell writes are not snapshotted, and
the window has not been run on a real Mac.

**A reported failure installing mokata into a project virtualenv is not closed.** It cannot be
reproduced without the owner's command, error text and venv tool; `requires-python >= 3.10` is
enforced, and the isolated-install docs and the self-contained plugin may already cover it.

**Install routes.** `mokata upgrade` still proposes `pip install -U mokata` whatever the install
route — after `pipx`, `uv tool` or `brew`, use that tool's upgrade. The shadowing check compares
versions only, so a second install of the same version is silent. The Homebrew tap's currency was
not checked.

**The plugin's pinned `uvx` launch needs the version on PyPI.** Between a release reaching the
mirror's main branch and PyPI publishing it, a fresh `/plugin install` gets an MCP server that cannot
start; it starts once PyPI has the version.

**The review window serves any file in the repository** it is pointed at, `.env` included, to the
paired localhost page; semantic search writes an index under `.mokata/` and downloads a model only
on your click. **The `review` and `bug` Agent Skills share a name with Claude Code built-ins**; only
the command files were renamed.

**The Proof plumbing has narrow edges.** Spec anchors are checked lexically — a name present in a
file — and are not graph-queried at emit. Develop-turn guards cover Python files only, and the
evidence is recomputed each develop turn (up to three blast-radius queries). Only the completeness
gate produces typed evidence, and "proven" there means mapped to a test, not a test that passed.
Intention keywords see the branch name and changed paths, not your prompt; there is no CLI verb to
create an intention, and a sweep with keyword or path triggers adds a bounded `git` call.
`mokata suggest` matches a hand-tuned table and does not parse negation. The cache-prefix notice
fingerprints mokata's briefing prefix only, not MCP tool definitions. `remedyCommand` is MCP-only,
`consolidate_proposals` still fails the empty-state lint, and other subcommands still print some
errors to stderr.

**This release's exit criteria are not all met, and the ones that are not are listed here rather than
rounded up.**

1. **A release converging through the `pypi` approval is not yet demonstrated live.** The PENDING and
   UNREADABLE states are graded against fakes; that GitHub reports an approval hold as
   `status: waiting` is owed to the first cut that runs this code.
2. **A stranger's install is not demonstrated on a clean machine.** The plugin's `uvx` launch and
   each-hook-once are tested through the real entry points, not in a live Claude Code session.
3. **Python repositories: met by the tests** — the walker the index and the IDE tree share, and
   `defs`, return project files only; the baseline runs the repository's environment from an
   un-activated parent and names it.
4. **The IDE criterion is not met as written.** ⌘P's p50 is above 30 ms (above); IDE answers equal
   `mokata query`'s byte for byte; Meaning answers without code-review-graph and says "unavailable"
   without the extra; the four visuals render offline; the Windows and macOS legs were not seen.
5. **Proof: not met.** No real run, so no counters, no reproduced study, no compliance table, no
   per-turn context reading and no certified harness. `NO-API-KEY-GUARD` is green over `src/mokata/`
   with the harness present.
6. **The gate ladder is built:** a gate can ship advisory and ledger what it would have blocked, and
   is promoted only through a human-gated proposal — landing on soft.
7. **Order-independence still stands at nine seeds of ten.** No stage of this release recorded the
   tenth.

**Carried from 0.0.22, unchanged.** After a run ships its spec scope keeps applying — start a new
`/mokata:brainstorm` for further work. Read-only subagents are not judged on MCP tool calls. The
shell lane reads commands and does not run them; the parser remains a heuristic. An agent id outside
`^[A-Za-z0-9_-]{1,80}$` gets no subagent contract. A conversational run is bound only when a pipeline
was asked for by name. `ReadCache` is blocked by the harness. Graph freshness still misses a source
file in no baseline that git will not name, and on Windows a same-size write that restores the
mtime — rebuild the code graph yourself after a restore or a vendor drop; at most **500** drifted
paths are hashed per query and **5000** deletion records kept. The token estimate has been measured
once: real output tokens at **1.552×** the `chars/4` estimate over **5** calibratable responses.
TypeScript is an optional extra and **plain JavaScript is not covered at all**. The PostgreSQL floor
is enforced and one dimension of its evidence is manual (**PostgreSQL 16.13** is not a PostgreSQL
14). The Windows notification sound is called, not verified; on Linux an MCP gated write with no
audio player makes no sound; `LISTEN`/`NOTIFY` does not survive a transaction-mode pooler. The
branch-protection degraded pass is bounded, not closed (maintainer-facing). The PR gate still takes
26–31 minutes against a target of under 10, last measured at the **0.0.18** cut (contributor-facing
only).

---

## Upgrading

    pip install -U mokata          # or: pipx upgrade mokata · uv tool upgrade mokata · brew upgrade mokata
    mokata upgrade

`mokata upgrade` refreshes the commands, skills and hooks in your checkout. **This is the release
where five project commands are renamed** — `/review` becomes `/mokata-review`, and likewise `init`,
`bug`, `resume` and `upgrade` — and the old files mokata wrote are removed; your own command files are
never touched. `mokata doctor` names anything out of step, including the Python your tests will run
in.

**On the plugin route**, install [`uv`](https://docs.astral.sh/uv/) if you do not have it; the
plugin's MCP server will not start without it. If you also ran `mokata init` in the repository, the
hooks now fire once.

**For the IDE's new views**, `pip install "mokata[ide]"`; for Meaning search, also
`pip install 'mokata[embeddings]'` and press **Fetch** once to download the model.
