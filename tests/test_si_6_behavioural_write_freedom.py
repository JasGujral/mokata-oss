"""SI.6-DELEGATED-BLINDNESS, the BEHAVIOURAL half — a write-free charter is pinned by OBSERVING it.

The static half (`test_si_6_delegated_blindness.py` + the sweep's registers) closes what a stdlib-
`ast` resolver can close, and DECLARES the rest: `_writegraph.UNCLOSED_SHAPES` names the factory-
bound receiver, the inherited method, the callable passed as an argument, dynamic dispatch. Closing
shape 2 statically means inferring a factory's return type inside an AST resolver — type inference
with no bottom — so it stays declared rather than guessed.

THIS SUITE COVERS WHAT THE STATIC HALF DECLARES IT CANNOT SEE, and it does so by sidestepping
resolution entirely: it RUNS the subject and looks at the disk. The two halves compose. Neither is
weakened here — the register, the closure, both coherence directions and the exception set are the
static half's and are untouched.

THE GAP THIS CLOSES, precisely. The static sweep can ask "is this site CLASSIFIED?" It can never ask
"is the classification TRUE?" A register entry reading "One item out by id; no write path" keeps
that entry no matter what the function grows later, and the entry is what the sweep checks. Plant a
delegated write inside such a function and:

  * the sweep stays GREEN — the site is registered, and the writer it now reaches is itself a
    registered UNGATED_BY_DESIGN site, so no coherence direction fires either;
  * this suite goes RED, because a byte moved.

That is `test_a_delegated_write_planted_in_a_bound_subject_reds_this_harness` and
`test_the_static_sweep_stays_green_on_the_very_same_delegation`, and the pair is the deliverable.
The class is not hypothetical: DB.S7d measured it once already, when an `_append` of one record
planted inside `_ask_group` — a function whose whole charter is "settles NOTHING" — passed all 4972
unit tests, SI.6 and D5 included.

TWO PROPERTIES OF P10 (`test_db_s7d_group_decision.py:414`) ARE LOAD-BEARING AND SURVIVE INTACT:

  1. THE SNAPSHOT IS WHOLE-TREE AND BYTE-EXACT. Not journal-only, not store-only — "the write that
     breaks the asker's charter is by definition one nobody anticipated, so a snapshot scoped to the
     file the pin's author thought of would miss it." Bytes, not mtimes.

  2. IT ASSERTS THE SUBJECT ACTUALLY RAN, before it asserts nothing moved — "without them a stubbed-
     out asker that does nothing at all would satisfy (c) vacuously, and a pin that a no-op passes
     is not a pin."

Property 2 is the whole design of the generalisation, because a reusable harness is exactly how you
bind fifty charters and accidentally assert nothing fifty times. So it is not left to each binding's
author to remember. Every `Binding` MUST carry an `evidence` callable — the harness refuses to
construct one without it — and `TestEveryBindingsEvidenceIsFalsifiable` NEUTERS each subject in turn
and requires that binding's evidence to STOP being produced. An "it really ran" check that a no-op
still satisfies is caught by the harness rather than trusted.

That guard is not theoretical either. 0.0.17 produced the failure twice: `WINDOW_HOLDER` stopped
parking and every window test went green without entering the window (doc 85 §7e), and stage 16's
leak test would have passed on an empty ledger had it not asserted the record existed first.

WHY THE BOUND SET IS SMALL, AND WHY THAT IS THE HONEST OUTCOME. `UNGATED_BY_DESIGN` holds 85 entries.
Nine are bound. The rest are refused IN WRITING in `REFUSED` below, one line each, and
`test_every_register_entry_is_either_bound_or_refused_in_writing` makes the split TOTAL — a new
register entry that lands in neither REDs this suite and forces a human to say which it is. The
three reasons a charter cannot be bound are worth stating up front, because each is a real limit of
this instrument rather than an omission:

  (a) THE SHARED DB IS NOT IN THE TREE. 27 entries are Postgres/pgvector reads. Their durable
      substrate is a shared database, and a whole-tree snapshot of the repo would be unchanged for
      them BY CONSTRUCTION — including if they wrote. That is a pin that passes vacuously, which is
      exactly what property 2 exists to forbid, so they are refused rather than bound. Their SQLite
      TWINS are bound, and the register itself calls them twins ("the shared table's twin of the
      sqlite listing above").

  (b) THE REGISTER'S UNIT IS A STATEMENT; THIS HARNESS'S UNIT IS A FUNCTION. Where the two coincide
      the charter binds. `memory/_sqlite.py:_apply_pragmas` is the case where they do not: its entry
      says of the `PRAGMA busy_timeout=` statement that "it stores no byte", and that is true — but
      the FUNCTION also calls `_switch_to_wal`, which the register's own next entry says "DOES
      change a durable byte (the db header)". The entry is correct and the function is not write-
      free; only a function-scoped claim can be bound behaviourally.

  (c) THE CLAIM IS SCOPED TO PART OF THE BODY. `team_journal.py:live_recover` reads "Read-only here;
      the enqueue it feeds is a journal append". It is read-only in its SQL and it ends in
      `recover_stranded_floor`, which appends to the journal. Binding it write-free would assert a
      falsehood, and an unbound charter that is NAMED is worth more than a bound one that lies.

NO PRODUCTION SEAM WAS ADDED. Every subject below is reached through its ordinary public call; no
`src/` file is touched by this stage.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import textwrap
import unittest
from contextlib import closing
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import _writegraph
import test_si_6_writegate_side_doors as sweep

from mokata.memory import _sqlite
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem


# ======================================================================================
# THE SNAPSHOT. P10's, unchanged in kind: whole-tree, byte-exact.
# ======================================================================================

def _tree_snapshot(root):
    """Every byte under `root` — the store, its WAL sidecars, the ledger, the manifest, artifacts.

    Deliberately WHOLE-TREE rather than scoped to the file this pin's author had in mind: the write
    that breaks a charter is by definition the one nobody anticipated. Bytes, not mtimes — a re-read
    that rewrites identical content is not a write in the sense this pins.

    KEPT BYTE-EXACT ON PURPOSE, and it was not obvious it could be. A SQLite store in WAL mode
    rewrites its `-shm` shared-memory index on an ordinary SELECT, which would have forced either a
    file-class exclusion (blinding the harness to every WAL-buffered write — the store's real writes
    land in `-wal`) or a row-level content dump instead of bytes (a second notion of "durable" to
    keep true). Neither was needed: `SQLiteBackend._connect` opens a connection PER OPERATION and
    closes it, and the last connection to close checkpoints and removes `-wal`/`-shm`. At rest the
    store is a single complete `m.db`, so P10's snapshot generalises verbatim. The bindings below
    therefore open and close their own connections INSIDE the measured window, which makes the
    window stricter than the charter (it measures connect+read+close), never weaker."""
    snap = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                snap[os.path.relpath(path, root)] = fh.read()
    return snap


def _changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


# ======================================================================================
# THE FIXTURE. One seeded store, built BEFORE any snapshot is taken.
# ======================================================================================

class _Fixture:
    """A temp repo holding a seeded local memory store, plus the ids the subjects need.

    Seeded through the ordinary `SQLiteBackend.put`, so the FTS index, the edge rows and the scope
    columns are all provisioned by the real code path rather than hand-built. Everything a subject
    could need exists before the `before` snapshot is taken, so no binding can pass merely because
    its subject found nothing to do."""

    def __init__(self, root):
        self.root = root
        self.path = os.path.join(root, "m.db")
        self.backend = SQLiteBackend(self.path)
        self.target = MemoryItem.create("target-subject", "the superseded value")
        self.source = MemoryItem.create("source-subject", "the superseding value",
                                        supersedes=[self.target.id])
        self.backend.put(self.target)
        self.backend.put(self.source)

    def connect(self):
        """A connection through the ONE factory, for the two subjects that take a `conn`."""
        return closing(_sqlite.connect_sqlite(self.path))


# ======================================================================================
# THE HARNESS.
# ======================================================================================

class MissingEvidence(Exception):
    """Raised when a binding is constructed without an "it really ran" check.

    This is the generalisation's central refusal. P10 asserted (a) a real verdict and (b) the prompt
    was emitted before (c) not one byte changed, because a stubbed-out subject satisfies (c)
    vacuously. A REUSABLE harness makes that omission cheap and invisible at scale, so it is not a
    convention here — a binding without evidence cannot be constructed at all."""


class Binding:
    """One `UNGATED_BY_DESIGN` charter, bound to the behaviour of the function it describes.

    `key`       — the register key. Must be live in `UNGATED_BY_DESIGN`; a binding to a charter
                  nobody claims is a test with no subject.
    `run`       — `run(fx)` drives the subject through its ordinary call and returns its result.
    `evidence`  — `evidence(fx, result)` returns what proves the subject REALLY RAN. Required.
                  Not merely truthy: `TestEveryBindingsEvidenceIsFalsifiable` neuters the subject
                  and requires this to stop being produced, so an evidence check a no-op still
                  satisfies is caught here rather than believed.
    `neuter`    — `(owner, attribute)` the falsifiability control replaces with a no-op. It names
                  the SUBJECT ITSELF, so the control proves the evidence is specific to it and not
                  to the fixture standing behind it.
    """

    def __init__(self, key, run, evidence, neuter):
        if evidence is None:
            raise MissingEvidence(
                f"{key[0]}:{key[1]} was bound with no 'it really ran' check. A binding that asserts "
                f"only 'nothing was written' is satisfied by a subject that does nothing at all, "
                f"and a pin a no-op passes is not a pin (P10's (a) and (b)). Supply `evidence=`.")
        if key not in sweep.UNGATED_BY_DESIGN:
            raise KeyError(f"{key[0]}:{key[1]} is not a live UNGATED_BY_DESIGN entry")
        self.key, self.run, self.evidence, self.neuter = key, run, evidence, neuter

    @property
    def label(self):
        return f"{self.key[0]}:{self.key[1]}"


def assert_write_free(case, binding):
    """Run one bound subject and assert (a) it produced its evidence and (b) it changed not one byte.

    The order is P10's and it matters: the evidence is checked FIRST, so a subject that silently did
    nothing fails as "it did not run" rather than passing as "it wrote nothing"."""
    with tempfile.TemporaryDirectory() as d:
        fx = _Fixture(d)
        before = _tree_snapshot(d)
        result = binding.run(fx)
        after = _tree_snapshot(d)

        case.assertTrue(
            binding.evidence(fx, result),
            f"{binding.label} produced no evidence that it ran. Its byte comparison below would "
            f"then pass for the wrong reason — the subject did nothing, rather than doing its work "
            f"without writing.")
        changed = _changed(before, after)
        case.assertEqual(
            [], changed,
            f"{binding.label} WROTE while its register entry claims it does not — {changed}.\n\n"
            f"The entry reads: {sweep.UNGATED_BY_DESIGN[binding.key][:220]}…\n\n"
            f"This is SI.6-DELEGATED-BLINDNESS behaviourally: the static sweep cannot catch this, "
            f"because the site is REGISTERED and stays registered whatever the function grows. "
            f"Either the write belongs behind a WriteGate, or the register entry is now false and "
            f"a human must rewrite it.")
        return result


# ======================================================================================
# THE BOUND SET. Nine charters, derived from UNGATED_BY_DESIGN — see the module docstring for
# why the other 76 are refused, and `REFUSED` below for the per-entry reason.
# ======================================================================================

def _bindings():
    """Built lazily so `MissingEvidence`/`KeyError` surface inside a test, not at import."""
    return [
        # ---- memory/_sqlite.py -------------------------------------------------------------
        Binding(
            ("memory/_sqlite.py", "_current_mode"),
            # module attribute lookup, so the falsifiability control's patch takes effect
            run=lambda fx: _run_with_conn(fx, lambda c: _sqlite._current_mode(c)),
            # "PRAGMA journal_mode with no `=`, i.e. a HEADER READ". It really ran iff it came back
            # with the mode the store is actually on.
            evidence=lambda fx, r: r == "wal",
            neuter=(_sqlite, "_current_mode")),

        # ---- memory/backends.py — SQLiteBackend. Each is the LOCAL half of a twin whose
        #      Postgres half is refused for reason (a); see REFUSED.
        Binding(
            ("memory/backends.py", "SQLiteBackend.fts5_available"),
            run=lambda fx: _run_with_conn(fx, lambda c: SQLiteBackend.fts5_available(c)),
            # "the probe's only output is a boolean" — and the probe CREATEs and DROPs a temp table
            # to get it, which is the interesting half: the claim is that none of that is durable.
            evidence=lambda fx, r: r is True,
            neuter=(SQLiteBackend, "fts5_available")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.get"),
            run=lambda fx: fx.backend.get(fx.source.id),
            evidence=lambda fx, r: r is not None and r.id == fx.source.id and r.value == fx.source.value,
            neuter=(SQLiteBackend, "get")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.all"),
            run=lambda fx: fx.backend.all(),
            evidence=lambda fx, r: sorted(i.id for i in r) == sorted([fx.source.id, fx.target.id]),
            neuter=(SQLiteBackend, "all")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.hydrate"),
            run=lambda fx: fx.backend.hydrate([fx.source.id]),
            evidence=lambda fx, r: [i.id for i in r] == [fx.source.id],
            neuter=(SQLiteBackend, "hydrate")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.open_edges"),
            run=lambda fx: fx.backend.open_edges(fx.source.id),
            # the seeded `supersedes` relation, projected by `put` — so the read has a real row to
            # find and cannot pass by finding nothing.
            evidence=lambda fx, r: [e.kind for e in r] == ["supersedes"],
            neuter=(SQLiteBackend, "open_edges")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.expand_from"),
            run=lambda fx: fx.backend.expand_from([fx.source.id], 2),
            evidence=lambda fx, r: len(r) >= 1,
            neuter=(SQLiteBackend, "expand_from")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.lexical_search"),
            run=lambda fx: fx.backend.lexical_search("superseding"),
            # `[(item, score)]` — a ranked hit on the seeded row, so the read found something and
            # cannot pass by returning the empty list it returns when FTS is unavailable.
            evidence=lambda fx, r: any(item.id == fx.source.id for item, _score in r),
            neuter=(SQLiteBackend, "lexical_search")),
        Binding(
            ("memory/backends.py", "SQLiteBackend.usage_stats"),
            # the READ twin of `record_usage`, which is the registered WRITER sitting next to it in
            # the same class — the closest neighbour a drifting read could delegate to.
            run=lambda fx: fx.backend.usage_stats([fx.source.id]),
            evidence=lambda fx, r: fx.source.id in r,
            neuter=(SQLiteBackend, "usage_stats")),
    ]


def _run_with_conn(fx, work):
    """Open, use and CLOSE a connection inside the measured window (see `_tree_snapshot`)."""
    with fx.connect() as conn:
        return work(conn)


# ======================================================================================
# THE REFUSED SET. Every UNGATED_BY_DESIGN entry that is NOT bound, and why — in writing, one
# line each. `test_every_register_entry_is_either_bound_or_refused_in_writing` makes the split
# total, so a new register entry cannot land outside both and go unnoticed.
# ======================================================================================

#  (a) THE SHARED DB IS NOT IN THE TREE. A whole-tree snapshot of the repo is unchanged for these
#      BY CONSTRUCTION, including if they wrote — so a binding would pass vacuously, which is the
#      one thing property 2 forbids. Not "these are fine": UNMEASURED, and said out loud.
_SHARED_DB_SUBSTRATE = "refused (a) — reads a shared Postgres/pgvector DB; its durable substrate " \
                       "is not under the repo tree, so a whole-tree snapshot would be unchanged " \
                       "whatever it did. The SQLite twin of this read IS bound."

REFUSED = {
    ("memory/backends.py", "PostgresBackend._read_edges_present"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend._read_scope_backfilled"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.all"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.expand_from"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.get"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.hydrate"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.index_epoch"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.lexical_search"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.list_projects"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.open_edges"): _SHARED_DB_SUBSTRATE,
    ("memory/backends.py", "PostgresBackend.usage_stats"): _SHARED_DB_SUBSTRATE,
    # ...and four shared-DB entries that are WRITERS. Refusal (a) is true of them too, but it is
    # not the reason: they never claimed write-freedom in the first place.
    ("memory/backends.py", "PostgresBackend.record_usage"):
        "writes — the shared table's telemetry UPDATE (and its substrate is the shared DB, (a))",
    ("memory/vector.py", "PgVectorBackend.all"): _SHARED_DB_SUBSTRATE,
    ("memory/vector.py", "PgVectorBackend.get"): _SHARED_DB_SUBSTRATE,
    ("memory/vector.py", "PgVectorBackend.list_projects"): _SHARED_DB_SUBSTRATE,
    ("memory/vector.py", "PgVectorBackend.read_stamp"): _SHARED_DB_SUBSTRATE,
    ("memory/vector.py", "PgVectorBackend.semantic_search"): _SHARED_DB_SUBSTRATE,
    ("memory/vector.py", "PgVectorBackend.write_stamp"):
        "writes — upserts the index's (embedder, dim) stamp row (shared DB, so (a) as well)",
    ("session_transport.py", "PostgresTransport.list_projects"): _SHARED_DB_SUBSTRATE,
    ("session_transport.py", "PostgresTransport.list_tags"): _SHARED_DB_SUBSTRATE,
    ("session_transport.py", "PostgresTransport.read_bundle"): _SHARED_DB_SUBSTRATE,
    ("team_audit.py", "SharedAuditLog.append"):
        "writes — INSERTs one row into the shared audit table (shared DB, so (a) as well)",
    ("team_audit.py", "SharedAuditLog.list_projects"): _SHARED_DB_SUBSTRATE,
    ("team_audit.py", "SharedAuditLog.max_seq"): _SHARED_DB_SUBSTRATE,
    ("team_audit.py", "SharedAuditLog.read"): _SHARED_DB_SUBSTRATE,
    ("team_journal.py", "_edges_present"): _SHARED_DB_SUBSTRATE,
    ("team_journal.py", "_read_remote"): _SHARED_DB_SUBSTRATE,
    ("teamdb.py", "_read_schema_version"): _SHARED_DB_SUBSTRATE,
    ("teamdb.py", "_run_ddl"):
        "writes — the idempotent shared-schema DDL pass (shared DB, so (a) as well)",
    ("teamdb.py", "table_present"): _SHARED_DB_SUBSTRATE,
    ("teamdb.py", "probe._work"): _SHARED_DB_SUBSTRATE,

    #  (b) THE CLAIM IS SCOPED TO ONE STATEMENT INSIDE A FUNCTION THAT DOES MORE. The register's
    #      unit is a site; this harness's unit is a function. Both entries are TRUE as written.
    ("memory/_sqlite.py", "_apply_pragmas"):
        "refused (b) — the entry's 'it stores no byte' is true OF THE `PRAGMA busy_timeout=` "
        "statement it names, but the FUNCTION also calls `_switch_to_wal`, which the register's "
        "own next entry says 'DOES change a durable byte (the db header)'. A statement-scoped "
        "claim cannot be bound to a function-scoped observation. Measured, not assumed: binding "
        "it REDs on a fresh store, on `m.db`.",

    #  (c) THE CLAIM IS SCOPED TO PART OF THE BODY, and the rest of the body writes.
    ("team_journal.py", "live_recover"):
        "refused (c) — 'Read-only HERE; the enqueue it feeds is a journal append'. The SQL is a "
        "read and the function ends in `recover_stranded_floor`, which appends to the journal "
        "(team_journal.py:1775). Binding it write-free would assert a falsehood; naming it here "
        "is the honest form. Its shared-DB read also falls under (a).",

    #  (d) THE ENTRY DOES NOT CLAIM WRITE-FREEDOM AT ALL. `UNGATED_BY_DESIGN` means "not a GOVERNED
    #      durable write", which is a different claim: most of these say plainly that they write,
    #      and say why the write needs no approval (run-state, telemetry, an audit record, a
    #      derived index). There is no write-free charter here to bind. One line each names the
    #      write, so "it does not claim write-freedom" stays checkable rather than asserted.
    ("atomicfile.py", "atomic_write_text"): "writes — it IS the write primitive, no target of its own",
    ("cli_commands/knowledge.py", "cmd_ci_check"): "writes — the CI PR-comment body at --comment-file",
    ("dashboard.py", "write_dashboard"): "writes — derived HTML under temp_local/",
    ("dashboard.py", "write_governance_dashboard"): "writes — derived HTML under temp_local/",
    ("extras_install.py", "record_extra_decline"): "writes — the user's standing decline in ~/.mokata",
    ("flush_liveness.py", "clear_state"): "writes — flush backoff/liveness state under temp_local/",
    ("flush_liveness.py", "store_state"): "writes — flush backoff/liveness state under temp_local/",
    ("flush_liveness.py", "update_state"): "writes — flush backoff/liveness state under temp_local/",
    ("govern/ledger.py", "AuditLedger._write_counter"): "writes — the ledger's O(1) seq sidecar",
    ("govern/ledger.py", "AuditLedger.record"): "writes — the audit ledger row itself",
    ("govern/lifecycle.py", "_write_tombstone"): "writes — the removal tombstone in ~/.mokata",
    ("govern/revert.py", "ReversibleStateStore.revert"): "writes — undoes a state write under temp_local/",
    ("injection_ledger.py", "record_injected"): "writes — the per-session injected ledger under temp_local/",
    ("knowledge/anchor_fingerprints.py", "_write_record"): "writes — the anchor→fingerprint record",
    ("knowledge/ast_backend.py", "AstBackend._save_cache"): "writes — the incremental AST edge cache",
    ("knowledge/freshness.py", "drain_dirty"): "writes — consume-and-CLEAR of the dirty-set",
    ("knowledge/freshness.py", "mark_dirty"): "writes — appends a touched path to the dirty-set",
    ("knowledge/user_prefs.py", "record_graph_decline"): "writes — the user's graph-adoption decline",
    ("memory/_sqlite.py", "_switch_to_wal"): "writes — the entry says so: 'DOES change a durable byte'",
    ("memory/backends.py", "SQLiteBackend.__init__"): "writes — CREATE TABLE/INDEX on first open; "
        "the claim is 'it does not ASSERT a fact', not 'it writes nothing'",
    ("memory/backends.py", "SQLiteBackend._backfill_edges"): "writes — INSERTs the migrated edge rows",
    ("memory/backends.py", "SQLiteBackend._backfill_lifecycle_columns"): "writes — UPDATEs the "
        "valid_from/valid_to columns and stamps user_version",
    ("memory/backends.py", "SQLiteBackend._backfill_scope_columns"): "writes — UPDATEs the columns and "
        "stamps user_version; 'changes nothing A HUMAN APPROVED' is not 'changes no byte'",
    ("memory/backends.py", "SQLiteBackend._ensure_edges"): "writes — CREATE TABLE/INDEX, ADD COLUMN",
    ("memory/backends.py", "SQLiteBackend._ensure_fts"): "writes — creates the FTS table and backfills it",
    ("memory/backends.py", "SQLiteBackend._ensure_lifecycle_columns"): "writes — ALTER TABLE ADD COLUMN",
    ("memory/backends.py", "SQLiteBackend._ensure_scope_columns"): "writes — ALTER TABLE ADD COLUMN",
    ("memory/backends.py", "SQLiteBackend.record_usage"): "writes — the telemetry UPDATE itself",
    ("memory/store.py", "MemoryStore.recall_relevant"): "writes — it is the CALL SITE of the usage "
        "stamp; the entry's own words are 'the read path that STAMPS'",
    ("memory/store.py", "MemoryStore.record_usage"): "writes — the telemetry counters",
    ("migrate_channels.py", "_write_marker"): "writes — the once-migrated idempotence marker",
    ("plans.py", "write_plan_file"): "writes — the plan draft under temp_local/",
    ("plugin_cache.py", "record_plugin_root"): "writes — the machine-local cache under ~/.mokata",
    ("progress_events.py", "ProgressLog.append_event"): "writes — appends a run-telemetry event "
        "under temp_local/",
    ("session_state.py", "SessionScopedStore.delete"): "writes — deletes session run-state",
    ("session_state.py", "SessionScopedStore.update"): "writes — session run-state under temp_local/",
    ("state.py", "StateStore._atomic_write"): "writes — process/run state under temp_local/",
    ("state.py", "StateStore.delete"): "writes — deletes process/run state",
    ("team_health.py", "store"): "writes — the health verdict cache under temp_local/",
    ("team_journal.py", "TeamJournal._append_all"): "writes — the journal append funnel",
    ("team_journal.py", "TeamJournal.compact"): "writes — rewrites the journal with a subset",
    ("vault.py", "vault_pull"): "writes — copies the artifact out to an operator-named dest",
    ("visibility.py", "capture_session_snapshot"): "writes — the session baseline under temp_local/",
}


# ======================================================================================
# THE PINS.
# ======================================================================================

class TestEveryBoundCharterIsWriteFree(unittest.TestCase):
    """The nine bound charters, each RUN and each observed to move not one byte.

    MUTATION for every one of them: add ANY durable write to the subject — a delegated
    `self.record_usage(...)`, a `journal._append(...)`, a bare `open(..., "w")` — and the byte
    comparison goes RED. The static sweep does not, and that is the point (see
    `TestThePlantedDelegatedWrite`)."""

    def test_each_bound_subject_runs_and_writes_nothing(self):
        for binding in _bindings():
            with self.subTest(charter=binding.label):
                assert_write_free(self, binding)


class TestTheHarnessRefusesAVacuousBinding(unittest.TestCase):
    """Property 2, enforced by the harness rather than left to each binding's author.

    "A generalised harness makes it easy to bind fifty charters and accidentally assert nothing
    fifty times." These are the tests that stop that."""

    def test_a_binding_with_no_evidence_check_cannot_be_constructed(self):
        """The refusal is at CONSTRUCTION, not at assertion time: a binding with no "it really ran"
        check must not exist long enough to be run and pass."""
        with self.assertRaises(MissingEvidence) as caught:
            Binding(("memory/backends.py", "SQLiteBackend.get"),
                    run=lambda fx: None, evidence=None, neuter=(SQLiteBackend, "get"))
        self.assertIn("no 'it really ran' check", str(caught.exception))
        self.assertIn("a pin a no-op passes is not a pin", str(caught.exception))

    def test_a_binding_to_a_charter_nobody_claims_is_refused(self):
        """A binding whose key is not live in the register is a test with no subject — and, worse,
        one that would keep passing after the charter it names was deleted."""
        with self.assertRaises(KeyError):
            Binding(("memory/backends.py", "NoSuchBackend.get"),
                    run=lambda fx: None, evidence=lambda fx, r: True,
                    neuter=(SQLiteBackend, "get"))

    def test_the_harness_reds_when_the_subject_did_not_run(self):
        """The (a)-half of P10, fired. A subject neutered to a no-op writes nothing at all, so the
        BYTE comparison would pass it. The evidence check is what refuses it — and it must fail as
        "it did not run", never as a silent green."""
        binding = _bindings()[2]                              # SQLiteBackend.get
        self.assertEqual("memory/backends.py:SQLiteBackend.get", binding.label, "precondition")
        with mock.patch.object(SQLiteBackend, "get", lambda self, item_id: None):
            with self.assertRaises(AssertionError) as caught:
                assert_write_free(self, binding)
        self.assertIn("produced no evidence that it ran", str(caught.exception))

    def test_the_snapshot_itself_catches_a_write(self):
        """AN INSTRUMENT NOBODY HAS SEEN FAIL IS ONE NOBODY HAS TESTED. If `_tree_snapshot` returned
        `{}` — or silently skipped the store — every pin above would pass forever. So a REAL
        registered writer is run inside the same window and must be caught.

        `SQLiteBackend.record_usage` is deliberately the writer used: it is UNGATED_BY_DESIGN, it
        sits in the same class as five of the bound reads, and it is the nearest thing a drifting
        read could call."""
        with tempfile.TemporaryDirectory() as d:
            fx = _Fixture(d)
            before = _tree_snapshot(d)
            fx.backend.record_usage([fx.source.id], "2026-01-01T00:00:00+00:00")
            self.assertEqual(["m.db"], _changed(before, _tree_snapshot(d)),
                             "the snapshot did not see a REGISTERED durable write land in the "
                             "store — every write-free pin in this file is now vacuous")


class TestEveryBindingsEvidenceIsFalsifiable(unittest.TestCase):
    """THE GUARD THAT MAKES THE GENERALISATION SAFE, and the reason a `Binding` names its subject.

    Requiring an `evidence` callable stops a binding asserting NOTHING. It does not stop one
    asserting something a no-op also satisfies — `evidence=lambda fx, r: True` type-checks, reads
    like diligence, and is exactly the `WINDOW_HOLDER` failure (doc 85 §7e): every window test went
    green without entering the window.

    So each binding's evidence is put to the test it must pass: NEUTER the subject, and require the
    evidence to stop being produced. Evidence that survives its own subject being replaced by a
    no-op is evidence about the fixture, not about the subject."""

    def test_neutering_each_subject_destroys_that_bindings_evidence(self):
        for binding in _bindings():
            with self.subTest(charter=binding.label):
                owner, attr = binding.neuter
                with tempfile.TemporaryDirectory() as d:
                    fx = _Fixture(d)
                    with mock.patch.object(owner, attr, _noop_like(owner, attr)):
                        try:
                            result = binding.run(fx)
                            survived = bool(binding.evidence(fx, result))
                        except Exception:
                            # the neutered subject broke its caller — the evidence could not be
                            # produced, which is the outcome this control is asking for
                            survived = False
                self.assertFalse(
                    survived,
                    f"{binding.label}'s 'it really ran' check STILL PASSES with the subject "
                    f"replaced by a no-op. It is not evidence about the subject — it is evidence "
                    f"about the fixture, and this charter is currently pinned by nothing but a "
                    f"byte comparison a no-op also satisfies.")


def _noop_like(owner, attr):
    """A do-nothing replacement with the shape the call site expects (plain / static / bound)."""
    original = owner.__dict__.get(attr)
    if isinstance(original, staticmethod):
        return staticmethod(lambda *a, **kw: None)
    return lambda *a, **kw: None


class TestTheSplitIsTotal(unittest.TestCase):
    """Every register entry is BOUND or REFUSED IN WRITING — never merely absent."""

    def test_every_register_entry_is_either_bound_or_refused_in_writing(self):
        """The honesty pin. An entry in neither set is a write-freedom claim nobody has looked at,
        and the failure mode of a behavioural suite is silent omission: nothing goes red when a
        charter is simply never bound. So the split is TOTAL, and a new `UNGATED_BY_DESIGN` entry
        REDs here until a human says which side it falls on."""
        bound = {b.key for b in _bindings()}
        accounted = bound | set(REFUSED)
        register = set(sweep.UNGATED_BY_DESIGN)

        missing = sorted(register - accounted)
        self.assertEqual(
            [], missing,
            f"{len(missing)} UNGATED_BY_DESIGN entries are neither bound behaviourally nor refused "
            f"in writing: {missing}. Add a `Binding` if the entry claims the FUNCTION writes "
            f"nothing, or a `REFUSED` line saying why it cannot be bound. Silence is the one "
            f"answer this stage does not accept.")
        stale = sorted(accounted - register)
        self.assertEqual([], stale,
                         f"these keys have left the register and their entries here are dead: "
                         f"{stale}")
        self.assertEqual(set(), bound & set(REFUSED),
                         "a charter cannot be both bound and refused")

    def test_every_refusal_names_which_of_the_four_reasons_it_is(self):
        """A refusal is a claim too, and an unreasoned one is worse than an unbound charter: it
        looks considered. Same standard the register holds its own entries to.

        Checked STRUCTURALLY rather than by length, because length is the one property a
        placeholder can have. Each refusal must open by naming its class — (a) shared-DB substrate,
        (b) statement-scoped claim, (c) part-of-body claim, or "writes" for an entry that never
        claimed write-freedom — so "why is this not bound?" has a categorical answer per entry
        rather than a sentence a reader has to interpret."""
        classes = ("refused (a)", "refused (b)", "refused (c)", "writes —")
        for key, reason in sorted(REFUSED.items()):
            with self.subTest(charter=f"{key[0]}:{key[1]}"):
                self.assertTrue(
                    reason.strip().startswith(classes),
                    f"{key[0]}:{key[1]} is refused without naming which class of refusal it is "
                    f"(one of {classes}): {reason!r}")
                self.assertGreater(len(reason.strip()), 30,
                                   f"{key[0]}:{key[1]} is refused with no real reason: {reason!r}")

    def test_the_bound_set_is_not_empty_and_every_binding_names_a_live_charter(self):
        """The other vacuous shape: a suite that binds nothing at all passes every test above."""
        bindings = _bindings()
        self.assertGreaterEqual(len(bindings), 9, "the bound set has shrunk — say so deliberately")
        for binding in bindings:
            self.assertIn(binding.key, sweep.UNGATED_BY_DESIGN)


class TestTheScopedRefusalsAreMeasuredNotAsserted(unittest.TestCase):
    """Refusals (b) and (c) say a function writes. THEY ARE CHECKED HERE.

    F3 at the 0.0.17 review convicted four register entries that "earned their classification from
    a prose caller list nothing checks, and all four were false". A refusal is the same kind of
    claim: "this cannot be bound because the function writes" is a sentence someone will one day
    inherit and trust. So the two refusals whose reason is a factual claim about behaviour carry a
    test, not a comment."""

    def test_the_statement_scoped_refusal_writes_a_byte_when_it_runs(self):
        """REFUSAL (b), measured. `_apply_pragmas`'s entry is TRUE of the statement it names — the
        `PRAGMA busy_timeout=` really does store no byte. The FUNCTION is a different subject: it
        goes on to call `_switch_to_wal`, which the register's own next entry says "DOES change a
        durable byte (the db header)".

        Run on a store that is NOT yet in WAL, which is the state a first open finds, the whole
        function moves `m.db`. That is why a statement-scoped claim cannot be bound to a
        function-scoped observation — and it is measured rather than reasoned about."""
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE t(x)")
                conn.commit()
            # `closing(...)`, not a bare chained `sqlite3.connect(path).execute(...)`. On CPython
            # 3.12 that chained form leaves the connection alive until the CYCLIC collector runs
            # (MEASURED: 1 live handle with no `gc.collect()` on 3.12, 0 on 3.10) — so the handle
            # is still on `m.db` when this block's `TemporaryDirectory` is removed. POSIX unlinks
            # an open file happily and never notices; Windows refuses the unlink and the cleanup
            # dies with `WinError 32: file in use by another process`. Same reasoning, and the same
            # remedy, as `test_ms_s5_single_flusher._shared_pg`: the handle goes FIRST, always
            # deterministically, never left to a collector.
            with closing(sqlite3.connect(path)) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual("delete", mode,
                             "precondition: the store must start OFF wal, or the switch is a no-op "
                             "and this test measures nothing")
            before = _tree_snapshot(d)
            with closing(sqlite3.connect(path)) as conn:
                _sqlite._apply_pragmas(conn, path, busy_timeout_ms=1000, out=None, seen=None)
            self.assertEqual(["m.db"], _changed(before, _tree_snapshot(d)),
                             "`_apply_pragmas` wrote nothing — if the WAL switch has moved out of "
                             "it, the function may now be write-free and refusal (b) should be "
                             "reconsidered as a BINDING")

    def test_the_part_of_body_refusal_names_a_write_the_static_half_declares_it_cannot_see(self):
        """REFUSAL (c), and the clearest live evidence that the two halves compose.

        `live_recover` is registered "Read-only HERE; the enqueue it feeds is a journal append" —
        an honest entry that names its own write. Follow that write and it runs straight into
        `_writegraph.UNCLOSED_SHAPES` shape 2, on the REAL tree rather than a planted package:

            live_recover -> recover_stranded_floor -> journal.append(...)
                            where `journal = TeamJournal.for_surface(surface)`

        `for_surface` is a FACTORY, so rule (e) — which binds `x = SomeClass(...)` and infers no
        return types — cannot bind the receiver, and the edge is not resolved. The closure is not
        wrong; it is doing exactly what it declares. `UNCLOSED_SHAPES` even names this stage as the
        other half of the gap.

        So the assertion is on the DECLARED LIMIT, not on a defect. If it ever REDs, the static
        half has closed shape 2 and this refusal should be re-derived rather than deleted — the
        same re-anchoring rule the static half's own negative control carries."""
        graph, closure = sweep._write_closure()
        recover = ("team_journal.py", "recover_stranded_floor")
        funnel = ("team_journal.py", "TeamJournal._append_all")

        self.assertIn(funnel, closure.direct,
                      "precondition: the journal append funnel is a registered direct writer")
        self.assertNotIn(recover, closure.direct)
        self.assertNotIn(
            recover, closure.derived,
            "the closure now REACHES `recover_stranded_floor`'s journal append. Shape 2 (the "
            "factory-bound receiver) has been closed statically — re-derive refusal (c) against "
            "what the static half can now see, rather than deleting this test.")
        self.assertNotIn(
            ("team_journal.py", "TeamJournal.append"), graph.callees(recover),
            "`journal.append` is resolved after all — see above, the same re-derivation applies")
        self.assertIn("receiver bound to a FACTORY",
                      "\n".join(_writegraph.UNCLOSED_SHAPES),
                      "the gap this refusal rests on is no longer DECLARED by the static half, "
                      "which makes it an undeclared blind spot rather than a divided one")


# ======================================================================================
# THE DEMONSTRATION. A delegated write planted in a bound subject: RED here, GREEN over there.
# ======================================================================================

def _plant(root, files):
    """Write a throwaway package and return its root. Borrowed from the static half."""
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
    return root


class TestThePlantedDelegatedWrite(unittest.TestCase):
    """THE STAGE'S DELIVERABLE. Reproduces DB.S7d's class of proof for the generalised harness.

    DB.S7d planted an `_append` of one record inside `_ask_group` and watched all 4972 unit tests
    pass, SI.6 and D5 included. The same shape is planted here in a BOUND subject — and the two
    tests below are a matched pair, because either alone proves nothing:

      * this harness goes RED and names the file that moved;
      * the static sweep, on the same delegation with a real AST to look at, stays GREEN.

    And the reason it stays green is the interesting part, and is NOT that stage 1a's closure failed
    to see the call. The closure sees it perfectly. The sweep is green because the site is
    REGISTERED — `SQLiteBackend.get` has an UNGATED_BY_DESIGN entry reading "One item out by id; no
    write path", and that entry keeps classifying the function however the function changes. Nor
    does any coherence direction fire: the writer it now delegates to (`record_usage`) is itself
    UNGATED_BY_DESIGN, so no write-free claim reaches a GOVERNED write.

    A static register can ask whether a site is CLASSIFIED. It cannot ask whether the classification
    is TRUE. That is the whole of what this half adds."""

    def test_a_delegated_write_planted_in_a_bound_subject_reds_this_harness(self):
        """The delegated shape exactly: the bound subject's own body still names no writer — it
        calls a sibling, and the sibling writes."""
        binding = next(b for b in _bindings()
                       if b.key == ("memory/backends.py", "SQLiteBackend.get"))
        original = SQLiteBackend.get

        def _get_that_also_stamps(self, item_id):
            item = original(self, item_id)
            self.record_usage([item_id], "2026-01-01T00:00:00+00:00")   # <- the planted delegation
            return item

        with mock.patch.object(SQLiteBackend, "get", _get_that_also_stamps):
            with self.assertRaises(AssertionError) as caught:
                assert_write_free(self, binding)

        message = str(caught.exception)
        self.assertIn("WROTE while its register entry claims it does not", message)
        self.assertIn("m.db", message, "the failure must NAME what moved — a byte comparison that "
                                       "reds without saying where is one nobody can act on")
        self.assertIn("One item out by id", message,
                      "and it must quote the CHARTER it just falsified, so the reader can see the "
                      "sentence that is now untrue rather than go hunting for it")

    def test_the_static_sweep_stays_green_on_the_very_same_delegation(self):
        """The other half of the pair, on a planted package so the static instruments have a REAL
        AST to work on rather than a runtime patch they could never see.

        Two getters are planted, both registered read-only, because the sweep reaches them by two
        different routes and the result must be green on BOTH:

          * `get_delegating` calls `self._stamp(...)`, a name that is NOT in the sweep's write
            vocabulary (`_BACKEND_MUT` is put/update/delete/record_usage). Its own body therefore
            names no writer at all — DB.S7d's shape exactly — and it is the CLOSURE that reaches it.
          * `get_visibly` calls `self.record_usage(...)`, a name that IS in the vocabulary, so the
            direct scan sees the write sitting in the read's own body.

        The second is the stronger half and worth stating plainly: the sweep does not even need to
        be blind. It can see the write perfectly, in the body of a function registered "no write
        path", and still be green — because what it checks is whether the site is CLASSIFIED, and
        it is."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
                "backends.py": """
                    from .io import atomic_write_text

                    class Backend:
                        def _stamp(self, ids):
                            atomic_write_text("store", ids)     # a writer the VOCABULARY misses

                        def record_usage(self, ids):
                            atomic_write_text("store", ids)     # a writer the vocabulary knows

                        def get_delegating(self, item_id):
                            '''One item out by id; no write path.'''
                            self._stamp([item_id])              # <- the planted delegation
                            return item_id

                        def get_visibly(self, item_id):
                            '''One item out by id; no write path.'''
                            self.record_usage([item_id])        # <- planted, and in plain sight
                            return item_id
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            delegating = ("backends.py", "Backend.get_delegating")
            visibly = ("backends.py", "Backend.get_visibly")
            hidden_writer = ("backends.py", "Backend._stamp")

            # 1) the DELEGATED route: invisible to the direct scan, resolved by the closure.
            self.assertNotIn(delegating, direct,
                             "`_stamp` is not in the write vocabulary, so the delegating getter's "
                             "own body must name no writer — that is the DB.S7d shape")
            self.assertIn(hidden_writer, direct)
            self.assertEqual("backends.py:Backend.get_delegating -> backends.py:Backend._stamp",
                             closure.path_text(delegating))

            # 2) the VISIBLE route: the direct scan sees the write inside the read's own body.
            self.assertIn(visibly, direct,
                          "`record_usage` IS in the vocabulary, so this write is in plain sight")

            # 3) AND IT MAKES NO DIFFERENCE TO THE VERDICT. Every site the sweep can see is
            #    registered — the two getters under a read-only claim — so it has nothing to
            #    report by either route.
            register = {
                delegating: "One item out by id; no write path.",
                visibly: "One item out by id; no write path.",
                hidden_writer: "transient run-state telemetry; not a governed durable write",
                ("backends.py", "Backend.record_usage"):
                    "transient run-state telemetry; not a governed durable write",
            }
            seen = set(closure.direct) | set(closure.derived)
            self.assertEqual(
                [], sorted(seen - set(register)),
                "precondition: the planted package must be FULLY registered — the point is a green "
                "sweep over a false entry, not a green sweep over an incomplete one")
            self.assertNotIn(hidden_writer, sweep.GATED,
                             "and the writer is UNGATED_BY_DESIGN-shaped, so the write-free "
                             "coherence direction has no GOVERNED write to fire on either")

    def test_the_two_halves_disagree_and_that_disagreement_is_the_stage(self):
        """Stated as one assertion so it cannot be read as two unrelated results: on the SAME
        subject, with the SAME delegation, the static half classifies it and the behavioural half
        convicts it."""
        self.assertIn(("memory/backends.py", "SQLiteBackend.get"), sweep.UNGATED_BY_DESIGN,
                      "the subject is registered read-only by the static half")
        self.assertIn(("memory/backends.py", "SQLiteBackend.record_usage"), sweep.UNGATED_BY_DESIGN,
                      "and the writer it delegates to is registered too — so no coherence "
                      "direction fires and the sweep is green by its own correct rules")
        self.assertIn(("memory/backends.py", "SQLiteBackend.get"), {b.key for b in _bindings()},
                      "while the behavioural half BINDS it, and reds the moment it writes")


if __name__ == "__main__":
    unittest.main()
