"""Is mokata installed where it should be? (0.0.23 B4 — `INSTALL-ISOLATED-BY-DEFAULT`).

Two install shapes cause trouble that nothing else reports, so `mokata doctor` and
`mokata setup <harness>` name them. Both are SILENT when the install is healthy.

  1. **In the project's own environment.** ``sys.prefix`` sits inside the repository being worked
     on — `pip install mokata` ran in the project's venv. That mixes mokata's dependencies (mcp,
     starlette, uvicorn, ...) into the project's own, breaks the moment that venv is recreated, and
     binds the hooks `mokata setup claude` wires to that venv's absolute path. The remedy is an
     isolated install: `pipx install mokata`, `uv tool install mokata`, or Homebrew.

  2. **Shadowing.** The ``mokata`` that PATH resolves to is a different install, at a different
     version, from the one running this process — typically an activated venv holding an older
     mokata ahead of the tool install. Typed commands then run one version while the hooks and the
     MCP server run another.

Cheap by construction: the in-project check is path arithmetic; the shadowing check reads the
other install's ``*.dist-info/METADATA`` when it lives in a venv, and only otherwise runs
``<that mokata> --version`` with a 3-second bound. Any failure to tell is silence, never a guess.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from typing import Callable, List, Optional, Tuple

from . import __version__

#: The isolated install commands, in the order the docs give them.
ISOLATED_INSTALLS = ("pipx install mokata", "uv tool install mokata",
                     "brew install JasGujral/mokata/mokata")
#: Bound on asking a foreign `mokata` its version — a doctor line must not hang.
VERSION_TIMEOUT_SECONDS = 3

CODE_IN_PROJECT = "install-in-project-env"
CODE_SHADOWED = "install-shadowed"

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _is_within(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:            # different drives on Windows
        return False


def _is_mokata_source_checkout(root: str) -> bool:
    """A developer's `pip install -e .` inside mokata's OWN checkout is the contributor setup, not
    the mistake this check exists for."""
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    return re.search(r'(?m)^name\s*=\s*"mokata"\s*$', text) is not None


def in_project_env(root: str, prefix: Optional[str] = None) -> Optional[str]:
    """The warning text when this mokata runs from an environment inside ``root``; else None."""
    prefix = sys.prefix if prefix is None else prefix
    if not root or not prefix or not _is_within(prefix, root):
        return None
    if _is_mokata_source_checkout(root):
        return None
    return ("mokata is installed inside this repository's own environment (%s). That mixes "
            "mokata's dependencies (mcp, starlette, uvicorn, ...) into your project's, breaks "
            "when that environment is recreated, and binds the hooks `mokata setup claude` wires "
            "to its absolute path. Install mokata isolated instead — `%s`, `%s`, or `%s` — then "
            "`pip uninstall mokata` in this environment and re-run `mokata setup claude`."
            % ((prefix,) + ISOLATED_INSTALLS))


def _parse_version(text: str) -> Optional[str]:
    m = _VERSION_RE.search(text or "")
    return m.group(1) if m else None


def _version_tuple(v: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _metadata_version(prefix: str) -> Optional[str]:
    """Read mokata's version from a venv's site-packages without running anything."""
    patterns = (os.path.join(prefix, "lib", "python*", "site-packages", "mokata-*.dist-info"),
                os.path.join(prefix, "Lib", "site-packages", "mokata-*.dist-info"))
    for pattern in patterns:
        for info in sorted(glob.glob(pattern)):
            try:
                with open(os.path.join(info, "METADATA"), encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("Version:"):
                            return line.split(":", 1)[1].strip() or None
                        if not line.strip():
                            break
            except OSError:
                continue
    return None


def _ask_version(exe: str) -> Optional[str]:
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True,
                              timeout=VERSION_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return _parse_version((proc.stdout or "") + " " + (proc.stderr or ""))


def path_mokata_version(exe: str) -> Optional[str]:
    """The version of the `mokata` at ``exe``: from its venv's metadata when it lives in one (a
    `pyvenv.cfg` beside its bin dir), else by asking it, bounded. None when it cannot be told."""
    prefix = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
    if os.path.isfile(os.path.join(prefix, "pyvenv.cfg")):
        found = _metadata_version(prefix)
        if found:
            return found
    return _ask_version(exe)


def shadowed(which: Callable[[str], Optional[str]] = shutil.which,
             own_scripts: Optional[str] = None,
             own_version: str = __version__,
             version_of: Callable[[str], Optional[str]] = path_mokata_version,
             ) -> Optional[str]:
    """The warning text when PATH's `mokata` is a different install at a different version from
    this process; else None (also None when there is no `mokata` on PATH or its version is
    unknowable)."""
    exe = which("mokata")
    if not exe:
        return None
    own_scripts = sysconfig.get_path("scripts") if own_scripts is None else own_scripts
    if own_scripts and _norm(os.path.dirname(os.path.realpath(exe))) == _norm(own_scripts):
        return None                       # PATH resolves to this very install
    other = version_of(exe)
    if not other or other == own_version:
        return None
    try:
        rel = "older" if _version_tuple(other) < _version_tuple(own_version) else "newer"
    except ValueError:
        rel = "different"
    return ("`mokata` on PATH is %s (mokata %s), a %s install than the one running this command "
            "(mokata %s, %s). Typed commands then run %s while the hooks and the MCP server run "
            "%s. Usually an activated virtualenv holds the stray copy: `pip uninstall mokata` "
            "inside it (or `deactivate`), and keep one isolated install — `%s` or `%s`."
            % (exe, other, rel, own_version, sys.prefix, other, own_version,
               ISOLATED_INSTALLS[0], ISOLATED_INSTALLS[1]))


def warnings(root: str, **kw) -> List[Tuple[str, str]]:
    """``[(code, text), ...]`` — empty when the install is healthy. Never raises."""
    out: List[Tuple[str, str]] = []
    try:
        text = in_project_env(root, kw.get("prefix"))
        if text:
            out.append((CODE_IN_PROJECT, text))
    except Exception:                     # a diagnostic must never take its host down
        pass
    try:
        skw = {k: kw[k] for k in ("which", "own_scripts", "own_version", "version_of") if k in kw}
        text = shadowed(**skw)
        if text:
            out.append((CODE_SHADOWED, text))
    except Exception:
        pass
    return out
