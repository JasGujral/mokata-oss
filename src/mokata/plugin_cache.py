"""Plugin-root cache (Stage 23) — a transparent, documented, user-level cache.

`${CLAUDE_PLUGIN_ROOT}` is NOT substituted inside a slash-command markdown body (only in
hooks/MCP configs), so the `/mokata:init` command can't hard-code where the bundled engine
lives. The SessionStart hook DOES know its own location, so it records the plugin root to
`~/.mokata/plugin-root`; the command reads that to find `<root>/src/mokata` and run the
engine pip-free.

This is a **local cache only** — no network, no project artifact, and it holds no
code/memory/config (so P2's human-gate is N/A). It is documented here so "nothing silent"
still reads true: it is disclosed, overwritten cheaply each session, and safe to delete.
"""

from __future__ import annotations

import os
from typing import Optional

from . import MOKATA_DIR

PLUGIN_ROOT_FILENAME = "plugin-root"


def _cache_path(home: Optional[str] = None) -> str:
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, MOKATA_DIR, PLUGIN_ROOT_FILENAME)


def record_plugin_root(plugin_root: str, home: Optional[str] = None) -> Optional[str]:
    """Idempotently write the absolute plugin root to ~/.mokata/plugin-root. Returns the
    cache path, or None on any failure — it must NEVER raise (the SessionStart hook is
    async/observability and can't hard-fail)."""
    try:
        path = _cache_path(home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(os.path.abspath(plugin_root) + "\n")
        return path
    except Exception:
        return None


def read_plugin_root(home: Optional[str] = None) -> Optional[str]:
    """Return the cached plugin root, or None if absent/unreadable/empty."""
    try:
        path = _cache_path(home)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        return value or None
    except Exception:
        return None
