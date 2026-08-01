"""D5 — THE SWEEP REGISTER: every broad exception handler in src/, classified.

This is the SI.6 `TestZeroBypass` pattern, pointed at the other half of doc 74's finding. SI.6 swept
every durable WRITE and forced each into a register; this sweeps every broad CATCH and does the
same. The reason is identical, and it is the reason D5 exists at all: a fix for the handlers we
happened to notice is worth little if a new one can be added next week. `except Exception: pass` is
a single line, it is invisible in review, and 212 of them accumulated without anyone deciding they
should.

A handler is BROAD when it is `except Exception`, `except BaseException`, or a bare `except:`.
Every one is registered as exactly one of:

  (i)   DEGRADE_CLEAN   a deliberate fail-open that ALREADY says so — it prints, returns a verdict
                        carrying the reason, or has a documented fallback contract. Verified, not
                        excused: each entry names WHERE the signal comes out.
  (ii)  DEGRADES_LOUD   was a SILENT degrade; D5 gave it a `note_degraded()` notice (or made it
                        stop swallowing). The fallback still falls back — it just stops being a
                        secret. These are the stage's targets, and the per-subsystem tests in
                        test_d5_notices.py force each one to fail and assert the notice.
  (iii) NARROW_IS_HONEST  the broad catch cannot be narrowed because the real exception class is
                        not nameable at module scope (an optional dependency — psycopg, neo4j,
                        jsonschema — whose import is lazy by design). Narrowing would mean importing
                        the optional dep to catch it, which is the bug the lazy import prevents.
  (iv)  SUPPRESS_OK     silence IS correct: cleanup on the way out (unlink-tmp-then-re-raise), a
                        best-effort cosmetic, or a guard around a callee whose own docstring
                        promises it never raises. Each entry says WHY.

The counts here are the HONEST ones, re-measured at the head of this stage. Doc 74 said "168
`except Exception` / ~25 bare `pass`". Both were stale/wrong: there were **212** broad handlers
(210 `except Exception` + 2 `except BaseException`) and **ZERO** bare `except:` — the "~25 bare
pass" was really "handlers whose body is exactly `pass`", of which there were 47.

WHAT FAILS THIS TEST: adding a broad handler and not classifying it. That is the whole point. If
you are here because CI is red, do not add an entry to make it green — decide what the handler
actually is. If a real failure can pass through it unannounced, it is a (ii) and it owes the user a
notice.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")

DEGRADE_CLEAN = "i"
DEGRADES_LOUD = "ii"
NARROW_IS_HONEST = "iii"
SUPPRESS_OK = "iv"

# ---------------------------------------------------------------------------------- THE REGISTER
# (relpath, enclosing qualname) -> (type, justification)
#
# Keyed by function, not by line: a line number goes stale on the next edit, and a stale register
# is a lie that makes the next reader trust the whole list less (the SI.6 rule). A function holding
# several broad handlers of the SAME type carries one entry; the audit checks the COUNT too, so a
# new handler smuggled into an already-registered function still fails.
REGISTER = {}   # populated below, grouped by subsystem


def _register(relpath, entries):
    for qual, (kind, why) in entries.items():
        REGISTER[(relpath, qual)] = (kind, why)


# ------------------------------------------------- the team WRITE PATH: journal · flush · overlay
# The journal is the ONE durable team write path, so a swallow here is not a degrade — it is a lost
# write. Five handlers LEFT this sweep rather than being registered in it:
#
#   * `_read_remote` stopped swallowing ALTOGETHER. It returned None on a DB error, and None means
#     "no such row remotely" — which on the DELETE path is the SUCCESS signal. A transient error
#     marked a gated PRUNE as FLUSHED and ledgered a `team_flush` for a delete that never touched
#     Postgres. That is a FALSE SUCCESS, not a fallback, and there is nothing to fall back TO, so it
#     now propagates to the (registered, loud) per-entry handler in `_flush_locked`.
#   * `_floor_rows` stopped swallowing: `[] on any error` made a locked/corrupt SQLite floor read as
#     "no stranded rows", so floor recovery silently never ran and `sync` reported a clean pass. It
#     propagates to `sync`'s existing `floor recovery skipped: {exc}` channel (registered below).
#   * `TeamJournal._append` (fsync) → OSError; `TeamJournal._records` (a torn last line) →
#     json.JSONDecodeError; `_canon_doc` → UnicodeDecodeError / (JSONDecodeError, ValueError);
#     `_default_connect` → `_JournalUnavailable` (get_connection funnels EVERY failure into it).
#   * `flush_liveness.flush_with_liveness` + `pending_status` DELETED a guard around
#     `run_mode.read_mode`, whose contract is "Never raises" — it was dead code that could only ever
#     have hidden a bug in this module.

_register("team_journal.py", {
    "_edges_present": (DEGRADES_LOUD,
        "DB.S7a — the v5 EDGE-TABLE capability probe on the FLUSH connection, and what makes "
        "'a v4 team degrades byte-identically' (E3) true rather than hoped-for. It must be a PROBE "
        "and not a try/except around the projection itself, and the reason is specific to Postgres: "
        "a statement that errors inside an open transaction ABORTS that transaction, so a "
        "'just try it and catch the missing table' projection would poison the approval group's "
        "transaction and turn a v4 store's every flush into a rollback. Broad for `_apply`'s reason "
        "one entry down — psycopg is an optional extra whose error classes cannot be named at "
        "module scope — and fail-CLOSED, because unknown is not permission. LOUD: a real v4 store "
        "answers NULL without raising and never lands here, so this handler firing means a v5 store "
        "silently stopped projecting; `note_degraded('memory-edges', SCHEMA)` names the drift and "
        "the fix. NOTE the memoizing `setattr` below it carries NO handler on purpose — psycopg3's "
        "Connection has no `__slots__` (checked against the live driver), so guarding it would hide "
        "a genuinely novel connection object behind a silent per-entry re-probe."),
    "_apply_approval_group._apply": (DEGRADES_LOUD,
        "The per-entry DB failure handler, and the reason `_read_remote` can now propagate safely. "
        "A statement (or the CAS-miss re-read) that fails mid-apply leaves the entry PENDING — no "
        "marker is appended, so the replay still reads it as pending and the next healthy flush "
        "re-applies it idempotently (MS.S5). Loud: `note_degraded('team-flush', UNREACHABLE)`. "
        "Broad because `conn` is a psycopg connection and psycopg is an OPTIONAL extra whose error "
        "class cannot be named at module scope — narrowing it wrong would turn a transient DB blip "
        "into a CRASHED flush, which is worse than the swallow."),
    "_ask_conflict": (DEGRADE_CLEAN,
        "Non-interactive stdin → `read_yes_no` raises → fail-CLOSED to 'defer'. The signal comes "
        "out loudly and by contract: the conflict stays CONFLICTED, is counted in "
        "`SyncResult.deferred`, and `mokata sync` prints 'some conflicts need your decision'. It "
        "never silently picks a winner — deferring is the whole point of the handler. "
        "(DB.S6/R4 renamed it from `_decide_conflict`: it now only ASKS — the state change moved "
        "to the ONE resolver, `MemoryStore.apply_proposal`.)"),
    "_ask_group": (DEGRADE_CLEAN,
        "DB.S7d — `_ask_conflict`'s handler, one approval wider, and identical in kind: "
        "non-interactive stdin → `read_yes_no` raises → fail-CLOSED to 'defer'. Deferring is "
        "LOUDER here than in the single case, not quieter: every member of the approval stays "
        "CONFLICTED and ALL of them are counted in `SyncResult.deferred`, so a group that could "
        "not be asked about reports as N un-decided writes rather than as a clean pass. Broad for "
        "the same reason as `_ask_conflict`: `read_yes_no` raises `EOFError` under unittest and "
        "`OSError` under pytest's captured stdin for the same condition, and a handler that names "
        "one of them silently stops fail-closing under the other runner."),
    "_conflict_resolver": (DEGRADES_LOUD,
        "DB.S6/R4 — building the ONE resolver means building a `MemoryStore`, which composes the "
        "configured backend chain (SQLite, Postgres via an OPTIONAL driver, the vault backend); "
        "its raisable set spans classes this module cannot name without depending on the optional "
        "extras. The fallback resolves NOTHING: every conflict stays conflicted and is counted as "
        "deferred, so `sync` reports 'some conflicts need your decision' rather than a clean pass "
        "over conflicts it silently could not touch. Loud: `note_degraded('sync-conflicts', "
        "UNREACHABLE)`."),
    "_group_transaction": (DEGRADE_CLEAN,
        "DB.S6/I1 — a connection object that HAS a `transaction` attribute but cannot produce a "
        "context manager from it. Broad because `conn` is a third-party driver object (or an "
        "injected double) whose failure classes cannot be named at module scope. Returning None "
        "is the documented degrade and it is not silent in effect: the caller falls back to "
        "per-entry apply and announces any PARTIAL outcome through `FAILURE_PARTIAL_APPLY`."),
    "_mark_group_conflicted": (DEGRADE_CLEAN,
        "DB.S6/I1 — the post-rollback re-read of each member's remote row, used only to make the "
        "conflict prompt richer. Broad because it is the same psycopg surface as the apply. The "
        "fallback keeps whatever remote the outcome already carried; what must NOT be skipped is "
        "the conflict MARKER itself, which is why the handler wraps only the read. Losing the "
        "marker would leave the entry pending and retried forever against a row it cannot win."),
    "sync": (DEGRADE_CLEAN,
        "Floor recovery is best-effort and MUST NOT break sync — but it now PRINTS: `emit(f'floor "
        "recovery skipped: {exc}')`. This is the loud channel `_floor_rows` propagates into, so a "
        "floor that cannot be opened is reported instead of read as 'nothing stranded'. Broad is "
        "honest: `recover` is an injected callable, so its raisables are the caller's, not ours."),
})

_register("flush_liveness.py", {
    "load_state": (DEGRADE_CLEAN,
        "Documented fallback contract: a missing/corrupt liveness file reads as a FRESH state. This "
        "file is pure observability metadata (retry counters) — the backlog itself is read LIVE off "
        "the journal, never from here — and a fresh state means 'retry now', which is the safe "
        "direction. Nothing is hidden: `pending_status` still reports the real pending count."),
    "store_state": (DEGRADE_CLEAN,
        "Best-effort persistence of the same observability metadata, by documented contract ('a "
        "write failure never breaks the flush'). The durable record is the JOURNAL; losing a retry "
        "counter costs at most one extra retry attempt and loses no write."),
    "update_state": (DEGRADE_CLEAN,
        "The locked RMW of that same metadata (MS.S6), same documented contract: any failure "
        "returns the state unpersisted rather than breaking the flush. Worst case is a lost retry "
        "increment — bounded, and it loses no write."),
    "clear_state": (SUPPRESS_OK,
        "Unlinks a stale observability file whose ABSENCE is the desired end state ('no backlog → "
        "no machinery'). A failed unlink costs at most one extra backoff read; nothing is lost and "
        "there is nothing to announce — a notice here would be noise on the DRAINED/happy path."),
    "badge_segment": (SUPPRESS_OK,
        "The statusline runs in a SEPARATE process on a zero-network hot path, on every prompt: it "
        "must never break, never hang and never print (a notice shouted into a rendering context is "
        "not a log). The authoritative surface for this exact failure is `pending_status`, which "
        "doctor/MCP call and which DOES announce it (LOCAL_IO). The badge merely goes quiet."),
})

_register("memory/overlay.py", {
    "JournalOverlay._pending": (SUPPRESS_OK,
        "The remaining broad handler is the PER-ENTRY payload decode, not the journal read (that "
        "one narrowed to OSError + a loud LOCAL_IO notice: an unreadable journal used to switch "
        "read-your-writes OFF in silence, which is the CM.S3 bug wearing the overlay's clothes). "
        "The sole producer of these payloads is `store._durable_write`, which stamps `doc` with "
        "`json.dumps(item.to_doc())` for EVERY op (put/update/delete alike), so a malformed entry "
        "is not reachable from mokata's own write path; skipping one is the only sane answer to a "
        "hand-corrupted line, and the OSError case above is where a real failure now surfaces."),
})

_register("memory/tiered.py", {
    "tiered_recall": (DEGRADES_LOUD,
        "TWO handlers, and the loud one is why this is (ii). (a) `backend.semantic_search` failing "
        "used to drop the SEMANTIC TIER in total silence — recall carried on ranking lexical-only "
        "as if semantic had never been configured. It now announces "
        "`note_degraded('memory-semantic', ...)` and still degrades to the lexical floor. It stays "
        "BROAD honestly: `semantic_search` is an index-backed pgvector call whose raisables span "
        "`psycopg.Error` (an OPTIONAL, lazily imported extra — not nameable at module scope), the "
        "embedder's own errors, and the decode of each stored doc; narrowing to the subset we can "
        "NAME would let a driver error crash every recall. (b) The per-item `graph_scorer` hiccup "
        "is a plain (iv): it contributes 0.0 to an OPTIONAL ranking signal, the item still ranks on "
        "lexical + semantic, no result is lost — only a boost — and a per-item notice would be "
        "noise (the tier is off by default anyway)."),
    "lexical_tier": (DEGRADES_LOUD,
        "DB.S3 — `backend.lexical_search` failing must not take recall down with it, so the tier "
        "falls back to the Jaccard floor it replaced. It ANNOUNCES "
        "`note_degraded('memory-lexical', ...)`: the user asked for FTS-ranked recall and is "
        "getting keyword overlap, which is exactly the silence D5 exists to end. BROAD for the "
        "same honest reason as the semantic handler above it — `lexical_search` spans a psycopg "
        "driver error (an OPTIONAL, lazily imported extra, not nameable at module scope), a "
        "sqlite3 error, and the decode of each stored doc; narrowing to what we CAN name would "
        "turn a swallow into a crash on every recall."),
    "_expansion_tier": (DEGRADES_LOUD,
        "DB.S7b (K1) — the ≤2-hop edge expansion. A failed traversal must not take a recall down "
        "with it: the direct matches are already ranked correctly and that IS the pre-DB.S7b "
        "answer, so the fallback is right. The SILENCE would have been the bug, and specifically "
        "so here — with no notice, `the walk failed` is indistinguishable from `this store has no "
        "edges`, and the second is the normal case. A user whose expansion is quietly broken would "
        "see a perfectly plausible recall forever. It announces "
        "`note_degraded('memory-expansion', ...)` and falls back to direct-match ranking. BROAD "
        "for the identical reason as the two handlers above: `expand_from` spans a psycopg driver "
        "error (an OPTIONAL, lazily imported extra — not nameable at module scope), a sqlite3 "
        "error on a store mid-migration, and any third-party adapter's own classes."),
})


# ---------------------------------------------------------------- DB.S4 · the embeddings tier
# Eight handlers arrived with DB.S4 (pgvector wired + the consented embeddings extra). Every one
# of them guards a boundary with an OPTIONAL third-party package on the far side — model2vec,
# huggingface_hub, psycopg, pip — which is precisely the shape that cannot be narrowed: the
# classes live in packages mokata refuses to import at module scope, so an `except` naming them
# would either force the dependency or miss the one class that actually fires.
_register("memory/embed.py", {
    "_load_model2vec": (DEGRADE_CLEAN,
        "The blessed extra's model load. BROAD by necessity — the raisables span huggingface_hub's "
        "error tree, `requests`' transport errors, `safetensors`, and plain OSError on a corrupt "
        "cache, all from an OPTIONAL extra unnameable at module scope. Re-raised as the ONE class "
        "callers catch (`ModelUnavailable`), so nothing is swallowed here; the DEGRADE and its "
        "notice happen one level up in `detect_embedder`, which is where the caller can tell an "
        "absent extra (the zero-dep default, no notice) from an installed-but-broken one (a real "
        "degrade, `note_degraded('memory-embedder')` with the fix)."),
    "embedder_identity": (DEGRADE_CLEAN,
        "Probing an unknown callable's output dimension. The seam accepts ANY `text -> "
        "list[float]`, so this calls CALLER code whose raisables are unknowable by construction. "
        "Fails CLOSED: dim `0`, which `verify_stamp` reads as a mismatch — an embedder mokata "
        "cannot identify is refused onto an index rather than waved through. No notice, because "
        "the refusal itself is the loud part and it carries the named finding."),
})

_register("memory/vector.py", {
    "PgVectorBackend.read_stamp": (DEGRADE_CLEAN,
        "Reading the index's embedder stamp. Three failures — no stamp TABLE (a pre-DB.S4 "
        "provision), a table this DML-only role may not read, a row that is not the (embedder, "
        "dim) pair — and one honest answer: none is a stamp, so all read as UNSTAMPED, which is "
        "the documented pre-DB.S4 behaviour. Deliberately permissive rather than fail-closed: an "
        "unreadable stamp is indistinguishable from an index provisioned before stamps existed, "
        "and refusing every such index would take the semantic tier away from the users who opted "
        "in earliest. The case the binding EXISTS for — a readable stamp that disagrees — still "
        "refuses loudly (`verify_stamp` → `EmbedderStampMismatch` → a named finding naming the "
        "re-embed migration). BROAD because the raiser is the optional psycopg driver's tree."),
})

# ------------------------------------------------------- M-4/R5: the injected SUMMARY DRAFTER seam
_register("memory/consolidation.py", {
    "_draft_summary": (DEGRADES_LOUD,
        "M-4/R5's INJECTED SUMMARY DRAFTER (D9: the harness agent, never an in-process LLM). Broad "
        "by construction — the seam calls FOREIGN caller code (a harness callback that may reach a "
        "subprocess, a socket, or a timeout), so its raisables are unknowable at module scope, the "
        "`embedder_identity` situation exactly. It must not raise: a drafted summary is a nicety, "
        "the consolidation pass is not, and a dead drafter may not take the merge/prune/archive "
        "proposals riding beside it down with it. LOUD rather than clean because the fallback is "
        "VISUALLY INDISTINGUISHABLE from success — the placeholder line reads like a summary, so a "
        "human at the gate would otherwise approve mechanical text believing the turns had been "
        "read. `note_degraded('memory-summary-drafter', PROVIDER)` names the fallback in those "
        "terms — PROVIDER and not ENGINE because the engine loaded fine and a reinstall fixes "
        "nothing; what failed is caller code across a seam. "
        "Only MALFUNCTIONS speak: no drafter is the documented default (a notice on every default "
        "install is noise — the embed.py lesson) and a None return is an explicit decline, which is "
        "an answer, not a fault."),
})

_register("memory/tier_report.py", {
    "_semantic_engine": (DEGRADE_CLEAN,
        "Resolving which embedder doctor should REPORT. Renders `unknown` on any failure. doctor "
        "is the command you run WHEN things are broken, so a duck-typed or half-written surface "
        "must yield a word, not a traceback — a diagnostic that crashes on a broken repo is a "
        "diagnostic that is never there when it is needed. `unknown` is itself printed with its "
        "own explanatory note, so the failure is visible in the report rather than silent."),
    "_lexical_engine": (DEGRADE_CLEAN,
        "The same contract for the lexical axis, plus the store TEARDOWN. Opening the store the "
        "way a recall would is what makes the answer live rather than a guess from config; any "
        "failure to do so is reported as `unknown`. The inner close() handler is teardown of a "
        "read-only diagnostic — nothing to degrade to, nothing a user could act on."),
    "_memory_tool": (DEGRADE_CLEAN,
        "Reading the resolved `memory_store` tool, only to decide whether an unset embedder "
        "setting should be reported as `auto` (an opted-in pgvector store implies it). None on "
        "failure means 'don't infer', which is the conservative half of a purely informational "
        "line — never a finding, never an input to doctor's exit code."),
})

_register("extras_install.py", {
    "install_extra": (DEGRADE_CLEAN,
        "The consent-install VERIFY step. `verify` is a CALLER-supplied probe that imports an "
        "OPTIONAL third-party package and may load a model, so its raisables belong to packages "
        "mokata cannot import at module scope to name. A failed probe means 'not usable' — which "
        "IS the answer the caller needs — so it becomes `ok=False` plus a message, and the caller "
        "stays on its fallback tier. Reported to the user (`note: <extra> is not active — "
        "<message>`), never silent. The pip subprocess itself is NOT in this sweep: it catches the "
        "narrow, named `TimeoutExpired` / `(OSError, ValueError)`."),
})

_register("adoption_modes.py", {
    "offer_mode_extras": (DEGRADE_CLEAN,
        "G1's two post-init OFFERS (embeddings for memory|full, code-graph for full), and the "
        "SAME contract as `onboarding.run_wizard` below — deliberately, because they are the same "
        "step reached by the other route. Both guard an optional, additive offer that runs AFTER "
        "`init_repo` has already written and validated the manifest, so a failure here must not "
        "undo an init that has succeeded. Neither is silent: each prints its own 'note: the "
        "… offer was skipped (<Class>: <msg>); mokata's built-in <floor> is active' AND records "
        "the same string on `ModeOffersResult.notes`, so the reason comes out on the terminal and "
        "in the returned verdict. BROAD because the far side is a pip subprocess plus an "
        "optional-package import probe (embeddings) and a subprocess-backed graph client "
        "(code-graph) — neither's raisables are nameable at this module's scope."),
})

_register("onboarding.py", {
    "run_wizard": (DEGRADE_CLEAN,
        "DB.S4's post-setup embeddings OFFER. Guards an optional, additive step that runs AFTER "
        "the repo is already wired, so a failure here must not abort a setup that has succeeded — "
        "the user simply stays on the built-in hashing tier, and is TOLD so ('note: the embeddings "
        "offer was skipped …; mokata's built-in hashing tier is active'). BROAD because the far "
        "side is a pip subprocess plus an optional-package import probe."),
})


# ------------------------------------------------------------------ hooks + CLI surfaces + badge
# The hook runtime, the shared cli_commands helpers, and the statusline/progress badge.
#
# Two files LEFT this sweep entirely rather than being registered in it, which is the outcome the
# register exists to encourage: `cli_commands/_common.py` and `cli_commands/sync.py` now hold ZERO
# broad handlers. `_common._backend_projects` stopped swallowing altogether (see below), and the
# rest narrowed to the classes their callees actually raise.

_register("hook_cli.py", {
    "_read_stdin_bounded._reader": (SUPPRESS_OK,
        "A torn-down/errored stdin IS 'no input' — there is no other answer to give. The docstring "
        "promises `Never raises`, and the caller falls back to its own default (its own contract). "
        "Nothing degrades: the hook still runs, on the default it would have used anyway."),
    # `secret_guard_main` is NOT registered because it is no longer BROAD — THE headline D5 site
    # left this sweep instead of being justified in it. A broken `govern` import meant every
    # Write/Edit/Bash proceeded UNSCANNED FOR SECRETS, forever, with zero output: a security control
    # silently disabled while the user believed they were protected. It now catches ImportError
    # ALONE (a real bug in `govern` surfaces instead of hiding inside the security no-op), exit 0
    # stays (a hook may never wedge a session), and it emits `note_degraded('secret-guard',
    # FAILURE_ENGINE)`. Asserted by test_hook_secret_guard.TestAnUnimportableEngineDegradesLOUDLY.
    "gate_guard_main": (DEGRADES_LOUD,
        "The fail-open FLOOR. The broad catch and the `return 0` are both CORRECT and both stay — a "
        "floor that only catches the failures we predicted is not a floor, and a gate that wedges "
        "the editor gets uninstalled (an uninstalled gate enforces nothing). But every EXPECTED "
        "case (no file target, not a mokata repo, an unparseable envelope) already returns 0 "
        "explicitly ABOVE, so reaching here is always something genuinely unexpected — and a gate "
        "persistently failing open enforces NOTHING while the badge says governance is on. "
        "`note_degraded('gate-guard', FAILURE_ENGINE)`: it still allows the write, it just says so."),
    "_record_plugin_root": (SUPPRESS_OK,
        "A best-effort CACHE of a hint. Nothing degrades when it fails — `/mokata:init` re-derives "
        "the plugin root — so there is no fallback to announce, and a notice would be noise on a "
        "SessionStart that is working perfectly."),
    "_mcp_reachability_warning": (SUPPRESS_OK,
        "Best-effort ADVISORY about a DIFFERENT failure. A failure here loses only the advisory; no "
        "capability of mokata's degrades. SessionStart is async/observability and must never block "
        "or slow the session, so 'any doubt → say nothing' is the documented contract."),
    "session_start_main": (DEGRADE_CLEAN,
        "Three handlers, and the signal comes out of the one that matters: a broken config `_emit`s "
        "'mokata: bootstrap skipped (<exc>)' straight into the session context, where the user "
        "reads it. The other two guard best-effort OBSERVABILITY writes to gitignored temp_local/ "
        "(the bootstrap-calibration log, the session snapshot) — if they fail, nothing the user "
        "relies on degrades and the briefing is unaffected."),
    "_mokata_segment": (SUPPRESS_OK,
        "Cosmetic: the statusline badge. Documented contract — '\"\" when mokata isn't initialized "
        "here, the badge is disabled, or the engine is unavailable. Never raises.' The harness "
        "re-runs the statusline on every state change, so a notice from here would be the noisiest "
        "line in mokata, and nothing a user relies on for correctness is lost."),
    "_run_wrapped": (SUPPRESS_OK,
        "Best-effort composition of the USER's OWN pre-existing statusLine command. Documented: any "
        "failure/timeout degrades to '' so mokata's badge still renders and the harness never "
        "blocks. Their command failing is their business, not a mokata degrade."),
})

_register("cli_commands/collab.py", {
    "cmd_session": (DEGRADES_LOUD,
        "`_backend_projects` used to SWALLOW a failed query on the shared Postgres into the same "
        "`None` a LOCAL backend returns, so an unreachable backend printed 'session: 0 project(s) "
        "with bundles on the shared backend' — an unreachable backend rendered as an EMPTY one. It "
        "no longer swallows; this handler catches the propagating driver error and degrades with "
        "the REASON (same shape + rc as the construct-failure handler directly above it)."),
})

_register("cli_commands/memory.py", {
    "cmd_memory": (DEGRADES_LOUD,
        "The worst of the three `_backend_projects` lies: a failed query on the shared backend came "
        "back as the same `None` a LOCAL backend returns, so an unreachable Postgres printed "
        "'memory backend: postgres — local/per-repo (single project: X)' — naming the backend as "
        "shared in the same breath it called it per-repo. It no longer swallows; a query failure "
        "now says so, and only a genuinely local backend reaches the `is None` branch."),
})

_register("cli_commands/rules.py", {
    "_cmd_audit_team": (DEGRADE_CLEAN,
        "The signal is the print: 'audit: shared log unavailable (<exc>)'. This handler was ALREADY "
        "honest — it is the one caller of `_backend_projects` that told the truth, and it started "
        "telling MORE of it for free the moment `_backend_projects` stopped swallowing the driver "
        "error it was written to report."),
    "_audit_surface_or_none": (DEGRADE_CLEAN,
        "The signal comes out of the caller: None → 'mokata is not initialized here — no team "
        "audit. Run `mokata init` first.' NOTE for a follow-up: a CORRUPT manifest also lands here "
        "and renders as 'not initialized', which is imprecise (it IS initialized, just broken). Out "
        "of D5's assigned scope; recorded rather than quietly left."),
})

# progress.py — the statusline badge + the todo/progress surface. EVERY handler here returns a safe
# default for a piece of COSMETIC rendering (a badge segment, a counter, a todo list); none of them
# guards a decision, a write, or a governed read. Two reasons the silence is right rather than
# merely convenient: nothing a user relies on for CORRECTNESS degrades (the badge omits or defaults
# a segment), and the harness re-runs the statusline on EVERY state change — a notice from here
# would be the single noisiest line in mokata, which is how loud notices get ignored.
_register("progress.py", {
    "_badge_state": (SUPPRESS_OK,
        "The brainstorm-restore probe. On failure the badge falls through to the checkpoint-derived "
        "stage below — still TRUE, just less specific."),
    "_user_stage_arc_lines": (SUPPRESS_OK, "Guards `_badge_state` (documented: caller guards); no arc rendered."),
    "_worktree_lines": (SUPPRESS_OK,
        "WT.S4. The run's worktree standing appended to the read-only run-progress block. Both "
        "things it can render are ADDITIVE and cosmetic — a binding LINE and a one-per-run OFFER "
        "that creates nothing — so their absence costs a suggestion, never a capability or a "
        "warning. Broad because the callee spans git-worktree probing + the session registry across "
        "three OSes, and `mokata progress` / the `progress` MCP tool must never die rendering a "
        "footnote."),
    "build_stage_badge": (SUPPRESS_OK, "Guards `_badge_state` + `badge_verbosity`; falls back to no badge / BADGE_FULL."),
    "active_skill_surface": (SUPPRESS_OK, "Guards `_badge_state`; no skill surface rendered."),
    "build_todo_items": (SUPPRESS_OK, "An unreadable checkpoint → an empty todo list, not a crashed surface."),
    "_logged_user_stage": (SUPPRESS_OK, "An unreadable progress log → None; the checkpoint still derives the stage."),
    "_shipped_run_ids": (SUPPRESS_OK, "An unreadable progress log → empty set (B-LIFE): NO run reads as shipped, so a run is SHOWN, never wrongly retired — the safe floor for the read-only progress/badge surface."),
    "statusline_enabled": (SUPPRESS_OK, "An unreadable setting → the DEFAULT (True). The badge shows; nothing is lost."),
    "badge_verbosity": (SUPPRESS_OK, "An unreadable setting → the DEFAULT (BADGE_FULL)."),
    "statusline_badge": (SUPPRESS_OK, "An unresolvable run mode → the LOCAL default (the zero-config mode)."),
    "_ledger_tail": (SUPPRESS_OK, "An unreadable ledger tail → []; the badge simply shows no recent activity."),
    "_badge_agents": (SUPPRESS_OK, "An unbuildable lane summary → ''; the badge omits the agents segment."),
    "_develop_counter": (SUPPRESS_OK, "An unbuildable lane summary → ''; the badge omits the develop counter."),
})


# badge_run.py (B-BADGE) — session-scoped run resolution: the statusline BADGE, plus REVIEW-FIX.R1's
# verdict key (`resolve_verdict_run`, registered last with its own reasoning). Every BADGE handler
# here guards a COSMETIC read or a best-effort convenience write; none guards a decision, a
# governed write, or enforcement. The silence is right for the SAME two reasons as progress.py:
# nothing a user relies on for correctness degrades (resolution just falls open to the clean/no-run
# badge or to live-narrowing), and the harness re-runs the statusline on EVERY state change, so a
# notice from here would be mokata's noisiest line. The LOUD degrade for the underlying registry /
# state IO already lives once, at its owner (`session_registry` / `gate_hook._live_runs`); these are
# downstream cosmetic readers of that same state and must not double-announce it.
_register("badge_run.py", {
    "resolve_badge_run": (SUPPRESS_OK,
        "Any resolution error → None (the clean `mokata` badge) — the read-only statusline's safe "
        "floor. The registry IO it reads announces its own failure at its owner."),
    "_single_live_run": (SUPPRESS_OK,
        "The R-MCP narrowing read (reuses `gate_hook._live_runs`, which owns the loud degrade) → "
        "None; the badge falls to the no-run cell rather than guessing a run."),
    "_run_is_shipped": (SUPPRESS_OK,
        "The B-LIFE end-of-run read (delegates to `progress._shipped_run_ids`, which owns the log "
        "read) → NOT shipped; a run that can't be read as finished is SHOWN, never wrongly retired."),
    "read_binding": (SUPPRESS_OK,
        "An unreadable binding file → None, i.e. treated as no binding — resolution falls open to "
        "live-narrowing exactly as if the session were never bound."),
    "bind_session_run": (SUPPRESS_OK,
        "A best-effort convenience WRITE: on failure the binding is simply absent and the badge "
        "resolves via live-narrowing (ii) instead of (i). Nothing a user relies on degrades."),
    "maybe_bind_on_session_start": (SUPPRESS_OK,
        "The SessionStart writer — async/observability, must NEVER block a session. On any failure "
        "no binding is written; the badge falls open to live-narrowing."),
    # REVIEW-FIX.R1 — the verdict-key resolver. Not cosmetic: it feeds the SHIP GATE, and that is
    # exactly why silence is correct here. Its failure value (None) is the STRICTEST outcome — the
    # caller BLOCKS with a legible remedy naming `--run` — so nothing a real failure could hide can
    # pass a gate. The remedy line IS the loud half; a second notice would fire on the read-only
    # statusline path that shares these tiers.
    "resolve_verdict_run": (SUPPRESS_OK,
        "Any resolution error → None, which makes `ship_review_gate` BLOCK ('no run to attribute "
        "it to' + the `--run` remedy). A swallowed failure can only ever make ship STRICTER, never "
        "let an unreviewed change through; the state/registry IO announces itself at its owner."),
    "_bound_run": (SUPPRESS_OK,
        "The tier-(i) binding read (checkpoint existence) → None, i.e. treated as no binding: the "
        "badge falls to live-narrowing and the verdict key falls to the gate hook's resolver, which "
        "refuses on ambiguity. Never a wrong run, only a narrower one."),
    # RE-ENTRY — the approval-key resolver, the third consumer of these tiers. Same reasoning as
    # `resolve_verdict_run`: its failure value is the STRICTEST one available.
    "resolve_run_for_evidence": (SUPPRESS_OK,
        "Any resolution error → None, and None makes `mcp.consent._approval_run` fall back to this "
        "PROCESS's own session id — the narrowest possible key, i.e. exactly the pre-RE-ENTRY "
        "behaviour. A swallowed failure can therefore only ever make an approval HARDER to redeem "
        "(a refusal the human resolves by re-approving), never easier; it cannot widen who may "
        "redeem and cannot reach a commit. The state/registry IO announces itself at its owner."),
})


# --------------------------------------- the SESSION SPINE: bootstrap · worktrees · registry · mode
# The briefing, the worktree manager, the live-session registry, the run mode, the plugin cache and
# the version surface. THREE handlers LEFT this sweep rather than being registered in it, because a
# real failure could pass through them and the user could not have known:
#
#   * `bootstrap._always_on_rule_lines` — the worst of the lot. It swallowed EVERYTHING and returned
#     [], so the project's captured rules & guardrails simply never reached the briefing, and the
#     briefing looked NORMAL: byte-indistinguishable from a project that had captured no rules. The
#     user believed governance was on and it was not. Now narrowed + LOUD (see test_d5_notices).
#   * `bootstrap._render`'s team-health block — `except Exception: pass` deleted the health verdict
#     line AND the work-locally offer, and their ABSENCE is exactly what a healthy LOCAL briefing
#     looks like, so a broken shared DB read as a clean session. It now falls back to an OFFLINE
#     verdict and STILL PRINTS THE LINE (the `degrade.resolve_read_routing` fallback SHAPE).
#   * `worktree.is_changed` — a failed `git status` probe read as UNCHANGED, so `remove(force=False)`
#     DELETED a worktree that may have held uncommitted work and ledgered `ok=True, changed=False`.
#     It now FAILS CLOSED: an unknown status is CHANGED, the worktree is kept, and the audit row
#     says "status probe failed" rather than asserting a cleanliness nobody ever observed.
_register("bootstrap.py", {
    "build_bootstrap": (SUPPRESS_OK,
        "Transient live-session-registry upkeep (`session_registry.touch`), explicitly NOT part of "
        "the briefing: the comment above it pins the contract — 'a registry hiccup must never "
        "affect the bootstrap output/budget'. Nothing the user relies on degrades; `mokata windows` "
        "simply misses one window, and the very next touchpoint re-registers it."),
})

_register("run_mode.py", {
    "_db_checks": (DEGRADE_CLEAN,
        "The signal IS the return value: a probe failure becomes `PreflightCheck('shared-database', "
        "False, 'probe failed: <exc>', fix=<the pooler hint>)`, which renders in the fail-closed "
        "'team mode: NOT READY — activation refused' report. Team mode is never half-activated, and "
        "the refusal names the exception it caught."),
})

_register("session_worktree.py", {
    "create_worktree": (DEGRADE_CLEAN,
        "Two handlers. (1) The `git worktree add` runner: belt-and-braces around a runner whose "
        "contract is never-raise, and the signal comes out anyway — it emits 'git worktree add "
        "failed (<exc>) — nothing created.' and returns `WorktreeCreateResult(created=False, "
        "reason=…)`. (2) `session_registry.touch(scope=…)` AFTER the worktree was created: transient "
        "upkeep of a scope LABEL; the worktree exists and is reported either way."),
    "binding_line": (SUPPRESS_OK,
        "WT.S4. The run↔worktree binding rendered onto the read-only run-progress block. A binding "
        "that cannot be resolved is indistinguishable from no binding — which is what an unbound "
        "run (the common case) looks like — so None is both the safe and the HONEST answer. Broad "
        "because it spans the registry + `repo_identity` path resolution."),
    "emit_run_start_offer_once": (SUPPRESS_OK,
        "WT.S4. The run-start worktree OFFER. It is TEXT that creates nothing, so its absence "
        "costs a suggestion and never a capability — and the failure direction is the SAFE one: no "
        "offer means no worktree, which is exactly the never-automatic posture the offer exists to "
        "protect. Broad because it spans git probing + the registry."),
    "merge_ready_handoff": (SUPPRESS_OK,
        "WT.S4. Ship's merge-ready branch handoff. None is the same answer an UNBOUND run gets, "
        "and an unbound run is defined to produce no handoff prose and no error — so the fallback "
        "lands ship exactly where it landed before WT.S4 rather than breaking the phase. The work "
        "is not lost by the silence: the branch and the worktree are both still on disk."),
})

_register("session_registry.py", {
    "list_sessions": (DEGRADES_LOUD,
        "D5b. The read-only window list. Its docstring ALREADY promised 'an unreadable/locked "
        "registry lists nothing rather than raising into the read-only `mokata windows` path' and "
        "the code did not keep it: the typed handler's own fallback (`store.read`) re-opened the "
        "same file the locked RMW had just failed on, unguarded, so an unreadable (OSError) or torn "
        "(ValueError) registry raised straight out of the degrade path. Broad IS the contract here "
        "— the callers are read-only views (`mokata windows`, the statusline, the MCP read tool) "
        "where an exception wedges the view rather than degrading a feature, and an empty list is "
        "exactly what 'no registry' means. It is a (ii), not a swallow, because it now SPEAKS: "
        "`note_degraded('session-registry', local-io, …)` announces the failure once on stderr and "
        "in doctor, and carries the exception text as the notice's `detail` — so even the "
        "AttributeError this could otherwise hide stops reading as 'no windows open'."),
})

_register("mcp/server.py", {
    "_serve.wrapper._run": (DEGRADE_CLEAN,
        "MCP-R.D0 · R2. The systemic dispatch wrapper runs each tool BODY in a worker thread and "
        "catches ANY uncaught exception so it can reclaim the outcome into mokata's own voice — a "
        "structured `status:\"error\"` + `isError` + `reason` (the exception TYPE name) + an "
        "actionable `hint`, returned as the tool result the model sees. Broad IS the contract: the "
        "body is an arbitrary tool whose failure class is not nameable here, and the WHOLE point is "
        "to convert every failure into the typed vocab instead of letting FastMCP's generic handler "
        "(or a daemon-thread traceback onto stdio) speak for mokata. It is a (i) DEGRADE_CLEAN: the "
        "signal comes out AS the verdict the caller receives — nothing is swallowed, the failure is "
        "named. `str(exc)` is deliberately NOT surfaced (it can carry a DSN/path/arg)."),
    "_register_this_window": (DEGRADES_LOUD,
        "R-MCP. The MCP server self-registers its run in the MS.S2 live-session registry on the "
        "first tool call (and refreshes on every subsequent one) so the gate hook can soundly "
        "disambiguate windows. Broad IS the contract: `session_registry.touch` spans identity "
        "minting, PID/OS probing, and cross-process-locked transient-file IO, and none of that is "
        "worth failing a user's tool call over — registering this window is pure side-effect upkeep "
        "beside the tool the user actually asked for. It is a (ii), not a swallow, because it "
        "SPEAKS: `note_degraded('session-registry', local-io, …)` announces the failure once on "
        "stderr and in doctor and carries the exception text as `detail`; the hook then simply "
        "stays fail-open on ambiguity (its pre-R-MCP behaviour), so nothing is silently promoted."),
})

_register("mcp/tools_spec.py", {
    "_graph_required_emit_refusal": (SUPPRESS_OK,
        "GR.S3 — a best-effort MCP-loop BACKSTOP for the Lens-1 graph.required gate: it reads the "
        "persisted brainstorm's chosen-approach radius and refuses `spec_emit` on a degraded one. It "
        "fails OPEN (returns None → the emit proceeds to the normal gates) because the primary "
        "enforcement is the engine `approve()` gate; a fault reading persisted state must never turn "
        "this redundant guard into a NEW failure mode for a write the user asked for. Broad because "
        "restoring a session spans state IO, JSON parsing, and settings reads."),
    "_prior_art_emit_refusal": (SUPPRESS_OK,
        "GR-PA-WIRE — the MCP-loop enforcement of the prior-art step-ran gate: it reads the durable "
        "`approved_approach` Handoff and refuses `spec_emit` when the chosen approach's prior-art "
        "step never ran. Like its GR.S3 sibling it fails OPEN (returns None → the emit proceeds) on "
        "any read fault, because a fault reading persisted state must never become a NEW failure "
        "mode for a write the user asked for — the gate REFUSES on a positively-read not-run verdict, "
        "never on an inability to read. Broad because loading the Handoff spans state IO + JSON "
        "parsing. (Distinct from the fail-CLOSED verdict itself: a legacy/missing prior_art is a "
        "read that SUCCEEDS and returns not-run → refusal; this handler only catches read FAULTS.)"),
    "_code_anchor_emit_refusal": (SUPPRESS_OK,
        "H-6 S4 WIRE — the MCP-loop enforcement of the code-anchor freshness gate, and the third "
        "member of the family directly above: it reads the durable `approved_approach` Handoff and "
        "refuses `spec_emit` when the chosen approach's prior-art CITATIONS are anchored to code "
        "that has moved. Fails OPEN for the same reason both siblings do — a fault reading "
        "persisted state must never become a NEW failure mode for a write the user asked for, and "
        "that argument is stronger here because this gate is itself a new failure mode (a "
        "deliberate contract change, CHANGELOG 0.0.16). Unlike the prior-art sibling it is ALSO "
        "fail-open on absent EVIDENCE, not just on read faults: no baseline is no opinion (H-6 "
        "decision #6), so there is no not-run-shaped verdict to refuse on. Broad because the frame "
        "spans state IO, JSON parsing, a per-anchor file hash, and (for symbol anchors) an ADOPTED "
        "graph client whose failure classes are a third party's."),
    "_knowledge_layer": (SUPPRESS_OK,
        "H-6 S4 WIRE — builds the adopted code graph for the gate above, the same way "
        "`MemoryStore.from_surface` does (`store.py:267`, itself registered SUPPRESS_OK for this "
        "reason). NO GRAPH IS A VALID ANSWER, not a fault: the overwhelmingly common case is a repo "
        "that has adopted none, and `None` is exactly what the anchor-shape split expects — symbol "
        "anchors then decline. Announcing it would fire on every emit in every un-adopted repo to "
        "report the absence of an optional capability."),
})
_register("cli_commands/spec.py", {
    "_emit_knowledge_layer": (SUPPRESS_OK,
        "H-6 S4 WIRE — the CLI twin of `mcp/tools_spec.py:_knowledge_layer` directly above, "
        "identical reasoning and identical fallback. It is a SEPARATE function rather than a shared "
        "import because the two surfaces already keep their gate plumbing separate (see the "
        "`_prior_art_emit_refusal` / CLI pair); what must be shared is the VERDICT, and that is "
        "`handoff_code_anchor_gate`, which both call."),
})
_register("mcp/consent.py", {
    # RE-ENTRY — the two new reads at the consent boundary. NEITHER can reach a commit: one picks
    # the KEY a proposal is filed under, the other only formats a surfacing line.
    "_approval_run": (SUPPRESS_OK,
        "Resolving WHICH run an approval belongs to. On any fault it falls back to "
        "`session.current_run_id()` — this process's own id, the NARROWEST key there is and exactly "
        "the pre-RE-ENTRY behaviour. So a swallowed failure can only make an approval harder to "
        "redeem (a named refusal the human clears by re-approving), never easier: it cannot widen "
        "who may redeem, cannot skip `_verify`, and cannot reach a commit — consent still requires "
        "an on-disk record a human minted out-of-band. Broad because resolution spans state IO, "
        "JSON parsing, env and the registry read; narrowing it would just re-list those."),
    "_other_pending": (SUPPRESS_OK,
        "The 'other writes already awaiting you' ids for the shared `awaiting_block` head. Purely "
        "ADDITIVE surfacing over `approval.pending`: on any fault it names no others, which is the "
        "pre-RE-ENTRY result — the proposal, its id and both commands out are unaffected. A "
        "surfacing read must never be able to fail the propose it is decorating (the same "
        "discipline as the `awaiting` reporters). The store IO announces itself at its owner."),
})
_register("mcp/tools_approve.py", {
    "_in_chat_enabled": (DEGRADE_CLEAN,
        "AP-MCP. Gates the in-chat `approve` tool on `settings.approvals.in_chat`. Broad IS the "
        "contract because the failure mode must FAIL CLOSED to a named default: an uninitialized / "
        "unparseable / unreadable manifest reads as OFF (returns False), so a broken config can "
        "NEVER hand the model an in-chat approve surface. `config_get` funnels several failure "
        "classes (ManifestError, ConfigError, OSError) into 'no such setting'; the signal is the "
        "default-OFF verdict itself — the tool then returns `_disabled(...)`, which names the "
        "human-gated TTY enable path to the user's face. Default-OFF is the documented posture, so "
        "swallowing to it loses nothing."),
    "_approval_seq": (SUPPRESS_OK,
        "Cosmetic. Echoes the audit-ledger seq of the `approved` entry the call JUST wrote, purely "
        "so the transcript can point at the record. The approval has ALREADY committed at this "
        "point; a ledger re-read failure omits the `ledger_seq` field and nothing else — the id is "
        "a convenience, not the authority. Registered SUPPRESS_OK: never fail an already-minted "
        "approval over a cosmetic echo."),
})

_register("version.py", {
    "check_for_update": (DEGRADE_CLEAN,
        "The signal is the returned verdict: 'couldn't check for updates (offline or unreachable) — "
        "you're on mokata <v>.' A blocked/failed opt-in egress is the DOCUMENTED offline path (the "
        "one outbound call in mokata), and it says so to the user's face."),
    "detect_install_method": (SUPPRESS_OK,
        "Guards `plugin_cache.read_plugin_root`, whose own docstring promises None rather than an "
        "exception ('or None if absent/unreadable/empty') — a promise it keeps by narrowing to "
        "(OSError, UnicodeDecodeError). There is no honest class to name for a callee that cannot "
        "raise, and `install_method` is a cosmetic label on the `mokata version` line."),
})

_register("harness_setup.py", {
    "_write_json": (SUPPRESS_OK,
        "The BaseException cleanup-then-re-raise, and the reason BaseException is CORRECT here: a "
        "KeyboardInterrupt landing mid-write must ALSO delete the temp file, and `except Exception` "
        "would miss exactly that. It swallows nothing — it unlinks the temp file and `raise`s."),
})

# ------------------------------------------------------------ the MEMORY store: identity · access
# The remaining handlers in the store are of two kinds, and neither can hide a real failure from the
# user. The ACCESS checks are fail-CLOSED guards around a policy that is itself fail-closed and
# never raises; the NEVER-RAISE guards wrap callees whose own docstrings promise they cannot raise
# (`run_mode.read_mode`, `project.derive_project_id`, `team_audit.actor`, `flush_liveness
# .pending_status`) — there is no narrower class to name for an exception a callee says is
# impossible, and inventing one would be a word no failure ever earns.
#
# The latent bug D5 RECORDED here — `_identity_and_access_for` setting `access = None` on error,
# which turned team-mode enforcement OFF (FAIL-OPEN, the opposite of the "fail-closed" its own
# docstring claimed) — was CLOSED in D5b: the team-mode fallback is now a deny-by-default
# `AccessPolicy` (enforce=True, zero grants). The broad handler registered below is the OTHER one in
# that function (the identity guard), which is unchanged. See test_d5b_fail_closed.py.
# PRE-SIMP (0.0.15) — `_identity_and_access_for` moved store.py -> memory/selection.py (the backend
# build/select/scope/identity extraction); its broad identity guard is re-registered there, verbatim.
_register("memory/selection.py", {
    "_identity_and_access_for": (SUPPRESS_OK,
        "Guards `team_audit.actor()`, whose contract is never-raise. An unresolvable identity is not "
        "a degraded capability — the write path stamps the placeholder author and carries on."),
})
# PRE-SIMP (0.0.15) — the TEAM-mode journal-first write + best-effort flush moved store.py ->
# memory/team_writer.py (the injected team-writer seam); their broad guards ride the TeamWriter
# methods now (store's `_journal_team_write`/`_best_effort_flush` are thin, except-free delegators).
_register("memory/team_writer.py", {
    "TeamWriter._item": (DEGRADE_CLEAN,
        "DB.S6/R3 — parsing one side of a conflict out of a JSON doc written by ANOTHER machine, "
        "possibly by another mokata version. Broad because every way that blob can be wrong (torn "
        "JSON, a non-dict, a missing key, a type the item model rejects) lands here and the answer "
        "to all of them is the same one: this side cannot be shown. It degrades to None, which the "
        "conflict prompt renders honestly as 'theirs: unreadable' rather than inventing a value the "
        "human would resolve against. Narrowing it would let a teammate's malformed row break a "
        "READ path (`detect_issues`), which is the failure this arm exists to prevent."),
    "TeamWriter.journal_write": (SUPPRESS_OK,
        "Two guards, both around never-raise callees: `project.derive_project_id` ('Never raises') "
        "and `team_audit.actor`. The journal entry is written either way — only its `project` label "
        "and `who` attribution fall back, and the WRITE itself is never at risk."),
    "TeamWriter.flush": (DEGRADE_CLEAN,
        "The signal is CM.S4's whole point: a failed flush is COUNTED, not forgotten. The backlog "
        "surfaces as the statusline's `N pending` segment and doctor's 'N approved write(s) "
        "journaled locally and NOT yet flushed to the team DB' — so a swallow here cannot hide a "
        "stranded write; the journal still holds it and the surfaces still say so."),
})
_register("memory/staleness.py", {
    "read_index_epoch": (DEGRADES_LOUD,
        "DB.S7c2 (STALE-REF) — reading the minting store's `index_epoch` to stamp onto a citation. "
        "Broad because every way it can fail says ONE thing to the caller — we could not establish "
        "an epoch — and none of them are nameable at module scope (psycopg is an optional extra). "
        "The MISSING-capability path never reaches the handler: the SQLite floor has no "
        "`index_epoch` at all and is answered by the `hasattr` probe above, silently and correctly, "
        "because STALE-REF was never on there. So arriving HERE means a backend that CLAIMS the "
        "capability could not answer — and that is precisely the case that must not be silent: "
        "citations go out un-stamped, the approve-path check never fires again, and a team that "
        "switched STALE-REF on by moving to Postgres would go on believing it is protecting them. "
        "Nothing downstream would ever attribute a never-firing gate to a driver error weeks "
        "earlier. Degrading to OFF (not raising) is still the right direction — a stamp is "
        "evidence, and evidence that cannot be gathered is absent evidence, never a broken recall. "
        "`note_degraded('stale-ref', SCHEMA)` names the loss and its fix."),
})
_register("memory/store.py", {
    "MemoryStore._moved_code_anchors": (DEGRADES_LOUD,
        "H-6 S3 — the code-anchor evidence behind the CODE_ANCHOR_STALE arm. It must not take the "
        "OTHER arms down with it (contradiction, near-dup and cross-writer are unaffected by a "
        "failure to hash a file), so it degrades to 'no moved anchors' and `detect_issues` still "
        "answers. LOUD for the reason `_attach_subgraph` directly below is: silence here is "
        "uniquely misleading. This arm is the ONLY thing that would tell a human the code under a "
        "decision moved, and a clean governance view is exactly what they would read as "
        "confirmation that their anchors are current — the reassurance-shaped failure. It "
        "announces `note_degraded('memory-code-anchors', LOCAL_IO)`. Broad because the frame spans "
        "a JSON record read, per-file hashing, and (for symbol anchors) an ADOPTED graph client "
        "whose failure classes are a third-party's and not nameable at module scope."),
    "MemoryStore._attach_subgraph": (DEGRADES_LOUD,
        "DB.S7c1 (K2) — the edge read behind a conflict's subgraph. It must NOT take the conflict "
        "down with it: a CAS conflict is an approved write that has not landed, and dropping it to "
        "protect a piece of context would invert the priority the whole arm exists to set. So it "
        "falls back to `subgraph=None` and the conflict still surfaces. LOUD because the silence "
        "is uniquely misleading here — showing no relations is indistinguishable from an item that "
        "genuinely has none, which is the COMMON case, so a broken read reads to a human as "
        "reassurance right before they overwrite something. It announces "
        "`note_degraded('memory-subgraph', ...)`. BROAD for the same reason as the expansion and "
        "recall handlers: `open_edges` spans a psycopg driver error (an OPTIONAL, lazily imported "
        "extra not nameable at module scope), a sqlite3 error on a store mid-migration, and any "
        "third-party adapter's own classes."),
    "MemoryStore._subgraph_visible": (SUPPRESS_OK,
        "DB.S7c1 — silence is correct and the FALLBACK is the whole point: an unreadable scope set "
        "returns an EMPTY set, not None. None means 'no scope context' and prunes nothing, which "
        "would show every dst id to an identity whose visibility we just failed to establish; the "
        "empty set prunes every item-target edge instead, so the subgraph degrades to code anchors "
        "and discloses nothing. No second notice is owed — the `_attach_subgraph` handler above "
        "already announces a broken store read, and two notices for one failure trains the reader "
        "to skim them."),
    "MemoryStore._team_mode": (SUPPRESS_OK,
        "Guards `run_mode.read_mode`, whose docstring says 'Never raises'. False = LOCAL = the "
        "fail-closed direction (an unknown mode is NEVER team)."),
    "MemoryStore.pending_status": (SUPPRESS_OK,
        "Guards `flush_liveness.pending_status`, which is degrade-clean to None by contract."),
    "MemoryStore.from_surface": (DEGRADE_CLEAN,
        "The knowledge-layer build for the graph RECALL TIER. The signal comes out of the briefing: "
        "the SessionStart 'Capabilities (resolved now)' block renders `code_graph -> UNAVAILABLE (no "
        "provider present)` / `(degraded; preferred … absent)`. Recall keeps its lexical+semantic "
        "tiers — the documented grep floor, which 'silently contributes nothing' by design."),
    "MemoryStore.recall_relevant": (DEGRADE_CLEAN,
        "The same graph tier, at query time (`make_graph_scorer`). Same signal (the capability "
        "block), same floor: lexical+semantic always hold."),
    "MemoryStore._can_read_item": (SUPPRESS_OK,
        "Guards `AccessPolicy.can_read` → `roles_for`, which is ITSELF fail-closed and never raises "
        "('deny on doubt' — it catches and returns an empty role set). Deny is the safe direction: "
        "an item you may not see is absent, which is what the policy decided anyway."),
    "MemoryStore._access_denied_edit": (SUPPRESS_OK,
        "Guards `AccessPolicy.can_edit` (itself fail-closed/never-raises). And the deny is LOUD "
        "regardless — the caller returns the 'access denied: …' refusal message to the user."),
    "MemoryStore._can_approve": (SUPPRESS_OK,
        "Guards `AccessPolicy.can_approve` (itself fail-closed/never-raises). Deny-on-doubt, and the "
        "refusal is surfaced by the caller."),
    "MemoryStore.promote_scope": (SUPPRESS_OK,
        "Guards `AccessPolicy.can_promote_scope` (itself fail-closed/never-raises). The refusal is "
        "returned to the user: 'access denied: <who> lacks the promotion-approver role for …'."),
    "MemoryStore._superseded_items": (SUPPRESS_OK,
        "M-1/R9 (S2) — resolves the items a write would RETIRE, purely so the gate prompt can show "
        "whose approved memory is about to be displaced. Silence is correct because the failure "
        "costs a DECORATION, and raising would cost the DECISION: this runs inside `render_write`, "
        "i.e. while building the approval prompt itself, so an unreadable backend or a dangling id "
        "would take out the human's ability to approve anything about that item — a cosmetic defect "
        "escalated into a gate outage. Nothing is suppressed about the WRITE: the item still goes "
        "through the WriteGate, the secret scan and the human gate unchanged, and a missing prior "
        "renders as one fewer provenance line, which is honest (we could not read it, so we do not "
        "claim it). Deliberately no notice — the reader is looking at the prompt right now, and the "
        "absent block IS the signal."),
    "MemoryStore.record_usage": (SUPPRESS_OK,
        "DB.S5 — THE degrade-clean seam for usage telemetry, and the whole reason it is a seam. "
        "This is a WRITE riding a READ, so the failure it must never have is turning a recall into "
        "something that can fail: a failed counter bump is swallowed and reported as False, and "
        "the recall it rode has already produced its answer. Deliberately SILENT (no notice): it "
        "fires on a read-only store, a v3 team schema or a locked file — none of which the user "
        "needs to hear about mid-recall, and none of which makes the returned answer wrong. Broad "
        "by necessity: it spans a psycopg driver error (an OPTIONAL extra, not nameable at module "
        "scope), a sqlite3 error, and any third-party adapter's own classes."),
    "MemoryStore.usage_signals": (DEGRADE_CLEAN,
        "DB.S5 — the READ half of the same telemetry. Returns `{}`, and `{}` is the ZERO signal, "
        "which the fusion scores as 0.0 recency + 0.0 usage — i.e. the PRE-DB.S5 three-term "
        "ranking, which is a correct ranking. The signal is a ranking BOOST, not a result, so "
        "losing it costs an improvement and never a row. No notice on purpose: a notice would be "
        "an announcement that a feature failed to make things better, which is noise. Broad for "
        "the same reason as the writer above."),
})

_register("memory/migrate.py", {
    "migrate_memory": (DEGRADE_CLEAN,
        "Two handlers. (1) The destination CAS-base read: an unreachable destination mid-migration "
        "ABORTS and writes NOTHING, returning `MigrateResult(aborted=True, error=\"destination "
        "'<to>' unreadable: <exc>\")` — the loudest possible answer. (2) `team_audit.actor()`, a "
        "never-raise callee; the placeholder author is used."),
})

# -------------------------------------------------- (iii) the OPTIONAL psycopg driver (lazy import)
# Every handler below guards a call on a psycopg connection object. The real classes are psycopg's
# (`psycopg.OperationalError` & co), and naming them at module scope would mean importing the
# optional `postgres` extra into mokata's dependency-free core — which is the exact bug the lazy
# import exists to prevent. `memory/intelligence.py` shows the honest alternative where it is worth
# it (a lazily-built except tuple that names psycopg's `Error` only when the extra is installed);
# for a teardown `close()` there is nothing to degrade TO and nothing a user could act on, so the
# broad catch stays and says why.
_register("memory/_pg.py", {
    "get_connection": (NARROW_IS_HONEST,
        "Catches ANY psycopg connect failure and RE-RAISES it as the caller's typed `unavailable` "
        "('database unavailable: <exc>') — a `DegradedCapability` carrying the reason. It converts, "
        "it does not swallow; and the class it converts FROM lives in the optional extra."),
    "_is_live": (NARROW_IS_HONEST,
        "A third-party connection's `.closed` property. False → reconnect, the safe direction."),
    "reset_manager": (NARROW_IS_HONEST,
        "Best-effort `close()` on cached psycopg connections during teardown."),
})

_register("memory/backends.py", {
    "PostgresBackend._read_edges_present": (DEGRADES_LOUD,
        "DB.S7a — the v5 EDGE-TABLE capability probe on the shared store, and the exact twin of "
        "`team_journal._edges_present` (registered there with the same reasoning; two call sites, "
        "one contract). Broad because every way it can fail says the same thing to the caller: an "
        "ancient server without `to_regclass`, a psycopg error class that cannot be named at module "
        "scope (optional extra), a dead connection — none of them establish that the table is "
        "there, and unknown is not permission. Fail-CLOSED to False, because projecting into a "
        "table that may not exist would ABORT the caller's Postgres transaction, which is a far "
        "worse outcome than skipping a derived projection. LOUD rather than silent, and this is the "
        "load-bearing half: a genuinely v4 store answers this probe SUCCESSFULLY with NULL and "
        "never reaches the handler, so arriving here means something else went wrong on a store "
        "that may well be v5 — and the consequence (a projection that quietly stops tracking its "
        "docs) is exactly the kind of drift nobody would attribute to the cause. "
        "`note_degraded('memory-edges', SCHEMA)` names it and its fix (`mokata team init`, whose "
        "backfill re-derives the whole projection)."),
    "PostgresBackend.close": (NARROW_IS_HONEST,
        "Teardown `close()` on a psycopg connection — the driver's classes are not nameable without "
        "a hard dependency on the optional extra, and the connection is being dropped either way."),
    "SQLiteBackend.fts5_available": (DEGRADE_CLEAN,
        "DB.S3's FTS5 CAPABILITY PROBE — the handler IS the answer. FTS5 is a compile-time SQLite "
        "option, so 'what does this sqlite3 do when it lacks FTS5' is precisely the question being "
        "asked; `sqlite3.OperationalError` is today's answer but the contract depended on is 'the "
        "CREATE did not work'. False ⇒ the lexical tier is the Jaccard floor, and `tiered.lexical_"
        "tier` announces THAT (`note_degraded('memory-lexical')`) — the loudness lives where the "
        "user-visible capability is lost, not in the probe."),
    "SQLiteBackend._ensure_fts": (DEGRADE_CLEAN,
        "DB.S3 index provisioning (virtual table + sync triggers + backfill). Broad because the "
        "probe already said FTS5 EXISTS, so anything raising here is an environment fault — a "
        "`memory_fts` created with a different shape, a read-only store, a malformed `doc` the "
        "backfill's json_extract rejects. None of it justifies failing a whole store's "
        "construction: False ⇒ the Jaccard floor, which is what the floor is FOR, and the degrade "
        "is announced by `tiered.lexical_tier` on the first recall."),
    "PostgresBackend._read_scope_backfilled": (SUPPRESS_OK,
        "DB.S2b's BACKFILL-STAMP PROBE — a capability probe like `fts5_available` directly above, "
        "and the handler IS the answer. It asks 'has this shared store's scope backfill run?' by "
        "reading `mokata_schema_version.scope_backfilled`; a missing column (a pre-DB.S2b "
        "artifact), a missing table, or any driver fault are all the SAME answer — not proven, so "
        "no. False ⇒ no scope predicate is pushed and `scope.union_read` filters from the item "
        "`doc` instead, which is the pre-DB.S2b read path: the RESULT IS IDENTICAL, only slower. "
        "So nothing a caller relies on can pass through unannounced — there is no degraded answer "
        "to announce, and this is the fail-CLOSED direction (a True on doubt would filter on stale "
        "columns and silently drop another tenant's rows). A genuine connection fault does not "
        "hide here either: the very next real query raises it loudly."),
})

_register("memory/vector.py", {
    "PgVectorBackend.close": (NARROW_IS_HONEST,
        "Teardown `close()` on a psycopg connection (same as PostgresBackend.close)."),
})


# ------------------------------------- the READ-ONLY SURFACES: docs · code graph · views · PR check
# The subsystems whose job is to TELL THE USER SOMETHING. A silent degrade is at its most dangerous
# here, because the output of a disarmed checker is not an error — it is a clean bill of health. Four
# of these (docsync's sweep, the govern view's memory panel, the session diff, the PR check's
# spec-awareness leg) printed the word "OK"/"PASSED"/"0 items"/"no changes" when the thing they were
# meant to read had FAILED to read. Each now carries a `note_degraded()` notice AND marks its own
# result degraded, so an unchecked result can never be read as a checked one.
_register("docsync.py", {
    "_check_symbols": (DEGRADES_LOUD,
        "The code-graph `resolve(sym)` predicate is INJECTED, so its class is the CALLER's (a graph "
        "client, an MCP tool) and is not nameable here — the catch stays broad. What changed is that "
        "the skip is COUNTED: a throwing resolver used to skip every symbol silently and let the doc "
        "be declared FRESH. It now emits `note_degraded('docsync-symbols', ...)` and marks the audit "
        "degraded, so a doc whose symbol check never ran cannot render as 'OK'."),
})

_register("knowledge/graph_backend.py", {
    "CodeReviewGraphBackend.query": (DEGRADE_CLEAN,
        "Converts ANY injected-client/subprocess failure into the typed `BackendError` and RAISES "
        "it. It swallows nothing: `KnowledgeLayer._run` catches that BackendError, falls to the grep "
        "floor, and (D5) `make_graph_scorer` now announces the fall. Broad because the client is a "
        "'bring your own tool' boundary — its failure class is the adopted tool's, not mokata's."),
    "CodeReviewGraphBackend.semantic": (DEGRADE_CLEAN,
        "GR.S2 twin of `.query` for the adopted graph's semantic index: converts ANY client/"
        "subprocess failure into the typed `BackendError` and RAISES it — swallows nothing. "
        "`KnowledgeLayer.semantic` catches that BackendError and degrades LOUD (empty result, "
        "degraded=True, note). Broad for the same bring-your-own-tool boundary reason as `.query`."),
    "CodeReviewGraphBackend.resolves": (SUPPRESS_OK,
        "GR.S2 rider — docsync symbol-existence check. A graph hiccup during resolution must NOT "
        "manufacture a false 'stale symbol' finding on a valid doc, so a failure returns True "
        "(assume present) — the conservative, no-false-positive default. The audit's own "
        "AuditDegradation records when the check was disarmed; this catch only guards a single "
        "lookup from turning a transient error into a wrong finding."),
    "CodeReviewGraphBackend.recover": (SUPPRESS_OK,
        "GR.S2(k) one bounded OPERATIONAL recovery attempt (re-probe -> refresh -> re-probe). It "
        "must be TOTAL — any failure means 'did not recover' (False), and `KnowledgeLayer._run` "
        "then degrades LOUD to the AST floor with a `note_degraded` notice. Silence is correct "
        "HERE because the caller announces the degrade; recover() is only the run-state signal."),
})

_register("execmode/review_graph.py", {
    "graph_verify": (SUPPRESS_OK,
        "GR.S2(m) THIN verification slice. Each per-symbol graph lookup is wrapped so ONE symbol's "
        "hiccup skips just that symbol and the bounded verification continues on the rest — it is "
        "NOT the enforcement gate (review's pass/fail is untouched; these are surfaced findings). "
        "The layer already degrades LOUD on a real backend failure inside `_run`; graph_verify's "
        "own `degraded` flag announces a graph-absent verify. So a skipped symbol is not a silent "
        "wrong answer — it is one fewer advisory note on a best-effort pass."),
})

_register("deprecation.py", {
    "warn_deprecated": (SUPPRESS_OK,
        "SIMP.S2 — the broad catch wraps ONLY the best-effort `ledger.record(deprecation_notice)` "
        "AFTER the notice has already printed to stderr (the user has SEEN the deprecation). The "
        "ledger row is a redundant audit trail of a visible event; a failure to write it must not "
        "crash the read hot path (this runs from `build_backend`) nor undo the warn the user "
        "already got. Silence is correct — nothing is hidden that was not already shown."),
})

_register("knowledge/graph_adopt.py", {
    "adopt_graph": (SUPPRESS_OK,
        "GR.S2(j) best-effort semantic PROVISIONING after a successful adopt (operational, not "
        "the pin itself, which already committed through the gate). A failure here — e.g. the "
        "[embeddings] extra isn't installed — is announced with a legible note and leaves the "
        "graph structural-only; it must never fail the adoption that already landed. Silence is "
        "wrong (hence the note); the broad catch keeps a provisioning hiccup from unwinding a "
        "committed manifest write."),
})

_register("knowledge/crg_client.py", {
    "CodeReviewGraphClient.health": (SUPPRESS_OK,
        "GR.S2 liveness pre-check — a TOTAL probe that must never raise (like a statusline read). "
        "It answers True/False; a broad failure IS unhealthy (False). Silence is correct HERE "
        "because the CALLER (the keep-functional machinery) is what announces the degrade LOUDLY "
        "and degrades to the AST floor — health() is only the cheap signal, never the enforcement."),
})

_register("knowledge/neo4j_backend.py", {
    "Neo4jGraphClient.__init__": (NARROW_IS_HONEST,
        "`driver.verify_connectivity()` fails in the neo4j DRIVER's own classes "
        "(`ServiceUnavailable`/`AuthError`), which are only reachable through the optional, lazily "
        "imported extra — and the driver may itself be an injected double. Nothing is swallowed: it "
        "re-raises the typed `Neo4jUnavailable`, which `select_backends` now reports LOUDLY."),
    "connect_neo4j_client": (NARROW_IS_HONEST,
        "`neo4j.GraphDatabase.driver()` raises the driver's own `ConfigurationError` for a bad URI. "
        "Same lazy-import argument; same conversion to the typed `Neo4jUnavailable`."),
    "Neo4jGraphClient.close": (SUPPRESS_OK,
        "Cleanup on the way out. The client is being discarded — there is nothing left to degrade "
        "and no read depends on it."),
})

_register("dashboard.py", {
    "build_governance_view": (SUPPRESS_OK,
        "Guards ONLY the `compute_session_diff` block (the memory read beside it is now narrowly "
        "caught and LOUD). The callee is degrade-clean and emits its OWN classed notice for the "
        "failure that matters; this guard exists so a COSMETIC 'since last session' panel cannot "
        "take the whole governance view down. Broad because the callee spans memory + ledger + "
        "snapshot IO, and re-listing its classes here would drift from it."),
})

_register("visibility.py", {
    "changed_since_line": (SUPPRESS_OK,
        "ONE cosmetic line in the SessionStart briefing, and the briefing must start even if it "
        "cannot. The callee already emits the classed notice for the failure that matters (an "
        "unreadable memory store) and now returns a `degraded` diff, which this function SURFACES "
        "rather than swallowing — so silence here loses no signal."),
})

_register("mcp/tools_read.py", {
    "status": (SUPPRESS_OK,
        "Two handlers, both guarding ADDITIVE, optional keys on an otherwise-complete status "
        "response. (a) `pending`: `resolve_read_routing` never raises on the read path and carries "
        "its own DegradeNotice, so the degrade is reported elsewhere; the only thing this can lose "
        "is a COUNT. (b) DOC-ONBOARD `wiring`: `hook_wiring.wiring_drift` is itself never-raise "
        "(it returns `checked=False` rather than throwing), and the same verdict also reaches the "
        "user via the SessionStart briefing and `mokata doctor --wiring` — so this can only ever "
        "cost one advisory key on one of three surfaces."),
    "session_windows": (SUPPRESS_OK,
        "Two handlers: (a) `SR.touch` — WRITE-side self-registration inside a READ tool; failing to "
        "register affects only whether THIS window appears in the list, and corrupts nothing. (b) "
        "`SW.offer_text_once` — a one-time, purely ADDITIVE worktree offer (data, never an action). "
        "Both are broad because they span PID probing + transient-file IO across three OSes, and "
        "neither may break a listing that is otherwise complete."),
    # REVIEW-FIX.R3 (0.0.16) — the 6r review loop's in-harness half. Both mirror the CLI twins
    # registered above under `cli_commands/runviews.py`, deliberately: one truth source read and
    # written through two surfaces must not degrade in two different directions. R4 moved BOTH
    # pairs together, which is what R3 left the blur here FOR.
    "review_status": (DEGRADE_CLEAN,
        "Fail-CLOSED by construction, and that is where the signal comes out: the ONLY thing this "
        "handler can produce is `blocks: True` — a STOP the caller must act on — so an unreadable "
        "gate can never let an unreviewed change ship. Documented in the tool's own docstring "
        "('an unreadable gate reports a block rather than raising'), and it is the same contract "
        "the CLI twin `cmd_progress_review_status` carries. Broad because the span reaches run "
        "resolution (session registry + `state/` probing) and a JSONL scan across three OSes. "
        "`{exc}` is NOT echoed into the message — a parse error can quote the offending log line, "
        "and a `review_verdict` line carries FINDINGS text, which may quote project content; this "
        "tool's contract is that no findings reach any answer, including this one. REVIEW-FIX.R4 "
        "made that bar binding on the CLI twin too, and closed the last divergence: this answer no "
        "longer claims 'review hasn't run' when the read RAISED — it reports `readable: false` with "
        "the shared read-error sentences, the same pair the CLI prints."),
    "review_record": (DEGRADE_CLEAN,
        "Returns the failure IN the result, and REVIEW-FIX.R4 made it LOUD: `recorded: false`, "
        "`status: 'error'`, `satisfies_gate: false`, a `reason` naming `{exc}`, and a `message` "
        "from the SAME `review_record_failed_line` builder its CLI twin prints as it exits "
        "non-zero. Broad because appending to the shared progress-event JSONL spans filesystem, "
        "locking and encoding faults, and the review skill must never break on the act of writing "
        "down its own verdict. Safe in the same direction as its twin: nothing recorded means the "
        "ship gate finds no verdict and BLOCKS. Echoing `{exc}` is safe here (unlike "
        "`review_status`) because this is a WRITE fault carrying no log content, and the findings "
        "text is the CALLER'S OWN argument on this call."),
})

_register("worktree_list.py", {
    "build_worktree_report": (DEGRADE_CLEAN,
        "The registry read behind the session half of the join. Where the signal comes out: this "
        "handler's ONLY effect is `registry_ok=False`, which is on the returned report, PRINTED by "
        "`render()` ('the session registry could not be read'), carried in the MCP payload, and — "
        "the part that makes it a (i) rather than a swallow — FORCES every unresolvable worktree to "
        "the `unknown` verdict instead of the false `no-session`. A read failure is reported as a "
        "read failure, never as evidence of absence (P16). The callee `list_sessions` contracts "
        "never to raise and emits its own classed notice on the way, so this guard is "
        "belt-and-braces over a documented never-raiser; broad because that callee spans PID "
        "probing + transient-file IO across three OSes."),
    "_row": (SUPPRESS_OK,
        "Guards `run_for_branch`, whose own contract is degrade-clean (it returns None on any "
        "registry/identity fault — session_worktree.py). The only outcome this handler can produce "
        "is 'no run resolved', which is the SAME state an unbound worktree already has and is "
        "handled explicitly below it; it can promote nothing and hide no verdict. Broad because "
        "the callee reaches the session registry + repo identity across three OSes, and a "
        "read-only lister must never traceback into the surface it is rendering."),
})

_register("schema.py", {
    "_optional_jsonschema_errors": (NARROW_IS_HONEST,
        "D5 TRIED to narrow this and was WRONG to. The failure it guards is 'a PRESENT but "
        "INCOMPATIBLE jsonschema', which fails from inside its OWN internals in its OWN classes — "
        "and the dep is lazily imported by design, so nothing is in scope at module level. "
        "`test_manifest.test_present_but_validator_raises_degrades` pins that contract. Silence is "
        "also correct: the pass is ADDITIVE polish, and the authoritative built-in structural checks "
        "below it ALWAYS run — losing it loses phrasing, never a verdict."),
})

_register("legibility.py", {
    "_color_enabled": (SUPPRESS_OK,
        "`stream` is ANY object an embedder handed us (a StringIO, a closed file, a custom writer "
        "with no `isatty`), so its failure class is not ours to name. Fail-safe by construction: "
        "every failure returns False = NO colour. The thing withheld is an ANSI escape, and "
        "withholding it is always safe — there is no state in which this hides a degrade."),
})

_register("crossplat.py", {
    "current_user": (SUPPRESS_OK,
        "`getpass.getuser()` raises DIFFERENT classes on different platforms for the SAME condition "
        "(KeyError from the POSIX `pwd` lookup with no passwd entry; OSError on Windows; OSError on "
        "POSIX from 3.13). Naming them would encode a version+platform matrix that goes stale. "
        "Nothing degrades: '' is the documented 'unresolvable user', and the callers that NEED an "
        "identity (team writes) fail-CLOSED on it with a named fix."),
})

_register("degrade.py", {
    "resolve_read_routing": (DEGRADE_CLEAN,
        "Guards the `team_health.check` probe and fails CLOSED to `HealthVerdict(OFFLINE, 'health "
        "check failed')`. OFFLINE is a TROUBLE state, so the verdict it produces is precisely the "
        "one that raises the ⚠ badge and drives this module's own DegradeNotice — the failure comes "
        "OUT as the loud signal. Broad because the probe reaches the optional psycopg driver, and "
        "because a routing decision on the memory read path must NEVER raise (a health check that "
        "died must not take the read with it). The `read_mode` handler beside it was narrowed."),
})

_register("team_health.py", {
    "check": (DEGRADE_CLEAN,
        "`except Exception as exc: HealthVerdict(OFFLINE, f'probe failed: {exc}')` — the verdict "
        "CARRIES the reason and renders as ⚠ + the work-locally offer on every surface. The probe is "
        "documented as itself fail-closed and this refuses to trust it; broad because a probe of an "
        "optional driver over a network can fail in the driver's classes. Nothing is hidden: the "
        "exception text is what the user reads."),
    "store": (SUPPRESS_OK,
        "A CACHE write, and the verdict it caches has ALREADY been returned to the caller — every "
        "surface renders the correct health with or without it. A failed write costs one extra "
        "≤500ms probe; it cannot make a broken connection look healthy (an un-cached read is "
        "UNKNOWN, which shows no ⚠ and claims nothing). Observability must not break what it "
        "observes. NOTE: `cached_or_neutral`'s handler was DELETED, not narrowed — `read_mode` "
        "cannot raise, so it was dead code that would have returned LOCAL (a clean, healthy-looking "
        "badge) to a team user whose mode lookup broke."),
})

_register("__init__.py", {
    "package_data_root": (SUPPRESS_OK,
        "`importlib.resources.files` is the OPTIMISTIC path; its failure classes belong to the "
        "packaging ecosystem (a zipimport/frozen loader, an embedder's custom finder). The fallback "
        "is not a degraded answer — it is the SAME answer by a more direct route (this module's "
        "directory IS the package directory), so there is nothing to announce."),
    "_force_utf8_io": (SUPPRESS_OK,
        "Runs at IMPORT time on whatever `sys.stdout` happens to be, which an embedder may have "
        "replaced. The failure class is the EMBEDDER's. Failing changes nothing about correctness — "
        "a glyph may fall back to the legacy Windows encoding, which is COSMETIC and which the "
        "`ascii_only` renderings exist for. An import-time raise would make mokata unimportable."),
})


def broad_handlers():
    """Every broad handler in src/, keyed by (relpath, qualname) -> list of (lineno, kind).

    AST, not grep: a `# except Exception` in a docstring or a comment must not count, and a handler
    nested three functions deep must not be attributed to the module."""
    found = {}
    for root, _dirs, files in os.walk(SRC):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            # POSIX-style, always: REGISTER's keys are the canonical form, and `os.path.relpath`
            # hands back `cli_commands\collab.py` on Windows — which matches no key, so every
            # handler reads as unregistered AND every entry reads as stale. Normalise here, at the
            # comparison boundary (the same `.replace(os.sep, "/")` the D1/D2 and SI.6 sweeps use).
            rel = os.path.relpath(path, SRC).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            _walk(tree, rel, [], found)
    return found


def _walk(node, rel, stack, found):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _walk(child, rel, stack + [child.name], found)
            continue
        if isinstance(child, ast.ExceptHandler):
            t = child.type
            if t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException")):
                kind = "bare" if t is None else t.id
                qual = ".".join(stack) or "<module>"
                found.setdefault((rel, qual), []).append((child.lineno, kind))
        _walk(child, rel, stack, found)


# ------------------------------------------------------- the engine + governance internals (D5)
# The gate/ledger/enforcement CORE — `govern/gate.py`, `govern/ledger.py`, `govern/tdd.py`,
# `govern/trust.py`, `govern/resume.py`, `engine/spec.py`, `execmode/orchestrator.py` — holds ZERO
# broad handlers and appears nowhere in this register. That is worth stating out loud: the sweep
# expected the worst rot at the centre and found the centre clean. What it found instead was that
# the SURFACES which READ the governance substrate (doctor) and the LENSES which advise on it
# (spec-awareness, amend, decompose) were the ones swallowing.

_register("engine/amend.py", {
    "begin_amend": (DEGRADES_LOUD,
        "THE governance find of the stage. This is gate 2 — the BLAST-RADIUS lens — re-run "
        "precisely when an amendment WIDENS scope. It used to set `plan.impact = None`, leave "
        "`plan.ok = True`, record nothing, and approve the widened amendment AS IF THE LENS HAD RUN "
        "AND FOUND NOTHING. The inline comment excusing it ('degrade-clean: no graph → no lens') "
        "was factually false: `compute_impact` is itself degrade-clean and does NOT raise when the "
        "graph is missing, so nothing reaching this handler was ever the no-graph case. It now "
        "FAILS CLOSED (`plan.ok = False`, gate `blast-radius`) — a gate that fails open silently "
        "must not fail open at all. Stays broad because the graph backend is a pluggable optional "
        "dep; broad + fail-CLOSED is safe, broad + fail-open was the bug."),
})
_register("engine/emit.py", {
    "mark_emitted": (DEGRADES_LOUD,
        "The resume checkpoint was lost silently, so P17's 'resume from the last PASSED gate' "
        "silently resumed from the WRONG point. `phases.py:_mark_gate_passed` already handled the "
        "IDENTICAL failure loudly via `session_flow.note_persist_failure`; emit simply never called "
        "it. Now it does — the existing channel, not a second one."),
    "note_supersede": (DEGRADES_LOUD,
        "SPEC-REEMIT-CLOBBER. The ledger entry is what makes a SUPERSEDED spec reachable (no "
        "archive-read tool ships), so losing it silently would leave the archived version sitting "
        "at a key nothing names — history that exists but cannot be found. The spec itself is "
        "already committed and must NOT be failed by its own bookkeeping, so this rides the same "
        "`session_flow.note_persist_failure` channel `mark_emitted` above uses, with its own key. "
        "BROAD ON PURPOSE, exactly as there: the ledger is a caller-supplied object whose failure "
        "modes are not knowable here."),
})
_register("engine/phases.py", {
    "_mark_gate_passed": (DEGRADE_CLEAN,
        "The precedent emit.py was missing: already calls `note_persist_failure('gate:' + phase)`, "
        "a loud once-per-moment stderr warning. The phase itself succeeded; only its crash-safety "
        "checkpoint is best-effort, and the user is told when even that fails."),
})
_register("engine/spec_awareness.py", {
    "expand_touch_set": (DEGRADES_LOUD,
        "It returned `degraded=False` unconditionally, so when the graph was wired but every query "
        "raised, the report announced 'mode: graph-expanded touch-set' while running on the bare "
        "lexical floor — false confidence, and a spec about a CALLER of the change goes unseen. The "
        "flag now tells the truth and `note_degraded('code-graph')` says so. Broad: the graph "
        "backend is a pluggable optional dep whose error classes are not nameable at module "
        "scope."),
})
_register("execmode/decompose.py", {
    "_graph_expand": (DEGRADES_LOUD,
        "A faulting symbol was skipped while `graph_backed` stayed True if any OTHER symbol "
        "succeeded — so two dependent subtasks were declared independent and the plan recommended "
        "running them as RACING parallel subtasks, labelled 'graph-verified'. Now any fault drops "
        "`graph_backed` (and with it the parallel recommendation) and appends a warning. "
        "'Graph-verified' must mean the graph was actually asked."),
})
_register("govern/graph_required.py", {
    "graph_required_enabled": (SUPPRESS_OK,
        "An unreadable/absent manifest → the SAFE default (required-on). Mirrors "
        "`progress.statusline_enabled`: a config read that falls to its documented default, not a "
        "capability degrade. Broad because a malformed manifest can raise from any layer of the "
        "settings read."),
})
_register("govern/gate.py", {
    "WriteGate._carried_forward_seq": (SUPPRESS_OK,
        "UX-CONFIRM (D7b) — reads the ledger to answer 'has this exact content already been "
        "approved in this run'. The guarded call is `ledger.entries()`, i.e. file I/O, and the "
        "failure direction is what makes silence correct: this function's only power is to say "
        "'do not ask again', so failing to read it means the human IS asked. An unreadable ledger "
        "costs an extra confirmation prompt — never a skipped one, never a skipped gate. That is "
        "the fail-CLOSED direction, and it is the same call `approval.from_state` makes (an "
        "unreadable record is not an approval, so it grants nothing). "
        "Nothing is suppressed about the WRITE: self-protect, the trust dial, the secret scan and "
        "governance enforcement all run BEFORE this is consulted and are untouched by it. "
        "Deliberately no notice — a degrade notice for 'you were asked to confirm something' would "
        "fire on exactly the safe outcome, and the extra prompt is itself the visible signal."),
})
_register("govern/enforce.py", {
    "evaluate": (DEGRADE_CLEAN,
        "Explicit fail-CLOSED: doubt becomes a VISIBLE, non-overridable HARD violation. The "
        "loudest possible outcome — the run stops and the user is told."),
})
_register("govern/hooks.py", {
    "run_async_hook": (DEGRADE_CLEAN,
        "The failure is captured into `HookResult.message` AND written to the ledger — that IS the "
        "contract of an async hook: it cannot block the run, so it reports instead."),
})
_register("govern/tokens.py", {
    "log_calibration": (SUPPRESS_OK,
        "Pure OBSERVABILITY (a token-count calibration row). Documented never-raises; the caller "
        "checks None. Nothing the user relies on degrades if it fails — losing a calibration sample "
        "changes no behaviour and no verdict, so there is no fallback to announce."),
    "log_bootstrap_calibration": (SUPPRESS_OK,
        "Same, on the SessionStart hot path — where an exception would cost the user their briefing "
        "to save a metrics row."),
})
_register("session_flow.py", {
    "SessionFlow.checkpoint": (DEGRADE_CLEAN,
        "Calls `note_persist_failure` — a loud, once-per-moment, secret-safe stderr warning. This "
        "is the channel `engine/emit.py` was missing."),
})

# ------------------------------------------------------------------------ MCP admin + the team DB
_register("mcp_admin.py", {
    "_terminate": (SUPPRESS_OK,
        "Tearing down a DOOMED subprocess's pipes, with a `kill()` fallback right below. There is "
        "nothing left to save and nothing to tell the user: the handshake failure itself is already "
        "being reported by the caller."),
    "handshake": (SUPPRESS_OK,
        "A best-effort `wait(timeout=2)` reap before reading stderr. Failing to reap loses nothing — "
        "the handshake verdict is decided by what was (or was not) read, not by the reap."),
    "handshake._reader": (DEGRADE_CLEAN,
        "A stream torn down mid-read yields an empty line, which fails CLOSED to a loud `error` "
        "HandshakeResult. The failure surfaces as the verdict; it is not swallowed."),
    "status_lines": (DEGRADE_CLEAN,
        "Returns `(False, ['mokata-mcp: status check skipped ({exc})'])` — not-connected AND the "
        "cause, printed."),
    "full_status": (DEGRADE_CLEAN,
        "Returns a FullStatus of all-False carrying 'status check skipped ({exc})'. An "
        "informational path that never raises, and never claims health it did not verify."),
    "grant_status": (DEGRADE_CLEAN,
        "Fail-CLOSED direction: a scope that cannot be read reads as NOT granted, so doctor prints "
        "`permitted ✗` plus the fix. Erring toward 'not granted' can only ever cost the user an "
        "unnecessary re-grant; erring the other way would claim a permission that does not exist."),
    "unreachable_registration": (SUPPRESS_OK,
        "A second net over a pure path-builder whose own reads already narrow-catch OSError and "
        "JSONDecodeError internally. Nothing can reach it, and nothing degrades if it does."),
    "version_parity": (SUPPRESS_OK,
        "B-VER — a second net over `resolve_registered` (whose own `_read_server` already "
        "narrow-catches OSError/JSONDecodeError, over a pure path-builder). If the impossible "
        "happened it falls to an `unregistered` finding, which `status_lines`/`full_status` "
        "already report on the same surfaces — nothing health-claiming is asserted, nothing lost."),
    "scope_shadow": (SUPPRESS_OK,
        "B-VER — a second net over the same pure path-builder + narrow-catching `_read_server`. "
        "The fail-clean direction is 'no shadow' (a MISSED warning, never a wrong action); a "
        "scope-shadow risk is advisory, so silence here can only cost an un-flagged duplicate, "
        "never a bad write."),
    "parity_lines": (DEGRADE_CLEAN,
        "B-VER — the shared reporter. Its `except Exception` RETURNS "
        "`['mokata-mcp: version-parity check skipped ({exc})']`, naming the cause on the same "
        "line it prints — the status_lines/full_status pattern. The failure is announced, not hid."),
})
_register("awaiting.py", {
    "pending_lines": (DEGRADE_CLEAN,
        "MCP-R.D2 — the shared 'waiting on you' reporter (the parity_lines / "
        "skills_visibility_lines pattern). Its `except Exception` RETURNS "
        "`['mokata pending: check skipped ({exc})']`, naming the cause on the line it prints. "
        "This one is load-bearing: the fail-clean direction MUST NOT be an empty list, because "
        "`pending_lines` renders empty as 'nothing is waiting on you ✓' — a health claim it did "
        "not verify, on the surface a stuck user runs to find out why they are blocked. Hence "
        "`_pending` does not catch at all and this handler announces instead of returning []."),
    "liveness_lines": (DEGRADE_CLEAN,
        "MCP-R.D2 — returns `['mokata liveness: budget check skipped ({exc})']`, naming the cause "
        "on the line it prints. It only reads two D0 budget CONSTANTS, so nothing can realistically "
        "reach it; if an import did break, saying so beats asserting a bound mokata could not read."),
    "statusline_segment": (SUPPRESS_OK,
        "MCP-R.D2 — the COSMETIC half of the same read. A statusline segment renders on every "
        "harness tick and must never break the statusline (nor print an error into a one-line "
        "badge), and its failure costs nothing that is not already reported: the same wait is "
        "carried by the tool result unconditionally and by `pending_lines` loudly. Silence here "
        "hides no state that another surface does not announce — which is exactly what makes it "
        "SUPPRESS_OK where the doctor reporter above is not."),
    "_rides_harness_prompt": (SUPPRESS_OK,
        "MCP-R.D2/UX-NOTIFY — classifying whether THIS wait raises a harness permission prompt. "
        "Fails toward 'no harness channel', which can only ever make mokata's OWN signal more "
        "visible, never less: the caller then relies on the statusline + the unconditional "
        "tool-result head. A second net besides — `mcp_admin.grant_status` (registered above) "
        "already never raises."),
    "_statusline_wired": (SUPPRESS_OK,
        "MCP-R.D2/UX-NOTIFY — 'will the statusline segment actually reach a human?'. Unknown reads "
        "as False, which only DROPS a channel from an advisory classification; it never suppresses "
        "the wait itself (the tool-result head is unconditional) and never gates a write."),
})
_register("hook_wiring.py", {
    "hook_wiring_report": (DEGRADES_LOUD,
        "HOOK-RESOLVE — the read-only wiring check that answers 'would the gates actually fire?'. "
        "Broad on purpose: it runs inside `mokata doctor`, the command you reach for when things "
        "are ALREADY wrong, so a hand-mangled settings.json or an exotic home must not crash the "
        "diagnosis. What it must never do is fall to a bare EMPTY report — empty is the honest "
        "answer for 'no mokata hooks are wired here', so returning it for 'the check blew up' "
        "would tell a plugin user their enforcement plane is fine when nobody looked, which is "
        "the exact class of silence this whole stage exists to kill. The failure is carried out "
        "as `HookWiringReport.unverifiable`, which `govern.doctor.hook_resolution_findings` "
        "reports as an error-severity `hooks-unverifiable` finding naming the cause. "
        "`_resolves` is NOT registered because it is no longer broad — it narrowed to "
        "(ImportError, OSError, ValueError), the whole of what a which/path-exists probe raises."),
    "wiring_drift": (SUPPRESS_OK,
        "DOC-ONBOARD — 'is the wiring STALE?', a different question from the one above and with a "
        "different honest failure answer. It runs on the SessionStart path and inside every MCP "
        "`status` call, so it is broad for the same reason `hook_wiring_report` is: a weird "
        "settings.json must not break a session. It cannot lose a verdict the way an empty report "
        "could, because it does NOT fall back to 'current' — it returns `checked=False`, which "
        "every caller renders as SILENCE, never as a clean bill of health. The loud story for an "
        "unreadable wiring surface is already owned by `hooks-unverifiable` above, and saying it "
        "twice in two voices is how a user learns to ignore both."),
})
_register("skills_visibility.py", {
    "skills_visibility": (DEGRADE_CLEAN,
        "B-SKILLS — the read-only visibility check. Its `except Exception` returns an "
        "`uncheckable` SkillsVisibilityFinding whose `render` prints `mokata skills: could not "
        "check visibility ({detail})` — the failure BECOMES the verdict, printed on a doctor root "
        "that must never crash. Nothing health-claiming is asserted; nothing is hidden."),
    "skills_visibility_lines": (DEGRADE_CLEAN,
        "B-SKILLS — the shared reporter (the parity_lines pattern). Its `except Exception` RETURNS "
        "`['mokata skills: visibility check skipped ({exc})']`, naming the cause on the line it "
        "prints. A second net over `skills_visibility` (which already never raises)."),
    "briefing_offer": (SUPPRESS_OK,
        "B-SKILLS — the SessionStart OFFER (the WT.S1 `offer_text_once` shape). On any failure it "
        "returns None (no offer). An offer is advisory: its absence costs nothing and must never "
        "break the briefing; the underlying `skills_visibility` never raises, so this is a second "
        "net."),
    "_plugin_shadow": (SUPPRESS_OK,
        "B-SKILLS — a second net over `mcp_admin.plugin_shadow` (whose own reads narrow-catch "
        "OSError/JSONDecodeError). Falls clean to None (no plugin note); the fail-clean direction "
        "is 'no shadow' — a MISSED advisory note, never a wrong action or a bad write."),
})
_register("teamdb.py", {
    "_read_schema_version": (NARROW_IS_HONEST,
        "It inspects `exc.sqlstate` and RE-RAISES anything it does not recognise — the opposite of "
        "swallowing. It cannot narrow to `psycopg.Error`: psycopg is an OPTIONAL extra, lazily "
        "imported by design, so naming its class at module scope would make the core depend on the "
        "very driver this branch exists to tolerate the absence of."),
    "_run_ddl": (NARROW_IS_HONEST,
        "Raises `ProvisionError` — DDL fails CLOSED and clean. Same optional-psycopg reason."),
    "table_present": (NARROW_IS_HONEST,
        "Raises the caller's typed `unavailable(...)`. Same optional-psycopg reason."),
    "probe": (SUPPRESS_OK,
        "An `importlib.util.find_spec` guard; falling through still yields the fail-closed probe "
        "verdict below."),
    "probe._work": (DEGRADE_CLEAN,
        "Records `box['error'] = str(exc)`, which becomes `ProbeResult(reachable=False, error=…)` → "
        "`team_health.classify` → OFFLINE → the ⚠ badge. The model citizen of the codebase: the "
        "exception becomes the verdict."),
})
_register("db_doctor.py", {
    "deep_check": (DEGRADE_CLEAN,
        "DB.S1 — the DSN deep-check's fail-closed probe guard. `teamdb.probe` is itself fail-closed, "
        "but this never trusts it to be: any escape becomes "
        "`ProbeResult(reachable=False, conn_reason=CONN_NETWORK_UNREACHABLE, error=str(exc))`, which "
        "`classify` turns into the NAMED, printed `db-network` finding in doctor's `database (team "
        "DSN)` section (and flips `report.ok`). The exception becomes the verdict — nothing silent. "
        "Broad because `probe` may be an injected callable whose raisables are the caller's, not "
        "ours, and the real driver class (psycopg) is an optional, lazily-imported extra."),
})
_register("team.py", {
    "driver_present": (SUPPRESS_OK,
        "An `importlib.util.find_spec` probe — its whole purpose is to answer a yes/no question, and "
        "any failure IS the 'no'."),
    "_join_vault": (DEGRADE_CLEAN,
        "Returns `JoinStep('vault', 'skipped', '…not a readable mokata vault ({exc}) — skipped.')`, "
        "printed in the join report."),
    "_join_verify": (DEGRADE_CLEAN,
        "Returns `JoinStep('verify', 'skipped', 'doctor could not run ({exc}).')` — surfaced."),
})

# ---------------------------------------------------------- the never-raises contracts + cleanup
# THE LESSON OF THIS GROUP, and the one correction D5 had to make to its OWN sweep: a handful of
# these sites were first classified TOO-BROAD and narrowed — and the narrowing BROKE THEM, because
# their documented contract is not "catch the classes we predicted", it is literally "never raises,
# whatever the surface does". For a function on the hook hot path that is not sloppiness; it is the
# reason the hook is allowed to wrap the user's editor at all. Narrowing them made their docstrings
# lies and re-armed dead-code guards that `flush_liveness` had deleted precisely because
# `read_mode` cannot raise. They are registered SUPPRESS_OK, and the tests that pin them raise an
# ARBITRARY exception on purpose — that is the contract being asserted, not an accident of fixture.
_register("run_mode.py", {
    "stored_mode": (SUPPRESS_OK,
        "Documented 'never an exception'. Backs `read_mode`, which runs in the SessionStart / "
        "statusline / gate-guard hooks — separate short-lived processes wrapping Claude Code, where "
        "an escaping exception does not degrade a feature, it WEDGES THE EDITOR. Fail-CLOSED and "
        "therefore safe (absent → local; an unknown mode is NEVER team), and a surface broken enough "
        "to raise here is reported loudly by doctor's manifest checks on the next line — so silence "
        "costs the user no information."),
})
_register("dsn.py", {
    "_manifest_data": (SUPPRESS_OK,
        "Documented 'any broken/absent surface reads as an empty manifest → the default env name, "
        "never raises'. Same class as `run_mode.stored_mode`, and same reason not to narrow it: the "
        "contract IS the broad catch. Nothing is hidden — an unset/undiscovered DSN surfaces "
        "immediately and loudly downstream as the health verdict '$MOKATA_PG_DSN is not set'."),
})
_register("memory/access.py", {
    "AccessPolicy.roles_for": (DEGRADE_CLEAN,
        "Deliberate fail-CLOSED — deny on doubt, documented — and the denial reaches the user as an "
        "explicit refusal at every call site. Silence is impossible: the user is told NO."),
})
_register("atomicfile.py", {
    "atomic_write_text": (SUPPRESS_OK,
        "The one legitimate `except BaseException` in the codebase: unlink-the-temp-file then "
        "`raise`. It must be BaseException so a KeyboardInterrupt mid-write also cleans up, and "
        "nothing is swallowed — every exception is re-raised unchanged."),
})
_register("baseline.py", {
    "baseline_status": (DEGRADE_CLEAN,
        "Reports RED *with the reason* — the loudest possible in-band signal."),
})
_register("brainstorm_impact.py", {
    "compute_impact": (DEGRADE_CLEAN,
        "Sets `degraded=True`, which the impact report renders as lowered confidence. This is the "
        "flag `spec_awareness.expand_touch_set` was NOT setting (see above) — same situation, and "
        "this module already got it right."),
})
_register("memory/intelligence.py", {
    "_field": (SUPPRESS_OK,
        "M-1/R9 (S2) — one provenance FIELD rendered as text for the gate's highlight block. The "
        "guarded call is `str(value)`, and the only way it raises is a value whose `__str__` does: "
        "a provenance dict is doc JSON, so it can be hand-edited, imported, or written by a build "
        "that modelled the field differently. It degrades to the literal word `unknown`, which is "
        "the module's honest answer for an absent fact anyway — so the failure mode and the "
        "no-data mode render identically, and neither overstates what mokata knows. Silence is "
        "right for the same reason as `_superseded_items`: this is the approval PROMPT being "
        "built, and a render that threw would deny the human the decision. Read-only — it computes "
        "nothing durable and mutates no item."),
})
_register("prior_art.py", {
    "_safe": (SUPPRESS_OK,
        "GR-PA — the per-query wrap for the prior-art step, the exact parallel of "
        "`review_graph.graph_verify`: ONE existing-implementations lookup failing skips just that "
        "query and the bounded, evidence-gathering pass continues. Prior art is ADVISORY and never "
        "the enforcement gate — the step-RAN gate refuses only a MISSING step, never a failed query, "
        "and the result's `tier` (+ empty `no prior art found via <tier>`) is honest about how hard "
        "mokata looked. So a skipped query is one fewer extend-candidate on a best-effort pass, not "
        "a silent wrong answer."),
})
_register("branch_protection.py", {
    "check_branch_protection": (DEGRADE_CLEAN,
        "Fail-CLOSED FAIL verdict naming the error; the release refuses."),
})
_register("cli_commands/runviews.py", {
    "cmd_progress_mark": (SUPPRESS_OK,
        "Two handlers: transient run-registry upkeep (commented 'registry upkeep never breaks "
        "recording a stage' — the stage IS recorded, only the index is best-effort), and one that "
        "PRINTS `could not record '{stage}' ({exc}); continuing`. REVIEW-FIX.R4 left the return-0 "
        "posture here DELIBERATELY while making its `record-review` sibling exit non-zero, and the "
        "code says why: a `stage_enter` moves a BADGE, a `review_verdict` is the GATE EVIDENCE ship "
        "refuses to proceed without. Losing the first costs a cosmetic; do not unify by symmetry."),
    "cmd_progress_record_review": (DEGRADES_LOUD,
        "REVIEW-FIX.R4 — was a (i): it printed `could not record the verdict ({exc}); continuing` "
        "and returned 0, so a review whose verdict was LOST exited GREEN and nothing reading the "
        "exit code could tell it from a recorded one. It now prints the shared "
        "`review_record_failed_line` — the failure, its CONSEQUENCE ('ship's review gate will BLOCK "
        "as if review never ran'), and the terminal remedy — and returns "
        "`RECORD_REVIEW_FAILED_EXIT` (1, not the cluster's gate-BLOCK 2; the reasoning is at the "
        "constant). Still broad, and still must not raise: appending to the shared JSONL spans "
        "filesystem, locking and encoding faults, and the review skill must not break on the act of "
        "writing down its own verdict. `{exc}` is echoed because this is a WRITE fault — it carries "
        "no log CONTENT, unlike the read path's (see `cmd_progress_review_status`)."),
    "cmd_progress_review_status": (DEGRADE_CLEAN,
        "Fail-CLOSED: returns 2, unchanged and non-negotiable — an unreadable gate can never let an "
        "unreviewed change ship. REVIEW-FIX.R4 fixed WHAT IT SAYS on the way out. It printed "
        "`review hasn't run — run /mokata:review first ({exc})`, which was two defects in one line: "
        "it named a remedy (re-run review) that cannot fix a log that will not read, and it echoed "
        "`{exc}` — a parse fault quotes the offending line, and a `review_verdict` line carries "
        "FINDINGS text, which may quote project content. It now prints the shared "
        "`review_read_error_message`/`_unblock` pair: fault named by KIND, located by PATH, no "
        "`{exc}`. Broad because the span reaches surface load, run resolution (session registry + "
        "`state/` probing) and a JSONL scan across three OSes. This handler covers only a read that "
        "RAISED; the unreadable/damaged-log distinction itself lives in `ship_review_gate`, so both "
        "surfaces get the same two answers from one place."),
    "cmd_windows": (SUPPRESS_OK,
        "Degrade-clean self-registration + a one-time worktree OFFER. The rows still list; the offer "
        "is advisory (P14) and asserts nothing when absent."),
})

_register("vault.py", {
    "_record_integrity_failure": (DEGRADES_LOUD,
        "DB.S9 — the audit record of a vault artifact that failed its content-hash check at pull. "
        "The REFUSAL is not in this handler: `vault_pull` raises `VaultError` regardless, and "
        "nothing is ever copied, so a swallow here can never turn a caught tamper into a served "
        "one. What it protects is the write TO the ledger, which may legitimately be impossible — "
        "`team join` hash-verifies a vault at a ref that is often a read-only or foreign checkout "
        "with no writable `.mokata/temp_local/`. Failing the pull because we could not write our "
        "own audit note would be the tail wagging the dog. Broad because the ledger's append path "
        "spans OSError (read-only fs, permissions), a corrupt sidecar counter, and a torn tail — "
        "and the ONE thing that must not happen is a novel exception class from the audit trail "
        "masking the integrity refusal with a stack trace. Loud: `note_degraded('vault', ...)` "
        "names the artifact and says the refusal still stands and nothing was copied."),
})


# ------------------------------------------------------------- GR.S4: the graph FRESHNESS contract
# Freshness is a READ-TIME enhancement (doc 85: no watcher, no daemon). Its whole contract is that
# it NEVER breaks a query and NEVER blocks a tool call — so every handler here is a best-effort
# transient-run-state / observability swallow, EXCEPT the one top-level reconcile guard, which is
# LOUD (a broken freshness reconcile could mask staleness, and that must not be a secret).
_register("knowledge/freshness.py", {
    "FreshnessController.ensure_fresh": (DEGRADES_LOUD,
        "The ONE top-level freshness guard. A crashed reconcile means the answer may not reflect "
        "the latest edits — the query STILL proceeds (freshness never blocks a query), but it says "
        "so once via `note_degraded('graph-freshness', UNREACHABLE)`. Broad because a reconcile "
        "spans git probes, StateStore I/O, and backend refresh — any of which may fail novelly, and "
        "none may become a reason a query can't answer."),
    "mark_dirty": (SUPPRESS_OK,
        "The PostToolUse ASYNC OBSERVABILITY append (doc 85). Its contract IS 'never raises, never "
        "blocks' — a failed dirty-set append just means the next query reconciles via the HEAD/mtime "
        "path instead. Silence is the correct loudness for the async lane."),
    "git_changed_since": (SUPPRESS_OK,
        "A degrade-clean git probe → [] on any error. [] means 'no changes detected via git'; the "
        "cold-walk baseline + dirty-set still catch edits, so a missing/broken git never masks "
        "staleness — it just narrows which signal caught it."),
    "_sid": (SUPPRESS_OK,
        "Session-id resolution falls back to 'default' if identity can't be resolved — cosmetic "
        "scoping of a transient run-state file; nothing is lost."),
    "FreshnessController._anchor_signal": (SUPPRESS_OK,
        "H-6 S2 — the code-anchor tripwire's input. It degrades to NO anchor signal, which is "
        "byte-identical to the pre-H-6 reconcile: the other three signals (dirty-set, HEAD, cold "
        "walk) still land, and a test pins exactly that rather than the weaker 'it still answers' "
        "(which `ensure_fresh`'s own DEGRADES_LOUD handler above would satisfy on its own — but "
        "only by ABANDONING a dirty-set `drain_dirty` has already consumed, losing the edit it "
        "named). Silent because the outcome is the ABSENCE of a fourth signal, not a wrong answer, "
        "and because `ensure_fresh` is already the loud voice for a reconcile that genuinely broke. "
        "Broad because the frame spans a JSON read, file hashing and a bounded scan, and none of "
        "those failures changes what the caller should do."),
    "FreshnessController._store": (SUPPRESS_OK,
        "Lazily resolves the transient StateStore; None ⇒ freshness simply doesn't persist state "
        "(byte-identical to no freshness). GR.S1-cache precedent."),
    "FreshnessController._load_state": (SUPPRESS_OK,
        "A torn/absent transient state file → a fresh FreshnessState (cold start re-seeds). Same "
        "silent-rebuild contract as the AST edge cache."),
    "FreshnessController._save_state": (SUPPRESS_OK,
        "Best-effort transient run-state write; a failure → the baseline isn't advanced and the next "
        "query re-reconciles. Never fatal (GR.S1-cache precedent)."),
    "FreshnessController._load_index": (SUPPRESS_OK,
        "A torn/absent transient cold-baseline index → None (rebuilt on the next cold start). "
        "Best-effort transient run-state."),
    "FreshnessController._save_index": (SUPPRESS_OK,
        "Best-effort transient write of the cold-baseline index; a failure only means the out-of-band "
        "recheck lacks a baseline this instance — the dirty-set/HEAD path still reconciles."),
    "FreshnessController._cold_walk": (SUPPRESS_OK,
        "Best-effort baseline seed. A failed walk → no baseline + no forced rebuild; fresh backend "
        "instances still re-parse changed files via the mtime-keyed on-disk cache, so staleness is "
        "not masked."),
    "FreshnessController.for_root": (SUPPRESS_OK,
        "Construction guard: an unreachable run-state surface → None, so freshness doesn't engage "
        "(byte-identical). Never a reason a layer can't be built."),
    "FreshnessController.for_surface": (SUPPRESS_OK,
        "Same construction guard as `for_root` — None ⇒ no freshness, byte-identical."),
    "FreshnessController.recheck_after_answer": (SUPPRESS_OK,
        "Best-effort out-of-band catch (∝ result size). A failure → no requery; the dirty-set + HEAD "
        "reconcile still cover the common cases, so this never blocks or crashes a query."),
    "_invalidate_ast": (SUPPRESS_OK,
        "Per-backend best-effort AST invalidation; a failure leaves that one backend uninvalidated "
        "(its own mtime-keyed cache still re-parses on a fresh instance). Never fatal."),
    "_refresh_graph": (SUPPRESS_OK,
        "Best-effort proactive graph re-index; a failure returns False ⇒ `_rebuild` sets "
        "`answer_from_floor` and the AST-floor note is emitted downstream (the degrade IS announced, "
        "just not here)."),
})

_register("knowledge/about_code.py", {
    "check_about_code_anchors": (SUPPRESS_OK,
        "GR.S4 write-time validation is a PROPOSAL warning, never a block. It is FAIL-OPEN by design "
        "(only an authoritative non-resolution flags), so any error → a clean check (no false alarm) "
        "and the proposal proceeds untouched — validation must never break the write path."),
})

_register("knowledge/graph_backend.py", {
    "CodeReviewGraphBackend.refresh_index": (SUPPRESS_OK,
        "GR.S4/GR.S2(k) proactive re-index. Degrade-clean → False, which the freshness controller "
        "treats as a rebuild failure ⇒ answer from the AST floor with a LOUD note (announced there, "
        "not swallowed here). Broad because the client's refresh spans subprocess/transport errors."),
    "CodeReviewGraphBackend.supports_kind": (SUPPRESS_OK,
        "CRG-NAV capability PROBE, not an operation: it asks an adopted client whether its interface "
        "maps a kind. It cannot hide a real failure — the fallback is the PERMISSIVE answer (True), "
        "which sends the query down the ordinary graph path where a genuine failure IS loud "
        "(BackendError → `note_degraded` in `_run`). Broad because the client is third-party."),
})

_register("knowledge/layer.py", {
    "_supports_kind": (SUPPRESS_OK,
        "The layer-side half of the SAME CRG-NAV capability probe. Identical reasoning: it fails "
        "PERMISSIVE (assume the backend answers), so nothing is skipped silently — the query still "
        "runs and any real failure is announced by the loud degrade path below it."),
    "KnowledgeLayer._run": (SUPPRESS_OK,
        "Belt-and-braces around `ensure_fresh`, which is ITSELF the registered loud guard and never "
        "raises — so this is effectively unreachable; if freshness somehow raised, the query still "
        "answers (freshness never blocks a query)."),
    "KnowledgeLayer.semantic": (SUPPRESS_OK,
        "Same belt-and-braces around the already-guarded `ensure_fresh` on the semantic path."),
    "graph_structure_line": (SUPPRESS_OK,
        "The GR.S4 briefing structure line is a nicety — any failure → None, i.e. the line is absent "
        "and the briefing is byte-identical. A cosmetic derivation, never load-bearing."),
})

_register("hook_cli.py", {
    "dirty_track_main": (SUPPRESS_OK,
        "GR.S4 PostToolUse ASYNC OBSERVABILITY hook. By contract (doc 85) it NEVER blocks and NEVER "
        "fails a tool call — the outer `except` IS the exit-0 floor. A failed dirty-set append just "
        "means the next query reconciles via the HEAD/mtime path instead. Silence is the correct "
        "loudness for the async lane (mirrors `session_start`/`statusline`)."),
    "user_prompt_submit_main": (SUPPRESS_OK,
        "H-1a UserPromptSubmit ASYNC CONTEXT-INJECTION hook, and the ONE place where the usual D5 "
        "answer (`note_degraded`, so the fallback stops being a secret) is the WRONG one. The "
        "asymmetry is the event: this hook fires on EVERY prompt the human submits, so a notice "
        "here is not one line about a broken subsystem, it is a line in the human's transcript "
        "every single turn — and the thing being announced is that a turn got LESS context than "
        "it could have, never that a guarantee was broken. Nothing is enforced here and nothing "
        "the user relies on is lost: mokata's rules are still enforced by the gates, and the "
        "SessionStart briefing still carries the always-on set. Compare `gate_guard_main`, which "
        "IS loud for the opposite reason — a gate failing open enforces NOTHING while the badge "
        "says governance is on, which is a lie in the proof (P16). The outer `except` is also the "
        "FAIL-OPEN floor itself: on this event a non-zero exit does not block a tool call, it "
        "eats the human's turn, so every arm must reach `return 0`."),
})

_register("knowledge/anchor_fingerprints.py", {
    "read_record": (SUPPRESS_OK,
        "H-6 S1. Absent, unreadable and malformed all mean ONE thing to every caller — there is no "
        "baseline — and the module's central rule (decision #6, `memory.staleness.is_stale`'s "
        "direction) is that no baseline is NO OPINION. So the degrade is not a quieter version of "
        "the answer, it IS the answer: every anchor declines, no proposal is raised and no approval "
        "is refused. Nothing is lost that a human relies on, because the thing lost is a claim "
        "mokata would otherwise have had no evidence for. Broad because a corrupt JSON file, a "
        "permission error and a torn read are indistinguishable from the reader's side and none is "
        "nameable in a way that would change the outcome."),
    "_defining_paths": (SUPPRESS_OK,
        "The SYMBOL arm's graph read. A client/process failure yields `DECLINE_GRAPH_FAILED`, and "
        "the decline is REPORTED on the verdict rather than swallowed — `AnchorVerdict.reason` "
        "carries it to whichever surface asked. This is `about_code.py:8-11`'s fail-OPEN rule, "
        "which is a governing contract rather than a degrade: mokata never manufactures a "
        "'this code moved' claim from a graph that could not answer. A `note_degraded` here would "
        "fire on every adopted-graph hiccup for a feature whose correct behaviour in that case is "
        "to say nothing at all."),
    "evaluate_anchors": (SUPPRESS_OK,
        "Per-anchor isolation on a batch read: one anchor that blows up must not cost the other "
        "anchors their verdicts. The failure is NOT silent — it becomes a DECLINED verdict carrying "
        "the exception text in `reason`, which is the same channel every other decline uses, and "
        "the surfaces above render it."),
    "record_anchors": (SUPPRESS_OK,
        "Derived bookkeeping on a read path — the `knowledge.freshness.mark_dirty` / "
        "`injection_ledger.record_injected` pattern, same class of transient-directory state. A "
        "failed mint degrades to 'no baseline for this anchor', which by decision #6 means the "
        "anchor declines: strictly the pre-H-6 behaviour, never a wrong claim. Announcing it would "
        "put a line in the human's transcript about a bookkeeping miss whose entire consequence is "
        "one more session with nothing to compare against."),
})

_register("injection_ledger.py", {
    "_sid": (SUPPRESS_OK,
        "Identical to `knowledge.freshness._sid`, which this module's whole shape is copied from, "
        "and registered for the same reason: resolving the session identity is a LOOKUP with a "
        "documented floor (`\"default\"`), so a failure is not a degraded capability — it is the "
        "fallback bucket the function already promises. There is nothing to announce."),
    "record_injected": (SUPPRESS_OK,
        "H-1a S4 bookkeeping on the async context-injection lane. A ledger write that fails "
        "degrades to \"we don't know what was injected this session\", whose entire consequence is "
        "that one item may be injected twice — the exact state this feature exists to improve on, "
        "and strictly no worse than not having the ledger at all. Announcing it would put a line "
        "in the human's transcript, potentially every turn, about a bookkeeping miss that costs "
        "them one duplicated memory line. Mirrors `knowledge.freshness.mark_dirty`, the same "
        "pattern on the same class of transient run-state."),
})

_register("bootstrap.py", {
    "build_injection": (SUPPRESS_OK,
        "H-1a's per-turn recall pack, and it shares its reasoning with — and is the OTHER HALF of "
        "— `hook_cli.user_prompt_submit_main` (see that entry). One thing worth stating separately: "
        "the memory-read failure this swallows is ALREADY announced loudly on the surface that owns "
        "it. `bootstrap._always_on_rule_lines` calls `note_degraded('memory-rules', …)` when the "
        "store cannot be read at SessionStart — the loudest degrade in the codebase, and rightly, "
        "because there it means the project's guardrails are not being applied. Repeating it here "
        "would not add information; it would add it ONCE PER PROMPT until the user stops reading "
        "the channel, which is how a warning that matters gets tuned out before the day it does. "
        "The pack degrades to EMPTY, the hook then emits no channel at all, and the turn proceeds "
        "exactly as it did before H-1a existed."),
    "_graph_structure_line": (SUPPRESS_OK,
        "The briefing wrapper for the GR.S4 structure line: None on any failure ⇒ absent line, "
        "byte-identical briefing. The briefing must never crash on a cosmetic addition."),
    "_wiring_drift_line": (SUPPRESS_OK,
        "DOC-ONBOARD. The briefing wrapper for the stale-wiring advisory: None on any failure ⇒ "
        "absent line, byte-identical briefing — the same shape (and the same reasoning) as "
        "`_graph_structure_line` directly above. Nothing is lost that the user relies on: the "
        "verdict it renders is `hook_wiring.wiring_drift`, which is itself never-raise, and the "
        "SAME verdict is carried by `mokata doctor --wiring` and the `status` MCP tool — so a "
        "swallow here costs one advisory line on one of three surfaces, never the answer. "
        "Broad because it spans settings.json parsing + `shutil.which` probing + the harness "
        "expectation import."),
})

_register("mcp/tools_memory.py", {
    "remember": (SUPPRESS_OK,
        "GR.S4 about_code validation is best-effort proposal metadata: any failure is swallowed so "
        "the proposal proceeds unchanged (no warning attached). Validation never blocks a write "
        "(P2 untouched) — silence here degrades to exactly the pre-GR.S4 proposal."),
})


class TestTheSweepIsComplete(unittest.TestCase):
    """Every broad handler in src/ is classified. A new one that nobody classified FAILS."""

    def test_every_broad_handler_is_registered(self):
        sites = broad_handlers()
        unregistered = sorted(set(sites) - set(REGISTER))
        detail = "\n".join(
            f"    {rel}:{qual}  (line {sites[(rel, qual)][0][0]})" for rel, qual in unregistered)
        self.assertEqual(
            unregistered, [],
            "UNREGISTERED BROAD HANDLER(S) — a new `except Exception` appeared in src/ and nobody "
            "said what it is.\n\n" + detail + "\n\n"
            "Decide, then register it in test_d5_sweep_register.py:\n"
            "  * can a REAL failure pass through it unannounced? → it is a SILENT DEGRADE. Give it "
            "a `degrade.note_degraded(...)` notice (the fallback still falls back — it just stops "
            "being a secret) and register it DEGRADES_LOUD.\n"
            "  * is a narrower class the honest contract? → narrow it, and it leaves this sweep.\n"
            "  * is silence genuinely correct (cleanup, a cosmetic, a callee that never raises)? → "
            "register it SUPPRESS_OK with the reason.\n"
            "Do NOT add an entry just to turn CI green.")

    def test_the_register_carries_no_stale_entries(self):
        """A justification for a handler that no longer exists is a lie (the SI.6 rule)."""
        sites = broad_handlers()
        stale = sorted(set(REGISTER) - set(sites))
        self.assertEqual(stale, [],
                         f"these registered handlers no longer exist — remove them: {stale}")

    def test_every_entry_carries_a_justification(self):
        for key, (kind, why) in REGISTER.items():
            self.assertIn(kind, (DEGRADE_CLEAN, DEGRADES_LOUD, NARROW_IS_HONEST, SUPPRESS_OK))
            self.assertTrue(why.strip(), f"{key} has no justification")

    def test_there_are_no_bare_excepts(self):
        """`except:` also catches KeyboardInterrupt and SystemExit — it does not merely hide a bug,
        it makes the process unkillable at that line. There were zero at the head of D5; there must
        be zero after it."""
        sites = broad_handlers()
        bare = sorted(k for k, v in sites.items() if any(kind == "bare" for _line, kind in v))
        self.assertEqual(bare, [], f"bare `except:` — name the exception: {bare}")

    def test_BaseException_is_only_ever_cleanup_that_re_raises(self):
        """The only honest use: remove a temp file on the way out, then `raise`. Anything else that
        catches BaseException is swallowing a Ctrl-C."""
        sites = broad_handlers()
        for (rel, qual), handlers in sorted(sites.items()):
            for _line, kind in handlers:
                if kind == "BaseException":
                    self.assertEqual(
                        REGISTER[(rel, qual)][0], SUPPRESS_OK,
                        f"{rel}:{qual} catches BaseException — that is only ever correct for "
                        f"cleanup-then-re-raise")


class TestTheAuditReport(unittest.TestCase):
    """Prints the disposition of every broad handler. Runs in CI on every push, so the real numbers
    are in the output every time — including the ones we did not like."""

    def test_the_audit_report(self):
        sites = broad_handlers()
        total = sum(len(v) for v in sites.values())
        buckets = {DEGRADE_CLEAN: [], DEGRADES_LOUD: [], NARROW_IS_HONEST: [], SUPPRESS_OK: []}
        for key in sorted(sites):
            kind = REGISTER.get(key, ("?", ""))[0]
            if kind in buckets:
                buckets[kind].append(key)

        titles = {
            DEGRADE_CLEAN: "(i)   DEGRADE-CLEAN — already says so (prints / verdict / contract)",
            DEGRADES_LOUD: "(ii)  DEGRADES LOUD — was SILENT; D5 gave it a classed notice",
            NARROW_IS_HONEST: "(iii) BROAD IS HONEST — the real class is an optional, lazily "
                              "imported dep",
            SUPPRESS_OK: "(iv)  SUPPRESS-OK — silence is correct (cleanup / cosmetic / never-raises)",
        }
        lines = ["", "=" * 78,
                 f"D5 SWEEP — every broad exception handler in src/  [{total} handlers, "
                 f"{len(sites)} sites]", "=" * 78]
        for kind in (DEGRADES_LOUD, DEGRADE_CLEAN, NARROW_IS_HONEST, SUPPRESS_OK):
            lines.append(f"\n{titles[kind]}  [{len(buckets[kind])}]")
            for rel, qual in buckets[kind]:
                lines.append(f"    {rel}:{qual}")
        print("\n".join(lines))
        self.assertTrue(buckets[DEGRADES_LOUD], "D5 fixed at least one silent degrade")


if __name__ == "__main__":
    unittest.main()
