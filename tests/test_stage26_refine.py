"""Stage 26 — the `refine` front-end skill (review existing code → approve a scoped set →
hand off to the existing spec→test→develop→review flow).

Both jsonschema states. `refine` is in the catalog; its HARD-GATE blocks a spec until a
scoped set is approved; the approved set persists and is read by the completeness gate;
`mokata run refine` emits protocol + grounding; the template frontmatter conforms and
carries the `mokata ·` prefix; `mokata enter refine` works as a standalone front-end.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.cli import main
from mokata.engine import Spec, TestRef, run_completeness_gate
from mokata.engine.spec import AcceptanceCriterion
from mokata.pipeline import ENTRY_PHASES, PHASE_GATES, PhaseError, plan_entry
from mokata.refine import (
    REFINE_PROTOCOL,
    Refinement,
    RefineGateError,
    RefineSession,
    load_approved_refinements,
    persist_refinements,
)
from mokata.skills import command_markdown, get_skill, skill_names
from mokata.state import StateStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _refs():
    return [
        Refinement(title="extract auth boundary", rationale="god object",
                   principle="separation of concerns", tradeoff="more files",
                   behavior_impact="preserving", priority=1, dimension="architecture"),
        Refinement(title="validate untrusted input", rationale="injection risk",
                   principle="defense in depth", tradeoff="slightly more code",
                   behavior_impact="changing", priority=1, dimension="security"),
    ]


def _approved_session(scope_in=None, scope_out=None):
    s = RefineSession("src/auth.py")
    s.propose(_refs(), scope_in=scope_in or ["architecture", "security"],
              scope_out=scope_out or ["performance"])
    s.approve(["extract auth boundary"], approver="jas")
    return s


# ----------------------------------------------------------------- catalog + skill

class TestRefineInCatalog(unittest.TestCase):
    def test_refine_is_registered(self):
        self.assertIn("refine", skill_names())
        skill = get_skill("refine")
        self.assertEqual(skill.phase, "refine")
        self.assertTrue(skill.summary.startswith("mokata ·"))

    def test_protocol_covers_the_required_devices(self):
        p = REFINE_PROTOCOL.lower()
        for token in ("ground", "$arguments", "hard-gate", "prioriti", "hand off",
                      "behavior-preserving", "security", "performance"):
            self.assertIn(token, p, f"refine protocol missing '{token}'")


# ----------------------------------------------------------------- the HARD-GATE

class TestRefineGate(unittest.TestCase):
    def test_no_handoff_before_approval(self):
        s = RefineSession("src/auth.py")
        s.propose(_refs())
        self.assertFalse(s.can_emit_spec)
        with self.assertRaises(RefineGateError):
            s.handoff()

    def test_approval_requires_selecting_a_proposed_refinement(self):
        s = RefineSession("src/auth.py")
        s.propose(_refs())
        from mokata.refine import RefineError
        with self.assertRaises(RefineError):
            s.approve(["nonexistent"], approver="jas")
        with self.assertRaises(RefineError):
            s.approve([], approver="jas")

    def test_handoff_after_approval(self):
        s = _approved_session()
        self.assertTrue(s.can_emit_spec)
        plan = s.handoff()
        self.assertEqual(plan.target, "src/auth.py")
        self.assertEqual([r.title for r in plan.refinements], ["extract auth boundary"])
        self.assertIn("architecture", plan.scope_in)
        self.assertIn("performance", plan.scope_out)

    def test_persist_enforces_the_gate(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            unapproved = RefineSession("x")
            unapproved.propose(_refs())
            with self.assertRaises(RefineGateError):
                persist_refinements(unapproved, store)


# ----------------------------------------------------------------- persist + gate

class TestRefinementsPersistAndGate(unittest.TestCase):
    def test_persist_then_load(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            persist_refinements(_approved_session(), store)
            plan = load_approved_refinements(store)
            self.assertIsNotNone(plan)
            self.assertEqual(plan.target, "src/auth.py")
            self.assertEqual(plan.approver, "jas")
            self.assertEqual(len(plan.refinements), 1)

    def test_completeness_gate_reads_refinements(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            persist_refinements(_approved_session(), store)
            spec = Spec(title="refine auth", criteria=[
                AcceptanceCriterion("AC-1", "auth boundary extracted"),
                AcceptanceCriterion("AC-2", "behavior preserved (characterization)"),
            ])
            tests = [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])]
            res = run_completeness_gate(spec, tests, store=store)
            self.assertTrue(res.passed)
            self.assertTrue(res.refinements_present)
            self.assertFalse(res.approach_present)        # came via refine, not brainstorm
            self.assertIn("refinement", res.render().lower())


# ----------------------------------------------------------------- front-end phase

class TestRefineAsFrontEnd(unittest.TestCase):
    def test_refine_is_an_entry_phase_with_a_hard_gate(self):
        self.assertIn("refine", ENTRY_PHASES)
        self.assertEqual(PHASE_GATES["refine"].id, "refinement-approval")

    def test_plan_entry_refine_runs_standalone_and_hands_off(self):
        plan = plan_entry("refine")
        self.assertEqual(plan.phases_run, ["refine"])
        self.assertEqual(plan.gates_applied[0].id, "refinement-approval")
        # it hands off to the whole pipeline (everything is "downstream")
        self.assertIn("emit", plan.skipped_downstream)
        self.assertIn("completeness_gate", plan.skipped_downstream)

    def test_refine_takes_no_to_target(self):
        with self.assertRaises(PhaseError):
            plan_entry("refine", stop="emit")

    def test_linear_phases_unchanged(self):
        # adding the front-end didn't perturb the linear pipeline entry behavior
        plan = plan_entry("brainstorm")
        self.assertEqual(plan.phases_run, ["brainstorm"])


# ----------------------------------------------------------------- CLI + template

class TestRefineCLIAndTemplate(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_run_refine_emits_protocol_and_grounding(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run(["run", "refine", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("refine", out.lower())
            self.assertIn("HARD-GATE", out)
            self.assertIn("Grounding", out)          # render_skill appends grounding

    def test_skills_refine_shows_detail(self):
        rc, out = self._run(["skills", "refine"])
        self.assertEqual(rc, 0)
        self.assertIn("refinement-approval", out)

    def test_enter_refine_cli(self):
        rc, out = self._run(["enter", "refine"])
        self.assertEqual(rc, 0)
        self.assertIn("refine", out)

    def test_template_frontmatter_conforms_and_carries_prefix(self):
        path = os.path.join(ROOT, "templates", "commands", "refine.md")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # single source: the file is exactly what command_markdown renders
        self.assertEqual(text, command_markdown(get_skill("refine")))
        self.assertTrue(text.startswith("---\n"))
        head = text.split("---", 2)[1]
        self.assertIn("name: refine", head)
        self.assertIn("description: mokata ·", head)   # the mokata · prefix
        self.assertIn("argument-hint:", head)


if __name__ == "__main__":
    unittest.main()
