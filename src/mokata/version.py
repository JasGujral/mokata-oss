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
from dataclasses import dataclass, field
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
    # Justification for the B310 suppression: `url` is the hardcoded GitHub Releases API constant
    # (an https:// URL built in-code, never user input; no file:/custom scheme reachable); the call
    # is opt-in, timeout-bounded, and ledger-accounted, and the caller degrades clean on failure.
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310  # nosec B310
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
        except (OSError, TypeError):
            # D5 — the real raisers of a ledger append: the local file/disk under `.mokata/`
            # (OSError) and a non-serializable field (TypeError, from `json.dumps`). The netguard
            # accounting line is best-effort and must never block the opt-in check itself.
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
# DOC-ONBOARD — THE TAIL. Installing the new code is not upgrading mokata: `pip install -U`
# replaces the package and leaves `.claude/settings.json` exactly as the PREVIOUS version wrote
# it, so a hook added — or a matcher widened — since the user's last `mokata setup claude` is
# silently absent. The upgrade is not finished until the wiring is refreshed and verified, so
# these steps are part of the recipe, not an afterthought a user is expected to know.
#
# They are declared here, once, and used twice: printed for a hand-upgrader (`upgrade_steps`)
# and RUN for the one-command story (`finish_upgrade`). Same steps, same order, no divergence
# between what we document and what we do.
UPGRADE_TAIL_STEPS = (
    "mokata setup claude   # refresh the harness wiring (previews the change, asks first)",
    "mokata doctor --wiring   # confirm the gates resolve and the wiring is current",
)

# The plugin route gets the VERIFY step only. `mokata setup claude` is not its remedy — a plugin
# install is rewired by the plugin update itself — and printing a fix that does nothing for the
# reader is worse than printing none.
PLUGIN_TAIL_STEPS = (
    "mokata doctor --wiring   # confirm the plugin's hooks resolve after the update",
)


def upgrade_steps(method: str) -> List[str]:
    """The upgrade recipe for an install method (display-only; the CLI gates the run).

    Every route ends with the same question answered — is the wiring current? — because that
    is the half of an upgrade that used to be left to the user to know about."""
    if method == "plugin":
        return list(PLUGIN_UPDATE_STEPS) + list(PLUGIN_TAIL_STEPS)
    if method == "source":
        return ["git pull   # you're on a source checkout",
                "pip install -e .   # reinstall the editable package"] + list(UPGRADE_TAIL_STEPS)
    return ["pip install -U mokata"] + list(UPGRADE_TAIL_STEPS)


def pip_upgrade_command() -> List[str]:
    return [sys.executable, "-m", "pip", "install", "-U", "mokata"]


def run_pip_upgrade(runner: Optional[Callable[[List[str]], Any]] = None) -> List[str]:
    """Run `pip install -U mokata` (ONLY when the caller has already human-gated it).
    Returns the command run. The runner is injectable so callers/tests don't shell out."""
    cmd = pip_upgrade_command()
    runner = runner or (lambda c: __import__("subprocess").run(c, check=False))
    runner(cmd)
    return cmd


# --- DOC-ONBOARD: finishing the job ---------------------------------------------
@dataclass
class UpgradeTail:
    """What the post-install half of the upgrade actually did."""
    commands: List[List[str]] = field(default_factory=list)
    wiring_refreshed: bool = False
    doctor_ok: Optional[bool] = None      # None = never ran (the refresh was declined)


def upgrade_tail_commands(root: str = ".", *, scope: str = "project",
                          assume_yes: bool = False) -> List[List[str]]:
    """The two commands that finish an upgrade, spawned as SUBPROCESSES of the NEW mokata.

    This indirection is the whole correctness of the feature. `pip install -U` has just
    replaced the package on disk, but THIS process still holds the OLD modules in memory —
    every matcher, every hook spec, every path resolver. Re-wiring in-process would faithfully
    write the wiring of the version the user just upgraded AWAY from, which is worse than not
    re-wiring at all: it would look done and leave them stale. `-m mokata` re-enters through
    the freshly-installed code.

    `--yes` propagates only when the human passed it to `mokata upgrade`. Without it, the
    spawned `setup` shows its preview diff and asks — the same gate, unchanged, and the only
    thing standing between an upgrade and a settings.json write."""
    base = [sys.executable, "-m", "mokata"]
    setup_cmd = base + ["setup", "claude", "--path", str(root), "--scope", scope]
    if assume_yes:
        setup_cmd.append("--yes")
    return [setup_cmd, base + ["doctor", "--wiring", "--path", str(root)]]


def _default_tail_runner(cmd: List[str]) -> int:
    """Run a tail step with the parent's stdio INHERITED — so `setup`'s preview diff prints to
    the user's terminal and its y/N gate reads the user's keyboard. A captured runner here would
    silently convert the human gate into a fail-closed decline.

    The flush is not cosmetic. Our own `print` output sits in Python's buffer while the child
    writes straight to the file descriptor, so without it the line explaining WHY a preview diff
    is about to appear arrives after the diff it was introducing."""
    import subprocess
    sys.stdout.flush()
    sys.stderr.flush()
    return subprocess.run(cmd, check=False).returncode


def finish_upgrade(root: str = ".", *, scope: str = "project", assume_yes: bool = False,
                   runner: Optional[Callable[[List[str]], int]] = None,
                   out: Optional[Callable[[str], None]] = None) -> UpgradeTail:
    """Refresh the harness wiring (human-gated), then verify it — the half of `mokata upgrade`
    that used to be homework.

    Ordering is deliberate: a DECLINED refresh skips the verification entirely. Nothing was
    written, so there is nothing new to check, and running doctor anyway would report the stale
    wiring the user just chose to keep as though it were a fresh failure. They are told the
    wiring is untouched and given the one command that changes that."""
    emit = out or print
    run = runner or _default_tail_runner
    setup_cmd, doctor_cmd = upgrade_tail_commands(root, scope=scope, assume_yes=assume_yes)
    tail = UpgradeTail()

    emit("")
    emit("Finishing the upgrade — the new code is installed; the harness wiring is not yet.")
    tail.commands.append(setup_cmd)
    if run(setup_cmd) != 0:
        emit("harness wiring NOT refreshed (declined, or setup reported a problem) — your "
             "existing wiring is untouched. Run `mokata setup claude` when you're ready.")
        return tail
    tail.wiring_refreshed = True

    tail.commands.append(doctor_cmd)
    tail.doctor_ok = run(doctor_cmd) == 0
    if not tail.doctor_ok:
        emit("the wiring check above found problems — fix them before relying on the gates.")
    return tail
