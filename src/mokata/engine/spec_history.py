"""SPEC-HISTORY read surface (0.0.23 D6) — read a superseded spec back.

SPEC-REEMIT-CLOBBER made a re-emit (and SI-DEV's amend) ARCHIVE the prior spec at
`spec_archive__<run>__v<N>` instead of destroying it, and the ledger names the key — but nothing read
the archive back, so a human could not see a superseded spec without opening the state store by hand.
This is the read half, shared by `mokata spec show --version N / --versions` and the MCP `spec_show`
tool's `version` / `list_versions`, so the two surfaces cannot disagree about a run's history.

Read-only. The CURRENT version is `emit.spec_version(store)` (what the committer wrote beside the
spec); every earlier version is probed at the archive key the committer would have used for it —
the SAME run identity (`emit._reemit_run`), never a second derivation. A version that was never
archived (a gap) is listed as missing rather than skipped, and an archive that cannot be parsed is
MALFORMED, never "absent".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .spec import Spec
from .spec_gate import SPEC_STATE_KEY

#: Never probe more than this many archive keys (a hand-edited version number cannot make a read
#: walk the disk unboundedly).
MAX_VERSIONS = 200


def _parse(data: Any) -> Tuple[Optional[Spec], bool]:
    if not data:
        return None, False
    try:
        return Spec.from_dict(data), False
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, True


def _archive(run_id: str, version: int) -> str:
    from ..spec_scope import archive_key
    from .emit import _reemit_run
    return archive_key(_reemit_run(None, run_id or ""), version)


def current_version(store: Any) -> int:
    from .emit import spec_version
    return spec_version(store)


def spec_at_version(store: Any, run_id: str, version: int) -> Dict[str, Any]:
    """`{version, key, current, spec, malformed}` for `version` of this run's spec. `spec` is None
    when that version is absent (never emitted / never archived) or malformed (`malformed=True`)."""
    cur = current_version(store)
    if version == cur and cur:
        key = SPEC_STATE_KEY
    else:
        key = _archive(run_id, version)
    try:
        data = store.read(key)
    except (OSError, AttributeError):
        data = None
    spec, malformed = _parse(data)
    return {"version": version, "key": key, "current": version == cur and bool(cur),
            "spec": spec, "malformed": malformed}


def spec_versions(store: Any, run_id: str) -> List[Dict[str, Any]]:
    """Every version of this run's spec, oldest first: `{version, key, current, present, malformed,
    title, ac_count}`. Empty when the run has no spec."""
    cur = current_version(store)
    out: List[Dict[str, Any]] = []
    for v in range(max(1, cur - MAX_VERSIONS + 1), cur + 1):
        row = spec_at_version(store, run_id, v)
        spec = row.pop("spec")
        row.update({"present": spec is not None or row["malformed"],
                    "title": spec.title if spec is not None else "",
                    "ac_count": len(spec.criteria) if spec is not None else 0})
        out.append(row)
    return out


def render_versions(rows: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for r in rows:
        if r["malformed"]:
            state = "MALFORMED — present but unreadable"
        elif not r["present"]:
            state = "not archived"
        else:
            state = f"'{r['title']}' — {r['ac_count']} AC(s)"
        lines.append(f"  v{r['version']}{' (current)' if r['current'] else ''}: {state}  "
                     f"[{r['key']}]")
    return lines


__all__ = ["MAX_VERSIONS", "current_version", "spec_at_version", "spec_versions",
           "render_versions"]
