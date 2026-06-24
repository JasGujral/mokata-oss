"""Governed pipeline-state store.

The manifest + constitution are mokata's *config* (committed, hand-edited). Pipeline
runs also produce durable *state* — the first being the brainstorm phase's approved
approach (D6/D7). That state lives under `.mokata/state/` as plain, indented JSON so it
is reviewable and committable alongside the rest of the config (K7 spirit), and is read
back by downstream phases (e.g. the completeness gate).

`StateStore` is a tiny, dependency-free read/write surface. It does not gate writes
itself — the human gate sits upstream (an approach is only persisted *after* explicit
approval); this is the mechanism that approval drives.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


class StateStore:
    def __init__(self, root: str) -> None:
        # root is the directory state files live in (e.g. <repo>/.mokata/state).
        self.root = root

    def path(self, name: str) -> str:
        return os.path.join(self.root, f"{name}.json")

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    def write(self, name: str, data: Dict[str, Any]) -> str:
        """Write `data` as indented JSON; create the dir if needed. Returns the path."""
        os.makedirs(self.root, exist_ok=True)
        path = self.path(name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=False) + "\n")
        return path

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the parsed JSON, or None if the artifact does not exist."""
        path = self.path(name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def delete(self, name: str) -> bool:
        """Remove the artifact. Returns True if it existed, False otherwise."""
        path = self.path(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
