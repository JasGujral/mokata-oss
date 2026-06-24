"""A3 — Tool-presence detection + graceful degradation (the detection half).

A `Detector` answers one question: *is this declared tool actually present on this
machine?* It never raises for an absent tool — absence is a normal, returned value.
That is what lets the router (A2) degrade cleanly instead of hard-failing.

Detection strategies (from a tool's `detect` block in the manifest):
  - command       : the named executable is on PATH            (shutil.which)
  - python_module : the named module can be imported           (find_spec)
  - path          : the named filesystem path exists           (~ expanded)
  - always        : conceptually always available              (pure fallbacks)

`overrides` lets callers (tests, dry-runs, `mokata init` previews) force a tool's
presence without touching the real environment.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Dict, Optional


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
        # Unknown strategy -> treat as absent (the manifest validator rejects these,
        # but detection must still be total and never throw).
        return False

    def detect_all(self, tools: Dict[str, Dict]) -> Dict[str, bool]:
        """Presence map for every tool in the manifest (one pass, cached)."""
        return {tid: self.is_present(tid, tdef) for tid, tdef in tools.items()}
