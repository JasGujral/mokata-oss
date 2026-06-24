# mokata — framework spine.
#
# Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
#
# The spine is the conductor every other layer plugs into:
#   - A1 stack manifest (schema + file)          -> manifest.py, schema.py
#   - A2 capability router (need -> tool + fallback) -> router.py
#   - A3 tool-presence detection + degradation    -> detect.py
#   - A4 SessionStart bootstrap (<= 2k tokens)     -> bootstrap.py
#   - A5 unified config + constitution surface     -> config.py
#   - A7 `mokata init` onboarding                  -> init.py, profiles.py, cli.py

__version__ = "1.2.3"

# The directory, relative to a repo root, that holds mokata's committed config.
MOKATA_DIR = ".mokata"
MANIFEST_FILENAME = "manifest.json"
CONSTITUTION_FILENAME = "constitution.md"
