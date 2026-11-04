"""`/api/review/*` — the review context beside the diff (0.0.26 P7 `ID-REVIEW-CONTEXT`).

  GET  /api/review/tests        the run's tests GROUPED BY ITS ACs: each AC's mapped tests, each
                                test's latest status and red/green history, untested ACs flagged,
                                tests no AC claims, and the approval that let the change in
                                (`?run=&page=&per=` pages the ACs; `&ac=&tpage=` pages one AC's
                                tests)
  POST /api/review/tests/run    `{run, tests: [...]} | {run, ac} | {run, failing: true} |
                                {run, all: true}` -> `{id, job}`: re-run those tests with the
                                project's own test command, as a visible `tests.run` job
  GET  /api/review/spec-diff    `?run=&a=&b=`: two versions of the run's spec as Markdown (the
                                page renders both side by side) and the AC-level change list
  GET  /api/review/doc          `?path=&rev=`: a Markdown file's text, in the working tree or at a
                                revision (the rendered view, the rendered diff of a changed .md)
  GET  /api/review/blame        `?path=&hunk=&base=&head=`: blame + line history of ONE hunk
                                (`review.blame` job — 202 with the handle past the sync window)

Everything is read from records mokata already keeps (`ide/review_context.py`); the only work this
starts is the user's own test command, as a job they can watch and cancel. The one write: a re-run's
outcome goes into the run's test record (run-state, `test_runs.record_test_run`, the Stop gate's
evidence) with its provenance — `source: "ide"`, the human, the command, exit code, time (ruled
2026-10-28).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

from starlette.responses import JSONResponse

from .. import review_context as RC

SAMPLE_RUN = "sample-run"


def _sample_tests() -> Dict[str, Any]:
    """The SAMPLE's panel — invented, labelled, nothing read from disk."""
    def t(tid: str, hist: List[str], never_red: bool = False) -> Dict[str, Any]:
        return {"id": tid, "status": hist[-1] if hist else "unrun", "never_red": never_red,
                "mapped": "both", "history": [{"state": s, "basis": "tdd record"} for s in hist]}
    crit = [
        {"id": "AC-1", "text": "SAMPLE — an empty input parses to an empty list", "kind": "behaviour",
         "status": "green", "untested": False, "tests": [t("tests/test_parse.py::test_empty",
                                                           ["red", "green"])]},
        {"id": "AC-2", "text": "SAMPLE — whitespace around items is dropped", "kind": "behaviour",
         "status": "red", "untested": False, "tests": [t("tests/test_parse.py::test_strip", ["red"]),
                                                      t("tests/test_parse.py::test_keep", ["green"],
                                                        True)]},
        {"id": "AC-3", "text": "SAMPLE — a trailing comma is an error", "kind": "behaviour",
         "status": "untested", "untested": True, "tests": []},
    ]
    for c in crit:
        c.update(tests_total=len(c["tests"]), tests_pages=1, tests_page=1)
    return {"sample": True, "run": SAMPLE_RUN, "state": "ok",
            "spec": {"title": "SAMPLE — fix the parser", "version": 2},
            "approval": {"state": "ok", "approach": "tokenize first", "approver": "SAMPLE human",
                         "approved_at": "", "spec_version": 2},
            "command": {"configured": False, "text": "", "argv": [], "runnable": False,
                        "note": "the SAMPLE runs no tests (`mokata ide` without --demo)"},
            "last_suite": None, "rerun_note": RC.RERUN_NOTE, "page": 1, "per": RC.PER_PAGE,
            "pages": 1, "status": "", "matching": 3, "totals": {"acs": 3, "untested": 1, "green": 1, "red": 1, "unrun": 0,
                                   "tests": 3, "never_red": 1},
            "criteria": crit, "unmapped": {"total": 0, "tests": []}}


def _sample_spec_diff() -> Dict[str, Any]:
    old = "# SAMPLE — fix the parser\n\n## Acceptance criteria\n\n- **AC-1** an empty input " \
          "parses to an empty list\n"
    new = old + "- **AC-2** whitespace around items is dropped\n\n```mermaid\ngraph LR\n  " \
                "tokenize --> parse\n```\n"
    return {"sample": True, "run": SAMPLE_RUN, "state": "ok", "current_version": 2,
            "versions": [1, 2],
            "old": {"version": 1, "state": "ok", "title": "SAMPLE — fix the parser",
                    "markdown": old},
            "new": {"version": 2, "state": "ok", "title": "SAMPLE — fix the parser",
                    "markdown": new},
            "criteria": {"added": [{"id": "AC-2", "text": "whitespace around items is dropped"}],
                         "removed": [], "changed": [], "same": 1},
            "approval": {"state": "ok", "approach": "tokenize first", "approver": "SAMPLE human",
                         "approved_at": "", "spec_version": 2}}


def routes(reg: Any, ctx: Any) -> None:
    state = ctx.state
    q = ctx.q
    lock = threading.Lock()
    #: run -> test id -> [{state, at, job}] — the re-runs started from THIS window, for their
    #: per-run history in the panel; each re-run is ALSO written to the run's test record, with
    #: provenance (`RC.record_rerun`, `RC.RERUN_NOTE`).
    reruns: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def _unset(what: str):
        from ..statedir import initialized, not_initialized
        return None if initialized(state.root) else not_initialized(what)

    def _reruns(rid: str) -> Dict[str, List[Dict[str, Any]]]:
        with lock:
            return {k: list(v) for k, v in reruns.get(rid, {}).items()}

    # ------------------------------------------------------------------- tests by AC
    def tests(request: Any) -> Dict[str, Any]:
        if state.demo:
            return _sample_tests()
        unset = _unset("a run's tests by acceptance criterion")
        if unset is not None:
            return unset
        rid = q(request, "run")
        return RC.tests_panel(state.root, rid, page=q(request, "page"), per=q(request, "per"),
                              ac=q(request, "ac"), tpage=q(request, "tpage"),
                              status=q(request, "status"),
                              reruns=_reruns(rid.strip()), command=RC.test_command(state.root))
    reg.register("/api/review/tests", tests, kind="json", feature="review")

    async def run(request: Any):
        b = await ctx.body(request)
        try:
            if state.demo:
                state.attach_loop()
                job = state.jobs.start("tests.run", {"route": "/api/review/tests/run",
                                                     "run": SAMPLE_RUN, "sample": True},
                                       lambda: {"sample": True, "tests": [], "command": "",
                                                "env": "", "output": "SAMPLE — nothing ran\n",
                                                "output_bytes": 0, "passed": 0, "failed": 0},
                                       label="tests · SAMPLE (nothing runs)", scope="SAMPLE")
                return JSONResponse({"id": job.id, "job": job.snapshot()})
            if _unset("tests") is not None:
                raise ctx.bad_request("this repository is not set up for mokata — it has no run "
                                      "whose tests could be re-run (`mokata init`)")
            rid = b.get("run") if isinstance(b.get("run"), str) else ""
            mine = RC.run_tests_of(state.root, rid, _reruns(rid.strip()))
            latest = mine["latest"]
            if b.get("all") is True:
                ids = sorted(latest)
            elif b.get("failing") is True:
                ids = sorted(t for t, st in latest.items() if st == "red")
                if not ids:
                    raise ctx.bad_request("no test of this run is red")
            elif isinstance(b.get("ac"), str) and b["ac"]:
                if b["ac"] not in mine["by_ac"]:
                    raise ctx.bad_request(f"the spec has no criterion {b['ac'][:80]!r}")
                ids = mine["by_ac"][b["ac"]]
                if not ids:
                    raise ctx.bad_request(f"{b['ac'][:80]} has no test mapped to it — it is "
                                          "untested")
            elif isinstance(b.get("tests"), list):
                ids = RC.check_test_ids(b["tests"])
                stray = [t for t in ids if t not in latest]
                if stray:
                    raise ctx.bad_request(f"{stray[0][:120]!r} is not a test this run's ACs map "
                                          "— the panel re-runs the run's own tests")
            else:
                raise ctx.bad_request("say which tests: {tests: [...]}, {ac}, {failing: true} "
                                      "or {all: true}")
            ids = RC.check_test_ids(ids)
            cmd = RC.test_command(state.root)
            if not cmd["runnable"]:
                raise ctx.bad_request(cmd["note"])
        except ctx.bad_request as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)
        rid = rid.strip()
        root, argv = state.root, cmd["argv"]
        holder: Dict[str, str] = {}

        def on_test(tid: str, st: str, at: float) -> None:
            with lock:
                reruns.setdefault(rid, {}).setdefault(tid, []).append(
                    {"state": st, "at": at, "job": holder.get("id", "")})

        state.attach_loop()
        client = request.headers.get("x-mokata-client") or None
        job = state.jobs.start("tests.run", {"route": "/api/review/tests/run", "run": rid,
                                             "tests": ids},
                               lambda: RC.run_tests(
                                   root, argv, ids, on_test,
                                   on_done=lambda res, done: RC.record_rerun(
                                       root, rid, cmd["text"], res, done)),
                               label=f"tests · {len(ids)} · run {rid[:8]}",
                               scope=os.path.basename(state.root), client=client)
        holder["id"] = job.id
        return JSONResponse({"id": job.id, "job": job.snapshot()})
    reg.register("/api/review/tests/run", run, kind="action", feature="review")

    # ------------------------------------------------------------------- spec as Markdown
    def spec_diff(request: Any) -> Dict[str, Any]:
        if state.demo:
            return _sample_spec_diff()
        unset = _unset("a run's spec versions")
        if unset is not None:
            return unset
        return RC.spec_diff(state.root, q(request, "run"), q(request, "a"), q(request, "b"))
    reg.register("/api/review/spec-diff", spec_diff, kind="json", feature="review")

    def doc(request: Any) -> Dict[str, Any]:
        if state.demo:
            path = q(request, "path")
            if not path.lower().endswith(RC.MD_EXT):
                raise ctx.bad_request("only Markdown files (.md, .markdown, .mdx) render here")
            return {"sample": True, "path": path, "rev": q(request, "rev"), "state": "ok",
                    "bytes": 0, "text": "# SAMPLE\n\nNothing here is from your repository.\n"}
        return RC.doc_text(state.root, q(request, "path"), q(request, "rev"))
    reg.register("/api/review/doc", doc, kind="json", feature="review")

    # ------------------------------------------------------------------- blame on the hunk
    def blame(request: Any):
        path, hunk = q(request, "path"), q(request, "hunk")
        base, head = q(request, "base") or "HEAD", q(request, "head")
        RC.parse_hunk(hunk)                       # validate NOW: a bad request is a 400 at once
        RC.check_rev(base, "base")
        if head:
            RC.check_rev(head, "head")
        if not path:
            raise ctx.bad_request("path is required")
        if state.demo:
            return lambda: {"sample": True, "path": path, "state": "sample",
                            "note": "the SAMPLE has no git history"}
        root = state.root
        return lambda: RC.hunk_blame(root, path, hunk, base, head)
    reg.register("/api/review/blame", blame, kind="json", feature="review", job="review.blame",
                 job_label=lambda r: f"blame · {q(r, 'path')[:120]}")
