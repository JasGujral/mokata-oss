"""TM.S4 — standing audit-publish consent (doc 48 C5/P-10).

The batched audit publish is covered by an EXPLICIT, revocable standing consent captured ONCE at
onboarding — human-gated + ledgered — replacing per-batch prompts WITHOUT weakening the gate (a
secret still hard-blocks; P2 intact). Proven here:
  * grant is human-gated (decline writes nothing) + ledgered; `has_standing_consent` reflects it;
  * revoke is human-gated + ledgered and returns to per-batch gating;
  * with consent granted, `share_audit` publishes a clean batch WITHOUT a per-batch prompt …
  * … but a SECRET in the batch is STILL hard-blocked (never a governance bypass);
  * onboarding capture is context-gated (offered with a shared context, skipped otherwise).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, config_cmd, team_audit as TA
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger

_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"
_ACTOR_VARS = ("MOKATA_ACTOR", "USER", "USERNAME", "LOGNAME")
_NO = lambda _t: False


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _consent(d):
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return (json.load(fh).get("settings") or {}).get("audit", {}).get("standing_consent")


class _Actor:
    def __init__(self, name="alice"):
        self.name = name

    def __enter__(self):
        self._saved = {k: os.environ.pop(k, None) for k in _ACTOR_VARS + TA.PG_DSN_ENVS}
        os.environ["MOKATA_ACTOR"] = self.name
        return self

    def __exit__(self, *exc):
        for k in _ACTOR_VARS + TA.PG_DSN_ENVS:
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v


class _FakeAuditPg:
    """Append-only in-memory stand-in for the shared audit backend (INSERT only; no UPDATE)."""
    def __init__(self):
        self.rows = []
        self._id = 0

    def execute(self, sql, params=None):
        head = " ".join(sql.split()).upper()

        class _C:
            def __init__(self, rows=None):
                self._r = list(rows or [])

            def fetchone(self):
                return self._r[0] if self._r else None

            def fetchall(self):
                return list(self._r)
        if head.startswith("INSERT"):
            ns, act, seq, kind, at, entry = params
            self._id += 1
            self.rows.append({"namespace": ns, "actor": act, "seq": seq, "entry": entry})
            return _C()
        if head.startswith("SELECT MAX(SEQ)"):
            ns, act = params
            seqs = [r["seq"] for r in self.rows if r["namespace"] == ns and r["actor"] == act]
            return _C([(max(seqs) if seqs else None,)])
        return _C()


class TestGrantRevoke(unittest.TestCase):
    def test_grant_is_gated_and_ledgered(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            ledger = AuditLedger.from_mokata_dir(s.mokata_dir)
            res = TA.grant_standing_consent(d, s, assume_yes=True, ledger=ledger,
                                            out=lambda _m: None)
            self.assertTrue(res.granted)
            self.assertTrue(TA.has_standing_consent(Surface.load(d).manifest.data))
            self.assertTrue(_consent(d))                    # flag persisted
            events = [e for e in ledger.entries() if e.get("kind") == "audit_consent"]
            self.assertTrue(events and events[-1]["decision"] == "granted")

    def test_grant_declined_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            res = TA.grant_standing_consent(d, s, confirm=_NO, out=lambda _m: None)
            self.assertFalse(res.granted)
            self.assertFalse(TA.has_standing_consent(Surface.load(d).manifest.data))

    def test_revoke_is_gated_and_ledgered(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            TA.grant_standing_consent(d, s, assume_yes=True, out=lambda _m: None)
            ledger = AuditLedger.from_mokata_dir(s.mokata_dir)
            res = TA.revoke_standing_consent(d, s, assume_yes=True, ledger=ledger,
                                             out=lambda _m: None)
            self.assertFalse(res.granted)
            self.assertFalse(TA.has_standing_consent(Surface.load(d).manifest.data))
            events = [e for e in ledger.entries() if e.get("kind") == "audit_consent"]
            self.assertEqual(events[-1]["decision"], "revoked")


def _enable_sharing(d):
    config_cmd.config_set(d, "settings.audit.shared", "true", assume_yes=True, out=lambda _m: None)
    config_cmd.config_set(d, "settings.project.id", "acme", assume_yes=True, out=lambda _m: None)


class TestShareHonorsConsent(unittest.TestCase):
    def test_consent_publishes_without_a_per_batch_prompt(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            _enable_sharing(d)
            led = AuditLedger.from_mokata_dir(s.mokata_dir)
            led.record("memory_write", target="t", decision="approved", reason="ok")
            TA.grant_standing_consent(d, s, assume_yes=True, out=lambda _m: None)
            surface = Surface.load(d)                        # fresh — reflects the granted consent
            client = _FakeAuditPg()
            # no per-batch approval (confirm=None, assume_yes=False) — the standing consent stands in.
            res = TA.share_audit(d, surface, assume_yes=False, confirm=None,
                                 out=lambda _m: None, client=client)
            self.assertTrue(res.committed, res.message)
            self.assertGreaterEqual(res.published, 1)

    def test_secret_in_batch_is_still_blocked_despite_consent(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            _enable_sharing(d)
            led = AuditLedger.from_mokata_dir(s.mokata_dir)
            led.record("memory_write", target="t", decision="approved", reason=_SECRET_DSN)
            TA.grant_standing_consent(d, s, assume_yes=True, out=lambda _m: None)
            surface = Surface.load(d)
            client = _FakeAuditPg()
            res = TA.share_audit(d, surface, assume_yes=False, confirm=None,
                                 out=lambda _m: None, client=client)
            self.assertFalse(res.committed)                 # secret hard-blocks even with consent
            self.assertTrue(res.findings)


class TestOnboardingCapture(unittest.TestCase):
    def test_capture_grants_with_a_shared_context(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            cap = TA.capture_standing_consent(d, s, "MOKATA_PG_DSN",
                                              {"MOKATA_PG_DSN": "postgres://h/db"},
                                              assume_yes=True, out=lambda _m: None)
            self.assertEqual(cap.status, "wired")
            self.assertTrue(TA.has_standing_consent(Surface.load(d).manifest.data))

    def test_capture_skips_without_a_shared_context(self):
        with tempfile.TemporaryDirectory() as d, _Actor():
            s = _repo(d)
            cap = TA.capture_standing_consent(d, s, "MOKATA_PG_DSN", {},
                                              assume_yes=True, out=lambda _m: None)
            self.assertEqual(cap.status, "skipped")
            self.assertFalse(TA.has_standing_consent(Surface.load(d).manifest.data))


if __name__ == "__main__":
    unittest.main()
