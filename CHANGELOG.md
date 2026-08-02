# Changelog

All notable changes to mokata are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Re-baselined at 0.0.1.** mokata is published fresh at **0.0.1** as its inaugural public
> release. Earlier internal iterations (the pre-1.x and 1.x series) were a stabilizing phase and
> are intentionally collapsed into this entry — 0.0.1 is the honest starting point for an
> early-stage, fast-moving project. The detailed build history lives in the repository's internal
> build log.

## [0.0.16] — 2026-08-02

**Memory intelligence at scale.** Memory that ages, summarizes itself, heals across writers and
carries typed edges — proven at 100,000 items on live Postgres rather than asserted — plus an
upgrade path that finishes the job and gates that hold on Windows.

### Added

- **`mokata upgrade` finishes the job.** Installing the package was never the whole upgrade:
  `pip install -U mokata` replaces the code and leaves `.claude/settings.json` carrying the
  wiring the *previous* version wrote, so a hook added (or a matcher widened) since your last
  `mokata setup claude` was silently absent — no error, no warning, just a gate that never
  fires. `mokata upgrade` now refreshes the harness wiring through `setup`'s own **preview-diff
  gate** and then runs the wiring check, so the whole story is one command. Human-gated end to
  end: decline either gate and nothing is written (`--no-refresh` opts out; `--yes` approves
  both non-interactively, still previewed). Hand-upgraders get the matching steps from
  `mokata upgrade`'s printed recipe, and the source-checkout route carries the same tail.
- **Stale wiring is now visible where you already are.** One verdict — "is the wiring on disk
  the wiring this mokata writes?" — surfaced on three channels, because which one can reach you
  depends on what is broken: a **SessionStart briefing line** (when the hooks still run), the
  **`status` MCP tool** (when they do not — the hooks-dead-but-MCP-alive case), and the named
  `hooks-wiring-stale` **doctor finding** both derive from. A current install sees nothing on
  any of them.
- **`mokata doctor --wiring`** — the wiring-only check: are mokata's gates wired, launchable,
  and current? Exits non-zero if not, and works on an uninitialized repo, so it is runnable the
  moment a `pip install` finishes.
- **`mokata release-notes-check`** — release plumbing, listed here because it ships. It fails
  when `RELEASE_NOTES.md` announces a different version than the tag being cut, or drops a fact
  this file declares under `### Known limitations` for that version — the measurements, the code
  identifiers and the versions, matched rather than the wording, so the notes can be written
  freely but a disclosure cannot be quietly watered down. It runs fail-closed in the release
  script and again at tag time in CI. It exists because the known limitation below was, until
  now, protected by nothing but someone remembering to re-type it.

### Added — memory

- **Memory summaries are written, not templated.** Consolidating a ≥3-turn cluster used to
  produce an f-string; it now produces a real drafted summary. The drafting is *inverted* —
  mokata hands the turn cluster to the harness agent you are already talking to, and the text
  comes back through a seam. No model is embedded in mokata and no API key is involved. The
  draft is a **proposal**: it rides the same secret-scan → human-gate → ledger path the
  placeholder did, so nothing reaches your store unapproved. With no drafter present the output
  is byte-identical to before, and a drafter that raises, times out, declines or returns junk
  falls back to the same placeholder — a *malfunction* says so loudly, because a placeholder
  shown at an approval prompt looks exactly like a real summary and you would approve it
  believing your turns had been read.
- **Memory ages.** Items now carry usage signals (`hit_count`, `last_recalled_at`) and
  bi-temporal validity windows, and recall ranking gains recency and usage terms that break ties
  between things that already match. Over-budget scopes get **proposed** archival sweeps — one
  reviewable decision per bucket. Archiving closes a validity window and never deletes a row:
  your memory is re-openable, and provenance survives. An item with no hits scores exactly as it
  did before, so existing stores rank identically.
- **Scoped memory filters in the database.** Team stores now populate and filter on scope
  columns directly rather than reading a superset and filtering in Python. If a store predates
  the backfill, mokata detects that and falls back to the slower correct path rather than
  trusting half-populated columns.
- **Two teammates writing the same memory produce a proposal, not a lost fact.** When a
  flush-time conflict is detected it becomes a healing proposal resolved through the gate you
  already use. Entries sharing an approval apply in a single transaction, so a conflict rolls
  the whole group back — a partial heal can no longer retire a fact and lose its replacement.
  Retiring a fact whose replacement is still undecided, discarded or blocked is refused outright.
  Contradiction and staleness surfacing, canonicalized subjects and conservative near-duplicate
  detection ride the same path.

### Added — worktrees and navigation

- **Pipelines can run in their own worktree.** At run start mokata now *offers* a run-bound
  worktree and branch — always offered, never automatic — keeps the run↔worktree binding
  visible, and hands you a merge-ready branch at ship.
- **`mokata worktree list`** (and a `worktree_list` MCP tool) — a read-only join of your
  worktrees against their sessions, with a staleness verdict per row (merged · no-session ·
  idle · active) and a real empty state.
- **Code navigation goes through the graph.** Navigation and impact queries route through the
  code-review graph rather than ad-hoc search, and degrade cleanly to the old path when no graph
  is available.

### Added — spec and review

- **`spec_show`** — an MCP read tool that fetches the current spec, so phase prompts stop
  re-searching the repo for something mokata already knows.
- **Re-emitting a spec no longer clobbers the old one.** A second `spec_emit` on a run archives
  and versions the prior spec instead of overwriting it, and work already in flight re-routes to
  `spec_amend`.
- **Standalone `spec` is named as supported on both surfaces.** Running `spec` without a prior
  brainstorm is an intentional path; it now says so from one shared constant rather than
  degrading silently and leaving you guessing whether you had skipped a step.
- **⚠ NEW FAILURE MODE — `mokata spec emit` and the `spec_emit` MCP tool can now refuse on a
  stale code anchor** (`code-anchor-ref`). If the design decisions your approach was approved
  against name code (`about_code`), and that code has changed since those decisions were
  recorded, emitting is blocked with the anchors named and the road out: re-read the changed
  code, decide whether the decisions still hold, then re-approve and emit. **This is a
  deliberate contract change** — both surfaces gained a way to fail that they did not have
  before, which is why it is called out here rather than only in the internal build log.
  Nothing is written when it fires. It is conservative by design: with no recorded baseline for
  an anchor, or a symbol anchor and no adopted code graph, mokata has no evidence the code moved
  and says nothing rather than blocking you on a guess.

### Changed — memory ranking

- **Memory ranking changed: a signal that matches nothing can no longer outrank one that does.**
  This alters the ORDER `recall` returns results in — deliberately, and only where an embedder is
  wired or an item carries recall history. Retrieval now holds one rule end to end: *no
  non-matching signal, alone or in combination, may outrank a real match.* Two places broke it.
  The **semantic tier** was weighted four times the lexical floor with no bound at all, and
  embedding cosine between two *unrelated* items is a positive number — so on the built-in
  token-hash embedder an item matching nothing collected more than a perfect keyword match, and
  buried the answers under filler. The tier's weight is now derived from the embedder's own
  measured noise rather than being one fixed number for every embedder: a quiet embedder keeps its
  full weight, a noisy one is held to what it can be trusted with, and an embedder that cannot be
  characterized fails closed and says so. Separately, the two **recall-history terms** (recency and
  usage) were each individually below the lexical floor but *summed to exactly it*, so an item you
  had recalled often enough tied a perfect match and won the tiebreak — they are now bounded as a
  sum, keeping their relative weighting. Measured on the 5,000-item benchmark: retrieval with the
  graph-expansion tier on went from **+0.0pp to +33.3pp recall** against the keyword floor, and the
  semantic tier stopped costing recall and ranking quality. **If you have not opted into an
  embedder (the default) and your store has no recall history yet, your ranking is unchanged** —
  arithmetically, term for term.
- **A semantic (pgvector) store no longer switches on token-hash embeddings you did not ask for.**
  Selecting the opt-in `pgvector` memory store used to resolve its embedder to `auto`, whose
  documented floor is the built-in token-hash embedder — so a team that opted into a semantic
  store *without* naming an embedder, and without the `embeddings` extra installed, silently
  filled a real vector index with token-hash vectors. Measured at 100,000 items, that tier is
  **net-negative on recall** (see the known limitation below), so it must not be switched on by
  the *absence* of configuration. Now: name an embedder and you get it (including
  `memory.embedder: hashing`, if that is what you want, and an explicit ask that falls back still
  says so); name none and the semantic tier stays **off**, with a notice saying why and how to
  turn it on. Nothing changes for a store that already names its embedder.

### Known limitations

- **The SQLite FTS5/BM25 lexical tier ranks *worse* than the keyword floor it replaced, and at
  scale it costs recall — measured, not suspected.** `normalize_lexical_scores` scales each
  engine's scores against the best score *in its own result set*, which flattens exactly the gap
  that would have ranked a mid-pack answer. On the 100,000-item benchmark, against the Jaccard
  keyword floor on the same probes and the same code — only the corpus size differs — the FTS
  tier measures **−5.6pp recall (0.5000 → 0.4444)** and **−10.8pp MRR@10 (0.8334 → 0.7258)**.
  At 5,000 items the same comparison loses no recall at all and only −3.3pp MRR, so the small
  corpus hides more than half of it: normalizing against the result set's own max only begins
  dropping real answers once there are enough genuine competitors. **This is shipping as-is and
  the fix is scheduled for 0.0.17** alongside the BM25/H-4 ranking work, because the correct
  repair is rank-preserving normalization rather than a constant to tune, and it wants measuring
  in one pass with the rest of the ranking. It is recorded here rather than left to be discovered:
  if you run a large store and your lexical results look mis-ordered, this is why.

### Performance

- **Recall no longer reads your whole memory store to answer one question.** Retrieval used to
  begin by materializing *every* active visible item and decoding each one, on every recall —
  then ranking what it had already paid for. On a 100,000-item store that was **51,606 rows and
  4,533 ms**. Each tier now nominates its own ranked shortlist in the database, the capped union
  is hydrated, and — the part with teeth — **your scope predicate travels with that query** instead
  of being applied afterwards. Same store, after: **26 rows and 20.1 ms** (225× on latency, ~2,000×
  on rows), and the read no longer grows with the store. This also fixes a real correctness bug on
  the way: the ranked query's limit used to be taken over the *whole* store and only then
  intersected with what you were allowed to see, so a reader whose matching items all ranked below
  the cut got **nothing back while their own rows sat unread underneath**.
- **Per-turn memory injection cost three-quarters of a second per prompt.** The per-turn recall
  that rides `UserPromptSubmit` was still doing the whole-store read described above — one layer
  up, where the retrieval fix had not reached it. Measured on a 100,000-item store over 20
  consecutive turns: **787.0 ms → 36.8 ms per turn (21×)**. If you use mokata on a large store,
  this was a latency you paid on *every single prompt*.

### Fixed

- **Windows is now actually first-class, as the docs already claimed.** `platform-support.md` has
  described mokata as "a first-class citizen on Windows, macOS, and Linux" — and for the
  self-protect gate that was **not true**: it enforced *neither way* on Windows. The tokenizer
  treated `\` as a shell escape (it is POSIX's escape character, but Windows' path **separator**),
  which collapsed `C:\…\site-packages\pkg\mod.py` into a string with no path components left — so
  a write into an installed mokata **was never judged at all** (exit 0 where a block is required),
  while ordinary in-repo writes were **over-blocked** because their paths collapsed to relative
  ones that resolved outside the workspace. Both directions came from one root cause and are fixed
  at the root; POSIX tokenization is byte-identical. Alongside it: a POSIX-only `os.geteuid` call
  evaluated at *import* time took an entire test module down during discovery on Windows, and hook
  output capture and worktree path comparison are now encoding- and separator-agnostic. **This
  release makes the platform claim true rather than editing it down** — the Windows CI matrix now
  *executes* these paths instead of asserting about them as strings.
- **Re-entering a pipeline no longer wedges the approval loop.** Returning to brainstorm on an
  existing pipeline and re-generating the spec used to pop an approval prompt on every request;
  approvals are now keyed to the pipeline, and the shared awaiting-head names the other pending
  writes rather than leaving one invisible.
- **A re-entered session can see its own pipeline's state.** MCP state is scoped to the resolved
  evidence run instead of the current session id, so refusal gates stop going quiet across a
  `/clear`.
- **Review verdicts survive a new session.** Verdict lookup is session-aware and keyed to the
  bound run, a run-less read fails closed rather than guessing, and the backward verdict scan is
  bounded without the answer ever depending on the bound.
- **A failed review record is loud.** Recording a review verdict now exits non-zero and says so
  on failure, and both surfaces distinguish "could not read the verdict" from "there is no
  verdict" — previously the same silence.
- **One source of review truth.** The orphaned `check_ship_readiness` path is gone (it carried a
  trust-the-caller shape and a `criteriona` render bug); review status and recording are twinned
  across CLI and MCP.
- **The Homebrew formula vendors 29 resources, not 28** — the 0.0.15 note above miscounted
  against its own lockfile.

### Security

- **Hooks resolve without a shell that completes filenames for you.** Plugin hooks previously
  leaned on cmd.exe's `PATHEXT` completion of an extension-less path — which is a *shell*
  behaviour, not a process-spawn one. Where the harness does not use that shell (PowerShell is
  the default on Windows CI), the launcher did not resolve and **`secret-guard` and `gate-guard`
  simply did not run** — the scan and every run-state gate silently off, with no error to see.
  The setup route now uses the exec form, the plugin route names its shell explicitly, `doctor`
  reports a per-shell finding, and the CI matrix *executes* hooks across platforms rather than
  asserting about them as strings. A self-resolving shim plus a hard `doctor` check cover the
  matching GUI-minimal `PATH` case, and an unresolvable wired hook is now a loud failure instead
  of a quiet one.
- **Writes to mokata's own installed code are blocked, non-overridably.** A single verdict covers
  writing into site-packages, into a mokata install, or outside the workspace root — including
  the Bash side-door. There is no override flag and no environment switch.
- **The secret scanner stops flagging your variable names.** The entropy backstop scans assigned
  *values*, not identifiers, and an anchored word-structure exemption keeps `SCREAMING_SNAKE` and
  `camelCase` names from reading as high-entropy tokens — under a known-shape floor, so a real
  AWS key in a well-named constant still blocks.
- **`mokata secret ignore`** — when the entropy layer is still wrong, you can record an ignore,
  but only through the CLI, only for the entropy layer, keyed by content hash, and version
  controlled. Signature-detected credentials are **refused by name** and can never be ignored.
  Entries expire on content, not on a timer: rename the identifier or delete the line and the
  entry reports inert. A forged ignore store with a valid checksum suppresses nothing, because
  signature findings never reach it.

### Documentation

- **[`mokata-hook: command not found`](https://mokata.ai/how-to/fix-mokata-hook-command-not-found/)**
  — a troubleshooting page titled by the literal error string, so searching the error finds the
  fix. Claude Code drops an unresolvable hook silently, which makes a dead seatbelt and a
  working one look identical from the outside; this page names that and fixes it.
- **[Which setup command do I need?](https://mokata.ai/how-to/which-setup-command/)** — a
  decision table for `mokata init` (your repo) vs `mokata setup claude` (your agent) vs the
  plugin, plus the upgrade runbook.

## [0.0.15] — 2026-07-22

**Simplification & retrieval foundation.** One storage shape, real retrieval tiers, consented
embeddings, a robust MCP surface, and graduated adoption. No breaking changes; no schema-version
bump; local stays the zero-config default. Requires **Python ≥ 3.10**.

### Added

- **Real lexical retrieval.** Memory recall's lexical tier now ranks **in the database** —
  SQLite **FTS5 + bm25** locally, Postgres **tsvector + ts_rank** for teams — replacing the
  Python keyword-overlap scan. The index stays in sync via DB triggers (any writer, any client),
  a build without FTS5 degrades cleanly back to the keyword floor **and says so**, and
  `lexical_mode` reports which engine is actually ranking.
- **Consented semantic tier.** A real embedder ships as the `mokata[embeddings]` extra
  (model2vec, numpy-only) — **installed only on explicit interactive consent** (decline is
  recorded once, never re-asked; `--yes`/CI can never reach pip). Zero-dep hashing remains the
  fallback, honestly labeled. pgvector is wired **opt-in** for teams (HNSW provisioned at
  `team init`). The index is **stamped with the embedder identity**; changing embedders refuses
  into a gated `mokata memory reembed` — mixed-embedder vectors can never silently poison
  recall. `mokata doctor` reports the live retrieval stack.
- **Team-DB onboarding that catches the classic traps.** `mokata team connect` inspects the
  connection string's shape (secret-free — the value is never echoed or stored): it names the
  provider and **flags transaction-mode poolers** (Supabase :6543, Neon `-pooler`, RDS Proxy)
  before they silently break sessions. `mokata doctor` gains a DSN **deep-check** that names the
  failing layer — driver / network / auth / pooler / schema-version — each with its fix.
- **A robust MCP surface.** Every tool call is **bounded** (60s interactive; `baseline` capped at
  120s instead of 10 minutes of silence) and returns a structured status from one documented
  vocabulary — `timed_out` names the operation and the CLI fallback; exceptions return in
  mokata's own voice; no call can return nothing. Tools carry **typed annotations**
  (read-only/destructive/idempotent/open-world), a `response_format` (concise default), **cursor
  pagination** (`audit` no longer returns the whole ledger by default), and typed **input
  validation** with a path-traversal guard. A gated write's result now **leads with
  `AWAITING APPROVAL`** — proposal id + the exact approve/abort commands — `mokata doctor` shows
  what's pending, the statusline shows `⏳ awaiting approval`, and every gated tool documents the
  three outcomes (waiting / human-declined / fault) so waiting is never mistaken for stuck.
- **Graduated adoption.** `mokata init --mode seatbelt|memory|full` — three named on-ramps, each
  printing a quickstart whose commands were verified against that mode's real wiring. `memory`
  and `full` offer the embeddings extra through the consent flow; `seatbelt` never does.
- **One-time gated migrations.** `mokata migrate <channel>` moves obsidian / native-memory /
  vault / memory-share data into the canonical store — preview → explicit approval → WriteGate
  with provenance, idempotent, never destructive of the source.
- **Backup, done properly.** `mokata memory export` / `import` is now the single backup/restore
  surface: timestamped files under `.mokata/backups/`, secret-scanned both directions,
  provenance-stamped on import — and a round trip **never launders approval status**.
- **`mokata approve --list`** — see every write waiting on you (ids, tools, age; never content).
- **Prior-art gate, live.** Spec emit (MCP + CLI) now structurally refuses when the bound
  prior-art step didn't run — reading the durable approval record on both surfaces.
- **Setup legibility.** `mokata doctor` reports whether mokata's skills/commands are actually
  wired in *this* root (the empty-`/`-menu case, e.g. a fresh worktree), and a new session on an
  un-wired root explains why and names the fix.
- **Homebrew machinery.** The formula is now generated — url, sha256, and all 29 dependency
  resources rendered from a lockfile by script, verified end-to-end with a real
  `brew install` + working MCP server. The tap publish follows this release (pip/pipx remain the
  live paths until it lands).

### Changed

- Crash-safety: every committed-config writer (manifest, constitution, stack import, graph pin)
  now writes **atomically** — a mid-write crash can no longer corrupt `.mokata/manifest.json`
  (which, since this release, would loudly refuse rather than silently misbehave).
- Team-mode journal reads are **cached on file identity** and the journal **compacts** past a
  flushed-entry threshold — team repos no longer slow down forever.
- Session transports are **derived from the repo's mode** (solo = local files, team = the one
  Postgres DSN) with `--file` as the explicit escape hatch; a team repo with no DSN **refuses**
  rather than silently writing a private local file; a torn manifest fails closed with the fix
  named.
- MCP write tools resolve their configuration **once per call** (was three times).
- The graph status hint now names the **actual answering backend** — the embedded AST floor no
  longer mislabels itself as "the grep floor".
- CI actions bumped to **Node-24-native** releases across all workflows (still full-SHA-pinned);
  the release-consistency check now also **guards the published action pins**, so they can never
  drift from the release version again.
- Docs: a ground-up truth pass across every public page (107 findings fixed), a rebuilt landing
  page, and slash commands documented in the form your `/` menu actually shows (bare `/name` on
  the pip route) — enforced by a docsync guard.

### Deprecated

- The **Obsidian** and **native-memory** memory backends, the **vault** channel (transport +
  artifact vault), the **memory-share.json** channel, and the **Neo4j code-graph backend** — each
  warns once per repo, keeps working, and has a gated migration (`mokata migrate <channel>`).
  **Removal is scheduled for 0.0.17.** Committed manifests listing deprecated providers still
  resolve (with the warning) — nothing silently vanishes.

### Fixed

- **Spec-amend no longer reads as stuck.** The amend flow returned instantly but buried the
  proposal id under the payload; it now leads with the awaiting head — and a bug where
  `approve=true` silently swallowed the demotion warning is fixed.
- **The MCP server was dead on arrival on Python 3.12** (a missing typing import at startup) —
  fixed, and a startup smoke test now stands the real server up over every tool in CI.
- `ci_check` with a malformed comma-list silently returned PASS over a change it never checked —
  malformed input is now a typed refusal.
- The team audit view reported the total count while returning a truncated page — counts are now
  consistent (page vs total, explicit).
- The bare `mokata approve` listing leaked memory *values* into a model-readable surface via item
  summaries — listings are now content-free.

### Honest boundaries

- **Scope filtering is not yet pushed into SQL** — the scope columns exist but aren't populated;
  pushing them down naïvely would have misfiled every team item as personal, so it waits for the
  0.0.16 write-path work (filed, guarded by a test that fails if anyone half-ships it).
- Some **runtime strings still print `/mokata:<name>`** command forms the pip-route `/` menu
  doesn't show (docs are fixed and guarded; the runtime sweep is filed for 0.0.16).
- The **live-Postgres CI legs** (real psycopg/tsvector/pgvector semantics) are wired as an opt-in
  workflow and were proven against a real engine locally; the hosted run awaits its first
  dispatch.
- `brew install mokata` is **not live until the post-release tap push** — pip/pipx are the
  canonical paths today, exactly as the docs state.

## [0.0.14] — 2026-07-17

**Graph mandatory + trust fixes.** The codebase graph becomes a first-class, always-on structural
layer with an honest fallback, and several trust surfaces are tightened. No breaking changes;
additive; no schema change; local stays the zero-config default. Requires **Python ≥ 3.10**.

### Added

- **Embedded stdlib-AST floor.** A zero-dependency structural backend now ships in the box: on a
  Python repo it answers callers/callees/imports/blast-radius by name-resolution
  (`degraded=false`) — a real floor **above** grep, not the adopted graph. Adopt a richer graph
  with `mokata graph adopt [code-review-graph|serena]` (human-gated); `mokata graph status` reports
  which backend actually answers today.
- **Graph mandatory-by-default.** `settings.graph.required` defaults **true**: a *degraded*
  (grep-floor) blast radius is **refused** as a decision input rather than letting a lexical guess
  drive a decision. The escape is explicit and honest — `--allow-degraded` accepts the degraded
  evidence for the session, is **TTY-reconfirmed** (a model cannot type it) and **ledgered**, and
  the result stays marked degraded.
- **Freshness-before-answer.** Every graph query front-runs a freshness check; a known-stale graph
  rebuilds *before* it answers, and a rebuild failure degrades loudly to the AST floor on **current**
  files — never stale structure.
- **The 9th backed gate — `approach-approval`.** The idea→code jump is now physically blocked: with
  a run registered but no approach approved, a native `Write`/`Edit` to an implementation file is
  refused (exit 2) by the gate-guard hook. It is the 4th run-state gate, overridable like the others
  (named, reasoned, session-scoped, ledgered).
- **Opt-in in-chat approve.** An `mcp__mokata__approve` tool can be enabled
  (`settings.approvals.in_chat`, **default-OFF**; enabling is itself a human-gated, ledgered config
  write). It performs the same single-use, content-hash-bound, expiring approval as `mokata approve`,
  never rides the `mcp__mokata__*` auto-grant (setup writes a `permissions.ask` entry so the harness
  prompts on **every** call), and is ledgered `actor="chat-relayed"`. Out of the box the model still
  cannot mint its own consent.
- **Typed approach `decisions[]`.** An approved approach carries machine-readable decisions
  (statement · rationale · `about_code` anchors · deferred); at spec emit the deferred scope
  **derives** from `decisions[].deferred` (one truth, never hand-written twice), and review's first
  pass compares the diff's actual reach against the declared anchors (undeclared reach is a
  divergence finding). A **prior-art bound step** now gates brainstorm: approach approval is refused
  unless the prior-art step actually ran.
- **Setup one-shots ledgered + reset tombstone.** The six former setup one-shot writers sit in a
  **ledgered** register (TTY consent + audit record), the `KNOWN_BYPASS` register is **empty** and a
  sweep fails if any ungated durable writer ever appears, and `mokata reset` writes a user-scoped
  tombstone that survives `.mokata`'s removal.

### Changed

- **Session-true statusline + run lifecycle.** The active-run badge resolves **session-aware** (a
  fresh session never wears another session's run); a **shipped** run retires from the active badge
  and from `mokata progress` while a spec-emitted-but-unshipped run stays active — nothing is
  deleted, and explicit `run_id` views + resume still work.

### Fixed

- Simulated exec batches now report **zero** actual token spend and a `simulated` (never green)
  review verdict, instead of a placeholder estimate or a false pass.
- `offer_text_once` never raises; the tiered-semantic retrieval branch is kept and marked; and the
  `reset` propose→approve→redeem round trip no longer crashes — the delete is deferred past the gate
  so an approved record is never orphaned.

## [0.0.13] — 2026-07-14

**Correctness & Trust — the seatbelt is now enforced, not advertised.** Every change in this release
fixes silent data loss, a race, or a gate that could be walked around. No breaking changes; additive;
no schema change; local stays the zero-config default.

### Fixed — live bugs that were biting

- **Team writes never flushed on a custom DSN (C-1).** Teams on `team connect --dsn-env CUSTOM` read
  fine but **never flushed a single write**: health, flush and sync were hardwired to `MOKATA_PG_DSN`
  while the read backend honoured the configured env. Writes journalled forever and nobody was
  warned. There is now **one DSN resolver** — health, preflight, flush, reads, audit and session
  transport all resolve the same env var, the literal is named in exactly one module, and CI pins it.
- **The spec gate could brick every implementation write.** `emitted_spec` had **no writer reachable
  from any surface** and `spec_corpus` was read by three surfaces and written by none — so
  `spec-check` always answered "no saved specs — skipped". Because an approved approach *is*
  persisted and the gate *is* wired, the spec-persisted gate then blocked **every** implementation
  write after a real approval, permanently, pointing at a surface that did not exist. A brick, not a
  seatbelt. New `mokata spec emit` / `mokata spec show` + a gated `spec_emit` MCP tool, one committer
  behind both. Verified end-to-end: brick → emit → gate advances to TDD → RED → write allowed.
- **WAL-switch race** (found by the new two-process stress). `SQLITE_BUSY` on the delete→WAL switch
  was believed as a *permanent* degrade: a false user-facing notice, and the losing process stayed on
  the rollback journal. Now transient-retry + sibling-win; a non-busy refusal still degrades.
- **Approval misattribution.** The approval id was a *predicted* ledger sequence and could name the
  wrong entry under a two-process race. The gate now holds the ledger lock across commit +
  approved-record, so an approval carries the **real** assigned sequence (the human gate stays
  outside the hold).
- **Access control failed open.** Team-mode's identity/access fallback silently fell open. It is now
  **deny-by-default**; the one residual half-install path is named, loud, and stays local rather than
  posing as governed.
- **Fabricated ledger rows.** The sequential task floor invented `output="processed:<id>"` and
  `ok=true` for work **nothing ever ran** — and since the ledger is hash-chained, the fabrication was
  durably *attested*. The floor now runs the caller's real runner (real `ok`, failures included); with
  no runner nothing executes, so the result and its ledger row carry `simulated: true`, and every
  consumer (lanes, dashboard, why-timeline, progress counters) **labels it and never counts it**.
- **Silent degrades, swept.** 30 fixed. The worst: a torn or tampered ledger line made the hash-chain
  tamper check report **INTACT**; a scope-*widening* spec amendment silently skipped the blast-radius
  gate and was approved as if the lens had run; and the secret-guard hook swallowed its own
  `ImportError`, so every Write/Edit/Bash proceeded **unscanned for secrets**, silently, forever. All
  three are now loud and fail closed. `doctor` can answer "what degraded this session?", and a
  registered sweep keeps unclassified broad handlers out of CI.

### Added — the seatbelt

- **Hook-enforced gates.** A `PreToolUse` hook enforces run-state gates on **native Write/Edit**
  (exit 2) — not only on mokata's own tools, which was the hole. Two gates fire per run: no code
  before a persisted spec, and no code without a failing test (**RED is the permission to
  implement**). Test files are always writable. Corrupt state fails *open* on the gate; ambiguity
  between windows can never block. The P14 override (`mokata gate override / status / clear`) needs a
  named gate and a reason, re-confirms on a TTY, is session-scoped and hash-chain ledgered — and has
  **no MCP surface, by design**.
- **Human-minted approval — the model can no longer type its own consent.** `approve=true` /
  `confirm=true` were model-typed booleans that stood in for a human decision. They are demoted: they
  return a proposal and an honest note, and **commit nothing**. A commit now requires an on-disk
  approval record minted by **`mokata approve <id>`** in a separate TTY — content-bound (approving X
  then committing Y is arithmetically impossible, not merely refused), single-use (verify-and-burn in
  one locked read-modify-write), session-scoped, expiring, fail-closed off a TTY, and ledgered. There
  is **no `approve` MCP tool and no slash command** — a model-invocable approve *is* the hole. The
  secret hard-block survives a real human approval.
- **The trust dial is wired.** `settings.trust` was dead code: nothing in the codebase ever
  constructed a trust policy, so `read-only` did **nothing** while `doctor` linted the levels and the
  docs described them. A single write-policy seam now carries trust + tool identity + verified consent
  from the MCP boundary into every gate that actually writes, and read-only refuses *before* proposing
  (ledgered) rather than walking a human to a terminal for nothing. **Honest ladder, printed by
  `doctor`:** on MCP it is really *read-only ▸ write-allowed* — after human-minted approval,
  propose-only and gated-write coincide, so the middle rung pins the floor but adds no teeth; CLI
  writes carry identity but the dial is not yet enforced there.
- **The zero-bypass audit.** An AST sweep forces **every durable-write site** in `src/` into
  *gated* / *ungated-by-design* / *known-bypass*; an unregistered writer **fails CI** (tripwire
  proven by planting one), stale entries fail, the register is frozen, and the disposition prints on
  every push. It closed three real side doors: memory consolidation wrote outside the gate (being
  journal-first made it *look* governed), export was gated but never **scanned** on either surface
  (the two composed — one plants a secret, the other exfiltrates it), and a direct migrate clobbered
  a teammate's row and left `revision` stale, corrupting compare-and-set for *later* writes.
- **Scope binding — born from a real incident.** An agent built batch update/delete although the
  saved spec had **deferred** it, treating a user's instruction as authorization. The spec now carries
  a machine-checkable scope (authorized globs + deferred items with paths and literal markers), and
  the hook reads the incoming **content** (which it used to discard) — so a deferred feature added to
  an *authorized* file, the incident's actual shape, is caught with exit 2. The only road back is
  **`spec amend`: a forced phase regression** — writes blocked, completeness and blast-radius re-run,
  a fresh human approval, spec v*N*+1 with v*N* superseded (not deleted), RED owed for the new
  criteria, then a resume from the last passed gate. **A user's instruction is authorization to ASK,
  not to build.** Honest boundary: paths and literal markers, never semantics.

### Added — multi-session safety

Every Claude Code window is its own process, and every in-process lock was exactly that — per
process.

- **Atomic state** (temp + fsync + replace, plus OS file locks): a torn write used to **silently
  erase** state.
- **Session identity**: a minted `session_id` and session-scoped state keys — the keys were per-repo
  singletons that clobbered each other across windows, and the run id had no generator at all — plus
  `mokata windows` to see them.
- **Worktrees**: detected, offered (human-gated), and sharing one team project identity.
- **Ledger**: hash-chained with a locked O(1) sequence, a self-healing counter, `verify()`, and a
  `doctor` finding.
- **SQLite WAL** with an explicit busy timeout, an **idempotent single-flusher** sync, and a
  read-modify-write / TOCTOU sweep (nine racy shared sites closed).
- **Two-process stress in CI** (Linux + Windows): a seeded 2×2000-operation mixed workload with 16
  named invariants and the seed + replay command carried in every failure message.

### Added — session save & share

The save path **did not exist in production**: the brainstorm-progress, gate-checkpoint and
spec-emit writers had zero non-test callers, so the whole bundle/resume stack read state that nothing
ever wrote. An interrupted brainstorm was unrecoverable, and push reported "nothing is in progress"
while one was.

- **`session save`** — ungated by design (consent binds at the **share** boundary, not the local
  save) and wired to the real pipeline moments. Survives `kill -9`: the resume is a genuine disk
  round-trip, not an in-memory reset.
- **Per-turn autosave** — a crash loses **at most one brainstorm turn**, proven numerically. Exactly
  one state write per turn, and zero network on the save path.
- **Bundle v2** — a version-aware hash that binds the transcript, metadata and cross-repo flag (forged
  flags are caught), a bounded, secret-scanned transcript, and `--save-first` / `--allow-in-progress`
  / `--requirements-only` cross-repo requirements sharing.
- **Approval never crosses machines.** A real hole: hydrating a bundle imported the approved approach
  and its approved flags **verbatim** — approval authority travelled with the file. It is now stripped
  at the single hydrate seam, on every transport: the receiver's own gate owns approval, and the
  HARD-GATE survives every round trip.

### Added — data safety (D-rows)

- **No runtime DDL** — schema is verified, never created at runtime, so a team can no longer silently
  degrade to local SQLite; and a schema-version **range** so a version bump does not hard-split a team.
- **Downgrade-safe memory docs** — read-but-never-write compatibility, unknown fields preserved, and
  every mutation path (**including prune**) refuses a doc it cannot fully read: destroying fields you
  can't read is the same bug wearing a different verb.
- **Vault integrity failures are auditable** — a pull that fails its hash check records a
  hash-chained `vault_integrity` event (by hash *prefix*, never restating the artifact) and refuses,
  copying nothing.
- **`docsync` false positives fixed at the source** — 35 of them, in three fabrication classes; the
  allow-list that was masking real drift is gone, and reach is pinned so the fix can't disarm the
  check.

### Honest boundaries

These are real, registered, and not excused:

- **"Zero writes bypass the gate" is NOT true repo-wide.** It *is* true — and proven — of the memory,
  export and migrate funnel. **Six CLI/bootstrap setup one-shots** (init, harness setup, skill
  write/prune, governance lifecycle remove) still write without passing the gate. They sit in a frozen
  six-entry register that CI enforces, and they are filed for **0.0.14**.
- **The gates bind Write/Edit and mokata's own tools.** An agent with arbitrary **shell** access is
  out of scope — Bash is a side door the hook does not gate.
- **The trust dial is not enforced on the CLI** yet, and propose-only has no extra teeth on MCP beyond
  the human-approval floor. Both are filed, not built.
- **The Windows two-process-stress proof lands on public CI.** The step is wired on both operating
  systems; the private repo's Actions are billing-constrained, so the Windows cells and the live
  database leg are verified on the public mirror's CI at the cut, before publish.

## [0.0.12] — 2026-07-08

**A legible skills pipeline, native domain knowledge, and a docs↔code reconciler. No breaking
changes; additive; local stays the zero-config default; no schema change.** This release makes
mokata's existing pipeline visible and continuous, adds ten clean-room domain-knowledge skills that
attach to the phase where they apply, and ships a new skill that keeps your docs true to your code.

- **Skills are now legible.** Every skill carries a `## Contract` (what it CAN do, what it MUST NOT,
  and which real gate backs each boundary) and an active-skill banner, single-sourced so the
  statusline, in-chat surface, and `mokata progress` always agree. Each skill also gains an
  anti-rationalization table and a verification checkbox, and skills auto-engage when the moment
  fits. `mokata skills` now lists the **complete** curated catalog — grouped into runnable pipeline
  skills and standalone/auto-firing ones (`docsync`, `govern`, `session`, `playbook`, `mcp-repair`),
  each with detail and search.
- **Ten native domain-knowledge skills.** API design, security & hardening, performance,
  frontend/accessibility, browser testing, CI/CD, git workflow, deprecation & migration,
  documentation/ADRs, and shipping & launch — each attaches to the pipeline phase where it applies
  and feeds the gate already running there (e.g. security items are hard-enforced rules; an API
  contract change walks its blast radius; a deprecation records to the ledger). Authored clean-room
  from primary sources (OWASP, RFCs, web.dev/Core Web Vitals, MDN/WCAG, Google eng-practices) with
  cited URLs.
- **`mokata docsync` — keep docs true to the code.** Point it at a doc (`mokata docsync <path>`) or
  let it find the relevant docs; it audits every claim against the code (commands, config keys, skill
  counts, install path, versions) and highlights drift with severity, then offers **human-gated**
  fixes (preview the diff, write only on approval). It also auto-fires when a change touches a
  documented symbol.
- **Develop shifts problems left.** On a non-trivial ambiguity, develop now stops, asks one question,
  and amends the spec (human-gated) before continuing — instead of assuming and surfacing the issue
  at review. Brainstorm gains a design pre-mortem and a doc-freshness check.
- **Fixes.** Hooks resolve reliably under a GUI-launched minimal PATH (SessionStart briefing +
  secret-guard no longer silently skip); team-mode memory resolves conflicting scoped items to one
  winner; a team-Postgres read-through cache keeps retrieval and gates from blocking on the network.
- **Hardening & docs.** CI dependency installs are hash-pinned (`--require-hashes`) for a stronger
  supply-chain posture, and a new developer "How it works" documentation section explains the
  pipeline, gates, knowledge graph, memory, governance, and the domain-skills layer.

## [0.0.11] — 2026-07-07

**Team mode — a shared, governed team brain over your own Postgres. No breaking changes; additive;
local stays the zero-config default.** mokata gains an explicit **run mode** and the infrastructure
to share a governed brain across a team — on the team's own database, with nothing ever phoned home.

- **Run mode, first-class and visible:** `mokata mode` shows the current mode plus a team-readiness
  preflight; `mokata mode set local|team` switches it through the human-gated write path. `local`
  is the zero-config default (a no-op that writes nothing on an already-local repo); `set team`
  runs a **fail-closed** preflight — a usable run identity, `$MOKATA_PG_DSN` present, the DB
  reachable within a ≤500ms probe, and a compatible schema version — and only then activates. Team
  mode is **never half-activated**, and the mode is surfaced in the status badge, the SessionStart
  briefing, and `mokata doctor`.
- **Team setup on your own backend:** `mokata team init` is first-time setup and the **sole owner
  of DDL** — it guides a backend pick (`--backend managed|compose|local`, managed DSN the golden
  path), fails closed with a named fix when `$MOKATA_PG_DSN` is unset, provisions the shared tables
  idempotently on **vanilla Postgres ≥14 (no extensions)**, pins the team project identity, and runs
  a live CONNECTED test. `mokata team join <source>` is the new-member onboarding path (a joiner
  never runs DDL): it chains **adopt → connect → activate → vault pull → onboard → consent → doctor**,
  each a confirmable step, inheriting the pinned team project id. The individual steps ship too —
  `status`, `adopt`, `connect --dsn-env <ENV>`, `disconnect`. The DSN **value is never stored** (only
  the env-var name), and **mokata hosts nothing**.
- **Shared memory over Postgres:** a team-shared store with a **scope hierarchy** (personal →
  project → team → global), **typed items** (rule / guardrail / best-practice / context / reference
  / decision) each carrying an **enforcement binding** (advisory / soft / hard), **in-run hard-rule
  enforcement**, and shared **formulas** (typed domain facts). `mokata memory promote` moves a rule's
  enforcement binding (human-gated); `mokata memory review` runs the proposal workflow (Draft →
  In-Review → Approved, proposer ≠ approver).
- **Journal-first team writes, conflict-safe:** every durable team write lands in a **crash-safe
  local journal first**, so **offline never blocks** and nothing is lost. `mokata sync` flushes and
  reconciles — each flushed write **inherits the ledger id of its original human approval** (never a
  governance bypass) and is re-**secret-scanned**, and each memory write is **compare-and-set** on a
  revision column so a concurrent-writer conflict **surfaces through the human gate**, never a silent
  last-writer-wins.
- **Scoped-consent access + audit-publish consent:** access to the shared brain is governed by
  **scoped consent**; `mokata audit --consent show|grant|revoke` manages a **revocable standing
  consent** for the batched audit-publish (captured during `team join`), while the per-publish
  secret-scan still hard-blocks — never a governance bypass.
- **Release safety:** `mokata branch-protection-check` verifies the public default branch is
  protected — no force-push, no deletion, required checks — **fail-closed** (exit 1 if unprotected or
  unverifiable), auth supplied by the `gh` CLI (no token hard-coded or accepted).
- **Team ops kit:** a `docker-compose.team.yml` + `.env.example` for self-hosting the shared
  Postgres, and an `llms.txt` at the docs root.

**Also:**

- The in-Claude-Code MCP repair skill is renamed to **`/mokata:mcp-repair`**.
- `mokata setup claude` surfaces an explicit **permission-grant** step when wiring the MCP server.
- **Python ≥ 3.10** is the supported floor.

## [0.0.10] — 2026-07-06

**"Inside Claude Code" — richer in-terminal UX, a gated settings wizard, a `doctor` coverage
matrix, and three hook/setup fixes. No breaking changes; additive; no new dependencies.**

- **`/mokata:menu` command palette:** `mokata menu` shows every mokata command and skill on one
  screen with gate markers, derived from the shipped command/skill files (single source — no drift).
- **`/mokata:docs [topic]`:** points to the published docs — lists topics with their URLs and
  resolves a topic to its page. Docs are read online, not bundled in the wheel; local-first (never
  fetches at runtime).
- **Gated settings wizard:** `mokata config wizard` walks you through mokata's settings
  interactively — every change routed through the same human-gated write path (secret-scan + schema
  validation + write gate + audit ledger), and fail-closed when non-interactive. It's a front-end,
  never a second write path.
- **Consistent output + `mokata doctor --matrix`:** verdicts, progress, and doctor tables now share
  one look (colour on a TTY; clean ASCII when piped or under `NO_COLOR`). `mokata doctor` gains an
  opt-in capability **coverage matrix** — pass / degraded / fail for every capability, single-sourced
  from the resolver.
- **Token-estimate calibration:** the tokenizer-free chars÷4 estimate now logs estimate-vs-actual to
  the ledger, so the ~2k briefing budget's safety margin is measured, not merely asserted.

**Fixes:**

- **Hooks never hang.** `mokata-hook statusline` / `session-start` no longer block if stdin is an
  open pipe with no writer — a bounded read falls back to defaults (the "hooks never block a
  session" contract).
- **Mis-wired hooks are visible.** `mokata-hook` with a missing or unknown subcommand now exits
  non-zero (exit 1) instead of looking successful — and never uses the reserved security-block code.
- **Clean uninstall.** `mokata unsetup claude` removes config files it created once they become
  empty instead of leaving `{}` husks; files that still hold your own content are preserved.

## [0.0.9] — 2026-07-04

Promotes `0.0.9rc1` unchanged (same code; version fields and notes only). Install with
`pip install mokata`.

**Installs from PyPI, no clone — and the MCP server works out of the box.**

- **pip-installable:** `pip install mokata` now ships everything (command templates, hooks, and
  Agent Skills are packaged in the wheel) — no repo clone needed. The bundled MCP server's SDK is a
  default dependency on **Python 3.10+**, so `mokata-mcp` runs out of the box (on 3.9 the CLI still
  works; the MCP server prints a clear upgrade message).
- **One-command wiring:** `mokata setup claude` registers the MCP server at an absolute path,
  verifies the connection (`CONNECTED ✓`), and wires commands + skills + the status line. New
  `mokata mcp start | status | install`, and a `/mokata:mcp-repair` repair skill that re-registers the
  server from inside Claude Code.
- **Skills stay fresh on update:** re-running `mokata setup claude` now syncs the Agent Skills and
  prunes stale/removed mokata skills (your own skills are never touched).

**Progress you can see, and a review you can trust.**

- **Redesigned always-on status badge:** the full brainstorm → spec → develop → review → ship arc,
  each stage marked done/current/pending, with a live `develop [done/total]` counter. Configure via
  `settings.ux.badge_verbosity` (`full` default | `minimal`). `/mokata:progress` now shows the
  user-stage arc and what's pending this session.
- **Independent review closes the pipeline:** `/mokata:review` runs as a **fresh-context subagent**
  by default (re-deriving its verdict from a self-contained brief, not the builder's context), and
  `/mokata:ship` now **blocks unless a passing review is on record** for the run — evidence over
  claims. Degrades cleanly to inline review where a harness has no subagents; toggle with
  `settings.review.independent`. Fixes review not reliably firing after `develop`.
- **Brainstorm saves a plan:** when you approve an approach, the design write-up is saved as a plan
  file; `mokata plan list | show | export` keeps an editable copy in your repo.

**Under the hood.** Reproducible, Sigstore-signed wheels published to PyPI from CI via OIDC Trusted
Publishing (public repo only), a fail-closed release pipeline that won't tag on a red matrix, and
internal refactors (`cli`/`mcp` split into packages) with no behavior change. Local-first, no
telemetry, Apache-2.0.

**Known issue** (fix scheduled for the next release): invoking `mokata-hook statusline` /
`session-start` by hand with stdin attached to a pipe that never closes can block until the pipe
does. Claude Code's own hook invocation (payload + EOF) is unaffected — normal use never hits this.

## [0.0.8] — 2026-07-01

**Fix: no duplicate Agent Skills when the plugin is installed.**

Fixed: `mokata setup claude` (the no-plugin path) now detects an installed mokata **plugin** and
**skips writing the project-scope Agent Skills**, since the plugin already provides them — running
both previously made Claude Code list every mokata skill twice (`mokata:<name>` from the plugin
plus a bare `<name>` from `.claude/skills/`). Detected via a `plugin.json` named `mokata` under
`~/.claude/plugins/`; the plan output says `Agent Skills: SKIPPED` when suppressed. Commands, hooks,
and MCP wiring are unchanged. No effect when the plugin isn't installed.

## [0.0.7] — 2026-07-01

**Agent Skills surface. No breaking changes; additive.**

Added: mokata's core capabilities now also register as Claude Code **Agent Skills** (which Claude
auto-engages from their `description`), alongside the existing `/mokata:*` slash commands. 14
skills (the 0.0.7 set — the curated catalog has since grown to 16) (`brainstorm`, `spec`, `develop`, `review`, `refine`, `test`, `debug`, `bug`, `optimize`,
`ship`, `onboard`, `govern`, `session`, `playbook`) ship as `skills/<name>/SKILL.md`, each
**rendered from the one command template** — a single source with a drift guard, so a skill can
never diverge from or duplicate its command. Installed by **both** paths: the plugin (`skills/` +
`"skills"` in `plugin.json`) and `mokata setup claude` (writes `.claude/skills/<name>/SKILL.md`,
removed cleanly by `mokata unsetup claude` without touching your own skills). Non-Claude harnesses
degrade clean (no skills surface).

## [0.0.6] — 2026-07-01

**Windows portability fix. No breaking changes; Linux/macOS behavior unchanged.**

The Windows CI matrix ran for the first time on the 0.0.5 re-cut and exposed two real,
Windows-only bugs (the prior green runs were Linux-only). Both are fixed:

Fixed:
- **SQLite memory backend held a file handle across operations** — a persistent connection
  kept `memory.db` open, so on Windows a tempdir teardown failed with
  `PermissionError: [WinError 32] … used by another process` (dozens of tests). The
  file-backed SQLite backend now uses a short-lived connection per operation (no OS handle
  outlives a call — also a real resource-leak fix); an in-memory (`:memory:`) DB keeps its
  connection, since it has no file to leak.
- **Text files written without an explicit encoding** landed as cp1252 on Windows (em-dash
  `—` → `0x97`), then the utf-8 read raised `UnicodeDecodeError`. Every text-mode file
  open/read/write now declares `encoding="utf-8"`.

Guarded:
- A lint test fails if any text-mode `open()` / `read_text` / `write_text` omits `encoding=`.
- A portability test exercises the memory store in a temp dir and asserts no lingering file
  handle (removable while the backend is alive) — reproducible on every OS.

## [0.0.5] — 2026-07-01

**Portable sessions, in-Claude-Code UX, every-agent reach & supply-chain trust.
No breaking changes.**

Fixed:
- **Hook invocation** — replaced the fragile `sh launch.sh → python3` hook chain with a
  PATH-resolved `mokata-hook` console entry point (the same reliable mechanism `mokata-mcp`
  uses). This fixed the `python3: command not found` pre-hook error class for PATH-resolved
  installs; the GUI-launched minimal-PATH variant was fully closed in 0.0.12, which resolves
  `mokata-hook` to an absolute path. `launch.sh` remains only as a last-resort pure-plugin fallback.

Added:
- **Portable / shareable sessions** — `mokata session push <tag>` / `pull <tag>` / `list` / `name`:
  package checkpoints + approach + in-progress brainstorm + relevant memory into a
  machine-path-free, versioned, **secret-scanned + human-gated** bundle (local file or shared
  transport); start on one machine, resume on another, or hand it to a teammate.
- **In-Claude-Code UX** — an always-on **stage badge** (statusline, on by default, merge-safe);
  pipeline flow legibility (gate verdicts, why-blocked + how-to-unblock, one-key gate responses,
  progress counters); the parallel-agent **lanes** view + `/mokata:progress` / `watch` / `govern`
  slash commands + MCP tools; **full command-surface parity** (every user command reachable in
  Claude Code, enforced by a CI parity gate); assisted **task decomposition** + parallel-plan
  confirm; a **brainstorm anti-drift anchor**; and the native **to-do widget** projection — all
  channel-specific renderers over one `RunProgress`.
- **Magical first-run + reconfigure** — an interactive `/mokata:setup` Q&A wizard (detect → wire →
  build → guardrails, human-gated) and a re-runnable `mokata reconfigure` to change what's wired.
- **Memory intelligence** — explainable retrieval (why a memory surfaced), memory-health nudges
  (stale / contradictory / unused), and auto-proposed guardrails from observed corrections
  (proposal-only, human-gated).
- **CI / PR check** — the completeness + spec-awareness gate as a reusable GitHub Action; a
  `/mokata:review` PR comment. Opt-in, degrade-clean.
- **Every agent** — in-harness surfaces for **Cursor, GitHub Copilot, Windsurf, Codex, Gemini CLI,
  and Aider** (not just Claude Code). Language coverage (Python/JS-TS/Go/Rust/Java) +
  Windows/macOS/Linux CI matrix. (A **VS Code extension** and a read-only **Copilot Chat `@mokata`**
  participant are **planned — not available**.)
- **Sharing** — publishable governed **community stacks** (`mokata stacks`): publish over git or
  the design vault, discover a reviewable versioned index, and adopt via the gated install path —
  **no telemetry**. (Team mode over a shared backend — `mokata team join`, shared memory, and the
  shared audit log — lands in 0.0.11.)

Hardened:
- **Supply-chain trust** — reproducible sdist+wheel, a **CycloneDX SBOM**, and a **Sigstore
  build-provenance attestation** at tag-time; all five CI workflows least-privilege + SHA-pinned.
- **Reliability** — a seeded fuzz/edge pass across the hot paths (no false-blocks); a
  **performance budget** (`mokata lat-check`) with measured per-operation latencies.
- **Release process** — `mokata release-check` **verifies version-consistency at the exact commit**
  before any tag; Pages deploy restricted to `main`.

## [0.0.4] — 2026-06-28

**Governance transparency, session lifecycle, portability & hardening. No breaking changes.**

Added:
- **`mokata govern`** — a self-contained, clickable local dashboard of the governed state: rules
  & guardrails (with line-budget), memory by kind with provenance, the read/write adoption ratio,
  and pending self-healing proposals — read-only.
- **`mokata audit --why`** — a what + decision + **why** timeline; every gate / deviation /
  spec-conflict / self-healing decision now records its rationale.
- **`mokata sessions` / `mokata resume`** — list past/active runs and resume from the last passed
  gate; plus a **mid-brainstorm checkpoint** so you can leave a brainstorm at any step and come
  back (the approach HARD-GATE still holds).
- **git-worktree isolation** — opt-in (`settings.execution.worktrees`): parallel/fanout tasks and
  paused/WIP sessions run in throwaway worktrees, auto-cleaned, degrade-clean without git.
- **Cross-harness portability** — a `Harness` boundary with **claude** (reference), **codex**, and
  **cowork** adapters; `mokata harness` shows the capability matrix; missing capabilities degrade
  clearly (never pretend). A "use mokata in Cowork" how-to.
- **`mokata version` / `mokata upgrade`** — offline version info; opt-in update check (the one
  outbound call, netguard-accounted); human-gated upgrade; `/mokata:version`.

Hardened:
- **Secret guard** — broadened to 18 credential formats + a seeded fuzz invariant; pure-hex
  digests / paths / URLs / UUIDs no longer false-positive (real secrets in context still block).
- **Repo/OSS hygiene** — Dependabot, CodeQL, Scorecard, CODEOWNERS.
- **Live-DB CI** — Postgres + pgvector + Neo4j service containers exercise the shared-memory /
  semantic / graph paths for real (the core stays dependency-free).
- **Docs** — README + CLI reference audited to match the full command surface, with a docs-vs-code
  drift guard test.

## [0.0.3] — 2026-06-28

**Wires up governance/token features that previously had no runtime path, plus a second
secret-guard precision fix. No breaking changes.**

Added / now reachable:
- **`mokata memory consolidate`** — surface proposal-only memory consolidations (merge/summarize/
  prune); read-only, applying stays the existing human-gated path.
- **`mokata skill author`** — author a skill via RED-GREEN-for-docs, written through the
  human-gated WriteGate.
- **`mokata playbook --dense`** — output-density compression of sub-agent handbacks
  (content-preserving, off by default; `settings.governance.output_density`).
- **Karpathy gates** now run per pipeline phase (toggleable via `settings.governance.karpathy.<id>`,
  audited), **lethal-trifecta gating** now guards a private outbound `vault push` (human-gated +
  logged), **rules-learning** now surfaces proposal-only rule promotions from recurring
  corrections in `mokata rules`, **per-task model routing** is available (opt-in via
  `settings.execution.model_routing`), and the SessionStart briefing emits a **cache-stable
  prefix**. All off-by-default / degrade-clean / human-gated where they write.

Fixed:
- **Secret guard precision** — the entropy layer no longer flags long file paths / URLs / UUIDs
  in content as secrets (it broke writes of any file containing a path); real-secret detection is
  unchanged. (Complements the 0.0.2 envelope fix.)

## [0.0.2] — 2026-06-27

**Critical fix.** The PreToolUse **secret-guard hook** scanned the entire hook payload —
including Claude Code's high-entropy `session_id` and `transcript_path` — which tripped the
secret detector and **blocked every Write/Edit/Bash call** for installed plugin users. The guard
now parses the PreToolUse envelope and scans **only the tool's content and target path**, never
the envelope metadata. Real-secret detection is unchanged (secrets in a command, file content, or
a `.env`/`.pem` path still hard-block); `--text`/`--path` usage and raw-text scanning are
preserved. Added regression tests for the envelope path. No feature changes.

## [0.0.1] — 2026-06-27

The inaugural public release — the full feature set, built clean-room, local-first, Apache-2.0.
A spec-driven, test-first framework for Claude Code with a real codebase **knowledge graph**,
persistent **self-healing, shareable memory**, and **human-gated, audited governance** as its
spine.

### Spine, knowledge & engine
- **Spine.** Stack manifest + schema, capability router with declared fallback, tool detection +
  graceful degradation, sub-2k-token SessionStart briefing, unified config/constitution surface,
  `mokata init`; capability-negotiation + BYO-tool adapter contracts; MCP registry/discovery.
- **Knowledge graph.** Adopted codebase-graph adapter with a grep floor; typed structural queries
  (callers/callees/implementers/imports/blast-radius); incremental re-index with staleness
  surfacing; `@lat` drift anchors / `lat-check`. **External Neo4j adapter** — wire a team graph as
  the `code_graph` provider (env-var credentials), degrade-clean to grep.
- **Engine & TDD.** 7-phase pipeline (brainstorm → analysis → strawman → pre-mortem → probes →
  completeness gate → emit); provable completeness gate (every AC maps to a test, RED before
  GREEN); spec persisted + spec-persisted precondition; **anti-assumption / ground-in-code**
  discipline; per-run execution-mode selector (sequential default / parallel: fresh-subagent
  isolation + two-stage review + fan-out, degrade-safe).

### Memory — the institutional brain
- Persistent / decision / **typed** memory (rule · guardrail · best-practice · context ·
  reference), on by default; self-healing by surfacing old→new diffs; per-type toggles.
- **Tiered retrieval** — lexical floor + graph-proximity + semantic (pluggable embedder / pgvector
  vector backend), fused + ranked, frugal top-k, degrade-clean.
- **Sharing** — `memory export`/`import` (file), `memory migrate` (sqlite ↔ obsidian ↔ postgres),
  and a team-shared **Postgres** store whose schema mokata owns (`mokata_memory`).
- **Guided capture** — `/mokata:onboard` LLM-processes rules/guardrails/conventions/docs/context
  into typed, human-gated memory that the skills reference just-in-time.
- **Team design vault** — push a named brainstorm-plan/spec → teammates search → pull → review
  (versioned, gated, secret-scanned).

### Governance, safety & UX
- **Spec-awareness / regression guard** — a change is checked against saved specs + decisions and
  raised (deviation gate, human-gated, logged) before it can break them.
- **Plan-adherence deviation gate**; **universal human-gated writes** (every code/memory/config
  write through one `WriteGate`: secret-scan hard-block → approval → commit → audit ledger);
  reversible + resumable; local-first, **zero telemetry**; per-adapter trust dials.
- **Run observability** — parallel-aware terminal lanes (`mokata progress --lanes`) and an opt-in
  self-contained clickable HTML dashboard (`mokata watch`); read-only, frugal, local-first.
- **Composability** — profiles (minimal/standard/full/custom), per-layer/tool toggles, standalone
  skills, mid-pipeline entry; verified `mokata ship` (green + ACs met + review passed, then
  human-chosen landing — never auto-merge).

### Notes
- **Early & stabilizing:** 0.0.1 is an early release of a fast-moving project; expect rapid
  iteration. Pin the version if you need stability.
- No required runtime dependencies — `jsonschema`, `mcp`, `postgres` (psycopg), and `neo4j` are
  optional extras, each lazily imported and degraded over. The suite passes with `jsonschema`
  both absent and present.
- Clean-room throughout: no dependency on, or text copied from, any other framework
  (Apache-2.0, under MoStack).

[0.0.16]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.16
[0.0.15]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.15
[0.0.14]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.14
[0.0.13]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.13
[0.0.12]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.12
[0.0.11]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.11
[0.0.1]: https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.1
