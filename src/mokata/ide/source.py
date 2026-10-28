"""Where the review window's data comes from: the REAL repo, or the labelled SAMPLE (0.0.22 E2).

Both sources answer the same questions with the same shapes, so the UI has one code path. The demo
answers carry `sample: True` on every payload and the UI shows a SAMPLE banner whenever it sees
one (doc 85 §7g: an absent answer and a real one must never share a representation — and neither
may a made-up one). The demo writes nothing and reads nothing from disk.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

from . import diffs as D


class RealSource:
    sample = False

    def __init__(self, root: str, focus_of: Optional[Callable[[], Optional[str]]] = None) -> None:
        self.root = root
        # P41 — the focused session (the server owns focus). When it works in a linked worktree
        # (P31 binding), the code views read THAT tree: Code view, outline, ⌘P, search, the
        # structural query, impact and the graph views. Shared state stays the main checkout's.
        self.focus_of = focus_of or (lambda: None)

    def code_tree(self) -> Dict[str, Any]:
        """Which tree the code views read now: `{root, tree, worktree, label, session, note}`."""
        from ..repo_identity import canonical_repo_root, worktree_label, worktree_toplevel
        sid = self.focus_of()
        out: Dict[str, Any] = {"sample": False, "root": self.root, "tree": self.root,
                               "worktree": False, "label": "main", "session": sid, "note": ""}
        if not sid:
            return out
        from .. import session_worktrees as SW
        b = SW.binding(self.root, sid)
        path = str(b.get("path") or "") if b else ""
        if not path:
            return out
        top = worktree_toplevel(path) if os.path.isdir(path) else None
        if top is None or os.path.realpath(canonical_repo_root(top)) != \
                os.path.realpath(canonical_repo_root(self.root)):
            out["note"] = (f"session {sid[:8]}'s worktree {path} no longer exists — the code views "
                           "read the main checkout")
            return out
        out.update(tree=top, worktree=True, label=worktree_label(top))
        return out

    def _code_root(self) -> str:
        return self.code_tree()["tree"]

    def state(self, focus: Optional[str]) -> Dict[str, Any]:
        # P33 — the session LIST is not in the state any more (one source: `/api/sessions/list`,
        # paginated); the state names the focused session's row, for the "Viewing" context.
        from . import sessions as SS
        row = SS.get(self.root, focus) if focus else None
        return {"sample": False, "repo": self.root, "focus": focus,
                "focus_row": row.facts() if row else None,
                "git": D.is_git(self.root), "branches": D.branches(self.root)}

    def live(self, mokata_files: bool = False) -> Dict[str, Any]:
        return {"sample": False, **D.live(self.root, mokata_files).facts()}

    def session(self, sid: str) -> Dict[str, Any]:
        return {"sample": False, **D.session_diff(self.root, sid).facts()}

    def write(self, snap: str) -> Dict[str, Any]:
        return {"sample": False, **D.write_diff(self.root, snap).facts()}

    def session_list(self, offset: int, limit: int, q: str, filt: str,
                     needs: List[str]) -> Dict[str, Any]:
        from . import sessions as SS
        return SS.page(self.root, offset, limit, q, filt, needs=needs)

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
    def tree(self, dir: str = "", filter: str = "", offset: int = 0) -> Dict[str, Any]:
        from . import nav
        return nav.tree(self._code_root(), dir, filter, offset)

    def file(self, path: str, with_outline: bool = False) -> Dict[str, Any]:
        from . import nav
        root = self._code_root()
        out = nav.read_file(root, path)
        sid = self.focus_of()
        if sid and isinstance(out.get("text"), str):
            # R28 — ONE "Viewing" context: with a session focused, the Code gutter marks what THAT
            # session changed in this file (against what it found), and says so.
            regions = D.session_regions(self.root, sid, out["path"], out["text"])
            out["changes"] = [dict(r, end=min(r["end"], max(out.get("lines") or 1, 1)))
                              for r in regions or []]
            out["changes_against"] = f"session {sid[:8]}"
            out["viewing"] = {"kind": "session", "session": sid,
                              "changed_here": regions is not None}
        else:
            out["viewing"] = {"kind": "live", "session": None, "changed_here": None}
        if with_outline:
            # P35 (G16): the file and its outline in ONE request — opening or reloading a file
            # (a live edit) asks once, not twice.
            out["outline"] = {"sample": False, **nav.outline(out["path"], out.get("text"), root)}
        return out

    def outline(self, path: str) -> Dict[str, Any]:
        from . import nav
        root = self._code_root()
        rel = nav.contained_rel(root, path)
        return {"sample": False, **nav.outline(rel, D._read_text(root, rel), root)}

    def quick(self, q: str, changed: List[str]) -> Dict[str, Any]:
        from . import nav
        return nav.quick_open(self._code_root(), q, changed)

    def query(self, kind: str, target: str, depth: int) -> str:
        from . import nav
        return nav.query_payload(nav.run(self._code_root(), kind, target, depth)) + "\n"

    def impact(self, path: str, lines: str, symbols: Optional[List[str]] = None,
               budget: str = "default") -> Dict[str, Any]:
        from . import nav
        return nav.impact(self._code_root(), path, lines, symbols, budget=budget)

    # --- 0.0.23 C2 search ---------------------------------------------------------------
    def search(self, q: str, mode: str, case: bool, only: Optional[List[str]],
               offset: int = 0) -> Dict[str, Any]:
        from . import search
        return search.text(self._code_root(), q, mode, case, only, offset)

    def meaning(self, q: str, only: Optional[List[str]]) -> Dict[str, Any]:
        from . import search
        out = search.meaning(self.root, q, only)
        if self.code_tree()["worktree"] and isinstance(out, dict):
            # P41 — the meaning index is built once, on the main checkout; it is not re-embedded
            # per worktree, and says so rather than passing main-checkout chunks off as the tree's.
            said = ("meaning search reads the main checkout's index — your worktree's changed "
                    "files are not in it; use word search for them")
            out = dict(out, note=f"{out['note']} | {said}" if out.get("note") else said)
        return out

    def semantic_status(self) -> Dict[str, Any]:
        from . import search
        return {"sample": False, **search.status()}

    def semantic_fetch(self) -> Dict[str, Any]:
        from . import search
        return {"sample": False, **search.fetch_model()}

    # --- 0.0.23 C3 graph views ----------------------------------------------------------
    def graph(self, view: str, symbol: str, to: str, depth: int,
              page: int = 1) -> Dict[str, Any]:
        from . import visuals as V
        root = self._code_root()
        if view == "focus":
            return V.focus(root, symbol, page=page)
        if view == "path":
            return V.path(root, symbol, to, depth)
        if view == "untested":
            return V.untested(root, symbol, depth, page=page)
        return V.ripple(root, symbol, depth, page=page)

    # --- 0.0.23 C5 the plan, the spec and the lifecycle of a run (see `plans`) --------------
    # 0.0.26 P29 — on a repo mokata has not set up there are no runs to have a plan, a spec or a
    # lifecycle: each answers the not-initialized STATE (200) with the one command, not an error.
    def _unset(self, what: str) -> Optional[Dict[str, Any]]:
        from .statedir import initialized, not_initialized
        return None if initialized(self.root) else not_initialized(what)

    # 0.0.26 O10 — `focus`: the focused session's own run is the default (a resumed session opens
    # the run it continues, not the newest one).
    def runs(self, source: str, focus: Optional[str] = None) -> Dict[str, Any]:
        from . import plans
        src = plans.check_source(source)
        unset = self._unset("its runs, plans and specs")
        if unset is not None:
            return {**unset, "source": src}
        return plans.team_runs(self.root) if src == "team" else plans.local_runs(self.root, focus)

    def plan(self, run: str, source: str, version: int) -> Dict[str, Any]:
        from . import plans
        unset = self._unset("a run's plan and spec")
        if unset is not None:
            return unset
        return plans.plan(self.root, run, source, version)

    def lifecycle(self, run: str) -> Dict[str, Any]:
        from . import plans
        unset = self._unset("a run's lifecycle")
        if unset is not None:
            return unset
        return plans.lifecycle(self.root, run)

    def saved_plans(self) -> Dict[str, Any]:
        from . import plans
        return plans.saved_plans(self.root)

    def saved_plan(self, slug: str, where: str) -> Dict[str, Any]:
        from . import plans
        return plans.saved_plan(self.root, slug, where)

    # --- 0.0.25 ID3 session browser / needs-you / time-lapse / treemap (see `session_views`) --
    def browser(self, bus_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        from . import session_views as SV
        return SV.browser(self.root, bus_events=bus_events)

    def timelapse(self, sid: str) -> Dict[str, Any]:
        from . import session_views as SV
        return SV.timelapse(self.root, sid)

    def treemap(self, sid: Optional[str], size: str) -> Dict[str, Any]:
        from . import session_views as SV
        return SV.treemap(self.root, sid, size)


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
             "first": now - 3600, "last": now - 300, "last_iso": "SAMPLE",
             "prompt": "SAMPLE · fix the parser crash on empty input", "started": now - 3600,
             "ended": 0.0, "duration": 3300.0, "status": "live", "status_basis": "SAMPLE",
             "branch": "worktree-sample-a", "worktree": "", "segments": 1,
             "sources": ["startup"], "known_by": ["SAMPLE"], "kind": "changed",
             "too_large": [], "chain": ["sample-session-a"], "end_reason": "",
             "resumed_from": "", "resumes": []},
            {"id": "sample-session-b", "short": "sample-B", "run": "run-retry-fetch",
             "files": ["src/net.py"], "writes": 2, "claim": "bound",
             "first": now - 2400, "last": now - 600, "last_iso": "SAMPLE",
             "prompt": "SAMPLE · retry fetch on timeout", "started": now - 2400,
             "ended": now - 600, "duration": 1800.0, "status": "ended",
             "status_basis": "SAMPLE", "branch": "worktree-sample-b", "worktree": "",
             "segments": 1, "sources": ["startup"], "known_by": ["SAMPLE"], "kind": "changed",
             "too_large": [], "chain": ["sample-session-b"], "end_reason": "SAMPLE",
             "resumed_from": "", "resumes": []},
        ]

    def state(self, focus: Optional[str]) -> Dict[str, Any]:
        f = focus or self._sessions[0]["id"]
        return {"sample": True, "repo": "SAMPLE repository (nothing here is real)",
                "focus": f, "focus_row": next((s for s in self._sessions if s["id"] == f), None),
                "git": True, "branches": ["main", "sample/parser-fix", "sample/retry"]}

    def session_list(self, offset: int, limit: int, q: str, filt: str,
                     needs: List[str]) -> Dict[str, Any]:
        words = [w for w in (q or "").lower().split() if w]
        rows = [dict(s, stage="develop", waiting=False) for s in self._sessions
                if all(w in (s["prompt"] + " " + s["id"]).lower() for w in words)
                and (filt != "live" or s["status"] == "live")]
        return {"sample": True, "rows": rows[offset:offset + limit], "total": len(rows),
                "all": len(self._sessions), "offset": offset, "limit": limit, "q": q,
                "filter": filt, "live": 1, "live_window_secs": 900}

    def write(self, snap: str) -> Dict[str, Any]:
        return _fake(f"src/parser.py: write ({snap})",
                     [D.text_diff("src/parser.py", _A_OLD, _A_NEW)])

    def live(self, mokata_files: bool = False) -> Dict[str, Any]:
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

    def tree(self, dir: str = "", filter: str = "", offset: int = 0) -> Dict[str, Any]:
        from . import nav
        idx = nav._DirIndex(sorted(self._FILES))
        flt = (filter or "").strip().lower()
        if flt:
            rows = [p for p in idx.files if flt in p.lower()]
            return {"sample": True, "count": len(idx.files), "pruned": "SAMPLE", "mode": "filter",
                    "filter": filter, "total": len(rows), "offset": 0, "next": None,
                    "entries": [{"kind": "file", "path": p, "name": p.rsplit("/", 1)[-1]}
                                for p in rows]}
        d = (dir or "").strip("/")
        if d not in idx.subdirs:
            d = ""
        rows = [{"kind": "dir", "path": sd, "name": sd.rsplit("/", 1)[-1],
                 "files": idx.under.get(sd, 0)} for sd in sorted(idx.subdirs[d])]
        rows += [{"kind": "file", "path": p, "name": p.rsplit("/", 1)[-1]}
                 for p in sorted(idx.direct[d])]
        return {"sample": True, "count": len(idx.files), "pruned": "SAMPLE", "mode": "dir",
                "dir": d, "total": len(rows), "offset": 0, "next": None, "entries": rows,
                "files_under": idx.under.get(d, 0)}

    def file(self, path: str, with_outline: bool = False) -> Dict[str, Any]:
        text = self._FILES.get(path)
        if text is None:
            out = {"sample": True, "path": path, "text": None, "note": "not in the SAMPLE"}
        else:
            n = text.count("\n")
            out = {"sample": True, "path": path, "text": text, "lines": n,
                   "changes": [{"start": 1, "end": n, "kind": "mod"}], "changes_against": "SAMPLE"}
        if with_outline:
            out["outline"] = self.outline(path)
        return out

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

    def impact(self, path: str, lines: str, symbols: Optional[List[str]] = None,
               budget: str = "default") -> Dict[str, Any]:
        return {"sample": True, "pending": [], "path": path, "symbols": [], "status": "unknown",
                "is_test": path.startswith("tests/"), "note": self._NO_GRAPH}

    def search(self, q: str, mode: str, case: bool, only: Optional[List[str]],
               offset: int = 0) -> Dict[str, Any]:
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
        n = sum(len(f["matches"]) for f in files)
        return {"sample": True, "q": q, "mode": mode, "files": files, "offset": 0, "count": n,
                "total": n, "more": False, "next_offset": None, "large_files": [],
                "partial": False, "engine": "python-floor", "engine_note": "SAMPLE"}

    def meaning(self, q: str, only: Optional[List[str]]) -> Dict[str, Any]:
        return {"sample": True, "q": q, "kind": "meaning", "results": [], "available": False,
                "state": "sample", "note": "the SAMPLE has no semantic index — open a real "
                "repository"}

    def semantic_status(self) -> Dict[str, Any]:
        return {"sample": True, "available": False, "state": "sample"}

    def semantic_fetch(self) -> Dict[str, Any]:
        return self.semantic_status()

    def graph(self, view: str, symbol: str, to: str, depth: int,
              page: int = 1) -> Dict[str, Any]:
        return {"sample": True, "view": view, "nodes": [], "edges": [], "note": self._NO_GRAPH}

    # --- the plan / spec / lifecycle of the two SAMPLE runs (invented, labelled) ---------------
    def _demo_run(self, run: str) -> str:
        from . import plans
        rid = plans.check_run_id(run)
        if rid not in _DEMO_PLANS:
            from .server import BadRequest
            raise BadRequest(f"no run {rid} in the SAMPLE")
        return rid

    def runs(self, source: str, focus: Optional[str] = None) -> Dict[str, Any]:
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

    def saved_plans(self) -> Dict[str, Any]:
        return {"sample": True, "plans": [{"slug": "sample-parser-fix", "where": "internal",
                                           "updated": self._t0,
                                           "path": ".mokata/temp_local/plans/sample-parser-fix.md"}],
                "total": 1, "dirs": {"internal": ".mokata/temp_local/plans", "exported": "plans"}}

    def saved_plan(self, slug: str, where: str) -> Dict[str, Any]:
        from .server import BadRequest
        if slug != "sample-parser-fix" or where != "internal":
            raise BadRequest(f"no saved plan {slug} in this SAMPLE")
        return {"sample": True, "slug": slug, "where": where, "updated": self._t0,
                "path": ".mokata/temp_local/plans/sample-parser-fix.md", "truncated": False,
                "text": "# SAMPLE — fix the parser\n\n## Approved approach\n\n- tokenize first, "
                        "then parse\n- keep the public API\n"}

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

    # --- 0.0.25 ID3 over the SAMPLE: invented sessions, one marked as waiting on a human -------
    def browser(self, bus_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        from . import session_views as SV
        return SV.sample_browser(self._sessions)

    def timelapse(self, sid: str) -> Dict[str, Any]:
        entries = self.timeline(sid or None)["entries"]
        steps = [{"i": i, "t": e["t"], "kind": "write" if e["kind"] == "after" else "before",
                  "path": e["path"], "snap": e["id"], "state": "ok", "added": None,
                  "removed": None, "measured": e["kind"] != "after"}
                 for i, e in enumerate(entries)]
        files = list(dict.fromkeys(s["path"] for s in steps))
        return {"sample": True, "session": sid, "run": "", "steps": steps, "files": files,
                "count": len(steps), "truncated": False, "reads": "SAMPLE",
                "unmeasured": sum(1 for s in steps if s["kind"] == "write")}

    def treemap(self, sid: Optional[str], size: str) -> Dict[str, Any]:
        from . import session_views as SV
        touched = {e["path"] for e in self.timeline(sid or None)["entries"]}
        leaves = [{"path": p, "loc": t.count("\n"), "bytes": len(t), "weight": max(1, t.count("\n")),
                   "writes": 1 if p in touched else 0, "added": t.count("\n") if p in touched else 0,
                   "removed": 0, "unmeasured": 0} for p, t in sorted(self._FILES.items())]
        dirs = SV.layout(leaves)
        return {"sample": True, "session": sid or "", "size": size, "width": SV.TREEMAP_W,
                "height": SV.TREEMAP_H, "files": leaves, "dirs": dirs, "count": len(leaves),
                "total_files": len(leaves), "omitted": 0,
                "changed": sum(1 for lf in leaves if lf["writes"]), "not_on_disk": [],
                "heat_scale": "SAMPLE", "sized_by": "lines of code"}

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
