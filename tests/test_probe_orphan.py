"""PROBE-ORPHAN — an abandoned `teamdb.probe` worker must not install a connection into `_MANAGER`.

THE DEFECT (doc 84, root-caused 2026-08-01 as the product-side residual under WIN-DOCTOR-SCHEMA).
`teamdb.probe` runs its work in a DAEMON THREAD joined for `budget_ms`; on timeout it returns
"unreachable — no response within Nms" and walks away, but the thread lives on. It connected
through `_pg.get_connection`, which PUBLISHES into the process-global `_MANAGER` — so when the
slow `psycopg.connect` finally returned, the connection was cached with nobody waiting for it.

Two consequences, and both are pinned below:

  1. A probe that reported UNREACHABLE could leave a LIVE CACHED CONNECTION on that DSN, which the
     next caller silently reused — a connection whose own probe said the database was down.
  2. `reset_manager()` was NOT AUTHORITATIVE while a probe was in flight: a teardown that ran
     BEFORE the orphan landed was simply overwritten by it.

Measured at filing, not theorised: `_MANAGER` was empty immediately after the bounded probe and
held the orphan's connection 2.5s later. That is exactly the shape `test_the_orphan_never_lands`
reproduces — a connect slow enough to miss the budget, then observed after it completes.

WHY THE FIX IS STRUCTURAL rather than a flag. A worker that can reach the cache at all must be
trusted to check "did my caller give up?" at exactly the right moment, and that check races the
caller's own decision. Instead the worker now opens an UNMANAGED connection (`_pg.open_unmanaged`)
that it holds privately, and only a caller STILL WAITING publishes it (`_pg.adopt`). A worker that
cannot publish cannot orphan, whatever the timing.

NO REAL DATABASE IS NEEDED. The defect is about WHO PUBLISHES and WHEN, not about psycopg — so
these drive the real `probe`/`_MANAGER` code with `_raw_connect` replaced by a controllable
double. A test that needed a live Postgres would not run here, and this is precisely the class of
bug that must be pinned where it is cheap to run.

BUT NO DRIVER IS NOT THE SAME AS NO DATABASE, and conflating the two is what made this file lie
for one commit. `probe` opens with a DRIVER-PRESENCE GATE (`importlib.util.find_spec("psycopg")`)
and returns `driver-absent` BEFORE it ever starts the worker thread. Doubling `_raw_connect` does
not get past that gate — it is upstream of the seam. So on a machine with the `postgres` extra
installed these passed, and on the CI unit legs (`pip install -e .`, no extra) the same four went
red with "the worker never started — the test proved nothing". That message was the test being
HONEST: the probe really had returned without probing.

The gate is therefore a PRECONDITION these tests must STATE, not inherit from the ambient
environment. `setUp` installs a spec-bearing psycopg stub (the idiom already used by
`test_d1_d2_schema_ddl_version` and `test_tm_s3_team_init`) so the gate reads PRESENT on every
machine, in both directions: a runner without the extra no longer short-circuits, and a runner
WITH it no longer reaches the real driver. `test_the_gate_itself` pins the absent path, so
closing the hole does not cost the coverage that accidentally lived in it.
"""
import importlib.machinery
import importlib.util
import sys
import threading
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import teamdb
from mokata.memory import _pg

DSN = "postgresql://probe-orphan@localhost:5432/nowhere"

# How long a latch may wait before we call it a hang. Generous on purpose: it is a FAILURE
# DETECTOR, never the synchronisation itself — every wait below is released by a real event, so a
# green run does not spend this budget and a loaded runner cannot turn a pass into a fail by being
# slow. Widening it can only ever convert a hang into a slower hang, never a red into a green.
HANG_S = 30


class _PsycopgStub:
    """A stand-in module that satisfies `probe`'s driver-presence gate and NOTHING else.

    `importlib.util.find_spec` reads `__spec__` off an already-imported module, so a stub without
    one still reads as ABSENT — the spec is the whole point of this object.

    `connect` raises rather than returning a fake: every test here doubles `_pg._raw_connect`, so
    nothing should ever reach the driver. If something does, that is a test which stopped
    exercising the seam it claims to, and it must say so loudly instead of quietly succeeding.
    """

    __spec__ = importlib.machinery.ModuleSpec("psycopg", None)

    @staticmethod
    def connect(*a, **kw):                               # pragma: no cover - a tripwire
        raise AssertionError(
            "the psycopg stub's connect() was called — `_pg._raw_connect` was not doubled, so "
            "this test is no longer exercising the probe seam it claims to")


class FakeConn:
    """The smallest thing `probe` treats as a connection. Records its own closure so a test can
    tell "closed" from "leaked" — the two failure modes are different and only one is safe.

    `close()` also SETS A LATCH. The abandoned worker finishes on its own schedule by design, so a
    test that needs it done must wait for the event the worker actually raises; polling `closed`
    with a sleep would make the wall clock, not the program, decide whether the test passed."""

    def __init__(self, label="conn"):
        self.label = label
        self.closed = 0
        self.executed = []
        self.close_latch = threading.Event()

    def execute(self, sql, *args):
        self.executed.append(sql)
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = 1
        self.close_latch.set()

    def await_closed(self, case):
        """Block until the worker has demonstrably closed this connection."""
        case.assertTrue(self.close_latch.wait(HANG_S),
                        f"the probe worker never closed {self!r} — it is still in flight, so "
                        f"anything asserted about _MANAGER now would be asserted too early")

    def __repr__(self):                                  # pragma: no cover - debugging aid
        return f"<FakeConn {self.label} closed={self.closed}>"


class ProbeOrphanTest(unittest.TestCase):
    def setUp(self):
        _pg.reset_manager()
        self.addCleanup(_pg.reset_manager)
        self._install_driver()

    def _install_driver(self):
        """State the precondition every probe-driving test in this class depends on: the driver
        reads as PRESENT, so `probe` gets past its gate and actually starts a worker."""
        previous = sys.modules.get("psycopg")
        sys.modules["psycopg"] = _PsycopgStub

        def restore():
            if previous is None:
                sys.modules.pop("psycopg", None)
            else:
                sys.modules["psycopg"] = previous

        self.addCleanup(restore)
        self.assertIsNotNone(importlib.util.find_spec("psycopg"),
                             "the driver-presence stub did not take — every probe below would "
                             "return `driver-absent` without starting a worker, and would then "
                             "pass or fail for a reason that has nothing to do with PROBE-ORPHAN")

    def assertProbedForReal(self, result):
        """Guard against the vacuous pass. Several assertions below ("not reachable", "the cache
        is empty") are ALSO satisfied by a probe that bailed at the driver gate without opening
        anything — which is exactly how this file went green on a machine that had the extra and
        red on one that did not. Every not-reachable expectation states this too."""
        self.assertTrue(result.driver_present)
        self.assertNotEqual("driver-absent", result.error,
                            "the probe never ran — it returned at the driver-presence gate, so "
                            "this test proved nothing about who publishes a connection")

    # ------------------------------------------------------------------ the defect itself
    def test_the_orphan_never_lands(self):
        """THE pin. A connect slower than the probe's budget: the probe must report unreachable AND
        the late connection must never appear in `_MANAGER`.

        The 2.5s observation window at filing is compressed here — the assertion is made after the
        worker has demonstrably FINISHED (joined), which is strictly stronger than sleeping and
        hoping the race resolved."""
        started, release = threading.Event(), threading.Event()
        conn = FakeConn("late")

        def slow_connect(dsn, **kw):
            started.set()
            release.wait(5)                              # outlive the probe's budget, deliberately
            return conn

        with mock.patch.object(_pg, "_raw_connect", slow_connect):
            result = teamdb.probe(DSN, budget_ms=50)
            self.assertTrue(started.wait(HANG_S),
                            "the worker never started — the test proved nothing")
            self.assertProbedForReal(result)
            self.assertFalse(result.reachable)
            self.assertEqual("timeout", result.error)
            # Let the abandoned worker finish, then wait on ITS latch for it to actually be done.
            release.set()
            conn.await_closed(self)

        self.assertNotIn(DSN, _pg._MANAGER,
                         "the abandoned probe worker installed its connection into the process-"
                         "global _MANAGER — the next caller will silently reuse a connection whose "
                         "own probe reported the database UNREACHABLE")
        self.assertEqual(1, conn.closed,
                         "the abandoned connection was neither cached NOR closed — it leaked. "
                         "Not caching it is only half the fix; the socket must be released.")

    def test_reset_manager_is_authoritative_while_a_probe_is_in_flight(self):
        """The second consequence. A teardown that runs BEFORE the orphan lands must STAY done —
        it used to be silently overwritten by the late publish."""
        started, release = threading.Event(), threading.Event()
        conn = FakeConn("late")

        def slow_connect(dsn, **kw):
            started.set()
            release.wait(5)
            return conn

        with mock.patch.object(_pg, "_raw_connect", slow_connect):
            result = teamdb.probe(DSN, budget_ms=50)
            self.assertTrue(started.wait(HANG_S),
                            "the worker never started — there was no in-flight probe for the "
                            "teardown below to race, so this proved nothing")
            self.assertProbedForReal(result)
            _pg.reset_manager()                          # teardown, while the worker is STILL live
            release.set()
            conn.await_closed(self)

        self.assertEqual({}, _pg._MANAGER,
                         "a `reset_manager()` that ran while a probe was in flight was overwritten "
                         "by the late worker — reset is not authoritative")

    # ------------------------------------------------------------------ the success path
    def test_a_probe_that_finishes_in_time_DOES_publish(self):
        """The positive control, and it is load-bearing: "never publishes" would also be satisfied
        by a fix that broke caching outright, costing every consumer a reconnect. The manager's
        one-connection-per-DSN benefit is preserved on the path that still has a waiter."""
        conn = FakeConn("prompt")
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: conn), \
                mock.patch.object(teamdb, "_read_schema_version", return_value=(True, 5, 3)):
            result = teamdb.probe(DSN, budget_ms=5_000)
        self.assertTrue(result.reachable)
        self.assertIs(conn, _pg._MANAGER.get(DSN),
                      "a probe that completed within its budget did NOT publish its connection — "
                      "every later consumer now pays a reconnect")
        self.assertEqual(0, conn.closed)

    def test_a_connection_that_connects_but_fails_its_round_trip_is_closed_not_cached(self):
        """Connected, then unusable. Caching it would hand the next caller a connection the probe
        itself just declared unreachable — the same aliasing hole by a different route."""
        conn = FakeConn("broken")
        conn.execute = mock.Mock(side_effect=RuntimeError("server closed the connection"))
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: conn):
            result = teamdb.probe(DSN, budget_ms=5_000)
        self.assertProbedForReal(result)
        self.assertFalse(result.reachable)
        self.assertNotIn(DSN, _pg._MANAGER)
        self.assertEqual(1, conn.closed, "an unusable connection was left open")

    def test_a_failed_connect_caches_nothing_and_still_reports_cleanly(self):
        with mock.patch.object(_pg, "_raw_connect",
                               side_effect=OSError("connection refused")):
            result = teamdb.probe(DSN, budget_ms=5_000)
        self.assertProbedForReal(result)
        self.assertFalse(result.reachable)
        self.assertEqual({}, _pg._MANAGER)

    # ------------------------------------------------------------- the precondition, pinned
    def test_the_gate_itself(self):
        """The driver-presence gate the four tests above must get PAST, pinned here so that
        stating it as a precondition does not silently drop it from the suite.

        This is also the negative control for `assertProbedForReal`: it is the one probe in this
        file that legitimately returns `driver-absent`, and it must do so WITHOUT starting a
        worker — the gate's entire job is to answer before anything is opened."""
        started = threading.Event()

        def must_not_run(dsn, **kw):                     # pragma: no cover - the point is 0 calls
            started.set()
            return FakeConn("should-never-exist")

        # Absence has to be STATED too, and for the same reason presence did: popping setUp's stub
        # only uncovers whatever the machine happens to have on disk. `sys.modules[...] = None`
        # makes `import psycopg` raise; patching `find_spec` makes discovery agree.
        # (The idiom `test_tm_s2_connection_probe._no_psycopg` already uses.)
        real_find_spec = importlib.util.find_spec
        sys.modules["psycopg"] = None

        def absent(name, *a, **k):
            return None if name == "psycopg" else real_find_spec(name, *a, **k)

        with mock.patch("importlib.util.find_spec", side_effect=absent), \
                mock.patch.object(_pg, "_raw_connect", must_not_run):
            result = teamdb.probe(DSN, budget_ms=5_000)

        self.assertFalse(result.driver_present)
        self.assertEqual("driver-absent", result.error)
        self.assertEqual(teamdb.CONN_DRIVER_ABSENT, result.conn_reason)
        self.assertFalse(result.reachable)
        self.assertFalse(started.is_set(),
                         "the driver-absent gate started a worker anyway — it is supposed to "
                         "answer before anything is opened")
        self.assertEqual({}, _pg._MANAGER)

    # ------------------------------------------------------------------ the seam's own contract
    def test_open_unmanaged_does_not_touch_the_cache(self):
        conn = FakeConn("private")
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: conn):
            got = _pg.open_unmanaged(DSN, RuntimeError)
        self.assertIs(conn, got)
        self.assertEqual({}, _pg._MANAGER,
                         "`open_unmanaged` published into _MANAGER — it is the seam whose entire "
                         "purpose is NOT to")

    def test_open_unmanaged_fails_exactly_as_get_connection_does(self):
        """The two differ ONLY in whether the result is published. A caller that swapped one for
        the other must not have to handle a different failure shape."""
        with mock.patch.object(_pg, "_raw_connect", side_effect=OSError("refused")):
            with self.assertRaises(ValueError):
                _pg.open_unmanaged(DSN, ValueError)
            with self.assertRaises(ValueError):
                _pg.get_connection(DSN, ValueError)

    def test_adopt_never_leaves_both_connections_open(self):
        """The no-leak invariant, stated over the PAIR rather than over one of them.

        This replaces `test_adopt_closes_a_connection_it_displaces`, which pinned the same
        invariant by naming WHICH connection closes — and named the wrong one (ADOPT-LIVE-HANDLE).
        The leak this guards is "two live sockets, one cache slot"; which of the two is disposed of
        is a separate decision, and the two tests below make it."""
        first, second = FakeConn("first"), FakeConn("second")
        _pg.adopt(DSN, first)
        _pg.adopt(DSN, second)
        self.assertEqual(1, first.closed + second.closed,
                         "exactly one of the two must be closed — both open leaks a socket, both "
                         "closed leaves the cache holding a dead connection")
        self.assertIn(DSN, _pg._MANAGER)
        self.assertEqual(0, _pg._MANAGER[DSN].closed,
                         "the connection left IN the cache is closed — every later consumer gets "
                         "a dead handle")

    def test_adopt_keeps_the_LIVE_cached_connection_and_closes_the_incoming_one(self):
        """ADOPT-LIVE-HANDLE. `adopt` displaced-and-closed the CACHED connection, and that is
        backwards: the cached connection is the one with OTHER REFERENTS.

        `PostgresBackend`, `PgVectorStore`, `PostgresTransport` and the shared audit ledger each
        take the managed connection ONCE in `__init__` and hold it as `self._conn` for their whole
        lifetime (20+ `self._conn.execute` sites in the backend alone). The incoming connection has
        exactly one referent — the probe worker that just opened it. So closing the cached one
        INVALIDATES live handles across the process, while closing the incoming one disposes of a
        redundancy nobody else can name. Same no-leak guarantee, opposite blast radius."""
        held = FakeConn("held-by-consumers")
        _pg.adopt(DSN, held)
        incoming = FakeConn("probe-opened")
        _pg.adopt(DSN, incoming)
        self.assertIs(held, _pg._MANAGER[DSN],
                      "a live cached connection was displaced — every consumer still holding it "
                      "as self._conn now has a closed handle")
        self.assertEqual(0, held.closed)
        self.assertEqual(1, incoming.closed, "the redundant incoming connection leaked")

    def test_adopt_DOES_replace_a_dead_cached_connection(self):
        """The converse, and it is load-bearing: "never displace" would also be satisfied by an
        adopt that could never refresh a broken cache, stranding the process on a dead socket.
        A cached connection that reports closed has nothing worth keeping."""
        dead = FakeConn("dead")
        _pg.adopt(DSN, dead)
        dead.close()
        incoming = FakeConn("fresh")
        _pg.adopt(DSN, incoming)
        self.assertIs(incoming, _pg._MANAGER[DSN],
                      "a DEAD cached connection was kept over a live incoming one — the cache "
                      "can no longer heal itself")
        self.assertEqual(0, incoming.closed)

    def test_a_probe_does_not_close_the_connection_its_consumers_are_holding(self):
        """The end-to-end shape, through the REAL `probe`, because this is how it reached users.

        A consumer takes the managed connection and holds it (what every Postgres consumer does).
        An ordinary probe then runs — `mokata doctor`, `team status`, or the 30s `team_health` TTL
        refresh that `MemoryStore.from_surface` triggers via `resolve_read_routing`. Before this
        fix the probe's `adopt` closed the consumer's handle, and its next statement raised
        `psycopg.OperationalError: the connection is closed`. Reproduced on live Postgres 16.14
        against a real `PostgresBackend`, and as the 2 of 77 DB.S8e errors in CI."""
        held = FakeConn("held")
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: held):
            self.assertIs(held, _pg.get_connection(DSN, ValueError))
        probe_conn = FakeConn("probe")
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: probe_conn), \
                mock.patch.object(teamdb, "_read_schema_version", return_value=(True, 5, 3)):
            result = teamdb.probe(DSN, budget_ms=5_000)
        self.assertTrue(result.reachable, "the probe must still succeed — this is not a fix that "
                                          "works by making the probe fail")
        self.assertEqual(0, held.closed,
                         "an ordinary probe closed the connection its consumers are still holding "
                         "— every live PostgresBackend in the process is now broken")
        self.assertIs(held, _pg._MANAGER[DSN])

    # --------------------------------------------------- the un-forceable interleaving
    def test_abandoning_takes_a_connection_the_worker_already_handed_over(self):
        """The race `_abandon_probe` exists for, pinned DIRECTLY because it cannot be forced
        through the thread.

        The worker's `finally` can complete between `join` returning and the caller taking the
        lock — `Thread.is_alive()` is still True during teardown — so the connection is already in
        the box while its owner is walking away. Setting `abandoned` alone would then leave BOTH
        sides believing the other owns it, and the socket leaks.

        Driving the real function with a pre-populated box reproduces exactly that state, which no
        amount of sleeping around a real thread can do deterministically."""
        conn = FakeConn("handed-over")
        box = {"conn": conn, "reachable": True}
        stranded = teamdb._abandon_probe(box, threading.Lock())
        self.assertIs(conn, stranded,
                      "the caller gave up while the connection was already in the box, and "
                      "`_abandon_probe` did not hand it back — nobody closes it")
        self.assertTrue(box["abandoned"])
        self.assertNotIn("conn", box, "the connection was handed back AND left in the box — two "
                                      "owners, which is how it gets closed twice or not at all")

    def test_abandoning_before_the_worker_connected_takes_nothing(self):
        """The ordinary case: the worker has not reached its finally, so there is nothing to close
        and the flag is what does the work."""
        box = {}
        self.assertIsNone(teamdb._abandon_probe(box, threading.Lock()))
        self.assertTrue(box["abandoned"])

    def test_adopt_is_idempotent_for_the_same_connection(self):
        conn = FakeConn("same")
        _pg.adopt(DSN, conn)
        _pg.adopt(DSN, conn)
        self.assertIs(conn, _pg._MANAGER[DSN])
        self.assertEqual(0, conn.closed, "adopting the SAME connection twice closed it")


if __name__ == "__main__":
    unittest.main()
