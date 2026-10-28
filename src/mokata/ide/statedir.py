"""Where the IDE keeps its OWN state for a repo — and the one rule that decides it (0.0.26 P29,
`IDE-UNINITIALIZED-REPO`).

`mokata ide` must work on a stranger's repo: `pip install "mokata[ide]" && mokata ide`, beside
their own claude, before anyone ran `mokata init` (doc 113 §6 #6). Opening a folder is not consent
to write into it. So:

  * A repo mokata GOVERNS (it has a `.mokata/` of its own, or resolves to an initialized one — the
    main checkout of a worktree) keeps the IDE's scratch state where it always did:
    `<repo>/.mokata/temp_local/ide/` and `.../temp_local/state/` — gitignored run-state.
  * Any other folder gets NOTHING written into it. The IDE's scratch state (server record, launch
    lock, workspace tabs, semantic index, context items, the AST floor's parse cache) lives in the
    user's own profile, keyed by the repo's real path:
    `~/.mokata/ide-repos/<sha256(realpath)[:16]>/{ide,state,knowledge_ast}/`, with a `repo.txt`
    naming the folder it belongs to (so a human can tell what a directory is for, and delete it).

The decision is re-made on every call (two `os.path` tests), so after `mokata setup claude` the
next write lands in the repo's own `.mokata/temp_local/` — the place the hooks and the CLI read.

`GOVERNANCE` is what lights up after `mokata setup claude`; the health panel and every
not-initialized answer name it, so the page says exactly what setup buys rather than "run init".

Stdlib-only, no heavy imports (the CLI's bridge client reads the server record through here).
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional

SETUP_COMMAND = "mokata setup claude"
NOT_INITIALIZED = "not-initialized"
USER_DIRNAME = ".mokata"
REPOS_DIRNAME = "ide-repos"
REPO_NOTE = "repo.txt"

# (key, what lights up) — the governance features an uninitialised repo does not have yet.
GOVERNANCE = (
    ("runs", "Runs: every approved plan and spec version, per run"),
    ("lifecycle", "Lifecycle: brainstorm → spec → test → build → review, with the gate each "
                  "step passed"),
    ("sessions", "Sessions: what each Claude Code session is writing, and which one waits on you"),
    ("snapshots", "Snapshots: a before/after of every file a session writes — time-lapse, compare"),
    ("approvals", "Approvals: the human gate on durable writes, answered from this page"),
    ("doctor", "Health: `mokata doctor`'s checks and the Claude Code wiring"),
)


def initialized(root: str) -> bool:
    """Would `Surface.load(root)` find a manifest? (Same resolver: a worktree of an initialized
    repo is initialized.) Never raises."""
    from .. import MANIFEST_FILENAME, MOKATA_DIR
    if os.path.exists(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)):
        return True
    from ..repo_identity import resolve_mokata_root          # never raises (its contract)
    return resolve_mokata_root(root).root is not None


def governed(root: str) -> bool:
    """May the IDE keep scratch state inside this repo? Only when mokata is already there."""
    from .. import MOKATA_DIR
    return os.path.isdir(os.path.join(root, MOKATA_DIR)) or initialized(root)


def user_home() -> str:
    return os.path.expanduser("~")


def repo_key(root: str) -> str:
    real = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def user_repo_dir(root: str) -> str:
    """The user-level home of THIS repo's IDE state (nothing is created here by asking)."""
    return os.path.join(user_home(), USER_DIRNAME, REPOS_DIRNAME, repo_key(root))


def _user_sub(root: str, name: str) -> str:
    return os.path.join(user_repo_dir(root), name)


def note_repo(root: str) -> None:
    """Write `repo.txt` (the folder this directory belongs to) once the directory exists."""
    base = user_repo_dir(root)
    note = os.path.join(base, REPO_NOTE)
    if os.path.isdir(base) and not os.path.exists(note):
        try:
            with open(note, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(os.path.realpath(os.path.abspath(root)) + "\n")
        except OSError:
            pass                              # a label, never a reason a write fails


def ide_dir(root: str) -> str:
    """The IDE's own records: `server.json`, `bridge.json`, `workspace.json`, `semantic/`."""
    if governed(root):
        from ..repo_identity import temp_local_dir          # P31: a worktree shares the main store
        return os.path.join(temp_local_dir(root), "ide")
    return _user_sub(root, "ide")


def state_dir(root: str) -> str:
    """The run-state store the IDE writes (context items). In a governed repo this IS
    `tdd_state.state_dir(root)` — the store the hooks read."""
    if governed(root):
        from ..tdd_state import state_dir as repo_state_dir
        return repo_state_dir(root)
    return _user_sub(root, "state")


def ast_cache_dir(root: str) -> Optional[str]:
    """The AST floor's parse cache: None = the backend's in-repo default (a governed repo); "" =
    memory only (0.0.26 P39: a repo that has not run setup gets nothing written for it — not in
    the repo, not in the user's profile — until the user consents)."""
    return None if governed(root) else ""


def lights_up() -> List[Dict[str, str]]:
    return [{"key": k, "what": w} for k, w in GOVERNANCE]


def not_initialized(what: str, sample: bool = False) -> Dict[str, Any]:
    """The 200 answer of a governance route on a repo mokata has not set up: a STATE, not an
    error — the page renders the one command and what it lights up."""
    return {"sample": sample, "state": NOT_INITIALIZED, "command": SETUP_COMMAND,
            "note": f"mokata is not set up in this repo — run `{SETUP_COMMAND}` to see {what} "
                    f"here", "lights_up": lights_up()}


__all__ = ["SETUP_COMMAND", "NOT_INITIALIZED", "GOVERNANCE", "initialized", "governed",
           "user_repo_dir", "repo_key", "note_repo", "ide_dir", "state_dir", "ast_cache_dir",
           "lights_up", "not_initialized"]
