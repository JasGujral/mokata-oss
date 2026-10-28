"""`mokata update` — update mokata in one action (0.0.26 L2, `MOK-UPDATE`).

It shows the plan, asks ONE "Update now? [Y/n]" (`--yes` answers it; that answer — or the IDE's
click — is the P2 consent), runs the installer's own command, then finishes the job with the NEW
mokata (`mokata update --finish`: refresh the vendored commands/skills, re-wire only if the
wiring changed). `--check` is the print-only mode; `--offline` is print-only with no network.

The plan:
  1. how was mokata installed?            (`install_route.detect_installer`, or the plugin)
  2. which version is installed / latest? (ONE network call, only when the human runs it)
  3. what changed between them?           (the release notes of every version in between)
  4. the ONE command that upgrades AND keeps the extras   (`install_route.plan_upgrade`)
  5. the follow-up (`update --finish`, run automatically after a successful upgrade).

Where it runs: an upgrade replaces the environment mokata runs from. POSIX lets a running
process's files be replaced, so the CLI runs the command in the foreground and shows its output.
Windows locks the running `mokata.exe`, so there (and for the IDE on Windows) the command runs
from a DETACHED updater (`_updater.py`, copied out of the package and started with the base
Python, no console window) that waits for mokata to exit; it writes a result file the next
`mokata` / IDE start reports ("updated to X", or the error and the line to run by hand).

The release feed is the public mirror's GitHub Releases list: one call returns every version AND
its notes. Offline (or blocked) it says so and falls back to printing the line — nothing runs
without knowing there is something newer.

The IDE health panel's update row uses this same plan and executor.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import __version__

DIST = "mokata"
RELEASES_URL = "https://github.com/JasGujral/mokata-oss/releases"
RELEASES_API = "https://api.github.com/repos/JasGujral/mokata-oss/releases?per_page=50"
PLUGIN = "plugin"
#: Lines of each release's notes shown in the delta; the full notes are one link away.
NOTES_LINES = 8


# ----------------------------------------------------------------------------- the release feed
@dataclass
class Release:
    version: str
    notes: str = ""


@dataclass
class ReleaseFeed:
    ok: bool
    releases: List[Release] = field(default_factory=list)
    error: str = ""

    @property
    def latest(self) -> Optional[str]:
        vs = [r.version for r in self.releases]
        return max(vs, key=version_tuple) if vs else None


def version_tuple(tag: str):
    """'v0.0.4' / '0.0.4' -> (0, 0, 4); non-numeric parts -> 0."""
    cleaned = (tag or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    parts = []
    for piece in cleaned.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def _default_fetch(url: str) -> str:
    """GET the release list (stdlib only). Raises on any failure — the caller degrades clean."""
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "mokata-update"})
    # B310: `url` is the hardcoded https constant above, never user input; opt-in (the human ran
    # `mokata update` or pressed "check"), timeout-bounded, ledger-accounted.
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310  # nosec B310
        return resp.read().decode("utf-8")


def parse_releases(raw: str) -> List[Release]:
    """GitHub's release list -> published, non-draft, non-prerelease versions with their notes.
    A single release object (the `/latest` shape) is accepted too."""
    data = json.loads(raw)
    items = data if isinstance(data, list) else [data]
    out: List[Release] = []
    for it in items:
        if not isinstance(it, dict) or it.get("draft") or it.get("prerelease"):
            continue
        tag = str(it.get("tag_name") or it.get("name") or "").strip()
        if not tag:
            continue
        out.append(Release(version=tag.lstrip("vV"), notes=str(it.get("body") or "")))
    return out


def fetch_releases(*, fetcher: Optional[Callable[[str], str]] = None,
                   ledger: Any = None) -> ReleaseFeed:
    """The ONE outbound call. Accounted in the ledger first; never raises."""
    if ledger is not None:
        try:
            ledger.record("update_check", outbound=True, source=RELEASES_API)
        except (OSError, TypeError):
            pass                       # the accounting line is best-effort; never blocks the check
    try:
        releases = parse_releases((fetcher or _default_fetch)(RELEASES_API))
    except Exception as exc:  # noqa: BLE001 — offline / blocked / bad JSON all mean "could not check"
        return ReleaseFeed(ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    if not releases:
        return ReleaseFeed(ok=False, error="the release feed listed no published release")
    return ReleaseFeed(ok=True, releases=releases)


def changelog_delta(releases: List[Release], current: str, latest: str) -> List[Release]:
    """Every release newer than `current` up to `latest`, oldest first."""
    lo, hi = version_tuple(current), version_tuple(latest)
    picked = [r for r in releases if lo < version_tuple(r.version) <= hi]
    return sorted(picked, key=lambda r: version_tuple(r.version))


def _excerpt(notes: str, n: int = NOTES_LINES) -> List[str]:
    lines = [ln.rstrip() for ln in notes.replace("\r\n", "\n").split("\n") if ln.strip()]
    return lines[:n] + (["…"] if len(lines) > n else [])


# ----------------------------------------------------------------------------- the plan
@dataclass
class UpdatePlan:
    installer: str                     # uv-tool | pipx | pip | uv-pip | none | plugin
    current: str
    command: str                       # the ONE line (plugin: Claude Code's two slash lines)
    argv: Optional[List[str]] = None   # the same command, resolved to run (None: print only)
    deferred: bool = False             # Windows: run only after this process exits (detached)
    keeps: str = ""                    # what the line installs, e.g. "mokata[full]"
    reason: str = ""                   # a caveat on the line (pin, editable, Windows lock)
    checked: bool = False              # was the release feed asked?
    online: bool = False               # ... and did it answer?
    offline_note: str = ""
    latest: Optional[str] = None
    delta: List[Release] = field(default_factory=list)
    follow_up: List[str] = field(default_factory=list)
    vendored: List[Dict[str, Any]] = field(default_factory=list)   # per-scope drift reports

    @property
    def newer(self) -> bool:
        return bool(self.latest) and version_tuple(self.latest) > version_tuple(self.current)

    def to_dict(self) -> Dict[str, Any]:
        return {"installer": self.installer, "current": self.current, "command": self.command,
                "keeps": self.keeps, "reason": self.reason, "runnable": self.argv is not None,
                "deferred": self.deferred, "checked": self.checked,
                "online": self.online, "offline_note": self.offline_note, "latest": self.latest,
                "newer": self.newer,
                "delta": [{"version": r.version, "notes": _excerpt(r.notes)} for r in self.delta],
                "follow_up": list(self.follow_up), "vendored": list(self.vendored),
                "releases_url": RELEASES_URL}


def _install_line(method: Optional[str], home: Optional[str], route: Dict[str, Any]):
    """The plugin first, then the owning installer's `InstallPlan`."""
    from .install_route import InstallPlan, plan_upgrade
    from .version import PLUGIN_UPDATE_STEPS, detect_install_method
    if (method or detect_install_method(home=home)) == PLUGIN:
        return InstallPlan(PLUGIN, ["the plugin"], None,
                           "  then  ".join(s.split("#")[0].strip() for s in PLUGIN_UPDATE_STEPS),
                           reason="run these inside Claude Code")
    return plan_upgrade(**route)


def plan_update(root: str = ".", *, check: bool = True,
                fetcher: Optional[Callable[[str], str]] = None, ledger: Any = None,
                home: Optional[str] = None, current: Optional[str] = None,
                method: Optional[str] = None, **route: Any) -> UpdatePlan:
    """Everything `mokata update` prints, as data. `check=False` makes no network call (the IDE's
    page-load pass). `method="plugin"` forces the plugin route (else detected); `route` is passed
    to `install_route.plan_upgrade` (tests inject the environment)."""
    current = current or __version__
    ip = _install_line(method, home, route)
    plan = UpdatePlan(installer=ip.installer, current=current, command=ip.display,
                      argv=ip.argv, deferred=ip.deferred,
                      keeps=ip.specs[0] if ip.specs else DIST, reason=ip.reason)
    if check:
        plan.checked = True
        feed = fetch_releases(fetcher=fetcher, ledger=ledger)
        plan.online = feed.ok
        if feed.ok:
            plan.latest = feed.latest
            if plan.newer:
                plan.delta = changelog_delta(feed.releases, current, plan.latest or current)
        else:
            plan.offline_note = ("could not reach the release feed (offline, or blocked) — "
                                 f"{feed.error}. The line below upgrades to whatever is latest.")
    from .harness_setup import vendored_drift
    plan.vendored = vendored_drift(root, home=home)
    plan.follow_up = _follow_up(plan)
    return plan


def _follow_up(plan: UpdatePlan) -> List[str]:
    """After the upgrade line: the new version's wiring, commands and skills. Without a new
    version: refresh only what differs from THIS mokata on disk."""
    if plan.installer == PLUGIN:
        return ["mokata doctor --wiring"]          # the plugin update rewires itself
    scopes = [v for v in plan.vendored if v.get("installed")]
    flag = {"project": "", "user": " --scope user"}
    if plan.newer or not plan.checked:
        # A new version (or unknown): its wiring may have changed; setup previews and asks first.
        return [f"mokata setup claude{flag[v['scope']]}" for v in scopes] or []
    return [f"mokata setup claude --skills-only{flag[v['scope']]}"
            for v in scopes if v.get("drift")]


# ----------------------------------------------------------------------------- rendering
def render_update(plan: UpdatePlan, *, check: bool = True) -> str:
    """The plan as text. `check=True` is the print-only wording (`--check` / `--offline`)."""
    head = ("mokata update --check — prints what to run; installs and changes nothing."
            if check else "mokata update")
    out: List[str] = [head, ""]
    out.append(f"  installed  {plan.current}  ({plan.installer}; keeps {plan.keeps})")
    if not plan.checked:
        out.append("  latest     not checked")
    elif not plan.online:
        out.append(f"  latest     unknown — {plan.offline_note}")
    else:
        out.append(f"  latest     {plan.latest}" + ("" if plan.newer else "  (up to date)"))
    if plan.delta:
        out += ["", f"What changed {plan.current} → {plan.latest}:"]
        for r in reversed(plan.delta):
            out.append(f"  {r.version}")
            out += [f"    {ln}" for ln in _excerpt(r.notes)] or ["    (no notes)"]
        out.append(f"  full notes: {RELEASES_URL}")
    runs = not check and plan.argv is not None and plan.newer
    if plan.newer or not plan.online:
        out += ["", "Will run:" if runs else "Run:", f"  {plan.command}"]
        if plan.reason:
            out.append(f"  ({plan.reason})")
    elif plan.reason and plan.installer != PLUGIN:
        out += ["", f"When there is a new version: {plan.command}  ({plan.reason})"]
    for v in plan.vendored:
        if not v.get("installed"):
            continue
        where = f"{v['scope']} ({v['dir']})"
        if not v.get("drift"):
            out += ["", f"Vendored commands + skills, {where}: match this mokata."]
            continue
        out += ["", f"Vendored commands + skills, {where}: {v['summary']}"]
        for path in v.get("examples", []):
            out.append(f"  {path}")
    if plan.follow_up:
        out += ["", "Then, automatically (`mokata update --finish` with the new mokata):" if runs
                else "Then:" if (plan.newer or not plan.online) else "Run:"]
        out += [f"  {c}" for c in plan.follow_up]
    if plan.checked and plan.online and not plan.newer and not plan.follow_up:
        out += ["", "Nothing to do."]
    return "\n".join(out)


# ----------------------------------------------------------------------------- the executor
def finish_argv(root: str, python: Optional[str] = None) -> List[str]:
    """The follow-up, run by the NEW mokata: this process still holds the old modules."""
    return [python or sys.executable, "-m", "mokata", "update", "--finish", "--path",
            os.path.abspath(root)]


def _fg_run(argv: List[str]) -> int:
    """Foreground: stdio inherited, so the installer's progress is the user's to watch."""
    import subprocess
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        return subprocess.run(argv, check=False).returncode
    except OSError as exc:
        print(f"could not start {argv[0]}: {exc}", file=sys.stderr)
        return 127


def run_update(plan: UpdatePlan, root: str, *, runner: Optional[Callable[[List[str]], int]] = None,
               out: Callable[[str], None] = print) -> int:
    """POSIX CLI: the confirmed command, then the follow-up with the new mokata. Exactly two
    spawns: `plan.argv`, then `finish_argv(root)`."""
    run = runner or _fg_run
    rc = run(list(plan.argv or []))
    if rc != 0:
        out(f"\nthe upgrade failed (exit {rc}). Run it yourself when the cause is fixed:\n"
            f"  {plan.command}")
        return 1
    out("\nupgraded — finishing with the new mokata (vendored commands/skills, wiring):")
    return 0 if run(finish_argv(root)) == 0 else 1


def _mokata_home(home: Optional[str] = None) -> str:
    return os.path.join(home or os.path.expanduser("~"), ".mokata")


def result_path(home: Optional[str] = None) -> str:
    return os.path.join(_mokata_home(home), "update-result.json")


def _base_python() -> str:
    """An interpreter OUTSIDE the environment being upgraded: a venv's base Python (uv tool and
    pipx envs are venvs), so the updater does not itself hold a file the installer replaces."""
    base = getattr(sys, "_base_executable", None)
    return base if base and os.path.isfile(base) else sys.executable


def _detach_kwargs(windows: bool) -> Dict[str, Any]:
    import subprocess
    if windows:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {"start_new_session": True}


def launch_detached(plan: UpdatePlan, root: str, *, wait_pids: List[int],
                    home: Optional[str] = None, spawn: Optional[Callable[..., Any]] = None,
                    windows: Optional[bool] = None) -> Dict[str, Any]:
    """Start the updater that runs `plan.argv` once every pid in `wait_pids` has exited. Writes
    `<home>/.mokata/update/{updater.py, job.json}`; the updater writes `result_path(home)`."""
    import shutil
    import subprocess
    windows = (os.name == "nt") if windows is None else windows
    d = os.path.join(_mokata_home(home), "update")
    os.makedirs(d, exist_ok=True)
    script = os.path.join(d, "updater.py")
    shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_updater.py"),
                    script)
    job = {"wait_pids": [int(p) for p in wait_pids], "argv": list(plan.argv or []),
           "display": plan.command, "from": plan.current,
           "finish_argv": finish_argv(root),
           "version_argv": [sys.executable, "-c", "import mokata; print(mokata.__version__)"],
           "result": result_path(home)}
    path = os.path.join(d, "job.json")
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(job, fh, indent=2)
    os.replace(tmp, path)                            # the updater never reads a half-written job
    try:
        os.remove(result_path(home))                 # a stale result must not be read as this one
    except OSError:
        pass
    (spawn or subprocess.Popen)([_base_python(), "-I", script, path],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                **_detach_kwargs(windows))
    return job


def take_result(home: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The detached updater's result, read ONCE (removed after reading)."""
    p = result_path(home)
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        os.remove(p)
    except OSError:
        pass
    return data if isinstance(data, dict) else None


def render_result(res: Dict[str, Any]) -> str:
    if res.get("ok"):
        line = f"mokata updated: {res.get('from') or '?'} → {res.get('to') or 'the latest'}."
        if res.get("finish_returncode") not in (None, 0):
            line += (" The follow-up did not finish — run `mokata update --finish` "
                     "(refreshes the vendored commands/skills and wiring).")
        return line
    return (f"mokata update did not complete: {res.get('error') or 'unknown error'}.\n"
            f"  run it yourself: {res.get('manual') or 'mokata update --check'}")


def finish_update(root: str = ".", *, home: Optional[str] = None,
                  setup: Optional[Callable[..., Any]] = None,
                  out: Callable[[str], None] = print) -> int:
    """`mokata update --finish` — run by the NEW mokata right after an upgrade (the human's yes to
    the update covers it). Per scope where mokata's commands/skills are vendored: re-wire with a
    full `setup claude` when the wiring is not what this mokata writes, else refresh only the
    commands/skills when they differ. Nothing else, and nothing where nothing was set up."""
    from types import SimpleNamespace
    from .govern.doctor import wiring_drift_findings
    from .harness_setup import setup_harness, vendored_drift
    setup = setup or setup_harness
    # Never raises (doctor's contract: a check that cannot run is itself a finding -> re-wire).
    stale_wiring = bool(wiring_drift_findings(SimpleNamespace(root=str(root)), home))
    from . import __version__ as now
    out(f"mokata {now}")
    rc = 0
    did = False
    for v in vendored_drift(root, home=home):
        if not v.get("installed"):
            continue
        rewire = stale_wiring and v["scope"] == "project"
        if not (rewire or v.get("drift")):
            continue
        did = True
        what = "re-wiring (the wiring changed)" if rewire else "refreshing commands + skills"
        out(f"{v['scope']}: {what}")
        res = setup("claude", root=root, scope=v["scope"], assume_yes=True, home=home,
                    skills_only=not rewire, out=out)
        rc |= 0 if getattr(res, "message", "") == "ok" else 1
    if not did:
        out("commands, skills and wiring already current.")
    return rc


__all__ = ["Release", "ReleaseFeed", "UpdatePlan", "RELEASES_API", "RELEASES_URL",
           "changelog_delta", "fetch_releases", "finish_argv", "finish_update",
           "launch_detached", "parse_releases", "plan_update", "render_result", "render_update",
           "result_path", "run_update", "take_result", "version_tuple"]
