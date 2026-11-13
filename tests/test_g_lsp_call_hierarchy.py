"""0.0.28 G `ID-LSP-CALL-HIERARCHY` — "who calls this?" / "what does this call?" from the project's own
language server, through the read-only bridge, shown in the Find Usages box.

Business rules — what the person at the IDE can rely on (rule: docs/build/handoff/0028-evidence/G/G.rule.md):
  * ONE DOOR, THREE NEW READS. The bridge may now send `textDocument/prepareCallHierarchy`,
    `callHierarchy/incomingCalls` and `callHierarchy/outgoingCalls` — nothing else new, nothing that writes.
  * UNTRUSTED MEANS NOTHING SPAWNED AND NOTHING SENT; the call graph answers callers, labelled; callees are
    empty and say why (G-Q4). A revoke while a question runs ends it with the untrusted answer: no further
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
        """Untrusted: `/api/lsp/calls` starts no process; callers come from the call graph,
        labelled `source: graph`; callees are empty and say they need the language server."""
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
        self.assertEqual("none", b["source"])
        self.assertEqual([], rows_of(b))
        self.assertFalse(b["asked"])
        self.assertIn("language server", b["note"])
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
        self.assertEqual(([], False), (rows_of(b), b["asked"]))
        self.assertIn("call hierarchy", b["note"])
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
        """W5 item 1 — the race the full suite hit 1-2 times in 2 per run, forced every time.
        The reader that refuses the over-cap frame ends the server; that very kill wakes the
        exit watcher, whose `_gone("exited")` used to claim the pending question first and answer
        it "exited (exit -9)". Here the watcher is made to win: it runs to completion inside the
        reader's kill, on its own thread, before the reader takes the lock again. The reason the
        reader decided on must be the one the answer carries."""
        c, state, srv = self.serve("--frame", str(self.CAP + 1))
        real_close, raced = srv.tree.close, []

        def close_then_the_watcher_wins():
            real_close()                                  # the real end of the real tree
            if not raced:
                raced.append(True)
                t = threading.Thread(target=srv._gone, args=("exited",))
                t.start()
                t.join(30)
                self.assertFalse(t.is_alive(), "the watcher's _gone never returned")
        with mock.patch.object(srv.tree, "close", close_then_the_watcher_wins):
            a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual([True], raced, "the race was not forced: the kill path never ran")
        self.assertEqual(("graph", "crashed"), (a["source"], a["lsp"]["state"]), a)
        self.assertIn("frame of 65537 bytes", a["lsp"]["note"])
        self.assertNotIn("exited", a["lsp"]["note"])
        self.assertTrue(wait_for(lambda: not srv.alive))


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


@unittest.skipUnless(os.environ.get("MOKATA_BIG_REPO"), "nightly: MOKATA_BIG_REPO names a 68k-file tree")
class BigRepoFallback(_Repo):

    def test_graph_callers_over_a_big_tree_stay_a_job_with_progress(self):
        big = os.environ["MOKATA_BIG_REPO"]
        c, state = boot(big)
        self.addCleanup(state.close)
        rel = next(os.path.relpath(os.path.join(d, f), big).replace(os.sep, "/")
                   for d, _ds, fs in os.walk(big) for f in fs if f.endswith(".py"))
        r = c.get(f"/api/lsp/calls?path={rel}&line=1&dir=in&sync_ms=0")
        self.assertEqual(202, r.status_code, r.text)
        a = lsp_get(c, f"/api/lsp/calls?path={rel}&line=1&dir=in&job={r.json()['job']}", within=3600).json()
        self.assertEqual("graph", a["source"])


@unittest.skipUnless(any(L.find_binary(os.getcwd(), n) for n in ("pyright-langserver", "typescript-language-server")),
                     "opt: no real language server on PATH")
class RealServerSmoke(_Repo):

    def test_a_real_server_answers_callers_of_a_three_function_repo(self):
        T.decide(self.repo, True, "test")
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        self.assertTrue(wait_for(state.lsp.get("python").is_ready, timeout=120))
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual("lsp", a["source"], a)
        self.assertIn("total", [r["name"] for r in rows_of(a)])


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
