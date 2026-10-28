#!/usr/bin/env python3
"""mokata Stop session-harvest hook (0.0.22 D6, H-1c) — STANDALONE SHIM.

The hook runtime lives in the installed package (``mokata.hook_cli``) and is launched as the
``mokata-hook stop`` console entry point — no bare ``python3``.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python stop.py`` invocation still work: it makes ``mokata`` importable from an
adjacent source checkout, then delegates to the single source of truth.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location: <root>/src
# (the parent of the `mokata` package) is two directories up from here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mokata_emit import main as _front  # noqa: E402  (the stdlib-only emitter, ruling U13)


def main(argv=None):
    """`stop` through the same front door `mokata-hook` runs: spooled, drained off the clock."""
    return _front(["stop", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
