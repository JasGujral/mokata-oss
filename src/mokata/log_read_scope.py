"""The SessionStart hook's one-parse scope for the progress log (0.0.28 F round 3).

A module of its own, holding nothing but the flag, so the hook can open the scope before the
briefing without importing `progress_events` (and its dependencies) on a path that may never read
the log: that import alone was a measured 3-4 ms of the O12b start-up budget. `progress_events`
reads `SHARED`; see `progress_events._walk` and `_parse_rows` for what the cache is and why it is sound.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import gc
from typing import Any, Dict, Optional

#: path -> cache entry while a scope is open; None outside every scope
SHARED: Optional[Dict[str, Dict[str, Any]]] = None


class shared_backward_reads:
    """Context manager: while open, backward reads of the progress log share one parse."""

    def __enter__(self):
        global SHARED
        self._prev, SHARED = SHARED, {}
        # The cache builds tens of thousands of events that live until the scope closes; the
        # cyclic GC re-walking that growing heap mid-build is pure cost (none of it is cyclic
        # garbage). Paused for the scope, and only if it was running; the hook exits right after.
        self._gc = gc.isenabled()
        if self._gc:
            gc.disable()
        return self

    def __exit__(self, *exc):
        global SHARED
        SHARED = self._prev
        if self._gc:
            gc.enable()
        return False
