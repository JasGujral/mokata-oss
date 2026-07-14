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
    "_flush_locked": (DEGRADES_LOUD,
        "The per-entry DB failure handler, and the reason `_read_remote` can now propagate safely. "
        "A statement (or the CAS-miss re-read) that fails mid-apply leaves the entry PENDING — no "
        "marker is appended, so the replay still reads it as pending and the next healthy flush "
        "re-applies it idempotently (MS.S5). Loud: `note_degraded('team-flush', UNREACHABLE)`. "
        "Broad because `conn` is a psycopg connection and psycopg is an OPTIONAL extra whose error "
        "class cannot be named at module scope — narrowing it wrong would turn a transient DB blip "
        "into a CRASHED flush, which is worse than the swallow."),
    "_decide_conflict": (DEGRADE_CLEAN,
        "Non-interactive stdin → `read_yes_no` raises → fail-CLOSED to 'defer'. The signal comes "
        "out loudly and by contract: the conflict stays CONFLICTED, is counted in "
        "`SyncResult.deferred`, and `mokata sync` prints 'some conflicts need your decision'. It "
        "never silently picks a winner — deferring is the whole point of the handler."),
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
    "build_stage_badge": (SUPPRESS_OK, "Guards `_badge_state` + `badge_verbosity`; falls back to no badge / BADGE_FULL."),
    "active_skill_surface": (SUPPRESS_OK, "Guards `_badge_state`; no skill surface rendered."),
    "build_todo_items": (SUPPRESS_OK, "An unreadable checkpoint → an empty todo list, not a crashed surface."),
    "_logged_user_stage": (SUPPRESS_OK, "An unreadable progress log → None; the checkpoint still derives the stage."),
    "statusline_enabled": (SUPPRESS_OK, "An unreadable setting → the DEFAULT (True). The badge shows; nothing is lost."),
    "badge_verbosity": (SUPPRESS_OK, "An unreadable setting → the DEFAULT (BADGE_FULL)."),
    "statusline_badge": (SUPPRESS_OK, "An unresolvable run mode → the LOCAL default (the zero-config mode)."),
    "_ledger_tail": (SUPPRESS_OK, "An unreadable ledger tail → []; the badge simply shows no recent activity."),
    "_badge_agents": (SUPPRESS_OK, "An unbuildable lane summary → ''; the badge omits the agents segment."),
    "_develop_counter": (SUPPRESS_OK, "An unbuildable lane summary → ''; the badge omits the develop counter."),
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
_register("memory/store.py", {
    "_identity_and_access_for": (SUPPRESS_OK,
        "Guards `team_audit.actor()`, whose contract is never-raise. An unresolvable identity is not "
        "a degraded capability — the write path stamps the placeholder author and carries on."),
    "MemoryStore._team_mode": (SUPPRESS_OK,
        "Guards `run_mode.read_mode`, whose docstring says 'Never raises'. False = LOCAL = the "
        "fail-closed direction (an unknown mode is NEVER team)."),
    "MemoryStore.pending_status": (SUPPRESS_OK,
        "Guards `flush_liveness.pending_status`, which is degrade-clean to None by contract."),
    "MemoryStore._journal_team_write": (SUPPRESS_OK,
        "Two guards, both around never-raise callees: `project.derive_project_id` ('Never raises') "
        "and `team_audit.actor`. The journal entry is written either way — only its `project` label "
        "and `who` attribution fall back, and the WRITE itself is never at risk."),
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
    "MemoryStore._best_effort_flush": (DEGRADE_CLEAN,
        "The signal is CM.S4's whole point: a failed flush is COUNTED, not forgotten. The backlog "
        "surfaces as the statusline's `N pending` segment and doctor's 'N approved write(s) "
        "journaled locally and NOT yet flushed to the team DB' — so a swallow here cannot hide a "
        "stranded write; the journal still holds it and the surfaces still say so."),
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
    "PostgresBackend.close": (NARROW_IS_HONEST,
        "Teardown `close()` on a psycopg connection — the driver's classes are not nameable without "
        "a hard dependency on the optional extra, and the connection is being dropped either way."),
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
        "An ADDITIVE, optional `pending` key on an otherwise-complete status response. "
        "`resolve_read_routing` never raises on the read path and carries its own DegradeNotice, so "
        "the degrade is reported elsewhere; the only thing this can lose is a COUNT."),
    "session_windows": (SUPPRESS_OK,
        "Two handlers: (a) `SR.touch` — WRITE-side self-registration inside a READ tool; failing to "
        "register affects only whether THIS window appears in the list, and corrupts nothing. (b) "
        "`SW.offer_text_once` — a one-time, purely ADDITIVE worktree offer (data, never an action). "
        "Both are broad because they span PID probing + transient-file IO across three OSes, and "
        "neither may break a listing that is otherwise complete."),
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
_register("branch_protection.py", {
    "check_branch_protection": (DEGRADE_CLEAN,
        "Fail-CLOSED FAIL verdict naming the error; the release refuses."),
})
_register("cli_commands/runviews.py", {
    "cmd_progress_mark": (SUPPRESS_OK,
        "Two handlers: transient run-registry upkeep (commented 'registry upkeep never breaks "
        "recording a stage' — the stage IS recorded, only the index is best-effort), and one that "
        "PRINTS `could not record '{stage}' ({exc}); continuing`."),
    "cmd_progress_record_review": (DEGRADE_CLEAN,
        "PRINTS the failure, and ship's gate fails closed downstream anyway (no verdict ⇒ block)."),
    "cmd_progress_review_status": (DEGRADE_CLEAN,
        "Fail-CLOSED: prints `({exc})` and returns 2."),
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
