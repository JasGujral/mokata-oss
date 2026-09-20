"""THE BELOW-FLOOR ARM, AGAINST A REAL SERVER THAT IS ACTUALLY BELOW THE FLOOR.
(0.0.20 stage 08a — lane C, the first slice.)

`PG-FLOOR-LIVE-DIMENSION-UNGRADED` (doc 84), and the exact sentence the release notes carry:

    "a genuinely below-floor server — a real PostgreSQL 14 — was never used. The below-floor arm
     is therefore proven against a simulated version report and not against the database it
     describes."

⭐ **WHAT THIS CLOSES, AND WHAT IT DOES NOT.** It closes the narrow disclosure above: the REFUSE
arm, driven end to end from `PQserverVersion()` on a real PostgreSQL 14 rather than from a number a
stub handed back. It does **not** close the 121-test skip-vs-pass harness that is the rest of
`PG-FLOOR-LIVE-DIMENSION-UNGRADED` — that is booked as its own stage (see
`docs/build/handoff/08-pg-floor-shape-guard.report.md` §4), because 220 env-var-keyed call sites
across 14 files do not move with a workflow edit.

🔴 THIS FILE MAY NOT BE READ AS EVIDENCE UNTIL IT HAS RUN
----------------------------------------------------------
Neither coordinator shell can run GitHub Actions, so this was written and never executed. ⛔ **The
disclosure in the release notes STAYS until a real runner is seen going green here** — retiring it
on the presence of a test rather than on the test's result is `DISCLOSURE-PRESENCE-IS-NOT-
DISCLOSURE-TRUTH`, which is a row this project already carries.

WHY A SEPARATE SERVICE AND NOT THE EXISTING ONE
------------------------------------------------
`live-db-legs.yml`'s service is `pgvector/pgvector:pg16` — above the floor, on purpose, because
every other leg needs a modern server and pgvector. A below-floor arm needs a server that is
genuinely old, so it gets its own job and its own container. ⚠ It needs **no** extension and no
schema: the whole subject is the version handshake.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import importlib.util
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import teamdb

#: The DSN of a server that is DELIBERATELY BELOW the floor. A distinct variable from
#: `MOKATA_PG_DSN`, and not a reuse: pointing this arm at the modern service would make it pass by
#: proving nothing, which is the failure mode the whole live-leg family keeps guarding against.
BELOW_DSN_VAR = "MOKATA_PG_BELOW_DSN"


def _have_psycopg() -> bool:
    return importlib.util.find_spec("psycopg") is not None


def below_floor_live():
    """`(on, reason)` — the gate, exported so a workflow preflight can import THE SAME predicate
    rather than re-deriving an equivalent one. Two implementations of "is the below-floor server
    up" is how a preflight passes while the test it guards skips anyway."""
    if os.environ.get("MOKATA_LIVE_DB") != "1":
        return False, "MOKATA_LIVE_DB is not 1"
    if not os.environ.get(BELOW_DSN_VAR):
        return False, "%s is unset — there is no below-floor server to talk to" % BELOW_DSN_VAR
    if not _have_psycopg():
        return False, "psycopg is not installed (the `postgres` extra)"
    return True, ""


_ON, _REASON = below_floor_live()
_SKIP = ("the below-floor live arm is OFF (%s). ⛔ This is an UN-RUN check, not a passing one "
         "(doc 85 §7g): the release notes' disclosure stands until a runner is seen going green "
         "here." % _REASON)


@unittest.skipUnless(_ON, _SKIP)
class TheServerIsGenuinelyBelowTheFloor(unittest.TestCase):
    """The precondition, asserted rather than assumed. A job that pointed this at a modern server
    by accident would pass every assertion below it and prove the opposite of what it claims."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(os.environ[BELOW_DSN_VAR])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        except Exception:                          # noqa: BLE001 — teardown must not mask a result
            pass

    def test_the_major_is_read_through_MOKATAS_OWN_reader(self):
        """⭐ Not `SHOW server_version`. The whole row is that mokata's read is the thing that can
        lapse, so the number under test has to come out of `teamdb._server_major` — the same
        function the connect path calls — and not out of a query written for this test."""
        major = teamdb._server_major(self.conn)
        self.assertIsNotNone(
            major,
            "mokata could not read a major from a REAL, REACHABLE PostgreSQL connection. That is "
            "`SERVER-VERSION-SHAPE-CHANGE-LAPSES-THE-FLOOR` happening: every server now reads "
            "FLOOR_UNKNOWN and the dated commitment is not being enforced.")
        self.assertLess(
            major, teamdb.MIN_PG_MAJOR,
            "this arm is pointed at a server that is NOT below the floor (major %s >= %s), so it "
            "would prove the opposite of what it claims" % (major, teamdb.MIN_PG_MAJOR))

    def test_and_it_agrees_with_the_server_s_own_answer(self):
        """The independent probe. `_server_major` reading a plausible-but-wrong number would
        satisfy the assertion above; the server is asked directly and the two must agree."""
        reported = self.conn.execute("SHOW server_version").fetchone()[0]
        self.assertEqual(int(str(reported).split(".")[0]), teamdb._server_major(self.conn),
                         "libpq's PQserverVersion and the server's own SHOW disagree: %r"
                         % (reported,))


@unittest.skipUnless(_ON, _SKIP)
class TheFloorREFUSESIt(unittest.TestCase):
    """🔴 THE ARM THE DISCLOSURE IS ABOUT, driven from a real handshake end to end."""

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(os.environ[BELOW_DSN_VAR])
        self.addCleanup(self.conn.close)
        self.major = teamdb._server_major(self.conn)

    def test_on_or_after_the_enforcement_date_the_verdict_is_REFUSE(self):
        verdict = teamdb.floor_verdict(self.major, today=teamdb.PG_FLOOR_ENFORCED_FROM)
        self.assertEqual(teamdb.FLOOR_REFUSE, verdict,
                         "a REAL below-floor server on the enforcement date did not produce "
                         "FLOOR_REFUSE — the published commitment is not met")

    def test_before_it_the_verdict_is_WARN_and_not_refuse(self):
        """The other side of the dated promise. A floor that refused early would break working
        setups ahead of a date this project published and does not own."""
        import datetime as _dt
        day_before = teamdb.PG_FLOOR_ENFORCED_FROM - _dt.timedelta(days=1)
        self.assertEqual(teamdb.FLOOR_WARN,
                         teamdb.floor_verdict(self.major, today=day_before))

    def test_the_notice_names_the_real_major_and_carries_nothing_else(self):
        """CM.S1 secret-safety on the one path that holds a DSN: the server major is a fact about
        the software; the host, user, database and password are the user's."""
        notice = teamdb.floor_notice(self.major, teamdb.FLOOR_REFUSE)
        self.assertIn(str(self.major), notice,
                      "the refusal does not name the version it is refusing")
        dsn = os.environ[BELOW_DSN_VAR]
        self.assertNotIn(dsn, notice, "the whole DSN reached a user-facing notice")
        # ⚠ And the PARTS, not only the whole: a notice that interpolated the host or the user
        # would not contain the DSN verbatim and would still have leaked. Derived from the DSN in
        # hand rather than from a list of field names, so a DSN shape this test has not seen is
        # covered too.
        for part in [p for p in dsn.replace("://", "/").replace("@", "/").split("/") if len(p) > 2]:
            self.assertNotIn(part, notice, "a DSN component (%r) reached a user-facing notice"
                             % part)
