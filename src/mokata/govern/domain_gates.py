"""Lane D2 (0.0.25) — the frontend/backend DOMAIN GATES: checks with an exit code, run on a repo.

Doc 85 §4: a skill Contract may cite a gate only if it is BACKED — a real tool or mokata-owned
script that exits non-zero on a violation, with a detection fixture proving it fires. This module is
the enforcement point for the domain gates `skill_contracts.GATES` registers, and `mokata gate check
<id>` is the surface that runs them (the reachability pin asks for exactly that: a surface that RUNS
the gate, not a module that merely exists).

Two kinds of backing live here:

  * MOKATA-OWNED, stdlib — `dep-cooldown` (package-manager release-age config + git/URL specs that
    bypass it) and `route-guarded` (a static scan of NestJS controllers). The check IS this code.
  * TOOL-BACKED — `bundle-budget` (size-limit / `lhci assert`), `react-lint` (`eslint --max-warnings
    0` with eslint-plugin-react-hooks), `bundle-secret-scan` (`gitleaks dir`), `dep-vulnerability`
    (`npm audit --audit-level=high`). mokata detects whether the repo is configured for the tool,
    runs it bounded, and propagates its exit code. A tool or config that is ABSENT is reported as
    NOT CONFIGURED — its own state, exit 3, never a pass.

`dep-vulnerability`'s osv-scanner route is wired but ADVISORY: its "exit 1 on findings" semantics
could not be verified where this was built (the OSV API was unreachable), so a run through it is
evaluated and reported as would-block, never as a block. `render-regression` has no tool that exits
non-zero and is not run here at all — it is an advisory boundary in the registry.

Verdicts go through `enforce.decide_gate` (the 0.0.23 D3 enforcement ladder) — the rule Sentinel,
not a second one. What each gate CANNOT judge is stated in its result: `route-guarded` proves a guard
is attached, never that the guard's policy is right; that stays a human review.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..quiet_spawn import hidden
from ..repo_paths import name_of

# ------------------------------------------------------------------------------- states & exits
PASS = "pass"
FAIL = "fail"
ERROR = "error"                       # the check could not complete — never a pass
NOT_CONFIGURED = "not-configured"     # the repo is not set up for this gate — never a pass

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_CONFIGURED = 3               # branch_protection's EXIT_DEGRADED precedent: distinct from FAIL

#: Bound on every tool spawn. A hang is an ERROR, never a pass.
TOOL_TIMEOUT_SECONDS = 600.0

#: The cooldown floor `dep-cooldown` requires when none is given: three days between a version's
#: publication and the first install that may resolve to it.
DEFAULT_MIN_COOLDOWN_DAYS = 3.0

#: Gate ids this module runs. `render-regression` is deliberately absent (advisory, no tool).
DOMAIN_GATE_IDS = ("dep-cooldown", "route-guarded", "bundle-budget", "react-lint",
                   "bundle-secret-scan", "dep-vulnerability")
ADVISORY_DOMAIN_GATE_IDS = ("render-regression",)

_EXCLUDED_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".next", "out", "coverage",
                            ".turbo", ".cache"})


@dataclass
class GateCheck:
    """One domain gate's raw check. `state` is pass | fail | error | not-configured; `findings` name
    each violation; `backing` is the command (or mokata check) that produced the state; `advisory`
    marks a result from a route whose exit semantics are unverified (evaluated, never blocking)."""

    gate_id: str
    state: str
    summary: str
    findings: List[str] = field(default_factory=list)
    backing: str = ""
    advisory: bool = False
    caveat: str = ""

    @property
    def passed(self) -> bool:
        return self.state == PASS


@dataclass
class GateRun:
    """A decided run: the raw check plus the Sentinel's verdict and the process exit code."""

    check: GateCheck
    exit_code: int
    message: str


# ------------------------------------------------------------------------------- small readers
def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _read_package_json(root: str) -> Optional[dict]:
    text = _read_text(os.path.join(root, "package.json"))
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _search_path(root: str, path: Optional[str]) -> str:
    """Where tools are looked up: the repo's own `node_modules/.bin` first, then `path` (or PATH)."""
    local_bin = os.path.join(root, "node_modules", ".bin")
    rest = path if path is not None else os.environ.get("PATH", "")
    return os.pathsep.join(p for p in (local_bin, rest) if p)


def _which(name: str, root: str, path: Optional[str]) -> Optional[str]:
    return shutil.which(name, path=_search_path(root, path))


def _run_tool(argv: Sequence[str], cwd: str,
              timeout: float = TOOL_TIMEOUT_SECONDS) -> Tuple[Optional[int], str]:
    """Run a tool bounded and captured. (returncode, tail-of-output); returncode None = could not
    run or timed out (the text says which)."""
    try:
        proc = subprocess.run(list(argv), cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              **hidden())
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return None, f"could not launch {argv[0]}: {exc}"
    lines = [ln for ln in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
             if ln.strip()]
    return proc.returncode, "\n".join(lines[-15:])


# =============================================================================== dep-cooldown
#: Dependency spec prefixes that resolve OUTSIDE the registry, so no release-age gate applies.
_BYPASS_PREFIXES = ("git+", "git:", "git://", "github:", "gitlab:", "bitbucket:", "gist:",
                    "http://", "https://")
#: npm's bare `owner/repo[#ref]` shorthand is a GitHub dependency. A scoped name never reaches here
#: (it is a KEY); values starting with `@` are not shorthand.
_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+(#.*)?$")
_DEP_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*$", re.IGNORECASE)
_MINUTES_PER = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440, "w": 10080}


def _bypass_specs(pkg: dict) -> List[str]:
    out: List[str] = []
    for section in _DEP_SECTIONS + ("overrides", "resolutions"):
        deps = pkg.get(section)
        if not isinstance(deps, dict):
            continue
        for name, spec in sorted(deps.items()):
            if not isinstance(spec, str):
                continue
            s = spec.strip()
            if s.lower().startswith(_BYPASS_PREFIXES) or _GITHUB_SHORTHAND.match(s):
                out.append(f"{section}.{name} = {s!r} resolves outside the registry — no "
                           f"release-age gate applies to it")
    pnpm = pkg.get("pnpm")
    if isinstance(pnpm, dict) and isinstance(pnpm.get("overrides"), dict):
        out.extend(_bypass_specs({"pnpm.overrides": pnpm["overrides"]}))
    return out


def _ini_value(text: str, keys: Sequence[str]) -> Optional[str]:
    """The last `key=value` in an .npmrc for any of `keys` (later lines win, as npm reads them)."""
    found: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() in keys:
            found = v.strip().strip("'\"")
    return found


def _yaml_top_value(text: str, key: str) -> Optional[str]:
    """A TOP-LEVEL scalar `key: value` from a YAML file (no nesting needed for these settings)."""
    pat = re.compile(rf"^{re.escape(key)}\s*:\s*(.+?)\s*(?:#.*)?$")
    for raw in text.splitlines():
        if raw[:1].isspace():
            continue
        m = pat.match(raw)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def _to_minutes(value: str, bare_unit: str) -> Optional[float]:
    m = _DURATION.match(value or "")
    if not m:
        return None
    unit = (m.group(2) or bare_unit).lower()
    return float(m.group(1)) * _MINUTES_PER[unit]


def _detect_managers(root: str, pkg: dict) -> List[str]:
    found = []
    if os.path.isfile(os.path.join(root, "pnpm-lock.yaml")) or \
            os.path.isfile(os.path.join(root, "pnpm-workspace.yaml")):
        found.append("pnpm")
    if os.path.isfile(os.path.join(root, "yarn.lock")) or \
            os.path.isfile(os.path.join(root, ".yarnrc.yml")):
        found.append("yarn")
    if os.path.isfile(os.path.join(root, "package-lock.json")) or \
            os.path.isfile(os.path.join(root, "npm-shrinkwrap.json")):
        found.append("npm")
    if not found:
        declared = str(pkg.get("packageManager") or "").split("@", 1)[0].strip()
        found.append(declared if declared in ("npm", "pnpm", "yarn") else "npm")
    return found


def _cooldown_minutes(root: str, manager: str) -> Tuple[Optional[float], str]:
    """(configured cooldown in minutes or None, where it was read). Units per manager's docs:
    npm `min-release-age` is days; pnpm `minimumReleaseAge` is minutes; Yarn `npmMinimalAgeGate`
    is a duration (a bare number is minutes)."""
    npmrc = _read_text(os.path.join(root, ".npmrc")) or ""
    if manager == "npm":
        v = _ini_value(npmrc, ("min-release-age",))
        return (_to_minutes(v, "d") if v is not None else None), ".npmrc min-release-age"
    if manager == "pnpm":
        ws = _read_text(os.path.join(root, "pnpm-workspace.yaml")) or ""
        v = _yaml_top_value(ws, "minimumReleaseAge")
        if v is not None:
            return _to_minutes(v, "m"), "pnpm-workspace.yaml minimumReleaseAge"
        v = _ini_value(npmrc, ("minimum-release-age", "minimumReleaseAge"))
        return (_to_minutes(v, "m") if v is not None else None), ".npmrc minimum-release-age"
    yarnrc = _read_text(os.path.join(root, ".yarnrc.yml")) or ""
    v = _yaml_top_value(yarnrc, "npmMinimalAgeGate")
    return (_to_minutes(v, "m") if v is not None else None), ".yarnrc.yml npmMinimalAgeGate"


def check_dep_cooldown(root: str, min_days: float = DEFAULT_MIN_COOLDOWN_DAYS) -> GateCheck:
    """FAIL when the repo's package manager has no release-age cooldown of at least `min_days`, or
    any dependency is a git / `github:` / URL spec (those resolve outside the registry, so no age
    gate can apply). NOT CONFIGURED when there is no package.json."""
    gid = "dep-cooldown"
    backing = "mokata dep-cooldown check (stdlib: .npmrc / pnpm-workspace.yaml / .yarnrc.yml)"
    pkg = _read_package_json(root)
    if pkg is None:
        return GateCheck(gid, NOT_CONFIGURED, "no readable package.json — not a JS/TS project",
                         backing=backing)
    findings: List[str] = []
    floor = min_days * 1440
    for manager in _detect_managers(root, pkg):
        minutes, source = _cooldown_minutes(root, manager)
        if minutes is None:
            findings.append(f"{manager}: no release-age cooldown configured ({source} unset or "
                            f"unreadable)")
        elif minutes < floor:
            findings.append(f"{manager}: cooldown {minutes / 1440:g} day(s) from {source} is below "
                            f"the {min_days:g}-day floor")
    findings.extend(_bypass_specs(pkg))
    if findings:
        return GateCheck(gid, FAIL, f"{len(findings)} cooldown violation(s)", findings,
                         backing=backing)
    return GateCheck(gid, PASS, f"release-age cooldown >= {min_days:g} day(s); no registry-bypassing "
                     "dependency specs", backing=backing)


# =============================================================================== route-guarded
_ROUTE_DECORATORS = frozenset({"Get", "Post", "Put", "Patch", "Delete", "Options", "Head", "All",
                               "Search"})
_GUARD_DECORATORS = frozenset({"UseGuards"})
_PUBLIC_DECORATORS = frozenset({"Public"})
_MODIFIERS = frozenset({"public", "private", "protected", "static", "async", "readonly", "get",
                        "set", "override", "abstract", "declare"})
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_GLOBAL_GUARD = re.compile(r"\.useGlobalGuards\s*\(|provide\s*:\s*APP_GUARD\b")
_COMPOSITE = re.compile(
    r"(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=)")


def _strip_comments(src: str) -> str:
    """Blank out // and /* */ comments, keeping strings and every newline (line numbers hold)."""
    out = []
    i, n = 0, len(src)
    quote: Optional[str] = None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _skip_balanced(s: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket matching s[i] == open_ch (strings respected)."""
    depth = 0
    quote: Optional[str] = None
    n = len(s)
    while i < n:
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _members(body: str, base: int) -> List[Tuple[str, List[str], int, Dict[str, str]]]:
    """Class members at depth 1: (name, decorator names, absolute offset, decorator args)."""
    out = []
    i, n = 0, len(body)
    decos: List[str] = []
    args: Dict[str, str] = {}
    while i < n:
        c = body[i]
        if c.isspace() or c == ";" or c == ",":
            i += 1
            continue
        if c == "@":
            m = _IDENT.match(body, i + 1)
            if not m:
                i += 1
                continue
            name = m.group(0)
            j = m.end()
            while j < n and body[j] == ".":          # @Foo.Bar(...)
                m2 = _IDENT.match(body, j + 1)
                if not m2:
                    break
                name, j = m2.group(0), m2.end()
            k = j
            while k < n and body[k].isspace():
                k += 1
            arg = ""
            if k < n and body[k] == "(":
                end = _skip_balanced(body, k, "(", ")")
                arg, j = body[k + 1:end - 1].strip(), end
            decos.append(name)
            args.setdefault(name, arg)
            i = j
            continue
        start = i
        member_name = ""
        # read the member's head up to its body `{` or terminating `;`, at paren depth 0
        while i < n:
            ch = body[i]
            if ch in "'\"`":
                i = _skip_string(body, i)
                continue
            if ch == "(":
                i = _skip_balanced(body, i, "(", ")")
                continue
            if ch == "{":
                i = _skip_balanced(body, i, "{", "}")
                break
            if ch == ";":
                i += 1
                break
            if not member_name:
                m = _IDENT.match(body, i)
                if m:
                    if m.group(0) not in _MODIFIERS:
                        member_name = m.group(0)
                    i = m.end()
                    continue
            i += 1
        out.append((member_name, decos, base + start, args))
        decos, args = [], {}
    return out


def _skip_string(s: str, i: int) -> int:
    q = s[i]
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == q:
            return i + 1
        i += 1
    return i


def _ts_files(root: str) -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".ts") and not fn.endswith((".d.ts", ".spec.ts", ".test.ts")):
                out.append(os.path.join(dirpath, fn))
    return out


def _composite_guard_decorators(sources: Dict[str, str]) -> set:
    """Names of custom decorators built with `applyDecorators(... UseGuards(...) ...)` — a route
    carrying one IS guarded, and a static scan that ignored them would cry wolf on idiomatic code."""
    names = set()
    for text in sources.values():
        for m in re.finditer(r"applyDecorators\s*\(", text):
            end = _skip_balanced(text, m.end() - 1, "(", ")")
            if "UseGuards" not in text[m.start():end]:
                continue
            head = text[max(0, m.start() - 400):m.start()]
            defs = list(_COMPOSITE.finditer(head))
            if defs:
                names.add(defs[-1].group(1) or defs[-1].group(2))
    return names


def check_route_guarded(root: str) -> GateCheck:
    """FAIL naming every NestJS route handler (`@Get/@Post/...`) that has no guard: no `@UseGuards`
    (or a composite decorator wrapping it) on the handler or its controller class, no explicit
    `@Public()`, and no global guard (`app.useGlobalGuards(...)` / an `APP_GUARD` provider).
    Proves a guard is ATTACHED — never that the guard's policy is correct (a human review)."""
    gid = "route-guarded"
    backing = "mokata route-guarded static scan (stdlib, NestJS controllers)"
    caveat = ("attachment only — whether each guard's authorization policy is RIGHT stays a human "
              "review (OWASP A01)")
    sources: Dict[str, str] = {}
    for path in _ts_files(root):
        text = _read_text(path)
        if text is not None:
            sources[path] = _strip_comments(text)
    controllers = {p: t for p, t in sources.items() if re.search(r"@Controller\s*\(", t)}
    if not controllers:
        return GateCheck(gid, NOT_CONFIGURED, "no NestJS @Controller found under the repo",
                         backing=backing, caveat=caveat)
    global_guard = [str(name_of(p, root)) for p, t in sorted(sources.items())
                    if _GLOBAL_GUARD.search(t)]
    guard_names = _GUARD_DECORATORS | _composite_guard_decorators(sources)
    routes = 0
    findings: List[str] = []
    for path, text in sorted(controllers.items()):
        rel = str(name_of(path, root))
        for cm in re.finditer(r"@Controller\s*\(", text):
            cls = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)[^{]*\{").search(text, cm.end())
            if not cls:
                continue
            # the class's decorator block: from the end of the previous statement (a `;` or `}`)
            # to `class` — so a guard stacked ABOVE `@Controller` counts as well as one below it
            head_from = max(text.rfind(";", 0, cm.start()), text.rfind("}", 0, cm.start())) + 1
            class_decos = {m.group(1) for m in re.finditer(r"@([A-Za-z_$][\w$]*)",
                                                           text[head_from:cls.start()])}
            class_ok = bool(class_decos & (guard_names | _PUBLIC_DECORATORS))
            open_at = cls.end() - 1
            close_at = _skip_balanced(text, open_at, "{", "}")
            for name, decos, off, args in _members(text[open_at + 1:close_at - 1], open_at + 1):
                verbs = [d for d in decos if d in _ROUTE_DECORATORS]
                if not verbs:
                    continue
                routes += 1
                if class_ok or global_guard or set(decos) & (guard_names | _PUBLIC_DECORATORS):
                    continue
                line = text.count("\n", 0, off) + 1
                findings.append(f"{rel}:{line} {cls.group(1)}.{name or '?'} "
                                f"(@{verbs[0]}({args.get(verbs[0], '')})) has no guard and is "
                                "not @Public()")
    if findings:
        return GateCheck(gid, FAIL, f"{len(findings)} of {routes} route handler(s) unguarded",
                         findings, backing=backing, caveat=caveat)
    how = f"global guard in {', '.join(global_guard)}" if global_guard else "per-route/class"
    return GateCheck(gid, PASS, f"all {routes} route handler(s) guarded or @Public() ({how})",
                     backing=backing, caveat=caveat)


# =============================================================================== bundle-budget
_SIZE_LIMIT_FILES = (".size-limit.json", ".size-limit.js", ".size-limit.cjs", ".size-limit.mjs",
                     ".size-limit.ts")
_LHCI_FILES = ("lighthouserc.js", "lighthouserc.cjs", "lighthouserc.json", "lighthouserc.yml",
               "lighthouserc.yaml", ".lighthouserc.js", ".lighthouserc.cjs", ".lighthouserc.json",
               ".lighthouserc.yml", ".lighthouserc.yaml")


def _tool_result(gid: str, rc: Optional[int], tail: str, backing: str, fail_codes: Sequence[int],
                 ok_summary: str, fail_summary: str, advisory: bool = False) -> GateCheck:
    if rc is None:
        return GateCheck(gid, ERROR, tail, backing=backing, advisory=advisory)
    if rc == 0:
        return GateCheck(gid, PASS, ok_summary, backing=backing, advisory=advisory)
    findings = [ln for ln in tail.splitlines() if ln.strip()]
    if rc in fail_codes:
        return GateCheck(gid, FAIL, f"{fail_summary} (exit {rc})", findings, backing=backing,
                         advisory=advisory)
    return GateCheck(gid, ERROR, f"tool exited {rc} — not a recognised verdict", findings,
                     backing=backing, advisory=advisory)


def check_bundle_budget(root: str, path: Optional[str] = None) -> GateCheck:
    """Run size-limit when configured (package.json `size-limit` or a `.size-limit.*` file), else
    `lhci assert` when a lighthouserc is present, and propagate the exit code. `lhci assert` reads
    results a prior `lhci collect` wrote — mokata runs no browser and no dev server."""
    gid = "bundle-budget"
    pkg = _read_package_json(root) or {}
    if "size-limit" in pkg or any(os.path.isfile(os.path.join(root, f)) for f in _SIZE_LIMIT_FILES):
        exe = _which("size-limit", root, path)
        backing = "size-limit"
        if exe is None:
            return GateCheck(gid, NOT_CONFIGURED, "size-limit is configured but not installed "
                             "(npm install --save-dev size-limit)", backing=backing)
        rc, tail = _run_tool([exe], root)
        return _tool_result(gid, rc, tail, backing, (1,), "every asset within its size-limit budget",
                            "size-limit reports an asset over budget")
    if any(os.path.isfile(os.path.join(root, f)) for f in _LHCI_FILES):
        exe = _which("lhci", root, path)
        backing = "lhci assert"
        if exe is None:
            return GateCheck(gid, NOT_CONFIGURED, "a lighthouserc is present but lhci is not "
                             "installed (npm install --save-dev @lhci/cli)", backing=backing)
        rc, tail = _run_tool([exe, "assert"], root)
        return _tool_result(gid, rc, tail, backing, (1,), "every lhci assertion passed",
                            "lhci assert reports a failing assertion")
    return GateCheck(gid, NOT_CONFIGURED, "no size-limit config and no lighthouserc — no budget is "
                     "declared", backing="size-limit | lhci assert")


# =============================================================================== react-lint
_ESLINT_FILES = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
                 "eslint.config.mts", "eslint.config.cts", ".eslintrc", ".eslintrc.js",
                 ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")


def _react_hooks_configured(root: str) -> Tuple[bool, str]:
    """(react-hooks recommended is wired, the eslint config it was found in or why not)."""
    pkg = _read_package_json(root) or {}
    configs = [f for f in _ESLINT_FILES if os.path.isfile(os.path.join(root, f))]
    texts = [(f, _read_text(os.path.join(root, f)) or "") for f in configs]
    if isinstance(pkg.get("eslintConfig"), dict):
        texts.append(("package.json eslintConfig", json.dumps(pkg["eslintConfig"])))
    if not texts:
        return False, "no eslint config"
    for name, text in texts:
        if "react-hooks" in text or "reactHooks" in text:
            return True, name
    return False, f"eslint config ({', '.join(n for n, _ in texts)}) does not enable " \
                  "eslint-plugin-react-hooks"


def check_react_lint(root: str, path: Optional[str] = None) -> GateCheck:
    """Run `eslint --max-warnings 0 .` when the repo's eslint config enables
    eslint-plugin-react-hooks; propagate the exit code (any warning fails)."""
    gid = "react-lint"
    backing = "eslint --max-warnings 0 (eslint-plugin-react-hooks recommended)"
    ok, where = _react_hooks_configured(root)
    if not ok:
        return GateCheck(gid, NOT_CONFIGURED, f"{where} — react-lint is not configured",
                         backing=backing)
    exe = _which("eslint", root, path)
    if exe is None:
        return GateCheck(gid, NOT_CONFIGURED, f"eslint is configured ({where}) but not installed",
                         backing=backing)
    rc, tail = _run_tool([exe, "--max-warnings", "0", "."], root)
    return _tool_result(gid, rc, tail, backing, (1,), f"eslint clean with zero warnings ({where})",
                        "eslint reports errors or warnings")


# =============================================================================== bundle-secret-scan
#: Built-output directories scanned, in order. Only those that exist are scanned.
BUILD_OUTPUT_DIRS = ("dist", "build", "out", os.path.join(".next", "static"))


def check_bundle_secret_scan(root: str, path: Optional[str] = None) -> GateCheck:
    """`gitleaks dir <d> --exit-code 1` over every built-output directory that exists. Exit 1 =
    a credential in shipped output. A missing directory is NOT passed to gitleaks (it exits 1 on a
    missing path too, which would read as a leak)."""
    gid = "bundle-secret-scan"
    backing = "gitleaks dir <build output> --exit-code 1"
    dirs = [d for d in BUILD_OUTPUT_DIRS if os.path.isdir(os.path.join(root, d))]
    if not dirs:
        return GateCheck(gid, NOT_CONFIGURED, "no built output (dist/ build/ out/ .next/static) — "
                         "build first", backing=backing)
    exe = _which("gitleaks", root, path)
    if exe is None:
        return GateCheck(gid, NOT_CONFIGURED, "gitleaks is not installed", backing=backing)
    findings: List[str] = []
    for d in dirs:
        rc, tail = _run_tool([exe, "dir", os.path.join(root, d), "--no-banner", "--redact",
                              "--exit-code", "1"], root)
        if rc is None or rc not in (0, 1):
            return GateCheck(gid, ERROR, f"gitleaks on {d}: {tail if rc is None else f'exit {rc}'}",
                             backing=backing)
        if rc == 1:
            findings.append(f"{d}: gitleaks found a credential in built output")
            findings.extend(ln for ln in tail.splitlines() if "leak" in ln.lower())
    if findings:
        return GateCheck(gid, FAIL, "credential(s) in built output", findings, backing=backing)
    return GateCheck(gid, PASS, f"no credential in {', '.join(dirs)}", backing=backing)


# =============================================================================== dep-vulnerability
def check_dep_vulnerability(root: str, path: Optional[str] = None) -> GateCheck:
    """`npm audit --audit-level=high` when an npm lockfile exists (verified: exit 1 on a
    high/critical advisory, 0 on none). Otherwise, when osv-scanner is installed and a lockfile
    exists, `osv-scanner scan source -r .` — wired but ADVISORY (exit semantics unverified)."""
    gid = "dep-vulnerability"
    if any(os.path.isfile(os.path.join(root, f)) for f in ("package-lock.json",
                                                            "npm-shrinkwrap.json")):
        backing = "npm audit --audit-level=high"
        exe = _which("npm", root, path)
        if exe is None:
            return GateCheck(gid, NOT_CONFIGURED, "npm lockfile present but npm is not installed",
                             backing=backing)
        rc, tail = _run_tool([exe, "audit", "--audit-level=high"], root)
        return _tool_result(gid, rc, tail, backing, (1,), "no high/critical advisory",
                            "npm audit reports a high/critical advisory (or could not reach the "
                            "registry — never a pass)")
    lockfiles = [f for f in ("pnpm-lock.yaml", "yarn.lock") if os.path.isfile(os.path.join(root, f))]
    if lockfiles:
        backing = "osv-scanner scan source -r . (ADVISORY route)"
        exe = _which("osv-scanner", root, path)
        if exe is None:
            return GateCheck(gid, NOT_CONFIGURED, f"{lockfiles[0]} present; the npm audit route "
                             "needs package-lock.json and osv-scanner is not installed",
                             backing=backing)
        rc, tail = _run_tool([exe, "scan", "source", "-r", "."], root)
        return _tool_result(gid, rc, tail, backing, (1,), "osv-scanner found no known vulnerability",
                            "osv-scanner reports vulnerabilities", advisory=True)
    return GateCheck(gid, NOT_CONFIGURED, "no JS lockfile to audit", backing="npm audit")


# =============================================================================== the decider
def run_domain_gate(gate_id: str, root: str, *, path: Optional[str] = None,
                    min_days: float = DEFAULT_MIN_COOLDOWN_DAYS) -> GateRun:
    """Run one domain gate on `root` and decide it through the D3 Sentinel. Each gate is called
    by name (no dispatch table) so the reachability pin sees a structural path to every one."""
    root = os.path.abspath(root)
    if gate_id == "dep-cooldown":
        check = check_dep_cooldown(root, min_days)
    elif gate_id == "route-guarded":
        check = check_route_guarded(root)
    elif gate_id == "bundle-budget":
        check = check_bundle_budget(root, path)
    elif gate_id == "react-lint":
        check = check_react_lint(root, path)
    elif gate_id == "bundle-secret-scan":
        check = check_bundle_secret_scan(root, path)
    elif gate_id == "dep-vulnerability":
        check = check_dep_vulnerability(root, path)
    elif gate_id in ADVISORY_DOMAIN_GATE_IDS:
        check = GateCheck(gate_id, NOT_CONFIGURED,
                          "advisory boundary — no tool exits non-zero on a render regression; "
                          "render counts are a human review (no Contract may cite this gate)")
        return GateRun(check, EXIT_NOT_CONFIGURED, f"[ADVISORY] {gate_id} — {check.summary}")
    else:
        raise ValueError(f"unknown domain gate {gate_id!r}; one of "
                         f"{DOMAIN_GATE_IDS + ADVISORY_DOMAIN_GATE_IDS}")
    return decide_domain_gate(check)


def decide_domain_gate(check: GateCheck) -> GateRun:
    """Map a raw check to a verdict and an exit code. NOT CONFIGURED is its own state (exit 3) and
    never reaches the Sentinel as a pass. A FAIL or ERROR is decided HARD (exit 1) unless the route
    is advisory, which the Sentinel evaluates as would-block (exit 0)."""
    if check.state == NOT_CONFIGURED:
        return GateRun(check, EXIT_NOT_CONFIGURED,
                       f"[NOT CONFIGURED] {check.gate_id} — {check.summary}")
    from ..skills import Gate
    from .enforce import decide_gate
    gate = Gate(check.gate_id, check.summary, kind="check",
                enforcement="advisory" if check.advisory else "hard")
    reason = check.summary if not check.findings else f"{check.summary}: {check.findings[0]}"
    decision = decide_gate(gate, check.passed, reason=reason)
    return GateRun(check, EXIT_PASS if decision.allowed else EXIT_FAIL, decision.message)


def render_run(run: GateRun) -> str:
    c = run.check
    lines = [run.message]
    for f in c.findings:
        lines.append(f"  - {f}")
    if c.backing:
        lines.append(f"  backing: {c.backing}")
    if c.caveat:
        lines.append(f"  not judged: {c.caveat}")
    return "\n".join(lines)


__all__ = [
    "ADVISORY_DOMAIN_GATE_IDS", "BUILD_OUTPUT_DIRS", "DEFAULT_MIN_COOLDOWN_DAYS", "DOMAIN_GATE_IDS",
    "ERROR", "EXIT_FAIL", "EXIT_NOT_CONFIGURED", "EXIT_PASS", "FAIL", "GateCheck", "GateRun",
    "NOT_CONFIGURED", "PASS", "check_bundle_budget", "check_bundle_secret_scan",
    "check_dep_cooldown", "check_dep_vulnerability", "check_react_lint", "check_route_guarded",
    "decide_domain_gate", "render_run", "run_domain_gate",
]
