#!/usr/bin/env python3
"""mokata sync SECURITY hook (G4 + I1) — secret guard — STANDALONE SHIM.

The hook runtime now lives in the installed package (``mokata.hook_cli``) and is launched
as the ``mokata-hook secret-guard`` console entry point (Stage 53b) — no bare ``python3``.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python secret_guard.py`` invocation still work: it makes ``mokata`` importable from an
adjacent source checkout, then delegates to the single source of truth.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mokata.hook_cli import secret_guard_main as main  # noqa: E402  (re-exported as `main`)


if __name__ == "__main__":
    raise SystemExit(main())
