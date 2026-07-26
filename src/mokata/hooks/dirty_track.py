#!/usr/bin/env python3
"""mokata async OBSERVABILITY hook (GR.S4) — graph dirty-track — STANDALONE SHIM.

The hook runtime lives in the installed package (``mokata.hook_cli``) and is launched as the
``mokata-hook dirty-track`` console entry point — no bare ``python3``.

This is the PostToolUse ASYNC OBSERVABILITY lane (doc 85): it records touched paths into the
session graph dirty-set so the read-time freshness contract can reconcile the code graph before
the next query answers. It NEVER blocks and always exits 0 — the sync PreToolUse security hooks
are untouched.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python dirty_track.py`` invocation still work: it makes ``mokata`` importable from an adjacent
source checkout, then delegates to the single source of truth.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location: <root>/src
# (the parent of the `mokata` package) is two directories up from here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mokata.hook_cli import dirty_track_main as main  # noqa: E402  (re-exported as `main`)


if __name__ == "__main__":
    raise SystemExit(main())
