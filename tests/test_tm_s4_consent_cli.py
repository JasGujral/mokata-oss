"""TM.S4 — the `mokata audit --consent show|grant|revoke` surface (revocable standing consent)."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli, team_audit as TA
from mokata.config import Surface

_ACTOR_VARS = ("MOKATA_ACTOR", "USER", "USERNAME", "LOGNAME")


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue() + err.getvalue()


class TestConsentCli(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in _ACTOR_VARS}
        os.environ["MOKATA_ACTOR"] = "alice"

    def tearDown(self):
        os.environ.pop("MOKATA_ACTOR", None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_grant_show_revoke_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, _ = _run(["audit", "--consent", "grant", "--path", d, "--yes"])
            self.assertEqual(rc, 0)
            self.assertTrue(TA.has_standing_consent(Surface.load(d).manifest.data))

            rc, show = _run(["audit", "--consent", "show", "--path", d])
            self.assertIn("GRANTED", show)

            rc, _ = _run(["audit", "--consent", "revoke", "--path", d, "--yes"])
            self.assertEqual(rc, 0)
            self.assertFalse(TA.has_standing_consent(Surface.load(d).manifest.data))


if __name__ == "__main__":
    unittest.main()
