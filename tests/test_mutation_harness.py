"""Self-tests for `scripts/mutate.sh` — the harness the mutation passes are graded by.

A mutation harness is an INSTRUMENT, and an instrument that reads wrong is worse than none at all:
every pin it ever graded inherits its credibility. This harness produced a FALSE GREEN on a
size-preserving mutant (a pure statement reorder), which was the fourth integrity failure in a
row, so its five fixes are pinned here rather than left as comments in a shell script.

The load-bearing pin is `TestTheStaleBytecodeDefect`, and it is deliberately shaped like the bug:
a PURE REORDER of two statements — the same bytes, moved — so the mutated file is byte-for-byte
the same SIZE as the pristine one. CPython validates a cached `.pyc` on nothing but the source's
size and its mtime truncated to whole SECONDS, so a size-preserving mutation applied inside the
same second as the previous compile of that file reuses the PRISTINE bytecode and passes.

Reproducing that faithfully needs the whole SHAPE of the batch that produced it, not just the
reorder, and each of the six iterations replays it in three steps:

  1. FLUSH — a size-CHANGING victim mutation. Without this the loop cannot reproduce the bug at
     all: a size-preserving mutant's bytecode survives its own restore (the mirror hazard), so the
     cache would hold the MUTANT and there would never be a PRISTINE pyc to go stale against. In
     the real batch this was M4, whose byte-count change is what invalidated the cache.
  2. DECOY — mutate a DIFFERENT module, so the victim is imported PRISTINE and its bytecode is
     re-stamped at the victim's current mtime. In the real batch this was M5/M6/M7, three
     consecutive mutants on `item.py` while `store.py` sat pristine.
  3. REORDER — the size-preserving mutation, landing in the same integer second. In the real batch
     this was M8, and it reported GREEN having never been compiled. This is the step under test.

That ordering is not incidental; drop step 1 or step 2 and the pin silently stops reproducing
anything, which is why both are spelled out rather than inlined. Six iterations because on an
unfixed harness the second-collision is probabilistic per iteration (~0.2s apart, so usually but
not always the same second). Under the fix all six are deterministic, because the fix removes the
timing dependence rather than narrowing it — and both halves of that fix are pinned outright by
`test_no_bytecode_compiled_from_a_mutated_source_is_left_behind`, which fails the moment either is
reverted, so the suite does not rest on a probabilistic pin alone.

`TestTheHarnessCanStillSayGreen` is the completeness check, and it is not a formality. Every other
test here asserts RED or BROKEN, so a harness that had regressed into answering RED unconditionally
would sail through all of them. Something no test covers must still come back GREEN, or the
instrument has stopped discriminating and its REDs mean nothing either.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTATE_SH = os.path.join(ROOT, "scripts", "mutate.sh")

# The fixture the harness is pointed at. `snapshot` is ordering-sensitive in exactly the way the
# real defect was: the durable read must happen AFTER the mutation of the thing being read, so
# swapping the two lines is observable. The two lines are a permutation of each other's bytes,
# which is what makes the mutation SIZE-PRESERVING and therefore invisible to pyc invalidation.
VICTIM = '''\
"""Fixture module for the mutation-harness self-tests."""


def snapshot(state):
    state["items"].append("new")
    state["saved"] = list(state["items"])
    return state


def never_exercised(flag):
    """Nothing in the fixture suite calls this — it is the honest-survivor case."""
    if flag:
        return "yes"
    return "no"
'''

# A SECOND module, mutated between the victim's mutations. Its only job is to make the suite
# import `victim` while `victim` is PRISTINE, which is what stamps pristine bytecode with the
# victim's current mtime — the precondition the false green actually needed. This mirrors the real
# batch, where three consecutive mutants touched `item.py` while `store.py` sat pristine, and the
# mutant that then went green was the next one to touch `store.py`.
DECOY = '''\
"""Second fixture module — mutated so the victim is imported while pristine."""


def label(n):
    if n > 0:
        return "many"
    return "none"
'''

DECOY_OLD = 'return "many"'
DECOY_NEW = 'return "some"'

VICTIM_TEST = '''\
import unittest

import decoy
import victim


class TestSnapshot(unittest.TestCase):
    def test_the_saved_copy_includes_the_appended_item(self):
        out = victim.snapshot({"items": ["a"]})
        self.assertIn("new", out["saved"],
                      "the snapshot was taken BEFORE the append — write-ordering defect")


class TestDecoy(unittest.TestCase):
    def test_label(self):
        self.assertEqual("many", decoy.label(3))
'''

# A size-CHANGING victim mutation (+1 byte). Its verdict is irrelevant; it exists to invalidate
# any cached victim bytecode, which is what lets the next pristine import actually recompile.
FLUSH_OLD = 'state["items"].append("new")'
FLUSH_NEW = 'state["items"].append("new2")'

# The size-preserving reorder: same characters, swapped lines.
ORDER_OLD = ('    state["items"].append("new")\n'
             '    state["saved"] = list(state["items"])')
ORDER_NEW = ('    state["saved"] = list(state["items"])\n'
             '    state["items"].append("new")')


def _read(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


def _read_bytes(path):
    return pathlib.Path(path).read_bytes()


def _run(tmp, label, old, new, pattern="test_victim.py", target=None):
    """Drive the real `scripts/mutate.sh` against the fixture and return its verdict line."""
    env = dict(os.environ, PYTHON=sys.executable, MUTATE_TESTS_DIR=tmp)
    env.pop("PYTHONDONTWRITEBYTECODE", None)   # the harness must set this itself, not inherit it
    proc = subprocess.run(
        [MUTATE_SH, label, target or os.path.join(tmp, "victim.py"), old, new, pattern],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    return proc.stdout.strip()


class _Fixture(unittest.TestCase):
    def setUp(self):
        if sys.platform.startswith("win"):
            self.skipTest("POSIX shell harness; the Windows legs do not run mutation passes")
        if not os.access(MUTATE_SH, os.X_OK):
            self.skipTest("scripts/mutate.sh is not executable in this checkout")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.victim = os.path.join(self.tmp, "victim.py")
        with open(self.victim, "w", encoding="utf-8") as fh:
            fh.write(VICTIM)
        with open(os.path.join(self.tmp, "decoy.py"), "w", encoding="utf-8") as fh:
            fh.write(DECOY)
        with open(os.path.join(self.tmp, "test_victim.py"), "w", encoding="utf-8") as fh:
            fh.write(VICTIM_TEST)

    def _flush_run(self, i):
        """A size-CHANGING victim mutation, which is the step that clears the cache.

        Without it the loop cannot reproduce the bug: a size-preserving mutant's own bytecode
        survives its restore (the mirror hazard), so the cache holds the MUTANT and the next
        iteration never sees a PRISTINE pyc to go stale against. In the real batch this step was
        M4, whose byte-count change is exactly what invalidated the pyc and let M5 recompile
        `store.py` pristine — which is the state M8 then went green against.
        """
        return _run(self.tmp, f"flush #{i}", FLUSH_OLD, FLUSH_NEW)

    def _decoy_run(self, i):
        """Import the victim while it is PRISTINE, stamping bytecode at its current mtime."""
        return _run(self.tmp, f"decoy #{i}", DECOY_OLD, DECOY_NEW,
                    target=os.path.join(self.tmp, "decoy.py"))


class TestTheStaleBytecodeDefect(_Fixture):
    """Holes 1 and 2 — the false GREEN, and the poisoned restore that mirrors it."""

    def test_a_size_preserving_reorder_is_RED_six_times_back_to_back(self):
        # THE REGRESSION PIN — flush, decoy, reorder, six times; see the module docstring for why
        # all three steps are needed. On an unfixed harness the reorder matches the pristine pyc on
        # both of the only two fields CPython checks (same size by construction, same integer
        # second because the steps take ~0.2s) and the mutant is never compiled at all. Under the
        # FIX this is deterministic: the pyc is deleted before the run and never written during it,
        # so no amount of clock collision can matter. Verified to CATCH a revert of either half.
        verdicts = []
        for i in range(6):
            self._flush_run(i)
            self._decoy_run(i)
            verdicts.append(_run(self.tmp, f"reorder #{i}", ORDER_OLD, ORDER_NEW))
        for i, line in enumerate(verdicts):
            self.assertTrue(line.startswith("RED"),
                            f"run #{i} did not kill a size-preserving mutant — stale bytecode is "
                            f"being reused again, and every GREEN this harness reports is now "
                            f"suspect. Got: {line!r}")

    def test_the_mutation_is_genuinely_size_preserving(self):
        # If this ever stops holding, the pin above silently stops testing what it claims to: a
        # size-CHANGING mutant is structurally immune (size alone invalidates the pyc), so it
        # would pass while proving nothing about the defect.
        self.assertEqual(len(ORDER_OLD), len(ORDER_NEW),
                         "the reorder changed the byte count, so it no longer reproduces the "
                         "condition the false green needed")
        pristine = _read(self.victim)
        self.assertEqual(len(pristine), len(pristine.replace(ORDER_OLD, ORDER_NEW)),
                         "mutating the fixture changed its size")

    def test_no_bytecode_compiled_from_a_mutated_source_is_left_behind(self):
        # The mirror hazard: a mutant pyc surviving the restore leaves a CLEAN working tree
        # executing code that is not in it. Nothing may outlive the run.
        _run(self.tmp, "reorder", ORDER_OLD, ORDER_NEW)
        cache = os.path.join(self.tmp, "__pycache__")
        leftovers = [f for f in os.listdir(cache) if f.startswith("victim.")] \
            if os.path.isdir(cache) else []
        self.assertEqual([], leftovers,
                         "bytecode for the mutated module outlived the run; a later import can "
                         "pick it up against the RESTORED source and execute the mutant")

    def test_the_source_is_byte_identical_after_a_run(self):
        before = _read_bytes(self.victim)
        _run(self.tmp, "reorder", ORDER_OLD, ORDER_NEW)
        self.assertEqual(before, _read_bytes(self.victim), "the restore was not faithful")
        self.assertFalse(os.path.exists(self.victim + ".bak"), "a .bak was left behind")


class TestTheHarnessCanStillSayGreen(_Fixture):
    """The completeness check — every other test here asserts RED or BROKEN."""

    def test_a_mutation_no_test_covers_is_reported_GREEN(self):
        line = _run(self.tmp, "uncovered branch", 'return "yes"', 'return "no!"')
        self.assertTrue(line.startswith("GREEN"),
                        "the harness answered something other than GREEN for a mutation nothing "
                        f"exercises — if it now says RED unconditionally, its REDs prove nothing "
                        f"and neither does any pass it graded. Got: {line!r}")
        self.assertIn("SURVIVOR", line)


class TestNoVerdictIsInventedFromNoEvidence(_Fixture):
    """Holes 4 and 5 — the two ways 'no result' used to be laundered into a verdict."""

    def test_a_mutation_that_cannot_be_applied_reports_BROKEN(self):
        line = _run(self.tmp, "bad pattern", "THIS_TEXT_IS_NOT_IN_THE_FILE", "x")
        self.assertTrue(line.startswith("BROKEN"),
                        f"a mutation that never applied produced a verdict: {line!r}")

    def test_a_pattern_matching_several_sites_reports_BROKEN(self):
        # Two edits is not the mutation that was described, so it gets no verdict.
        line = _run(self.tmp, "ambiguous", "    return ", "    return  ")
        self.assertTrue(line.startswith("BROKEN"),
                        f"a pattern hitting several sites produced a verdict: {line!r}")

    def test_a_test_pattern_matching_no_files_reports_BROKEN_not_GREEN(self):
        # `unittest` prints "Ran 0 tests ... OK" here, which the old harness read as a survivor.
        line = _run(self.tmp, "typo in pattern", ORDER_OLD, ORDER_NEW,
                    pattern="test_no_such_file_xyz.py")
        self.assertTrue(line.startswith("BROKEN"),
                        f"an empty test run was read as a verdict: {line!r}")
        self.assertNotIn("GREEN", line)

    def test_the_source_is_restored_even_when_the_mutation_fails(self):
        # Hole 3: the old harness restored only on the happy path, so ANY abort left the file
        # mutated and every later mutant in the batch was applied on top of it.
        before = _read_bytes(self.victim)
        _run(self.tmp, "bad pattern", "THIS_TEXT_IS_NOT_IN_THE_FILE", "x")
        self.assertEqual(before, _read_bytes(self.victim),
                         "an aborted mutation left the source modified — the next mutant in a "
                         "batch would be graded as a COMPOUND mutant nobody designed")
        self.assertFalse(os.path.exists(self.victim + ".bak"), "a .bak was left behind")


class TestTheHarnessIsShippable(unittest.TestCase):
    """It is a dev tool and ships PUBLIC — no internal-only exclusion, so nothing to keep in step."""

    def test_it_exists_and_is_executable(self):
        self.assertTrue(os.path.isfile(MUTATE_SH), "missing scripts/mutate.sh")
        self.assertTrue(os.access(MUTATE_SH, os.X_OK), "scripts/mutate.sh is not executable")

    def test_it_documents_the_defect_it_exists_to_prevent(self):
        src = _read(MUTATE_SH)
        for needle in ("PYTHONDONTWRITEBYTECODE", "trap restore EXIT INT TERM",
                       "size-preserving", "CONTRAPOSITIVE", "MIRROR HAZARD"):
            self.assertIn(needle, src,
                          f"{needle!r} is gone from the header — the reasoning that makes the "
                          f"fixes non-negotiable must not be simplified away")

    def test_it_hardcodes_no_developer_path(self):
        src = _read(MUTATE_SH)
        self.assertNotIn("/Users/", src, "a developer-specific path leaked into a shipped script")
        self.assertNotIn("jsvenv", src, "a developer-specific virtualenv leaked into the script")


if __name__ == "__main__":
    unittest.main()
