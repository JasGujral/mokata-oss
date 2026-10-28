"""The minimum Claude Code version mokata needs (0.0.22 E1; CC-COMPAT 0.0.26), and the doctor check.

Every Claude Code surface mokata depends on — hook events and payload fields, settings keys, the
plugin manifest, skill frontmatter, the `claude` CLI flags it invokes or tells you to run — is listed
with the oldest version that provides it in `claude_code_compat.json` (the machine-readable manifest,
with a source per row). The floor is DERIVED from it: the newest `since` among the `core` surfaces.
`opt-in` surfaces (http hooks for `--ide-bridge`, `onFailure` for `--http-gates`) are checked only
when wired. README/docs state the floor and a test fails if they disagree with the manifest; a weekly
workflow (`cc-compat.yml`) runs the manifest's CLI checks and `claude plugin validate --strict`
against the latest Claude Code.

Below the floor nothing breaks loudly — the hook that would have fired simply does not — so the
doctor says WHICH surfaces are missing, by name, instead of letting them read as working.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

Version = Tuple[int, int, int]

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_code_compat.json")
SCOPES = ("core", "opt-in")


def _load_manifest(path: str = MANIFEST_PATH) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for s in data["surfaces"]:
        if s["scope"] not in SCOPES:
            raise ValueError(f"{s['id']}: scope must be one of {SCOPES}")
        if s["scope"] == "opt-in" and not s.get("opt_in"):
            raise ValueError(f"{s['id']}: an opt-in surface names its flag")
        if s["since"] is not None and parse(s["since"]) is None:
            raise ValueError(f"{s['id']}: since {s['since']!r} is not x.y.z")
    return data


#: The whole manifest (surfaces, sources, the date it was checked).
MANIFEST: Dict[str, Any] = {}
#: (surface id, since, what mokata loses below it) for every DATED core surface — the floor's input.
FEATURES: Tuple[Tuple[str, Version, str], ...] = ()
MIN_VERSION: Version = (0, 0, 0)


def since(surface_id: str) -> Optional[Version]:
    """The version a surface arrived in (None when undated). KeyError for an unknown id."""
    for s in MANIFEST["surfaces"]:
        if s["id"] == surface_id:
            return parse(s["since"]) if s["since"] else None
    raise KeyError(surface_id)


def opt_in_surfaces(flag: str) -> List[Dict[str, Any]]:
    return [s for s in MANIFEST["surfaces"] if s.get("opt_in") == flag]


_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_TIMEOUT_SECS = 3.0


def parse(text: Optional[str]) -> Optional[Version]:
    m = _VERSION_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def fmt(v: Version) -> str:
    return ".".join(str(x) for x in v)


MANIFEST = _load_manifest()
FEATURES = tuple((s["id"], parse(s["since"]), s["loss"]) for s in MANIFEST["surfaces"]
                 if s["scope"] == "core" and s["since"])
MIN_VERSION = max(v for _f, v, _l in FEATURES)


@lru_cache(maxsize=1)
def installed_version() -> Optional[Version]:
    """`claude --version`, parsed; None when Claude Code is not on PATH or will not answer."""
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=_TIMEOUT_SECS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse(p.stdout) or parse(p.stderr)


@dataclass(frozen=True)
class VersionCheck:
    version: Optional[Version]
    missing: Tuple[Tuple[str, Version, str], ...]

    @property
    def ok(self) -> bool:
        return self.version is None or not self.missing

    def detail(self) -> str:
        lost = "; ".join(f"{f} needs {fmt(v)}: {loss}" for f, v, loss in self.missing)
        return (f"Claude Code {fmt(self.version)} is older than mokata's minimum "
                f"{fmt(MIN_VERSION)} — {lost}. Update Claude Code (`claude update`).")


def check(version: Optional[Version] = None) -> VersionCheck:
    """Which features this Claude Code lacks. No version (not installed) checks nothing: another
    harness is not a Claude Code that is too old."""
    v = version if version is not None else installed_version()
    if v is None:
        return VersionCheck(None, ())
    return VersionCheck(v, tuple(f for f in FEATURES if v < f[1]))


def missing_features(version: Version) -> List[str]:
    return [f for f, since, _l in FEATURES if version < since]


__all__ = ["MANIFEST", "MANIFEST_PATH", "FEATURES", "MIN_VERSION", "parse", "fmt", "since",
           "opt_in_surfaces", "installed_version", "VersionCheck", "check", "missing_features"]
