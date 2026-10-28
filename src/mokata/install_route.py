"""How was mokata installed, and therefore how must its optional extras be installed?
(0.0.25 Lane I — `INSTALL-EXTRA-NO-PIP-IN-UV-TOOL`, `INSTALL-SELF-WINDOWS-LOCK` floor).

Until 0.0.24 every extra went through ``sys.executable -m pip install``. On the documented primary
route — ``uv tool install mokata`` — that interpreter has **no pip** (reproduced 2026-10-18:
``No module named pip``), so every extra failed and setup fell back to the floor tiers while the
user believed they had chosen full. An extra must be installed by the installer that owns the
environment, or it either fails or is silently dropped by that installer's next upgrade:

  ==========  ===============================  ===============================================
  installer   detected by                      command (extras survive the installer's upgrade)
  ==========  ===============================  ===============================================
  uv-tool     ``<prefix>/uv-receipt.toml``     ``uv tool install <receipt reqs> --with <extra>``
                                               (+ ``--with-executables-from`` for CRG)
  pipx        ``<prefix>/pipx_metadata.json``  ``pipx inject [--include-apps] mokata <reqs>``
  pip         ``import pip`` works             ``<python> -m pip install <extra's reqs>``
  uv-pip      no pip, ``uv`` on PATH           ``uv pip install --python <python> <reqs>``
  ==========  ===============================  ===============================================

Two rules that are easy to get wrong, each measured rather than assumed:

* **uv tool: rebuild from the RECEIPT, never from a guess.** ``uv tool install`` with a changed
  requirement set rebuilds the env from exactly the requirements given, so the plan re-states every
  requirement the receipt already records (the user's own ``--with`` entries, their version pin,
  their ``--python``) and adds ours. No ``--upgrade``: setup never changes mokata's version under
  the user. ``uv tool upgrade mokata`` afterwards keeps the extras (measured 0.0.22 → 0.0.24).
  A receipt this module cannot faithfully re-state (constraints, overrides, an editable source)
  gets NO argv — the human is shown the command instead (§7g: unsure is not a yes).
* **pip / pipx: install the extra's REQUIREMENTS, not ``mokata[extra]``.** Naming mokata itself
  makes pip re-resolve mokata (an unpinned spec upgrades it; a pinned one fails for any build not
  on PyPI, e.g. a dev checkout). The extra's requirement lines come from mokata's own installed
  metadata, so they cannot drift from ``pyproject.toml``.

Windows + uv-tool: the env being rebuilt contains the running ``mokata.exe``, which Windows locks;
a rebuild there can leave a half-written env. Until the Windows leg proves otherwise the plan is
``deferred``: no argv, the exact command is printed for after setup exits.

Pure planning — nothing here spawns a process. Stdlib-only.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import metadata as _md
from importlib.util import find_spec
from pathlib import PurePath
from typing import Callable, Dict, List, Optional, Sequence, Tuple

UV_TOOL = "uv-tool"
PIPX = "pipx"
PIP = "pip"
UV_PIP = "uv-pip"
NO_INSTALLER = "none"

INSTALLERS = (UV_TOOL, PIPX, PIP, UV_PIP, NO_INSTALLER)

DIST = "mokata"

#: Optional packages whose COMMAND mokata launches (detect.py `command` strategy, the CRG
#: client). Their executables must be exposed, not just their modules (0.0.25 I-02, measured).
EXPOSE_EXECUTABLES = ("code-review-graph",)

_UV_RECEIPT = "uv-receipt.toml"
_PIPX_METADATA = "pipx_metadata.json"


@dataclass
class InstallPlan:
    installer: str
    specs: List[str]                         # what was asked for, as given
    argv: Optional[List[str]] = None         # None -> do not run; show `display` instead
    display: str = ""                        # the one-line command a human can paste
    deferred: bool = False                   # runnable, but only after this process exits
    reason: str = ""                         # why argv is None (empty when it is runnable)
    # Further commands to run after `argv`, in order, each only if the previous succeeded. pipx
    # needs two: `--include-apps` refuses a package with no app of its own (model2vec), so the
    # libraries and code-review-graph's command go in separately. `display` shows them all.
    then: List[List[str]] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return self.argv is not None and not self.deferred


@dataclass
class _Req:
    name: str
    extras: Tuple[str, ...] = ()
    specifier: str = ""
    url: str = ""                            # PEP 508 direct reference, already rendered

    def render(self) -> str:
        s = self.name + (f"[{','.join(self.extras)}]" if self.extras else "")
        if self.url:
            return f"{s} @ {self.url}"
        return s + self.specifier

    def key(self) -> Tuple[str, Tuple[str, ...]]:
        return (_canon(self.name), tuple(sorted(_canon(e) for e in self.extras)))


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ------------------------------------------------------------------ detection
def detect_installer(prefix: Optional[str] = None,
                     which: Callable[[str], Optional[str]] = shutil.which,
                     has_pip: Optional[bool] = None) -> str:
    """Which installer owns the environment mokata is running from. Markers before probes: a uv
    tool env and a pipx venv are named by files their installer writes, which is exact."""
    prefix = sys.prefix if prefix is None else prefix
    if os.path.isfile(os.path.join(prefix, _UV_RECEIPT)):
        return UV_TOOL
    if os.path.isfile(os.path.join(prefix, _PIPX_METADATA)):
        return PIPX
    if has_pip is None:
        has_pip = find_spec("pip") is not None
    if has_pip:
        return PIP
    if which("uv"):
        return UV_PIP
    return NO_INSTALLER


# ------------------------------------------------------------------ the extra's own requirements
_EXTRA_CLAUSE = re.compile(r"""\s*(?:and\s+)?extra\s*==\s*['"]([^'"]+)['"]\s*(?:and\s+)?""")


def extra_requirements(extra: str, requires: Optional[Sequence[str]] = None) -> List[str]:
    """mokata's own `Requires-Dist` lines for `extra`, with the `extra == …` clause removed and any
    other marker kept. Read from the INSTALLED metadata so it cannot drift from pyproject."""
    if requires is None:
        try:
            requires = _md.requires(DIST) or []
        except _md.PackageNotFoundError:
            requires = []
    out: List[str] = []
    for line in requires:
        req, _, marker = line.partition(";")
        m = _EXTRA_CLAUSE.search(marker)
        if not m or _canon(m.group(1)) != _canon(extra):
            continue
        rest = _EXTRA_CLAUSE.sub(" ", marker).strip()
        rest = re.sub(r"^(and|or)\s+|\s+(and|or)$", "", rest).strip()
        out.append(req.strip() + (f"; {rest}" if rest else ""))
    return out


_SELF_EXTRA = re.compile(r"^\s*mokata\s*\[([^\]]+)\]\s*$", re.I)


def _expand_self_extras(specs: Sequence[str],
                        requires: Optional[Sequence[str]] = None) -> List[str]:
    out: List[str] = []
    for spec in specs:
        m = _SELF_EXTRA.match(spec)
        if not m:
            out.append(spec)
            continue
        for extra in (e.strip() for e in m.group(1).split(",")):
            out.extend(r for r in extra_requirements(extra, requires) if r not in out)
    return out


# ------------------------------------------------------------------ the uv receipt
def _load_toml(path: str) -> Optional[dict]:
    try:
        with open(path, "rb") as fh:
            return tomllib.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _receipt_reqs(entry: dict) -> Optional[_Req]:
    """One receipt requirement -> a _Req, or None when it cannot be re-stated faithfully."""
    name = entry.get("name")
    if not isinstance(name, str) or entry.get("editable"):
        return None
    extras = tuple(entry.get("extras") or ())
    url = ""
    if entry.get("git"):
        url = "git+" + str(entry["git"])
    elif entry.get("url"):
        url = str(entry["url"])
    elif entry.get("directory") or entry.get("path"):
        p = PurePath(str(entry.get("directory") or entry.get("path")))
        if not p.is_absolute():
            return None                                   # a relative source — don't guess its base
        url = p.as_uri()
    elif any(k not in ("name", "extras", "specifier") for k in entry):
        return None                                       # an unknown source shape — don't guess
    return _Req(name=name, extras=extras, specifier=str(entry.get("specifier") or ""), url=url)


def _uv_tool_argv(prefix: str, specs: Sequence[str], uv: str) -> Tuple[Optional[List[str]], str]:
    """(argv, reason). argv None means: show the command, do not run it."""
    data = _load_toml(os.path.join(prefix, _UV_RECEIPT))
    if data is None:
        return None, "could not read uv's tool receipt"
    tool = data.get("tool") or {}
    if tool.get("constraints") or tool.get("overrides") or tool.get("build-constraint-dependencies"):
        return None, "the uv tool receipt carries constraints/overrides this plan does not re-state"
    reqs: List[_Req] = []
    for entry in tool.get("requirements") or []:
        r = _receipt_reqs(entry) if isinstance(entry, dict) else None
        if r is None:
            return None, f"the uv tool receipt has a requirement this plan cannot re-state: {entry!r}"
        reqs.append(r)
    mains = [r for r in reqs if _canon(r.name) == DIST and not r.extras]
    if not mains:
        return None, "the uv tool receipt does not name mokata as its tool"
    main = mains[0]
    withs: List[str] = []
    seen = {main.key()}
    for r in reqs:
        if r.key() in seen:
            continue
        seen.add(r.key())
        withs.append(r.render())
    for spec in specs:
        if spec not in withs:
            withs.append(spec)
    argv = [uv, "tool", "install", main.render()]
    py = tool.get("python")
    if isinstance(py, str) and py:
        argv += ["--python", py]
    for w in withs:
        argv += ["--with", w]
    # Executables: the ones the receipt already exposes from other packages, plus ours.
    exposed = []
    for ep in tool.get("entrypoints") or []:
        frm = ep.get("from") if isinstance(ep, dict) else None
        if isinstance(frm, str) and _canon(frm) != DIST and frm not in exposed:
            exposed.append(frm)
    for spec in specs:
        name = re.split(r"[\[<>=!~;@ ]", spec, 1)[0]
        if _canon(name) in {_canon(x) for x in EXPOSE_EXECUTABLES} and name not in exposed:
            exposed.append(name)
    for e in exposed:
        argv += ["--with-executables-from", e]
    return argv, ""


# ------------------------------------------------------------------ the plan
#: An argument needs no quoting in cmd.exe, PowerShell OR a POSIX shell only when it is made of
#: these. Everything else is quoted (`[`/`]` are PowerShell wildcards, `<`/`>` are redirections in
#: cmd and every shell, `,` builds an array in PowerShell, `;` ends a statement).
_SHELL_SAFE = re.compile(r"^[A-Za-z0-9_\-./\\:+]+$")


def quote_arg(arg: str, platform: Optional[str] = None) -> str:
    """One argument, quoted so the line runs AS PRINTED in this platform's shells.

    0.0.26 L1 (`WIN-FIX-LINE-POSIX-QUOTED`, doc 84): until 0.0.25 every printed line went through
    `shlex.quote`, whose single quotes cmd.exe passes through LITERALLY — Jas's Windows run got
    `'mokata[embeddings]'` and the line failed as printed. Windows: double quotes, which cmd and
    PowerShell both strip (with `\"` for an inner quote, the CommandLineToArgvW rule). POSIX:
    `shlex.quote`."""
    platform = platform or sys.platform
    if arg and _SHELL_SAFE.match(arg):
        return arg
    if platform.startswith("win"):
        return '"' + arg.replace('"', '\\"') + '"'
    return shlex.quote(arg)


def _display(argv: Sequence[str], platform: Optional[str] = None) -> str:
    """A pasteable line. The executable is shown by its bare name when it is the one on PATH."""
    shown = list(argv)
    if shown and os.path.basename(shown[0]).split(".")[0] in ("uv", "pipx"):
        shown[0] = os.path.basename(shown[0]).split(".")[0]
    return " ".join(quote_arg(a, platform) for a in shown)


def plan_install(specs: Sequence[str], *,
                 prefix: Optional[str] = None,
                 executable: Optional[str] = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 has_pip: Optional[bool] = None,
                 platform: Optional[str] = None,
                 requires: Optional[Sequence[str]] = None) -> InstallPlan:
    """The install plan for `specs` under the installer that owns THIS environment. Never raises,
    never spawns. `requires` is mokata's Requires-Dist (injectable for tests)."""
    prefix = sys.prefix if prefix is None else prefix
    executable = executable or sys.executable
    platform = platform or sys.platform
    specs = list(specs)
    inst = detect_installer(prefix, which=which, has_pip=has_pip)

    if inst == UV_TOOL:
        uv = which("uv") or "uv"
        argv, reason = _uv_tool_argv(prefix, specs, uv)
        if argv is None:
            fallback = ["uv", "tool", "install", "mokata"] + [x for s in specs for x in ("--with", s)]
            return InstallPlan(UV_TOOL, specs, None, _display(fallback, platform), reason=reason)
        if not which("uv"):
            return InstallPlan(UV_TOOL, specs, None, _display(argv, platform),
                               reason="`uv` is not on PATH from this process")
        if platform.startswith("win"):
            return InstallPlan(UV_TOOL, specs, argv, _display(argv, platform), deferred=True,
                               reason="Windows locks the running mokata.exe; run this after "
                                      "setup exits")
        return InstallPlan(UV_TOOL, specs, argv, _display(argv, platform))

    reqs = _expand_self_extras(specs, requires)
    if not reqs:
        return InstallPlan(inst, specs, None, "",
                           reason="mokata's installed metadata names no requirements for "
                                  + ", ".join(specs))

    if inst == PIPX:
        # `pipx inject --include-apps` exits 1 when ANY injected package has no app of its own
        # ("No apps associated with package model2vec" — install-e2e, pipx leg, 0.0.25 cut). So a
        # package whose command must reach PATH (code-review-graph) is injected on its own with
        # the flag, and the libraries go in a plain inject first.
        pipx = which("pipx")
        exposed = {_canon(x) for x in EXPOSE_EXECUTABLES}
        apps = [r for r in reqs if _canon(re.split(r"[\[<>=!~;@ ]", r, 1)[0]) in exposed]
        libs = [r for r in reqs if r not in apps]
        cmds = []
        if libs:
            cmds.append([pipx or "pipx", "inject", DIST, *libs])
        if apps:
            cmds.append([pipx or "pipx", "inject", "--include-apps", DIST, *apps])
        shown = " && ".join(_display(c, platform) for c in cmds)
        if not pipx:
            return InstallPlan(PIPX, specs, None, shown,
                               reason="`pipx` is not on PATH from this process")
        return InstallPlan(PIPX, specs, cmds[0], shown, then=cmds[1:])

    if inst == PIP:
        argv = [executable, "-m", "pip", "install", *reqs]
        return InstallPlan(PIP, specs, argv, _display(argv, platform))

    if inst == UV_PIP:
        argv = [which("uv") or "uv", "pip", "install", "--python", executable, *reqs]
        return InstallPlan(UV_PIP, specs, argv, _display(argv, platform))

    show = [executable, "-m", "ensurepip"]
    return InstallPlan(NO_INSTALLER, specs, None,
                       _display(show, platform) + " && " + _display([executable, "-m", "pip", "install", *reqs], platform),
                       reason="this environment has no pip and no `uv` on PATH")

# ------------------------------------------------------------------ mokata WITH an extra (0.0.26 L1)
def _with_extra(req: _Req, extra: str) -> _Req:
    extras = tuple(req.extras) + (() if extra in req.extras else (extra,))
    return _Req(name=req.name, extras=extras, specifier=req.specifier, url=req.url)


def _uv_tool_line(prefix: str, extra: str, drop_pin: bool = False) -> Optional[List[str]]:
    """`uv tool install "mokata[<extra>]"` re-stating the receipt (python, pin, source, the user's
    own `--with`s) so the rebuild changes nothing but the extra. None when the receipt cannot be
    re-stated faithfully — the caller then shows the bare line."""
    data = _load_toml(os.path.join(prefix, _UV_RECEIPT))
    tool = (data or {}).get("tool") or {}
    if not tool or tool.get("constraints") or tool.get("overrides") \
            or tool.get("build-constraint-dependencies"):
        return None
    reqs: List[_Req] = []
    for entry in tool.get("requirements") or []:
        r = _receipt_reqs(entry) if isinstance(entry, dict) else None
        if r is None:
            return None
        reqs.append(r)
    mains = [r for r in reqs if _canon(r.name) == DIST]
    if not mains:
        return None
    main = _with_extra(mains[0], extra) if extra else mains[0]
    if drop_pin and main.specifier.strip().startswith("=="):
        main = _Req(name=main.name, extras=main.extras, specifier="", url=main.url)
    argv = ["uv", "tool", "install", main.render()]
    py = tool.get("python")
    if isinstance(py, str) and py:
        argv += ["--python", py]
    for r in reqs:
        if r is not mains[0] and _canon(r.name) != DIST:
            argv += ["--with", r.render()]
    return argv


def _pipx_main_package(prefix: str) -> Optional[str]:
    """What the user gave `pipx install` (a name, a path, a URL), from pipx's own metadata."""
    import json
    try:
        with open(os.path.join(prefix, _PIPX_METADATA), encoding="utf-8") as fh:
            main = (json.load(fh) or {}).get("main_package") or {}
    except (OSError, ValueError):
        return None
    spec = main.get("package_or_url")
    return spec if isinstance(spec, str) and spec else None


def plan_extra_install(extra: str, *,
                       prefix: Optional[str] = None,
                       executable: Optional[str] = None,
                       which: Callable[[str], Optional[str]] = shutil.which,
                       has_pip: Optional[bool] = None,
                       platform: Optional[str] = None,
                       requires: Optional[Sequence[str]] = None) -> InstallPlan:
    """The ONE line that gives THIS environment `mokata[<extra>]`, by the installer that owns it.

    0.0.26 L1 (`FULL-INSTALLS-EVERYTHING-IN-ONE-GO`). DISPLAY ONLY — `argv` is always None:
    setup never rebuilds the environment it is running from (on Windows that env holds the locked
    `mokata.exe`; that self-rebuild is what left Jas's full setup "PARTIALLY wired"). The human
    runs this line, then `mokata setup claude --mode <m>` again, which only detects and wires.

      uv-tool  ``uv tool install "mokata[full]"`` (+ the receipt's python / pin / source / --with)
               — uv rebuilds a tool whose requirements changed (measured 2026-10-21).
      pipx     ``pipx install --force "mokata[full]"`` — pipx records the extra, so
               ``pipx upgrade`` keeps it (an `inject` would not be re-applied from the spec).
      pip      ``<python> -m pip install <the extra's requirements>`` — never ``mokata[...]``:
               pip would re-resolve mokata itself (install_route's pip rule).
      uv-pip   ``uv pip install --python <python> <requirements>``.
    """
    prefix = sys.prefix if prefix is None else prefix
    executable = executable or sys.executable
    platform = platform or sys.platform
    inst = detect_installer(prefix, which=which, has_pip=has_pip)
    me = f"{DIST}[{extra}]"
    if inst == UV_TOOL:
        argv = _uv_tool_line(prefix, extra) or ["uv", "tool", "install", me]
        return InstallPlan(UV_TOOL, [me], None, _display(argv, platform))
    if inst == PIPX:
        src = _pipx_main_package(prefix) or DIST
        if src.startswith(("git+", "http:", "https:")):
            spec = f"{me} @ {src}"
        elif os.path.isabs(src) or src.startswith("."):
            spec = f"{src}[{extra}]"                      # a local path: `<path>[full]`
        else:
            spec = me                                     # PyPI: never re-pin what pipx resolved
        return InstallPlan(PIPX, [me], None, _display(["pipx", "install", "--force", spec], platform))
    reqs = extra_requirements(extra, requires)
    if inst == UV_PIP and reqs:
        return InstallPlan(UV_PIP, [me], None, _display(
            ["uv", "pip", "install", "--python", executable, *reqs], platform))
    if inst == PIP and reqs:
        return InstallPlan(PIP, [me], None, _display(
            [executable, "-m", "pip", "install", *reqs], platform))
    # No readable metadata (a source tree on PYTHONPATH), or no installer at all: the documented
    # line. It names mokata itself, which is right for a fresh environment.
    return InstallPlan(inst, [me], None, _display([executable, "-m", "pip", "install", me], platform),
                       reason="" if inst in (PIP, UV_PIP) else
                       "this environment has no pip and no `uv` on PATH")


# ------------------------------------------------------------------ upgrading mokata itself
def _editable_source(dist: str = DIST) -> Optional[str]:
    """The source directory when mokata is an editable (dev checkout) install, else None."""
    try:
        found = _md.distribution(dist)
    except _md.PackageNotFoundError:
        return None
    path = next((f for f in (found.files or ()) if f.name == "direct_url.json"), None)
    if path is None:
        return None
    try:
        with open(found.locate_file(path), encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    if not raw:
        return None
    import json
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if (data.get("dir_info") or {}).get("editable"):
        url = str(data.get("url") or "")
        return url[len("file://"):] if url.startswith("file://") else (url or "?")
    return None


def installed_extras(requires: Optional[Sequence[str]] = None,
                     have: Optional[Callable[[str], bool]] = None,
                     platform: Optional[str] = None) -> List[str]:
    """The user-facing extras this environment actually HAS (every requirement of the extra is
    installed), as the smallest set that covers them: ``["full"]`` rather than ``["full",
    "embeddings", "graph-ts", "ide"]``. Read from mokata's installed metadata, so a new extra or a
    changed ``full`` needs no edit here. An extra whose requirements are all base dependencies
    (``mcp``) is not one the user chose and is left out.

    0.0.26 L2 (`MOK-UPDATE`): pip / uv pip have no record of the extras a user asked for, so the
    upgrade line re-derives them — `pip install -U mokata` would keep the old extras' packages but
    never install a package a NEW version adds to `[full]`."""
    platform = platform or sys.platform
    if requires is None:
        try:
            requires = _md.requires(DIST) or []
        except _md.PackageNotFoundError:
            requires = []
    if have is None:
        def have(name: str) -> bool:
            try:
                _md.distribution(name)
                return True
            except _md.PackageNotFoundError:
                return False

    def applies(marker: str) -> bool:
        m = re.search(r"sys_platform\s*(==|!=)\s*['\"]([^'\"]+)['\"]", marker)
        if not m:
            return True
        return (platform == m.group(2)) == (m.group(1) == "==")

    base: set = set()
    by_extra: Dict[str, set] = {}
    for line in requires:
        req, _, marker = line.partition(";")
        name = _canon(re.split(r"[\[<>=!~;@ (]", req.strip(), maxsplit=1)[0])
        m = _EXTRA_CLAUSE.search(marker)
        if not m:
            base.add(name)
            continue
        rest = _EXTRA_CLAUSE.sub(" ", marker)
        if not applies(rest):
            continue
        by_extra.setdefault(_canon(m.group(1)), set()).add(name)
    satisfied = {e: r for e, r in by_extra.items()
                 if r and not r <= base and all(have(n) for n in r)}
    kept: List[str] = []
    covered: set = set()
    for e in sorted(satisfied, key=lambda e: (-len(satisfied[e]), e)):
        if satisfied[e] <= covered:
            continue
        kept.append(e)
        covered |= satisfied[e]
    return sorted(kept)


def _pipx_metadata(prefix: str) -> dict:
    import json
    try:
        with open(os.path.join(prefix, _PIPX_METADATA), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_WIN_LOCK = ("Windows locks mokata.exe while it runs — close mokata and the IDE, then run this")


def plan_upgrade(*, prefix: Optional[str] = None,
                 executable: Optional[str] = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 has_pip: Optional[bool] = None,
                 platform: Optional[str] = None,
                 installer: Optional[str] = None,
                 editable: Optional[str] = "detect",
                 requires: Optional[Sequence[str]] = None,
                 have: Optional[Callable[[str], bool]] = None) -> InstallPlan:
    """The ONE command that upgrades mokata ITSELF and keeps its extras, by the installer that owns
    this environment (0.0.26 L2, `MOK-UPDATE`). `display` is the line a human can paste; `argv` is
    the same command resolved for `mokata update` to run on the human's yes (None when there is
    nothing to run: a source checkout, no installer, the installer not on PATH). `specs[0]` names
    what it installs, e.g. ``mokata[full]``.

    `deferred` (Windows): the environment holds the running, locked `mokata.exe` — the command
    may only run AFTER this process exits, from the detached updater (`update.launch_detached`).

      uv-tool  ``uv tool upgrade mokata`` — keeps the receipt's extras / --with (measured 0.0.22 →
               0.0.24). A pinned receipt is re-stated without the pin + ``--upgrade``.
      pipx     ``pipx upgrade mokata`` — keeps ``mokata[full]`` as installed; with injected
               packages (0.0.25's extras route) ``--include-injected`` upgrades them too.
      pip      ``<python> -m pip install --upgrade "mokata[<extras it has>]"`` — naming the extras
               is what installs a package a newer `[full]` adds.
      uv-pip   ``uv pip install --python <python> --upgrade "mokata[<extras>]"``.

    A dev checkout (editable install) is never upgraded by an installer — it is ``git pull``."""
    prefix = sys.prefix if prefix is None else prefix
    executable = executable or sys.executable
    platform = platform or sys.platform
    win = platform.startswith("win")
    src = _editable_source() if editable == "detect" else editable
    inst = installer or detect_installer(prefix, which=which, has_pip=has_pip)
    if src:
        return InstallPlan(inst, [DIST], None, "git pull",
                           reason=f"this is an editable install of a source checkout ({src}) — "
                                  "update it with git, not an installer")

    def plan(me: str, argv: List[str], tool: Optional[str], reason: str = "") -> InstallPlan:
        shown = _display(argv, platform)
        if tool is not None:
            exe = which(tool)
            if not exe:
                return InstallPlan(inst, [me], None, shown,
                                   reason=f"`{tool}` is not on PATH from this process")
            argv = [exe] + argv[1:]
        return InstallPlan(inst, [me], argv, shown, deferred=win,
                           reason=reason + (". " if reason and win else "")
                           + (_WIN_LOCK if win else ""))

    if inst == UV_TOOL:
        data = _load_toml(os.path.join(prefix, _UV_RECEIPT)) or {}
        tool = data.get("tool") or {}
        main = None
        for entry in tool.get("requirements") or []:
            if isinstance(entry, dict) and _canon(str(entry.get("name", ""))) == DIST:
                main = entry
                break
        extras = tuple((main or {}).get("extras") or ())
        me = _Req(DIST, extras=extras).render()
        spec = str((main or {}).get("specifier") or "").strip()
        if spec.startswith("=="):
            # `uv tool upgrade` honours the pin, so it would upgrade nothing. Re-state the receipt
            # (python, source, --with) without the pin; extras stay because they are re-stated.
            argv = _uv_tool_line(prefix, "", drop_pin=True) or ["uv", "tool", "install", me]
            return plan(me, argv + ["--upgrade"], "uv",
                        f"your uv tool install pins mokata{spec}; `uv tool upgrade` keeps a pin — "
                        "this reinstalls without it")
        return plan(me, ["uv", "tool", "upgrade", DIST], "uv")
    if inst == PIPX:
        meta = _pipx_metadata(prefix)
        main = meta.get("main_package") or {}
        m = _SELF_EXTRA.match(str(main.get("package_or_url") or ""))
        extras = tuple(e.strip() for e in m.group(1).split(",")) if m else ()
        me = _Req(DIST, extras=extras).render()
        argv = ["pipx", "upgrade", DIST]
        if meta.get("injected_packages"):
            argv = ["pipx", "upgrade", "--include-injected", DIST]
        return plan(me, argv, "pipx")
    extras = tuple(installed_extras(requires, have, platform))
    me = _Req(DIST, extras=extras).render()
    if inst == PIP:
        return plan(me, [executable, "-m", "pip", "install", "--upgrade", me], None)
    if inst == UV_PIP:
        return plan(me, ["uv", "pip", "install", "--python", executable, "--upgrade", me], "uv")
    return InstallPlan(NO_INSTALLER, [me], None,
                       _display(["uv", "tool", "install", me, "--upgrade"], platform),
                       reason="this environment has no pip and no `uv` on PATH")


__all__ = [
    "UV_TOOL", "PIPX", "PIP", "UV_PIP", "NO_INSTALLER", "INSTALLERS",
    "InstallPlan", "detect_installer", "extra_requirements", "installed_extras",
    "plan_extra_install", "plan_install", "plan_upgrade", "quote_arg",
]
