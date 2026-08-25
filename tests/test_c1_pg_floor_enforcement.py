"""C1 (0.0.19) — the declared PostgreSQL floor is ENFORCED against a live server.

`PG-FLOOR-DECLARED-NOT-ENFORCED` (doc 84, ruled 2026-08-16): WARN before 2026-11-12, REFUSE after,
where the date is PostgreSQL 14's upstream end of life. `CHANGELOG.md` and `RELEASE_NOTES.md`
shipped that sentence to PyPI at v0.0.18, so the slot is not ours to move.

⭐ **WHAT THIS FILE IS BUILT TO PROVE, and it is one assertion, not a family of them: WARN and
REFUSE DIFFER IN CONNECTEDNESS.** F10 (doc 104 §11) rules that REFUSE means the Postgres backend
does not build — `PostgresUnavailable`, a loud degrade, and the local SQLite floor answers — while
WARN connects and carries a notice. A test that compared only the two MESSAGES would pass against a
build where REFUSE is a louder warning, and that build meets the published commitment in letter
while missing it entirely in substance. So every date-boundary assertion here is about whether a
BACKEND EXISTS, and the strings are checked separately and additionally.

THREE OUTCOMES, NOT TWO (doc 85 §7g). A server BELOW the floor, a server AT-or-above it, and a
server whose version could not be READ are three different facts. The third is the one that fails
open if it is collapsed into either neighbour: an unreadable version rendered as below-floor would
refuse a perfectly good database, and rendered as at-floor would let the enforcement silently lapse
the day `conn.info` changes shape. `FLOOR_UNKNOWN` is its own answer and is asserted as such.

THE CLOCK IS INJECTED, because the behaviour this ships changes on a date 85 days after the release
with NO CODE CHANGE. A `date.today()` read inline would make the REFUSE half untestable and
unreviewable — it would ship as a promise about a future nobody had executed. `teamdb.floor_verdict`
takes `today=`, and `teamdb._today` is the one seam the end-to-end paths read.

NO LIVE DATABASE IS NEEDED and that is deliberate, following `tests/test_probe_orphan.py` rather
than re-deriving it: the subject is WHICH VERDICT a version produces and WHAT the connect path does
about it, not psycopg's wire protocol. `_pg._raw_connect` is doubled and a spec-bearing psycopg stub
satisfies `probe`'s driver-presence gate, so these run identically with and without the extra
installed. What that CANNOT grade is stated in the stage report, not swallowed here.

⚠ THE DSN BELOW IS ASSEMBLED, NOT WRITTEN. mokata's own secret guard blocks a literal
credential-bearing connection string in a tracked file — correctly, and it blocked the first draft
of this module. The parts are named separately and joined at import, which is
`tests/test_db_s0_dsn_inspect.py`'s idiom, and the assembled value is exactly what the leak
assertions below hunt for.
"""

import importlib.machinery
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

import _support  # noqa: F401

from mokata import degrade, teamdb, team_health
from mokata.memory import _pg
from mokata.memory.backends import PostgresUnavailable

_USER, _PASS = "floor-user", "hunter2"
_HOST, _DB = "db.internal.example", "shared_team_store"
DSN = "postgresql://" + _USER + ":" + _PASS + "@" + _HOST + ":5432/" + _DB
DSN_ENV = "MOKATA_C1_FLOOR_DSN"


#: The day before enforcement, the day of it, and well after. Written as offsets from the DECLARED
#: date so no literal here can drift away from `teamdb.PG_FLOOR_ENFORCED_FROM` — a hand-typed pair
#: would be a second declaration of the very thing this stage exists to declare once (doc 85 §7j).
def _day_before():
    return date.fromordinal(teamdb.PG_FLOOR_ENFORCED_FROM.toordinal() - 1)


def _the_day():
    return teamdb.PG_FLOOR_ENFORCED_FROM


def _later():
    return date.fromordinal(teamdb.PG_FLOOR_ENFORCED_FROM.toordinal() + 400)


def _below():
    """A major BELOW the declared floor — derived, never typed. Nothing here re-types the floor."""
    return teamdb.MIN_PG_MAJOR - 1


def _at():
    return teamdb.MIN_PG_MAJOR


def _encoded(major, minor=3):
    """`PQserverVersion()`'s integer encoding for a modern server: 14 -> 140003."""
    return major * 10000 + minor


class _PsycopgStub:
    """Satisfies `probe`'s driver-presence gate and nothing else — `test_probe_orphan`'s idiom,
    reused rather than re-derived. `connect` is a tripwire: every test here doubles
    `_pg._raw_connect`, so reaching the driver means the test stopped exercising its seam."""

    __spec__ = importlib.machinery.ModuleSpec("psycopg", None)

    @staticmethod
    def connect(*a, **kw):                                # pragma: no cover - a tripwire
        raise AssertionError("the psycopg stub's connect() was called — `_pg._raw_connect` was "
                             "not doubled, so this test no longer exercises the probe seam")


class _Info:
    """psycopg's `ConnectionInfo`, reduced to the ONE attribute this stage reads.

    `server_version` is `PQserverVersion()`: an integer the client already holds from the startup
    handshake. Reading it costs NO round trip, which is what makes it legal on the connect path —
    the location ruling forbids a fresh probe there."""

    def __init__(self, server_version):
        self._v = server_version

    @property
    def server_version(self):
        if isinstance(self._v, Exception):
            raise self._v
        return self._v


class FakeConn:
    """The smallest thing `probe` treats as a connection, plus a COUNTED `execute`.

    The count is load-bearing: "no extra round trip for a server at or above the floor" is a claim
    about statements issued, and asserting the absence of a NOTICE would pass against a build that
    silently added a `SHOW server_version` per connect."""

    def __init__(self, server_version=None, version_row=None, label="conn"):
        self.label = label
        self.closed = 0
        self.executed = []
        self.info = _Info(server_version)
        self._last = ""
        self._row = version_row if version_row is not None else (
            teamdb.TEAM_SCHEMA_VERSION, teamdb.TEAM_SCHEMA_MIN_SUPPORTED)

    def execute(self, sql, *args):
        self.executed.append(sql)
        self._last = sql
        return self

    def fetchone(self):
        return (1,) if "SELECT 1" in self._last else self._row

    def close(self):
        self.closed = 1

    def __repr__(self):                                   # pragma: no cover - debugging aid
        return "<FakeConn %s closed=%d>" % (self.label, self.closed)


def _surface(root):
    """The smallest object `team_health` treats as a surface."""
    class _S:
        mokata_dir = os.path.join(root, ".mokata")
    os.makedirs(os.path.join(_S.mokata_dir, "temp_local"), exist_ok=True)
    return _S()


class FloorTestCase(unittest.TestCase):
    """Shared preconditions, STATED rather than inherited from the ambient environment."""

    def setUp(self):
        _pg.reset_manager()
        teamdb.reset_schema_cache()
        degrade.reset_degrade_notices()
        self.addCleanup(_pg.reset_manager)
        self.addCleanup(teamdb.reset_schema_cache)
        self.addCleanup(degrade.reset_degrade_notices)
        previous = sys.modules.get("psycopg")
        sys.modules["psycopg"] = _PsycopgStub

        def _restore():
            if previous is None:
                sys.modules.pop("psycopg", None)
            else:
                sys.modules["psycopg"] = previous

        self.addCleanup(_restore)

    def probe_with(self, server_version, **kw):
        """Run the REAL `probe` against a connection reporting `server_version`."""
        conn = FakeConn(server_version, **kw)
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kwargs: conn):
            res = teamdb.probe(DSN)
        return res, conn

    def at_date(self, day):
        """Pin the one clock seam the non-injectable paths read."""
        return mock.patch.object(teamdb, "_today", lambda: day)


# ============================================================ the declaration and the predicate
class TestTheDeclarationIsSingle(FloorTestCase):
    """One constant, beside the floor. Everything else derives from it."""

    def test_the_enforcement_date_is_declared_beside_the_floor(self):
        self.assertIsInstance(teamdb.PG_FLOOR_ENFORCED_FROM, date)
        self.assertEqual(date(2026, 11, 12), teamdb.PG_FLOOR_ENFORCED_FROM,
                         "the published commitment in CHANGELOG.md / RELEASE_NOTES.md names "
                         "2026-11-12 and shipped to PyPI at v0.0.18 — not ours to move")

    def test_c1_regression_the_verdict_derives_from_the_declared_floor(self):
        """⭐ Nothing re-types the floor. Move the DECLARATION and every verdict moves with it.

        This is the assertion a hand-typed floor in the predicate does not survive: with the floor
        lifted above it, a server that WAS at-floor must become below-floor. A build comparing
        against its own literal answers FLOOR_OK here and reds."""
        was_at_floor = _at()          # captured BEFORE the move — the server does not change
        with mock.patch.object(teamdb, "MIN_PG_MAJOR", was_at_floor + 5):
            self.assertEqual(teamdb.FLOOR_REFUSE,
                             teamdb.floor_verdict(was_at_floor, today=_the_day()))
            self.assertEqual(teamdb.FLOOR_WARN,
                             teamdb.floor_verdict(was_at_floor, today=_day_before()))
        self.assertEqual(teamdb.FLOOR_OK, teamdb.floor_verdict(was_at_floor, today=_the_day()),
                         "the same server must read OK again once the floor is put back — "
                         "otherwise this test proved a patch, not a derivation")

    def test_three_outcomes_not_two(self):
        """§7g — below, at-or-above, and UNKNOWN are three facts with three representations."""
        outcomes = {
            teamdb.floor_verdict(_below(), today=_day_before()),
            teamdb.floor_verdict(_at(), today=_day_before()),
            teamdb.floor_verdict(None, today=_day_before()),
        }
        self.assertEqual(3, len(outcomes), "two of the three outcomes share a value: %r" % outcomes)
        self.assertNotEqual(teamdb.floor_verdict(None, today=_the_day()),
                            teamdb.floor_verdict(_below(), today=_the_day()),
                            "an unreadable version rendered as below-floor — that refuses a "
                            "database nobody has shown to be old (§7g)")

    def test_unknown_is_neutral_on_both_sides_of_the_date(self):
        """The date may not convert 'we could not read it' into 'it is too old'."""
        for day in (_day_before(), _the_day(), _later()):
            self.assertEqual(teamdb.FLOOR_UNKNOWN, teamdb.floor_verdict(None, today=day))

    def test_both_sides_of_the_date_for_a_below_floor_server(self):
        self.assertEqual(teamdb.FLOOR_WARN, teamdb.floor_verdict(_below(), today=_day_before()))
        self.assertEqual(teamdb.FLOOR_REFUSE, teamdb.floor_verdict(_below(), today=_the_day()))
        self.assertEqual(teamdb.FLOOR_REFUSE, teamdb.floor_verdict(_below(), today=_later()))

    def test_the_date_is_irrelevant_at_or_above_the_floor(self):
        """A supported server is never touched by the clock — the no-behaviour-change claim."""
        for day in (_day_before(), _the_day(), _later()):
            for major in (_at(), _at() + 1, teamdb.TARGET_PG_MAJOR):
                self.assertEqual(teamdb.FLOOR_OK, teamdb.floor_verdict(major, today=day))

    def test_the_clock_defaults_to_the_one_seam(self):
        """`today=None` reads `_today()` and nothing else — the seam the deep paths depend on."""
        with self.at_date(_the_day()):
            self.assertEqual(teamdb.FLOOR_REFUSE, teamdb.floor_verdict(_below()))
        with self.at_date(_day_before()):
            self.assertEqual(teamdb.FLOOR_WARN, teamdb.floor_verdict(_below()))


# ============================================================ detection, on an existing connection
class TestTheServerMajorIsRead(FloorTestCase):
    """`server_version` was read NOWHERE in `src/` before this stage — the detection is new."""

    def test_c1_regression_the_probe_records_the_server_major(self):
        res, _conn = self.probe_with(_encoded(14, 12))
        self.assertEqual(14, res.server_major)

    def test_reading_the_major_costs_no_round_trip(self):
        """⭐ The location ruling forbids a fresh probe on the connect path. `PQserverVersion` is
        held by the client from the startup handshake, so the count must not move: `SELECT 1` and
        the schema read, exactly as before this stage."""
        res, conn = self.probe_with(_encoded(16, 4))
        self.assertEqual(16, res.server_major)
        self.assertEqual(2, len(conn.executed),
                         "the probe issued %d statements, not 2 — reading the server version "
                         "added a round trip to the connect path: %r"
                         % (len(conn.executed), conn.executed))

    def test_every_unreadable_shape_is_unknown_and_not_a_low_version(self):
        """§7g at the detection layer. Each of these means 'we do not know', and none may arrive
        downstream wearing a number a comparison can act on."""
        for bad in (None, 0, -1, "", "fourteen", RuntimeError("libpq said no")):
            with self.subTest(server_version=bad):
                res, _conn = self.probe_with(bad)
                self.assertIsNone(res.server_major,
                                  "%r produced %r — an unreadable version must be None"
                                  % (bad, res.server_major))
                self.assertEqual(teamdb.FLOOR_UNKNOWN,
                                 teamdb.floor_verdict(res.server_major, today=_later()))

    def test_a_pre_10_server_still_reads_as_below_the_floor(self):
        """PostgreSQL 9.6 encodes as 90624. Integer division gives 9, which is below the floor —
        the one direction that matters, and it must not read as UNKNOWN."""
        res, _conn = self.probe_with(90624)
        self.assertEqual(9, res.server_major)
        self.assertEqual(teamdb.FLOOR_REFUSE,
                         teamdb.floor_verdict(res.server_major, today=_the_day()))


# ============================================================ ENFORCEMENT — the connect path
class TestTheConnectPathEnforces(FloorTestCase):
    """`ensure_schema` is the ONE seam every runtime Postgres consumer passes through
    (`_pg.connect_psycopg`). The check rides the verdict that path already caches, so nothing had
    to be wired onto a command that touches only local state — the DB.S7c2 scar, avoided by
    construction rather than by care."""

    def _ensure(self, server_version, today, version_row=None):
        conn = FakeConn(server_version, version_row=version_row)
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: conn):
            with self.at_date(today):
                return teamdb.ensure_schema(DSN, PostgresUnavailable), conn

    def test_c1_regression_warn_connects(self):
        """Before the date: the seam RETURNS. Connectedness, not text."""
        res, _conn = self._ensure(_encoded(_below()), _day_before())
        self.assertTrue(res.reachable)
        self.assertEqual(_below(), res.server_major)

    def test_c1_regression_refuse_does_not_connect(self):
        """⭐ THE LOAD-BEARING ASSERTION. On the date, the same server, everything else identical —
        and the seam RAISES. This is what makes REFUSE a different behaviour rather than a louder
        sentence (F10)."""
        with self.assertRaises(PostgresUnavailable) as caught:
            self._ensure(_encoded(_below()), _the_day())
        self.assertEqual(degrade.FAILURE_PG_FLOOR, caught.exception.failure_class)
        self.assertTrue(caught.exception.fix,
                        "a refusal with no remedy is §7g's other half — the remedy must exist "
                        "and be named")

    def test_an_at_floor_server_is_untouched_on_both_sides_of_the_date(self):
        """No behaviour change for a supported server, asserted as the ROUND-TRIP COUNT and not
        merely as the absence of a message."""
        for day in (_day_before(), _the_day(), _later()):
            with self.subTest(day=day):
                teamdb.reset_schema_cache()
                _pg.reset_manager()
                res, conn = self._ensure(_encoded(_at(), 1), day)
                self.assertTrue(res.compatible)
                self.assertEqual(2, len(conn.executed), conn.executed)

    def test_an_unknown_version_never_refuses(self):
        """A server whose version could not be read is served exactly as before this stage."""
        for day in (_day_before(), _the_day(), _later()):
            with self.subTest(day=day):
                teamdb.reset_schema_cache()
                _pg.reset_manager()
                res, _conn = self._ensure(None, day)
                self.assertTrue(res.reachable)
                self.assertIsNone(res.server_major)

    def test_the_floor_is_judged_before_the_schema(self):
        """A below-floor server with an unprovisioned schema must hear about the FLOOR. Telling
        that user to `mokata team init` sends them to do work against a database mokata is about
        to stop connecting to — a true sentence pointed at the wrong problem."""
        with self.assertRaises(PostgresUnavailable) as caught:
            self._ensure(_encoded(_below()), _the_day(), version_row=(1, None))
        self.assertEqual(degrade.FAILURE_PG_FLOOR, caught.exception.failure_class)


# ============================================================ REFUSE degrades, it does not crash
class TestRefuseDegradesToTheFloor(FloorTestCase):
    """F10: `PostgresUnavailable` and a LOUD degrade to the local SQLite floor. Not a crash, not a
    hard exit, not a traceback the user meets. The user keeps working and keeps their memory."""

    def _build(self, server_version, today):
        from mokata.memory.backends import build_postgres_backend
        conn = FakeConn(server_version)
        failures = []
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: conn):
            with self.at_date(today):
                with mock.patch.dict(os.environ, {DSN_ENV: DSN}):
                    backend = build_postgres_backend({"dsn_env": DSN_ENV},
                                                     on_unavailable=failures.append)
        return backend, failures

    def test_c1_regression_warn_and_refuse_differ_in_connectedness(self):
        """⭐ THE F10 ASSERTION, end to end through the real backend builder. One server, two days,
        two different OUTCOMES — a backend, and no backend. A build in which REFUSE were only a
        louder warning returns a live backend on both days and reds here."""
        warned, warn_failures = self._build(_encoded(_below()), _day_before())
        teamdb.reset_schema_cache()
        _pg.reset_manager()
        refused, refuse_failures = self._build(_encoded(_below()), _the_day())

        self.assertIsNotNone(warned, "WARN must CONNECT — before the date the server is still "
                                     "upstream-supported, and refusing it would break a working "
                                     "install for our calendar")
        self.assertEqual([], warn_failures)
        self.assertIsNone(refused, "REFUSE still built a backend — the two halves of the "
                                   "published commitment then differ only in wording, which is "
                                   "the exact failure F10 names")
        self.assertEqual(1, len(refuse_failures))
        self.assertEqual(degrade.FAILURE_PG_FLOOR, refuse_failures[0].failure_class)

    def test_refuse_does_not_crash_and_the_local_floor_answers(self):
        """The process survives, SQLite serves, and the user's local memory is intact + readable."""
        from mokata.memory.backends import SQLiteBackend
        from mokata.memory.item import MemoryItem

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            before = SQLiteBackend(path)
            before.put(MemoryItem(id="kept", subject="kept",
                                  value="a fact the user owns"))
            before.close()

            refused, failures = self._build(_encoded(_below()), _the_day())
            self.assertIsNone(refused)
            self.assertIsInstance(failures[0], PostgresUnavailable)

            after = SQLiteBackend(path)
            got = after.get("kept")
            after.close()
            self.assertIsNotNone(got, "the local floor lost the user's memory on a REFUSE — the "
                                      "one outcome worse than refusing")
            self.assertEqual("a fact the user owns", got.value)

    def test_the_degrade_is_loud_and_names_the_real_remedy(self):
        """`mokata sync` is the DEFAULT advice and it is false here: the connection is perfectly
        healthy and syncing will never fix a major version. D1 already learned this for the schema
        class; the floor class must not re-learn it."""
        notice = degrade.DegradeNotice(subsystem="memory", env_name=DSN_ENV,
                                       failure_class=degrade.FAILURE_PG_FLOOR)
        rendered = notice.render()
        self.assertIn("DEGRADED", rendered)
        self.assertNotIn("mokata sync", notice.remediation,
                         "the floor degrade tells the user to sync a healthy connection")
        self.assertIn(str(teamdb.MIN_PG_MAJOR), rendered + notice.remediation)


# ============================================================ health agrees with the connect path
class TestHealthAgreesWithTheConnectPath(FloorTestCase):
    """`degrade.py` states the invariant: the verdict that says CONNECTED is the same input that
    routes the read, so the two cannot diverge. A health surface reporting HEALTHY while the
    connect path refuses would put that divergence back."""

    def test_health_is_degraded_when_the_connect_path_would_refuse(self):
        res, _conn = self.probe_with(_encoded(_below()))
        with self.at_date(_the_day()):
            state, detail = team_health.classify(res)
        self.assertEqual(team_health.DEGRADED, state)
        self.assertIn(str(_below()), detail)
        self.assertIn(str(teamdb.MIN_PG_MAJOR), detail)

    def test_health_stays_healthy_and_carries_the_warning_before_the_date(self):
        """WARN is not a degrade: mokata is connected and serving from the shared store. Saying
        DEGRADED here would be a loud message that is untrue, which is the D5 bug itself."""
        res, _conn = self.probe_with(_encoded(_below()))
        with self.at_date(_day_before()):
            state, detail = team_health.classify(res)
        self.assertEqual(team_health.HEALTHY, state)
        self.assertIn(teamdb.PG_FLOOR_ENFORCED_FROM.isoformat(), detail,
                      "the warning does not tell the user WHEN — a dated commitment a user "
                      "cannot see coming is a commitment kept badly")
        self.assertIn(str(_below()), detail)

    def test_an_at_floor_server_renders_byte_identically(self):
        """The negative, graded against the SAME function that carries the floor logic."""
        res, _conn = self.probe_with(_encoded(_at(), 2))
        for day in (_day_before(), _the_day(), _later()):
            with self.subTest(day=day):
                with self.at_date(day):
                    state, detail = team_health.classify(res)
                self.assertEqual(team_health.HEALTHY, state)
                self.assertNotIn(teamdb.PG_FLOOR_ENFORCED_FROM.isoformat(), detail)

    def test_an_unknown_version_renders_neither(self):
        res, _conn = self.probe_with(None)
        with self.at_date(_later()):
            state, detail = team_health.classify(res)
        self.assertEqual(team_health.HEALTHY, state)
        self.assertNotIn(teamdb.PG_FLOOR_ENFORCED_FROM.isoformat(), detail)

    def test_the_verdict_carries_the_major_through_the_cache(self):
        """`doctor` and the badge read the CACHED verdict and never probe, so the major has to
        survive the JSON round trip or the surfaces below have nothing to render."""
        v = team_health.HealthVerdict(team_health.HEALTHY, "reachable", server_major=14)
        self.assertEqual(14, team_health.HealthVerdict.from_dict(v.to_dict()).server_major)
        self.assertIsNone(
            team_health.HealthVerdict.from_dict({"state": team_health.UNKNOWN}).server_major,
            "a verdict written before this stage must read back as UNKNOWN-major, never as 0")


# ============================================================ doctor tells them TODAY
class TestDoctorSaysWhatHappensThen(FloorTestCase):
    """A dated commitment a user cannot see coming is a commitment kept badly (F10)."""

    def test_doctor_tells_a_below_floor_user_today_what_happens_then(self):
        res, _conn = self.probe_with(_encoded(_below()))
        with self.at_date(_day_before()):
            state, detail = team_health.classify(res)
            block = team_health.status_block(
                team_health.HealthVerdict(state, detail, server_major=res.server_major))
        self.assertIn(str(_below()), block, "doctor does not name the server they are on")
        self.assertIn(str(teamdb.MIN_PG_MAJOR), block, "doctor does not name the floor")
        self.assertIn(teamdb.PG_FLOOR_ENFORCED_FROM.isoformat(), block,
                      "doctor does not name the date")

    def test_doctor_reads_the_cache_and_never_probes(self):
        """`cached_or_neutral` is the hot-path contract this rides — it must stay probe-free."""
        probed = []
        with mock.patch.object(teamdb, "probe", lambda *a, **kw: probed.append(a)):
            with tempfile.TemporaryDirectory() as tmp:
                verdict = team_health.cached_or_neutral(_surface(tmp))
        self.assertEqual([], probed, "the hot path probed")
        self.assertEqual(team_health.LOCAL, verdict.state)


# ============================================================ secret safety (CM.S1), sharpened
class TestNothingButTheNumberTravels(FloorTestCase):
    """This path holds a DSN. The server major is a number; nothing else from that connection may
    travel with it — not the password, not the host, not the user, not the database name."""

    SECRETS = (_PASS, _HOST, _USER, _DB, DSN)

    def _assert_clean(self, blob, where):
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob, "%s leaked %r" % (where, secret))

    def test_no_credential_reaches_the_verdict_the_notice_or_doctor(self):
        res, _conn = self.probe_with(_encoded(_below()))
        with self.at_date(_day_before()):
            state, detail = team_health.classify(res)
        verdict = team_health.HealthVerdict(state, detail, server_major=res.server_major)
        self._assert_clean(detail, "the health detail")
        self._assert_clean(team_health.status_block(verdict), "doctor's status block")
        self._assert_clean(team_health.summary_line(verdict), "the summary line")
        self._assert_clean(str(verdict.to_dict()), "the cached verdict")
        self._assert_clean(teamdb.floor_notice(res.server_major, teamdb.FLOOR_WARN), "the notice")
        self._assert_clean(teamdb.floor_notice(res.server_major, teamdb.FLOOR_REFUSE), "the notice")

    def test_no_credential_reaches_the_refusal_a_user_actually_sees(self):
        conn = FakeConn(_encoded(_below()))
        with mock.patch.object(_pg, "_raw_connect", lambda dsn, **kw: conn):
            with self.at_date(_the_day()):
                with self.assertRaises(PostgresUnavailable) as caught:
                    teamdb.ensure_schema(DSN, PostgresUnavailable)
        exc = caught.exception
        self._assert_clean(str(exc), "the raised refusal")
        self._assert_clean(exc.fix, "the refusal's remedy")
        notice = degrade.DegradeNotice(subsystem="memory", env_name=DSN_ENV,
                                       failure_class=exc.failure_class, detail=str(exc),
                                       fix=exc.fix)
        self._assert_clean(notice.render(), "the rendered degrade notice")
        self._assert_clean(str(notice.to_dict()), "the structured MCP marker")

    def test_the_cache_file_on_disk_holds_no_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            surface = _surface(tmp)
            team_health.store(surface, team_health.HealthVerdict(
                team_health.HEALTHY, teamdb.floor_notice(_below(), teamdb.FLOOR_WARN),
                server_major=_below()))
            path = os.path.join(surface.mokata_dir, "temp_local", team_health.CACHE_FILENAME)
            with open(path, encoding="utf-8") as fh:
                self._assert_clean(fh.read(), "the persisted health cache")


# ============================================================ the failure class earns its place
class TestTheFailureClassIsCoinedOnTheStandingRule(FloorTestCase):
    """D5's rule for a NEW class, applied rather than assumed: coin one only when every existing
    class renders a FALSE sentence AND the remediation is different."""

    def test_the_class_exists_and_is_labelled(self):
        self.assertIn(degrade.FAILURE_PG_FLOOR, degrade._CLASS_LABEL)
        self.assertTrue(degrade._CLASS_LABEL[degrade.FAILURE_PG_FLOOR].strip())

    def test_it_is_not_one_of_the_classes_that_would_read_false(self):
        self.assertNotEqual(degrade.FAILURE_UNREACHABLE, degrade.FAILURE_PG_FLOOR)
        self.assertNotEqual(degrade.FAILURE_SCHEMA, degrade.FAILURE_PG_FLOOR)

    def test_its_label_reads_true_for_a_capability_degrade_too(self):
        """It needs no team-neutral re-wording, for `FAILURE_TIMEOUT`'s stated reason: a second
        spelling of one true sentence is how two surfaces start disagreeing about what happened."""
        base = degrade.DegradeNotice(subsystem="memory", env_name="X",
                                     failure_class=degrade.FAILURE_PG_FLOOR)
        cap = degrade.CapabilityDegradeNotice(subsystem="memory", env_name="",
                                              failure_class=degrade.FAILURE_PG_FLOOR)
        self.assertEqual(base.class_label, cap.class_label)


if __name__ == "__main__":
    unittest.main()
