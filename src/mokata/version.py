"""Stage 45b — version & upgrade UX.

`version_info` is a pure, OFFLINE snapshot (version + profile + install method + Python):
local-first, zero egress. `check_for_update` is the opt-in outbound call, on the release feed
`mokata update` reads (`update.fetch_releases`). Upgrading is `mokata update`'s job (0.0.26 L2):
the plan, one confirm, the installer's command, then the vendored skills and wiring.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import __version__
from .plugin_cache import read_plugin_root

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
        # D5 — deliberately left BROAD, with no narrow class to name: `read_plugin_root`'s own
        # docstring promises it returns None rather than raising ("or None if absent/unreadable/
        # empty"), so this handler guards a contract that is already never-raise. Any exception
        # reaching it is by definition one the callee promised could not happen; there is no honest
        # class to enumerate, and `install_method` is cosmetic (it labels a `mokata version` line).
        root = None
    if root:
        src = os.path.abspath(os.path.join(root, "src"))
        if pkg == os.path.join(src, "mokata") or pkg.startswith(src + os.sep):
            return "plugin"
    # SELF-PROTECT (0.0.16 stage 3): this WAS the codebase's only site-/dist-packages predicate,
    # inline and cosmetic. It is now single-sourced in `selfprotect.in_installed_tree`, where it is
    # load-bearing (rule (a) of the non-overridable write block) — one predicate, two callers, so
    # the label a user reads and the tree the gate refuses can never drift apart.
    from .selfprotect import in_installed_tree
    if in_installed_tree(pkg):
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


def check_for_update(current: Optional[str] = None, *,
                     fetcher: Optional[Callable[[str], str]] = None,
                     ledger: Any = None,
                     home: Optional[str] = None) -> UpdateCheck:
    """Opt-in (`mokata version --check`): installed vs the latest published release, from the
    SAME feed `mokata update` reads (`update.fetch_releases` — one call, ledger-accounted,
    degrade-clean: a failed/blocked fetch returns ok=False, never raises)."""
    from .update import fetch_releases, version_tuple
    current = current or __version__
    feed = fetch_releases(fetcher=fetcher, ledger=ledger)
    if not feed.ok:
        return UpdateCheck(ok=False, current=current, latest=None, up_to_date=False,
                           message="couldn't check for updates (offline or unreachable) — "
                                   f"you're on mokata {current}.")
    latest = feed.latest or current
    up_to_date = version_tuple(current) >= version_tuple(latest)
    if up_to_date:
        msg = f"up to date — mokata {current} is the latest release."
    else:
        msg = (f"a newer mokata is available: {latest} (you have {current}). "
               "Run `mokata update` for the release notes and the one line that upgrades it.")
    return UpdateCheck(ok=True, current=current, latest=latest,
                       up_to_date=up_to_date, message=msg)
