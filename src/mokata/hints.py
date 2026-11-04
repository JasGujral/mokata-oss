"""0.0.27 P14 `ID-NEXT-STEP-HINTS` — the next step, DERIVED from what mokata recorded, never guessed.

px0 shows a random tip fetched from its website. mokata says what THIS run owes next — "approve the
approach", "3 ACs have no test", "tests ran before your last write: re-run", "reviewed: land it" —
from the run's own recorded state, offline, with every hint citing the fact it came from. The rule
is summarised here.

ONE derivation, three surfaces: `mokata suggest` (no arguments), the MCP `suggest` tool (no
arguments), the IDE Activity panel (`/api/hints`); and bare `mokata`'s "next:" line. All four call
`derive_batch` / `derive`, so they cannot disagree.

THE ANSWER — `status` is exactly one of (doc 85 §7g: an absent answer and a real one never share a
representation):
  * `hints`   — one or more hints apply;
  * `none`    — ONLY from a positive fact: the run is recorded finished (rung C0);
  * `unknown` — a store the ladder needed could not be read, or a read hit its bound before the
                fact was settled; the reason names the store. Never rendered as "nothing to do".

THE RULE — at most: every R2 (a proposal waiting on you) + ONE pipeline hint (tier B or the tier C
ladder, never both) + R12. Tier B (writes are blocked) suppresses tier C, so a hint never says
"develop" while development writes are blocked; the tier C ladder is first-match, so two hints can
never give contradicting advice (`test_no_two_hints_give_conflicting_advice` checks the product).

  R1   identity: no run / several runs, none identifiably yours
  R2   an unapproved proposal for this run or its session chain        → mokata approve <id>
  B·R4 a spec amendment is open (development writes blocked)           → /mokata:spec
  B·R3 the newest gate decision blocks, nothing written since (live)   → the gate's unblock
  B·R3e the same block, in a session that ended                        → mokata resume
  C0   run finished (RUN_FINISHED or checkpoint completed_at)          → status none
  C1   approach not approved  C2 no emitted spec  C3 ACs with no test  C4 amendment owes RED
  C5   mapped tests never run C6 not current (a write / Bash call / test edit after the last suite)
  C6u  currentness cannot be settled (a read bound)                    → re-run (clears itself)
  C7   failing at the last run  C8 review missing/failed/stale/changed since  C9 reviewed → ship
  R12  the code graph is adopted but not built                         → mokata graph build

NEVER: a hint writes, spawns, calls git / the graph / the proof trace / a model, or reads a key. It
is text plus a command string; the human runs the command, and that command asks for its own
consent (P2). No caching anywhere in here — the cost is bounded by the run's state, not the repo.

READS (each three-state — ABSENT / UNREADABLE / OK — and read-only):
  run identity `run_resolver.resolve_run` · approach `approved_approach__<run>` · spec
  `emitted_spec__<run>` · amendment `spec_amend__<run>` · RED set `tdd_phase__<run>` · test record
  `test_runs__<run>` · checkpoint `pipeline_run__<run>` · proposals `approval.pending_strict` · gate
  decisions + Bash calls `EventStore.read_run_readonly` (bracketed, writes nothing) · review verdicts
  + finishes: the progress log, read BACKWARD once per batch until each run's start · writes: the
  snapshot timeline (bounded by its own pruning), read once per batch · the code graph: one `os.path.exists`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import ControlSignal, MokataError
from .plugin_runtime import slash_command

HINTS_VERSION = 1
STATUS_HINTS, STATUS_NONE, STATUS_UNKNOWN = "hints", "none", "unknown"

#: rule ids (the IDE, the CLI and the tests switch on these)
R_INIT, R_NO_RUN, R_AMBIGUOUS = "init", "no-run", "ambiguous"
R_APPROVE = "approve"
R_AMEND_OPEN, R_GATE, R_GATE_ENDED = "amend-open", "gate-block", "gate-block-ended"
C_APPROACH, C_SPEC, C_UNMAPPED, C_RED_OWED, C_NEVER_RUN = (
    "approach", "spec", "unmapped-acs", "red-owed", "never-run")
C_RERUN, C_RERUN_UNSURE, C_FAILING = "rerun", "rerun-unsure", "failing"
C_REVIEW, C_REVIEW_FAILED, C_SHIP, C_FINISHED = "review", "review-failed", "ship", "finished"
R_GRAPH = "graph-unbuilt"

#: the ADVICE each rule gives — what the conflict property test reasons over
ADVICE = {R_INIT: "init", R_NO_RUN: "start", R_AMBIGUOUS: "pick-run", R_APPROVE: "approve",
          R_AMEND_OPEN: "writes-blocked", R_GATE: "writes-blocked", R_GATE_ENDED: "resume",
          C_APPROACH: "approve-approach", C_SPEC: "spec", C_UNMAPPED: "test",
          C_RED_OWED: "test", C_NEVER_RUN: "run-tests", C_RERUN: "re-run",
          C_RERUN_UNSURE: "re-run", C_FAILING: "develop", C_REVIEW: "review",
          C_REVIEW_FAILED: "develop", C_SHIP: "ship", C_FINISHED: "done", R_GRAPH: "build-graph"}

#: a session with no end marker and activity within this window is live (the IDE's own window)
LIVE_SECS = 15 * 60
#: one test id / text field longer than this is clipped when shown
TEXT_MAX = 120
#: gate decisions / tool calls read per run (newest) — see `_gate_facts` for why the gate answer
#: is complete at ANY limit ≥ 1, and `_bash_after` for what a full page of tool calls means
EVENT_ROWS = 500
#: timeline / progress-log reads tolerate this much clock skew between writers (seconds)
SKEW = 5.0
#: the progress log's backward-scan bound (the same 8 MiB the ship gate reads under)
LOG_BYTE_CAP = 8 * 1024 * 1024
_BLOCK = 64 * 1024


class HintRefused(MokataError):
    """A hint with no cited fact — a guess. Never constructed (HARD: a bug in a rule, not a state
    of the repo — there is no floor to fall back to)."""


@dataclass(frozen=True)
class Cite:
    """The recorded fact a hint came from: which store, which row/key, when."""
    source: str
    ref: str
    at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "ref": self.ref, "at": self.at}


@dataclass(frozen=True)
class Hint:
    rule: str
    text: str
    command: str
    cites: Tuple[Cite, ...]
    writes: bool = False          # the command makes a (gated) durable write — it will ask
    optional: bool = False        # offered, not owed (C0's "start the next run")

    def __post_init__(self) -> None:
        if not self.cites:
            raise HintRefused(f"hint '{self.rule}' cites no recorded fact — a hint is derived, "
                              "never guessed")

    @property
    def key(self) -> str:
        """The dismissal key: the rule + THIS hint's own cited facts — an unrelated run's events,
        a token spend or the shared log growing never change it; its own fact changing does."""
        raw = json.dumps([self.rule, [[c.source, c.ref, c.at] for c in self.cites]],
                         sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "advice": ADVICE.get(self.rule, ""), "text": self.text,
                "command": self.command, "writes": self.writes, "optional": self.optional,
                "key": self.key, "cites": [c.to_dict() for c in self.cites]}


# ------------------------------------------------------------------------------------ helpers

def _clip(s: Any, n: int = TEXT_MAX) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _ago(t: Optional[float], now: float) -> str:
    if not t:
        return "at an unknown time"
    s = max(0, int(now - t))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def _epoch(ts: Any) -> float:
    from .gate_blocks import epoch_of
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    return epoch_of(ts)


def _names(ids: Sequence[str], n: int = 3) -> str:
    shown = ", ".join(_clip(i, 60) for i in ids[:n])
    return shown + (f" and {len(ids) - n} more" if len(ids) > n else "")


class _Unknown(ControlSignal):
    """A fact the ladder needed could not be settled. Carries the honest reason. A CONTROL signal,
    not a failure: caught one frame up (`derive_batch`), where it becomes `status: unknown`."""


@dataclass
class _Read:
    """Byte / row counts of one batch — reported in `basis.read`, so V-SCALE can assert them."""
    log_bytes: int = 0
    timeline_lines: int = 0
    event_rows: int = 0
    state_files: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {"log_bytes": self.log_bytes, "timeline_lines": self.timeline_lines,
                "event_rows": self.event_rows, "state_files": self.state_files}


# ----------------------------------------------------------------------- per-run state reads

def _state_dir(root: str) -> str:
    from .tdd_state import state_dir
    return state_dir(root)


def _strict(root: str, name: str, rd: _Read) -> Any:
    from .state import StateStore
    rd.state_files += 1
    return StateStore(_state_dir(root)).read_strict(name)


def _need(got: Any, what: str) -> Any:
    """OK → the value; ABSENT → None; UNREADABLE → `_Unknown` naming the store."""
    if got.state == "ok":
        return got.value
    if got.state == "absent":
        return None
    raise _Unknown(f"{what} {got.reason} ({os.path.basename(got.path)}) — `mokata doctor`")


def _canon(test_id: str) -> Tuple[str, str, str]:
    """One identity for every spelling of a test id: `./t.py::x`, `t.py::x` and `t.py::x` are one."""
    from .test_ids import test_key_of, test_param_of, test_path_of
    return (test_path_of(test_id), test_key_of(test_id), test_param_of(test_id))


def _label(c: Tuple[str, str, str]) -> str:
    path, key, param = c
    return (f"{path}::{key.replace('.', '::')}" if path else key) + (f"[{param}]" if param else "")


@dataclass
class _Run:
    """What one subject's ladder reads — gathered lazily, so a rung only pays for its facts."""
    root: str
    run: str
    chain: List[str]
    rd: _Read
    now: float
    log: Dict[str, Any] = field(default_factory=dict)        # filled by the batch log scan
    _cache: Dict[str, Any] = field(default_factory=dict)

    def get(self, name: str, fn: Any) -> Any:
        if name not in self._cache:
            self._cache[name] = fn()
        return self._cache[name]

    # -- run state
    def checkpoint(self) -> Dict[str, Any]:
        from .govern.resume import CHECKPOINT_PREFIX
        return self.get("checkpoint", lambda: _need(
            _strict(self.root, CHECKPOINT_PREFIX + self.run, self.rd),
            "the run's checkpoint") or {})

    def approach(self) -> Optional[Dict[str, Any]]:
        def read() -> Optional[Dict[str, Any]]:
            from .brainstorm import Handoff
            data = _need(_strict(self.root, f"approved_approach__{self.run}", self.rd),
                         "the approved approach")
            if not data:
                return None
            try:
                h = Handoff.from_dict(data)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise _Unknown(f"the approved approach is present but unreadable "
                               f"({type(exc).__name__}) — `mokata doctor`") from exc
            return {"approver": h.approver, "at": h.approved_at}
        return self.get("approach", read)

    def spec(self) -> Optional[Any]:
        def read() -> Optional[Any]:
            from .engine.spec import Spec
            data = _need(_strict(self.root, f"emitted_spec__{self.run}", self.rd),
                         "the emitted spec")
            if not data:
                return None
            try:
                return Spec.from_dict(data)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise _Unknown(f"the emitted spec is present but unreadable "
                               f"({type(exc).__name__}) — `mokata spec show`") from exc
        return self.get("spec", read)

    def amend(self) -> Any:
        def read() -> Any:
            from .spec_scope import NO_AMEND, STATUS_CLOSED, STATUS_OPEN, amend_from_state, \
                amend_key
            data = _need(_strict(self.root, amend_key(self.run), self.rd),
                         "the spec-amendment record")
            if data is None:
                return NO_AMEND
            if not isinstance(data, dict) or data.get("status") not in (STATUS_OPEN,
                                                                        STATUS_CLOSED):
                # `amend_from_state` reads this as "no amendment" — its fail-OPEN floor. A hint
                # must not: a record is there and says nothing it can trust.
                raise _Unknown("the spec-amendment record has the wrong shape — "
                               "`mokata doctor`")
            return amend_from_state(data)
        return self.get("amend", read)

    def red(self) -> set:
        def read() -> set:
            from .tdd_state import from_state, state_key
            data = _need(_strict(self.root, state_key(self.run), self.rd), "the TDD record")
            return set(from_state(data)[0]) if data else set()
        return self.get("red", read)

    def tests(self) -> Optional[Dict[str, Any]]:
        def read() -> Optional[Dict[str, Any]]:
            from .test_runs import read_test_record_strict
            self.rd.state_files += 1
            got = read_test_record_strict(self.root, self.run)
            return _need(got, "the test record")
        return self.get("tests", read)

    # -- events (gate decisions, Bash calls) — bracketed, write-free
    def events(self, typ: str) -> Any:
        def read() -> Any:
            from .events.store import store_for_root
            r = store_for_root(self.root).read_run_readonly(
                run_id=self.run, session_ids=self.chain, types=[typ], limit=EVENT_ROWS)
            self.rd.event_rows += len(r.rows)
            if r.fault:
                raise _Unknown(f"{r.fault} — `mokata doctor`")
            return r
        return self.get("events:" + typ, read)


# ------------------------------------------------------------------ the batch backward scans

def _scan_log(root: str, runs: Dict[str, "_Run"], rd: _Read) -> None:
    """ONE backward read of the shared progress log for every run in the batch. Per run: its
    newest `review_verdict`, whether a `run_finished` exists, and whether the scan reached the
    run's START (its checkpoint `created_at`, minus skew) — only then is "no verdict" / "not
    finished" ABSENT rather than unknown. A corrupt line inside the window, an unreadable log or
    the byte bound reached before a run's start are recorded per run as the reason; the ladder
    raises it only if it needs that fact."""
    from .progress_events import (PROGRESS_EVENTS_FILENAME, REVIEW_VERDICT, RUN_FINISHED)
    path = os.path.join(_state_dir(root), PROGRESS_EVENTS_FILENAME)
    want: Dict[str, Dict[str, Any]] = {}
    for rid, r in runs.items():
        try:
            created = r.checkpoint().get("created_at")
        except _Unknown as exc:
            r.log = {"fault": str(exc)}
            continue
        start = float(created) - SKEW if isinstance(created, (int, float)) and \
            not isinstance(created, bool) else None
        r.log = {"verdict": None, "finished": None, "settled": False, "fault": "",
                 "start": start}
        want[rid] = r.log
    if not want:
        return
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        for st in want.values():
            st["settled"] = True                       # no log at all: nothing was recorded
        return
    except OSError as exc:
        for st in want.values():
            st["fault"] = f"the progress log could not be read ({type(exc).__name__})"
        return
    open_runs = set(want)
    try:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        floor = max(0, size - LOG_BYTE_CAP)
        pos, carry = size, b""

        def take(raw: bytes) -> None:
            line = raw.strip()
            if not line:
                return
            try:
                e = json.loads(line)
            except ValueError:
                e = None
            if not isinstance(e, dict):
                for rid in list(open_runs):
                    want[rid]["fault"] = ("the progress log has a damaged line in the window "
                                          "read — `mokata doctor`")
                    open_runs.discard(rid)
                return
            t = _epoch(e.get("ts"))
            for rid in list(open_runs):
                st = want[rid]
                if st["start"] is not None and t and t < st["start"]:
                    st["settled"] = True               # past this run's start: nothing older
                    open_runs.discard(rid)
                    continue
                if e.get("run_id") != rid:
                    continue
                if e.get("type") == RUN_FINISHED and st["finished"] is None:
                    st["finished"] = e
                elif e.get("type") == REVIEW_VERDICT and st["verdict"] is None and \
                        isinstance(e.get("data"), dict):
                    st["verdict"] = e
                if st["finished"] is not None:
                    st["settled"] = True               # C0 decides; nothing older matters
                    open_runs.discard(rid)

        while pos > floor and open_runs:
            step = min(_BLOCK, pos - floor)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step)
            rd.log_bytes += len(chunk)
            parts = (chunk + carry).split(b"\n")
            carry = parts.pop(0)
            for raw in reversed(parts):
                take(raw)
                if not open_runs:
                    break
        if open_runs and pos == 0:
            if carry:
                take(carry)
            for rid in list(open_runs):
                want[rid]["settled"] = True            # read the whole log
                open_runs.discard(rid)
        for rid in open_runs:
            want[rid]["fault"] = (f"the progress log's read bound ({LOG_BYTE_CAP // (1024 * 1024)}"
                                  f" MiB) was reached before run {rid}'s start")
    except OSError as exc:
        for rid in open_runs:
            want[rid]["fault"] = f"the progress log could not be read ({type(exc).__name__})"
    finally:
        fh.close()


def timeline_path(root: str) -> str:
    """The snapshot timeline — `ide/snapshots._timeline_path`'s file, located here WITHOUT
    importing `mokata.ide` (the hints serve the CLI and MCP without the `[ide]` extra)."""
    return os.path.join(os.path.dirname(_state_dir(root)), "snapshots", "timeline.jsonl")


#: `ide/snapshots.MAX_ENTRIES` — the timeline keeps the newest this-many lines (pinned equal by a
#: test, so the two cannot drift).
TIMELINE_KEEP = 20000


def _scan_timeline(root: str, runs: Dict[str, "_Run"], rd: _Read) -> Dict[str, Any]:
    """ONE read of the snapshot timeline for the batch: per run, the times of its AFTER captures
    (its recorded writes). The file is bounded by its own pruning (`TIMELINE_KEEP` lines, kept
    newest), so a whole read is bounded too. `pruned` + `oldest` say how far back "no write after
    X" can be SETTLED: always when the file was never pruned, else only for X at or after its
    oldest line."""
    path = timeline_path(root)
    out: Dict[str, Any] = {"writes": {rid: [] for rid in runs}, "oldest": None,
                           "pruned": False, "fault": ""}
    members = {rid: (r.run, set(r.chain)) for rid, r in runs.items()}
    lines = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                lines += 1
                try:
                    d = json.loads(raw)
                except ValueError:
                    out["fault"] = "the snapshot timeline has a damaged line — `mokata doctor`"
                    break
                if not isinstance(d, dict):
                    continue
                try:
                    t = float(d.get("t") or 0.0)
                except (TypeError, ValueError):
                    t = 0.0
                if out["oldest"] is None or t < out["oldest"]:
                    out["oldest"] = t
                if d.get("kind") != "after":
                    continue
                for rid, (run, chain) in members.items():
                    if (run and d.get("run") == run) or (d.get("session") in chain):
                        out["writes"][rid].append(t)
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError) as exc:
        out["fault"] = f"the snapshot timeline could not be read ({type(exc).__name__})"
    rd.timeline_lines += lines
    out["pruned"] = lines >= TIMELINE_KEEP
    return out


def _writes_after(tl: Dict[str, Any], rid: str, x: float) -> Tuple[Optional[int], str]:
    """(how many recorded writes after `x`, "") when settled; (None, why) when the timeline
    cannot say (it was pruned past `x`)."""
    if tl["fault"]:
        raise _Unknown(tl["fault"])
    n = sum(1 for t in tl["writes"].get(rid, []) if t > x)
    if n:
        return n, ""
    oldest = tl.get("oldest")
    if not tl["pruned"] or (oldest is not None and oldest <= x):
        return 0, ""
    return None, (f"the snapshot timeline keeps only the newest {TIMELINE_KEEP:,} writes, and "
                  "they all came after that moment")


def _latest_write(tl: Dict[str, Any], rid: str) -> Optional[float]:
    w = tl["writes"].get(rid) or []
    return max(w) if w else None


# ----------------------------------------------------------------------------- liveness

def session_live(root: str, chain: Sequence[str], run: str, now: float) -> Tuple[bool, str]:
    """(live?, basis) for a session chain — from records only, no process spawn: a SessionEnd
    marker on the newest session's last segment = ended; its recorded Claude pid gone = ended;
    otherwise a heartbeat within `LIVE_SECS`, or the run's MCP process registered alive, = live."""
    from . import session_index as SI
    from .run_resolver import live_runs
    from .session_registry import pid_alive
    for sid in list(chain)[:1]:
        rec = SI.read(root, sid) or {}
        segs = rec.get("segments") or []
        if segs and isinstance(segs[-1], dict):
            seg = segs[-1]
            if seg.get("end"):
                return False, "SessionEnd recorded"
            proc = seg.get("proc")
            if isinstance(proc, list) and proc and not pid_alive(proc[0]):
                return False, "its Claude Code process is gone"
        hb = float(rec.get("heartbeat") or 0.0)
        if hb and now - hb <= LIVE_SECS:
            return True, f"activity {_ago(hb, now)}"
    if run and live_runs(root, {run}):
        return True, "its run's MCP process is registered alive"
    return False, "no SessionEnd, and no activity recorded in the last 15 min"


# ----------------------------------------------------------------------------- the ladder

def _gate_facts(r: _Run, latest_write: Optional[float]) -> Optional[Any]:
    """The standing block, or None. COMPLETE at any page size: a block stands only while it is the
    NEWEST gate decision (any later non-blocking decision supersedes it — `gate_blocks`), and the
    newest decisions are exactly what the newest-first page holds."""
    from .events.schema import GATE_DECISION
    from .gate_blocks import pending_block
    page = r.events(GATE_DECISION)
    return pending_block(list(reversed(page.rows)), latest_write or 0.0)


def _bash_after(r: _Run, x: float) -> Tuple[Optional[List[float]], str]:
    """Bash calls recorded after `x` (newest first), or (None, why) when the page cannot say."""
    from .events.schema import TOOL_CALL
    page = r.events(TOOL_CALL)
    times = [_epoch(e.ts) for e in page.rows if str((e.data or {}).get("tool")) == "Bash"]
    after = [t for t in times if t > x]
    if after:
        return after, ""
    if page.truncated:
        oldest = min((_epoch(e.ts) for e in page.rows), default=None)
        if oldest is None or oldest > x:
            return None, (f"more than {EVENT_ROWS} tool calls since the last test run "
                          "(the read stopped there)")
    return [], ""


def _bridge(root: str) -> Tuple[bool, str]:
    from pathlib import Path
    from .harness_setup import LOCAL_SETTINGS, SetupError, ide_bridge_surfaces
    try:
        return bool(ide_bridge_surfaces(Path(root) / ".claude" / LOCAL_SETTINGS)), ""
    except SetupError as exc:
        return False, _clip(str(exc), 200)
    except OSError as exc:
        return False, type(exc).__name__


def _unblock_command(gate: str) -> Tuple[str, str]:
    from .legibility import GATE_UNBLOCK
    line = GATE_UNBLOCK.get(gate or "", "")
    cmd = ""
    if "`" in line:
        cmd = line.split("`")[1]
    return line, cmd or "mokata progress"


def _tier_b(r: _Run, tl: Dict[str, Any], live: bool, live_basis: str,
            finished: bool) -> Optional[Hint]:
    am = r.amend()
    if am.is_open:
        return Hint(R_AMEND_OPEN,
                    f"spec amendment v{am.from_version} → v{am.to_version} is open: development "
                    "writes are blocked until it closes" +
                    (f" ({_clip(am.reason, 80)})" if am.reason else ""),
                    "/mokata:spec", (Cite("state", f"spec_amend__{r.run}"),), writes=True)
    block = _gate_facts(r, _latest_write(tl, r.run) if not tl["fault"] else None)
    if block is None:
        return None
    if tl["fault"]:
        raise _Unknown(tl["fault"])
    gate = str((block.data or {}).get("gate", ""))
    at = _epoch(block.ts)
    n_after, why = _writes_after(tl, r.run, at + 1.0)
    if n_after is None:
        raise _Unknown(f"cannot tell whether anything was written after the gate block ({why})")
    cite = (Cite("events", f"gate_decision#{block.seq}", at),)
    if not live:
        if finished:
            return None                                   # a finished run's blocks are history
        return Hint(R_GATE_ENDED,
                    f"gate '{_clip(gate, 40)}' blocked {_ago(at, r.now)} in a session that ended "
                    f"({live_basis}) — resume the run to clear it",
                    "mokata resume", cite)
    secret = "secret" in gate.lower()
    line, cmd = _unblock_command(gate)
    if secret:
        text = (f"a write was blocked for a secret {_ago(at, r.now)}: remove it — a security "
                "block cannot be approved away")
    else:
        subj = _clip((block.data or {}).get("subject", ""), TEXT_MAX)
        text = (f"gate '{_clip(gate, 40)}' blocked {subj or 'a write'} {_ago(at, r.now)}; "
                "nothing written since" + (f" — {line}" if line else ""))
    return Hint(R_GATE, text, cmd, cite)


def _ladder(r: _Run, tl: Dict[str, Any], bridge: Tuple[bool, str], live: bool,
            live_basis: str) -> Tuple[str, List[Hint], str]:
    """(status, hints, none-reason) for one run's pipeline part — tier B or tier C, never both."""
    cp = r.checkpoint()
    finished_ev = r.log.get("finished") if r.log else None
    completed = cp.get("completed_at") if isinstance(cp.get("completed_at"), str) else ""
    finished = bool(finished_ev or completed)
    b = _tier_b(r, tl, live, live_basis, finished)
    if b is not None:
        return STATUS_HINTS, [b], ""
    # ---- C0: a recorded finish is the ONLY `none`
    if finished:
        when = (finished_ev or {}).get("ts") or completed
        landing = ((finished_ev or {}).get("data") or {}).get("landing", "")
        cite = (Cite("progress_log", f"run_finished:{r.run}", _epoch(when)) if finished_ev
                else Cite("state", f"pipeline_run__{r.run}.completed_at", _epoch(completed)))
        reason = f"run {r.run} finished {when}" + (f" ({landing})" if landing else "")
        return STATUS_NONE, [Hint(C_FINISHED, reason + " — start the next run when you are "
                                  "ready", slash_command("brainstorm", r.root), (cite,), optional=True)], reason
    if r.log.get("fault"):
        raise _Unknown(r.log["fault"])
    # ---- C1 / C2
    ap = r.approach()
    if ap is None:
        return STATUS_HINTS, [Hint(C_APPROACH, "the approach is waiting for your approval",
                                   slash_command("brainstorm", r.root),
                                   (Cite("state", f"approved_approach__{r.run}: absent"),),
                                   writes=True)], ""
    spec = r.spec()
    if spec is None:
        return STATUS_HINTS, [Hint(C_SPEC, "the approach is approved; no spec has been emitted",
                                   "/mokata:spec",
                                   (Cite("state", f"approved_approach__{r.run}",
                                         _epoch(ap.get("at"))),
                                    Cite("state", f"emitted_spec__{r.run}: absent")),
                                   writes=True)], ""
    # ---- C3 / C4 / C5 (the test map)
    rec = r.tests() or {"last_suite": None, "ac_tests": {}, "planned": {}, "tests": {},
                        "qualified": {}}
    acs = [c.id for c in spec.criteria]
    mapped: Dict[str, List[str]] = {}
    for ac in acs:
        mapped[ac] = sorted(set(rec["ac_tests"].get(ac, [])) | set(rec["planned"].get(ac, [])))
    unmapped = [ac for ac in acs if not mapped[ac]]
    if unmapped:
        return STATUS_HINTS, [Hint(C_UNMAPPED, f"{len(unmapped)} acceptance criteria have no "
                                   f"test: {_names(unmapped, 5)}", "/mokata:test",
                                   tuple(Cite("spec", f"{r.run}:{ac}") for ac in unmapped))], ""
    am = r.amend()
    if am.red_owed:
        owed = list(am.owed(r.red()))
        if owed:
            return STATUS_HINTS, [Hint(C_RED_OWED, f"the amended spec owes a failing run for "
                                       f"{len(owed)} test(s): {_names(owed)}", "/mokata:test",
                                       (Cite("state", f"spec_amend__{r.run}.red_owed"),))], ""
    # one identity per test, whatever spelling recorded it
    alias = {}
    for full, spellings in rec.get("qualified", {}).items():
        for sp in spellings:
            alias[sp] = full
    hist: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for tid, h in rec["tests"].items():
        c = _canon(alias.get(tid, tid))
        cur = hist.get(c)
        if cur is None or (h.get("last_at") or 0) > (cur.get("last_at") or 0):
            hist[c] = dict(h, id=tid)
    wanted = sorted({_canon(alias.get(t, t)) for ts in mapped.values() for t in ts})
    never = [c for c in wanted if c not in hist or hist[c].get("last") is None]
    if never:
        return STATUS_HINTS, [Hint(C_NEVER_RUN, f"{len(never)} mapped test(s) have never run: "
                                   f"{_names([_label(c) for c in never])} — run them and "
                                   "expect RED", "mokata progress test-run --failed --command "
                                   "\"<your test command>\" --test <id>",
                                   (Cite("test_record", f"test_runs__{r.run}: no outcome for "
                                         f"{len(never)}"),))], ""
    # ---- C6 / C6u: are the outcomes still CURRENT?  T = the last suite run (ruling 3)
    suite = rec.get("last_suite") or {}
    t_suite = suite.get("at") if isinstance(suite.get("at"), (int, float)) else None
    T = float(t_suite) if t_suite is not None else max(
        float(hist[c].get("last_at") or 0.0) for c in wanted)
    bash_off = not bridge[0]
    tail = (" (writes made through Bash are not tracked: the IDE bridge is off — "
            "`mokata setup claude --ide-bridge`)") if bash_off else ""
    n_writes, why_w = _writes_after(tl, r.run, T)
    bash_times: Optional[List[float]] = []
    why_b = ""
    if bridge[0]:
        bash_times, why_b = _bash_after(r, T)
    stale_stamp = _stale_stamps(r.root, [hist[c] for c in wanted])
    latest = max([t for t in tl["writes"].get(r.run, [])] + (bash_times or []), default=0.0)
    if n_writes or bash_times or stale_stamp:
        behind = sorted({_label(c) for c in wanted
                         if float(hist[c].get("last_at") or 0.0) < latest} |
                        {_label(_canon(h["id"])) for h in stale_stamp})
        what = []
        if n_writes:
            what.append(f"{n_writes} write(s)")
        if bash_times:
            what.append(f"{len(bash_times)} Bash command(s)")
        if stale_stamp:
            what.append(f"{len(stale_stamp)} test(s) edited")
        cites = [Cite("test_record", f"test_runs__{r.run}.last_suite", T)]
        if n_writes:
            cites.append(Cite("timeline", f"{n_writes} write(s) after", latest or None))
        if bash_times:
            cites.append(Cite("events", f"tool_call Bash ×{len(bash_times)}", max(bash_times)))
        if stale_stamp:
            cites.append(Cite("test_record", "stamp changed: " + _names(
                [h["id"] for h in stale_stamp], 2)))
        return STATUS_HINTS, [Hint(C_RERUN, f"tests last ran {_ago(T, r.now)}; "
                                   f"{' and '.join(what)} since — re-run: "
                                   f"{_names(behind) if behind else 'the suite'}",
                                   "mokata progress test-run --passed|--failed --command "
                                   "\"<your test command>\"", tuple(cites))], ""
    if n_writes is None or bash_times is None:
        return STATUS_HINTS, [Hint(C_RERUN_UNSURE, f"cannot confirm the tests ran after the latest "
                                   f"change ({why_w or why_b}) — re-run them",
                                   "mokata progress test-run --passed|--failed --command "
                                   "\"<your test command>\"",
                                   (Cite("test_record", f"test_runs__{r.run}.last_suite", T),))], ""
    # ---- C7: failing at the last (current) run
    red = [c for c in wanted if hist[c].get("last") == "red"]
    if red:
        return STATUS_HINTS, [Hint(C_FAILING, f"{len(red)} test(s) failing at the last run "
                                   f"{_ago(T, r.now)}: {_names([_label(c) for c in red])}{tail}",
                                   "/mokata:develop",
                                   tuple(Cite("test_record", _label(c), hist[c].get("last_at"))
                                         for c in red[:20]))], ""
    # ---- C8 / C9: the review
    ev = r.log.get("verdict")
    if ev is None:
        return STATUS_HINTS, [Hint(C_REVIEW, f"tests are green; no review is recorded{tail}",
                                   "/mokata:review",
                                   (Cite("test_record", f"test_runs__{r.run}.last_suite", T),
                                    Cite("progress_log", f"review_verdict:{r.run}: absent")))], ""
    vt = _epoch(ev.get("ts"))
    vcite = Cite("progress_log", f"review_verdict:{ev.get('event_id', '')}", vt)
    if not ev["data"].get("passed"):
        return STATUS_HINTS, [Hint(C_REVIEW_FAILED, "review did not pass: address its findings, "
                                   "then review again", "/mokata:develop", (vcite,))], ""
    max_age = _max_age(r.root)
    if max_age > 0 and (not vt or (r.now - vt) / 3600.0 > max_age):
        return STATUS_HINTS, [Hint(C_REVIEW, f"the review is older than {max_age:g} h "
                                   f"(recorded {_ago(vt, r.now)}) — review again",
                                   "/mokata:review", (vcite,))], ""
    n_since, why_v = _writes_after(tl, r.run, vt)
    bash_v: Optional[List[float]] = []
    if bridge[0]:
        bash_v, why_b2 = _bash_after(r, vt)
        why_v = why_v or why_b2
    if n_since or bash_v:
        return STATUS_HINTS, [Hint(C_REVIEW, f"the code changed after the review "
                                   f"({_ago(vt, r.now)}) — review again", "/mokata:review",
                                   (vcite, Cite("timeline", "writes after the verdict")))], ""
    if n_since is None or bash_v is None:
        return STATUS_HINTS, [Hint(C_REVIEW, f"cannot confirm nothing changed after the review "
                                   f"({why_v}) — review again", "/mokata:review", (vcite,))], ""
    return STATUS_HINTS, [Hint(C_SHIP, f"reviewed {_ago(vt, r.now)}, tests green and current — "
                               f"ready to land{tail}", "/mokata:ship", (vcite,))], ""


def _stale_stamps(root: str, hs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The tests whose content stamp NOW differs from the stamp of their latest outcome (an edit to
    the test — Bash or not — since it ran). Only tests that recorded a stamp are compared."""
    from .test_runs import stamps_of
    with_stamp = [h for h in hs if h.get("stamp")]
    if not with_stamp:
        return []
    now = stamps_of(root, [h["id"] for h in with_stamp])
    return [h for h in with_stamp if h["id"] in now and now[h["id"]] != h["stamp"]]


def _max_age(root: str) -> float:
    from .progress_events import REVIEW_VERDICT_MAX_AGE_HOURS, review_verdict_max_age_hours
    from .config import ConfigError, Surface
    from .manifest import ManifestError
    try:
        return review_verdict_max_age_hours(Surface.load(root))
    except (ConfigError, ManifestError, OSError):
        return REVIEW_VERDICT_MAX_AGE_HOURS   # an unloadable surface keeps the bound ON (gate floor)


def _crg_state() -> Tuple[str, str, str]:
    """(state, path, why) for the code-review-graph mokata would run, decided WITHOUT a spawn and by
    the SAME predicates `crg_resolve.resolve_crg` uses (INTEG-FIX2 r2: the hint and the resolver
    agree on every case). state is one of:
      ok        — in mokata's environment, its installed version (dist-info) in range: `path`
      refused   — present but resolve_crg refuses it (no dist-info, version out of range): `why`
      unchecked — named by MOKATA_CRG: only a `--version` spawn could check it, and P14 spawns
                  nothing, so this is said as "cannot confirm", never as "run graph build"
      missing   — not installed (`why`)
      off       — MOKATA_CRG=off
    The environment case IS `resolve_crg()` (a PATH lookup + a dist-info directory listing)."""
    import shutil
    from .crg_resolve import OFF_VALUES, OVERRIDE_ENV, resolve_crg
    override = os.environ.get(OVERRIDE_ENV, "").strip()
    if override.lower() in OFF_VALUES:
        return "off", "", f"switched off ({OVERRIDE_ENV}={override})"
    if override:
        hit = shutil.which(override) if not os.path.isabs(override) else (
            override if os.path.isfile(override) and os.access(override, os.X_OK) else None)
        if not hit:
            return "refused", "", (f"{OVERRIDE_ENV} names {override}, which is not an "
                                   "executable — refused")
        return "unchecked", hit, (f"{OVERRIDE_ENV} names {hit}; its version cannot be confirmed "
                                  "without running it")
    res = resolve_crg()
    if res.ok:
        return "ok", res.path or "", ""
    if res.refused:
        return "refused", "", res.refused
    return "missing", "", "not installed in the environment mokata runs from"


R_GRAPH_MISSING = "graph-tool-missing"
ADVICE[R_GRAPH_MISSING] = "install-graph"


def _graph_hint(root: str) -> Tuple[Optional[Hint], str]:
    """R12: the committed manifest adopts a code graph and its index does not exist. ONE manifest
    read, ONE `os.path.exists` (the index is never opened, M6) and ONE presence check of the tool
    (INTEG-FIX2 ruling: the hint never claims what it has not checked — "run graph build" is said
    only when the tool is on disk; absent, it says so and how to install it)."""
    from .crg_resolve import CRG_COMMAND, INSTALL_HINT
    from .knowledge.crg_build import db_path
    from .knowledge.graph_adopt import _load_manifest_data, graph_pinned_tool
    try:
        tool = graph_pinned_tool(_load_manifest_data(root))
    except (OSError, ValueError) as exc:
        return None, f"code-graph check skipped: the manifest could not be read ({type(exc).__name__})"
    if not tool or os.path.exists(db_path(root)):
        return None, ""
    if tool != CRG_COMMAND:
        return None, f"code-graph check skipped: mokata cannot check whether {tool} is installed"
    state, path, why = _crg_state()
    if state == "off":
        return None, f"code graph adopted but {why}"
    if state != "ok":
        fix = ("`mokata doctor` checks it" if state == "unchecked" else INSTALL_HINT)
        return Hint(R_GRAPH_MISSING, f"{tool} is adopted but {why} — impact and blast radius "
                    f"answer from the floor; {fix}", "mokata doctor",
                    (Cite("manifest", f"code_graph pins {tool}"),
                     Cite("fs", f"{tool}: {state}"))), ""
    return Hint(R_GRAPH, f"{tool} is adopted but this repo's code graph is not built — impact "
                "and blast radius answer from the floor until it is", "mokata graph build",
                (Cite("manifest", f"code_graph pins {tool}"),
                 Cite("fs", f"{tool}: on disk at {path}"),
                 Cite("fs", ".code-review-graph/graph.db: absent")), writes=True), ""


# ----------------------------------------------------------------------------- the API

@dataclass(frozen=True)
class Subject:
    """Whose next step: a Claude session (and its resumed chain) and/or a run. Either may be
    unknown — the identity rules (R1) decide what that means."""
    session: str = ""
    run: str = ""
    chain: Tuple[str, ...] = ()
    #: True for a card that IS one session (the IDE): its run is the session's own or none — the
    #: repo-wide fallback would put another window's run on this card (AC15).
    own_only: bool = False


def _resolve(root: str, s: Subject) -> Tuple[Optional[str], str, Optional[Hint], str]:
    """(run, basis, R1-hint-or-None, note)."""
    from .run_resolver import ATTACHED_BASES, live_runs, resolve_run, run_ids
    if s.run:
        return s.run, "explicit", None, ""
    res = None
    for sid in (s.chain or ((s.session,) if s.session else ())):
        res = resolve_run(root, session_id=sid)
        if res.basis == "bound":
            break
    if s.own_only and (res is None or res.basis != "bound"):
        return None, "none", Hint(R_NO_RUN, "this session has no tracked run — start one",
                                  "/mokata:brainstorm",
                                  (Cite("run_resolver", f"session {s.session[:12]}: no run "
                                                        "bound"),)), ""
    if res is None or res.basis != "bound":
        res = resolve_run(root)
    if res.run_id is None:
        if res.ambiguous:
            return None, res.basis, Hint(
                R_AMBIGUOUS, f"{len(res.candidates)} runs, none identifiably yours — pick one",
                "mokata sessions", (Cite("run_resolver", "ambiguous: " +
                                         _names(list(res.candidates), 4)),)), ""
        return None, res.basis, Hint(R_NO_RUN, "no tracked run in this repository — start one",
                                     "/mokata:brainstorm",
                                     (Cite("run_resolver", "no run state"),)), ""
    if res.basis not in ATTACHED_BASES:
        live = live_runs(root, set(run_ids(_state_dir(root))))
        if len(live) >= 2:
            return None, res.basis, Hint(
                R_AMBIGUOUS, f"{len(live)} live runs, and nothing ties this caller to one — "
                "pick one", "mokata sessions",
                (Cite("run_resolver", f"{res.basis}: {len(live)} live runs"),)), ""
    return res.run_id, res.basis, None, ""


def derive_batch(root: str, subjects: Sequence[Subject], *, now: Optional[float] = None
                 ) -> List[Dict[str, Any]]:
    """Every subject's answer, reading each SHARED store once for the whole batch (the progress
    log and the timeline, backward). Pure read: nothing is written, spawned or cached."""
    from .repo_identity import is_initialized, state_root
    now = time.time() if now is None else now
    rd = _Read()
    if not is_initialized(state_root(root)):         # P31: a linked worktree reads the main store
        h = Hint(R_INIT, "mokata is not set up in this repository", "mokata init",
                 (Cite("fs", ".mokata/: absent"),), writes=True)
        return [_answer(STATUS_HINTS, [h], subject=s, run=None, basis="none", now=now, rd=rd)
                for s in subjects]
    resolved: List[Tuple[Subject, Optional[str], str, Optional[Hint]]] = []
    runs: Dict[str, _Run] = {}
    for s in subjects:
        run, basis, r1, _note = _resolve(root, s)
        resolved.append((s, run, basis, r1))
        if run and run not in runs:
            chain = list(s.chain or ((s.session,) if s.session else ()))
            runs[run] = _Run(root, run, chain, rd, now)
    _scan_log(root, runs, rd)
    tl = _scan_timeline(root, runs, rd)
    bridge = _bridge(root)
    graph, graph_note = _graph_hint(root)
    try:
        from .approval import pending_strict
        proposals, bad = pending_strict(root, now)
    except OSError as exc:
        proposals, bad = [], [f"the proposal store could not be read ({type(exc).__name__})"]
    from .gate_blocks import in_chain
    out = []
    for s, run, basis, r1 in resolved:
        hints: List[Hint] = []
        if r1 is not None:
            out.append(_answer(STATUS_HINTS, [r1] + ([graph] if graph else []), subject=s,
                               run=None, basis=basis, now=now, rd=rd, note=graph_note))
            continue
        r = runs[run]
        if bad:
            out.append(_answer(STATUS_UNKNOWN, [], subject=s, run=run, basis=basis, now=now,
                               rd=rd, reason="; ".join(bad[:3]) + " — `mokata approve --list`"))
            continue
        for p in proposals:
            if p.approved or not in_chain(run, r.chain, p.run_id, p.run_id):
                continue
            hints.append(Hint(R_APPROVE, f"proposal {p.proposal_id} ({_clip(p.tool, 40)}) is "
                              "waiting on you", f"mokata approve {p.proposal_id}",
                              (Cite("approvals", p.proposal_id, p.created_at),)))
        try:
            live, live_basis = session_live(root, r.chain, run, now)
            status, pipe, none_reason = _ladder(r, tl, bridge, live, live_basis)
        except _Unknown as exc:
            out.append(_answer(STATUS_UNKNOWN, [], subject=s, run=run, basis=basis, now=now,
                               rd=rd, reason=str(exc)))
            continue
        if status == STATUS_NONE and hints:
            status = STATUS_HINTS                 # a finished run with a proposal still owed
        hints += pipe
        if graph:
            hints.append(graph)
        out.append(_answer(status, hints, subject=s, run=run, basis=basis, now=now, rd=rd,
                           reason=none_reason, note=graph_note,
                           bash="observed" if bridge[0] else "unobserved (IDE bridge off)"))
    return out


def derive(root: str, *, session: str = "", run: str = "", chain: Iterable[str] = (),
           now: Optional[float] = None) -> Dict[str, Any]:
    """One subject's answer (see `derive_batch`)."""
    return derive_batch(root, [Subject(session or "", run or "", tuple(chain))], now=now)[0]


def _answer(status: str, hints: List[Hint], *, subject: Subject, run: Optional[str],
            basis: str, now: float, rd: _Read, reason: str = "", note: str = "",
            bash: str = "") -> Dict[str, Any]:
    return {"version": HINTS_VERSION, "status": status,
            "hints": [h.to_dict() for h in hints], "count": len(hints),
            "reason": reason,
            "subject": {"session": subject.session, "run": run or "",
                        "chain": list(subject.chain)},
            "basis": {"run": basis, "source": "recorded state", "read": rd.to_dict(),
                      "bash": bash, "note": note},
            "derived_at": now}


#: Hints that sit BESIDE the pipeline step rather than being it (approvals, the code graph).
_SIDE_RULES = frozenset({R_APPROVE, R_GRAPH, R_GRAPH_MISSING})


def primary(ans: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The answer's ONE pipeline step (what bare `mokata` shows as next), else its first hint."""
    hs = ans.get("hints") or []
    for h in hs:
        if h["rule"] not in _SIDE_RULES:
            return h
    return hs[0] if hs else None


def render(ans: Dict[str, Any]) -> str:
    """The CLI's text: one line per hint with its command, the cites indented under it. `unknown`
    is said as unknown, never as "no suggestions"."""
    lines: List[str] = []
    run = ans["subject"].get("run")
    head = f"next step{' for run ' + run if run else ''} (from recorded state; " \
           f"run picked by: {ans['basis']['run']})"
    lines.append(head)
    if ans["status"] == STATUS_UNKNOWN:
        lines.append(f"  unknown — {ans['reason']}")
        return "\n".join(lines)
    if ans["status"] == STATUS_NONE:
        lines.append(f"  nothing owed — {ans['reason']}")
    for h in ans["hints"]:
        lines.append(f"  {'(optional) ' if h['optional'] else ''}{h['text']}")
        lines.append(f"    → {h['command']}" + ("   (asks before it writes)" if h["writes"]
                                                  else ""))
        for c in h["cites"]:
            lines.append(f"      from {c['source']}: {c['ref']}")
    if ans["basis"].get("note"):
        lines.append(f"  note: {ans['basis']['note']}")
    lines.append("  (nothing is run for you — each command asks for its own consent)")
    return "\n".join(lines)


__all__ = ["HINTS_VERSION", "STATUS_HINTS", "STATUS_NONE", "STATUS_UNKNOWN", "ADVICE", "Cite",
           "Hint", "HintRefused", "Subject", "derive", "primary", "derive_batch", "render", "session_live",
           "timeline_path", "TIMELINE_KEEP"]
