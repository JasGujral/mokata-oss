#!/usr/bin/env python3
"""mokata async CONTEXT-INJECTION hook (H-1a) — per-turn recall injection — STANDALONE SHIM.

The hook runtime lives in the installed package (``mokata.hook_cli``) and is launched as the
``mokata-hook user-prompt-submit`` console entry point — no bare ``python3``.

This is the UserPromptSubmit ASYNC CONTEXT-INJECTION lane: on every prompt the human submits it
returns a small ``additionalContext`` pack of the project's rules plus the memory relevant to THIS
turn, so memory is per-turn inbuilt RAG rather than a once-per-session briefing. It only ADDS
context and ALWAYS exits 0 — a non-zero exit on this event does not block a tool call, it eats the
human's turn, so every failure arm degrades to silence.

This thin shim is kept so the legacy ``launch.sh`` fallback and any direct
``python user_prompt_submit.py`` invocation still work: it makes ``mokata`` importable from an
adjacent source checkout, then delegates to the single source of truth.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

# Make the package importable whether run from the repo or an installed location: <root>/src
# (the parent of the `mokata` package) is two directories up from here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mokata.hook_cli import user_prompt_submit_main as main  # noqa: E402  (re-exported)


if __name__ == "__main__":
    raise SystemExit(main())
