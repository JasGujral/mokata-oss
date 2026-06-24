"""D5 — spec-compliance review: built code matches the spec; flags any feature not
traceable to an acceptance criterion (reuses the AC-mapper)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.engine import (
    AcceptanceCriterion,
    FeatureRef,
    Spec,
    spec_compliance_review,
)


def spec2():
    return Spec("auth", [AcceptanceCriterion("AC-1", "log in"),
                         AcceptanceCriterion("AC-2", "log out")])


class TestSpecCompliance(unittest.TestCase):
    def test_flags_feature_not_mapped_to_any_ac(self):
        features = [FeatureRef("login", ["AC-1"]),
                    FeatureRef("logout", ["AC-2"]),
                    FeatureRef("analytics", [])]          # extra, untraceable
        result = spec_compliance_review(spec2(), features)
        self.assertTrue(result.has_unspecified)
        self.assertTrue(any(f.kind == "unspecified-feature" and f.ref == "analytics"
                            for f in result.findings))

    def test_flags_feature_mapped_only_to_unknown_ac(self):
        features = [FeatureRef("login", ["AC-1"]),
                    FeatureRef("logout", ["AC-2"]),
                    FeatureRef("rogue", ["AC-99"])]       # references a non-existent AC
        result = spec_compliance_review(spec2(), features)
        self.assertTrue(any(f.ref == "rogue" for f in result.findings))

    def test_flags_unimplemented_ac(self):
        spec = Spec("auth", [AcceptanceCriterion("AC-1"), AcceptanceCriterion("AC-2"),
                             AcceptanceCriterion("AC-3")])
        result = spec_compliance_review(spec, [FeatureRef("login", ["AC-1"])])
        unimpl = [f.ref for f in result.findings if f.kind == "unimplemented-ac"]
        self.assertIn("AC-2", unimpl)
        self.assertIn("AC-3", unimpl)

    def test_compliant_when_features_and_acs_line_up(self):
        features = [FeatureRef("login", ["AC-1"]), FeatureRef("logout", ["AC-2"])]
        result = spec_compliance_review(spec2(), features)
        self.assertTrue(result.compliant)
        self.assertEqual(result.findings, [])


if __name__ == "__main__":
    unittest.main()
