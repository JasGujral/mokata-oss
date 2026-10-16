"""Where the review window's data comes from: the REAL repo, or the labelled SAMPLE (0.0.22 E2).

Both sources answer the same questions with the same shapes, so the UI has one code path. The demo
answers carry `sample: True` on every payload and the UI shows a SAMPLE banner whenever it sees
one (doc 85 §7g: an absent answer and a real one must never share a representation — and neither
may a made-up one). The demo writes nothing and reads nothing from disk.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import diffs as D


class RealSource:
    sample = False

    def __init__(self, root: str) -> None:
        self.root = root

    def state(self, focus: Optional[str]) -> Dict[str, Any]:
        from . import sessions as SS
        rows = [r.facts() for r in SS.sessions(self.root)]
        return {"sample": False, "repo": self.root, "focus": focus, "sessions": rows,
                "git": D.is_git(self.root), "branches": D.branches(self.root)}

    def live(self) -> Dict[str, Any]:
        return {"sample": False, **D.live(self.root).facts()}

    def session(self, sid: str) -> Dict[str, Any]:
        return {"sample": False, **D.session_diff(self.root, sid).facts()}

    def timeline(self, sid: Optional[str]) -> Dict[str, Any]:
        from . import snapshots as S
        snaps = S.timeline(self.root, session=sid) if sid else S.timeline(self.root)
        return {"sample": False, "entries": [s.facts() for s in snaps[-500:]]}

    def between(self, a: str, b: str) -> Dict[str, Any]:
        return {"sample": False, **D.between(self.root, a, b).facts()}

    def compare(self, base: str, head: str) -> Dict[str, Any]:
        return {"sample": False, **D.refs(self.root, base, head).facts()}

    def pr(self, url: str) -> Dict[str, Any]:
        return {"sample": False, **D.pr(self.root, url).facts()}

    # --- 0.0.23 C1 navigation: every answer from the graph layer (see `nav`) ----------------
    def tree(self) -> Dict[str, Any]:
        from . import nav
        return nav.tree(self.root)

    def file(self, path: str) -> Dict[str, Any]:
        from . import nav
        return nav.read_file(self.root, path)

    def outline(self, path: str) -> Dict[str, Any]:
        from . import nav
        rel = nav.contained_rel(self.root, path)
        return {"sample": False, **nav.outline(rel, D._read_text(self.root, rel))}

    def quick(self, q: str, changed: List[str]) -> Dict[str, Any]:
        from . import nav
        return nav.quick_open(self.root, q, changed)

    def query(self, kind: str, target: str, depth: int) -> str:
        from . import nav
        return nav.query_payload(nav.run(self.root, kind, target, depth)) + "\n"

    def impact(self, path: str, lines: str) -> Dict[str, Any]:
        from . import nav
        return nav.impact(self.root, path, lines)

    # --- 0.0.23 C2 search ---------------------------------------------------------------
    def search(self, q: str, mode: str, case: bool, only: Optional[List[str]]) -> Dict[str, Any]:
        from . import search
        return search.text(self.root, q, mode, case, only)

    def meaning(self, q: str, only: Optional[List[str]]) -> Dict[str, Any]:
        from . import search
        return search.meaning(self.root, q, only)

    def semantic_status(self) -> Dict[str, Any]:
        from . import search
        return {"sample": False, **search.status()}

    def semantic_fetch(self) -> Dict[str, Any]:
        from . import search
        return {"sample": False, **search.fetch_model()}

    # --- 0.0.23 C3 graph views ----------------------------------------------------------
    def graph(self, view: str, symbol: str, to: str, depth: int) -> Dict[str, Any]:
        from . import visuals as V
        if view == "focus":
            return V.focus(self.root, symbol)
        if view == "path":
            return V.path(self.root, symbol, to, depth)
        if view == "untested":
            return V.untested(self.root, symbol, depth)
        return V.ripple(self.root, symbol, depth)

    # --- 0.0.23 C5 the plan, the spec and the lifecycle of a run (see `plans`) --------------
    def runs(self, source: str) -> Dict[str, Any]:
        from . import plans
        return plans.team_runs(self.root) if plans.check_source(source) == "team" \
            else plans.local_runs(self.root)

    def plan(self, run: str, source: str, version: int) -> Dict[str, Any]:
        from . import plans
        return plans.plan(self.root, run, source, version)

    def lifecycle(self, run: str) -> Dict[str, Any]:
        from . import plans
        return plans.lifecycle(self.root, run)


# ------------------------------------------------------------------------------------- SAMPLE

_A_OLD = "def parse(text):\n    return text.split(',')\n"
_A_NEW = ("def parse(text):\n    if not text:\n        return []\n"
          "    return [p.strip() for p in text.split(',')]\n")
_B_OLD = "TIMEOUT = 5\n\ndef fetch(url):\n    return get(url, timeout=TIMEOUT)\n"
_B_NEW = ("TIMEOUT = 10\nRETRIES = 3\n\ndef fetch(url):\n    for _ in range(RETRIES):\n"
          "        try:\n            return get(url, timeout=TIMEOUT)\n"
          "        except Timeout:\n            continue\n    raise Timeout(url)\n")
_T_NEW = "def test_empty_input_gives_empty_list():\n    assert parse('') == []\n"


def _fake(title: str, files: List[D.FileDiff], note: str = "") -> Dict[str, Any]:
    return {"sample": True, **D.DiffSet("SAMPLE · " + title, files, note).facts()}


class DemoSource:
    """Two parallel sessions on two runs, a timeline, a live diff and a PR — all invented."""
    sample = True

    def __init__(self) -> None:
        now = time.time()
        self._t0 = now - 3600
        self._sessions = [
            {"id": "sample-session-a", "short": "sample-A", "run": "run-parser-fix",
             "files": ["src/parser.py", "tests/test_parser.py"], "writes": 3, "claim": "bound",
             "first": now - 3600, "last": now - 300, "last_iso": "SAMPLE"},
            {"id": "sample-session-b", "short": "sample-B", "run": "run-retry-fetch",
             "files": ["src/net.py"], "writes": 2, "claim": "bound",
             "first": now - 2400, "last": now - 600, "last_iso": "SAMPLE"},
        ]

    def state(self, focus: Optional[str]) -> Dict[str, Any]:
        return {"sample": True, "repo": "SAMPLE repository (nothing here is real)",
                "focus": focus or self._sessions[0]["id"], "sessions": self._sessions,
                "git": True, "branches": ["main", "sample/parser-fix", "sample/retry"]}

    def live(self) -> Dict[str, Any]:
        return _fake("Live changes", [D.text_diff("src/parser.py", _A_OLD, _A_NEW),
                                      D.text_diff("src/net.py", _B_OLD, _B_NEW),
                                      D.text_diff("tests/test_parser.py", "", _T_NEW)])

    def session(self, sid: str) -> Dict[str, Any]:
        if sid.endswith("session-b"):
            return _fake("What session sample-B wrote", [D.text_diff("src/net.py", _B_OLD, _B_NEW)])
        return _fake("What session sample-A wrote",
                     [D.text_diff("tests/test_parser.py", "", _T_NEW),
                      D.text_diff("src/parser.py", _A_OLD, _A_NEW)])

    def timeline(self, sid: Optional[str]) -> Dict[str, Any]:
        t = self._t0
        rows = [("sample-session-a", "run-parser-fix", "tests/test_parser.py", "before", 0),
                ("sample-session-a", "run-parser-fix", "tests/test_parser.py", "after", 300),
                ("sample-session-a", "run-parser-fix", "src/parser.py", "before", 900),
                ("sample-session-a", "run-parser-fix", "src/parser.py", "after", 960),
                ("sample-session-b", "run-retry-fetch", "src/net.py", "before", 1200),
                ("sample-session-b", "run-retry-fetch", "src/net.py", "after", 1300),
                ("sample-session-a", "run-parser-fix", "src/parser.py", "after", 3300)]
        entries = [{"id": f"sample-{i}", "t": t + dt, "session": s, "run": r, "path": p,
                    "kind": k, "sha": "", "state": "ok", "size": 0}
                   for i, (s, r, p, k, dt) in enumerate(rows) if not sid or s == sid]
        return {"sample": True, "entries": entries}

    def between(self, a: str, b: str) -> Dict[str, Any]:
        return _fake(f"src/parser.py: {a} → {b}", [D.text_diff("src/parser.py", _A_OLD, _A_NEW)])

    def compare(self, base: str, head: str) -> Dict[str, Any]:
        return _fake(f"{base}...{head}", [D.text_diff("src/net.py", _B_OLD, _B_NEW)])

    # --- navigation over the SAMPLE: its three invented files; no graph behind them ---------
    _FILES = {"src/parser.py": _A_NEW, "src/net.py": _B_NEW, "tests/test_parser.py": _T_NEW}
    _NO_GRAPH = ("the SAMPLE has no code graph — structural answers need a real repository "
                 "(`mokata ide` without --demo)")

    def tree(self) -> Dict[str, Any]:
        files = sorted(self._FILES)
        return {"sample": True, "count": len(files), "files": files, "truncated": False,
                "pruned": "SAMPLE"}

    def file(self, path: str) -> Dict[str, Any]:
        text = self._FILES.get(path)
        if text is None:
            return {"sample": True, "path": path, "text": None, "note": "not in the SAMPLE"}
        n = text.count("\n")
        return {"sample": True, "path": path, "text": text, "lines": n,
                "changes": [{"start": 1, "end": n, "kind": "mod"}], "changes_against": "SAMPLE"}

    def outline(self, path: str) -> Dict[str, Any]:
        from . import nav
        return {"sample": True, **nav.outline(path, self._FILES.get(path))}

    def quick(self, q: str, changed: List[str]) -> Dict[str, Any]:
        from . import nav
        rows, matcher = nav.fuzzy(q, [c for c in changed if c in self._FILES])
        rest, _ = nav.fuzzy(q, [f for f in sorted(self._FILES) if f not in changed])
        return {"sample": True, "q": q, "matcher": matcher, "matcher_note": "",
                "changed": [{"path": c, "score": s} for c, s in rows],
                "files": [{"path": c, "score": s} for c, s in rest], "symbols": []}

    def query(self, kind: str, target: str, depth: int) -> str:
        import json
        return json.dumps({"sample": True, "kind": kind, "target": target, "references": [],
                           "basis": "", "note": self._NO_GRAPH}) + "\n"

    def impact(self, path: str, lines: str) -> Dict[str, Any]:
        return {"sample": True, "path": path, "symbols": [], "status": "unknown",
                "is_test": path.startswith("tests/"), "note": self._NO_GRAPH}

    def search(self, q: str, mode: str, case: bool, only: Optional[List[str]]) -> Dict[str, Any]:
        from . import search
        pat = search._compile(q, mode, case) if q.strip() else None
        files = []
        for path, text in sorted(self._FILES.items()):
            if only is not None and path not in only:
                continue
            hits = [{"line": n, "text": ln, "spans": search._spans(pat, ln)}
                    for n, ln in enumerate(text.splitlines(), 1) if pat and pat.search(ln)]
            if hits:
                files.append({"path": path, "matches": hits})
        return {"sample": True, "q": q, "mode": mode, "files": files,
                "total": sum(len(f["matches"]) for f in files), "truncated": False,
                "engine": "python-floor", "engine_note": "SAMPLE"}

    def meaning(self, q: str, only: Optional[List[str]]) -> Dict[str, Any]:
        return {"sample": True, "q": q, "kind": "meaning", "results": [], "available": False,
                "state": "sample", "note": "the SAMPLE has no semantic index — open a real "
                "repository"}

    def semantic_status(self) -> Dict[str, Any]:
        return {"sample": True, "available": False, "state": "sample"}

    def semantic_fetch(self) -> Dict[str, Any]:
        return self.semantic_status()

    def graph(self, view: str, symbol: str, to: str, depth: int) -> Dict[str, Any]:
        return {"sample": True, "view": view, "nodes": [], "edges": [], "note": self._NO_GRAPH}

    # --- the plan / spec / lifecycle of the two SAMPLE runs (invented, labelled) ---------------
    def _demo_run(self, run: str) -> str:
        from . import plans
        rid = plans.check_run_id(run)
        if rid not in _DEMO_PLANS:
            from .server import BadRequest
            raise BadRequest(f"no run {rid} in the SAMPLE")
        return rid

    def runs(self, source: str) -> Dict[str, Any]:
        from . import plans
        if plans.check_source(source) == "team":
            from .server import BadRequest
            raise BadRequest("the SAMPLE is not team-connected — the team source is not available")
        rows = [{"id": rid, "short": rid, "title": p["spec"]["title"], "approach": "ok",
                 "spec": "ok", "sessions": [s["id"] for s in self._sessions if s["run"] == rid],
                 "updated": 0} for rid, p in _DEMO_PLANS.items()]
        return {"sample": True, "source": "local", "runs": rows, "default": rows[0]["id"],
                "team": {"configured": False, "note": "SAMPLE"}}

    def plan(self, run: str, source: str, version: int) -> Dict[str, Any]:
        from . import plans
        if plans.check_source(source) == "team":
            from .server import BadRequest
            raise BadRequest("the SAMPLE is not team-connected — the team source is not available")
        rid = self._demo_run(run)
        p = _DEMO_PLANS[rid]
        spec = p["spec"]
        if version and version != spec["current_version"]:
            if not any(v["version"] == version for v in p["versions"]):
                spec = {"state": "no-such-version", "version": version,
                        "current_version": spec["current_version"],
                        "note": f"this SAMPLE run has no spec v{version}"}
            else:
                spec = {**spec, "version": version, "superseded": True,
                        "criteria": spec["criteria"][:1]}
        return {"sample": True, "source": "local", "label": "SAMPLE", "run": rid,
                "approach": p["approach"], "spec": spec, "versions": p["versions"],
                "amendment": {"open": False}}

    def lifecycle(self, run: str) -> Dict[str, Any]:
        from . import plans
        rid = self._demo_run(run)
        stages = plans.lifecycle_stages()
        cur = _DEMO_PLANS[rid]["at"]
        t = self._t0
        rows, marks = [], []
        for i, s in enumerate(stages):
            st = "done" if i < stages.index(cur) else "current" if s == cur else "pending"
            rows.append({"stage": s, "status": st, "entered": "SAMPLE" if st != "pending" else "",
                         "evidence": "recorded" if st != "pending" else "",
                         "blocks": 1 if s == "develop" and rid == "run-parser-fix" else 0,
                         "amendments": 1 if s == "spec" and rid == "run-parser-fix" else 0})
            if st != "pending":
                marks.append({"ts": "SAMPLE", "t": t + 300 * i, "kind": "stage_enter",
                              "stage": s, "label": f"entered {s}"})
        if rid == "run-parser-fix":
            marks.append({"ts": "SAMPLE", "t": t + 700, "kind": "spec_amend", "stage": "spec",
                          "label": "spec amended v1 → v2", "reason": "empty input"})
            marks.append({"ts": "SAMPLE", "t": t + 1000, "kind": "gate_block",
                          "stage": "develop", "label": "gate tdd blocked src/parser.py"})
        marks.sort(key=lambda m: m["t"])
        return {"sample": True, "run": rid, "stages": rows, "current": cur, "finished": False,
                "finished_at": "", "regressed": False, "amendment": {"open": False},
                "started": t, "started_basis": "SAMPLE", "marks": marks,
                "test_record": {"failing": 0, "passing": 1}}

    def pr(self, url: str) -> Dict[str, Any]:
        return _fake("PR #42 (sample/repo) against origin/main",
                     [D.text_diff("src/parser.py", _A_OLD, _A_NEW),
                      D.text_diff("tests/test_parser.py", "", _T_NEW)])


_DEMO_PLANS: Dict[str, Dict[str, Any]] = {
    "run-parser-fix": {
        "at": "develop",
        "approach": {
            "state": "ok", "topic": "SAMPLE · empty input crashes the parser",
            "name": "Guard empty input in parse()",
            "summary": "Return an **empty list** for empty input and strip each field.\n\n"
                       "- smallest change that fixes the crash\n- keeps `parse` pure",
            "pros": ["one function changes", "no new dependency"],
            "cons": ["callers relying on `['']` change behaviour"],
            "targets": ["src/parser.py::parse"],
            "decisions": [{"id": "D1", "statement": "`parse('')` returns `[]`",
                           "rationale": "Q2", "about_code": ["parse"], "deferred": []}],
            "questions": [{"text": "What should empty input return?", "answer": "an empty list"}],
            "approver": "SAMPLE human", "approved_at": "SAMPLE", "domains": []},
        "spec": {"state": "ok", "version": 2, "current_version": 2, "superseded": False,
                 "title": "SAMPLE · parse handles empty input", "approach": "Guard empty input",
                 "domains": [],
                 "criteria": [{"id": "AC1", "text": "`parse('')` returns `[]`", "test": "passing",
                               "anchors": [{"kind": "symbol", "ref": "src/parser.py::parse",
                                            "source": "decision", "confidence": "high"}]},
                              {"id": "AC2", "text": "fields are **stripped**", "test": "none"}],
                 "anchor_lines": ["AC1 → src/parser.py::parse"],
                 "tests": {"failing": [], "passing": ["AC1"]},
                 "scope": {"authorized": ["src/parser.py", "tests/**"], "deferred": []}},
        "versions": [{"version": 1, "current": False, "present": True, "malformed": False,
                      "key": "SAMPLE", "title": "SAMPLE · parse handles empty input",
                      "ac_count": 1},
                     {"version": 2, "current": True, "present": True, "malformed": False,
                      "key": "SAMPLE", "title": "SAMPLE · parse handles empty input",
                      "ac_count": 2}]},
    "run-retry-fetch": {
        "at": "spec",
        "approach": {"state": "ok", "topic": "SAMPLE · flaky fetch", "name": "Retry with a cap",
                     "summary": "Retry `fetch` up to three times on `Timeout`.", "pros": [],
                     "cons": [], "targets": ["src/net.py::fetch"], "decisions": [],
                     "questions": [], "approver": "SAMPLE human", "approved_at": "SAMPLE",
                     "domains": []},
        "spec": {"state": "ok", "version": 1, "current_version": 1, "superseded": False,
                 "title": "SAMPLE · fetch retries", "approach": "Retry with a cap", "domains": [],
                 "criteria": [{"id": "AC1", "text": "a timeout is retried 3 times", "test": "none"}],
                 "anchor_lines": [], "tests": {"failing": [], "passing": []}},
        "versions": [{"version": 1, "current": True, "present": True, "malformed": False,
                      "key": "SAMPLE", "title": "SAMPLE · fetch retries", "ac_count": 1}]},
}


__all__ = ["RealSource", "DemoSource"]
