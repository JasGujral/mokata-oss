"""D4 — pre-mortem + probes: derive adversarial probes/risks from the approved approach
before the completeness gate."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import Approach, BrainstormSession
from mokata.engine import Probe, derive_probes, pre_mortem


def handoff_with_cons():
    s = BrainstormSession("payments")
    s.propose_approaches([
        Approach("stripe", "use Stripe",
                 pros=["fast to ship"], cons=["vendor lock-in", "fees"]),
        Approach("inhouse", "build it", pros=["control"], cons=["PCI scope"]),
    ])
    s.approve("jas", "stripe")
    return s.handoff()


class TestProbeDerivation(unittest.TestCase):
    def test_probes_are_derived_from_the_approach_cons(self):
        probes = derive_probes(handoff_with_cons())
        risks = {p.risk for p in probes}
        # the chosen approach's cons become risk probes
        self.assertIn("vendor lock-in", risks)
        self.assertIn("fees", risks)

    def test_each_probe_is_a_question(self):
        for p in derive_probes(handoff_with_cons()):
            self.assertIsInstance(p, Probe)
            self.assertTrue(p.question.strip())

    def test_generic_premortem_angles_present_even_without_cons(self):
        s = BrainstormSession("thing")
        s.propose_approaches([
            Approach("a", "x", pros=["p"], cons=["c"]),
            Approach("b", "y", pros=["p"], cons=["c"]),
        ])
        s.approve("jas", "a")
        probes = derive_probes(s.handoff())
        # at least the standard failure/scale/rollback angles are always there
        self.assertTrue(len(probes) >= 3)

    def test_premortem_summary_names_the_approved_approach(self):
        result = pre_mortem(handoff_with_cons())
        self.assertEqual(result.approach, "stripe")
        self.assertIn("stripe", result.summary())
        self.assertTrue(result.probes)


if __name__ == "__main__":
    unittest.main()
