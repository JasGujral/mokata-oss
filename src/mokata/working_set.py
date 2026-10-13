"""HP.S2 (0.0.22 D2) — the `WorkingSet`: ONE run-keyed object a phase reads instead of N lookups.

The row: *"run-keyed `working_set__<run>` holding REFERENCES (spec key · approach key · phase ·
touched-files · plan slug) so each phase reads ONE keyed object instead of N independent lookups"*,
surfaced in the briefing resume, a read-only MCP tool and the session bundle.

⭐ REFERENCES, NOT COPIES — and that decides what is stored. The phase, the spec and the approach
already have owners on disk (the checkpoint, `emitted_spec__<run>`, `approved_approach__<run>`). A
second copy of any of them would be a second truth that drifts the first time one write lands and
the other does not. So `working_set(root, run)` DERIVES every reference from its owner on each read,
and the persisted `working_set__<run>` holds only what has NO other home:

  * `touched_files` — the repo-relative paths this run's sessions wrote, appended by the PostToolUse
    dirty-track hook for a session BOUND to the run (C1's claim), so parallel runs never mix their
    files. Capped at `TOUCHED_CAP`, oldest dropped, and the drop COUNTED (never silent).
  * `notes` — short free-text notes a phase leaves for the next (`add_note`). Capped likewise.

Never raises on read: an unreadable record reads as empty, said by `readable=False`.
"""

from __future__ import annotations

import os

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .state import StateStore
from .tdd_state import state_dir

WORKING_SET_PREFIX = "working_set__"
TOUCHED_CAP = 200
NOTES_CAP = 20
NOTE_MAX_CHARS = 280


@dataclass(frozen=True)
class WorkingSet:
    run_id: str
    phase: Optional[str]                 # the next pipeline phase to run (checkpoint)
    last_passed: Optional[str]           # the last pipeline gate passed (checkpoint)
    stage: Optional[str]                 # the last user-stage entered (progress log)
    approach_key: Optional[str]          # the state key holding the approved approach, if any
    spec_key: Optional[str]              # the state key holding the emitted spec, if any
    plan_slug: Optional[str]             # the brainstorm plan's slug, derived from its topic
    touched_files: List[str] = field(default_factory=list)
    touched_dropped: int = 0             # how many older paths the cap dropped
    notes: List[str] = field(default_factory=list)
    readable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def line(self) -> str:
        """One briefing line."""
        files = self.touched_files[-3:]
        more = len(self.touched_files) - len(files) + self.touched_dropped
        bits = [f"{len(self.touched_files) + self.touched_dropped} file(s) touched"
                + (f": {', '.join(files)}" if files else "") + (f" (+{more})" if more > 0 else "")]
        if self.spec_key:
            bits.append(f"spec `{self.spec_key}`")
        if self.plan_slug:
            bits.append(f"plan `{self.plan_slug}`")
        if self.notes:
            bits.append(f"{len(self.notes)} note(s) — last: {self.notes[-1]}")
        return "working set: " + " · ".join(bits)


def _store(root: str) -> StateStore:
    return StateStore(state_dir(root))


def _persisted(root: str, run_id: str) -> "tuple":
    try:
        data = _store(root).read(WORKING_SET_PREFIX + run_id)
    except Exception:  # noqa: BLE001
        return {}, False
    return (data if isinstance(data, dict) else {}), True


def working_set(root: str, run_id: str) -> WorkingSet:
    """The run's working set: every reference derived from its owner, plus the persisted extras."""
    from .govern.resume import PipelineCheckpoint
    from .progress_events import STAGE_ENTER, ProgressLog
    from .run_resolver import APPROACH_PREFIX, SPEC_PREFIX
    store = _store(root)
    data, readable = _persisted(root, run_id)
    phase = last = stage = approach_key = spec_key = slug = None
    try:
        cp = PipelineCheckpoint(store, run_id)
        phase, last = cp.resume_phase(), cp.last_passed()
    except Exception:  # noqa: BLE001
        readable = False
    try:
        if store.exists(APPROACH_PREFIX + run_id):
            approach_key = APPROACH_PREFIX + run_id
            topic = (store.read(approach_key) or {}).get("topic")
            if isinstance(topic, str) and topic.strip():
                from .plans import plan_slug
                slug = plan_slug(topic)
        if store.exists(SPEC_PREFIX + run_id):
            spec_key = SPEC_PREFIX + run_id
        for e in ProgressLog.from_state_dir(state_dir(root)).read_events_backward():
            if e.get("run_id") == run_id and e.get("type") == STAGE_ENTER:
                stage = e.get("stage")
                break
    except Exception:  # noqa: BLE001
        readable = False
    touched = [p for p in data.get("touched_files", []) if isinstance(p, str)]
    notes = [n for n in data.get("notes", []) if isinstance(n, str)]
    dropped = data.get("touched_dropped", 0)
    return WorkingSet(run_id, phase, last, stage, approach_key, spec_key, slug, touched,
                      dropped if isinstance(dropped, int) else 0, notes, readable)


def _update(root: str, run_id: str, mutate) -> None:
    def _m(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        mutate(cur)
        cur["updated_at"] = time.time()
        return cur
    _store(root).update(WORKING_SET_PREFIX + run_id, _m, default={})


def record_touched(root: str, run_id: str, rel_path: str) -> None:
    """Append a repo-relative path this run wrote (deduplicated, capped, the drop counted). A path
    outside the repo, or one carrying a control character, is not a working-set file and is not
    recorded (0.0.22 verification D-5a: `../../etc/hosts` and raw newlines reached the briefing)."""
    if not run_id or not rel_path:
        return
    from .repo_paths import split_path
    parts = split_path(rel_path)
    if (os.path.isabs(rel_path) or not parts or ".." in parts or ":" in parts[0]
            or any(ord(c) < 32 for c in rel_path)):
        return

    def _m(cur: Dict[str, Any]) -> None:
        files = [p for p in cur.get("touched_files", []) if isinstance(p, str) and p != rel_path]
        files.append(rel_path)
        over = max(0, len(files) - TOUCHED_CAP)
        cur["touched_files"] = files[over:]
        cur["touched_dropped"] = int(cur.get("touched_dropped", 0) or 0) + over
    _update(root, run_id, _m)


def add_note(root: str, run_id: str, note: str) -> str:
    """Leave a short note for the run's next phase. Returns the stored (trimmed) note."""
    text = " ".join((note or "").split())[:NOTE_MAX_CHARS]
    if not run_id or not text:
        raise ValueError("a working-set note needs a run and some text")

    def _m(cur: Dict[str, Any]) -> None:
        notes = [n for n in cur.get("notes", []) if isinstance(n, str)] + [text]
        cur["notes"] = notes[-NOTES_CAP:]
    _update(root, run_id, _m)
    return text


__all__ = ["WORKING_SET_PREFIX", "TOUCHED_CAP", "NOTES_CAP", "WorkingSet", "working_set",
           "record_touched", "add_note"]
