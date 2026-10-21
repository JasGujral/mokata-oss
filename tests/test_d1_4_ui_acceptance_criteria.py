"""0.0.25 Lane D1 stage 4 — the UI acceptance-criterion form; completeness refuses a UI AC without
a state matrix.

Research 2026-10-16 item 6: a UI criterion that states only the happy path ships a component whose
loading, empty, error and disabled states nobody decided. What must be true for the user:
  * a UI criterion (`"kind": "ui"`) carries its BEHAVIOUR (Given/When/Then, in `text`) AND a state
    matrix — what the user sees when it is default, loading, empty, error and disabled;
  * the completeness gate REFUSES a UI criterion without the full matrix, naming the criterion, the
    missing states and the fix, and nothing is written; `"n/a — why"` counts as a decision;
  * a behaviour criterion is unchanged (no `kind`/`states` on disk); malformed shapes are refused
    loudly; pre-1.0, no back-compat path exists for a UI criterion without states;
  * a spec routed to the React web pack with NO UI criterion gets an advisory (never a block).
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.engine.completeness import (UI_MATRIX_FIX, WARN_NO_UI_CRITERION,
                                        run_completeness_gate)
from mokata.engine.emit import emit_spec, spec_from_payload
from mokata.engine.spec import UI_STATES, AcceptanceCriterion, Spec
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.run_resolver import SPEC_PREFIX
from mokata.session_state import SessionScopedStore
from mokata.state import StateStore
from mokata.tdd_state import state_dir

RID = "rUI"
FULL = {"default": "Save is enabled", "loading": "Save is disabled and a spinner shows",
        "empty": "n/a — the form always has fields", "error": "an alert names the failure",
        "disabled": "Save is greyed out and not focusable"}
GWT = "Given a dirty form, when the user clicks Save, then the change is persisted"


def _payload(*criteria, domains=None):
    p = {"title": "Saving the profile form", "criteria": list(criteria),
         "tests": [{"name": f"test_{c['id']}", "ac_ids": [c["id"]]} for c in criteria]}
    if domains is not None:
        p["domains"] = domains
    return p


def _ui(ac_id="AC-1", states=None):
    c = {"id": ac_id, "text": GWT, "kind": "ui"}
    if states is not None:
        c["states"] = states
    return c


class _Repo(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        env = mock.patch.dict(os.environ, {"HOME": self._td.name, "USERPROFILE": self._td.name})
        env.start()
        self.addCleanup(env.stop)
        self.root = os.path.join(self._td.name, "repo")
        os.makedirs(self.root)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *_: None)
        PipelineCheckpoint(Surface.load(self.root).state, RID).ensure_registered()
        self.store = SessionScopedStore(StateStore(state_dir(self.root)), RID)

    def emit(self, payload):
        spec, tests = spec_from_payload(payload)
        return emit_spec(Surface.load(self.root), spec, tests, store=self.store, run_id=RID,
                         assume_yes=True)

    def on_disk(self):
        return StateStore(state_dir(self.root)).read(SPEC_PREFIX + RID)


class AUiCriterionCarriesBehaviourAndStates(_Repo):

    def test_a_full_matrix_emits_and_round_trips(self):
        out = self.emit(_payload(_ui(states=FULL)))
        self.assertTrue(out.committed, out.reason)
        ac = self.on_disk()["criteria"][0]
        self.assertEqual("ui", ac["kind"])
        self.assertEqual(GWT, ac["text"])
        self.assertEqual(list(UI_STATES), list(ac["states"]), "stored in the canonical order")
        self.assertEqual(FULL["empty"], ac["states"]["empty"], "n/a — why is a decision, kept")
        back = Spec.from_dict(self.on_disk()).criteria[0]
        self.assertEqual(([], FULL), (back.missing_states(), back.states))

    def test_a_behaviour_criterion_is_unchanged_on_disk(self):
        out = self.emit(_payload({"id": "AC-1", "text": "the API rejects an empty name"}))
        self.assertTrue(out.committed, out.reason)
        self.assertEqual({"id", "text"}, set(self.on_disk()["criteria"][0]))


class CompletenessRefusesAUiCriterionWithoutAMatrix(_Repo):

    def test_no_matrix_is_refused_and_nothing_is_written(self):
        out = self.emit(_payload(_ui()))
        self.assertFalse(out.committed)
        self.assertEqual("completeness", out.gate)
        self.assertIn("AC-1", out.reason)
        self.assertIn(UI_MATRIX_FIX, out.reason)
        self.assertEqual(list(UI_STATES), out.completeness.ui_incomplete["AC-1"])
        self.assertIsNone(self.on_disk(), "a refused emit writes nothing")

    def test_a_partial_matrix_names_exactly_the_missing_states(self):
        partial = {k: v for k, v in FULL.items() if k not in ("loading", "error")}
        partial["disabled"] = "   "                             # blank is not a decision
        out = self.emit(_payload(_ui(states=partial)))
        self.assertFalse(out.committed)
        self.assertEqual(["loading", "error", "disabled"], out.completeness.ui_incomplete["AC-1"])
        self.assertIn("UI state matrix incomplete: AC-1 (missing loading, error, disabled)",
                      out.completeness.render())

    def test_the_gate_evidence_records_the_matrix_check(self):
        spec, tests = spec_from_payload(_payload(_ui(states={"default": "x"})))
        gr = run_completeness_gate(spec, tests)
        checks = {c.check_id: c.status for c in gr.evidence.tested}
        self.assertEqual("fail", checks["ui-state-matrix:AC-1"].lower())

    def test_the_mcp_surface_refuses_it_before_any_proposal(self):
        from mokata.mcp import tools_spec as T
        res = T.spec_emit(path=self.root, title="t", criteria=[_ui()],
                          tests=[{"name": "test_a", "ac_ids": ["AC-1"]}])
        self.assertEqual("blocked", res["status"])
        self.assertEqual("completeness", res["gate"])
        self.assertFalse(res["committed"])
        self.assertIn("state matrix", res["reason"])


class MalformedShapesAreRefusedLoudly(unittest.TestCase):

    def test_each_malformed_shape_is_a_value_error_at_the_surface(self):
        bad = [
            {"id": "AC-1", "text": GWT, "kind": "screen"},
            _ui(states={"hover": "underline"}),
            _ui(states=["default"]),
            {"id": "AC-1", "text": GWT, "states": {"default": "x"}},   # states on a behaviour AC
            _ui(states={"default": 3}),
        ]
        for c in bad:
            with self.subTest(criterion=json.dumps(c)):
                with self.assertRaises(ValueError):
                    spec_from_payload(_payload(c))

    def test_a_persisted_malformed_ui_criterion_reads_as_malformed(self):
        from mokata.engine.spec_gate import read_emitted_spec
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            store.write("emitted_spec", {"title": "t", "criteria": [
                {"id": "AC-1", "text": GWT, "kind": "ui", "states": {"hover": "x"}}]})
            spec, malformed = read_emitted_spec(store)
            self.assertIsNone(spec)
            self.assertTrue(malformed)


class AReactSpecWithoutAUiCriterionIsAdvised(unittest.TestCase):

    def test_advisory_only(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "the hook returns the cached value")],
                    domains=["frontend-react"])
        from mokata.engine.spec import TestRef
        gr = run_completeness_gate(spec, [TestRef("test_a", ["AC-1"])])
        self.assertTrue(gr.passed)
        self.assertIn(WARN_NO_UI_CRITERION, gr.warnings)
        spec.criteria.append(AcceptanceCriterion("AC-2", GWT, kind="ui", states=dict(FULL)))
        gr = run_completeness_gate(spec, [TestRef("test_a", ["AC-1", "AC-2"])])
        self.assertNotIn(WARN_NO_UI_CRITERION, gr.warnings)

    def test_the_cli_consent_identity_carries_the_form(self):
        from mokata.cli_commands.spec import _consent_criteria
        payload = _payload(_ui(states=FULL))
        spec, _ = spec_from_payload(payload)
        self.assertEqual([{"id": "AC-1", "text": GWT, "kind": "ui", "states": FULL}],
                         _consent_criteria(spec, payload))


if __name__ == "__main__":
    unittest.main()
