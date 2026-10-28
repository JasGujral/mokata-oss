"""HP.S3 + H-3 (0.0.22 D4) — the subagent contract: what a subagent may do, and what it must hand back.

THE PROBLEM
-----------
mokata asks for subagents in prose (the closing review, the parallel execution path) and then trusts
the conversation about what they did. A subagent told "read only" could write; a subagent's
handback was whatever its last message happened to say, capped by cutting it off at a character
count; and a review recorded as "independent" was a claim nothing checked
(SUBAGENT-REVIEW-INSTRUCTED-NOT-VERIFIED). Instructed, not observed.

THE FIX — THREE HOOK POINTS, ONE RECORD PER SUBAGENT
---------------------------------------------------
Claude Code gives every subagent an `agent_id`, and the hooks see it at all three moments:

  1. SubagentStart — mokata records the CONTRACT (`subagent__<agent_id>`): the session it belongs
     to, that session's run (C1's claim — never a guess), its type, whether it is read-only
     (`settings.subagents.read_only_types`), and its depth. It tells the subagent the contract
     (context injection), including the HandbackReport it must end with.
  2. PreToolUse inside the subagent (the envelope carries `agent_id`) — the gate-guard ENFORCES it:
     a write by a read-only subagent is blocked, and so is a subagent starting a subagent past
     `settings.subagents.max_recursion_depth`. Each refusal is ledgered as
     `subagent_contract_unhonored`.
  3. SubagentStop — mokata reads the HANDBACK from the subagent's final message. A contracted
     subagent that returns no valid HandbackReport is sent back ONCE to write one (SubagentStop can
     block); if it still has none it is let go and the miss is ledgered — never a loop. The
     outcome is a `subagent_finished` progress event keyed to the run, which is how a review that
     says it was independent can be checked against a subagent that was actually seen to finish.

PARALLEL RUNS
-------------
Every record is keyed by `agent_id` and carries its parent's `session_id` and run id, both read at
SubagentStart from that session's own claim. Two windows running two pipelines each contract and
count their own subagents.

SCOPE, STATED
-------------
Only a session that is IN a pipeline (its claim is bound or open) contracts its subagents. A person
using subagents outside a mokata run sees no injection, no block and no ledger row — the
positive-trigger contract every gate keeps.

DEPTH — what is and is not measured: Claude Code does not let a subagent start a subagent today, so
every observed subagent is depth 1 and the depth check is a belt for the day that changes (and the
`max_recursion_depth: 0` setting, which forbids subagents in a run outright). It is not a
measurement of nesting the harness never shows.

Never raises: every reader degrades to "no contract", every writer to a no-op.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .state import StateStore
from .tdd_state import state_dir

#: `subagent__<agent_id>.json` under temp_local/state — one contract per subagent.
CONTRACT_PREFIX = "subagent__"
#: `settings.subagents.*`.
SUBAGENT_SETTINGS_KEY = "subagents"
#: Defaults: the harness's own exploration/planning types are read-only by design, so mokata
#: enforces what their descriptions promise.
DEFAULT_READ_ONLY_TYPES: Tuple[str, ...] = ("Explore", "Plan")
DEFAULT_MAX_DEPTH = 1
MAX_DEPTH_LIMIT = 3

#: The ledger kind every refusal is recorded under (the row's own name).
LEDGER_KIND = "subagent_contract_unhonored"
#: The gate name a block reports (not one of `gate_hook.GATES`: a contract, not a run-state gate).
GATE_READ_ONLY = "subagent-read-only"
GATE_DEPTH = "subagent-depth"

#: The tools that write a file. Bash is judged by its parsed write targets instead.
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
#: The tools that start a subagent (renamed across Claude Code versions; both are matched).
SPAWN_TOOLS = ("Agent", "Task")

_AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# ---------------------------------------------------------------------------------- HandbackReport

HANDBACK_MARKER = "HANDBACK"
STATUSES = ("done", "partial", "blocked")
_FIELD_LINE = re.compile(r"^\s*[-*]?\s*(status|summary|files|tests|open)\s*:\s*(.*)$", re.I)

HANDBACK_FORMAT = (
    "End your final message with a HandbackReport, exactly this shape (one line each):\n"
    "HANDBACK\n"
    "status: done | partial | blocked\n"
    "summary: what you found or did, in a sentence or two\n"
    "files: the files you changed, comma-separated, or none\n"
    "tests: the command you ran and its result, or not run\n"
    "open: what is unresolved, or none (required when status is blocked)"
)


@dataclass(frozen=True)
class HandbackReport:
    """H-3 — what a subagent hands back, as fields rather than as a truncated transcript."""
    status: str
    summary: str
    files: Tuple[str, ...] = field(default_factory=tuple)
    tests: str = ""
    open: str = ""

    def facts(self) -> Dict[str, Any]:
        return {"status": self.status, "summary": self.summary, "files": list(self.files),
                "tests": self.tests, "open": self.open}

    def line(self) -> str:
        files = ", ".join(self.files) if self.files else "no files"
        return f"{self.status} · {files} · tests: {self.tests or 'not stated'} · {self.summary}"


def parse_handback(text: Any) -> Tuple[Optional[HandbackReport], str]:
    """`(report, "")`, or `(None, why)` — from the LAST `HANDBACK` block in `text`.

    Refuses what it cannot read rather than guessing: no marker, an unknown status, an empty
    summary, or `blocked` with nothing open each come back with the reason, so the subagent can be
    told exactly what to fix."""
    if not isinstance(text, str) or not text.strip():
        return None, "the final message is empty"
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().strip("`*#: ").upper() == HANDBACK_MARKER:
            start = i
    if start is None:
        return None, f"no `{HANDBACK_MARKER}` block in the final message"
    got: Dict[str, str] = {}
    for ln in lines[start + 1:start + 1 + 20]:
        if ln.strip().startswith("```"):
            if got:
                break
            continue
        m = _FIELD_LINE.match(ln)
        if m:
            got.setdefault(m.group(1).lower(), m.group(2).strip())
    status = got.get("status", "").lower()
    if status not in STATUSES:
        return None, (f"status must be one of {', '.join(STATUSES)}"
                      + (f" (got {status!r})" if status else " (missing)"))
    summary = got.get("summary", "")
    if not summary:
        return None, "summary is missing"
    open_ = got.get("open", "")
    if status == "blocked" and open_.lower() in ("", "none", "-"):
        return None, "a blocked handback must say what is open"
    files_raw = got.get("files", "")
    files = tuple(f.strip() for f in files_raw.split(",")
                  if f.strip() and f.strip().lower() not in ("none", "-"))
    return HandbackReport(status, summary, files, got.get("tests", ""), open_), ""


# ---------------------------------------------------------------------------------- the contract

@dataclass(frozen=True)
class SubagentSettings:
    read_only_types: Tuple[str, ...] = DEFAULT_READ_ONLY_TYPES
    max_recursion_depth: int = DEFAULT_MAX_DEPTH
    problems: Tuple[str, ...] = field(default_factory=tuple)


def subagent_settings(root: str) -> SubagentSettings:
    """`settings.subagents`, read with one JSON load (hook-cheap). A bad value is refused with its
    reason and the default kept — never clamped, never raised."""
    try:
        with open(os.path.join(root, ".mokata", "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        raw = ((data.get("settings") or {}).get(SUBAGENT_SETTINGS_KEY)
               if isinstance(data, dict) else None)
    except (OSError, ValueError, AttributeError):
        raw = None
    if raw is None:
        return SubagentSettings()
    if not isinstance(raw, dict):
        return SubagentSettings(problems=("settings.subagents is not a table",))
    problems: List[str] = []
    types = DEFAULT_READ_ONLY_TYPES
    if "read_only_types" in raw:
        val = raw["read_only_types"]
        if isinstance(val, list) and all(isinstance(t, str) and t.strip() for t in val):
            types = tuple(t.strip() for t in val)
        else:
            problems.append("read_only_types must be a list of agent type names")
    depth = DEFAULT_MAX_DEPTH
    if "max_recursion_depth" in raw:
        val = raw["max_recursion_depth"]
        if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= MAX_DEPTH_LIMIT:
            problems.append(f"max_recursion_depth must be a whole number 0..{MAX_DEPTH_LIMIT}")
        else:
            depth = val
    unknown = sorted(set(raw) - {"read_only_types", "max_recursion_depth"})
    if unknown:
        problems.append("unknown key(s): " + ", ".join(unknown))
    return SubagentSettings(types, depth, tuple(problems))


def _valid_agent_id(agent_id: Any) -> Optional[str]:
    if isinstance(agent_id, str) and _AGENT_ID.match(agent_id.strip()):
        return agent_id.strip()
    return None


def _session_run(root: str, session_id: Optional[str]) -> Tuple[str, Optional[str]]:
    from .run_intent import session_claim
    return session_claim(root, session_id)


def start_contract(root: str, session_id: Optional[str], agent_id: Any,
                   agent_type: Any) -> Optional[Dict[str, Any]]:
    """SubagentStart: record this subagent's contract, or None when its session is in no pipeline."""
    aid = _valid_agent_id(agent_id)
    if not aid or not session_id:
        return None
    try:
        from .run_intent import CLAIM_NONE
        claim, rid = _session_run(root, session_id)
        if claim == CLAIM_NONE:
            return None
        cfg = subagent_settings(root)
        atype = agent_type.strip() if isinstance(agent_type, str) else ""
        contract = {"agent_id": aid, "agent_type": atype, "session_id": session_id,
                    "run_id": rid, "read_only": atype in cfg.read_only_types, "depth": 1,
                    "max_depth": cfg.max_recursion_depth, "at": time.time()}
        StateStore(state_dir(root)).write(CONTRACT_PREFIX + aid, contract)
        return contract
    except Exception as exc:  # noqa: BLE001 — an unrecorded contract is today's behaviour
        _say_degraded("this subagent's contract was NOT recorded, so it runs unpoliced", exc)
        return None


def read_contract(root: str, agent_id: Any) -> Optional[Dict[str, Any]]:
    aid = _valid_agent_id(agent_id)
    if not aid:
        return None
    try:
        data = StateStore(state_dir(root)).read(CONTRACT_PREFIX + aid)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def contract_context(contract: Dict[str, Any]) -> str:
    """The SubagentStart injection: the contract, said to the subagent in its own context."""
    run = contract.get("run_id") or "the run this session is registering"
    lines = [f"mokata · subagent contract for {run}:"]
    if contract.get("read_only"):
        lines.append("- READ-ONLY: you may read, search and run commands that change nothing; "
                     "any file write is blocked.")
    if contract.get("depth", 1) >= contract.get("max_depth", DEFAULT_MAX_DEPTH):
        lines.append("- do not start further subagents; that is blocked.")
    lines.append("- " + HANDBACK_FORMAT.replace("\n", "\n  "))
    return "\n".join(lines)


@dataclass(frozen=True)
class ContractVerdict:
    allowed: bool
    gate: str = ""
    reason: str = ""


def check_tool(root: str, agent_id: Any, tool_name: Any, path: Optional[str],
               bash_targets: List[str], session_id: Optional[str] = None,
               bash_unreadable: Tuple[str, ...] = ()) -> ContractVerdict:
    """PreToolUse: does this tool call honour the contract of the agent making it?

    `agent_id` is None in the main conversation: only a spawn is judged there, against
    `max_recursion_depth` (0 forbids subagents in a run). Inside a subagent, a contracted read-only
    agent may not write (a write tool, or a Bash command with a parsed write target), and a spawn
    past the depth limit is refused. An agent with no contract is unpoliced (it belongs to no run)."""
    name = tool_name if isinstance(tool_name, str) else ""
    if agent_id is None:
        if name in SPAWN_TOOLS and session_id:
            from .run_intent import CLAIM_NONE
            claim, _rid = _session_run(root, session_id)
            if claim != CLAIM_NONE and subagent_settings(root).max_recursion_depth < 1:
                return ContractVerdict(False, GATE_DEPTH,
                                       "this run allows no subagents "
                                       "(`settings.subagents.max_recursion_depth` is 0)")
        return ContractVerdict(True)
    contract = read_contract(root, agent_id)
    if contract is None:
        return ContractVerdict(True)
    if name in SPAWN_TOOLS:
        depth = int(contract.get("depth") or 1)
        limit = int(contract.get("max_depth") if contract.get("max_depth") is not None
                    else DEFAULT_MAX_DEPTH)
        if depth + 1 > limit:
            return ContractVerdict(False, GATE_DEPTH,
                                   f"a subagent at depth {depth} may not start another "
                                   f"(`settings.subagents.max_recursion_depth` is {limit})")
    if contract.get("read_only"):
        # D5 — a command whose writes mokata cannot read counts as a write here: read-only means
        # provably read-only.
        target = path if name in WRITE_TOOLS else (bash_targets[0] if bash_targets else None)
        if (name in WRITE_TOOLS) or (name == "Bash" and (bash_targets or bash_unreadable)):
            atype = contract.get("agent_type") or "this"
            return ContractVerdict(False, GATE_READ_ONLY,
                                   f"the {atype} subagent is read-only under this run's contract "
                                   f"and may not write `{target or 'a file'}` — hand the change "
                                   f"back to the main conversation in your HandbackReport")
    return ContractVerdict(True)


def ledger_unhonored(root: str, contract: Optional[Dict[str, Any]], what: str,
                     **fields: Any) -> None:
    """Record one refusal or miss under `subagent_contract_unhonored`. Best-effort: the decision
    has already been made whether or not it is counted."""
    try:
        from .govern.ledger import AuditLedger
        c = contract or {}
        AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")).record(
            LEDGER_KIND, gate="subagent-contract", decision=what, agent_id=c.get("agent_id"),
            agent_type=c.get("agent_type"), run_id=c.get("run_id"), **fields)
    except Exception:  # noqa: BLE001
        pass


@dataclass(frozen=True)
class StopVerdict:
    block: bool                       # send the subagent back to write its handback
    reason: str = ""
    report: Optional[HandbackReport] = None


def finish_contract(root: str, payload: Dict[str, Any]) -> StopVerdict:
    """SubagentStop: read the handback, ask ONCE for a missing one, and record the outcome.

    `stop_hook_active` is Claude Code's own "you already sent it back once" flag: when it is set,
    a still-missing report is let go and ledgered — mokata never holds a subagent in a loop."""
    contract = read_contract(root, payload.get("agent_id"))
    if contract is None:
        return StopVerdict(False)
    report, why = parse_handback(payload.get("last_assistant_message"))
    if "last_assistant_message" not in payload:
        # 0.0.22 E1 — a Claude Code that does not SEND the final message cannot be asked for a
        # report it gives mokata no way to read: sending the subagent back would loop on nothing.
        # Recorded as the harness's gap, never charged to the subagent.
        _record_finished(root, contract, None, "this Claude Code sends no final message "
                                                "(`last_assistant_message`)")
        return StopVerdict(False)
    if report is None and not payload.get("stop_hook_active"):
        return StopVerdict(True, f"mokata: this subagent's handback is not readable ({why}). "
                                 + HANDBACK_FORMAT)
    if report is None:
        ledger_unhonored(root, contract, "handback-missing", why=why)
    # R1 (0.0.26) — a `done` handback must be backed: implementation changed ⇒ a test was run and
    # passed. Sent back ONCE with the reason; on re-entry let go and ledgered — never a loop.
    from .stop_gate import GATE_STOP_EVIDENCE, remedy, subagent_untested
    untested = subagent_untested(report)
    if untested and contract.get("run_id"):
        from .gate_hook import read_override
        if GATE_STOP_EVIDENCE in read_override(root, contract["run_id"]):
            untested = ""
    if untested and not payload.get("stop_hook_active"):
        return StopVerdict(True, f"mokata {GATE_STOP_EVIDENCE}: {untested}. {remedy()}")
    if untested:
        ledger_unhonored(root, contract, "done-untested", why=untested)
    _record_finished(root, contract, report, why)
    _record_usage(root, contract, payload)
    return StopVerdict(False, report=report)


def _record_usage(root: str, contract: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """R6 (0.0.26) — what this subagent actually ran on (models, tokens), from its own transcript:
    the per-run cost record. Absent when Claude Code gave no transcript — never a guessed zero."""
    rid = contract.get("run_id")
    if not rid:
        return
    from .model_tiers import record_usage, usage_from_transcript
    usage = usage_from_transcript(payload.get("agent_transcript_path"))
    if usage is None:
        return
    try:
        record_usage(root, rid, str(contract.get("agent_id") or ""),
                     str(contract.get("agent_type") or ""), usage)
    except Exception as exc:  # noqa: BLE001
        _say_degraded("a subagent's model/token usage was NOT recorded for its run", exc)


def _record_finished(root: str, contract: Dict[str, Any], report: Optional[HandbackReport],
                     why: str) -> None:
    rid = contract.get("run_id")
    if not rid:
        return
    try:
        from .progress_events import record_subagent_finished
        record_subagent_finished(state_dir(root), rid, contract, report, why)
    except Exception as exc:  # noqa: BLE001
        _say_degraded("a subagent finished but was NOT recorded, so its run cannot count it", exc)


def _say_degraded(what: str, exc: BaseException) -> None:
    try:
        from .degrade import FAILURE_LOCAL_IO, note_degraded
        note_degraded("subagent-contract", FAILURE_LOCAL_IO, fallback=what, detail=str(exc),
                      fix="check permissions/disk under `.mokata/temp_local/`, then run "
                          "`mokata doctor`")
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "CONTRACT_PREFIX", "SUBAGENT_SETTINGS_KEY", "DEFAULT_READ_ONLY_TYPES", "DEFAULT_MAX_DEPTH",
    "LEDGER_KIND", "GATE_READ_ONLY", "GATE_DEPTH", "WRITE_TOOLS", "SPAWN_TOOLS",
    "HANDBACK_MARKER", "HANDBACK_FORMAT", "STATUSES", "HandbackReport", "parse_handback",
    "SubagentSettings", "subagent_settings", "start_contract", "read_contract",
    "contract_context", "ContractVerdict", "check_tool", "ledger_unhonored", "StopVerdict",
    "finish_contract",
]
