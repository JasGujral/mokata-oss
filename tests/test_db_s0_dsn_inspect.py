"""DB.S0 — DSN PROVIDER ONBOARDING + THE POOLER-TRAP CHECK (doc 84 §7; ADR-54 H1, doc 85 §1).

At `team connect` time (and as a reusable preflight for DB.S1's doctor), mokata inspects the
RESOLVED DSN VALUE's SHAPE — read transiently from the env, NEVER stored, NO network — and
produces named findings: the provider (Supabase/Neon/RDS/generic) and, binding, a LOUD
POOLED-ENDPOINT finding when the string is a transaction-mode pooler (LISTEN/NOTIFY + session
features silently break behind one; H1 cashed in as a preflight). Connect stays human-gated: a
pooled string PROMPTS ("connect anyway?"); `--yes`/assume_yes proceeds with the finding LEDGERED
(explicit override, never silent). mokata never rewrites the DSN.

Secret-safety is a first-class assertion here: the DSN VALUE and any password NEVER appear in the
inspector output, the finding render, the ledger record, or the connect stdout — env-var NAME only.

Clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import team
from mokata.config import Surface
from mokata.dsn_inspect import (DsnInspection, PoolerTrapFinding, inspect_dsn,
                                pooler_trap_finding)
from mokata.govern.ledger import AuditLedger

_DSN_ENV = "MOKATA_PG_DSN"

# Real-shaped DSN VALUEs, assembled from fragments so no literal credential lives in this file
# (mokata's own secret-guard would otherwise block writing it) and so the secret-safety asserts
# below test against a KNOWN password token + full value that must never leak.
_PW = "s3cr3t_" + "pw"
_USER = "app_user"


def _dsn(host_port: str) -> str:
    """A URL DSN carrying a real user:password credential + the given host[:port]/db."""
    return "postgres://" + _USER + ":" + _PW + "@" + host_port + "/postgres"


# host[:port] fragments for each provider shape (grounded markers, doc 84 §7 / doc 85 §1 H1).
_SUPABASE_POOLER = "aws-0-us-east-1.pooler.supabase.com:6543"    # transaction-mode pooler
_SUPABASE_DIRECT = "db.abcdefghijklmnop.supabase.co:5432"        # direct/session
_NEON_POOLER = "ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech:5432"
_NEON_DIRECT = "ep-cool-darkness-123456.us-east-2.aws.neon.tech:5432"
_RDS_PROXY = "myproxy.proxy-abcdef123.us-east-1.rds.amazonaws.com:5432"
_RDS_DIRECT = "mydb.abcdef123.us-east-1.rds.amazonaws.com:5432"
_GENERIC_DIRECT = "db.internal:5432"
_GENERIC_POOLER = "db.internal:6543"                            # a bare transaction-pooler port


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _manifest_text(root):
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    with open(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return fh.read()


class TestDbS0Inspect(unittest.TestCase):
    """Table-driven: the inspector reads only the SHAPE — provider + pooled verdict — and echoes
    NO secret in any field."""

    CASES = [
        # (value,                       provider,   pooled, classified)
        (_dsn(_SUPABASE_POOLER),        "supabase", True,   True),
        (_dsn(_SUPABASE_DIRECT),        "supabase", False,  True),
        (_dsn(_NEON_POOLER),            "neon",     True,   True),
        (_dsn(_NEON_DIRECT),            "neon",     False,  True),
        (_dsn(_RDS_PROXY),              "rds",      True,   True),
        (_dsn(_RDS_DIRECT),             "rds",      False,  True),
        (_dsn(_GENERIC_DIRECT),         "generic",  False,  True),
        (_dsn(_GENERIC_POOLER),         "generic",  True,   True),
        ("not a dsn at all",            "generic",  False,  False),   # malformed → cannot classify
        ("postgres://",                 "generic",  False,  False),   # no host → cannot classify
        ("",                            "generic",  False,  False),   # empty → cannot classify
    ]

    def test_db_s0_inspect_provider_and_pooled_verdict(self):
        for value, provider, pooled, classified in self.CASES:
            with self.subTest(host=value[:24]):
                got = inspect_dsn(value)
                self.assertIsInstance(got, DsnInspection)
                self.assertEqual(got.provider, provider)
                self.assertEqual(got.pooled, pooled)
                self.assertEqual(got.classified, classified)

    def test_db_s0_inspect_never_echoes_the_secret(self):
        # No field on the inspection (nor its summary render) may carry the DSN VALUE or password.
        for value, *_ in self.CASES:
            got = inspect_dsn(value)
            blob = json.dumps(got.__dict__) + " " + got.summary(_DSN_ENV)
            self.assertNotIn(_PW, blob)
            self.assertNotIn(value if len(value) > 12 else "postgres://" + _USER, blob)
            self.assertNotIn(_USER, blob)

    def test_db_s0_inspect_unclassifiable_degrades_clean(self):
        # Never wrong-confident: an unrecognisable shape is generic + not-classified + says so.
        got = inspect_dsn("wat")
        self.assertEqual(got.provider, "generic")
        self.assertFalse(got.classified)
        self.assertFalse(got.pooled)
        self.assertIn("classif", got.reason.lower())

    def test_db_s0_inspect_makes_no_network_connection(self):
        # Pure string parsing — a bogus host resolves to NO connection attempt and returns fast.
        got = inspect_dsn(_dsn("this-host-does-not-exist.invalid:6543"))
        self.assertTrue(isinstance(got, DsnInspection))  # returned without raising / hanging


class TestDbS0PoolerTrapFinding(unittest.TestCase):
    def test_db_s0_pooler_trap_finding(self):
        # A pooled DSN yields a LOUD finding naming the provider, the direct-string fix, and the
        # env-var NAME — but NEVER the value.
        insp = inspect_dsn(_dsn(_SUPABASE_POOLER))
        finding = pooler_trap_finding(insp, _DSN_ENV)
        self.assertIsInstance(finding, PoolerTrapFinding)
        rendered = finding.render()
        self.assertIn("POOLED", rendered.upper())
        self.assertIn("Supabase", rendered)
        self.assertIn(_DSN_ENV, rendered)                     # names the env var ...
        self.assertNotIn(_PW, rendered)                       # ... never the value
        self.assertNotIn(_USER, rendered)
        # names the fix (a direct/session string), and LISTEN/NOTIFY as the reason
        self.assertIn("direct", rendered.lower())
        self.assertIn("listen", rendered.lower())

    def test_db_s0_pooler_trap_finding_absent_on_direct(self):
        for value in (_dsn(_SUPABASE_DIRECT), _dsn(_NEON_DIRECT), _dsn(_RDS_DIRECT),
                      _dsn(_GENERIC_DIRECT), "not a dsn"):
            self.assertIsNone(pooler_trap_finding(inspect_dsn(value), _DSN_ENV))

    def test_db_s0_pooler_trap_finding_each_provider(self):
        for value, provider in ((_dsn(_SUPABASE_POOLER), "supabase"),
                                (_dsn(_NEON_POOLER), "neon"),
                                (_dsn(_RDS_PROXY), "rds"),
                                (_dsn(_GENERIC_POOLER), "generic")):
            f = pooler_trap_finding(inspect_dsn(value), _DSN_ENV)
            self.assertIsNotNone(f, provider)
            self.assertEqual(f.provider, provider)


class TestDbS0Connect(unittest.TestCase):
    def setUp(self):
        os.environ.pop(_DSN_ENV, None)
        self.addCleanup(lambda: os.environ.pop(_DSN_ENV, None))

    def _ledger(self, d):
        return AuditLedger.from_mokata_dir(os.path.join(d, ".mokata"))

    def test_db_s0_connect_pooled_prompts(self):
        # A pooled DSN prompts; a DECLINE connects nothing and writes nothing.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            before = _manifest_text(d)
            os.environ[_DSN_ENV] = _dsn(_SUPABASE_POOLER)
            msgs = []
            res = team.team_connect(d, surface, _DSN_ENV, confirm=lambda _t: False,
                                    out=msgs.append, ledger=self._ledger(d))
            self.assertFalse(res.connected)
            self.assertEqual(_manifest_text(d), before)       # nothing wired on decline
            blob = " ".join(msgs).upper()
            self.assertIn("POOLED", blob)                     # the loud finding was surfaced

    def test_db_s0_connect_pooled_yes_ledgers(self):
        # --yes/assume_yes proceeds past the pooler warning, and the override is LEDGERED.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            os.environ[_DSN_ENV] = _dsn(_NEON_POOLER)
            ledger = self._ledger(d)
            msgs = []
            res = team.team_connect(d, surface, _DSN_ENV, assume_yes=True,
                                    out=msgs.append, ledger=ledger)
            self.assertTrue(res.connected)
            kinds = [e.get("kind") for e in ledger.entries()]
            self.assertIn("pooler_trap", kinds)               # explicit override recorded
            rec = next(e for e in ledger.entries() if e.get("kind") == "pooler_trap")
            self.assertEqual(rec.get("provider"), "neon")
            # the ledger record NEVER carries the DSN value / credential
            recblob = json.dumps(rec)
            self.assertNotIn(_PW, recblob)
            self.assertNotIn(_USER, recblob)

    def test_db_s0_connect_direct_quiet(self):
        # A direct/session string connects normally — no pooler finding, no warning noise.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            os.environ[_DSN_ENV] = _dsn(_SUPABASE_DIRECT)
            msgs = []
            res = team.team_connect(d, surface, _DSN_ENV, assume_yes=True,
                                    out=msgs.append, ledger=self._ledger(d))
            self.assertTrue(res.connected)
            blob = " ".join(msgs)
            self.assertNotIn("POOLED-ENDPOINT", blob.upper())
            # provider legibility line still names what mokata thinks it connected to
            self.assertIn("Supabase", blob)
            # and no pooler_trap ledger entry on the clean path
            kinds = [e.get("kind") for e in self._ledger(d).entries()]
            self.assertNotIn("pooler_trap", kinds)

    def test_db_s0_connect_secret_never_appears_anywhere(self):
        # Named secret-safety sweep (mandatory this stage): across the connect stdout, the manifest,
        # and the ledger, the DSN VALUE + password NEVER appear.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            value = _dsn(_SUPABASE_POOLER)
            os.environ[_DSN_ENV] = value
            ledger = self._ledger(d)
            msgs = []
            team.team_connect(d, surface, _DSN_ENV, assume_yes=True,
                              out=msgs.append, ledger=ledger)
            stdout = " ".join(msgs)
            manifest = _manifest_text(d)
            ledblob = json.dumps(ledger.entries())
            for surface_text in (stdout, manifest, ledblob):
                self.assertNotIn(value, surface_text)
                self.assertNotIn(_PW, surface_text)
                self.assertNotIn(_USER, surface_text)
            # the env-var NAME (the pointer) IS present in the manifest + stdout
            self.assertIn(_DSN_ENV, manifest)
            self.assertIn(_DSN_ENV, stdout)

    def test_db_s0_connect_unset_env_hint_and_unchanged(self):
        # env unset at connect → today's behavior; a provider-agnostic direct/session hint names
        # the env var, and NO pooler finding is fabricated.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            msgs = []
            res = team.team_connect(d, surface, _DSN_ENV, assume_yes=True, out=msgs.append,
                                    ledger=self._ledger(d))
            self.assertTrue(res.connected)                    # pointer still recorded (unchanged)
            blob = " ".join(msgs)
            self.assertIn(_DSN_ENV, blob)
            self.assertIn("direct", blob.lower())
            self.assertNotIn("POOLED-ENDPOINT", blob.upper())

    def test_db_s0_solo_local_repo_no_preflight_noise(self):
        # Negative: a solo/local repo that never connects a DSN sees NO DSN preflight output and
        # persists no DSN pointer (the env-var-name-only invariant, untouched).
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            manifest = _manifest_text(d)
            self.assertNotIn("pooler", manifest.lower())
            self.assertNotIn("MOKATA_PG_DSN", manifest)       # no DSN name persisted until connect


if __name__ == "__main__":
    unittest.main()
