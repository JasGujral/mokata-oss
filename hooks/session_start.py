#!/usr/bin/env python3
"""SessionStart hook — mokata's sub-2k-token bootstrap (A4) — STANDALONE SHIM.

The hook runtime now lives in the installed package (``mokata.hook_cli``) and is launched
as the ``mokata-hook session-start`` console entry point (Stage 53b) — no bare ``python3``.
The plugin ``hooks.json`` forwards ``${CLAUDE_PLUGIN_ROOT}`` and ``mokata setup`` forwards
the clone root, so the engine can be located for the ``/mokata:init`` command.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python session_start.py`` invocation still work. Run standalone, the plugin root is this
file's grandparent (``<root>/hooks/session_start.py``) — honouring an explicit
``CLAUDE_PLUGIN_ROOT`` when one is passed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mokata.hook_cli import session_start_main  # noqa: E402


def main() -> int:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return session_start_main(["--plugin-root", root])


if __name__ == "__main__":
    sys.exit(main())
