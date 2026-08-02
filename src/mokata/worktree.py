"""Stage 51 — git-worktree isolation (parallel/fanout tasks + paused/WIP sessions).

A throwaway git worktree per isolated unit so concurrent or suspended work never stomps the
main working tree. OFF/opt-in by default (a run without a manager behaves exactly as today),
DEGRADE-CLEAN (not a git repo / git unavailable / disabled ⇒ in-place fallback, never a
crash), and AUDITED (create/remove logged to the ledger). Worktrees live under the gitignored
`.mokata/temp_local/worktrees/` and are AUTO-CLEANED — a clean (unchanged) worktree is removed
on completion; the `isolated()` context manager force-removes throwaway task worktrees so no
orphan is ever left.

Git is injected (`git=`) so the manager is fully testable with a fake; the default runner
shells out to `git`. Dependency-free (subprocess + stdlib); clean-room.
"""

from __future__ import annotations

import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, List, Optional, Tuple

from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME

WORKTREES_DIRNAME = "worktrees"


@dataclass
class GitResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class Worktree:
    label: str
    path: str


@dataclass
class RemoveResult:
    removed: bool
    changed: bool


@dataclass
class GitWorktree:
    """One row of `git worktree list --porcelain` (WT-LIST / FR-WT-1). Pure git facts — the SESSION
    join and the staleness verdict are built on top of this, in `worktree_list.py`, so this stays a
    faithful parse of what git said and nothing more."""

    path: str
    head: str = ""
    branch: str = ""            # short name ("main"), "" when detached or bare
    bare: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False
    is_main: bool = False       # git lists the MAIN worktree first


def session_worktree_label(run_id: str) -> str:
    """A stable label for a paused/WIP session's worktree (Stage 50 tie-in)."""
    return f"session-{run_id}"


def _default_git(args: List[str], cwd: Optional[str] = None) -> GitResult:
    """Shell out to `git` (the only place that touches a real git). Never raises — any
    failure (git absent, bad repo) becomes a non-zero GitResult so callers degrade clean."""
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=60)
        return GitResult(p.returncode, p.stdout, p.stderr)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:  # git missing / bad call
        return GitResult(127, "", str(exc))


# ------------------------------------------------------------------ WT-LIST — READ-ONLY queries
# FR-WT-1 needs three git FACTS that nothing here asked for before: which worktrees exist, what the
# repo's default branch is, and which branches are already merged into it. They live beside the
# manager because this is the module that owns talking to git about worktrees — they go through the
# SAME `_default_git` runner (injectable for tests), so there is still exactly one way mokata
# spawns git. Every one of them is READ-ONLY: no add, no remove, and deliberately NO `prune` (a
# read must never be the thing that mutates what it read — that is FR-WT-2/3, 0.0.17).

def list_worktrees(root: str,
                   git: Optional[Callable[..., GitResult]] = None) -> Optional[List[GitWorktree]]:
    """Every git worktree of `root`'s repo, parsed from `git worktree list --porcelain`, main
    checkout FIRST (git's own order). Returns None when git could not answer at all — not a repo,
    git absent, a broken repo — so the caller can say WHY rather than showing an empty list that
    looks like "no worktrees". Never raises."""
    runner = git or _default_git
    try:
        r = runner(["worktree", "list", "--porcelain"], cwd=root)
    except (OSError, subprocess.SubprocessError, ValueError):
        # D5 — the default runner converts these itself; an INJECTED runner (tests, a host-supplied
        # git) can raise them straight through. "Can't ask git" is reported as None, never as [] —
        # conflating the two is exactly the ambiguous empty state FR-WT-1 exists to remove.
        return None
    if not getattr(r, "ok", False):
        return None
    return _parse_worktree_porcelain(getattr(r, "stdout", "") or "")


def _parse_worktree_porcelain(text: str) -> List[GitWorktree]:
    """The `--porcelain` grammar: stanzas separated by a blank line, each opening with `worktree
    <path>` and carrying `HEAD <sha>` / `branch <ref>` / the bare `bare`, `detached`, `locked`,
    `prunable` flags (`locked`/`prunable` may carry a trailing reason). Unknown keys are IGNORED,
    not an error — git adds them over time and a lister must not break on a newer git."""
    out: List[GitWorktree] = []
    cur: Optional[GitWorktree] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            cur = None                                  # stanza break
            continue
        key, _, rest = line.partition(" ")
        rest = rest.strip()
        if key == "worktree":
            cur = GitWorktree(path=rest, is_main=not out)
            out.append(cur)
            continue
        if cur is None:
            continue                                    # a stray line before any `worktree`
        if key == "HEAD":
            cur.head = rest
        elif key == "branch":
            cur.branch = rest[len("refs/heads/"):] if rest.startswith("refs/heads/") else rest
        elif key == "bare":
            cur.bare = True
        elif key == "detached":
            cur.detached = True
        elif key == "locked":
            cur.locked = True
        elif key == "prunable":
            cur.prunable = True
    return out


def default_branch(root: str,
                   git: Optional[Callable[..., GitResult]] = None) -> Optional[str]:
    """The repo's default branch, or None when it genuinely cannot be determined. Grounded, in
    confidence order: the remote's own HEAD (`refs/remotes/origin/HEAD`, which git records and is
    authoritative for a repo with a remote), then a LOCAL `main`, then a local `master` — each
    VERIFIED to exist, never assumed. A remoteless repo on a non-conventional branch therefore
    returns None, and the caller must not claim "merged" off a guess (P16)."""
    runner = git or _default_git

    def _ask(args: List[str]) -> Optional[str]:
        try:
            r = runner(args, cwd=root)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if not getattr(r, "ok", False):
            return None
        val = (getattr(r, "stdout", "") or "").strip()
        return val or None

    head = _ask(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"])
    if head:
        return head.split("/", 1)[1] if head.startswith("origin/") else head
    for name in ("main", "master"):
        if _ask(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"]):
            return name
    return None


def merged_branches(root: str, base: str,
                    git: Optional[Callable[..., GitResult]] = None) -> Optional[set]:
    """The branches already merged into `base`, or None when git could not answer (so the caller
    reports "merged-check skipped" instead of asserting a branch is NOT merged). `--format` is used
    deliberately: plain `git branch --merged` prefixes a branch checked out in another worktree
    with `+`, which is EVERY branch this feature cares about."""
    if not base:
        return None
    runner = git or _default_git
    try:
        r = runner(["branch", "--merged", base, "--format=%(refname:short)"], cwd=root)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not getattr(r, "ok", False):
        return None
    return {ln.strip() for ln in (getattr(r, "stdout", "") or "").splitlines() if ln.strip()}


class WorktreeManager:
    """Create/remove throwaway git worktrees for isolated units. Opt-in + degrade-clean."""

    def __init__(self, root: str, ledger: Any = None,
                 git: Optional[Callable[..., GitResult]] = None,
                 enabled: bool = True) -> None:
        self.root = root
        self.ledger = ledger
        self._git = git or _default_git
        self.enabled = enabled

    def _log(self, kind: str, **fields: Any) -> None:
        if self.ledger is not None:
            try:
                self.ledger.record(kind, **fields)
            except OSError:
                # D5 — the ledger is an append to a local file; the only failure that isn't a bug
                # is the disk/permissions under `.mokata/` (OSError). An audit line is best-effort
                # and must never abort the worktree op it is describing.
                pass

    def available(self) -> bool:
        """True only when enabled AND `root` is inside a git work tree. Degrade-clean: any
        error (git absent, not a repo) ⇒ False ⇒ the caller runs in-place."""
        if not self.enabled:
            return False
        try:
            r = self._git(["rev-parse", "--is-inside-work-tree"], cwd=self.root)
        except (OSError, subprocess.SubprocessError, ValueError):
            # D5 — the classes a git runner genuinely raises: the default runner already converts
            # these to a non-zero GitResult, but an INJECTED runner (tests, a host-supplied git)
            # can raise them straight through. "Can't ask git" ⇒ not available ⇒ run in-place,
            # which is the SAFE direction here (no worktree is created).
            return False
        return r.ok and "true" in r.stdout.lower()

    def _wt_path(self, label: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", label) or "wt"
        return os.path.join(self.root, MOKATA_DIR, TEMP_LOCAL_DIRNAME,
                            WORKTREES_DIRNAME, safe)

    def create(self, label: str) -> Optional[Worktree]:
        """Add a detached worktree for `label`, or None when unavailable / git fails."""
        if not self.available():
            return None
        path = self._wt_path(label)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            r = self._git(["worktree", "add", "--detach", path], cwd=self.root)
        except (OSError, subprocess.SubprocessError) as exc:
            # D5 — the real raisers: the mkdir under `.mokata/temp_local/` (OSError) and an injected
            # git runner (SubprocessError). Failing to CREATE is the safe direction (the caller runs
            # in-place); it is already audited with the reason, so it was never silent.
            self._log("worktree_create", label=label, ok=False, reason=str(exc)[:160])
            return None
        if not r.ok:
            self._log("worktree_create", label=label, ok=False,
                      reason=(r.stderr or "git worktree add failed").strip()[:160])
            return None
        self._log("worktree_create", label=label, ok=True, path=path)
        return Worktree(label=label, path=path)

    def _changed_probe(self, wt: Worktree) -> Tuple[bool, bool]:
        """`(changed, probe_ok)` — the raw `git status --porcelain` probe behind `is_changed`.

        D5 — this probe now FAILS CLOSED, and that is the one deliberate behaviour change in the
        sweep. It used to read an unknown status as UNCHANGED: an exception returned False, and so
        did a NON-ZERO git (`r.ok and …` is False when git failed). Either way `remove(force=False)`
        then went on to DELETE a worktree that may have held uncommitted work, and logged
        `ok=True, changed=False` — a FALSE audit row asserting the tree was clean when nobody had
        ever managed to look. The docstring promised "so cleanup doesn't silently discard work" and
        the fallback direction did exactly that.

        An unknown status is therefore CHANGED. The worst case of the safe direction is an orphan
        worktree the user deletes by hand; the worst case of the old direction was destroyed work.
        `probe_ok` lets `remove` say WHICH it was in the audit row — "changed" and "we could not
        tell" are different facts and the ledger must not conflate them."""
        try:
            r = self._git(["status", "--porcelain"], cwd=wt.path)
        except (OSError, subprocess.SubprocessError):
            return True, False              # can't ask git ⇒ assume dirty (fail closed)
        if not r.ok:
            return True, False              # git said no ⇒ a failed probe is NOT "clean"
        return bool(r.stdout.strip()), True

    def is_changed(self, wt: Worktree) -> bool:
        """True when the worktree has uncommitted changes — or when we could not TELL (a failed
        `git status` probe reads as CHANGED, never as clean, so cleanup can't discard work)."""
        return self._changed_probe(wt)[0]

    def remove(self, wt: Worktree, force: bool = False) -> RemoveResult:
        """Remove a worktree. A clean (unchanged) one is removed; a CHANGED one — or one whose
        status probe FAILED, which we must not assume is clean — is kept unless `force` (throwaway
        task scratch), so work is never silently lost. Audited, with the reason."""
        changed, probe_ok = self._changed_probe(wt)
        if changed and not force:
            self._log("worktree_remove", label=wt.label, ok=False, changed=True,
                      reason=("changed — kept for review" if probe_ok
                              else "status probe failed — kept for review"))
            return RemoveResult(removed=False, changed=True)
        args = ["worktree", "remove"] + (["--force"] if (force or changed) else []) + [wt.path]
        try:
            r = self._git(args, cwd=self.root)
            self._git(["worktree", "prune"], cwd=self.root)   # never leave a stale ref
            removed = r.ok
        except (OSError, subprocess.SubprocessError):
            # D5 — the real raisers (an injected runner; the default one converts these itself).
            # `removed=False` is the honest, safe direction: the worktree is still there.
            removed = False
        self._log("worktree_remove", label=wt.label, ok=removed, changed=changed)
        return RemoveResult(removed=removed, changed=changed)

    @contextmanager
    def isolated(self, label: str, force_remove: bool = True) -> Iterator[Optional[Worktree]]:
        """Run an isolated unit in a throwaway worktree. Yields the Worktree, or None when
        worktrees are unavailable/disabled (the caller then runs IN-PLACE, exactly as today).
        Always cleans up on exit — no orphan worktree is ever left (force by default)."""
        wt = self.create(label)
        try:
            yield wt
        finally:
            if wt is not None:
                self.remove(wt, force=force_remove)
