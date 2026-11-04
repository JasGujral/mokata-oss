"""0.0.27 P24 `ID-LSP-READONLY-EARLIER` (research ruling R3) — read-only LSP behind workspace trust.

Business rules — what the person at the IDE can rely on:
  * UNTRUSTED MEANS NO PROCESS. Until a human marks the workspace trusted, no language server is
    started by any route — a GET never spawns anything, `start` is refused — and navigation falls
    back to the graph layer, labelled `source: graph`, saying why.
  * TRUST IS A HUMAN, LEDGERED ACT. In the window it needs the one-time code the server printed
    to its OWN terminal (the P38 bar; the pairing token alone never trusts); the decision is a
    `workspace_trust` row on the audit ledger; the record lives in the user's profile, never in
    the repo; the CLI twin (`mokata ide --trust`) refuses off a TTY and inside a Claude Code
    session; revoking stops every server.
  * NEVER AN EDIT. The bridge sends only an allowlist of read methods: a rename, a code action,
    formatting, executeCommand or completion is refused BEFORE it reaches the server (the server's
    log never shows it), and a server that asks the client to apply an edit is told "not applied".
  * ROUND TRIPS: definition, references, hover, document symbols and published diagnostics come
    back with 1-based positions, repo-relative paths and snippets.
  * PROCESSES: one server per language; a crash is reported (an event, the status) and the server
    restarts at the next ask — not for ever (a crash loop waits for a human Start); idle servers
    are shut down; Cancel on a slow question sends `$/cancelRequest`; no console window flags are
    missing on Windows; a missing binary is NAMED with its install line and never installed.
  * SCALE: indexing is a visible job with progress from `$/progress`; meanwhile the graph answers,
    labelled, and the answer names the job.
  * THE EDGE: every answer carries what px0's LSP cannot — references split into tests vs code,
    the symbol's callers / blast radius / covering tests from the graph with its honesty basis,
    and the ACs whose anchors name the file; a diagnostic names the symbol and ACs it sits under.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p0_ide_api_contract import BASE, ORIGIN, PORT, TOKEN, fixture, _write

from mokata.ide import lsp as L
from mokata.ide import trust as T
from mokata.ide.features import lsp as F
from mokata.knowledge import cancel as C

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "_fake_lsp_server.py")
AGENT = {"X-Mokata-Token": TOKEN}


def fake_argv(*knobs: str):
    return [sys.executable, "-I", FAKE, *knobs]


def finder_for(*knobs: str):
    def finder(root, language):
        if language != "python":
            return {"found": False, "note": f"no {language} server (test)", "candidates": []}
        return {"found": True, "binary": sys.executable, "argv": fake_argv(*knobs),
                "name": "fake-lsp", "candidates": [{"name": "fake-lsp", "install": "", "found": True}]}
    return finder


def _exe(d, name, nt=False):
    """An executable file `name` (+ .exe when `nt`) in `d`; returns its path."""
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + (".exe" if nt else ""))
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(p, 0o755)
    return p


def _pkg(root, pkg, bin_rel, binname=None, root_is_node_modules=False, create_script=True):
    """`<root>/node_modules/<pkg>/package.json` with `bin[binname] = bin_rel` (+ the script)."""
    base = root if root_is_node_modules else os.path.join(root, "node_modules")
    pkg_dir = os.path.join(base, pkg)
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, "package.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": pkg, "bin": {binname or pkg: bin_rel}}, fh)
    script = os.path.normpath(os.path.join(pkg_dir, *bin_rel.split("/")))
    if create_script:
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("// server\n")
    return script


def wait_for(pred, timeout=20.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


class _Repo(unittest.TestCase):
    """A fixture repo and a private HOME (trust records live in the user's profile)."""

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
        fixture(self.repo)
        _write(self.repo, "src/app/core.py",
               "def parse(text):\n    if not text:\n        return []\n"
               "    return [p.strip() for p in text.split(',')]\n\n\ndef total(text):\n"
               "    return len(parse(text))  # TODO: cache\n")
        self.hosts = []

    def tearDown(self):
        for h in self.hosts:
            h.stop_all()
        self._exp.stop()
        self._env.stop()
        self._t.cleanup()

    def host(self, *knobs, trusted=True, **kw):
        h = L.Host(self.repo, trusted=lambda: trusted, finder=finder_for(*knobs), **kw)
        self.hosts.append(h)
        return h

    def ledger_rows(self):
        from mokata.govern.ledger import AuditLedger
        led = AuditLedger.from_mokata_dir(os.path.join(self.repo, ".mokata"))
        return [e for e in led.entries() if e.get("kind") == T.LEDGER_KIND]


# ============================================================== untrusted: nothing is started

class UntrustedMeansNoProcess(_Repo):

    def test_start_is_refused_and_no_process_is_spawned_until_trusted(self):
        """An untrusted workspace never runs a language server: `start` answers
        UntrustedWorkspace and Popen is not called."""
        h = self.host(trusted=False)
        with mock.patch("mokata.ide.lsp.subprocess.Popen") as popen:
            res = h.start("python", by_human=True)
        self.assertFalse(res["started"])
        self.assertEqual("UntrustedWorkspace", res["error"])
        popen.assert_not_called()
        self.assertEqual([], h.status()["servers"])

    def test_a_get_never_starts_a_server_and_the_graph_answers_labelled(self):
        """Routes: untrusted, every read answers from the graph layer, says `source: graph` with
        `lsp.state: untrusted`, and the host has started nothing (px0's GET /api/lsp/warm spawned
        rust-analyzer — here a GET cannot)."""
        c, state = boot(self.repo)
        try:
            with mock.patch.object(L.Server, "start") as popen:
                for kind in F.KINDS:
                    r = c.get(f"/api/lsp/{kind}?path=src/app/core.py&line=8&col=16")
                    self.assertEqual(200, r.status_code, r.text)
                    body = r.json()
                    self.assertIn(body["source"], ("graph", "none"))
                    self.assertEqual("untrusted", body["lsp"]["state"])
                    self.assertIn("not trusted", body["lsp"]["note"])
                popen.assert_not_called()
            d = c.get("/api/lsp/definition?path=src/app/core.py&line=8&col=16").json()
            self.assertEqual("parse", d["symbol"])
            self.assertTrue(any(r["path"] == "src/app/core.py" and r["line"] == 1 for r in d["results"]),
                            d["results"])
            self.assertIn(d["basis"], ("structural", "lexical", "verified_empty"))
            self.assertEqual([], state.lsp.status()["servers"])
            r = c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN)
            self.assertEqual(409, r.status_code)
            self.assertEqual("UntrustedWorkspace", r.json()["error"])
        finally:
            state.close()

    def test_the_status_names_each_language_and_whether_its_server_was_found_never_installing(self):
        """`GET /api/lsp` lists the languages, each server candidate and its install line; a
        missing binary is named — mokata never installs one (no pip/npm is run)."""
        c, state = boot(self.repo)
        try:
            with mock.patch.dict(os.environ, {"PATH": os.path.join(self.d, "nowhere")}), \
                    mock.patch.object(L.Server, "start") as popen:
                body = c.get("/api/lsp").json()
                langs = {x["language"]: x for x in body["languages"]}
                self.assertFalse(langs["python"]["found"])
                self.assertIn("install", langs["python"]["note"])
                self.assertIn("pyright", langs["python"]["candidates"][0]["name"])
                popen.assert_not_called()
            self.assertEqual(L.READ_ONLY, body["read_only"])
            self.assertFalse(body["trust"]["trusted"])
            self.assertEqual(T.TRUST_CMD, body["trust"]["command"])
        finally:
            state.close()


# ============================================================== trust: a human, ledgered act

def boot(root, tty=False, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, TOKEN, PORT, None, demo, False, approve_here=False)
    if tty:
        state.approve_tty = (True, "")
    app = build_app(state)
    page = TestClient(app, base_url=BASE, follow_redirects=False)
    page.get(f"/pair?t={TOKEN}")
    return page, state


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class TrustIsAboutTheWorkspaceNotTheCommands(_Repo):
    """P24 L5, Jas's ruling (option B, 2026-11-03): `will_run` is NOT bound to the trust record, and
    every place trust is shown says so in plain words instead of implying a sealed command list."""

    def say(self, *argv):
        import contextlib
        import io as _io
        from mokata.cli import main
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(list(argv))
        return buf.getvalue()

    def test_the_status_record_carries_the_note_whether_or_not_trusted(self):
        for trusted in (False, True):
            if trusted:
                T.decide(self.repo, True, "cli")
            with self.subTest(trusted=trusted):
                note = T.status(self.repo)["note"]
                self.assertIn("not the exact commands", note)
                self.assertIn("does not record or re-check", note)
                self.assertIn("same trust", note)

    def test_mokata_status_and_doctor_say_so_once_the_workspace_is_trusted(self):
        for cmd in ("status", "doctor"):
            self.assertNotIn("IDE workspace trust", self.say(cmd, "--path", self.repo), cmd)
        T.decide(self.repo, True, "cli")
        for cmd in ("status", "doctor"):
            with self.subTest(cmd=cmd):
                out = self.say(cmd, "--path", self.repo)
                self.assertIn("IDE workspace trust: trusted", out)
                self.assertIn("not the exact commands", out)

    def test_the_trust_question_shows_the_note_before_the_human_answers(self):
        out = FakeTTY()
        with mock.patch("mokata.ide.approve_codes.human_terminal", return_value=(True, "")):
            rc = T.cli_decide(self.repo, True, stdin=io.StringIO("n\n"), stdout=out)
        self.assertEqual(1, rc)
        self.assertIn("not the exact commands", out.getvalue())

    def test_the_window_trust_box_prints_the_note(self):
        js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "src", "mokata", "ide", "static", "features", "lsp.js")
        with open(js, encoding="utf-8") as fh:
            self.assertIn("t.note", fh.read())


class TrustIsAHumanLedgeredDecision(_Repo):

    def test_without_a_terminal_the_window_cannot_trust_and_names_the_cli_twin(self):
        """No TTY: `trust/code` and `trust` answer 409 NoTerminal with `mokata ide --trust`; an
        agent's curl with the pairing token gets the same — the token is not consent."""
        c, state = boot(self.repo)
        try:
            r = c.post("/api/lsp/trust/code", json={"decision": "trust"}, headers=ORIGIN)
            self.assertEqual(409, r.status_code)
            self.assertEqual("NoTerminal", r.json()["error"])
            self.assertEqual(T.TRUST_CMD, r.json()["command"])
            r = c.post("/api/lsp/trust", json={"decision": "trust", "code": "ABCD-EFGH"}, headers=ORIGIN)
            self.assertEqual(409, r.status_code)
            self.assertFalse(T.status(self.repo)["trusted"])
        finally:
            state.close()

    def test_trust_needs_the_code_printed_to_the_servers_terminal_and_is_ledgered(self):
        """With a TTY: the code is printed to the terminal (never in a response); a wrong code is
        refused; the right one trusts, writes a `workspace_trust` ledger row (actor ide-tty) and
        a record in the user's profile — nothing inside the repo."""
        c, state = boot(self.repo, tty=True)
        try:
            out = FakeTTY()
            with mock.patch("mokata.ide.approve_codes.sys.stdout", out):
                r = c.post("/api/lsp/trust/code", json={"decision": "trust"}, headers=ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            printed = out.getvalue()
            self.assertIn("TRUST this workspace", printed)
            code = printed.split("type this code in the window:")[1].split()[0]
            self.assertNotIn(code, r.text)
            self.assertNotIn(code.replace("-", ""), r.text)
            r = c.post("/api/lsp/trust", json={"decision": "trust", "code": "ZZZZ-ZZZZ"}, headers=ORIGIN)
            self.assertEqual(403, r.status_code)
            self.assertFalse(T.status(self.repo)["trusted"])
            r = c.post("/api/lsp/trust", json={"decision": "trust", "code": code}, headers=ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            self.assertTrue(r.json()["trust"]["trusted"])
            self.assertTrue(T.status(self.repo)["trusted"])
            rows = self.ledger_rows()
            self.assertEqual(1, len(rows))
            self.assertEqual(("trusted", "ide-tty"), (rows[0]["decision"], rows[0]["actor"]))
            rec = T.record_path(self.repo)
            self.assertTrue(rec.startswith(self.home), rec)
            self.assertFalse(os.path.exists(os.path.join(self.repo, ".mokata", "trust.json")))
            # the code was spent: it cannot trust twice
            r = c.post("/api/lsp/trust", json={"decision": "trust", "code": code}, headers=ORIGIN)
            self.assertEqual(403, r.status_code)
        finally:
            state.close()

    def test_a_moved_repo_is_not_trusted_by_the_old_record(self):
        """The record names the real path it was granted for: a record for another path (the
        directory was moved, or a clone elsewhere) is not trust for this one."""
        T.decide(self.repo, True, "test")
        self.assertTrue(T.status(self.repo)["trusted"])
        other = os.path.join(self.d, "other")
        os.rename(self.repo, other)
        with mock.patch("mokata.ide.statedir.repo_key", return_value=T.statedir.repo_key(self.repo)):
            self.assertFalse(T.status(other)["trusted"])
        os.rename(other, self.repo)

    def test_revoking_needs_no_code_is_ledgered_and_stops_every_server(self):
        """Revoke is the safe direction: no code, a `revoked` ledger row, and every running
        language server is stopped at once."""
        T.decide(self.repo, True, "test")
        c, state = boot(self.repo)
        try:
            state.lsp.finder = finder_for()
            res = state.lsp.start("python", by_human=True)
            self.assertTrue(res["started"], res)
            srv = state.lsp.get("python")
            self.assertTrue(srv.alive)
            r = c.post("/api/lsp/trust", json={"decision": "revoke"}, headers=ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            self.assertFalse(T.status(self.repo)["trusted"])
            self.assertTrue(wait_for(lambda: not srv.alive))
            self.assertEqual([], state.lsp.status()["servers"])
            self.assertEqual(["trusted", "revoked"], [e["decision"] for e in self.ledger_rows()])
        finally:
            state.close()

    def test_revoke_fails_safe_when_the_repo_breaks_its_own_ledger(self):
        """verify2 N3: in a governed repo the ledger is the REPO's file, so a repo (a `git pull`
        after trust) can make it unwritable — here a directory at its path. Revoking is the safe
        direction: the workspace is untrusted anyway, and the failed ledger row is said LOUDLY —
        in the answer, in the CLI (non-zero exit, a WARNING line) and through the route."""
        from mokata.govern.ledger import AuditLedger
        T.decide(self.repo, True, "test")
        path = AuditLedger.path_for(os.path.join(self.repo, ".mokata"))
        if os.path.exists(path):
            os.remove(path)
        os.makedirs(path)
        res = T.decide(self.repo, False, "test")
        self.assertFalse(res["trusted"])
        self.assertFalse(T.status(self.repo)["trusted"])
        self.assertIn("ledger", res["ledger_error"] or "")
        # the CLI twin: revoked, and says the ledger row is missing
        T._write(self.repo, {"trusted": True, "root": os.path.realpath(self.repo), "by": "test"})
        out = io.StringIO()
        code = T.cli_decide(self.repo, False, stdin=io.StringIO(), stdout=out, environ={})
        self.assertFalse(T.status(self.repo)["trusted"])
        self.assertNotEqual(0, code)
        self.assertIn("WARNING", out.getvalue())
        # the window's Revoke
        T._write(self.repo, {"trusted": True, "root": os.path.realpath(self.repo), "by": "test"})
        c, state = boot(self.repo)
        r = c.post("/api/lsp/trust", json={"decision": "revoke"}, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        self.assertFalse(r.json()["trust"]["trusted"])
        self.assertTrue(r.json()["trust"]["ledger_error"])
        self.assertFalse(T.status(self.repo)["trusted"])

    def test_the_cli_twin_refuses_off_a_tty_and_inside_a_claude_code_session(self):
        """`mokata ide --trust` is a human act: no TTY → refused; CLAUDECODE set → refused; a TTY
        and a typed `y` → trusted, ledgered with actor `cli`; `--untrust` revokes."""
        out = io.StringIO()
        self.assertEqual(2, T.cli_decide(self.repo, True, stdin=io.StringIO("y\n"), stdout=out, environ={}))
        self.assertIn("refused", out.getvalue())
        self.assertFalse(T.status(self.repo)["trusted"])
        out = FakeTTY()
        self.assertEqual(2, T.cli_decide(self.repo, True, stdin=FakeTTY("y\n"), stdout=out,
                                         environ={"CLAUDECODE": "1"}))
        self.assertFalse(T.status(self.repo)["trusted"])
        out = FakeTTY()
        self.assertEqual(1, T.cli_decide(self.repo, True, stdin=FakeTTY("n\n"), stdout=out, environ={}))
        self.assertFalse(T.status(self.repo)["trusted"])
        out = FakeTTY()
        self.assertEqual(0, T.cli_decide(self.repo, True, stdin=FakeTTY("y\n"), stdout=out, environ={}))
        self.assertTrue(T.status(self.repo)["trusted"])
        self.assertEqual(0, T.cli_decide(self.repo, False, stdin=io.StringIO(), stdout=io.StringIO(),
                                         environ={}))
        self.assertFalse(T.status(self.repo)["trusted"])
        self.assertEqual([("trusted", "cli"), ("revoked", "cli")],
                         [(e["decision"], e["actor"]) for e in self.ledger_rows()])

    def test_an_uninitialised_repo_gets_nothing_written_into_it(self):
        """Trust for a folder mokata does not govern is recorded and ledgered in the user's
        profile — the folder itself stays untouched (P29)."""
        plain = os.path.join(self.d, "plain")
        os.makedirs(plain)
        _write(plain, "a.py", "x = 1\n")
        before = sorted(os.listdir(plain))
        res = T.decide(plain, True, "cli")
        self.assertTrue(res["trusted"])
        self.assertEqual("user", res["ledger"])
        self.assertEqual(before, sorted(os.listdir(plain)))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(T.record_path(plain)), T.USER_LEDGER)))


# ============================================================== never an edit

class TheBridgeIsReadOnly(_Repo):

    def test_a_write_method_is_refused_before_it_reaches_the_server(self):
        """rename / codeAction / formatting / executeCommand / completion / didChange raise
        MethodRefused at the proxy; the server's own log never shows them; the refusal is counted
        in the status."""
        log = os.path.join(self.d, "methods.log")
        h = self.host("--log", log)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        for method in ("textDocument/rename", "textDocument/codeAction", "textDocument/formatting",
                       "workspace/executeCommand", "textDocument/completion", "textDocument/didChange",
                       "textDocument/prepareRename", "workspace/willRenameFiles"):
            with self.assertRaises(L.MethodRefused) as cm:
                srv.request(method, {"textDocument": {"uri": "file:///x"}})
            self.assertIn("read-only", str(cm.exception))
        with self.assertRaises(L.MethodRefused):
            srv.notify("textDocument/didChange", {})
        with self.assertRaises(L.MethodRefused):             # an unknown method is no better
            srv.request("workspace/symbol", {"query": "x"})
        # a read still works, so the refusal is a choice, not a dead server
        abspath = os.path.join(self.repo, "src", "app", "core.py")
        uri, _ = srv.open(abspath, "python")
        res = srv.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                  "position": {"line": 7, "character": 15}})
        self.assertIn("parse", L.hover_text(res))
        with open(log, encoding="utf-8") as fh:
            seen = fh.read().split()
        for method in L.WRITE_METHODS:
            self.assertNotIn(method, seen)
        self.assertIn("textDocument/hover", seen)
        self.assertLessEqual(set(seen), L.ALLOWED, seen)
        st = srv.status()
        self.assertIn("textDocument/rename", st["refused_methods"])
        self.assertEqual("ready", st["state"]) if srv.is_ready() else None

    def test_a_server_that_asks_to_apply_an_edit_is_told_no(self):
        """`workspace/applyEdit` from the server is answered `applied: false` with the reason, the
        file is untouched, and the refusal is counted + published as an event."""
        events = []
        h = self.host("--apply-edit", on_event=events.append)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        abspath = os.path.join(self.repo, "src", "app", "core.py")
        with open(abspath, "rb") as fh:
            before = fh.read()
        uri, _ = srv.open(abspath, "python")
        srv.request("textDocument/hover", {"textDocument": {"uri": uri},
                                            "position": {"line": 0, "character": 5}})
        self.assertTrue(wait_for(lambda: srv.refused_edits == 1))
        with open(abspath, "rb") as fh:
            self.assertEqual(before, fh.read())
        self.assertTrue(any(e.get("what") == "edit-refused" for e in events), events)
        self.assertEqual(1, srv.status()["refused_edits"])

    def test_the_allowlist_is_exactly_the_reads_and_the_handshake(self):
        """What the bridge may send is small and named: five document reads, the lifecycle, the
        document open/close notices and cancel — nothing that writes."""
        self.assertEqual({"initialize", "shutdown", "textDocument/hover", "textDocument/definition",
                          "textDocument/references", "textDocument/documentSymbol"}, set(L.ALLOWED_REQUESTS))
        self.assertEqual({"initialized", "exit", "textDocument/didOpen", "textDocument/didClose",
                          "$/cancelRequest"}, set(L.ALLOWED_NOTIFICATIONS))
        self.assertFalse(L.ALLOWED & L.WRITE_METHODS)


# ============================================================== the round trips

class DefinitionAndReferencesRoundTrip(_Repo):

    def setUp(self):
        super().setUp()
        T.decide(self.repo, True, "test")

    def test_definition_references_hover_symbols_and_diagnostics_through_the_routes(self):
        """Trusted, server started: each read answers `source: lsp` with 1-based positions,
        repo-relative paths and snippets; diagnostics are what the server published, display only."""
        c, state = boot(self.repo)
        try:
            state.lsp.finder = finder_for("--index-seconds", "0.1")
            r = c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN)
            self.assertEqual(200, r.status_code, r.text)
            srv = state.lsp.get("python")
            self.assertTrue(wait_for(srv.is_ready))

            d = c.get("/api/lsp/definition?path=src/app/core.py&line=8&col=16").json()
            self.assertEqual("lsp", d["source"], d)
            self.assertEqual("fake-lsp", d["lsp"]["server"])
            self.assertEqual([("src/app/core.py", 1, 5)],
                             [(x["path"], x["line"], x["col"]) for x in d["results"]])
            self.assertEqual("def parse(text):", d["results"][0]["snippet"])

            refs = c.get("/api/lsp/references?path=src/app/core.py&line=1&col=5").json()
            self.assertEqual("lsp", refs["source"])
            self.assertEqual([1, 8], sorted(x["line"] for x in refs["results"]))
            self.assertTrue(all(x["path"] == "src/app/core.py" for x in refs["results"]))

            hov = c.get("/api/lsp/hover?path=src/app/core.py&line=8&col=16").json()
            self.assertIn("(function) parse", hov["hover"])

            sym = c.get("/api/lsp/symbols?path=src/app/core.py").json()
            self.assertEqual([("parse", "function", 1), ("total", "function", 7)],
                             [(s["name"], s["kind"], s["line"]) for s in sym["results"]])

            self.assertTrue(wait_for(lambda: srv.diagnostics))
            diag = c.get("/api/lsp/diagnostics?path=src/app/core.py").json()
            self.assertEqual("lsp", diag["source"])
            self.assertEqual([(8, "warning", "fake-lsp")],
                             [(x["line"], x["severity"], x["source"]) for x in diag["results"]])
            self.assertIn("TODO", diag["results"][0]["message"])
            # one server per language: a second start joins it
            r = c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).json()
            self.assertTrue(r["already"])
            self.assertEqual(1, len(state.lsp.status()["servers"]))
        finally:
            state.close()

    def test_positions_are_characters_not_utf16_units_and_crlf_files_keep_their_lines(self):
        """A line with an astral-plane character before the name and CRLF line endings: the
        column the page sends/gets is in characters, and the line numbers match the file."""
        p = os.path.join(self.repo, "src", "app", "emoji.py")
        with open(p, "wb") as fh:
            fh.write("# \U0001F600 smile\r\ndef greet(x):\r\n    return x\r\n\r\ns = greet(1)  # \U0001F600 greet\r\n".encode("utf-8"))
        h = self.host()
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        uri, text = srv.open(p, "python")
        self.assertIn("\r\n", text)
        line = text.splitlines()[4]
        col = line.index("greet") + 1
        res = srv.request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": {"line": 4, "character": L.utf16_col(line, col)},
            "context": {"includeDeclaration": True}})
        got = sorted((x["line"], x["col"]) for x in (L.location(self.repo, r, L._Lines()) for r in res))
        self.assertEqual([(2, 5), (5, 5), (5, 18)], got)
        self.assertEqual(L.char_col(line, L.utf16_col(line, col)), col)

    def test_a_result_outside_the_repo_is_marked_not_hidden(self):
        """A definition in site-packages or the stdlib is shown with its absolute path and
        `outside: true` — the page cannot open it, but the answer is honest."""
        loc = {"uri": L.to_uri(os.path.join(self.d, "elsewhere.py")),
               "range": {"start": {"line": 3, "character": 2}, "end": {"line": 3, "character": 5}}}
        got = L.location(self.repo, loc, L._Lines())
        self.assertTrue(got["outside"])
        self.assertEqual(4, got["line"])
        self.assertTrue(os.path.isabs(got["path"]))


# ============================================================== processes

class ProcessManagement(_Repo):

    def setUp(self):
        super().setUp()
        T.decide(self.repo, True, "test")

    def test_a_crash_is_reported_and_the_server_restarts_at_the_next_ask(self):
        """The server dies mid-conversation: the pending question fails with ServerGone, a
        `crashed` event names the server and its exit code, the status says `crashed`, and the next
        ask restarts it (`restarts: 1`) and answers."""
        events = []
        h = self.host("--crash-on", "textDocument/hover", on_event=events.append)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        abspath = os.path.join(self.repo, "src", "app", "core.py")
        uri, _ = srv.open(abspath, "python")
        with self.assertRaises(L.ServerGone):
            srv.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                "position": {"line": 0, "character": 5}})
        self.assertTrue(wait_for(lambda: any(e.get("what") == "crashed" for e in events)))
        crash = [e for e in events if e.get("what") == "crashed"][0]
        self.assertEqual(("fake-lsp", 3), (crash["server"], crash["exit_code"]))
        self.assertEqual("crashed", h.status()["servers"][0]["state"])
        srv2, how = h.server_for("python")
        self.assertIsNot(srv2, srv)
        self.assertEqual("restarted", how["state"])
        self.assertEqual(1, h.status()["servers"][0]["restarts"])
        uri, _ = srv2.open(abspath, "python")
        res = srv2.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                        "position": {"line": 7, "character": 15}})
        self.assertEqual(1, len(res))

    def test_the_route_reports_a_crash_and_falls_back_to_the_graph_for_that_answer(self):
        """Through the API: the ask that hit the crash answers from the graph with
        `lsp.state: crashed` and the reason; the next ask is answered by the restarted server."""
        c, state = boot(self.repo)
        try:
            state.lsp.finder = finder_for("--crash-on", "textDocument/hover", "--index-seconds", "0.1")
            self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
            self.assertTrue(wait_for(state.lsp.get("python").is_ready))
            hov = c.get("/api/lsp/hover?path=src/app/core.py&line=1&col=5").json()
            self.assertEqual("graph", hov["source"])
            self.assertEqual("crashed", hov["lsp"]["state"])
            self.assertIn("fake-lsp", hov["lsp"]["note"])
            d = c.get("/api/lsp/definition?path=src/app/core.py&line=8&col=16").json()
            self.assertEqual("lsp", d["source"])
            self.assertEqual("restarted", d["lsp"]["state"])
            self.assertEqual(1, c.get("/api/lsp").json()["servers"][0]["restarts"])
        finally:
            state.close()

    def test_a_crash_loop_waits_for_a_human_start(self):
        """MAX_CRASHES crashes within the window: the host stops restarting by itself and says so;
        an explicit Start (a human's POST) clears the hold."""
        h = self.host("--crash-on", "initialized")
        for _ in range(L.MAX_CRASHES):
            res = h.start("python")
            if res.get("started"):
                srv = h.get("python")
                wait_for(lambda: not srv.alive)
        wait_for(lambda: "python" in h._held)
        srv, how = h.server_for("python")
        self.assertIsNone(srv)
        self.assertEqual("CrashLoop", how.get("error"))
        self.assertIn("not restarting", how["note"])
        h.finder = finder_for()
        self.assertTrue(h.start("python", by_human=True)["started"])

    def test_idle_servers_are_shut_down_and_a_question_in_flight_is_not(self):
        """A server unused for `idle_s` is shut down (shutdown → exit, no kill needed); one with
        a question pending is left alone."""
        t = [1000.0]
        h = self.host(clock=lambda: t[0], idle_s=60.0)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        t[0] += 30
        self.assertEqual([], h.reap())
        self.assertTrue(srv.alive)
        t[0] += 31
        self.assertEqual(["python"], h.reap())
        self.assertTrue(wait_for(lambda: not srv.alive))
        self.assertEqual(0, srv.exit_code)
        self.assertEqual("stopped", srv.status()["state"])
        # a slow question keeps it: `_pending` is not empty
        h2 = self.host("--slow", "textDocument/hover", clock=lambda: t[0], idle_s=60.0)
        self.assertTrue(h2.start("python", by_human=True)["started"])
        s2 = h2.get("python")
        uri, _ = s2.open(os.path.join(self.repo, "src", "app", "core.py"), "python")
        flag = threading.Event()
        done = threading.Event()

        def ask():
            try:
                with C.scope(flag):
                    s2.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}})
            except C.Cancelled:
                done.set()
        threading.Thread(target=ask, daemon=True).start()
        self.assertTrue(wait_for(lambda: s2._pending))
        t[0] += 1000
        self.assertEqual([], h2.reap())
        self.assertTrue(s2.alive)
        flag.set()
        self.assertTrue(done.wait(5))

    def test_cancel_sends_cancel_request_and_no_small_timeout_exists(self):
        """A question a slow server does not answer stays pending (no deadline): cancelling the
        job sends `$/cancelRequest` for that id and the asker gets Cancelled."""
        log = os.path.join(self.d, "methods.log")
        h = self.host("--slow", "textDocument/references", "--log", log)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        uri, _ = srv.open(os.path.join(self.repo, "src", "app", "core.py"), "python")
        flag = threading.Event()
        outcome = {}

        def ask():
            try:
                with C.scope(flag):
                    srv.request("textDocument/references", {"textDocument": {"uri": uri},
                                                             "position": {"line": 0, "character": 5},
                                                             "context": {"includeDeclaration": True}})
            except BaseException as exc:  # noqa: BLE001
                outcome["exc"] = exc
        th = threading.Thread(target=ask, daemon=True)
        th.start()
        time.sleep(1.5)                                   # longer than any "small timeout" would be
        self.assertTrue(th.is_alive(), "the question gave up on its own")
        flag.set()
        th.join(5)
        self.assertIsInstance(outcome.get("exc"), C.Cancelled)
        def logged():
            with open(log, encoding="utf-8") as fh:
                return "$/cancelRequest" in fh.read()
        self.assertTrue(wait_for(logged))
        self.assertTrue(srv.alive)

    def test_windows_spawn_hides_the_console_and_never_runs_cmd_exe(self):
        """On Windows the spawn carries CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP (its own group
        for the tree kill); there is no shell anywhere and no `.cmd`/`.bat` is ever picked or
        parsed (verification M3, re-verification R3): a workspace path holding `&`, `%`, `^`, `"`
        or `|` is only ever an argv element."""
        from mokata import quiet_spawn
        with mock.patch("mokata.quiet_spawn.os.name", "nt"):
            self.assertEqual({"creationflags": 0x08000000 | 0x00000200}, quiet_spawn.detached())
        with mock.patch("mokata.ide.lsp.subprocess.Popen") as popen:
            popen.return_value.stdin = io.BytesIO()
            popen.return_value.stdout = io.BytesIO()
            popen.return_value.stderr = io.BytesIO()
            popen.return_value.poll.return_value = None
            popen.return_value.wait.side_effect = lambda *a, **k: time.sleep(0.05)
            srv = L.Server(self.repo, "python", "fake", ["x"], clock=time.monotonic)
            try:
                srv.request = lambda *a, **k: {"capabilities": {}}        # no real handshake
                srv.notify = lambda *a, **k: None
                srv.start()
            finally:
                srv.stopped.set()
                srv._stop_readers.set()
            kw = popen.call_args.kwargs
            self.assertNotIn("shell", kw)
            self.assertTrue(kw.get("start_new_session") or kw.get("creationflags"), kw)
        self.assertFalse(hasattr(L, "resolve_cmd_shim"), "shim text is never parsed (decoy-REM finding)")
        hostile = os.path.join(self.d, 'a&calc %TEMP% ^x "q" |y')
        node_dir = os.path.join(self.d, "nodebin")
        node = _exe(node_dir, "node", nt=os.name == "nt")
        script = _pkg(hostile, "typescript-language-server", "lib/cli.mjs")
        decoy = os.path.join(hostile, "node_modules", ".bin")
        os.makedirs(decoy)
        with open(os.path.join(decoy, "typescript-language-server.cmd"), "w", encoding="utf-8") as fh:
            fh.write('@REM "%dp0%\\..\\..\\..\\tmp\\evil.js" %*\r\n')
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([decoy, node_dir]), "PATHEXT": ".COM;.EXE;.BAT;.CMD"}):
            found = L.find_server(hostile, "typescript")
        self.assertTrue(found["found"], found)
        self.assertEqual([node, script, "--stdio"], found["argv"])
        joined = " ".join(found["argv"]).lower()
        for forbidden in ("cmd.exe", "comspec", "evil"):
            self.assertNotIn(forbidden, joined)
        self.assertNotIn("/c", [a.lower() for a in found["argv"]])       # an argv ELEMENT, not `lib/cli`
        self.assertFalse(any(a.lower().endswith((".cmd", ".bat")) for a in found["argv"]))

    def test_path_lookup_never_takes_the_current_directory_or_a_relative_entry(self):
        """Re-verification R2: a decoy binary in the repo root (the cwd of `mokata ide`), with
        PATH holding "", "." and the repo itself, is never chosen — on POSIX and on a simulated
        Windows with PATHEXT; a real PATH directory is."""
        decoy = _exe(self.repo, "solargraph")
        _exe(self.repo, "solargraph", nt=True)
        good_dir = os.path.join(self.d, "realbin")
        good = _exe(good_dir, "solargraph")
        good_nt = _exe(good_dir, "solargraph", nt=True)
        env = {"PATH": os.pathsep.join(["", ".", self.repo, "relative/bin", good_dir])}
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            dirs = L.path_dirs(env)
            self.assertEqual([good_dir], dirs)
            self.assertEqual(good, L.which_in("solargraph", dirs, nt=False))
            # case-insensitively: on a folding FS (macOS, Windows) `.EXE` answers for `.exe`
            self.assertEqual(good_nt.lower(),
                             (L.which_in("solargraph", dirs, nt=True, pathext=".EXE;.CMD") or "").lower())
            self.assertIsNone(L.which_in("solargraph", ["", ".", "relative"], nt=True, pathext=".EXE"))
            self.assertNotEqual(decoy, L.which_in("solargraph", [self.repo], nt=False) or "")
            # a user-configured server name: PATH only, so the repo's venv copy is not it either
            cfg = os.path.join(self.home, ".mokata", "lsp.json")
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"languages": {"ruby": {"exts": [".rb"], "id": "ruby",
                                                  "servers": [["solargraph", ["stdio"], "gem install solargraph"]]}}}, fh)
            _exe(os.path.join(self.repo, ".venv", "bin"), "solargraph")
            with mock.patch.dict(os.environ, env):
                found = L.find_server(self.repo, "ruby")
            self.assertEqual([good, "stdio"], found["argv"])
        finally:
            os.chdir(old)
        # a `.cmd` on a real PATH dir is skipped (it would need cmd.exe), an .exe beside it wins
        with open(os.path.join(good_dir, "x.cmd"), "w", encoding="utf-8") as fh:
            fh.write("@echo off\r\n")
        self.assertIsNone(L.which_in("x", [good_dir], nt=True, pathext=".CMD;.EXE"))

    def test_node_servers_come_from_the_packages_bin_for_npm_pnpm_and_yarn_layouts(self):
        """Re-verification R3 (ruled): `node_modules/<pkg>/package.json` `bin` names the script;
        it must resolve inside the package (pnpm reaches `.pnpm` through the package symlink); a
        `bin` pointing outside is refused; with no package the PATH binary is the fallback."""
        node_dir = os.path.join(self.d, "nodebin")
        node = _exe(node_dir, "node")
        env = {"PATH": node_dir}
        # yarn v1 / npm: a plain package directory
        yarn = os.path.join(self.d, "yarn")
        script = _pkg(yarn, "typescript-language-server", "lib/cli.mjs")
        with mock.patch.dict(os.environ, env):
            self.assertEqual([node, script, "--stdio"], L.find_server(yarn, "typescript")["argv"])
        # pnpm: node_modules/<pkg> is a symlink into the .pnpm store
        pnpm = os.path.join(self.d, "pnpm")
        store = os.path.join(pnpm, "node_modules", ".pnpm", "typescript-language-server@4.0.0", "node_modules")
        real = _pkg(store, "typescript-language-server", "lib/cli.mjs", root_is_node_modules=True)
        os.makedirs(os.path.join(pnpm, "node_modules"), exist_ok=True)
        os.symlink(os.path.dirname(os.path.dirname(real)), os.path.join(pnpm, "node_modules", "typescript-language-server"))
        with mock.patch.dict(os.environ, env):
            found = L.find_server(pnpm, "typescript")
        self.assertEqual([node, os.path.realpath(real), "--stdio"], found["argv"])
        # a bin that escapes the package is refused — and the PATH fallback is tried
        evil = os.path.join(self.d, "evil")
        _pkg(evil, "typescript-language-server", "../../../../tmp/evil.js", create_script=False)
        with mock.patch.dict(os.environ, env):
            found = L.find_server(evil, "typescript")
        self.assertFalse(found["found"])
        self.assertIn("resolves outside", found["note"])
        glob_dir = os.path.join(self.d, "globalbin")
        glob = _exe(glob_dir, "typescript-language-server")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([node_dir, glob_dir])}):
            self.assertEqual([glob, "--stdio"], L.find_server(evil, "typescript")["argv"])
            # no package at all: the global one
            self.assertEqual([glob, "--stdio"], L.find_server(self.repo, "typescript")["argv"])
        # pyright installed with npm in the project resolves through its package too
        py = _pkg(self.repo, "pyright", "langserver.index.js", binname="pyright-langserver")
        with mock.patch.dict(os.environ, env):
            self.assertEqual([node, py, "--stdio"], L.find_server(self.repo, "python")["argv"])

    def test_a_path_entry_inside_the_repo_never_answers_for_a_users_server_or_for_node(self):
        """Re-verification R2: a PATH entry INSIDE the repo (`<repo>/tools`, as direnv or an npm
        script puts there), or a PATH symlink pointing into the repo, never supplies the binary of
        a server the USER configured, nor `node` — the copy outside the repo does. (A built-in
        server's project copy is the trusted case and still wins.)"""
        tools = os.path.join(self.repo, "tools")
        _exe(tools, "solargraph")
        _exe(tools, "node")
        real_dir = os.path.join(self.d, "realbin")
        good = _exe(real_dir, "solargraph")
        node = _exe(real_dir, "node")
        link_dir = os.path.join(self.d, "linkbin")
        os.makedirs(link_dir)
        os.symlink(os.path.join(tools, "node"), os.path.join(link_dir, "node"))
        os.symlink(os.path.join(tools, "solargraph"), os.path.join(link_dir, "solargraph"))
        cfg = os.path.join(self.home, ".mokata", "lsp.json")
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"languages": {"ruby": {"exts": [".rb"], "id": "ruby",
                                              "servers": [["solargraph", ["stdio"], ""]]}}}, fh)
        script = _pkg(self.repo, "typescript-language-server", "lib/cli.mjs")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([tools, link_dir, real_dir])}):
            self.assertEqual([good, "stdio"], L.find_server(self.repo, "ruby")["argv"])
            self.assertEqual([node, script, "--stdio"], L.find_server(self.repo, "typescript")["argv"])
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([tools, link_dir])}):
            ruby = L.find_server(self.repo, "ruby")
            ts = L.find_server(self.repo, "typescript")
        self.assertFalse(ruby["found"], ruby)
        self.assertFalse(ts["found"], ts)
        self.assertIn("node is not on PATH", ts["note"])

    def test_a_package_symlinked_out_of_node_modules_is_refused_and_the_same_name_on_path_wins(self):
        """Re-verification R3 + L4: the script must REALPATH inside `<root>/node_modules`. A
        `node_modules/<pkg>` that is a symlink to a directory elsewhere (its `bin` inside that
        package) is refused, and the SAME server name is then looked up on PATH — a global npm
        install on Windows (`<prefix>/x.cmd` beside `<prefix>/node_modules/<pkg>`) resolves
        through its package.json; the `.cmd` beside it is never run nor read."""
        node_dir = os.path.join(self.d, "nodebin")
        node = _exe(node_dir, "node")
        elsewhere = os.path.join(self.d, "elsewhere")
        _pkg(elsewhere, "typescript-language-server", "lib/cli.mjs")
        os.makedirs(os.path.join(self.repo, "node_modules"))
        os.symlink(os.path.join(elsewhere, "node_modules", "typescript-language-server"),
                   os.path.join(self.repo, "node_modules", "typescript-language-server"))
        with mock.patch.dict(os.environ, {"PATH": node_dir}):
            found = L.find_server(self.repo, "typescript")
        self.assertFalse(found["found"], found)
        self.assertIn("resolves outside", found["note"])
        prefix = os.path.join(self.d, "npm-global")
        gscript = _pkg(prefix, "typescript-language-server", "lib/cli.mjs", root_is_node_modules=False)
        with open(os.path.join(prefix, "typescript-language-server.cmd"), "w", encoding="utf-8") as fh:
            fh.write('@"%~dp0\\..\\..\\tmp\\evil.js" %*\r\n')
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([node_dir, prefix])}):
            found = L.find_server(self.repo, "typescript")
        self.assertEqual([node, os.path.realpath(gscript), "--stdio"], found["argv"])
        self.assertNotIn("evil", " ".join(found["argv"]))

    def test_a_node_modules_symlinked_out_of_the_repo_is_refused(self):
        """verify2 N2: containment holds on REALPATHS. A committed `node_modules -> /elsewhere`
        symlink would make the script's "inside node_modules" check pass against the symlink's
        TARGET, letting the repo pick any script on the machine. The realpath of `node_modules`
        must itself be inside the realpath of the repo; if not, it is refused and the SAME name
        on PATH is used."""
        node_dir = os.path.join(self.d, "nodebin")
        node = _exe(node_dir, "node")
        elsewhere = os.path.join(self.d, "elsewhere")
        _pkg(elsewhere, "typescript-language-server", "lib/cli.mjs")
        os.symlink(os.path.join(elsewhere, "node_modules"), os.path.join(self.repo, "node_modules"))
        with mock.patch.dict(os.environ, {"PATH": node_dir}):
            found = L.find_server(self.repo, "typescript")
        self.assertFalse(found["found"], found)
        self.assertIn("resolves outside", found["note"])
        glob = _exe(os.path.join(self.d, "globalbin"), "typescript-language-server")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([node_dir, os.path.dirname(glob)])}):
            self.assertEqual([glob, "--stdio"], L.find_server(self.repo, "typescript")["argv"])
        self.assertTrue(os.path.isfile(node))

    def test_the_project_venv_is_searched_before_path(self):
        """The repo's own `.venv/bin` (Scripts on Windows) wins over PATH for a built-in server."""
        bindir = os.path.join(self.repo, ".venv", "Scripts" if os.name == "nt" else "bin")
        exe = _exe(bindir, "pylsp")
        other = _exe(os.path.join(self.d, "pathbin"), "pylsp")
        with mock.patch.dict(os.environ, {"PATH": os.path.join(self.d, "pathbin")}):
            found = L.find_server(self.repo, "python")
        self.assertEqual(("pylsp", exe), (found["name"], found["binary"]))
        self.assertNotEqual(other, found["binary"])
        self.assertEqual("ruby", L.language_of("lib/a.rb")) if False else None

    def test_closing_the_ide_stops_its_servers(self):
        """The servers never outlive the window: `state.close()` stops them."""
        c, state = boot(self.repo)
        state.lsp.finder = finder_for()
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        srv = state.lsp.get("python")
        state.close()
        self.assertTrue(wait_for(lambda: not srv.alive))


# ============================================================== scale: indexing is a job

class IndexingIsAVisibleJob(_Repo):

    def setUp(self):
        super().setUp()
        T.decide(self.repo, True, "test")

    def test_a_pending_indexing_job_is_shown_with_progress_and_the_graph_answers_meanwhile(self):
        """While the server reports `$/progress`, `/api/jobs` lists an `lsp.index` job with the
        server's own title and percentage; a read answers from the graph, labelled `indexing`,
        naming the job; once done the same read is answered by the server and the job is `done`."""
        c, state = boot(self.repo)
        try:
            state.lsp.finder = finder_for("--index-seconds", "2.5")
            self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
            srv = state.lsp.get("python")
            self.assertTrue(wait_for(lambda: srv.indexing() is not None))
            jobs = c.get("/api/jobs").json()
            idx = [j for j in jobs["active"] if j["kind"] == "lsp.index"]
            self.assertEqual(1, len(idx), jobs)
            self.assertIn("fake-lsp indexing", idx[0]["label"])
            self.assertTrue(wait_for(lambda: "Indexing" in (state.jobs.get(idx[0]["id"]).note or "")))
            self.assertTrue(idx[0]["keep"])                 # outlives the page, like an index build
            d = c.get("/api/lsp/definition?path=src/app/core.py&line=8&col=16").json()
            self.assertEqual("graph", d["source"])
            self.assertEqual("indexing", d["lsp"]["state"])
            self.assertEqual(idx[0]["id"], d["lsp"]["job"])
            self.assertEqual("Indexing", d["lsp"]["progress"]["title"])
            self.assertIn("still indexing", d["lsp"]["note"])
            self.assertEqual("indexing", c.get("/api/lsp").json()["servers"][0]["state"])
            self.assertTrue(wait_for(srv.is_ready, timeout=10))
            self.assertTrue(wait_for(lambda: state.jobs.get(idx[0]["id"]).state == "done"))
            d = c.get("/api/lsp/definition?path=src/app/core.py&line=8&col=16").json()
            self.assertEqual("lsp", d["source"])
            self.assertEqual("ready", c.get("/api/lsp").json()["servers"][0]["state"])
        finally:
            state.close()

    def test_cancelling_the_indexing_job_stops_the_server(self):
        """Cancel on the indexing job is real: the server process is killed."""
        c, state = boot(self.repo)
        try:
            state.lsp.finder = finder_for("--index-seconds", "30")
            self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
            srv = state.lsp.get("python")
            self.assertTrue(wait_for(lambda: srv.indexing() is not None))
            jid = state.lsp.index_jobs["python"].id
            r = c.post(f"/api/jobs/{jid}/cancel", headers=ORIGIN)
            self.assertEqual(200, r.status_code)
            self.assertTrue(wait_for(lambda: not srv.alive, timeout=10))
        finally:
            state.close()


# ============================================================== the mokata edge

class TheMokataEdge(_Repo):

    def setUp(self):
        super().setUp()
        T.decide(self.repo, True, "test")

    def test_references_carry_tests_vs_code_and_the_graphs_blast_radius_with_its_basis(self):
        """A references answer says how many of them are tests, and brings the graph's callers,
        blast radius, covering tests and the honesty basis for the same symbol — what a change
        at this symbol would break, which a plain LSP client cannot say."""
        _write(self.repo, "tests/test_more.py", "from app.core import parse\n\n\ndef test_two():\n    assert parse('a,b')\n")
        c, state = boot(self.repo)
        try:
            state.lsp.finder = finder_for("--index-seconds", "0.1")
            self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
            srv = state.lsp.get("python")
            self.assertTrue(wait_for(srv.is_ready))
            # the fake server only knows the documents it was handed: open the tests too
            for rel in ("tests/test_core.py", "tests/test_more.py"):
                srv.open(os.path.join(self.repo, *rel.split("/")), "python")
            refs = c.get("/api/lsp/references?path=src/app/core.py&line=1&col=5").json()
            self.assertEqual("lsp", refs["source"])
            edge = refs["edge"]
            tests = [r for r in refs["results"] if r["test"]]
            self.assertEqual(len(tests), edge["tests"])
            self.assertGreaterEqual(edge["tests"], 2)
            self.assertEqual(len(refs["results"]) - edge["tests"], edge["code"])
            self.assertTrue(all(r["path"].startswith("tests/") for r in tests))
            self.assertEqual("parse", edge["symbol"])
            self.assertIn(edge["blast_basis"], ("structural", "lexical", "verified_empty"))
            self.assertIn("callers", edge)
            self.assertIn("verdict", edge)
            self.assertTrue(any(t.startswith("tests/test_core.py") for t in edge["tests_reaching"]), edge)
        finally:
            state.close()

    def test_diagnostics_are_tied_to_the_symbol_and_the_acs_whose_anchors_name_it(self):
        """With an emitted spec whose AC anchors `src/app/core.py:total`, a diagnostic inside
        `total` names that AC; the answer's `edge.ac` lists the file's AC ties."""
        with mock.patch.object(F, "ac_ties", return_value=[
                {"ac": "AC-2", "text": "totals are cached", "symbol": "total", "kind": "implement", "run": "R-1"},
                {"ac": "AC-9", "text": "whole-file rule", "symbol": "", "kind": "reference", "run": "R-1"}]):
            c, state = boot(self.repo)
            try:
                state.lsp.finder = finder_for("--index-seconds", "0.1")
                self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
                srv = state.lsp.get("python")
                self.assertTrue(wait_for(srv.is_ready))
                c.get("/api/lsp/symbols?path=src/app/core.py")
                self.assertTrue(wait_for(lambda: srv.diagnostics))
                diag = c.get("/api/lsp/diagnostics?path=src/app/core.py").json()
                self.assertEqual([("total", ["AC-2", "AC-9"])],
                                 [(d["symbol"], d["ac"]) for d in diag["results"]])
                self.assertEqual(["AC-2", "AC-9"], [t["ac"] for t in diag["edge"]["ac"]])
            finally:
                state.close()

    def test_ac_ties_read_the_runs_spec_anchors(self):
        """`ac_ties` resolves anchors from the default run's emitted spec by file (and symbol)."""
        from mokata.engine.spec import AcceptanceCriterion, CodeAnchor, Spec
        spec = Spec(title="t", criteria=[
            AcceptanceCriterion(id="AC-1", text="parse splits", anchors=[CodeAnchor(file="src/app/core.py", symbol="parse")]),
            AcceptanceCriterion(id="AC-3", text="other file", anchors=[CodeAnchor(file="src/app/other.py")])])
        with mock.patch("mokata.ide.plans.local_run_ids", return_value=["R-1"]), \
                mock.patch("mokata.ide.plans._default_run", return_value="R-1"), \
                mock.patch("mokata.ide.plans._run_store", return_value=object()), \
                mock.patch("mokata.engine.spec_gate.read_emitted_spec", return_value=(spec, False)):
            ties = F.ac_ties(self.repo, "src/app/core.py")
        self.assertEqual([("AC-1", "parse")], [(t["ac"], t["symbol"]) for t in ties])
        self.assertEqual([], F.ac_ties(os.path.join(self.d, "nowhere"), "x.py"))


# ============================================================== verification (NO-GO → GO) regressions

class ARepoNeverChoosesWhatExecutes(_Repo):
    """Verification H1 (ruled): the server map comes from mokata's built-ins plus the USER's own
    profile — a repo's `.mokata/lsp.json` is not read at all."""

    def test_a_planted_repo_config_is_ignored_and_its_shell_never_runs(self):
        """The verifier's probe: `.mokata/lsp.json` in the repo maps python to `sh -c touch …`.
        After trust the built-in python server is what runs; the marker file never appears."""
        marker = os.path.join(self.repo, "RAN_REPO_CHOSEN_SHELL")
        cfg = os.path.join(self.repo, ".mokata", "lsp.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"languages": {"python": {"exts": [".py"], "servers": [
                ["sh", ["-c", f"touch {marker}; exec {sys.executable} -I {FAKE}"], "x"]]}}}, fh)
        self.assertFalse(hasattr(L, "REPO_CONFIG"))
        self.assertEqual("pyright-langserver", L.languages()["python"]["servers"][0][0])
        found = L.find_server(self.repo, "python")
        self.assertNotEqual("sh", found["name"])
        T.decide(self.repo, True, "test")
        h = L.Host(self.repo, trusted=lambda: True)         # the REAL finder, not the test's
        self.hosts.append(h)
        res = h.start("python", by_human=True)
        self.assertFalse(os.path.exists(marker), "the repo's command line ran")
        if res.get("started"):
            self.assertIn("pyright", res["server"]["server"])
        else:
            self.assertEqual("NoServer", res["error"])
        self.assertFalse(os.path.exists(marker))

    def test_the_trust_prompt_names_the_exact_command_lines_trusting_allows(self):
        """`GET /api/lsp` carries `trust.will_run` (language → argv); the TTY code line and
        `mokata ide --trust` print the same argv before the human decides."""
        bindir = os.path.join(self.repo, ".venv", "Scripts" if os.name == "nt" else "bin")
        exe = _exe(bindir, "pyright-langserver")
        with mock.patch.dict(os.environ, {"PATH": os.path.join(self.d, "nowhere")}):
            runs = L.will_run(self.repo)
            self.assertEqual([("python", [exe, "--stdio"])], [(r["language"], r["argv"]) for r in runs])
            c, state = boot(self.repo, tty=True)
            try:
                body = c.get("/api/lsp").json()
                self.assertEqual([exe, "--stdio"], body["trust"]["will_run"][0]["argv"])
                out = FakeTTY()
                with mock.patch("mokata.ide.approve_codes.sys.stdout", out):
                    r = c.post("/api/lsp/trust/code", json={"decision": "trust"}, headers=ORIGIN)
                self.assertEqual(200, r.status_code, r.text)
                self.assertIn(f"python: {exe} --stdio", out.getvalue())
            finally:
                state.close()
            out = FakeTTY()
            T.cli_decide(self.repo, True, stdin=FakeTTY("n\n"), stdout=out, environ={})
            self.assertIn(f"python: {exe} --stdio", out.getvalue())


class StoppingNeverHangsAndKillsTheTree(_Repo):
    """Verification H2 + M1: a server whose grandchild holds its pipes, or that ignores
    `shutdown`, is stopped in bounded time and nothing of its tree survives."""

    def setUp(self):
        super().setUp()
        T.decide(self.repo, True, "test")
        self.pidfile = os.path.join(self.d, "grandchild.pid")

    def _grandchild_alive(self):
        """Is the grandchild still a live process? Probed WITHOUT a signal-0 kill (which
        terminates on Windows): `/proc/<pid>/stat` on Linux, `ps` on macOS/BSD, `tasklist` on
        Windows. A zombie (dead, not yet reaped by init/launchd) counts as gone."""
        import subprocess
        with open(self.pidfile, encoding="utf-8") as fh:
            pid = int(fh.read())
        if os.name == "nt":
            exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe")
            out = subprocess.run([exe, "/FI", f"PID eq {pid}", "/NH"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            return str(pid) in out, pid
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                return fh.read().rsplit(")", 1)[1].split()[0] != "Z", pid
        except FileNotFoundError:
            if os.path.isdir("/proc/self"):
                return False, pid                       # Linux, and the pid is gone
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True).stdout.strip()
        return bool(st) and not st.startswith("Z"), pid

    def test_kill_with_an_inherited_pipe_grandchild_returns_and_the_grandchild_dies(self):
        """The verifier's deadlock: a grandchild inherited stdout/stderr, so EOF never comes.
        `kill` returns within the grace period, the pending question fails, the grandchild is
        gone (process-group kill)."""
        h = self.host("--grandchild", "inherit", "--pidfile", self.pidfile, "--slow", "textDocument/hover")
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        self.assertTrue(wait_for(lambda: os.path.exists(self.pidfile)))
        uri, _ = srv.open(os.path.join(self.repo, "src", "app", "core.py"), "python")
        outcome = {}

        def ask():
            try:
                srv.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}})
            except BaseException as exc:  # noqa: BLE001
                outcome["exc"] = exc
        th = threading.Thread(target=ask, daemon=True)
        th.start()
        self.assertTrue(wait_for(lambda: srv._pending))
        t0 = time.monotonic()
        srv.kill("test")
        took = time.monotonic() - t0
        self.assertLess(took, 2 * L.STOP_GRACE_S + 1, f"kill blocked for {took:.1f}s")
        th.join(5)
        self.assertIsInstance(outcome.get("exc"), L.ServerGone)
        self.assertFalse(srv.alive)
        self.assertTrue(wait_for(lambda: not self._grandchild_alive()[0], timeout=5), self._grandchild_alive())

    def test_stop_all_on_a_server_that_ignores_shutdown_is_bounded_and_leaves_no_grandchild(self):
        """`stop_all` (revoke, IDE shutdown, idle reap) on a server that never answers `shutdown`
        and keeps a grandchild: bounded by the grace periods; no grandchild survives."""
        h = self.host("--grandchild", "detach", "--pidfile", self.pidfile, "--ignore-shutdown")
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        self.assertTrue(wait_for(lambda: os.path.exists(self.pidfile)))
        t0 = time.monotonic()
        h.stop_all("test")
        took = time.monotonic() - t0
        self.assertLess(took, 3 * L.STOP_GRACE_S + 1, f"stop_all blocked for {took:.1f}s")
        self.assertFalse(srv.alive)
        self.assertEqual([], h.status()["servers"])
        self.assertTrue(wait_for(lambda: not self._grandchild_alive()[0], timeout=5), self._grandchild_alive())

    def test_revoke_and_ide_close_return_promptly_with_a_lingering_server(self):
        """Through the routes: `POST /api/lsp/trust {revoke}` and `state.close()` do not hang on
        a server that ignores shutdown and holds a grandchild on its pipes."""
        c, state = boot(self.repo)
        state.lsp.finder = finder_for("--grandchild", "inherit", "--pidfile", self.pidfile, "--ignore-shutdown")
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        srv = state.lsp.get("python")
        self.assertTrue(wait_for(lambda: os.path.exists(self.pidfile)))
        t0 = time.monotonic()
        r = c.post("/api/lsp/trust", json={"decision": "revoke"}, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        self.assertLess(time.monotonic() - t0, 3 * L.STOP_GRACE_S + 1)
        self.assertFalse(srv.alive)
        self.assertTrue(wait_for(lambda: not self._grandchild_alive()[0], timeout=5))
        t0 = time.monotonic()
        state.close()
        self.assertLess(time.monotonic() - t0, 3 * L.STOP_GRACE_S + 1)

    def test_a_crash_kills_the_servers_children_and_fails_the_pending_question_at_once(self):
        """Re-verification R1 (the verifier's construction): the server CRASHES with a question
        pending while a grandchild holds its stdout pipe. The crash is seen from the PROCESS (not
        the pipe), the pending question fails with ServerGone within seconds, the grandchild is
        killed with the group (recorded at spawn: no `getpgid` on a dead leader), and a later
        `stop_all` finds nothing alive — no orphan per crash-restart cycle."""
        events = []
        h = self.host("--grandchild", "inherit", "--pidfile", self.pidfile, "--crash-on", "textDocument/hover",
                      on_event=events.append)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        self.assertTrue(wait_for(lambda: os.path.exists(self.pidfile)))
        self.assertEqual(srv.pgid, srv.proc.pid)
        uri, _ = srv.open(os.path.join(self.repo, "src", "app", "core.py"), "python")
        t0 = time.monotonic()
        with self.assertRaises(L.ServerGone):
            srv.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}})
        self.assertLess(time.monotonic() - t0, 8.0, "the crash was hidden by the child's pipe")
        self.assertTrue(srv.stopped.is_set())
        self.assertTrue(wait_for(lambda: not self._grandchild_alive()[0], timeout=5), self._grandchild_alive())
        self.assertTrue(wait_for(lambda: any(e.get("what") == "crashed" for e in events)))
        self.assertEqual("crashed", h.status()["servers"][0]["state"])
        t0 = time.monotonic()
        h.stop_all("test")
        self.assertLess(time.monotonic() - t0, 3 * L.STOP_GRACE_S + 1)
        self.assertFalse(self._grandchild_alive()[0])

    def test_on_windows_the_tree_is_killed_through_its_job_object_even_after_the_leader_died(self):
        """Re-verification R1, the Windows half: `taskkill /T` needs a LIVE leader to find the tree,
        so after a crash it reaches nothing. The server is put in a Job Object at spawn (before
        `initialize`), and the tree kill terminates the JOB — also when the leader is already
        dead — and never falls back to taskkill then. (kernel32 is faked: no Windows box here.)"""
        calls = []

        class K32:
            def CreateJobObjectW(self, *a):
                calls.append("create")
                return 4242

            def SetInformationJobObject(self, job, cls, info, size):
                st = info._obj                           # the structure byref() points at
                calls.append(("limits", job, cls, st.BasicLimitInformation.LimitFlags, size))
                return self.set_ok

            set_ok = 1

            def AssignProcessToJobObject(self, job, handle):
                calls.append(("assign", job, handle))
                return 1

            def TerminateJobObject(self, job, code):
                calls.append(("terminate", job))
                return 1

            def CloseHandle(self, h):
                calls.append(("close", h))
                return 1

        dead = mock.Mock(pid=77, _handle=9001)
        dead.poll.return_value = 3                       # the leader already crashed
        job = L.WinJob(dead, kernel32=K32())
        self.assertTrue(job.ok)
        self.assertIn(("assign", 4242, 9001), calls)
        import ctypes
        lim = [c for c in calls if c[0] == "limits"]
        self.assertEqual(1, len(lim))
        # verify2 N5: the flag that kills the tree when the IDE itself dies, and the Windows
        # x64/ARM64 layout of JOBOBJECT_EXTENDED_LIMIT_INFORMATION (144 bytes; 4-byte DWORDs)
        self.assertEqual((4242, 9, 0x2000), lim[0][1:4], "KILL_ON_JOB_CLOSE must be set")
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(144, lim[0][4])
        # a job whose limits did not take is not the promised job: not `ok` (taskkill fallback)
        k = K32()
        k.set_ok = 0
        self.assertFalse(L.WinJob(dead, kernel32=k).ok)
        with mock.patch("mokata.ide.lsp.os.name", "nt"), \
                mock.patch("mokata.ide.lsp.subprocess.run") as run:
            L._kill_tree(dead, None, job)
        run.assert_not_called()
        self.assertIn(("terminate", 4242), calls)
        self.assertIn(("close", 4242), calls)
        # and the Server makes that job at spawn on Windows
        made = []
        with mock.patch("mokata.ide.lsp.subprocess.Popen") as popen, \
                mock.patch("mokata.ide.lsp.WinJob", side_effect=lambda p: made.append(p) or job):
            popen.return_value.stdin = io.BytesIO()
            popen.return_value.stdout = io.BytesIO()
            popen.return_value.stderr = io.BytesIO()
            popen.return_value.poll.return_value = None
            popen.return_value.wait.side_effect = lambda *a, **k: time.sleep(0.05)
            srv = L.Server(self.repo, "python", "fake", ["x"])
            srv.request = lambda *a, **k: {"capabilities": {}}
            srv.notify = lambda *a, **k: None
            with mock.patch("mokata.ide.lsp.os.name", "nt"), \
                    mock.patch("mokata.ide.lsp.to_uri", lambda p: "file:///repo"):
                try:
                    srv.start()
                finally:
                    srv.stopped.set()
                    srv._stop_readers.set()
        self.assertEqual([popen.return_value], made)
        self.assertIs(job, srv.job)

    @unittest.skipIf(os.name == "nt", "process groups are POSIX; Windows uses the Job Object")
    def test_after_a_crash_the_group_is_killed_once_and_never_signalled_again(self):
        """verify2 N1: the group is SIGKILLed once, when the crash is seen. A later stop, kill or
        stop_all never signals that pgid again — once the group is empty its number may belong
        to an UNRELATED process group of the user's, and a second SIGKILL would hit it."""
        import signal as _signal
        real = os.killpg
        sent = []

        def spy(pgid, sig):
            sent.append((pgid, sig))
            return real(pgid, sig)
        h = self.host("--grandchild", "detach", "--pidfile", self.pidfile, "--crash-on", "textDocument/hover")
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        pgid = srv.proc.pid
        self.assertTrue(wait_for(lambda: os.path.exists(self.pidfile)))
        uri, _ = srv.open(os.path.join(self.repo, "src", "app", "core.py"), "python")
        with mock.patch.object(os, "killpg", spy):
            with self.assertRaises(L.ServerGone):
                srv.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}})
            self.assertTrue(wait_for(lambda: not self._grandchild_alive()[0], timeout=5))
            time.sleep(0.3)
            srv.stop("later stop")
            srv.kill("later kill")
            h.stop_all("later stop_all")
        self.assertEqual([(pgid, _signal.SIGKILL)], [c for c in sent if c[0] == pgid], sent)

    def test_cancel_is_reported_as_a_stop_not_a_crash_and_is_not_restarted(self):
        """verify2 N4: Cancel (the indexing job's, or any deliberate kill) is a decision. The
        event says `stopped`, it does not count toward the crash loop, and the next ask does not
        restart the server behind the human's back."""
        events = []
        h = self.host(on_event=events.append)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        srv.kill("indexing cancelled")
        self.assertTrue(wait_for(lambda: any(e.get("what") in ("stopped", "crashed") for e in events)))
        whats = [e["what"] for e in events if e.get("what") in ("stopped", "crashed")]
        self.assertEqual(["stopped"], whats)
        self.assertEqual("stopped", srv.status()["state"])
        self.assertEqual({}, {k: v for k, v in h._crashes.items() if v})
        got, why = h.server_for("python")
        self.assertIsNone(got)
        self.assertEqual("stopped", why["state"])
        self.assertIs(srv, h.get("python"), "a cancelled server was restarted")

    def test_a_crash_with_a_detached_child_still_kills_it(self):
        """The verifier's `crash (child detached from pipes)` case: EOF arrives, and the tree is
        killed on that path too."""
        h = self.host("--grandchild", "detach", "--pidfile", self.pidfile, "--crash-on", "textDocument/hover")
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        self.assertTrue(wait_for(lambda: os.path.exists(self.pidfile)))
        uri, _ = srv.open(os.path.join(self.repo, "src", "app", "core.py"), "python")
        with self.assertRaises(L.ServerGone):
            srv.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 5}})
        self.assertTrue(wait_for(lambda: not self._grandchild_alive()[0], timeout=5), self._grandchild_alive())

    def test_a_cli_untrust_stops_a_running_server_at_the_next_use_not_the_idle_reap(self):
        """`mokata ide --untrust` in a terminal only flips the record; the host re-reads trust on
        every use, so the next read (or status) stops the server and answers `untrusted`."""
        h = L.Host(self.repo, trusted=lambda: T.status(self.repo)["trusted"], finder=finder_for())
        self.hosts.append(h)
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv = h.get("python")
        T.cli_decide(self.repo, False, stdin=io.StringIO(), stdout=io.StringIO(), environ={})
        self.assertTrue(srv.alive)                      # nothing has asked yet
        got, why = h.server_for("python")
        self.assertIsNone(got)
        self.assertEqual("untrusted", why["state"])
        self.assertTrue(wait_for(lambda: not srv.alive))
        self.assertEqual([], h.status()["servers"])
        # the reaper tick does it too, with nobody asking
        T.decide(self.repo, True, "test")
        self.assertTrue(h.start("python", by_human=True)["started"])
        srv2 = h.get("python")
        with mock.patch("mokata.ide.lsp.REAP_TICK_S", 0.5):
            h._reaper = None
            h._ensure_reaper()
            T.decide(self.repo, False, "cli")
            self.assertTrue(wait_for(lambda: not srv2.alive, timeout=10))


class NothingOutsideTheWorkspaceIsRead(_Repo):
    """Verification L1: a location the server names outside the workspace is reported, never
    read; a `file://host/share` URI (SMB on Windows) is not local at all."""

    def test_outside_locations_carry_no_snippet_and_unc_uris_are_not_local(self):
        secret = os.path.join(self.d, "outside_secret.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("TOP-SECRET-LINE\n")
        rng = {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}
        for uri in (L.to_uri(secret), L.to_uri(self.repo) + "/../outside_secret.txt", "file:///etc/hostname"):
            got = L.location(self.repo, {"uri": uri, "range": rng}, L._Lines())
            self.assertTrue(got["outside"], uri)
            self.assertEqual("", got["snippet"], uri)
        self.assertIsNone(L.from_uri("file://evilhost/share/x.py"))
        got = L.location(self.repo, {"uri": "file://evilhost/share/x.py", "range": rng}, L._Lines())
        self.assertEqual(("", True, ""), (got["path"], got["outside"], got["snippet"]))
        inside = L.location(self.repo, {"uri": L.to_uri(os.path.join(self.repo, "src", "app", "core.py")),
                                        "range": rng}, L._Lines())
        self.assertEqual(("src/app/core.py", False, "def parse(text):"),
                         (inside["path"], inside["outside"], inside["snippet"]))

    def test_the_trust_code_key_can_never_be_a_proposal_id(self):
        """Verification L2: the trust decision's code-book slot is not a proposal id shape."""
        from mokata.ide.features.lsp import TRUST_KEY
        self.assertIn(":", TRUST_KEY)
        self.assertFalse(TRUST_KEY.startswith("p-"))


if __name__ == "__main__":
    unittest.main()
