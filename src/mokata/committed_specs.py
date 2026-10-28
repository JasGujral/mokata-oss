"""L7b (0.0.26, ruling D1) — OPT-IN committed specs: `.mokata/specs/<run>.json`.

By default a spec lives only in `.mokata/temp_local/` (run state, git-ignored): no repo litter, and
the research behind D1 found committed spec artefacts add review burden. The cost of that default
is CI: a fresh checkout has no `temp_local/`, so `mokata docsync`'s spec-drift leg and `mokata
ci-check` have no spec to check, and they used to say so in a way that read like a pass.

`settings.specs.commit: true` (a governed manifest setting — `mokata config set` is human-gated and
ledgered) turns on the committed copy. When it is on, the ONE spec committer
(`engine/emit.spec_commit`) also writes the APPROVED spec — criteria, `tasks[]`, `clarifications[]`,
`assumptions[]`, version and plan hash, exactly the bytes the run state holds — to
`.mokata/specs/<run_id>.json`. It is written inside the same human-gated commit that persists the
spec (P2): emit, MCP emit and `spec amend` all land there, so an amendment UPDATES the committed
file (keyed by run, so a retitled spec does not leave a stale twin).

Readers: `docsync.spec_drift` and `ci_check` load this directory when it is present. When it is
absent they print `NO_COMMITTED_NOTE` — the check still exits 0 (nothing was there to check), but
never silently.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import MOKATA_DIR

#: The tracked directory (NOT under temp_local/, so `.mokata/.gitignore` leaves it versioned).
SPECS_DIRNAME = "specs"
#: The governed setting (`settings.<SETTING>`), and its default: off.
SETTING = "specs.commit"
#: The most committed spec files a reader loads — a repo with more than this many runs on record is
#: told how many were left out, never silently truncated.
MAX_COMMITTED = 500

NO_COMMITTED_NOTE = ("no committed specs — spec drift not checked (nothing in .mokata/specs/). "
                     "Opt in with `mokata config set settings.specs.commit true` so approved "
                     "specs are committed and CI can check them")


def specs_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, SPECS_DIRNAME)


def spec_path(root: str, run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in run_id) or "spec"
    return os.path.join(specs_dir(root), safe + ".json")


def commit_enabled(root: str) -> bool:
    """Is `settings.specs.commit` true in this repo's manifest? Read directly (one small JSON read,
    no Surface), so the committer can ask from inside its gated commit. Anything unreadable or not
    exactly `true` reads as OFF — the default, which writes nothing."""
    try:
        with open(os.path.join(root, MOKATA_DIR, "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    specs = ((data.get("settings") or {}).get("specs") or {}) if isinstance(data, dict) else {}
    return isinstance(specs, dict) and specs.get("commit") is True


def render(run_id: str, data: Dict[str, Any]) -> str:
    """The committed file's text: the approved spec as persisted, plus its run id. Sorted keys and a
    trailing newline, so a re-commit of the same spec is byte-identical (a clean diff in review)."""
    body = dict(data)
    body["run_id"] = run_id
    return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_committed(root: str, run_id: str, data: Dict[str, Any]) -> str:
    """Write the approved spec to `.mokata/specs/<run_id>.json`, atomically. Called ONLY from
    `engine/emit.spec_commit`, inside the WriteGate commit closure (P2). Returns the path."""
    from .atomicfile import atomic_write_text
    path = spec_path(root, run_id)
    # newline="\n": a COMMITTED file must be byte-identical on every OS (no CRLF churn on Windows).
    atomic_write_text(path, render(run_id, data), newline="\n")
    return path


@dataclass
class CommittedSpecs:
    """What `.mokata/specs/` holds. `present` is False when the directory is absent or empty — the
    case the readers must SAY out loud."""

    present: bool
    specs: List[Any] = field(default_factory=list)     # engine.spec.Spec
    skipped: List[str] = field(default_factory=list)   # unreadable files (named, never dropped)
    left_out: int = 0                                  # files past MAX_COMMITTED

    @property
    def note(self) -> str:
        if not self.present:
            return NO_COMMITTED_NOTE
        bits = [f"{len(self.specs)} committed spec(s) in .mokata/specs/"]
        if self.skipped:
            bits.append(f"{len(self.skipped)} unreadable: {', '.join(self.skipped[:5])}")
        if self.left_out:
            bits.append(f"{self.left_out} not loaded (over {MAX_COMMITTED})")
        return "; ".join(bits)


def load_committed(root: str) -> CommittedSpecs:
    """Every committed spec, read-only and bounded. Never raises."""
    from .engine.spec import Spec
    d = specs_dir(root)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except OSError:
        return CommittedSpecs(present=False)
    if not names:
        return CommittedSpecs(present=False)
    out = CommittedSpecs(present=True, left_out=max(0, len(names) - MAX_COMMITTED))
    for name in names[:MAX_COMMITTED]:
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            out.specs.append(Spec.from_dict(data))
        except (OSError, ValueError, TypeError, KeyError):
            out.skipped.append(name)
    return out


def committed_path_for(root: str, run_id: Optional[str]) -> Optional[str]:
    """Where an approval of `run_id`'s spec would be committed, or None when the setting is off —
    for the approval preview, so the human sees the tracked write before approving it."""
    if not root or not run_id or not commit_enabled(root):
        return None
    from .repo_paths import name_of
    return name_of(spec_path(root, run_id), root)


__all__ = ["MAX_COMMITTED", "NO_COMMITTED_NOTE", "SETTING", "SPECS_DIRNAME", "CommittedSpecs",
           "commit_enabled", "committed_path_for", "load_committed", "render", "spec_path",
           "specs_dir", "write_committed"]
