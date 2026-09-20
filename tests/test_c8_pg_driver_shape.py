"""THE FLOOR'S ONE INPUT IS A THIRD-PARTY PROPERTY, AND THIS IS WHAT REDS WHEN IT MOVES.
(0.0.20 stage 08 — lane C, part 1.)

`SERVER-VERSION-SHAPE-CHANGE-LAPSES-THE-FLOOR` (doc 84). mokata's PostgreSQL floor is a
**published, dated commitment** — 2026-11-12, PostgreSQL 14's upstream end-of-life, which is not
ours to move — and the entire enforcement hangs on ONE read: `conn.info.server_version`
(`teamdb.py:167`), libpq's `PQserverVersion()` reached through a psycopg property whose getter is
third-party code.

The guard around that read is correct and degrade-clean: anything unreadable answers `None`, and
`None` is `FLOOR_UNKNOWN`, which is neutral — because §7g says *unknown* must never arrive
downstream wearing a value a comparison can act on.

⛔ **AND NEUTRAL IS EXACTLY WHAT A LAPSE LOOKS LIKE.** If psycopg renames the attribute, moves it,
or changes its type, every server silently reads `unknown`, `floor_verdict` never returns
`FLOOR_REFUSE`, and **a dated public commitment stops being met with every test in this repo still
green.** The degrade is doing its job and the promise is broken anyway.

⭐ **WHY THE EXISTING TESTS CANNOT SEE THIS, WHICH IS THE POINT OF A SEPARATE FILE.**
`test_c1_pg_floor_enforcement` drives a **psycopg stub** — deliberately, because the floor's logic
is mokata's and not psycopg's wire protocol. A stub has whatever shape the test gives it, so it has
the right shape **forever**, by construction. Every arm of the floor can be green against a library
that no longer exists in that form. **This file is the one place the REAL library is read.**

⚠ AND IT DOES NOT NEED A SERVER. The row's fix direction ends *"which needs the live leg the row
below says does not exist"* — true of the OTHER half, the one that proves a real server still
yields a parseable major. **The half that catches a rename, a move or a type change needs only the
installed package**, and that half is here, so it is not waiting on infrastructure.

🔴 WHAT A SKIP MEANS HERE
--------------------------
`postgres` is an OPTIONAL extra, so psycopg is absent from the ordinary unit run and every class
below skips. **A skipped run of this file has proven NOTHING** (§7g) — it is not a pass, and
nothing may read it as one. `live-db-legs.yml` installs `.[postgres]` and its preflight imports
`real_psycopg()` from this module, so the leg fails rather than reporting success for a check that
never ran. ⛔ Two implementations of "is the driver real" is how a preflight ends up passing while
the tests it guards skip anyway; there is one, and it lives here with the tests.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import teamdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEAMDB = os.path.join(ROOT, "src", "mokata", "teamdb.py")

#: The function whose ONE read this file exists to protect. Named, not inlined, because the AST
#: assertions below have to agree with the runtime ones about which function they mean.
READER = "_server_major"


def real_psycopg():
    """`(present, reason)` — is the REAL psycopg importable here?

    ⚠ Also refuses a STUB. `test_c1_pg_floor_enforcement` installs a fake `psycopg` into
    `sys.modules` for the duration of a test, and a predicate that merely imported the name would
    answer True for it — which would make this file's whole subject (the real library's shape)
    graded against a fake that has the right shape by construction. Presence is decided by the
    module having a real file on disk."""
    try:
        import psycopg
    except Exception as exc:                       # noqa: BLE001 — any import failure is "absent"
        return False, "psycopg is not installed (%s)" % exc.__class__.__name__
    if not getattr(psycopg, "__file__", None):
        return False, "the `psycopg` in sys.modules is a stub, not the installed package"
    return True, ""


_REAL, _REASON = real_psycopg()
_SKIP = ("the postgres extra is not installed here, so the REAL driver's shape cannot be read. "
         "⛔ This is an UN-RUN check, not a passing one (doc 85 §7g) — `live-db-legs.yml` runs it "
         "with `.[postgres]` and fails if it skips there.")


class TheAttributePathMokataReadsIsDerivedFromTheCode(unittest.TestCase):
    """Runs everywhere, including without psycopg: it reads mokata's source, not the driver.

    ⭐ This is what keeps the guard and the guarded from drifting apart. A file that hand-typed
    `"conn.info.server_version"` and checked psycopg for it would go on passing after `teamdb`
    started reading something else — the guard would be grading a string nobody uses."""

    def _reader(self):
        with open(TEAMDB, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == READER:
                return node
        return None

    def test_the_reader_still_exists(self):
        self.assertIsNotNone(self._reader(),
                             "`teamdb.%s` is gone; this whole guard is addressed to it" % READER)

    def test_it_reads_exactly_one_attribute_chain_off_the_connection(self):
        """The floor's input, derived. If the read moves, this names the new chain rather than
        continuing to certify the old one."""
        chains = []
        for node in ast.walk(self._reader()):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) \
                    and isinstance(node.value.value, ast.Name):
                chains.append("%s.%s.%s" % (node.value.value.id, node.value.attr, node.attr))
        self.assertEqual(["conn.info.server_version"], sorted(set(chains)),
                         "the floor's single input changed shape in mokata's own code: %s"
                         % sorted(set(chains)))

    def test_the_chain_is_read_inside_a_try(self):
        """The degrade that makes UNKNOWN reachable at all. Without it a driver change is a crash
        on the connect path rather than a neutral verdict — a different defect, not a better one."""
        self.assertTrue(any(isinstance(n, ast.Try) for n in ast.walk(self._reader())),
                        "`%s` no longer guards the third-party read" % READER)


@unittest.skipUnless(_REAL, _SKIP)
class TheRealDriverStillOffersThatPath(unittest.TestCase):
    """🔴 THE ROW. Read against the INSTALLED psycopg — the one thing a stub can never tell you."""

    def test_ConnectionInfo_still_exposes_server_version(self):
        import psycopg
        info = getattr(psycopg, "ConnectionInfo", None)
        self.assertIsNotNone(info, "psycopg no longer exports `ConnectionInfo`")
        self.assertTrue(hasattr(info, "server_version"),
                        "psycopg's ConnectionInfo no longer offers `server_version` — mokata's "
                        "PostgreSQL floor now reads UNKNOWN on every server, and a DATED PUBLIC "
                        "COMMITMENT has stopped being enforced with every other test still green")

    def test_it_is_a_CLASS_level_descriptor_and_not_an_instance_afterthought(self):
        """`conn.info.server_version` has to resolve on the type. A value assigned in `__init__`
        would satisfy `hasattr` on an instance and vanish from the class, and mokata never
        constructs a `ConnectionInfo` itself."""
        import psycopg
        descriptor = None
        for klass in psycopg.ConnectionInfo.__mro__:
            if "server_version" in klass.__dict__:
                descriptor = klass.__dict__["server_version"]
                break
        self.assertIsNotNone(descriptor,
                            "`server_version` is not defined on ConnectionInfo or any base")
        self.assertTrue(hasattr(descriptor, "__get__"),
                        "`server_version` is no longer a descriptor (%s) — the attribute chain "
                        "mokata reads may still exist and no longer be a computed version"
                        % type(descriptor).__name__)

    def test_the_connection_type_still_reaches_it_through_info(self):
        import psycopg
        self.assertTrue(hasattr(psycopg.Connection, "info"),
                        "psycopg's Connection no longer exposes `.info`, so the chain "
                        "`conn.info.server_version` cannot resolve at all")


class TheDegradeIsExercisedRatherThanAssumed(unittest.TestCase):
    """§7f — the assertions above would all pass against a `_server_major` that had stopped
    working. These drive it."""

    class _Info:
        def __init__(self, value):
            self._value = value

        @property
        def server_version(self):
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _Conn:
        def __init__(self, value):
            self.info = TheDegradeIsExercisedRatherThanAssumed._Info(value)

    def test_a_real_encoding_yields_the_major(self):
        self.assertEqual(16, teamdb._server_major(self._Conn(160_004)))
        self.assertEqual(9, teamdb._server_major(self._Conn(9_0624)),
                         "the 9.x encoding must answer 9 — below the floor, which is the only "
                         "direction that matters")

    def test_every_unreadable_shape_answers_None_and_not_a_number(self):
        """The four ways the third-party read can go wrong, and they mean ONE thing."""
        for value in (RuntimeError("driver changed"), AttributeError("gone"), "16.4", 0):
            self.assertIsNone(teamdb._server_major(self._Conn(value)), repr(value))

    def test_and_None_is_UNKNOWN_which_is_never_below_the_floor(self):
        self.assertEqual(teamdb.FLOOR_UNKNOWN, teamdb.floor_verdict(None))
        self.assertNotEqual(teamdb.FLOOR_REFUSE, teamdb.floor_verdict(None))
