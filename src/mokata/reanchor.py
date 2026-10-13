"""CP.S1 + H-1b (0.0.22 D1): a gate survives compaction with its banner, hard rules and pipeline
position intact — per Claude Code session, so parallel runs each get their own.

THE PROBLEM
-----------
Compaction replaces the conversation with a summary. Everything mokata enforces is on disk (the run
checkpoint, the approved approach, the emitted spec, the TDD red set, overrides), so the GATES never
forget — but the MODEL does: after a compaction it no longer knows which run it is driving, which
phase that run is in, or why its next implementation write will be blocked. The SessionStart
briefing already re-fires on `source="compact"`, but it is session-blind: it lists mokata's gates
in general and never says which of them binds THIS session now. That is compaction amnesia, and the
cost is a model that hits a block it no longer understands and starts working around it.

THE FIX
-------
  * H-1b — a `PreCompact` hook writes an ANCHOR for this session (`compact_anchor__<session>`): its
    run, that run's phase, and the gate obligations still open — computed from the same on-disk
    state the gates read, so it is a snapshot of the truth, not of the conversation.
  * CP.S1 — on SessionStart with `source` `compact` (or `resume`), the briefing gains a re-anchor
    block for THIS session: the run, its position, the obligation that will decide the next
    implementation write, any override in force, and the one hard rule that matters most after a
    summary (decide from the record, not from memory). When a pre-compaction anchor exists, the
    block says whether anything moved across the compaction — the round trip is checked, not
    assumed.

ONE IDENTITY, PARALLEL-SAFE
---------------------------
The run is read from `run_intent.session_claim` — the binding this session's OWN registration
produced (0.0.22 C1). Never from "the active run" of the repo: two windows compacting side by side
must each be told about their own run. A session with no claim gets no re-anchor block (nothing of
its own is in force) and the general briefing is unchanged.

The episodic-note half of H-1b (propose a memory of what was being done) is D6's harvest, which
rides this hook point; it is not built here.

Never raises: every reader degrades to "say nothing extra".
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .state import StateStore
from .tdd_state import state_dir

#: `compact_anchor__<claude_session_id>.json` — written by PreCompact, read after it.
ANCHOR_PREFIX = "compact_anchor__"

#: The SessionStart sources after which the model has lost its conversational context.
REANCHOR_SOURCES = ("compact", "resume")

#: The hard rule a summarised model most needs (the gates hold; the memory of them does not).
HARD_RULE = ("decide from mokata's RECORD, not from the summary: `mokata progress` / the "
             "`progress` tool for where the run is, `review_status` before ship. A block you no "
             "longer remember the reason for is still a block — read its message, never route "
             "around it.")


@dataclass(frozen=True)
class Position:
    """Where ONE session's run stands, read from the state the gates themselves read."""
    claim: str                         # run_intent.CLAIM_* — none / open / bound
    run_id: Optional[str] = None
    stage: Optional[str] = None        # the last user-stage entered (progress log)
    approach: bool = False             # an approach is approved
    spec: bool = False                 # a spec is emitted
    red: int = 0                       # failing tests on record (TDD)
    finished: bool = False             # a ship-completion event is recorded
    overrides: Tuple[str, ...] = field(default_factory=tuple)

    def facts(self) -> Dict[str, Any]:
        d = asdict(self)
        d["overrides"] = list(self.overrides)
        return d


def position(root: str, session_id: Optional[str]) -> Position:
    """This session's position. `Position("none")` for a session with no claim, or on any fault."""
    try:
        from .run_intent import CLAIM_BOUND, CLAIM_OPEN, session_claim
        claim, rid = session_claim(root, session_id)
        if claim == CLAIM_OPEN:
            return Position("open")
        if claim != CLAIM_BOUND or not rid:
            return Position("none")
        from .gate_hook import read_override
        from .progress_events import RUN_FINISHED, STAGE_ENTER, ProgressLog
        from .run_resolver import APPROACH_PREFIX, SPEC_PREFIX
        from .tdd_state import load
        sd = state_dir(root)
        store = StateStore(sd)
        red, _green = load(store, rid)
        stage = None
        finished = False
        for e in ProgressLog.from_state_dir(sd).read_events_backward():
            if e.get("run_id") != rid:
                continue
            if e.get("type") == RUN_FINISHED and stage is None:
                finished = True
                stage = "ship"
                break
            if e.get("type") == STAGE_ENTER:
                stage = e.get("stage")
                break
        return Position("bound", rid, stage, store.exists(APPROACH_PREFIX + rid),
                        store.exists(SPEC_PREFIX + rid), len(red), finished,
                        tuple(sorted(read_override(root, rid))))
    except Exception:  # noqa: BLE001 — a re-anchor that cannot read says nothing extra
        return Position("none")


def write_anchor(root: str, session_id: str) -> Optional[Position]:
    """H-1b — PreCompact: persist this session's position before the conversation is summarised."""
    if not session_id:
        return None
    pos = position(root, session_id)
    if pos.claim == "none":
        return pos
    try:
        StateStore(state_dir(root)).write(ANCHOR_PREFIX + session_id,
                                          {"at": time.time(), "position": pos.facts()})
    except Exception as exc:  # noqa: BLE001
        try:
            from .degrade import FAILURE_LOCAL_IO, note_degraded
            note_degraded("compaction-anchor", FAILURE_LOCAL_IO, detail=str(exc),
                          fallback="the pre-compaction anchor was not saved; the re-anchor after "
                                   "compaction will still read the run's state, but cannot say "
                                   "whether anything moved",
                          fix="check permissions/disk under `.mokata/temp_local/`")
        except Exception:  # noqa: BLE001
            pass
    return pos


def _read_anchor(root: str, session_id: str) -> Optional[Dict[str, Any]]:
    try:
        data = StateStore(state_dir(root)).read(ANCHOR_PREFIX + session_id)
    except Exception:  # noqa: BLE001
        return None
    pos = data.get("position") if isinstance(data, dict) else None
    return pos if isinstance(pos, dict) else None


#: The file a NEW implementation write would make, when this run has touched none yet.
PROBE_NEW_FILE = "src/new_module.py"


def next_write(root: str, session_id: str, pos: Position) -> str:
    """THE GATE's own verdict for this session's next implementation write — asked of
    `gate_hook.check_write`, never re-derived (0.0.22 verification, D-2: a parallel ladder told a
    freshly compacted model "allowed" during an open spec amend, "blocked" under an override, and
    "finished" while spec-scope still blocked). Judged for the implementation file this run last
    touched, else for a new one."""
    from .gate_hook import check_write, is_implementation_path
    probe, label = PROBE_NEW_FILE, "a new implementation file"
    if pos.run_id:
        try:
            from .working_set import working_set
            impl = [p for p in working_set(root, pos.run_id).touched_files
                    if is_implementation_path(p)]
            if impl:
                probe, label = impl[-1], f"`{impl[-1]}`"
        except Exception:  # noqa: BLE001 — fall back to the new-file probe
            pass
    import os
    out = check_write(root, os.path.join(root, probe), session_id=session_id)
    reason = out.reason if len(out.reason) <= 240 else out.reason[:237] + "..."
    if out.allowed and out.overridden:
        return f"ALLOWED for {label}, only by a ledgered override of {out.gate}"
    if out.allowed:
        return f"ALLOWED for {label} ({reason})"
    return f"BLOCKED [{out.gate}] for {label} — {reason}"


def build_reanchor(root: str, session_id: Optional[str], source: Optional[str]) -> Optional[str]:
    """CP.S1 — the re-anchor block for SessionStart after a compaction/resume, or None."""
    if source not in REANCHOR_SOURCES or not session_id:
        return None
    pos = position(root, session_id)
    if pos.claim == "none":
        return None
    when = "compaction" if source == "compact" else "resume"
    lines: List[str] = [f"mokata · re-anchored after {when} — THIS session's run (from the record):"]
    if pos.claim == "open":
        lines.append("- run: not registered yet")
    else:
        where = f"stage '{pos.stage}'" if pos.stage else "no stage entered yet"
        lines.append(f"- run: {pos.run_id} · {where} · approach "
                     f"{'approved' if pos.approach else 'not approved'} · spec "
                     f"{'emitted' if pos.spec else 'not emitted'} · {pos.red} failing test(s)")
    lines.append(f"- next implementation write: {next_write(root, session_id, pos)}")
    if pos.overrides:
        lines.append(f"- overrides in force (ledgered): {', '.join(pos.overrides)}")
    if pos.run_id:
        try:
            from .working_set import working_set
            lines.append("- " + working_set(root, pos.run_id).line())
        except Exception:  # noqa: BLE001 — the working set is a convenience on this block
            pass
    if source == "compact":
        before = _read_anchor(root, session_id)
        if before is None:
            lines.append("- across the compaction: no pre-compaction anchor was recorded "
                         "(PreCompact hook not wired?) — the position above is read fresh")
        elif before == pos.facts():
            lines.append("- across the compaction: unchanged (pre-compaction anchor matches)")
        else:
            moved = sorted(k for k in pos.facts() if before.get(k) != pos.facts()[k])
            lines.append(f"- across the compaction: MOVED ({', '.join(moved)}) — trust the "
                         f"position above, not the summary")
    lines.append(f"- hard rule: {HARD_RULE}")
    return "\n".join(lines)


__all__ = ["ANCHOR_PREFIX", "REANCHOR_SOURCES", "HARD_RULE", "PROBE_NEW_FILE", "Position",
           "position", "next_write", "write_anchor", "build_reanchor"]
