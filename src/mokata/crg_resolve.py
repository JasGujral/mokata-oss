"""0.0.26 O15 `CRG-RESOLUTION-HERMETIC` — WHICH code-review-graph mokata runs, and why.

Measured on Jas's Mac at checkpoint 2: mokata was installed as a `uv tool` with `[full]`, so a
`code-review-graph` executable sat on PATH. A fresh core venv (`.[dev,ide]`, NO code-review-graph)
ran the unit suite, `detect.find_command` found the uv tool's copy through `shutil.which`, the
router used it unpinned and version-unchecked against repos it was never built for, the suite
printed `code-graph: DEGRADED … (the backend is unreachable)` naming no binary, and stalled.

THE RULE. code-review-graph is a Python package mokata is coupled to by version (it is mokata's
`[full]` extra), so the only copy mokata may run is the one installed in ITS OWN environment:

  * the executable must sit in this environment's scripts dir (`detect.env_scripts_dirs`), AND
  * the package's metadata must sit in this environment's site-packages (purelib/platlib), AND
  * that version must be inside the range the client is grounded against
    (`crg_client.check_version_string`). Out of range -> REFUSED, with the reason.

A copy found only on PATH outside this environment is never run; it is REPORTED (`doctor`,
`setup`) with the fix. No spawn happens here at all: the version comes from the installed
metadata, so resolution cannot hang.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import sysconfig
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Tuple

CRG_COMMAND = "code-review-graph"
CRG_DIST = "code-review-graph"

#: `MOKATA_CRG=off` (or `none` / `0`): code-review-graph is switched OFF — the code graph runs on
#: its floor and nothing is resolved or spawned. The unit suite's default (`tests/_support.py`).
OFF_VALUES = ("off", "none", "0")

#: An EXPLICIT choice of binary (a test's fake, a developer's checkout). Explicit is the point: it is
#: never discovered, only named. It is still version-checked — by a BOUNDED `--version` run.
OVERRIDE_ENV = "MOKATA_CRG"
VERSION_PROBE_TIMEOUT = 15.0
_PROBED: dict = {}

# Version-compat bounds (semver, inclusive lower / exclusive upper-major). CRG exposes no schema
# version, so the handshake is on its version string. The adopted-tool contract mokata maps was
# grounded against the 2.x line. Lives HERE (import-light) and is re-exported by `crg_client`.
CRG_MIN_VERSION: Tuple[int, ...] = (2, 0, 0)
CRG_MAX_MAJOR: int = 3            # refuse 3.x until the interface is re-grounded


def version_out_of_range(v: str) -> str:
    """'' when `v` is inside the grounded range, else the reason (one sentence)."""
    try:
        vt = tuple(int(p) for p in v.split(".")[:3])
    except (ValueError, AttributeError):
        vt = ()
    if not vt or vt < CRG_MIN_VERSION or vt[0] >= CRG_MAX_MAJOR:
        return (f"code-review-graph {v} is outside the supported range "
                f">={'.'.join(map(str, CRG_MIN_VERSION))},<{CRG_MAX_MAJOR}.0.0")
    return ""


#: The fix a user reads when mokata's environment has no usable code-review-graph.
INSTALL_HINT = ("install `mokata[full]` (or add code-review-graph to the environment mokata runs "
                "from: `uv tool install --reinstall 'mokata[full]'` / `pipx inject mokata "
                "code-review-graph`)")


@dataclass
class CrgResolution:
    """The answer to "which code-review-graph does mokata run?".

    `path` is set ONLY when mokata will run it (in-env, version in range). `refused` says why an
    in-env copy was not used. `outside` is a copy on PATH outside this environment — never run,
    only reported."""
    path: Optional[str] = None
    version: Optional[str] = None
    refused: str = ""
    outside: Optional[str] = None
    origin: str = ""                 # "env" | "MOKATA_CRG" — where the used/refused copy came from
    disabled: bool = False           # MOKATA_CRG=off — switched off on purpose

    @property
    def ok(self) -> bool:
        return self.path is not None

    def describe(self) -> str:
        """One line naming the binary and its state — what doctor/setup/the degrade notice say."""
        if self.ok:
            where = ("named by MOKATA_CRG" if self.origin == OVERRIDE_ENV
                     else "mokata's environment")
            return f"code-review-graph {self.version} at {self.path} ({where})"
        if self.disabled:
            return ("code-review-graph is switched off (MOKATA_CRG=off); the code graph runs on "
                    "its floor.")
        parts = []
        if self.refused:
            parts.append(self.refused)
        else:
            parts.append("code-review-graph is not installed in the environment mokata runs from")
        if self.outside:
            parts.append(f"found code-review-graph on PATH outside mokata's environment "
                         f"({self.outside}) — it is NOT used")
        return "; ".join(parts) + f". Fix: {INSTALL_HINT}."


def _site_dirs() -> list:
    out = []
    for key in ("purelib", "platlib"):
        d = sysconfig.get_path(key)
        if d and d not in out:
            out.append(d)
    return out


def env_dist_version(dist: str = CRG_DIST, site_dirs: Optional[Iterable[str]] = None
                     ) -> Optional[str]:
    """The version of `dist` installed in THIS environment's site-packages, or None. Reads the
    `<name>-<version>.dist-info` directory name only — no import, no spawn, no metadata walk (that
    cost ~40 ms and resolution runs on hook paths), and PYTHONPATH is not consulted."""
    want = dist.replace("-", "_").lower() + "-"
    for d in (site_dirs if site_dirs is not None else _site_dirs()):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if low.startswith(want) and low.endswith(".dist-info"):
                return name[len(want):-len(".dist-info")]
    return None


def probe_version(path: str, timeout: float = VERSION_PROBE_TIMEOUT) -> Optional[str]:
    """`<path> --version`, BOUNDED (stdin closed, timeout), remembered per (path, mtime). Only the
    explicitly named override is probed this way; the in-env copy is read from its metadata."""
    import subprocess
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    if key in _PROBED:
        return _PROBED[key]
    from .quiet_spawn import hidden
    from .knowledge.crg_client import parse_crg_version
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL, **hidden())
        version = parse_crg_version(proc.stdout) or parse_crg_version(proc.stderr)
    except (OSError, subprocess.SubprocessError):
        version = None
    _PROBED[key] = version
    return version


def _checked(res: "CrgResolution", path: str, version: Optional[str]) -> "CrgResolution":
    if not version:
        res.refused = (f"code-review-graph at {path} did not report a version mokata can check "
                       f"— refused")
        return res
    why = version_out_of_range(version)
    if why:
        res.refused = f"{why} (at {path}) — refused"
        res.version = version
        return res
    res.path, res.version = path, version
    return res


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def resolve_crg(*, scripts_dirs: Optional[Iterable[str]] = None,
                version_of: Optional[Callable[[], Optional[str]]] = None,
                which: Callable[..., Optional[str]] = shutil.which) -> CrgResolution:
    """Resolve the code-review-graph mokata may run. Never raises; spawns only a bounded
    `--version` of an explicitly named `MOKATA_CRG` binary."""
    from .detect import env_scripts_dirs
    override = os.environ.get(OVERRIDE_ENV, "").strip()
    if override.lower() in OFF_VALUES:
        return CrgResolution(origin=OVERRIDE_ENV, disabled=True)
    if override:
        res = CrgResolution(origin=OVERRIDE_ENV)
        hit = which(override) if not os.path.isabs(override) else (
            override if os.path.isfile(override) and os.access(override, os.X_OK) else None)
        if hit is None:
            res.refused = f"MOKATA_CRG names {override}, which is not an executable — refused"
            return res
        return _checked(res, hit, probe_version(hit))
    dirs = list(scripts_dirs if scripts_dirs is not None else env_scripts_dirs())
    res = CrgResolution(origin="env")
    in_env = None
    for d in dirs:
        hit = which(CRG_COMMAND, path=d)
        if hit:
            in_env = hit
            break
    on_path = which(CRG_COMMAND)
    if on_path and (in_env is None or _norm(on_path) != _norm(in_env)) and \
            os.path.dirname(_norm(on_path)) not in {_norm(d) for d in dirs}:
        res.outside = on_path
    if in_env is None:
        return res
    version = (version_of or env_dist_version)()
    if not version:
        res.refused = (f"code-review-graph at {in_env} has no installed package in mokata's "
                       f"environment, so its version cannot be checked — refused")
        return res
    return _checked(res, in_env, version)
