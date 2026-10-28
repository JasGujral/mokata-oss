"""0.0.22 stage B1 — `MACOS-IS-THE-CUT-MACHINE-AND-HAS-NO-LEG`.

Every cut is taken on macOS; until this stage nothing ran the suite there. This file pins the leg's
SHAPE (it exists, it blocks, it is mirror-only by a loud refusal rather than a green skip) and its
REACH (the two host-dependent regressions of 2026-10-04 are inside what it runs). Whether it REDS
when those fixes are reverted is a fact about a macOS runner and is recorded at its first run
(exit criterion 3), not claimed here.
"""
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")
MIRROR = "JasGujral/mokata-oss"
MACOS = {"os": "macos-latest", "python": "3.12", "jsonschema": "present"}


def _job():
    with open(CI_YML, encoding="utf-8") as fh:
        return safe_load(fh.read(), "grade the macOS leg")["jobs"]["test"]


class MacosIsTheCutMachineAndHasNoLeg(unittest.TestCase):

    def setUp(self):
        self.job = _job()

    def test_macos_is_the_cut_machine_and_has_a_leg(self):
        """Sharded since 0.0.26 O6: the macOS leg is one include PER SHARD, and together they must
        cover every shard the matrix lists — a missing shard is a slice of the suite macOS skips."""
        include = self.job["strategy"]["matrix"].get("include") or []
        mac = [{k: str(v) for k, v in e.items()} for e in include
               if {k: str(e.get(k)) for k in MACOS} == MACOS]
        self.assertTrue(mac, "no macos-latest leg on the PR gate — the cut machine is ungraded again")
        self.assertEqual(sorted(str(s) for s in self.job["strategy"]["matrix"]["shard"]),
                         sorted(e.get("shard") for e in mac),
                         "the macOS leg does not run every shard of the unit suite")

    def test_the_leg_blocks(self):
        for e in self.job["strategy"]["matrix"].get("include") or []:
            if e.get("os") == "macos-latest":
                self.assertNotIn("continue-on-error", e)
        self.assertNotIn("continue-on-error", self.job)

    def test_off_the_mirror_it_REFUSES_rather_than_reading_green(self):
        """Ruling R5 (billing): mirror only. The guard is the FIRST step, so nothing is installed
        or billed past it, and it exits 1: a skipped macOS leg would read GREEN."""
        first = self.job["steps"][0]
        cond = first.get("if", "")
        self.assertIn("runner.os == 'macOS'", cond)
        self.assertIn("github.repository != '%s'" % MIRROR, cond)
        self.assertIn("exit 1", first.get("run", ""))
        self.assertNotIn("exit 0", first.get("run", ""))

    def test_the_guard_is_a_no_op_on_every_other_leg(self):
        """Negative: the condition names macOS, so the Linux and Windows legs never reach it."""
        self.assertNotIn("ubuntu", self.job["steps"][0]["if"])
        self.assertNotIn("Windows", self.job["steps"][0]["if"])

    def test_the_leg_runs_the_whole_unit_suite_unconditionally(self):
        # Sharded since 0.0.26 O6: each shard leg runs its slice, all shards together the suite.
        steps = [s for s in self.job["steps"]
                 if s.get("run", "").strip().startswith(
                     "python scripts/shard_tests.py run --shard ${{ matrix.shard }} ")
                 and "-s tests -t tests" in s.get("run", "")]
        self.assertEqual(1, len(steps), "the unit-suite step moved — re-derive this pin")
        self.assertNotIn("if", steps[0], "the unit suite became conditional: some leg skips it")

    def test_the_two_host_regressions_of_2026_10_04_are_inside_its_reach(self):
        """Reach, not result: the tests that went red on macOS alone exist under the suite the leg
        discovers. If either is renamed away, the leg's first-run evidence loses its subject."""
        for module, name in (
                ("test_a41_the_retry_reads_the_head.py", "def test_the_git_remedies_are_real_git_commands"),
                ("test_a39_the_event_stream_is_typed_and_derived.py",
                 "def test_the_ledger_root_is_derived_structurally_and_refuses_a_foreign_path")):
            with self.subTest(module=module):
                with open(os.path.join(ROOT, "tests", module), encoding="utf-8") as fh:
                    self.assertIn(name, fh.read())


if __name__ == "__main__":
    unittest.main()
