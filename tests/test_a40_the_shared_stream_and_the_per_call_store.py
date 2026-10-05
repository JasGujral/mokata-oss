"""0.0.21 stage 11 · CM.S5 — publishing the typed stream, and the invariant that replaced a cache.

THE MEASUREMENT THIS STAGE OPENED WITH, because doc 84 carried CM.S5 as ✅ 0.0.17 and the tree
disagreed in BOTH halves — and because the row's own source document contained the proof:

  1. `mokata_events` had NEVER been written to. `team init` provisioned it from v1 and the DDL's
     own comment said *"provisioned only; local-first population until a later UI"*. Zero inserts,
     zero readers, every build. ⭐ doc 77 §6 C-5 wrote the same sentence in **2026-07** — *"zero
     inserts, zero LISTEN/NOTIFY subscribers anywhere in src/"*.
  2. There was no `LISTEN` and no `NOTIFY` anywhere in `src/`. Every match for those words is a
     WARNING that they die behind a transaction-mode pooler, which is not the same as using them.
  3. ⛔ **AND `ReadThroughCache` — the thing CM.S5 existed to invalidate — WAS DELETED BY CM.S3**,
     which doc 77 itself asked for (*"drop `ReadThroughCache` outright rather than re-wiring
     invalidation"*). So the SUBSCRIBER half of this row has no consumer in this tree at all.

WHAT THIS FILE GRADES, and the order is the argument:

  1. the shared schema can HOLD the typed envelope — v6, DERIVED from the envelope rather than
     typed beside it, so the two cannot drift;
  2. the retirement of the v5 trio REFUSES rather than migrating when it finds rows, because
     "nothing ever wrote here" is a claim about somebody else's database;
  3. NOTIFY is a doorbell with a declared three-state answer about whether it can ring at all —
     a pooler makes *"nothing could have been listening"* look exactly like *"nobody was"*;
  4. publishing is EGRESS: opt-in, human-gated, secret-scanned, and the scan is a HARD block;
  5. the publish's own trail is distinguished, because a publish leaves exactly one event behind
     and `nothing-new` would otherwise be a state nobody can ever observe;
  6. the resume tail cannot lose the middle — the bug a `since_seq` flag on `query` would have
     shipped looking like a one-line change;
  7. ⭐ **and the invariant doc 77 asked for is a GUARD, not prose.** It is load-bearing precisely
     BECAUSE the cache is gone: pull-based freshness is now the whole story, so a long-lived
     store holding a connection is the defect. It will RED when the IDE server lands (ID.S3,
     0.0.24) — which is the point, not a problem.

⚠ The round trip through a REAL Postgres — absence crossing as NULL and not 0, the NOTIFY actually
being received by a separate connection — lives in `tests/integration/test_cm_s5_live_db.py`,
because it needs a server. This file grades everything that can be graded without one, and says
so rather than mocking a database and calling it measured.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import io
import json
import os
import re
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import team_events as TE
from mokata import teamdb
from mokata.events import EVENTS_SETTINGS_KEY, GateDecision, MemoryOp, ToolCall, emit
from mokata.events.schema import ENVELOPE_KEYS
from mokata.events.store import EventStore, store_for_root

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "mokata")


def a_repo():
    from mokata.events import store as STORE
    from mokata.init import init_repo
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    STORE.reset_enabled_cache()
    return d


def a_surface(root):
    from mokata.config import Surface
    return Surface.load(root)


def shared_events_ddl():
    """Every provisioning statement that mentions the events table — derived, never typed."""
    return [s for s in teamdb.provision_sql()
            if isinstance(s, str) and teamdb.EVENTS_TABLE in s]


def shared_event_columns():
    """The v6 column names, parsed out of the CREATE this build actually ships."""
    create = next(s for s in shared_events_ddl() if s.lstrip().startswith("CREATE TABLE"))
    body = create[create.index("(") + 1:create.rindex(")")]
    cols, depth, cur = [], 0, ""
    for ch in body:                       # split on top-level commas only
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cols.append(cur.strip())
    return [c.split()[0] for c in cols if c.split()]


def _as_stored(payload):
    from mokata.events.store import StoredEvent
    return StoredEvent(seq=1, event_id="e", ts="t", schema_version=1, type=payload.TYPE,
                       session_id="s", run_id="", actor="a", duration_ms=None, ledger_seq=None,
                       data=payload.to_data())


# --------------------------------------------- 1 · the shared schema can hold the typed envelope

class TheSharedSchemaCanHoldTheTypedEnvelope(unittest.TestCase):

    def test_the_version_moved_and_the_floor_did_NOT(self):
        """v6 is additive for every client: nothing in the runtime REQUIRES this table, so
        raising the floor would fail-close every existing team for an opt-in feature. The tripwire
        is stated in `teamdb`: the day a runtime read becomes mandatory here, the floor moves."""
        self.assertEqual(teamdb.TEAM_SCHEMA_VERSION, 6)
        self.assertEqual(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, 3)

    def test_every_envelope_field_has_a_shared_column_OF_THE_SAME_NAME(self):
        """⭐ THE ANTI-DRIFT TEST, and both sides are DERIVED. `ENVELOPE_KEYS` is the schema's own
        tuple and the column list is parsed from the shipped DDL — so a field added to the
        envelope without a column reds here, and nobody has to remember to update a list.

        The v5 shape is why this exists: it was `(kind, at, payload)` against an envelope of ten
        fields, and the names did not even correspond — `kind` meant one of 65 LEDGER kinds in the
        audit table and would have meant one of 7 event TYPES here."""
        cols = set(shared_event_columns())
        missing = sorted(k for k in ENVELOPE_KEYS if k not in cols)
        self.assertEqual(missing, [], f"envelope fields with no shared column: {missing}")
        self.assertIn("data", cols, "the typed payload itself has nowhere to land")

    def test_the_v5_trio_is_DELETED_not_deprecated(self):
        """Pre-1.0 (doc 85 §7d). Two names for one thing is how drift starts, and `kind` was the
        worst of them."""
        ddl = " ".join(shared_events_ddl())
        for dead in ("kind", "at", "payload"):
            self.assertIn(f"DROP COLUMN IF EXISTS {dead}", ddl,
                          f"the v5 column `{dead}` is not retired")
        self.assertNotIn("payload TEXT", ddl, "the v5 payload column is still being created")

    def test_the_create_and_the_alters_agree_so_a_FRESH_db_needs_no_migration(self):
        """A fresh database takes the final shape from the CREATE; the ALTERs are its no-ops. If
        the two disagree, a new team and an upgraded team get different tables — which is the
        failure every `IF NOT EXISTS` migration seam exists to prevent."""
        ddl = shared_events_ddl()
        created = set(shared_event_columns())
        added = {re.search(r"ADD COLUMN IF NOT EXISTS (\w+)", s).group(1)
                 for s in ddl if "ADD COLUMN IF NOT EXISTS" in s}
        self.assertTrue(added, "no ADD COLUMN statements — an existing v5 artifact cannot upgrade")
        self.assertEqual(sorted(added - created), [],
                         "columns are ADDED that the CREATE does not create")

    def test_publishing_is_idempotent_BY_PREDICATE(self):
        ddl = " ".join(shared_events_ddl())
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS", ddl)
        self.assertIn("(namespace, event_id)", ddl,
                      "the natural key is not unique, so a re-publish duplicates rows")

    def test_a_NON_EMPTY_v5_table_is_REFUSED_and_the_refusal_names_the_remedy(self):
        """⛔ The retirement rests on a MEASUREMENT (nothing ever wrote here), and a measurement
        about somebody else's database is still a claim. So the DDL refuses rather than migrating,
        and the message says what to do — never a silent drop of three columns of data."""
        guard = [s for s in shared_events_ddl() if "RAISE EXCEPTION" in s]
        self.assertEqual(len(guard), 1, "there is no refusal, or more than one")
        text = guard[0]
        self.assertIn("information_schema.columns", text, "the guard does not test the OLD shape")
        self.assertIn("NOTHING was changed", text)
        self.assertIn("team", text.lower())

    def test_the_guard_runs_AFTER_the_create_so_a_fresh_db_can_be_asked(self):
        """Ordering, and it is not cosmetic: on a fresh database the table does not exist yet, and
        a plpgsql block that SELECTs from a missing relation fails to plan."""
        ddl = shared_events_ddl()
        create_at = next(i for i, s in enumerate(ddl) if s.lstrip().startswith("CREATE TABLE"))
        guard_at = next(i for i, s in enumerate(ddl) if "RAISE EXCEPTION" in s)
        self.assertLess(create_at, guard_at)

    def test_the_drops_come_AFTER_the_guard(self):
        ddl = shared_events_ddl()
        guard_at = next(i for i, s in enumerate(ddl) if "RAISE EXCEPTION" in s)
        first_drop = next(i for i, s in enumerate(ddl) if "DROP COLUMN" in s)
        self.assertLess(guard_at, first_drop,
                        "a column is dropped before the refusal could have fired")


# ------------------------------------------------ 2 · NOTIFY is a doorbell, and says when it cannot

class NotifyIsADoorbellAndDeclaresWhenItCannotRing(unittest.TestCase):

    def test_the_channel_is_a_constant_and_a_legal_identifier(self):
        """A channel name is a Postgres IDENTIFIER (63 bytes). A per-project channel would
        truncate two long project ids into ONE name, which is the worst possible failure for a
        routing key — so the namespace travels in the payload instead."""
        self.assertLessEqual(len(TE.NOTIFY_CHANNEL.encode("utf-8")), 63)
        self.assertRegex(TE.NOTIFY_CHANNEL, r"^[a-z_][a-z0-9_]*$")

    def test_a_DIRECT_connection_says_supported(self):
        self.assertEqual(TE.notify_support("postgresql://u@127.0.0.1:5432/db")[0],
                         TE.NOTIFY_SUPPORTED)

    def test_every_POOLER_SHAPE_says_unsupported_and_says_why(self):
        for dsn in ("postgresql://u@x.pooler.supabase.com:6543/postgres",
                    "postgresql://u@ep-a-pooler.us-east-2.aws.neon.tech/db",
                    "postgresql://u@my.proxy-abc.us-east-1.rds.amazonaws.com/db"):
            state, why = TE.notify_support(dsn)
            with self.subTest(dsn=dsn):
                self.assertEqual(state, TE.NOTIFY_POOLED)
                self.assertIn("direct", why.lower())

    def test_an_UNPARSEABLE_dsn_is_UNKNOWN_and_this_test_is_why_it_exists(self):
        """⛔ FOUND BY RUNNING IT. The first draft read `dsn_inspect`'s `.pooled` bool, which is ONE
        axis of a three-state answer: an unclassifiable string has `pooled=False`, so the literal
        text `not a dsn at all` came back as *"notifications are deliverable"*. §7j — a derivation
        that types its own scope. It reads the `verdict` property now, which is where the three
        states live."""
        for bad in ("not a dsn at all", "", None):
            with self.subTest(dsn=bad):
                self.assertEqual(TE.notify_support(bad)[0], TE.NOTIFY_UNKNOWN)

    def test_the_three_states_are_three_and_never_collapse(self):
        self.assertEqual(len({TE.NOTIFY_SUPPORTED, TE.NOTIFY_POOLED, TE.NOTIFY_UNKNOWN}), 3)

    def test_the_pooler_verdict_is_READ_from_dsn_inspect_and_not_re_derived(self):
        """§7j. One module owns the connection-shape question; a second opinion here would be a
        second answer that drifts.

        ⚠ Asserted over the AST, not over the text, and the first draft taught me why: a
        `assertNotIn(".pooled", source)` reds on the DOCSTRING that explains the bug. That is
        `PIN-SUBSTRING-COMMENT-HOLE` — a scanned region containing prose about itself — which this
        tree has already been bitten by once, in `_mutant_driver_contract`'s `mutator_vars`."""
        path = os.path.join(SRC, "team_events.py")
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        reads = sorted({node.attr for node in ast.walk(tree)
                        if isinstance(node, ast.Attribute) and node.attr in ("pooled", "verdict")})
        self.assertEqual(reads, ["verdict"],
                         "this module reads an axis of the connection-shape answer other than "
                         f"the one three-state `verdict`: {reads}")


class _OneRow:
    """A cursor-shaped answer to exactly one SELECT."""

    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class _SchemaAnswering:
    """Every connection double DECLARES the shared schema it is pretending to be.

    ⭐ `SharedEventLog` asks before it writes (`_require_schema`, review F1), and a double that
    cannot answer no longer constructs. That is the point: the alternative — defaulting the answer
    inside the production code when the connection does not know — would make the floor check
    invisible to every double, which is the §7e failure the check exists to close.

    ⚠ The DEFAULT here is the CURRENT schema, so these doubles grade the publish path rather than
    the floor. The floor itself is graded on doubles that declare an OLD version, in
    `ThePreV6StoreDeclinesByNameRatherThanTracebacking`."""

    schema_version = 6
    schema_min_supported = 3

    def _schema_answer(self, sql):
        """The version row when `sql` is teamdb's schema read, else `None` (not this question)."""
        if "mokata_schema_version" not in sql:
            return None
        if self.schema_version is None:                 # a store with no schema row at all
            return _OneRow(None)
        return _OneRow((self.schema_version, self.schema_min_supported))


class TheNotifyPayloadIsIdentifiersOnlyAndBounded(unittest.TestCase):

    class _Conn(_SchemaAnswering):
        def __init__(self):
            self.sent = []

        rowcount = 1

        def execute(self, sql, params=None):
            answer = self._schema_answer(sql)
            if answer is not None:
                return answer
            self.sent.append((sql, params))
            return self

        def close(self):
            return

    def _notify(self, who):
        conn = self._Conn()
        log = TE.SharedEventLog(client=conn)
        payload = log.notify("p_abc", who, 3, 42)
        self.assertEqual(conn.sent[-1][1][0], TE.NOTIFY_CHANNEL)
        return payload

    def test_the_payload_carries_identifiers_and_counts_and_nothing_else(self):
        self.assertEqual(set(json.loads(self._notify("alice"))), {"ns", "actor", "n", "to"})

    def test_a_pathological_actor_DROPS_the_name_rather_than_truncating_it(self):
        """Half a name is a wrong name. The namespace alone is still a complete instruction."""
        payload = self._notify("a" * (TE.NOTIFY_PAYLOAD_CAP_BYTES + 500))
        self.assertLessEqual(len(payload.encode("utf-8")), TE.NOTIFY_PAYLOAD_CAP_BYTES)
        self.assertNotIn("actor", payload)
        self.assertIn("ns", payload)

    def test_the_payload_never_carries_the_EVENT(self):
        """A doorbell, not a delivery. A woken reader goes and SELECTs — which is also what makes
        a MISSED notification harmless, and that is what lets this fail open safely."""
        payload = self._notify("alice")
        self.assertNotIn("data", payload)
        self.assertNotIn("gate_decision", payload)

    def test_the_notify_is_sent_AFTER_the_inserts(self):
        """⛔ The whole correctness argument. Postgres delivers a notification at COMMIT and the
        connection is autocommit, so the rows are durable by the time this runs. Sent first, a
        woken reader would SELECT and find nothing — a doorbell for an empty doorstep."""
        src = io.open(os.path.join(SRC, "team_events.py"), encoding="utf-8").read()
        body = src[src.index("def _commit()"):]
        self.assertLess(body.index("append_all"), body.index("log.notify"))


# --------------------------------------------------------- 3 · publishing is egress, and is gated

class PublishingIsEgressAndIsGated(unittest.TestCase):

    def setUp(self):
        self.root = a_repo()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_sharing_is_OFF_by_default_and_publish_writes_NOTHING(self):
        """Local-first. `settings.events.enabled` (does the local store record?) and
        `settings.events.shared` (does anything leave the machine?) are different questions, and
        conflating them is how an observability lane becomes an egress channel."""
        surface = a_surface(self.root)
        self.assertFalse(TE.shared_enabled(surface.manifest.data))
        res = TE.publish(self.root, surface, assume_yes=True)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "shared-off")
        self.assertEqual(res.published, 0)

    def test_the_preview_is_read_only_and_says_how_to_turn_it_on(self):
        ok, n, _env, msg = TE.pending_publish(self.root, a_surface(self.root))
        self.assertFalse(ok)
        self.assertEqual(n, 0)
        self.assertIn("settings.events.shared", msg)

    def test_the_settings_live_in_the_EVENTS_namespace_and_not_a_second_one(self):
        data = {"settings": {EVENTS_SETTINGS_KEY: {"shared": True}}}
        self.assertTrue(TE.shared_enabled(data))
        self.assertFalse(TE.shared_enabled({"settings": {"team": {"shared": True}}}))

    def test_the_DSN_ladder_prefers_the_events_override_then_the_team_name(self):
        env = {TE.EVENTS_OVERRIDE_ENV: "events-dsn", "MOKATA_PG_DSN": "team-dsn"}
        self.assertEqual(TE.resolve_dsn(data={}, environ=env), "events-dsn")
        self.assertEqual(TE.resolve_dsn(data={}, environ={"MOKATA_PG_DSN": "team-dsn"}),
                         "team-dsn")
        self.assertIsNone(TE.resolve_dsn(data={}, environ={}))

    def test_an_explicit_per_call_name_WINS_over_both(self):
        env = {"CUSTOM": "custom-dsn", TE.EVENTS_OVERRIDE_ENV: "events-dsn"}
        self.assertEqual(TE.resolve_dsn("CUSTOM", data={}, environ=env), "custom-dsn")

    def test_no_DSN_degrades_CLEAN_with_a_message_naming_the_env_vars(self):
        with self.assertRaises(TE.SharedEventsUnavailable) as caught:
            TE.SharedEventLog(None)
        for name in TE.PG_DSN_ENVS:
            self.assertIn(name, str(caught.exception))

    def test_the_DSN_SECRET_never_reaches_a_message_or_a_row(self):
        """The value is read and never stored — only the env-var NAME is ever displayed."""
        src = io.open(os.path.join(SRC, "team_events.py"), encoding="utf-8").read()
        self.assertIn("env_name", src)
        self.assertNotIn("{dsn}", src, "a DSN value is interpolated into a string somewhere")


class _FakeConn(_SchemaAnswering):
    """A connection that counts INSERTs. Enough to prove the GATE's behaviour without a server —
    and the live round trip is in `tests/integration/test_cm_s5_live_db.py`, not faked here."""

    rowcount = 1

    def __init__(self):
        self.inserts = 0
        self.notifies = 0
        self.closed = 0

    def execute(self, sql, params=None):
        answer = self._schema_answer(sql)
        if answer is not None:
            return answer
        head = sql.lstrip().upper()
        if head.startswith("INSERT"):
            self.inserts += 1
        if "PG_NOTIFY" in head:
            self.notifies += 1
        return self

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []

    def close(self):
        self.closed += 1


class TheSecretScanIsAHardBlockOnTheWayOut(unittest.TestCase):
    """⭐ Stage 10 PROVED the payloads carry no content — a planted marker walked through all seven
    types and reached none of them. The gate is here anyway, because that is a property of the
    seven types that exist TODAY, and a future type is exactly what would break it. This class is
    the control that the gate is not decorative."""

    def setUp(self):
        from mokata import config_cmd
        self.root = a_repo()
        config_cmd.config_set(self.root, f"settings.{EVENTS_SETTINGS_KEY}.shared", "true",
                              assume_yes=True, out=lambda *_a: None)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_an_event_carrying_a_SECRET_is_refused_even_under_assume_yes(self):
        """P2 — a standing consent or a `--yes` approves the SEND; it never approves a leak."""
        emit(self.root, MemoryOp(op="read", item_id="AKIAIOSFODNN7EXAMPLE", count=1),
             session_id="s1", actor="alice")
        conn = _FakeConn()
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertFalse(res.ok, "a secret in an event payload was published")
        self.assertEqual(conn.inserts, 0, "rows were inserted despite the refusal")
        self.assertTrue(res.findings, "blocked with no finding — the reason is unexplainable")

    def test_a_CLEAN_batch_of_the_same_shape_PUBLISHES(self):
        """The other half of the control: without the planted secret the identical path commits,
        so the refusal above is attributable to the SCAN and not to the plumbing."""
        emit(self.root, MemoryOp(op="read", item_id="mem-1234", count=1),
             session_id="s1", actor="alice")
        conn = _FakeConn()
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertTrue(res.ok, res.message)
        self.assertGreater(conn.inserts, 0)
        self.assertEqual(conn.notifies, 1, "the doorbell did not ring exactly once")

    def test_the_gate_is_entered_as_an_EGRESS_write_and_this_test_is_why(self):
        """⛔ FOUND BY MUTATION. Nothing asserted the gate's `kind`, so a mutant changing `send`
        to `code` SURVIVED — and `send` is the whole reason the secret scan is a hard block here:
        it is the EGRESS rule. Captured by substituting the gate rather than by reading the
        source, because the claim is about the request that is actually submitted."""
        from mokata.govern import gate as GATE
        emit(self.root, MemoryOp(op="read", item_id="mem-1", count=1), session_id="s1")
        seen = []
        original = GATE.WriteGate.submit

        def _capture(self_gate, request, **kw):
            seen.append(request)
            return original(self_gate, request, **kw)

        GATE.WriteGate.submit = _capture
        try:
            TE.publish(self.root, a_surface(self.root), assume_yes=True, client=_FakeConn())
        finally:
            GATE.WriteGate.submit = original
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].kind, "send",
                         "publishing is egress and must enter the gate under the egress rule")
        self.assertTrue(seen[0].target.startswith(TE.PUBLISH_TARGET_PREFIX))
        self.assertIn("event", seen[0].tool, "the ledger cannot attribute this write to events")

    def test_a_BACKLOG_is_capped_and_the_remainder_is_REPORTED_not_dropped(self):
        """⛔ FOUND BY MUTATION. Removing the batch cap SURVIVED, because no test ever handed
        `publish` more events than one batch may carry. The tail that does not fit must be
        REPORTED (`remaining`), or a caller cannot tell a finished publish from a partial one —
        §7g at the one place a human decides whether to run it again."""
        for i in range(5):
            emit(self.root, MemoryOp(op="read", item_id=f"mem-{i}", count=1), session_id="s1")
        original_cap = TE.PUBLISH_BATCH_CAP
        TE.PUBLISH_BATCH_CAP = 3
        try:
            res = TE.publish(self.root, a_surface(self.root), assume_yes=True,
                             client=_FakeConn())
        finally:
            TE.PUBLISH_BATCH_CAP = original_cap
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.attempted, 3, "the batch cap was not honoured")
        self.assertGreater(res.remaining, 0, "the un-sent tail was not reported")
        self.assertIn("queued for the next publish", res.message)

    def test_a_BORROWED_connection_is_NEVER_closed_by_the_callee(self):
        """⛔ FOUND BY MUTATION. The source-level pin (`if client is None:` appears in the file)
        SURVIVED a mutant that removed the guard from `publish` and left it in the preview path —
        a substring that is true of the file and false of the function. Asserted on the object.

        It matters beyond tidiness: an injected connection belongs to the CALLER, and closing it
        means the next thing the caller does with its own connection fails for a reason that
        points at the wrong module."""
        emit(self.root, MemoryOp(op="read", item_id="mem-1", count=1), session_id="s1")
        conn = _FakeConn()
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(conn.closed, 0, "the publish closed a connection it had borrowed")

    def test_an_OWNED_connection_IS_closed(self):
        """The other half, so the test above cannot be satisfied by never closing anything."""
        src = io.open(os.path.join(SRC, "team_events.py"), encoding="utf-8").read()
        self.assertEqual(src.count("log.close()"), 2,
                         "the two paths that OWN a connection must both close it")

    def test_a_DECLINED_gate_publishes_nothing_and_rings_nothing(self):
        emit(self.root, MemoryOp(op="read", item_id="mem-1", count=1), session_id="s1")
        conn = _FakeConn()
        res = TE.publish(self.root, a_surface(self.root), assume_yes=False,
                         confirm=lambda _p: False, client=conn)
        self.assertFalse(res.ok)
        self.assertEqual((conn.inserts, conn.notifies), (0, 0))


# ------------------------------------------------------- 4 · the publish's own trail is a FACT

class ThePublishLeavesExactlyOneEventBehindAndSaysSo(unittest.TestCase):
    """⛔ FOUND BY RUNNING IT AGAINST A REAL DATABASE. A publish is a governed `send`, so the gate
    records it, the ledger PROJECTS that record, and the projected event is itself unpublished —
    so every publish leaves exactly one new event behind. The loop is bounded (one row per
    publish, never a cascade) and the row is worth keeping: the one write that moves data off this
    machine is the last write a shared audit surface should be blind to.

    But it makes `nothing-new` UNREACHABLE in practice, and a state nobody can observe is the §7g
    smell — so the result tells the two apart."""

    def test_the_predicate_keys_on_the_TARGET_PREFIX_both_sides_share(self):
        ev = GateDecision("write_gate", "approved",
                          subject=f"{TE.PUBLISH_TARGET_PREFIX}MOKATA_PG_DSN/p_abc")
        self.assertTrue(TE.is_own_publish_trail(_as_stored(ev)))

    def test_an_ORDINARY_gate_decision_is_not_the_publishers_own_trail(self):
        self.assertFalse(TE.is_own_publish_trail(
            _as_stored(GateDecision("write_gate", "approved", subject="src/a.py"))))

    def test_a_non_gate_event_is_never_the_trail(self):
        self.assertFalse(TE.is_own_publish_trail(_as_stored(ToolCall("x", "mcp", True))))

    def test_the_prefix_is_declared_ONCE_and_the_publisher_writes_THAT_constant(self):
        """If the publisher's target and the predicate drifted apart, the predicate would silently
        stop matching and `only-own-trail` would never fire again."""
        src = io.open(os.path.join(SRC, "team_events.py"), encoding="utf-8").read()
        self.assertEqual(src.count('PUBLISH_TARGET_PREFIX = "'), 1)
        self.assertIn('target=f"{PUBLISH_TARGET_PREFIX}', src)

    def test_the_predicate_does_NOT_key_on_the_tool_name(self):
        """`tool` is policy-substitutable (`policy_tool`), so a predicate keyed on it grades
        nothing the moment a policy renames it."""
        path = os.path.join(SRC, "team_events.py")
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "is_own_publish_trail")
        self.assertNotIn("events_publish", ast.dump(fn))


# --------------------------------------------------- 5 · the resume tail cannot lose the middle

class TheResumeTailCannotLoseTheMiddle(unittest.TestCase):

    def _store(self, n):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        st = EventStore(os.path.join(d, "e.db"))
        for i in range(n):
            st.emit(ToolCall(f"t{i}", "mcp", True), session_id="s")
        return st

    def test_after_seq_is_OLDEST_first_which_is_the_OPPOSITE_of_query(self):
        """⭐ The reason `after_seq` is a method and not a flag on `query`. `query` returns the
        NEWEST slice; a publisher using that with a backlog larger than the limit would send the
        newest rows, record its high-water mark, and NEVER COME BACK FOR THE MIDDLE."""
        st = self._store(5)
        self.assertEqual([e.data["tool"] for e in st.after_seq(0, limit=3)], ["t0", "t1", "t2"])
        self.assertEqual([e.data["tool"] for e in st.query(limit=3)], ["t2", "t3", "t4"])

    def test_repeated_calls_cover_EVERY_event_with_no_gap_and_no_repeat(self):
        st = self._store(11)
        seen, cursor = [], 0
        while True:
            batch = st.after_seq(cursor, limit=4)
            if not batch:
                break
            seen.extend(e.data["tool"] for e in batch)
            cursor = batch[-1].seq
        self.assertEqual(seen, [f"t{i}" for i in range(11)])

    def test_it_keys_on_SEQ_and_not_on_the_second_resolution_timestamp(self):
        """Two events in one second cannot be ordered by `ts`, so a `ts`-based resume must either
        re-send or skip. Both are wrong, and this is the fixture where it shows."""
        st = self._store(3)
        self.assertEqual(len({e.ts for e in st.after_seq(0)}), 1,
                         "fixture assumption: these landed in the same second")
        self.assertEqual([e.seq for e in st.after_seq(1)], [2, 3])

    def test_an_absent_store_resumes_as_empty_and_never_raises(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        # Built outside the assertion — `test_repo_paths_invariant` convicts an `os.path.join`
        # inside an assert in a module that posix-spells elsewhere, and it is right to: the two
        # spellings must never meet across a comparison. Second time this stage; same lesson.
        absent = os.path.join(d, "nope.db")
        self.assertEqual(EventStore(absent).after_seq(0), [])

    def test_both_readers_go_through_ONE_row_builder(self):
        """A column added to one SELECT and wired up in one reader only is the drift this closes."""
        src = io.open(os.path.join(SRC, "events", "store.py"), encoding="utf-8").read()
        self.assertEqual(src.count("def _row_to_event"), 1)
        self.assertEqual(src.count("self._row_to_event(r)"), 2)


# -------------------------- 6 · THE INVARIANT doc 77 ASKED FOR, as a guard instead of a sentence

# What makes a class a CONNECTION OWNER. DERIVED, not typed: the owner set is whatever reaches one
# of the two sanctioned connection factories, so a new backend joins the set by existing rather
# than by somebody remembering to add it here (§7j).
_CONNECTION_FACTORIES = ("connect_psycopg", "connect_sqlite")


def _trees(src=SRC):
    """CORPUS: THE WORKING TREE — this asks what mokata SHIPS, and `sync-public.sh` mirrors `src/`
    with `rsync`, so an untracked `.py` under `src/` really is published."""
    out = {}
    for base, _dirs, files in os.walk(src):
        if "__pycache__" in base:
            continue
        for fn in sorted(f for f in files if f.endswith(".py")):
            path = os.path.join(base, fn)
            with io.open(path, encoding="utf-8") as fh:
                out[_support.posix_rel(path, src)] = ast.parse(fh.read(), filename=path)
    return out


def connection_owners(trees):
    owners = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) \
                    and any(m in ast.dump(node) for m in _CONNECTION_FACTORIES):
                owners.add(node.name)
    return owners


def _owner_call(node, owners):
    """The owner class a call expression constructs — BARE or ATTRIBUTE-QUALIFIED.

    ⛔ `isinstance(func, ast.Name)` alone was the hole: `store.EventStore(path)` and
    `S.EventStore(path)` are the ordinary spellings in a module that imports the module rather
    than the name, and both walked straight through (review F3)."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id in owners:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in owners:
        return func.attr
    return None


def _builds_owner(node, owners):
    for inner in ast.walk(node):
        built = _owner_call(inner, owners)
        if built:
            return built
    return None


def long_lived_holders(trees):
    """Every place a connection owner could OUTLIVE one call.

    ⛔ **THIS KNEW THREE SHAPES AND ITS DOCSTRING SAID THREE WERE "THE WAYS PYTHON KEEPS A
    THING".** The stage's independent review planted seven realistic holders and watched FIVE walk
    through (F3): attribute-qualified construction, an aliased module, a LOWERCASE container, a
    `global` rebound inside a function, and `self.store = EventStore(root)` on a server class.
    ⭐ **That last one is the ID.S3 shape** — and the class below promises, in bold, to red when
    ID.S3's long-lived IDE server lands. A guard that cannot see the shape it promises about turns
    a forward claim into §7h, and a guard graded only by its own three fixtures grades its own
    three fixtures (§7i).

    Six shapes now, and the three added are named by what keeps the reference alive rather than by
    how it is spelled: a module-level binding, a cached function, a container entry, a `global`
    rebind, an instance or class attribute, and any of those reached through an attribute-qualified
    constructor."""
    found = []
    owners = connection_owners(trees)
    for rel, tree in trees.items():
        for node in tree.body:                               # module level only
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                built = _builds_owner(node, owners)
                if built:
                    found.append((rel, node.lineno, f"module-level {built}()"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decs = " ".join(ast.dump(d) for d in node.decorator_list)
                if "lru_cache" in decs or "cached_property" in decs or "'cache'" in decs:
                    built = _builds_owner(node, owners)
                    if built:
                        found.append((rel, node.lineno, f"cached {node.name}() builds {built}()"))
                # ⭐ A `global` rebind outlives the call that performed it, which is the whole
                # point of the statement — so the DECLARATION is what makes the name long-lived,
                # and the set is read off the function rather than guessed from the name's case.
                declared_global = {name for stmt in ast.walk(node)
                                   if isinstance(stmt, ast.Global) for name in stmt.names}
                for stmt in ast.walk(node):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    built = _owner_call(stmt.value, owners)
                    if not built:
                        continue
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id in declared_global:
                            found.append((rel, stmt.lineno, f"global {target.id} = {built}()"))
            if isinstance(node, ast.Assign):
                built = _owner_call(node.value, owners)
                if not built:
                    continue
                for target in node.targets:
                    # ⚠ NO `.isupper()`. A container is long-lived because it is a container, not
                    # because somebody spelled it in capitals — `_cache[root] = EventStore(root)`
                    # keeps the connection exactly as hard as `_CACHE[root]` does.
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                        found.append((rel, node.lineno, f"{target.value.id}[...] = {built}()"))
                    elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                            and target.value.id in ("self", "cls"):
                        found.append((rel, node.lineno,
                                      f"{target.value.id}.{target.attr} = {built}()"))
    return sorted(set(found))


class NoLongLivedStoreHoldsAConnection(unittest.TestCase):
    """doc 77 CM.S5, verbatim: *"pull-based freshness is acceptable BECAUSE stores are per-call —
    document that invariant so nobody introduces a long-lived store."*

    ⭐ It was prose, and it is now the WHOLE freshness story, because CM.S3 deleted the cache this
    row was written to invalidate. ⛔ And it is about to be tested for real: ID.S3's IDE server
    (0.0.24, doc 86's IDE-TRACK map) is a long-lived process by definition. **This guard reddening
    when that lands is the CORRECT outcome** — it forces the IDE to deal with freshness rather
    than inherit a store that has quietly gone stale."""

    @classmethod
    def setUpClass(cls):
        cls.trees = _trees()

    def test_the_sweep_FINDS_the_connection_owners_or_it_grades_nothing(self):
        """§7i, and the floor is derived: a sweep whose corpus comes back empty passes for the
        wrong reason. The known owners are asserted as a SUBSET so new ones join without an edit."""
        owners = connection_owners(self.trees)
        self.assertGreaterEqual(len(owners), 5, f"the owner derivation collapsed: {owners}")
        for known in ("EventStore", "PostgresBackend", "SQLiteBackend", "SharedEventLog"):
            self.assertIn(known, owners)

    def test_NOTHING_in_the_tree_holds_a_connection_owner_across_calls(self):
        holders = long_lived_holders(self.trees)
        self.assertEqual(holders, [],
                         "a connection owner outlives one call, so pull-based freshness is no "
                         "longer true of this tree and `mokata_events` push stops being "
                         "optional:\n" + "\n".join(f"  {r}:{ln}  {w}" for r, ln, w in holders))

    def test_a_MODULE_LEVEL_store_is_caught(self):
        self.assertTrue(self._planted("_STORE = EventStore('/tmp/x.db')\n"))

    def test_a_CACHED_FACTORY_is_caught(self):
        self.assertTrue(self._planted(
            "import functools\n"
            "@functools.lru_cache(maxsize=None)\n"
            "def store_for(root):\n"
            "    return EventStore(root)\n"))

    def test_a_CONTAINER_WRITE_is_caught(self):
        self.assertTrue(self._planted(
            "_CACHE = {}\n"
            "def get(root):\n"
            "    _CACHE[root] = EventStore(root)\n"
            "    return _CACHE[root]\n"))

    def test_an_ORDINARY_per_call_construction_is_NOT_caught(self):
        """The negative control, and it is the one that makes the three above meaningful: the
        shipped shape — build it, use it, drop it — must pass, or the guard would simply forbid
        using a store at all."""
        self.assertFalse(self._planted(
            "def read(root):\n"
            "    store = EventStore(root)\n"
            "    return store.query()\n"))

    # ---- the five shapes the review planted and the first version missed (F3) ----------------
    #
    # ⭐ EACH ONE IS HERE BECAUSE IT WALKED THROUGH. The guard's three original fixtures were the
    # three shapes it could see, which is the circularity §7i names: the corpus was the author's
    # imagination. These five came from somebody else's.

    def test_an_ATTRIBUTE_QUALIFIED_constructor_is_caught(self):
        """`store.EventStore(path)` — the ordinary spelling when a module imports the module."""
        self.assertTrue(self._planted(
            "from mokata.events import store\n"
            "_STORE = store.EventStore('/tmp/x.db')\n"))

    def test_an_ALIASED_module_does_not_hide_the_constructor(self):
        self.assertTrue(self._planted(
            "from mokata.events import store as S\n"
            "_STORE = S.EventStore('/tmp/x.db')\n"))

    def test_a_LOWERCASE_container_is_caught(self):
        """A container keeps the connection because it is a container. The `.isupper()` filter
        made the guard's reach depend on a naming convention nothing enforces."""
        self.assertTrue(self._planted(
            "_cache = {}\n"
            "def store_for(root):\n"
            "    _cache[root] = EventStore(root)\n"
            "    return _cache[root]\n"))

    def test_a_GLOBAL_rebound_inside_a_function_is_caught(self):
        """The `global` statement is the declaration that the binding outlives the call."""
        self.assertTrue(self._planted(
            "_STORE = None\n"
            "def store_for(root):\n"
            "    global _STORE\n"
            "    _STORE = EventStore(root)\n"
            "    return _STORE\n"))

    def test_an_INSTANCE_ATTRIBUTE_on_a_server_class_is_caught(self):
        """⭐ **THE ID.S3 SHAPE.** A long-lived process holding its store on `self` is what the
        class docstring promises to red on, and it was the shape the guard could least see."""
        self.assertTrue(self._planted(
            "class Server:\n"
            "    def __init__(self, root):\n"
            "        self.store = EventStore(root)\n"))

    def test_a_CLASS_ATTRIBUTE_assigned_in_a_classmethod_is_caught(self):
        self.assertTrue(self._planted(
            "class Server:\n"
            "    @classmethod\n"
            "    def setup(cls, root):\n"
            "        cls.store = EventStore(root)\n"))

    def test_a_PER_CALL_attribute_on_a_LOCAL_object_is_NOT_caught(self):
        """The negative control for the two above. `self`/`cls` are what make an attribute
        outlive the call; an attribute on a local object does not, and a guard that cannot tell
        them apart convicts every correct per-call use and gets deleted (§7f)."""
        self.assertFalse(self._planted(
            "def read(root):\n"
            "    holder = Holder()\n"
            "    holder.store = EventStore(root)\n"
            "    return holder.store.query()\n"))

    def _planted(self, source):
        trees = dict(self.trees)
        trees["_planted.py"] = ast.parse(source)
        return any(rel == "_planted.py" for rel, _ln, _why in long_lived_holders(trees))

    def test_the_known_module_level_caches_hold_VALUES_and_not_stores(self):
        """The adjacent true thing, so the guard above is not misread as "no module-level state".
        `events.store._ENABLED_CACHE` is a per-root BOOL and is a CORRECTION rather than an
        optimisation — `test_mcp_surf_single_load` convicted the version that rebuilt a `Surface`
        per ledger instance. Caching a VALUE is fine; caching a CONNECTION is the defect."""
        from mokata.events import store as STORE
        STORE.reset_enabled_cache()
        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        STORE.enabled_for_root(root)
        self.assertTrue(STORE._ENABLED_CACHE, "the cache under test is empty, so this grades 0")
        for value in STORE._ENABLED_CACHE.values():
            self.assertIsInstance(value, bool)


class TheEventStoreIsPerCallByConstruction(unittest.TestCase):

    def test_store_for_root_hands_back_a_FRESH_store_every_time(self):
        """The positive statement of the invariant, asserted on the object rather than inferred
        from the absence of a cache."""
        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertIsNot(store_for_root(root), store_for_root(root))

    def test_the_shared_log_is_built_per_publish_and_CLOSED(self):
        src = io.open(os.path.join(SRC, "team_events.py"), encoding="utf-8").read()
        self.assertIn("log.close()", src, "the publish path leaks its connection")
        self.assertIn("if client is None:", src,
                      "an INJECTED connection must not be closed by the callee that borrowed it")

    def test_the_live_round_trip_lives_in_the_INTEGRATION_suite_and_exists(self):
        """⚠ This file cannot grade absence-crossing-as-NULL or a received NOTIFY without a
        server, and mocking one and calling it measured is the failure this release keeps finding.
        The live leg is a real file, and this asserts it is THERE — a pointer to a module that
        does not exist is how a measurement goes missing between two suites."""
        live = os.path.join(ROOT, "tests", "integration", "test_cm_s5_live_db.py")
        self.assertTrue(os.path.isfile(live), f"the live CM.S5 leg is missing: {live}")
        body = io.open(live, encoding="utf-8").read()
        self.assertIn("MOKATA_LIVE_DB", body, "the live leg is not gated like its siblings")
        # The RECEIVE side is the half only a server can grade — `pg_notify` lives in the
        # publisher, so looking for it here would have passed on a leg that never listened.
        self.assertIn("LISTEN ", body, "the live leg never subscribes")
        self.assertIn("notifies(", body, "the live leg never waits for a notification")
        self.assertIn("NOTHING was changed", body,
                      "the live leg does not exercise the v6 refusal")


# ----------------------------------------- 7 · §7i — the publish must be REACHABLE FROM THE CLI

class ThePublishIsReachableFromASURFACE(unittest.TestCase):
    """⛔ THE NEAR-MISS THIS CLASS EXISTS FOR. `team_events.publish` was written and graded
    against a real Postgres while being reachable from NOTHING — a perfect mechanism nobody runs,
    which is §7i and is the class this release has filed more often than any other. It was caught
    by asking *"what command invokes this?"* after the code was green, which is later than it
    should have been asked.

    Built from the REAL parser, not from a grep: a `register` call that is never reached, or a
    subparser whose `func` points at the wrong handler, both pass a text search."""

    def _parser(self):
        from mokata.cli import build_parser
        return build_parser()

    def test_mokata_events_is_a_real_subcommand(self):
        args = self._parser().parse_args(["events"])
        self.assertTrue(callable(getattr(args, "func", None)))

    def test_it_dispatches_to_the_events_handler_and_not_to_a_neighbour(self):
        from mokata.cli_commands.events import cmd_events
        self.assertIs(self._parser().parse_args(["events"]).func, cmd_events)

    def test_share_and_yes_are_both_parseable(self):
        args = self._parser().parse_args(["events", "--share", "--yes"])
        self.assertTrue(args.share)
        self.assertTrue(args.yes)

    def test_the_handler_actually_CALLS_publish(self):
        """A subcommand wired to a handler that does something else is the other half of the same
        failure. Asserted on the call, by substitution rather than by reading the source."""
        import mokata.team_events as live
        from mokata.cli_commands import events as cli_events
        calls = []

        class _Res:
            ok, reason = True, "committed"

        original = live.publish
        live.publish = lambda *a, **kw: (calls.append(kw) or _Res())
        try:
            root = a_repo()
            self.addCleanup(shutil.rmtree, root, True)
            import argparse
            rc = cli_events._cmd_events_share(
                argparse.Namespace(path=root, yes=True, share=True))
        finally:
            live.publish = original
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1, "the --share handler did not reach publish()")
        self.assertTrue(calls[0].get("assume_yes"), "--yes did not reach the gate")

    def test_the_status_surface_PRINTS_the_pooler_verdict(self):
        """The §7e declaration exists so a human can read it. A verdict that is computed and never
        displayed is a declaration nobody receives — so the surface is pinned, not just the
        function. ⚠ Keyed on the STATE CONSTANTS, never on the sentence, so rewording the message
        does not silently delete the guarantee."""
        src = io.open(os.path.join(SRC, "cli_commands", "events.py"), encoding="utf-8").read()
        for const in ("NOTIFY_SUPPORTED", "NOTIFY_POOLED", "NOTIFY_UNKNOWN"):
            self.assertIn(const, src, f"the status surface cannot render {const}")
        self.assertIn("notify_support(", src)


# ====================================================================================================
# CM.S5 REVIEW REPAIRS — F1 (the floor's degrade did not exist) and F2 (the pointer reported
# success over a skipped store). Both were found by the stage's independent review against a live
# server, and NEITHER had a behavioural test or a mutant before this block.
# ====================================================================================================

class _PointerConn(_SchemaAnswering):
    """A connection whose RESUME POINTER and CONFLICT behaviour are both dialled by the test.

    ⭐ `_FakeConn` answers `MAX(local_seq)` with `0` and every INSERT with `rowcount = 1`, which is
    the ONE arrangement in which the pointer cannot lie. That is why 57 tests passed over F2."""

    rowcount = 1

    def __init__(self, mark=0, conflict_all=False):
        self.mark = int(mark)
        self.conflict_all = bool(conflict_all)
        self.inserts = 0
        self.notifies = 0
        self.closed = 0

    def execute(self, sql, params=None):
        answer = self._schema_answer(sql)
        if answer is not None:
            return answer
        head = sql.lstrip().upper()
        if "MAX(LOCAL_SEQ)" in head:
            return _OneRow((self.mark,))
        if head.startswith("INSERT"):
            self.inserts += 1
            self.rowcount = 0 if self.conflict_all else 1
            return self
        if "PG_NOTIFY" in head:
            self.notifies += 1
        return self

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []

    def close(self):
        self.closed += 1


class ThePreV6StoreDeclinesByNameRatherThanTracebacking(unittest.TestCase):
    """⛔ `teamdb`'s floor note claimed a v5 store *"simply declines it with a named degrade"*, in
    three places, and **no such decline existed** (review F1). `compatibility()` is IN RANGE for
    v3/v4/v5 because the global floor is 3, so the publisher's first statement asked the pre-v6
    table for `local_seq` and a v5 team — **every existing team** — got `UndefinedColumn` as an
    uncaught traceback out of `mokata events --share`.

    §7h: the pin encoded a false premise. ⭐ And the stage's own mutant graded the CONSTANT while
    nothing graded the behaviour the constant is named for — which is why this class asks the
    BEHAVIOUR of each version and not the number."""

    def _conn_at(self, version):
        conn = _PointerConn()
        conn.schema_version = version
        return conn

    def test_a_v5_store_RAISES_THE_TYPED_DEGRADE_and_names_the_remedy(self):
        with self.assertRaises(TE.SharedEventsUnavailable) as caught:
            TE.SharedEventLog(client=self._conn_at(5))
        msg = str(caught.exception)
        self.assertIn("v%d" % teamdb.EVENTS_SCHEMA_MIN, msg)
        self.assertIn("v5", msg)
        self.assertIn("mokata team init", msg, "a degrade that names no remedy is a traceback "
                                               "with better manners")

    def test_every_version_BELOW_the_floor_declines_and_the_floor_itself_does_not(self):
        """The whole in-range band, not one sample: v3 and v4 are as `compatible` as v5 and were
        as broken."""
        for version in range(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, teamdb.EVENTS_SCHEMA_MIN):
            with self.subTest(version=version):
                self.assertTrue(
                    teamdb.compatibility(version, teamdb.TEAM_SCHEMA_MIN_SUPPORTED).compatible,
                    "this version is no longer in range, so the premise of F1 has changed and "
                    "this test is pinning a situation that cannot arise — re-derive it")
                with self.assertRaises(TE.SharedEventsUnavailable):
                    TE.SharedEventLog(client=self._conn_at(version))
        TE.SharedEventLog(client=self._conn_at(teamdb.EVENTS_SCHEMA_MIN))   # must NOT raise

    def test_an_UNPROVISIONED_store_is_told_apart_from_an_OLD_one(self):
        """§7g — "there is no schema" and "the schema is too old" are different instructions."""
        conn = self._conn_at(None)
        with self.assertRaises(TE.SharedEventsUnavailable) as caught:
            TE.SharedEventLog(client=conn)
        self.assertIn("not provisioned", str(caught.exception))

    def test_a_schema_read_that_EXPLODES_is_a_named_degrade_and_not_a_pass(self):
        class _Hostile(_PointerConn):
            def execute(self, sql, params=None):
                if "mokata_schema_version" in sql:
                    raise RuntimeError("connection reset")
                return super().execute(sql, params)

        with self.assertRaises(TE.SharedEventsUnavailable) as caught:
            TE.SharedEventLog(client=_Hostile())
        self.assertIn("could not be read", str(caught.exception))

    def test_the_FEATURE_floor_is_the_schema_this_build_speaks(self):
        """The tripwire, as an assertion. The events table gains a column the publisher reads →
        `TEAM_SCHEMA_VERSION` moves → this reds unless `EVENTS_SCHEMA_MIN` moved with it."""
        self.assertEqual(teamdb.EVENTS_SCHEMA_MIN, teamdb.TEAM_SCHEMA_VERSION)
        self.assertGreater(teamdb.EVENTS_SCHEMA_MIN, teamdb.TEAM_SCHEMA_MIN_SUPPORTED,
                           "the feature floor is at or below the global one, so it is not a "
                           "feature floor and this whole class is unreachable")

    def test_the_GLOBAL_floor_did_NOT_move_which_is_the_point_of_a_feature_floor(self):
        """⛔ The repair that would have been wrong: raising `TEAM_SCHEMA_MIN_SUPPORTED` to 6
        refuses a v5 team's memory, sessions and audit over a table it is not using — the
        fail-closed trap that constant documents at length."""
        self.assertEqual(3, teamdb.TEAM_SCHEMA_MIN_SUPPORTED)
        self.assertTrue(teamdb.compatibility(5, 3).compatible)


class TheResumePointerCannotReportSuccessOverASkippedStore(unittest.TestCase):
    """⛔ The pointer is `MAX(local_seq)` for this (namespace, actor) in the TEAM's table — a
    borrowed memory that assumes one producer per name and a local store whose seqs only rise.
    Review F2 broke both against a live server, and in every case the answer was `ok=True`,
    *"nothing new to publish"*, MCP `in_sync`, exit 0, with everything local unpublished.

    ⚠ These tests grade the REFUSALS, not a fix. The fix is a local high-water mark
    (`PUBLISH-POINTER-IS-BORROWED-MEMORY`, doc 84). What they forbid is a GREEN over a skip."""

    def setUp(self):
        from mokata import config_cmd
        self.root = a_repo()
        config_cmd.config_set(self.root, f"settings.{EVENTS_SETTINGS_KEY}.shared", "true",
                              assume_yes=True, out=lambda *_a: None)
        for i in range(4):
            emit(self.root, MemoryOp(op="read", item_id="item-%d" % i, count=1),
                 session_id="s1", actor="alice")
        self.store = store_for_root(self.root)
        # ⭐ DERIVED, never typed: enabling the setting is itself a gated write and emits its own
        # `gate_decision`, so the store holds MORE than the four events this fixture emits. A
        # typed `4` passed when it was written and would red the day another emitter is added —
        # and every assertion below is about an OFFSET from this number, not about the number.
        self.high = self.store.max_seq()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_local_store_reports_its_OWN_high_water(self):
        """The control. Without this, every assertion below could be passing on a zero."""
        self.assertGreaterEqual(self.high, 4, "the fixture's own emits did not land")
        self.assertEqual(self.high, self.store.count(),
                         "`seq` has a gap, so every `high - sent` subtraction below is wrong")

    def test_a_pointer_AHEAD_of_the_local_store_REFUSES_instead_of_reporting_in_sync(self):
        conn = _PointerConn(mark=5000)
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertFalse(res.ok, "a publish that skipped every local event reported success")
        self.assertEqual("pointer-ahead", res.reason)
        self.assertEqual(0, conn.inserts)
        self.assertIn("MOKATA_ACTOR", res.message, "the refusal names no remedy")
        self.assertIn("5000", res.message)

    def test_a_pointer_LEVEL_WITH_the_store_is_the_ordinary_nothing_new(self):
        """The other half of the same comparison: `mark == local_high` is a publisher that is up
        to date, and it must NOT be caught by the guard above (§7f — a guard that convicts the
        healthy case is one that gets deleted)."""
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True,
                         client=_PointerConn(mark=self.high))
        self.assertTrue(res.ok)
        self.assertEqual("nothing-new", res.reason)

    def test_an_UNREADABLE_local_store_is_not_an_EMPTY_one(self):
        """§7g, and review F7: `count()` and `after_seq` both swallow a corrupt store into
        `0`/`[]`, so *"nothing to publish"* and *"I cannot see your events"* rendered identically
        on the one surface a human checks. `max_seq()` answers `None` and the publisher refuses."""
        with io.open(self.store.path, "wb") as fh:
            fh.write(b"this is not a sqlite database")
        self.assertIsNone(self.store.max_seq())
        self.assertEqual(0, self.store.count(), "count() still answers 0 — that is its contract, "
                                                "and the reason the publisher cannot read it")
        conn = _PointerConn()
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertFalse(res.ok)
        self.assertEqual("local-unreadable", res.reason)
        self.assertEqual(0, conn.inserts)

    def test_a_STUCK_pointer_refuses_rather_than_reporting_a_committed_publish(self):
        """The head is already in the team's store under another name, so `MAX(local_seq)` for
        THIS name never moves and the tail behind it can never be reached (F2, scenario b)."""
        from unittest import mock
        conn = _PointerConn(conflict_all=True)
        with mock.patch.object(TE, "PUBLISH_BATCH_CAP", 2):
            res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertFalse(res.ok, "a stalled publisher reported a committed publish")
        self.assertEqual("pointer-stuck", res.reason)
        self.assertEqual(2, res.attempted)
        self.assertEqual(self.high - 2, res.remaining, "the unreachable tail is not counted")
        self.assertIn("MOKATA_ACTOR", res.message)

    def test_the_SAME_arrangement_with_rows_LANDING_is_an_ordinary_publish(self):
        """The control for the test above — same cap, same backlog, inserts that land."""
        from unittest import mock
        conn = _PointerConn(conflict_all=False)
        with mock.patch.object(TE, "PUBLISH_BATCH_CAP", 2):
            res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertTrue(res.ok, res.message)
        self.assertEqual("committed", res.reason)
        self.assertEqual(2, res.published)
        self.assertEqual(self.high - 2, res.remaining)

    def test_REMAINING_is_a_COUNT_and_not_a_zero_or_one_FLAG(self):
        """Review F4. `fetched` is capped at `cap + 1`, so the old `len(fetched) - len(batch)` was
        0 or 1 and nothing else — printed as "1 more queued" with any backlog at all."""
        from unittest import mock
        with mock.patch.object(TE, "PUBLISH_BATCH_CAP", 1):
            res = TE.publish(self.root, a_surface(self.root), assume_yes=True,
                             client=_PointerConn())
        self.assertEqual(self.high - 1, res.remaining,
                         "one event was sent, so the rest are still queued and the result must "
                         "say how many — a flag would say 1 whatever the backlog")
        self.assertIn("%d more queued" % (self.high - 1), res.message)

    def test_the_PREVIEW_agrees_with_the_publisher_on_every_one_of_those_answers(self):
        """`pending_publish` read the same capped fetch and printed its length as an exact figure.
        Two surfaces over one question must not disagree."""
        ok, n, _env, msg = TE.pending_publish(self.root, a_surface(self.root),
                                              client=_PointerConn())
        self.assertTrue(ok)
        self.assertEqual(self.high, n)
        ok, n, _env, msg = TE.pending_publish(self.root, a_surface(self.root),
                                              client=_PointerConn(mark=5000))
        self.assertFalse(ok, "the preview printed a confident count over a contradicted pointer")
        self.assertEqual(0, n)
        self.assertIn("MOKATA_ACTOR", msg)


class TheStatusWordTellsSuccessFromRefusal(unittest.TestCase):
    """review F5 — a fully deduplicated publish COMMITTED, published nothing, and reported
    `status: "blocked"`: the same word the secret-scan refusal returns, with `committed: true`
    beside it. Graded as a PURE FUNCTION over every reason both publishers can return, because the
    live call that produced the defect returns two of them."""

    def _reasons_in(self, module_rel, result_class):
        """Every `reason=` string the module can return, read off its own source. ⭐ DERIVED: a
        reason added without a status arm is exactly how a word falls through to `blocked`, and a
        typed list here would not notice (§7j)."""
        text = io.open(os.path.join(SRC, module_rel), encoding="utf-8").read()
        found = set(re.findall(r'reason=["\']([a-z0-9 \-]+)["\']', text))
        self.assertTrue(found, "no reasons parsed, so this grades nothing")
        return found

    def test_a_DEDUPLICATED_publish_is_committed_and_not_blocked(self):
        from mokata.mcp.tools_team import publish_status
        self.assertEqual("committed", publish_status(
            "committed", committed=True, in_sync=("nothing-new", "only-own-trail"),
            unavailable=("unavailable", "local-unreadable")))

    def test_a_REFUSAL_is_blocked_and_a_SUCCESS_never_is(self):
        from mokata.mcp.tools_team import publish_status
        for refusal in ("pointer-ahead", "pointer-stuck", "shared-off"):
            with self.subTest(reason=refusal):
                self.assertEqual("blocked", publish_status(
                    refusal, committed=False, in_sync=("nothing-new", "only-own-trail")))

    def test_EVERY_reason_the_events_publisher_can_return_has_a_status_arm(self):
        """§7g as a totality check: a reason with no arm is silently a refusal."""
        from mokata.mcp.tools_team import publish_status
        expected = {
            "committed": "committed", "only-own-trail": "in_sync", "nothing-new": "in_sync",
            "unavailable": "unavailable", "local-unreadable": "unavailable",
            "pointer-ahead": "blocked", "pointer-stuck": "blocked", "shared-off": "blocked",
        }
        for reason in sorted(self._reasons_in("team_events.py", TE.PublishResult)):
            with self.subTest(reason=reason):
                self.assertIn(reason, expected,
                              "a new publish reason with no declared status: it currently reads "
                              "as `blocked`, which is what a secret-scan refusal reads as")
                self.assertEqual(
                    expected[reason],
                    publish_status(reason, committed=(reason == "committed"),
                                   in_sync=("nothing-new", "only-own-trail"),
                                   unavailable=("unavailable", "local-unreadable")))

    def test_the_AUDIT_surface_shares_the_one_function_and_not_the_one_defect(self):
        """The identical shape shipped on `audit_share` before CM.S5 existed. One function, two
        declared vocabularies — because the vocabularies differ and hiding that is the defect."""
        from mokata.mcp.tools_team import publish_status
        self.assertEqual("committed",
                         publish_status("committed", committed=True, in_sync=("in sync",)))
        self.assertEqual("in_sync",
                         publish_status("in sync", committed=True, in_sync=("in sync",)))
        text = io.open(os.path.join(SRC, "mcp", "tools_team.py"), encoding="utf-8").read()
        self.assertEqual(2, text.count("status = publish_status("),
                         "a status word is being computed somewhere other than the one function")
        self.assertNotIn("and res.published else", text,
                         "the `committed if ... and published` shape is back")


class TheDoorbellIsNotRungForAnEmptyDoorstep(unittest.TestCase):
    """review F6 — `notify` is sent AFTER the inserts, which its docstring argues at length so a
    woken reader never finds an empty doorstep, and was then sent UNCONDITIONALLY. A batch whose
    every row conflicted woke every listener in the team to SELECT nothing."""

    def setUp(self):
        from mokata import config_cmd
        self.root = a_repo()
        config_cmd.config_set(self.root, f"settings.{EVENTS_SETTINGS_KEY}.shared", "true",
                              assume_yes=True, out=lambda *_a: None)
        emit(self.root, MemoryOp(op="read", item_id="item", count=1),
             session_id="s1", actor="alice")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_batch_where_NOTHING_landed_sends_NO_notification(self):
        conn = _PointerConn(conflict_all=True)
        TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertGreater(conn.inserts, 0, "nothing was attempted, so this grades nothing")
        self.assertEqual(0, conn.notifies, "the doorbell rang for an empty doorstep")

    def test_a_batch_where_ROWS_landed_DOES_send_one(self):
        """The control. Without it, the condition above could be `if False`."""
        conn = _PointerConn(conflict_all=False)
        res = TE.publish(self.root, a_surface(self.root), assume_yes=True, client=conn)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(1, conn.notifies)


if __name__ == "__main__":
    unittest.main()
