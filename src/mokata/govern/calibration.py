"""H-2 · the calibration LOOP — the half R11 wired and nothing ever closed.

R11 (`govern.tokens`) built the record, the margin and `doctor`'s `calibration-drift` finding,
and then logged **the estimate alone**: `hook_cli`'s one production caller passes
`actual=None`, so the comparison the finding reads has never existed outside a test. Doc 82's
H-2 names the missing half exactly — *"read real usage from the hook envelopes / session
transcript … and complete the calibration loop that's already wired; derive a per-model ratio
and store it in `memory_stats.json`"*. This module is that half: `adapters.transcript` turns the
JSONL into counts, this folds the counts into a per-model ratio, persists it beside the memory
counters, and writes ONE ledger record per model carrying a REAL `actual` — so the existing
finding becomes reachable in the field instead of only under a test that hands it a number.

⭐ **THE RATIO IS A QUOTIENT OF TOTALS, NOT A MEAN OF RATIOS, and the difference is not
cosmetic.** A three-character message's own ratio is dominated by `ceil(len/4)`'s quantisation,
so averaging per-message ratios lets the shortest messages — of which a transcript has many —
outvote the long ones that actually consume the budgets. `actual_total / estimate_total` weights
every observation by its size, which is what a calibration constant for a budget means.

⚠ **AND THAT IS WHY A LOST OR REPEATED READ WINDOW IS HARMLESS, WHICH IS WHAT DECIDES THE WRITE
ORDER.** Both totals move by the same window, so the quotient is unchanged either way; the only
field a repeated window corrupts is `samples`. So the cursor advances BEFORE the fold is
persisted: a crash loop then costs a window of samples (unbiased) rather than re-folding the
same window forever (and inflating `samples` without bound).

⛔ **NO RATIO IS PUBLISHED BELOW `CALIBRATION_MIN_ESTIMATE_TOKENS` (§7g).** A ratio over 40
estimated tokens is noise wearing a number's clothes, and a number is what a reader acts on.
Below the floor the model's row carries its totals and `ratio: None` — *"not measured yet"* and
*"measured at 0.97"* must not share a representation.

🔴 **WHAT THIS MODULE DOES NOT CLAIM.** It calibrates the `chars/4` RULE, on assistant output,
over the messages `adapters.transcript` can account for wholly. It is not a measure of what a
session spent (the input side is unmeasurable from here — see that module), and it does not
assert that the margin holds: whether the measured ratio stays at or under
`CALIBRATION_MARGIN_RATIO` is now a MEASURABLE fact and an UNMEASURED one, and the first real
session is what measures it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from ..adapters.transcript import (
    TranscriptCursor,
    TranscriptReading,
    TokenSample,
    read_samples,
)
from .tokens import CALIBRATION_MARGIN_RATIO, log_calibration

# The `memory_stats` sub-key H-2 names. A SUB-key and not a sibling file: H-2 says
# `memory_stats.json`, and one more state file for four integers is the kind of growth doc 85
# argues against.
CALIBRATION_STATS_KEY = "calibration"

# Below this many ESTIMATED tokens a model's ratio is not published (see the module docstring).
CALIBRATION_MIN_ESTIMATE_TOKENS = 200

# The cursor lives with the dirty-set, in the gitignored transient area (24D) — it is a bookmark,
# not governed state, and it dies with the checkout rather than being committed.
CALIBRATION_DIRNAME = "calibration"

# The ledger context prefix, so a transcript-derived record is distinguishable from the
# `bootstrap` estimate-only rows R11 has been writing since 0.0.16.
CALIBRATION_CONTEXT_PREFIX = "transcript:"


@dataclass(frozen=True)
class ModelCalibration:
    """One model's measured `chars/4` constant. Totals, a count, and a ratio that is None until
    the floor is cleared — never a default of 1.0, which would read as "measured, and perfect"."""
    model: str
    samples: int
    estimate_total: int
    actual_total: int

    @property
    def ratio(self) -> Optional[float]:
        if self.estimate_total < CALIBRATION_MIN_ESTIMATE_TOKENS or self.estimate_total <= 0:
            return None
        return self.actual_total / self.estimate_total

    @property
    def over_margin(self) -> bool:
        """The estimate was supposed to run HIGH. A published ratio above the margin means it
        does not, and the 2000-token bootstrap budget is being enforced against an under-count."""
        r = self.ratio
        return r is not None and r > CALIBRATION_MARGIN_RATIO

    def to_dict(self) -> Dict[str, Any]:
        return {"samples": self.samples, "estimate_total": self.estimate_total,
                "actual_total": self.actual_total}


def fold(samples: List[TokenSample]) -> Dict[str, ModelCalibration]:
    """Group counts-only samples by model into totals. Pure."""
    acc: Dict[str, List[int]] = {}
    for s in samples:
        row = acc.setdefault(s.model, [0, 0, 0])
        row[0] += 1
        row[1] += int(s.estimate)
        row[2] += int(s.actual)
    return {m: ModelCalibration(model=m, samples=v[0], estimate_total=v[1], actual_total=v[2])
            for m, v in acc.items()}


def calibration_of(stats: Any) -> Dict[str, ModelCalibration]:
    """The per-model calibration recorded in a `memory_stats` dict. Pure, and tolerant: a
    malformed row is DROPPED rather than raising into a read-only surface (`doctor` reads this)."""
    out: Dict[str, ModelCalibration] = {}
    section = (stats or {}).get(CALIBRATION_STATS_KEY) if isinstance(stats, dict) else None
    if not isinstance(section, dict):
        return out
    for model, row in section.items():
        if not isinstance(model, str) or not isinstance(row, dict):
            continue
        try:
            rec = ModelCalibration(model=model,
                                   samples=int(row.get("samples", 0) or 0),
                                   estimate_total=int(row.get("estimate_total", 0) or 0),
                                   actual_total=int(row.get("actual_total", 0) or 0))
        except (TypeError, ValueError):
            continue
        if rec.samples <= 0 and rec.estimate_total <= 0:
            continue
        out[model] = rec
    return out


def merged_stats(current: Any, folded: Dict[str, ModelCalibration]) -> Dict[str, Any]:
    """The whole new `memory_stats` value: `current` with `folded` ADDED to its calibration rows.

    ⚠ **EVERY OTHER KEY IS PRESERVED, and that is the point of this function existing rather
    than a dict literal at the call site.** `memory_store._persist_stats` used to return
    `{"reads": …, "writes": …}` — a read-modify-write that TYPED its own schema and silently
    dropped anything else in the file (§7j). It had nothing to drop while the file held exactly
    those two keys; this one adds a third, so that hazard became live and is fixed on both
    sides. A merge that only knows its own keys is not a merge."""
    base: Dict[str, Any] = dict(current) if isinstance(current, dict) else {}
    section = base.get(CALIBRATION_STATS_KEY)
    rows: Dict[str, Any] = dict(section) if isinstance(section, dict) else {}
    for model, rec in folded.items():
        prev = rows.get(model) if isinstance(rows.get(model), dict) else {}
        try:
            prev_samples = int(prev.get("samples", 0) or 0)
            prev_est = int(prev.get("estimate_total", 0) or 0)
            prev_act = int(prev.get("actual_total", 0) or 0)
        except (TypeError, ValueError):
            prev_samples = prev_est = prev_act = 0
        rows[model] = {"samples": prev_samples + rec.samples,
                       "estimate_total": prev_est + rec.estimate_total,
                       "actual_total": prev_act + rec.actual_total}
    base[CALIBRATION_STATS_KEY] = rows
    return base


# ------------------------------------------------------------------ the cursor (a bookmark only)

def cursor_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, CALIBRATION_DIRNAME)


def cursor_path(root: str, transcript_path: str) -> str:
    """One bookmark per transcript, keyed by a digest of its PATH.

    ⭐ **THE PATH IS THE IDENTITY HERE, and that is deliberate after what keying cost signal 1.**
    The dirty-set was dead for a release because its writer and its reader resolved the session
    through two different ladders. This bookmark has exactly ONE writer and ONE reader — the same
    hook process — so it keys on the thing the hook is handed, and the `session_id` inside the
    record is the CHECK rather than the key: a transcript path reused under a new session restarts
    the read instead of trusting an offset into someone else's content."""
    digest = hashlib.sha1(transcript_path.encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(cursor_dir(root), f"cursor__{digest}.json")


def load_cursor(root: str, transcript_path: str) -> Optional[TranscriptCursor]:
    """The stored bookmark, or None (absent / unreadable / malformed). Never raises."""
    try:
        with open(cursor_path(root, transcript_path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("session_id")
    offset = data.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return None
    return TranscriptCursor(session_id=sid if isinstance(sid, str) else "", offset=offset)


def save_cursor(root: str, transcript_path: str, cursor: TranscriptCursor) -> bool:
    """Persist the bookmark. Returns whether it landed; never raises."""
    path = cursor_path(root, transcript_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        from ..atomicfile import atomic_write_text
        atomic_write_text(path, json.dumps({"session_id": cursor.session_id,
                                            "offset": int(cursor.offset)}) + "\n",
                          tmp_suffix=".json")
        return True
    except Exception:  # noqa: BLE001 — see `observe`: the async lane never fails an action
        return False


# ------------------------------------------------------------------------------- the loop itself

@dataclass
class CalibrationOutcome:
    """What one `observe` did, in facts a test can assert and a reader can act on. `reason` is
    non-None exactly when nothing was recorded, and says which of the several different nothings
    it was."""
    reading: Optional[TranscriptReading] = None
    folded: Dict[str, ModelCalibration] = None          # type: ignore[assignment]
    recorded: bool = False
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.folded is None:
            self.folded = {}


def observe(root: str, *, transcript_path: Optional[str], session_id: str = "",
            stats_store: Any = None, ledger: Any = None) -> CalibrationOutcome:
    """Read what the transcript has appended, fold it, persist it, and log it. Never raises.

    Called from the async observability lane, so every failure degrades to a reason rather than
    an exception — and to a REASON rather than an empty success, because "there was nothing to
    measure" and "the measurement could not happen" are different answers (§7g)."""
    try:
        if not transcript_path:
            return CalibrationOutcome(reason="no transcript path in the hook envelope")
        cursor = load_cursor(root, transcript_path)
        reading = read_samples(transcript_path, session_id=session_id, cursor=cursor)

        # The cursor advances FIRST — see the module docstring: a repeated window cannot move the
        # ratio, but it can inflate `samples` without bound, and a crash loop is the shape that
        # would do it.
        #
        # ⛔ **AND IT ADVANCES BEFORE THE READABILITY BRANCH, WHICH IS THE WHOLE OF REVIEW F2.**
        # `read_samples` can return a REASON *and* real progress: a single JSONL line longer than
        # the entire read cap comes back as `reason="no complete record inside the read cap"` with
        # `consumed=len(blob)` and an ADVANCED cursor — written that way on purpose, so the reader
        # does not sit on it forever. `observe` returned on `not readable` first and threw that
        # cursor away, so **ONE oversized record wedged calibration for the rest of the session**:
        # every later hook call re-read the same bytes and reported the same reason, and the
        # instrument looked like it was running. A reason and a non-zero `consumed` are two facts,
        # not one (§7g) — and this is the second place in this stage where a reason channel was
        # read as "nothing happened".
        if reading.consumed:
            save_cursor(root, transcript_path, reading.cursor)
        if not reading.readable:
            return CalibrationOutcome(reading=reading, reason=reading.reason)
        if not reading.samples:
            if reading.schema_blind:
                return CalibrationOutcome(
                    reading=reading,
                    reason=(f"{reading.assistant} assistant record(s) read and NOT ONE carried a "
                            "token count — the transcript JSONL shape may have moved"))
            if reading.records == 0:
                # §7g once more, at the smallest scale in this module: "the session has
                # written nothing since we last looked" and "the session wrote records we could
                # not calibrate" are the difference between a quiet repo and a moved schema, and
                # `schema_blind` above can only mean the second if this means the first.
                return CalibrationOutcome(reading=reading,
                                          reason="no new transcript records since the last read")
            return CalibrationOutcome(reading=reading,
                                      reason=f"none of the {reading.records} new record(s) is a "
                                             "calibratable assistant message")

        folded = fold(reading.samples)
        store = stats_store
        if store is None:
            from ..state import StateStore
            from ..tdd_state import state_dir
            store = StateStore(state_dir(root))
        from ..memory.store import MEMORY_STATS_KEY
        store.update(MEMORY_STATS_KEY, lambda cur: merged_stats(cur, folded),
                     default={"reads": 0, "writes": 0})

        # ONE ledger record per model, carrying a REAL `actual`. This is the whole reason the
        # loop exists: `doctor`'s `calibration-drift` finding reads the ledger, and until now
        # nothing in `src/` ever gave it an `actual` to compare against.
        led = ledger
        if led is None:
            try:
                from .ledger import AuditLedger
                led = AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR))
            except Exception:  # noqa: BLE001 — observability; see the register entry
                led = None
        if led is not None:
            for model, rec in sorted(folded.items()):
                log_calibration(led, f"{CALIBRATION_CONTEXT_PREFIX}{model}",
                                estimate=rec.estimate_total, actual=rec.actual_total)
        return CalibrationOutcome(reading=reading, folded=folded, recorded=True)
    except Exception as exc:  # noqa: BLE001 — the async lane NEVER fails a tool call
        return CalibrationOutcome(reason=f"calibration skipped ({type(exc).__name__})")
