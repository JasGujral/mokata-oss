"""R1 (0.0.26 Lane R) — the Stop / SubagentStop EVIDENCE gate: a "done" has to be backed by the run.

THE FAILURE THIS EXISTS FOR. A session mid-cycle writes "all done — implemented and tested" and
stops, and nothing checked it: an acceptance criterion with no test, or a test recorded RED and never
seen GREEN, ships as "done". The research candidate (doc 113 §4, from the 2026-10-16 skills research:
superpowers' verification-before-completion — "no completion claim without fresh evidence") puts the
check where the claim is made: Claude Code's `Stop` / `SubagentStop` hooks can BLOCK a stop with a
reason, which sends the model back to work with that reason in hand.

WHEN IT SPEAKS (positive trigger — everything else is a silent allow):
  * the session is ATTACHED to a mokata run (`run_resolver` explicit/pinned/own/bound — never a
    guessed run), the run has an emitted spec (it is past brainstorm/spec: there are ACs to prove)
    and it has not shipped;
  * the stop CLAIMS completion — its final message (`last_assistant_message`) says done/complete/
    all tests pass (`claims_done`). A stop that asks a question or reports a blocker is not a claim.

WHAT IT CHECKS — RECORDED STATE ONLY (doc 113 §4 research: "gate on recorded state, never on the
model's text"; never a repo walk, so the cost does not grow with a multi-million-line tree):
  (a) owed = red − green is empty (`tdd_phase__<run>`);
  (b) every acceptance criterion of the emitted spec has a mapped test (`test_runs`: the map seeded
      at emit + extended by recorded runs) and at least one of them is recorded GREEN;
  (c) the last recorded suite run (`mokata progress test-run`) PASSED and is not OLDER than the
      run's last write (`working_set.last_write_at`, stamped at hook time);
  (d) once the run is in review, a review verdict is recorded (`review_record`).

FAIL-CLOSED, NEVER A TRAP. A missing piece of evidence BLOCKS, and so does a check that could not
finish (an unreadable record; a crash — `hook_cli.stop_gate_main` blocks on any exception). But a
human must never be held in a loop by a hook, so the gate uses mokata's existing exits:
  * the P14 override — `mokata gate override stop-evidence --reason "…"` (explicit, re-confirmed,
    session-scoped, ledgered) — is the human's way to let an unverified stop through;
  * after `MAX_STOP_BLOCKS` consecutive blocks of the same session (counted across Claude Code's own
    `stop_hook_active` re-entries) the stop is RELEASED — but loudly: a `systemMessage` the human
    sees names what is unverified and the override, and the release is ledgered (`stop_evidence`).
    Nothing marks the run done: ship's own gates still stand.
  * a stop after a TRIPPED 3-fix breaker or a capped review loop (R4) is ALLOWED — stopping to
    escalate is exactly what those ask for.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

GATE_STOP_EVIDENCE = "stop-evidence"
#: Consecutive blocks of one session's stop before it is released (loudly) — never a trap.
MAX_STOP_BLOCKS = 3
#: The per-session block counter (run-state, temp_local; one small JSON key per Claude session).
STOP_GATE_PREFIX = "stop_gate__"
#: The ledger kind a released (unverified) stop is recorded under.
LEDGER_KIND = "stop_evidence"

# A COMPLETION CLAIM — the sentence shapes a model ends a "done" turn with. Deliberately about the
# claim, not about any word: "done" in "what's left to be done?" is a question, not a claim.
_DONE = re.compile(
    r"(?:\ball (?:the )?(?:\w+ )?tests? (?:now )?(?:pass|passes|passing|passed|are green|green)\b"
    r"|\b(?:it|this|that|everything|all|the (?:work|task|feature|fix|change|implementation))"
    r"(?:'s| is| are| has been| have been) (?:now |all |fully )?"
    r"(?:done|complete|completed|finished|implemented|working)\b"
    r"|\bimplementation (?:is )?(?:now )?complete\b"
    r"|\bready (?:to|for) (?:ship|review|merge)\b"
    r"|\bi(?:'ve| have) (?:now )?(?:finished|completed|implemented|fixed)\b"
    r"|\bsuccessfully (?:implemented|completed|fixed)\b"
    r"|✓ \w+ done\b)",
    re.IGNORECASE)
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_NOT_RUN = re.compile(r"^\s*(?:not run|none|n/?a|no|skipped|-)?\s*\.?\s*$", re.IGNORECASE)
_TEST_FAILED = re.compile(r"\b(?:[1-9]\d* (?:failed|failing|errors?)|fail(?:ed|ing|s)?|errors?)\b",
                          re.IGNORECASE)
_ZERO_FAILED = re.compile(r"\b0 (?:failed|failing|errors?)\b", re.IGNORECASE)


class Decision:
    """What the Stop hook answers: `block` with `reason` (back to work), or allow — optionally with
    `message`, a line the HUMAN sees (Claude Code's `systemMessage`)."""

    __slots__ = ("block", "reason", "message")

    def __init__(self, block: bool = False, reason: str = "", message: str = "") -> None:
        self.block, self.reason, self.message = block, reason, message

    def payload(self) -> Optional[Dict[str, Any]]:
        if self.block:
            return {"decision": "block", "reason": self.reason}
        if self.message:
            return {"systemMessage": self.message}
        return None


ALLOW = Decision()


def claims_done(text: Any) -> bool:
    """Does this final message CLAIM the work is complete? Questions are never claims."""
    if not isinstance(text, str) or not text.strip():
        return False
    tail = text.strip()[-2000:]
    if tail.endswith("?"):
        return False
    return _DONE.search(tail) is not None


# ------------------------------------------------------------------------------------ evidence

def _ac_ids(spec: Any) -> List[str]:
    crit = spec.get("criteria") if isinstance(spec, dict) else None
    if not isinstance(crit, list):
        raise ValueError("the run's emitted spec has no readable criteria")
    return [c["id"] for c in crit if isinstance(c, dict) and isinstance(c.get("id"), str)]


class LogFacts:
    """What one bounded backward scan of the progress log says about a run: its latest stage,
    whether it shipped, whether a review verdict is recorded, and its failed-review streak."""

    __slots__ = ("stage", "shipped", "reviewed", "failed_streak")

    def __init__(self) -> None:
        self.stage, self.shipped, self.reviewed, self.failed_streak = "", False, False, 0


def log_facts(root: str, run_id: str) -> LogFacts:
    """ONE backward scan (`read_events_backward`, byte-capped), stopping once every fact is known.
    The ship question is `run_resolver._run_is_shipped`'s, asked here because that helper lives in
    `progress` (~50 ms of imports on a hook's clock)."""
    from .progress_events import REVIEW_VERDICT, RUN_FINISHED, STAGE_ENTER, ProgressLog
    from .tdd_state import state_dir
    facts = LogFacts()
    stage_known = streak_done = False
    for e in ProgressLog.from_state_dir(state_dir(root)).read_events_backward():
        if e.get("run_id") != run_id:
            continue
        kind = e.get("type")
        if kind == RUN_FINISHED and not stage_known:
            facts.shipped = stage_known = True
        elif kind == STAGE_ENTER and not stage_known:
            facts.stage = str(e.get("stage") or "")
            facts.shipped = facts.stage == "ship"
            stage_known = True
        elif kind == REVIEW_VERDICT:
            facts.reviewed = True
            data = e.get("data") if isinstance(e.get("data"), dict) else {}
            if not streak_done:
                if data.get("passed"):
                    streak_done = True
                else:
                    facts.failed_streak += 1
        if stage_known and streak_done:
            break
    return facts


def _last_write_at(root: str, run_id: str) -> Optional[float]:
    from .state import StateStore
    from .tdd_state import state_dir
    from .working_set import WORKING_SET_PREFIX
    data = StateStore(state_dir(root)).read(WORKING_SET_PREFIX + run_id)
    at = data.get("last_write_at") if isinstance(data, dict) else None
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def missing_evidence(root: str, run_id: str, facts: Optional[LogFacts] = None) -> List[str]:
    """What this run cannot yet show for a "done", one sentence each; [] when the RECORD backs it.
    Recorded state only — the emitted spec, the TDD record, the test-run record, the working set's
    last write, the progress log. No repo walk: the cost does not grow with the repository.
    Raises on a record it cannot read — the caller turns that into a fail-closed block."""
    from .run_resolver import SPEC_PREFIX
    from .state import StateStore
    from .tdd_state import TDD_STATE_PREFIX, from_state, state_dir
    from .test_runs import mapped_tests, read_test_record
    store = StateStore(state_dir(root))
    acs = _ac_ids(store.read(SPEC_PREFIX + run_id))
    red: set = set()
    green: set = set()
    if store.exists(TDD_STATE_PREFIX + run_id):
        data = store.read(TDD_STATE_PREFIX + run_id)
        if not isinstance(data, dict):
            raise ValueError("the run's TDD record is unreadable")
        red, green = from_state(data)
    reasons = []
    owed = sorted(red - green)
    if owed:
        reasons.append("still RED (recorded failing, never recorded passing): " + ", ".join(owed))
    record = read_test_record(root, run_id)
    unmapped = [ac for ac in acs if not mapped_tests(record, ac)]
    unproven = [ac for ac in acs if mapped_tests(record, ac)
                and not set(mapped_tests(record, ac)) & green]
    if unmapped:
        reasons.append("no test is recorded for acceptance criteria " + ", ".join(unmapped))
    if unproven:
        reasons.append("no test recorded PASSING for acceptance criteria " + ", ".join(unproven))
    suite = record["last_suite"]
    wrote = _last_write_at(root, run_id)
    if suite is None:
        reasons.append("no test run is recorded for this run")
    elif not suite.get("passed"):
        reasons.append("the last recorded test run FAILED")
    elif wrote is not None and not (isinstance(suite.get("at"), (int, float))
                                    and suite["at"] >= wrote):
        reasons.append("the last recorded test run is OLDER than the run's last write — the code "
                       "being called done was never run")
    facts = log_facts(root, run_id) if facts is None else facts
    if facts.stage in ("review",) and not facts.reviewed:
        reasons.append("the run is in review but no review verdict is recorded")
    return reasons


def record_hint() -> str:
    return ("Record the evidence as it happens: `mokata progress test-run --passed|--failed "
            "--command \"<the command you ran>\" --test <test id> --ac <AC>=<test id>`")


# ------------------------------------------------------------------------------------ the counter

def _counter_key(session_id: str) -> str:
    return STOP_GATE_PREFIX + (_SAFE.sub("_", session_id)[:120] or "_")


def _bump(root: str, session_id: str, run_id: str, continuing: bool) -> int:
    """This block's number in the session's current streak (1 on a fresh stop)."""
    from .state import StateStore
    from .tdd_state import state_dir

    def _mutate(cur: Any) -> Dict[str, Any]:
        n = cur.get("blocks") if isinstance(cur, dict) else 0
        n = n if isinstance(n, int) and not isinstance(n, bool) and continuing else 0
        return {"session_id": session_id, "run_id": run_id, "blocks": n + 1}

    return StateStore(state_dir(root)).update(_counter_key(session_id), _mutate)["blocks"]


def _reset(root: str, session_id: str) -> None:
    from .state import StateStore
    from .tdd_state import state_dir
    store = StateStore(state_dir(root))
    if store.exists(_counter_key(session_id)):
        store.delete(_counter_key(session_id))


def _ledger(root: str, **fields: Any) -> None:
    try:
        from .govern.ledger import AuditLedger
        AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")).record(
            LEDGER_KIND, gate=GATE_STOP_EVIDENCE, **fields)
    except Exception:  # noqa: BLE001 — the decision is made whether or not it is counted
        pass


def remedy() -> str:
    return (f"Show the evidence, then stop: write the missing test(s), run them and record the "
            f"run, or say plainly what is NOT done instead of claiming it is. {record_hint()}. If "
            f"a human accepts the stop unverified: mokata gate override {GATE_STOP_EVIDENCE} "
            f"--reason \"<why>\"")


def block_or_release(root: str, session_id: str, run_id: str, what: str,
                     continuing: bool) -> Decision:
    """Block (streak < MAX_STOP_BLOCKS) or release loudly. `what` names the unverified evidence."""
    try:
        n = _bump(root, session_id, run_id, continuing)
    except Exception:  # noqa: BLE001 — no counter: fall back to the harness's own one-shot flag
        n = MAX_STOP_BLOCKS + 1 if continuing else 1
    if n <= MAX_STOP_BLOCKS:
        try:
            from .progress_events import record_gate_block
            from .tdd_state import state_dir
            record_gate_block(state_dir(root), run_id, GATE_STOP_EVIDENCE, "<stop>")
        except Exception:  # noqa: BLE001
            pass
        return Decision(True, f"mokata {GATE_STOP_EVIDENCE} ({n}/{MAX_STOP_BLOCKS}): this stop "
                              f"claims the work is done, but the run's record does not back it — "
                              f"{what}. {remedy()}")
    try:
        _reset(root, session_id)
    except Exception:  # noqa: BLE001
        pass
    _ledger(root, decision="released-unverified", run=run_id, session=session_id, why=what)
    return Decision(message=f"mokata: this stop was let through UNVERIFIED after "
                            f"{MAX_STOP_BLOCKS} blocks — {what}. The run is not marked done; "
                            f"ship's gates still apply. Accept it on purpose with: mokata gate "
                            f"override {GATE_STOP_EVIDENCE} --reason \"<why>\"")


# ------------------------------------------------------------------------------------ the decision

def decide(root: str, payload: Dict[str, Any]) -> Decision:
    """The Stop decision for one hook payload. May raise — `hook_cli.stop_gate_main` fails closed."""
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return ALLOW
    sid = sid.strip()
    if not claims_done(payload.get("last_assistant_message")):
        return ALLOW
    from .run_resolver import SPEC_PREFIX, resolve_run
    from .state import StateStore
    from .tdd_state import state_dir
    res = resolve_run(root, session_id=sid)
    if not res.attached or not res.run_id:
        return ALLOW
    run_id = res.run_id
    if not StateStore(state_dir(root)).exists(SPEC_PREFIX + run_id):
        return ALLOW
    continuing = bool(payload.get("stop_hook_active"))
    try:
        facts = log_facts(root, run_id)
        if facts.shipped:
            return ALLOW
        from .gate_hook import read_override
        if GATE_STOP_EVIDENCE in read_override(root, run_id):
            return ALLOW
        from .loop_guard import REVIEW_ROUND_CAP, tripped_failures
        if tripped_failures(root, run_id) or facts.failed_streak >= REVIEW_ROUND_CAP:
            return ALLOW                # R4 asked it to stop and escalate — this IS that stop
        missing = missing_evidence(root, run_id, facts)
    except Exception as exc:  # noqa: BLE001 — could not verify ⇒ not verified (fail CLOSED)
        missing = [f"mokata could not verify the evidence ({type(exc).__name__}: {exc})"]
    if not missing:
        _reset(root, sid)
        return ALLOW
    return block_or_release(root, sid, run_id, "; ".join(missing), continuing)


# ------------------------------------------------------------------------------------ subagents

def subagent_untested(report: Any) -> str:
    """Why a subagent's `done` HandbackReport is not backed — "" when it is (or claims nothing).

    A subagent that hands back `status: done` having changed IMPLEMENTATION files must say which
    test it ran, and that test must not have failed."""
    if report is None or getattr(report, "status", "") != "done":
        return ""
    from .gate_hook import is_implementation_path
    impl = [f for f in (getattr(report, "files", ()) or ()) if is_implementation_path(f)]
    if not impl:
        return ""
    tests = getattr(report, "tests", "") or ""
    if _NOT_RUN.match(tests):
        return (f"the handback says done and changed implementation ({', '.join(impl[:3])}) but "
                f"names no test it ran")
    if _TEST_FAILED.search(_ZERO_FAILED.sub("", tests)):
        return f"the handback says done but its tests did not pass ({tests.strip()[:120]})"
    return ""


__all__ = ["GATE_STOP_EVIDENCE", "MAX_STOP_BLOCKS", "STOP_GATE_PREFIX", "LEDGER_KIND", "Decision",
           "ALLOW", "claims_done", "LogFacts", "log_facts", "missing_evidence", "block_or_release",
           "decide", "subagent_untested", "remedy", "record_hint"]
