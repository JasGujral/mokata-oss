"""Which Python runs the PROJECT's own tests — resolved explicitly, never inherited (0.0.23 B2).

`VENV-TEST-INTERPRETER`. mokata runs the project's test command (`baseline`) with
`subprocess.run(shell=True)`, which used to inherit mokata's OWN process environment. From a CLI in
an activated venv that worked by accident. From the MCP server, a hook or the IDE the environment is
whatever Claude Code was launched with — the desktop app, or `claude` started before activating — so
`pytest` was not found, or ran on an interpreter without the project's dependencies, and the
baseline reported a false RED.

So the project's interpreter is RESOLVED, in this order, and the result is named in every report:

  1. `settings.python` in the manifest — an env directory or an interpreter path (the override);
  2. `$VIRTUAL_ENV` / `$CONDA_PREFIX` — the user activated one: honour it (Jas 2026-10-15: *"if the
     user is inside the virtual env, we still want this to run"*);
  3. an environment found IN THE REPO ROOT by marker (`repo_walk.ENV_MARKERS`) — `.venv` first,
     then `venv`, `env`, then any other name, sorted;
  4. a tool-managed env outside the repo — `poetry env info -p`, `pipenv --venv` — only when the
     tool is on PATH, bounded;
  5. none — run unchanged, and SAY so ("system Python — no project env found").

Then the command runs with that env's `bin/` (`Scripts\\` on Windows) first on PATH and
`VIRTUAL_ENV` set: the effect of `activate`, without depending on anyone's shell. mokata's own code
keeps running from its own install; only the project's subprocesses use the project env.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .repo_walk import is_python_env

#: `settings.python` — the explicit override (an env dir, or an interpreter inside one).
SETTINGS_KEY = "python"
#: In-repo names tried first, in this order; any other marker-carrying top-level dir follows, sorted.
PREFERRED_NAMES = (".venv", "venv", "env")
#: Bound on asking poetry/pipenv where their env lives — a doctor line must not hang on a tool.
TOOL_TIMEOUT_SECONDS = 10

SYSTEM_LINE = "system Python — no project env found"


@dataclass(frozen=True)
class ProjectEnv:
    path: str          # the environment's root directory
    how: str           # how it was found — shown to the user verbatim
    conda: bool = False

    @property
    def bin_dirs(self):
        if os.name == "nt":
            if self.conda:
                return [self.path, os.path.join(self.path, "Scripts"),
                        os.path.join(self.path, "Library", "bin")]
            return [os.path.join(self.path, "Scripts")]
        return [os.path.join(self.path, "bin")]

    def line(self) -> str:
        return f"{self.path} (found via {self.how})"


def settings_python(manifest: Any) -> Optional[str]:
    """`settings.python` from a manifest, or None. Tolerates a missing/odd manifest."""
    if manifest is None:
        return None
    try:
        value = manifest.setting(SETTINGS_KEY, None)
    except AttributeError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _env_root_of(path: str) -> Optional[str]:
    """An env dir as given, or the env that holds an interpreter path (`…/bin/python`)."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
        return path
    if os.path.isfile(path):
        up = os.path.dirname(os.path.dirname(path))          # …/env/bin/python -> …/env
        if is_python_env(up):
            return up
        return os.path.dirname(path)                        # a conda python.exe sits at the root
    return None


def _is_conda(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "conda-meta"))


def _in_repo(root: str) -> Optional[ProjectEnv]:
    try:
        names = os.listdir(root)
    except OSError:
        return None
    ordered = [n for n in PREFERRED_NAMES if n in names]
    ordered += sorted(n for n in names if n not in PREFERRED_NAMES)
    for name in ordered:
        full = os.path.join(root, name)
        if os.path.isdir(full) and is_python_env(full):
            return ProjectEnv(full, f"{name}/ in the repo", conda=_is_conda(full))
    return None


def _ask_tool(argv, root: str) -> Optional[str]:
    if not shutil.which(argv[0]):
        return None
    try:
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=TOOL_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not out:
        return None
    candidate = out[-1].strip()
    return candidate if os.path.isdir(candidate) else None


def resolve(root: str, settings_value: Optional[str] = None,
            environ: Optional[Mapping[str, str]] = None, *, ask_tools: bool = True
            ) -> Optional[ProjectEnv]:
    """The project's Python environment, or None (then the caller says `SYSTEM_LINE`)."""
    environ = os.environ if environ is None else environ
    root = os.path.abspath(root or ".")
    if settings_value:
        # normpath: a relative `tools/py` from settings is joined onto a Windows root as
        # `...\\tools/py` — normalise so the path named in reports is the platform's own spelling.
        given = os.path.expanduser(settings_value)
        found = _env_root_of(os.path.normpath(given if os.path.isabs(given)
                                              else os.path.join(root, given)))
        if found:
            return ProjectEnv(found, "settings.python", conda=_is_conda(found))
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        value = environ.get(var)
        if value and os.path.isdir(value):
            return ProjectEnv(os.path.abspath(value), f"${var}", conda=var == "CONDA_PREFIX")
    found = _in_repo(root)
    if found:
        return found
    if ask_tools and (os.path.exists(os.path.join(root, "pyproject.toml"))
                      or os.path.exists(os.path.join(root, "Pipfile"))):
        for argv, how in ((["poetry", "env", "info", "-p"], "poetry"), (["pipenv", "--venv"], "pipenv")):
            path = _ask_tool(argv, root)
            if path:
                return ProjectEnv(path, how, conda=_is_conda(path))
    return None


def activated(environ: Mapping[str, str], env: Optional[ProjectEnv]) -> dict:
    """A copy of `environ` with `env` activated the way `activate` would; unchanged when None."""
    out = dict(environ)
    if env is None:
        return out
    path_var = next((k for k in out if k.upper() == "PATH"), "PATH")
    existing = out.get(path_var, "")
    out[path_var] = os.pathsep.join(env.bin_dirs + ([existing] if existing else []))
    if env.conda:
        out["CONDA_PREFIX"] = env.path
    else:
        out["VIRTUAL_ENV"] = env.path
    out.pop("PYTHONHOME", None)
    return out


def describe(env: Optional[ProjectEnv]) -> str:
    return env.line() if env else SYSTEM_LINE


__all__ = ["ProjectEnv", "SETTINGS_KEY", "SYSTEM_LINE", "activated", "describe", "resolve",
           "settings_python"]
