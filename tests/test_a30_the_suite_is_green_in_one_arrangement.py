"""The suite is green in ONE arrangement, and nothing chose it.

0.0.21 stage 04, from `A-TEST-PASSES-OR-FAILS-BY-WHAT-RAN-BEFORE-IT` (doc 84).

⛔ **THE ROW'S INSTANCE WAS NOT AN ORDERING AT ALL, AND THE GROUNDING IS WHAT FOUND THAT.** The row
says its victim *"passes alone"* and reds inside `precut.py`'s 57-module set. Measured at `50d1e37` in
all four invocation forms — single test id, class, module, `discover` — it was **RED in every one**,
and red at `1d8b9de` too. It asked the environment for its own precondition: the test needed *"a
command that DOES resolve"* (its words) and built it from `harness_setup.resolved_console_script`,
whose documented third rung is *"the bare name as a last resort"*. On a box without mokata's console
scripts the command did not resolve, `hooks-not-firing` fired first, and **the per-shell check the test
exists to grade was never reached.** So *"passes alone"* was a property of the machine it was measured
on, not of the test — and the fix the row prescribes (*"make the victim state its precondition rather
than inherit it"*) was the right fix for the wrong inherited thing.

⭐ **BUT THE INSTANCE WAS NEVER THE FINDING. The finding is what the GREEN is worth.** The full suite
runs in alphabetical discovery order; nothing chose that order and nothing pins it. Every cross-module
residue in this tree is invisible until the order moves, and a suite that is green *in one arrangement*
is one rename away from telling a different story. `scripts/shuffle-order.py` moves it, reproducibly.

WHAT THIS FILE GRADES
---------------------
  * **the seed reproduces** — a seed you cannot replay is not a seed, and the sort-before-shuffle is
    load-bearing for that (`random.shuffle` permutes whatever order it is handed).
  * **the tool FINDS A PLANTED RESIDUE** — the anti-vacuity control, and the reason the tool has a
    declared tests-dir seam at all. An instrument nobody has watched succeed is not an instrument.
  * **a both-order failure is NOT reported as an ordering finding** — the noise the first version of
    the tool drowned in. Four environment reds on this VM meant a ⚠ on every run, which trains a
    reader to ignore it and costs every other finding it would make (§7i).
  * **a residue the ALPHABET CREATES is found too** — the difference is taken both ways, and a
    shuffle-only report would never see it.
  * **the parse reconciles or there is NO VERDICT** — the failing ids come from parsing unittest's
    output, and a parse that lost one would UNDER-report, which is the silent direction (§7e).
  * **the victim of the row is order-independent now**, in every invocation form.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "shuffle-order.py"


def _tool():
    """The tool as a module, so its pure parts can be graded without a subprocess."""
    spec = importlib.util.spec_from_file_location("_shuffle_order", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_tool(tests_dir, *args):
    env = {**os.environ, "MOKATA_SHUFFLE_TESTS_DIR": str(tests_dir),
           "PYTHONPATH": str(ROOT / "src")}
    # utf-8 on BOTH ends: the tool writes UTF-8 (see its `_utf8_stdio`), and decoding it with the
    # locale codec (cp1252 on a Windows runner) would garble the very glyphs the asserts read.
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, cwd=str(ROOT), check=False)


def _plant(d, *, victim_fails_when_after):
    """A miniature tests/ tree with a PLANTED cross-module residue.

    `test_zz_leaver` writes a process-global marker; `test_aa_victim` asserts on it. Because `aa` sorts
    BEFORE `zz`, alphabetical order runs the victim FIRST and it passes; a shuffle that puts `zz` first makes
    it fail. ⚠ The fixture modules are named `test_*` because that is what the tool SELECTS — the first
    version of this fixture used bare names, the tool found zero modules, refused under its own floor,
    and every assertion in this class failed against an empty stdout. The fixture was wrong, not the tool. `victim_fails_when_after` picks which way round the assertion points, so the same fixture
    can plant a residue the shuffle exposes OR one the alphabet creates."""
    d = Path(d)
    (d / "_marker.py").write_text("SEEN = []\n", encoding="utf-8")
    (d / "test_zz_leaver.py").write_text(textwrap.dedent('''
        import unittest, _marker
        class TheLeaver(unittest.TestCase):
            def test_it_leaves_process_global_state_behind(self):
                _marker.SEEN.append("zz")      # never cleaned up — that IS the defect
                self.assertTrue(True)
    '''), encoding="utf-8")
    # ⚠ INVERTED ONCE, AND RECORDED. `victim_fails_when_after=True` means the victim must FAIL when the
    # leaver ran BEFORE it — so it asserts the marker is EMPTY, which passes alphabetically (victim
    # first) and fails when a shuffle puts the leaver first. The first version had this the other way
    # round and the "shuffle exposes" test searched forty seeds for a case the fixture could not
    # produce, while the "alphabet creates" test failed for the same reason. The fixture was wrong,
    # not the tool — and the tool's output is what showed it, by reporting the opposite direction.
    cond = "not " if victim_fails_when_after else ""
    (d / "test_aa_victim.py").write_text(textwrap.dedent('''
        import unittest, _marker
        class TheVictim(unittest.TestCase):
            def test_it_reads_state_it_did_not_set(self):
                self.assertTrue(%sbool(_marker.SEEN),
                                "the victim's verdict depends on whether zz_leaver ran first")
    ''') % cond, encoding="utf-8")
    # Filler, so the selection clears the tool's own floor.
    for i in range(10):
        (d / ("test_mm_filler%02d.py" % i)).write_text(textwrap.dedent('''
            import unittest
            class Filler(unittest.TestCase):
                def test_nothing_interesting(self):
                    self.assertEqual(1, 1)
        '''), encoding="utf-8")
    return d


class TheSeedReproduces(unittest.TestCase):

    def setUp(self):
        self.t = _tool()

    def test_the_same_seed_gives_the_same_order(self):
        mods = ["m%02d" % i for i in range(20)]
        self.assertEqual(self.t.shuffled(mods, 42), self.t.shuffled(mods, 42))

    def test_a_different_seed_gives_a_different_order(self):
        """The control: an order that ignored the seed would satisfy the test above."""
        mods = ["m%02d" % i for i in range(20)]
        self.assertNotEqual(self.t.shuffled(mods, 42), self.t.shuffled(mods, 43))

    def test_the_shuffle_SORTS_FIRST_so_the_seed_survives_a_different_listing(self):
        """⚠ LOAD-BEARING. `random.shuffle` permutes whatever order it is handed, so without the sort
        the same seed would reproduce a DIFFERENT order on a filesystem that lists differently — and
        a seed you cannot replay is not a seed. This is the one property the whole tool rests on."""
        mods = ["m%02d" % i for i in range(20)]
        self.assertEqual(self.t.shuffled(mods, 7), self.t.shuffled(list(reversed(mods)), 7))

    def test_it_permutes_rather_than_dropping_or_duplicating(self):
        mods = ["m%02d" % i for i in range(20)]
        self.assertEqual(sorted(self.t.shuffled(mods, 7)), sorted(mods))

    def test_a_selection_under_the_floor_is_REFUSED(self):
        """A shuffle over a handful of modules cannot express a cross-module residue, so a green
        there would be a confident answer to a question nobody asked."""
        with tempfile.TemporaryDirectory() as d:
            for i in range(3):
                (Path(d) / ("test_x%d.py" % i)).write_text("", encoding="utf-8")
            proc = _run_tool(d, "--seed", "1")
            self.assertEqual(2, proc.returncode)
            self.assertIn("REFUSING", proc.stderr)


class TheToolFindsAPlantedResidue(unittest.TestCase):
    """⭐ THE ANTI-VACUITY CONTROLS. A finding tool that has never been watched finding anything is
    not an instrument — and this is why the tool has a declared tests-dir seam."""

    def test_a_residue_the_SHUFFLE_exposes_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d, victim_fails_when_after=True)
            # seed chosen by search below; any seed that puts zz_leaver before aa_victim will do.
            found = None
            for seed in range(1, 40):
                proc = _run_tool(d, "--seed", str(seed))
                if "fail ONLY in the shuffled order" in proc.stdout:
                    found = (seed, proc)
                    break
            self.assertIsNotNone(found, "no seed in 1..39 exposed a PLANTED residue — the tool "
                                        "cannot find the thing it exists to find")
            seed, proc = found
            self.assertEqual(1, proc.returncode)
            self.assertIn("test_aa_victim.TheVictim.test_it_reads_state_it_did_not_set", proc.stdout)
            self.assertIn("Reproduce: python3 scripts/shuffle-order.py --seed %d" % seed,
                          proc.stdout)

    def test_a_residue_the_ALPHABET_creates_is_reported_too(self):
        """The other direction, which a shuffle-only report would never see: here the victim passes
        only when the leaver HAS run, so alphabetical order (victim first) is the failing one."""
        with tempfile.TemporaryDirectory() as d:
            _plant(d, victim_fails_when_after=False)
            found = None
            for seed in range(1, 40):
                proc = _run_tool(d, "--seed", str(seed))
                if "fail ONLY in alphabetical order" in proc.stdout:
                    found = proc
                    break
            self.assertIsNotNone(found, "a residue the ALPHABET creates was never reported")
            self.assertIn("test_aa_victim.TheVictim.test_it_reads_state_it_did_not_set", found.stdout)

    def test_a_test_that_fails_in_BOTH_orders_is_NOT_an_ordering_finding(self):
        """🔴 THE NOISE THE FIRST VERSION DROWNED IN. On this VM four environment reds meant a ⚠ on
        every run, which trains a reader to ignore the tool and costs every other finding it would
        ever make (§7i). A red test is a red test; some other instrument owns it."""
        with tempfile.TemporaryDirectory() as d:
            _plant(d, victim_fails_when_after=True)
            (Path(d) / "test_nn_always_red.py").write_text(textwrap.dedent('''
                import unittest
                class AlwaysRed(unittest.TestCase):
                    def test_it_fails_in_every_order(self):
                        self.fail("nothing to do with ordering")
            '''), encoding="utf-8")
            proc = _run_tool(d, "--seed", "1")
            self.assertIn("fail in BOTH orders", proc.stdout)
            self.assertIn("test_nn_always_red.AlwaysRed.test_it_fails_in_every_order", proc.stdout)
            for line in proc.stdout.splitlines():
                if "ONLY in the shuffled order" in line or "ONLY in alphabetical order" in line:
                    self.assertNotIn("test_nn_always_red", proc.stdout.split(line)[1].split("Reproduce")[0])

    def test_a_parse_that_does_NOT_reconcile_yields_NO_VERDICT(self):
        """🔴 ADDED AFTER A MUTANT SURVIVED. Dropping the reconciliation was GREEN against the first
        version of this file — so the one guard standing between this tool and silently UNDER-reporting
        a difference was ungraded, which is §7e in the instrument built to grade §7i.

        The offender is realistic, not contrived: a test that PASSES while printing unittest-shaped
        output. This session's own pre-cut runner read a captured subprocess's `FAILED` line as its
        parent's verdict, which is the same mistake one layer up."""
        with tempfile.TemporaryDirectory() as d:
            for i in range(10):
                (Path(d) / ("test_clean%02d.py" % i)).write_text(textwrap.dedent('''
                    import unittest
                    class Clean(unittest.TestCase):
                        def test_ok(self):
                            self.assertEqual(1, 1)
                '''), encoding="utf-8")
            (Path(d) / "test_zz_noisy.py").write_text(textwrap.dedent('''
                import sys, unittest
                class Noisy(unittest.TestCase):
                    def test_it_prints_unittest_shaped_output_while_passing(self):
                        # A captured subprocess, a doctest, a fixture log — any of these can carry
                        # this shape. The parse must not silently absorb it.
                        # ⚠ THE LEADING NEWLINE MATTERS, and finding that out was worth the trip:
                        # without it the line lands amid unittest's progress dots (`....FAIL: ...`)
                        # and the parse correctly ignores it. Real unittest failures always start a
                        # line, so the parser is right to anchor there — and the offender this test
                        # plants has to look like the real thing to grade anything.
                        print("\nFAIL: test_something (some.module.SomeClass)", file=sys.stderr)
                        self.assertTrue(True)
            '''), encoding="utf-8")
            proc = _run_tool(d, "--seed", "3")
            self.assertEqual(2, proc.returncode,
                             "an unreconciled parse must be NO VERDICT (2), not a confident 0 or 1")
            self.assertIn("NO VERDICT", proc.stdout)
            self.assertIn("parse did NOT reconcile", proc.stdout)
            self.assertIn("UNDER-report", proc.stdout,
                          "and it must name the direction it refuses to be wrong in")

    def test_a_clean_tree_reports_NO_order_dependent_test_and_says_it_is_a_sample(self):
        """The control on all of the above: a tool that reported a finding unconditionally would pass
        every test in this class. And the message must not overclaim — one green seed is one sample."""
        with tempfile.TemporaryDirectory() as d:
            for i in range(10):
                (Path(d) / ("test_clean%02d.py" % i)).write_text(textwrap.dedent('''
                    import unittest
                    class Clean(unittest.TestCase):
                        def test_ok(self):
                            self.assertEqual(1, 1)
                '''), encoding="utf-8")
            proc = _run_tool(d, "--seed", "5")
            self.assertEqual(0, proc.returncode)
            self.assertIn("NO ORDER-DEPENDENT TEST AT THIS SEED", proc.stdout)
            self.assertIn("this is a search, not a proof", proc.stdout)


class TheVictimOfTheRowIsOrderIndependentNow(unittest.TestCase):
    """The row's own instance, graded where it lives rather than through the tool."""

    VICTIM = ("test_hook_shell_agnostic.TestDoctorPerShellFinding"
              ".test_doctor_surfaces_the_finding_end_to_end")

    def _run(self, *names):
        return subprocess.run(
            [sys.executable, "-m", "unittest", *names],
            capture_output=True, text=True, cwd=str(ROOT / "tests"),
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, check=False)

    def test_it_passes_alone_which_is_what_the_row_claimed_and_measured_false(self):
        self.assertEqual(0, self._run(self.VICTIM).returncode,
                         "the row said this passed alone; it did not, in any invocation form")

    def test_it_passes_beside_the_module_that_precedes_it_in_the_failing_batch(self):
        self.assertEqual(0, self._run("test_hook_secret_guard",
                                      "test_hook_shell_agnostic").returncode)

    def test_it_does_not_ask_the_ENVIRONMENT_for_its_own_precondition(self):
        """⛔ The actual defect, pinned so it cannot come back. The test needed a command that
        resolves and obtained it from a resolver documented to fall back to a bare name — so on a box
        without mokata's console scripts it graded `hooks-not-firing` instead of its subject, and said
        `AssertionError` rather than "my fixture is broken"."""
        src = (ROOT / "tests" / "test_hook_shell_agnostic.py").read_text(encoding="utf-8")
        body = src.split("def test_doctor_surfaces_the_finding_end_to_end", 1)[1].split("def test_", 1)[0]
        self.assertIn("_a_launchable_program", body,
                      "the wired program must be CONSTRUCTED in the test's own temp dir")
        self.assertNotIn("resolved_console_script", body,
                         "asking the environment for a precondition is how this was misfiled as an "
                         "ORDERING defect for eight days")
        self.assertIn('assertNotIn("hooks-not-firing"', body,
                      "and the precondition must ASSERT itself, so a broken fixture says so (§7g)")


if __name__ == "__main__":
    unittest.main()
