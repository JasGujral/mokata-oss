"""0.0.28 F round 3 — the SessionStart hook parses the progress log ONCE.

What must be true for the user:
  * A session start on a busy repo (progress log at its 8 MiB read bound) stays inside the
    session-start ceiling on EVERY source — startup, resume, clear, compact (the budget itself is
    pinned in `test_hb_hook_budget.SessionStartHoldsOnEverySourceAtTheLogBound`).
  * Sharing the parse changes NO answer: inside `shared_backward_reads()` every backward read —
    and the next-step derivation's own scan, damaged lines and the read bound included — returns
    exactly what the uncached read returns, across appends made between reads, readers that stop
    early, and a log replaced or truncated underneath.
  * The scope is the hook's alone: it is open for the whole SessionStart and closed after it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hints as H
from mokata import log_read_scope as SCOPE
from mokata import progress_events as PE
from mokata.log_read_scope import shared_backward_reads
from mokata.progress_events import PROGRESS_EVENTS_FILENAME, ProgressLog
from mokata.tdd_state import state_dir


def _ev(i, run="r1", typ="phase_progress", ts="2030-01-01T00:00:00Z", pad=0):
    return json.dumps({"event_id": f"e{i}", "ts": ts, "type": typ, "stage": "develop",
                       "run_id": run, "data": {"i": i, "pad": "x" * pad}})


#: one file body per shape the reader must agree on — blank lines, damaged lines (not JSON, a JSON
#: non-object, invalid UTF-8), lines of mixed lengths, a torn last line, a damaged FIRST line
BODIES = {
    "plain": "".join(_ev(i, pad=i % 37) + "\n" for i in range(400)),
    "blank-and-damaged": "".join(
        (_ev(i) if i % 7 else ("{not json" if i % 2 else "[1, 2]")) + "\n" + ("\n" if i % 11 == 0
                                                                              else "")
        for i in range(300)),
    "torn-tail": "".join(_ev(i, pad=i) + "\n" for i in range(200)) + '{"event_id": "half',
    "damaged-first": "garbage\n" + "".join(_ev(i) + "\n" for i in range(200)),
    "one-line-no-newline": _ev(0),
    "empty": "",
}


class _Log:
    def __init__(self, test, body):
        t = tempfile.TemporaryDirectory()
        test.addCleanup(t.cleanup)
        self.root = os.path.realpath(t.name)
        os.makedirs(state_dir(self.root), exist_ok=True)
        self.path = os.path.join(state_dir(self.root), PROGRESS_EVENTS_FILENAME)
        self.write(body)

    def write(self, body, mode="wb"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        if mode == "ab":
            with open(self.path, "ab") as fh:
                fh.write(data)
        else:
            with open(self.path, "wb") as fh:
                fh.write(data)

    def size(self):
        return os.path.getsize(self.path)

    def plain(self, cap):
        return list(ProgressLog(self.path).read_events_backward(byte_cap=cap))


def _caps(size):
    """Every bound that matters: none, 0, the whole file, one past, and cuts inside lines, on line
    edges, and one block (64 KiB) away."""
    out = {None, 0, size, size + 1, 1, 64 * 1024, 64 * 1024 + 3}
    out.update(range(max(1, size - 300), size + 2, 7))
    out.update(range(1, min(size, 900), 13))
    return sorted(out, key=lambda c: -1 if c is None else c)


class SameEventsAsTheUncachedRead(unittest.TestCase):

    def test_every_body_every_cap_any_order(self):
        for name, body in BODIES.items():
            log = _Log(self, body)
            caps = _caps(log.size())
            want = {c: log.plain(c) for c in caps}
            for order in (caps, list(reversed(caps))):          # shallow first, and deep first
                with shared_backward_reads():
                    for c in order:
                        with self.subTest(body=name, cap=c, first=order[0]):
                            self.assertEqual(want[c], log.plain(c))

    def test_a_reader_that_stops_early_leaves_the_next_reader_whole(self):
        log = _Log(self, BODIES["plain"] * 200)                  # > one block
        want = log.plain(None)
        with shared_backward_reads():
            for stop in (1, 5, 300, 2000):
                it = ProgressLog(log.path).read_events_backward(byte_cap=None)
                got = [next(it) for _ in range(stop)]
                it.close()
                self.assertEqual(want[:stop], got)
                self.assertEqual(want, log.plain(None))

    def test_appends_between_reads(self):
        log = _Log(self, BODIES["blank-and-damaged"])
        with shared_backward_reads():
            for k in range(6):
                cap = (None, 500, 5000)[k % 3]
                log.plain(cap)                                   # warm (partly) at this size
                log.write(_ev(1000 + k, pad=k) + "\n" + ("\n" if k % 2 else "") +
                          ("oops\n" if k == 3 else ""), mode="ab")
                for c in _caps(log.size()):
                    with self.subTest(k=k, cap=c):
                        with self._uncached():
                            want = log.plain(c)
                        self.assertEqual(want, log.plain(c))

    def test_an_append_off_a_line_boundary_and_a_reader_mid_walk(self):
        log = _Log(self, BODIES["torn-tail"])
        with shared_backward_reads():
            it = ProgressLog(log.path).read_events_backward(byte_cap=None)
            first = next(it)
            log.write('", "x": 1}\n' + _ev(9999) + "\n", mode="ab")   # completes the torn line
            rest = list(it)                                      # the old walk: the old file
            with self._uncached():
                want_new = log.plain(None)
            self.assertEqual(want_new, log.plain(None))
        self.assertEqual(first["event_id"], "e199")
        self.assertEqual([e["event_id"] for e in rest][-1], "e0")

    def test_replaced_and_truncated_underneath(self):
        log = _Log(self, BODIES["plain"])
        with shared_backward_reads():
            log.plain(None)
            log.write(BODIES["damaged-first"])                   # shorter: truncated in place
            with self._uncached():
                want = log.plain(None)
            self.assertEqual(want, log.plain(None))
            log.plain(None)
            other = BODIES["damaged-first"].replace('"e', '"f') + _ev(5000) + "\n"
            self.assertEqual(len(BODIES["damaged-first"]), other.index(_ev(5000)))
            tmp = log.path + ".new"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(other)                                  # same lines, different events
            os.replace(tmp, log.path)                            # a new inode, longer
            with self._uncached():
                want = log.plain(None)
            self.assertEqual(want, log.plain(None))
            os.remove(log.path)
            self.assertEqual([], log.plain(None))

    def _uncached(self):
        return mock.patch.object(SCOPE, "SHARED", None)


class TheDerivationScanIsUnchanged(unittest.TestCase):
    """`hints._scan_log` — per run: newest verdict, finished, settled, the damaged-line fault and
    the read-bound fault — identical inside the scope and out."""

    def _runs(self, created):
        return {rid: SimpleNamespace(checkpoint=lambda c=c: {"created_at": c}, log={})
                for rid, c in created.items()}

    def _scan(self, root, created):
        runs = self._runs(created)
        rd = H._Read()
        H._scan_log(root, runs, rd)
        return {rid: r.log for rid, r in runs.items()}

    def _bodies(self):
        def T(i):
            return "2030-01-01T00:%02d:%02dZ" % divmod(i, 60)
        start = H._epoch(T(5))
        rows = []
        for i in range(90):
            run = ("r1", "r2", "r3")[i % 3]
            typ = ("review_verdict" if i % 5 == 0 else "run_finished" if i == 41 else
                   "phase_progress")
            rows.append(_ev(i, run=run, typ=typ, ts=T(i), pad=i))
        good = "\n".join(rows) + "\n"
        damaged = "\n".join(rows[:30] + ["{torn"] + rows[30:]) + "\n"
        created = {"r1": start, "r2": start + 30, "r3": None, "r4": start}
        return {"good": good, "damaged": damaged, "empty": ""}, created

    def test_every_body_every_bound(self):
        bodies, created = self._bodies()
        for name, body in bodies.items():
            log = _Log(self, body)
            for cap in sorted({1, 50, 400, 2000, log.size(), log.size() + 1, H.LOG_BYTE_CAP}):
                with self.subTest(body=name, cap=cap), mock.patch.object(H, "LOG_BYTE_CAP", cap):
                    want = self._scan(log.root, created)
                    with shared_backward_reads():
                        ProgressLog(log.path).find_last_event(lambda e: False)   # warm it
                        got = self._scan(log.root, created)
                        self.assertEqual(want, got)
                        self.assertEqual(want, self._scan(log.root, created))
                if name == "damaged" and cap > log.size():
                    self.assertIn("damaged line", want["r1"]["fault"])   # the case is reached

    def test_a_torn_tail_appended_inside_the_scope(self):
        bodies, created = self._bodies()
        log = _Log(self, bodies["good"])
        with shared_backward_reads():
            self._scan(log.root, created)                        # cached at this size
            log.write('{"event_id": "torn', mode="ab")           # a writer mid-line
            got = self._scan(log.root, created)
            with mock.patch.object(SCOPE, "SHARED", None):
                want = self._scan(log.root, created)
            self.assertEqual(want, got)
        self.assertIn("damaged line", want["r1"]["fault"])

    def test_an_unreadable_log_is_reported_not_read_as_empty(self):
        bodies, created = self._bodies()
        log = _Log(self, bodies["good"])
        unreadable = OSError("unreadable")
        with mock.patch.object(H, "open", create=True, side_effect=unreadable):
            want = self._scan(log.root, created)
        with shared_backward_reads(), \
                mock.patch.object(PE, "open", create=True, side_effect=unreadable):
            got = self._scan(log.root, created)
        self.assertIn("could not be read", want["r1"]["fault"])
        self.assertEqual(want, got)

    def test_inside_the_scope_the_log_is_parsed_once(self):
        """THE round-3 budget fix: derive's scan re-parses nothing the start-up already parsed
        (on a log at its 8 MiB bound each parse is ~40 ms of the session-start ceiling)."""
        bodies, created = self._bodies()
        log = _Log(self, bodies["good"] * 40)
        real = PE._parse_line
        calls = []

        def counting(raw):
            calls.append(1)
            return real(raw)
        with shared_backward_reads():
            list(ProgressLog(log.path).read_events_backward(byte_cap=None))
            with mock.patch.object(PE, "_parse_line", counting), \
                    mock.patch.object(H.json, "loads",
                                      side_effect=AssertionError("derive parsed the log itself")):
                self._scan(log.root, created)
                list(ProgressLog(log.path).read_events_backward(byte_cap=None))
        self.assertEqual([], calls)

    def test_no_log_inside_the_scope(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = os.path.realpath(t.name)
        want = self._scan(root, {"r1": 1.0})
        with shared_backward_reads():
            self.assertEqual(want, self._scan(root, {"r1": 1.0}))


class TheScopeIsTheHooksAlone(unittest.TestCase):

    def test_closed_outside_and_restored_after(self):
        self.assertIsNone(SCOPE.SHARED)
        with shared_backward_reads():
            self.assertIsNotNone(SCOPE.SHARED)
            with shared_backward_reads():
                pass
            self.assertIsNotNone(SCOPE.SHARED)
        self.assertIsNone(SCOPE.SHARED)

    def test_session_start_runs_inside_it(self):
        from mokata import hook_cli
        seen = []

        class Stop(Exception):
            pass

        def first_step(*a, **k):                         # the hook body's first call
            seen.append(SCOPE.SHARED is not None)
            raise Stop
        with mock.patch.object(hook_cli, "_parse_flags", first_step):
            with self.assertRaises(Stop):
                hook_cli.session_start_main([])
        self.assertEqual([True], seen)
        self.assertIsNone(SCOPE.SHARED)
        self.assertEqual("session_start_main", hook_cli.session_start_main.__name__)


# ============================================================== round 4 — the per-line cost

#: Every line shape the block parse must decide exactly as the line-by-line parse does: the plain
#: event; JSON whitespace json.loads allows at the edges (and the BOM and `\x0b` it does not); two
#: values on a line; a value that only completes on the NEXT line; non-objects; NaN/Infinity;
#: non-ASCII; blank and whitespace-only; a long line.
TRICKY = [
    b'{"event_id": "a", "type": "phase_progress", "run_id": "r1"}',
    b'{"a":1}\r', b'{"a":1}  \t', b' {"a":1}', b'\t{"a":1}', b'\r{"a":1}',
    '﻿{"a":1}'.encode("utf-8"), b'{"a":1}\x0b', b'\x0c{"a":1}',
    b'{"a":1}{"b":2}', b'{"a":1} x', b'{"a":1},', b'{"a":[{}', b'{}]}', b'{"a":1', b',"b":2}',
    b'[1, 2]', b'1', b'"s"', b'null', b'true', b'{}', b'{', b'}', b'{"a":NaN}', b'{"a":-Infinity}',
    '{"é":"ü","k":"日本"}'.encode("utf-8"), b'', b'   ', b'\t', b'\r', b'{"a":"' + b"x" * 70000 + b'"}',
    b'{"a":{"b":{"c":[1,{"d":null}]}}}', b'{"a":1}   \r',
]
INVALID_UTF8 = b'{"a":"\xff"}'


def _same(a, b):
    """Row lists equal, NaN-safe (json.dumps renders NaN identically on both sides)."""
    def norm(rows):
        return [(off, "DAMAGED" if v is PE.DAMAGED else json.dumps(v, sort_keys=True))
                for off, v in rows]
    return norm(a) == norm(b)


class TheBlockParseIsTheLineParse(unittest.TestCase):
    """`_parse_rows` — one decode per block, the C scanner per line — against `_cached_row` line by
    line, on every line shape in `TRICKY`, in seeded random blocks, with and without a line that is
    not UTF-8 (which sends the whole block line by line)."""

    def _reference(self, parts, off):
        out = []
        for raw in parts:
            v = PE._cached_row(raw)
            if v is not None:
                out.append((off, v))
            off += len(raw) + 1
        return out

    def test_each_shape_alone_and_in_every_neighbourhood(self):
        for i, line in enumerate(TRICKY):
            for parts in ([line], [line, TRICKY[0]], [TRICKY[0], line],
                          [line, TRICKY[(i + 1) % len(TRICKY)]], [line, line]):
                with self.subTest(line=line[:40], parts=len(parts)):
                    self.assertTrue(_same(self._reference(parts, 7), PE._parse_rows(parts, 7)))

    def test_seeded_random_blocks(self):
        import random
        rnd = random.Random(2028)
        for trial in range(400):
            parts = [rnd.choice(TRICKY) for _ in range(rnd.randint(1, 30))]
            if trial % 5 == 0:
                parts.insert(rnd.randrange(len(parts) + 1), INVALID_UTF8)
            with self.subTest(trial=trial):
                self.assertTrue(_same(self._reference(parts, 0), PE._parse_rows(parts, 0)))

    def test_the_whole_reader_on_a_log_of_every_shape(self):
        import random
        rnd = random.Random(7)
        body = b"\n".join(rnd.choice(TRICKY + [INVALID_UTF8]) for _ in range(3000)) + b"\n"
        log = _Log(self, body)
        for cap in (None, 1000, 64 * 1024 + 5, log.size()):
            want = log.plain(cap)
            with shared_backward_reads():
                got = log.plain(cap)
            with self.subTest(cap=cap):
                self.assertEqual(json.dumps(want, sort_keys=True), json.dumps(got, sort_keys=True))


class TheTimelineLineParseIsJsonLoads(unittest.TestCase):

    def test_every_shape(self):
        for raw in TRICKY:
            line = raw.decode("utf-8") + "\n"           # as `for raw in fh` hands it over
            with self.subTest(line=line[:40]):
                try:
                    want = ("ok", json.dumps(json.loads(line), sort_keys=True))
                except ValueError as exc:
                    want = ("raise", type(exc))
                try:
                    got = ("ok", json.dumps(H._loads_line(line), sort_keys=True))
                except ValueError as exc:
                    got = ("raise", type(exc))
                self.assertEqual(want, got)


def _stage_log(rnd, n=1500):
    """A log of three runs' stage entries (some with no stage), finishes, noise and damage."""
    lines = []
    for i in range(n):
        k = rnd.random()
        run = rnd.choice(["r1", "r2", "r3", None, 7])
        if k < 0.05:
            lines.append("garbage")
        elif k < 0.1:
            lines.append("")
        else:
            typ = rnd.choice([PE.STAGE_ENTER, PE.STAGE_ENTER, PE.RUN_FINISHED, "phase_progress",
                              "phase_progress", ["x"]])
            stage = rnd.choice(["spec", "develop", "", None])
            lines.append(json.dumps({"event_id": f"e{i}", "ts": "2030-01-01T00:00:00Z",
                                     "type": typ, "stage": stage, "run_id": run, "data": {}}))
    return "\n".join(lines) + "\n"


QUERIES = [(rid, types, where)
           for rid in ("r1", "r2", "r3", "r9", None)
           for types in ((PE.STAGE_ENTER,), (PE.RUN_FINISHED, PE.STAGE_ENTER))
           for where in (None, lambda e: e.get("stage"))]


class LatestIsTheFirstMatchOfTheWalk(unittest.TestCase):
    """`ProgressLog.latest` inside the scope (an index the readers share) against the walk it
    replaces, for every run (one absent, a non-str one), both type sets, with and without the
    stage predicate, every cap, queries in an order that builds the index shallow then deep, and
    appends landing between queries."""

    def _walk_answer(self, log, rid, types, where, cap):
        with mock.patch.object(SCOPE, "SHARED", None):
            for e in ProgressLog(log.path).read_events_backward(byte_cap=cap):
                if e.get("run_id") == rid and e.get("type") in types and \
                        (where is None or where(e)):
                    return e
        return None

    def test_every_query_every_cap(self):
        import random
        rnd = random.Random(11)
        log = _Log(self, _stage_log(rnd))
        caps = (None, 0, 300, 5000, 64 * 1024, log.size(), log.size() + 1)
        for order in (list(QUERIES), list(reversed(QUERIES))):
            with shared_backward_reads():
                for cap in caps:
                    for rid, types, where in order:
                        with self.subTest(rid=rid, types=types, where=where is not None, cap=cap):
                            self.assertEqual(self._walk_answer(log, rid, types, where, cap),
                                             ProgressLog(log.path).latest(rid, types, cap, where))

    def test_appends_between_queries_and_a_walk_in_flight(self):
        import random
        rnd = random.Random(12)
        log = _Log(self, _stage_log(rnd, 400))
        with mock.patch.object(SCOPE, "SHARED", None):
            before = log.plain(None)                     # the log the walk below begins on
        with shared_backward_reads():
            walker = ProgressLog(log.path).read_events_backward(byte_cap=None)
            first = next(walker)
            for k in range(8):
                rid = ("r1", "r2", "r9")[k % 3]
                ev = {"event_id": f"n{k}", "ts": "2030-01-01T00:00:00Z", "type": PE.STAGE_ENTER,
                      "stage": ("", "ship")[k % 2], "run_id": rid, "data": {}}
                log.write(json.dumps(ev) + "\n" + ("garbage\n" if k == 5 else ""), mode="ab")
                for qrid, types, where in QUERIES:
                    with self.subTest(k=k, rid=qrid, types=types, where=where is not None):
                        self.assertEqual(self._walk_answer(log, qrid, types, where, None),
                                         ProgressLog(log.path).latest(qrid, types, None, where))
            rest = list(walker)                          # the walk sees the log it began on
        self.assertEqual(before, [first] + rest)

    def test_replaced_underneath(self):
        import random
        log = _Log(self, _stage_log(random.Random(13), 300))
        with shared_backward_reads():
            for rid, types, where in QUERIES:
                ProgressLog(log.path).latest(rid, types, None, where)
            tmp = log.path + ".new"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(_stage_log(random.Random(14), 500))
            os.replace(tmp, log.path)
            for rid, types, where in QUERIES:
                with self.subTest(rid=rid, types=types, where=where is not None):
                    self.assertEqual(self._walk_answer(log, rid, types, where, None),
                                     ProgressLog(log.path).latest(rid, types, None, where))

    def test_the_readers_ask_latest_and_answer_as_before(self):
        """`current_stage` — the reader with a predicate — against its old loop, on every run."""
        import random
        log = _Log(self, _stage_log(random.Random(15)))
        sd = os.path.dirname(log.path)
        for rid in ("r1", "r2", "r3", "r9"):
            want = "develop"
            with mock.patch.object(SCOPE, "SHARED", None):
                for e in ProgressLog(log.path).read_events_backward():
                    if e.get("type") == PE.STAGE_ENTER and e.get("run_id") == rid and \
                            e.get("stage"):
                        want = str(e["stage"])
                        break
            with shared_backward_reads():
                with self.subTest(rid=rid):
                    self.assertEqual(want, PE.current_stage(sd, rid))


REFERENCE_SCAN_LOG = r'''
def _scan_log(root: str, runs: Dict[str, "_Run"], rd: _Read) -> None:
    """ONE backward read of the shared progress log for every run in the batch. Per run: its
    newest `review_verdict`, whether a `run_finished` exists, and whether the scan reached the
    run's START (its checkpoint `created_at`, minus skew) — only then is "no verdict" / "not
    finished" ABSENT rather than unknown. A corrupt line inside the window, an unreadable log or
    the byte bound reached before a run's start are recorded per run as the reason; the ladder
    raises it only if it needs that fact."""
    from mokata.progress_events import (PROGRESS_EVENTS_FILENAME, REVIEW_VERDICT, RUN_FINISHED)
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


'''


def _reference_scan_log():
    """The derivation's log scan exactly as it was before round 4 (c768125, uncached), to grade
    round 4's per-line fast path against the code it replaced, not against itself."""
    from typing import Any, Dict
    from mokata.gate_blocks import epoch_of

    def _epoch(ts):
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            return float(ts)
        return epoch_of(ts)
    ns = {"os": os, "json": json, "Dict": Dict, "Any": Any, "_state_dir": H._state_dir,
          "_Unknown": H._Unknown, "SKEW": H.SKEW, "_BLOCK": H._BLOCK, "_epoch": _epoch,
          "_Read": H._Read, "_Run": object}
    exec(REFERENCE_SCAN_LOG, ns)
    return ns


class TheDerivationLoopIsTheOriginalLoop(unittest.TestCase):
    """`hints._scan_log` (round 4: settles nothing when no run can settle, skips `own` for lines
    that are neither a finish nor a verdict) against the original loop, on seeded random logs:
    several runs with no start / an early / a late start, timestamps on both sides of each start
    (ISO, numeric, missing, garbage), finishes and verdicts (with and without a dict body),
    damage, and every cap — uncached and inside the scope."""

    def _log(self, rnd, n):
        base = 1_900_000_000
        lines = []
        for i in range(n):
            k = rnd.random()
            if k < 0.01:
                lines.append("{torn")
                continue
            t = base + i * 10 + rnd.randint(-40, 40)
            ts = rnd.choice([
                __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(t)),
                t, None, "garbage"])
            typ = rnd.choice(["phase_progress"] * 6 + [PE.RUN_FINISHED, PE.REVIEW_VERDICT])
            data = rnd.choice([{}, {"passed": True}, "not a dict"])
            lines.append(json.dumps({"event_id": f"e{i}", "ts": ts, "type": typ,
                                     "run_id": rnd.choice(["r1", "r2", "r3", "rX", None]),
                                     "data": data}))
        return "\n".join(lines) + "\n", base

    def test_seeded_logs_every_cap(self):
        import random
        ref = _reference_scan_log()
        for seed in range(40):
            rnd = random.Random(seed)
            body, base = self._log(rnd, rnd.choice([50, 400, 2000]))
            log = _Log(self, body)
            created = {"r1": None, "r2": base + rnd.randint(0, 20000),
                       "r3": base + rnd.randint(0, 20000)}
            for cap in (log.size() + 1, rnd.randint(200, max(201, log.size()))):
                with mock.patch.object(H, "LOG_BYTE_CAP", cap):
                    ref["LOG_BYTE_CAP"] = cap
                    runs_w = {rid: SimpleNamespace(checkpoint=lambda c=c: {"created_at": c},
                                                   log={}) for rid, c in created.items()}
                    ref["_scan_log"](log.root, runs_w, H._Read())
                    want = {rid: r.log for rid, r in runs_w.items()}
                    for scoped in (False, True):
                        runs_g = {rid: SimpleNamespace(checkpoint=lambda c=c: {"created_at": c},
                                                       log={}) for rid, c in created.items()}
                        if scoped:
                            with shared_backward_reads():
                                H._scan_log(log.root, runs_g, H._Read())
                        else:
                            H._scan_log(log.root, runs_g, H._Read())
                        got = {rid: r.log for rid, r in runs_g.items()}
                        with self.subTest(seed=seed, cap=cap, scoped=scoped):
                            self.assertEqual(json.dumps(want, sort_keys=True, default=str),
                                             json.dumps(got, sort_keys=True, default=str))


class OneProcessSnapshotPerStart(unittest.TestCase):
    """The start's index row and its ancestry map read ONE process-table snapshot (two `ps -A`
    on macOS were ~50 ms of every start)."""

    def test_the_hook_takes_one_and_hands_it_to_both(self):
        from mokata import hook_cli, session_ancestry, session_index
        from mokata.init import init_repo
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = os.path.realpath(t.name)
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        snap = object()
        calls, got = [], {}

        def lookup():
            calls.append(1)
            return snap
        payload = {"session_id": "S1", "cwd": d, "source": "startup", "transcript_path": ""}
        with mock.patch.object(session_ancestry, "system_lookup", lookup), \
                mock.patch.object(session_index, "on_start",
                                  lambda *a, **k: got.__setitem__("index", k.get("lookup"))), \
                mock.patch.object(session_ancestry, "record_session",
                                  lambda *a, **k: got.__setitem__("ancestry", k.get("lookup"))), \
                mock.patch.object(hook_cli, "_payload_from_stdin", lambda: payload), \
                mock.patch.object(hook_cli, "_emit", lambda *a, **k: None):
            hook_cli.session_start_main([])
        self.assertEqual(1, len(calls))
        self.assertEqual({"index": snap, "ancestry": snap}, got)

    def test_the_start_row_hands_its_snapshot_to_the_process_probe(self):
        from mokata import session_index
        from mokata.init import init_repo
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = os.path.realpath(t.name)
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        snap, seen = object(), []
        with mock.patch.object(session_index, "claude_process",
                               lambda now=None, lookup=None: seen.append(lookup)):
            session_index.on_start(d, "S1", "startup", d, lookup=snap)
        self.assertEqual([snap], seen)

    def test_the_index_row_reads_the_snapshot_it_is_given(self):
        from mokata import session_ancestry, session_index
        with mock.patch.object(session_ancestry, "system_lookup",
                               side_effect=AssertionError("took its own snapshot")):
            self.assertIsNone(session_index.claude_process(0.0, lookup=lambda pid: None))


class TheScopePausesTheCollectorOnlyWhileOpen(unittest.TestCase):

    def test_restored_either_way(self):
        import gc
        was = gc.isenabled()
        try:
            for start in (True, False):
                (gc.enable if start else gc.disable)()
                with shared_backward_reads():
                    self.assertFalse(gc.isenabled())
                self.assertEqual(start, gc.isenabled())
        finally:
            (gc.enable if was else gc.disable)()


if __name__ == "__main__":
    unittest.main()
