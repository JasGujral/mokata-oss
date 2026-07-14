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
