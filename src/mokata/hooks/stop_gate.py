#!/usr/bin/env python3
"""mokata Stop EVIDENCE gate hook (0.0.26 R1) — STANDALONE SHIM.

The hook runtime lives in the installed package (``mokata.hook_cli``) and is launched as the
``mokata-hook stop-gate`` console entry point — no bare ``python3``.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python stop_gate.py`` invocation still work: it makes ``mokata`` importable from an
adjacent source checkout, then delegates to the single source of truth.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location: <root>/src
# (the parent of the `mokata` package) is two directories up from here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mokata.hook_cli import stop_gate_main as main  # noqa: E402  (re-exported as `main`)


if __name__ == "__main__":
    raise SystemExit(main())
