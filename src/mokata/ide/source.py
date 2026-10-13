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

    def pr(self, url: str) -> Dict[str, Any]:
        return _fake("PR #42 (sample/repo) against origin/main",
                     [D.text_diff("src/parser.py", _A_OLD, _A_NEW),
                      D.text_diff("tests/test_parser.py", "", _T_NEW)])


__all__ = ["RealSource", "DemoSource"]
