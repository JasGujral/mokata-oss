"""A3 — Tool-presence detection + graceful degradation (the detection half).

A `Detector` answers one question: *is this declared tool actually present on this
machine?* It never raises for an absent tool — absence is a normal, returned value.
That is what lets the router (A2) degrade cleanly instead of hard-failing.

Detection strategies (from a tool's `detect` block in the manifest):
  - command       : the named executable is on PATH            (shutil.which)
  - python_module : the named module can be imported           (find_spec)
  - path          : the named filesystem path exists           (~ expanded)
  - obsidian      : an Obsidian config dir or a configured vault exists (Stage 24A)
  - always        : conceptually always available              (pure fallbacks)

`overrides` lets callers (tests, dry-runs, `mokata init` previews) force a tool's
presence without touching the real environment.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Dict, Optional


def _obsidian_config_dirs() -> "list[str]":
    """The real per-OS locations Obsidian keeps its config under. The bare `~/.obsidian`
    the old detection checked usually doesn't exist (esp. macOS), so Obsidian was never
    detected (Stage 24A). Broadened to the actual macOS / Linux / Windows locations."""
    home = os.path.expanduser("~")
    dirs = [
        # macOS
        os.path.join(home, "Library", "Application Support", "obsidian"),
        # Linux (XDG) + Flatpak
        os.path.join(home, ".config", "obsidian"),
        os.path.join(home, ".var", "app", "md.obsidian.Obsidian", "config", "obsidian"),
        # legacy location some setups still use
        os.path.join(home, ".obsidian"),
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:  # Windows
        dirs.append(os.path.join(appdata, "obsidian"))
    return dirs


def _obsidian_present(tool_def: Dict) -> bool:
    """Obsidian is 'present' if a configured vault path exists, or any real Obsidian
    config dir does. Honors `config.vault` so pointing at an external vault counts."""
    config = (tool_def or {}).get("config") or {}
    vault = config.get("vault")
    if vault and os.path.isdir(os.path.expanduser(vault)):
        return True
    return any(os.path.isdir(d) for d in _obsidian_config_dirs())


class Detector:
    def __init__(
        self,
        overrides: Optional[Dict[str, bool]] = None,
        cache: bool = True,
    ) -> None:
        # overrides: tool_id -> forced present/absent (wins over real detection).
        self._overrides: Dict[str, bool] = dict(overrides or {})
        self._cache_enabled = cache
        self._cache: Dict[str, bool] = {}

    def is_present(self, tool_id: str, tool_def: Dict) -> bool:
        if tool_id in self._overrides:
            return self._overrides[tool_id]
        if self._cache_enabled and tool_id in self._cache:
            return self._cache[tool_id]

        result = self._detect(tool_def)

        if self._cache_enabled:
            self._cache[tool_id] = result
        return result

    @staticmethod
    def _detect(tool_def: Dict) -> bool:
        detect = (tool_def or {}).get("detect") or {}
        dtype = detect.get("type")
        name = detect.get("name")

        if dtype == "always":
            return True
        if dtype == "command":
            return bool(name) and shutil.which(name) is not None
        if dtype == "python_module":
            if not name:
                return False
            try:
                return importlib.util.find_spec(name) is not None
            except (ImportError, ValueError, ModuleNotFoundError):
                # A broken/namespace parent should read as "absent", not crash.
                return False
        if dtype == "path":
            return bool(name) and os.path.exists(os.path.expanduser(name))
        if dtype == "obsidian":
            return _obsidian_present(tool_def)
        # Unknown strategy -> treat as absent (the manifest validator rejects these,
        # but detection must still be total and never throw).
        return False

    def detect_all(self, tools: Dict[str, Dict]) -> Dict[str, bool]:
        """Presence map for every tool in the manifest (one pass, cached)."""
        return {tid: self.is_present(tid, tdef) for tid, tdef in tools.items()}
