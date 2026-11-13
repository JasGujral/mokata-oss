"""0.0.27 P13 `ID-VIS-PROOF` — the AC → test → code TRACE, and the risk overlay on it (ruling U15:
the launch demo; mokata's core differentiator made visible).

For a change set — a run, one session's worktree diff, or a commit range — this module computes,
deterministically and from stores that already exist, the proof that the change was built the
mokata way:

    AC ──recorded/planned──▶ test ──structural/lexical──▶ code (the changed definitions)
     └────────anchor────────────────────────────────────▶ code

  * the ACs come from the run's EMITTED spec (`emitted_spec__<run>`, `engine.spec_gate`);
  * the AC → test map from the run's test record (`test_runs`: seeded at emit, extended by every
    `mokata progress test-run --ac`), and each test's RED-then-GREEN history from the same record
    plus the persisted TDD phase (`tdd_state`): a test that was never recorded failing before it
    passed is "not TDD-proven";
  * the code from the change set's hunks: the changed definitions (`nav.outline`), each with its
    callers, blast radius and the tests that reach it (`nav.impact` — the SAME blast-radius walk
    the Impact view runs, with its `basis` and its disclosed cut);
  * the approvals and gate verdicts from the audit ledger and the progress log, keyed to the run.

THE RULE (round 5 — the fallback form HANDOFF.md names; every path goes through `resolve_test`):
a test is identified by its FULL id `path::Class::name` (pytest's node id, parametrisation
stripped; `tests\\t.py` and `./tests/t.py` are the same file), and ONLY a full id can prove: its file
must be tracked by git in the tree being judged (not ignored; at `head` for a range; a live trace
also accepts an untracked file git does not ignore), and its AST — parsed here, fresh from that
text, with `ast` — must bind exactly that qualified key ONCE. A BARE id never proves (nothing short
of enumerating every tracked, untracked and ignored file could show a name unique, and that is not
bounded by the change set). A class-qualified id whose class does not hold the name is
`unlocated`; a name the file binds more than once (two classes, a module function and a method, a
rebinding, a star import in scope) makes a qualifier-less id `ambiguous`; ids of different
qualifiers are different tests and their histories never merge. Every other case is a NON-PROVING
state, shown with its reason, and counts toward nothing: `ambiguous`, `unlocated`, `unparsed` (the
file does not parse / has no structural outline), `ignored`, `deleted` (git shows the file or the
definition removed). A test is proven only when EVERY recorded id of it — each param, each spelling
of its full id — went red, then green, and is green now. Non-Python test files are located by a
structural outline of their own text, by name, for qualifier-less ids only.

Round 6 (J1-J5): every spelling of one file is ONE path (`./`, `//`, `..`, `\\`, and a case
variant where git says the filesystem folds case — `_Locator.canon`); a rootdir-relative id
resolves against the pytest rootdirs (directories holding a pytest config) and is `ambiguous`
when more than one file answers; a test a runner never collects is `unlocated`; a proof holds
only while the test's content stamp is the one it ran green under; and a later RED under a bare
or dotted spelling that could name the test (`could_name`) blocks its proof.

Round 7 (K1-K5): a bare id is qualified at write time ONLY from the runner's own report
(`test_runs.full_ids`), so a bare node here stays bare and its AC says `bare id: run with full ids
to prove`; every tracked file ending in a rootdir-relative path is a candidate (no config needed
to be ambiguous); case folding is probed on the filesystem; the stamp is the AST of the test plus
its scopes' non-test code and the conftest.py chain; a nested walk's counts never overwrite the
job's own done/total.

The RISK OVERLAY is derived, never guessed: an AC with no test; a test with no RED on record; a
changed definition no test reaches (`untested` only on a structural answer that was not cut —
otherwise `unknown`, an absence of evidence is not evidence of absence); gates red (blocked) or
skipped (an override, a stop let through unverified); the blast radius (fan-in) of each change.

BOUNDED BY THE CHANGE SET — WITH ONE DISCLOSED EXCEPTION. The graph is asked one question per
changed definition, and the test locator reads only the files recorded ids name. The exception
is the graph's own index: with no code-review-graph, the FIRST trace in a process builds the AST
floor's repo index (`ast_backend._ensure_index` — a walk of the repository's source files, pruned
by directory name, not by `.gitignore`). It runs inside this same job, reported ("indexing the
repository …", then the floor's own "reading the AST index" file count) and cancellable per file;
later traces reuse it and re-parse only changed files. Nothing in this module walks the repo. Thousands of ACs and tests
are PAGED (`page` / `size`, the answer's `next`), never capped; the page draws at most
`visuals.DRAW_LIMIT` nodes and SAYS when it reduced the picture (`draw.reduced`, `draw.note`) — the
rest is the same paged data as a table ("show all"). Progress is reported from inside the walk
(`knowledge.cancel.report`) so the route runs it as a visible job (`ide/jobs.py`, `proof.trace`).

NO API KEY, NO GRAPH NEEDED. With no code-review-graph installed the layer answers from the floor
(the AST reader on Python, the lexical floor otherwise) and the answer says so (`graph.backend`,
`graph.basis`, `graph.note`). Nothing here writes.

The JSON shape is versioned (`TRACE_VERSION`) and documented in `features/proof.contract.json`;
P46 (the change explainer) consumes it — change the shape, bump the version.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..knowledge.cancel import checkpoint, current_reporter, report, reporting
from . import diffs as D
from . import nav
from ..errors import MokataError
from ..test_ids import (OUTSIDE, TARGET_STATES, UNRESOLVED, fs_folds_case, normalise_path,
                        py_test_defs, raw_path_of, same_target, stamp_of, stamp_target,
                        test_key_of, test_label_of, test_name_of, test_param_of, test_path_of,
                        test_qualifier_of)
from ..repo_paths import as_name, name_of

TRACE_VERSION = 1
#: rows (ACs + code with no AC) per page — a page, not a cap; `next` continues.
PAGE_ROWS = 200
MAX_PAGE_ROWS = 2000

# --- vocabulary (the page and P46 switch on these words; the contract lists them) -------------
AC_PROVEN, AC_GREEN_NOT_TDD, AC_RED, AC_UNRUN, AC_UNTESTED, AC_AMBIGUOUS = (
    "proven", "green-not-tdd", "red", "unrun", "untested", "ambiguous")
TDD_PROVEN, TDD_GREEN_ONLY, TDD_RED, TDD_UNRECORDED = ("proven", "green-only", "red", "unrecorded")
#: a test node's STATE in the tree: present (located, or its file exists), deleted (the record
#: names a test the tree no longer holds), ambiguous (a bare name several tests answer to),
#: unlocated (could not be looked for — a foreign head). Only `present` can prove.
STATE_PRESENT, STATE_DELETED, STATE_AMBIGUOUS, STATE_UNLOCATED, STATE_UNPARSED, STATE_IGNORED = (
    "present", "deleted", "ambiguous", "unlocated", "unparsed", "ignored")
NON_PROVING = frozenset({STATE_DELETED, STATE_AMBIGUOUS, STATE_UNLOCATED, STATE_UNPARSED,
                         STATE_IGNORED})
COV_COVERED, COV_UNTESTED, COV_UNKNOWN, COV_PENDING, COV_TEST = (
    "covered", "untested", "unknown", "pending", "test")
GATE_RED, GATE_SKIPPED, GATE_PASSED, GATE_UNKNOWN = ("red", "skipped", "passed", "unknown")
EDGE_AC_TEST, EDGE_TEST_CODE, EDGE_AC_CODE = ("tested-by", "exercises", "anchors")
TARGET_RUN, TARGET_SESSION, TARGET_RANGE, TARGET_LIVE = ("run", "session", "range", "live")



class TraceRefused(MokataError, ValueError):
    """A target the trace cannot be asked for (no such run, a bad ref, nothing named) — a HARD
    error: the route answers it as a refusal, nothing falls back."""


# ============================================================================== the change set

def _new_side_ranges(fd: Dict[str, Any]) -> List[Tuple[int, int]]:
    """The NEW-side line ranges a file's hunks touched (added lines; a pure deletion marks the
    line it left behind so the enclosing definition still counts as changed)."""
    out: List[Tuple[int, int]] = []
    for h in fd.get("hunks") or ():
        lo = hi = None
        last_new = None
        for op, _old, new, _text in h.get("lines") or ():
            if new is not None:
                last_new = new
            if op == "+":
                lo = new if lo is None else min(lo, new)
                hi = new if hi is None else max(hi, new)
            elif op == "-" and lo is None and last_new is not None:
                lo = hi = last_new
        if lo is not None and hi is not None:
            out.append((lo, hi))
    return out


def _lines_spec(ranges: Sequence[Tuple[int, int]]) -> str:
    return ",".join(f"{lo}-{hi}" if hi != lo else str(lo) for lo, hi in ranges)


def _files_of(ds: Dict[str, Any]) -> List[Dict[str, Any]]:
    files = []
    for fd in ds.get("files") or ():
        path = fd.get("path") or ""
        if not path:
            continue
        ranges = _new_side_ranges(fd)
        files.append({"path": path, "status": fd.get("status") or "modified",
                      "added": int(fd.get("added") or 0), "removed": int(fd.get("removed") or 0),
                      "too_large": bool(fd.get("too_large")), "binary": bool(fd.get("binary")),
                      "lines": _lines_spec(ranges), "hunks": len(fd.get("hunks") or ()),
                      "is_test": nav._is_test(path)})
    return sorted(files, key=lambda f: f["path"])


def _merge_files(sets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[str, Dict[str, Any]] = {}
    for ds in sets:
        for f in _files_of(ds):
            cur = by.get(f["path"])
            if cur is None:
                by[f["path"]] = f
                continue
            cur["added"] += f["added"]
            cur["removed"] += f["removed"]
            cur["hunks"] += f["hunks"]
            cur["too_large"] = cur["too_large"] or f["too_large"]
            cur["lines"] = ",".join(x for x in (cur["lines"], f["lines"]) if x)
    return [by[k] for k in sorted(by)]


def _sessions_of_run(root: str, rid: str) -> List[str]:
    from .plans import _sessions_by_run
    return sorted(_sessions_by_run(root).get(rid, []))


def _run_of_session(root: str, sid: str) -> Optional[str]:
    from ..run_intent import CLAIM_BOUND, session_claim
    claim, rid = session_claim(root, sid)
    if claim == CLAIM_BOUND and rid:
        return rid
    from .sessions import sessions as all_sessions
    for r in all_sessions(root, transcripts=False):
        if r.id == sid and r.run:
            return r.run
    return None


def change_set(root: str, *, run: Optional[str], session: Optional[str], base: str,
               head: str, live: bool = False) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    """`(target, files, note)` — what changed, from the one source each target has:
    a session → `diffs.session_diff` (exact in its worktree); a range → `diffs.refs`; the live
    working tree → `diffs.live` (uncommitted and untracked, what a stranger's repo has to show
    before any run); a run → every session bound to it, plus the run's working set paths when no
    session diff names them (those come with no hunks: the whole file is read as changed)."""
    note = ""
    if live:
        ds = D.live(root).facts()
        if ds.get("note") and not ds.get("files"):
            raise TraceRefused(ds["note"])
        target = {"kind": TARGET_LIVE, "run": run, "session": None, "base": "", "head": "",
                  "title": ds.get("title") or "working tree", "basis": "git"}
        return target, _files_of(ds), str(ds.get("note") or "")
    if session:
        ds = D.session_diff(root, session).facts()
        target = {"kind": TARGET_SESSION, "run": run, "session": session, "base": "",
                  "head": "", "title": ds.get("title") or f"session {session[:8]}",
                  "basis": str(ds.get("basis") or "exact")}
        return target, _files_of(ds), str(ds.get("note") or "")
    if base or head:
        if not (base and head):
            raise TraceRefused("a commit range needs both base and head")
        ds = D.refs(root, base, head).facts()
        if ds.get("note") and not ds.get("files"):
            raise TraceRefused(ds["note"])
        target = {"kind": TARGET_RANGE, "run": run, "session": None, "base": base,
                  "head": head, "title": ds.get("title") or f"{base}...{head}", "basis": "git"}
        return target, _files_of(ds), str(ds.get("note") or "")
    if not run:
        raise TraceRefused("name a run, a session or a commit range (base + head)")
    sids = _sessions_of_run(root, run)
    sets = [D.session_diff(root, sid).facts() for sid in sids]
    files = _merge_files(sets)
    named = {f["path"] for f in files}
    from ..working_set import working_set
    ws = working_set(root, run)
    extra = sorted(p for p in ws.touched_files if p not in named)
    for p in extra:
        files.append({"path": p, "status": "modified", "added": 0, "removed": 0,
                      "too_large": False, "binary": False, "lines": "", "hunks": 0,
                      "is_test": nav._is_test(p)})
    files.sort(key=lambda f: f["path"])
    if extra:
        note = (f"{len(extra)} file(s) known only from the run's working set (no diff recorded): "
                "read whole")
    if ws.touched_dropped:
        note = (note + " | " if note else "") + \
            f"the working set dropped {ws.touched_dropped} older path(s) (its cap)"
    target = {"kind": TARGET_RUN, "run": run, "session": None, "base": "", "head": "",
              "title": f"run {run}", "basis": "sessions" if sids else "working-set"}
    return target, files, note


# ============================================================================== the run's record

def _run_store(root: str, rid: str) -> Any:
    from .plans import _run_store
    return _run_store(root, rid)


def _spec_acs(root: str, rid: str) -> Tuple[List[Dict[str, Any]], str]:
    """`(acs, note)` — each AC `{id, text, kind, anchors}`; the note says when the spec is absent
    or malformed (then there are no ACs to prove, and the answer says why)."""
    from ..engine.spec_gate import read_emitted_spec
    spec, malformed = read_emitted_spec(_run_store(root, rid))
    if malformed:
        return [], "the run's emitted spec is present but unreadable (malformed)"
    if spec is None:
        return [], "the run has no emitted spec — no acceptance criteria to prove"
    acs = []
    for c in spec.criteria:
        acs.append({"id": c.id, "text": (c.text or "")[:400], "kind": c.kind,
                    "anchors": [{"file": a.file, "symbol": a.symbol, "kind": a.kind,
                                 "source": a.source} for a in c.anchors]})
    return acs, ""


def _tdd(root: str, rid: str) -> Tuple[Set[str], Set[str], Dict[str, Dict[str, Any]]]:
    """`(red, green, history)` — the persisted phase sets and, where the test record kept them,
    each test's first RED / first GREEN-after-RED times."""
    from ..tdd_state import load
    from ..test_runs import read_test_record
    try:
        red, green = load(_run_store(root, rid), rid)
    except (OSError, ValueError, TypeError):
        red, green = set(), set()
    rec = read_test_record(root, rid)
    return red, green, rec.get("tests") or {}


def tdd_verdict(test_id: str, red: Set[str], green: Set[str],
                history: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The TDD proof of ONE recorded test id. With timestamps (the test record's per-test history)
    the ORDER decides: the test's state is its LATEST outcome, and "proven" means it was recorded
    RED first, GREEN after that (`red_at < green_at`), and its latest run is green. With the phase
    sets alone (a record from before timed history) membership decides, and `basis: "sets"` says
    so. ONE id, its own history only — a test with several ids is judged by `node_verdict`."""
    test_ids = [test_id] if test_id else []
    timed = [history[i] for i in test_ids if history.get(i)]
    if timed:
        latest = max(timed, key=lambda h: (h.get("last_at") or 0.0))
        reds = [h["red_at"] for h in timed if h.get("red_at") is not None]
        red_at = min(reds) if reds else None
        greens = [h["green_at"] for h in timed
                  if h.get("green_at") is not None and red_at is not None and h["green_at"] > red_at]
        green_at = min(greens) if greens else None
        firsts = [h.get("first") for h in timed if h.get("first")]
        test_first = ("red" in firsts) if firsts else None     # None: unknown (older record)
        last = latest.get("last")
        if last == "red":
            tdd = TDD_RED
        elif (red_at is not None and green_at is not None and red_at < green_at
              and test_first is not False):
            tdd = TDD_PROVEN
        else:
            tdd = TDD_GREEN_ONLY
        return {"tdd": tdd, "red_at": red_at, "green_at": green_at, "last": last,
                "last_at": latest.get("last_at"), "basis": "timed"}
    # No timestamps: a record written before the timed history existed (the user's own `.mokata/`
    # data — kept, never discarded). Membership alone decides, and `basis: "sets"` says so.
    ids = set(test_ids)
    if ids & red and ids & green:
        tdd = TDD_PROVEN
    elif ids & red:
        tdd = TDD_RED
    elif ids & green:
        tdd = TDD_GREEN_ONLY
    else:
        tdd = TDD_UNRECORDED
    return {"tdd": tdd, "red_at": None, "green_at": None, "last": None, "last_at": None,
            "basis": "sets" if tdd != TDD_UNRECORDED else "none"}


#: clock margin around the run's window when attaching ledger rows that name no run
WINDOW_MARGIN_S = 300.0


def _parse_at(at: Any) -> float:
    from .plans import _parse_ts
    return _parse_ts(at) if isinstance(at, str) else 0.0


def run_window(root: str, rid: str) -> Tuple[Optional[float], Optional[float]]:
    """`(start, end)` of the run in epoch seconds — its checkpoint's `created_at` (write-once) and
    `completed_at` (None while it runs). Both None when the run has no checkpoint."""
    from ..govern.resume import CHECKPOINT_PREFIX
    try:
        data = _run_store(root, rid).read(CHECKPOINT_PREFIX + rid)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return None, None
    ca = data.get("created_at")
    start = float(ca) if isinstance(ca, (int, float)) and not isinstance(ca, bool) else None
    end = _parse_at(data.get("completed_at")) or None
    return start, end


def _ledger_rows(root: str, rid: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """`(rows_of_run, rows_unkeyed_in_window, note)` — the audit ledger's rows for this run,
    oldest first, with NO cap (verification F5: a cap with no notice made a skipped gate vanish,
    which reads as a clean run).

    Bounded by the RUN, not the ledger: the file is append-only with monotonic `at`, so the read
    BISECTS the bytes to the first row at or after the run's start (`created_at` − a clock margin)
    and streams forward from there, reporting progress (it runs inside the proof job). A run with
    no checkpoint start streams the whole file — said in `note`.

    Rows that name the run (`run == rid`) are the run's. Rows that name NO run at all (the Karpathy
    and spec gate writers record none) are attached when they fall inside the run's window —
    returned separately so the caller can LABEL them (`attached: time-window`)."""
    from .. import MOKATA_DIR
    from ..govern.ledger import AuditLedger
    path = AuditLedger.path_for(os.path.join(root, MOKATA_DIR))
    mine: List[Dict[str, Any]] = []
    unkeyed: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return mine, unkeyed, ""
    start, end = run_window(root, rid)
    lo_t = (start - WINDOW_MARGIN_S) if start is not None else None
    hi_t = (end + WINDOW_MARGIN_S) if end is not None else None
    note = "" if start is not None else ("the run has no recorded start — the whole audit ledger "
                                         "was read for its rows")
    size = os.path.getsize(path)
    import json as _json
    with open(path, "rb") as fh:

        def line_at(off: int) -> Tuple[int, Optional[Dict[str, Any]]]:
            fh.seek(off)
            if off > 0:
                fh.readline()
            start_off = fh.tell()
            raw = fh.readline()
            if not raw:
                return start_off, None
            try:
                d = _json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                d = None
            return start_off, d if isinstance(d, dict) else None

        pos = 0
        if lo_t is not None:
            lo, hi = 0, size
            while hi - lo > 4096:
                mid = (lo + hi) // 2
                _o, d = line_at(mid)
                if d is None or _parse_at(d.get("at")) < lo_t:
                    lo = mid
                else:
                    hi = mid
            pos = lo
        fh.seek(pos)
        if pos > 0:
            fh.readline()
        n = 0
        while True:
            checkpoint()
            raw = fh.readline()
            if not raw:
                break
            n += 1
            if n % 2000 == 0:
                report(done=fh.tell(), total=size, unit="bytes", note="reading the audit ledger")
            try:
                d = _json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(d, dict):
                continue
            if d.get("run") == rid:
                mine.append(d)
            elif "run" not in d and lo_t is not None:
                t = _parse_at(d.get("at"))
                if t >= lo_t and (hi_t is None or t <= hi_t):
                    unkeyed.append(d)
    return mine, unkeyed, note


def _log_events(root: str, rid: str) -> List[Dict[str, Any]]:
    """This run's progress events, oldest first, uncapped — STREAMED line by line under the job
    (G4: the whole-log read held every run's events in memory and could not be cancelled); only
    this run's rows are kept, so memory is the run's, not the repository's history."""
    from ..progress_events import ProgressLog
    from ..tdd_state import state_dir
    import json as _json
    path = ProgressLog.from_state_dir(state_dir(root)).path
    out: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        n = 0
        while True:
            raw = fh.readline()
            if not raw:
                break
            n += 1
            if n % 2000 == 0:
                checkpoint()
                report(done=fh.tell(), total=size, unit="bytes", note="reading the progress log")
            # cheap pre-filter: the run id must appear in the line at all
            if rid.encode("utf-8") not in raw:
                continue
            try:
                e = _json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(e, dict) and e.get("run_id") == rid:
                out.append(e)
    return out


def _verdict_of(e: Dict[str, Any]) -> str:
    """A Karpathy / spec gate row's verdict — `unknown` when the row says neither way (never
    `passed` by default: verification F5)."""
    passed = e.get("passed")
    if isinstance(passed, bool):
        return GATE_PASSED if passed else GATE_RED
    decision = str(e.get("decision") or e.get("verdict") or "").lower()
    if decision in ("pass", "passed", "ok", "allowed", "allow"):
        return GATE_PASSED
    if decision in ("fail", "failed", "block", "blocked", "refused", "deny", "denied"):
        return GATE_RED
    return GATE_UNKNOWN


def gates_and_approvals(root: str, rid: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """`(gates, approvals, note)` for the run, oldest first.

    gates: `{gate, verdict: red|skipped|passed|unknown, at, path, detail, source, attached}` — a
    `gate_block` in the progress log is RED; a `gate_override` or a stop `released-unverified` in
    the ledger is SKIPPED; a `write_gate` decision, a passed review verdict, a Karpathy/spec gate
    pass is PASSED; a row that says neither way is UNKNOWN. `attached` is `run` for a row that
    names the run, `time-window` for a Karpathy / spec gate row that names no run and fell inside
    the run's window (labelled, never silently merged).
    approvals: `{proposal, decision, target, actor, at, tool}` from the `write_approval` and
    `plan_approved` ledger rows."""
    gates: List[Dict[str, Any]] = []
    approvals: List[Dict[str, Any]] = []
    for e in _log_events(root, rid):
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        if e.get("type") == "gate_block":
            gates.append({"gate": str(data.get("gate") or "?"), "verdict": GATE_RED,
                          "at": str(e.get("ts") or ""), "path": str(data.get("path") or ""),
                          "detail": "blocked", "source": "progress-log", "attached": "run"})
        elif e.get("type") == "review_verdict":
            ok = bool(data.get("passed"))
            gates.append({"gate": "review", "verdict": GATE_PASSED if ok else GATE_RED,
                          "at": str(e.get("ts") or ""), "path": "",
                          "detail": "review passed" if ok else "review did not pass",
                          "source": "progress-log", "attached": "run"})
    mine, unkeyed, note = _ledger_rows(root, rid)
    for e in mine:
        kind, at = str(e.get("kind") or ""), str(e.get("at") or "")
        if kind == "gate_override":
            gates.append({"gate": str(e.get("gate") or "?"), "verdict": GATE_SKIPPED, "at": at,
                          "path": "", "detail": f"overridden by {e.get('actor') or 'a human'}: "
                          f"{str(e.get('reason') or '')[:200]}", "source": "ledger",
                          "attached": "run"})
        elif kind == "stop_evidence":
            gates.append({"gate": str(e.get("gate") or "stop-evidence"), "verdict": GATE_SKIPPED,
                          "at": at, "path": "", "detail": "stop let through unverified: "
                          + str(e.get("why") or "")[:200], "source": "ledger", "attached": "run"})
        elif kind == "write_gate":
            blocked = str(e.get("decision") or "") not in ("allowed", "approved", "committed", "ok")
            gates.append({"gate": "write-gate", "verdict": GATE_RED if blocked else GATE_PASSED,
                          "at": at, "path": str(e.get("target") or ""),
                          "detail": f"{e.get('decision') or '?'}"
                          + (f": {str(e.get('reason') or '')[:160]}" if e.get("reason") else ""),
                          "source": "ledger", "attached": "run"})
        elif kind in ("karpathy_gate", "gate"):
            gates.append({"gate": str(e.get("gate") or kind), "verdict": _verdict_of(e), "at": at,
                          "path": "", "detail": str(e.get("phase") or e.get("reason") or
                                                    e.get("detail") or "")[:160],
                          "source": "ledger", "attached": "run"})
        elif kind == "write_approval":
            approvals.append({"proposal": str(e.get("proposal") or ""),
                              "decision": str(e.get("decision") or ""),
                              "target": str(e.get("target") or ""),
                              "actor": str(e.get("actor") or e.get("approved_by") or ""),
                              "at": at, "tool": str(e.get("tool") or "")})
        elif kind == "plan_approved":
            approvals.append({"proposal": "", "decision": "plan-approved", "target": "",
                              "actor": str(e.get("approver") or e.get("actor") or ""),
                              "at": at, "tool": ""})
    for e in unkeyed:
        kind, at = str(e.get("kind") or ""), str(e.get("at") or "")
        if kind in ("karpathy_gate", "gate"):
            gates.append({"gate": str(e.get("gate") or kind), "verdict": _verdict_of(e), "at": at,
                          "path": "", "detail": (str(e.get("phase") or e.get("reason") or
                                                     e.get("detail") or "")[:140]
                                                 + " (row names no run; in this run's window)"),
                          "source": "ledger", "attached": "time-window"})
    gates.sort(key=lambda g: (g["at"], g["gate"]))
    approvals.sort(key=lambda a: (a["at"], a["proposal"]))
    return gates, approvals, note


# ============================================================================== test identity

# the id parsing and the AST locator live in `mokata.test_ids` (the record path uses them too)
_PARAM = re.compile(r"\[.*\]$")


def _qualify(defs: Optional[Dict[str, Any]], line: int, name: str) -> str:
    """The qualified key of the function named `name` whose span holds `line` (the innermost),
    from `py_test_defs`; the bare `name` when the AST does not place it."""
    best: Optional[Tuple[str, int]] = None
    for key, ln, end in (defs or {}).get("funcs") or ():
        if key.rsplit(".", 1)[-1] == name and ln <= line <= end and (best is None or ln > best[1]):
            best = (key, ln)
    return best[0] if best else name


# ============================================================================== the builder

def _code_id(path: str, name: str) -> str:
    return f"code:{path}#{name}"


def _test_id(path: str, key: str) -> str:
    """A test node's id: `test:` + its full id (`path::Class::name`; `key` is `Class.name`)."""
    return f"test:{path}::{key.replace('.', '::')}"


def _ac_id(ac: str) -> str:
    return f"ac:{ac}"


def _risk_of(code: Dict[str, Any]) -> int:
    """fan-in × missing coverage: 0 when a test reaches it; else 1 + callers (an uncovered hub
    outranks an uncovered leaf). `unknown` coverage counts half: it may be covered past the cut."""
    if code["coverage"] in (COV_COVERED, COV_TEST):
        return 0
    base = 1 + int(code.get("fan_in") or 0)
    return base if code["coverage"] == COV_UNTESTED else max(1, base // 2)


class _Build:
    """One trace under construction (the mutable middle of `trace`)."""

    def __init__(self) -> None:
        self.acs: Dict[str, Dict[str, Any]] = {}
        self.tests: Dict[str, Dict[str, Any]] = {}
        self.code: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def edge(self, src: str, dst: str, kind: str, basis: str, site: str = "") -> None:
        key = (src, dst, kind)
        if key not in self.edges:
            self.edges[key] = {"source": src, "target": dst, "kind": kind, "basis": basis,
                               "site": site}

    def test(self, nid: str, **attrs: Any) -> Dict[str, Any]:
        t = self.tests.get(nid)
        if t is None:
            t = self.tests[nid] = {"id": nid, "label": "", "path": "", "line": 0, "symbol": "",
                                   "located": False, "changed": False, "recorded_ids": [],
                                   "acs": [], "code": [], "tdd": TDD_UNRECORDED, "ambiguous": [],
                                   "state": STATE_PRESENT, "reason": "", "params": [],
                                   "qualifier": "", "tdd_note": "", "qualified_from": []}
        for k, v in attrs.items():
            if v or k not in t:
                t[k] = v
        return t


def _head_text(root: str, head: str, rel: str) -> Optional[str]:
    """The file as it is AT `head` (`git show head:rel`), None when it is not there."""
    code, out = D._git(root, "show", "--end-of-options", f"{head}:{rel}")
    return out if code == 0 else None


def status_paths(root: str) -> Dict[str, str]:
    """`{path: untracked|ignored|modified|deleted}` for every path `git status` lists (tracked
    changes, untracked AND ignored files — G3: a gitignored test the walk crosses must be seen;
    a rename's old path is `deleted` — G6) — the ONLY working-tree facts a coverage claim may be
    checked against; its size is the tree's dirtiness, never a walk of the repository."""
    code, out = D._git(root, "status", "--porcelain", "--untracked-files=all",
                       "--ignored=matching", "-z")
    if code != 0:
        return {}
    out_paths: Dict[str, str] = {}
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]                 # git spells paths with `/`
        if xy == "!!":
            out_paths[rel] = "ignored"
        elif xy == "??":
            out_paths[rel] = "untracked"
        elif "D" in xy:
            out_paths[rel] = "deleted"
        else:
            out_paths[rel] = "modified"
        if "R" in xy or "C" in xy:
            # with -z a rename / copy carries the OLD path as its own NUL field next
            if i < len(fields) and fields[i]:
                old = fields[i]
                if "R" in xy:
                    out_paths[old] = "deleted"
            i += 1
    return out_paths


def range_dirty(root: str, head: str, files: List[Dict[str, Any]],
                dirty: Optional[Dict[str, str]] = None) -> str:
    """Why a commit range CANNOT be judged from the working tree — "" when the tree IS `head` for
    everything a coverage claim would read (verification F3: an uncommitted test made code
    `covered` that is untested at `head`). Dirty when HEAD is not `head`, or any changed / test
    path is modified, untracked or deleted in the working tree. Files the blast walk CROSSES are
    checked after the walk (`crossed_dirty`), against the walk's own file set."""
    code, mine = D._git(root, "rev-parse", "--verify", f"{head}^{{commit}}")
    code2, cur = D._git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if code != 0 or code2 != 0:
        return f"cannot resolve {head} and HEAD"
    if mine.strip() != cur.strip():
        return f"the working tree is at {cur.strip()[:10]}, not at {head} ({mine.strip()[:10]})"
    dirty = status_paths(root) if dirty is None else dirty
    changed = {f["path"] for f in files}
    for rel in sorted(dirty):
        if rel in changed or nav._is_test(rel.rstrip("/")) or any(
                rel.endswith("/") and c.startswith(rel) for c in changed):
            return f"{rel} is {dirty[rel]} in the working tree"
    return ""


def _dirty_hit(dirty: Dict[str, str], path: str) -> Optional[str]:
    if path in dirty:
        return path
    for rel in dirty:
        if rel.endswith("/") and path.startswith(rel):
            return rel
    return None


def crossed_dirty(dirty: Dict[str, str], crossed: Set[str], capped: bool) -> str:
    """After the walk (coordinator ruling): an uncommitted change to a file the blast walk
    crossed also makes coverage `unknown`, said. Bounded by the walk's own file set; when a
    caller list was cut (`references_capped`) the crossed set is incomplete, so ANY dirty path
    counts — conservative, still no walk."""
    if not dirty:
        return ""
    if capped:
        rel = sorted(dirty)[0]
        return (f"{rel} is {dirty[rel]} in the working tree and the walk's caller list was cut, "
                f"so it may have crossed it")
    for path in sorted(crossed):
        rel = _dirty_hit(dirty, path)
        if rel is not None:
            return f"{rel} is {dirty[rel]} in the working tree and the blast walk crossed it"
    return ""


def _inner_counts(at: int, of: int) -> Any:
    """A progress sink for a walk nested in the per-file loop (the graph floor's index): its own
    counts become WORDS in the note, and the job's `done`/`total` stay this loop's files — never
    50,000 index files "done" of 2 (K5)."""
    outer = current_reporter()

    def sink(done: Any = None, total: Any = None, unit: Any = None, note: Any = None,
             **_kw: Any) -> None:
        if outer is None:
            return
        words = str(note or "working")
        if done is not None:
            words += f" — {int(done):,} {unit or 'items'}"
            if total:
                words += f" of {int(total):,}"
        outer(done=at, total=of, unit="files", note=words)
    return sink


def _changed_code(root: str, files: List[Dict[str, Any]], b: _Build, layer_root: str,
                  head_only: str = "", head: str = "") -> Dict[str, Any]:
    """Every changed definition, with its blast radius and the tests reaching it (one `nav.impact`
    per changed non-test file — bounded by the change set, past the floor's one-time repo index:
    the module docstring). Changed TEST files contribute their
    test definitions as located, changed tests. Returns the graph facts (basis, backend, cut).

    `head_only` (a reason) — the range's head is not what the working tree holds: definitions are
    read from `git show head:path`, NO blast radius is walked (the graph answers for the tree, not
    for `head`) and every coverage is `unknown`, said per node and in the graph note."""
    bases: Dict[str, int] = {}
    backends: Set[str] = set()
    notes: List[str] = []
    crossed: Set[str] = set()
    capped = False
    pending_total = 0
    walk_defs: Dict[str, Optional[Dict[str, Any]]] = {}
    n = len(files)
    indexed = False
    if head_only:
        notes.append(f"judged at {head}: {head_only} — coverage and blast radius are not "
                     f"determined from a tree that is not {head}; commit or stash, or trace the "
                     f"working tree")
    for i, f in enumerate(files):
        checkpoint()
        report(done=i, total=n, unit="files", note=f["path"])
        path = f["path"]
        if f["status"] == "deleted" or f["binary"] and not f["too_large"]:
            if f["status"] != "deleted":
                b.code[_code_id(path, "(binary)")] = _code_row(path, "(binary)", "file", 0, f,
                                                               COV_UNKNOWN, note="binary file")
            continue
        if head_only:
            text = _head_text(root, head, path)
            ol = nav.outline(path, text, None) if text is not None else {"symbols": []}
            ranges = nav.parse_lines(f["lines"]) if f["lines"] else None
            if f["is_test"]:
                _walk_test_file(b, path, text, ol, ranges)
                continue
            for sym in ol.get("symbols") or ():
                end = sym.get("end") or sym["line"]
                if ranges is not None and not any(sym["line"] <= hi and end >= lo
                                                  for lo, hi in ranges):
                    continue
                b.code[_code_id(path, sym["name"])] = _code_row(
                    path, sym["name"], sym.get("kind") or "def", sym["line"], f, COV_UNKNOWN,
                    note=f"not determined: {head_only}")
            if not (ol.get("symbols") or ()) and not f["is_test"]:
                b.code[_code_id(path, "(file)")] = _code_row(path, "(file)", "file", 0, f,
                                                             COV_UNKNOWN,
                                                             note=f"not determined: {head_only}")
            continue
        if f["is_test"]:
            text = D._read_text(layer_root, path)
            _walk_test_file(b, path, text, nav.outline(path, text, layer_root),
                            nav.parse_lines(f["lines"]) if f["lines"] else None)
            continue
        if not indexed:
            # the first impact question builds the graph floor's repo index (once per process):
            # say so, so a cold trace on a big repo is a visible step, never a silent stall
            report(note="indexing the repository for the blast radius (first trace only)")
            indexed = True
        try:
            with reporting(_inner_counts(i, n)):
                imp = nav.impact(layer_root, path, f["lines"] or "1-100000000")
        except nav.PathRefused as exc:
            b.code[_code_id(path, "(refused)")] = _code_row(path, "(refused)", "file", 0, f,
                                                            COV_UNKNOWN, note=str(exc))
            continue
        if imp.get("status") == "no-symbols":
            b.code[_code_id(path, "(file)")] = _code_row(path, "(file)", "file", 0, f,
                                                         COV_UNKNOWN, note=imp.get("note") or
                                                         "no definition the graph can name")
            continue
        for p in imp.get("pending") or ():
            pending_total += 1
            b.code[_code_id(path, p["name"])] = _code_row(path, p["name"], p["kind"], p["line"],
                                                          f, COV_PENDING,
                                                          note="blast radius on request")
        for s in imp.get("symbols") or ():
            blast, callers = s.get("blast") or {}, s.get("callers") or {}
            basis = str(blast.get("basis") or "")
            bases[basis] = bases.get(basis, 0) + 1
            if blast.get("backend"):
                backends.add(str(blast["backend"]))
            cov = {"covered": COV_COVERED, "untested": COV_UNTESTED}.get(s.get("verdict"),
                                                                        COV_UNKNOWN)
            for ref in callers.get("references") or ():
                if isinstance(ref, dict) and ref.get("path"):
                    crossed.add(str(ref["path"]))
            if callers.get("references_capped"):
                capped = True
            cid = _code_id(path, s["name"])
            row = _code_row(path, s["name"], s.get("kind") or "def", s.get("line") or 0, f, cov,
                            fan_in=int(callers.get("references_total") or
                                       len(callers.get("references") or ())),
                            blast=int(blast.get("count") or 0), cut=bool(blast.get("truncated")),
                            full_available=bool(blast.get("full_available")), basis=basis,
                            note=str(blast.get("not_determined") or ""))
            b.code[cid] = row
            for ref in s.get("tests") or ():
                tpath, _, rest = str(ref).partition(":")
                crossed.add(tpath)
                line_s, _, sym = rest.partition(" ")
                sym = sym or f"line {line_s}"
                ln = int(line_s) if line_s.isdigit() else 0
                # the test's identity is its full id: qualify the reached function by its file's
                # AST (one parse per test file the walk reached — bounded by the walk's answer)
                if tpath.endswith(".py"):
                    if tpath not in walk_defs:
                        walk_defs[tpath] = py_test_defs(D._read_text(layer_root, tpath))
                    key = _qualify(walk_defs[tpath], ln, sym)
                else:
                    key = sym
                tid = _test_id(tpath, key)
                t = b.test(tid, label=tid[5:], path=tpath, line=ln, symbol=sym, located=True,
                           qualifier=key.rpartition(".")[0])
                if cid not in t["code"]:
                    t["code"].append(cid)
                row["tests"].append(tid)
                b.edge(tid, cid, EDGE_TEST_CODE,
                       "structural" if basis in ("structural", "verified_empty") else "lexical",
                       f"{tpath}:{line_s}")
        if imp.get("graph_note"):
            notes.append(str(imp["graph_note"]))
    report(done=n, total=n, unit="files", note="")
    from .visuals import basis_word
    return {"basis": basis_word(bases, list(bases)) if bases else "none",
            "backend": ", ".join(sorted(backends)) or "", "bases": bases,
            "pending": pending_total, "note": " | ".join(sorted(set(notes))),
            "crossed": crossed, "capped": capped}


def _walk_test_file(b: _Build, path: str, text: Optional[str], ol: Dict[str, Any],
                    ranges: Optional[List[Tuple[int, int]]]) -> None:
    """A changed test file's changed tests, as located walk nodes under their FULL ids: a Python
    file by its own AST (`TestA.test_x`, never collapsed to `test_x`), any other by its outline."""
    if path.endswith(".py"):
        defs = py_test_defs(text)
        rows = [(key, ln, end) for key, ln, end in (defs or {}).get("funcs") or ()]
    else:
        rows = [(str(x["name"]), int(x["line"]), int(x.get("end") or x["line"]))
                for x in ol.get("symbols") or ()]
    for key, ln, end in rows:
        name = key.rsplit(".", 1)[-1]
        if not name.startswith("test"):
            continue
        if ranges is not None and not any(ln <= hi and end >= lo for lo, hi in ranges):
            continue
        nid = _test_id(path, key)
        b.test(nid, label=nid[5:], path=path, line=ln, symbol=name, located=True, changed=True,
               qualifier=key.rpartition(".")[0])


def _code_row(path: str, name: str, kind: str, line: int, f: Dict[str, Any], cov: str, *,
              fan_in: int = 0, blast: int = 0, cut: bool = False, full_available: bool = False,
              basis: str = "", note: str = "") -> Dict[str, Any]:
    return {"id": _code_id(path, name), "label": f"{path}#{name}", "path": path, "symbol": name,
            "kind": kind, "line": int(line or 0), "changed": True, "status": f["status"],
            "added": f["added"], "removed": f["removed"], "coverage": cov, "fan_in": fan_in,
            "blast": blast, "blast_cut": cut, "full_available": full_available, "basis": basis,
            "tests": [], "acs": [], "approvals": [], "note": note, "risk": 0}


#: the files whose directory a pytest run takes as its rootdir
_ROOTDIR_FILES = ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


class _Locator:
    """Where a recorded test id's definition IS, in the tree being judged — asked only for the
    files the record's ids NAME (bounded by the record, cached per file): git's word on the file
    (tracked / untracked / ignored / absent) and the file's own AST. Never a repo walk."""

    def __init__(self, root: str, layer_root: str, head: str = "",
                 files: Sequence[Dict[str, Any]] = ()) -> None:
        self.root, self.layer_root, self.head = root, layer_root, head
        self._py: Dict[str, Optional[Dict[str, Any]]] = {}
        self._flat: Dict[str, Optional[Dict[str, int]]] = {}
        self._tracked: Dict[str, str] = {}
        self._files = list(files)
        self._removed: Optional[Set[Tuple[str, str]]] = None
        self._gone: Optional[Set[str]] = None
        self._ic: Optional[bool] = None
        self._canon: Dict[str, str] = {}
        self._roots: Optional[List[str]] = None
        self._where: Dict[str, Tuple[str, List[str]]] = {}
        self._targets: Dict[str, str] = {}
        self._links: Dict[str, str] = {}

    # -- ONE path per file (J1, J5): case-folded where the filesystem folds case, and the pytest
    #    rootdir a rootdir-relative id was written against ----------------------------------------
    def ignorecase(self) -> bool:
        """Whether this filesystem folds case — PROBED (`test_ids.fs_folds_case`: the repo's
        `.git` stat-ed under a flipped case), never read from git's `core.ignorecase`, which a
        repo can set either way (K3). `Tests/x.py` IS `tests/x.py` there, and only there."""
        if self._ic is None:
            self._ic = fs_folds_case(self.layer_root) or fs_folds_case(self.root)
        return self._ic

    def _ls(self, *specs: str) -> List[str]:
        code, out = D._git(self.root, "ls-files", "-z", "--", *specs)
        return [x for x in out.split("\0") if x] if code == 0 else []

    def canon(self, rel: str) -> str:
        """The ONE spelling of the file `rel` names: case-folded where the filesystem folds case
        (`_case`), then every symlink resolved (`_real`: `tests/alias.py -> test_w.py` and
        `tlink -> tests` are `tests/test_w.py` — L1). Cached per path: bounded by the ids."""
        if rel in self._canon:
            return self._canon[rel]
        # at `head` a link is NEVER resolved — not on disk (which is not `head`), not in the tree
        # (0.0.28 E2, refute M6): a path with a link component is `unlocated` (`link_at_head`)
        self._canon[rel] = self._case(rel) if self.head else self._real(self._case(rel))
        return self._canon[rel]

    # -- 0.0.28 E2: the target a recorded outcome is bound to ------------------------------------
    def target(self, rel: str) -> str:
        """The file `rel` (a path as typed) opens NOW, as `test_ids.stamp_target` says — the SAME
        function the writer ran. At `head`: a path with a link component is `UNRESOLVED`; any other
        path is itself, spelled as `head` holds it. Cached per path (bounded by the ids)."""
        if rel not in self._targets:
            checkpoint()
            if not self.head:
                self._targets[rel] = stamp_target(self.layer_root, rel)
            else:
                norm = normalise_path(rel)
                if norm.startswith("/") or ".." in norm.split("/") or ".." in rel.split("/"):
                    self._targets[rel] = UNRESOLVED
                else:
                    c = self._case(norm)
                    self._targets[rel] = UNRESOLVED if self.link_at_head(c) else c
        return self._targets[rel]

    def link_at_head(self, rel: str) -> str:
        """The first component of `rel` that is a SYMLINK (mode 120000) in `head`'s tree, "" when
        none is. One `git ls-tree` over the path's prefixes; cached per path."""
        if not self.head or not rel:
            return ""
        if rel not in self._links:
            parts = rel.split("/")
            prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            code, out = D._git(self.root, "ls-tree", "-z", "--end-of-options", self.head,
                               "--", *prefixes)
            links = set()
            for entry in (out.split("\0") if code == 0 else ()):
                meta, _tab, name = entry.partition("\t")
                if meta.startswith("120000 "):
                    links.add(name)
            self._links[rel] = next((p for p in prefixes if p in links), "")
        return self._links[rel]

    def _real(self, rel: str) -> str:
        """`rel` with every symlink on it resolved, as a repo-relative name — when the real file
        is inside the repo. A link that leaves the repo keeps its spelling (and locates nothing a
        test can be proven by: its text is not the repo's)."""
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            return rel
        base = os.path.realpath(self.layer_root)
        real = os.path.realpath(os.path.join(base, *rel.split("/")))
        if os.path.normcase(real) == os.path.normcase(os.path.join(base, *rel.split("/"))):
            return rel
        try:
            inside = os.path.commonpath([os.path.normcase(real), os.path.normcase(base)]) == \
                os.path.normcase(base) and os.path.normcase(real) != os.path.normcase(base)
        except ValueError:                       # another drive (Windows): not inside
            inside = False
        return self._case(name_of(real, base)) if inside else rel

    def _case(self, rel: str) -> str:
        """The spelling the tree HOLDS on a case-folding filesystem — the tracked (or, live,
        on-disk) name whose case differs only; `rel` itself otherwise."""
        if not rel or not self.ignorecase() or rel.startswith("/") or ".." in rel.split("/"):
            return rel
        checkpoint()
        hit = ""
        if self.head:
            hit = self._canon_at_head(rel)
        else:
            names = self._ls(":(icase,literal)" + rel)
            hit = rel if rel in names else (names[0] if len(names) == 1 else "")
            if not hit:
                hit = self._canon_on_disk(rel)
        return hit or rel

    @staticmethod
    def _fold(names: Iterable[str], comp: str) -> str:
        names = list(names)
        if comp in names:
            return comp
        m = [n for n in names if n.casefold() == comp.casefold()]
        return m[0] if len(m) == 1 else ""

    def _canon_on_disk(self, rel: str) -> str:
        cur, parts = self.layer_root, []
        for comp in rel.split("/"):
            try:
                hit = self._fold(os.listdir(cur), comp)
            except OSError:
                return ""
            if not hit:
                return ""
            parts.append(hit)
            cur = os.path.join(cur, hit)
        return "/".join(parts)

    def _canon_at_head(self, rel: str) -> str:
        parts: List[str] = []
        for comp in rel.split("/"):
            code, out = D._git(self.root, "ls-tree", "-z", "--name-only",
                               f"{self.head}:{'/'.join(parts)}")
            hit = self._fold((x for x in out.split("\0") if x), comp) if code == 0 else ""
            if not hit:
                return ""
            parts.append(hit)
        return "/".join(parts)

    def rootdirs(self) -> List[str]:
        """The directories a pytest run can take as its ROOTDIR besides the repo root: every
        tracked directory holding a pytest.ini / pyproject.toml / tox.ini / setup.cfg — one
        pathspec query of git's index, not a walk of the filesystem."""
        if self._roots is None:
            found = self._ls(*(f":(glob)**/{n}" for n in _ROOTDIR_FILES))
            self._roots = sorted({f.rpartition("/")[0] for f in found} - {""})
        return self._roots

    def _held(self, rel: str) -> bool:
        if self.head and self.link_at_head(rel):
            return False                 # a committed link is never a candidate at head (E2)
        return self.exists(rel) or self.tracked(rel) == "tracked"

    def locate(self, rel: str) -> Tuple[str, List[str]]:
        """`(path, candidates)` for the file a test id names: the repo-relative file, and every
        `<rootdir>/<rel>` that exists too (a pytest node id is relative to ITS rootdir — a
        monorepo's `backend/` — never silently a different root-level file). No candidate at the
        root or under a rootdir: the ONE tracked file ending in `/<rel>`, when there is one.
        More than one candidate: the caller reports the id `ambiguous` — the rootdir is unknown."""
        if not rel:
            return rel, []
        if rel in self._where:
            return self._where[rel]
        p = self.canon(rel)
        cands = [p] if self._held(p) else []
        if not rel.startswith("/") and ".." not in rel.split("/"):
            for r in self.rootdirs():
                q = self.canon(f"{r}/{rel}")
                if q != p and self._held(q):
                    cands.append(q)
            # K2: a node id is relative to a rootdir pytest chose — with no config file that is
            # the directory it was RUN from, which no record says. Every tracked file ending in
            # `/<rel>` is therefore a candidate too: one beside the root file makes it ambiguous
            if not any(c in rel for c in "*?[]"):
                tails = self._ls(f":(glob)**/{rel}") if not self.ignorecase() else \
                    self._ls(f":(icase,glob)**/{rel}")
                tails = [self.canon(t) for t in tails]          # a symlinked twin is ONE file
                cands += [t for t in tails if t not in cands and self._held(t)]
        cands = sorted(set(cands))
        self._where[rel] = (cands[0] if len(cands) == 1 else p, cands)
        return self._where[rel]

    def label(self, test_id: str) -> str:
        """A recorded id's IDENTITY in the tree being judged: its full id with the file named by
        `locate` — every alias of one file (`./`, `//`, `..`, `\\`, a case variant where case
        folds, a rootdir-relative spelling) is ONE label, so ONE node and ONE history."""
        path = test_path_of(test_id)
        return test_label_of(test_id, self.locate(path)[0] if path else None)

    def stamp(self, rel: str, key: str, typed: str = "", real: Optional[str] = None
              ) -> Optional[str]:
        """The test's content stamp as the judged tree holds it now (`test_ids.stamp_of`: the
        test, its scopes' non-test code, the conftest.py files on its path). 0.0.28 E2: PER
        RECORDED ID — the conftests of the path as that id `typed` it, united with those of its
        `real` target, exactly as the writer (`test_runs.stamps_of`) stamped it."""
        defs = self.py_defs(rel) if rel.endswith(".py") else None

        def read(r: str) -> Optional[str]:
            c = self.canon(r)
            return self.text(c) if self.exists(c) else None
        return stamp_of(typed or rel, self.text(rel), key, defs=defs, read=read,
                        real=None if real in TARGET_STATES else real)

    def text(self, rel: str) -> Optional[str]:
        """The file in the tree being judged (`head`'s blob for a range read at head)."""
        return _head_text(self.root, self.head, rel) if self.head else \
            D._read_text(self.layer_root, rel)

    # -- positive evidence of removal (coordinator ruling: "deleted" only when git shows it) ----
    def removed_files(self) -> Set[str]:
        """Test files git shows REMOVED: deleted in the working tree against HEAD, or deleted by
        the change set itself (a `deleted` file row)."""
        if self._gone is None:
            gone = {f["path"] for f in self._files if f.get("status") == "deleted"}
            if not self.head:
                gone |= {p for p, k in status_paths(self.root).items() if k == "deleted"}
            self._gone = gone
        return self._gone

    @staticmethod
    def _keys_of(rel: str, text: Optional[str]) -> Set[str]:
        if text is None:
            return set()
        if rel.endswith(".py"):
            return set(((py_test_defs(text) or {}).get("keys") or {}))
        return {str(x["name"]) for x in (nav.outline(rel, text, None).get("symbols") or ())}

    def removed_keys(self) -> Set[Tuple[str, str]]:
        """`(file, qualified key)` of every test definition git shows removed: each test key of a
        removed test file (its HEAD content), plus the keys the change set's hunks took out of a
        test file that still exists (its HEAD keys minus its keys now)."""
        if self._removed is None:
            out: Set[Tuple[str, str]] = set()
            for rel in self.removed_files():
                if nav._is_test(rel):
                    out |= {(rel, k) for k in self._keys_of(rel, _head_text(self.root, "HEAD", rel))}
            for f in self._files:
                rel = f["path"]
                if not f.get("is_test") or rel in self.removed_files():
                    continue
                before = _head_text(self.root, "HEAD", rel)
                now = self.text(rel)
                if before is None or now is None:
                    continue
                out |= {(rel, k) for k in self._keys_of(rel, before) - self._keys_of(rel, now)}
            self._removed = {(p, k) for p, k in out if k.rsplit(".", 1)[-1].startswith("test")}
        return self._removed

    def removed_names(self) -> Set[str]:
        return {k.rsplit(".", 1)[-1] for _p, k in self.removed_keys()}

    def gone(self, rel: str, key: str) -> bool:
        """Positive evidence that `rel::key` was removed."""
        return rel in self.removed_files() or (rel, key) in self.removed_keys()

    def exists(self, rel: str) -> bool:
        if self.head:
            return _head_text(self.root, self.head, rel) is not None
        try:
            return os.path.isfile(os.path.join(self.layer_root, *rel.split("/")))
        except OSError:
            return False

    def tracked(self, rel: str) -> str:
        """`tracked` | `untracked` | `ignored` | `absent` — what GIT says about `rel` in the tree
        being judged (at `head`: in that commit or not). Positive evidence, cached per path."""
        if rel in self._tracked:
            return self._tracked[rel]
        if self.head:
            st = "tracked" if _head_text(self.root, self.head, rel) is not None else "absent"
        else:
            code, _o = D._git(self.root, "ls-files", "--error-unmatch", "--", rel)
            if code == 0:
                st = "tracked"
            elif not self.exists(rel):
                st = "absent"
            else:
                code, _o = D._git(self.root, "check-ignore", "-q", "--", rel)
                st = "ignored" if code == 0 else "untracked"
        self._tracked[rel] = st
        return st

    def py_defs(self, rel: str) -> Optional[Dict[str, Any]]:
        """The Python file's AST bindings (`py_test_defs`), parsed fresh from the judged text."""
        if rel not in self._py:
            checkpoint()
            self._py[rel] = py_test_defs(self.text(rel))
        return self._py[rel]

    def flat_names(self, rel: str) -> Optional[Dict[str, int]]:
        """A non-Python file's definitions `{name: count}` from a STRUCTURAL outline of the judged
        text itself (never a graph index, which can be stale); None when there is none."""
        if rel not in self._flat:
            text = self.text(rel)
            out: Optional[Dict[str, int]] = None
            if text is not None:
                ol = nav.outline(rel, text, None)
                if ol.get("basis") == "structural" and not ol.get("note"):
                    out = {}
                    for x in ol.get("symbols") or ():
                        out[str(x["name"])] = out.get(str(x["name"]), 0) + 1
            self._flat[rel] = out
        return self._flat[rel]


def _attach_record(b: _Build, acs: List[Dict[str, Any]], record: Dict[str, Any],
                   red: Set[str], green: Set[str], history: Dict[str, Dict[str, Any]],
                   loc: _Locator) -> None:
    """AC → test edges from the record (recorded runs first, the planned map second). Every
    recorded id goes through `resolve_test` (THE RULE) to ONE node — its full id — and a state.

    A test's verdict reads EVERY recorded spelling of its full id (`tests\\t.py::A::x` is
    `tests/t.py::A::x`) and nothing else: never a bare name's history, never another class's
    (verification F2, H1)."""
    from ..test_runs import mapped_tests
    located = {nid: t for nid, t in b.tests.items() if t["located"]}
    for ac in acs:
        aid = _ac_id(ac["id"])
        row = b.acs[aid] = {"id": aid, "ac": ac["id"], "text": ac["text"], "kind": ac["kind"],
                            "tests": [], "code": [], "anchors": ac["anchors"],
                            "status": AC_UNTESTED, "risk": 0, "proven_by": [], "note": ""}
        recorded = set(record["ac_tests"].get(ac["id"], []))
        for test_id in mapped_tests(record, ac["id"]):
            nid = _resolve(b, loc, test_id, located)
            t = b.tests[nid]
            if test_id not in t["recorded_ids"]:
                t["recorded_ids"].append(test_id)
            if aid not in t["acs"]:
                t["acs"].append(aid)
            if nid not in row["tests"]:
                row["tests"].append(nid)
            b.edge(aid, nid, EDGE_AC_TEST, "recorded" if test_id in recorded else "planned")
        for a in ac["anchors"]:
            if not a.get("symbol"):
                continue
            cid = _code_id(a["file"], a["symbol"])
            if cid in b.code:
                row["code"].append(cid)
                b.code[cid]["acs"].append(aid)
                b.edge(aid, cid, EDGE_AC_CODE, "anchor", a["file"])
    enforce_rule_on_located(b, loc)
    spellings: Dict[str, Set[str]] = {}
    loose: Dict[str, Set[str]] = {}
    # a spelling the writer QUALIFIED is kept in the phase sets for the TDD gate's lookups only —
    # its outcomes live under the full id it was rewritten to, never twice
    typed = {x for xs in (record.get("qualified") or {}).values() for x in xs}
    for hid in set(history) | ((red | green) - typed):
        if not isinstance(hid, str):
            continue
        if test_path_of(hid):
            spellings.setdefault(loc.label(hid), set()).add(hid)
        elif hid.strip():
            loose.setdefault(test_name_of(hid), set()).add(hid)
    qualified: Dict[str, Set[str]] = {}
    for full_id, typed in (record.get("qualified") or {}).items():
        qualified.setdefault(loc.label(full_id), set()).update(typed)
    for nid, t in b.tests.items():
        t["qualified_from"] = sorted(qualified.get(t["label"], ()))
        key = f"{t['qualifier']}.{t['symbol']}" if t.get("qualifier") else t["symbol"]
        bound: Dict[str, Tuple[Optional[str], str]] = {}
        for i in set(t["recorded_ids"]) | (spellings.get(t["label"], set()) if t["path"] else set()):
            typed = raw_path_of(i)
            if not typed:
                continue
            tgt = loc.target(typed)
            st = loc.stamp(t["path"], key, test_path_of(i), tgt) \
                if t["path"] and t["state"] == STATE_PRESENT else None
            bound[i] = (st, tgt)
        t.update(node_verdict(t, red, green, history, spellings, bound=bound,
                              loose=loose.get(t["symbol"], set()),
                              same=lambda a, b: same_target(loc.layer_root, a, b)))
        if t["state"] in NON_PROVING:
            t["tdd"] = t["state"]                    # what the trace knows about it, not a proof
        for cid in t["code"]:
            if nid not in b.code[cid]["tests"]:
                b.code[cid]["tests"].append(nid)
        for aid in t["acs"]:
            for cid in t["code"]:
                if cid not in b.acs[aid]["code"]:
                    b.acs[aid]["code"].append(cid)
                if aid not in b.code[cid]["acs"]:
                    b.code[cid]["acs"].append(aid)
    for row in b.acs.values():
        row["status"] = _ac_status([b.tests[n] for n in row["tests"]])
        # the tests that COUNT: present, and proven — what a `proven` status rests on, by name
        row["proven_by"] = sorted(n for n in row["tests"]
                                  if b.tests[n]["state"] == STATE_PRESENT
                                  and b.tests[n]["tdd"] == TDD_PROVEN) \
            if row["status"] == AC_PROVEN else []
        bare = sorted(b.tests[n]["label"] for n in row["tests"]
                      if b.tests[n]["reason"].startswith(BARE_REASON))
        if bare and row["status"] != AC_PROVEN:
            row["note"] = f"{BARE_REASON} ({', '.join(bare)})"
        row["risk"] = {AC_UNTESTED: 3, AC_RED: 3, AC_AMBIGUOUS: 3, AC_UNRUN: 2,
                       AC_GREEN_NOT_TDD: 1, AC_PROVEN: 0}[row["status"]]
        row["tests"].sort()
        row["code"].sort()


_REMOVED = "git shows the test's file or definition removed"
#: what a bare id says, on its test and on its AC (K1)
BARE_REASON = "bare id: run with full ids to prove"


def resolve_test(loc: _Locator, test_id: str, located: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """THE RULE, in one place (the module docstring). `{state, path, key, line, candidates,
    reason, …}` — `present` ONLY for a full id `path::[Class::…]name` whose file git tracks in the
    judged tree (not ignored; at `head` for a range) and whose AST binds exactly that key once:

      * a BARE id (no file: `test_x`, a dotted unittest id) never proves — `unlocated`, with the
        full ids the change set holds for that name as the hint (`deleted` when git shows a
        definition of that name removed);
      * a class-qualified id whose class does not hold the name — `unlocated`, never the file's
        other `test_x` (H2); `Class.name` bound twice — `ambiguous`;
      * a qualifier-less `path::name` whose file binds the name more than once ANYWHERE (two
        classes, a module function and a method, a rebinding) — `ambiguous` (H1); bound once
        but only inside a class — `unlocated` (a partial id; record `path::Class::name`);
      * a scope with `from x import *` can rebind anything — `ambiguous`;
      * a name that is not `test*` is not a test (`src/app.py::parse`) — `unlocated`;
      * a non-Python file: located only by a structural outline of its own text, by name, and
        only for a qualifier-less id; a qualified id there is `unlocated` (nesting unknown)."""
    name, param = test_name_of(test_id), test_param_of(test_id)
    qual, key, path = test_qualifier_of(test_id), test_key_of(test_id), test_path_of(test_id)
    out: Dict[str, Any] = {"name": name, "param": param, "qualifier": qual, "key": key,
                           "path": path, "line": 0, "candidates": [], "reason": ""}
    if not path:
        hint = sorted({t["label"] for t in located.values() if t["symbol"] == name})
        if name in loc.removed_names():
            out.update(state=STATE_DELETED, reason=_REMOVED)
        else:
            out.update(state=STATE_UNLOCATED, candidates=hint,
                       reason=f"{BARE_REASON} — record the full id path::Class::test"
                              + (f" (here: {', '.join(hint)})" if hint else ""))
        return out
    if not name.startswith("test"):
        out.update(state=STATE_UNLOCATED, reason=f"{name} is not a test (a test runner collects "
                                                  "only test* functions and methods)")
        return out
    path, where = loc.locate(path)
    out["path"] = path
    link = loc.link_at_head(path)
    if link:
        out.update(state=STATE_UNLOCATED,
                   reason=f"{link} is a symlink at {loc.head}: a link in a commit is not followed "
                          "(judge the working tree, or commit the file itself)")
        return out
    if len(where) > 1:
        out.update(state=STATE_AMBIGUOUS,
                   candidates=[f"{w}::{key.replace('.', '::')}" for w in where],
                   reason=f"{test_path_of(test_id)} names {len(where)} files ({', '.join(where)}) "
                          "— the pytest rootdir it was written against is unknown; record the "
                          "repo-relative full id")
        return out
    st = loc.tracked(path)
    if st == "absent":
        gone = loc.gone(path, key)
        out.update(state=STATE_DELETED if gone else STATE_UNLOCATED,
                   reason=_REMOVED if gone else "no such file in the tree being judged")
        return out
    if st == "ignored" or (st == "untracked" and loc.head):
        out.update(state=STATE_IGNORED, reason="its file is gitignored / not tracked in the tree "
                                                "being judged — only tracked files count")
        return out
    if not path.endswith(".py"):
        return _resolve_flat(loc, out)
    defs = loc.py_defs(path)
    if defs is None:
        out.update(state=STATE_UNPARSED, reason="its file does not parse — no outline; a file's "
                                                 "existence is not a test's location")
        return out
    keys: Dict[str, List[int]] = defs["keys"]
    scope = key.rpartition(".")[0] + "." if qual else ""
    if scope in defs["stars"]:
        out.update(state=STATE_AMBIGUOUS, candidates=[f"{path}::{key.replace('.', '::')}"],
                   reason="a `from … import *` in its scope can rebind the name")
        return out
    full = lambda k: f"{path}::{k.replace('.', '::')}"   # noqa: E731
    why_not = defs["uncollected"].get(key if qual else name)
    if qual:
        lines = keys.get(key, [])
        if len(lines) == 1 and why_not:
            out.update(state=STATE_UNLOCATED, line=lines[0],
                       reason=f"{path}::{key.replace('.', '::')} is never collected: {why_not}")
        elif len(lines) == 1:
            out.update(state=STATE_PRESENT, line=lines[0])
        elif len(lines) > 1:
            out.update(state=STATE_AMBIGUOUS, candidates=[full(key)] * len(lines),
                       reason=f"{path} binds {key.replace('.', '::')} {len(lines)} times")
        elif loc.gone(path, key):
            out.update(state=STATE_DELETED, reason=_REMOVED)
        else:
            others = sorted(k for k in keys if k.rsplit(".", 1)[-1] == name)
            out.update(state=STATE_UNLOCATED, candidates=[full(k) for k in others],
                       reason=f"{path} has no {key.replace('.', '::')}"
                              + (f" (it has {', '.join(full(k) for k in others)} — a different "
                                 f"test)" if others else ""))
        return out
    same = sorted(k for k in keys if k.rsplit(".", 1)[-1] == name)
    total = sum(len(keys[k]) for k in same)
    if total == 1 and same == [name] and why_not:
        out.update(state=STATE_UNLOCATED, line=keys[name][0],
                   reason=f"{path}::{name} is never collected: {why_not}")
    elif total == 1 and same == [name]:
        out.update(state=STATE_PRESENT, line=keys[name][0])
    elif total > 1:
        out.update(state=STATE_AMBIGUOUS, candidates=[full(k) for k in same for _ in keys[k]],
                   reason=f"{path} binds {name} {total} times ({', '.join(full(k) for k in same)})"
                          " — record the full id path::Class::test")
    elif total == 1:
        out.update(state=STATE_UNLOCATED, candidates=[full(same[0])],
                   reason=f"a partial id — {path} has {name} only as {full(same[0])}; record "
                          "that full id")
    elif loc.gone(path, key):
        out.update(state=STATE_DELETED, reason=_REMOVED)
    else:
        out.update(state=STATE_UNLOCATED, reason=f"{path} has no {name} and git shows no removal")
    return out


def _resolve_flat(loc: _Locator, out: Dict[str, Any]) -> Dict[str, Any]:
    """A non-Python test file: by name, on a structural outline of its own text only."""
    path, name, key = out["path"], out["name"], out["key"]
    names = loc.flat_names(path)
    if names is None:
        out.update(state=STATE_UNPARSED, reason="its file has no outline mokata can read — a "
                                                 "file's existence is not a test's location")
    elif out["qualifier"]:
        out.update(state=STATE_UNLOCATED, reason="a class-qualified id is located only in a Python "
                                                  "file (this outline does not carry nesting)")
    elif names.get(name, 0) == 1:
        out.update(state=STATE_PRESENT)
    elif names.get(name, 0) > 1:
        out.update(state=STATE_AMBIGUOUS, candidates=[f"{path}::{name}"] * names[name],
                   reason=f"{path} defines {name} {names[name]} times")
    elif loc.gone(path, key):
        out.update(state=STATE_DELETED, reason=_REMOVED)
    else:
        out.update(state=STATE_UNLOCATED, reason=f"{path} has no {name} and git shows no removal")
    return out


def _resolve(b: _Build, loc: _Locator, test_id: str,
             located: Dict[str, Dict[str, Any]]) -> str:
    """The node id a recorded test id stands for (creating the node) — through `resolve_test`.
    The node IS the full id (H1: `A::test_x` and `B::test_x` are two nodes, two histories)."""
    r = resolve_test(loc, test_id, located)
    label = loc.label(test_id)
    nid = "test:" + label
    t = b.test(nid, label=label, path=r["path"], line=r["line"], symbol=r["name"],
               qualifier=r["qualifier"])
    t["resolved"] = True
    t["state"], t["reason"] = r["state"], r["reason"]
    t["located"] = r["state"] == STATE_PRESENT
    if r["state"] == STATE_PRESENT:
        located[nid] = t
    if r["state"] == STATE_AMBIGUOUS:
        t["ambiguous"] = sorted({"test:" + c for c in r["candidates"]})
    return nid


def enforce_rule_on_located(b: _Build, loc: _Locator) -> None:
    """The tests the WALK located (a changed test file, a test the blast radius reached) go
    through the SAME rule, by their own full id — an ignored / unparsed / ambiguous one never
    counts (G3, H1) — and a code row whose only reaching tests are non-proving loses `covered`."""
    for t in list(b.tests.values()):
        if t.pop("resolved", False) or not t["located"] or t["state"] != STATE_PRESENT:
            continue
        r = resolve_test(loc, t["label"], {})
        if r["state"] != STATE_PRESENT:
            t["located"], t["state"], t["reason"] = False, r["state"], r["reason"]
            if r["state"] == STATE_AMBIGUOUS:
                t["ambiguous"] = sorted({"test:" + c for c in r["candidates"]})
    for row in b.code.values():
        if not row["tests"]:
            continue
        usable = [n for n in row["tests"] if b.tests[n]["state"] == STATE_PRESENT]
        if not usable and row["coverage"] == COV_COVERED:
            states = sorted({b.tests[n]["state"] for n in row["tests"]})
            row["coverage"] = COV_UNKNOWN
            row["note"] = f"the only test(s) reaching it are {', '.join(states)} — not coverage"


def node_verdict(t: Dict[str, Any], red: Set[str], green: Set[str],
                 history: Dict[str, Dict[str, Any]],
                 spellings: Optional[Dict[str, Set[str]]] = None, *,
                 bound: Optional[Dict[str, Tuple[Optional[str], str]]] = None,
                 loose: Iterable[str] = (),
                 same: Optional[Any] = None) -> Dict[str, Any]:
    """ONE test's TDD verdict. Its ids: every recorded id mapped to it plus every recorded
    spelling of its full id (`spellings`, by label) — never a bare name or another qualifier.
    EACH id is judged on its own history (`tdd_verdict`), and the test is proven only when EVERY
    id is (each param, each spelling: its own red→green, latest green); red when any id's latest
    is red; green-only otherwise. Proven also needs the test's content stamp NOW to be the one
    each id ran under (J4), and no later RED under a `loose` spelling that could name it (J2).

    0.0.28 E2 (`PROOF-SYMLINK-REPOINT`): `bound` is, per id, `(stamp now, target now)` — the target
    from `test_ids.stamp_target`, the function the writer ran; `same` compares two targets
    (`test_ids.same_target`). An id whose latest recorded target is not this file NOW is evidence
    about ANOTHER file: it does not vote, and the re-point is said, naming both targets. Proven
    then also needs, for every voting id, its red, its green and its stamp to be of ONE real file
    inside the repository."""
    bound = bound or {}
    same = same or (lambda a, b: bool(a) and a == b and a not in TARGET_STATES)
    ids = set(t["recorded_ids"])
    if t["path"]:
        ids |= (spellings or {}).get(t["label"], set())
    why: List[str] = []
    away = sorted(i for i in ids if _repointed(history.get(i) or {}, bound.get(i), same))
    for i in away:
        h = history[i]
        why.append(f"{test_path_of(i)} was {h['target']}, now {bound[i][1]} — the record is of "
                   "another file; re-run it red, then green")
    voters = (ids - set(away)) or ids
    verdicts = [tdd_verdict(i, red, green, history) for i in sorted(voters)]
    ran = [v for v in verdicts if v["tdd"] != TDD_UNRECORDED]
    if not ran:
        out = tdd_verdict("", set(), set(), {})
    else:
        out = dict(max(ran, key=lambda v: (v.get("last_at") or 0.0)))
        kinds = [v["tdd"] for v in verdicts]
        out["tdd"] = (TDD_RED if TDD_RED in kinds else
                      TDD_PROVEN if all(k == TDD_PROVEN for k in kinds) else TDD_GREEN_ONLY)
        out["red_at"] = min((v["red_at"] for v in ran if v["red_at"] is not None), default=None)
        out["green_at"] = max((v["green_at"] for v in ran if v["green_at"] is not None),
                              default=None)
        out["basis"] = "timed" if all(v["basis"] == "timed" for v in ran) else "sets"
        if out["tdd"] == TDD_PROVEN and voters is ids and away:
            out["tdd"] = TDD_GREEN_ONLY          # every id is of another file: nothing is left
        if out["tdd"] == TDD_PROVEN:
            gone = _target_trouble(sorted(voters), history, bound, same)
            if gone:
                out["tdd"] = TDD_GREEN_ONLY
                why.extend(gone)
        if out["tdd"] == TDD_PROVEN:
            # J4: a proof is of the test AS IT WAS when it ran green — a `@skip`, an `assert 0`,
            # a `__test__ = False` added since changes its stamp, and the proof does not survive
            # (J9: an id with no timed history at all has no order and no stamp — not proven)
            unstamped = sorted(i for i in voters if not (history.get(i) or {}).get("stamp"))
            stale = sorted(i for i in voters if (history.get(i) or {}).get("stamp")
                           and history[i]["stamp"] != (bound.get(i) or (None, ""))[0])
            if unstamped:
                out["tdd"] = TDD_GREEN_ONLY
                why.append("no timed, content-stamped record of " + ", ".join(unstamped)
                           + " as it ran — re-run it")
            elif stale:
                out["tdd"] = TDD_GREEN_ONLY
                why.append("the test changed since it ran green (" + ", ".join(stale) + ") — "
                           "re-run it")
        # J2: a RED recorded under a spelling that names no file (a bare `test_x`, a dotted
        # unittest id) that COULD be this test, later than its green, blocks the proof
        last_green = max((v.get("last_at") or 0.0 for v in ran if v.get("last") == "green"),
                         default=0.0)
        late = sorted(i for i in loose if could_name(i, t["path"], t.get("qualifier") or "",
                                                     t["symbol"])
                      and (history.get(i) or {}).get("last") == "red"
                      and ((history.get(i) or {}).get("last_at") or 0.0) >= last_green)
        if late and out["tdd"] != TDD_RED:
            out["tdd"] = TDD_RED
            why.append("a later RED was recorded under " + ", ".join(late) + ", which can name "
                       "this test — record full ids (path::Class::test)")
    out["params"] = sorted({test_param_of(i) for i in ids} - {""})
    out["tdd_note"] = "; ".join(why)
    return out


def _repointed(h: Dict[str, Any], now: Optional[Tuple[Optional[str], str]], same: Any) -> bool:
    """The id's latest outcome ran against a real file in the repo, and the id opens a DIFFERENT
    real file in the repo now (a re-pointed link, a file swapped for a link or back)."""
    was = h.get("target")
    if not now or not isinstance(was, str) or was in TARGET_STATES or now[1] in TARGET_STATES:
        return False
    return not same(was, now[1])


def _target_trouble(ids: List[str], history: Dict[str, Dict[str, Any]],
                    bound: Dict[str, Tuple[Optional[str], str]], same: Any) -> List[str]:
    """Why the voting ids' outcomes are NOT of one real file in the repo — [] when they are. In
    order: an id that leaves the repo or cannot be resolved (recorded or now); a record with no
    target (before 0.0.28, or damaged); a red and a green that ran in different files."""
    out: List[str] = []
    for i in ids:
        h = history.get(i) or {}
        path = test_path_of(i)
        now = (bound.get(i) or (None, UNRESOLVED))[1]
        seen = [h.get(k) for k in ("red_target", "green_target", "target")] + [now]
        if OUTSIDE in seen:
            out.append(f"{path} resolves outside the repository — a file outside it never proves")
        elif UNRESOLVED in seen:
            out.append(f"{path} cannot be resolved to a file in the repository (a dangling or "
                       "looping link, or a `..` through a link) — it never proves")
        elif not all(isinstance(x, str) and x for x in seen[:3]):
            out.append(f"no timed, target-bound record of {i} as it ran (recorded before mokata "
                       "0.0.28, or damaged) — re-run it red, then green")
        elif not same(h["red_target"], h["green_target"]):
            out.append(f"red and green ran in different files ({path}: red in {h['red_target']}, "
                       f"green in {h['green_target']}) — re-run it red, then green")
    return out


def could_name(spelling: str, path: str, qualifier: str, name: str) -> bool:
    """Whether a recorded id that names no file — `test_x`, or a dotted `pkg.test_mod.TestA.test_x`
    — could be the test `path::qualifier::name`: the same name, its class segments (when it has
    any after the module) the test's, its module segments a suffix of the test's file. Generous
    on purpose (J2): a spelling that MIGHT be this test is never ignored."""
    segs = [x for x in re.split(r"[.:]+", _PARAM.sub("", spelling.strip())) if x]
    if not segs or segs[-1] != name:
        return False
    pre = segs[:-1]
    if not pre:
        return True
    quals = [q for q in qualifier.split(".") if q]
    mod_segs = [x for x in re.sub(r"\.py$", "", path).split("/") if x]
    for cut in range(len(pre) + 1):
        mod, cls = pre[:cut], pre[cut:]
        if cls == quals and (not mod or mod_segs[-len(mod):] == mod):
            return True
    return False


_TDD_ORDER = {TDD_PROVEN: 0, TDD_RED: 1, TDD_GREEN_ONLY: 2, TDD_UNRECORDED: 3,
              STATE_DELETED: 3, STATE_AMBIGUOUS: 3, STATE_UNLOCATED: 3, STATE_UNPARSED: 3,
              STATE_IGNORED: 3}


def _ac_status(tests: List[Dict[str, Any]]) -> str:
    """An AC's status from the tests that can PROVE anything: an ambiguous, deleted or unlocated
    test contributes nothing (it is listed, on the overlay, but never counts as proof)."""
    usable = [t for t in tests if t["state"] not in NON_PROVING]
    if not usable:
        if tests and all(t["state"] == STATE_AMBIGUOUS for t in tests):
            return AC_AMBIGUOUS
        return AC_UNTESTED
    kinds = {t["tdd"] for t in usable}
    if TDD_RED in kinds:
        return AC_RED
    if TDD_PROVEN in kinds:
        return AC_PROVEN
    if TDD_GREEN_ONLY in kinds:
        return AC_GREEN_NOT_TDD
    return AC_UNRUN


def _attach_approvals(b: _Build, approvals: List[Dict[str, Any]]) -> None:
    by_path: Dict[str, List[Dict[str, Any]]] = {}
    for a in approvals:
        tgt = str(as_name(a["target"]))       # a ledger row: the hook payload's path
        if tgt:
            by_path.setdefault(tgt, []).append(a)
    for row in b.code.values():
        p = row["path"]
        hits = [a for t, rows in by_path.items() if t == p or t.endswith("/" + p) or p.endswith("/" + t)
                for a in rows]
        row["approvals"] = sorted({(a["decision"], a["actor"], a["at"], a["proposal"])
                                   for a in hits})
        row["approvals"] = [{"decision": d, "actor": ac, "at": at, "proposal": pr}
                            for d, ac, at, pr in row["approvals"]]


def _risk(b: _Build, gates: List[Dict[str, Any]], graph: Dict[str, Any]) -> Dict[str, Any]:
    for row in b.code.values():
        row["risk"] = _risk_of(row)
    untested_acs = sorted(a["ac"] for a in b.acs.values() if a["status"] == AC_UNTESTED)
    red_acs = sorted(a["ac"] for a in b.acs.values() if a["status"] == AC_RED)
    not_tdd = sorted(t["id"] for t in b.tests.values() if t["tdd"] in (TDD_GREEN_ONLY,
                                                                        TDD_UNRECORDED))
    ambiguous = sorted(t["id"] for t in b.tests.values() if t["state"] == STATE_AMBIGUOUS)
    deleted = sorted(t["id"] for t in b.tests.values() if t["state"] == STATE_DELETED)
    non_proving = sorted(t["id"] for t in b.tests.values() if t["state"] in NON_PROVING)
    without = sorted(c["id"] for c in b.code.values() if c["coverage"] == COV_UNTESTED)
    unknown = sorted(c["id"] for c in b.code.values() if c["coverage"] in (COV_UNKNOWN,
                                                                            COV_PENDING))
    hubs = sorted((c for c in b.code.values() if c["risk"] > 0),
                  key=lambda c: (-c["risk"], c["id"]))
    return {"untested_acs": untested_acs, "red_acs": red_acs, "tests_not_tdd_proven": not_tdd,
            "tests_ambiguous": ambiguous, "tests_deleted": deleted,
            "tests_non_proving": non_proving,
            "code_without_test": without, "code_coverage_unknown": unknown,
            "gates_red": [g for g in gates if g["verdict"] == GATE_RED],
            "gates_skipped": [g for g in gates if g["verdict"] == GATE_SKIPPED],
            "gates_unknown": [g for g in gates if g["verdict"] == GATE_UNKNOWN],
            "blast": {"fan_in_total": sum(c["fan_in"] for c in b.code.values()),
                      "cut": sum(1 for c in b.code.values() if c["blast_cut"]),
                      "top": [{"id": c["id"], "fan_in": c["fan_in"], "risk": c["risk"],
                               "coverage": c["coverage"]} for c in hubs[:20]]},
            "graph_basis": graph["basis"],
            "score": (3 * len(untested_acs) + 3 * len(red_acs) + len(not_tdd) + len(deleted)
                      + sum(c["risk"] for c in b.code.values())
                      + 5 * sum(1 for g in gates if g["verdict"] in (GATE_RED, GATE_SKIPPED)))}


def _page(b: _Build, page: int, size: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Rows = every AC (riskiest first) then every code node no AC reaches (riskiest first); a
    page is `size` rows with the tests and code they connect to."""
    ac_rows = sorted(b.acs.values(), key=lambda a: (-a["risk"], a["ac"]))
    reached = {c for a in ac_rows for c in a["code"]}
    orphan = sorted((c for c in b.code.values() if c["id"] not in reached),
                    key=lambda c: (-c["risk"], c["id"]))
    reached_tests = {t for a in ac_rows for t in a["tests"]} | \
                    {t for c in b.code.values() for t in c["tests"]}
    lone_tests = sorted((t for t in b.tests.values() if t["id"] not in reached_tests),
                        key=lambda t: (_TDD_ORDER.get(t["tdd"], 3) == 0, t["id"]))
    rows: List[Tuple[str, str]] = [("ac", a["id"]) for a in ac_rows] + \
                                  [("code", c["id"]) for c in orphan] + \
                                  [("test", t["id"]) for t in lone_tests]
    total = len(rows)
    start = (page - 1) * size
    chunk = rows[start:start + size]
    acs = [b.acs[i] for k, i in chunk if k == "ac"]
    code_ids: Set[str] = {i for k, i in chunk if k == "code"}
    test_ids: Set[str] = {i for k, i in chunk if k == "test"}
    for a in acs:
        code_ids.update(a["code"])
        test_ids.update(a["tests"])
    for cid in list(code_ids):
        test_ids.update(b.code[cid]["tests"])
    for tid in list(test_ids):
        code_ids.update(b.tests[tid]["code"])
    ids = {a["id"] for a in acs} | test_ids | code_ids
    edges = sorted((e for e in b.edges.values() if e["source"] in ids and e["target"] in ids),
                   key=lambda e: (e["source"], e["target"], e["kind"]))
    nodes = {"acs": acs,
             "tests": sorted((b.tests[t] for t in test_ids),
                             key=lambda t: (_TDD_ORDER[t["tdd"]] != 3 and
                                            -_TDD_ORDER[t["tdd"]], t["id"])),
             "code": sorted((b.code[c] for c in code_ids), key=lambda c: (-c["risk"], c["id"]))}
    nxt = page + 1 if start + size < total else None
    return ({"nodes": nodes, "edges": edges},
            {"n": page, "size": size, "rows": len(chunk), "total_rows": total,
             "total_acs": len(b.acs), "total_tests": len(b.tests), "total_code": len(b.code),
             "next": nxt})


def _draw(nodes: Dict[str, List[Dict[str, Any]]], more: bool = False) -> Dict[str, Any]:
    """What the page DRAWS: everything when it fits `visuals.DRAW_LIMIT`, else the riskiest ACs,
    the tests and code they connect to, within the limit — and it SAYS so (`reduced`, `note`);
    the full page stays in `nodes` for the table."""
    from .visuals import DRAW_LIMIT
    total = sum(len(v) for v in nodes.values())
    if total <= DRAW_LIMIT:
        return {"limit": DRAW_LIMIT, "total": total, "reduced": False, "note": "",
                "shown": sorted(n["id"] for v in nodes.values() for n in v)}
    shown: List[str] = []
    seen: Set[str] = set()

    def take(nid: str) -> bool:
        if nid in seen:
            return True
        if len(shown) >= DRAW_LIMIT:
            return False
        seen.add(nid)
        shown.append(nid)
        return True

    tests = {t["id"]: t for t in nodes["tests"]}
    for a in nodes["acs"]:
        if not take(a["id"]):
            break
        for tid in a["tests"]:
            take(tid)
            for cid in tests.get(tid, {}).get("code", ()):
                take(cid)
        for cid in a["code"]:
            take(cid)
    for c in nodes["code"]:
        if not take(c["id"]):
            break
    return {"limit": DRAW_LIMIT, "total": total, "reduced": True, "shown": shown,
            "note": f"drawing {len(shown)} of {total} nodes on this page (the riskiest first) — "
                    f"the table below lists all {total}" + ("; the next page continues" if more
                                                           else "")}


def trace(root: str, *, run: Optional[str] = None, session: Optional[str] = None,
          base: str = "", head: str = "", live: bool = False, page: int = 1,
          size: int = PAGE_ROWS, code_root: Optional[str] = None) -> Dict[str, Any]:
    """THE trace of one change set. See the module docstring for the sources; the contract file
    for the shape. Raises `TraceRefused` on a target that cannot be asked for."""
    from .plans import check_run_id, local_run_ids
    page = max(1, int(page))
    size = max(1, min(MAX_PAGE_ROWS, int(size)))
    rid = None
    if run:
        rid = check_run_id(run)
        if rid not in set(local_run_ids(root)):
            raise TraceRefused(f"no run {rid} has state in this repository")
    if session and not rid:
        rid = _run_of_session(root, session)
    report(note="reading the change set")
    target, files, note = change_set(root, run=rid, session=session, base=base, head=head,
                                     live=live)
    notes = [note] if note else []
    b = _Build()
    head_only = ""
    dirty: Dict[str, str] = {}
    if target["kind"] == TARGET_RANGE:
        dirty = status_paths(root)
        head_only = range_dirty(root, head, files, dirty)
    graph = _changed_code(root, files, b, code_root or root, head_only=head_only, head=head)
    if target["kind"] == TARGET_RANGE and not head_only:
        why = crossed_dirty(dirty, graph["crossed"], graph["capped"])
        if why:
            head_only = why
            for row in b.code.values():
                if row["coverage"] in (COV_COVERED, COV_UNTESTED):
                    row["coverage"] = COV_UNKNOWN
                    row["note"] = f"not determined: {why}"
            graph["note"] = " | ".join(x for x in (graph["note"], f"judged at {head}: {why} — "
                                                  "coverage is not determined from a tree that "
                                                  f"is not {head}") if x)
    loc = _Locator(root, code_root or root, head=head if head_only else "", files=files)
    acs: List[Dict[str, Any]] = []
    gates: List[Dict[str, Any]] = []
    approvals: List[Dict[str, Any]] = []
    red: Set[str] = set()
    green: Set[str] = set()
    history: Dict[str, Dict[str, Any]] = {}
    record = {"last_suite": None, "ac_tests": {}, "planned": {}}
    if rid:
        report(note="reading the run's record")
        acs, spec_note = _spec_acs(root, rid)
        if spec_note:
            notes.append(spec_note)
        from ..test_runs import read_test_record
        record = read_test_record(root, rid)
        red, green, history = _tdd(root, rid)
        gates, approvals, ledger_note = gates_and_approvals(root, rid)
        if ledger_note:
            notes.append(ledger_note)
    else:
        notes.append("no run is attached to this change set — no acceptance criteria, no TDD "
                     "record and no gate verdicts to show; the code → test half still stands")
    report(note="locating recorded tests")
    _attach_record(b, acs, record, red, green, history, loc)
    _attach_approvals(b, approvals)
    for row in b.code.values():
        row["tests"].sort()
        row["acs"].sort()
    risk = _risk(b, gates, graph)
    data, paging = _page(b, page, size)
    draw = _draw(data["nodes"], more=paging["next"] is not None)
    if graph["note"]:
        notes.append(graph["note"])
    if graph["pending"]:
        notes.append(f"{graph['pending']} changed definition(s) past the per-file bound: blast "
                     f"radius on request (Impact view)")
    suite = record.get("last_suite")
    return {"sample": False, "version": TRACE_VERSION, "target": target,
            "files": files, "nodes": data["nodes"], "edges": data["edges"],
            "gates": gates, "approvals": approvals, "risk": risk,
            "record": {"last_suite": suite, "red": len(red), "green": len(green),
                       "timed": len(history), "recorded_acs": len(record["ac_tests"]),
                       "planned_acs": len(record["planned"])},
            "graph": {"basis": graph["basis"], "backend": graph["backend"],
                      "bases": graph["bases"], "note": graph["note"]},
            "page": paging, "draw": draw, "note": " | ".join(notes)}


def targets(root: str, focus: Optional[str] = None) -> Dict[str, Any]:
    """What the Proof view can be asked about: the local runs (with how many ACs each has), the
    sessions with a recorded change, and whether a commit range can be asked (a git repo)."""
    from .plans import local_run_ids
    from .statedir import initialized
    out: Dict[str, Any] = {"sample": False, "initialized": initialized(root), "runs": [],
                           "sessions": [], "git": D.is_git(root), "focus": focus,
                           "default": None}
    if out["initialized"]:
        by_run = {}
        try:
            from .plans import _sessions_by_run
            by_run = _sessions_by_run(root)
        except OSError:          # an unreadable session index: the runs still list, unbound
            by_run = {}
        for rid in local_run_ids(root):
            acs, note = _spec_acs(root, rid)
            out["runs"].append({"run": rid, "acs": len(acs), "sessions": sorted(by_run.get(rid, [])),
                                "note": note})
        out["runs"].sort(key=lambda r: (-r["acs"], r["run"]))
        try:
            from .sessions import sessions as all_sessions
            for r in all_sessions(root, transcripts=False):
                out["sessions"].append({"session": r.id, "run": r.run or "", "files": len(r.files),
                                        "writes": r.writes})
        except OSError:          # an unreadable session index: no sessions to offer
            pass
        if focus and any(s["session"] == focus for s in out["sessions"]):
            out["default"] = {"session": focus}
        elif out["runs"]:
            out["default"] = {"run": out["runs"][0]["run"]}
    if out["default"] is None and out["git"]:
        out["default"] = {"live": "1"}
    if not out["initialized"]:
        out["note"] = ("this repository has no mokata runs yet — the working tree or a commit "
                       "range still traces code → tests; `mokata init` then a run adds the AC half")
    else:
        out["note"] = ""
    return out


__all__ = ["TRACE_VERSION", "PAGE_ROWS", "TraceRefused", "trace", "targets", "change_set",
           "tdd_verdict", "gates_and_approvals", "test_name_of", "test_path_of"]
