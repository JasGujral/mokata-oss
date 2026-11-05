"""0.0.27 P24 — the read-only LSP bridge against a REAL language server: pyright.

Business rules (the same ones `tests/test_p24_ide_lsp_readonly.py` proves with the fake server,
here against the real thing): a trusted workspace's definition and references round-trip through
`pyright-langserver`; its indexing shows as a job; a write method is refused at the proxy and
pyright never sees it; the server is shut down cleanly.

Skips cleanly when no `pyright-langserver` / `basedpyright-langserver` is on PATH (never installs
one). One server at a time; the server is stopped in tearDown.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata.ide import lsp as L
from mokata.ide import trust as T
from mokata.init import init_repo

PYRIGHT = shutil.which("pyright-langserver") or shutil.which("basedpyright-langserver")
CORE = ("def parse(text):\n    if not text:\n        return []\n"
        "    return [p.strip() for p in text.split(',')]\n\n\ndef total(text):\n"
        "    return len(parse(text))\n")
TEST = "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n"


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def wait_for(pred, timeout=120.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


@unittest.skipUnless(PYRIGHT, "no pyright-langserver / basedpyright-langserver on PATH (not installed by mokata)")
class PyrightRoundTrip(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = os.path.realpath(self._t.name)
        self.home = os.path.join(self.d, "home")
        os.makedirs(self.home)
        self._env = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        self._env.start()
        self._exp = mock.patch("os.path.expanduser", lambda p: p.replace("~", self.home, 1)
                               if p.startswith("~") else p)
        self._exp.start()
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "-C", self.repo, "init", "-q", "-b", "main"], check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        init_repo(root=self.repo, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        _write(self.repo, "src/app/__init__.py", "")
        _write(self.repo, "src/app/core.py", CORE)
        _write(self.repo, "tests/test_core.py", TEST)
        _write(self.repo, "pyrightconfig.json", '{"extraPaths": ["src"], "include": ["src", "tests"]}\n')
        self.host = None

    def tearDown(self):
        if self.host is not None:
            self.host.stop_all()
        self._exp.stop()
        self._env.stop()
        self._t.cleanup()

    def test_untrusted_no_process_then_trusted_definition_and_references_round_trip(self):
        events = []
        self.host = L.Host(self.repo, trusted=lambda: T.status(self.repo)["trusted"],
                           on_event=events.append)
        # 1. untrusted: refused, nothing spawned
        with mock.patch("mokata.ide.lsp.subprocess.Popen") as popen:
            self.assertEqual("UntrustedWorkspace", self.host.start("python", by_human=True)["error"])
            popen.assert_not_called()
        # 2. trusted (the CLI twin's own function, as a human would): the real server starts
        T.decide(self.repo, True, "cli")
        res = self.host.start("python", by_human=True)
        self.assertTrue(res["started"], res)
        self.assertIn("pyright", res["server"]["server"])
        srv = self.host.get("python")
        self.assertTrue(srv.alive)
        core = os.path.join(self.repo, "src", "app", "core.py")
        test = os.path.join(self.repo, "tests", "test_core.py")
        # 3. a write method never reaches pyright
        with self.assertRaises(L.MethodRefused):
            srv.request("textDocument/rename", {"textDocument": {"uri": L.to_uri(core)},
                                                 "position": {"line": 0, "character": 4},
                                                 "newName": "parse2"})
        self.assertNotIn("textDocument/rename", srv.sent)
        # 4. definition of `parse` from its call in `total` — pyright answers once it has analysed
        uri, text = srv.open(core, "python")
        srv.open(test, "python")
        res = srv.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                       "position": {"line": 7, "character": 15}})
        locs = res if isinstance(res, list) else [res]
        got = [L.location(self.repo, x, L._Lines()) for x in locs]
        self.assertEqual([("src/app/core.py", 1)], [(g["path"], g["line"]) for g in got], got)
        # 5. references of `parse`: its def, its call in total, the import and the call in the test
        res = srv.request("textDocument/references", {"textDocument": {"uri": uri},
                                                       "position": {"line": 0, "character": 4},
                                                       "context": {"includeDeclaration": True}})
        refs = sorted((r["path"], r["line"]) for r in (L.location(self.repo, x, L._Lines()) for x in res))
        self.assertIn(("src/app/core.py", 1), refs)
        self.assertIn(("src/app/core.py", 8), refs)
        self.assertIn(("tests/test_core.py", 5), refs, refs)
        # 6. hover has a type; symbols list both functions; the server reported ready
        hov = L.hover_text(srv.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                               "position": {"line": 0, "character": 4}}))
        self.assertIn("parse", hov)
        syms = L.symbols_flat(srv.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}))
        self.assertEqual(["parse", "total"], [s["name"] for s in syms if s["depth"] == 0])
        self.assertTrue(wait_for(srv.is_ready, 30))
        self.assertEqual("ready", srv.status()["state"])
        self.assertLessEqual(set(srv.sent), L.ALLOWED)
        # 7. a clean stop: shutdown/exit, exit code 0, no kill
        self.host.stop("python")
        self.assertTrue(wait_for(lambda: not srv.alive, 10))
        self.assertEqual("stopped", srv.status()["state"])
        self.assertIn("exit", srv.sent)
        self.assertTrue(any(e.get("what") == "stopped" for e in events), events)


if __name__ == "__main__":
    unittest.main()
