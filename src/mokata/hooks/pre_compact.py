#!/usr/bin/env python3
"""mokata PreCompact ANCHOR hook (0.0.22 D1, H-1b) — STANDALONE SHIM.

The hook runtime lives in the installed package (``mokata.hook_cli``) and is launched as the
``mokata-hook pre-compact`` console entry point — no bare ``python3``.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python pre_compact.py`` invocation still work: it makes ``mokata`` importable from an
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
    """`pre-compact` through the same front door `mokata-hook` runs: spooled, drained off the clock."""
    return _front(["pre-compact", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
