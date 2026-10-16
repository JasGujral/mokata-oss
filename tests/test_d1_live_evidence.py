"""0.0.23 stage D1 — `live-evidence`: LIVE-EVIDENCE + DEV-CC (passive).

LIVE-EVIDENCE — at develop time the approved approach's FROZEN consumer list is re-verified against
LIVE callers from the knowledge layer; a consumer in live code absent from the list (the 0.0.14
defect's shape) and a listed file nothing live references are each surfaced as a passive FINDING in
develop's per-turn pack — never a block, never a write.
DEV-CC passive — the same pack names the approved decisions and the existing guards on the files the
run has touched.
Both ride the ONE per-turn channel inside the ONE `settings.context.turn_tokens` budget.
"""
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import run_intent as RI
from mokata import working_set as WS
from mokata.bootstrap import build_injection
from mokata.config import Surface
from mokata.engine import live_evidence as LE
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.knowledge.query import BASIS_LEXICAL, BASIS_STRUCTURAL, QueryResult, Reference
from mokata.progress_events import STAGE_ENTER, ProgressLog
from mokata.run_resolver import APPROACH_PREFIX, SPEC_PREFIX
from mokata.state import StateStore
from mokata.tdd_state import state_dir


def _silent(_):
    pass


class _Layer:
    """A duck-typed knowledge layer: `blast_radius(sym)` answers from a fixed table."""

    def __init__(self, table, basis=BASIS_STRUCTURAL, raises=()):
        self.table, self.basis, self.raises, self.asked = table, basis, set(raises), []

    def blast_radius(self, sym, depth=2):
        self.asked.append(sym)
        if sym in self.raises:
            raise RuntimeError("graph down")
        refs = [Reference(path=p, line=1) for p in self.table.get(sym, [])]
        return QueryResult(kind="blast_radius", target=sym, references=refs, backend="fake",
                           basis=self.basis)


def _handoff(targets, files, decisions=()):
    return {
        "topic": "limits", "phase": "brainstorm", "approver": "jas", "approved_at": "now",
        "approach": {"name": "A", "summary": "s", "targets": list(targets),
                     "decisions": [dict(d) for d in decisions]},
        "answered_questions": [], "grounding": {},
        "impact": {"approach": "A", "targets": list(targets), "impacted_files": list(files)},
    }


class _Home(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = self._td.name
        p = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def repo(self, d, rid="rA", stage="develop", targets=("amountMax",),
             files=("src/useCurvesTable.py",), decisions=(), spec=True):
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, rid).ensure_registered()
        store = StateStore(state_dir(d))
        store.write(APPROACH_PREFIX + rid, _handoff(targets, files, decisions))
        if spec:
            store.write(SPEC_PREFIX + rid, {"title": "t", "criteria": [{"id": "AC-1",
                                                                         "text": "x"}]})
        if stage:
            ProgressLog.from_state_dir(state_dir(d)).append_event(STAGE_ENTER, stage, run_id=rid)
        return Surface.load(d)


class LiveEvidenceReverifiesTheFrozenList(unittest.TestCase):

    def test_the_0_0_14_shape_a_live_consumer_missing_from_the_list_is_a_finding(self):
        # The frozen list named a non-consumer and MISSED the real one; live code names the real one.
        layer = _Layer({"amountMax": ["src/useSingleCusipLimitsTable.py"]})
        found = LE.reverify_consumers(["amountMax"], ["src/useCurvesTable.py"], layer)
        kinds = {(f.kind, f.path) for f in found}
        self.assertIn((LE.MISSING, "src/useSingleCusipLimitsTable.py"), kinds)
        self.assertIn((LE.STALE, "src/useCurvesTable.py"), kinds)
        line = [f for f in found if f.kind == LE.MISSING][0].line()
        self.assertIn("NOT in the approach's frozen list", line)
        self.assertIn("never skip it for not being listed", line)
        self.assertIn("[structural]", line)

    def test_a_list_that_matches_live_code_yields_no_finding(self):
        layer = _Layer({"amountMax": ["src/a.py", "src/b.py"]})
        self.assertEqual([], LE.reverify_consumers(["amountMax"], ["./src/a.py", "src/b.py"],
                                                    layer))

    def test_stale_is_not_decided_when_a_target_went_unasked_or_failed(self):
        # A frozen file may belong to a target whose answer this turn never got — it is unasked,
        # not stale.
        layer = _Layer({"a": ["src/a.py"]}, raises={"b"})
        found = LE.reverify_consumers(["a", "b"], ["src/a.py", "src/only_b.py"], layer)
        self.assertEqual([], [f for f in found if f.kind == LE.STALE])
        many = ["t%d" % i for i in range(LE.MAX_TARGETS + 1)]
        found = LE.reverify_consumers(many, ["src/x.py"], _Layer({}))
        self.assertEqual([], [f for f in found if f.kind == LE.STALE])

    def test_a_lexical_answer_says_it_is_lexical(self):
        layer = _Layer({"amountMax": ["src/new.py"]}, basis=BASIS_LEXICAL)
        found = LE.reverify_consumers(["amountMax"], [], layer)
        self.assertIn("[lexical]", found[0].line())

    def test_no_layer_or_no_targets_is_silence_not_a_crash(self):
        self.assertEqual([], LE.reverify_consumers(["a"], ["x.py"], None))
        self.assertEqual([], LE.reverify_consumers([], ["x.py"], _Layer({})))

    def test_tests_and_docs_on_the_list_are_never_called_stale(self):
        found = LE.reverify_consumers(["a"], ["tests/test_a.py", "docs/a.md"], _Layer({"a": []}))
        self.assertEqual([], found)


class DevCcPassive(unittest.TestCase):

    def test_guards_are_found_in_source(self):
        src = ("def f(x):\n    if x is None:\n        raise ValueError('x')\n"
               "    assert x > 0\n    if x > 9:\n        return 9\n    if x:\n        x += 1\n")
        got = LE.guards_in_source(src)
        self.assertEqual([2, 4, 5], [ln for ln, _ in got])
        self.assertEqual("if x is None: raise", got[0][1])
        self.assertEqual("assert x > 0", got[1][1])

    def test_unparseable_source_has_no_guards(self):
        self.assertEqual([], LE.guards_in_source("def (:\n"))

    def test_decisions_reach_touched_files_by_their_about_code_path(self):
        class D:
            def __init__(self, i, about, stmt):
                self.id, self.about_code, self.statement = i, about, stmt
        decisions = [D("D1", ["src/a.py:limit"], "limits stay inclusive"),
                     D("D2", ["src/other.py"], "unrelated")]
        lines = LE.decisions_on_touched(decisions, ["src/a.py"])
        self.assertEqual(1, len(lines))
        self.assertIn("approach decision D1 on `src/a.py`: limits stay inclusive", lines[0])


class DevelopTurnLines(_Home):

    def test_develop_turn_lines_carry_findings_decisions_and_guards(self):
        with tempfile.TemporaryDirectory() as d:
            self.repo(d, decisions=[{"id": "D1", "statement": "max is inclusive",
                                     "about_code": ["src/limits.py"]}])
            os.makedirs(os.path.join(d, "src"))
            with open(os.path.join(d, "src", "limits.py"), "wb") as fh:
                fh.write(b"def check(v, m):\n    if v > m:\n        raise ValueError(v)\n")
            WS.record_touched(d, "rA", "src/limits.py")
            layer = _Layer({"amountMax": ["src/useSingleCusipLimitsTable.py"]})
            lines = LE.develop_turn_lines(d, "rA", layer=layer)
            text = "\n".join(lines)
            self.assertIn("live-evidence: `src/useSingleCusipLimitsTable.py`", text)
            self.assertIn("approach decision D1 on `src/limits.py`", text)
            self.assertIn("existing guard `src/limits.py:2` `if v > m: raise`", text)

    def test_outside_develop_there_are_no_lines_and_no_query(self):
        with tempfile.TemporaryDirectory() as d:
            self.repo(d, stage="review")
            layer = _Layer({"amountMax": ["src/x.py"]})
            self.assertEqual([], LE.develop_turn_lines(d, "rA", layer=layer))
            self.assertEqual([], layer.asked, "no graph query is paid outside develop")

    def test_no_stage_recorded_reads_the_emitted_spec_as_develop(self):
        with tempfile.TemporaryDirectory() as d:
            self.repo(d, stage=None)
            self.assertTrue(LE.run_in_develop(d, "rA"))
        with tempfile.TemporaryDirectory() as d:
            self.repo(d, stage=None, spec=False)
            self.assertFalse(LE.run_in_develop(d, "rA"))

    def test_the_layer_is_built_lazily_only_when_there_is_a_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.repo(d, targets=(), files=())
            built = []
            LE.develop_turn_lines(d, "rA", layer_factory=lambda: built.append(1))
            self.assertEqual([], built)

    def test_a_broken_layer_factory_is_silence(self):
        with tempfile.TemporaryDirectory() as d:
            self.repo(d)

            def boom():
                raise RuntimeError("no graph")
            self.assertEqual([], LE.develop_turn_lines(d, "rA", layer_factory=boom))


class TheOneChannelAndTheOneBudget(_Home):

    def test_evidence_rides_the_per_turn_pack_inside_its_budget(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self.repo(d)
            ev = ["- live-evidence: `src/x.py` references `a` in LIVE code but is NOT listed"]
            res = build_injection(surface, "change amountMax", evidence_lines=ev)
            self.assertIn(ev[0], res.text)
            self.assertLessEqual(res.token_estimate, res.budget)
            self.assertEqual([], res.item_ids, "an evidence line is not a memory item")

    def test_turn_tokens_zero_turns_the_evidence_off_too(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self.repo(d)
            res = build_injection(surface, "q", budget=0, evidence_lines=["- live-evidence: x"])
            self.assertEqual("", res.text)

    def test_a_tight_budget_drops_whole_lines_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self.repo(d)
            ev = ["- live-evidence: " + ("w " * 60) + str(i) for i in range(6)]
            res = build_injection(surface, "q", budget=120, evidence_lines=ev)
            self.assertLessEqual(res.token_estimate, 120)
            self.assertIn("not shown", res.text)

    def test_no_evidence_leaves_the_pack_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self.repo(d)
            a = build_injection(surface, "anything at all")
            b = build_injection(surface, "anything at all", evidence_lines=[])
            self.assertEqual(a, b)

    def test_the_hook_only_speaks_for_a_session_bound_to_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self.repo(d)
            with mock.patch("mokata.engine.live_evidence.develop_turn_lines",
                            return_value=["- live-evidence: hit"]) as dtl:
                self.assertEqual([], hook_cli._develop_evidence(surface, d, "S-unbound"))
                dtl.assert_not_called()
                RI.bind_registered(d, "S1", "rA")
                self.assertEqual(["- live-evidence: hit"],
                                 hook_cli._develop_evidence(surface, d, "S1"))
                self.assertEqual("rA", dtl.call_args[0][1])

    def test_the_hook_injects_the_evidence_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self.repo(d)
            RI.bind_registered(d, "S1", "rA")
            with mock.patch("mokata.engine.live_evidence.develop_turn_lines",
                            return_value=["- live-evidence: hit"]):
                pack = hook_cli._build_injection_for(surface, "edit the limits", d, "S1")
            self.assertIn("- live-evidence: hit", pack.text)


class TheProseBindsTheFinding(unittest.TestCase):

    def test_develop_skill_and_template_carry_the_live_evidence_clause(self):
        from mokata.skills import DEVELOP_LIVE_EVIDENCE
        root = os.path.join(os.path.dirname(__file__), "..", "src", "mokata")
        for rel in (("templates", "commands", "develop.md"), ("skills", "develop", "SKILL.md")):
            with open(os.path.join(root, *rel), "rb") as fh:
                body = fh.read().decode("utf-8")
            self.assertIn("## Live evidence beats the frozen list", body)
            self.assertIn("NEVER skip a live consumer", body)
        self.assertIn("FINDING, not a directive", DEVELOP_LIVE_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
