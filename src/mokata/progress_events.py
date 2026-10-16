"""Stage 6b — a minimal, EVENT-STREAM-SHAPED pipeline progress log.

An append-only JSONL record of the user-stage transitions a run passes through
(brainstorm → spec → develop → review → ship). It exists so the always-on stage badge
(Stage 54b `build_stage_badge`) can tell `develop` / `review` / `ship` apart — three
separate skills with no shared pipeline checkpoint, indistinguishable from the run-state
alone (the old "spec emitted → always develop" collapse).

Trust tier: OBSERVABILITY. Like the audit ledger (`govern/ledger.py`), this is append-only
and UNGATED — it is NOT a P2 durable code/memory/config write, so it never routes through
the WriteGate/human-gate. It mirrors the ledger's append/read pattern deliberately: one
persistence mechanism, not a second invention.

Event envelope — a COMPATIBLE SUBSET of 0.1.0's R1.S1a event stream, so R1.S1a can absorb
this log as a superset rather than a second store to migrate:

    {event_id, ts, schema_version, type, stage, run_id, data}

`type` ∈ {stage_enter, run_finished, review_verdict}. These map conceptually onto R1.S1a's
`PhaseTransition` (enter/finish) and verdict events. `run_finished` is the ship-completion event
(0.0.22 C1, `B-LIFE-FU`): written once, by `/mokata:ship` after the human chose how to land the
run, so "this run is over" is a recorded fact rather than an inference from entering `ship`. It
replaced `stage_pass`, a constant that was defined from 0.0.16 and never written by anything.
`run_id` reuses the EXISTING pipeline /
session_bundle run identity; R1.S1a introduces the fuller `session_id` (uuid-per-session) —
this field is the forward-compatible hook it will bind to.

Degrade-clean (P11): an absent / corrupt / unreadable log reads as EMPTY, never raises —
the badge falls back to its checkpoint-derived default.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .errors import MokataError

# The log lives beside the pipeline run-state (StateStore.root =
# .mokata/temp_local/state/), so a reader that already holds `surface.state` finds it
# without new plumbing. It is transient runtime data, gitignored like the state store.
PROGRESS_EVENTS_FILENAME = "progress-events.jsonl"

# Bump ONLY on an incompatible envelope change; R1.S1a keys off this to migrate the log.
PROGRESS_SCHEMA_VERSION = 1

# The event types this minimal log records. A strict subset of R1.S1a's vocabulary.
STAGE_ENTER = "stage_enter"
RUN_FINISHED = "run_finished"
REVIEW_VERDICT = "review_verdict"
#: 0.0.22 C2 (SL.S3) — a run-state gate BLOCKED an implementation write: mokata caught something
#: IN THE LOOP, before review. Written by the gate-guard hook, keyed to the run whose state blocked.
GATE_BLOCK = "gate_block"
#: 0.0.22 D4 (H-3) — a contracted subagent of this run FINISHED: its type, id, and the HandbackReport
#: it returned (or why it returned none). Written by the SubagentStop hook. What lets a review that
#: says it was independent be checked against a subagent that was actually seen to finish.
SUBAGENT_FINISHED = "subagent_finished"
PROGRESS_EVENT_TYPES = (STAGE_ENTER, RUN_FINISHED, REVIEW_VERDICT, GATE_BLOCK, SUBAGENT_FINISHED)

#: How a finished run was LANDED — the four options `/mokata:ship` step 3 presents to the human.
#: A finish names one; "finished, somehow" is not a record anybody can audit.
LANDINGS = ("merged", "pr", "kept", "discarded")

# The envelope keys every entry carries — the R1.S1a-compatible subset the drift/subset
# test asserts against (so the two schemas can never silently diverge).
ENVELOPE_KEYS = ("event_id", "ts", "schema_version", "type", "stage", "run_id", "data")

# How many trailing events a bounded read returns by default (frugal, P11 — a tail, not
# the whole history; the badge only needs the most recent transitions).
DEFAULT_TAIL = 200

# REVIEW-FIX.R2 — the BACKWARD scan's safety valve, in bytes of trailing log.
#
# It is NOT a correctness bound, and that is the whole distinction this stage turns on. `DEFAULT_TAIL`
# was one: a reader asking "did THIS run's review pass?" saw only the newest 200 events, so on a busy
# repo (every phase entry of every command in every run appends to this one never-rotated file) the
# run's own verdict fell out of the window and the gate read "review hasn't run". The backward scan
# stops at the FIRST match for the run it is asking about, so it reads only as far as it must and the
# answer no longer depends on how much noise landed after the evidence.
#
# 8 MiB is therefore the "something is wrong" line, not the "good enough" line: this log's entries
# measure ~200 bytes, so the cap covers ~40,000 events — years of a busy repo's transitions. Reaching
# it means the answer genuinely is not in the log (or the log needs rotation, FILED not built here);
# what it buys is that a read-only hot path can never page in an unbounded file.
SCAN_BYTE_CAP = 8 * 1024 * 1024

# How much of the file a backward scan pulls per seek. One block already spans ~300 events, so the
# common case (the evidence is recent) costs a single read.
_SCAN_BLOCK = 64 * 1024

# REVIEW-FIX.R4 — the READ-FAULT vocabulary: why a verdict read came back with nothing.
#
# Three kinds, and the whole point of naming them is that the REMEDY differs. The first two are
# proven by `ProgressLog.read_fault` against the log itself; the third is what a SURFACE reports
# when the read raised before any log could be probed (a broken/unloadable repo surface). None of
# the three is "review hasn't run", which is the fourth, genuinely different answer.
FAULT_UNREADABLE = "unreadable"     # the log exists and could not be opened/read
FAULT_CORRUPT = "corrupt"           # the log has lines that could not be parsed
FAULT_UNKNOWN = "unknown"           # the read itself failed before the log could be examined


def _parse_line(raw: bytes) -> Optional[Dict[str, Any]]:
    """One JSONL line -> an event dict, or None when it is blank / torn / not an object. Same
    degrade-clean contract as `read_events`: a corrupt line is SKIPPED, never raised."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None

# One process-wide lock serialises read-len-then-append across threads, exactly like the
# audit ledger: a POSIX text-mode append is atomic (O_APPEND) but Windows concurrent
# appends can clobber, so the lock keeps the append-only log correct on every OS.
_APPEND_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressLog:
    """Append-only progress-event log — the observability twin of `AuditLedger`."""

    def __init__(self, path: str) -> None:
        self.path = path

    @classmethod
    def from_state_dir(cls, state_root: str) -> "ProgressLog":
        """The log inside a StateStore's directory (`surface.state.root`)."""
        return cls(os.path.join(state_root, PROGRESS_EVENTS_FILENAME))

    @classmethod
    def from_surface(cls, surface: Any) -> "ProgressLog":
        """The log for a loaded Surface (reads/writes under .mokata/temp_local/state/)."""
        return cls.from_state_dir(surface.state.root)

    def append_event(self, type: str, stage: str, run_id: Optional[str] = None,
                     data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Append one R1.S1a-shaped event and return it. Never rewrites existing entries;
        the read-then-append is locked so concurrent writers never drop one. UNGATED —
        this writes straight to the JSONL, never through the WriteGate (observability)."""
        entry: Dict[str, Any] = {
            "event_id": uuid.uuid4().hex,
            "ts": _now_iso(),
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "type": type,
            "stage": stage,
            "run_id": run_id,
            "data": dict(data) if data else {},
        }
        with _APPEND_LOCK:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        # R1.S1c — project into the typed event stream, OUTSIDE the append lock (the same
        # reasoning as the ledger's projection: the canonical line is already on disk, and
        # taking a second store's lock while holding this one is how a deadlock gets written).
        _project_event(self.path, entry)
        return entry

    def read_events(self, tail: Optional[int] = DEFAULT_TAIL) -> List[Dict[str, Any]]:
        """The last `tail` well-formed events (all of them when `tail` is falsy). Bounded
        + degrade-clean: an absent file → []; a corrupt / half-written line is SKIPPED, so
        a truncated log never raises into the read-only badge/progress hot paths."""
        if not os.path.exists(self.path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue                 # skip a corrupt/partial line, don't raise
                    if isinstance(obj, dict):
                        out.append(obj)
        except OSError:
            return []
        if tail and tail > 0:
            return out[-tail:]
        return out

    def read_events_backward(self, byte_cap: Optional[int] = SCAN_BYTE_CAP):
        """Yield well-formed events NEWEST-FIRST, reading at most `byte_cap` trailing bytes.

        The read discipline for any question of the form "what is the latest X?" (REVIEW-FIX.R2).
        `read_events` is forward-only: it parses the WHOLE file and then slices the last `tail`
        entries, so its bound was never on the IO at all — only on what it handed back, which is
        exactly the wrong end to bound. This walks the file from the END in `_SCAN_BLOCK` chunks and
        lets the caller stop at the first hit, so the hot path reads a few KiB in the common case
        and the answer does not depend on how many unrelated events landed after the evidence.

        Bounded: never more than `byte_cap` bytes (falsy `byte_cap` = no cap). A line sliced in half
        by the cap is DROPPED rather than half-parsed. Degrade-clean, matching `read_events`: an
        absent/unreadable file yields nothing, a corrupt line is skipped, nothing raises."""
        try:
            fh = open(self.path, "rb")
        except OSError:
            return
        try:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            floor = max(0, size - byte_cap) if byte_cap and byte_cap > 0 else 0
            pos = size
            carry = b""            # the head of a line whose tail lives in the chunk just read
            while pos > floor:
                step = min(_SCAN_BLOCK, pos - floor)
                pos -= step
                fh.seek(pos)
                parts = (fh.read(step) + carry).split(b"\n")
                carry = parts.pop(0)
                for raw in reversed(parts):
                    obj = _parse_line(raw)
                    if obj is not None:
                        yield obj
            if pos == 0 and carry:
                # Only a scan that reached byte 0 holds a COMPLETE first line here; when the cap
                # stopped it short, `carry` is a fragment and is dropped.
                obj = _parse_line(carry)
                if obj is not None:
                    yield obj
        except OSError:
            return
        finally:
            fh.close()

    def find_last_event(self, match: Any,
                        byte_cap: Optional[int] = SCAN_BYTE_CAP) -> Optional[Dict[str, Any]]:
        """The MOST RECENT event satisfying `match`, or None (REVIEW-FIX.R2). Scans backward and
        returns at the first hit, so a run's own evidence is found in one block read however busy
        the shared log has since become. `byte_cap` is the safety valve, not the answer's bound."""
        for event in self.read_events_backward(byte_cap=byte_cap):
            if match(event):
                return event
        return None

    def read_fault(self, byte_cap: Optional[int] = SCAN_BYTE_CAP) -> Optional[str]:
        """WHY a read came back empty — `FAULT_UNREADABLE` / `FAULT_CORRUPT`, or None when the log
        is simply absent or wholly well-formed (REVIEW-FIX.R4).

        This exists because the degrade-clean contract above, which is right, ERASES its own
        reason: `read_events_backward` yields nothing on OSError and SKIPS a line it cannot parse,
        so an unreadable or damaged log and a log with no matching event are the SAME empty answer
        to every caller. `ship_review_gate` then reports "review hasn't run" and sends the operator
        to re-run review when the actual fault is on disk — the remedy naming the wrong problem.

        A SEPARATE probe rather than a health field threaded through the scan, deliberately: the
        scan is REVIEW-FIX.R2's pinned read discipline and its one job is to yield events cheaply;
        diagnosis is a different question, asked only when the answer is already a BLOCK, so it
        never costs the hot path (P11). It reads the SAME trailing `byte_cap` window the scan does,
        so the two can never disagree about what was in view.

        CORRUPT is reported for any unparseable line in that window. A torn final line is the
        expected casualty of a crash mid-append, and calling that "damaged" is the honest answer,
        not an alarmist one: bytes were skipped, so mokata genuinely cannot say a verdict was never
        recorded. The caller asks only after finding no verdict, so a log whose damage did not
        matter never produces this answer.

        Degrade-clean like everything else here: it never raises, and None means "no fault I can
        prove", which keeps the absent-verdict answer the default."""
        path = getattr(self, "path", None)
        if not isinstance(path, str) or not path or not os.path.exists(path):
            return None                 # no log at all == no evidence, and that is honest
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                floor = max(0, size - byte_cap) if byte_cap and byte_cap > 0 else 0
                fh.seek(floor)
                # A cap-sliced first line is a fragment of the SCAN's making, not damage on disk —
                # the backward scan drops it for the same reason, so this probe must not count it.
                skip_first, carry = floor > 0, b""
                while True:
                    chunk = fh.read(_SCAN_BLOCK)
                    if not chunk:
                        break
                    parts = (carry + chunk).split(b"\n")
                    carry = parts.pop()
                    for raw in parts:
                        if skip_first:
                            skip_first = False
                            continue
                        if raw.strip() and _parse_line(raw) is None:
                            return FAULT_CORRUPT
                if carry.strip() and not skip_first and _parse_line(carry) is None:
                    return FAULT_CORRUPT
        except OSError:
            return FAULT_UNREADABLE
        return None


# ============================================================ Stage 6r — the review verdict
#
# The closing review's verdict is PERSISTED here as a `review_verdict` event — the SAME
# append-only, event-stream-shaped log, NOT a second store (the 6b↔6r unification the
# re-audit asked for). `/mokata:ship` reads the persisted record instead of trusting the
# conversation: evidence over vibes, applied to review itself. The event data carries:
#     {passed: bool, independent: bool, findings?: int|str}
# `independent` is True when the review ran as a fresh-context subagent (re-derived from a
# self-contained brief), False when it degraded to the inline two-pass (no subagents / the
# user opted the isolation off). Ship surfaces the difference; it never hard-blocks on inline.


# Sentinel default for `record_review_verdict(run_id=...)`: distinguishes an OMITTED run_id
# (bind to the active run — the common case) from an EXPLICIT `run_id=None` (record a truly
# run-less verdict, e.g. a standalone review outside any pipeline run). Needed by the 6r-gate
# fix (doc-49 #3): a run-less verdict must never silently become the active run's evidence.
_CURRENT_RUN: Any = object()


def _resolve_verdict_run(surface: Any) -> Optional[str]:
    """The SESSION-AWARE run this surface's review verdict is keyed to — used by BOTH the record
    and the read, so record-key == read-key by construction (REVIEW-FIX.R1).

    Replaces the session-BLIND `progress.find_active_run`, which scanned every `pipeline_run__*`
    checkpoint in the shared state root and re-answered when `mark ship` retired a run: a `/clear`ed
    or second session resolved a FOREIGN run and shipped on its review.

    RUN-ID-DRIFT — this is now THE resolver (`run_resolver.resolve_run`), the same call every other
    surface makes, rather than a verdict-specific twin of it. Two properties R1 established are
    preserved by the ladder itself and must stay that way: ambiguity resolves to None so the caller
    FAILS CLOSED (mokata refuses rather than reading a foreign run's verdict), and ship RETIREMENT
    is not applied, so the verdict key survives `mokata progress mark ship` — otherwise ship's own
    entry mark would re-key the read away from the run whose verdict was recorded.
    Total: a surface with no readable `root` resolves to None rather than raising.

    SESSION-BIND-BY-ANCESTOR (0.0.23 F1) — `resolve_run_for_process`: the same ladder, plus the
    Claude session this process serves when process ancestry can name it (the MCP `review_record` /
    `review_status` pair, which cannot see a session id). Record and read both come through HERE, so
    record-key == read-key still holds by construction. No ancestry match is `resolve_run` exactly."""
    root = getattr(surface, "root", None)
    if not isinstance(root, str) or not root:
        return None
    from .run_resolver import resolve_run_for_process
    return resolve_run_for_process(root).run_id


def record_review_verdict(surface: Any, *, passed: bool, independent: bool,
                          findings: Optional[Any] = None,
                          blocking: Optional[int] = None,
                          run_id: Any = _CURRENT_RUN) -> Dict[str, Any]:
    """Append the closing review's verdict as a `review_verdict` event and return it.

    Observability tier, exactly like `stage_enter`: append-only + UNGATED (it writes no
    durable code/memory/config, so it never routes through the WriteGate/human-gate). When
    `run_id` is OMITTED, the run is resolved SESSION-AWARELY (`_resolve_verdict_run` — the same
    resolution `ship_review_gate` reads with, so the record-key and the read-key are the same run
    by construction, REVIEW-FIX.R1); passing `run_id=None` EXPLICITLY records a run-less verdict.

    A run-less verdict (explicit, or an omitted `run_id` that resolves to nothing) SATISFIES
    NOTHING: `latest_review_verdict` refuses a run-less read, so ship blocks. Recording still
    succeeds — the event is honest observability — but it is not evidence any gate will accept;
    the CLI says so, and the remedy is to re-record with an explicit `--run <run id>`."""
    if run_id is _CURRENT_RUN:
        run_id = _resolve_verdict_run(surface)
    data: Dict[str, Any] = {"passed": bool(passed), "independent": bool(independent)}
    if findings is not None:
        data["findings"] = findings
    # SL.S3 (0.0.22 C2) — the COUNT of Blocking findings this review raised: what review caught
    # that the loop did not. Absent is NOT zero (§7g): a verdict without it leaves the run's
    # caught-at-review rate UNMEASURED rather than reading as "review caught nothing".
    if blocking is not None:
        if isinstance(blocking, bool) or not isinstance(blocking, int) or blocking < 0:
            raise ValueError("blocking must be a whole number of Blocking findings (0 or more)")
        problem = verdict_contradiction(bool(passed), blocking)
        if problem:
            raise ValueError(problem)
        data["blocking"] = blocking
    return ProgressLog.from_surface(surface).append_event(
        REVIEW_VERDICT, "review", run_id=run_id, data=data)


def verdict_contradiction(passed: bool, blocking: Optional[int]) -> str:
    """REVIEW-LIB-SWEEP (0.0.22 C2) — a verdict and its Blocking count must agree, or "" when they do.

    Adopted, clean-room, from the blocking / non-blocking split that both Conventional Comments
    (`(blocking)` decoration, CC BY 3.0) and Google's review guide ("Nit:" marks the optional; the
    rest must be addressed, CC BY 3.0) rest on: a BLOCKING finding is by definition one the change
    may not be accepted with. So a PASS that raised one is not a pass, and a FAIL that raised none
    names nothing to fix. Mapped to the ship gate rather than added as prose: the record refuses,
    so ship can never accept a verdict its own count contradicts. No count, no check (§7g: unknown
    is not zero)."""
    if blocking is None:
        return ""
    if passed and blocking > 0:
        return (f"a review that raised {blocking} Blocking finding(s) cannot be recorded as PASSED "
                f"— record it as failed, or fix them and re-review")
    if not passed and blocking == 0:
        return ("a FAILED review must name at least one Blocking finding — if nothing blocks, "
                "record it as passed (Minor findings never fail a review)")
    return ""


def current_stage(state_root: str, run_id: Optional[str], default: str = "develop") -> str:
    """The stage `run_id` is in: its latest `stage_enter`, read newest-first and bounded.

    0.0.23 B3 (`STAGE-LABEL-DEVELOP-ON-EVERY-EVENT`): `gate_block` and `subagent_finished` were
    stamped `develop` whatever the run's stage — seen on a run still in brainstorm. `default` only
    when the run has no `stage_enter` yet (the pre-0.0.23 label, so nothing that read it moves)."""
    if not run_id:
        return default
    try:
        for e in ProgressLog.from_state_dir(state_root).read_events_backward():
            if e.get("type") == STAGE_ENTER and e.get("run_id") == run_id and e.get("stage"):
                return str(e["stage"])
    except Exception:
        return default
    return default


def record_gate_block(state_root: str, run_id: str, gate: str, path: str) -> Optional[Dict]:
    """An in-loop catch: a run-state gate blocked `path` in `run_id`. Written by the gate-guard
    hook. Observability tier (append-only, UNGATED). Returns the event, or None when there is no
    run to key it to — a run-less block is not a catch any run can be credited with."""
    if not run_id or not gate:
        return None
    return ProgressLog.from_state_dir(state_root).append_event(
        GATE_BLOCK, current_stage(state_root, run_id), run_id=run_id,
        data={"gate": gate, "path": path})


def record_subagent_finished(state_root: str, run_id: str, contract: Dict[str, Any],
                             report: Any, why: str = "") -> Optional[Dict]:
    """H-3 — a contracted subagent of `run_id` finished. Written by the SubagentStop hook.
    Observability tier (append-only, UNGATED). `report` is a `HandbackReport` or None; None records
    WHY there was none, so "no handback" and "no subagent" stay different facts."""
    if not run_id:
        return None
    data: Dict[str, Any] = {"agent_id": contract.get("agent_id"),
                            "agent_type": contract.get("agent_type") or "",
                            "read_only": bool(contract.get("read_only"))}
    if report is not None:
        data["handback"] = report.facts()
    else:
        data["handback_missing"] = why or "no handback"
    if data["agent_id"] and any(e.get("data", {}).get("agent_id") == data["agent_id"]
                                for e in subagents_finished(state_root, run_id)):
        # 0.0.22 live check: with the plugin AND `mokata init`'s project hooks both wired, every
        # hook fires twice — one subagent is one finish, so the second firing records nothing.
        return None
    return ProgressLog.from_state_dir(state_root).append_event(
        SUBAGENT_FINISHED, current_stage(state_root, run_id), run_id=run_id, data=data)


def subagents_finished(state_root: str, run_id: Optional[str],
                       since: Optional[str] = None) -> List[Dict[str, Any]]:
    """The `subagent_finished` events of `run_id` (oldest first), optionally only those at or after
    the ISO timestamp `since`. Empty for no run. Never raises."""
    if not run_id:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for e in ProgressLog.from_state_dir(state_root).read_events_backward():
            if e.get("type") != SUBAGENT_FINISHED or e.get("run_id") != run_id:
                continue
            ts = e.get("ts")
            if since and isinstance(ts, str) and ts < since:
                continue
            out.append(e)
    except Exception:  # noqa: BLE001 — an unreadable log observes nothing
        return []
    out.reverse()
    return out


@dataclass(frozen=True)
class ReviewYield:
    """SL.S3 — what review caught vs what the loop caught, for one run or summed over runs.

    `in_loop` is the number of DISTINCT `(gate, path)` blocks the run-state gates raised; a model
    retrying the same blocked write is one catch, not five. `at_review` is the Blocking findings
    the run's review verdicts recorded. `measured` is False when any verdict lacks its count, or
    when no review has run — `rate` is then None, never 0 (three states, never two: "review caught
    nothing" and "nobody counted" are different facts). `rate` is `at_review / (at_review +
    in_loop)`, and None too when nothing was caught at all (no denominator)."""
    run_id: Optional[str]
    in_loop: int
    at_review: int
    reviews: int
    measured: bool

    @property
    def rate(self) -> Optional[float]:
        total = self.in_loop + self.at_review
        if not self.measured or total == 0:
            return None
        return self.at_review / total

    def line(self) -> str:
        if not self.reviews and self.run_id is None:
            return "caught at review: not measured — no run has a review with its Blocking count"
        if not self.reviews:
            return "caught at review: not measured — no review verdict is on record"
        if not self.measured:
            return (f"caught at review: not measured — a review verdict was recorded without its "
                    f"Blocking count ({self.in_loop} caught in the loop)")
        if self.rate is None:
            return "caught at review: nothing caught, in the loop or at review"
        return (f"caught at review: {self.at_review} of {self.at_review + self.in_loop} "
                f"({self.rate:.0%}) — {self.in_loop} caught in the loop by the gates")


def _yields(log: "ProgressLog") -> Dict[str, ReviewYield]:
    blocks: Dict[str, set] = {}
    review: Dict[str, list] = {}
    for e in log.read_events(tail=None):
        rid = e.get("run_id")
        if not isinstance(rid, str) or not rid:
            continue
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        if e.get("type") == GATE_BLOCK:
            blocks.setdefault(rid, set()).add((str(data.get("gate")), str(data.get("path"))))
        elif e.get("type") == REVIEW_VERDICT:
            review.setdefault(rid, []).append(data.get("blocking"))
    out = {}
    for rid in set(blocks) | set(review):
        counts = review.get(rid, [])
        known = [c for c in counts if isinstance(c, int) and not isinstance(c, bool)]
        out[rid] = ReviewYield(rid, len(blocks.get(rid, ())), sum(known), len(counts),
                               bool(counts) and len(known) == len(counts))
    return out


def review_yield(surface: Any, run_id: Optional[str]) -> ReviewYield:
    """The caught-at-review figures for ONE run (unmeasured when the run has no record)."""
    if not run_id:
        return ReviewYield(None, 0, 0, 0, False)
    return _yields(ProgressLog.from_surface(surface)).get(
        run_id, ReviewYield(run_id, 0, 0, 0, False))


def review_yield_all(surface: Any) -> ReviewYield:
    """Summed over every run whose figures are MEASURED. Unmeasured runs are left out rather than
    counted as zero — and `reviews` counts only the runs that were summed."""
    measured = [y for y in _yields(ProgressLog.from_surface(surface)).values() if y.measured]
    return ReviewYield(None, sum(y.in_loop for y in measured), sum(y.at_review for y in measured),
                       len(measured), bool(measured))


class FinishRefused(MokataError):
    """`record_run_finished` would not write: no run to finish, or no landing named. A finish
    with no run proves nothing and one with no landing says nothing, so neither is recorded."""


def record_run_finished(surface: Any, *, landing: str,
                        run_id: Any = _CURRENT_RUN) -> Dict[str, Any]:
    """Append the run's ship-completion event (`run_finished`) and stamp the checkpoint's
    `completed_at`. Returns the event. `B-LIFE-FU` (0.0.22 C1).

    Until this existed the strongest end-of-run evidence was ENTERING ship (`stage_enter: ship`),
    written only by the CLI — so a run that finished through the harness never stamped
    `completed_at`, and "the run is over" was an inference from "the run started its last phase".

    Same tier as `stage_enter` and `review_verdict`: append-only and UNGATED (no durable
    code/memory/config is written). Unlike a verdict, it is NEVER recorded run-less: an omitted
    `run_id` resolves the same session-aware way the verdict does (`_resolve_verdict_run`), and if
    that resolves to nothing — no run, or several and none identifiably this caller's — it raises
    `FinishRefused` rather than write an event no reader can key to anything. `landing` must be
    one of `LANDINGS`. The `completed_at` stamp is best-effort: the event is the record."""
    landing = (landing or "").strip()
    if landing not in LANDINGS:
        raise FinishRefused("name how the run was landed: one of " + ", ".join(LANDINGS))
    if run_id is _CURRENT_RUN:
        run_id = _resolve_verdict_run(surface)
    if not isinstance(run_id, str) or not run_id.strip():
        raise FinishRefused("no run could be resolved here to finish — name it with --run "
                            "<run id> (`mokata sessions` lists them)")
    event = ProgressLog.from_surface(surface).append_event(
        RUN_FINISHED, "ship", run_id=run_id, data={"landing": landing})
    try:
        from .govern.resume import PipelineCheckpoint
        PipelineCheckpoint(surface.state, run_id).mark_completed()
    except Exception:  # noqa: BLE001 — the event is the record; the stamp is a display aid
        pass
    return event


def latest_review_verdict_event(surface: Any,
                                run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The most recent `review_verdict` EVENT for `run_id` — the whole envelope, so a caller can
    read WHEN it was recorded (`ts`) and not just what it said (REVIEW-FIX.R2).

    Same run-scoping and same refusals as `latest_review_verdict`, which is now a thin
    `["data"]` of this (one read, one set of rules — the shape it returns is unchanged).

    The read is a bounded BACKWARD scan that stops at this run's first match. It used to be
    `read_events()`, i.e. the last `DEFAULT_TAIL`=200 events of a single shared, never-rotated
    log that EVERY run's every phase entry appends to: a run's own verdict slid out of that
    window on a busy repo and the gate reported "review hasn't run". Post-R1 that fails CLOSED
    (a false BLOCK, never a false pass), but a gate that blocks on evidence which is right there
    on disk is still wrong, and a gate people learn to distrust is a gate they route around."""
    if run_id is None:
        return None
    try:
        log = ProgressLog.from_surface(surface)
    except (AttributeError, OSError, ValueError):
        # Read the callee before trusting this list. The SCAN is ALREADY degrade-clean — it yields
        # nothing on OSError and SKIPS a torn JSON line — so neither can escape it. The one class
        # that genuinely arrives here is AttributeError, from `from_surface` reaching through
        # `surface.state.root` on a malformed surface (the degrade-clean contract
        # `test_stage6r_independent_review` pins). OSError/ValueError are kept as defence-in-depth
        # on a documented FAIL-CLOSED path, not as decoration.
        #
        # Fail-CLOSED is what makes silence safe here, and it is the whole justification: no verdict
        # ⇒ `ship_review_gate` BLOCKS ("review hasn't run — run /mokata:review first"). The only
        # outcome this handler can produce is a STOP. It cannot let an unreviewed change through,
        # which is the single thing a swallowed read on the ship path must never do.
        return None
    # EXACT run match only (`run_id` is never None past the guard above), and a verdict whose `data`
    # is not an object is SKIPPED rather than accepted — the pre-R2 forward loop only ever committed
    # a match with dict data, so the scan keeps looking further back exactly as it used to.
    return log.find_last_event(
        lambda e: (e.get("type") == REVIEW_VERDICT and e.get("run_id") == run_id
                   and isinstance(e.get("data"), dict)))


def latest_review_verdict(surface: Any,
                          run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The most recent `review_verdict` event's data (`{passed, independent, findings?}`),
    or None when none was recorded. STRICTLY run-scoped (6r-gate fix, doc-49 #3): with a
    `run_id`, ONLY that run's verdicts count — a verdict recorded with `run_id: None`
    (outside any run) does NOT satisfy a real run, so neither a prior run's verdict nor a
    run-less one can leak into a fresh run's ship gate.

    `run_id=None` REFUSES (REVIEW-FIX.R1). It used to mean "consider every verdict": the filter at
    the loop below is `if run_id is not None and …`, so a run-less read did NO filtering at all and
    the last verdict in the whole stream won — a foreign run's PASS satisfying a gate that could not
    name its own run. There is no read a gate can trust without a run to key on, so the run-less
    read now returns None and the caller fails closed. This makes the run-scoped promise above TRUE
    on both branches. Degrade-clean: an absent/corrupt/unreadable log -> None (ship then blocks as
    if no review ran — fail-closed, evidence over vibes).

    REVIEW-FIX.R2 — the WINDOW this keyed read scans is now the whole log (bounded backward, stopping
    at this run's first match) instead of the trailing 200 events; see `latest_review_verdict_event`,
    which this returns the `data` of. The returned shape is unchanged."""
    event = latest_review_verdict_event(surface, run_id=run_id)
    if event is None:
        return None
    data = event.get("data")
    return data if isinstance(data, dict) else None


# settings.review.independent = on | off  (default "on") — mirrors settings.brainstorm.auto.
# `on` (default) runs the closing review as a fresh-context subagent; `off` restores the old
# inline two-pass for users who want it. Degrade-clean: an absent/broken/unrecognised value
# reads as `on`, so the stronger, independent review is never silently lost.
REVIEW_INDEPENDENT_ON, REVIEW_INDEPENDENT_OFF = "on", "off"


def review_independent_mode(surface: Any) -> str:
    """The saved `settings.review.independent` preference (Stage 6r). Default `on`."""
    try:
        s = surface.manifest.setting("review", {}) or {}
        v = s.get("independent", REVIEW_INDEPENDENT_ON) if isinstance(s, dict) else \
            REVIEW_INDEPENDENT_ON
        return REVIEW_INDEPENDENT_OFF if v == REVIEW_INDEPENDENT_OFF else REVIEW_INDEPENDENT_ON
    except AttributeError:
        # A surface with no `.manifest` (a double, a half-built surface). The fallback is the
        # STRONGER setting (`on` — the independent, fresh-context review), so a failure to read the
        # preference can never silently downgrade the review to the weaker inline pass.
        return REVIEW_INDEPENDENT_ON


# settings.review.verdict_max_age_hours = <hours> | 0 | "off"  (default 24) — REVIEW-FIX.R2.
#
# How old a `review_verdict` may be and still satisfy the ship gate. This closes the residual R1
# filed: with exactly ONE run in a repo the verdict key resolves the same way forever, so a PASS
# recorded a week ago — before a `/clear`, before more work landed — still satisfied `/ship`, and
# nothing on disk distinguished "just reviewed" from "reviewed then kept building". Recency is the
# only signal that separates them, and the log already records it.
#
# 24 hours is the default because the gap this gate exists to police is review→ship inside one piece
# of work: minutes to hours, overnight at the outside. A day is generous for that and still refuses
# last week's evidence. It is a bound on EVIDENCE, not on people — the remedy is one command and
# re-reviewing code you have since changed is the correct thing to do anyway. Teams that want a
# longer leash (a Friday review landing on Monday) set the hours; `0` or `off` disables the bound.
REVIEW_VERDICT_MAX_AGE_HOURS = 24.0


def review_verdict_max_age_hours(surface: Any) -> float:
    """The saved `settings.review.verdict_max_age_hours` bound, in hours; `0.0` means NO bound
    (REVIEW-FIX.R2). Default `REVIEW_VERDICT_MAX_AGE_HOURS`.

    Degrade-clean in the SAFE direction, and the direction matters both ways: an unreadable or
    nonsense value falls back to the DEFAULT bound (a broken setting can never silently disable the
    freshness check), while only an explicit `0`/`off` turns it off. Mirrors
    `review_independent_mode`'s shape — same settings block, same accessor style."""
    try:
        s = surface.manifest.setting("review", {}) or {}
        raw = s.get("verdict_max_age_hours", REVIEW_VERDICT_MAX_AGE_HOURS) \
            if isinstance(s, dict) else REVIEW_VERDICT_MAX_AGE_HOURS
    except AttributeError:
        # A surface with no `.manifest` (a double, a half-built surface) — the bound stays ON.
        return REVIEW_VERDICT_MAX_AGE_HOURS
    if isinstance(raw, bool):                       # `true`/`false` is not an age
        return REVIEW_VERDICT_MAX_AGE_HOURS
    if isinstance(raw, str) and raw.strip().lower() == "off":
        return 0.0
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return REVIEW_VERDICT_MAX_AGE_HOURS
    return hours if hours > 0 else 0.0


def _event_age_hours(event: Dict[str, Any]) -> Optional[float]:
    """Hours between `event`'s envelope `ts` and now, or None when there is no readable stamp.

    Grounded, not invented: `append_event` has stamped every entry with an ISO-8601 UTC `ts` since
    Stage 6b and `ts` is one of the pinned `ENVELOPE_KEYS`, so the timestamp this stage needs was
    ALREADY on the record — no new field, no schema change, and every verdict written by any shipped
    mokata carries one. None therefore does not mean "an old release wrote this"; it means the line
    was hand-edited or torn, which `ship_review_gate` treats as stale (see there).

    A negative age (a `ts` in the future — clock skew, an imported log) reads as fresh: the gate's
    job is to refuse STALE evidence, and mokata is not the arbiter of the system clock."""
    ts = event.get("ts") if isinstance(event, dict) else None
    if not isinstance(ts, str) or not ts:
        return None
    # THE one ISO lexer (`memory.lifecycle.parse_iso`): same never-raise contract this function
    # already had, same naive-is-UTC reading, and it lexes a `…Z` stamp on the declared 3.10 floor
    # where a bare `fromisoformat` does not. Imported inside the function deliberately — this module
    # loads today WITHOUT pulling `mokata.memory`, and a top-level import would drag that whole
    # package (backends, embeddings, vector store) into every importer of this light module.
    from .memory.lifecycle import parse_iso

    when = parse_iso(ts)
    if when is None:
        return None
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600.0


# ============================================================ REVIEW-FIX.R4 — the read-ERROR answer
#
# The sentences BOTH surfaces say when the verdict could not be READ, built in one place for the
# same reason R3's record lines are (`runviews.review_recorded_line`): one truth source deserves one
# wording, and a second copy is a drift class. The CLI prints `message` (+ `→ to unblock: …`) and the
# MCP `review_status` tool returns both fields — neither composes its own sentence.
#
# SECRET-SAFETY BAR (R3's, binding on both surfaces since R4): NO `{exc}` and no log content reaches
# these strings. A parse fault quotes the offending line, and a `review_verdict` line carries
# FINDINGS text, which can quote project content — so the fault is named by its KIND (a fixed
# vocabulary) and located by its PATH (derived from the repo root, never from the log's bytes).
_READ_FAULT_CLAUSE = {
    FAULT_UNREADABLE: "exists but could not be opened",
    FAULT_CORRUPT: "has damaged lines, so a recorded verdict may have been skipped",
    FAULT_UNKNOWN: "could not be read",
}


def review_read_error_message(fault: str, path: str) -> str:
    """The one line a read-ERROR reports. It says what is broken and, explicitly, what it is NOT —
    because the answer it replaces ("review hasn't run") sent operators to re-run a review that
    would not have fixed anything."""
    clause = _READ_FAULT_CLAUSE.get(fault, _READ_FAULT_CLAUSE[FAULT_UNKNOWN])
    return (f"review evidence could not be READ — the progress-event log ({path}) {clause}; "
            f"this is NOT 'review hasn't run'")


def review_read_error_unblock(path: str) -> str:
    """The remedy, which is the whole point of distinguishing this answer: repair the LOG. One
    wording for all three fault kinds — the operator's next action is the same in each, and moving
    the log aside (never deleting it) keeps the damaged evidence available to diagnose."""
    return (f"repair the log, not the review: inspect `{path}` (a torn LAST line is the usual "
            f"casualty) and fix its permissions or remove the damaged line(s), then re-read "
            f"`mokata progress review-status`. If it is beyond repair, move it aside "
            f"(`mv {path} {path}.corrupt`) and re-run /mokata:review to record a fresh verdict — "
            f"re-running review alone will NOT fix a log mokata cannot read")


def _read_fault_for(surface: Any) -> Optional[str]:
    """`ProgressLog.read_fault` for a SURFACE, made total. A surface too broken to even name its
    log cannot be read from either, so it reports `FAULT_UNKNOWN` rather than raising into
    `ship_review_gate`, whose degrade-clean contract (Stage 6r) is that it always returns a gate."""
    try:
        log = ProgressLog.from_surface(surface)
    except (AttributeError, OSError, TypeError, ValueError):
        return FAULT_UNKNOWN
    return log.read_fault()


def review_log_path(surface: Any) -> str:
    """Where this surface's progress-event log lives, for the read-error sentences. Total: a
    broken/half-built surface (or None) falls back to the bare filename, because a message that
    cannot name the exact path is still far more use than one that names the wrong remedy."""
    try:
        return ProgressLog.from_surface(surface).path
    except (AttributeError, OSError, TypeError, ValueError):
        return PROGRESS_EVENTS_FILENAME


#: D4 ruling — an independent pass with no subagent observed: credited as inline, and said so.
UNOBSERVED_INDEPENDENT_MESSAGE = ("review passed (inline — independence claimed, but no subagent "
                                  "of this run was observed)")


@dataclass(frozen=True)
class ReviewGate:
    """Ship's read of the persisted review verdict — evidence, not conversation context.

    `blocks` is True when ship must STOP (no verdict recorded, the verdict could not be READ, or
    the review FAILED). An inline (non-independent) PASS never blocks — capability-degraded
    harnesses must still ship — but `independent` is False so ship makes the difference visible
    and logs it."""

    present: bool          # a verdict was recorded at all
    passed: bool           # the review passed
    independent: bool      # ran as a fresh-context subagent (vs inline two-pass)
    blocks: bool           # ship must STOP
    message: str           # the one line ship surfaces
    unblock: str = ""      # the single action that clears a block
    # D4 — for a PASS recorded as independent: True when a subagent of the run was SEEN to finish at
    # or before it, False when none was (then `independent` is False: credited as inline), None when
    # the verdict did not claim independence or did not pass.
    observed: Optional[bool] = None
    # REVIEW-FIX.R4 — False when the verdict could not be READ (an unreadable/damaged log), as
    # opposed to read fine and found ABSENT. Both block; they need different remedies. Additive
    # with a True default, so every pre-R4 construction of this gate keeps its exact meaning.
    readable: bool = True


def ship_review_gate(surface: Any, run_id: Optional[str] = None) -> ReviewGate:
    """Derive ship's review gate from the PERSISTED verdict (Stage 6r).

    No verdict -> BLOCK (`review hasn't run — run /mokata:review first`). A failed verdict ->
    BLOCK. A passed verdict -> proceed, surfacing whether it was independent (`review passed
    (independent ✓)`) or inline (`review passed (inline — not independent)`). Ship does NOT
    hard-block on inline; it surfaces + logs the weaker signal.

    REVIEW-FIX.R1 — an omitted `run_id` resolves SESSION-AWARELY (`_resolve_verdict_run`, the same
    resolution `record_review_verdict` records under), and a run that cannot be resolved BLOCKS
    with the remedy instead of reading the stream unfiltered. Ordering-independent by construction:
    the resolution ignores B-LIFE ship retirement, so `mokata progress mark ship` (which ship.md
    records on ENTRY, before this read) cannot re-key the gate away from its own run.

    REVIEW-FIX.R2 — two window fixes on the READ, neither of which changes what a fresh, correctly
    keyed verdict does: (a) the verdict is found by a bounded BACKWARD scan, so a busy shared log
    can no longer bury this run's own evidence and produce a false "review hasn't run"; (b) a PASS
    older than `settings.review.verdict_max_age_hours` (default 24h) BLOCKS as stale, closing R1's
    filed residual — a single-run repo used to satisfy `/ship` forever off one week-old verdict."""
    if run_id is None:
        run_id = _resolve_verdict_run(surface)
    if run_id is None:
        # FAIL CLOSED — no run to attribute a review to. The old code read the whole stream here and
        # could pass the gate on a foreign/stale verdict; there is no evidence a gate can trust
        # without a run to key it to, so ship stops and the remedy names the run explicitly.
        return ReviewGate(
            present=False, passed=False, independent=False, blocks=True,
            message="review evidence has no run to attribute it to — mokata will not read "
                    "another run's verdict",
            unblock="name the run: `mokata progress review-status --run <run id>` "
                    "(`mokata sessions` lists them), recording the verdict under the same "
                    "`--run`; or start a tracked run with /mokata:brainstorm")
    event = latest_review_verdict_event(surface, run_id=run_id)
    v = event.get("data") if event else None
    if not isinstance(v, dict):
        # REVIEW-FIX.R4 — nothing came back, and WHY decides the remedy. The read below is
        # degrade-clean by design (an unreadable log yields nothing, a torn line is skipped), which
        # means an on-disk fault and a genuinely un-reviewed run arrive here as the SAME silence.
        # Ask the log which it was. Both answers BLOCK — fail-closed is unchanged and non-negotiable
        # — but one sends the operator to /mokata:review and the other to the log, and sending them
        # to the wrong one is the defect. Probed HERE, in the one gate both surfaces read, so the CLI
        # and the MCP twin cannot drift into two different answers.
        fault = _read_fault_for(surface)
        if fault:
            path = review_log_path(surface)
            return ReviewGate(present=False, passed=False, independent=False, blocks=True,
                              readable=False,
                              message=review_read_error_message(fault, path),
                              unblock=review_read_error_unblock(path))
        return ReviewGate(present=False, passed=False, independent=False, blocks=True,
                          message="review hasn't run — run /mokata:review first",
                          unblock="run /mokata:review first")
    passed = bool(v.get("passed"))
    independent = bool(v.get("independent"))
    if not passed:
        return ReviewGate(present=True, passed=False, independent=independent, blocks=True,
                          message="review failed — findings are unresolved",
                          unblock="run /mokata:review and address its findings")
    # REVIEW-FIX.R2 — a PASS that is too old is not evidence about the code being shipped. Checked
    # AFTER the failed branch so a stale FAIL keeps its more specific message (both remedies are the
    # same command). `passed=False` here is deliberate: the field is what the GATE concluded, and a
    # caller acting on `gate.passed` must not treat expired evidence as a pass.
    bound = review_verdict_max_age_hours(surface)
    age = _event_age_hours(event)
    if bound > 0 and (age is None or age > bound):
        ts = event.get("ts")
        when = ts if isinstance(ts, str) and ts else "at an unknown time"
        return ReviewGate(
            present=True, passed=False, independent=independent, blocks=True,
            message=f"review evidence is stale (recorded {when}); re-run /mokata:review",
            unblock=f"run /mokata:review again — the verdict is older than the "
                    f"{bound:g}h freshness bound (`mokata config set "
                    f"settings.review.verdict_max_age_hours <hours>` changes it; `off` disables)")
    if independent:
        # SUBAGENT-REVIEW-INSTRUCTED (0.0.22 D4) — "independent" was a claim nothing checked.
        # DERIVED here, never stored beside the verdict (§7f — a second stored fact drifts): was a
        # contracted subagent of THIS run seen to finish (SubagentStop's `subagent_finished`) at or
        # before the verdict?
        # RULING (2026-10-11, Jas delegated "decide what is best for the system"): an UNOBSERVED
        # claim is treated as INLINE — not blocked, not credited. Blocking it would be stricter than
        # an honest inline review, which ships; crediting it is the gap this row names. The gate
        # reports what it can prove, so a harness that cannot observe subagents (no hooks) reads
        # every review as inline, which is exactly what mokata can vouch for there.
        ts = event.get("ts")
        seen = [e for e in subagents_finished(surface.state.root, run_id)
                if not (isinstance(ts, str) and isinstance(e.get("ts"), str) and e["ts"] > ts)]
        if seen:
            return ReviewGate(present=True, passed=True, independent=True, blocks=False,
                              message="review passed (independent ✓)", observed=True)
        return ReviewGate(present=True, passed=True, independent=False, blocks=False,
                          message=UNOBSERVED_INDEPENDENT_MESSAGE, observed=False)
    return ReviewGate(present=True, passed=True, independent=False, blocks=False,
                      message="review passed (inline — not independent)")



# ======================================================================================
# R1.S1c — the typed projection
#
# ⭐ THIS MODULE PROMISED THIS IN 0.0.16 AND IT IS FINALLY TRUE. The docstring at the top
# declares the envelope "a COMPATIBLE SUBSET of 0.1.0's R1.S1a event stream, so R1.S1a can
# absorb this log as a superset rather than a second store to migrate." Absorbing it is what
# this does — and until now "forward-compatible" meant nothing had tried.
#
# ⚠ THE LOG STAYS. It is not replaced by the projection and must not be: `build_stage_badge`
# and the ship-review gate read it on the hot path with a BACKWARD scan that stops at the first
# match for the run it asks about (REVIEW-FIX.R2), and an answer that no longer depends on how
# much noise landed after the evidence is not a property to trade for tidiness.

def _project_event(log_path: str, entry: Dict[str, Any]) -> None:
    """Derive one typed event from a progress-log line. Never raises, never blocks.

    The three types map the way this module's own docstring said they would:

      * `stage_enter` → **PhaseTransition** `enter`; `run_finished` → **PhaseTransition**
        `(ship, finish)` — the run's last transition, so the typed stream sees the end too;
      * `review_verdict` → **GateDecision**, because a verdict IS a gate's decision and
        filing it as a phase transition would lose that — the review gate's own verdict is
        the thing an auditor looks for, and `progress_events`' docstring already separated
        "PhaseTransition (enter/pass)" from "verdict events" for the same reason.

    ⛔ NO `ledger_seq`. These events derive from the progress log, not from the audit ledger,
    so they carry no integrity claim — and a `ledger_seq` invented here would be the one thing
    `events.store`'s whole integrity story forbids (§7g)."""
    try:
        from .tdd_state import root_of_state_dir
        root = root_of_state_dir(os.path.dirname(log_path))
        if not root:
            return
        from .events import GateDecision, PhaseTransition, emit
        etype = str(entry.get("type", ""))
        stage = str(entry.get("stage", "") or "")
        run_id = entry.get("run_id")
        run_id = run_id if isinstance(run_id, str) else ""
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        if etype == REVIEW_VERDICT:
            verdict = data.get("verdict", data.get("decision", "recorded"))
            # No `reason` — see `events.schema.GateDecision`: a verdict's reason is free text
            # and the log already holds it.
            payload = GateDecision(gate="review", decision=str(verdict), subject=stage)
        elif etype == STAGE_ENTER:
            payload = PhaseTransition(phase=stage, outcome="enter")
        elif etype == RUN_FINISHED:
            payload = PhaseTransition(phase="ship", outcome="finish")
        elif etype == GATE_BLOCK:
            payload = GateDecision(gate=str(data.get("gate", "") or "gate"), decision="block",
                                   subject=stage)
        elif etype == SUBAGENT_FINISHED:
            handed = "handback" if "handback" in data else "no-handback"
            payload = GateDecision(gate="subagent-contract", decision=handed, subject=stage)
        else:
            return                     # an unknown type is not projected — see the register
        emit(root, payload, run_id=run_id)
    except Exception:  # noqa: BLE001 — observability never fails the write it describes
        return
