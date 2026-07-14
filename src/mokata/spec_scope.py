"""SI-DEV — the spec's MACHINE-CHECKABLE scope, and the amend record (0.0.13).

The incident this exists for: the agent built batch-update/delete although the saved spec explicitly
DEFERRED it, treating the user's instruction as authorization. SK.S3's protocol said ask-and-amend
before out-of-scope work — prose, which did not bind. Nothing persisted marked scope; nothing
blocked the write. This module is the persisted, decidable form of "what this spec may touch", so
the gate hook can turn an out-of-scope write into an exit code.

What is HONESTLY decidable (the verdict this design is built on)
---------------------------------------------------------------
A `PreToolUse` hook has the target path, the CONTENT about to be written (the envelope carries
`tool_input.content` / `new_string` — see `gate_hook.target_content`), the persisted state, and a
millisecond budget. From that it can decide exactly two things:

    * **path-glob membership** — is this file inside what the spec authorized, or inside what it
      explicitly deferred?
    * **literal marker presence** — does the text being written spell a token the spec named as
      deferred?

It CANNOT decide "does this diff semantically implement a deferred feature". No hook can. So the
scope is not inferred, it is DECLARED: the human approves it at emit, and the hook enforces exactly
what was declared — nothing more. Markers are what catch the realistic incident shape, where the
deferred feature is added to a file that is otherwise perfectly authorized (as it was: the batch
endpoint went into the same module as the single-item ones).

Where it refuses to decide, it FAILS OPEN — the SI.1 rule, unchanged:

    no `scope` section (a spec predating SI-DEV)   -> ALLOW   nothing was declared
    a `scope` that is absent/corrupt/unparseable   -> ALLOW   a broken map is not a violation
    `authorized` empty, and no deferred hit        -> ALLOW   mokata has no map for this path
    otherwise                                      -> decide

"`authorized` empty means allow" is the honest reading of "unmapped": a path can only be *outside*
an authorized set if there IS one. A spec that declares no authorized paths has drawn no map, so
there is nothing to be outside of — but its explicit NEGATIVES (the deferred items) still bind,
because those were declared.

Globs are `fnmatch`-style over the repo-relative POSIX path, and `*` DOES cross `/` (so
`src/api/*` covers `src/api/v2/items.py`). That is deliberate: a spec's authorized surface is
usually a subtree, and the alternative surprises people into thinking they are covered when they
are not.

The scope shape (additive to the spec's persisted JSON — no schema change)
--------------------------------------------------------------------------
    "scope": {
      "authorized": ["src/api/items.py", "src/api/serializers.py"],
      "deferred": [{"id": "D1", "item": "batch update/delete",
                    "paths": ["src/api/batch*.py"], "markers": ["batch_update", "bulk_delete"]}]
    }

Stdlib-only, and deliberately so: `gate_hook` imports this module, and its import surface is part of
SI.1's latency contract (stdlib + `state` + `tdd_state` + this). No engine, no govern, no config.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Set, Tuple

# The spec's additive scope key, and the two SI-DEV state keys. Siblings of SI.1's
# `gate_override__<run_id>` and SI.2's `tdd_phase__<run_id>` — the run id is IN the name, so both
# are session-scoped for free and pass through a `SessionScopedStore` verbatim.
#
# NEITHER prefix may start with `emitted_spec__`, `approved_approach__` or `tdd_phase__`: those are
# the prefixes `gate_hook._run_ids` scans to enumerate RUNS, so a key like `emitted_spec__<rid>__v1`
# would be parsed as a run called "<rid>__v1" — a phantom second run, which would make every write
# AMBIGUOUS and silently switch the gates off. Hence `spec_archive__`, not `emitted_spec__…__v1`.
SCOPE_KEY = "scope"
AMEND_PREFIX = "spec_amend__"           # the forced-regression marker: spec_amend__<run_id>
ARCHIVE_PREFIX = "spec_archive__"       # a superseded spec:            spec_archive__<run_id>__v<N>

# The amendment's lifecycle. `open` == the run has REGRESSED to the SPEC phase (development writes
# are blocked); `closed` == the amendment landed, and the record survives as the audit trail + the
# carrier of the RED the new ACs owe.
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


# ======================================================================================
# the scope
# ======================================================================================

@dataclass(frozen=True)
class DeferredItem:
    """One thing the spec explicitly said it is NOT building — and how a hook can recognise it.

    `paths` catches it landing in its own module; `markers` catches it landing inside an authorized
    one (the incident's actual shape). Either is enough to block; both are optional, and an item
    with neither is inert (declared for humans, undecidable for the hook — and we say so rather
    than guess)."""

    id: str
    item: str
    paths: Tuple[str, ...] = ()
    markers: Tuple[str, ...] = ()

    @property
    def decidable(self) -> bool:
        return bool(self.paths or self.markers)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "item": self.item,
                "paths": list(self.paths), "markers": list(self.markers)}


@dataclass(frozen=True)
class SpecScope:
    """What this spec may touch, and what it explicitly may not."""

    authorized: Tuple[str, ...] = ()
    deferred: Tuple[DeferredItem, ...] = ()

    @property
    def enforceable(self) -> bool:
        """Is there anything here a hook could act on? A scope section that declares neither an
        authorized surface nor a decidable deferral enforces nothing — and must not pretend to."""
        return bool(self.authorized) or any(d.decidable for d in self.deferred)

    def to_dict(self) -> Dict[str, Any]:
        return {"authorized": list(self.authorized),
                "deferred": [d.to_dict() for d in self.deferred]}


def _strs(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str) and v)


def scope_from_dict(data: Any) -> Optional[SpecScope]:
    """A `SpecScope` out of a persisted value — or None when there is none, or it is unreadable.

    Degrade-clean and FAIL-OPEN by construction: every malformed shape collapses to None, and None
    means "no scope was declared", which the caller must ALLOW. A corrupt scope section must never
    block a write (the file is already broken; wedging the editor on top of that is the one thing a
    gate must not do)."""
    if not isinstance(data, dict):
        return None
    deferred = []
    raw = data.get("deferred")
    if isinstance(raw, list):
        for d in raw:
            if not isinstance(d, dict) or not d.get("id"):
                continue
            deferred.append(DeferredItem(
                id=str(d["id"]), item=str(d.get("item", d["id"])),
                paths=_strs(d.get("paths")), markers=_strs(d.get("markers"))))
    return SpecScope(authorized=_strs(data.get("authorized")), deferred=tuple(deferred))


# ======================================================================================
# the decision
# ======================================================================================

@dataclass(frozen=True)
class ScopeVerdict:
    """Whether this write is inside the spec's declared scope (doc 85 §3: an `*Outcome`-shaped
    verdict; named `Verdict` because that is what the hook asks it for)."""

    allowed: bool
    reason: str
    item_id: str = ""           # the deferred item that refused it, when one did
    item: str = ""


ALLOW_NO_SCOPE = ScopeVerdict(True, "the spec declares no machine-checkable scope")


def _rel(path: str, root: str = "") -> str:
    """`path` as a repo-relative POSIX path, for matching. An absolute path outside the root, or an
    unrelativisable one, is matched as given — never rejected for being unfamiliar."""
    p = path.replace("\\", "/")
    if root and os.path.isabs(path):
        try:
            rel = os.path.relpath(path, root).replace("\\", "/")
            if not rel.startswith(".."):
                return rel
        except (ValueError, OSError):
            pass
    return p.lstrip("./") if p.startswith("./") else p


def _matches(path: str, globs: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def _markers_in(content: str, markers: Sequence[str]) -> str:
    """The first marker present in `content`, or "". Case-insensitive substring — a marker is a
    literal token the human named (`batch_update`), not a pattern to interpret."""
    if not content:
        return ""
    haystack = content.lower()
    for m in markers:
        if m.lower() in haystack:
            return m
    return ""


def classify(scope: Optional[SpecScope], path: str, content: str = "",
             root: str = "") -> ScopeVerdict:
    """May this write proceed, as far as the SPEC's declared scope is concerned?

    Pure and total — never raises, never reads, never writes. Order matters: the explicit NEGATIVE
    is checked first, so a deferred item inside an otherwise-authorized file still blocks (which is
    exactly how the incident happened)."""
    if scope is None or not scope.enforceable:
        return ALLOW_NO_SCOPE

    rel = _rel(path, root)

    # 1 — the explicit negatives. What the spec said it is NOT building.
    for item in scope.deferred:
        if item.paths and _matches(rel, item.paths):
            return ScopeVerdict(
                False,
                f"this file is where the spec says '{item.item}' would live, and the spec DEFERRED "
                f"it", item_id=item.id, item=item.item)
        hit = _markers_in(content, item.markers)
        if hit:
            return ScopeVerdict(
                False,
                f"this write spells '{hit}', and the spec DEFERRED '{item.item}'",
                item_id=item.id, item=item.item)

    # 2 — the authorized surface. Only decidable when the spec actually drew a map.
    if scope.authorized and not _matches(rel, scope.authorized):
        return ScopeVerdict(
            False,
            f"{rel} is outside the surface this spec authorized "
            f"({', '.join(scope.authorized[:4])}{'…' if len(scope.authorized) > 4 else ''})")

    return ScopeVerdict(True, "inside the spec's authorized scope")


# ======================================================================================
# the amend record — the persisted phase marker (SI-DEV's `develop -> SPEC` regression)
# ======================================================================================

@dataclass(frozen=True)
class AmendRecord:
    """A spec amendment in flight, or the trail of one that landed.

    `PIPELINE_PHASES` has no `develop` phase — the pipeline's checkpoint vocabulary is the seven
    brainstorm phases — so there was no persisted place to say "this run has regressed out of
    develop". THIS record is that place, and its presence-with-status-`open` IS the regression: the
    hook reads it and blocks development writes for as long as it stands.

    After it closes it does NOT go away. It carries `red_owed` — the tests the amendment's NEW
    acceptance criteria are owed (mapped by the completeness gate itself) — because SI.1's TDD gate
    keys on "was anything ever RED in this run", which a mid-run amendment would sail straight past:
    the red-set is a high-water mark and is already non-empty. Without this, a freshly-widened spec
    would license implementation with no failing test for the new behaviour at all."""

    status: str = STATUS_CLOSED
    reason: str = ""
    item: str = ""
    from_version: int = 0
    to_version: int = 0
    red_owed: Tuple[str, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.status == STATUS_OPEN

    def owed(self, red: Set[str]) -> Tuple[str, ...]:
        """The tests the amended spec still owes a RED for."""
        return tuple(t for t in self.red_owed if t not in red)


NO_AMEND = AmendRecord()


def amend_from_state(data: Any) -> AmendRecord:
    """An `AmendRecord` out of a persisted value. Absent/corrupt reads as NO_AMEND — i.e. "no
    amendment is in flight and none is owed", which is the fail-open floor."""
    if not isinstance(data, dict):
        return NO_AMEND
    status = data.get("status")
    if status not in (STATUS_OPEN, STATUS_CLOSED):
        return NO_AMEND
    try:
        return AmendRecord(
            status=status,
            reason=str(data.get("reason", "")),
            item=str(data.get("item", "")),
            from_version=int(data.get("from_version", 0) or 0),
            to_version=int(data.get("to_version", 0) or 0),
            red_owed=_strs(data.get("red_owed")),
        )
    except (TypeError, ValueError):
        return NO_AMEND


def amend_key(run_id: str) -> str:
    return AMEND_PREFIX + run_id


def archive_key(run_id: str, version: int) -> str:
    return f"{ARCHIVE_PREFIX}{run_id}__v{version}"


__all__ = [
    "AMEND_PREFIX", "ARCHIVE_PREFIX", "SCOPE_KEY", "STATUS_CLOSED", "STATUS_OPEN",
    "ALLOW_NO_SCOPE", "NO_AMEND",
    "AmendRecord", "DeferredItem", "ScopeVerdict", "SpecScope",
    "amend_from_state", "amend_key", "archive_key", "classify", "scope_from_dict",
]
