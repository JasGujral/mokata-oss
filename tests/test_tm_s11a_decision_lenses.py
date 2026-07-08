"""TM.S11a — brainstorm decision lenses: blast radius + architectural fit (doc 63 §4, doc 55 K3,
doc 42 R1.S4d/R1.S4e, doc 04 P21).

The stage that realizes P21 (correctness designed in, not reviewed in): in the SAME pre-spec pass,
every candidate approach gets BOTH a computed blast-radius impact (Lens 1) and a named
architectural-fit verdict (Lens 2), and the brainstorm approval HARD-GATE refuses until both are on
the table. Covers the grooming decision exactly:

  * `about_code` on MemoryItem (JSON, no DDL, round-trips, legacy = empty);
  * Lens 1 impact for two approaches to one goal is computed + comparable (callers/tests/docs +
    about_code decisions); graph-absent → grep/heuristic still scores;
  * the approval gate REFUSES until both lenses are present; the approved plan file records both;
  * over-threshold complexity OFFERS the deep review (R1.S4e) — never auto-runs it;
  * a mis-layered approach is flagged by Lens 2's verdict; brainstorm works with NO team memory.

Pure over injected layer + memory — a FAKE layer covers deterministic comparison; a REAL grep
KnowledgeLayer over the sample repo covers the graph-absent degrade path.
"""

import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _support import write_sample_repo

from mokata.brainstorm import (
    Approach,
    BrainstormGateError,
    BrainstormSession,
    Handoff,
)
from mokata.brainstorm_impact import (
    DESIGN_FIT_VERDICTS,
    FITS,
    MISFIT,
    RISK,
    ApproachImpact,
    DesignFitVerdict,
    classify_path,
    compare_impacts,
    compute_impact,
    deep_review_offer,
)
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.layer import KnowledgeLayer
from mokata.knowledge.query import QueryResult, Reference
from mokata.memory.item import MemoryItem


# ---------------------------------------------------------------- a deterministic fake layer
class _FakeLayer:
    """A stand-in graph: symbol → its caller references. `uses_graph=True` so it reads as a REAL
    graph (not the degraded grep floor)."""
    uses_graph = True

    def __init__(self, table):
        self.table = table

    def blast_radius(self, symbol, depth=2):
        return QueryResult("blast_radius", symbol,
                           references=list(self.table.get(symbol, [])), backend="fake")


def _ref(path, line, sym):
    return Reference(path, line, snippet="", symbol=sym)


def _decision(subject, about, id):
    it = MemoryItem.create(subject, "a team decision", id=id)
    it.about_code = list(about)
    return it


# ============================================================ about_code round-trip (no DDL)
class TestAboutCode(unittest.TestCase):
    def test_about_code_round_trips(self):
        it = MemoryItem.create("db-choice", "postgres", about_code=["db.connect", "db/pool.py"])
        back = MemoryItem.from_dict(it.to_dict())
        self.assertEqual(back.about_code, ["db.connect", "db/pool.py"])

    def test_legacy_item_has_empty_about_code(self):
        back = MemoryItem.from_dict({"subject": "s", "value": "v"})   # no about_code key
        self.assertEqual(back.about_code, [])


# ============================================================ Lens 1 — comparable impact
class TestLensOneImpact(unittest.TestCase):
    def _layer(self):
        # cache-aside touches the read path (small); write-through touches the write path (wide,
        # spilling into code + tests + docs).
        return _FakeLayer({
            "read_path": [_ref("app/reader.py", 10, "get")],
            "write_path": [
                _ref("app/writer.py", 5, "put"),
                _ref("app/api.py", 22, "handler"),
                _ref("tests/test_writer.py", 3, "test_put"),
                _ref("docs/writes.md", 1, None),
            ],
        })

    def test_two_approaches_are_computed_and_comparable(self):
        layer = self._layer()
        mem = [_decision("cache TTL policy", ["read_path"], "d1"),
               _decision("write durability", ["write_path", "app/writer.py"], "d2")]
        small = compute_impact("cache-aside", ["read_path"], layer=layer, memory_items=mem)
        wide = compute_impact("write-through", ["write_path"], layer=layer, memory_items=mem)

        # comparable: the write-through blast radius is strictly larger
        self.assertGreater(wide.magnitude, small.magnitude)
        self.assertEqual([i.approach for i in compare_impacts([wide, small])],
                         ["cache-aside", "write-through"])          # ranked smallest-first

        # buckets: callers/tests/docs are separated (the AC's callers/tests/docs)
        self.assertEqual(wide.bucket("code"), 2)                    # writer.py + api.py
        self.assertEqual(wide.bucket("test"), 1)                    # test_writer.py
        self.assertEqual(wide.bucket("doc"), 1)                     # writes.md
        self.assertEqual(wide.caller_count, 4)

        # about_code intersection → affected team decisions, unioned in
        self.assertEqual({d.subject for d in small.affected_decisions}, {"cache TTL policy"})
        self.assertEqual({d.subject for d in wide.affected_decisions}, {"write durability"})
        self.assertEqual(wide.affected_decisions[0].matched, ["app/writer.py", "write_path"])

    def test_classify_path_buckets(self):
        self.assertEqual(classify_path("tests/test_x.py"), "test")
        self.assertEqual(classify_path("docs/guide.md"), "doc")
        self.assertEqual(classify_path("ci.yml"), "config")
        self.assertEqual(classify_path("src/app.py"), "code")

    def test_graph_present_is_not_degraded(self):
        imp = compute_impact("a", ["read_path"], layer=self._layer())
        self.assertFalse(imp.degraded)


# ============================================================ graph-absent → grep degrade STILL scores
class TestDegradeStillScores(unittest.TestCase):
    def test_grep_floor_scores_without_a_real_graph(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = KnowledgeLayer(GrepBackend(root=d), None)     # the grep floor (no real graph)
            self.assertFalse(layer.uses_graph)
            mem = [_decision("helper contract", ["helper"], "d1")]
            imp = compute_impact("refactor", ["helper"], layer=layer, memory_items=mem, depth=2)
            # helper <- compute <- Impl.run/main: grep still finds the impact surface
            self.assertGreater(imp.caller_count, 0)
            self.assertTrue(imp.degraded)                          # marked heuristic
            # about_code intersection still scores against the touched symbols
            self.assertEqual([x.subject for x in imp.affected_decisions], ["helper contract"])

    def test_no_layer_still_scores_via_about_code(self):
        # heuristic (no graph at all): the target-symbol intersection alone still finds decisions
        mem = [_decision("owns read_path", ["read_path"], "d1")]
        imp = compute_impact("x", ["read_path"], layer=None, memory_items=mem)
        self.assertTrue(imp.degraded)
        self.assertEqual([d.subject for d in imp.affected_decisions], ["owns read_path"])


# ============================================================ Lens 2 — architectural-fit verdict
class TestLensTwoVerdict(unittest.TestCase):
    def test_mislayered_approach_is_flagged(self):
        v = DesignFitVerdict("bad", MISFIT, risks=["UI layer imports the DB layer directly"])
        self.assertTrue(v.flagged)
        self.assertTrue(v.valid)
        self.assertIn("MISFIT", v.summary_line())

    def test_a_flag_must_name_a_risk_fail_closed(self):
        # a risk/misfit with NO named risk is not a real verdict — it can't satisfy the gate
        self.assertFalse(DesignFitVerdict("x", MISFIT, risks=[]).valid)
        self.assertFalse(DesignFitVerdict("x", RISK, risks=["   "]).valid)
        self.assertFalse(DesignFitVerdict("x", "bogus", risks=[]).valid)

    def test_fits_is_valid_without_risks(self):
        self.assertTrue(DesignFitVerdict("ok", FITS, risks=[]).valid)
        self.assertFalse(DesignFitVerdict("ok", FITS, risks=[]).flagged)

    def test_verdicts_round_trip(self):
        v = DesignFitVerdict("a", RISK, risks=["ownership unclear"], rationale="why")
        self.assertEqual(DesignFitVerdict.from_dict(v.to_dict()), v)


# ============================================================ the HARD-GATE (both lenses required)
class TestApprovalGate(unittest.TestCase):
    def _session(self):
        s = BrainstormSession("add a caching layer")
        s.propose_approaches([
            Approach("cache-aside", "read-through", pros=["simple"], cons=["stale"],
                     targets=["read_path"]),
            Approach("write-through", "writes via cache", pros=["fresh"], cons=["slower"],
                     targets=["write_path"]),
        ])
        return s

    def test_gate_refuses_with_no_lenses(self):
        s = self._session()
        with self.assertRaises(BrainstormGateError):
            s.approve("jas", "cache-aside")
        self.assertFalse(s.approved)

    def test_gate_refuses_with_only_lens_one(self):
        s = self._session()
        s.assess_impacts()                                   # Lens 1 only
        self.assertIn("architectural fit (Lens 2)", s.missing_lenses("cache-aside"))
        with self.assertRaises(BrainstormGateError):
            s.approve("jas", "cache-aside")

    def test_gate_refuses_with_only_lens_two(self):
        s = self._session()
        s.record_design_fit("cache-aside", DesignFitVerdict("cache-aside", FITS, []))
        self.assertIn("blast radius (Lens 1)", s.missing_lenses("cache-aside"))
        with self.assertRaises(BrainstormGateError):
            s.approve("jas", "cache-aside")

    def test_gate_opens_with_both_lenses(self):
        s = self._session()
        s.assess_impacts(layer=_FakeLayer({"read_path": [_ref("app/r.py", 1, "g")]}))
        s.record_design_fit("cache-aside", DesignFitVerdict("cache-aside", FITS, []))
        s.record_design_fit("write-through", DesignFitVerdict("write-through", RISK,
                                                              risks=["extra write coupling"]))
        s.approve("jas", "cache-aside")                      # both on the table → allowed
        self.assertTrue(s.approved)
        self.assertTrue(s.lenses_ready("cache-aside"))

    def test_recording_an_invalid_verdict_is_refused(self):
        s = self._session()
        from mokata.brainstorm import BrainstormError
        with self.assertRaises(BrainstormError):
            s.record_design_fit("cache-aside", DesignFitVerdict("cache-aside", MISFIT, []))


# ============================================================ the plan file records both lenses
class TestPlanRecordsLenses(unittest.TestCase):
    def _approved(self):
        s = BrainstormSession("add caching")
        s.propose_approaches([
            Approach("cache-aside", "rt", pros=["p"], cons=["c"], targets=["read_path"]),
            Approach("write-through", "wt", pros=["p"], cons=["c"], targets=["write_path"]),
        ])
        layer = _FakeLayer({"read_path": [_ref("app/r.py", 1, "g")],
                            "write_path": [_ref("app/w.py", 1, "p")]})
        mem = [_decision("cache policy", ["read_path"], "d1")]
        s.assess_impacts(layer=layer, memory_items=mem)
        s.record_design_fit("cache-aside", DesignFitVerdict("cache-aside", FITS, []))
        s.record_design_fit("write-through", DesignFitVerdict("write-through", MISFIT,
                                                             risks=["breaks the read/write split"]))
        s.approve("jas", "cache-aside")
        return s

    def test_writeup_records_both_lenses(self):
        writeup = self._approved().design_writeup()
        self.assertIn("Decision inputs", writeup)
        self.assertIn("Blast radius", writeup)               # Lens 1 recorded
        self.assertIn("cache policy", writeup)               # affected team decision named
        self.assertIn("Architectural fit", writeup)          # Lens 2 recorded
        self.assertIn("MISFIT", writeup)                     # the flagged approach surfaces

    def test_handoff_carries_the_chosen_lenses_as_constraints(self):
        h = self._approved().handoff()
        self.assertIsInstance(h.impact, ApproachImpact)
        self.assertIsInstance(h.design_fit, DesignFitVerdict)
        self.assertEqual(h.impact.approach, "cache-aside")
        # round-trips through the persisted hand-off
        back = Handoff.from_dict(h.to_dict())
        self.assertEqual(back.impact.approach, "cache-aside")
        self.assertEqual(back.design_fit.verdict, FITS)


# ============================================================ deep-review OFFER (never auto-runs)
class TestDeepReviewOffer(unittest.TestCase):
    def test_over_threshold_offers_r1s4e(self):
        big = ApproachImpact("x", caller_count=30, impacted_files=["a", "b"])   # magnitude ≥ 20
        offer = deep_review_offer([big])
        self.assertIsNotNone(offer)
        self.assertIn("OFFER", offer)
        self.assertIn("R1.S4e", offer)
        self.assertIn("user-invoked", offer)

    def test_under_threshold_no_offer(self):
        small = ApproachImpact("x", caller_count=1, impacted_files=["a"])
        self.assertIsNone(deep_review_offer([small]))

    def test_a_design_misfit_also_offers(self):
        small = ApproachImpact("x", caller_count=1)
        offer = deep_review_offer([small], [DesignFitVerdict("x", MISFIT, ["bad layering"])])
        self.assertIsNotNone(offer)
        self.assertIn("MISFIT", offer)

    def test_session_offer_surfaces_over_threshold(self):
        s = BrainstormSession("big change")
        s.propose_approaches([
            Approach("a", "x", pros=["p"], cons=["c"], targets=["hot"]),
            Approach("b", "y", pros=["p"], cons=["c"], targets=["cold"]),
        ])
        wide = [_ref(f"f{i}.py", i, f"s{i}") for i in range(25)]
        s.assess_impacts(layer=_FakeLayer({"hot": wide, "cold": []}))
        self.assertIsNotNone(s.deep_review_offer())          # offered, not run (just a string)


# ============================================================ works with NO team memory (local)
class TestNoTeamMemory(unittest.TestCase):
    def test_impact_computes_with_no_memory(self):
        imp = compute_impact("a", ["read_path"],
                             layer=_FakeLayer({"read_path": [_ref("r.py", 1, "g")]}),
                             memory_items=None)
        self.assertEqual(imp.affected_decisions, [])         # no decisions, no crash
        self.assertEqual(imp.caller_count, 1)                # code impact still computed

    def test_full_brainstorm_flow_with_no_layer_and_no_memory(self):
        # local mode: no graph, no team memory — brainstorm still runs both lenses + approves
        s = BrainstormSession("local feature")
        s.propose_approaches([
            Approach("a", "x", pros=["p"], cons=["c"]),
            Approach("b", "y", pros=["p"], cons=["c"]),
        ])
        s.assess_impacts()                                   # degraded, but on the table
        s.record_design_fit("a", DesignFitVerdict("a", FITS, []))
        s.record_design_fit("b", DesignFitVerdict("b", FITS, []))
        s.approve("jas", "a")
        self.assertTrue(s.approved)
        self.assertTrue(s.impacts["a"].degraded)


# ============================================================ Approach.targets round-trips
class TestApproachTargets(unittest.TestCase):
    def test_targets_round_trip(self):
        a = Approach("a", "s", pros=["p"], cons=["c"], targets=["sym1", "file.py"])
        self.assertEqual(Approach.from_dict(a.to_dict()).targets, ["sym1", "file.py"])

    def test_legacy_approach_has_empty_targets(self):
        self.assertEqual(Approach.from_dict({"name": "a", "summary": "s"}).targets, [])


# ============================================================ the skill/command teaches both lenses
class TestSkillTeachesLenses(unittest.TestCase):
    """The SKILL.md/command change is specced + tested (not hacked in): the shipped brainstorm
    template + the engine's protocol both carry the two-lens hard-gate + the R1.S4e OFFER."""

    _PHRASES = (
        "blast radius",
        "architectural fit",
        "about_code",
        "cannot approve an approach until BOTH",
        "mis-layered approach is flagged",
        "OFFER",
    )

    def _template(self):
        import os
        here = os.path.dirname(__file__)
        path = os.path.join(here, "..", "src", "mokata", "templates", "commands", "brainstorm.md")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_template_carries_the_two_lenses(self):
        tpl = self._template()
        for phrase in self._PHRASES:
            self.assertIn(phrase, tpl, f"brainstorm.md missing lens phrase: {phrase!r}")

    def test_protocol_constant_mirrors_the_lenses(self):
        from mokata.brainstorm import BRAINSTORM_PROTOCOL
        for phrase in ("blast radius", "architectural fit", "about_code", "OFFER"):
            self.assertIn(phrase, BRAINSTORM_PROTOCOL,
                          f"BRAINSTORM_PROTOCOL missing lens phrase: {phrase!r}")

    def test_shipped_skill_matches_template_no_drift(self):
        # the generated skill must carry the lenses too (byte-identical to the builder output)
        import os
        from pathlib import Path
        import mokata.agent_skills as A
        here = os.path.dirname(__file__)
        templates = Path(here) / ".." / "src" / "mokata" / "templates" / "commands"
        shipped = Path(here) / ".." / "src" / "mokata" / "skills" / "brainstorm" / "SKILL.md"
        self.assertEqual(shipped.read_text(encoding="utf-8"),
                         A.skill_markdown("brainstorm", templates))


if __name__ == "__main__":
    unittest.main()
