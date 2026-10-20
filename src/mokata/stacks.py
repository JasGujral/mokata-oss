"""Stage 70 — community stacks & skill marketplace.

HONEST SCOPE — mokata runs **NO hosted marketplace**. There is no registry service, no
account, nothing phoned home. "Marketplace" here is three existing primitives composed:

  * PUBLISH — a governed per-framework STACK (a schema-valid manifest = governed config +
    a curated rule/guardrail set + the relevant skills) is shared exactly like any J3 stack:
    `mokata export` it, commit it to a git repo, or push it to the design vault. Others ADOPT
    it with the Stage-69 gated pull pattern (secret-scanned + human-gated).

  * DISCOVER — a versioned, curated `index.json` (this module's `build_index()` ships one
    in-package; the SAME format also lives as committed files under `mokata/stacks/`, and any
    git org / vault can host one) lists the available stacks. `mokata stacks list/search/show`
    read it — cheap, read-only, degrade-clean (no index / no source → a clear message).

  * INSTALL — `mokata stacks install <name>` is the **gated adopt path**: the stack's manifest
    is secret-scanned (community content is UNTRUSTED) then applied through the human-gated
    WriteGate/`apply_manifest`. Decline → nothing is wired. A secret in a stack → hard-blocked.

Nothing new is hosted or rebuilt: publish reuses `export_manifest`; install reuses the
`scan` + `apply_manifest` primitives the Stage-69 `team_adopt` is built on. Core stays
dependency-free; clean-room; Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .manifest import Manifest
from .profiles import build_manifest_data
from .errors import MokataError

# The curated catalog is versioned so a consumer can reason about compatibility. This is the
# INDEX format version (not a mokata release); it changes only if the index shape changes.
STACK_INDEX_VERSION = 1
INDEX_KIND = "mokata-stack-index"
INDEX_FILENAME = "index.json"

# The mokata version the shipped starter stacks were AUTHORED against. Kept as a stable
# constant (NOT the live `__version__`) so the committed mirror + the code generator never
# drift release-to-release — adopting a stack records this exactly like adopting any shared
# J3 stack records its author's version (informational; `mokata version` reads the installed
# package, never the manifest).
STACK_AUTHORED_VERSION = "0.0.5"

# The one honest sentence reused in output + docs, so the "no hosted marketplace" promise is
# stated at the surface, not just in docs.
HONEST_NOTE = (
    "mokata runs no hosted marketplace — stacks publish over git/the vault and install is the "
    "same human-gated, secret-scanned adopt path as any shared stack. Nothing is phoned home."
)


class StackError(MokataError):
    """A malformed curated index, an unknown stack, or an unreadable source (degrade-clean)."""


# ============================================================================ curated catalog
# Each starter stack: a per-framework governed CONFIG (a standard-profile manifest) + a curated
# guardrail set + the relevant pipeline skills. The guardrails/skills travel into the adopter's
# committed manifest under `settings.stack` — reviewable config, promotable to enforced,
# typed guardrails via `/mokata:onboard`. Clean-room: mokata's own words.
@dataclass(frozen=True)
class StackSpec:
    name: str
    framework: str
    summary: str
    base_profile: str
    guardrails: Tuple[str, ...]
    skills: Tuple[str, ...]
    tags: Tuple[str, ...]
    # 0.0.25 Lane D — which side of the wire the stack governs ("frontend" | "backend"), the domain
    # skills it brings (routed per spec by the classifier — the stack only declares them), and the
    # toolchain CANDIDATES `detect_toolchain` chooses between in a real repo. Declared, never
    # vendored: mokata installs none of these tools.
    kind: str = "backend"
    domains: Tuple[str, ...] = ()
    toolchain: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()


_COMMON_SKILLS = ("brainstorm", "spec", "test", "develop", "review", "ship")

_STARTER_SPECS: Tuple[StackSpec, ...] = (
    StackSpec(
        name="python-web",
        framework="Python web (FastAPI / Django / Flask)",
        summary="Governed stack for a Python web service — migrations, secrets-in-env, "
                "typed boundaries, tests-first.",
        base_profile="standard",
        guardrails=(
            "Never inline a credential or DSN — reference an env var; the secret-guard "
            "hard-blocks a secret in any committed file.",
            "A schema/DB change ships with a migration and a test that exercises it.",
            "Validate and type every request boundary (pydantic / serializers) before use.",
            "No new endpoint or behaviour without a failing test that demands it (RED first).",
        ),
        skills=_COMMON_SKILLS,
        tags=("python", "web", "api", "fastapi", "django", "flask"),
    ),
    StackSpec(
        name="node-ts",
        framework="Node.js + TypeScript",
        summary="Governed stack for a TypeScript service — strict types, no any-escapes, "
                "env-based config, tested boundaries.",
        base_profile="standard",
        guardrails=(
            "`strict` TypeScript stays on; a new `any` needs an explicit, reviewed reason.",
            "Configuration and secrets come from the environment, never a committed file.",
            "Public functions carry explicit return types; validate external input at the edge.",
            "No implementation before a failing test expresses the behaviour (RED before GREEN).",
        ),
        skills=_COMMON_SKILLS,
        tags=("node", "typescript", "ts", "javascript", "backend"),
    ),
    StackSpec(
        name="go-service",
        framework="Go service",
        summary="Governed stack for a Go service — errors handled explicitly, table-driven "
                "tests, env config, no swallowed failures.",
        base_profile="standard",
        guardrails=(
            "Every returned error is handled or wrapped with context — never discarded with `_`.",
            "Config and credentials read from the environment; nothing sensitive in the repo.",
            "Cover behaviour with table-driven tests; a new path needs a failing test first.",
            "Keep the exported surface small and documented; guard concurrency with the race "
            "detector in CI.",
        ),
        skills=_COMMON_SKILLS,
        tags=("go", "golang", "service", "backend"),
    ),
    StackSpec(
        name="react-web",
        framework="React (web) + TypeScript",
        summary="Governed stack for a React web app — component tests first, pure renders, "
                "authorization on every server entry point, measured performance.",
        base_profile="standard",
        guardrails=(
            "A component's behaviour lands only after a failing test that queries it by role or "
            "label (RED before GREEN).",
            "Every Server Function and route handler authorizes the caller for the object it "
            "touches; hiding a button is not access control.",
            "No secret carries a client-exposed env prefix; the built output is checked, not "
            "only the git history.",
            "A performance change is kept only with a before/after measurement on a production "
            "build.",
        ),
        skills=_COMMON_SKILLS,
        tags=("react", "web", "frontend", "typescript", "javascript", "jsx", "tsx"),
        kind="frontend",
        domains=("frontend-react", "frontend-a11y", "frontend-perf", "frontend-security"),
        toolchain=(("test_runner", ("vitest", "jest")), ("linter", ("oxlint", "eslint")),
                   ("type_checker", ("tsc",))),
    ),
    StackSpec(
        name="nestjs-api",
        framework="NestJS + TypeScript",
        summary="Governed stack for a NestJS API — guards on every route, runtime-validated DTOs, "
                "walked contract changes, tests through the testing module.",
        base_profile="standard",
        guardrails=(
            "Every route handler has a guard or an explicit, reviewed public marker; "
            "authorization never lives only in middleware.",
            "DTOs are runtime classes or schemas (never `import type`), validated with unknown "
            "fields stripped and rejected.",
            "A controller, provider or DTO contract change is walked to its callers before it "
            "lands.",
            "No behaviour without a failing test through the testing module or the HTTP layer "
            "(RED before GREEN).",
        ),
        skills=_COMMON_SKILLS,
        tags=("nestjs", "nest", "node", "typescript", "ts", "backend", "api"),
        kind="backend",
        domains=("backend-nestjs", "api", "security"),
        toolchain=(("test_runner", ("vitest", "jest")), ("linter", ("oxlint", "eslint")),
                   ("type_checker", ("tsc",))),
    ),
)

_SPECS_BY_NAME: Dict[str, StackSpec] = {s.name: s for s in _STARTER_SPECS}


def starter_stack_names() -> List[str]:
    return [s.name for s in _STARTER_SPECS]


# ============================================================================ JS/TS project facts
# 0.0.25 Lane D — the FRAMEWORK a touched file belongs to, read from its nearest `package.json`.
# These are STRUCTURAL facts about the repo (what the project declares it depends on), never a
# reading of the prompt: the domain classifier needs them to tell a React WEB component from a
# React Native one (both are `.tsx`), and a NestJS controller from an Express handler (both are
# `.ts` files named `*.controller.ts` in the wild). Read-only, stdlib-only, bounded (one parse per
# package dir, cached by the caller), degrade-clean (an unreadable or absent manifest is "no facts",
# so a framework-gated domain does NOT fire — never a guess).
FW_REACT_WEB = "react-web"
FW_REACT_NATIVE = "react-native"
FW_NESTJS = "nestjs"
FW_EXPRESS = "express"

PACKAGE_JSON = "package.json"
_DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies")
# A package that renders to the DOM. `next` ships its own renderer dependency, so it counts alone.
_REACT_WEB_DEPS = ("react-dom", "next")
# A React Native app (Expo included) is a different platform: no DOM, no web bundle, no
# Server Components — the React web rule pack does not apply to it.
_REACT_NATIVE_DEPS = ("react-native", "expo")
_NEST_DEPS = ("@nestjs/core",)


def read_package_json(directory: str) -> Optional[Dict[str, Any]]:
    """The parsed `package.json` in `directory`, or None (absent, unreadable, not an object)."""
    path = os.path.join(directory, PACKAGE_JSON)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def package_deps(pkg: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """Every dependency name the manifest declares (runtime + dev + peer), sorted."""
    out: set = set()
    for fld in _DEP_FIELDS:
        block = (pkg or {}).get(fld)
        if isinstance(block, dict):
            out.update(str(k) for k in block)
    return tuple(sorted(out))


def frameworks_of_package(pkg: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """The framework facts one `package.json` establishes. React Native EXCLUDES React web (an Expo
    app may list `react-dom` for its web target, but its components are native components); NestJS
    EXCLUDES plain Express (Nest pulls Express in through its platform adapter)."""
    deps = set(package_deps(pkg))
    out: List[str] = []
    native = any(d in deps for d in _REACT_NATIVE_DEPS)
    if native:
        out.append(FW_REACT_NATIVE)
    elif any(d in deps for d in _REACT_WEB_DEPS):
        out.append(FW_REACT_WEB)
    nest = any(d in deps for d in _NEST_DEPS)
    if nest:
        out.append(FW_NESTJS)
    elif "express" in deps:
        out.append(FW_EXPRESS)
    return tuple(out)


def nearest_package_dir(root: str, path: str) -> Optional[str]:
    """The closest directory at or above `path` (relative to `root`, or absolute under it) that
    holds a `package.json`, never climbing above `root`. None when there is none."""
    from .repo_paths import escapes_root, name_of, path_of
    if not root:
        return None
    root_abs = os.path.abspath(root)
    p = path if os.path.isabs(path) else path_of(root_abs, path)
    cur = os.path.dirname(os.path.abspath(p))
    while True:
        try:
            name = name_of(cur, root_abs)
        except ValueError:                    # another drive on Windows — not under the repo
            return None
        if name != "." and escapes_root(name):
            return None                       # outside the repo — never read above the root
        if os.path.isfile(os.path.join(cur, PACKAGE_JSON)):
            return cur
        if cur == root_abs:
            return None
        cur = os.path.dirname(cur)


def frameworks_for_file(root: str, path: str,
                        cache: Optional[Dict[str, Tuple[str, ...]]] = None) -> Tuple[str, ...]:
    """The framework facts of the project `path` belongs to (its nearest `package.json`), or ()
    when there is no manifest. `cache` maps a package dir to its facts so a surface of N files in
    one package parses the manifest once."""
    pdir = nearest_package_dir(root, path)
    if pdir is None:
        return ()
    if cache is not None and pdir in cache:
        return cache[pdir]
    facts = frameworks_of_package(read_package_json(pdir))
    if cache is not None:
        cache[pdir] = facts
    return facts


# ============================================================================ detect (suggest only)
# 0.0.25 Lane D — which starter stack a repo's packages look like, and the toolchain each package
# actually uses. SUGGEST-ONLY (doc 103 §8.4): it reads, prints, and installs nothing; adopting a
# stack is still `mokata stacks install`, the gated path. Bounded walk, stdlib only.
_FRAMEWORK_STACK = {FW_REACT_WEB: "react-web", FW_NESTJS: "nestjs-api"}
_SKIP_DIRS = frozenset({"node_modules", ".git", ".mokata", "dist", "build", ".next", "coverage",
                        ".venv", "venv", "__pycache__"})
DETECT_MAX_DEPTH = 3          # package dirs this deep below the root are considered
DETECT_MAX_PACKAGES = 50      # and at most this many manifests are parsed

_RUNNER_CONFIGS = {"vitest": ("vitest.config.ts", "vitest.config.mts", "vitest.config.js",
                              "vitest.config.mjs", "vitest.workspace.ts"),
                   "jest": ("jest.config.ts", "jest.config.js", "jest.config.mjs",
                            "jest.config.cjs", "jest.config.json")}
_LINTER_CONFIGS = {"oxlint": (".oxlintrc.json", "oxlint.json", "oxlintrc.json"),
                   "eslint": ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
                              "eslint.config.ts", ".eslintrc", ".eslintrc.js", ".eslintrc.cjs",
                              ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")}


@dataclass(frozen=True)
class Toolchain:
    """What one package uses. `test_runner` is "vitest" | "jest" | "" (none found); `linters` lists
    every linter the package wires (oxlint and eslint are commonly run together); `type_checker`
    is "tsc" when the package carries TypeScript, else ""."""

    test_runner: str
    linters: Tuple[str, ...]
    type_checker: str

    def to_dict(self) -> Dict[str, Any]:
        return {"test_runner": self.test_runner, "linters": list(self.linters),
                "type_checker": self.type_checker}


def _script(pkg: Optional[Dict[str, Any]], name: str) -> str:
    scripts = (pkg or {}).get("scripts")
    val = scripts.get(name) if isinstance(scripts, dict) else None
    return val if isinstance(val, str) else ""


def _has_any(directory: str, names: Tuple[str, ...]) -> bool:
    return any(os.path.isfile(os.path.join(directory, n)) for n in names)


def detect_toolchain(directory: str, pkg: Optional[Dict[str, Any]] = None) -> Toolchain:
    """The test runner, linters and type checker one package uses. Evidence order, strongest
    first: what its `test`/`lint` script RUNS, then a config file on disk, then a declared
    dependency. A runner named by the test script wins outright; with no script evidence, a config
    file beats a bare dependency; a tie at the same level resolves in candidate order (vitest, then
    jest) and is reported as that — never as both."""
    pkg = read_package_json(directory) if pkg is None else pkg
    deps = set(package_deps(pkg))
    test_script = _script(pkg, "test")
    runner = ""
    for level in ("script", "config", "dep"):
        for cand in ("vitest", "jest"):
            hit = ((level == "script" and cand in test_script)
                   or (level == "config" and (_has_any(directory, _RUNNER_CONFIGS[cand])
                                              or (cand == "jest" and "jest" in (pkg or {}))))
                   or (level == "dep" and cand in deps))
            if hit:
                runner = cand
                break
        if runner:
            break
    lint_script = _script(pkg, "lint")
    linters = tuple(c for c in ("oxlint", "eslint")
                    if c in lint_script or c in deps or _has_any(directory, _LINTER_CONFIGS[c]))
    checker = "tsc" if ("typescript" in deps or os.path.isfile(
        os.path.join(directory, "tsconfig.json"))) else ""
    return Toolchain(test_runner=runner, linters=linters, type_checker=checker)


@dataclass(frozen=True)
class StackDetection:
    """One package that looks like a starter stack: where it is (repo-relative, `/`-spelled, "."
    for the root), which stack, which side of the wire, and its toolchain."""

    stack: str
    path: str
    kind: str
    toolchain: Toolchain

    def to_dict(self) -> Dict[str, Any]:
        return {"stack": self.stack, "path": self.path, "kind": self.kind,
                "toolchain": self.toolchain.to_dict()}

    def line(self) -> str:
        tc = self.toolchain
        return (f"{self.stack} ({self.kind}) at {self.path} · test: {tc.test_runner or 'none'}"
                f" · lint: {', '.join(tc.linters) or 'none'} · types: {tc.type_checker or 'none'}")


def _package_dirs(root: str) -> List[str]:
    from .repo_paths import name_of, split_path
    out: List[str] = []
    root_abs = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root_abs):
        rel = name_of(dirpath, root_abs)
        depth = 0 if rel == "." else len(split_path(rel))
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        if depth >= DETECT_MAX_DEPTH:
            dirnames[:] = []
        if PACKAGE_JSON in filenames:
            out.append(dirpath)
            if len(out) >= DETECT_MAX_PACKAGES:
                break
    return out


def detect_stacks(root: str) -> List[StackDetection]:
    """Every package under `root` (bounded) whose declared framework maps to a starter stack, with
    its toolchain — React web → `react-web` (frontend), NestJS → `nestjs-api` (backend). A React
    Native app, a plain Express server or a framework-less TypeScript library is NOT suggested a
    framework stack. Read-only; an unreadable manifest is skipped."""
    from .repo_paths import name_of
    found: List[StackDetection] = []
    if not root or not os.path.isdir(root):
        return found
    root_abs = os.path.abspath(root)
    for pdir in _package_dirs(root_abs):
        pkg = read_package_json(pdir)
        if pkg is None:
            continue
        rel = str(name_of(pdir, root_abs))
        for fw in frameworks_of_package(pkg):
            name = _FRAMEWORK_STACK.get(fw)
            if name:
                found.append(StackDetection(stack=name, path=rel, kind=_SPECS_BY_NAME[name].kind,
                                            toolchain=detect_toolchain(pdir, pkg)))
    return found


# ============================================================================ manifest builder
def build_stack_manifest(name: str) -> Dict[str, Any]:
    """The governed manifest for a starter stack: the base profile's schema-valid config plus a
    `settings.stack` block carrying the framework, curated guardrails, and recommended skills.
    Deterministic and schema-valid by construction (built from `build_manifest_data`)."""
    spec = _SPECS_BY_NAME.get(name)
    if spec is None:
        raise StackError(f"unknown stack '{name}'; available: {', '.join(starter_stack_names())}")
    data = build_manifest_data(spec.base_profile, STACK_AUTHORED_VERSION)
    data.setdefault("settings", {})["stack"] = {
        "name": spec.name,
        "framework": spec.framework,
        "version": STACK_AUTHORED_VERSION,
        "summary": spec.summary,
        "guardrails": list(spec.guardrails),
        "skills": list(spec.skills),
        "tags": list(spec.tags),
        "kind": spec.kind,
        "domains": list(spec.domains),
        "toolchain": {role: list(cands) for role, cands in spec.toolchain},
    }
    return data


def _index_entry(spec: StackSpec) -> Dict[str, Any]:
    return {
        "name": spec.name,
        "framework": spec.framework,
        "summary": spec.summary,
        "version": STACK_AUTHORED_VERSION,
        "profile": spec.base_profile,
        "guardrails": len(spec.guardrails),
        "skills": list(spec.skills),
        "tags": list(spec.tags),
        "kind": spec.kind,
        "domains": list(spec.domains),
        # Where the manifest file lives in a file-backed catalog (the committed mirror + any
        # git-org/vault catalog use this same convention).
        "manifest": f"{spec.name}.json",
    }


def build_index() -> Dict[str, Any]:
    """The bundled curated index (always available, in-code — no packaging dependency)."""
    return {
        "schema_version": STACK_INDEX_VERSION,
        "kind": INDEX_KIND,
        "authored_for": STACK_AUTHORED_VERSION,
        "stacks": [_index_entry(s) for s in _STARTER_SPECS],
    }


# ============================================================================ index io (source)
def _validate_index(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or data.get("kind") != INDEX_KIND:
        raise StackError(f"not a mokata stack index (kind != '{INDEX_KIND}')")
    stacks = data.get("stacks")
    if not isinstance(stacks, list):
        raise StackError("stack index is malformed ('stacks' must be a list)")
    return data


def resolve_index_source(source: str) -> Optional[str]:
    """The path to an `index.json` for a file-backed catalog `source` (a file or a dir holding
    one). None when nothing is there (degrade-clean)."""
    if os.path.isfile(source):
        return source
    if os.path.isdir(source):
        cand = os.path.join(source, INDEX_FILENAME)
        if os.path.isfile(cand):
            return cand
    return None


def load_index(source: Optional[str] = None) -> Dict[str, Any]:
    """Load the curated index. `source=None` → the bundled in-code catalog (always available).
    A `source` (a git-org/vault checkout dir or an index.json) → that catalog, parsed +
    validated. Raises StackError with a clear message when a named source has no valid index."""
    if source is None:
        return build_index()
    path = resolve_index_source(source)
    if path is None:
        raise StackError(f"no stack index found at '{source}' (expected an {INDEX_FILENAME})")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise StackError(f"could not read the stack index '{path}': {exc}")
    return _validate_index(data)


def _stacks(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in index.get("stacks", []) if isinstance(s, dict) and s.get("name")]


# ============================================================================ list / search / show
def list_stacks(index: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every stack in the catalog, name-sorted. Cheap, read-only."""
    idx = index if index is not None else build_index()
    return sorted(_stacks(idx), key=lambda s: s["name"])


def _tokens(text: str) -> set:
    import re
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())}


@dataclass
class StackHit:
    entry: Dict[str, Any]
    score: float


def search_stacks(query: str, index: Optional[Dict[str, Any]] = None) -> List[StackHit]:
    """Lexical search over name + framework + summary + tags (token overlap), ranked. Read-only.
    Deterministic order: score DESC, then name ASC. Only matching stacks are returned."""
    q = _tokens(query)
    if not q:
        return []
    idx = index if index is not None else build_index()
    hits: List[StackHit] = []
    for entry in _stacks(idx):
        hay = _tokens(" ".join([
            entry.get("name", ""), entry.get("framework", ""), entry.get("summary", ""),
            " ".join(entry.get("tags", []) or []),
        ]))
        overlap = len(q & hay)
        if overlap:
            hits.append(StackHit(entry=entry, score=overlap / len(q | hay)))
    hits.sort(key=lambda h: (-h.score, h.entry["name"]))
    return hits


def show_stack(name: str, index: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The catalog entry for one stack, or None if absent. Read-only."""
    idx = index if index is not None else build_index()
    for entry in _stacks(idx):
        if entry.get("name") == name:
            return entry
    return None


# ============================================================================ resolve a manifest
def resolve_stack_manifest(name: str, source: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Return (raw_text, data) for a stack's manifest. Bundled (`source=None`) → built from
    code. File-backed source → read the `manifest` file named in that catalog's index entry.
    The raw text is what gets secret-scanned before any write (community content is untrusted)."""
    if source is None:
        data = build_stack_manifest(name)
        return Manifest.from_dict(data).to_json(), data
    index = load_index(source)
    entry = None
    for e in _stacks(index):
        if e.get("name") == name:
            entry = e
            break
    if entry is None:
        raise StackError(f"stack '{name}' is not in the catalog at '{source}'")
    idx_path = resolve_index_source(source)
    base = os.path.dirname(idx_path) if idx_path else source
    rel = entry.get("manifest") or f"{name}.json"
    mpath = os.path.join(base, rel)
    if not os.path.isfile(mpath):
        raise StackError(f"stack '{name}' index lists '{rel}', but that file is missing")
    try:
        with open(mpath, encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise StackError(f"could not read the stack manifest '{mpath}': {exc}")
    return raw, data


# ============================================================================ install (gated adopt)
@dataclass
class InstallResult:
    installed: bool
    aborted: bool = False
    blocked: bool = False
    name: str = ""
    findings: List[Any] = field(default_factory=list)
    path: Optional[str] = None
    message: str = ""


def install_stack(root: str, name: str, *, source: Optional[str] = None,
                  assume_yes: bool = False,
                  confirm: Optional[Callable[[str], bool]] = None,
                  force: bool = False,
                  out: Optional[Callable[[str], None]] = None) -> InstallResult:
    """Install a catalog stack as this repo's config — the SAME gated adopt path Stage 69 uses.

    Community content is UNTRUSTED, so the manifest is secret-scanned FIRST (a secret is a hard
    block — a stack must carry an env-var pointer, never a credential), then applied through the
    human-gated `apply_manifest` (declining writes nothing; an existing config needs `force`).
    """
    emit = out or (lambda *_a: None)
    emit(HONEST_NOTE)
    try:
        raw, data = resolve_stack_manifest(name, source=source)
    except StackError as exc:
        emit(str(exc))
        return InstallResult(False, name=name, message=str(exc))

    # SECURITY FIRST — scan the raw manifest text exactly like team_adopt / a vault pull.
    from .govern.secrets import scan
    findings = scan(text=raw, path=f"stack:{name}")
    if findings:
        emit(f"stacks install blocked — secret detected in stack '{name}' (a community stack "
             f"must not carry a credential; use an env-var pointer):")
        for f in findings:
            emit(f"  [{f.layer}/{f.kind}] {f.detail}")
        return InstallResult(False, blocked=True, name=name, findings=findings,
                             message="blocked: secret in stack content")

    from .share import apply_manifest
    result = apply_manifest(root, data, confirm=confirm, assume_yes=assume_yes, force=force)
    if result.errors:
        emit(f"stacks install rejected — stack '{name}' is not a valid governed manifest:")
        for e in result.errors:
            emit(f"  - {e}")
        return InstallResult(False, name=name, path=result.path,
                             message="rejected: invalid stack manifest")
    if not result.applied:
        emit(f"stacks install: {result.message}")
        return InstallResult(False, aborted=result.aborted, name=name, path=result.path,
                             message=result.message)

    stack_meta = (data.get("settings") or {}).get("stack") or {}
    fw = stack_meta.get("framework") or name
    emit(f"stacks install — wired the '{name}' stack ({fw}): governed config + "
         f"{len(stack_meta.get('guardrails') or [])} curated guardrail(s) + "
         f"{len(stack_meta.get('skills') or [])} recommended skill(s), recorded in your "
         f"manifest's settings.stack (reviewable; promote to enforced via /mokata:onboard).")
    emit("reversible: this is an audited config write — re-import a prior stack to undo.")
    return InstallResult(True, name=name, path=result.path, message="installed")
