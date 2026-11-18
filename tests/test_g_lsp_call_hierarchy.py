"""0.0.28 G `ID-LSP-CALL-HIERARCHY` — "who calls this?" / "what does this call?" from the project's own
language server, through the read-only bridge, shown in the Find Usages box.

Business rules — what the person at the IDE can rely on (rule: docs/build/handoff/0028-evidence/G/G.rule.md):
  * ONE DOOR, THREE NEW READS. The bridge may now send `textDocument/prepareCallHierarchy`,
    `callHierarchy/incomingCalls` and `callHierarchy/outgoingCalls` — nothing else new, nothing that writes.
  * UNTRUSTED MEANS NOTHING SPAWNED AND NOTHING SENT; the call graph answers callers AND callees, labelled
    (0.0.29 A, `LSP-CALLS-GRAPH-CALLEES`: G-Q4's empty callees are gone). A revoke while a question runs ends it with the untrusted answer: no further
    request is sent, the server is stopped, no language-server row survives.
  * THE ITEM NEVER CROSSES THE WIRE. A CallHierarchyItem (its opaque `data` included) goes back to the same
    server in the same job; the page never sees one and can never send one (any other parameter is a 400).
  * NO SERVER VALUE IS TRUSTED FOR SHAPE: malformed entries are dropped and COUNTED; a location outside the
    workspace (a UNC share, a host URI, `..`) is shown and never read.
  * BOUNDED AND SAID: at most CALLS_MAX rows per expansion with the exact total; no deadline (Cancel instead).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p0_ide_api_contract import ORIGIN, _write
from test_p24_ide_lsp_readonly import _Repo, boot, finder_for, lsp_get, wait_for

from mokata.ide import lsp as L
from mokata.ide import trust as T
from mokata.ide.features import lsp as F
from mokata.knowledge import cancel as C

DATA = "fake-data-7f3a"
CORE = "src/app/core.py"


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def calls(c, path, line, direction, col=None, extra=""):
    url = f"/api/lsp/calls?path={path}&line={line}&dir={direction}" + (f"&col={col}" if col else "") + extra
    return lsp_get(c, url)


def rows_of(ans):
    return [r for g in ans.get("groups", []) for r in g["rows"]]


class _Calls(_Repo):
    """A trusted repo with a started fake server whose methods are logged."""

    KNOBS: tuple = ()

    def setUp(self):
        super().setUp()
        T.decide(self.repo, True, "test")
        self.log = os.path.join(self.d, "methods.log")

    def seen(self):
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read().split()
        except OSError:
            return []

    def serve(self, *knobs):
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        state.lsp.finder = finder_for("--index-seconds", "0.1", "--log", self.log, *knobs)
        r = c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        srv = state.lsp.get("python")
        self.assertTrue(wait_for(srv.is_ready))
        return c, state, srv


# ============================================================== untrusted: nothing spawned, nothing sent

class UntrustedMeansNothingSent(_Repo):

    def test_untrusted_calls_spawn_nothing_send_nothing_and_the_graph_answers_callers(self):
        """Untrusted: `/api/lsp/calls` starts no process; callers AND callees come from the call
        graph, labelled `source: graph` (0.0.29 A, `LSP-CALLS-GRAPH-CALLEES`)."""
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        with mock.patch.object(L.Server, "start") as start, \
                mock.patch("mokata.ide.lsp.spawn_jobbed") as spawn:
            cin = calls(c, CORE, 1, "in", col=5)
            cout = calls(c, CORE, 7, "out", col=5)
            start.assert_not_called()
            spawn.assert_not_called()
        self.assertEqual(200, cin.status_code, cin.text)
        a = cin.json()
        self.assertEqual("graph", a["source"])
        self.assertEqual("untrusted", a["lsp"]["state"])
        self.assertFalse(a["asked"])
        self.assertEqual("parse", a["symbol"])
        self.assertIn(("src/app/core.py", 8), [(r["path"], r["line"]) for r in rows_of(a)])
        b = cout.json()
        self.assertEqual(("graph", "untrusted", False), (b["source"], b["lsp"]["state"], b["asked"]), b)
        self.assertEqual("total", b["symbol"])
        self.assertEqual(["len", "parse"], [r["name"] for r in rows_of(b)])
        self.assertEqual([], state.lsp.status()["servers"])

    def test_a_running_server_is_stopped_and_sent_nothing_once_trust_is_revoked(self):
        """A server started while trusted, then the record is revoked outside the window (a CLI
        `--untrust`): the next call question sends it nothing and stops it — even when the file
        asked about has no language server at all (untrusted is the FIRST rung)."""
        T.decide(self.repo, True, "test")
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        state.lsp.finder = finder_for("--index-seconds", "0.1")
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        srv = state.lsp.get("python")
        self.assertTrue(wait_for(srv.is_ready))
        _write(self.repo, "notes/readme.txt", "plain text\n")
        T.decide(self.repo, False, "test")
        before = list(srv.sent)
        a = calls(c, "notes/readme.txt", 1, "in").json()
        self.assertEqual("untrusted", a["lsp"]["state"])
        self.assertTrue(wait_for(lambda: not srv.alive), "the server kept running after the revoke")
        self.assertFalse([m for m in srv.sent[len(before):] if "allHierarchy" in m], srv.sent)


# ============================================================== revoke while the question runs

class RevokeMidJob(_Calls):

    def test_a_revoke_between_prepare_and_the_calls_request_sends_no_further_request(self):
        """The trust re-read before each request, against the REAL record (verify-G L-7): the
        record is revoked the moment prepare's answer lands (a CLI `--untrust` in that window);
        the incoming request is never sent, the answer is the untrusted one, the server stops."""
        c, state, srv = self.serve()
        real = srv.request

        def request(method, params, *a, **kw):
            res = real(method, params, *a, **kw)
            if method == "textDocument/prepareCallHierarchy":
                T.decide(self.repo, False, "test")
            return res
        with mock.patch.object(srv, "request", request):
            a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual(("graph", "untrusted", False), (a["source"], a["lsp"]["state"], a["asked"]), a)
        self.assertIn("textDocument/prepareCallHierarchy", srv.sent)
        self.assertNotIn("callHierarchy/incomingCalls", srv.sent)
        self.assertTrue(wait_for(lambda: not srv.alive), "the server kept running after the revoke")

    def test_revoking_in_the_window_while_the_server_is_slow_ends_the_job_untrusted(self):
        """The server sits on the incoming-calls request; the human revokes trust in the window:
        the job finishes with the untrusted answer (graph callers, no language-server rows) and
        the server is stopped."""
        c, state, srv = self.serve("--slow", "callHierarchy/incomingCalls")
        r = c.get(f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in&sync_ms=0")
        self.assertEqual(202, r.status_code, r.text)
        job = r.json()["job"]
        self.assertTrue(wait_for(lambda: "callHierarchy/incomingCalls" in self.seen()))
        rv = c.post("/api/lsp/trust", json={"decision": "revoke"}, headers=ORIGIN)
        self.assertEqual(200, rv.status_code, rv.text)
        a = lsp_get(c, f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in&job={job}").json()
        self.assertEqual("untrusted", a["lsp"]["state"], a)
        self.assertEqual("graph", a["source"])
        self.assertFalse(a["asked"])
        self.assertFalse(srv.alive)


# ============================================================== the round trip

class RoundTrip(_Calls):

    def test_callers_and_callees_of_a_function_one_level_with_positions_and_snippets(self):
        """Callers of `parse` (on its name): the root item `parse`, its callers `total` and
        `test_parse` with 1-based positions, snippets, call counts and the test chip. Callees of
        `total`: `parse`. The server's `data` went back untouched (the fake refuses otherwise)."""
        c, state, srv = self.serve()
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual(("lsp", "fake-lsp", True, True), (a["source"], a["lsp"]["server"], a["asked"], a["callable"]))
        self.assertEqual(1, len(a["groups"]))
        root = a["groups"][0]["item"]
        self.assertEqual(("parse", CORE, 1, 5), (root["name"], root["path"], root["line"], root["col"]))
        got = sorted((r["name"], r["path"], r["line"], r["col"], r["calls"], r["test"]) for r in rows_of(a))
        self.assertEqual([("test_parse", "tests/test_core.py", 4, 5, 1, True),
                          ("total", CORE, 7, 5, 1, False)], got)
        self.assertEqual("def total(text):", [r for r in rows_of(a) if r["name"] == "total"][0]["snippet"])
        self.assertEqual((2, False, 0, True), (a["total"], a["truncated"], a["skipped"], a["complete"]))
        b = calls(c, CORE, 7, "out", col=5).json()
        self.assertEqual([("parse", CORE, 1, 5)], [(r["name"], r["path"], r["line"], r["col"]) for r in rows_of(b)])
        self.assertIn("callHierarchy/outgoingCalls", srv.sent)

    def test_without_a_column_the_enclosing_function_is_asked_about(self):
        """The palette sends the caret LINE: inside `total`'s body, the question is about `total`;
        on a line no function encloses, nothing is asked and `callable` is false."""
        c, state, srv = self.serve()
        b = calls(c, CORE, 8, "out").json()
        self.assertEqual("total", b["groups"][0]["item"]["name"])
        self.assertEqual(["parse"], [r["name"] for r in rows_of(b)])
        _write(self.repo, "src/app/flat.py", "X = 1\n\n\ndef f():\n    return X\n")
        n = len(srv.sent)
        e = calls(c, "src/app/flat.py", 1, "in").json()
        self.assertIs(False, e["callable"])
        self.assertEqual([], e["groups"])
        self.assertNotIn("textDocument/prepareCallHierarchy", srv.sent[n:])

    def test_overloads_are_grouped_and_recursion_lists_the_function_itself(self):
        """Two definitions answer the prepare: each its own group. A self-call lists itself."""
        _write(self.repo, "src/app/a.py", "def dup():\n    return 1\n\n\ndef use_a():\n    return dup()\n")
        _write(self.repo, "src/app/b.py", "def dup():\n    return 2\n\n\ndef rec(n):\n    return rec(n - 1) + dup()\n")
        c, state, srv = self.serve()
        a = calls(c, "src/app/a.py", 1, "in", col=5).json()
        self.assertEqual(2, len(a["groups"]))
        self.assertEqual({"src/app/a.py", "src/app/b.py"}, {g["item"]["path"] for g in a["groups"]})
        self.assertEqual((2, 2), (a["items_named"], a["items_asked"]))
        r = calls(c, "src/app/b.py", 5, "in", col=5).json()
        self.assertIn("rec", [x["name"] for x in rows_of(r)])

    def test_a_cycle_expanded_five_levels_answers_each_level_and_terminates(self):
        """ping -> pong -> ping: expanding the only caller five times answers every level (the
        API is stateless — one level per question — so a cycle cannot loop)."""
        _write(self.repo, "src/app/cyc.py",
               "def ping(n):\n    return pong(n - 1)\n\n\ndef pong(n):\n    return ping(n - 1)\n")
        c, state, srv = self.serve()
        path, line, col = "src/app/cyc.py", 1, 5
        names = []
        for _ in range(5):
            a = calls(c, path, line, "in", col=col).json()
            rs = rows_of(a)
            self.assertEqual(1, len(rs), a)
            names.append(rs[0]["name"])
            path, line, col = rs[0]["path"], rs[0]["line"], rs[0]["col"]
        self.assertEqual(["pong", "ping", "pong", "ping", "pong"], names)


# ============================================================== a server without the capability

class NoFunctionHere(_Calls):

    def test_a_line_no_function_encloses_is_not_labelled_a_language_server_answer(self):
        """verify-G L-4: the mokata resolver found no function, nobody asked the server — the
        answer says so (`source: none`, `asked: false`), never `source: lsp`."""
        _write(self.repo, "src/app/consts.py", "LIMIT = 10\n")
        c, state, srv = self.serve()
        before = list(srv.sent)
        a = calls(c, "src/app/consts.py", 1, "in").json()
        self.assertEqual(("none", False, False), (a["source"], a["asked"], a["callable"]), a)
        self.assertNotEqual("lsp", a["basis"])
        self.assertIn("no function encloses line 1", a["note"])
        self.assertFalse([m for m in srv.sent[len(before):] if "allHierarchy" in m])


class NoProvider(_Calls):

    def test_a_server_without_call_hierarchy_is_asked_nothing_and_the_graph_answers(self):
        c, state, srv = self.serve("--no-call-hierarchy")
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual(("graph", "no-call-hierarchy", "fake-lsp"), (a["source"], a["lsp"]["state"], a["lsp"]["server"]))
        self.assertIn("total", [r["name"] for r in rows_of(a)])
        b = calls(c, CORE, 7, "out", col=5).json()
        self.assertEqual(("graph", "no-call-hierarchy", False), (b["source"], b["lsp"]["state"], b["asked"]))
        self.assertIn("parse", [r["name"] for r in rows_of(b)])
        self.assertIn("call hierarchy", b["lsp"]["note"])
        self.assertFalse([m for m in self.seen() if "allHierarchy" in m], self.seen())


# ============================================================== hostile replies

class HostileServer(_Calls):

    def setUp(self):
        super().setUp()
        self.outside = os.path.join(self.d, "outside_secret.py")
        with open(self.outside, "w", encoding="utf-8") as fh:
            fh.write("SECRET_OUTSIDE = 'SECRET-OUTSIDE'\n" * 40)   # every line, past any row's line

    def test_malformed_entries_are_dropped_and_counted_and_outside_ones_never_read(self):
        c, state, srv = self.serve("--malformed", "entries", "--outside", self.outside)
        r = calls(c, CORE, 1, "in", col=5)
        self.assertNotIn("SECRET-OUTSIDE", r.text)
        a = r.json()
        self.assertEqual("lsp", a["source"])
        self.assertEqual(12, a["skipped"], a)
        self.assertFalse(a["complete"])
        rs = rows_of(a)
        self.assertEqual(6, len(rs))
        outside = [x for x in rs if x["outside"]]
        self.assertEqual(4, len(outside), rs)
        for x in outside:
            self.assertEqual("", x["snippet"])
            self.assertFalse(x["col_exact"])
        shown = {x["name"][:30]: x["shown"] for x in outside}
        self.assertEqual("file://evilhost/share/x.py", shown["unc_caller"])
        self.assertTrue(all(len(x["name"]) <= 300 for x in rs))
        self.assertEqual(1, [x for x in rs if x["name"].startswith("<script>")][0]["calls"])

    def test_a_reply_that_is_not_a_list_and_garbage_prepare_items_are_counted(self):
        c, state, srv = self.serve("--malformed", "notlist")
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual((1, 0, [], False), (a["skipped"], a["total"], rows_of(a), a["complete"]))
        state.close()
        c, state, srv = self.serve("--malformed", "prepare")
        b = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual((1, 4), (b["items_named"], b["skipped"]))
        self.assertEqual(2, len(rows_of(b)))

    def test_a_uri_that_does_not_parse_is_skipped_and_counted_and_the_good_rows_stay(self):
        """verify-G M-1: `file://[abc/x.py` makes `urlsplit` RAISE. One such prepare item and one
        such incoming entry are malformed like any other: skipped, counted, and the answer keeps
        every good row — the job does not die and take the good rows with it."""
        c, state, srv = self.serve("--malformed", "baduri")
        r = calls(c, CORE, 1, "in", col=5)
        self.assertEqual(200, r.status_code, r.text)
        a = r.json()
        self.assertEqual(("lsp", True), (a["source"], a["asked"]), a)
        self.assertEqual((1, 2), (a["items_named"], a["skipped"]), a)
        self.assertEqual(["test_parse", "total"], sorted(x["name"] for x in rows_of(a)))
        self.assertFalse(a["complete"])

    def test_a_definition_location_whose_uri_does_not_parse_is_skipped_and_counted(self):
        """verify-G M-3: the same parse failure in `location()` failed the whole definition
        answer. One bad Location is skipped and counted; the good one stays."""
        c, state, srv = self.serve("--malformed", "baduri")
        r = lsp_get(c, f"/api/lsp/definition?path={CORE}&line=8&col=16")
        self.assertEqual(200, r.status_code, r.text)
        a = r.json()
        self.assertEqual("lsp", a["source"], a)
        self.assertEqual(1, a["skipped"], a)
        self.assertEqual([(CORE, 1)], [(x["path"], x["line"]) for x in a["results"]])

    def test_a_find_usages_location_whose_uri_does_not_parse_is_skipped_and_counted(self):
        """verify-G M-3 through Find Usages' LSP path (`usages._lsp_usages`): one bad Location
        no longer fails the answer; it is counted and said, the good usages stay."""
        import shutil
        import tempfile
        from mokata.ide import usages as U
        c, state, srv = self.serve("--malformed", "baduri")
        srv.open(os.path.join(self.repo, *CORE.split("/")), "python")
        d, _ = U.definition_at(self.repo, CORE, 1)
        w = U.SpillWriter(tempfile.mkdtemp(prefix="mk-g-usages-"), "usages")
        self.addCleanup(shutil.rmtree, os.path.dirname(w.path), True)
        head = U.find_usages(self.repo, self.repo, d, w, state.lsp)
        w.close()
        self.assertEqual("lsp", head["basis"], head)
        self.assertEqual(1, head["skipped"], head)
        self.assertIn("malformed", head["label"])
        with open(w.path, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh]
        self.assertIn((CORE, 8), [(x[0], x[1]) for x in rows])

    def test_a_server_error_says_its_code_never_its_text_and_the_graph_answers(self):
        c, state, srv = self.serve("--malformed", "error")
        r = calls(c, CORE, 1, "in", col=5)
        self.assertEqual(200, r.status_code, r.text)
        self.assertNotIn("SERVER-SECRET", r.text)
        a = r.json()
        self.assertEqual(("graph", "server-error", -32603), (a["source"], a["lsp"]["state"], a["lsp"]["code"]))

    def test_windows_and_unc_uri_forms_are_inside_or_outside_as_the_platform_reads_them(self):
        """A repo file in the spelling real servers send (`file:///c%3A/...` on Windows) is inside
        and read; `file://host/share` and `file:////host/share` are never local."""
        from test_p24_ide_lsp_readonly import HERE  # noqa: F401
        abspath = os.path.join(self.repo, *CORE.split("/"))
        uri = L.to_uri(abspath)
        if os.name == "nt":
            drive, rest = uri[len("file:///"):].split(":", 1)
            uri = "file:///%s%%3A%s" % (drive.lower(), rest)
        sel = {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 9}}
        base = {"name": "parse", "range": sel, "selectionRange": sel}
        lines = L.CallLines()
        lines.want(abspath, 0)
        lines.load()
        inside = L.call_row(self.repo, dict(base, uri=uri), lines)
        self.assertEqual((CORE, False, "def parse(text):"), (inside["path"], inside["outside"], inside["snippet"]))
        for bad in ("file://evilhost/share/app/core.py", "file:////evilhost/share/app/core.py",
                    "file://evilhost/c$/x.py"):
            row = L.call_row(self.repo, dict(base, uri=bad), L.CallLines())
            self.assertTrue(row["outside"], bad)
            self.assertEqual("", row["snippet"])


# ============================================================== the item never crosses the wire

class ItemNeverCrossesTheWire(_Calls):

    def test_the_answer_carries_no_item_and_no_data(self):
        c, state, srv = self.serve()
        for d in ("in", "out"):
            r = calls(c, CORE, 1 if d == "in" else 7, d, col=5)
            self.assertNotIn(DATA, r.text)
            self.assertNotIn("data", set(_walk_keys(r.json())))
            self.assertNotIn("uri", set(_walk_keys(r.json())))

    def test_a_page_supplied_item_data_or_unknown_parameter_is_refused_before_anything_runs(self):
        c, state, srv = self.serve()
        n = len(srv.sent)
        for extra in ("&item=%7B%22name%22%3A%22x%22%7D", "&data=%7B%7D", "&method=x", "&dir=in"):
            r = c.get(f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in{extra}")
            self.assertEqual(400, r.status_code, (extra, r.text))
        for bad_dir in ("sideways", "", "IN"):
            r = c.get(f"/api/lsp/calls?path={CORE}&line=1&col=5&dir={bad_dir}")
            self.assertEqual(400, r.status_code, (bad_dir, r.text))
        self.assertEqual(n, len(srv.sent))


# ============================================================== positions

class FrameCap(_Calls):
    """verify-G M-2: the reader refuses a frame over `FRAME_MAX` — announced by its header, or a
    header that never ends — and the server is ended with a reason the answer carries, instead of
    buffering without bound. The cap is patched small here; the fake sends exactly cap / cap+1."""

    CAP = 65536

    def setUp(self):
        super().setUp()
        p = mock.patch.object(L, "FRAME_MAX", self.CAP)
        p.start()
        self.addCleanup(p.stop)

    def test_a_frame_exactly_at_the_cap_is_read(self):
        c, state, srv = self.serve("--frame", str(self.CAP))
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual("lsp", a["source"], a)
        self.assertEqual(["test_parse", "total"], sorted(x["name"] for x in rows_of(a)))

    def test_a_frame_one_byte_over_the_cap_ends_the_server_and_says_why(self):
        c, state, srv = self.serve("--frame", str(self.CAP + 1))
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual(("graph", "crashed"), (a["source"], a["lsp"]["state"]), a)
        self.assertIn("frame of 65537 bytes", a["lsp"]["note"])
        self.assertIn("65536", a["lsp"]["note"])
        self.assertTrue(wait_for(lambda: not srv.alive))

    def test_a_header_that_never_ends_past_the_cap_ends_the_server_and_says_why(self):
        c, state, srv = self.serve("--headerless", str(self.CAP + 1))
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual(("graph", "crashed"), (a["source"], a["lsp"]["state"]), a)
        self.assertIn("no frame header", a["lsp"]["note"])
        self.assertTrue(wait_for(lambda: not srv.alive))

    def test_the_reason_survives_the_exit_watcher_that_the_kill_itself_wakes(self):
        """W5 item 1 / 0.0.29 A `W5-LSP-FRAMECAP-TEST-NOT-FORCED` — the REAL exit watcher is
        controlled (a synthetic `_gone` thread left the real one racing: 2 of ~21 pre-fix runs
        passed). The watcher runs the real `wait_exit`, then holds until the reader has ended the
        tree, then calls `_gone("exited")`. The reader's `close` lets it go and waits until the
        watcher has either returned from `_gone` (it won the claim) or entered `stopped.wait()` (it
        lost the claim and waits for the reader). The watcher has its whole chance before the
        reader goes on, every run: the reason the reader decided on (the frame cap) must be the
        one the answer carries."""
        gate, watcher_over = threading.Event(), threading.Event()

        def watch_exit(srv):
            tree = srv.tree
            if tree is None:
                return
            try:
                tree.wait_exit()
            except OSError:
                return
            gate.wait()
            try:
                srv._gone("exited")
            finally:
                watcher_over.set()
        p = mock.patch.object(L.Server, "_watch_exit", watch_exit)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(gate.set)
        c, state, srv = self.serve("--frame", str(self.CAP + 1))

        class Stopped(threading.Event):
            def wait(self, timeout=None):
                if threading.current_thread().name.endswith("-wait"):
                    watcher_over.set()                # the watcher lost the claim: it waits
                return super().wait(timeout)
        srv.stopped = Stopped()
        real_close, raced = srv.tree.close, []

        def close():
            real_close()                              # the real end of the real tree
            if threading.current_thread().name == f"mokata-lsp-{srv.language}" and not raced:
                raced.append(True)
                gate.set()
                watcher_over.wait()                   # an event the test controls, no clock
        srv.tree.close = close
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual([True], raced, "the race was not forced: the reader never ended the tree")
        self.assertEqual(("graph", "crashed"), (a["source"], a["lsp"]["state"]), a)
        self.assertIn("frame of 65537 bytes", a["lsp"]["note"])
        self.assertNotIn("exited", a["lsp"]["note"])
        self.assertTrue(wait_for(lambda: not srv.alive))


class GoneOnce(_Calls):
    """0.0.29 A `W5-LSP-GONE-LATER-CALLER-RETURNS-EARLY` — every stop / kill / exit path returns
    only when the server is over: `stopped` set and every pending question answered."""

    def _pending_hover(self, srv):
        box = {}

        def ask():
            try:
                srv.request("textDocument/hover", {"textDocument": {"uri": L.to_uri(
                    os.path.join(self.repo, *CORE.split("/")))}, "position": {"line": 0, "character": 4}})
            except L.ServerGone as exc:
                box["exc"] = exc
        t = threading.Thread(target=ask, daemon=True)
        t.start()
        self.assertTrue(wait_for(lambda: "textDocument/hover" in self.seen(), timeout=600))
        return t, box

    def test_a_second_kill_returns_only_after_the_first_has_ended_everything(self):
        c, state, srv = self.serve("--slow", "textDocument/hover")
        asker, box = self._pending_hover(srv)
        hold, inside = threading.Event(), threading.Event()
        self.addCleanup(hold.set)
        real_close, first = srv.tree.close, []
        entered = threading.Event()

        class Stopped(threading.Event):
            def wait(self, timeout=None):
                if threading.current_thread().name == "k2":
                    entered.set()
                return super().wait(timeout)
        srv.stopped = Stopped()

        def close():
            if not first:
                first.append(threading.current_thread().name)
                inside.set()
                hold.wait()
            real_close()
        srv.tree.close = close
        pending = list(srv._pending.values())
        self.assertEqual(["textDocument/hover"], [p["method"] for p in pending])
        t1 = threading.Thread(target=srv.kill, args=("first-reason",), daemon=True, name="k1")
        t1.start()
        self.assertTrue(inside.wait(600))
        seen = {}

        def second():
            srv.kill("second-reason")
            seen["stopped"] = srv.stopped.is_set()
            seen["answered"] = all(p["done"].is_set() and p["error"] is not None for p in pending)
        t2 = threading.Thread(target=second, daemon=True, name="k2")
        t2.start()
        self.assertTrue(wait_for(lambda: entered.is_set() or not t2.is_alive(), timeout=600))
        self.assertTrue(t2.is_alive(), "the second kill returned while the first was still ending the server")
        self.assertFalse(srv.stopped.is_set())
        hold.set()
        t1.join(600)
        t2.join(600)
        asker.join(600)
        self.assertEqual({"stopped": True, "answered": True}, seen)
        self.assertEqual(["k1"], first)
        self.assertIn("first-reason", str(box["exc"]))

    def test_a_close_that_raises_still_ends_the_pending_questions_and_sets_stopped(self):
        c, state, srv = self.serve("--slow", "textDocument/hover")
        asker, box = self._pending_hover(srv)
        real_close = srv.tree.close
        self.addCleanup(real_close)
        # Runs FIRST (cleanups are LIFO): if the code under test never set `stopped` (the M10
        # mutant), the teardown's own stop would be a LATER `_gone` caller waiting on it for ever —
        # the assertion below reds, and this keeps the red from turning into a hang.
        self.addCleanup(srv.stopped.set)

        def close():
            raise OSError("close failed")
        srv.tree.close = close
        with self.assertRaises(OSError):
            srv.kill("boom")
        srv.tree.close = real_close
        self.assertTrue(srv.stopped.is_set())
        asker.join(600)
        self.assertIsInstance(box.get("exc"), L.ServerGone)

    def test_an_idle_reap_returns_only_after_the_exit_is_published_and_alive_never_leads_it(self):
        """verify-B2 M2, FORCED (events, never a delay): the idle reap's `shutdown`/`exit` ends the
        server, and its end has TWO first-caller candidates — the exit watcher (`-wait`) and the
        reader at EOF. Either may claim `_gone` first; the CI run 43896526038 (ubuntu shard 4/4)
        caught the reader first, which an earlier version of this cell (holding only the watcher)
        mistook for an early return. Both orders are forced here: the other thread's `_gone` waits
        until the chosen first caller is inside `close`. Every non-reap `close` is held, so in that
        window nothing has published `exit_code`/`stopped`: `alive` must not say "over" and the
        reap — a later caller — must not return. Released: the reap returns with `exit_code` 0 and
        `status` `stopped`."""
        for first_name in ("mokata-lsp-python-wait", "mokata-lsp-python"):
            with self.subTest(first=first_name):
                self._reap_waits_for_the_publish(first_name)

    def _reap_waits_for_the_publish(self, first_name):
        c, state, srv = self.serve()
        h = state.lsp
        hold, inside, first_in = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(hold.set)
        self.addCleanup(first_in.set)
        real_close, real_kill, real_gone = srv.tree.close, srv.kill, srv._gone
        entered = threading.Event()
        closers = []
        enders = ("mokata-lsp-python-wait", "mokata-lsp-python")

        class Stopped(threading.Event):
            def wait(self, timeout=None):
                if threading.current_thread().name == "reap":
                    entered.set()
                return super().wait(timeout)
        srv.stopped = Stopped()

        def gone(why, unknown=None):
            name = threading.current_thread().name
            if name in enders and name != first_name:
                first_in.wait()               # the chosen first caller claims `_gone` first
            real_gone(why, unknown=unknown)

        def close():
            name = threading.current_thread().name
            if name != "reap":
                closers.append(name)
                if name == first_name:
                    first_in.set()
                inside.set()                  # an end is under way; nothing is published
                hold.wait()
            real_close()

        def kill(why="killed"):
            inside.wait()                     # the reap's own kill comes SECOND, always
            real_kill(why)
        srv.tree.close, srv.kill, srv._gone = close, kill, gone
        t = threading.Thread(target=h.reap, kwargs={"now": srv.last_used + h.idle_s + 1},
                             daemon=True, name="reap")
        t.start()
        self.assertTrue(inside.wait(600), "the server never exited on shutdown/exit")
        self.assertEqual(first_name, closers[0], "the forced first caller did not end it first")
        self.assertTrue(srv.alive, "`alive` said the server is over before anything published it")
        self.assertTrue(wait_for(lambda: entered.is_set() or not t.is_alive(), timeout=600))
        self.assertTrue(t.is_alive(), "the reap returned before the exit was published")
        hold.set()
        t.join(600)
        self.assertFalse(t.is_alive())
        self.assertEqual((False, 0, "stopped"), (srv.alive, srv.exit_code, srv.status()["state"]))

    def test_a_watcher_the_os_will_not_answer_still_publishes_a_crash(self):
        """`alive` reads only the published state, so the exit watcher must publish even when the
        OS wait fails: it falls back to asking each slice. Forced: the watcher's `wait_exit` raises
        — an OSError, and (verify-A r3 R3-4) anything else: a watcher that dies leaves a dead server
        "alive" for ever."""
        from mokata import quiet_spawn as Q
        real_wait = Q.Jobbed.wait_exit
        for exc in (OSError("the OS would not say"), ValueError("a closed kqueue")):
            with self.subTest(type(exc).__name__):
                def wait_exit(self_, timeout=None, exc=exc):
                    if threading.current_thread().name.endswith("-wait"):
                        raise exc
                    return real_wait(self_, timeout)
                with mock.patch.object(Q.Jobbed, "wait_exit", wait_exit):
                    c, state, srv = self.serve()
                    srv.proc.kill()                   # a crash nobody asked for
                    self.assertTrue(wait_for(lambda: not srv.alive, timeout=60),
                                    "a dead server stayed alive")
                self.assertEqual("crashed", srv.status()["state"])

    def test_an_unknown_exit_is_published_as_unknown_everywhere_and_never_as_our_kill(self):
        """verify-A r5 R5-2, end to end on a real server: the watcher's wait AND poll fail (a bug),
        so the exit cannot be known. Our own kill then reaps a real -9 — which must NOT be published
        as the server's exit: `status()`, the `crashed` event and a pending question's reason all
        say "exit code unknown", `exit_code` is None, and a later stop/kill never writes over it.
        Event-driven: the poll fails only once the question is pending."""
        from mokata import quiet_spawn as Q
        real_wait, real_poll = Q.Jobbed.wait_exit, Q.Jobbed.poll_exit
        go = threading.Event()

        def wait_exit(self_, timeout=None):
            if threading.current_thread().name.endswith("-wait"):
                raise TypeError("a bug in wait_exit")
            return real_wait(self_, timeout)

        def poll_exit(self_):
            if threading.current_thread().name.endswith("-wait"):
                go.wait()
                raise AttributeError("a bug in poll_exit")
            return real_poll(self_)
        self.addCleanup(go.set)
        with mock.patch.object(Q.Jobbed, "wait_exit", wait_exit), \
                mock.patch.object(Q.Jobbed, "poll_exit", poll_exit):
            c, state, srv = self.serve("--slow", "textDocument/hover")
            events, evented = [], threading.Event()
            real_on = srv.on_event

            def on_event(e):
                events.append(e)
                real_on(e)
                if e.get("what") == "crashed":
                    evented.set()
            srv.on_event = on_event
            asker, box = self._pending_hover(srv)
            go.set()
            self.assertTrue(srv.stopped.wait(60), "the unknown end was never published")
            self.assertTrue(evented.wait(60), "no crashed event")
            asker.join(60)
        st = srv.status()
        self.assertEqual(("crashed", None, True), (st["state"], st["exit_code"], st["exit_unknown"]))
        crashed = [e for e in events if e.get("what") == "crashed"]
        self.assertEqual(1, len(crashed), events)
        self.assertEqual((None, True), (crashed[0]["exit_code"], crashed[0]["exit_unknown"]))
        reason = str(box.get("exc"))
        self.assertIn("exit code unknown", reason)
        self.assertIn("AttributeError", reason)
        self.assertNotIn("exit -", reason)
        srv.kill()                                # a later reap never writes our -9 over it
        self.assertEqual((None, True), (srv.exit_code, srv.exit_unknown))
        js = open(os.path.join(os.path.dirname(os.path.abspath(L.__file__)), "static", "features",
                               "lsp.js"), encoding="utf-8").read()
        self.assertIn('e.exit_unknown ? "exit code unknown"', js)   # the UI says it too


class _Boom(BaseException):
    """A BaseException that is neither KeyboardInterrupt nor SystemExit."""


class WatcherPublishes(unittest.TestCase):
    """verify-A r4 R4-1 / r5 R5-1..R5-4 — `Server._watch_exit`, designed once against the matrix.
    Each row: the end is published EXACTLY ONCE, the exit is unknown exactly when it cannot be
    known, and the right notice is said. Event-driven: the test waits on the watcher thread's end
    (`join`) and on what `_gone` recorded — never a sleep."""

    def setUp(self):
        from mokata import degrade as D
        self.printed = []
        D.reset_degrade_notices()
        self.addCleanup(D.reset_degrade_notices)
        for p in (mock.patch.object(D, "_stderr", self.printed.append),
                  mock.patch.object(L, "READ_POLL_S", 0.001)):
            p.start()
            self.addCleanup(p.stop)
        self.hooked = []
        p = mock.patch.object(threading, "excepthook", lambda a: self.hooked.append(a.exc_type))
        p.start()
        self.addCleanup(p.stop)

    def _fake(self, wait, polls, gone=None, pid=4242, language="python"):
        """`wait`: an exception for `wait_exit` to raise (None: it returns). `polls`: one entry per
        `poll_exit` call — an exception to raise or a value to return; the last repeats."""
        import types
        calls = {"poll": 0}

        def wait_exit():
            if wait is not None:
                raise wait

        def poll_exit():
            calls["poll"] += 1
            r = polls[min(calls["poll"], len(polls)) - 1]
            if isinstance(r, BaseException):
                raise r
            return r
        fake = types.SimpleNamespace(
            tree=types.SimpleNamespace(wait_exit=wait_exit, poll_exit=poll_exit),
            stopped=threading.Event(), name=f"srv-{pid}", language=language,
            proc=types.SimpleNamespace(pid=pid), published=[], calls=calls)

        def default_gone(why, unknown=None):
            fake.published.append((why, unknown))
            fake.stopped.set()                # as the real `_gone` does, in its finally
        fake._gone = gone(fake) if gone else default_gone
        return fake

    def _run(self, fake, leaks=()):
        t = threading.Thread(target=L.Server._watch_exit, args=(fake,), daemon=True)
        t.start()
        t.join(60)
        self.assertFalse(t.is_alive(), "the watcher parked")
        # An Exception is HANDLED (said and published), never leaked to the thread; only a
        # BaseException keeps going after the publish.
        self.assertEqual(list(leaks), self.hooked)
        return fake

    def _said(self, kind):
        return [m for m in self.printed if f"lsp-exit-watch/{kind}/" in m]

    def test_oserror_then_success_publishes_a_known_end(self):
        f = self._run(self._fake(OSError("no wait"), [OSError("once"), 0]))
        self.assertEqual([("exited", None)], f.published)
        self.assertTrue(any("OSError" in m for m in self._said("wait-failed")), self.printed)
        self.assertEqual([], self._said("gave-up"))

    def test_oserror_25_times_publishes_unknown_after_exactly_the_bound(self):
        f = self._run(self._fake(OSError("no wait"), [OSError("never")]))
        self.assertEqual(L.WATCH_POLL_MISSES, f.calls["poll"])
        self.assertEqual(1, len(f.published))
        self.assertIn("OSError", f.published[0][1] or "")
        self.assertEqual(1, len(self._said("gave-up")), self.printed)

    def test_a_non_oserror_is_not_retried(self):
        """R5-4: a bug is not an OS refusal — one poll, then the unknown end."""
        f = self._run(self._fake(TypeError("a bug in wait"), [AttributeError("a bug in poll")]))
        self.assertEqual(1, f.calls["poll"])
        self.assertEqual(1, len(f.published))
        self.assertIn("AttributeError", f.published[0][1] or "")
        self.assertTrue(any("TypeError" in m for m in self._said("wait-failed")), self.printed)
        self.assertTrue(any("AttributeError" in m for m in self._said("gave-up")), self.printed)

    def test_a_baseexception_subclass_publishes_unknown_then_propagates(self):
        """Published first, by a FINALLY; then it keeps going — the D5 register allows a
        BaseException only through cleanup-then-re-raise, so the watcher never swallows one."""
        f = self._run(self._fake(_Boom("not an Exception"), [0]), leaks=[_Boom])
        self.assertEqual(1, len(f.published))
        self.assertIn("_Boom", f.published[0][1] or "")
        self.assertEqual(1, len(self._said("gave-up")), self.printed)
        self.assertEqual([_Boom], self.hooked)

    def test_keyboardinterrupt_publishes_unknown_then_propagates(self):
        f = self._run(self._fake(OSError("no wait"), [KeyboardInterrupt()]), leaks=[KeyboardInterrupt])
        self.assertEqual(1, len(f.published))
        self.assertIn("KeyboardInterrupt", f.published[0][1] or "")
        self.assertEqual([KeyboardInterrupt], self.hooked)

    def _notice_raises(self, kind, exc):
        from mokata import degrade as D
        real = D.note_degraded

        def note(subsystem, *a, **k):
            if f"/{kind}/" in subsystem:
                raise exc
            return real(subsystem, *a, **k)
        p = mock.patch.object(L, "note_degraded", note)
        p.start()
        self.addCleanup(p.stop)

    def test_a_raising_notice_on_the_first_path_does_not_skip_the_publish(self):
        self._notice_raises("wait-failed", ValueError("I/O operation on closed file"))
        f = self._run(self._fake(OSError("no wait"), [0]))
        self.assertEqual([("exited", None)], f.published)

    def test_a_raising_notice_on_the_give_up_path_does_not_skip_the_publish(self):
        self._notice_raises("gave-up", ValueError("I/O operation on closed file"))
        f = self._run(self._fake(TypeError("bug"), [AttributeError("bug")]))
        self.assertEqual(1, len(f.published))
        self.assertIn("AttributeError", f.published[0][1] or "")

    def test_a_notice_raising_an_interrupt_on_EVERY_call_never_skips_the_publish(self):
        """verify-A r6 R6-1: NO notice stands between the watcher and the publish. Every notice
        kind (wait-failed, gave-up, publish-failed) raises KeyboardInterrupt — or SystemExit — on
        EVERY call; the end is still published exactly once. Cases A–C are the verifier's
        `probe-r6.py`; the last row reaches the publish-failed notice. The interrupt then leaves the
        thread — after the publish, never instead of it."""
        def raising_publish(fake):
            def g(why, unknown=None):
                fake.published.append((why, unknown))
                fake.stopped.set()
                raise RuntimeError("on_event raised")
            return g
        cases = {
            "A: wait fails with OSError": (OSError("x"), [0], None),
            "B: wait raises SystemExit": (SystemExit(1), [0], None),
            "C: TypeError, then AttributeError": (TypeError("b"), [AttributeError("b")], None),
            "D: the publish raises": (None, [0], raising_publish),
        }
        for interrupt in (KeyboardInterrupt, SystemExit):
            for label, (wait, polls, gone) in cases.items():
                with self.subTest(interrupt=interrupt.__name__, case=label):
                    self.hooked.clear()

                    def note(*a, **k):
                        raise interrupt()
                    with mock.patch.object(L, "note_degraded", note):
                        f = self._run(self._fake(wait, polls, gone=gone), leaks=[interrupt])
                    self.assertEqual(1, len(f.published), f.published)

    def test_a_raising_publish_is_called_once_and_neither_loops_nor_deadlocks(self):
        def gone(fake):
            def g(why, unknown=None):
                fake.published.append((why, unknown))
                fake.stopped.set()            # the real `_gone` sets it in a finally, then events
                raise RuntimeError("on_event raised")
            return g
        f = self._run(self._fake(TypeError("bug"), [AttributeError("bug")], gone=gone))
        self.assertEqual(1, len(f.published))
        self.assertEqual(1, len(self._said("publish-failed")), self.printed)

    def test_two_servers_failing_are_each_said_both_kinds(self):
        """R5-3: notices are keyed per server and per kind; the dedup is the REAL one."""
        for pid in (1001, 1002):
            self._run(self._fake(TypeError(f"bug {pid}"), [AttributeError(f"bug {pid}")], pid=pid))
        self.assertEqual(2, len(self._said("wait-failed")), self.printed)
        self.assertEqual(2, len(self._said("gave-up")), self.printed)


class CallLinesBound(unittest.TestCase):
    """verify-G L-3: one enormous line (a minified bundle) is not read whole to keep 300 of it."""

    def test_a_giant_single_line_is_read_in_bounded_pieces(self):
        import tempfile
        import tracemalloc
        d = tempfile.mkdtemp(prefix="mk-g-giant-")
        p = os.path.join(d, "bundle.js")
        with open(p, "wb") as fh:
            fh.write(b"function caller(){return parse(1)}" + b";x=1" * 2_000_000 + b"\n")
            fh.write(b"function after(){}\n")
        lines = L.CallLines()
        lines.want(p, 0)
        lines.want(p, 1)
        tracemalloc.start()
        try:
            lines.load()
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1_000_000, "the giant line was held whole")
        self.assertTrue(lines.line(p, 0).startswith("function caller()"))
        self.assertEqual(L.CALL_LINE_MAX, len(lines.line(p, 0)))
        self.assertEqual("function after(){}", lines.line(p, 1))


class Columns(_Calls):

    def test_non_bmp_cjk_crlf_and_form_feed_lines_keep_character_columns_and_line_numbers(self):
        p = os.path.join(self.repo, "src", "app", "wide.py")
        with open(p, "wb") as fh:
            fh.write(("# \U0001F600 漢字\r\n\x0c\r\ndef 名(x):\r\n    return x\r\n\r\n\r\n"
                      "def caller():  # \U0001F600\r\n    return 名(1)\r\n").encode("utf-8"))
        c, state, srv = self.serve()
        a = calls(c, "src/app/wide.py", 3, "in", col=5).json()
        root = a["groups"][0]["item"]
        self.assertEqual(("名", 3, 5), (root["name"], root["line"], root["col"]))
        self.assertEqual([("caller", 7, 5)], [(r["name"], r["line"], r["col"]) for r in rows_of(a)])
        self.assertEqual("def caller():  # \U0001F600", rows_of(a)[0]["snippet"])


# ============================================================== cancel

class CancelStops(_Calls):

    def test_cancel_sends_cancel_request_ends_the_job_and_leaves_no_thread(self):
        c, state, srv = self.serve("--slow", "callHierarchy/incomingCalls")
        threads = {t.ident for t in threading.enumerate()}
        r = c.get(f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in&sync_ms=0")
        job = r.json()["job"]
        self.assertTrue(wait_for(lambda: "callHierarchy/incomingCalls" in self.seen()))
        self.assertEqual(200, c.post(f"/api/jobs/{job}/cancel", headers=ORIGIN).status_code)
        self.assertTrue(wait_for(lambda: "$/cancelRequest" in self.seen()))
        self.assertTrue(wait_for(lambda: c.get(f"/api/jobs/{job}").json()["job"]["state"] == "cancelled"))
        self.assertTrue(srv.alive, "a cancelled question stopped the server")
        self.assertTrue(wait_for(lambda: {t.ident for t in threading.enumerate()
                                          if not t.name.startswith(("AnyIO", "asyncio"))} <= threads),
                        [t.name for t in threading.enumerate() if t.ident not in threads])


# ============================================================== crash

class CrashFallsBack(_Calls):

    def test_a_crash_during_the_question_falls_back_to_the_graph_and_restarts_at_the_next_ask(self):
        c, state, srv = self.serve("--crash-on", "callHierarchy/incomingCalls")
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual(("graph", "crashed"), (a["source"], a["lsp"]["state"]))
        self.assertIn("total", [r["name"] for r in rows_of(a)])
        calls(c, CORE, 1, "in", col=5)
        self.assertEqual(1, c.get("/api/lsp").json()["servers"][0]["restarts"])


# ============================================================== the handshake and the silence bound

class SettingsHandshake(_Calls):
    """0.0.29 A, D14 H-A1 `REAL-PYRIGHT-NEVER-ANSWERS-CALL-HIERARCHY`. Root cause, from the traffic
    captured both ways against real pyright 1.1.390 (`0029-evidence/A/h-a1-traffic.txt`): mokata
    announced `workspace.workspaceFolders`, and pyright's `handleInitialized` then skips its settings
    pull and waits for a `workspace/didChangeConfiguration` VS Code would send. mokata never sends
    one, so every workspace stays pending and every request — `prepareCallHierarchy` first — is held
    forever. `--settings-pull` is that branch of pyright's shipped code, nothing more."""

    def test_a_pyright_shaped_server_answers_call_hierarchy_through_mokata(self):
        c, state, srv = self.serve("--settings-pull")
        a = _final(c, f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in", 20)
        self.assertEqual(("lsp", True), (a["source"], a["asked"]), a)
        self.assertIn("total", [r["name"] for r in rows_of(a)])
        self.assertNotIn("workspace/didChangeConfiguration", self.seen())

    def test_mokata_does_not_announce_workspace_folders_it_never_sends_changes_for(self):
        """The capability is the switch pyright reads; the claim was never true (mokata sends no
        `workspace/didChangeWorkspaceFolders`). The folder itself still goes in `initialize`."""
        sent = {}
        real = L.Server.request

        def spy(self_, method, params, *a, **k):
            if method == "initialize":
                sent.update(params)
            return real(self_, method, params, *a, **k)
        with mock.patch.object(L.Server, "request", spy):
            self.serve("--settings-pull")
        self.assertNotIn("workspaceFolders", sent["capabilities"]["workspace"])
        self.assertTrue(sent["capabilities"]["workspace"]["configuration"])
        self.assertEqual([os.path.basename(self.repo)], [f["name"] for f in sent["workspaceFolders"]])


class SilentServerFallsBack(_Calls):
    """D14: a server that never answers ends in a NAMED NOT RUN and the graph answers, labelled —
    never a silent 600 s wait. The bound is SILENCE (nothing heard from the server for
    `CALLS_SILENCE_S`), the house rule since D4: a server that is still talking is never cut off."""

    def _bounded(self, s=1.0):
        p = mock.patch.object(L, "CALLS_SILENCE_S", s)
        p.start()
        self.addCleanup(p.stop)

    def _assert_not_run(self, a, method):
        self.assertEqual(("graph", "no-answer", False), (a["source"], a["lsp"]["state"], a["asked"]), a)
        self.assertTrue(a["lsp"]["note"].startswith("NOT RUN"), a["lsp"])
        self.assertIn(method, a["lsp"]["note"])
        self.assertIn("fake-lsp", a["lsp"]["note"])

    def test_a_silent_prepare_ends_named_and_the_graph_answers_callers_and_callees(self):
        self._bounded()
        c, state, srv = self.serve("--slow", "textDocument/prepareCallHierarchy")
        a = _final(c, f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in", 20)
        self._assert_not_run(a, "textDocument/prepareCallHierarchy")
        self.assertIn("total", [r["name"] for r in rows_of(a)])
        self.assertTrue(wait_for(lambda: "$/cancelRequest" in self.seen()), self.seen())
        self.assertTrue(srv.alive, "a silent question stopped the server")
        b = _final(c, f"/api/lsp/calls?path={CORE}&line=7&col=5&dir=out", 20)
        self._assert_not_run(b, "textDocument/prepareCallHierarchy")
        self.assertIn("parse", [r["name"] for r in rows_of(b)])

    def test_a_silent_incoming_calls_ends_named_too(self):
        self._bounded()
        c, state, srv = self.serve("--slow", "callHierarchy/incomingCalls")
        a = _final(c, f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in", 20)
        self._assert_not_run(a, "callHierarchy/incomingCalls")

    def test_a_server_still_talking_is_not_cut_off(self):
        """The answer comes 2.5 s late with a `$/progress` every 0.3 s: under a 1 s silence bound
        it is the language server's answer, not a fallback."""
        self._bounded()
        c, state, srv = self.serve("--delay", "parse:2.5", "--heartbeat", "0.3")
        a = _final(c, f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in", 20)
        self.assertEqual("lsp", a["source"], a)

    def test_the_shipped_bound_is_silence_under_the_idle_reap_and_not_small(self):
        self.assertGreaterEqual(L.CALLS_SILENCE_S, 60.0)
        self.assertLess(L.CALLS_SILENCE_S, L.IDLE_S)


# ============================================================== scale

class HubIsBounded(_Calls):

    def _hub(self, lines, ext="py"):
        p = os.path.join(self.repo, "src", "app", "hub_callers." + ext)
        with open(p, "w", encoding="utf-8") as fh:
            for i in range(lines):
                fh.write("def caller_%d(): return parse('x')\n" % i)
        return "src/app/hub_callers." + ext

    def test_fifty_thousand_callers_are_cut_at_the_cap_with_the_exact_total(self):
        rel = self._hub(1000)
        c, state, srv = self.serve("--hub", "50000", "--hub-file", rel)
        wanted = []
        real_want = L.CallLines.want
        with mock.patch.object(L.CallLines, "want", lambda s, p, l0: (wanted.append(l0), real_want(s, p, l0))):
            a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual((L.CALLS_MAX, 50000, True, False),
                         (len(rows_of(a)), a["total"], a["truncated"], a["complete"]))
        self.assertLessEqual(len(wanted), L.CALLS_MAX + 1)
        self.assertTrue(all(r["snippet"].startswith("def caller_") for r in rows_of(a)))

    def test_a_million_line_caller_file_is_read_once_keeping_only_the_rows_lines(self):
        # Not a .py: the READER is under test, not the call graph. A 1M-function .py makes the
        # graph's first index of the repo (graph_edge, an existing cost, cached after) the slow
        # part — past the 60 s follow window on the CI runners (probe/matrix/G run 42595158407).
        rel = self._hub(1_000_000, ext="txt")
        c, state, srv = self.serve("--hub", "3000", "--hub-file", rel)
        kept = []
        real_load = L.CallLines.load

        def load(s, checkpoint=lambda: None):
            real_load(s, checkpoint)
            kept.append(sum(len(v) for v in s._got.values()))
        with mock.patch.object(L.CallLines, "load", load):
            a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual((L.CALLS_MAX, 3000), (len(rows_of(a)), a["total"]))
        self.assertLessEqual(kept[0], L.CALLS_MAX + 1)
        self.assertEqual("def caller_1999(): return parse('x')", rows_of(a)[-1]["snippet"])


#: 0.0.29 A `LSP-CALLS-UNRUN-CELLS` — the scale cells generate their own trees (the kit's 68k tree under
#: /private/tmp was eaten by tmp_cleaner) and run weekly on three OSes in `.github/workflows/real-lsp.yml`.
SCALE_FILES = int(os.environ.get("MOKATA_SCALE_FILES") or 68_000)
SCALE_FUNCS = int(os.environ.get("MOKATA_SCALE_FUNCS") or 1_000_000)


def _scale_tree(base, files):
    """`files` deterministic .py files of ~30 lines in ~5k directories; each defines three functions,
    the second calls the first and a function of the previous file. Returns (repo, one rel path)."""
    os.makedirs(base)
    per_dir = max(1, files // 5000)
    for i in range(files):
        d = os.path.join(base, "pkg", f"d{i // per_dir:05d}")
        os.makedirs(d, exist_ok=True)
        prev = max(0, i - 1)
        body = (f"def f{i}_a(x):\n    y = x + 1\n    return y\n\n\n"
                f"def f{i}_b(x):\n    z = f{i}_a(x)\n    w = f{prev}_c(z)\n    return w\n\n\n"
                f"def f{i}_c(x):\n" + "".join(f"    x = x + {k}\n" for k in range(15)) + "    return x\n")
        with open(os.path.join(d, f"m{i:06d}.py"), "w", encoding="utf-8") as fh:
            fh.write(body)
    first = f"pkg/d{0:05d}/m{0:06d}.py"
    return base, first


def _watch_job(c, jid, url):
    """Every snapshot of job `jid` until it finishes — ended by the job's own state, not a clock (the
    half-second is how often it is looked at, never how long it may take). Each look also re-asks
    `url` by `job=`, as a page-less asker does: that is what keeps its want (`GRACE_S` lease) alive —
    a snapshot alone is not a want, and the job is cancelled as abandoned (seen on the first
    real-lsp.yml run, Windows: "nobody is waiting for this any more")."""
    import time
    seen = []
    while True:
        c.get(f"{url}&job={jid}&sync_ms=0")
        j = c.get(f"/api/jobs/{jid}").json()["job"]
        seen.append(j)
        if j["state"] in ("done", "failed", "cancelled"):
            return seen
        time.sleep(0.5)


@unittest.skipUnless(os.environ.get("MOKATA_SCALE_LSP"), "weekly (real-lsp.yml): MOKATA_SCALE_LSP=1 generates a "
                     "68k-file tree and a 1M-function file")
class BigRepoFallback(_Repo):

    def _count_walks(self):
        """The COLD builds (`prior is None`: nothing loaded, every file parsed). A walk WITH a prior
        is `_StatFreshness`'s once-per-window re-stat of an un-set-up repo (`nav.py`): a `stat` per
        file, re-parsing only what moved — by design, and not a second build (seen on the first
        real-lsp.yml run's local repro: one cold build, then one stat pass from `ensure_fresh`)."""
        from mokata.knowledge.ast_backend import AstBackend
        walks = []
        real = AstBackend._walk_index

        def walk(b, prior, *a, **kw):
            if prior is None:
                walks.append(b.root)
            return real(b, prior, *a, **kw)
        p = mock.patch.object(AstBackend, "_walk_index", walk)
        p.start()
        self.addCleanup(p.stop)
        return walks

    def test_graph_callers_and_callees_over_a_generated_big_tree_stay_jobs_with_progress(self):
        big, rel = _scale_tree(os.path.join(self.d, "big"), SCALE_FILES)
        walks = self._count_walks()
        c, state = boot(big)
        self.addCleanup(state.close)
        answers = {}
        for d in ("in", "out"):
            r = c.get(f"/api/lsp/calls?path={rel}&line=7&dir={d}&sync_ms=0")
            if r.status_code == 200:          # the second asks a warm index: a plain answer is right
                answers[d] = r.json()
                continue
            self.assertEqual(202, r.status_code, r.text)
            jid = r.json()["job"]
            seen = _watch_job(c, jid, f"/api/lsp/calls?path={rel}&line=7&dir={d}")
            self.assertEqual("done", seen[-1]["state"], seen[-1])
            self.assertTrue(any(s.get("note") for s in seen), seen[:3])
            answers[d] = lsp_get(c, f"/api/lsp/calls?path={rel}&line=7&dir={d}&job={jid}", within=7200).json()
        self.assertEqual(("graph", "graph"), (answers["in"]["source"], answers["out"]["source"]))
        self.assertEqual(["f0_a", "f0_c"], sorted(r["name"] for r in rows_of(answers["out"])))
        self.assertEqual(1, len([w for w in walks if os.path.realpath(w) == os.path.realpath(big)]), walks)

    def test_a_language_server_answer_on_a_million_function_repo_arrives_before_the_index(self):
        with open(os.path.join(self.repo, "src", "app", "huge.py"), "w", encoding="utf-8") as fh:
            for i in range(SCALE_FUNCS):
                fh.write(f"def g{i}():\n    return parse('x')\n")
        T.decide(self.repo, True, "test")
        walks = self._count_walks()
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        from test_p24_ide_lsp_readonly import finder_for
        state.lsp.finder = finder_for("--index-seconds", "0.1")
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        self.assertTrue(wait_for(state.lsp.get("python").is_ready, timeout=600))
        # Asked by HOVER: the fake server answers it from the open document alone, while its call
        # hierarchy scans every definition in the workspace (1M here) — that would time the FAKE,
        # not mokata. Hover carries the same `graph_edge(wait=False)` as calls (row 7 pins calls).
        a = lsp_get(c, f"/api/lsp/hover?path={CORE}&line=1&col=5", within=7200).json()
        self.assertEqual("lsp", a["source"], a)
        self.assertEqual("indexing", a["edge"].get("state"), a["edge"])
        build = state.jobs.get(a["edge"]["job"])
        self.assertIsNotNone(build)
        self.assertTrue(build.finished.wait(7200))
        self.assertEqual("done", build.state)
        self.assertEqual(1, len(walks), walks)


def _final(c, url, within):
    """`lsp_get`, and when no final answer came: the job's own snapshot and every thread's stack in
    the failure — a real server that never answers must say where it stopped (the first
    real-lsp.yml run only said `KeyError: 'source'` after 600 s)."""
    r = lsp_get(c, url, within=within)
    if r.status_code == 202:
        import faulthandler
        import sys
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        jid = r.json().get("job")
        raise AssertionError(f"no answer within {within} s: {c.get(f'/api/jobs/{jid}').json()}")
    return r.json()


#: real-lsp.yml sets this: on that leg a missing server is a FAILURE, never a skip (the first run's
#: Windows leg skipped and showed green — the gate looked with `find_binary`, which refuses npm's
#: `.cmd` shims by design; the product finds an npm global through `find_server`).
_REQUIRE_REAL = bool(os.environ.get("MOKATA_REQUIRE_REAL_LSP"))


@unittest.skipUnless(_REQUIRE_REAL or L.find_server(os.getcwd(), "python").get("found"),
                     "weekly (real-lsp.yml): no real Python language server")
class RealServerSmoke(_Repo):

    def test_a_real_server_answers_callers_and_callees_of_a_three_function_repo(self):
        found = L.find_server(self.repo, "python")
        self.assertTrue(found.get("found"), found)
        T.decide(self.repo, True, "test")
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        self.assertTrue(wait_for(state.lsp.get("python").is_ready, timeout=600))
        a = _final(c, f"/api/lsp/calls?path={CORE}&line=1&col=5&dir=in", 600)
        self.assertEqual("lsp", a["source"], a)
        self.assertIn("total", [r["name"] for r in rows_of(a)])
        b = _final(c, f"/api/lsp/calls?path={CORE}&line=7&col=5&dir=out", 600)
        self.assertEqual("lsp", b["source"], b)
        self.assertIn("parse", [r["name"] for r in rows_of(b)])


def _dir_link(link, target):
    """A directory link the way the platform's package managers make one: a symlink, or on a
    Windows runner without symlink rights a junction (what pnpm itself makes there)."""
    os.makedirs(os.path.dirname(link), exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(target, link)


class PnpmGlobalVirtualStore(unittest.TestCase):
    """0.0.29 A `WIN-LSP-GLOBAL-STORE-UNPROVEN`, found by the first real `pnpm add -g` on a Windows
    runner: pnpm 9's GLOBAL layout keeps its virtual store BESIDE `node_modules`
    (`<PNPM_HOME>/global/5/.pnpm`), linked in from `node_modules/<pkg>` — the project layout keeps it
    INSIDE (`node_modules/.pnpm`). A script must lie inside its layout (`global/<n>`), never merely
    inside the PATH entry, never in another layout, and npm's bound (`node_modules`) is unchanged."""

    PKG = "typescript-language-server"

    def setUp(self):
        import tempfile
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="mk-a-pnpm-"))
        self.addCleanup(__import__("shutil").rmtree, self.base, True)
        self.root = os.path.join(self.base, "repo")
        os.makedirs(self.root)
        node_dir = os.path.join(self.base, "nodebin")
        os.makedirs(node_dir)
        node = os.path.join(node_dir, "node.exe" if os.name == "nt" else "node")
        with open(node, "w", encoding="utf-8") as fh:
            fh.write("")
        os.chmod(node, 0o755)
        self.home = os.path.join(self.base, "pnpm-home")
        self.layout = os.path.join(self.home, "global", "5")
        # PNPM_HOME as pnpm itself reads it: the global dir is DERIVED from it (verify-B2 M3), never
        # guessed from a PATH entry.
        self.env = {"PATH": os.pathsep.join([self.home, node_dir]), "PNPM_HOME": self.home}

    def _package(self, where):
        """A real package directory at `where` (package.json + lib/cli.mjs); returns the script."""
        os.makedirs(os.path.join(where, "lib"), exist_ok=True)
        with open(os.path.join(where, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": self.PKG, "bin": {self.PKG: "./lib/cli.mjs"}}, fh)
        script = os.path.join(where, "lib", "cli.mjs")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("")
        return script

    def _linked_from(self, real_dir):
        _dir_link(os.path.join(self.layout, "node_modules", self.PKG), real_dir)

    def _find(self):
        return L._global_node_package_bin(self.root, self.PKG, environ=self.env)

    def test_the_pnpm9_global_layout_with_its_store_beside_node_modules_is_found(self):
        script = self._package(os.path.join(self.layout, ".pnpm", f"{self.PKG}@4.3.3",
                                            "node_modules", self.PKG))
        self._linked_from(os.path.dirname(os.path.dirname(script)))
        head, why = self._find()
        self.assertIsNotNone(head, why)
        self.assertEqual(os.path.realpath(script), head[1])

    def test_a_package_linked_out_of_its_layout_is_refused(self):
        script = self._package(os.path.join(self.base, "elsewhere", self.PKG))
        self._linked_from(os.path.dirname(os.path.dirname(script)))
        head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("refused", why)

    def test_a_package_linked_into_ANOTHER_layout_is_refused(self):
        script = self._package(os.path.join(self.home, "global", "4", ".pnpm", f"{self.PKG}@4.3.3",
                                            "node_modules", self.PKG))
        self._linked_from(os.path.dirname(os.path.dirname(script)))
        head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("refused", why)

    def _installed(self):
        script = self._package(os.path.join(self.layout, ".pnpm", f"{self.PKG}@4.3.3",
                                            "node_modules", self.PKG))
        self._linked_from(os.path.dirname(os.path.dirname(script)))
        return script

    def test_a_global_dir_owned_by_someone_else_is_refused(self):
        """verify-B2 M3 / D14's rule, as for yarn: the realpath inside pnpm's global dir AND that dir
        owned by this user. Forced through the seam; the seam is asked about the dir's realpath."""
        self._installed()
        asked = []

        def foreign(path):
            asked.append(path)
            return False
        with mock.patch.object(L, "_owned_by_me", foreign):
            head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("not owned by this user", why)
        self.assertEqual([os.path.realpath(os.path.join(self.home, "global"))], asked)

    def test_the_global_dir_is_derived_never_guessed_from_a_path_entry(self):
        """A pnpm-shaped `global/<n>` under a PATH entry that is NOT pnpm's global dir is not
        searched: with PNPM_HOME elsewhere, nothing is found (fail closed)."""
        self._installed()
        other = os.path.join(self.base, "other-pnpm-home")
        os.makedirs(other)
        head, why = L._global_node_package_bin(self.root, self.PKG, environ=dict(self.env, PNPM_HOME=other))
        self.assertIsNone(head, head)

    def test_no_npm_config_variable_is_ever_read(self):
        """verify-A r3 R3-2 (D14: a derivation a repo can steer is High): `npm run` / `pnpm run` turn
        EVERY key of the repo's `.npmrc` into `npm_config_<key>`. So none is read — not `global-dir`,
        nor a planted `pnpm-home` / `xdg-data-home`. The install they point at is not found."""
        self._installed()
        other = os.path.join(self.base, "user-pnpm-home")
        os.makedirs(other)
        planted = os.path.join(self.home, "global")
        env = dict(self.env, PNPM_HOME=other, npm_config_global_dir=planted,
                   NPM_CONFIG_GLOBAL_DIR=planted, npm_config_pnpm_home=self.home,
                   npm_config_xdg_data_home=self.base)
        self.assertEqual(os.path.join(other, "global"), L._pnpm_global_dir(env))
        head, why = L._global_node_package_bin(self.root, self.PKG, environ=env)
        self.assertIsNone(head, head)

    def _npm_run_derives(self, runner):
        """`<runner> run e` in a repo whose `.npmrc` sets `global-dir` (and `pnpm-home`) to a REAL,
        owned, installed pnpm global dir; the script prints what it SAW and what mokata derived."""
        import shutil
        import subprocess
        planted = os.path.join(self.home, "global")
        self._installed()
        user = os.path.join(self.base, "user-pnpm-home")
        os.makedirs(user)
        with open(os.path.join(self.root, ".npmrc"), "w", encoding="utf-8") as fh:
            fh.write(f"global-dir={planted}\npnpm-home={self.home}\n")
        with open(os.path.join(self.root, "probe.py"), "w", encoding="utf-8") as fh:
            fh.write("import json, os\nfrom mokata.ide import lsp as L\n"
                     "head, why = L._global_node_package_bin(os.getcwd(), %r)\n"
                     "print(json.dumps({'saw': os.environ.get('npm_config_global_dir'),"
                     " 'derived': L._pnpm_global_dir(), 'found': head}))\n" % self.PKG)
        with open(os.path.join(self.root, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "r", "version": "1.0.0",
                       "scripts": {"e": '"%s" probe.py' % sys.executable}}, fh)
        src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(L.__file__))))
        env = {k: v for k, v in os.environ.items()
               if not k.lower().startswith(("npm_config_", "pnpm_"))}
        env.update(PNPM_HOME=user, PYTHONPATH=os.pathsep.join([src, env.get("PYTHONPATH", "")]),
                   npm_config_update_notifier="false")
        out = subprocess.run([shutil.which(runner), "run", "-s", "e"], cwd=self.root, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(0, out.returncode, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        # The channel is live — the repo's `.npmrc` DID reach the environment — so the refusal is
        # mokata's, not an accident of the runner.
        self.assertEqual(os.path.normcase(planted), os.path.normcase(got["saw"] or ""), got)
        self.assertEqual(os.path.normcase(os.path.join(user, "global")),
                         os.path.normcase(got["derived"]), got)
        self.assertIsNone(got["found"], got)

    @unittest.skipUnless(__import__("shutil").which("npm"), "npm is not on PATH")
    def test_a_repo_npmrc_under_npm_run_does_not_redirect_the_derivation(self):
        self._npm_run_derives("npm")

    @unittest.skipUnless(__import__("shutil").which("pnpm"), "pnpm is not on PATH")
    def test_a_repo_npmrc_under_pnpm_run_does_not_redirect_the_derivation(self):
        self._npm_run_derives("pnpm")

    def _layout_elsewhere(self):
        """A self-contained pnpm layout OUTSIDE the global dir: its `.pnpm` and `node_modules`."""
        os.makedirs(os.path.join(self.home, "global"), exist_ok=True)
        outside = os.path.join(self.base, "outside-layout")
        pkg = os.path.join(outside, ".pnpm", f"{self.PKG}@4.3.3", "node_modules", self.PKG)
        self._package(pkg)
        return outside, pkg

    def test_a_layout_dir_linked_out_of_the_global_dir_is_refused(self):
        """verify-A r3 R3-1 / V3: `global/5` ITSELF a link to a layout elsewhere. The script check
        alone passes — its scope is the layout, realpathed — so the layout dir's own realpath must be
        inside the global dir: the executed script's FINAL realpath is then inside it too."""
        outside, pkg = self._layout_elsewhere()
        _dir_link(os.path.join(outside, "node_modules", self.PKG), pkg)
        _dir_link(self.layout, outside)
        head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("refused", why)

    @unittest.skipUnless(os.name == "nt", "a junction is a Windows link")
    def test_a_layout_dir_JUNCTIONED_out_of_the_global_dir_is_refused(self):
        """The same, every hop a junction (what pnpm itself makes on Windows)."""
        import _winapi
        outside, pkg = self._layout_elsewhere()
        os.makedirs(os.path.join(outside, "node_modules"))
        _winapi.CreateJunction(pkg, os.path.join(outside, "node_modules", self.PKG))
        _winapi.CreateJunction(outside, self.layout)
        head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("refused", why)

    def _home_at(self, home):
        self.home = home
        self.layout = os.path.join(home, "global", "5")
        self.env = dict(self.env, PNPM_HOME=home)

    def test_a_global_dir_inside_the_repo_is_refused(self):
        """verify-A r3 R3-1 / V2: PNPM_HOME inside the repository — the repo would choose the server."""
        self._home_at(os.path.join(self.root, "pnpm-home"))
        self._installed()
        head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("inside the repository", why)

    def test_a_global_dir_LINKED_into_the_repo_is_refused(self):
        """…and a PNPM_HOME outside it that is a link INTO it (the realpath decides)."""
        inside = os.path.join(self.root, "pnpm-home")
        self._home_at(inside)
        self._installed()
        link = os.path.join(self.base, "linked-home")
        _dir_link(link, inside)
        self._home_at(link)
        head, why = self._find()
        self.assertIsNone(head, head)
        self.assertIn("inside the repository", why)

    def test_a_stray_non_ascii_digit_layout_is_skipped(self):
        """verify-A r3 R3-5: `"\u00b2".isdigit()` is True and `int("\u00b2")` raises."""
        script = self._installed()
        os.makedirs(os.path.join(self.home, "global", "\u00b2"))
        head, why = self._find()
        self.assertEqual(os.path.realpath(script), (head or [None, None])[1], why)

    def test_pnpms_global_dir_is_where_pnpm_puts_it(self):
        """@pnpm/config `getDataDir` + `global`: PNPM_HOME, XDG_DATA_HOME/pnpm, macOS
        ~/Library/pnpm, other POSIX ~/.local/share/pnpm, Windows %LOCALAPPDATA%\\pnpm, else ~/.pnpm."""
        j, home = os.path.join, os.path.expanduser("~")
        g = L._pnpm_global_dir
        self.assertEqual(j("/ph", "global"), g({"PNPM_HOME": "/ph", "XDG_DATA_HOME": "/x"}, plat="linux"))
        self.assertEqual(j("/x", "pnpm", "global"), g({"XDG_DATA_HOME": "/x"}, plat="darwin"))
        self.assertEqual(j(home, "Library", "pnpm", "global"), g({}, plat="darwin"))
        self.assertEqual(j(home, ".local", "share", "pnpm", "global"), g({}, plat="linux"))
        self.assertEqual(j("C:\\la", "pnpm", "global"), g({"LOCALAPPDATA": "C:\\la"}, plat="win32"))
        self.assertEqual(j(home, ".pnpm", "global"), g({}, plat="win32"))
        # verify-A r3 R3-6: a relative data dir would resolve against the cwd — not found, never guessed.
        self.assertIsNone(g({"PNPM_HOME": "rel/ph"}, plat="linux"))
        self.assertIsNone(g({"XDG_DATA_HOME": "rel/x"}, plat="linux"))
        self.assertIsNone(g({"LOCALAPPDATA": "rel\\la"}, plat="win32"))

    def test_npm_keeps_its_bound_a_package_linked_beside_node_modules_is_refused(self):
        npm_bin = os.path.join(self.base, "npm-prefix")
        script = self._package(os.path.join(npm_bin, ".store", self.PKG))
        _dir_link(os.path.join(npm_bin, "node_modules", self.PKG), os.path.dirname(os.path.dirname(script)))
        env = {"PATH": os.pathsep.join([npm_bin, self.env["PATH"].split(os.pathsep)[-1]])}
        head, why = L._global_node_package_bin(self.root, self.PKG, environ=env)
        self.assertIsNone(head, head)
        self.assertIn("refused", why)


#: yarn v1's Windows shim as `cmd-shim` writes it (`yarn global add`, 1.22): the script is named
#: relative to the shim's own directory (`%~dp0`). `{rel}` is that relative path.
YARN_CMD_SHIM = ('@IF EXIST "%~dp0\\node.exe" (\r\n'
                 '  "%~dp0\\node.exe"  "%~dp0\\{rel}" %*\r\n'
                 ') ELSE (\r\n'
                 '  @SETLOCAL\r\n'
                 '  @SET PATHEXT=%PATHEXT:;.JS;=;%\r\n'
                 '  node  "%~dp0\\{rel}" %*\r\n'
                 ')\r\n')
#: yarn 1.22's OWN bin shim, byte-shaped as the real Windows runner printed it (`real-lsp-run5.txt`):
#: one line naming the NEXT shim, `<yarn global dir>\node_modules\.bin\<name>.cmd`.
YARN_BIN_SHIM = '@"%~dp0\\{rel}"   %*\r\n'


class YarnGlobalShim(unittest.TestCase):
    """0.0.29 A, D14 H-A2 `WIN-YARN-GLOBAL-PREFIX-NOT-FOUND`: yarn v1 under npm's prefix puts its
    Windows shim in `<npm prefix>\\bin` (not `…\\Yarn\\bin`) and the package in `yarn global dir`. The
    shim is READ, never run, and followed only when its target's realpath lies inside yarn's global dir
    AND that dir is owned by this user; then the package's own `bin` runs (never a shim). Anything else
    stays "found only as <shim>"."""

    PKG = PnpmGlobalVirtualStore.PKG
    _package = PnpmGlobalVirtualStore._package

    def setUp(self):
        PnpmGlobalVirtualStore.setUp(self)        # the temp base, the repo and a `node` on PATH
        self.ygd = os.path.join(self.base, "yarn-data", "global")
        self.prefix_bin = os.path.join(self.base, "npm-prefix", "bin")
        os.makedirs(self.prefix_bin)
        self.shim = os.path.join(self.prefix_bin, self.PKG + ".cmd")
        node_dir = self.env["PATH"].split(os.pathsep)[-1]
        self.node = os.path.join(node_dir, "node.exe" if os.name == "nt" else "node")
        env = mock.patch.dict(os.environ, {"PATH": os.pathsep.join([self.prefix_bin, node_dir]),
                                           "YARN_GLOBAL_FOLDER": self.ygd})
        env.start()
        self.addCleanup(env.stop)

    def _shim_to(self, script, shim=None, text=YARN_CMD_SHIM):
        shim = shim or self.shim
        rel = os.path.relpath(script, os.path.dirname(shim)).replace(os.sep, "\\")
        os.makedirs(os.path.dirname(shim), exist_ok=True)
        with open(shim, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.format(rel=rel))

    def _assert_shim_only(self, got):
        self.assertFalse(got["found"], got)
        self.assertIn(f"found only as {self.shim}", got["note"])

    def test_a_shim_whose_script_lies_in_yarns_global_dir_is_followed_to_it(self):
        script = self._package(os.path.join(self.ygd, "node_modules", self.PKG))
        self._shim_to(script)
        got = L.find_server(self.root, "typescript")
        self.assertTrue(got["found"], got)
        self.assertEqual([self.node, os.path.realpath(script), "--stdio"], got["argv"])

    def test_yarn_122s_own_shim_chain_runs_the_package_bin_never_a_shim(self):
        """The real runner's shape: `<prefix>\\bin\\<name>.cmd` names `<global dir>\\node_modules\\.bin\\
        <name>.cmd`, itself a cmd-shim. What runs is `[node, <package.json bin>]` — no `.cmd` in argv."""
        script = self._package(os.path.join(self.ygd, "node_modules", self.PKG))
        inner = os.path.join(self.ygd, "node_modules", ".bin", self.PKG + ".cmd")
        self._shim_to(script, shim=inner)
        self._shim_to(inner, text=YARN_BIN_SHIM)
        got = L.find_server(self.root, "typescript")
        self.assertTrue(got["found"], got)
        self.assertEqual([self.node, os.path.realpath(script), "--stdio"], got["argv"])

    def test_a_shim_whose_script_is_outside_yarns_global_dir_is_not_followed(self):
        """The package IS installed in yarn's dir too: only the shim's target decides."""
        script = self._package(os.path.join(self.base, "elsewhere", self.PKG))
        self._package(os.path.join(self.ygd, "node_modules", self.PKG))
        self._shim_to(script)
        self._assert_shim_only(L.find_server(self.root, "typescript"))

    def test_a_script_linked_out_of_yarns_global_dir_is_not_followed(self):
        """Inside by its spelling, outside by its realpath: the realpath decides."""
        out = self._package(os.path.join(self.base, "elsewhere", self.PKG))
        _dir_link(os.path.join(self.ygd, "node_modules", self.PKG), os.path.dirname(os.path.dirname(out)))
        self._shim_to(os.path.join(self.ygd, "node_modules", self.PKG, "lib", "cli.mjs"))
        self._assert_shim_only(L.find_server(self.root, "typescript"))

    def test_yarns_global_dir_owned_by_someone_else_is_not_followed(self):
        script = self._package(os.path.join(self.ygd, "node_modules", self.PKG))
        self._shim_to(script)
        asked = []

        def foreign(path):
            asked.append(path)
            return False
        with mock.patch.object(L, "_owned_by_me", foreign):
            got = L.find_server(self.root, "typescript")
        self._assert_shim_only(got)
        self.assertEqual([os.path.realpath(self.ygd)], asked)

    def test_the_owner_check_reads_the_real_owner(self):
        self.assertTrue(L._owned_by_me(self.base))
        if os.name == "nt":
            self.assertFalse(L._owned_by_me(os.path.join(os.environ["SystemRoot"], "System32")))
        elif os.geteuid() != 0:
            self.assertFalse(L._owned_by_me("/"))

    def test_yarns_global_dir_is_where_yarn_v1_puts_it(self):
        j = os.path.join
        self.assertEqual(j("C:\\la", "Yarn", "Data", "global"),
                         L._yarn_global_dir({"LOCALAPPDATA": "C:\\la"}, nt=True))
        self.assertEqual(j("/xdg", "yarn", "global"), L._yarn_global_dir({"XDG_DATA_HOME": "/xdg"}, nt=False))
        self.assertEqual(j(os.path.expanduser("~"), ".config", "yarn", "global"), L._yarn_global_dir({}, nt=False))
        self.assertEqual(self.ygd, L._yarn_global_dir({"YARN_GLOBAL_FOLDER": self.ygd, "LOCALAPPDATA": "C:\\la"},
                                                      nt=True))
        self.assertIsNone(L._yarn_global_dir({"YARN_GLOBAL_FOLDER": "relative/dir"}, nt=False))


@unittest.skipUnless(os.environ.get("MOKATA_GLOBAL_STORE_DIR"),
                     "weekly (real-lsp.yml): a real `pnpm add -g` / `yarn global add` of typescript-language-server")
class RealGlobalStore(_Repo):
    """0.0.29 A `WIN-LSP-GLOBAL-STORE-UNPROVEN` — a real global install is found by `find_server`, and
    what it would run lies inside that tool's global home (`MOKATA_GLOBAL_STORE_DIR`: `PNPM_HOME` —
    its bin AND its store — for pnpm, `yarn global dir` for yarn v1). On POSIX the global bin is a
    plain script `find_binary` may find first; on Windows only the package's own `bin` can be run."""

    def _lookups(self):
        """Every PATH entry's global layouts and `_package_bin`'s verdict on each — what a red says,
        so one run decides (the first real run's Windows reds only said "not found")."""
        out = []
        dirs = [m for d in L.path_dirs(cwd=self.repo) for m in L._global_module_dirs(d)]
        for modules, anchor, scope in dirs + L._pnpm_layouts(self.repo)[0]:
            if os.path.isdir(modules):
                out.append((modules, anchor, L._package_bin(modules, anchor, self.repo,
                                                            "typescript-language-server", scope=scope)))
        return out

    def test_a_real_global_install_is_found(self):
        store = os.path.normcase(os.path.realpath(os.environ["MOKATA_GLOBAL_STORE_DIR"]))
        got = L.find_server(self.repo, "typescript")
        self.assertTrue(got["found"], (got, self._lookups()))
        self.assertEqual("typescript-language-server", got["name"], got)
        inside = [a for a in got["argv"]
                  if os.path.normcase(os.path.realpath(a)).startswith(store + os.sep)]
        self.assertTrue(inside, (store, got["argv"], self._lookups()))
        # never a shim in what would run (round 2: yarn's bin shim names another shim)
        self.assertFalse([a for a in got["argv"] if a.lower().endswith((".cmd", ".bat", ".ps1"))], got)

    @unittest.skipUnless(os.environ.get("MOKATA_PNPM_ROOT_G"), "a real pnpm leg only")
    def test_pnpms_global_dir_is_derived_exactly_as_pnpm_root_g_reports_it(self):
        """verify-B2 M3: mokata never runs pnpm; what it derives must BE pnpm's own answer."""
        real = os.path.normcase(os.path.realpath(os.environ["MOKATA_PNPM_ROOT_G"]))
        layouts, why = L._pnpm_layouts(self.repo)
        self.assertIn(real, [os.path.normcase(os.path.realpath(m)) for m, _a, _s in layouts], (why, layouts))


# ============================================================== one references surface (G-Q3)

class OneReferencesSurface(unittest.TestCase):

    def test_the_language_panel_reads_no_longer_include_references(self):
        """G-Q3: references have one surface (Find Usages). The Language panel's references read
        and its route are deleted; `textDocument/references` stays for Find Usages."""
        self.assertNotIn("references", F.KINDS)
        self.assertIn("textDocument/references", L.ALLOWED_REQUESTS)
        js = os.path.join(os.path.dirname(L.__file__), "static", "features", "lsp.js")
        with open(js, encoding="utf-8") as fh:
            self.assertFalse("lsp.references" in fh.read(), "the Language panel still offers references")


if __name__ == "__main__":
    unittest.main()
