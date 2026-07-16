"""GR.S2 (i) — USER-scoped decline records for the graph adoption offer.

The setup/init OFFER to adopt code-review-graph is consented and NEVER nags: once the human
declines for a repo, mokata records that decision in the USER's own profile (not the committed
project manifest) and never asks again. USER-scoped by design — it is the human's standing
preference, travels with them across re-clones, and is not a durable PROJECT write (so it needs
no WriteGate; recording the human's explicit "no" is not a silent durable project change, P2).

Stored at `~/.mokata/graph_declines.json` (XDG-agnostic, mirrors B-VER/DB.S4 user-scope
records). `user_home` is injectable for tests.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

_DECLINES_FILE = "graph_declines.json"


def _user_dir(user_home: Optional[str] = None) -> str:
    base = user_home or os.path.expanduser("~")
    return os.path.join(base, ".mokata")


def _declines_path(user_home: Optional[str] = None) -> str:
    return os.path.join(_user_dir(user_home), _DECLINES_FILE)


def _repo_key(root: str) -> str:
    return os.path.abspath(root)


def _load(user_home: Optional[str] = None) -> Dict[str, bool]:
    path = _declines_path(user_home)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def graph_declined(root: str, user_home: Optional[str] = None) -> bool:
    """True when the human has declined graph adoption for THIS repo (so we never re-ask)."""
    return bool(_load(user_home).get(_repo_key(root)))


def record_graph_decline(root: str, user_home: Optional[str] = None) -> None:
    """Record the human's decline for this repo in their user profile (idempotent)."""
    d = _load(user_home)
    d[_repo_key(root)] = True
    udir = _user_dir(user_home)
    try:
        os.makedirs(udir, exist_ok=True)
        with open(_declines_path(user_home), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except OSError:
        # A user-pref write that can't land is non-fatal — worst case we ask once more later.
        pass
