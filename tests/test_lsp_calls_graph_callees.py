"""0.0.29 A — the Callees tab answers from the code graph without a language server (the 0.0.28 published promise,
`LSP-CALLS-GRAPH-CALLEES`), the graph's first index never holds a language-server answer
(`LSP-CALLS-GRAPH-FIRST-INDEX-COST`), and a language server's kept answer is never handed out after a revoke
(`LSP-CALLS-KEPT-RESULT-AFTER-REVOKE`).

Business rules — what the person at the IDE can rely on (rule: docs/build/handoff/0029-evidence/A/A.rule.md):
  * NO SERVER, STILL CALLEES. Every rung where no language server answers (untrusted, unsupported, never started,
    indexing, no call hierarchy, crashed, a server error) lists what the function calls FROM THE CALL GRAPH,
    labelled `source: "graph"`, with the reason the server did not answer.
  * THE FUNCTION ASKED ABOUT, NOT ITS NAMESAKES. `A.run` and `B.run` are two functions: the callees of one never
    include a call the other makes (one graph query by name, then each definition's own span).
  * A ROW EXPANDS ONLY ONTO ITS OWN CALLEE. A callee row is expandable only when its column is exactly the callee's
    name at its call; expanding asks about that callee's own definition(s) — one group per definition, at most 20,
    with how many there were.
  * ONE INDEX BUILD, AND A SERVER'S ANSWER NEVER WAITS FOR IT. A cold code graph is built ONCE (the ⌘P job), shared by
    every asker; a language server's answer arrives at once saying its graph facts are `indexing`; a graph answer
    waits on that one build, and cancelling it ends only its own wait.
  * AFTER A REVOKE, NO KEPT LANGUAGE-SERVER ANSWER IS HANDED OUT — by the route's `job=` fetch or by `/api/jobs/{id}`.
    The fetch is re-asked afresh: the call graph answers, untrusted, nothing is sent, the server is stopped.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import os
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_g_lsp_call_hierarchy import CORE, _Calls, calls, rows_of
from test_p0_ide_api_contract import ORIGIN, _write
from test_p24_ide_lsp_readonly import _Repo, boot, lsp_get, wait_for

from mokata.ide import jobs as J
from mokata.ide import lsp as L
from mokata.ide import nav
from mokata.ide import trust as T
from mokata.knowledge.ast_backend import AstBackend

#: A safety net for "the condition the test controls never arrived", never a measure of speed: every wait below
#: is on an event or a state the test itself causes.
LONG = 600.0

TWO_RUNS = ("class A:\n"
            "    def run(self):\n"
            "        helper()\n"
            "        self.log('x')\n"
            "        return helper()\n"
            "\n"
            "\n"
            "class B:\n"
            "    def run(self):\n"
            "        other()\n"
            "\n"
            "\n"
            "def helper():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def other():\n"
            "    return 2\n")


def out_of(c, path, line, col=None):
    return calls(c, path, line, "out", col=col).json()


def names(ans):
    return [r["name"] for r in rows_of(ans)]


class _Untrusted(_Repo):

    def client(self):
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        return c, state


# ============================================================== row 1 — untrusted: the call graph answers callees

class Untrusted(_Untrusted):

    def test_untrusted_callees_come_from_the_call_graph_labelled_and_nothing_is_spawned(self):
        c, state = self.client()
        with mock.patch.object(L.Server, "start") as start, mock.patch("mokata.ide.lsp.spawn_jobbed") as spawn:
            a = out_of(c, CORE, 8)
            start.assert_not_called()
            spawn.assert_not_called()
        self.assertEqual(("graph", "untrusted", False), (a["source"], a["lsp"]["state"], a["asked"]), a)
        self.assertEqual("total", a["symbol"])
        self.assertEqual(["len", "parse"], names(a))
        row = rows_of(a)[1]
        self.assertEqual((CORE, 8, 16, True, 1), (row["path"], row["line"], row["col"], row["col_exact"], row["calls"]))
        self.assertEqual("return len(parse(text))  # TODO: cache", row["snippet"])
        self.assertTrue(a["basis"])
        self.assertTrue(a["backend"])
        self.assertNotIn("language server", a["note"])
        self.assertEqual([], state.lsp.status()["servers"])


# ============================================================== row 2 — the function asked about, not its namesakes

class SameNameIsolated(_Untrusted):

    def test_two_methods_named_run_in_one_file_each_list_only_their_own_callees(self):
        _write(self.repo, "src/app/runs.py", TWO_RUNS)
        c, _state = self.client()
        a = out_of(c, "src/app/runs.py", 3)                  # inside A.run
        self.assertEqual(["helper", "log"], names(a))
        self.assertEqual([2, 1], [r["calls"] for r in rows_of(a)])
        b = out_of(c, "src/app/runs.py", 10)                 # inside B.run
        self.assertEqual(["other"], names(b))

    def test_two_functions_named_run_in_two_files_each_list_only_their_own_callees(self):
        _write(self.repo, "src/app/one.py", "def run():\n    return alpha()\n\n\ndef alpha():\n    return 1\n")
        _write(self.repo, "src/app/two.py", "def run():\n    return beta()\n\n\ndef beta():\n    return 2\n")
        c, _state = self.client()
        self.assertEqual(["alpha"], names(out_of(c, "src/app/one.py", 2)))
        self.assertEqual(["beta"], names(out_of(c, "src/app/two.py", 2)))

    def test_a_nested_function_of_the_same_name_keeps_its_calls(self):
        _write(self.repo, "src/app/nest.py",
               "def run():\n"
               "    def run():\n"
               "        return inner_only()\n"
               "    return outer_only()\n")
        c, _state = self.client()
        self.assertEqual(["outer_only"], names(out_of(c, "src/app/nest.py", 4)))
        self.assertEqual(["inner_only"], names(out_of(c, "src/app/nest.py", 3)))


# ============================================================== row 3 — every non-LSP rung answers graph callees

class EveryRung(_Calls):

    def _assert_graph_callees(self, a, state_name):
        self.assertEqual(("graph", state_name), (a["source"], a["lsp"]["state"]), a)
        self.assertEqual(["len", "parse"], names(a), a)
        self.assertTrue(a["lsp"].get("note"), a)
        self.assertNotIn("need the language server", str(a))
        self.assertNotIn("callers only", str(a))

    def test_never_started(self):
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        self._assert_graph_callees(out_of(c, CORE, 8), "never-started")

    def test_unsupported_file_type(self):
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        with mock.patch.object(L, "language_of", lambda rel: None):
            self._assert_graph_callees(out_of(c, CORE, 8), "unsupported")

    def test_indexing(self):
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        from test_p24_ide_lsp_readonly import finder_for
        state.lsp.finder = finder_for("--index-seconds", "600")
        self.assertEqual(200, c.post("/api/lsp/start", json={"language": "python"}, headers=ORIGIN).status_code)
        srv = state.lsp.get("python")
        self.assertTrue(wait_for(lambda: srv.indexing() is not None, timeout=LONG))
        a = out_of(c, CORE, 8)
        self._assert_graph_callees(a, "indexing")
        self.assertIn("the call graph answers meanwhile", a["lsp"]["note"])

    def test_no_call_hierarchy(self):
        c, state, srv = self.serve("--no-call-hierarchy")
        self._assert_graph_callees(out_of(c, CORE, 8), "no-call-hierarchy")
        self.assertFalse([m for m in self.seen() if "allHierarchy" in m])

    def test_crashed(self):
        c, state, srv = self.serve("--crash-on", "callHierarchy/outgoingCalls")
        self._assert_graph_callees(out_of(c, CORE, 8), "crashed")

    def test_server_error(self):
        c, state, srv = self.serve()
        with mock.patch.object(L, "call_edges", side_effect=L.ServerError("fake: boom (code -32603)")):
            a = out_of(c, CORE, 8)
        self._assert_graph_callees(a, "server-error")
        self.assertEqual(-32603, a["lsp"]["code"])


# ============================================================== row 4 — rows: distinct, counted, exact col or not expandable

class Rows(_Untrusted):

    def test_distinct_callees_counted_with_exact_columns_and_a_chain_that_cannot_expand(self):
        _write(self.repo, "src/app/rows.py",
               "def work(self, x):\n"                                   # 1
               "    self.x.helper(1)\n"                                 # 2
               "    helper(2); helper(3)\n"                             # 3
               "    s = 'other(' + str(x)  # other( in a comment\n"     # 4
               "    y = (x\n"                                           # 5
               "         .strip()\n"                                    # 6
               "         )\n"                                           # 7
               "    return été(y)\n")                                   # 8
        c, _state = self.client()
        a = out_of(c, "src/app/rows.py", 2)
        by = {r["name"]: r for r in rows_of(a)}
        self.assertEqual(["helper", "str", "strip", "été"], names(a))
        h = by["helper"]
        self.assertEqual((2, 12, True, 2), (h["line"], h["col"], h["col_exact"], h["calls"]))
        self.assertNotIn("expandable", h)
        self.assertEqual((4, 20, True), (by["str"]["line"], by["str"]["col"], by["str"]["col_exact"]))
        st = by["strip"]                                    # the graph puts it on the chain's first line
        self.assertEqual((5, None, False, False), (st["line"], st["col"], st["col_exact"], st["expandable"]))
        e = by["été"]
        self.assertEqual((8, 12, True), (e["line"], e["col"], e["col_exact"]))
        self.assertEqual(4, a["total"])

    def test_a_line_naming_the_callee_twice_is_not_guessed(self):
        _write(self.repo, "src/app/twice.py",
               "def work(a, b):\n    return a.go(1) + b.go(2)\n")
        c, _state = self.client()
        g = rows_of(out_of(c, "src/app/twice.py", 2))[0]
        self.assertEqual(("go", None, False), (g["name"], g["col"], g["expandable"]))


# ============================================================== row 5 — expanding a callee row

class Expand(_Untrusted):

    def test_an_expanded_callee_row_answers_that_functions_own_callees(self):
        _write(self.repo, "src/app/runs.py", TWO_RUNS)
        c, _state = self.client()
        row = rows_of(out_of(c, "src/app/runs.py", 3))[0]           # helper(), called in A.run
        b = out_of(c, row["path"], row["line"], col=row["col"])
        self.assertEqual("helper", b["symbol"])
        self.assertEqual(1, len(b["groups"]))
        self.assertEqual(("src/app/runs.py", 13), (b["groups"][0]["item"]["path"], b["groups"][0]["item"]["line"]))
        self.assertEqual([], names(b))

    def test_a_callee_with_no_definition_says_so(self):
        _write(self.repo, "src/app/nodef.py", "def work():\n    return missing_fn()\n")
        c, _state = self.client()
        row = rows_of(out_of(c, "src/app/nodef.py", 2))[0]
        b = out_of(c, row["path"], row["line"], col=row["col"])
        self.assertEqual([], b["groups"])
        self.assertIn("the call graph holds no definition of `missing_fn` in this repository", b["note"])

    def test_three_definitions_are_three_groups(self):
        for i in range(3):
            _write(self.repo, f"src/app/d{i}.py", f"def dup():\n    return f{i}()\n")
        _write(self.repo, "src/app/use.py", "def use():\n    return dup()\n")
        c, _state = self.client()
        row = rows_of(out_of(c, "src/app/use.py", 2))[0]
        b = out_of(c, row["path"], row["line"], col=row["col"])
        self.assertEqual((3, 3, 3), (len(b["groups"]), b["items_named"], b["items_asked"]))
        self.assertEqual({"f0", "f1", "f2"}, set(names(b)))

    def test_twenty_five_definitions_ask_twenty_and_say_so(self):
        for i in range(25):
            _write(self.repo, f"src/app/m{i:02d}.py", f"def dup():\n    return g{i}()\n")
        _write(self.repo, "src/app/use.py", "def use():\n    return dup()\n")
        c, _state = self.client()
        row = rows_of(out_of(c, "src/app/use.py", 2))[0]
        b = out_of(c, row["path"], row["line"], col=row["col"])
        self.assertEqual((L.CALLS_ITEMS_MAX, 25, L.CALLS_ITEMS_MAX), (len(b["groups"]), b["items_named"], b["items_asked"]))
        self.assertFalse(b["complete"])


# ============================================================== row 6 — a hub is bounded

class HubIsBounded(_Untrusted):

    def test_fifty_thousand_distinct_callees_are_cut_at_the_cap_with_the_exact_total(self):
        n = 50_000
        with open(os.path.join(self.repo, "src", "app", "hub.py"), "w", encoding="utf-8") as fh:
            fh.write("def hub():\n")
            for i in range(n):
                fh.write(f"    callee_{i}()\n")
        c, _state = self.client()
        a = lsp_get(c, "/api/lsp/calls?path=src/app/hub.py&line=1&dir=out", within=LONG).json()
        self.assertEqual((L.CALLS_MAX, n, True), (len(rows_of(a)), a["total"], a["truncated"]))
        self.assertEqual("callee_0", rows_of(a)[0]["name"])
        self.assertEqual(f"callee_{L.CALLS_MAX - 1}", rows_of(a)[-1]["name"])


# ============================================================== rows 7, 8 — the first index

class _ColdIndex:
    """`AstBackend._walk_index` held on an Event the test owns, and counted."""

    def hold_the_index(self):
        self.gate = threading.Event()
        self.walks = []
        real = AstBackend._walk_index

        def walk(b, *a, **kw):
            self.walks.append(threading.current_thread().name)
            self.gate.wait()
            return real(b, *a, **kw)
        p = mock.patch.object(AstBackend, "_walk_index", walk)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.gate.set)


class LspNeverWaits(_ColdIndex, _Calls):

    def test_a_language_server_answer_on_a_cold_index_says_indexing_and_names_the_job(self):
        c, state, srv = self.serve()
        self.hold_the_index()
        a = calls(c, CORE, 1, "in", col=5).json()
        self.assertEqual("lsp", a["source"], a)
        self.assertEqual("indexing", a["edge"]["state"], a["edge"])
        self.assertTrue(a["edge"]["job"], a["edge"])
        self.assertIn("graph facts: the code graph is indexing", a["edge"]["note"])
        self.assertNotIn("callers", a["edge"])                       # never a 0 shown as computed
        d = lsp_get(c, f"/api/lsp/definition?path={CORE}&line=8&col=16").json()
        self.assertEqual(("lsp", "indexing"), (d["source"], d["edge"]["state"]), d)
        self.assertEqual(1, len(self.walks), self.walks)
        self.gate.set()
        job = state.jobs.get(a["edge"]["job"])
        self.assertTrue(job.finished.wait(LONG))
        self.assertEqual(J.DONE, job.state)
        a2 = calls(c, CORE, 1, "in", col=5).json()
        self.assertNotIn("state", a2["edge"])
        self.assertEqual(2, a2["edge"]["callers"])


class OneBuild(_ColdIndex, _Untrusted):

    def _note(self, c, jid):
        return c.get(f"/api/jobs/{jid}").json()["job"].get("note", "")

    def test_two_graph_askers_and_quick_open_share_one_build_and_a_cancel_ends_only_its_wait(self):
        c, state = self.client()
        self.hold_the_index()
        r1 = c.get(f"/api/lsp/calls?path={CORE}&line=8&dir=out&sync_ms=0")
        r2 = c.get(f"/api/lsp/calls?path={CORE}&line=1&dir=in&sync_ms=0")
        self.assertEqual((202, 202), (r1.status_code, r2.status_code))
        j1, j2 = r1.json()["job"], r2.json()["job"]
        threading.Thread(target=nav.quick_open, args=(self.repo, "@par"), daemon=True).start()
        for j in (j1, j2):
            self.assertTrue(wait_for(lambda j=j: "waiting for the code graph's index" in self._note(c, j),
                                     timeout=LONG), self._note(c, j))
        self.assertEqual(200, c.post(f"/api/jobs/{j1}/cancel", headers=ORIGIN).status_code)
        self.assertTrue(wait_for(lambda: c.get(f"/api/jobs/{j1}").json()["job"]["state"] == "cancelled",
                                 timeout=LONG))
        self.assertTrue(state.jobs.get(j1).stopped.wait(LONG), "the cancelled asker kept waiting on the index")
        build = state.jobs.find("index.symbols", {"root": os.path.abspath(state.source._code_root())})
        self.assertIsNotNone(build, "the shared build stopped with the asker's cancel")
        self.gate.set()
        b = lsp_get(c, f"/api/lsp/calls?path={CORE}&line=1&dir=in&job={j2}", within=LONG).json()
        self.assertEqual("graph", b["source"])
        self.assertIn("total", names(b))
        self.assertTrue(build.finished.wait(LONG))
        self.assertEqual(J.DONE, build.state)
        self.assertEqual(1, len(self.walks), self.walks)

    def test_a_cancelled_shared_build_is_said_never_rebuilt_inside_the_asker(self):
        c, state = self.client()
        self.hold_the_index()
        r = c.get(f"/api/lsp/calls?path={CORE}&line=8&dir=out&sync_ms=0")
        jid = r.json()["job"]
        self.assertTrue(wait_for(lambda: "waiting for the code graph's index" in self._note(c, jid), timeout=LONG))
        build = state.jobs.find("index.symbols", {"root": os.path.abspath(state.source._code_root())})
        self.assertEqual(200, c.post(f"/api/jobs/{build.id}/cancel", headers=ORIGIN).status_code)
        self.gate.set()
        a = lsp_get(c, f"/api/lsp/calls?path={CORE}&line=8&dir=out&job={jid}", within=LONG).json()
        self.assertEqual([], names(a))
        self.assertIn("the code graph's index did not finish", a["note"])
        self.assertEqual(1, len(self.walks), self.walks)


# ============================================================== row 9 — a kept answer after a revoke

class KeptAfterRevoke(_Calls):

    def _done_job(self, c, url):
        r = c.get(url + "&sync_ms=0")
        self.assertEqual(202, r.status_code, r.text)
        jid = r.json()["job"]
        self.assertTrue(wait_for(lambda: c.get(f"/api/jobs/{jid}").json()["job"]["state"] == "done", timeout=LONG))
        return jid

    def test_a_language_server_answer_fetched_by_job_after_a_revoke_is_re_asked_from_the_graph(self):
        c, state, srv = self.serve()
        url = f"/api/lsp/calls?path={CORE}&line=8&dir=out"
        jid = self._done_job(c, url)
        self.assertEqual("lsp", c.get(f"/api/jobs/{jid}").json()["result"]["source"])
        T.decide(self.repo, False, "test")                      # a CLI `--untrust`: the window never saw it
        n = len(srv.sent)
        a = lsp_get(c, f"{url}&job={jid}").json()
        self.assertEqual(("graph", "untrusted", False), (a["source"], a["lsp"]["state"], a["asked"]), a)
        self.assertEqual(["len", "parse"], names(a))
        self.assertFalse([m for m in srv.sent[n:] if m.startswith(("textDocument/", "callHierarchy/"))], srv.sent[n:])
        self.assertTrue(wait_for(lambda: not srv.alive, timeout=LONG), "the server kept running after the revoke")
        self.assertIsNone(c.get(f"/api/jobs/{jid}").json()["result"], "the jobs door still hands it out")

    def test_the_jobs_door_hands_out_no_kept_language_server_answer_after_a_revoke(self):
        c, state, srv = self.serve()
        jid = self._done_job(c, f"/api/lsp/definition?path={CORE}&line=8&col=16")
        T.decide(self.repo, False, "test")
        got = c.get(f"/api/jobs/{jid}").json()
        self.assertIsNone(got["result"])
        self.assertIsNone(got["partial"])

    def test_an_answer_no_server_was_asked_for_is_still_served_from_its_job(self):
        T.decide(self.repo, False, "test")
        c, state = boot(self.repo)
        self.addCleanup(state.close)
        url = f"/api/lsp/calls?path={CORE}&line=8&dir=out"
        jid = self._done_job(c, url)
        question = {"route": "/api/lsp/calls", "dir": "out", "line": "8", "path": CORE}
        self.assertIsNotNone(state.jobs.answer(jid, "lsp.query", question))
        self.assertEqual("graph", c.get(f"/api/jobs/{jid}").json()["result"]["source"])

    def test_kept_while_marks_only_a_running_job(self):
        self.assertFalse(J.kept_while(lambda: True))


if __name__ == "__main__":
    unittest.main()
