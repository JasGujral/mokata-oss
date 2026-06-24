"""E4 — per-task model routing: pick the cheapest capable model, escalate on BLOCKED.
Pluggable policy (no hard-coded model list); reuses the TokenTracker cost view."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.execmode import BLOCKED, DEFAULT_MODELS, Model, ModelRouter, model_cost


class TestRouting(unittest.TestCase):
    def test_route_picks_cheapest(self):
        d = ModelRouter().route("t1")
        self.assertEqual(d.model, "fast")
        self.assertEqual(d.tier, 1)
        self.assertFalse(d.escalated)

    def test_escalate_returns_a_stronger_model(self):
        r = ModelRouter()
        nxt = r.escalate(r.cheapest())
        self.assertEqual(nxt.tier, 2)

    def test_run_escalates_on_blocked(self):
        r = ModelRouter()
        seen = []

        def attempt(model):
            seen.append(model.name)
            return BLOCKED if model.name == "fast" else "ok"

        out = r.run_with_escalation(attempt, task_id="t1")
        names = [d.model for d in out.decisions]
        self.assertEqual(names[0], "fast")            # cheapest first
        self.assertIn("balanced", names)              # escalated to stronger
        self.assertTrue(out.resolved)
        self.assertEqual(out.final_model, "balanced")
        self.assertTrue(out.decisions[-1].escalated)

    def test_unresolved_when_all_models_blocked(self):
        r = ModelRouter()
        out = r.run_with_escalation(lambda m: BLOCKED, task_id="t1")
        self.assertFalse(out.resolved)
        self.assertEqual(out.final_model, "deep")     # escalated to the strongest

    def test_cost_view_reuses_tokentracker(self):
        self.assertGreater(model_cost(DEFAULT_MODELS[0], 1000, 1000), 0.0)
        # a deeper model costs more for the same tokens
        self.assertGreater(model_cost(DEFAULT_MODELS[-1], 1000, 1000),
                           model_cost(DEFAULT_MODELS[0], 1000, 1000))

    def test_policy_is_pluggable(self):
        r = ModelRouter([Model("tiny", 1, 0.0, 0.0), Model("huge", 2, 9.0, 9.0)])
        self.assertEqual(r.route().model, "tiny")


if __name__ == "__main__":
    unittest.main()
