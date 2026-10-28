"""0.0.26 O12b SESSIONSTART-BUDGET — the slow SessionStart briefing lines, computed OFF the hook.

Measured on a 1M-line repo after O12 (wall ms, loaded box): the git status call 158, the
first-session dependency check 119, the due-intentions check 100, the calibration ledger write 56.
Each is a fact that changes slowly or on a known trigger, so each is CACHED here with a freshness
key and refreshed by ONE detached job (`refresh_main`, started after the briefing is emitted):

  * FRESH (key unchanged and younger than its TTL) — the cached line, no work;
  * STALE — the last line (or what its "nothing" meant), with its AGE and that it is re-checking;
  * MISSING — a line SAYING the check is running in the background (never a silent omission).

A stale answer ALWAYS states its age — a stale "nothing to report" included ("mokata: no
hook-wiring drift — checked 3 h ago, re-checking"): an old absence of a warning is a claim too. The O12 structure line (`knowledge.structure_cache`)
is refreshed by the same job. Run-state only (temp_local/briefing/), entirely derived.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import sys
import time

CACHE_FILENAME = "lines.json"
LOCK_FILENAME = "refresh.lock"
CACHE_VERSION = 1
LOCK_STALE_SECS = 600.0
REFRESH_FLAG = "--refresh-briefing"

GIT, INTENTIONS, WIRING, MCP = "git", "intentions", "wiring", "mcp"


def skills_name() -> str:
    """The skills-menu check's answer differs by route (the plugin needs no wiring here)."""
    return "skills:plugin" if os.environ.get("CLAUDE_PLUGIN_ROOT") else "skills:cli"
MODE_CLI, MODE_PLUGIN = "mode:cli", "mode:plugin"


def age_of(seconds: float) -> str:
    """A human age: "under a minute", "12 min", "3 h", "2 d"."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "under a minute"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} d"


_AGED = " — checked {ago} ago, re-checking"


class _Producer:
    def __init__(self, ttl, missing, nothing):
        self.ttl = ttl                  # seconds a fresh-keyed answer stays fresh
        self.missing = missing          # said when there is no answer yet
        self.nothing = nothing          # what a "nothing to report" answer MEANS, said when stale

    def stale(self, line, ago: str) -> str:
        """A stale answer always states its age — a stale "nothing to report" too (never silent:
        the absence of a warning is itself a claim, and an old one must say how old it is)."""
        return (line if line else self.nothing) + _AGED.format(ago=ago)


_IN_BG = " is being checked in the background — its result is in the next briefing."

PRODUCERS = {
    GIT: _Producer(
        120.0,
        "- git: status is being read in the background (kept off session start) — `git status`",
        "- git: no status (not a git checkout, or git did not answer)"),
    INTENTIONS: _Producer(
        300.0,
        "mokata: due intentions are being checked in the background — `mokata memory --kind "
        "intention` lists them now.",
        "mokata: no intentions due"),
    WIRING: _Producer(
        3600.0,
        "mokata: the hook wiring" + _IN_BG + " (`mokata doctor` checks it now)",
        "mokata: no hook-wiring drift"),
    MCP: _Producer(
        3600.0, "mokata: MCP-server reachability" + _IN_BG,
        "mokata: no MCP-server reachability problem"),
    "skills:cli": _Producer(
        3600.0, "mokata: the skills menu" + _IN_BG, "mokata: the skills menu is wired"),
    "skills:plugin": _Producer(
        3600.0, "mokata: the skills menu" + _IN_BG, "mokata: the skills menu is wired"),
    MODE_CLI: _Producer(
        6 * 3600.0, "mokata: the optional-dependency check" + _IN_BG,
        "mokata: no optional dependency missing"),
    MODE_PLUGIN: _Producer(
        6 * 3600.0, "mokata: the optional-dependency check" + _IN_BG,
        "mokata: no optional dependency missing"),
}


def cache_dir(root: str) -> str:
    from .knowledge.structure_cache import cache_dir as _d
    return _d(root)


def _sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    if path.endswith("-wal") and st.st_size == 0:
        return None          # an EMPTY WAL comes and goes with every reader's open/close
    return [st.st_mtime_ns, st.st_size]


def key_for(root: str, name: str) -> dict:
    """What each answer depends on (stat signatures; cheap)."""
    from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    from .knowledge.structure_cache import _git_dir
    gd = _git_dir(root)
    git = [os.path.join(gd, "HEAD"), os.path.join(gd, "index")] if gd else []
    manifest = os.path.join(root, MOKATA_DIR, "manifest.json")
    home = os.path.expanduser("~")
    if name == GIT:
        paths = git
    elif name == WIRING:                    # every settings scope + the plugin registry + this engine
        here = os.path.dirname(os.path.abspath(__file__))
        paths = [os.path.join(root, ".claude", "settings.json"),
                 os.path.join(root, ".claude", "settings.local.json"),
                 os.path.join(home, ".claude", "settings.json"),
                 os.path.join(home, ".claude", "plugins", "installed_plugins.json"),
                 os.path.join(here, "hook_wiring.py")]
    elif name.startswith("skills:"):        # the commands/skills dirs this root and home wire
        paths = [os.path.join(base, ".claude", sub) for base in (root, home)
                 for sub in ("commands", "skills")]
        paths += [os.path.join(home, ".claude", "plugins", "installed_plugins.json"),
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_visibility.py")]
    elif name == MCP:
        paths = [os.path.join(root, ".mcp.json"), os.path.join(home, ".claude.json"),
                 sys.executable]
    elif name == INTENTIONS:
        db = os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, "memory", "memory.db")
        paths = [manifest, db, db + "-wal"] + git
    else:                                   # the dependency check: this interpreter + the manifest
        paths = [manifest, sys.executable]
    return {p: _sig(p) for p in paths}


def _read(root: str) -> dict:
    import json
    try:
        with open(os.path.join(cache_dir(root), CACHE_FILENAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("v") != CACHE_VERSION:
        return {}
    return data.get("entries") or {}


FRESH, STALE, MISSING = "fresh", "stale", "missing"


def lookup(root: str, name: str, *, now: "float | None" = None):
    """(state, line-to-show). Never raises; any fault reads as MISSING."""
    prod = PRODUCERS[name]
    try:
        entry = _read(root).get(name)
    except Exception:  # noqa: BLE001
        entry = None
    if not isinstance(entry, dict):
        return MISSING, prod.missing
    now = time.time() if now is None else now
    line, at = entry.get("line"), float(entry.get("at") or 0)
    if entry.get("key") == key_for(root, name) and now - at < prod.ttl:
        return FRESH, line
    return STALE, prod.stale(line, age_of(now - at))


def store(root: str, answers: dict, *, now: "float | None" = None) -> None:
    """Merge `{name: line-or-None}` into the cache, each keyed NOW."""
    import json
    from .atomicfile import atomic_write_text
    now = time.time() if now is None else now
    entries = _read(root)
    for name, line in answers.items():
        entries[name] = {"line": line, "at": now, "key": key_for(root, name)}
    atomic_write_text(os.path.join(cache_dir(root), CACHE_FILENAME),
                      json.dumps({"v": CACHE_VERSION, "entries": entries}, sort_keys=True))


# ---------------------------------------------------------------- derived (sync-fallback) values

DERIVED_FILENAME = "derived.json"


def derived(root: str, name: str, paths: list, compute, keep=lambda _v: True):
    """A value that is either FRESH for exactly `paths` (their stat signatures are unchanged since
    it was computed) or computed NOW — never served stale. For briefing lines that must be current
    (the project's rules) but rarely change: the hook then skips the import + read that produce
    them. `keep(value)` False ⇒ not cached (e.g. a failed read that must stay loud)."""
    import json
    key = {p: _sig(p) for p in paths}
    path = os.path.join(cache_dir(root), DERIVED_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entry = data.get(name) if isinstance(data, dict) else None
    except (OSError, ValueError):
        data, entry = {}, None
    if isinstance(entry, dict) and entry.get("key") == key:
        return entry.get("value")
    value = compute()
    # Cached only when the inputs did not move WHILE it was computed (a first read can create the
    # store; a concurrent write can land mid-read): the next call then simply derives it again.
    if keep(value) and {p: _sig(p) for p in paths} == key:
        try:
            from .atomicfile import atomic_write_text
            data = data if isinstance(data, dict) else {}
            data[name] = {"key": key, "value": value}
            atomic_write_text(path, json.dumps(data, sort_keys=True))
        except (OSError, TypeError, ValueError):
            pass                              # uncached: the next session start reads it again
    return value


# ------------------------------------------------------------------------------ the refresher

def _lock_fresh(d: str) -> bool:
    try:
        return time.time() - os.stat(os.path.join(d, LOCK_FILENAME)).st_mtime < LOCK_STALE_SECS
    except OSError:
        return False


def start_refresh(root: str, *, via_plugin: bool, calibrate: "int | None" = None,
                  capture: bool = False) -> bool:
    """Start the ONE detached refresher (unless one runs). It also logs this briefing's token
    estimate (`calibrate`) — the ledger write SessionStart used to pay synchronously."""
    try:
        d = cache_dir(root)
        os.makedirs(d, exist_ok=True)
        if _lock_fresh(d):
            return False
        args = [REFRESH_FLAG, root, "plugin" if via_plugin else "cli",
                str(int(calibrate)) if calibrate is not None else "-",
                "capture" if capture else "-"]
        from mokata_emit import spawn_mokata_emit
        return spawn_mokata_emit(args, cwd=d)
    except Exception:  # noqa: BLE001 — the next session start retries; its lines say "checking"
        return False


def _acquire(d: str) -> bool:
    path = os.path.join(d, LOCK_FILENAME)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _lock_fresh(d):
                return False
            try:
                os.remove(path)
            except OSError:
                return False
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    return False


def _release(d: str) -> None:
    try:
        os.remove(os.path.join(d, LOCK_FILENAME))
    except OSError:
        pass


def compute(root: str, surface, route: str) -> dict:
    """Every producer's answer, computed the slow way (here, off the hook)."""
    from .bootstrap import _wiring_drift_line
    from .hook_cli import _intentions_due, _mcp_reachability_warning, _mode_dependencies_missing
    from .live_context import git_line
    from .skills_visibility import briefing_offer
    return {GIT: git_line(root),
            skills_name(): briefing_offer(root),
            INTENTIONS: _intentions_due(surface),
            WIRING: _wiring_drift_line(surface),
            MCP: _mcp_reachability_warning(root),
            (MODE_PLUGIN if route == "plugin" else MODE_CLI):
                _mode_dependencies_missing(root, route == "plugin")}


def refresh_main(argv: list) -> int:
    """`<python> mokata_emit/__main__.py --refresh-briefing <root> <cli|plugin> <estimate|-> <capture|->`."""
    if len(argv) < 2:
        return 1
    root, route = argv[0], argv[1]
    d = cache_dir(root)
    try:
        os.makedirs(d, exist_ok=True)
        if not _acquire(d):
            return 0
    except OSError:
        return 0
    try:
        from .config import Surface
        surface = Surface.load(root)
        if len(argv) > 2 and argv[2] != "-":
            from .govern import log_bootstrap_calibration
            log_bootstrap_calibration(surface, int(argv[2]))
        if len(argv) > 3 and argv[3] == "capture":
            # the "since last session" baseline — after the briefing derived its diff
            from .visibility import capture_session_snapshot
            capture_session_snapshot(surface)
        store(root, compute(root, surface, route))
        # Re-derive the memory-derived lines AGAINST the baseline just captured, so the next
        # session start finds them fresh unless something really changed in between.
        from .bootstrap import _cached_changed_since, _cached_rule_lines
        from .context_budget import context_budget
        _cached_rule_lines(surface, context_budget(surface.manifest).briefing_rules_lines)
        _cached_changed_since(surface)
        from .bootstrap import _cached_resume_hint
        _cached_resume_hint(surface)
        from .knowledge import structure_cache
        if not structure_cache.is_fresh(root):
            from .knowledge.layer import graph_structure_line
            structure_cache.store(root, graph_structure_line(surface))
        return 0
    except Exception:  # noqa: BLE001 — nobody to tell; the next briefing still says "checking"
        return 1
    finally:
        _release(d)
