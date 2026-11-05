"""0.0.27 P14 `ID-NEXT-STEP-HINTS` — the next step, derived from what mokata recorded, never guessed.

Business rules — what must be true for the user (rule note: P14.rule.md, REVISION 1 + the
coordinator's final rulings):
  * Every hint names the recorded fact it came from; a hint with no fact cannot exist.
  * Deriving a hint writes nothing (not even an SQLite side file), spawns nothing, asks no model.
  * "Nothing owed" is said ONLY for a run recorded finished; a store that cannot be read, or a read
    that hit its bound, says UNKNOWN with the store named — never "nothing to do".
  * Two hints never contradict each other: never "develop" while writes are blocked, never "ship"
    beside "re-run" / "failing" / "untested ACs".
  * A failing test is not "failing" once something was written after its run: it is "re-run".
  * A hint is for THIS run: another run's proposals, gates and verdicts never hint here, and with
    two live runs and nothing tying the caller to one, the answer is "pick one".
  * The cost tracks the run's recorded state, never the repository's size.
  * The CLI, the MCP tool, the IDE route and bare `mokata` give the same next step.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import itertools
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval, hints as H
from mokata import session_index as SI
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.events import emit
from mokata.events.schema import GateDecision, ToolCall, TokenSpend
from mokata.events.store import store_for_root
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.progress_events import (PROGRESS_EVENTS_FILENAME, REVIEW_VERDICT, RUN_FINISHED,
                                    ProgressLog)
from mokata.state import StateStore
from mokata.tdd_state import state_dir
from mokata.test_runs import record_test_run

RUN = "R-p14"
SID = "sess-p14-0001"
T1 = "tests/test_a.py::test_one"
T2 = "tests/test_a.py::test_two"


def _silent(*_a, **_k):
    pass


class World:
    """A real initialised repo whose recorded state a test builds with mokata's own writers."""

    def __init__(self, tc: unittest.TestCase, run: str = RUN):
        self.d = tempfile.mkdtemp(prefix="p14-")
        tc.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)
        init_repo(root=self.d, profile="standard", assume_yes=True, out=_silent)
        self.run = run
        self.store = StateStore(state_dir(self.d))
        PipelineCheckpoint(self.store, run).ensure_registered()
        # the run began an hour ago, so facts stamped "a minute ago" are inside its window
        self.store.update(f"pipeline_run__{run}",
                          lambda cur: dict(cur, created_at=time.time() - 3600), default={})
        os.makedirs(os.path.join(self.d, "tests"), exist_ok=True)
        with open(os.path.join(self.d, "tests", "test_a.py"), "w", encoding="utf-8") as fh:
            fh.write("def test_one():\n    assert 1\n\n\ndef test_two():\n    assert 1\n")

    # --- the pipeline facts
    def approve(self):
        self.store.write(f"approved_approach__{self.run}", {
            "topic": "p14", "approach": {"name": "A"}, "approver": "jas",
            "approved_at": "2026-10-31T00:00:00Z"})
        return self

    def spec(self, *acs):
        acs = acs or ("AC-1", "AC-2")
        self.store.write(f"emitted_spec__{self.run}", Spec(
            title="p14", criteria=[AcceptanceCriterion(a, "x") for a in acs]).to_dict())
        return self

    def plan(self, mapping):
        def mut(cur):
            cur = dict(cur or {})
            cur.update(run_id=self.run, planned=mapping)
            return cur
        self.store.update(f"test_runs__{self.run}", mut, default=None)
        return self

    def test_run(self, passed, tests, at=None, ac_map=None):
        record_test_run(self.d, self.run, passed=passed, command="pytest", tests=tests,
                        ac_map=ac_map, at=at)
        return self

    def verdict(self, passed=True, ts=None):
        self._log(REVIEW_VERDICT, "review", {"passed": passed, "independent": True}, ts)
        return self

    def finish(self, ts=None):
        self._log(RUN_FINISHED, "ship", {"landing": "merged"}, ts)
        return self

    def _log(self, typ, stage, data, ts=None):
        if ts is None:
            ProgressLog.from_state_dir(state_dir(self.d)).append_event(typ, stage,
                                                                       run_id=self.run, data=data)
            return
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        with open(os.path.join(state_dir(self.d), PROGRESS_EVENTS_FILENAME), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({"event_id": f"e{ts}", "ts": stamp, "schema_version": 1,
                                 "type": typ, "stage": stage, "run_id": self.run,
                                 "data": data}) + "\n")

    def write_after(self, t, session=SID, path="src/a.py", run=None):
        p = H.timeline_path(self.d)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": f"s{t}", "t": t, "session": session,
                                 "run": self.run if run is None else run, "path": path,
                                 "kind": "after", "sha": "", "state": "ok", "size": 1}) + "\n")
        return self

    def gate(self, decision, gate="tdd", subject="src/a.py", session=SID, run=None):
        emit(self.d, GateDecision(gate=gate, decision=decision, subject=subject),
             session_id=session, run_id=self.run if run is None else run)
        return self

    def live(self, sid=SID):
        SI.on_start(self.d, sid, "startup", self.d, proc=None)
        return self

    def ended(self, sid=SID):
        SI.on_start(self.d, sid, "startup", self.d, proc=None)
        SI.on_end(self.d, sid, "exit")
        return self

    def green_and_reviewed(self):
        now = time.time()
        self.approve().spec("AC-1").test_run(False, [T1], at=now - 300, ac_map={"AC-1": [T1]})
        self.test_run(True, [T1], at=now - 200)
        self.verdict(True, ts=now - 100)
        return self

    def derive(self, **kw):
        kw.setdefault("run", self.run)
        return H.derive(self.d, **kw)


def rules(ans):
    return [h["rule"] for h in ans["hints"]]


def snapshot_tree(root):
    """Every file under `.mokata/` with its bytes' identity — what "writes nothing" is checked on."""
    out = {}
    base = os.path.join(root, ".mokata")
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            p = os.path.join(dp, fn)
            st = os.stat(p)
            with open(p, "rb") as fh:
                out[p] = (st.st_size, st.st_mtime_ns, hash(fh.read()))
    return out


# =========================================================================== each rule's fact

class EachHintNamesTheRecordedFactItCameFrom(unittest.TestCase):
    def test_each_hint_names_the_recorded_fact_it_came_from(self):
        """AC1 — every rung fires from its fact alone, and the hint cites that fact."""
        now = time.time()
        cases = []
        w = World(self)
        cases.append(("approach", w, "approved_approach__"))
        w = World(self).approve()
        cases.append(("spec", w, "emitted_spec__"))
        w = World(self).approve().spec("AC-1", "AC-2").plan({"AC-1": [T1]})
        cases.append(("unmapped-acs", w, f"{RUN}:AC-2"))
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        cases.append(("never-run", w, "no outcome"))
        w = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 50,
                                                       ac_map={"AC-1": [T1]})
        cases.append(("failing", w, "test_one"))
        w = World(self).approve().spec("AC-1").test_run(True, [T1], at=now - 50,
                                                       ac_map={"AC-1": [T1]})
        cases.append(("review", w, "review_verdict"))
        w = World(self).green_and_reviewed()
        cases.append(("ship", w, "review_verdict:"))
        w = World(self).green_and_reviewed().finish()
        cases.append(("finished", w, "run_finished"))
        for rule, world, cite in cases:
            with self.subTest(rule=rule):
                ans = world.derive()
                self.assertIn(rule, rules(ans), ans)
                h = [x for x in ans["hints"] if x["rule"] == rule][0]
                self.assertTrue(any(cite in c["ref"] for c in h["cites"]), h["cites"])

    def test_a_proposal_waiting_on_you_is_named_by_id_and_tool_only(self):
        """R2 + AC24 — named by id, tool and the command that resolves it; never its content."""
        w = World(self)
        p = approval.propose(w.d, tool="memory_write", args={"x": 1}, run_id=RUN,
                             summary="PLANTED-SUMMARY-hunter2", preview="PLANTED-PREVIEW-sk")
        ans = w.derive()
        h = [x for x in ans["hints"] if x["rule"] == "approve"][0]
        self.assertEqual(f"mokata approve {p.proposal_id}", h["command"])
        self.assertNotIn("PLANTED", json.dumps(ans))

    def test_the_amended_spec_owes_red(self):
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        w.store.write(f"spec_amend__{RUN}", {"status": "closed", "from_version": 1,
                                             "to_version": 2, "red_owed": [T2]})
        self.assertEqual(["red-owed"], rules(w.derive()))

    def _adopt_graph(self):
        w = World(self)
        man = os.path.join(w.d, ".mokata", "manifest.json")
        with open(man, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("capabilities", {}).setdefault("code_graph", {})["fallback"] = [
            "code-review-graph", "ast"]
        with open(man, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return w

    def _env(self, w, *, binary, dist=None, override=None):
        """A fake environment: `binary` puts an executable code-review-graph in its scripts dir;
        `dist` a `code_review_graph-<dist>.dist-info` in its site-packages; `override` sets
        MOKATA_CRG ("off", or "named" for an executable script outside the env)."""
        bindir, site = os.path.join(w.d, "envbin"), os.path.join(w.d, "site")
        os.makedirs(bindir, exist_ok=True)
        os.makedirs(site, exist_ok=True)

        def exe(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        if binary:
            exe(os.path.join(bindir, "code-review-graph" + (".exe" if os.name == "nt" else "")))
        if dist:
            os.makedirs(os.path.join(site, f"code_review_graph-{dist}.dist-info"))
        env = {k: v for k, v in os.environ.items() if k != "MOKATA_CRG"}
        if override == "off":
            env["MOKATA_CRG"] = "off"
        elif override == "named":
            named = os.path.join(w.d, "crg-named")
            exe(named)
            env["MOKATA_CRG"] = named
        return [mock.patch.dict(os.environ, env, clear=True),
                mock.patch("mokata.detect.env_scripts_dirs", return_value=(bindir,)),
                mock.patch("mokata.crg_resolve._site_dirs", return_value=[site]),
                mock.patch("subprocess.run", side_effect=AssertionError("no spawn")),
                mock.patch("subprocess.Popen", side_effect=AssertionError("no spawn")),
                mock.patch("mokata.knowledge.index.KnowledgeIndex.from_dict",
                           side_effect=AssertionError("the index must never be parsed"))]

    def test_the_hint_and_the_resolver_agree_on_every_case(self):
        """INTEG-FIX2 r2 — "run graph build" is said EXACTLY when `resolve_crg` would run the tool;
        every refusal it makes is a refusal here; a MOKATA_CRG binary (whose version only a spawn
        could check) is "cannot confirm", never "run graph build". No case spawns."""
        from contextlib import ExitStack
        from mokata import crg_resolve
        cases = [  # (name, env kwargs, the hint rule, resolve_crg().ok or None when it would spawn)
            ("not installed", dict(binary=False), "graph-tool-missing", False),
            ("binary, no dist-info", dict(binary=True), "graph-tool-missing", False),
            ("binary, version in range", dict(binary=True, dist="2.3.9"), "graph-unbuilt", True),
            ("binary, version out of range", dict(binary=True, dist="9.9.9"),
             "graph-tool-missing", False),
            ("binary, version too old", dict(binary=True, dist="1.0.0"),
             "graph-tool-missing", False),
            ("switched off", dict(binary=True, dist="2.3.9", override="off"), None, False),
            ("named by MOKATA_CRG", dict(binary=False, override="named"),
             "graph-tool-missing", None),
        ]
        for name, kw, want, resolver_ok in cases:
            with self.subTest(case=name):
                w = self._adopt_graph()
                with ExitStack() as st:
                    for p in self._env(w, **kw):
                        st.enter_context(p)
                    ans = w.derive()
                    if resolver_ok is not None:
                        self.assertEqual(resolver_ok, crg_resolve.resolve_crg().ok)
                graph = [h for h in ans["hints"] if h["rule"].startswith("graph")]
                self.assertEqual([want] if want else [], [h["rule"] for h in graph], graph)
                for h in graph:
                    self.assertEqual("graph-unbuilt" == h["rule"],
                                     h["command"] == "mokata graph build")
                if name == "named by MOKATA_CRG":
                    self.assertIn("cannot be confirmed", graph[0]["text"])
                if name == "not installed":
                    self.assertIn("not installed", graph[0]["text"])
                    self.assertIn("mokata[full]", graph[0]["text"])


class AHintWithNoCiteIsRefused(unittest.TestCase):
    def test_a_hint_with_no_cited_fact_is_refused(self):
        """AC2 — a hint is derived, never guessed: no cite, no hint."""
        with self.assertRaises(H.HintRefused):
            H.Hint("review", "review it", "/mokata:review", ())


# =========================================================================== never writes

class DerivingWritesNothing(unittest.TestCase):
    def _busy(self):
        w = World(self).green_and_reviewed().live()
        w.gate("allowed")
        emit(w.d, ToolCall(tool="Bash", surface="claude-code"), session_id=SID, run_id=RUN)
        approval.propose(w.d, tool="memory_write", args={"y": 2}, run_id=RUN)
        return w

    def test_deriving_hints_spawns_nothing_and_asks_no_model(self):
        """AC3 — no process, no network, no git."""
        w = self._busy()
        boom = AssertionError("derive must not spawn")
        with mock.patch("subprocess.Popen", side_effect=boom), \
                mock.patch("subprocess.run", side_effect=boom), \
                mock.patch("socket.socket", side_effect=AssertionError("no network")):
            ans = w.derive(chain=(SID,))
        self.assertIn(ans["status"], ("hints", "none"))

    def test_deriving_hints_writes_nothing_even_to_sqlite_side_files(self):
        """AC4 — `.mokata/` byte-identical after a derive, with the event store in WAL mode,
        both when its side files are absent and when they are present."""
        w = self._busy()
        db = store_for_root(w.d).path
        for setup in ("side files absent", "side files present"):
            with self.subTest(setup=setup):
                held = None
                if setup == "side files present":
                    held = sqlite3.connect(db)            # a writer holding the WAL open
                    held.execute("PRAGMA journal_mode=WAL")
                    held.execute("SELECT count(*) FROM events").fetchone()
                else:
                    for ext in ("-wal", "-shm"):
                        self.assertFalse(os.path.exists(db + ext), "setup: no side files")
                before = snapshot_tree(w.d)
                w.derive(chain=(SID,))
                H.derive(w.d)
                self.assertEqual(before, snapshot_tree(w.d))
                if held is not None:
                    held.close()

    def test_a_plain_readonly_open_creates_wal_side_files_the_probe(self):
        """The measured fact the immutable open exists for (kept as a fixture, not a claim)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        db = os.path.join(d, "e.db")
        c = sqlite3.connect(db)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()
        self.assertFalse(os.path.exists(db + "-wal"))
        from mokata.memory._sqlite import connect_sqlite_readonly
        r = connect_sqlite_readonly(db, immutable=True)
        r.execute("SELECT count(*) FROM t").fetchone()
        r.close()
        self.assertFalse(os.path.exists(db + "-shm"), "an immutable open creates nothing")
        r = connect_sqlite_readonly(db)
        r.execute("SELECT count(*) FROM t").fetchone()
        r.close()
        if not os.path.exists(db + "-shm"):
            self.skipTest("this SQLite build does not create side files on a mode=ro open")
        self.assertTrue(os.path.exists(db + "-shm"), "plain mode=ro created a side file")


class AWriterMidReadNeverGivesATornAnswer(unittest.TestCase):
    def test_a_writer_starting_mid_read_never_gives_a_torn_answer(self):
        """AC25 — a writer that starts between the before-signature and the query makes the read
        retry; if it keeps changing, the answer is UNKNOWN — never the old rows labelled current."""
        w = World(self)
        w.gate("blocked")
        es = store_for_root(w.d)

        def writer():
            c = sqlite3.connect(es.path)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("INSERT INTO events (event_id, ts, schema_version, type, session_id, "
                      "run_id, data) VALUES (?, '2026-10-31T00:00:00Z', 1, 'gate_decision', ?, "
                      "?, '{\"decision\": \"allowed\"}')", (f"x{time.time_ns()}", SID, RUN))
            c.commit()
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        r = es.read_run_readonly(run_id=RUN, types=["gate_decision"], _between=writer)
        self.assertTrue(r.fault, "two moving reads must be UNKNOWN, not rows")
        self.assertEqual([], r.rows)
        once = {"n": 0}

        def writer_once():
            if once["n"] == 0:
                writer()
            once["n"] += 1
        r = es.read_run_readonly(run_id=RUN, types=["gate_decision"], _between=writer_once)
        self.assertFalse(r.fault)
        self.assertEqual("allowed", r.rows[0].data.get("decision"),
                         "the retry reads the NEW truth")


# ===================================================================== unknown, never "nothing"

class EachUnreadableInputSaysUnknown(unittest.TestCase):
    def test_each_unreadable_input_says_unknown_naming_it(self):
        """AC6 — every store the ladder reads, damaged, gives UNKNOWN naming it; never `none`."""
        def corrupt(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{torn")

        def mk_approach(w):
            corrupt(w.store.path(f"approved_approach__{RUN}"))

        def mk_spec(w):
            w.approve()
            corrupt(w.store.path(f"emitted_spec__{RUN}"))

        def mk_tests(w):
            w.approve().spec("AC-1")
            corrupt(w.store.path(f"test_runs__{RUN}"))

        def mk_amend(w):
            corrupt(w.store.path(f"spec_amend__{RUN}"))

        def mk_checkpoint(w):
            corrupt(w.store.path(f"pipeline_run__{RUN}"))

        def mk_log(w):
            w.approve().spec("AC-1").test_run(True, [T1], at=time.time() - 9,
                                              ac_map={"AC-1": [T1]})
            with open(os.path.join(state_dir(w.d), PROGRESS_EVENTS_FILENAME), "a",
                      encoding="utf-8") as fh:
                fh.write('{"type": "review_verd')

        def mk_timeline(w):
            w.green_and_reviewed()
            p = H.timeline_path(w.d)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{torn\n")

        def mk_proposal(w):
            corrupt(os.path.join(state_dir(w.d), "write_proposal__p-deadbeef0000.json"))

        def mk_events(w):
            w.gate("blocked")
            with open(store_for_root(w.d).path, "wb") as fh:
                fh.write(b"not a database at all" * 100)

        for name, mk, needle in [("approach", mk_approach, "approved approach"),
                                 ("spec", mk_spec, "emitted spec"),
                                 ("test record", mk_tests, "test record"),
                                 ("amendment", mk_amend, "spec-amendment"),
                                 ("checkpoint", mk_checkpoint, "checkpoint"),
                                 ("progress log", mk_log, "progress log"),
                                 ("timeline", mk_timeline, "timeline"),
                                 ("proposal", mk_proposal, "proposal"),
                                 ("event store", mk_events, "event store")]:
            with self.subTest(store=name):
                w = World(self)
                mk(w)
                ans = w.derive()
                self.assertEqual("unknown", ans["status"], ans)
                self.assertIn(needle, ans["reason"])
                self.assertEqual([], ans["hints"])
                self.assertNotIn("no suggestions", H.render(ans))


class AFactBeyondAReadBoundIsUnknown(unittest.TestCase):
    def test_a_fact_beyond_a_read_cap_is_unknown_not_absent(self):
        """AC7 — the progress log's bound reached before the run's start: UNKNOWN, not "no review"."""
        w = World(self).approve().spec("AC-1")
        w.test_run(True, [T1], at=time.time() - 10, ac_map={"AC-1": [T1]})
        with mock.patch.object(H, "LOG_BYTE_CAP", 64):
            for i in range(50):
                ProgressLog.from_state_dir(state_dir(w.d)).append_event(
                    "stage_enter", "develop", run_id="R-other", data={"i": i})
            ans = w.derive()
        self.assertEqual("unknown", ans["status"], ans)
        self.assertIn("read bound", ans["reason"])

    def test_an_old_block_superseded_by_many_passes_is_settled(self):
        """A gate block older than a full page of later passes is SUPERSEDED (settled, no hint);
        a block that is the newest decision after them is a hint — the page size never decides."""
        w = World(self).approve().live()
        w.gate("blocked")
        for _ in range(H.EVENT_ROWS + 20):
            w.gate("allowed")
        self.assertNotIn("gate-block", rules(w.derive(chain=(SID,))))
        w.gate("blocked")
        self.assertIn("gate-block", rules(w.derive(chain=(SID,))))


class NoUnfinishedStateReadsNothingOwed(unittest.TestCase):
    def test_no_unfinished_state_reads_nothing_owed(self):
        """AC8 — `none` only from a recorded finish; every other state has a hint."""
        now = time.time()
        states = {
            "fresh": World(self),
            "approved": World(self).approve(),
            "spec, no test": World(self).approve().spec("AC-1"),
            "planned, never run": World(self).approve().spec("AC-1").plan({"AC-1": [T1]}),
            "red": World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 9,
                                                               ac_map={"AC-1": [T1]}),
            "green": World(self).approve().spec("AC-1").test_run(True, [T1], at=now - 9,
                                                                 ac_map={"AC-1": [T1]}),
            "reviewed": World(self).green_and_reviewed(),
        }
        for name, w in states.items():
            with self.subTest(state=name):
                ans = w.derive()
                self.assertEqual("hints", ans["status"], ans)
                self.assertTrue(ans["hints"])
        fin = World(self).green_and_reviewed().finish().derive()
        self.assertEqual("none", fin["status"])
        self.assertIn("finished", fin["reason"])


# ================================================================ never contradictory

CONFLICTS = [{"develop", "ship"}, {"re-run", "ship"}, {"develop", "writes-blocked"},
             {"test", "ship"}, {"review", "test"}, {"ship", "writes-blocked"},
             {"re-run", "develop"}]


class NoTwoHintsConflict(unittest.TestCase):
    def test_no_two_hints_give_conflicting_advice(self):
        """AC9 — over the product of the recorded facts, no answer carries two conflicting pieces
        of advice (the ladder is first-match; tier B suppresses tier C)."""
        now = time.time()
        facts = list(itertools.product([False, True],   # amendment open
                                       [None, "red", "green"],   # last outcome
                                       [False, True],   # a write after the run
                                       [None, True, False],   # review verdict passed?
                                       [False, True]))  # a live gate block
        for amend, outcome, wrote, review, block in facts:
            with self.subTest(amend=amend, outcome=outcome, wrote=wrote, review=review,
                              block=block):
                w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
                if outcome:
                    w.test_run(outcome == "green", [T1], at=now - 100)
                if wrote:
                    w.write_after(now - 50)
                if review is not None:
                    w.verdict(review, ts=now - 80)
                if amend:
                    w.store.write(f"spec_amend__{RUN}", {"status": "open",
                                                         "from_version": 1, "to_version": 2})
                if block:
                    w.live().gate("blocked")
                ans = w.derive(chain=(SID,))
                advice = {h["advice"] for h in ans["hints"]}
                for pair in CONFLICTS:
                    self.assertFalse(pair <= advice, (pair, ans["hints"]))
                pipeline = [h for h in ans["hints"] if h["rule"] not in ("approve",
                                                                         "graph-unbuilt")]
                self.assertLessEqual(len(pipeline), 1, "one pipeline hint at most")
                shutil.rmtree(w.d, ignore_errors=True)

    def test_blocked_writes_are_said_not_develop(self):
        """AC10 — an open amendment or a live gate block says writes are blocked, never develop."""
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 9,
                                                       ac_map={"AC-1": [T1]})
        w.store.write(f"spec_amend__{RUN}", {"status": "open", "from_version": 1,
                                             "to_version": 2})
        ans = w.derive()
        self.assertEqual(["amend-open"], rules(ans))
        self.assertIn("blocked", ans["hints"][0]["text"])
        w2 = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 9,
                                                        ac_map={"AC-1": [T1]}).live()
        w2.gate("blocked", gate="red-before-green")
        ans = w2.derive(chain=(SID,))
        self.assertEqual(["gate-block"], rules(ans))
        self.assertNotIn("develop", [h["advice"] for h in ans["hints"]])


# ======================================================================= failing goes stale

class FailingIsInvalidatedByALaterWrite(unittest.TestCase):
    def test_failing_is_invalidated_by_a_later_write(self):
        """AC11 — red, then a write: re-run (naming the test), not "failing"; red then green is
        never "failing"; a test-file edit (its stamp moved) is a write too."""
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 100,
                                                       ac_map={"AC-1": [T1]})
        self.assertEqual(["failing"], rules(w.derive()))
        w.write_after(now - 10)
        ans = w.derive()
        self.assertEqual(["rerun"], rules(ans))
        self.assertIn("test_one", ans["hints"][0]["text"])
        w2 = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 100,
                                                        ac_map={"AC-1": [T1]})
        w2.test_run(True, [T1], at=now - 50)
        self.assertNotIn("failing", rules(w2.derive()))
        w3 = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 100,
                                                        ac_map={"AC-1": [T1]})
        with open(os.path.join(w3.d, "tests", "test_a.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef helper():\n    return 2\n")
        with open(os.path.join(w3.d, "tests", "test_a.py"), "w", encoding="utf-8") as fh:
            fh.write("def test_one():\n    assert 2\n\n\ndef test_two():\n    assert 1\n")
        self.assertEqual(["rerun"], rules(w3.derive()))

    def test_a_partial_rerun_still_names_the_rest(self):
        """Ruling 3 — T is the last SUITE run; re-running one of several tests after a write names
        the others; re-running all clears it."""
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(
            False, [T1, T2], at=now - 100, ac_map={"AC-1": [T1, T2]})
        w.write_after(now - 50)
        w.test_run(False, [T1], at=now - 40)
        w.write_after(now - 30)
        ans = w.derive()
        self.assertEqual(["rerun"], rules(ans))
        self.assertIn("test_two", ans["hints"][0]["text"])
        w.test_run(False, [T1, T2], at=now - 5)
        self.assertEqual(["failing"], rules(w.derive()))


class TheCutoffIsTheLastSuiteRun(unittest.TestCase):
    def test_a_write_before_the_last_suite_run_is_not_owed_a_rerun(self):
        """Ruling 3 — T is `last_suite.at`, NOT the oldest test's own last run. test_two last ran
        long ago; a write; then a suite run of test_one only. The write is BEFORE T: nothing is
        owed a re-run (under T = min(last_at) it would wrongly say re-run test_two)."""
        now = time.time()
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1, T2]})
        w.test_run(True, [T2], at=now - 1000)
        w.write_after(now - 500)
        w.test_run(True, [T1], at=now - 100)
        ans = w.derive()
        self.assertNotIn("rerun", rules(ans), ans["hints"])
        self.assertEqual(["review"], rules(ans))

    def test_a_write_after_the_suite_names_exactly_the_tests_not_rerun_since(self):
        """Run A (both tests) at T1, a write, run B (test_one only) at T2, another write: the hint
        names test_two — the one not re-run since the write — and test_one too, since the second
        write came after T2; with the second write BEFORE run B only test_two is behind."""
        now = time.time()
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1, T2]})
        w.test_run(True, [T1, T2], at=now - 1000)
        w.write_after(now - 500)
        w.test_run(True, [T1], at=now - 100)
        w.write_after(now - 50)
        text = w.derive()["hints"][0]["text"]
        self.assertIn("test_two", text)
        self.assertIn("test_one", text)


class BashWritesAreCountedOrDisclosed(unittest.TestCase):
    def test_bash_writes_are_counted_or_disclosed(self):
        """AC12 — bridge on: a Bash call after the last run means re-run; bridge off: every later
        hint SAYS Bash writes are not tracked (never implied current)."""
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(True, [T1], at=now - 100,
                                                       ac_map={"AC-1": [T1]})
        ans = w.derive()
        self.assertEqual(["review"], rules(ans))
        self.assertIn("Bash", ans["hints"][0]["text"])
        self.assertIn("unobserved", ans["basis"]["bash"])
        emit(w.d, ToolCall(tool="Bash", surface="claude-code"), session_id=SID, run_id=RUN)
        with mock.patch.object(H, "_bridge", return_value=(True, "")):
            ans = w.derive(chain=(SID,))
        self.assertEqual(["rerun"], rules(ans))
        self.assertIn("Bash", ans["hints"][0]["text"])


class TimelineCapSaysCannotConfirm(unittest.TestCase):
    def test_timeline_cap_says_cannot_confirm_then_clears(self):
        """AC13 — a pruned timeline that starts after the last run cannot settle "no write since":
        it says so, and a re-run (T moves inside the window) clears it."""
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(True, [T1], at=now - 1000,
                                                       ac_map={"AC-1": [T1]})
        with mock.patch.object(H, "TIMELINE_KEEP", 5):
            for i in range(6):
                w.write_after(now - 500 + i, session="other", run="R-other")
            ans = w.derive()
            self.assertEqual(["rerun-unsure"], rules(ans), ans)
            w.test_run(True, [T1], at=now - 5)
            self.assertNotIn("rerun-unsure", rules(w.derive()))

    def test_the_timeline_bound_matches_the_writer(self):
        from mokata.ide import snapshots as S
        self.assertEqual(S.MAX_ENTRIES, H.TIMELINE_KEEP)


# ============================================================================ blocks age out

class OnlyTheSameGateSupersedesItsBlock(unittest.TestCase):
    def test_a_later_pass_of_the_same_gate_clears_the_block(self):
        w = World(self).approve().live()
        w.gate("blocked", gate="tdd")
        w.gate("allowed", gate="tdd")
        self.assertNotIn("gate-block", rules(w.derive(chain=(SID,))))

    def test_another_gates_pass_never_hides_a_block(self):
        """Low L1 — a secret block is not hidden by the next unrelated allowed write decision."""
        w = World(self).approve().live()
        w.gate("blocked", gate="write-secret", subject="config.py")
        w.gate("allowed", gate="tdd")
        self.assertEqual(["gate-block"], rules(w.derive(chain=(SID,))))


class BlocksAgeOut(unittest.TestCase):
    def test_an_ended_sessions_block_ages_out_to_resume(self):
        """AC14 — the same block in an ended session is "resume", with its age."""
        w = World(self).approve().ended()
        w.gate("blocked")
        ans = w.derive(chain=(SID,))
        self.assertEqual(["gate-block-ended"], rules(ans))
        self.assertIn("ago", ans["hints"][0]["text"])
        self.assertEqual("mokata resume", ans["hints"][0]["command"])

    def test_a_finished_runs_old_block_is_history(self):
        """Ruling 4 — C0 outranks R3e: a finished run's ended-session block is history."""
        w = World(self).green_and_reviewed().ended()
        w.gate("blocked")
        w.finish()
        ans = w.derive(chain=(SID,))
        self.assertEqual("none", ans["status"])

    def test_a_write_after_the_block_clears_it(self):
        w = World(self).approve().live()
        w.gate("blocked")
        w.write_after(time.time() + 5)
        self.assertNotIn("gate-block", rules(w.derive(chain=(SID,))))


# ================================================================================ identity

class Identity(unittest.TestCase):
    def test_wrong_window_run_never_hints_and_basis_is_shown(self):
        """AC15 — two live runs, a caller nothing ties to either: "pick one", no run's hints."""
        w = World(self, run="R-a").approve()
        PipelineCheckpoint(w.store, "R-b").ensure_registered()
        w.store.write("approved_approach__R-b", {"topic": "b", "approach": {"name": "B"}})
        with mock.patch("mokata.run_resolver.live_runs", return_value=["R-a", "R-b"]):
            ans = H.derive(w.d)
        self.assertEqual(["ambiguous"], rules(ans))
        self.assertEqual("", ans["subject"]["run"])
        out = H.render(H.derive(w.d, run="R-a"))
        self.assertIn("run picked by: explicit", out)

    def test_another_runs_facts_never_hint_here(self):
        """AC10 (identity) — another run's proposal, gate and verdict never hint for this run;
        a session-scoped proposal (run id = session id) is found through the chain."""
        w = World(self).approve().live()
        approval.propose(w.d, tool="memory_write", args={"o": 1}, run_id="R-other")
        w.gate("blocked", run="R-other", session="sess-other")
        self.assertEqual(["spec"], rules(w.derive(chain=(SID,))))
        approval.propose(w.d, tool="memory_write", args={"s": 1}, run_id=SID)
        self.assertIn("approve", rules(w.derive(chain=(SID,))))

    def test_one_test_spelled_two_ways_counts_once(self):
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(
            False, ["./tests/test_a.py::test_one", T1], at=now - 9,
            ac_map={"AC-1": ["./tests/test_a.py::test_one", T1]})
        ans = w.derive()
        self.assertIn("1 test(s) failing", ans["hints"][0]["text"])

    def test_linked_worktree_session_reads_the_shared_store(self):
        """AC17 — a session in a linked worktree reads the ONE shared store (P31)."""
        w = World(self).approve()
        subprocess.run(["git", "-C", w.d, "commit", "-q", "--allow-empty", "-m", "x"],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        wt = os.path.join(tempfile.mkdtemp(), "wt")
        self.addCleanup(shutil.rmtree, os.path.dirname(wt), ignore_errors=True)
        subprocess.run(["git", "-C", w.d, "worktree", "add", "-q", wt], check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(rules(w.derive()), rules(H.derive(wt, run=RUN)))


# ================================================================================= dismissal

class DismissalKey(unittest.TestCase):
    def test_dismissal_key_survives_unrelated_events_and_moves_with_its_own_fact(self):
        """AC18 (unit half) — another run's events and token spend never change a hint's key;
        its own fact changing does."""
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 50,
                                                       ac_map={"AC-1": [T1]})
        k1 = w.derive()["hints"][0]["key"]
        emit(w.d, TokenSpend(label="x", input_tokens=1, output_tokens=1), session_id=SID,
             run_id=RUN)
        ProgressLog.from_state_dir(state_dir(w.d)).append_event("stage_enter", "spec",
                                                                run_id="R-other")
        self.assertEqual(k1, w.derive()["hints"][0]["key"])
        w.test_run(False, [T1], at=now - 5)
        self.assertNotEqual(k1, w.derive()["hints"][0]["key"])


# ============================================================================ surfaces agree

class EverySurfaceAgrees(unittest.TestCase):
    def test_every_surface_gives_the_same_next_step_and_honest_status(self):
        """AC20 — CLI `suggest`, MCP `suggest` and bare `mokata` say the derived next step; an
        UNKNOWN is never rendered as "no suggestions"."""
        from mokata.cli import main as cli_main
        from mokata.mcp import tools_read as T
        w = World(self).approve()
        ans = H.derive(w.d)
        mcp = T.suggest(path=w.d)
        self.assertEqual(ans["hints"][0]["rule"], mcp["hints"][0]["rule"])
        self.assertEqual("recorded state", mcp["basis"]["source"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["suggest", "--path", w.d])
        self.assertIn(ans["hints"][0]["command"], buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--path", w.d])
        self.assertIn(ans["hints"][0]["command"], buf.getvalue())
        with open(w.store.path(f"emitted_spec__{RUN}"), "w", encoding="utf-8") as fh:
            fh.write("{torn")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["suggest", "--path", w.d])
        self.assertIn("unknown", buf.getvalue())
        self.assertNotIn("no suggestions", buf.getvalue())
        self.assertEqual("unknown", T.suggest(path=w.d)["status"])

    def test_the_flag_mode_says_its_basis(self):
        from mokata.mcp import tools_read as T
        w = World(self)
        self.assertEqual("as you described it", T.suggest(path=w.d, spec=True)["basis"])


class EachCardGetsItsOwnRunsHint(unittest.TestCase):
    def test_each_card_gets_its_own_runs_hint_and_an_unbound_card_borrows_none(self):
        """AC15/AC19 (IDE) — the panel's batch: each session's card is hinted from the run THAT
        session is bound to; a session bound to nothing says so — it never borrows the repo's run."""
        try:
            from mokata.ide.source import RealSource
        except ImportError:
            self.skipTest("the [ide] extra is not installed")
        from mokata import run_intent
        w = World(self, run="R-a").approve()
        PipelineCheckpoint(w.store, "R-b").ensure_registered()
        for sid in ("S-a", "S-b", "S-c"):
            SI.on_start(w.d, sid, "startup", w.d, proc=None)
        run_intent.bind_registered(w.d, "S-a", "R-a")
        run_intent.bind_registered(w.d, "S-b", "R-b")
        got = RealSource(w.d).hints(["S-a", "S-b", "S-c"])
        self.assertFalse(got["sample"])
        a, b, c = (got["answers"][k] for k in ("S-a", "S-b", "S-c"))
        self.assertEqual(("R-a", ["spec"]), (a["subject"]["run"], rules(a)))
        self.assertEqual(("R-b", ["approach"]), (b["subject"]["run"], rules(b)))
        self.assertEqual(["no-run"], rules(c))


class TheIdeIsToldWhenRunStateMoves(unittest.TestCase):
    def test_a_terminal_test_run_moves_the_runstate_fingerprint_for_its_run_only(self):
        """HIGH-1 (server half) — `mokata progress test-run` emits no event; the watcher's
        fingerprint of the run-state records sees it and names THAT run. A progress-log append (a
        verdict, a finish) names its run only inside, so it means "any run"."""
        try:
            from mokata.ide.server import _runstate_fp, runstate_moved
        except ImportError:
            self.skipTest("the [ide] extra is not installed")
        w = World(self)
        PipelineCheckpoint(w.store, "R-other").ensure_registered()
        a = _runstate_fp(w.d)
        self.assertEqual([], runstate_moved(a, _runstate_fp(w.d)))
        time.sleep(0.01)
        w.test_run(True, [T1])
        b = _runstate_fp(w.d)
        self.assertEqual([RUN], runstate_moved(a, b))
        w.verdict(True)
        self.assertIsNone(runstate_moved(b, _runstate_fp(w.d)))

    def test_the_hints_route_is_a_read_never_an_action(self):
        """AC5 — `/api/hints` is a GET `json` read; the feature registers no action."""
        try:
            from mokata.ide.features import hints as F
        except ImportError:
            self.skipTest("the [ide] extra is not installed")
        with open(os.path.join(os.path.dirname(F.__file__), "hints.contract.json"),
                  encoding="utf-8") as fh:
            routes = json.load(fh)["routes"]
        self.assertEqual([("/api/hints", "GET", "json")],
                         [(r["path"], r["method"], r["kind"]) for r in routes])
        seen = []

        class Reg:
            def register(self, path, fn, kind, feature):
                seen.append((path, kind))

        class Ctx:
            state = None
        F.routes(Reg(), Ctx())
        self.assertEqual([("/api/hints", "json")], seen)


class HomeNextLineComesFromTheHint(unittest.TestCase):
    def test_home_next_line_comes_from_the_hint(self):
        """AC21 — bare `mokata`'s run line keeps its words; its next pair is the primary hint; a
        run it cannot read says so and points at doctor."""
        from mokata.home import build_home
        w = World(self).approve()
        text, nxt = build_home(w.d)
        self.assertIn("run:", text)
        self.assertEqual("/mokata:spec", nxt[0])
        with open(w.store.path(f"emitted_spec__{RUN}"), "w", encoding="utf-8") as fh:
            fh.write("{torn")
        text, nxt = build_home(w.d)
        self.assertIn("could not be read", text)
        self.assertEqual("mokata doctor", nxt[0])


# ===================================================================================== scale

class CostTracksRunStateNotRepoSize(unittest.TestCase):
    def test_hints_cost_tracks_run_state_not_repo_size(self):
        """AC22 — on a repo with thousands of files, with the repo walk and `subprocess` patched to
        fail, derive still answers; nothing is walked."""
        w = World(self).green_and_reviewed()
        src = os.path.join(w.d, "src")
        for i in range(2000):
            sub = os.path.join(src, f"d{i % 40}")
            os.makedirs(sub, exist_ok=True)
            with open(os.path.join(sub, f"f{i}.py"), "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
        real_scandir = os.scandir

        def guarded(path="."):
            if os.path.abspath(str(path)).startswith(os.path.abspath(src)):
                raise AssertionError("derive walked the repository")
            return real_scandir(path)
        with mock.patch("os.walk", side_effect=AssertionError("derive walked the repository")), \
                mock.patch("os.scandir", side_effect=guarded), \
                mock.patch("subprocess.run", side_effect=AssertionError("no spawn")):
            ans = w.derive()
        self.assertEqual(["ship"], rules(ans))

    def test_one_batch_reads_each_shared_store_once(self):
        """AC23 — S subjects read the shared progress log once: the bytes read do not grow with S,
        and are reported in `basis.read`."""
        w = World(self).green_and_reviewed()
        log = ProgressLog.from_state_dir(state_dir(w.d))
        for i in range(3000):
            log.append_event("stage_enter", "develop", run_id=f"R-noise-{i % 7}")
        one = H.derive_batch(w.d, [H.Subject(run=RUN)])[0]["basis"]["read"]["log_bytes"]
        many = H.derive_batch(w.d, [H.Subject(run=RUN)] * 25)
        self.assertEqual(one, many[0]["basis"]["read"]["log_bytes"])
        self.assertGreater(one, 0)


# ================================================================================== content

class NeverQuotesSecrets(unittest.TestCase):
    def test_hint_never_quotes_a_secret_or_a_proposal(self):
        """AC24 — a secret block shows no matched content; a gate subject is clipped text."""
        w = World(self).approve().live()
        w.gate("blocked", gate="write-secret", subject="config.py AKIAPLANTEDSECRET")
        ans = w.derive(chain=(SID,))
        self.assertEqual(["gate-block"], rules(ans))
        self.assertNotIn("AKIAPLANTED", json.dumps(ans))
        self.assertIn("cannot be approved away", ans["hints"][0]["text"])
        w2 = World(self).approve().live()
        w2.gate("blocked", subject="<script>alert(1)</script>" + "x" * 400)
        text = w2.derive(chain=(SID,))["hints"][0]["text"]
        self.assertLess(len(text), 400)


class HintsNeedNoIde(unittest.TestCase):
    def test_importing_hints_loads_no_ide_module(self):
        """Ruling 2 — the hints serve CLI/MCP without the `[ide]` extra: importing them pulls in
        no `mokata.ide` module."""
        code = ("import sys; import mokata.hints, mokata.gate_blocks; "
                "bad=[m for m in sys.modules if m.startswith('mokata.ide')]; "
                "print(bad); sys.exit(1 if bad else 0)")
        env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))), "src"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_the_needs_you_queue_and_the_hints_share_one_block_rule(self):
        from mokata import gate_blocks
        from mokata.ide import session_views as SV
        self.assertIs(gate_blocks.BLOCKING, SV.BLOCKING)
        self.assertIs(gate_blocks.pending_block, SV._pending_block)


if __name__ == "__main__":
    unittest.main()
