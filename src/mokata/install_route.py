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
            raw = fh.read()
    except OSError:
        return None
    try:
        import tomllib  # type: ignore[import-not-found]   # 3.11+
    except ImportError:
        # 3.10 — the declared floor — has no stdlib TOML reader, and `uv tool install` builds the
        # env on whatever Python uv picks: measured at I-03, a fresh runner's tool env was 3.10.20
        # and every extra failed with "could not read uv's tool receipt". uv writes receipts in a
        # small, fixed subset of TOML, which `_ReceiptReader` reads.
        try:
            return _ReceiptReader(raw.decode("utf-8")).document()
        except (ValueError, UnicodeDecodeError):
            return None
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


class _ReceiptReader:
    """The TOML subset uv writes in `uv-receipt.toml`: `[table]` headers, `key = value` with keys
    bare or quoted, values that are basic/literal strings, booleans, integers, arrays and inline
    tables, and `#` comments. Anything outside that subset raises ValueError — the caller then
    shows the command instead of running it (unsure is not a yes)."""

    _ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}

    def __init__(self, text: str) -> None:
        self.s, self.i = text, 0

    def _ws(self, newlines: bool = True) -> None:
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r" or (newlines and c == "\n"):
                self.i += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                return

    def _peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _expect(self, ch: str) -> None:
        if self._peek() != ch:
            raise ValueError(f"expected {ch!r} at {self.i}")
        self.i += 1

    def _key(self) -> str:
        c = self._peek()
        if c in "\"'":
            return self._string()
        m = re.match(r"[A-Za-z0-9_-]+", self.s[self.i:])
        if not m:
            raise ValueError(f"bad key at {self.i}")
        self.i += m.end()
        return m.group(0)

    def _string(self) -> str:
        q = self._peek()
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                raise ValueError("unterminated string")
            c = self.s[self.i]
            self.i += 1
            if c == q:
                return "".join(out)
            if c == "\\" and q == '"':
                e = self.s[self.i:self.i + 1]
                self.i += 1
                if e == "u" or e == "U":
                    n = 4 if e == "u" else 8
                    out.append(chr(int(self.s[self.i:self.i + n], 16)))
                    self.i += n
                elif e in self._ESC:
                    out.append(self._ESC[e])
                else:
                    raise ValueError(f"bad escape \\{e}")
            else:
                out.append(c)

    def _value(self):
        self._ws()
        c = self._peek()
        if c in "\"'":
            return self._string()
        if c == "[":
            self.i += 1
            items = []
            while True:
                self._ws()
                if self._peek() == "]":
                    self.i += 1
                    return items
                items.append(self._value())
                self._ws()
                if self._peek() == ",":
                    self.i += 1
        if c == "{":
            self.i += 1
            table = {}
            while True:
                self._ws(newlines=False)
                if self._peek() == "}":
                    self.i += 1
                    return table
                k = self._key()
                self._ws(newlines=False)
                self._expect("=")
                table[k] = self._value()
                self._ws(newlines=False)
                if self._peek() == ",":
                    self.i += 1
        for word, val in (("true", True), ("false", False)):
            if self.s.startswith(word, self.i):
                self.i += len(word)
                return val
        m = re.match(r"[+-]?\d+", self.s[self.i:])
        if m:
            self.i += m.end()
            return int(m.group(0))
        raise ValueError(f"unsupported value at {self.i}")

    def document(self) -> dict:
        doc: dict = {}
        cur = doc
        while True:
            self._ws()
            if self.i >= len(self.s):
                return doc
            if self._peek() == "[":
                self.i += 1
                if self._peek() == "[":
                    raise ValueError("arrays of tables are not in the receipt subset")
                cur = doc
                while True:
                    self._ws(newlines=False)
                    cur = cur.setdefault(self._key(), {})
                    self._ws(newlines=False)
                    if self._peek() == ".":
                        self.i += 1
                        continue
                    self._expect("]")
                    break
                continue
            k = self._key()
            self._ws(newlines=False)
            self._expect("=")
            cur[k] = self._value()


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
def _display(argv: Sequence[str]) -> str:
    """A pasteable line. The executable is shown by its bare name when it is the one on PATH."""
    shown = list(argv)
    if shown and os.path.basename(shown[0]).split(".")[0] in ("uv", "pipx"):
        shown[0] = os.path.basename(shown[0]).split(".")[0]
    return " ".join(shlex.quote(a) for a in shown)


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
            return InstallPlan(UV_TOOL, specs, None, _display(fallback), reason=reason)
        if not which("uv"):
            return InstallPlan(UV_TOOL, specs, None, _display(argv),
                               reason="`uv` is not on PATH from this process")
        if platform.startswith("win"):
            return InstallPlan(UV_TOOL, specs, argv, _display(argv), deferred=True,
                               reason="Windows locks the running mokata.exe; run this after "
                                      "setup exits")
        return InstallPlan(UV_TOOL, specs, argv, _display(argv))

    reqs = _expand_self_extras(specs, requires)
    if not reqs:
        return InstallPlan(inst, specs, None, "",
                           reason="mokata's installed metadata names no requirements for "
                                  + ", ".join(specs))

    if inst == PIPX:
        pipx = which("pipx")
        argv = [pipx or "pipx", "inject", DIST, *reqs]
        if any(_canon(re.split(r"[\[<>=!~;@ ]", r, 1)[0]) in
               {_canon(x) for x in EXPOSE_EXECUTABLES} for r in reqs):
            argv.insert(2, "--include-apps")
        if not pipx:
            return InstallPlan(PIPX, specs, None, _display(argv),
                               reason="`pipx` is not on PATH from this process")
        return InstallPlan(PIPX, specs, argv, _display(argv))

    if inst == PIP:
        argv = [executable, "-m", "pip", "install", *reqs]
        return InstallPlan(PIP, specs, argv, _display(argv))

    if inst == UV_PIP:
        argv = [which("uv") or "uv", "pip", "install", "--python", executable, *reqs]
        return InstallPlan(UV_PIP, specs, argv, _display(argv))

    show = [executable, "-m", "ensurepip"]
    return InstallPlan(NO_INSTALLER, specs, None,
                       _display(show) + " && " + _display([executable, "-m", "pip", "install", *reqs]),
                       reason="this environment has no pip and no `uv` on PATH")

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


def plan_upgrade(*, prefix: Optional[str] = None,
                 executable: Optional[str] = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 has_pip: Optional[bool] = None,
                 platform: Optional[str] = None,
                 installer: Optional[str] = None,
                 editable: Optional[str] = "detect") -> InstallPlan:
    """How to upgrade mokata ITSELF, by the installer that owns this environment (0.0.25).

    Until 0.0.25 `mokata upgrade` always ran `sys.executable -m pip install -U mokata`, which on
    the documented primary route (`uv tool install mokata`) has no pip and failed. Each installer
    upgrades in its own way, and each keeps the extras it installed (measured 0.0.22 → 0.0.24 for
    `uv tool upgrade`; `pipx upgrade` keeps injected packages; pip leaves other packages alone).

    A dev checkout (editable install) is never upgraded for the user — it is their working tree.
    Windows + uv tool is `deferred`: the running mokata.exe is locked (same rule as extras)."""
    prefix = sys.prefix if prefix is None else prefix
    executable = executable or sys.executable
    platform = platform or sys.platform
    src = _editable_source() if editable == "detect" else editable
    if src:
        return InstallPlan(installer or detect_installer(prefix, which=which, has_pip=has_pip),
                           [DIST], None, "git pull",
                           reason=f"this is an editable install of a source checkout ({src}) — "
                                  "update it with git, not an installer")
    inst = installer or detect_installer(prefix, which=which, has_pip=has_pip)
    if inst == UV_TOOL:
        uv = which("uv")
        argv = [uv or "uv", "tool", "upgrade", DIST]
        data = _load_toml(os.path.join(prefix, _UV_RECEIPT)) or {}
        for entry in (data.get("tool") or {}).get("requirements") or []:
            if isinstance(entry, dict) and _canon(str(entry.get("name", ""))) == DIST \
                    and str(entry.get("specifier", "")).strip().startswith("=="):
                return InstallPlan(UV_TOOL, [DIST], None, "uv tool install mokata --upgrade",
                                   reason=f"your uv tool install pins mokata{entry['specifier']};"
                                          " `uv tool upgrade` keeps a pin — reinstall without it")
        if not uv:
            return InstallPlan(UV_TOOL, [DIST], None, _display(argv),
                               reason="`uv` is not on PATH from this process")
        if platform.startswith("win"):
            return InstallPlan(UV_TOOL, [DIST], argv, _display(argv), deferred=True,
                               reason="Windows locks the running mokata.exe; close mokata "
                                      "(and the IDE), then run this")
        return InstallPlan(UV_TOOL, [DIST], argv, _display(argv))
    if inst == PIPX:
        pipx = which("pipx")
        argv = [pipx or "pipx", "upgrade", DIST]
        if not pipx:
            return InstallPlan(PIPX, [DIST], None, _display(argv),
                               reason="`pipx` is not on PATH from this process")
        return InstallPlan(PIPX, [DIST], argv, _display(argv))
    if inst == PIP:
        argv = [executable, "-m", "pip", "install", "--upgrade", DIST]
        return InstallPlan(PIP, [DIST], argv, _display(argv))
    if inst == UV_PIP:
        argv = [which("uv") or "uv", "pip", "install", "--python", executable, "--upgrade", DIST]
        return InstallPlan(UV_PIP, [DIST], argv, _display(argv))
    return InstallPlan(NO_INSTALLER, [DIST], None, "uv tool install mokata --upgrade",
                       reason="this environment has no pip and no `uv` on PATH")


__all__ = [
    "UV_TOOL", "PIPX", "PIP", "UV_PIP", "NO_INSTALLER", "INSTALLERS",
    "InstallPlan", "detect_installer", "extra_requirements", "plan_install", "plan_upgrade",
]
