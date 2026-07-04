#!/usr/bin/env python3
"""SessionStart hook — mokata's sub-2k-token bootstrap (A4) — STANDALONE SHIM.

The hook runtime now lives in the installed package (``mokata.hook_cli``) and is launched
as the ``mokata-hook session-start`` console entry point (Stage 53b) — no bare ``python3``.
The plugin ``hooks.json`` forwards ``${CLAUDE_PLUGIN_ROOT}`` and ``mokata setup`` forwards
the clone root, so the engine can be located for the ``/mokata:init`` command.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python session_start.py`` invocation still work. Stage 3 moved this file INTO the package
(``<root>/src/mokata/hooks/session_start.py``), so run standalone the plugin root is this
file's three-parents-up clone root (``<root>``, which holds ``src/mokata``) — honouring an
explicit ``CLAUDE_PLUGIN_ROOT`` when one is passed (the marketplace/plugin case).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location. This file
# now lives at <root>/src/mokata/hooks/, so <root>/src (the parent of the `mokata` package)
# is two directories up from here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mokata.hook_cli import session_start_main  # noqa: E402


def main() -> int:
    # Standalone fallback: the clone root is three parents up from this file's directory
    # (<root>/src/mokata/hooks -> <root>). It holds src/mokata, honouring the plugin-root
    # cache contract (version.py resolves <root>/src/mokata). The marketplace plugin sets
    # CLAUDE_PLUGIN_ROOT explicitly, which wins.
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return session_start_main(["--plugin-root", root])


if __name__ == "__main__":
    sys.exit(main())
