"""Stage 45b — version & upgrade UX.

`version_info` is a pure, OFFLINE snapshot (version + profile + install method + Python):
local-first, zero egress. `check_for_update` is the ONE opt-in outbound call — it is
netguard-accounted (logged), degrade-clean offline (a blocked/failed check just says so),
and dependency-free (stdlib urllib only). `upgrade` is human-gated and never auto-runs.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from . import __version__
from .plugin_cache import read_plugin_root

# The published-release source for the opt-in check (the public mirror).
RELEASE_API = "https://api.github.com/repos/JasGujral/mokata-oss/releases/latest"
PLUGIN_UPDATE_STEPS = (
    "/plugin marketplace update mostack",
    "/plugin install mokata@mostack   # reinstall to pick up the update",
)


# --- offline display ------------------------------------------------------------
@dataclass
class VersionInfo:
    version: str
    profile: str
    install_method: str          # pip | plugin | source
    python: str

    def render(self) -> str:
        return (f"mokata {self.version}\n"
                f"  profile: {self.profile}\n"
                f"  install: {self.install_method}\n"
                f"  python:  {self.python}")


def detect_install_method(home: Optional[str] = None,
                          package_file: Optional[str] = None) -> str:
    """Best-effort: 'plugin' when the recorded plugin root contains this package,
    'pip' when it lives in a site/dist-packages tree, else 'source' (a dev checkout).
    Never raises."""
    pkg = os.path.dirname(os.path.abspath(package_file or __file__))
    try:
        root = read_plugin_root(home=home)
    except Exception:
        root = None
    if root:
        src = os.path.abspath(os.path.join(root, "src"))
        if pkg == os.path.join(src, "mokata") or pkg.startswith(src + os.sep):
            return "plugin"
    if (os.sep + "site-packages" + os.sep) in pkg or \
            (os.sep + "dist-packages" + os.sep) in pkg:
        return "pip"
    return "source"


def version_info(profile: str = "(not initialized)",
                 home: Optional[str] = None) -> VersionInfo:
    return VersionInfo(version=__version__, profile=profile,
                       install_method=detect_install_method(home=home),
                       python=platform.python_version())


# --- opt-in update check (the only egress) --------------------------------------
@dataclass
class UpdateCheck:
    ok: bool
    current: str
    latest: Optional[str]
    up_to_date: bool
    message: str

    def render(self) -> str:
        return self.message


def _version_tuple(tag: str):
    """Parse 'v0.0.4' / '0.0.4' into a comparable tuple; non-numeric parts -> 0."""
    cleaned = (tag or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    parts = []
    for piece in cleaned.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def _default_fetch(url: str) -> str:
    """Fetch the latest release tag from the GitHub releases API (stdlib only). Raises on
    any network/parse failure — the caller degrades clean."""
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "mokata-version-check"})
    with urllib.request.urlopen(req, timeout=5) as resp:       # noqa: S310 (https only)
        data = json.loads(resp.read().decode("utf-8"))
    tag = data.get("tag_name") or data.get("name")
    if not tag:
        raise ValueError("no tag_name in release response")
    return tag


def check_for_update(current: Optional[str] = None, *,
                     fetcher: Optional[Callable[[str], str]] = None,
                     ledger: Any = None,
                     home: Optional[str] = None) -> UpdateCheck:
    """Opt-in: compare the installed version to the latest published release. This is the
    ONE outbound call — accounted in the ledger and degrade-clean (a failed/blocked fetch
    returns ok=False with a friendly message, never raises)."""
    current = current or __version__
    if ledger is not None:
        try:
            ledger.record("update_check", outbound=True, source=RELEASE_API)
        except Exception:
            pass
    fetch = fetcher or _default_fetch
    try:
        tag = fetch(RELEASE_API)
    except Exception:
        return UpdateCheck(ok=False, current=current, latest=None, up_to_date=False,
                           message="couldn't check for updates (offline or unreachable) — "
                                   f"you're on mokata {current}.")
    latest = (tag or "").strip().lstrip("vV")
    up_to_date = _version_tuple(current) >= _version_tuple(latest)
    if up_to_date:
        msg = f"up to date — mokata {current} is the latest release."
    else:
        msg = (f"a newer mokata is available: {latest} (you have {current}). "
               f"Run `mokata upgrade` to update.")
    return UpdateCheck(ok=True, current=current, latest=latest,
                       up_to_date=up_to_date, message=msg)


# --- human-gated upgrade --------------------------------------------------------
def upgrade_steps(method: str) -> List[str]:
    """The upgrade recipe for an install method (display-only; the CLI gates the run)."""
    if method == "plugin":
        return list(PLUGIN_UPDATE_STEPS)
    if method == "source":
        return ["git pull   # you're on a source checkout",
                "pip install -e .   # reinstall the editable package"]
    return ["pip install -U mokata"]


def pip_upgrade_command() -> List[str]:
    return [sys.executable, "-m", "pip", "install", "-U", "mokata"]


def run_pip_upgrade(runner: Optional[Callable[[List[str]], Any]] = None) -> List[str]:
    """Run `pip install -U mokata` (ONLY when the caller has already human-gated it).
    Returns the command run. The runner is injectable so callers/tests don't shell out."""
    cmd = pip_upgrade_command()
    runner = runner or (lambda c: __import__("subprocess").run(c, check=False))
    runner(cmd)
    return cmd
