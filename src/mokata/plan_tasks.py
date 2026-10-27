"""R2 + R3 (0.0.26 Lane R) — the spec's TASK PLAN, its approval HASH, and the capped CLARIFY record.

Three things the 2026-10-16 research recommended, each reduced to something a gate can check:

R2 — `tasks[]` + per-task scope + plan-hash attestation
-------------------------------------------------------
The plan was prose ("decompose the work into small, ordered, verifiable tasks"). Prose does not bind.
Now the spec payload carries an explicit `tasks[]`, approved in the SAME `mokata approve` as the ACs
(no new pipeline phase):

    "tasks": [{"id": "T1", "title": "items endpoint", "ac_ids": ["AC1"],
               "scope": ["src/api/items.py"], "files": ["src/api/items.py"],
               "depends_on": [], "consumes": "", "produces": "", "parallel": false}]

  * every AC is covered by >= 1 task and every task names >= 1 AC — so, with the completeness gate
    mapping every AC to a test, every task has >= 1 test (completeness, extended to the plan);
  * `scope` is the task's authorized surface (globs, `*` crosses `/`, matched like `spec_scope`); when
    the spec draws `scope.authorized`, every task glob must sit inside it (a SUBSET);
  * the gate-guard hook narrows the spec's scope to the ACTIVE task's globs, so develop works task by
    task: an implementation write outside the active task is the existing `spec-scope` block.

The ACTIVE task is a run-state cursor (`task_cursor__<run_id>`, ungated, the tier of `tdd_phase__`):
the explicit `current` when it is still open, else the first open task whose `depends_on` are done.
`mokata spec task start|done|status` moves and reads it. Once every task is done the union of the
task scopes stays writable (review fixes), and nothing outside it.

The PLAN HASH: SHA-256 over the persisted spec's canonical JSON (minus `version` and the hash itself),
computed by the one committer (`engine/emit.spec_commit`) inside the human-gated write — the hash IS
the approval. Every later stage attests it:

    develop   the gate-guard hook refuses implementation writes on a mismatch (not overridable —
              the remedy is re-approval: re-emit, or `spec_amend` once work is in flight);
    stages    `progress mark develop|review|ship` refuses to enter a stage on a mismatch, and stamps
              the hash it attested into the stage event;
    review    the review verdict is stamped with the plan hash it reviewed;
    ship      the ship gate refuses a mismatch, and a review verdict stamped with another plan.

A spec persisted WITHOUT tasks[] or without a hash (a 0.0.25 spec in the user's `.mokata/`) is
refused LOUDLY by the same check — rule 1: no fallback for the old shape, re-emit it.

R3 — capped clarify (`clarifications[]`)
----------------------------------------
spec-kit's clarify loop, reimplemented: AT MOST FIVE questions (`CLARIFY_CAP`), asked one at a time,
each carrying the RECOMMENDED answer. Five is spec-kit `/clarify`'s own hard cap ("maximum of 5 total
questions" per session, picked by impact x uncertainty) — enough for the high-impact unknowns, small
enough that the conversation stays a conversation; its `/specify` uses 3 inline markers, a different
device. After the cap, work proceeds on STATED ASSUMPTIONS, recorded in the spec (`assumptions[]`).
The record rides the spec payload and is approved with the ACs (P2):

    "clarifications": [{"id": "Q1", "question": "...", "recommended": "...", "answer": "...",
                        "category": "edge cases"?}],
    "assumptions":    [{"id": "A1", "text": "...", "for": "Q2"?}]

A sixth clarification is refused at the wire; an unanswered one must be covered by an assumption
(`for`). The brainstorm session's question list holds the same cap (`brainstorm.BrainstormSession.ask`).

Stdlib + `repo_paths` + `spec_scope` only: `gate_hook` imports this, and its import surface is part of
the hook budget (hashlib is imported lazily, on the attest path only).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .spec_scope import _matches, _rel

# ======================================================================================
# keys + numbers
# ======================================================================================

TASKS_KEY = "tasks"
CLARIFICATIONS_KEY = "clarifications"
ASSUMPTIONS_KEY = "assumptions"
PLAN_HASH_KEY = "plan_hash"
#: The persisted fields the plan hash does NOT cover: the version counter (bumped by the committer
#: on supersede) and the hash itself.
UNHASHED = ("version", PLAN_HASH_KEY)
HASH_PREFIX = "sha256:"

#: R3 — the clarify cap (spec-kit `/clarify`: at most five questions per session).
CLARIFY_CAP = 5

#: The active-task cursor, run-scoped. NOT a run-enumeration prefix (`run_resolver` scans
#: `emitted_spec__` / `approved_approach__` / `tdd_phase__` / `pipeline_run__`), so it can never
#: mint a phantom run.
TASK_CURSOR_PREFIX = "task_cursor__"

# Attestation codes.
ATTEST_OK = "ok"
ATTEST_UNSEALED = "unsealed"          # a persisted spec with no tasks[] / no hash (pre-0.0.26)
ATTEST_MISMATCH = "mismatch"          # the plan changed after approval

PLAN_GATE = "spec-scope"              # the existing gate id these blocks ride (gate_hook.GATE_SCOPE)


# ======================================================================================
# the task
# ======================================================================================

@dataclass(frozen=True)
class Task:
    """One step of the approved plan."""

    id: str
    title: str
    ac_ids: Tuple[str, ...]
    scope: Tuple[str, ...]
    files: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()
    consumes: str = ""
    produces: str = ""
    parallel: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "ac_ids": list(self.ac_ids),
                "scope": list(self.scope), "files": list(self.files),
                "depends_on": list(self.depends_on), "consumes": self.consumes,
                "produces": self.produces, "parallel": self.parallel}

    def covers(self, path: str, root: str = "") -> bool:
        """Is `path` inside this task's surface (its scope globs, or one of its named files)?"""
        rel = _rel(path, root)
        return _matches(rel, self.scope) or rel in self.files


def _strs(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value if isinstance(v, str) and v)


def tasks_from_state(raw: Any) -> Tuple[Task, ...]:
    """Tasks out of a PERSISTED spec — lenient (a malformed row is skipped). The persisted plan is
    judged by its HASH (`attest`), not re-validated here: a spec the committer sealed was valid."""
    out: List[Task] = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        out.append(Task(id=str(t["id"]), title=str(t.get("title", "")),
                        ac_ids=_strs(t.get("ac_ids")), scope=_strs(t.get("scope")),
                        files=_strs(t.get("files")), depends_on=_strs(t.get("depends_on")),
                        consumes=str(t.get("consumes", "") or ""),
                        produces=str(t.get("produces", "") or ""),
                        parallel=bool(t.get("parallel", False))))
    return tuple(out)


def _str_list(raw: Any, what: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        raise ValueError(f"{what} must be a list of non-empty strings")
    return tuple(v.strip() for v in raw)


def tasks_from_payload(raw: Any, ac_ids: Sequence[str],
                       authorized: Sequence[str] = ()) -> Tuple[Task, ...]:
    """The wire `tasks[]`, STRICTLY validated. Raises ValueError naming what is wrong — a plan the
    hook cannot enforce must fail at the surface, never reach the gate as an empty one."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "a spec needs a task plan: tasks: [{id, title, ac_ids: [...], scope: [globs]}] — "
            "every acceptance criterion in >= 1 task; develop works task by task inside each "
            "task's scope (`tasks` was sent but is not a non-empty list)")
    known = set(ac_ids)
    tasks: List[Task] = []
    seen: set = set()
    for t in raw:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not t["id"].strip():
            raise ValueError("each task needs an `id` (and a title, ac_ids and scope)")
        tid = t["id"].strip()
        if tid in seen:
            raise ValueError(f"task {tid}: duplicate task id")
        seen.add(tid)
        title = t.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"task {tid}: needs a `title`")
        acs = _str_list(t.get("ac_ids"), f"task {tid}: `ac_ids`")
        if not acs:
            raise ValueError(f"task {tid}: names no acceptance criterion (`ac_ids`) — a task no "
                             f"criterion demands is work nothing will test")
        unknown = [a for a in acs if a not in known]
        if unknown:
            raise ValueError(f"task {tid}: `ac_ids` names unknown criteria: {', '.join(unknown)}")
        scope = _str_list(t.get("scope"), f"task {tid}: `scope`")
        if not scope:
            raise ValueError(f"task {tid}: needs a `scope` (the globs it may write) — the hook "
                             f"narrows develop to the active task's scope")
        if authorized:
            outside = [g for g in scope if not _matches(g, authorized)]
            if outside:
                raise ValueError(f"task {tid}: scope {', '.join(outside)} is outside the spec's "
                                 f"scope.authorized — a task scope is a SUBSET of the spec's")
        files = _str_list(t.get("files"), f"task {tid}: `files`")
        stray = [f for f in files if not _matches(f, scope)]
        if stray:
            raise ValueError(f"task {tid}: files {', '.join(stray)} are outside its own scope")
        deps = _str_list(t.get("depends_on"), f"task {tid}: `depends_on`")
        parallel = t.get("parallel", False)
        if not isinstance(parallel, bool):
            raise ValueError(f"task {tid}: `parallel` must be true or false")
        tasks.append(Task(id=tid, title=title.strip(), ac_ids=acs, scope=scope, files=files,
                          depends_on=deps, consumes=str(t.get("consumes") or ""),
                          produces=str(t.get("produces") or ""), parallel=parallel))
    ids = {t.id for t in tasks}
    for t in tasks:
        missing = [d for d in t.depends_on if d not in ids]
        if missing:
            raise ValueError(f"task {t.id}: depends_on unknown tasks: {', '.join(missing)}")
        if t.id in t.depends_on:
            raise ValueError(f"task {t.id}: depends on itself")
    _no_cycles(tasks)
    uncovered = [a for a in ac_ids if not any(a in t.ac_ids for t in tasks)]
    if uncovered:
        raise ValueError(f"acceptance criteria in no task: {', '.join(uncovered)} — every AC "
                         f"needs >= 1 task")
    return tuple(tasks)


def _no_cycles(tasks: Sequence[Task]) -> None:
    deps = {t.id: set(t.depends_on) for t in tasks}
    done: set = set()
    while deps:
        ready = [k for k, v in deps.items() if v <= done]
        if not ready:
            raise ValueError(f"tasks depend on each other in a cycle: {', '.join(sorted(deps))}")
        for k in ready:
            done.add(k)
            del deps[k]


# ======================================================================================
# R3 — the capped clarify record
# ======================================================================================

@dataclass(frozen=True)
class Clarification:
    id: str
    question: str
    recommended: str
    answer: str = ""
    category: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "question": self.question, "recommended": self.recommended,
                "answer": self.answer, "category": self.category}


@dataclass(frozen=True)
class Assumption:
    id: str
    text: str
    for_question: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "for": self.for_question}


def cap_message(n: int) -> str:
    return (f"clarify cap: {n} clarifying questions recorded, the cap is {CLARIFY_CAP} — after "
            f"{CLARIFY_CAP} questions mokata stops asking and proceeds on STATED ASSUMPTIONS "
            f"(record each in the spec's `assumptions[]`), never a sixth question")


def clarify_from_payload(raw_q: Any, raw_a: Any) -> Tuple[Tuple[Clarification, ...],
                                                           Tuple[Assumption, ...]]:
    """The wire `clarifications[]` + `assumptions[]`, strictly validated; the cap HOLDS here."""
    if raw_q is None:
        raw_q = []
    if raw_a is None:
        raw_a = []
    if not isinstance(raw_q, list):
        raise ValueError("`clarifications` must be a list of {id, question, recommended, answer}")
    if not isinstance(raw_a, list):
        raise ValueError("`assumptions` must be a list of {id, text, for?}")
    if len(raw_q) > CLARIFY_CAP:
        raise ValueError(cap_message(len(raw_q)))
    qs: List[Clarification] = []
    for q in raw_q:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not q["id"].strip():
            raise ValueError("each clarification needs an `id`")
        qid = q["id"].strip()
        for f in ("question", "recommended"):
            if not isinstance(q.get(f), str) or not q[f].strip():
                raise ValueError(f"clarification {qid}: needs `{f}` (each question is asked WITH "
                                 f"its recommended answer)")
        answer = q.get("answer") or ""
        if not isinstance(answer, str):
            raise ValueError(f"clarification {qid}: `answer` must be text")
        qs.append(Clarification(id=qid, question=q["question"].strip(),
                                recommended=q["recommended"].strip(), answer=answer.strip(),
                                category=str(q.get("category") or "")))
    if len({q.id for q in qs}) != len(qs):
        raise ValueError("clarification ids must be unique")
    ids = {q.id for q in qs}
    asm: List[Assumption] = []
    for a in raw_a:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"].strip():
            raise ValueError("each assumption needs an `id`")
        if not isinstance(a.get("text"), str) or not a["text"].strip():
            raise ValueError(f"assumption {a['id']}: needs `text` (the assumption stated)")
        target = a.get("for") or ""
        if target and target not in ids:
            raise ValueError(f"assumption {a['id']}: `for` names no clarification ({target})")
        asm.append(Assumption(id=a["id"].strip(), text=a["text"].strip(),
                              for_question=str(target)))
    covered = {a.for_question for a in asm}
    open_q = [q.id for q in qs if not q.answer and q.id not in covered]
    if open_q:
        raise ValueError(f"clarifications {', '.join(open_q)} are unanswered and no assumption "
                         f"covers them — proceed on a stated assumption ({{id, text, for}})")
    return tuple(qs), tuple(asm)


def clarify_from_state(raw_q: Any, raw_a: Any) -> Tuple[Tuple[Clarification, ...],
                                                         Tuple[Assumption, ...]]:
    """Lenient read of a persisted record (judged by the plan hash, not re-validated)."""
    qs = tuple(Clarification(id=str(q["id"]), question=str(q.get("question", "")),
                             recommended=str(q.get("recommended", "")),
                             answer=str(q.get("answer", "") or ""),
                             category=str(q.get("category", "") or ""))
               for q in (raw_q if isinstance(raw_q, list) else [])
               if isinstance(q, dict) and q.get("id"))
    asm = tuple(Assumption(id=str(a["id"]), text=str(a.get("text", "")),
                           for_question=str(a.get("for", "") or ""))
                for a in (raw_a if isinstance(raw_a, list) else [])
                if isinstance(a, dict) and a.get("id"))
    return qs, asm


# ======================================================================================
# the plan hash
# ======================================================================================

def plan_hash(data: Dict[str, Any]) -> str:
    """SHA-256 of the persisted spec's canonical JSON, minus `UNHASHED`."""
    import hashlib
    body = {k: v for k, v in data.items() if k not in UNHASHED}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return HASH_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def seal(data: Dict[str, Any]) -> Dict[str, Any]:
    """`data` with its plan hash — called ONLY by the committer, inside the human-gated write."""
    out = dict(data)
    out[PLAN_HASH_KEY] = plan_hash(out)
    return out


@dataclass(frozen=True)
class PlanAttestation:
    """Does the persisted plan still carry the bytes the human approved?"""

    code: str
    reason: str
    approved: str = ""        # the hash stamped at approval
    actual: str = ""          # the hash of what is on disk now

    @property
    def ok(self) -> bool:
        return self.code == ATTEST_OK


def attest(data: Any) -> PlanAttestation:
    """Judge a persisted spec dict. Pure; never raises; hashlib only on a sealed spec."""
    if not isinstance(data, dict):
        return PlanAttestation(ATTEST_UNSEALED, "the spec on record is unreadable")
    approved = data.get(PLAN_HASH_KEY)
    tasks = data.get(TASKS_KEY)
    if not isinstance(approved, str) or not approved or not isinstance(tasks, list) or not tasks:
        return PlanAttestation(
            ATTEST_UNSEALED,
            "this run's spec was approved WITHOUT a task plan and plan hash (it predates "
            "mokata 0.0.26's tasks[] + plan attestation) — run `mokata spec migrate <run>` "
            "(`mokata doctor` names the run) to approve a one-task plan, or re-emit it with "
            "tasks[] (/mokata:spec, or `spec_amend` once work is in flight)")
    actual = plan_hash(data)
    if actual != approved:
        return PlanAttestation(
            ATTEST_MISMATCH,
            f"the plan CHANGED after approval — the spec on record hashes to {actual[:19]}…, the "
            f"approved plan was {approved[:19]}…. Nothing may proceed against an unapproved plan: "
            f"re-approve it (re-emit through /mokata:spec, or `spec_amend` once work is in flight)",
            approved=approved, actual=actual)
    return PlanAttestation(ATTEST_OK, "the plan matches its approved hash", approved=approved,
                           actual=actual)


# ======================================================================================
# run-scoped reads (the stages' attestation, the task cursor)
# ======================================================================================

def spec_key(run_id: str) -> str:
    return "emitted_spec__" + run_id


def cursor_key(run_id: str) -> str:
    return TASK_CURSOR_PREFIX + run_id


def read_run_spec(root: str, run_id: str) -> Optional[Dict[str, Any]]:
    """This run's persisted spec dict, or None (absent / unreadable). Never raises."""
    try:
        from .state import StateStore
        from .tdd_state import state_dir
        data = StateStore(state_dir(root)).read(spec_key(run_id))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def attest_run(root: str, run_id: Optional[str]) -> Optional[PlanAttestation]:
    """The run's plan attestation — None when the run has no spec (nothing to attest)."""
    if not run_id:
        return None
    data = read_run_spec(root, run_id)
    return None if data is None else attest(data)


#: The stages that attest the plan on ENTRY (`progress mark`): everything after the approval.
ATTESTED_STAGES = ("develop", "review", "ship")


def stage_attestation(root: str, run_id: Optional[str], stage: str) -> Tuple[Dict[str, Any], str]:
    """`(event data, refusal)` for entering `stage` on `run_id`. A stage after approval stamps the
    plan hash it attested (`{"plan_hash": ...}`); a plan that is not the approved one REFUSES the
    entry (`refusal` names why). No spec on the run, or a stage before approval: nothing to attest.
    Never raises."""
    if stage not in ATTESTED_STAGES:
        return {}, ""
    att = attest_run(root, run_id)
    if att is None:
        return {}, ""
    if not att.ok:
        return {}, f"not entering '{stage}' — {att.reason}"
    return {PLAN_HASH_KEY: att.approved}, ""


@dataclass(frozen=True)
class Cursor:
    current: str = ""
    done: Tuple[str, ...] = ()


def cursor_from_state(data: Any) -> Cursor:
    if not isinstance(data, dict):
        return Cursor()
    cur = data.get("current")
    return Cursor(current=cur if isinstance(cur, str) else "", done=_strs(data.get("done")))


def _ready(task: Task, done: Iterable[str]) -> bool:
    return set(task.depends_on) <= set(done)


def active_task(tasks: Sequence[Task], cursor: Cursor) -> Optional[Task]:
    """The task develop is on: the cursor's `current` while it is open, else the first open task
    whose dependencies are done. None when every task is done."""
    done = set(cursor.done)
    for t in tasks:
        if t.id == cursor.current and t.id not in done:
            return t
    for t in tasks:
        if t.id not in done and _ready(t, done):
            return t
    return None


@dataclass(frozen=True)
class TaskVerdict:
    allowed: bool
    reason: str
    task_id: str = ""


def classify_task(tasks: Sequence[Task], cursor: Cursor, path: str,
                  root: str = "") -> TaskVerdict:
    """May an implementation write to `path` proceed under the ACTIVE task? Pure, never raises."""
    if not tasks:
        return TaskVerdict(True, "no task plan")
    task = active_task(tasks, cursor)
    if task is None:
        if any(t.covers(path, root) for t in tasks):
            return TaskVerdict(True, "every task is done; inside the plan's surface")
        return TaskVerdict(False, f"{_rel(path, root)} is outside every task of the approved "
                                  f"plan (all tasks are done)")
    if task.covers(path, root):
        return TaskVerdict(True, f"inside the active task {task.id}", task_id=task.id)
    owner = next((t.id for t in tasks if t.covers(path, root)), "")
    hint = (f" — it belongs to {owner}: finish {task.id} (`mokata spec task done {task.id}`) or "
            f"switch (`mokata spec task start {owner}`)" if owner else
            " — no task of the approved plan covers it")
    return TaskVerdict(False, f"{_rel(path, root)} is outside the ACTIVE task {task.id} "
                              f"'{task.title}' (scope: {', '.join(task.scope[:4])}){hint}",
                       task_id=task.id)


def read_cursor(root: str, run_id: str) -> Cursor:
    try:
        from .state import StateStore
        from .tdd_state import state_dir
        return cursor_from_state(StateStore(state_dir(root)).read(cursor_key(run_id)))
    except (OSError, ValueError):
        return Cursor()


def move_cursor(root: str, run_id: str, tasks: Sequence[Task], task_id: str, *,
                done: bool, plan: str) -> Cursor:
    """`start` (done=False) or `done` (done=True) a task. Run-state, UNGATED (the tier of the TDD
    record). Raises ValueError for an unknown task, or starting one whose dependencies are open."""
    from .state import StateStore
    from .tdd_state import state_dir
    by_id = {t.id: t for t in tasks}
    if task_id not in by_id:
        raise ValueError(f"no task {task_id} in the approved plan "
                         f"(tasks: {', '.join(by_id) or 'none'})")
    box: Dict[str, Cursor] = {}

    def _mutate(current: Any) -> Dict[str, Any]:
        cur = cursor_from_state(current)
        finished = list(cur.done)
        if done:
            if task_id not in finished:
                finished.append(task_id)
            new_current = "" if cur.current == task_id else cur.current
        else:
            open_deps = [d for d in by_id[task_id].depends_on if d not in finished]
            if open_deps:
                raise ValueError(f"task {task_id} depends on {', '.join(open_deps)}, which "
                                 f"{'is' if len(open_deps) == 1 else 'are'} not done")
            finished = [d for d in finished if d != task_id]
            new_current = task_id
        box["c"] = Cursor(current=new_current, done=tuple(finished))
        return {"run_id": run_id, "current": new_current, "done": finished, "plan_hash": plan}

    StateStore(state_dir(root)).update(cursor_key(run_id), _mutate, default=None)
    return box["c"]


__all__ = [
    "ASSUMPTIONS_KEY", "ATTESTED_STAGES", "ATTEST_MISMATCH", "ATTEST_OK", "ATTEST_UNSEALED",
    "CLARIFICATIONS_KEY", "CLARIFY_CAP", "PLAN_GATE", "PLAN_HASH_KEY", "TASKS_KEY",
    "TASK_CURSOR_PREFIX", "Assumption", "Clarification", "Cursor", "PlanAttestation", "Task",
    "TaskVerdict", "active_task", "attest", "attest_run", "cap_message", "clarify_from_payload",
    "clarify_from_state", "classify_task", "cursor_from_state", "cursor_key", "move_cursor",
    "plan_hash", "read_cursor", "read_run_spec", "seal", "stage_attestation",
    "tasks_from_payload", "tasks_from_state",
]
