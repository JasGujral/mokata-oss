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

__version__ = "0.0.15"

# The directory, relative to a repo root, that holds mokata's committed config.
MOKATA_DIR = ".mokata"
MANIFEST_FILENAME = "manifest.json"
CONSTITUTION_FILENAME = "constitution.md"

# Everything mokata creates as its own data lives under MOKATA_DIR. Inside it there is a
# committed/transient split (Stage 24D): committed config (manifest, constitution, an
# exported stack if the team commits it) sits at the .mokata/ root; everything
# transient/runtime (pipeline state, resume checkpoints, the freshness index, caches, the
# SQLite memory store + vault, and — by default — the audit ledger) lives under
# .mokata/temp_local/, which a committed .mokata/.gitignore keeps out of version control.
TEMP_LOCAL_DIRNAME = "temp_local"


def package_data_root():
    """The directory that holds mokata's packaged data — ``templates/``, ``hooks/``,
    ``skills/`` (and ``stacks/``).

    Stage 3 relocated that data INSIDE the installed package (``src/mokata/…``), so it
    resolves the SAME way for a pip-installed wheel and for an editable/clone install:
    ``importlib.resources.files("mokata")`` is the package directory in both cases (site-
    packages for a wheel, ``<clone>/src/mokata`` for ``-e .``). This removes the old
    ``parents[2]`` clone assumption that made ``pip install <wheel>`` unable to run
    ``mokata setup``. Falls back to this module's own directory if importlib.resources
    can't hand back a concrete filesystem path (never raises)."""
    from pathlib import Path
    try:
        from importlib.resources import files
        root = Path(str(files("mokata")))
        if root.is_dir():
            return root
    except Exception:
        # (iv) SUPPRESS-OK: `importlib.resources.files` is the OPTIMISTIC path, and its failure
        # classes are the packaging ecosystem's, not mokata's — a zipimport/frozen loader raises
        # something none of the stdlib docs pin down, and a custom finder in an embedder's app can
        # raise anything at all. The fallback below is not a degraded answer, it is the SAME answer
        # by a more direct route (this module's own directory IS the package directory), so nothing
        # is lost and there is nothing to announce. Narrowing here would only turn a working install
        # into an import-time crash on an exotic loader.
        pass
    return Path(__file__).resolve().parent


def _force_utf8_io() -> None:
    """On Windows, make stdout/stderr speak UTF-8 so mokata's console output — arrows (→),
    checkmarks (✓), box drawing, em-dashes — never dies with `UnicodeEncodeError` on the legacy
    cp1252 console (or a cp1252 pipe when output is captured). POSIX terminals are already UTF-8,
    so this is a no-op there, keeping behavior byte-identical across platforms. Fully guarded:
    it never raises and never blocks import (an embedder with an exotic stream is left alone)."""
    import os
    import sys

    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
            if reconfigure is not None and current != "utf8":
                reconfigure(encoding="utf-8")
        except Exception:
            # (iv) SUPPRESS-OK: this runs at IMPORT time, on whatever `sys.stdout` happens to be —
            # which an embedder may have replaced with a StringIO, a closed handle, or a custom
            # writer whose `reconfigure` raises anything. The failure class is therefore the
            # EMBEDDER's, not ours. Failing here changes nothing about correctness: mokata's console
            # glyphs may fall back to the legacy Windows encoding, which is a COSMETIC outcome, and
            # `ascii_only` renderings exist precisely for that case. An import-time raise, by
            # contrast, would make mokata unimportable — a far worse failure than a mangled arrow.
            pass


_force_utf8_io()
