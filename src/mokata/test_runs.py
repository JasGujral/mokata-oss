"""R1 (0.0.26 Lane R) — the run's RECORDED test evidence: the last suite run and the AC→test map.

The Stop evidence gate (`stop_gate`) decides from recorded state only — never by walking the repo,
which on a multi-million-line tree is seconds to minutes. This module is where that state is written:

  * the AC→test map is seeded at EMIT from the tests the completeness gate mapped (`seed_ac_map` —
    the CLI `emit_spec` and the MCP emit both call it once the gate passes), and extended whenever a
    test run names an AC (`--ac AC-1=test_id`);
  * a test RUN is recorded with `mokata progress test-run --passed|--failed --command "<cmd>"
    [--test <id>]… [--ac <AC>=<id>]…`: it stamps the last suite run (when, passed?, what command) and
    records the named tests RED (failed) or GREEN (passed) in the run's TDD record
    (`tdd_state.record`), which is also what licenses implementation under the TDD gate.

One per-run key, `test_runs__<run_id>`, in the run's state dir (gitignored temp_local, the tier of
`tdd_phase__`): run-tracking, UNGATED. Written by `StateStore.update` (locked, atomic). Read by the
Stop hook in ONE lock-free JSON read (`read_test_record`), never raising.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .state import StateStore
from .tdd_state import state_dir

TEST_RUNS_PREFIX = "test_runs__"
#: A test id or AC id longer than this is not an id.
ID_CHARS = 300


def key(run_id: str) -> str:
    return TEST_RUNS_PREFIX + run_id


def _ids(values: Iterable[Any]) -> List[str]:
    return sorted({v.strip()[:ID_CHARS] for v in values if isinstance(v, str) and v.strip()})


def _map(raw: Any) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        return {}
    return {k: _ids(v) for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, list) and _ids(v)}


#: 0.0.28 D (`TEST-RUN-NOT-RUN-LABELS`, ruling D-Q1) — a test run is EXACTLY ONE of these, stored as
#: `last_suite.verdict` (it replaced the `passed` bool: a bool cannot say "cancelled" or "not all
#: ran", and every reader that tested it said "FAILED" for both).
PASSED, FAILED, CANCELLED, NOT_ALL_RAN = "passed", "failed", "cancelled", "not-all-ran"
VERDICTS = (PASSED, FAILED, CANCELLED, NOT_ALL_RAN)
#: What a record without a verdict is: written before 0.0.28 (or damaged). Refused loudly — data the
#: user owns is never shimmed (CLAUDE.md, pre-1.0).
UNREADABLE_SUITE = "unreadable"
OLD_RECORD = ("the last recorded test run cannot be read (it has no verdict: it was recorded before "
              "mokata 0.0.28, or the record is damaged) — re-run the tests")


def verdict_of(failed: int, not_run: int, complete: bool) -> str:
    """The rule, in its order: any red -> failed; else any test that did not run -> not-all-ran;
    else a run that did not finish -> cancelled; else passed."""
    if failed:
        return FAILED
    if not_run:
        return NOT_ALL_RAN
    if not complete:
        return CANCELLED
    return PASSED


def suite_verdict(suite: Any) -> Tuple[str, str]:
    """THE one reader of a recorded test run: `(verdict, words)`. Every consumer (the Stop gate, the
    merge gate, the review panel, the proof route) says these words and tests no bool. A record
    with no verdict reads `unreadable` with the instruction to re-run the tests."""
    v = suite.get("verdict") if isinstance(suite, dict) else None
    if v not in VERDICTS:
        return UNREADABLE_SUITE, OLD_RECORD
    why = suite.get("reason") if isinstance(suite.get("reason"), str) else ""
    if v == PASSED:
        return v, "the last recorded test run passed"
    if v == FAILED:
        return v, "the last recorded test run FAILED" + (f": {why}" if why else "")
    if v == CANCELLED:
        return v, ("the last recorded test run was CANCELLED before it finished"
                   + (f" ({why})" if why else "") + " — re-run the tests")
    return v, "NOT ALL tests ran in the last recorded test run" + (f": {why}" if why else "")


def _suite(raw: Any) -> Optional[Dict[str, Any]]:
    """The stored suite as readers see it: with `said` — the one reader's words — beside it."""
    if not isinstance(raw, dict):
        return None
    return {**raw, "said": suite_verdict(raw)[1]}


def read_test_record(root: str, run_id: Optional[str]) -> Dict[str, Any]:
    """`{"last_suite": {...} | None, "ac_tests": {AC: [ids]}, "planned": {AC: [ids]}}` — ONE
    lock-free read; an absent or unreadable record reads as empty (and the gate that reads it then
    finds no evidence, which is the fail-closed answer)."""
    data = None
    if run_id:
        try:
            data = StateStore(state_dir(root)).read(key(run_id))
        except Exception:  # noqa: BLE001 — a hook read never raises; empty IS no evidence
            data = None
    data = data if isinstance(data, dict) else {}
    return {"last_suite": _suite(data.get("last_suite")),
            "ac_tests": _map(data.get("ac_tests")), "planned": _map(data.get("planned")),
            "tests": _history(data.get("tests")), "qualified": _qualified(data.get("qualified"))}


def read_test_record_strict(root: str, run_id: str) -> Any:
    """0.0.27 P14 — the run's test record told THREE ways (`state.StrictRead`): ABSENT (no record:
    no test has been mapped or run), UNREADABLE (a record is there and could not be read — a hint
    must then say "unknown", never "no tests"), or OK with the SAME shape `read_test_record`
    returns. Read-only and lock-free; `read_test_record` (the Stop hook's fail-closed read) is
    unchanged."""
    from .state import OK, UNREADABLE, StrictRead
    got = StateStore(state_dir(root)).read_strict(key(run_id))
    if got.state != OK:
        return got
    data = got.value
    if not isinstance(data, dict):
        return StrictRead(UNREADABLE, reason="is not a test record (wrong shape)", path=got.path)
    suite = data.get("last_suite")
    if isinstance(suite, dict) and suite_verdict(suite)[0] == UNREADABLE_SUITE:
        # a run recorded before the verdict existed: refused loudly, with what to do (D-Q1)
        return StrictRead(UNREADABLE, reason=OLD_RECORD, path=got.path)
    return StrictRead(OK, {"last_suite": _suite(suite),
                           "ac_tests": _map(data.get("ac_tests")),
                           "planned": _map(data.get("planned")),
                           "tests": _history(data.get("tests")),
                           "qualified": _qualified(data.get("qualified"))}, path=got.path)


def _qualified(raw: Any) -> Dict[str, List[str]]:
    """`{full id: [the spellings it was recorded as]}` — the ids write-time qualification
    (`full_ids`) rewrote, kept so the trace can SAY a test was named by a bare / dotted id."""
    if not isinstance(raw, dict):
        return {}
    return {k: _ids(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, list)}


def _history(raw: Any) -> Dict[str, Dict[str, Any]]:
    """`{test_id: {red_at, green_at, last, last_at}}` — the per-test RED-then-GREEN times
    (0.0.27 P13): `red_at` = the first time the test was recorded failing, `green_at` = the first
    time it was recorded passing AFTER that red (None until then), `last`/`last_at` = its latest
    recorded outcome. Bounded by the number of tests named, never by a count."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        out[k] = {"red_at": _t(v.get("red_at")), "green_at": _t(v.get("green_at")),
                  "last": v.get("last") if v.get("last") in ("red", "green") else None,
                  "last_at": _t(v.get("last_at")),
                  # the FIRST recorded outcome: a test whose first run was green was never
                  # test-first (None on a record from before this field existed)
                  "first": v.get("first") if v.get("first") in ("red", "green") else None,
                  # the test's content stamp at its latest outcome (`test_ids.py_test_defs`): a
                  # proof does not outlive an edit to the test (P13 round 6, J4)
                  "stamp": v.get("stamp") if isinstance(v.get("stamp"), str) else None,
                  # 0.0.28 E2: the repo-relative REAL file each outcome ran against
                  # (`test_ids.stamp_target`), in clear; absent on a record from before 0.0.28,
                  # which then never proves ("re-run it red, then green")
                  **{k: v[k] for k in TARGET_KEYS if isinstance(v.get(k), str)}}
    return out


def _t(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


#: 0.0.28 E2 — the file the latest RED ran against, the latest GREEN, and the latest outcome.
TARGET_KEYS = ("red_target", "green_target", "target")


def _advance(hist: Dict[str, Dict[str, Any]], ids: Iterable[str], passed: bool,
             at: float, stamps: Optional[Dict[str, Optional[str]]] = None,
             targets: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    """One recorded outcome for each test id, folded into the per-test history (with the test's
    content stamp at that moment, when it could be read, and — 0.0.28 E2 — the file it ran against:
    `target`, and `green_target` / `red_target` for this outcome's side). A target is written on
    EVERY outcome; one that cannot be told is DELETED, never carried over from an earlier outcome."""
    side = "green_target" if passed else "red_target"
    for tid in ids:
        h = dict(hist.get(tid) or {})
        if stamps is not None and tid in stamps:
            h["stamp"] = stamps[tid]
        got = (targets or {}).get(tid)
        if isinstance(got, str) and got:
            h["target"] = h[side] = got
        else:
            h.pop("target", None)
            h.pop(side, None)
        if h.get("first") is None and h.get("last_at") is None:
            h["first"] = "green" if passed else "red"
        if passed:
            if h.get("red_at") is not None and h.get("green_at") is None:
                h["green_at"] = at
            h["last"] = "green"
        else:
            if h.get("red_at") is None:
                h["red_at"] = at
            elif h.get("green_at") is not None:
                h["red_at"], h["green_at"] = at, None      # regressed: the proof starts over
            h["last"] = "red"
        h["last_at"] = at
        hist[tid] = h
    return hist


def parse_ac_pairs(pairs: Iterable[str]) -> Dict[str, List[str]]:
    """`["AC-1=test_a", …]` → `{"AC-1": ["test_a"]}`. A malformed pair raises ValueError."""
    out: Dict[str, List[str]] = {}
    for pair in pairs or ():
        ac, sep, test = (pair or "").partition("=")
        if not sep or not ac.strip() or not test.strip():
            raise ValueError(f"--ac takes AC=TEST (e.g. AC-1=test_login), got {pair!r}")
        out.setdefault(ac.strip(), []).append(test.strip())
    return out


#: Who recorded a test run — PROVENANCE, so an IDE re-run (a human pressed Re-run; 0.0.26 P7,
#: ruled 2026-10-28) counts as evidence yet is always told apart from a run recorded through
#: `mokata progress test-run` (the agent's channel, or a human at the CLI).
#: 0.0.27 P21: "runner" — mokata's own full-suite runner (`merge_suite`), the only source the gated
#: merge counts: the configured command, unfiltered, with the tree stamped before and after.
SOURCES = ("cli", "ide", "runner")


def full_ids(root: str, run_id: str, ids: Iterable[str],
             reported: Iterable[str] = ()) -> Dict[str, str]:
    """`{recorded id: the id to store}` — P13 round 7 (K1): an id is rewritten ONLY from what the
    test runner itself reported, never guessed from the directories the run touched:

      * a bare name that exactly one of the runner's `reported` full ids (pytest node ids from the
        run's `-rA` / junit / `--collect-only` output) answers to → that full id;
      * a dotted unittest id names its MODULE (`pkg.test_mod.TestA.test_x` → `pkg/test_mod.py`,
        relative to the repo root, the directory `python -m unittest` runs from): rewritten when
        exactly one split names a file that binds the key once and would collect it;
      * a path a runner printed relative to its own rootdir that is not a file at the repo root,
        when exactly ONE tracked file in the whole index ends in it (`git ls-files`).

    Anything else is stored as typed: a bare id never proves. Never raises."""
    from .test_ids import normalise_path, qualify_ids, test_path_of, tracked_ending
    ids = [t for t in ids if isinstance(t, str) and t.strip()]
    try:
        out = qualify_ids(root, ids, reported)
        for tid in ids:
            p = test_path_of(tid)
            if not p or tid in out or os.path.isfile(os.path.join(root, *p.split("/"))):
                continue
            hits = tracked_ending(root, p)
            if len(hits) == 1:
                raw = tid.strip()
                out[tid] = normalise_path(hits[0]) + raw[raw.index("::"):]
        return out
    except (OSError, ValueError, TypeError):
        return {}


def _note_qualified(cur: Dict[str, List[str]], full: Dict[str, str]) -> Dict[str, List[str]]:
    for typed, f in full.items():
        if typed != f:
            cur[f] = _ids(cur.get(f, []) + [typed])
    return cur


def stamps_of(root: str, ids: Iterable[str]) -> Dict[str, Optional[str]]:
    """Each full id's content stamp as the tree holds it NOW — the test, the non-test code of its
    scopes and the conftest.py files on its path and its real path (`test_ids.stamp_of`); None: no
    file, no such test. Each file read once."""
    return _stamps_and_targets(root, ids)[0]


def _stamps_and_targets(root: str, ids: Iterable[str]
                        ) -> Tuple[Dict[str, Optional[str]], Dict[str, str]]:
    """`(stamps, targets)` of each id that names a file: its stamp (`stamps_of`) and the file it
    opens NOW (`test_ids.stamp_target` of the id's path as typed) — what a recorded outcome binds."""
    from .test_ids import (TARGET_STATES, raw_path_of, stamp_of, stamp_target, test_key_of,
                           test_path_of)
    out: Dict[str, Optional[str]] = {}
    targets: Dict[str, str] = {}
    texts: Dict[str, Optional[str]] = {}

    def read(rel: str) -> Optional[str]:
        if rel not in texts:
            try:
                with open(os.path.join(root, *rel.split("/")), encoding="utf-8",
                          errors="replace") as fh:
                    texts[rel] = fh.read()
            except OSError:
                texts[rel] = None
        return texts[rel]
    for tid in ids:
        p = test_path_of(tid)
        if p:
            targets[tid] = tgt = stamp_target(root, raw_path_of(tid))
            out[tid] = stamp_of(p, read(p), test_key_of(tid), read=read,
                                real=None if tgt in TARGET_STATES else tgt)
    return out, targets


def record_test_run(root: str, run_id: str, *, passed: bool, command: str = "",
                    tests: Iterable[str] = (), ac_map: Optional[Dict[str, List[str]]] = None,
                    at: Optional[float] = None, results: Optional[Dict[str, bool]] = None,
                    source: str = "cli", actor: str = "",
                    exit_code: Optional[int] = None,
                    reported: Iterable[str] = (),
                    tree: Optional[Dict[str, Any]] = None,
                    not_run: Optional[Dict[str, str]] = None,
                    complete: bool = True) -> Dict[str, Any]:
    """Record one test run for `run_id`: the last suite run, the named tests RED (failed) or GREEN
    (passed) in the TDD record, and any AC→test pairs. Returns the stored `last_suite`.

    `results` ({test: passed?}) records a run whose tests did not all end the same way — each test
    RED or GREEN by its own outcome (an IDE re-run of several tests). `source`/`actor`/`exit_code`
    are the run's provenance and are stored with it. `reported` is what the TEST RUNNER itself
    said it ran (its full ids, `test_ids.runner_ids`): the only thing a bare name is ever
    qualified against (K1). `tree` (P21) is where the run happened as git said it — the worktree,
    its head and cleanliness before and after, and the scope — stored with the run, never derived.

    0.0.28 D: the run's `verdict` (`verdict_of`) replaces the old `passed` bool. `passed` is the
    runner's own word for the run (exit 0); `not_run` ({test: why}) names tests that never ran (not an
    outcome: never RED, never GREEN); `complete=False` is a run that was cancelled part-way. Stored
    with `reason` (unless passed) and the `failed` / `not_run` id lists."""
    if not run_id:
        raise ValueError("no run to record the test run against — name it with --run")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    named = list(tests) + [t for v in (ac_map or {}).values() for t in (v or ())] + \
        list((results or {}).keys())
    full = full_ids(root, run_id, named, reported)
    tests = [full.get(t, t) for t in tests]
    ac_map = {ac: [full.get(t, t) for t in (v or ())] for ac, v in (ac_map or {}).items()}
    results = {full.get(t, t): v for t, v in (results or {}).items()} or None
    ids = _ids(tests)
    pairs = _map(ac_map or {})
    for linked in pairs.values():
        ids = sorted(set(ids) | set(linked))
    store = StateStore(state_dir(root))
    from .tdd_state import record

    def typed_too(xs: List[str]) -> List[str]:
        # the TDD gate's phase sets keep the spelling it was GIVEN beside the full id, so a gate
        # asked about `test_x` still finds the RED recorded as `tests/t.py::test_x`
        return _ids(list(xs) + [o for o, f in full.items() if f in set(xs)])
    if results:
        ok = _ids(t for t, v in results.items() if v)
        bad = _ids(t for t, v in results.items() if not v)
        ids = sorted(set(ids) | set(ok) | set(bad))
        record(store, run_id, green=typed_too(ok), red=typed_too(bad))
    elif ids:
        record(store, run_id, **({"green": typed_too(ids)} if passed else {"red": typed_too(ids)}))
    when = time.time() if at is None else at
    # each test's own outcome when the run says it (`results`); otherwise the run's own word
    failed = _ids(t for t, v in results.items() if not v) if results else ([] if passed else ids)
    skipped = {t.strip()[:ID_CHARS]: (w or "")[:300] for t, w in (not_run or {}).items()
               if isinstance(t, str) and t.strip()}
    verdict = FAILED if not passed and not failed else verdict_of(len(failed), len(skipped),
                                                                 complete)
    suite = {"at": when, "verdict": verdict, "reason": _reason(verdict, failed, skipped, exit_code),
             "failed": failed, "not_run": sorted(skipped),
             "command": (command or "").strip()[:ID_CHARS], "tests": ids,
             "source": source, "actor": (actor or "")[:ID_CHARS],
             "exit_code": exit_code if isinstance(exit_code, int) else None}
    if isinstance(tree, dict):
        suite["tree"] = dict(tree)
    # each test's own outcome when the run says it (`results`); otherwise the run's verdict
    outcomes = {t.strip()[:ID_CHARS]: bool(v) for t, v in (results or {}).items() if isinstance(t, str)}

    def _mutate(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        merged = _map(cur.get("ac_tests"))
        for ac, linked in pairs.items():
            merged[ac] = _ids(merged.get(ac, []) + linked)
        return {"run_id": run_id, "last_suite": suite, "ac_tests": merged,
                "planned": _map(cur.get("planned")),
                "tests": _fold(_history(cur.get("tests"))),
                "qualified": _note_qualified(_qualified(cur.get("qualified")), full)}

    stamps, targets = _stamps_and_targets(root, ids)

    def _fold(hist: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        hist = _advance(hist, [t for t in ids if outcomes.get(t, bool(passed))], True, when,
                        stamps, targets)
        return _advance(hist, [t for t in ids if not outcomes.get(t, bool(passed))], False, when,
                        stamps, targets)

    store.update(key(run_id), _mutate, default=None)
    return suite


def _reason(verdict: str, failed: List[str], not_run: Dict[str, str],
            exit_code: Optional[int]) -> str:
    """Why a run is not `passed`, in words ("" when it is)."""
    if verdict == PASSED:
        return ""
    if verdict == FAILED:
        if failed:
            return f"{len(failed)} test(s) failed: " + ", ".join(failed[:5]) + \
                (f" and {len(failed) - 5} more" if len(failed) > 5 else "")
        return "the test command failed" + (f" (exit {exit_code})" if isinstance(exit_code, int)
                                            else "")
    if verdict == NOT_ALL_RAN:
        first = next(iter(sorted(not_run.items())), ("", ""))
        return (f"{len(not_run)} test(s) did not run" + (f" — {first[0]}: {first[1]}" if first[1]
                                                         else ""))
    return "the run was cancelled before every test had run"


def record_outcome(store: StateStore, run_id: str, tests: Iterable[str], *, passed: bool,
                   at: Optional[float] = None) -> None:
    """One RED or GREEN outcome for `tests`, folded into the run's per-test timed history — the
    write `TddGuard.record_red/record_green` make (0.0.27 P13, coordinator ruling 2) so a test
    recorded through the MCP gate carries the same red-before-green proof as one recorded by
    `mokata progress test-run`. Touches only the `tests` history; the suite record is left as is.
    `store` is the run's state store (the key carries the run id, so a session-scoped store passes
    it through verbatim)."""
    ids = _ids(tests)
    if not run_id or not ids:
        return
    when = time.time() if at is None else at
    from .tdd_state import root_of_state_dir
    root = root_of_state_dir(getattr(store, "root", "") or "")
    stamps: Dict[str, Optional[str]] = {}
    targets: Dict[str, str] = {}
    full: Dict[str, str] = {}
    if root:
        full = full_ids(root, run_id, ids)
        ids = _ids(full.get(t, t) for t in ids)
        stamps, targets = _stamps_and_targets(root, ids)

    def _mutate(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur.update(run_id=run_id,
                   tests=_advance(_history(cur.get("tests")), ids, passed, when, stamps,
                                     targets),
                   qualified=_note_qualified(_qualified(cur.get("qualified")), full))
        return cur

    store.update(key(run_id), _mutate, default=None)


def seed_ac_map(root: str, run_id: str, tests: Iterable[Any]) -> None:
    """At EMIT: the AC→test map the completeness gate just checked (TestRefs: `name`, `ac_ids`),
    stored as the run's PLANNED map (replaced on every emit; the recorded runs' map is kept).
    Best-effort — the emit has already been decided."""
    if not run_id or not root:
        return
    planned: Dict[str, List[str]] = {}
    for t in tests or ():
        name = getattr(t, "name", None)
        for ac in getattr(t, "ac_ids", None) or ():
            if isinstance(name, str) and isinstance(ac, str):
                planned.setdefault(ac, []).append(name)

    def _mutate(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur.update(run_id=run_id, planned=_map(planned))
        return cur

    try:
        StateStore(state_dir(root)).update(key(run_id), _mutate, default=None)
    except OSError:
        pass                    # the Stop gate then asks for the map to be recorded by test-run


def mapped_tests(record: Dict[str, Any], ac: str) -> List[str]:
    return _ids(record["ac_tests"].get(ac, []) + record["planned"].get(ac, []))


__all__ = ["SOURCES", "TEST_RUNS_PREFIX", "VERDICTS", "PASSED", "FAILED", "CANCELLED", "NOT_ALL_RAN",
           "UNREADABLE_SUITE", "OLD_RECORD", "verdict_of", "suite_verdict", "key", "read_test_record", "read_test_record_strict", "parse_ac_pairs", "record_test_run",
           "record_outcome", "seed_ac_map", "mapped_tests", "full_ids", "stamps_of", "TARGET_KEYS"]
