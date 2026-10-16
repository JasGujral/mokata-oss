"""0.0.23 D8 — EV-GATE scope (a)–(c) (doc 84): typed `artifacts[]` + `tested[]` per gate outcome,
"evidence covers the stated ACs" as a finding, persisted under .mokata (observability tier)."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.engine import run_completeness_gate
from mokata.engine.spec import AcceptanceCriterion, Spec, TestRef
from mokata.govern import enforce
from mokata.govern import evidence as ev
from mokata.govern.ledger import AuditLedger
from mokata.skills import Gate


def _spec(n=2):
    return Spec("t", [AcceptanceCriterion(f"AC{i}", "x") for i in range(1, n + 1)])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mokata-d8-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------- (a) typed artifacts
class TestD8Artifacts(unittest.TestCase):
    def test_d8_three_types(self):
        self.assertEqual(("path", "url", "content"), ev.ARTIFACT_TYPES)
        for t, ref in (("path", "tests/a.py:3"), ("url", "https://x.test/r"), ("content", "log")):
            self.assertEqual(t, ev.Artifact(t, ref).type)

    def test_d8_unknown_type_refused(self):
        with self.assertRaises(ValueError):
            ev.Artifact("blob", "x")

    def test_d8_empty_ref_refused(self):
        with self.assertRaises(ValueError):
            ev.Artifact("path", "")

    def test_d8_url_needs_scheme(self):
        with self.assertRaises(ValueError):
            ev.Artifact("url", "not a url")

    def test_d8_content_is_capped(self):
        a = ev.Artifact("content", "x" * (ev.CONTENT_CAP + 50))
        self.assertLess(len(a.ref), ev.CONTENT_CAP + 50)
        self.assertTrue(a.ref.endswith("[truncated]"))

    def test_d8_round_trip(self):
        a = ev.Artifact("url", "https://x.test", "pr")
        self.assertEqual(a, ev.Artifact.from_dict(a.to_dict()))


# --------------------------------------------------------------------------- (b) tested[] inventory
class TestD8Tested(unittest.TestCase):
    def test_d8_status_vocabulary(self):
        with self.assertRaises(ValueError):
            ev.TestedCheck("c", "green")
        with self.assertRaises(ValueError):
            ev.TestedCheck("", "pass")

    def test_d8_skip_and_error_are_not_exercised(self):
        self.assertTrue(ev.TestedCheck("c", "pass").exercised)
        self.assertTrue(ev.TestedCheck("c", "fail").exercised)
        self.assertFalse(ev.TestedCheck("c", "skip").exercised)
        self.assertFalse(ev.TestedCheck("c", "error").exercised)

    def test_d8_every_gate_decision_carries_evidence(self):
        d = enforce.decide_gate(Gate("g", "d"), True, tested=[{"check_id": "c", "status": "pass"}],
                                artifacts=[{"type": "path", "ref": "a.py"}])
        self.assertEqual(("g", "pass", "hard"), (d.evidence.gate_id, d.evidence.verdict,
                                                 d.evidence.enforcement))
        self.assertEqual("c", d.evidence.tested[0].check_id)
        self.assertEqual("a.py", d.evidence.artifacts[0].ref)

    def test_d8_evidence_verdict_tracks_the_ladder(self):
        self.assertEqual("block", enforce.decide_gate(Gate("g", "d"), False).evidence.verdict)
        adv = Gate("g", "d", enforcement="advisory")
        self.assertEqual("would-block", enforce.decide_gate(adv, False).evidence.verdict)
        soft = Gate("g", "d", enforcement="soft")
        self.assertEqual("override", enforce.decide_gate(soft, False,
                                                         override=lambda _v: True).evidence.verdict)

    def test_d8_malformed_check_is_loud(self):
        with self.assertRaises(ValueError):
            enforce.decide_gate(Gate("g", "d"), True, tested=[{"check_id": "c", "status": "??"}])

    def test_d8_completeness_inventory_is_what_it_ran(self):
        r = run_completeness_gate(_spec(2), [TestRef("t1", ["AC1"], path="tests/t.py", line=7)])
        ids = [(t.check_id, t.status) for t in r.evidence.tested]
        self.assertEqual([("spec-has-criteria", "pass"), ("ac-maps-to-test:AC1", "pass"),
                          ("ac-maps-to-test:AC2", "fail")], ids)
        self.assertEqual([("path", "tests/t.py:7")],
                         [(a.type, a.ref) for a in r.evidence.artifacts])
        self.assertEqual("block", r.evidence.verdict)

    def test_d8_pathless_test_is_content_artifact(self):
        r = run_completeness_gate(_spec(1), [TestRef("t1", ["AC1"])])
        self.assertEqual([("content", "t1")], [(a.type, a.ref) for a in r.evidence.artifacts])


# --------------------------------------------------------------------------- (c) coverage finding
class TestD8CoverageFinding(unittest.TestCase):
    def _ev(self, *checks):
        return ev.GateEvidence("g", "pass", tested=[ev.TestedCheck(*c) for c in checks])

    def test_d8_all_covered(self):
        f = ev.evidence_covers_acs(self._ev(("a", "pass", ("AC1", "AC2"))), ["AC1", "AC2"])
        self.assertTrue(f.ok)
        self.assertEqual("evidence-covers-acs", f.finding_id)

    def test_d8_uncovered_named(self):
        f = ev.evidence_covers_acs(self._ev(("a", "pass", ("AC1",))), ["AC1", "AC2"])
        self.assertFalse(f.ok)
        self.assertEqual(["AC2"], f.uncovered)
        self.assertIn("AC2", f.message)

    def test_d8_skipped_check_does_not_cover(self):
        f = ev.evidence_covers_acs(self._ev(("a", "skip", ("AC1",))), ["AC1"])
        self.assertEqual(["AC1"], f.uncovered)

    def test_d8_failed_check_is_unproven_not_covered(self):
        f = ev.evidence_covers_acs(self._ev(("a", "fail", ("AC1",))), ["AC1"])
        self.assertFalse(f.ok)
        self.assertEqual(([], ["AC1"]), (f.uncovered, f.unproven))

    def test_d8_pass_without_evidence_is_a_finding(self):
        # the point of tested[]: a gate that says pass while exercising nothing is visible.
        d = enforce.decide_gate(Gate("g", "d"), True)
        f = ev.evidence_covers_acs(d.evidence, ["AC1"])
        self.assertTrue(d.allowed)
        self.assertFalse(f.ok)

    def test_d8_finding_never_flips_the_verdict(self):
        r = run_completeness_gate(_spec(2), [TestRef("t1", ["AC1"])])
        self.assertFalse(r.passed)
        self.assertEqual(["AC2"], r.evidence_finding.unproven)
        ok = run_completeness_gate(_spec(1), [TestRef("t1", ["AC1"])])
        self.assertTrue(ok.passed)
        self.assertTrue(ok.evidence_finding.ok)


# --------------------------------------------------------------------------- persistence
class TestD8Persistence(_Tmp):
    def test_d8_record_and_load(self):
        root = os.path.join(self.tmp, "evidence")
        e = ev.GateEvidence("g", "pass", "hard", [ev.Artifact("path", "a.py")],
                            [ev.TestedCheck("c", "pass", ("AC1",))])
        path = ev.record_evidence(root, "run-1", e)
        self.assertEqual(os.path.join(root, "run-1", "evidence.jsonl"), path)
        [back] = ev.load_evidence(root, "run-1")
        self.assertEqual(("g", "run-1"), (back.gate_id, back.run_id))
        self.assertEqual(e.tested, back.tested)
        self.assertTrue(back.at)

    def test_d8_append_only(self):
        root = os.path.join(self.tmp, "evidence")
        ev.record_evidence(root, "r", ev.GateEvidence("g1", "pass"))
        ev.record_evidence(root, "r", ev.GateEvidence("g2", "block"))
        self.assertEqual(["g1", "g2"], [x.gate_id for x in ev.load_evidence(root, "r")])

    def test_d8_root_beside_state(self):
        from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
        state_root = os.path.join(self.tmp, MOKATA_DIR, TEMP_LOCAL_DIRNAME, "state")

        class S:
            root = state_root
        got = ev.evidence_root_for_state(S())
        # compared as POSIX path PARTS, never as a native-built name (Windows-safe)
        parts = os.path.normpath(got).split(os.sep)
        self.assertEqual([".mokata", "temp_local", "evidence"], parts[-3:])

    def test_d8_run_id_cannot_traverse(self):
        root = os.path.join(self.tmp, "evidence")
        for bad in ("../../x", "..", "a/b\\c:d", ""):
            name = ev.safe_run_dirname(bad)
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn(":", name)
            self.assertNotIn(name, ("", ".", ".."))
        path = ev.record_evidence(root, "../../escape", ev.GateEvidence("g", "pass"))
        self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(root)))

    def test_d8_degrade_clean(self):
        self.assertIsNone(ev.record_evidence(None, "r", ev.GateEvidence("g", "pass")))
        self.assertIsNone(ev.record_evidence(self.tmp, "", ev.GateEvidence("g", "pass")))
        self.assertEqual([], ev.load_evidence(self.tmp, "nope"))
        # a file where the run dir should be -> the write fails -> None, never raises
        root = os.path.join(self.tmp, "evidence")
        os.makedirs(root)
        open(os.path.join(root, "blocked"), "w", encoding="utf-8").close()
        self.assertIsNone(ev.record_evidence(root, "blocked", ev.GateEvidence("g", "pass")))

    def test_d8_corrupt_line_skipped(self):
        root = os.path.join(self.tmp, "evidence")
        ev.record_evidence(root, "r", ev.GateEvidence("g1", "pass"))
        with open(ev.evidence_path(root, "r"), "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        ev.record_evidence(root, "r", ev.GateEvidence("g2", "pass"))
        self.assertEqual(["g1", "g2"], [x.gate_id for x in ev.load_evidence(root, "r")])

    def test_d8_emit_persists_evidence_under_mokata(self):
        from mokata import session
        from mokata.config import Surface
        from mokata.engine.emit import emit_spec
        from mokata.init import init_repo
        run = "d8-run"
        with mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run}):
            session.reset_for_test()
            try:
                init_repo(root=self.tmp, profile="standard", assume_yes=True,
                          out=lambda *_a: None)
                surface = Surface.load(self.tmp)
                ledger = AuditLedger(os.path.join(self.tmp, "audit.jsonl"))
                out = emit_spec(surface, _spec(2), [TestRef("t1", ["AC1"])], ledger=ledger,
                                run_id=run, assume_yes=True)
                self.assertFalse(out.committed)           # still refused (hard gate)
                root = os.path.join(self.tmp, ".mokata", "temp_local", "evidence")
                [rec] = ev.load_evidence(root, run)
                self.assertEqual(("completeness", "block"), (rec.gate_id, rec.verdict))
                with open(ev.evidence_path(root, run), encoding="utf-8") as fh:
                    raw = json.loads(fh.readline())
                self.assertEqual(ev.EVIDENCE_SCHEMA_VERSION, raw["schema_version"])
            finally:
                session.reset_for_test()

    def test_d8_pipeline_persists_evidence(self):
        from mokata import session
        from mokata.brainstorm import Approach, Grounding, Handoff
        from mokata.engine.phases import run_pipeline
        from mokata.state import StateStore
        state = StateStore(os.path.join(self.tmp, ".mokata", "temp_local", "state"))
        os.makedirs(state.root)
        handoff = Handoff(topic="t", approach=Approach("a", "b"), answered_questions=[],
                          grounding=Grounding(False, None, False, None), approver="j",
                          approved_at="now")
        with mock.patch.dict(os.environ, {session.SESSION_ID_ENV: "pipe-run"}):
            session.reset_for_test()
            try:
                run_pipeline(handoff, _spec(1), [], store=state)
            finally:
                session.reset_for_test()
        root = os.path.join(self.tmp, ".mokata", "temp_local", "evidence")
        [rec] = ev.load_evidence(root, "pipe-run")
        self.assertEqual(("completeness", "block"), (rec.gate_id, rec.verdict))


if __name__ == "__main__":
    unittest.main()
