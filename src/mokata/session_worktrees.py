"""P31 SESSION-WORKTREES — every Claude Code session works in its own git worktree, and mokata knows
which session made which change (rulings by Jas, 2026-10-22).

Jas: "we do a lot of claude resume for old sessions — how to make sure we pick the right code back;
also we will be running multiple sessions in parallel". Before this stage a session's changes were
attributed ONLY from Write/Edit tool paths, so a `sed -i`, a formatter, codegen or a `git mv` run
through Bash was never attributed, and every session shared ONE working tree.

Rulings: (1) every session runs in its own worktree by default; (2) worktrees branch from the current
HEAD (`worktree.baseRef: "head"`); (3) merging back is approved by the user in the IDE, and the
worktree is cleaned after the merge. mokata does NOT build a worktree system: Claude Code ships one
(`claude -w`, the `EnterWorktree` tool, `.claude/worktrees/<name>/` on branch `worktree-<name>`,
`--resume` returning a session to its worktree). This module only:

  * POLICY — `settings.sessions.worktree: always|off` (default `always`); no git → off, said once.
  * BINDING — session → worktree (path, branch, base commit), recorded from every hook's `cwd`
    (which follows the worktree; `${CLAUDE_PROJECT_DIR}` does not), kept across a resume.
  * GATE   — `entry_refusal`: with `always`, a session's first write to the repo is refused until
    the session is in a linked worktree (fail-closed, one clear "enter your worktree first").
  * CHANGES — exact attribution for a worktree session: `git diff <base>` in its worktree +
    untracked files. That is every change, Bash-made ones included, with no per-call cost.
  * FALLBACK — a session NOT in a worktree (policy off) still gets its Bash changes attributed: the
    PreToolUse gate stamps the call's start, the PostToolUse lane (deferred through the O3 drainer,
    end time captured at hook time) runs ONE bounded `git status --porcelain=v2 -z` and attributes
    the paths whose mtime falls inside the call's window (deletions: absent from the previous scan),
    with a content hash; a path inside two sessions' overlapping windows is marked "shared".

State lives in the ONE shared store (`tdd_state.state_dir`, the main checkout's), keyed by Claude
Code's session id. Every function here is total: it degrades to "nothing known" and never raises
into a hook. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from .repo_identity import canonical_repo_root, worktree_toplevel

POLICY_ALWAYS = "always"
POLICY_OFF = "off"
POLICIES = (POLICY_ALWAYS, POLICY_OFF)
DEFAULT_POLICY = POLICY_ALWAYS
SETTINGS_SECTION = "sessions"
SETTINGS_KEY = "worktree"
CONFIG_KEY = f"settings.{SETTINGS_SECTION}.{SETTINGS_KEY}"
OFF_COMMAND = f"mokata config set {CONFIG_KEY} {POLICY_OFF}"

#: Claude Code's own layout (code.claude.com/docs/en/worktrees).
WORKTREES_REL = (".claude", "worktrees")
BRANCH_PREFIX = "worktree-"

#: The gate's name on a refusal (`BLOCKED [worktree] …`).
GATE_WORKTREE = "worktree"

BINDING_PREFIX = "wt_binding__"
ATTRIBUTION_PREFIX = "attribution__"
WINDOWS_KEY = "bash_windows"
SCAN_KEY = "bash_status_scan"

#: Bounds — a hook never walks without one.
MAX_WINDOWS = 64
MAX_ATTRIBUTED = 2000
MAX_SCAN_ENTRIES = 20000
MAX_HASH_BYTES = 1024 * 1024
MAX_CHANGED_FILES = 2000
#: mtime granularity slack around a Bash call's window (ns).
WINDOW_SLACK_NS = 50_000_000
#: `last_seen` on a binding is refreshed at most this often (a write per tool call is waste).
SEEN_REFRESH_SECS = 60.0
GIT_TIMEOUT = 10.0

SHARED = "shared"
NOT_GIT = ("not a git repository — sessions share this directory (mokata runs each session in its "
           "own git worktree only in a git repo)")


# ================================================================================ git, quietly
def _git(cwd: str, *args: str, timeout: float = GIT_TIMEOUT) -> Tuple[int, str]:
    """`git -C cwd …` with no console window on Windows, never raising: (code, stdout|stderr)."""
    from .quiet_spawn import hidden
    try:
        p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, **hidden())
    except FileNotFoundError:
        return 127, "git is not installed"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout if p.returncode == 0 else (p.stderr or p.stdout)


def _git_bytes(cwd: str, *args: str, timeout: float = GIT_TIMEOUT) -> Tuple[int, bytes]:
    from .quiet_spawn import hidden
    try:
        p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, **hidden())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1, b""
    return p.returncode, p.stdout


def is_git(path: str) -> bool:
    """Zero-subprocess: is `path` inside a git checkout (main or linked)?"""
    from .repo_identity import _common_dir
    try:
        return _common_dir(path) is not None
    except (OSError, ValueError):
        return False


def _gitdir_of(tree: str) -> Optional[str]:
    gp = os.path.join(tree, ".git")
    if os.path.isdir(gp):
        return gp
    try:
        with open(gp, "r", encoding="utf-8", errors="replace") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    if not line.lower().startswith("gitdir:"):
        return None
    gd = line[len("gitdir:"):].strip()
    return os.path.normpath(gd if os.path.isabs(gd) else os.path.join(tree, gd))


def branch_of(tree: str) -> str:
    """The branch checked out in `tree`, by reading its HEAD file ("" when detached/unknown)."""
    gd = _gitdir_of(tree)
    if not gd:
        return ""
    try:
        with open(os.path.join(gd, "HEAD"), "r", encoding="utf-8", errors="replace") as fh:
            head = fh.readline().strip()
    except OSError:
        return ""
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""


def head_of(tree: str) -> str:
    code, out = _git(tree, "rev-parse", "--verify", "-q", "HEAD")
    return out.strip() if code == 0 else ""


def base_commit(tree: str, branch: str) -> str:
    """The commit `tree`'s branch was created from: the first entry of the branch's reflog (git
    writes `branch: Created from HEAD` there), else the merge-base with the main checkout's HEAD.
    "" when neither can be read."""
    if branch:
        code, out = _git(tree, "reflog", "show", "--format=%H", f"refs/heads/{branch}", "--")
        if code == 0:
            shas = [s for s in out.split() if re.fullmatch(r"[0-9a-f]{40,64}", s)]
            if shas:
                return shas[-1]
    main = canonical_repo_root(tree)
    main_head = head_of(main)
    if main_head:
        code, out = _git(tree, "merge-base", "HEAD", main_head)
        if code == 0 and out.strip():
            return out.strip()
    return ""


# ================================================================================ policy
class Policy:
    """What applies to a session at `cwd`: `value` (always|off), `configured` (what the manifest
    says), `reason` (why it is off when configured always — no git), `problem` (a bad setting)."""

    __slots__ = ("value", "configured", "reason", "problem")

    def __init__(self, value: str, configured: str, reason: str = "", problem: str = "") -> None:
        self.value, self.configured, self.reason, self.problem = value, configured, reason, problem

    @property
    def always(self) -> bool:
        return self.value == POLICY_ALWAYS

    def to_dict(self) -> Dict[str, str]:
        return {"value": self.value, "configured": self.configured, "reason": self.reason,
                "problem": self.problem}


def configured_policy(root: str) -> Tuple[str, str]:
    """`(value, problem)` from `settings.sessions.worktree` — one JSON load (hook-cheap). A bad
    value is refused by name and the default kept."""
    try:
        with open(os.path.join(root, ".mokata", "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        raw = (((data.get("settings") or {}).get(SETTINGS_SECTION) or {}).get(SETTINGS_KEY)
               if isinstance(data, dict) else None)
    except (OSError, ValueError, AttributeError):
        raw = None
    if raw is None:
        return DEFAULT_POLICY, ""
    if isinstance(raw, str) and raw.strip().lower() in POLICIES:
        return raw.strip().lower(), ""
    return DEFAULT_POLICY, (f"{CONFIG_KEY} = {raw!r} is not one of {', '.join(POLICIES)} — "
                            f"using {DEFAULT_POLICY}")


def effective_policy(root: str, cwd: Optional[str] = None) -> Policy:
    configured, problem = configured_policy(root)
    if configured == POLICY_OFF:
        return Policy(POLICY_OFF, configured, problem=problem)
    if not is_git(cwd or root):
        return Policy(POLICY_OFF, configured, reason=NOT_GIT, problem=problem)
    return Policy(POLICY_ALWAYS, configured, problem=problem)


# ================================================================================ naming
def _slug(text: str, n: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n].strip("-")


def worktree_name(session_id: str, run_id: Optional[str] = None) -> str:
    """The `EnterWorktree` name for a session: `mk-<run>-<session>` (run slug when a pipeline is
    registered). Short, `[a-z0-9-]` only — a valid directory and branch name on every OS."""
    parts = ["mk"]
    if run_id:
        parts.append(_slug(run_id, 12) or "run")
    parts.append(_slug(session_id, 8) or "session")
    return "-".join(parts)


def worktrees_dir(root: str) -> str:
    return os.path.join(canonical_repo_root(root), *WORKTREES_REL)


# ================================================================================ binding
def _store(root: str):
    from .state import StateStore
    from .tdd_state import state_dir
    return StateStore(state_dir(root))


def _bkey(session_id: str) -> str:
    return BINDING_PREFIX + re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)


def binding(root: str, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    try:
        data = _store(root).read(_bkey(session_id))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("path") else None


def bindings(root: str) -> Dict[str, Dict[str, Any]]:
    """Every recorded session → worktree binding (bounded by the state dir listing)."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from .tdd_state import state_dir
        d = state_dir(root)
        names = [n for n in os.listdir(d) if n.startswith(BINDING_PREFIX) and n.endswith(".json")]
    except OSError:
        return out
    store = _store(root)
    for n in sorted(names)[:1000]:
        data = store.read(n[:-len(".json")])
        if isinstance(data, dict) and data.get("session") and data.get("path"):
            out[str(data["session"])] = data
    return out


def bind(root: str, session_id: Optional[str], cwd: Optional[str],
         now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Record that `session_id` works in the linked worktree `cwd` is in. Writes only when the
    binding is new or moved (and refreshes `last_seen` at most once a minute); the base commit is
    read from git ONCE, at the first bind. Returns the binding, or None when `cwd` is not in a linked
    worktree (the existing binding is kept: a resumed session may start at the main checkout before
    Claude Code returns it to its worktree). Never raises."""
    if not session_id or not cwd:
        return None
    try:
        tree = worktree_toplevel(cwd)
        if tree is None:
            return None
        ts = time.time() if now is None else float(now)
        real = os.path.realpath(tree)
        cur = binding(root, session_id)
        if cur and os.path.realpath(str(cur.get("path", ""))) == real:
            if ts - float(cur.get("last_seen") or 0) < SEEN_REFRESH_SECS:
                return cur
            cur = dict(cur, last_seen=ts)
            _store(root).write(_bkey(session_id), cur)
            return cur
        br = branch_of(tree)
        rec = {"session": session_id, "path": tree, "branch": br, "base": base_commit(tree, br),
               "main": canonical_repo_root(tree), "bound_at": ts, "last_seen": ts}
        if cur and cur.get("carried_from"):
            rec["carried_from"] = cur["carried_from"]
        _store(root).write(_bkey(session_id), rec)
        return rec
    except (OSError, ValueError, TypeError):
        return None


def carry_binding(root: str, session_id: str, from_session: str) -> bool:
    """A resumed conversation with a NEW id (fork / older builds) continues `from_session`: it
    carries that session's worktree binding, so `--resume` picks the same code back. A session's
    own binding is never overwritten."""
    if not session_id or not from_session or session_id == from_session:
        return False
    if binding(root, session_id):
        return False
    prev = binding(root, from_session)
    if not prev:
        return False
    try:
        rec = dict(prev, session=session_id, carried_from=from_session, last_seen=time.time())
        _store(root).write(_bkey(session_id), rec)
        return True
    except (OSError, ValueError, TypeError):
        return False


# ================================================================================ the gate
def _inside(path: str, top: str) -> bool:
    try:
        rp, rt = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(top))
    except (OSError, ValueError):
        return False
    return rp == rt or rp.startswith(rt.rstrip(os.sep) + os.sep)


def _session_run(root: str, session_id: Optional[str]) -> Optional[str]:
    try:
        from .run_intent import CLAIM_BOUND, session_claim
        claim, rid = session_claim(root, session_id)
        return rid if claim == CLAIM_BOUND else None
    except (OSError, ValueError, ImportError):
        return None


def enter_instruction(root: str, session_id: str) -> str:
    """What the session must do: re-enter its bound worktree if it has one on disk, else create
    one with a name derived from the session (and its run)."""
    b = binding(root, session_id)
    if b and os.path.isdir(str(b.get("path", ""))):
        return (f"call the EnterWorktree tool with path \"{b['path']}\" (this session's worktree, "
                f"branch {b.get('branch') or '?'})")
    return (f"call the EnterWorktree tool with name \"{worktree_name(session_id, _session_run(root, session_id))}\" "
            f"(Claude Code creates .claude/worktrees/<name>/ on branch {BRANCH_PREFIX}<name>)")


def entry_refusal(root: str, cwd: str, session_id: Optional[str], targets: List[str]) -> str:
    """The `always` policy at the gate: "" when the write may proceed, else the one-line refusal.

    Refused only when ALL hold: a real session (a payload `session_id`), the policy is `always` (a
    mokata repo in git, not switched off), the session is NOT in a linked worktree, and a target is
    inside the main checkout's tree but not inside a linked worktree. A target outside the repo is
    not this gate's business (self-protect owns that). Never raises (→ "")."""
    try:
        if not session_id or not targets:
            return ""
        if worktree_toplevel(cwd) is not None:
            return ""
        pol = effective_policy(root, cwd)
        if not pol.always:
            return ""
        main = canonical_repo_root(cwd)
        hit = None
        for t in targets:
            ab = t if os.path.isabs(t) else os.path.join(cwd, t)
            if _inside(ab, main) and worktree_toplevel(os.path.dirname(ab) or ab) is None:
                hit = ab
                break
        if hit is None:
            return ""
        return (f"BLOCKED [{GATE_WORKTREE}] enter your worktree first — mokata runs every session in "
                f"its own git worktree so parallel and resumed sessions never share files. "
                f"{enter_instruction(root, session_id)}, then retry the edit. "
                f"(To work in the main checkout instead: `{OFF_COMMAND}`.)")
    except (OSError, ValueError, TypeError):
        return ""


# ================================================================================ SessionStart
def briefing_line(root: str, cwd: str, session_id: Optional[str]) -> Optional[str]:
    """The SessionStart briefing's worktree line: bind when already in one; instruct EnterWorktree
    BEFORE the first edit when not; say once why the policy is off when it is degraded (no git).
    None when there is nothing to say (policy switched off on purpose)."""
    try:
        pol = effective_policy(root, cwd)
        if pol.problem:
            prob = f" ({pol.problem})"
        else:
            prob = ""
        if worktree_toplevel(cwd) is not None:
            b = bind(root, session_id, cwd) or {}
            return (f"mokata: this session works in its own worktree {b.get('path') or cwd} "
                    f"(branch {b.get('branch') or branch_of(cwd) or '?'}, from "
                    f"{(b.get('base') or '?')[:10]}). Merge back from the IDE's Worktrees panel "
                    f"(you approve it).{prob}")
        if not pol.always:
            if pol.reason:
                return f"mokata: session worktrees are off here — {pol.reason}.{prob}"
            return None
        if not session_id:
            return None
        return (f"mokata: BEFORE your first edit, {enter_instruction(root, session_id)}. Every "
                f"session works in its own git worktree (branched from the current HEAD), so "
                f"parallel and resumed sessions never share files; edits in the main checkout are "
                f"refused until you do.{prob}")
    except (OSError, ValueError, TypeError):
        return None


# ================================================================================ changes
def _parse_name_status(text: str) -> List[Tuple[str, str]]:
    parts = text.split("\0")
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(parts) - 1:
        st = parts[i]
        if not st:
            i += 1
            continue
        if st[:1] in ("R", "C"):
            if i + 2 >= len(parts):
                break
            out.append((st[:1], parts[i + 2]))
            i += 3
        else:
            out.append((st[:1], parts[i + 1]))
            i += 2
    return out


def worktree_changes(tree: str, base: str, cap: int = MAX_CHANGED_FILES) -> Dict[str, Any]:
    """Every change in `tree` since `base`: committed on the branch, staged, unstaged — Bash-made
    ones included — plus untracked files. `{ok, files: [{path, status}], truncated, note}`."""
    if not os.path.isdir(tree):
        return {"ok": False, "files": [], "truncated": False,
                "note": f"the worktree {tree} no longer exists"}
    ref = base or "HEAD"
    code, out = _git(tree, "diff", "--name-status", "-z", "--no-renames", "--end-of-options", ref)
    if code != 0:
        return {"ok": False, "files": [], "truncated": False,
                "note": f"git diff failed: {out.strip()[:200]}"}
    files = [{"path": p, "status": s} for s, p in _parse_name_status(out)]
    code, out = _git(tree, "ls-files", "--others", "--exclude-standard", "-z")
    if code == 0:
        files += [{"path": p, "status": "?"} for p in out.split("\0") if p]
    return {"ok": True, "files": files[:cap], "truncated": len(files) > cap,
            "note": "" if base else "base commit unknown — compared against HEAD"}


def session_changes(root: str, session_id: str) -> Dict[str, Any]:
    """A session's changes: EXACT (`git diff <base>` + untracked in its worktree) when it is bound
    to one, else the fallback attribution (Bash windows) — the answer names which."""
    b = binding(root, session_id)
    if b:
        res = worktree_changes(str(b["path"]), str(b.get("base") or ""))
        res.update({"session": session_id, "mode": "worktree", "worktree": b["path"],
                    "branch": b.get("branch", ""), "base": b.get("base", "")})
        return res
    att = attribution(root, session_id)
    files = [{"path": p, "status": v.get("status", "M"), "how": v.get("how", "bash"),
              "shared": v.get("shared", [])} for p, v in sorted(att.items())]
    return {"ok": True, "session": session_id, "mode": "fallback", "files": files,
            "truncated": False,
            "note": ("not in a worktree — Bash changes attributed by call window (paths changed by "
                     "two overlapping sessions are marked shared); Write/Edit changes are in the "
                     "session's snapshots")}


# ================================================================================ fallback (Bash)
def _wkey_store(root: str):
    return _store(root)


def bash_begin(root: str, session_id: Optional[str], call_id: str, cwd: str,
               now_ns: Optional[int] = None) -> bool:
    """PreToolUse(Bash), sync and cheap: stamp the call's START for a session that is NOT in a
    worktree (a worktree session is attributed exactly by git; it needs no window). One small
    locked state update; no git."""
    if not session_id or not call_id or worktree_toplevel(cwd) is not None or not is_git(cwd):
        return False
    t = time.time_ns() if now_ns is None else int(now_ns)

    def add(cur):
        rows = list((cur or {}).get("windows") or [])
        rows.append({"session": session_id, "id": call_id, "start": t, "end": None})
        return {"windows": rows[-MAX_WINDOWS:]}
    try:
        _wkey_store(root).update(WINDOWS_KEY, add, default={})
        return True
    except (OSError, ValueError, TypeError):
        return False


def _parse_porcelain_v2(raw: bytes, cap: int = MAX_SCAN_ENTRIES) -> Dict[str, str]:
    """`{path: XY}` from `git status --porcelain=v2 -z` (renames → the new path; untracked → `??`)."""
    out: Dict[str, str] = {}
    parts = raw.split(b"\0")
    i = 0
    while i < len(parts) and len(out) < cap:
        ent = parts[i]
        i += 1
        if not ent:
            continue
        kind = ent[:1]
        try:
            if kind == b"1":
                f = ent.split(b" ", 8)
                out[f[8].decode("utf-8", "surrogateescape")] = f[1].decode("ascii", "replace")
            elif kind == b"2":
                f = ent.split(b" ", 9)
                out[f[9].decode("utf-8", "surrogateescape")] = f[1].decode("ascii", "replace")
                i += 1                                         # the original path follows
            elif kind == b"u":
                f = ent.split(b" ", 10)
                out[f[10].decode("utf-8", "surrogateescape")] = "UU"
            elif kind == b"?":
                out[ent[2:].decode("utf-8", "surrogateescape")] = "??"
        except (IndexError, ValueError):
            continue
    return out


def _hash(path: str) -> str:
    try:
        if os.path.getsize(path) > MAX_HASH_BYTES:
            return "too-large"
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _covers(w: Dict[str, Any], t: Optional[int], lo: int, hi: int, now: int) -> bool:
    """Does another session's Bash window `w` overlap this call's window [lo, hi] — and, for a path
    with an mtime `t`, contain `t`? (An open window extends to `now`.)"""
    ws = int(w["start"]) - WINDOW_SLACK_NS
    we = int(w.get("end") or now) + WINDOW_SLACK_NS
    if ws > hi or we < lo:
        return False
    return t is None or ws <= t <= we


def attribution(root: str, session_id: str) -> Dict[str, Dict[str, Any]]:
    try:
        data = _store(root).read(ATTRIBUTION_PREFIX + re.sub(r"[^A-Za-z0-9_.-]", "_", session_id))
    except (OSError, ValueError):
        return {}
    files = (data or {}).get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def bash_end(root: str, session_id: Optional[str], call_id: str, cwd: str,
             end_ns: Optional[int] = None) -> List[str]:
    """PostToolUse(Bash), run by the O3 drainer (off the harness's clock) with the end time captured
    at hook time: close this call's window, run ONE bounded `git status --porcelain=v2 -z`, and
    attribute to `session_id` every changed path whose mtime falls in the window (a deletion: a path
    the previous scan did not see deleted). A path inside another session's overlapping window is
    recorded with `shared`. Returns the paths attributed to this session. Never raises."""
    try:
        if not session_id or worktree_toplevel(cwd) is not None or not is_git(cwd):
            return []
        end = time.time_ns() if end_ns is None else int(end_ns)
        store = _store(root)
        box: Dict[str, Any] = {}

        def close(cur):
            rows = list((cur or {}).get("windows") or [])
            for w in rows:
                if w.get("session") == session_id and w.get("id") == call_id and w.get("end") is None:
                    w["end"] = end
                    box["w"] = dict(w)
            return {"windows": rows[-MAX_WINDOWS:]}
        store.update(WINDOWS_KEY, close, default={})
        mine = box.get("w")
        if mine is None:
            return []                                     # no start stamp → no window to attribute
        windows = [w for w in (store.read(WINDOWS_KEY) or {}).get("windows", [])
                   if isinstance(w, dict)]
        top = canonical_repo_root(cwd)
        code, raw = _git_bytes(top, "--no-optional-locks", "status", "--porcelain=v2", "-z",
                               "--untracked-files=all")
        if code != 0:
            return []
        now_status = _parse_porcelain_v2(raw)
        prev = store.read(SCAN_KEY) or {}
        prev_status = prev.get("status") if isinstance(prev.get("status"), dict) else {}
        lo, hi = int(mine["start"]) - WINDOW_SLACK_NS, end + WINDOW_SLACK_NS
        hits: Dict[str, Dict[str, Any]] = {}
        for rel, xy in now_status.items():
            if rel.split("/", 1)[0] == ".mokata":
                continue
            ab = os.path.join(top, *rel.split("/"))
            deleted = "D" in xy
            if deleted:
                if "D" in str(prev_status.get(rel, "")):
                    continue
                t = None
            else:
                try:
                    t = os.lstat(ab).st_mtime_ns
                except OSError:
                    continue
                if not (lo <= t <= hi):
                    continue
            others = sorted({w["session"] for w in windows
                             if w.get("session") != session_id and w.get("start") is not None
                             and _covers(w, t if t is not None else None, lo, hi, end)})
            hits[rel] = {"how": "bash", "status": "D" if deleted else ("A" if xy == "??" else "M"),
                         "at": t, "sha256": "" if deleted else _hash(ab), "call": call_id,
                         "shared": others}
        store.write(SCAN_KEY, {"status": now_status, "at": end})
        if hits:
            key = ATTRIBUTION_PREFIX + re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)

            def merge(cur):
                files = dict((cur or {}).get("files") or {})
                files.update(hits)
                if len(files) > MAX_ATTRIBUTED:
                    keep = sorted(files.items(), key=lambda kv: kv[1].get("at") or 0)[-MAX_ATTRIBUTED:]
                    files = dict(keep)
                return {"session": session_id, "files": files}
            store.update(key, merge, default={})
            for other in {s for h in hits.values() for s in h["shared"]}:
                okey = ATTRIBUTION_PREFIX + re.sub(r"[^A-Za-z0-9_.-]", "_", other)

                def mark(cur, other=other):
                    files = dict((cur or {}).get("files") or {})
                    for rel, h in hits.items():
                        if other in h["shared"]:
                            e = dict(files.get(rel) or {"how": "bash", "status": h["status"],
                                                        "at": h["at"], "sha256": h["sha256"]})
                            e["shared"] = sorted(set(e.get("shared") or []) | {session_id})
                            files[rel] = e
                    return {"session": other, "files": files}
                store.update(okey, mark, default={})
        return sorted(hits)
    except (OSError, ValueError, TypeError, KeyError):
        return []


def tool_call_id(payload: Dict[str, Any]) -> str:
    v = payload.get("tool_use_id")
    return v if isinstance(v, str) else ""


__all__ = ["POLICY_ALWAYS", "POLICY_OFF", "POLICIES", "DEFAULT_POLICY", "CONFIG_KEY", "OFF_COMMAND",
           "GATE_WORKTREE", "SHARED", "Policy", "configured_policy", "effective_policy",
           "worktree_name", "worktrees_dir", "branch_of", "head_of", "base_commit", "binding",
           "bindings", "bind", "carry_binding", "entry_refusal", "enter_instruction", "briefing_line",
           "worktree_changes", "session_changes", "bash_begin", "bash_end", "attribution",
           "tool_call_id", "is_git"]
