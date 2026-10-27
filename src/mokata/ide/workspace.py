"""The IDE workspace — open tabs, the active tab, closed tabs (for reopen), expanded tree dirs,
scroll positions, the mode/panel and the focused session — persisted per repository on the
SERVER (0.0.26 P2, `ID-TABS` + session restore; doc 109 §13.4).

WHERE. `<repo>/.mokata/temp_local/ide/workspace.json`, beside the server's own record
(`server.json`). Per workspace = per repo root, so every worktree has its own; and it is the
server's file, not the browser's, so a `mokata ide` relaunch and a `/mokata:review-window` from a
session both re-open the same tabs (shared CLI <-> IDE). It is IDE scratch state under
`.mokata/temp_local/` (like the semantic index and the server record) — never the repo's files.
On a repo mokata has not set up NOTHING is written (0.0.26 P39, after P29): the workspace lives
in this server's memory (`_MEMORY`, per repo) until the user consents to governance — the Welcome
screen's gated `mokata init` — after which the next save lands in the repo's own
`.mokata/temp_local/ide/`. Opening a stranger's folder writes neither into it nor into the user's
profile on its behalf.

BOUNDED. Every list is capped (`MAX_*`), every string clipped, the file is at most `MAX_BYTES`;
an over-long list keeps its newest entries. A path must be a plain repo-relative name (no
absolute path, drive, NUL or `..`) — the page reads it through the contained file routes anyway.

USER-OWNED DATA (BRIEF rule 1). A file that is not JSON, not an object, or of another `schema`
is REFUSED LOUDLY: it is moved aside to `workspace.corrupt-<ts>.json` (never deleted, never
silently overwritten), a line goes to stderr, the IDE starts clean, and the answer names what
happened (`refused`). It never crashes the server.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from ..repo_paths import as_name

# 2 (0.0.26 P34, G20/R31): ONE tab per file — a tab carries the view it shows (`code` or `diff`,
# the Δ toggle), the separate `diff` kind is gone. A schema-1 file is refused loudly like any other.
SCHEMA = 2
FILE_NAME = "workspace.json"
MAX_TABS = 40
MAX_CLOSED = 20
MAX_DIRS = 400
MAX_PATH = 1024
MAX_ID = 200
MAX_BYTES = 64 * 1024
TAB_KINDS = ("file", "commit")     # "commit": pinned commit tabs — format reserved
TAB_VIEWS = ("code", "diff")       # a file tab shows the file (Code) or its change (Review): the Δ toggle
MODES_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DRIVE = re.compile(r"^[A-Za-z]:")


#: The workspace of each repo mokata does not govern (P39): kept for this server's life only.
_MEMORY: Dict[str, Dict[str, Any]] = {}


def _memory_key(root: str) -> Optional[str]:
    """The in-memory key of an UNGOVERNED repo, or None when the repo is governed (disk)."""
    from .statedir import governed
    return None if governed(root) else os.path.normcase(os.path.realpath(os.path.abspath(root)))


def workspace_path(root: str) -> str:
    from .server import record_path
    return os.path.join(os.path.dirname(record_path(root)), FILE_NAME)


def empty() -> Dict[str, Any]:
    return {"schema": SCHEMA, "tabs": [], "active": None, "closed": [], "expanded": [],
            "collapsed": [], "mode": None, "panel": None, "focus": None, "run": None,
            "saved_at": 0.0}


# ------------------------------------------------------------------------------- normalise
def clean_path(p: Any) -> Optional[str]:
    """A repo-relative '/' name, or None. Syntactic only — reads stay behind the routes' own
    containment (`nav.contained_rel`)."""
    if not isinstance(p, str) or not p or len(p) > MAX_PATH or "\0" in p:
        return None
    p = as_name(p)                                # crossed in from the browser: one spelling
    if p.startswith("/") or _DRIVE.match(p):
        return None
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if not parts or any(s == ".." for s in parts):
        return None
    return "/".join(parts)


def _int(v: Any, lo: int = 0, hi: int = 10_000_000) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, n))


def _ident(v: Any) -> Optional[str]:
    return v[:MAX_ID] if isinstance(v, str) and v and "\0" not in v else None


def clean_tab(t: Any) -> Optional[Dict[str, Any]]:
    """One tab, or None when it is not a tab. `key` is its identity (no two tabs share one)."""
    if not isinstance(t, dict) or t.get("kind") not in TAB_KINDS:
        return None
    kind = t["kind"]
    out: Dict[str, Any] = {"kind": kind}
    if kind == "commit":
        sha = t.get("sha")
        if not isinstance(sha, str) or not SHA_RE.match(sha):
            return None
        out["sha"] = sha
        path = clean_path(t.get("path")) if t.get("path") else None
        if path:
            out["path"] = path
        out["key"] = f"commit:{sha}:{path or ''}"
    else:
        path = clean_path(t.get("path"))
        if not path:
            return None
        out["path"] = path
        out["view"] = t.get("view") if t.get("view") in TAB_VIEWS else "code"
        out["key"] = f"file:{path}"                  # unique by (path, kind): one tab per file
    out["line"] = _int(t.get("line"), 1, 10_000_000)
    out["scroll"] = _int(t.get("scroll"), 0, 100_000_000)
    out["pinned"] = bool(t.get("pinned"))
    return out


def _paths(v: Any, cap: int) -> List[str]:
    out: List[str] = []
    for p in v if isinstance(v, list) else []:
        c = clean_path(p)
        if c and c not in out:
            out.append(c)
    return out[-cap:]


def normalize(doc: Any) -> Tuple[Dict[str, Any], int]:
    """`(clean workspace, entries dropped)`. Never raises: anything that is not a valid entry is
    dropped (and counted); every list is capped, newest kept."""
    d = doc if isinstance(doc, dict) else {}
    out = empty()
    dropped = 0
    seen = set()
    tabs = []
    for t in d.get("tabs") if isinstance(d.get("tabs"), list) else []:
        c = clean_tab(t)
        if c is None or c["key"] in seen:
            dropped += 1
            continue
        seen.add(c["key"])
        tabs.append(c)
    if len(tabs) > MAX_TABS:
        dropped += len(tabs) - MAX_TABS
        tabs = tabs[-MAX_TABS:]
    out["tabs"] = tabs
    active = d.get("active")
    out["active"] = active if isinstance(active, str) and active in {t["key"] for t in tabs} \
        else (tabs[-1]["key"] if tabs else None)
    closed = []
    for t in d.get("closed") if isinstance(d.get("closed"), list) else []:
        c = clean_tab(t)
        if c is None:
            dropped += 1
            continue
        closed.append(c)
    out["closed"] = closed[-MAX_CLOSED:]
    out["expanded"] = _paths(d.get("expanded"), MAX_DIRS)
    out["collapsed"] = _paths(d.get("collapsed"), MAX_DIRS)
    for k in ("mode", "panel"):
        v = d.get(k)
        out[k] = v if isinstance(v, str) and MODES_RE.match(v) else None
    out["focus"] = _ident(d.get("focus"))
    out["run"] = _ident(d.get("run"))
    try:
        out["saved_at"] = float(d.get("saved_at") or 0.0)
    except (TypeError, ValueError):
        out["saved_at"] = 0.0
    # The byte bound: shed the oldest closed tabs, then dirs, then tabs, until it fits.
    while len(_dump(out)) > MAX_BYTES:
        for k in ("closed", "collapsed", "expanded", "tabs"):
            if out[k]:
                out[k] = out[k][1:]
                dropped += 1
                break
        else:                                                  # pragma: no cover — cannot grow
            break
        if out["active"] not in {t["key"] for t in out["tabs"]}:
            out["active"] = out["tabs"][-1]["key"] if out["tabs"] else None
    return out, dropped


def _dump(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------- disk
def load(root: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """`(workspace, refused)`. A missing file is an empty workspace. A file mokata cannot read
    as its own is moved aside and named in `refused` — loud, never fatal, never deleted."""
    mem = _memory_key(root)
    if mem is not None:
        return normalize(_MEMORY.get(mem) or empty())[0], None
    path = workspace_path(root)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES * 4 + 1)
    except FileNotFoundError:
        return empty(), None
    except OSError as exc:
        return empty(), f"could not read {path}: {exc} — starting with no saved tabs"
    why = None
    doc: Any = None
    if len(raw) > MAX_BYTES * 4:
        why = f"larger than {MAX_BYTES * 4} bytes"
    else:
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            why = f"not JSON ({exc.__class__.__name__})"
    if why is None and not isinstance(doc, dict):
        why = "not a JSON object"
    if why is None and doc.get("schema") != SCHEMA:
        why = f"schema {doc.get('schema')!r}, this mokata reads schema {SCHEMA}"
    if why is None:
        return normalize(doc)[0], None
    aside = os.path.join(os.path.dirname(path), f"workspace.corrupt-{int(time.time() * 1000)}.json")
    try:
        os.replace(path, aside)
        kept = f"moved aside to {aside}"
    except OSError as exc:
        kept = f"could not move it aside ({exc}); it is left as it was and will not be overwritten"
        aside = ""
    msg = (f"mokata ide: refused the saved workspace {path}: {why}; {kept}. "
           "Starting with no saved tabs.")
    try:
        sys.stderr.write(msg + "\n")
    except (OSError, ValueError):                              # pragma: no cover — no stderr
        pass
    return empty(), msg


def save(root: str, doc: Any) -> Dict[str, Any]:
    """Normalise and write atomically ('\\n' newlines on every OS). Returns what was written.
    Refuses (OSError) rather than overwrite a file it could not move aside."""
    clean, _ = normalize(doc)
    clean["saved_at"] = time.time()
    mem = _memory_key(root)
    if mem is not None:
        _MEMORY[mem] = clean                 # P39: no consent yet — nothing goes to disk
        return clean
    path = workspace_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_dump(clean))
    os.replace(tmp, path)
    return clean


__all__ = ["SCHEMA", "MAX_TABS", "MAX_CLOSED", "MAX_DIRS", "MAX_BYTES", "TAB_KINDS",
           "workspace_path", "empty", "clean_path", "clean_tab", "normalize", "load", "save"]
