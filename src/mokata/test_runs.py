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

import time
from typing import Any, Dict, Iterable, List, Optional

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
    suite = data.get("last_suite")
    return {"last_suite": suite if isinstance(suite, dict) else None,
            "ac_tests": _map(data.get("ac_tests")), "planned": _map(data.get("planned"))}


def parse_ac_pairs(pairs: Iterable[str]) -> Dict[str, List[str]]:
    """`["AC-1=test_a", …]` → `{"AC-1": ["test_a"]}`. A malformed pair raises ValueError."""
    out: Dict[str, List[str]] = {}
    for pair in pairs or ():
        ac, sep, test = (pair or "").partition("=")
        if not sep or not ac.strip() or not test.strip():
            raise ValueError(f"--ac takes AC=TEST (e.g. AC-1=test_login), got {pair!r}")
        out.setdefault(ac.strip(), []).append(test.strip())
    return out


def record_test_run(root: str, run_id: str, *, passed: bool, command: str = "",
                    tests: Iterable[str] = (), ac_map: Optional[Dict[str, List[str]]] = None,
                    at: Optional[float] = None) -> Dict[str, Any]:
    """Record one test run for `run_id`: the last suite run, the named tests RED (failed) or GREEN
    (passed) in the TDD record, and any AC→test pairs. Returns the stored `last_suite`."""
    if not run_id:
        raise ValueError("no run to record the test run against — name it with --run")
    ids = _ids(tests)
    pairs = _map(ac_map or {})
    for linked in pairs.values():
        ids = sorted(set(ids) | set(linked))
    store = StateStore(state_dir(root))
    if ids:
        from .tdd_state import record
        record(store, run_id, **({"green": ids} if passed else {"red": ids}))
    suite = {"at": time.time() if at is None else at, "passed": bool(passed),
             "command": (command or "").strip()[:ID_CHARS], "tests": ids}

    def _mutate(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        merged = _map(cur.get("ac_tests"))
        for ac, linked in pairs.items():
            merged[ac] = _ids(merged.get(ac, []) + linked)
        return {"run_id": run_id, "last_suite": suite, "ac_tests": merged,
                "planned": _map(cur.get("planned"))}

    store.update(key(run_id), _mutate, default=None)
    return suite


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


__all__ = ["TEST_RUNS_PREFIX", "key", "read_test_record", "parse_ac_pairs", "record_test_run",
           "seed_ac_map", "mapped_tests"]
