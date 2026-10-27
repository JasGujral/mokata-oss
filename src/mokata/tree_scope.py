"""P41 MCP-FOLLOWS-WORKTREE — which working tree a code question is about.

THE GAP (found by P31)
----------------------
Every Claude Code session now works in its own git worktree (`.claude/worktrees/<name>/`, P31). The
hooks follow it: their `cwd` is the worktree. The mokata MCP server does not: Claude Code starts it
once, in the main checkout, and `EnterWorktree` never moves it. So `query defs/callers/blast_radius`,
`decompose`, the spec impact lenses and every other code answer read the MAIN checkout's files — the
code the session is NOT editing. A symbol the session just added "does not exist"; a call it just
added has no caller. Wrong answers, for every worktree session.

THE BRIDGE
----------
The MCP process cannot see Claude Code's session id (harness gap #25642). Two facts already exist:

  * `session_ancestry.session_for_process` — which session THIS process serves (the hooks record
    their ancestor pids; the MCP walks its own; three guards make a wrong match answer None);
  * `session_worktrees.binding` — which worktree that session works in (P31 writes it from the hook
    `cwd` on every gated tool call).

`scope_for(path)` joins them, per tool call, into ONE `TreeScope`: the directory whose files answer.

THE RULES (fail-closed, never a guess)
--------------------------------------
  * `path` itself inside a linked worktree       -> that worktree (`worktree`).
  * the session resolves and is bound to a live worktree -> that worktree (`session-worktree`).
  * the session resolves, no binding              -> the main checkout, nothing to say (`main`).
    (A worktree with no binding yet has had no gated write, so it is still HEAD = the main
    checkout's code: the main answer is the right one.)
  * the session resolves, its worktree is gone     -> the main checkout, SAID (`worktree-gone`).
  * the session does NOT resolve and session worktrees exist -> the main checkout, SAID
    (`ambiguous`). Another session's worktree is never picked: "the only one" can still be someone
    else's.
  * no session and no worktrees                    -> the main checkout, nothing to say.

`serving(scope)` makes a scope current for the duration of one MCP tool body (a thread-local: the
MCP runs each tool body in its own worker thread). `code_tree_for(root)` is what the knowledge layer
asks: "is this question about a worktree, and whose main checkout holds the shared graph?".

Shared state stays in the main checkout's `.mokata/` (P31). Nothing here writes anything.
Stdlib-only, never raises. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

from .repo_identity import (canonical_repo_root, is_initialized, state_root, worktree_label,
                            worktree_toplevel)

BASIS_MAIN = "main"
BASIS_WORKTREE = "worktree"                  # the path asked about is itself in a worktree
BASIS_SESSION = "session-worktree"           # the session this MCP serves is bound to one
BASIS_GONE = "worktree-gone"                 # its bound worktree no longer exists
BASIS_AMBIGUOUS = "ambiguous"                # the session is unknown and worktrees exist

#: The bases whose answer is about a linked worktree's files.
WORKTREE_BASES = (BASIS_WORKTREE, BASIS_SESSION)

AMBIGUOUS_NOTE = ("answered for the MAIN checkout — this MCP server could not tell which Claude Code "
                  "session it serves (no process-ancestry match), and {n} session worktree(s) "
                  "exist, so it will not guess one; ask again with `path` set to your worktree's "
                  "path to get an answer about its files")
GONE_NOTE = ("answered for the MAIN checkout — this session's worktree {path} no longer exists "
             "(merged or removed)")


@dataclass(frozen=True)
class TreeScope:
    """The tree one code question is answered for."""

    tree: str                      # whose files answer
    main: str                      # the main checkout (the shared graph + `.mokata/`)
    basis: str = BASIS_MAIN
    session: Optional[str] = None
    note: str = ""

    @property
    def is_worktree(self) -> bool:
        return self.basis in WORKTREE_BASES

    @property
    def label(self) -> str:
        return worktree_label(self.tree) if self.is_worktree else "main"

    @property
    def reportable(self) -> bool:
        """Whether an answer must carry this scope: a worktree answer, or a main answer with a
        reason (ambiguous / gone). A plain main-checkout answer stays byte-identical."""
        return self.is_worktree or bool(self.note)

    def to_dict(self) -> Dict[str, Any]:
        return {"tree": self.tree, "label": self.label, "basis": self.basis,
                "session": self.session, "note": self.note}


def _main_of(path: str) -> str:
    return canonical_repo_root(path)


def _live_worktree(path: str) -> bool:
    return bool(path) and os.path.isdir(path) and worktree_toplevel(path) is not None


def scope_for(path: str = ".", *, lookup: Any = None, pid: Optional[int] = None) -> TreeScope:
    """The tree a code question asked with `path` from THIS process is about (rules: module doc).
    `lookup`/`pid` are the process-ancestry seams (tests). Never raises: any fault answers the main
    checkout of `path`, with no claim about a worktree."""
    ap = os.path.abspath(path or ".")
    try:
        wt = worktree_toplevel(ap)
        if wt is not None:
            if code_tree_for(wt) is not None:
                return TreeScope(tree=wt, main=_main_of(wt), basis=BASIS_WORKTREE)
            return TreeScope(tree=ap, main=ap)       # a worktree holding its own mokata state
        from . import session_worktrees as SW
        from .session_ancestry import session_for_process
        root = state_root(ap)
        sid = session_for_process(root, lookup=lookup, pid=pid)
        if sid:
            b = SW.binding(root, sid)
            if not b:
                return TreeScope(tree=ap, main=ap, session=sid)
            bp = str(b.get("path") or "")
            if _live_worktree(bp) and os.path.realpath(_main_of(bp)) == os.path.realpath(_main_of(ap)):
                return TreeScope(tree=worktree_toplevel(bp) or bp, main=_main_of(ap),
                                 basis=BASIS_SESSION, session=sid)
            return TreeScope(tree=ap, main=ap, basis=BASIS_GONE, session=sid,
                             note=GONE_NOTE.format(path=bp))
        live = [b for b in SW.bindings(root).values() if _live_worktree(str(b.get("path") or ""))]
        if live:
            return TreeScope(tree=ap, main=ap, basis=BASIS_AMBIGUOUS,
                             note=AMBIGUOUS_NOTE.format(n=len(live)))
    except Exception:  # noqa: BLE001 — no answer about a worktree is the strict answer
        pass
    return TreeScope(tree=ap, main=ap)


# ---------------------------------------------------------------------------- the serving scope
_SERVING = threading.local()


@contextmanager
def serving(scope: Optional[TreeScope]) -> Iterator[Optional[TreeScope]]:
    """Make `scope` the current one for this thread (one MCP tool body)."""
    prev = getattr(_SERVING, "scope", None)
    _SERVING.scope = scope
    try:
        yield scope
    finally:
        _SERVING.scope = prev


def current() -> Optional[TreeScope]:
    return getattr(_SERVING, "scope", None)


def code_tree_for(root: str) -> Optional[Tuple[str, str]]:
    """`(worktree, main checkout)` when a code question about `root` must be answered from a
    linked worktree's files, else None. `root` in a worktree answers for it; `root` being the main
    checkout answers for the serving scope's worktree (the session this MCP call serves)."""
    try:
        ap = os.path.abspath(root or ".")
        wt = worktree_toplevel(ap)
        if wt is not None:
            main = _main_of(wt)
            # Only when the worktree SHARES an initialized main checkout's mokata state (P31): a
            # worktree whose main checkout is not a mokata repo (it may hold its own `.mokata/`)
            # is a repo in its own right and is answered as one, exactly as before.
            if os.path.realpath(main) != os.path.realpath(wt) and is_initialized(main) and \
                    os.path.realpath(state_root(wt)) == os.path.realpath(main):
                return wt, main
            return None
        sc = current()
        if sc is not None and sc.is_worktree and \
                os.path.realpath(sc.main) == os.path.realpath(_main_of(ap)):
            return sc.tree, sc.main
    except (OSError, ValueError):
        return None
    return None


def code_root(root: str) -> str:
    """The directory whose FILES a code read about `root` should open (the worktree, or `root`)."""
    hit = code_tree_for(root)
    return hit[0] if hit else root


__all__ = ["TreeScope", "scope_for", "serving", "current", "code_tree_for", "code_root",
           "BASIS_MAIN", "BASIS_WORKTREE", "BASIS_SESSION", "BASIS_GONE", "BASIS_AMBIGUOUS"]
