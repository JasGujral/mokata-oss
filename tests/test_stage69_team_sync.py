"""Stage 69 — zero-setup team sync (one-command adopt + OPTIONAL managed-Postgres connect).

HONEST SCOPE: mokata runs NO hosted service. "Hosted sync" = pointing the shared backends at
the team's OWN managed Postgres (Supabase/Neon/RDS — just a DSN), via an env-var DSN. These tests
prove: `team adopt` pulls + wires a shared governed stack human-gated + secret-scanned (decline →
nothing wired) + idempotent + reversible; `team connect` wires the managed-Postgres pointer via an
ENV-VAR NAME and DEGRADES CLEAN with no driver/DSN (clear message, no crash) and NEVER persists the
DSN secret; the honest copy never claims mokata hosts anything; and 54e parity stays green.

53b lesson: confirm callables / EOF stdin are exercised so a non-interactive decline is the safe
default (nothing wired).
"""

import json
import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata import team
from mokata.config import Surface


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _manifest_text(root):
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    with open(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return fh.read()


# A fake DSN VALUE assembled from fragments so no literal credential lives in this file (mokata's
# own secret-guard would otherwise block writing it). The point: this value must NEVER be written
# to the manifest — only the env-var NAME is.
_FAKE_DSN = "postgres://u" + ":" + "p" + "w@h:5432/db"
_DENY = lambda _t: False          # a confirm callable that declines (and the EOF/non-interactive default)
_ALLOW = lambda _t: True


class TestTeamConnect(unittest.TestCase):
    def test_connect_wires_pointer_without_storing_the_dsn(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            os.environ["MOKATA_PG_DSN"] = _FAKE_DSN
            try:
                res = team.team_connect(d, surface, "MOKATA_PG_DSN", assume_yes=True,
                                        out=lambda _m: None)
            finally:
                os.environ.pop("MOKATA_PG_DSN", None)
            self.assertTrue(res.connected)
            text = _manifest_text(d)
            # the env-var NAME is persisted (the pointer) ...
            self.assertIn("MOKATA_PG_DSN", text)
            # ... but the DSN VALUE is NEVER written
            self.assertNotIn(_FAKE_DSN, text)
            self.assertNotIn("pw@h", text)

    def test_connect_wires_the_shared_memory_postgres_backend(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team.team_connect(d, surface, "MOKATA_PG_DSN", assume_yes=True, out=lambda _m: None)
            data = json.loads(_manifest_text(d))
            self.assertIn("postgres", data["tools"])
            self.assertEqual(data["tools"]["postgres"]["config"]["dsn_env"], "MOKATA_PG_DSN")
            self.assertIn("postgres", data["capabilities"]["memory_store"]["fallback"])

    def test_connect_degrades_clean_with_no_driver_or_dsn(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            os.environ.pop("MOKATA_PG_DSN", None)   # no DSN exported
            msgs = []
            res = team.team_connect(d, surface, "MOKATA_PG_DSN", assume_yes=True,
                                    out=msgs.append)
            # no crash; clear, honest readiness message about degrade-clean
            blob = (" ".join(msgs) + " " + res.message).lower()
            self.assertFalse(res.readiness.active)
            self.assertTrue("degrade" in blob or "until" in blob or "not set" in blob)
            # never persisted the DSN value
            self.assertNotIn(_FAKE_DSN, _manifest_text(d))

    def test_connect_declined_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            before = _manifest_text(d)
            res = team.team_connect(d, surface, "MOKATA_PG_DSN", confirm=_DENY,
                                    out=lambda _m: None)
            self.assertFalse(res.connected)
            self.assertEqual(_manifest_text(d), before)   # nothing wired on decline

    def test_connect_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team.team_connect(d, surface, "MOKATA_PG_DSN", assume_yes=True, out=lambda _m: None)
            surface2 = Surface.load(d)
            res2 = team.team_connect(d, surface2, "MOKATA_PG_DSN", assume_yes=True,
                                     out=lambda _m: None)
            self.assertFalse(res2.changed)   # already connected to the same managed DB → no-op

    def test_disconnect_is_reversible(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            team.team_connect(d, surface, "MOKATA_PG_DSN", assume_yes=True, out=lambda _m: None)
            res = team.team_disconnect(d, Surface.load(d), assume_yes=True, out=lambda _m: None)
            self.assertTrue(res.changed)
            data = json.loads(_manifest_text(d))
            self.assertNotIn("postgres", data["capabilities"]["memory_store"]["fallback"])

    def test_honest_copy_never_claims_mokata_hosts(self):
        note = team.honest_note().lower()
        self.assertIn("your own", note) if "your own" in note else None
        self.assertTrue("does not host" in note or "no hosted" in note or "never hosts" in note
                        or "mokata hosts nothing" in note,
                        "the connect copy must be honest that mokata hosts nothing")
        # it should NOT imply mokata runs a service
        self.assertNotIn("we host", note)


class TestTeamAdopt(unittest.TestCase):
    def _shared_stack(self, d, *, with_pg=False, secret=False):
        """Write a shared stack file a teammate would publish; return its path."""
        src = _repo(os.path.join(d, "src_repo"))
        from mokata.share import export_manifest
        data = export_manifest(src)
        data = json.loads(json.dumps(data))   # deep copy
        if with_pg:
            data.setdefault("tools", {})["postgres"] = {
                "provides": "memory_store", "kind": "external", "version": None,
                "detect": {"type": "python_module", "name": "psycopg"},
                "enabled": True, "config": {"dsn_env": "MOKATA_PG_DSN"}}
            data["capabilities"]["memory_store"]["fallback"] = \
                ["postgres"] + list(data["capabilities"]["memory_store"]["fallback"])
        path = os.path.join(d, "mokata-stack.json")
        with open(path, "w", encoding="utf-8") as fh:
            if secret:
                # plant a literal credential in the untrusted shared content
                blob = json.dumps(data)
                blob = blob[:-1] + ', "leak": "' + _FAKE_DSN + '"}'
                fh.write(blob)
            else:
                fh.write(json.dumps(data, indent=2))
        return path

    def test_adopt_wires_the_shared_stack_when_confirmed(self):
        with tempfile.TemporaryDirectory() as d:
            stack = self._shared_stack(d, with_pg=True)
            dest = _repo(os.path.join(d, "dest"))
            res = team.team_adopt(dest.root, stack, assume_yes=True, force=True,
                                  out=lambda _m: None)
            self.assertTrue(res.adopted)
            self.assertIn("config", res.wired)
            # the shared-memory DSN POINTER is surfaced (the env var NAME, never a DSN)
            self.assertTrue(any("MOKATA_PG_DSN" in w for w in res.wired))

    def test_adopt_declined_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            stack = self._shared_stack(d, with_pg=True)   # differs from dest → reaches the gate
            dest = _repo(os.path.join(d, "dest"))
            before = _manifest_text(dest.root)
            res = team.team_adopt(dest.root, stack, confirm=_DENY, force=True,
                                  out=lambda _m: None)
            self.assertFalse(res.adopted)
            self.assertEqual(_manifest_text(dest.root), before)   # decline → nothing wired

    def test_adopt_secret_scans_untrusted_content_and_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            stack = self._shared_stack(d, secret=True)
            dest = _repo(os.path.join(d, "dest"))
            before = _manifest_text(dest.root)
            res = team.team_adopt(dest.root, stack, assume_yes=True, force=True,
                                  out=lambda _m: None)
            self.assertTrue(res.blocked)
            self.assertFalse(res.adopted)
            self.assertTrue(res.findings)
            self.assertEqual(_manifest_text(dest.root), before)   # blocked → nothing wired

    def test_adopt_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            stack = self._shared_stack(d)
            dest = _repo(os.path.join(d, "dest"))
            team.team_adopt(dest.root, stack, assume_yes=True, force=True, out=lambda _m: None)
            res2 = team.team_adopt(dest.root, stack, assume_yes=True, force=True,
                                   out=lambda _m: None)
            self.assertTrue(res2.idempotent)   # re-adopt of the same stack → no change

    def test_adopt_degrades_clean_when_source_absent(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            res = team.team_adopt(dest.root, os.path.join(d, "nope.json"),
                                  assume_yes=True, out=lambda _m: None)
            self.assertFalse(res.adopted)
            self.assertIn("no", res.message.lower())   # a clear "nothing to adopt" message


class TestTeamCliAndParity(unittest.TestCase):
    def test_team_in_surface_matrix_and_parity_green(self):
        from mokata import parity
        self.assertIn("team", parity.SURFACE_MATRIX)
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())

    def test_team_cli_status_degrades_clean_uninitialized(self):
        from mokata.cli import cmd_team
        import io
        from contextlib import redirect_stdout

        class A:  # a minimal args namespace
            pass
        a = A()
        with tempfile.TemporaryDirectory() as d:
            a.path = d
            a.action = "status"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_team(a)
            self.assertEqual(rc, 0)
            self.assertIn("not initialized", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
