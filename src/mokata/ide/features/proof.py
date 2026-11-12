"""`GET /api/proof` · `GET /api/proof/targets` — the AC → test → code trace and its risk overlay
(0.0.27 P13, `ID-VIS-PROOF`; ruling U15: the launch demo).

`/api/proof` is a JOB route (`proof.trace`, the query lane): the trace asks the code graph one
question per changed definition, so on a wide change set it runs visibly (progress per file,
Cancel, the Activity drawer) and answers 202 + a handle past the sync window; the page follows it
as it follows every other job. The target is validated HERE, before any work starts — a bad run id,
a half-named range or no target at all is a 400 at once, never a failed job.

`/api/proof/targets` is a light read: the runs that have a spec, the sessions with recorded
changes, whether a commit range can be asked, and the default the view opens on.

The trace builder itself is `mokata.ide.proof` (data only, no HTTP); the answer's shape is
`proof.contract.json` beside this file (versioned: `proof.TRACE_VERSION`). P46 consumes it.
"""

from __future__ import annotations

from typing import Any, Dict


def routes(reg, ctx) -> None:
    state, q = ctx.state, ctx.q

    def _int(request, name: str, default: int, lo: int, hi: int) -> int:
        raw = q(request, name)
        if not raw:
            return default
        if not raw.isdigit() or not lo <= int(raw) <= hi:
            raise ctx.bad_request(f"{name} must be a whole number between {lo} and {hi}")
        return int(raw)

    def proof(request):
        from .. import proof as P
        run, session = q(request, "run")[:128], q(request, "session")[:200]
        base, head = q(request, "base")[:200], q(request, "head")[:200]
        live = q(request, "live") == "1"
        page = _int(request, "page", 1, 1, 100000)
        size = _int(request, "size", P.PAGE_ROWS, 1, P.MAX_PAGE_ROWS)
        if bool(base) != bool(head):
            raise ctx.bad_request("a commit range needs both base and head")
        if not (run or session or base or live):
            raise ctx.bad_request("name a run, a session, a commit range (base + head) or "
                                  "live=1 (the working tree)")
        if sum(1 for x in (session, base, live) if x) > 1:
            raise ctx.bad_request("ask about ONE change set: a session, a commit range or the "
                                  "working tree")
        if run:
            from ..plans import check_run_id
            check_run_id(run)
            if not state.demo:
                from ..plans import local_run_ids
                if run not in set(local_run_ids(state.root)):
                    raise ctx.bad_request(f"no run {run} has state in this repository")
        if (base or live) and not state.demo:
            from .. import diffs as D
            if not D.is_git(state.root):
                raise ctx.bad_request("a commit range or the working tree needs a git repository")

        def work() -> Dict[str, Any]:
            try:
                return state.source.proof(run, session, base, head, live, page, size)
            except P.TraceRefused as exc:
                raise ctx.bad_request(str(exc)) from exc
        return work

    def label(request) -> str:
        what = (f"session {q(request, 'session')[:12]}" if q(request, "session") else
                f"{q(request, 'base')[:30]}...{q(request, 'head')[:30]}" if q(request, "base") else
                "working tree" if q(request, "live") == "1" else
                f"run {q(request, 'run')[:40]}")
        pg = q(request, "page")
        return f"proof · {what}" + (f" · page {pg[:6]}" if pg and pg != "1" else "")

    def targets(_request) -> Dict[str, Any]:
        return state.source.proof_targets()

    reg.register("/api/proof", proof, kind="json", feature="proof", job="proof.trace",
                 job_label=label)
    reg.register("/api/proof/targets", targets, kind="json", feature="proof")


# ------------------------------------------------------------------------------------- SAMPLE

def sample_trace(*, run: str = "", session: str = "", base: str = "", head: str = "",
                 live: bool = False, page: int = 1, size: int = 200) -> Dict[str, Any]:
    """The SAMPLE trace (labelled `sample: True` on the payload, like every demo answer): one AC
    proven RED → GREEN, one covered by a test that never went red, one with no test; a changed
    definition no test reaches; one gate red and one skipped. The page shows exactly what the
    real view shows, with nothing real behind it."""
    from .. import proof as P
    rid = run or "run-parser-fix"
    t0 = 1_700_000_000.0
    acs = [
        {"id": "ac:AC-1", "ac": "AC-1", "text": "empty input parses to an empty list",
         "kind": "behaviour", "tests": ["test:tests/test_parser.py::test_empty_input"],
         "code": ["code:src/parser.py#parse"], "anchors": [{"file": "src/parser.py",
                                                            "symbol": "parse", "kind": "impl",
                                                            "source": "spec"}],
         "status": P.AC_PROVEN, "risk": 0, "proven_by": ["test:tests/test_parser.py::test_empty_input"],
         "note": ""},
        {"id": "ac:AC-2", "ac": "AC-2", "text": "whitespace around items is stripped",
         "kind": "behaviour", "tests": ["test:tests/test_parser.py::test_strip"],
         "code": ["code:src/parser.py#parse"], "anchors": [], "status": P.AC_GREEN_NOT_TDD,
         "risk": 1, "proven_by": [], "note": ""},
        {"id": "ac:AC-3", "ac": "AC-3", "text": "a malformed item is reported with its position",
         "kind": "behaviour", "tests": [], "code": [], "anchors": [], "status": P.AC_UNTESTED,
         "risk": 3, "proven_by": [], "note": ""},
    ]
    tests = [
        {"id": "test:tests/test_parser.py::test_empty_input", "label": "tests/test_parser.py::test_empty_input",
         "path": "tests/test_parser.py", "line": 4, "symbol": "test_empty_input", "located": True,
         "changed": True, "recorded_ids": ["tests/test_parser.py::test_empty_input"], "acs": ["ac:AC-1"],
         "code": ["code:src/parser.py#parse"], "tdd": P.TDD_PROVEN, "red_at": t0 + 60,
         "green_at": t0 + 600, "last": "green", "last_at": t0 + 600, "basis": "timed",
         "ambiguous": [], "state": "present", "reason": "", "params": [], "qualifier": "",
         "tdd_note": "", "qualified_from": []},
        {"id": "test:tests/test_parser.py::test_strip", "label": "tests/test_parser.py::test_strip",
         "path": "tests/test_parser.py", "line": 9, "symbol": "test_strip", "located": True,
         "changed": True, "recorded_ids": ["tests/test_parser.py::test_strip"], "acs": ["ac:AC-2"],
         "code": ["code:src/parser.py#parse"], "tdd": P.TDD_GREEN_ONLY, "red_at": None,
         "green_at": None, "last": "green", "last_at": t0 + 600, "basis": "timed", "ambiguous": [],
         "state": "present", "reason": "", "params": [], "qualifier": "",
         "tdd_note": "", "qualified_from": []},
    ]
    code = [
        {"id": "code:src/parser.py#parse", "label": "src/parser.py#parse", "path": "src/parser.py",
         "symbol": "parse", "kind": "function", "line": 1, "changed": True, "status": "modified",
         "added": 3, "removed": 1, "coverage": P.COV_COVERED, "fan_in": 4, "blast": 9,
         "blast_cut": False, "full_available": False, "basis": "structural",
         "tests": ["test:tests/test_parser.py::test_empty_input", "test:tests/test_parser.py::test_strip"],
         "acs": ["ac:AC-1", "ac:AC-2"],
         "approvals": [{"decision": "redeemed", "actor": "human", "at": "SAMPLE", "proposal": "p-1"}],
         "note": "", "risk": 0},
        {"id": "code:src/parser.py#report_error", "label": "src/parser.py#report_error",
         "path": "src/parser.py", "symbol": "report_error", "kind": "function", "line": 12,
         "changed": True, "status": "modified", "added": 6, "removed": 0,
         "coverage": P.COV_UNTESTED, "fan_in": 2, "blast": 3, "blast_cut": False,
         "full_available": False, "basis": "structural", "tests": [], "acs": [], "approvals": [],
         "note": "", "risk": 3},
    ]
    edges = [
        {"source": "ac:AC-1", "target": "test:tests/test_parser.py::test_empty_input",
         "kind": P.EDGE_AC_TEST, "basis": "recorded", "site": ""},
        {"source": "ac:AC-1", "target": "code:src/parser.py#parse", "kind": P.EDGE_AC_CODE,
         "basis": "anchor", "site": "src/parser.py"},
        {"source": "ac:AC-2", "target": "test:tests/test_parser.py::test_strip",
         "kind": P.EDGE_AC_TEST, "basis": "planned", "site": ""},
        {"source": "test:tests/test_parser.py::test_empty_input", "target": "code:src/parser.py#parse",
         "kind": P.EDGE_TEST_CODE, "basis": "structural", "site": "tests/test_parser.py:4"},
        {"source": "test:tests/test_parser.py::test_strip", "target": "code:src/parser.py#parse",
         "kind": P.EDGE_TEST_CODE, "basis": "structural", "site": "tests/test_parser.py:9"},
    ]
    gates = [
        {"gate": "no-code-without-failing-test", "verdict": P.GATE_RED, "at": "SAMPLE",
         "path": "src/parser.py", "detail": "blocked", "source": "progress-log", "attached": "run"},
        {"gate": "write-gate", "verdict": P.GATE_PASSED, "at": "SAMPLE", "path": "src/parser.py",
         "detail": "approved", "source": "ledger", "attached": "run"},
        {"gate": "stop-evidence", "verdict": P.GATE_SKIPPED, "at": "SAMPLE", "path": "",
         "detail": "stop let through unverified: no test is recorded for AC-3", "source": "ledger",
         "attached": "run"},
    ]
    kind = (P.TARGET_SESSION if session else P.TARGET_RANGE if base else
            P.TARGET_LIVE if live else P.TARGET_RUN)
    nodes = {"acs": acs, "tests": tests, "code": code}
    return {"sample": True, "version": P.TRACE_VERSION,
            "target": {"kind": kind, "run": rid, "session": session or None, "base": base,
                       "head": head, "title": f"SAMPLE · {kind} {session or base or rid}",
                       "basis": "SAMPLE"},
            "files": [{"path": "src/parser.py", "status": "modified", "added": 9, "removed": 1,
                       "too_large": False, "binary": False, "lines": "1-4,12-18", "hunks": 2,
                       "is_test": False},
                      {"path": "tests/test_parser.py", "status": "modified", "added": 8,
                       "removed": 0, "too_large": False, "binary": False, "lines": "4-12",
                       "hunks": 1, "is_test": True}],
            "nodes": nodes, "edges": edges, "gates": gates,
            "approvals": [{"proposal": "p-1", "decision": "redeemed", "target": "src/parser.py",
                           "actor": "human", "at": "SAMPLE", "tool": "write_file"}],
            "risk": {"untested_acs": ["AC-3"], "red_acs": [],
                     "tests_not_tdd_proven": ["test:tests/test_parser.py::test_strip"],
                     "tests_ambiguous": [], "tests_deleted": [], "tests_non_proving": [],
                     "code_without_test": ["code:src/parser.py#report_error"],
                     "code_coverage_unknown": [],
                     "gates_red": [gates[0]], "gates_skipped": [gates[2]], "gates_unknown": [],
                     "blast": {"fan_in_total": 6, "cut": 0,
                               "top": [{"id": "code:src/parser.py#report_error", "fan_in": 2,
                                        "risk": 3, "coverage": P.COV_UNTESTED}]},
                     "graph_basis": "structural", "score": 3 + 1 + 3 + 10},
            "record": {"last_suite": {"at": t0 + 600, "verdict": "passed", "reason": "",
                                      "failed": [], "not_run": [],
                                      "said": "the last recorded test run passed",
                                      "command": "pytest -q",
                                      "tests": ["tests/test_parser.py::test_empty_input",
                                                "tests/test_parser.py::test_strip"]},
                       "red": 1, "green": 2, "timed": 2, "recorded_acs": 1, "planned_acs": 1},
            "graph": {"basis": "structural", "backend": "SAMPLE", "bases": {"structural": 2},
                      "note": ""},
            "page": {"n": page, "size": size, "rows": 4, "total_rows": 4, "total_acs": 3,
                     "total_tests": 2, "total_code": 2, "next": None},
            "draw": {"limit": 150, "total": 7, "reduced": False, "note": "",
                     "shown": sorted(n["id"] for v in nodes.values() for n in v)},
            "note": "SAMPLE — nothing here is real"}
