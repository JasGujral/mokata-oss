"""0.0.28 lane D `TEST-RUN-NOT-RUN-LABELS` (ruling D-Q1) — a test run is exactly one of: passed /
failed / cancelled / not-all-ran.

Business rules — what must be true for the user, each:
  * One word per run, by one rule: any red -> failed; else any test that did not run -> not-all-ran;
    else a run cut short -> cancelled; else passed. `reason` says why whenever it is not passed.
  * "FAILED" is said only when a test (or the command) failed. A cancelled all-green run and a run
    whose tests could not start are never told as FAILED — by the Stop gate, the merge gate, the
    review panel or the proof route; all four say the SAME words (one reader, `suite_verdict`).
  * A green test is never shown red: the review panel colours a test by its OWN outcome.
  * A record written before the verdict existed is refused loudly (UNREADABLE) with what to do —
    "re-run the tests" — never read as passed or failed.
  * Recording a run of 10k tests is linear.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import test_runs as TR

A, B, C = "tests/test_a.py::test_a", "tests/test_b.py::test_b", "tests/test_c.py::test_c"


class _Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-d-verdict-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, ".mokata", "temp_local"), exist_ok=True)

        from _plan import sealed
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir
        StateStore(state_dir(self.root)).write("emitted_spec__R1", sealed(
            {"title": "t", "criteria": [{"id": "AC1", "text": "it works"}]}))

    def record(self, **kw):
        kw.setdefault("command", "pytest -q")
        kw.setdefault("ac_map", {"AC1": [A]})
        return TR.record_test_run(self.root, "R1", **kw)

    def stored(self):
        return TR.read_test_record(self.root, "R1")["last_suite"]


class VerdictIsOneWord(_Root):

    def test_all_green(self):
        s = self.record(passed=True, results={A: True, B: True})
        self.assertEqual(("passed", ""), (s["verdict"], s["reason"]))
        self.assertNotIn("passed", {k for k in s if k == "passed"}, "the bool is gone (replaced)")
        self.assertEqual([], s["failed"])

    def test_mixed_red(self):
        s = self.record(passed=False, results={A: True, B: False}, not_run={C: "could not start"})
        self.assertEqual("failed", s["verdict"], "any red is failed, before any not-run")
        self.assertEqual([B], s["failed"])
        self.assertIn("1 test(s) failed", s["reason"])

    def test_mixed_not_run(self):
        s = self.record(passed=True, results={A: True}, not_run={B: "could not start: no pytest"})
        self.assertEqual("not-all-ran", s["verdict"])
        self.assertEqual([B], s["not_run"])
        self.assertIn("did not run", s["reason"])
        self.assertIn("no pytest", s["reason"])

    def test_cancelled_all_green(self):
        s = self.record(passed=True, results={A: True}, complete=False)
        self.assertEqual("cancelled", s["verdict"])
        self.assertTrue(s["reason"])

    def test_nothing_ran(self):
        s = self.record(passed=True, results={}, not_run={A: "could not start", B: "could not start"})
        self.assertEqual("not-all-ran", s["verdict"])
        self.assertIn("2 test(s) did not run", s["reason"])

    def test_the_cli_word_with_no_tests(self):
        self.assertEqual("failed", self.record(passed=False)["verdict"])
        self.assertEqual("passed", self.record(passed=True)["verdict"])

    def test_the_rule_in_order(self):
        self.assertEqual("failed", TR.verdict_of(1, 1, False))
        self.assertEqual("not-all-ran", TR.verdict_of(0, 1, False))
        self.assertEqual("cancelled", TR.verdict_of(0, 0, False))
        self.assertEqual("passed", TR.verdict_of(0, 0, True))

    def test_ten_thousand_tests_record_in_linear_time(self):
        def t(n):
            res = {"tests/t.py::test_%d" % i: i % 7 != 0 for i in range(n)}
            t0 = time.perf_counter()
            s = self.record(passed=False, results=res)
            return time.perf_counter() - t0, s
        small, _ = t(1000)
        big, s = t(10000)
        self.assertEqual("failed", s["verdict"])
        self.assertEqual(len([i for i in range(10000) if i % 7 == 0]), len(s["failed"]))
        self.assertLess(big, 30 * max(small, 0.01), "10x the tests cost %.2fs vs %.2fs" % (big, small))


class OldRecordRefusedLoudly(_Root):

    def old(self):
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir
        StateStore(state_dir(self.root)).write(TR.key("R1"), {
            "run_id": "R1", "last_suite": {"at": time.time(), "passed": True, "command": "pytest",
                                           "tests": [A], "source": "cli"}})

    def test_the_strict_read_is_unreadable_with_the_instruction(self):
        from mokata.state import UNREADABLE
        self.old()
        got = TR.read_test_record_strict(self.root, "R1")
        self.assertEqual(UNREADABLE, got.state)
        self.assertIn("re-run the tests", got.reason)

    def test_the_one_reader_says_unreadable_never_passed(self):
        self.old()
        v, words = TR.suite_verdict(self.stored())
        self.assertEqual("unreadable", v)
        self.assertIn("re-run the tests", words)

    def test_the_stop_gate_refuses_it_with_the_instruction(self):
        from mokata.stop_gate import missing_evidence
        self.old()
        why = missing_evidence(self.root, "R1")
        self.assertTrue(any("re-run the tests" in r for r in why), why)
        self.assertFalse(any("FAILED" in r for r in why), why)


class ConsumersAgree(_Root):
    """For each verdict, the Stop gate, the review panel's colouring and the proof route say the
    same words as `suite_verdict`; a green test is never red."""

    CASES = {
        "passed": dict(passed=True, results={A: True, B: True}),
        "failed": dict(passed=False, results={A: True, B: False}),
        "cancelled": dict(passed=True, results={A: True, B: True}, complete=False),
        "not-all-ran": dict(passed=True, results={A: True}, not_run={B: "could not start"}),
    }

    def test_the_stop_gate_says_the_one_readers_words(self):
        from mokata.stop_gate import missing_evidence
        for v, kw in self.CASES.items():
            with self.subTest(v):
                self.record(**kw)
                verdict, said = TR.suite_verdict(self.stored())
                self.assertEqual(v, verdict)
                why = missing_evidence(self.root, "R1")
                if v == "passed":
                    self.assertFalse(any("test run" in r and "passed" not in r for r in why), why)
                else:
                    self.assertIn(said, why)
                    self.assertEqual(v == "failed", any("FAILED" in r for r in why),
                                     "FAILED only for a failed run: %r" % (why,))

    # the words each verdict must say, written here - not computed by the function under test
    WORDS = {"passed": "the last recorded test run passed", "failed": "the last recorded test run FAILED",
             "cancelled": "CANCELLED before it finished", "not-all-ran": "NOT ALL tests ran",
             "old": "re-run the tests"}

    def routes_say(self):
        """What the two routes a person reads ANSWER: the proof route (`trace`) and the review
        panel's (`tests_panel`) - their real output, not the reader they share (verifier L1)."""
        from mokata.ide.proof import trace
        from mokata.ide.review_context import tests_panel
        return (trace(self.root, run="R1")["record"]["last_suite"]["said"],
                tests_panel(self.root, "R1")["last_suite"]["said"])

    def test_the_proof_route_and_the_panel_carry_the_same_words(self):
        for v, kw in self.CASES.items():
            with self.subTest(v):
                self.record(**kw)
                proof_said, panel_said = self.routes_say()
                self.assertEqual(proof_said, panel_said, "the proof route and the panel say one thing")
                self.assertIn(self.WORDS[v], panel_said)
                self.assertEqual(v == "failed", "FAILED" in panel_said, panel_said)

    def test_an_old_record_tells_both_routes_to_re_run_the_tests(self):
        """A record written before 0.0.28 (no verdict): the panel says what to do, as the gates do
        (verifier L2: it showed bare "not run" chips and nothing else)."""
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir
        self.record(passed=True, results={A: True, B: True})
        st = StateStore(state_dir(self.root))
        data = st.read(TR.key("R1"))
        data["last_suite"] = {k: v for k, v in data["last_suite"].items() if k not in ("verdict", "reason")}
        data["last_suite"]["passed"] = True                       # the pre-0.0.28 shape
        st.write(TR.key("R1"), data)
        proof_said, panel_said = self.routes_say()
        self.assertEqual(proof_said, panel_said)
        self.assertIn(self.WORDS["old"], panel_said)
        self.assertNotIn("passed", panel_said.replace("re-run", ""), "an old record is not read as passed")

    def test_a_green_test_is_never_red_in_the_panel(self):
        from mokata.ide.review_context import test_history
        for v, kw in self.CASES.items():
            with self.subTest(v):
                self.record(**kw)
                suite = self.stored()
                latest_a = test_history(A, set(), set(), suite, {})[0]
                self.assertEqual("green", latest_a, "A passed in every case")
                latest_b = test_history(B, set(), set(), suite, {})[0]
                self.assertEqual({"passed": "green", "failed": "red", "cancelled": "green",
                                  "not-all-ran": "unrun"}[v], latest_b)

    def test_the_merge_gate_says_the_same_words(self):
        from test_p21_gated_merge import _Merge, _state
        from mokata import merge_gate as MG
        from mokata.test_runs import key as tkey

        class M(_Merge):
            def runTest(self):
                pass
        m = M()
        m.setUp()
        self.addCleanup(m.doCleanups)
        wt = m.session()
        m.evidence(wt)
        st = _state(m.main)
        m.blocks = lambda w: MG.plan_merge(m.main, w).blocked
        m.codes = lambda w: {b["code"] for b in m.blocks(w)}
        base = st.read(tkey("R1"))
        self.assertEqual("passed", base["last_suite"]["verdict"], "the runner records a verdict")
        self.assertFalse([c for c in m.codes(wt) if c.startswith("suite-")])
        want = {"failed": ("suite-failed", "FAILED"), "cancelled": ("suite-cancelled", "CANCELLED"),
                "not-all-ran": ("suite-not-all-ran", "NOT ALL")}
        for v, (code, word) in want.items():
            with self.subTest(v):
                data = dict(base, last_suite=dict(base["last_suite"], verdict=v, reason="why"))
                st.write(tkey("R1"), data)
                blocks = m.blocks(wt)
                codes = {b["code"] for b in blocks}
                self.assertIn(code, codes)
                self.assertEqual(v == "failed", "suite-failed" in codes)
                text = " ".join(str(b) for b in blocks)
                self.assertIn(TR.suite_verdict(data["last_suite"])[1], text)
                self.assertIn(word, text)
        old = dict(base, last_suite={k: x for k, x in base["last_suite"].items()
                                     if k not in ("verdict", "reason")})
        st.write(tkey("R1"), old)
        self.assertIn("unknown-evidence", m.codes(wt), "a pre-verdict record never passes")


if __name__ == "__main__":
    unittest.main()
