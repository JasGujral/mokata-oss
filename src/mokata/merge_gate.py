"""0.0.27 P21 `ID-MERGE-GATED` — the gated merge: a session worktree's branch lands in the main
checkout's branch only with recorded evidence AT ITS HEAD and a human's approval of exactly that
merge. One implementation for the IDE, the CLI and MCP (rule note `reports/P21.rule.md`).

WHAT IT MERGES. `refs/heads/<b>` at `H` (a linked worktree's branch) into `refs/heads/<B>` at `base`
(the branch the main checkout has checked out). Local only — no fetch, push, forge or PR.
Strategies: `merge-commit` (parents base, H; the default), `squash` (parent base), `ff` (M = H).
Because `base` must be an ancestor of `H` (sync first otherwise), the merged tree IS `H^{tree}` —
the tree that was tested and reviewed; `merge-tree` confirms it, anything else is `tree-mismatch`.

THE PRECONDITIONS (all read from recorded state, three-state: ok / absent / unreadable; only ok
passes, unreadable reads `unknown` and blocks):
  P1 exactly one run is bound to the worktree (or a human's evidence override for "no run")
  P2 the last test run was mokata's runner, the FULL configured command, in this worktree, with
     head == H and a clean tree both before and after the run, and it passed; nothing owed
  P3 every acceptance criterion maps to a test that passed in that run
  P4 the review verdict passes (`ship_review_gate`) and is stamped at this worktree, H and base
  P5 both trees clean, no operation in progress, the main checkout on B at base
  P6 base is an ancestor of H (not behind)
  P7 nothing a checkout would silently destroy: hidden local changes, case collisions,
     unportable names (Windows), gitlinks, sparse / split indexes
plus, before anything runs: no repo filter or merge driver applies to a changed path, and the
committer identity is known.

THE EXECUTION (`run_merge`) — one commit point, git truth everywhere:
  s1 re-plan under the per-base lock; any drift from what the human approved refuses by name
  s2 `merge-tree --write-tree base H` -> T (must equal H^{tree})
  s3 `commit-tree` -> M (objects only; unsigned)
  s4 take `.git/index.lock` WITH our owner record; re-measure the main checkout lock-free; seal the
     job (no Cancel after this); burn the approval
  s5 CAS: `update-ref --stdin` {verify b H; update B M base} — THE COMMIT POINT
  s6 bring the main checkout to M through a temp index, then commit it onto `.git/index`
A failure in s6 KEEPS the lock (a `git commit` there would revert the merge) until Recover.
Recovery (`recover`) decides from B, the index and the files — never from the journal's phase.

Nothing here runs a repo hook, filter, merge driver, signing program or a git found in the repo
(`git_safe`). Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import merge_lock as ML
from .errors import MokataError
from .git_safe import Git, GitSafeError
from .knowledge import cancel as C

TOOL = "worktree_merge"
TOOL_OVERRIDE = "worktree_merge_override"
TOOL_UNLOCK = "worktree_merge_unlock"
APPROVAL_RUN = "gated-merge"
STRATEGIES = ("merge-commit", "squash", "ff")
CHAT_ACTOR = "chat-relayed"

JOURNAL_PREFIX = "merge__"
OVERRIDE_PREFIX = "merge_override__"
LEDGER_KIND = "gated_merge"
REQUEST_PREFIX = "merge_request__"

# ---------------------------------------------------------------- outcome states (all distinct)
READY, BLOCKED, REFUSED, BUSY, CANCELLED = "ready", "blocked", "refused", "busy", "cancelled"
#: A transient index.lock held by another git: the merge is BUSY (retryable), not blocked.
INDEX_LOCKED = "index-locked"
CONFLICT, TREE_MISMATCH, DIRTY = "conflict", "tree-mismatch", "dirty"
BASE_MOVED, HEAD_MOVED, DIVERGED = "base-moved", "head-moved", "checkout-diverged"
NEEDS_FILTER, NEEDS_DRIVER = "needs-filter", "needs-merge-driver"
EXEC_CHANGED, NO_IDENTITY, GIT_TOO_OLD = "exec-config-changed", "no-identity", "git-too-old"
INTERRUPTED, INCOMPLETE, MERGED = "interrupted", "merged-checkout-incomplete", "merged"
NOT_MERGED, LOCK_HELD, CONTENT_CHANGED = "not-merged", "lock-held", "content-changed"
HIDDEN, CASE, UNPORTABLE, SUBMODULE = ("hidden-local-change", "case-collision", "unportable-path",
                                       "submodule-change")
UNSUPPORTED_INDEX = "unsupported-index"
#: A blocker whose code IS the outcome state (named, not a generic "blocked").
NAMED = frozenset({NEEDS_FILTER, NEEDS_DRIVER, NO_IDENTITY, GIT_TOO_OLD, HIDDEN, CASE, UNPORTABLE,
                   SUBMODULE, UNSUPPORTED_INDEX, EXEC_CHANGED})

_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}$")
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD", "rebase-merge",
                "rebase-apply", "BISECT_LOG")
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.I)
_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_CONFIG_KEYS = r"^(core\.hookspath|core\.fsmonitor|filter\..*|merge\..*\.driver|commit\.gpgsign)$"


class MergeGateError(MokataError):
    """A merge-gate request that cannot be answered as asked (an unknown strategy, a target that is
    not a worktree of this repo). The message says what to do."""


# ================================================================================ records
@dataclass
class MergePlan:
    """The previewed merge (doc 85: a `*Plan` is the actions before the write). `args` is exactly
    what an approval binds; `blocked` lists every unmet precondition with its cite."""

    ok: bool
    state: str
    blocked: List[Dict[str, str]] = field(default_factory=list)
    args: Dict[str, Any] = field(default_factory=dict)
    card: Dict[str, Any] = field(default_factory=dict)

    def reason(self) -> str:
        return "; ".join(b["text"] for b in self.blocked)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "state": self.state, "blocked": list(self.blocked),
                "args": dict(self.args), "card": dict(self.card), "reason": self.reason()}

    def render(self) -> str:
        c = self.card
        lines = [f"merge     : {c.get('branch', '?')} @ {str(c.get('head', ''))[:12]} -> "
                 f"{c.get('into', '?')} @ {str(c.get('base', ''))[:12]}",
                 f"strategy  : {self.args.get('strategy', '?')}  ·  {c.get('commits', '?')} commit(s), "
                 f"{c.get('files', '?')} file(s)",
                 f"lands     : tree {str(c.get('head_tree', ''))[:12]} — "
                 + ("the branch head's tree, NOT tested or reviewed by mokata (override)"
                    if c.get("override") else "the tree that was tested and reviewed"),
                 f"author    : {self.args.get('author', '?')}",
                 "signing   : unsigned (the gated merge runs no signing program)",
                 f"hooks     : not run — {', '.join(c.get('hooks') or []) or 'none present'} "
                 f"(this bypasses the repo's commit hooks)",
                 "lock      : the main checkout's index is locked while the merge writes files; "
                 "git commands there wait"]
        for e in c.get("evidence") or []:
            lines.append(f"evidence  : {e}")
        if c.get("override"):
            lines.append(f"override  : merging WITHOUT run evidence — {c['override']}")
        if self.args.get("message") is not None:
            lines.append("message   :")
            lines += ["  " + ln for ln in str(self.args.get("message") or "").splitlines()[:12]]
        for b in self.blocked:
            lines.append(f"BLOCKED   : [{b['code']}] {b['text']}")
        return "\n".join(lines)


@dataclass
class MergeOutcome:
    """What a merge (or a recovery) did (doc 85: an `*Outcome`). `merged` only when the CAS landed
    AND the main checkout reads back M; every other state names what is true instead."""

    state: str
    message: str
    merge_id: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)

    @property
    def merged(self) -> bool:
        return self.state == MERGED

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "merged": self.merged, "message": self.message,
                "merge_id": self.merge_id, "facts": dict(self.facts)}


def _block(code: str, text: str, cite: str = "") -> Dict[str, str]:
    return {"code": code, "text": text, "cite": cite}


# ================================================================================ small reads
def _store(main: str):
    from .state import StateStore
    from .tdd_state import state_dir
    return StateStore(state_dir(main))


def read3(store: Any, key: str) -> Tuple[str, Any]:
    """('ok', data) | ('absent', None) | ('unreadable', None) — a damaged record is never absent."""
    try:
        if not store.exists(key):
            return "absent", None
    except OSError:
        return "unreadable", None
    data = store.read(key)
    if not isinstance(data, dict):
        return "unreadable", None
    return "ok", data


def _same(a: str, b: str) -> bool:
    """One file-system object? (aliases, case flips on a folding FS, symlinks, `a/./b`, `a//b`)."""
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _sha(text: Any) -> str:
    return hashlib.sha256(json.dumps(text, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _ident(raw: str) -> str:
    """`Name <mail> 1700000000 +0000` -> `Name <mail>` (the date is not part of the identity)."""
    return re.sub(r"\s+\d+\s+[+-]\d{4}\s*$", "", raw.strip())


def _z(out: bytes) -> List[str]:
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _gitdir(git: Git, tree: str) -> Optional[str]:
    code, out = git.text(tree, "rev-parse", "--absolute-git-dir", read=True)
    return out if code == 0 and out else None


def _common_dir(git: Git, tree: str) -> Optional[str]:
    code, out = git.text(tree, "rev-parse", "--path-format=absolute", "--git-common-dir", read=True)
    return out if code == 0 and out else None


def _rev(git: Git, tree: str, ref: str) -> Optional[str]:
    code, out = git.text(tree, "rev-parse", "--verify", "-q", ref + "^{commit}", read=True)
    return out if code == 0 and _SHA.match(out) else None


def _status_clean(git: Git, tree: str, untracked: bool) -> Optional[bool]:
    code, out, _err = git.run(tree, "status", "--porcelain=v2", "-z", "--ignore-submodules=none",
                              "--untracked-files=" + ("all" if untracked else "no"), read=True)
    if code != 0:
        return None
    return not out.strip(b"\0")


def _in_progress(gitdir: str) -> List[str]:
    return [n for n in _IN_PROGRESS + ("index.lock",) if os.path.exists(os.path.join(gitdir, n))]


# ================================================================================ tree stamp
def tree_stamp(path: str, git: Optional[Git] = None) -> Dict[str, Any]:
    """Where a test run or a review happened, as git says it is: the worktree, its HEAD, whether it
    was clean (untracked files count), and the main checkout's HEAD (the base it was judged
    against). `{"error": ...}` when git cannot say — that reads `unknown` at merge time."""
    own = git is None
    try:
        g = git or Git(path).__enter__()
    except GitSafeError as exc:
        return {"error": str(exc)}
    try:
        code, top = g.text(path, "rev-parse", "--show-toplevel", read=True)
        if code != 0:
            return {"error": f"not a git worktree: {top[:200]}", "git": False}
        head = _rev(g, top, "HEAD")
        clean = _status_clean(g, top, untracked=True)
        from .repo_identity import canonical_repo_root
        try:
            main = canonical_repo_root(top)
        except (OSError, ValueError) as exc:
            return {"error": f"the main checkout could not be found: {exc}"}
        return {"worktree": os.path.realpath(top), "head": head, "clean": clean,
                "base": _rev(g, main, "HEAD"), "main": os.path.realpath(main), "at": time.time()}
    finally:
        if own:
            g.close()


# ================================================================================ the target
def _worktrees(git: Git, main: str) -> List[Dict[str, Any]]:
    code, out, _e = git.run(main, "worktree", "list", "--porcelain", "-z", read=True)
    if code != 0:
        return []
    rows: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {}
    for item in out.decode("utf-8", "surrogateescape").split("\0"):
        if not item:
            if cur:
                rows.append(cur)
                cur = {}
            continue
        k, _, v = item.partition(" ")
        cur[k] = v if v else True
    if cur:
        rows.append(cur)
    return rows


def find_target(git: Git, main: str, target: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The LISTED linked worktree `target` names — by path (same file-system object, however it is
    spelled) or by branch — or (None, why). The main checkout is never a target."""
    rows = _worktrees(git, main)
    if not rows:
        return None, "git could not list this repository's worktrees"
    linked = rows[1:]
    hits = [r for r in linked if os.path.isdir(str(r.get("worktree", "")))
            and os.path.exists(target) and _same(str(r["worktree"]), target)]
    if not hits:
        want = target if target.startswith("refs/heads/") else "refs/heads/" + target
        hits = [r for r in linked if r.get("branch") == want]
    if rows and os.path.exists(target) and _same(str(rows[0].get("worktree", "")), target):
        return None, "that is the main checkout — the merge goes INTO it, from a session worktree"
    if len(hits) != 1:
        return None, (f"{target!r} is not a linked worktree of this repository (by path or branch)"
                      if not hits else f"{target!r} names {len(hits)} worktrees — use its path")
    return hits[0], ""


# ================================================================================ evidence
def _runs_bound(main: str, worktree: str) -> List[str]:
    from .session_worktrees import _session_run, bindings
    runs = set()
    for sid, b in bindings(main).items():
        if _same(str(b.get("path", "")), worktree):
            rid = _session_run(main, sid)
            if rid:
                runs.add(rid)
    return sorted(runs)


def override_key(common: str, worktree: str, head: str) -> str:
    return OVERRIDE_PREFIX + hashlib.sha256(
        f"{os.path.realpath(common)}\0{os.path.realpath(worktree)}\0{head}".encode()).hexdigest()[:20]


def read_override(main: str, common: str, worktree: str, head: str) -> Optional[Dict[str, Any]]:
    st, data = read3(_store(main), override_key(common, worktree, head))
    if st != "ok" or data.get("head") != head:
        return None
    return data


def evidence(main: str, run_id: str, worktree: str, head: str, base: str
             ) -> Tuple[List[Dict[str, str]], Dict[str, Any], List[str]]:
    """(blockers, fingerprint, card lines) for P2–P4 of `run_id` at this worktree and head."""
    from .config import Surface
    from .run_resolver import SPEC_PREFIX
    from .tdd_state import TDD_STATE_PREFIX, from_state
    from .test_runs import key as test_key, mapped_tests, _map
    store = _store(main)
    out: List[Dict[str, str]] = []
    lines: List[str] = []
    fp: Dict[str, Any] = {"run": run_id}
    unknown = lambda what: _block("unknown-evidence", f"{what} could not be read — the evidence "  # noqa: E731
                                  "is UNKNOWN, which never passes (`mokata doctor`)", what)
    if not Surface.is_initialized(main):
        return [_block("unknown-evidence", "this repository is not initialised for mokata, so no "
                       "evidence can be read (`mokata init`)")], fp, lines
    st, spec = read3(store, SPEC_PREFIX + run_id)
    if st == "unreadable":
        return [unknown(f"the run's emitted spec ({run_id})")], fp, lines
    if st == "absent":
        return [_block("no-spec", f"run {run_id} has no emitted spec — nothing says what done "
                       f"means (/mokata:spec)")], fp, lines
    crit = spec.get("criteria")
    if not isinstance(crit, list):
        return [unknown(f"the run's spec criteria ({run_id})")], fp, lines
    acs = [c["id"] for c in crit if isinstance(c, dict) and isinstance(c.get("id"), str)]
    fp["plan_hash"] = spec.get("plan_hash")
    st, rec = read3(store, test_key(run_id))
    if st == "unreadable":
        out.append(unknown("the run's test record"))
        rec = {}
    suite = (rec or {}).get("last_suite") if isinstance((rec or {}).get("last_suite"), dict) else None
    tree = suite.get("tree") if suite and isinstance(suite.get("tree"), dict) else {}
    if st == "absent" or suite is None:
        out.append(_block("no-suite-run", "no full test run is recorded — run the suite with "
                          "mokata's runner (`mokata worktree test <worktree>` or Run suite in the "
                          "IDE)"))
    else:
        cite = f"test run at {time.strftime('%Y-%m-%d %H:%M', time.localtime(suite.get('at') or 0))}"
        fp["suite_at"] = suite.get("at")
        if suite.get("source") != "runner":
            out.append(_block("suite-not-runner", f"the last test run was {suite.get('source')!r} "
                              f"by {suite.get('actor') or '?'}, not mokata's full-suite runner — "
                              f"an attested or partial run is shown, not counted", cite))
        elif tree.get("scope") != "full":
            out.append(_block("suite-subset", "the last run was a subset of the tests, not the "
                              f"suite — run the full command: {suite.get('command') or '?'}", cite))
        elif not _same(str(tree.get("worktree") or ""), worktree):
            out.append(_block("suite-elsewhere", "the last full run was in another worktree "
                              f"({tree.get('worktree')}), not this one", cite))
        elif tree.get("head_before") != tree.get("head_after"):
            out.append(_block("suite-moved", "the branch moved DURING the last test run — what "
                              "ran is not one commit", cite))
        elif tree.get("head_after") != head:
            out.append(_block("suite-stale", f"the last full run was at "
                              f"{str(tree.get('head_after'))[:12]}, not at {head[:12]} — re-run the "
                              f"suite", cite))
        elif not (tree.get("clean_before") is True and tree.get("clean_after") is True):
            out.append(_block("suite-dirty", "the worktree had uncommitted changes during the last "
                              "run — what ran is not the commit being merged", cite))
        elif not suite.get("passed") or suite.get("exit_code") not in (0,):
            out.append(_block("suite-failed", "the last full test run FAILED", cite))
        else:
            lines.append(f"tests green at {head[:12]}: `{suite.get('command')}` "
                         f"({cite}, by {suite.get('actor') or '?'})")
    green: set = set()
    red: set = set()
    st_t, tdd = read3(store, TDD_STATE_PREFIX + run_id)
    if st_t == "unreadable":
        out.append(unknown("the run's TDD record"))
    elif st_t == "ok":
        try:
            red, green = from_state(tdd)
        except (TypeError, ValueError, KeyError):
            out.append(unknown("the run's TDD record"))
    owed = sorted(red - green)
    if owed:
        out.append(_block("owed-red", "still RED (recorded failing, never passing): "
                          + ", ".join(owed[:10])))
    record = {"ac_tests": _map((rec or {}).get("ac_tests")), "planned": _map((rec or {}).get("planned"))}
    unmapped = [a for a in acs if not mapped_tests(record, a)]
    unproven = [a for a in acs if mapped_tests(record, a) and not set(mapped_tests(record, a)) & green]
    if unmapped:
        out.append(_block("ac-unmapped", "no test is mapped to " + ", ".join(unmapped[:10])))
    if unproven:
        out.append(_block("ac-unproven", "no mapped test PASSED for " + ", ".join(unproven[:10])))
    if acs and not unmapped and not unproven:
        lines.append(f"{len(acs)} acceptance criteria, each with a passing test")
    # P4 — review
    try:
        from .progress_events import latest_review_verdict_event, ship_review_gate
        surface = Surface.load(main)
        gate = ship_review_gate(surface, run_id=run_id)
        ev = latest_review_verdict_event(surface, run_id=run_id) if gate.present else None
    except (OSError, ValueError, MokataError) as exc:
        out.append(unknown(f"the review verdict ({exc})"))
        return out, fp, lines
    if gate.blocks:
        out.append(_block("review-blocks", gate.message if (gate.unblock or "") in gate.message
                             else f"{gate.message} — {gate.unblock}".strip(" —")))
    else:
        data = (ev or {}).get("data") if isinstance((ev or {}).get("data"), dict) else {}
        stamp = data.get("tree") if isinstance(data.get("tree"), dict) else None
        fp["verdict_ts"] = (ev or {}).get("ts")
        if stamp is None or stamp.get("error"):
            out.append(_block("review-unstamped", "the review verdict does not say which commit it "
                              "reviewed — the evidence is UNKNOWN; re-run review at "
                              f"{head[:12]} (/mokata:review)"))
        elif not _same(str(stamp.get("worktree") or ""), worktree) or stamp.get("head") != head:
            out.append(_block("review-stale", f"the review was of {str(stamp.get('head'))[:12]} in "
                              f"{stamp.get('worktree')}, not {head[:12]} here — re-review"))
        elif stamp.get("clean") is not True:
            out.append(_block("review-dirty", "the review ran on a worktree with uncommitted "
                              "changes — re-review the commit"))
        elif stamp.get("base") != base:
            out.append(_block("review-base", f"the review was against base "
                              f"{str(stamp.get('base'))[:12]}, the base is now {base[:12]} — "
                              f"re-review"))
        else:
            lines.append(f"review passed at {head[:12]}"
                         + ("" if gate.independent else " (inline two-pass, not an independent "
                                                        "reviewer)"))
    return out, fp, lines


# ================================================================================ the plan
def _changes(git: Git, main: str, base: str, head: str) -> Tuple[Optional[List[Tuple[str, str, str, str]]], str]:
    """[(old mode, new mode, status, path)] for base..head, no renames."""
    code, out, err = git.run(main, "diff-tree", "-r", "-z", "--raw", "--no-renames",
                             "--no-ext-diff", "--no-textconv", base, head, read=True)
    if code != 0:
        return None, err.strip()[:300]
    parts = out.decode("utf-8", "surrogateescape").split("\0")
    rows = []
    i = 0
    while i + 1 < len(parts):
        meta = parts[i]
        if not meta.startswith(":"):
            i += 1
            continue
        f = meta[1:].split()
        rows.append((f[0], f[1], f[4] if len(f) > 4 else "?", parts[i + 1]))
        i += 2
    return rows, ""


def _attr_hits(git: Git, main: str, rev: str, paths: List[str]) -> Tuple[Optional[Dict[str, List[str]]], str]:
    """{filter|merge: [driver]} that the attributes of `rev` assign to `paths` (values only)."""
    if not paths:
        return {"filter": [], "merge": []}, ""
    code, out, err = git.run(main, "check-attr", f"--source={rev}", "-z", "--stdin", "filter",
                             "merge", stdin=("\0".join(paths) + "\0").encode("utf-8", "surrogateescape"),
                             read=True)
    if code != 0:
        return None, err.strip()[:300]
    items = _z(out)
    hits: Dict[str, List[str]] = {"filter": [], "merge": []}
    for j in range(0, len(items) - 2, 3):
        attr, val = items[j + 1], items[j + 2]
        if attr in hits and val not in ("unspecified", "unset", "set", "binary", "text", "union"):
            hits[attr].append(val)
    return hits, ""


def _config(git: Git, main: str) -> Tuple[str, List[str]]:
    """(fingerprint, lines) of the effective config keys that name code (includes followed). Our
    own `-c` overrides (scope `command`) are not the repo's config and are left out — they carry
    this job's private hooks path, which differs per run."""
    code, out, _e = git.run(main, "config", "--show-origin", "--show-scope", "-z", "--get-regexp",
                            _CONFIG_KEYS, read=True)
    items = _z(out) if code == 0 else []
    kept: List[str] = []
    for j in range(0, len(items) - 2, 3):
        if items[j] != "command":
            kept += items[j:j + 3]
    return hashlib.sha256("\0".join(kept).encode("utf-8", "surrogateescape")).hexdigest(), kept


def _fold(path: str) -> str:
    from .repo_paths import fold_component
    return "/".join(fold_component(p) for p in path.split("/"))


def _unportable(path: str, main: str, longpaths: bool) -> str:
    for comp in path.split("/"):
        if _WIN_RESERVED.match(comp):
            return f"{path} (reserved name {comp!r})"
        if comp.endswith((".", " ")):
            return f"{path} (ends with a dot or space)"
        if _WIN_BAD.search(comp):
            return f"{path} (a character Windows cannot store)"
    if not longpaths and len(os.path.join(main, *path.split("/"))) >= 260:
        return f"{path} (longer than 260 characters and core.longpaths is off)"
    return ""


#: What a merge WITHOUT run evidence (the override path) has not shown, named in its message.
OVERRIDE_UNMET = ("no mokata run is bound to this branch", "no full-suite run by mokata's runner",
                  "no acceptance criterion proven by a passing test", "no review at this head")


def _evidence_line(head: str, run_id: Optional[str], blocked: List[Dict[str, str]],
                   override: Optional[Dict[str, Any]]) -> str:
    """The message's evidence line, built from the evidence ACTUALLY present at `head` — never a
    claim the plan did not verify. A clean plan says what was verified; an override says it is one
    and names each unmet item; a blocked plan names each blocker (it cannot be proposed as is)."""
    at = f"at {head[:12]}" + (f" (run {run_id})" if run_id else "")
    if blocked:
        return (f"Gated merge by mokata {at}: NOT ready — unmet: "
                + "; ".join(f"{b['code']}" for b in blocked) + ".")
    if override is not None:
        return (f"Gated merge by mokata {at}: merged under override "
                f"{override.get('id')} by {override.get('actor')}: {override.get('reason')}. "
                f"Unmet: " + "; ".join(OVERRIDE_UNMET) + ".")
    return f"Gated merge by mokata: tests green and review passed {at}."


def _draft_message(main: str, run_id: Optional[str], branch: str, head: str,
                   blocked: List[Dict[str, str]], override: Optional[Dict[str, Any]]) -> str:
    title, acs = "", []
    if run_id:
        from .run_resolver import SPEC_PREFIX
        st, spec = read3(_store(main), SPEC_PREFIX + run_id)
        if st == "ok":
            title = str(spec.get("title") or "")
            acs = [c for c in spec.get("criteria") or [] if isinstance(c, dict)]
    short = branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else branch
    lines = [f"Merge {short}" + (f": {title}" if title else "")]
    if acs:
        lines += ["", "Acceptance criteria:"]
        lines += [f"- {c.get('id')}: {str(c.get('text') or '')[:160]}" for c in acs[:30]]
    lines += ["", _evidence_line(head, run_id, blocked, override)]
    return "\n".join(lines)


def plan_merge(root: str, target: str, strategy: str = "merge-commit",
               message: Optional[str] = None, *, git: Optional[Git] = None,
               nt: Optional[bool] = None) -> MergePlan:
    """Preview the merge of `target` (a session worktree's path or branch) into the main checkout.
    Pure: reads git and mokata's record, writes nothing."""
    if strategy not in STRATEGIES:
        raise MergeGateError(f"strategy must be one of {', '.join(STRATEGIES)}")
    from .repo_identity import canonical_repo_root
    main = canonical_repo_root(root)
    if git is None:
        try:
            with Git(main) as g:
                return plan_merge(root, target, strategy, message, git=g, nt=nt)
        except GitSafeError as exc:
            return MergePlan(False, GIT_TOO_OLD if exc.code == GIT_TOO_OLD else BLOCKED,
                             [_block(exc.code, str(exc))])
    blocked: List[Dict[str, str]] = []
    row, why = find_target(git, main, target)
    if row is None:
        return MergePlan(False, BLOCKED, [_block("not-a-worktree", why)])
    wt = str(row["worktree"])
    common = _common_dir(git, main) or ""
    branch = str(row.get("branch") or "")
    head = str(row.get("HEAD") or "")
    if not branch.startswith("refs/heads/") or not _SAFE_BRANCH.match(branch[len("refs/heads/"):]):
        return MergePlan(False, BLOCKED, [_block("unsafe-branch", "the worktree is not on a plain "
                                                 "local branch (detached, or an unusual name)")])
    code, into = git.text(main, "symbolic-ref", "-q", "HEAD", read=True)
    if code != 0 or not into.startswith("refs/heads/"):
        return MergePlan(False, BLOCKED, [_block("detached-main", "the main checkout is on a "
                                                 "detached HEAD — check out the branch to merge into")])
    base = _rev(git, main, into) or ""
    if _rev(git, wt, "HEAD") != head or _rev(git, main, branch) != head:
        return MergePlan(False, BLOCKED, [_block("head-moving", "the worktree's branch is moving "
                                                 "while it is read — try again")])
    head_tree = git.text(main, "rev-parse", head + "^{tree}", read=True)[1]
    c1, behind = git.text(main, "rev-list", "--count", f"{head}..{base}", read=True)
    c2, ahead = git.text(main, "rev-list", "--count", f"{base}..{head}", read=True)
    behind_n = int(behind) if c1 == 0 and behind.isdigit() else -1
    ahead_n = int(ahead) if c2 == 0 and ahead.isdigit() else -1
    card: Dict[str, Any] = {"worktree": wt, "branch": branch, "head": head, "into": into,
                            "base": base, "head_tree": head_tree, "commits": ahead_n,
                            "behind": behind_n}
    # P6 — base
    if behind_n != 0:
        blocked.append(_block("behind", f"{behind_n if behind_n > 0 else '?'} commit(s) landed on "
                              f"{into[len('refs/heads/'):]} since this branch — Sync (rebase onto it), "
                              f"re-run the full suite, re-review, then merge"))
    if ahead_n == 0:
        blocked.append(_block("nothing-to-merge", "the branch has no commit the base lacks"))
    # P5 — trees
    wt_gitdir, main_gitdir = _gitdir(git, wt), _gitdir(git, main)
    for name, tree, gd, untracked in (("worktree", wt, wt_gitdir, True), ("main checkout", main,
                                                                          main_gitdir, False)):
        clean = _status_clean(git, tree, untracked)
        if clean is None:
            blocked.append(_block("unknown-tree", f"the {name}'s status could not be read"))
        elif not clean:
            blocked.append(_block("dirty-worktree" if tree == wt else "dirty-main",
                                  f"the {name} has uncommitted changes" + (" (untracked files "
                                  "count)" if untracked else "") + " — commit or clean them first"))
        busy = _in_progress(gd) if gd else ["(git dir unreadable)"]
        if busy == ["index.lock"]:
            blocked.append(_block(INDEX_LOCKED, f"the {name}'s index is locked by another git "
                                  "command (index.lock) — retry when it finishes"))
        elif busy:
            blocked.append(_block("op-in-progress", f"the {name} has an operation in progress or a "
                                  f"lock: {', '.join(busy)}"))
    # the change
    changes, err = _changes(git, main, base, head)
    if changes is None:
        return MergePlan(False, BLOCKED, blocked + [_block("unknown-change", f"the change could "
                                                           f"not be listed: {err}")], card=card)
    paths = [p for _o, _n, _s, p in changes]
    card["files"] = len(paths)
    added = [p for o, _n, s, p in changes if s == "A"]
    # P7
    gitlinks = [p for o, n, _s, p in changes if "160000" in (o, n)]
    if gitlinks:
        blocked.append(_block(SUBMODULE, f"the change touches {len(gitlinks)} submodule link(s) "
                              f"({gitlinks[0]}) — the gated merge does not update submodules"))
    code, sparse = git.text(main, "config", "--type=bool", "--get", "core.sparseCheckout", read=True)
    code2, split = git.text(main, "config", "--type=bool", "--get", "core.splitIndex", read=True)
    code3, shared = git.text(main, "rev-parse", "--shared-index-path", read=True)
    if sparse == "true" or split == "true" or (code3 == 0 and shared):
        blocked.append(_block(UNSUPPORTED_INDEX, "the main checkout uses a sparse checkout or a "
                              "split index — the gated merge does not support it in 0.0.27"))
    changed = set(paths)
    code, out, _e = git.run(main, "ls-files", "-v", "-z", read=True)
    hidden = [it[2:] for it in _z(out) if len(it) > 2 and (it[0] == "S" or it[0].islower())
              and it[2:] in changed] if code == 0 else []
    if hidden:
        blocked.append(_block(HIDDEN, f"{len(hidden)} changed path(s) are marked assume-unchanged "
                              f"or skip-worktree in the main checkout ({hidden[0]}) — a checkout "
                              f"would overwrite local changes git is hiding"))
    code, out, _e = git.run(main, "ls-tree", "-r", "-z", "--name-only", "--full-tree", head, read=True)
    if code == 0:
        groups: Dict[str, List[str]] = {}
        for p in _z(out):
            groups.setdefault(_fold(p), []).append(p)
        col = [g for g in groups.values() if len(g) > 1 and changed & set(g)]
        if col:
            blocked.append(_block(CASE, f"{len(col)} group(s) of paths differ only by case or a "
                                  f"Windows-equivalent spelling ({', '.join(col[0][:3])}) — a "
                                  f"checkout on macOS/Windows would overwrite one with another"))
    base_paths = None
    for p in added:
        full = os.path.join(main, *p.split("/"))
        if os.path.lexists(full):
            parent = os.path.dirname(full)
            try:
                exact = os.path.basename(full) in os.listdir(parent)
            except OSError:
                exact = True
            if exact:
                blocked.append(_block("untracked-collision", f"{p} already exists in the main "
                                      f"checkout (untracked) — the merge would overwrite it"))
                break
            if base_paths is None:
                c0, o0, _e0 = git.run(main, "ls-tree", "-r", "-z", "--name-only", "--full-tree",
                                      base, read=True)
                base_paths = {_fold(x) for x in _z(o0)} if c0 == 0 else set()
            if _fold(p) not in base_paths or _fold(p) in {_fold(x) for x in added if x != p}:
                blocked.append(_block(CASE, f"{p} collides with an existing file that differs only "
                                      f"by case"))
                break
    is_nt = (os.name == "nt") if nt is None else nt
    if is_nt:
        _c, lp = git.text(main, "config", "--type=bool", "--get", "core.longpaths", read=True)
        bad = next((u for u in (_unportable(p, main, lp == "true") for p in added) if u), "")
        if bad:
            blocked.append(_block(UNPORTABLE, f"a path cannot exist on Windows: {bad}"))
    # code the repo could choose: attributes in M and base, config, hooks
    attrs: Dict[str, List[str]] = {"filter": [], "merge": []}
    for rev in (head, base):
        hits, err = _attr_hits(git, main, rev, paths)
        if hits is None:
            blocked.append(_block("unknown-attributes", f"the attributes of the change could not be "
                                  f"read: {err}"))
            break
        for k in attrs:
            attrs[k] = sorted(set(attrs[k]) | set(hits[k]))
    config_fp, config_items = _config(git, main)
    configured = " ".join(config_items).lower()
    flt = [d for d in attrs["filter"] if f"filter.{d.lower()}." in configured]
    drv = [d for d in attrs["merge"] if f"merge.{d.lower()}.driver" in configured]
    if flt:
        blocked.append(_block(NEEDS_FILTER, f"the change's .gitattributes apply the filter "
                              f"{', '.join(flt)} (configured in this repo) — the gated merge never "
                              f"runs repo-chosen filters; merge from your own terminal"))
    if drv:
        blocked.append(_block(NEEDS_DRIVER, f"the change's .gitattributes name the merge driver "
                              f"{', '.join(drv)} — the gated merge never runs it"))
    hooks_dir = os.path.join(common or os.path.join(main, ".git"), "hooks")
    try:
        hooks = sorted(n for n in os.listdir(hooks_dir) if not n.endswith(".sample"))
    except OSError:
        hooks = []
    card["hooks"] = hooks
    # identity
    _c1, author = git.text(main, "var", "GIT_AUTHOR_IDENT", read=True)
    _c2, committer = git.text(main, "var", "GIT_COMMITTER_IDENT", read=True)
    if _c1 != 0 or _c2 != 0 or "<" not in author:
        blocked.append(_block(NO_IDENTITY, "git has no author identity here (user.name / "
                              "user.email) — set it with `git config user.email …`; identities "
                              "set only in GIT_* environment variables are not used"))
    # P1–P4 — the run and its evidence
    runs = _runs_bound(main, wt)
    override = None
    fp: Dict[str, Any] = {}
    run_id: Optional[str] = None
    lines: List[str] = []
    if len(runs) > 1:
        blocked.append(_block("ambiguous-run", f"{len(runs)} runs are bound to this worktree "
                              f"({', '.join(runs[:3])}) — the evidence would be ambiguous"))
    elif not runs:
        override = read_override(main, common, wt, head)
        if override is None:
            blocked.append(_block("no-run", "no mokata run is bound to this worktree, so there is "
                                  "no evidence to judge. To merge anyway, file an override: "
                                  f"`mokata worktree merge-override {wt} --reason \"…\"`"))
        else:
            card["override"] = (f"{override.get('id')} by {override.get('actor')}: "
                                f"{override.get('reason')}")
    else:
        run_id = runs[0]
        ev_block, fp, lines = evidence(main, run_id, wt, head, base)
        blocked += ev_block
    card["evidence"] = lines
    card["run"] = run_id
    msg = None if strategy == "ff" else (message if message is not None
                                          else _draft_message(main, run_id, branch, head,
                                                              blocked, override))
    args = {"repo": os.path.realpath(common) if common else "", "worktree": os.path.realpath(wt),
            "branch": branch, "head": head, "head_tree": head_tree, "into": into, "base": base,
            "strategy": strategy, "message": msg,
            "message_sha": None if msg is None else hashlib.sha256(msg.encode("utf-8")).hexdigest(),
            "evidence": fp, "override": (override or {}).get("id"),
            "author": _ident(author), "committer": _ident(committer),
            "git": [git.exe, git.version_text], "config": config_fp,
            "attrs": _sha(attrs), "hooks": hooks}
    state = READY
    if blocked:
        named = next((b["code"] for b in blocked if b["code"] in NAMED), "")
        state = named or BLOCKED
    return MergePlan(not blocked, state, blocked, args, card)


# ================================================================================ approval
def _approval_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """What the approval hashes: the plan's args minus the message text (its sha is bound)."""
    return {k: v for k, v in args.items() if k != "message"}


def propose(root: str, plan: MergePlan) -> Dict[str, Any]:
    from . import approval
    from .awaiting import APPROVE_CMD
    from .repo_identity import canonical_repo_root
    p = approval.propose(canonical_repo_root(root), tool=TOOL, args=_approval_args(plan.args),
                         run_id=APPROVAL_RUN, target=plan.args["worktree"],
                         summary=f"merge {plan.card.get('branch')} into {plan.card.get('into')} "
                                 f"({plan.args['strategy']})", preview=plan.render())
    _save_message(root, p.proposal_id, plan)
    return {"proposal_id": p.proposal_id, "command": APPROVE_CMD.format(proposal_id=p.proposal_id),
            "expires_in": p.expires_in(time.time())}


def _save_message(root: str, pid: str, plan: MergePlan) -> None:
    """What a re-call needs besides the id: the message text (only its sha is hashed), the
    strategy, and the heads the human saw — so a moved branch is NAMED, not 'content changed'."""
    from .repo_identity import canonical_repo_root
    a = plan.args
    _store(canonical_repo_root(root)).write(REQUEST_PREFIX + pid, {
        "message": a.get("message"), "strategy": a.get("strategy"), "head": a.get("head"),
        "base": a.get("base"), "into": a.get("into"), "worktree": a.get("worktree")})


def approved_request(root: str, proposal_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The (worktree, strategy, message) a proposal was made for, so a re-call needs only its id."""
    from . import approval
    from .repo_identity import canonical_repo_root
    main = canonical_repo_root(root)
    p = approval.load(main, proposal_id)
    if p is None or p.tool != TOOL:
        return None, "no such merge proposal (it expired, was pruned, or is not a merge)"
    st, data = read3(_store(main), REQUEST_PREFIX + proposal_id)
    if st != "ok" or data.get("strategy") not in STRATEGIES:
        return None, "the proposal's request record is missing — propose the merge again"
    return {"worktree": p.target, "message": data.get("message"), "strategy": data["strategy"],
            "head": data.get("head"), "base": data.get("base"), "into": data.get("into")}, ""


def ready_to_run(root: str, proposal_id: str) -> Tuple[bool, str, str]:
    """(ok, code, reason) — a cheap screen before a merge job starts: the proposal is a merge, a
    human approved it (not in chat), it is unused and unexpired. Burns nothing; `run_merge` re-checks
    everything against the re-planned merge."""
    from . import approval
    from .repo_identity import canonical_repo_root
    p = approval.load(canonical_repo_root(root), proposal_id)
    if p is None or p.tool != TOOL:
        return False, approval.REFUSED_UNKNOWN, approval._REFUSAL_TEXT[approval.REFUSED_UNKNOWN]
    for code, bad in ((approval.REFUSED_USED, p.status == approval.STATUS_USED),
                      (approval.REFUSED_REJECTED, p.status == approval.STATUS_REJECTED),
                      (approval.REFUSED_EXPIRED, p.expired()),
                      (approval.REFUSED_NOT_APPROVED, p.status != approval.STATUS_APPROVED)):
        if bad:
            return False, code, approval._REFUSAL_TEXT[code]
    if p.approved_by == CHAT_ACTOR:
        return False, "chat-relayed", CHAT_REFUSAL
    return True, "", ""


CHAT_REFUSAL = ("a merge needs an approval typed in your own terminal (`mokata approve <id>`) or "
                "the IDE's terminal code — an in-chat approval does not license a merge")


def _check_approval(main: str, proposal_id: str, args: Dict[str, Any]) -> Tuple[bool, str, str]:
    """(ok, code, reason) WITHOUT burning: is there a human approval of exactly this merge?"""
    from . import approval
    p = approval.load(main, proposal_id)
    code = approval._verify(p, chash=approval.content_hash(TOOL, _approval_args(args)),
                            run_id=APPROVAL_RUN, now=time.time())
    if code is not None:
        return False, code, approval._REFUSAL_TEXT.get(code, "refused")
    if p is not None and p.approved_by == CHAT_ACTOR:
        return False, "chat-relayed", CHAT_REFUSAL
    return True, "", ""


def _burn(main: str, proposal_id: str, args: Dict[str, Any],
          redeem: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Tuple[bool, str, str, Any]:
    from . import approval
    if redeem is not None:
        c = redeem(_approval_args(args))
    else:
        c = approval.redeem(main, proposal_id, tool=TOOL, args=_approval_args(args),
                            run_id=APPROVAL_RUN)
    return c.granted, c.code, c.reason, c.proposal


# ================================================================================ the journal
def _journal(main: str, mid: str, **fields: Any) -> None:
    store = _store(main)
    key = JOURNAL_PREFIX + mid

    def mut(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur.update(fields)
        cur["merge_id"] = mid
        cur["updated"] = time.time()
        return cur
    store.update(key, mut, default=None)


def journals(root: str) -> List[Dict[str, Any]]:
    """Every merge whose journal is not `done` — what every surface shows until it is recovered."""
    from .repo_identity import canonical_repo_root
    from .tdd_state import state_dir
    main = canonical_repo_root(root)
    try:
        names = sorted(n for n in os.listdir(state_dir(main))
                       if n.startswith(JOURNAL_PREFIX) and n.endswith(".json"))
    except OSError:
        return []
    out = []
    store = _store(main)
    for n in names[:200]:
        data = store.read(n[:-len(".json")])
        if isinstance(data, dict) and data.get("phase") != "done":
            out.append(data)
    return out


def _ledger(main: str, **fields: Any) -> None:
    from . import MOKATA_DIR
    from .govern.ledger import AuditLedger
    try:
        AuditLedger.from_mokata_dir(os.path.join(main, MOKATA_DIR)).record(LEDGER_KIND, **fields)
    except OSError:
        pass                           # the merge stands; the journal still holds its facts


# ================================================================================ execute
@dataclass
class Hooks:
    """Test seams INJECTED by the caller (never an environment switch): `at(step)` is called as
    each step begins, so a test can pause, fail or race the merge at an exact point."""

    at: Callable[[str], None] = lambda step: None


def _base_lock_path(main: str, args: Dict[str, Any]) -> str:
    from .tdd_state import state_dir
    h = hashlib.sha256(f"{args.get('repo')}\0{args.get('into')}".encode()).hexdigest()[:16]
    return os.path.join(state_dir(main), f"merge_base__{h}.lock")


def _hold_base(path: str):
    """The per-base cross-process lock, waited for visibly (Cancel works while waiting). Advisory:
    it serialises mokata merges on one machine; across machines the CAS is the backstop."""
    from .oslock import LockTimeout, file_lock
    os.makedirs(os.path.dirname(path), exist_ok=True)
    while True:
        C.checkpoint()
        cm = file_lock(path, timeout=0.5)
        try:
            cm.__enter__()
        except LockTimeout:
            C.report(note="waiting for another merge into this branch to finish")
            continue
        return cm


def run_merge(root: str, proposal_id: str, *, hooks: Optional[Hooks] = None,
            seal: Optional[Callable[[], bool]] = None,
            redeem: Optional[Callable[[Dict[str, Any]], Any]] = None) -> MergeOutcome:
    """Carry out the human-approved merge `proposal_id`. `seal()` is called once, just before the
    commit point: it returns False when a Cancel already won (the merge then stops, unchanged) and
    otherwise makes the job uncancellable. Every result is a named `MergeOutcome`.

    `redeem(approval_args)` is the surface's consent path, called ONCE at s4 (after the seal, before
    the CAS) to burn the human's approval; it returns an `approval.Consent`. The MCP tool passes
    its `_consent` (mcp/consent.py) here, so an MCP merge is licensed by exactly the consent check
    every other gated MCP write uses. Default: `approval.redeem` directly (CLI/IDE)."""
    from .repo_identity import canonical_repo_root
    main = canonical_repo_root(root)
    hk = hooks or Hooks()
    req, why = approved_request(main, proposal_id)
    if req is None:
        return MergeOutcome(REFUSED, why, proposal_id)
    strategy = req["strategy"]
    ML.mark_active(proposal_id, True)
    try:
        with Git(main) as git:
            return _execute(main, proposal_id, req, strategy, git, hk, seal, redeem)
    except C.Cancelled:
        try:
            _store(main).delete(JOURNAL_PREFIX + proposal_id)
        except OSError:
            pass
        return MergeOutcome(CANCELLED, "cancelled before the commit point — nothing changed",
                            proposal_id)
    except GitSafeError as exc:
        return MergeOutcome(GIT_TOO_OLD if exc.code == GIT_TOO_OLD else EXEC_CHANGED, str(exc),
                            proposal_id)
    finally:
        ML.mark_active(proposal_id, False)


def _execute(main: str, pid: str, req: Dict[str, Any], strategy: str, git: Git, hk: Hooks,
             seal: Optional[Callable[[], bool]],
             redeem: Optional[Callable[[Dict[str, Any]], Any]] = None) -> MergeOutcome:
    C.report(done=0, total=6, unit="steps", note="waiting for the branch")
    # s1 — re-plan, compare with what was approved
    hk.at("s1")
    C.report(done=0, total=6, unit="steps", note="s1 · re-checking the evidence and both trees")
    plan = plan_merge(main, req["worktree"], strategy, req.get("message"), git=git)
    if not plan.args:
        return MergeOutcome(plan.state, plan.reason(), pid, {"blocked": plan.blocked})
    cm = _hold_base(_base_lock_path(main, plan.args))
    try:
        plan = plan_merge(main, req["worktree"], strategy, req.get("message"), git=git)
        ok, code, reason = _check_approval(main, pid, plan.args)
        if not ok:
            moved = _moved(req, plan.args)
            if moved:
                return MergeOutcome(moved[0], moved[1], pid, {"plan": plan.card})
            return MergeOutcome(REFUSED if code != approval_content_changed() else CONTENT_CHANGED,
                                reason, pid, {"code": code})
        if not plan.ok:
            only_locked = plan.blocked and all(b["code"] == INDEX_LOCKED for b in plan.blocked)
            return MergeOutcome(BUSY if only_locked else plan.state, plan.reason(), pid,
                                {"blocked": plan.blocked})
        a = plan.args
        _journal(main, pid, phase="started", main_checkout=main, worktree=a["worktree"],
                 branch=a["branch"], head=a["head"], into=a["into"], base=a["base"],
                 strategy=strategy, started=time.time(), pid=os.getpid())
        # s2 — the merged tree, objects only
        hk.at("s2")
        C.report(done=1, total=6, unit="steps", note="s2 · computing the merged tree (no files touched)")
        code, out, err = git.run(main, "merge-tree", "--write-tree", "--no-messages", a["base"],
                                 a["head"])
        tree = out.decode("utf-8", "replace").splitlines()[0].strip() if out else ""
        if code == 1:
            return _end(main, pid, CONFLICT, "the branches conflict: " + ", ".join(
                out.decode("utf-8", "replace").splitlines()[1:6]))
        if code != 0 or not tree:
            return _end(main, pid, TREE_MISMATCH, f"git could not compute the merged tree: {err[:300]}")
        if tree != a["head_tree"]:
            return _end(main, pid, TREE_MISMATCH, f"the merged tree {tree[:12]} is not the tested "
                                                  f"tree {a['head_tree'][:12]} — nothing changed")
        # s3 — the commit, objects only, unsigned
        hk.at("s3")
        C.report(done=2, total=6, unit="steps", note="s3 · writing the merge commit (objects only)")
        if strategy == "ff":
            M = a["head"]
        else:
            parents = ["-p", a["base"]] + (["-p", a["head"]] if strategy == "merge-commit" else [])
            code, M = git.text(main, "commit-tree", "--no-gpg-sign", tree, *parents, "-F", "-",
                               stdin=(a["message"] or "").encode("utf-8"))
            if code != 0 or not _SHA.match(M):
                return _end(main, pid, NO_IDENTITY if "ident" in M.lower() else BLOCKED,
                            f"git could not write the merge commit: {M[:300]}")
        _journal(main, pid, merge=M)
        # s4 — the index lock with our record; re-measure lock-free; seal; burn
        hk.at("s4")
        C.report(done=3, total=6, unit="steps", note="s4 · locking the main checkout's index")
        gitdir = _gitdir(git, main) or os.path.join(main, ".git")
        lock = ML.IndexLock(gitdir, pid)
        try:
            lock.acquire()
        except ML.LockError as exc:
            return _end(main, pid, BUSY, str(exc), keep_journal=False)
        released = landed = False
        try:
            miss = _remeasure(git, main, a, M)
            if miss:
                lock.release()
                released = True
                return _end(main, pid, miss[0], miss[1])
            if seal is not None and not seal():
                lock.release()
                released = True
                return _end(main, pid, CANCELLED, "cancelled before the commit point — nothing "
                                                  "changed", keep_journal=False)
            git.sealed = True          # past here no git call can be cancelled
            granted, code, reason, prop = _burn(main, pid, a, redeem)
            if not granted:
                lock.release()
                released = True
                return _end(main, pid, REFUSED, reason)
            # s5 — THE COMMIT POINT
            hk.at("s5")
            C.report(done=4, total=6, unit="steps", note="s5 · moving the branch (compare-and-swap)")
            txn = (f"start\nverify {a['branch']} {a['head']}\n"
                   f"update {a['into']} {M} {a['base']}\nprepare\ncommit\n")
            code, _o, err = git.run(main, "update-ref", "-m", f"mokata gated merge {pid}", "--stdin",
                                    stdin=txn.encode("utf-8"), cancellable=False)
            if code != 0:
                lock.release()
                released = True
                now_b, now_h = _rev(git, main, a["into"]), _rev(git, main, a["branch"])
                if now_h != a["head"]:
                    return _end(main, pid, HEAD_MOVED, f"the branch moved to {str(now_h)[:12]} after "
                                                       f"your approval — nothing changed")
                if now_b != a["base"]:
                    return _end(main, pid, BASE_MOVED, f"{a['into']} moved to {str(now_b)[:12]} "
                                                       f"after your approval — nothing changed")
                return _end(main, pid, BUSY, f"the branch ref was locked by another git ({err[:200]}) "
                                             f"— nothing changed; retry", keep_journal=False)
            landed = True
            _journal(main, pid, phase="ref-updated", landed=time.time())
            _ledger(main, decision="merged", proposal=pid, into=a["into"], base=a["base"],
                    head=a["head"], merge=M, strategy=strategy,
                    approved_by=getattr(prop, "approved_by", ""))
            # s6 — bring the main checkout to M
            hk.at("s6")
            C.report(done=5, total=6, unit="steps", note="s6 · writing the merged files into the "
                                                         "main checkout (cannot be cancelled)")
            done, why = _checkout(git, main, gitdir, a["base"], M, lock, hk, a["into"])
            if not done:
                lock.keep()
                released = True
                _journal(main, pid, phase="checkout-incomplete", problem=why)
                return MergeOutcome(INCOMPLETE, (
                    f"MERGED — {a['into']} is now {M[:12]} — but the main checkout could not be "
                    f"brought to it: {why}. The index stays LOCKED so no git command there can "
                    f"undo the merge by accident. Fix the cause, then Recover "
                    f"(`mokata worktree merge --recover {pid}`)."), pid, {"merge": M})
            released = True
            ok_b = _rev(git, main, a["into"]) == M
            c_idx, _o2, _e2 = git.run(main, "diff-index", "--cached", "--quiet", M, read=True)
            if not ok_b or c_idx != 0:
                _journal(main, pid, phase="diverged")
                return MergeOutcome(DIVERGED, "the merge landed but the main checkout does not read "
                                              "back the merged commit — run Recover to see what is "
                                              "true", pid, {"merge": M})
            _journal(main, pid, phase="done", finished=time.time())
            C.report(done=6, total=6, unit="steps", note="merged")
            return MergeOutcome(MERGED, f"merged {a['branch'][11:]} into {a['into'][11:]} as "
                                        f"{M[:12]} ({strategy}). Undo while it is the tip: "
                                        f"`git update-ref {a['into']} {a['base']} {M}`; otherwise "
                                        f"`git revert {'-m 1 ' if strategy == 'merge-commit' else ''}"
                                        f"{M[:12]}`. The worktree is kept — "
                                        f"delete it separately.", pid,
                                {"merge": M, "into": a["into"], "base": a["base"]})
        finally:
            if not released:
                # an exception mid-step (a Cancel before the seal, a bug): before the commit point
                # nothing changed, so the lock goes; after it, the guard is never dropped silently
                (lock.keep if landed else lock.release)()
    finally:
        cm.__exit__(None, None, None)


def approval_content_changed() -> str:
    from .approval import REFUSED_CONTENT_CHANGED
    return REFUSED_CONTENT_CHANGED


def _moved(req: Dict[str, Any], args: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """When the approval no longer matches: which BRANCH moved since the human saw it, by name."""
    if req.get("head") and args.get("head") != req["head"]:
        return HEAD_MOVED, (f"the branch moved from {str(req['head'])[:12]} to "
                            f"{str(args.get('head'))[:12]} after your approval — nothing changed; "
                            f"re-test, re-review, approve again")
    if req.get("base") and args.get("base") != req["base"]:
        return BASE_MOVED, (f"{req.get('into')} moved from {str(req['base'])[:12]} to "
                            f"{str(args.get('base'))[:12]} after your approval — nothing changed")
    return None


def _end(main: str, pid: str, state: str, message: str, keep_journal: bool = True) -> MergeOutcome:
    if keep_journal:
        _journal(main, pid, phase="done", result=state, finished=time.time())
    else:
        try:
            _store(main).delete(JOURNAL_PREFIX + pid)
        except OSError:
            pass
    return MergeOutcome(state, message, pid)


def _remeasure(git: Git, main: str, a: Dict[str, Any], M: str) -> Optional[Tuple[str, str]]:
    """s4, UNDER our index lock, lock-free reads only (`diff-index --cached`, `status` with
    optional locks off) — never `write-tree` or a refresh, which would contend with our lock."""
    code, into = git.text(main, "symbolic-ref", "-q", "HEAD", read=True)
    if code != 0 or into != a["into"]:
        return BASE_MOVED, f"the main checkout is no longer on {a['into']} — nothing changed"
    now = _rev(git, main, a["into"])
    if now != a["base"]:
        return BASE_MOVED, (f"{a['into']} moved to {str(now)[:12]} after your approval — nothing "
                            f"changed; sync, re-test, re-review")
    if _rev(git, main, a["branch"]) != a["head"]:
        return HEAD_MOVED, "the worktree's branch moved after your approval — nothing changed"
    c, _o, _e = git.run(main, "diff-index", "--cached", "--quiet", a["base"], read=True)
    if c != 0:
        return DIRTY, "the main checkout's index has staged changes — nothing changed"
    clean = _status_clean(git, main, untracked=False)
    if clean is not True:
        return DIRTY, "the main checkout has uncommitted changes — nothing changed"
    _rows, _ = _changes(git, main, a["base"], a["head"])
    for _o, _n, s, p in (_rows or []):
        if s == "A" and os.path.lexists(os.path.join(main, *p.split("/"))):
            return DIRTY, f"{p} now exists in the main checkout — nothing changed"
    return None


def _head_on(git: Git, main: str, into: str, M: str) -> str:
    """"" when the main checkout's HEAD is still the branch the merge moved (symbolic ref) AND that
    branch is M; otherwise the honest reason. The CAS (s5) binds the BRANCH, not HEAD: a
    `git switch` / `symbolic-ref HEAD` between s4 and s6 would make s6 write M's files onto ANOTHER
    branch's checkout and still read back "merged". Checked immediately before any file is written."""
    code, sym = git.text(main, "symbolic-ref", "-q", "HEAD", read=True)
    if code != 0 or sym != into:
        return (f"the main checkout's HEAD moved to {(sym if code == 0 else 'a detached commit')!r} "
                f"after the branch was moved — no file was written there. Switch it back to {into} "
                f"(`git switch {into[11:] if into.startswith('refs/heads/') else into}`)")
    now = _rev(git, main, "HEAD")
    if now != M:
        return (f"{into} is {str(now)[:12]}, not the merge {M[:12]}, at checkout time — no file was "
                f"written")
    return ""


def _checkout(git: Git, main: str, gitdir: str, base: str, M: str, lock: "ML.IndexLock",
              hk: Hooks, into: str) -> Tuple[bool, str]:
    """s6: the files and the index to M through a temp index; the lock is committed onto
    `.git/index` only when that succeeded. (False, why) leaves the lock held. HEAD (symbolic ref
    and target) is re-checked first: a checkout onto a branch the merge did not move is refused."""
    moved = _head_on(git, main, into, M)
    if moved:
        return False, moved
    tmp = os.path.join(gitdir, f"mokata-merge-{lock.merge_id}.index")
    try:
        with open(os.path.join(gitdir, "index"), "rb") as src, open(tmp, "wb") as dst:
            dst.write(src.read())
    except OSError as exc:
        return False, f"the index could not be copied ({exc})"
    try:
        hk.at("s6-read-tree")
        code, _o, err = git.run(main, "read-tree", "-m", "-u", base, M,
                                env={"GIT_INDEX_FILE": tmp}, cancellable=False)
        if code != 0:
            return False, f"git read-tree failed: {err.strip()[:300]}"
        hk.at("s6-commit")
        lock.commit_index(tmp)
        return True, ""
    except (ML.LockError, OSError) as exc:
        return False, f"the merged files or index could not be written ({exc})"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ================================================================================ recover
#: The Recover help every surface repeats (CLI `--recover`, the lock message, the IDE row, the docs):
#: after a hard crash MID-COMMIT the lock holds index bytes, and a human who deletes it by hand
#: leaves the branch AT the merged commit while the index still reads the base - so a plain
#: `git commit` there would REVERT the merge. The right step is Recover, which decides from git.
HAND_DELETED_LOCK_HELP = ML.HAND_DELETED_LOCK_HELP


def recover(root: str, merge_id: str, *, unlock_proposal: str = "",
            hooks: Optional[Hooks] = None) -> MergeOutcome:
    """Decide from git truth what an interrupted merge left, and finish or report it. Never acts on
    a lock that is not provably ours and dead; removing ours needs its own approval (`TOOL_UNLOCK`)."""
    from .repo_identity import canonical_repo_root
    main = canonical_repo_root(root)
    st, j = read3(_store(main), JOURNAL_PREFIX + merge_id)
    if st != "ok":
        return MergeOutcome(REFUSED, f"no journal for merge {merge_id} ({st})", merge_id)
    if ML.is_active(merge_id):
        return MergeOutcome(BUSY, "that merge is still running in this process", merge_id)
    try:
        with Git(main) as git:
            cm = _hold_base(_base_lock_path(main, {"repo": _common_dir(git, main) and
                                                    os.path.realpath(_common_dir(git, main) or ""),
                                                    "into": j.get("into")}))
            try:
                return _recover(main, merge_id, j, git, unlock_proposal, hooks or Hooks())
            finally:
                cm.__exit__(None, None, None)
    except GitSafeError as exc:
        return MergeOutcome(EXEC_CHANGED, str(exc), merge_id)


def _recover(main: str, mid: str, j: Dict[str, Any], git: Git, unlock_pid: str,
             hk: Hooks) -> MergeOutcome:
    gitdir = _gitdir(git, main) or os.path.join(main, ".git")
    into, base, M = j.get("into", ""), j.get("base", ""), j.get("merge", "")
    B = _rev(git, main, into) if into else None
    lockp = ML.lock_path(gitdir)
    facts = {"into": into, "branch_now": B, "base": base, "merge": M}
    if os.path.lexists(lockp):
        rec, why = ML.read_record(lockp)
        if rec is None:
            return MergeOutcome(LOCK_HELD, f"`.git/index.lock` exists and is NOT mokata's ({why}) — "
                                           f"it is never touched. Close the git process holding it, "
                                           f"then Recover again. "
                                           + HAND_DELETED_LOCK_HELP.replace("<merge-id>", mid),
                                mid, facts)
        ok, why = ML.stale_verdict(lockp, rec)
        if not ok:
            return MergeOutcome(LOCK_HELD, f"the index lock is mokata's but cannot be removed: {why}. "
                                           + HAND_DELETED_LOCK_HELP.replace("<merge-id>", mid),
                                mid, facts)
        sha = ML.lock_sha(gitdir) or ""
        args = {"gitdir": os.path.realpath(gitdir), "lock_sha": sha, "merge": mid}
        if not unlock_pid:
            from . import approval
            from .awaiting import APPROVE_CMD
            p = approval.propose(main, tool=TOOL_UNLOCK, args=args, run_id=APPROVAL_RUN,
                                 target=lockp, summary=f"remove mokata's stale index lock (merge {mid})",
                                 preview=f"lock   : {lockp}\nowner  : {rec}\nverdict: {why}\n"
                                         f"branch : {into} is {str(B)[:12]} (base {base[:12]}, "
                                         f"merge {M[:12]})")
            facts.update(proposal_id=p.proposal_id,
                         command=APPROVE_CMD.format(proposal_id=p.proposal_id))
            return MergeOutcome(LOCK_HELD, f"the index lock is mokata's and stale ({why}). Approve "
                                           f"its removal: {facts['command']}, then Recover with it",
                                mid, facts)
        from . import approval
        c = approval.redeem(main, unlock_pid, tool=TOOL_UNLOCK, args=args, run_id=APPROVAL_RUN)
        if not c.granted:
            return MergeOutcome(REFUSED, c.reason, mid, facts)
        removed, why = ML.remove_stale(gitdir, sha)
        _ledger(main, decision="unlocked", merge=mid, removed=removed, why=why)
        if not removed:
            return MergeOutcome(LOCK_HELD, f"not removed: {why}", mid, facts)
    if B == base:
        _journal(main, mid, phase="done", result=NOT_MERGED, finished=time.time())
        return MergeOutcome(NOT_MERGED, f"the merge did NOT land: {into} is still {base[:12]}. "
                                        f"Nothing else was changed.", mid, facts)
    if not M or B != M:
        return MergeOutcome(DIVERGED, f"{into} is {str(B)[:12]} — neither the base {base[:12]} nor "
                                      f"the merge {M[:12]}. Nothing was touched; look at "
                                      f"`git log {into}` and `git status`", mid, facts)
    # B == M: the merge landed. Finish the checkout from what is on disk.
    lock = ML.IndexLock(gitdir, mid)
    try:
        lock.acquire()
    except ML.LockError as exc:
        return MergeOutcome(BUSY, str(exc), mid, facts)
    keep = True
    try:
        moved = _head_on(git, main, into, M)
        if moved:
            _journal(main, mid, phase="checkout-incomplete", problem=moved)
            return MergeOutcome(INCOMPLETE, f"MERGED, but the checkout cannot be finished: {moved}. "
                                            f"The index stays locked; Recover again", mid, facts)
        done, why, kept = _finish(git, main, gitdir, base, M, lock)
        if not done:
            _journal(main, mid, phase="checkout-incomplete", problem=why)
            return MergeOutcome(INCOMPLETE, f"MERGED, the checkout still could not finish: {why}. "
                                            f"The index stays locked; Recover again", mid, facts)
        keep = False
        _journal(main, mid, phase="done", result=MERGED, finished=time.time())
        _ledger(main, decision="recovered", merge=mid, kept=len(kept))
        note = (f" Your edits in {len(kept)} file(s) were kept as unstaged changes "
                f"({', '.join(kept[:5])})." if kept else "")
        return MergeOutcome(MERGED, f"merged: {into} is {M[:12]} and the main checkout now reads "
                                    f"it.{note}", mid, dict(facts, kept=kept))
    finally:
        if keep:
            lock.keep()


def _blob_of(git: Git, main: str, path: str) -> Optional[str]:
    full = os.path.join(main, *path.split("/"))
    if not os.path.lexists(full):
        return None
    code, out = git.text(main, "hash-object", "--", path, read=True)
    return out if code == 0 else "?"


def _finish(git: Git, main: str, gitdir: str, base: str, M: str, lock: "ML.IndexLock"
            ) -> Tuple[bool, str, List[str]]:
    """Bring the files of the change to M, never overwriting a file that is neither the base's nor
    M's version (it is kept, as an unstaged change); then the index to M."""
    rows, err = _changes(git, main, base, M)
    if rows is None:
        return False, err, []
    want_m, want_b = {}, {}
    for spec in ("M", "B"):
        rev = M if spec == "M" else base
        code, out, _e = git.run(main, "ls-tree", "-r", "-z", "--full-tree", rev, "--",
                                *[p for _o, _n, _s, p in rows], read=True)
        for item in _z(out):
            meta, _, path = item.partition("\t")
            (want_m if spec == "M" else want_b)[path] = meta.split()[2]
    write, remove, kept = [], [], []
    for _o, _n, _s, p in rows:
        now = _blob_of(git, main, p)
        if now == want_m.get(p):
            continue
        if now == want_b.get(p):
            (write if p in want_m else remove).append(p)
        else:
            kept.append(p)
    tmp = os.path.join(gitdir, f"mokata-merge-{lock.merge_id}.index")
    try:
        env = {"GIT_INDEX_FILE": tmp}
        code, _o, err = git.run(main, "read-tree", M, env=env, cancellable=False)
        if code != 0:
            return False, f"read-tree failed: {err[:300]}", kept
        if write:
            code, _o, err = git.run(main, "checkout-index", "-f", "-z", "--stdin", env=env,
                                    stdin=("\0".join(write) + "\0").encode("utf-8", "surrogateescape"),
                                    cancellable=False)
            if code != 0:
                return False, f"checkout-index failed: {err[:300]}", kept
        for p in remove:
            try:
                os.unlink(os.path.join(main, *p.split("/")))
            except FileNotFoundError:
                pass
            except OSError as exc:
                return False, f"{p} could not be removed ({exc})", kept
        git.run(main, "update-index", "-q", "--refresh", env=env, cancellable=False)
        try:
            lock.commit_index(tmp)
        except (ML.LockError, OSError) as exc:
            return False, f"the index could not be committed ({exc})", kept
        return True, "", kept
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ================================================================================ override
def file_override(root: str, target: str, reason: str, actor: str) -> Dict[str, Any]:
    """Record a human's evidence override for ONE worktree at ONE head. Callers gate it (a TTY
    confirmation, or a redeemed approval) — this only writes the record and the ledger line."""
    reason = (reason or "").strip()
    if len(reason) < 8:
        raise MergeGateError("an override needs a real reason (8+ characters) — it is shown on the "
                             "merge card and kept in the audit ledger")
    from .repo_identity import canonical_repo_root
    main = canonical_repo_root(root)
    with Git(main) as git:
        row, why = find_target(git, main, target)
        if row is None:
            raise MergeGateError(why)
        common = _common_dir(git, main) or ""
    wt, head = os.path.realpath(str(row["worktree"])), str(row.get("HEAD") or "")
    key = override_key(common, wt, head)
    rec = {"id": key[len(OVERRIDE_PREFIX):], "worktree": wt, "head": head,
           "repo": os.path.realpath(common), "reason": reason[:500], "actor": actor,
           "at": time.time(), "waives": "no-run"}
    _store(main).write(key, rec)
    _ledger(main, decision="override", worktree=wt, head=head, reason=reason[:500], actor=actor,
            override=rec["id"])
    return rec


def override_args(root: str, target: str, reason: str) -> Dict[str, Any]:
    from .repo_identity import canonical_repo_root
    main = canonical_repo_root(root)
    with Git(main) as git:
        row, why = find_target(git, main, target)
        if row is None:
            raise MergeGateError(why)
        common = _common_dir(git, main) or ""
    return {"repo": os.path.realpath(common), "worktree": os.path.realpath(str(row["worktree"])),
            "head": str(row.get("HEAD") or ""), "reason": (reason or "").strip()[:500]}


__all__ = ["APPROVAL_RUN", "Hooks", "MergeGateError", "MergeOutcome", "MergePlan", "STRATEGIES",
           "TOOL", "TOOL_OVERRIDE", "TOOL_UNLOCK", "approved_request", "evidence",
           "file_override", "find_target", "journals", "override_args", "plan_merge", "propose",
           "read3", "read_override", "ready_to_run", "recover", "run_merge", "tree_stamp"]
