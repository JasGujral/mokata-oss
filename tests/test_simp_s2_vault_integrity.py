"""SIMP.S2 — the vault channel: DB.S9 verify-on-pull SURVIVES as the deprecation-shim check,
and using the vault channel WARNS once per repo.

DB.S9 (kept by Jas veto, 2026-07-12): a vault artifact whose content no longer matches its
recorded hash is REFUSED at pull (nothing copied) and the mismatch is ledgered. SIMP.S2 keeps
that seam intact and makes the refusal NAME the migration command — a corrupt vault artifact is
refused loudly, never silently imported.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr

import _support  # noqa: F401

from mokata import MOKATA_DIR
from mokata import vault as V
from mokata.config import Surface
from mokata.init import init_repo


BRAINSTORM = "# Brainstorm\n\nApproach A vs B; chose A.\n"


def _silent(_):
    pass


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _push(root, name):
    src = os.path.join(root, f"{name}.src.md")
    _write(src, BRAINSTORM)
    V.commit_push(root, V.plan_push(root, name, src), author="a",
                  now="2026-06-27T00:00:00+00:00")


class TestVaultIntegrityShim(unittest.TestCase):
    def test_corrupt_artifact_refused_naming_the_migration(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _push(d, "p")
            _write(os.path.join(d, MOKATA_DIR, "vault", "p.md"), "tampered\n")   # corrupt it
            with self.assertRaises(V.VaultError) as cm:
                V.vault_pull(d, "p")
            msg = str(cm.exception)
            self.assertIn("content-hash", msg)          # DB.S9 check still fires
            self.assertIn("mokata migrate vault", msg)  # …and names the migration (SIMP.S2)

    def test_intact_pull_still_works_and_names_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _push(d, "p")
            content, _entry = V.vault_pull(d, "p")      # parity: intact pull unperturbed
            self.assertEqual(content, BRAINSTORM)

    def test_db_s9_ledger_row_survives(self):
        from mokata.govern import AuditLedger
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _push(d, "p")
            _write(os.path.join(d, MOKATA_DIR, "vault", "p.md"), "tampered\n")
            with self.assertRaises(V.VaultError):
                V.vault_pull(d, "p")
            ledger = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            rows = [e for e in ledger.entries() if e["kind"] == V.VAULT_INTEGRITY_KIND]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "refused")


class TestVaultTransportWarns(unittest.TestCase):
    def test_building_the_vault_transport_warns_once(self):
        from mokata.session_transport import make_transport
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stderr(buf):
                make_transport("vault", d)
            self.assertIn("0.0.17", buf.getvalue())
            self.assertIn("vault", buf.getvalue())
            buf2 = io.StringIO()
            with redirect_stderr(buf2):
                make_transport("vault", d)
            self.assertEqual(buf2.getvalue(), "")       # once per repo

    def test_local_transport_is_silent(self):
        from mokata.session_transport import make_transport
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stderr(buf):
                make_transport("local", d)
            self.assertEqual(buf.getvalue(), "")        # canonical transport → no warn


if __name__ == "__main__":
    unittest.main()
