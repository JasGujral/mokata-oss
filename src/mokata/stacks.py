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


class StackError(Exception):
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
)

_SPECS_BY_NAME: Dict[str, StackSpec] = {s.name: s for s in _STARTER_SPECS}


def starter_stack_names() -> List[str]:
    return [s.name for s in _STARTER_SPECS]


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
