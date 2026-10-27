"""R4 (0.0.26 Lane R) — the 3-fix BREAKER and the 5-round REVIEW-LOOP cap.

THE FAILURE THIS EXISTS FOR. An agent whose fix did not work tries another fix, and another, each
one a guess on top of the last — the "fourth blind fix". The research candidate (doc 113 §4, from
the 2026-10-16 skills research: superpowers' systematic-debugging rule "3+ failed fixes → stop and
question the approach") is a number, not a vibe: after THREE failed fix attempts on the SAME failure
the agent stops fixing and escalates (root-cause it with /mokata:debug, or hand it to the human). The
review loop gets the same shape: a review that fails, is fixed and re-reviewed is a loop, and FIVE
failed rounds is where it stops looping and asks the human.

WHAT IS RECORDED, AND BY WHOM.
  * A fix attempt is recorded with `mokata progress fix-attempt --failure "<signature>" --failed|
    --passed` (the develop/debug skills say when). The SIGNATURE is the failing test id or the error
    line; it is normalised (`failure_signature`) so the same failure re-run with a different
    whitespace or a fresh memory address is still the same failure.
  * A review round is a `review_verdict` event — already recorded by the review skill
    (`progress_events.record_review_verdict`); nothing new is asked of it.

WHAT IS ENFORCED, AND WHERE (observed, not instructed).
  * The breaker TRIPS on the third failed attempt at one signature, and the trip is LEDGERED
    (`loop_breaker`, decision `fix-breaker-tripped`). While it is tripped the gate-guard hook BLOCKS
    implementation writes for the run (gate `fix-breaker`), so the fourth blind fix cannot land
    however the agent words it. Per the research ruling it stays tripped until a NEW APPROACH is
    approved through the existing approach-approval (the run's `approved_approach__` record differs
    from the one on record at the trip) — three failed fixes say the approach is wrong, not the
    fourth guess. The human's P14 override (`mokata gate override fix-breaker`) is the other exit.
    A `--passed` attempt clears an UNtripped count only.
  * The review loop: `record_review_verdict` REFUSES a sixth verdict after five consecutive failed
    rounds (`progress_events.ReviewLoopCapped`) — round 6 escalates instead of running, and the
    refusal is ledgered (`loop_breaker`, decision `review-loop-capped`). The human's P14 override
    (`mokata gate override review-loop`) is the way on. The review skill bumps the reviewer's model
    tier at round 4 and defers Minor findings (skill text — `skills.REVIEW_LOOP_CAP_CLAUSE`).

STATE. One per-run key, `fix_breaker__<run_id>`, in the run's state dir (gitignored temp_local, the
same tier as the TDD record): run-tracking, never a durable code/memory/config write, so UNGATED like
`tdd_phase__`. Written by `StateStore.update` (locked read-modify-write, atomic replace). The read
the gate-guard hook makes is two lock-free JSON reads (`tripped_failures`), and it never raises.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import hashlib
import json
import os

from .state import StateStore
from .tdd_state import state_dir

#: The ledger kind for a breaker ruling (a trip, a review-loop refusal).
LEDGER_KIND = "loop_breaker"
#: `run_resolver.APPROACH_PREFIX`, spelled here for the gate-guard's import surface (pinned).
APPROACH_PREFIX = "approved_approach__"

#: Three failed fixes at one failure trip the breaker — the fourth is the blind one.
FIX_BREAKER_LIMIT = 3
#: Five failed review rounds in a row cap the loop — round six escalates.
REVIEW_ROUND_CAP = 5

#: The two gate ids (P14-overridable; listed in `gate_hook.GATES`).
GATE_FIX_BREAKER = "fix-breaker"
GATE_REVIEW_LOOP = "review-loop"

BREAKER_PREFIX = "fix_breaker__"
#: A signature longer than this is cut — a whole traceback is not an identity, its first line is.
SIGNATURE_CHARS = 200

#: What a tripped breaker tells the agent to do instead of a fourth fix.
ESCALATE = ("STOP fixing — escalate: root-cause it with /mokata:debug (reproduce, trace the real "
            "path, name the cause) or hand it to the human with what you tried")

_HEX = re.compile(r"0x[0-9a-fA-F]+")
_SPACE = re.compile(r"\s+")


def failure_signature(text: Any) -> str:
    """The identity of a failure: first non-empty line, whitespace collapsed, memory addresses
    blanked, lower-cased, capped. "" for nothing — an empty signature is refused, never counted."""
    if not isinstance(text, str):
        return ""
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    line = _HEX.sub("0x?", line)
    return _SPACE.sub(" ", line).strip().lower()[:SIGNATURE_CHARS]


def breaker_key(run_id: str) -> str:
    return BREAKER_PREFIX + run_id


def _failures(data: Any) -> Dict[str, Dict[str, Any]]:
    raw = data.get("failures") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out = {}
    for sig, row in raw.items():
        if isinstance(sig, str) and isinstance(row, dict):
            n = row.get("attempts")
            approach = row.get("approach")
            out[sig] = {"attempts": n if isinstance(n, int) and not isinstance(n, bool) and n > 0
                        else 0, "tripped": bool(row.get("tripped")),
                        "approach": approach if isinstance(approach, str) else ""}
    return out


def record_fix_attempt(root: str, run_id: str, failure: str, *, passed: bool) -> Dict[str, Any]:
    """Record one fix attempt at `failure` for `run_id` and return `{signature, attempts, tripped}`.

    A failed attempt counts; the third trips (recording the approach on record at that moment, and
    ledgering the ruling). A passed one clears an UNtripped count; a tripped one waits for a new
    approach. Raises ValueError for an empty signature or a missing run — a count keyed to nothing
    would be a breaker nothing can trip."""
    sig = failure_signature(failure)
    if not sig:
        raise ValueError("a fix attempt needs the failure it was aimed at (the failing test id or "
                         "the error line)")
    if not run_id:
        raise ValueError("no run to record the fix attempt against — name it with --run")
    seen: Dict[str, Any] = {}
    approach = approach_signature(root, run_id)

    def _mutate(current: Any) -> Dict[str, Any]:
        rows = _failures(current)
        row = rows.get(sig, {"attempts": 0, "tripped": False, "approach": ""})
        if row["tripped"] and row["approach"] != approach:
            row = {"attempts": 0, "tripped": False, "approach": ""}   # a new approach: fresh count
        if passed:
            if not row["tripped"]:
                rows.pop(sig, None)
                row = {"attempts": 0, "tripped": False, "approach": ""}
        else:
            row["attempts"] += 1
            if not row["tripped"] and row["attempts"] >= FIX_BREAKER_LIMIT:
                row.update(tripped=True, approach=approach)
                seen["newly_tripped"] = True
            rows[sig] = row
        seen.update(signature=sig, attempts=row["attempts"], tripped=row["tripped"])
        return {"run_id": run_id, "failures": rows}

    StateStore(state_dir(root)).update(breaker_key(run_id), _mutate, default=None)
    if seen.pop("newly_tripped", False):
        ledger(root, decision="fix-breaker-tripped", gate=GATE_FIX_BREAKER, run=run_id,
               failure=sig, attempts=seen["attempts"])
    return seen


def approach_signature(root: str, run_id: str) -> str:
    """A fingerprint of the run's approved approach (sha256 of its canonical JSON), "" for none."""
    try:
        data = StateStore(state_dir(root)).read(APPROACH_PREFIX + run_id)
    except Exception:  # noqa: BLE001 — an unreadable approach fingerprints as "none"
        return ""
    if not isinstance(data, dict):
        return ""
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def ledger(root: str, **fields: Any) -> None:
    """Ledger one breaker ruling. Best-effort: the ruling stands whether or not it is counted."""
    try:
        from .govern.ledger import AuditLedger
        AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")).record(LEDGER_KIND, **fields)
    except Exception:  # noqa: BLE001
        pass


def tripped_failures(root: str, run_id: Optional[str]) -> List[Tuple[str, int]]:
    """`[(signature, attempts)]` for every tripped breaker of `run_id` — ONE lock-free read, never
    raises (an absent/corrupt record trips nothing: the breaker can only ever be tripped by a
    count it actually read)."""
    if not run_id:
        return []
    try:
        data = StateStore(state_dir(root)).read(breaker_key(run_id))
    except Exception:  # noqa: BLE001 — a hook read must never raise
        return []
    tripped = [(s, r) for s, r in _failures(data).items() if r["tripped"]]
    if not tripped:
        return []
    current = approach_signature(root, run_id)      # a NEW approach approved since → cleared
    return sorted((s, r["attempts"]) for s, r in tripped if r["approach"] == current)


def breaker_message(tripped: List[Tuple[str, int]]) -> str:
    sig, n = tripped[0]
    more = f" (and {len(tripped) - 1} more)" if len(tripped) > 1 else ""
    return (f"{GATE_FIX_BREAKER}: {n} fix attempts at the same failure have failed — "
            f"\"{sig}\"{more}. A fourth blind fix is blocked: three failures say the APPROACH is "
            f"wrong. {ESCALATE}, then get a NEW approach approved (/mokata:brainstorm — the "
            f"approach-approval gate); that clears the breaker. Or the human overrides: mokata "
            f"gate override {GATE_FIX_BREAKER} --reason \"<why>\"")


# ------------------------------------------------------------------------------- the review loop

def failed_review_streak(events: Iterable[Dict[str, Any]], run_id: str) -> int:
    """How many of `run_id`'s most recent review verdicts FAILED in a row (newest-first `events`)."""
    streak = 0
    for e in events:
        if e.get("type") != "review_verdict" or e.get("run_id") != run_id:
            continue
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        if data.get("passed"):
            break
        streak += 1
    return streak


def review_loop_message(streak: int) -> str:
    return (f"{GATE_REVIEW_LOOP}: {streak} review rounds in a row have failed — the cap is "
            f"{REVIEW_ROUND_CAP}, so round {streak + 1} escalates instead of running. STOP the "
            f"review/fix loop and hand it to the human: what each round found, what was fixed, what "
            f"keeps failing. The human continues it with: mokata gate override "
            f"{GATE_REVIEW_LOOP} --reason \"<why>\"")


__all__ = ["FIX_BREAKER_LIMIT", "REVIEW_ROUND_CAP", "GATE_FIX_BREAKER", "GATE_REVIEW_LOOP",
           "LEDGER_KIND", "APPROACH_PREFIX", "approach_signature", "ledger",
           "BREAKER_PREFIX", "ESCALATE", "failure_signature", "breaker_key",
           "record_fix_attempt", "tripped_failures", "breaker_message", "failed_review_streak",
           "review_loop_message"]
