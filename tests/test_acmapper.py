"""D3 — AC-mapper: statically map each acceptance criterion to a test; flag any AC
with no mapped test. The traceability backbone."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.engine import (
    AcceptanceCriterion,
    Spec,
    TestRef,
    map_acceptance_criteria,
    scan_tests,
)


def spec3():
    return Spec(title="login", criteria=[
        AcceptanceCriterion("AC-1", "user can log in"),
        AcceptanceCriterion("AC-2", "user can log out"),
        AcceptanceCriterion("AC-3", "bad password is rejected"),
    ])


class TestMapping(unittest.TestCase):
    def test_unmapped_ac_is_flagged(self):
        tests = [
            TestRef("test_login", ["AC-1"]),
            TestRef("test_logout", ["AC-2"]),
        ]
        result = map_acceptance_criteria(spec3(), tests)
        self.assertFalse(result.fully_mapped)
        self.assertEqual(result.unmapped_ids, ["AC-3"])

    def test_full_coverage_passes(self):
        tests = [
            TestRef("test_login", ["AC-1"]),
            TestRef("test_logout", ["AC-2"]),
            TestRef("test_bad_pw", ["AC-3"]),
        ]
        result = map_acceptance_criteria(spec3(), tests)
        self.assertTrue(result.fully_mapped)
        self.assertEqual(result.unmapped_ids, [])
        self.assertEqual(result.coverage, 1.0)

    def test_one_test_can_cover_several_acs(self):
        tests = [TestRef("test_flow", ["AC-1", "AC-2", "AC-3"])]
        result = map_acceptance_criteria(spec3(), tests)
        self.assertTrue(result.fully_mapped)

    def test_mapping_records_the_tests_per_ac(self):
        tests = [TestRef("test_login", ["AC-1"]),
                 TestRef("test_login_again", ["AC-1"])]
        result = map_acceptance_criteria(spec3(), tests)
        by_id = {m.ac.id: m for m in result.mappings}
        self.assertEqual([t.name for t in by_id["AC-1"].tests],
                         ["test_login", "test_login_again"])
        self.assertFalse(by_id["AC-2"].mapped)


class TestStaticScan(unittest.TestCase):
    def test_scan_finds_ac_ids_in_test_functions(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_x.py"), "w", encoding="utf-8") as fh:
                fh.write(
                    "def test_login_works():  # AC-1\n"
                    "    assert True\n\n\n"
                    "def test_logout():\n"
                    "    # covers AC-2\n"
                    "    assert True\n"
                )
            refs = scan_tests(d, ["AC-1", "AC-2", "AC-3"])
            found = {r.name: r.ac_ids for r in refs}
            self.assertEqual(found.get("test_login_works"), ["AC-1"])
            self.assertEqual(found.get("test_logout"), ["AC-2"])

    def test_scan_does_not_confuse_ac_1_with_ac_10(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_y.py"), "w", encoding="utf-8") as fh:
                fh.write("def test_ten():  # AC-10\n    assert True\n")
            refs = scan_tests(d, ["AC-1", "AC-10"])
            ten = next(r for r in refs if r.name == "test_ten")
            self.assertEqual(ten.ac_ids, ["AC-10"])

    def test_scan_then_map_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_z.py"), "w", encoding="utf-8") as fh:
                fh.write("def test_a():  # AC-1\n    assert True\n")
            refs = scan_tests(d, ["AC-1", "AC-2", "AC-3"])
            result = map_acceptance_criteria(spec3(), refs)
            self.assertEqual(result.unmapped_ids, ["AC-2", "AC-3"])


if __name__ == "__main__":
    unittest.main()
