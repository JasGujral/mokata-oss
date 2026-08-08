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

---------------------------------------------------------------------------------------------
THE SIXTH HOLE — MUTATE-SH-NO-CONCURRENCY-INTERLOCK (0.0.17 stage 18), pinned from
`TestTheSnapshotIsNotRestoredOverSomebodyElsesWork` down.

The five holes above are all about BYTECODE. The sixth is about the SNAPSHOT, and it is the
same silent-corruption signature reached by a different door: the harness copies the target to
`<file>.bak`, and its EXIT trap restores that copy unconditionally. If ANYTHING else writes the
target while the run holds that snapshot, the restore reverts it — and because the restore puts
back the file the run itself started from, `git status` comes back CLEAN and `git diff` empty.
The work is simply gone, with no artefact anywhere saying so.

That is not hypothetical either: it happened at 0.0.17 stage 1a-FU, when a file was edited while
a mutation run had it snapshotted. It did not fire only because the run's verdict was read before
anyone touched the file again. Two near-misses on this one guardrail in two consecutive stages
is what promoted it from a rule in doc 85 §7b to a mechanism here.

WHY THE CHECK IS A CONTENT HASH AND MUST STAY ONE. The obvious cheap implementation is to stat
the file at snapshot time and compare mtime + size at restore time. That is EXACTLY the pair
CPython uses to validate a `.pyc`, and holes 1 and 2 above exist because a size-preserving edit
inside one integer second defeats it. Reproducing that comparison inside the guard AGAINST it
would be a joke with a straight face. So the harness records the sha256 of the bytes it itself
last wrote and re-hashes at restore time.
`test_a_size_preserving_third_party_edit_at_an_identical_mtime_is_still_caught` is the pin that
holds that line: its fixture forces the third party's edit to land at the SAME byte count and the
SAME mtime to the nanosecond, and asserts both of those facts before asserting the refusal — so
it is a direct demonstration that an mtime+size guard would have restored straight over the edit.

WHAT THE GUARD MUST NOT DO IS OVER-FIRE, and two pins bound it from the other side.
`test_an_edit_reverted_to_byte_identical_content_does_not_trip_the_guard` says the trigger is
changed BYTES, not "was written to" — a third party who edits and puts it back exactly has cost
nothing and must not be handed an error. And `test_a_run_killed_mid_flight_still_restores_cleanly`
is the case most likely to regress: a run killed at a timeout leaves the file holding exactly what
the harness last wrote, so the hash matches and the restore proceeds as it always did. That path
was observed working at 1a-FU and the new check must not be what breaks it.
"""

import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Overridable so the harness can be MUTATION-GRADED, which it otherwise cannot be.
#
# `scripts/mutate.sh` is the only sanctioned mutator (doc 85 §7b), so grading it means pointing it
# at itself — and bash reads a script lazily from a remembered file offset, so rewriting the file
# under the running interpreter corrupts the very run doing the grading. The batch therefore
# copies the script to a scratch path, mutates THE COPY, and points these tests at it through
# this variable. Nothing about the run under test changes: the copy is byte-identical apart from
# the one mutation being graded.
MUTATE_SH = os.environ.get("MUTATE_SH_UNDER_TEST") or os.path.join(ROOT, "scripts", "mutate.sh")

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


def _run_proc(tmp, label, old, new, pattern="test_victim.py", target=None, **env_extra):
    """Drive the real `scripts/mutate.sh` against the fixture and return the finished process."""
    # MUTATE_LOCKFILE keeps the harness's exclusive-run lock inside this test's own tmp dir. The
    # real default is one lock per checkout, which is correct for real runs — a mutation run
    # executes the whole suite, so two of them interleaving means each grades a tree carrying the
    # other's mutant. But it would make the SUITE non-hermetic: it would churn a file in the repo
    # root, leave one there if interrupted, and make two concurrent suite runs in one checkout
    # (two interpreters, say) fail each other on contention rather than on anything real.
    env = dict(os.environ, PYTHON=sys.executable, MUTATE_TESTS_DIR=tmp,
               MUTATE_LOCKFILE=os.path.join(tmp, ".mutate.lock"))
    env.pop("PYTHONDONTWRITEBYTECODE", None)   # the harness must set this itself, not inherit it
    env.update(env_extra)
    return subprocess.run(
        [MUTATE_SH, label, target or os.path.join(tmp, "victim.py"), old, new, pattern],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )


def _run(tmp, label, old, new, pattern="test_victim.py", target=None):
    """The verdict line alone — what most pins here care about."""
    return _run_proc(tmp, label, old, new, pattern, target).stdout.strip()


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


# ---------------------------------------------------------------------------------------------
# THE SIXTH HOLE — the snapshot interlock (0.0.17 stage 18, MUTATE-SH-NO-CONCURRENCY-INTERLOCK).
# ---------------------------------------------------------------------------------------------

# The interlock fixture is deliberately tiny. These pins are about the SNAPSHOT, not about
# verdicts, so the module needs nothing but one marker to mutate. That marker is size-preserving
# on purpose: every third-party edit below can then be made size-preserving too, which is the
# condition an mtime+size guard cannot see.
LOCK_VICTIM = 'MARKER = "pristine-aa"\n'
LOCK_OLD = '"pristine-aa"'
LOCK_NEW = '"mutated--aa"'

# The inner test the harness runs. Its whole job is to hand a THIRD PARTY a window INSIDE the
# run: the hook executes at exactly the moment `mutate.sh` is holding the `.bak` snapshot, which
# is precisely the state both 0.0.17 near-misses were in.
LOCK_TEST = '''\
import os
import unittest


class TestTheWindow(unittest.TestCase):
    def test_the_window(self):
        hook = os.environ.get("MUTATE_HOOK")
        if hook:
            with open(hook, encoding="utf-8") as fh:
                exec(compile(fh.read(), hook, "exec"), {"__name__": "mutate_hook"})
'''

# A third party replaces the file wholesale. Nothing subtle — this is the plain shape of "somebody
# saved an edit while a mutation run was up".
HOOK_THIRD_PARTY_EDIT = '''\
import os

with open(os.environ["MUTATE_VICTIM"], "w", encoding="utf-8") as fh:
    fh.write(%r)
'''

# The same thing, but handed the STRONGEST position an adversary can have: the same byte count,
# and the mtime put back to the nanosecond. On the two fields CPython uses to validate a `.pyc`
# — size, and mtime truncated to whole seconds — this edit is INDISTINGUISHABLE from the bytes
# the harness itself wrote. Only content identity can see it.
HOOK_SIZE_PRESERVING = '''\
import json
import os

target = os.environ["MUTATE_VICTIM"]
before = os.stat(target)
data = open(target, encoding="utf-8").read()
edited = data.replace('"mutated--aa"', '"3rdparty-aa"')
assert edited != data, "the fixture stopped editing anything"
assert len(edited) == len(data), "the fixture edit stopped being size-preserving"
with open(target, "w", encoding="utf-8") as fh:
    fh.write(edited)
os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
after = os.stat(target)
with open(os.environ["MUTATE_HOOK_REPORT"], "w", encoding="utf-8") as fh:
    json.dump({"size_before": before.st_size, "size_after": after.st_size,
               "mtime_ns_before": before.st_mtime_ns,
               "mtime_ns_after": after.st_mtime_ns}, fh)
'''

# Applied and reverted inside the same integer second, ending byte-for-byte where it started.
# Nothing was lost, so the guard must stay silent: the trigger is CHANGED BYTES, not "was
# written to". A guard that over-fires here gets switched off, and then it guards nothing.
HOOK_EDIT_THEN_REVERT = '''\
import os

target = os.environ["MUTATE_VICTIM"]
before = os.stat(target)
data = open(target, encoding="utf-8").read()
with open(target, "w", encoding="utf-8") as fh:
    fh.write(data.replace('"mutated--aa"', '"scratch--aa"'))
with open(target, "w", encoding="utf-8") as fh:
    fh.write(data)
os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
'''

# Holds the run open so the outer test can kill it, or race a second run against it.
#
# It runs from INSIDE the test phase (`LOCK_TEST` execs it), which is after the mutation has
# been applied AND after the script has finished recording what it applied. That is the right
# place for the interlock stories and the wrong place for the one below — see WINDOW_HOLDER.
HOOK_HOLD_OPEN = '''\
import time

time.sleep(120)
'''

# The same hold, but it ANNOUNCES that it is running before it parks. `HOOK_HOLD_OPEN` alone
# cannot tell the outer test whether the run has reached the test phase yet, so a test that needs
# the signal to land THERE — rather than in the mutator, which is where an idle machine puts it —
# has to guess, and guesses wrong on an idle machine and right on a loaded one. That is the shape
# of the load-dependence that made this whole defect read as a flake. Waiting on the marker makes
# "the run is inside its test phase" a fact instead of a probability.
HOOK_ANNOUNCE_THEN_HOLD = '''\
import os
import time

open(os.environ["MUTATE_HOOK_REPORT"], "w").write("in-the-test-phase")
time.sleep(120)
'''

# A stand-in `$PYTHON` that holds the run open INSIDE THE MUTATOR, which no hook can reach.
#
# `mutate.sh` invokes `$PYTHON` for two different jobs: the mutator (script on stdin, so argv
# starts with `-`) and the test runner (`-m unittest discover …`). This passes both straight
# through to the real interpreter — and then, if the target now holds the mutated bytes, it
# SLEEPS BEFORE EXITING. Its stdout is the command substitution's pipe, so the substitution
# cannot complete while it is alive, and the script is parked at exactly the instant the mutant
# is on disk and the run has not yet finished recording it.
#
# That instant is unreachable from `MUTATE_HOOK` (which fires during the tests, long after) and
# unreachable reliably by racing it — polling at 1 ms hits it about 90% of the time and the
# shipped 20 ms poll almost never, which is the whole reason this defect read as a flake for
# three stages. Holding it open makes it DETERMINISTIC, and it needs no change to mutate.sh:
# the hold is entirely inside a test-supplied interpreter.
# MUTATE_WINDOW_WHEN selects WHICH side of the apply to park on:
#
#   after-apply   the mutant is on disk and the run has recorded it. The window the shipped
#                 defect lived in.
#   before-apply  the run has recorded the mutant hash and NOTHING IS WRITTEN YET. The MIRROR
#                 window, and the one a naive fix opens while closing the other.
#
# ⚠ THE ARGV SHAPE IS LOAD-BEARING AND IT WENT STALE ONCE ALREADY, silently. This parked on
# `argv[1]` as the target, which was right while the mutator took `- <target> <old> <new>`.
# Splitting it into plan/apply made `argv[1]` the MODE, so the open() raised, the hold was
# skipped, and every window test went green without ever parking. Nothing failed; the tests
# simply stopped testing. It was caught only because a mutant that should have died survived.
# If the mutator's argv changes again, THIS is what to re-check first.
WINDOW_HOLDER = '''\
#!/usr/bin/env python3
import os
import subprocess
import sys
import time

real = os.environ["MUTATE_REAL_PYTHON"]
argv = sys.argv[1:]
hold = float(os.environ.get("MUTATE_WINDOW_HOLD", "30"))
when = os.environ.get("MUTATE_WINDOW_WHEN", "after-apply")

# The mutator is `- <mode> <target> <old> <new> [sha]`; the test runner is `-m unittest ...`.
if argv[:1] != ["-"]:
    sys.exit(subprocess.call([real] + argv))

mode = argv[1] if len(argv) >= 5 else ""
if mode not in ("plan", "apply"):
    # FAIL LOUD rather than pass the call through. A holder that no longer recognises the
    # mutator is a holder that no longer parks, and a window test that does not park is green
    # for the wrong reason — which is how this went unnoticed once already.
    sys.stderr.write("window-holder: unrecognised mutator argv %r\\n" % (argv,))
    sys.exit(97)

if mode == "apply" and when == "before-apply":
    time.sleep(hold)
rc = subprocess.call([real] + argv)
if mode == "apply" and when == "after-apply":
    time.sleep(hold)
sys.exit(rc)
'''

# A stand-in for util-linux `flock`, which stock macOS does not ship — without it the harness's
# flock branch would be untestable on this platform and would rot as dead code.
#
# It is a FAITHFUL stand-in, not a simulation. Real `flock -n <fd>` takes the lock on an
# already-open descriptor it inherited from the calling shell and then exits; the lock outlives
# it because the shell still holds that same open file description, and flock(2) locks belong to
# the OFD rather than to the process. `fcntl.flock` is that identical kernel primitive, so this
# exercises the real branch with real semantics.
FLOCK_SHIM = '''\
#!%s
import fcntl
import sys

args = [a for a in sys.argv[1:] if a != "-n"]
mode = fcntl.LOCK_EX | fcntl.LOCK_NB
if "-u" in args:
    mode = fcntl.LOCK_UN
    args = [a for a in args if a != "-u"]
try:
    fcntl.flock(int(args[0]), mode)
except OSError:
    sys.exit(1)
'''


class _InterlockFixture(unittest.TestCase):
    def setUp(self):
        if sys.platform.startswith("win"):
            self.skipTest("POSIX shell harness; the Windows legs do not run mutation passes")
        if not os.access(MUTATE_SH, os.X_OK):
            self.skipTest("scripts/mutate.sh is not executable in this checkout")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.victim = os.path.join(self.tmp, "victim.py")
        pathlib.Path(self.victim).write_text(LOCK_VICTIM, encoding="utf-8")
        pathlib.Path(os.path.join(self.tmp, "test_window.py")).write_text(
            LOCK_TEST, encoding="utf-8")
        self.hook = os.path.join(self.tmp, "hook.py")
        self.report = os.path.join(self.tmp, "hook-report.json")
        # Belt and braces. `_env` points the lock into `self.tmp`, so this should never find
        # anything; it is here so that dropping that override starts leaving visible residue in
        # the checkout rather than silently making the suite non-hermetic again.
        self.addCleanup(self._clear_lock_residue)

    @staticmethod
    def _clear_lock_residue():
        path = os.path.join(ROOT, ".mutate.lock")
        if os.path.exists(path):
            os.remove(path)

    def _mutant_text(self):
        """The bytes `mutate.sh` itself last wrote — the only content it may restore over."""
        return LOCK_VICTIM.replace(LOCK_OLD, LOCK_NEW)

    def _env(self, **extra):
        # Hermetic lock — see `_run` for why the real default lives at the checkout root instead.
        env = dict(os.environ, PYTHON=sys.executable, MUTATE_TESTS_DIR=self.tmp,
                   MUTATE_VICTIM=self.victim, MUTATE_HOOK_REPORT=self.report,
                   MUTATE_LOCKFILE=os.path.join(self.tmp, ".mutate.lock"))
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        env.pop("MUTATE_HOOK", None)
        env.update(extra)
        return env

    def _argv(self, label="interlock", target=None):
        return [MUTATE_SH, label, target or self.victim, LOCK_OLD, LOCK_NEW, "test_window.py"]

    def _run_with_hook(self, hook_src, label="interlock"):
        pathlib.Path(self.hook).write_text(hook_src, encoding="utf-8")
        return subprocess.run(self._argv(label), capture_output=True, text=True,
                              env=self._env(MUTATE_HOOK=self.hook), cwd=ROOT)

    def _spawn_holder(self, label="holder", **env_extra):
        """Start a run that parks inside the snapshot window until we kill it."""
        hook = os.path.join(self.tmp, "hold.py")
        pathlib.Path(hook).write_text(HOOK_HOLD_OPEN, encoding="utf-8")
        proc = subprocess.Popen(self._argv(label), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=self._env(MUTATE_HOOK=hook, **env_extra), cwd=ROOT,
                                start_new_session=True)
        self.addCleanup(self._reap, proc)
        return proc

    @staticmethod
    def _reap(proc):
        try:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            pass

    def _await_snapshot_window(self, path=None, timeout=60.0):
        """Block until the harness has applied its mutation — i.e. the snapshot is live."""
        path = path or self.victim
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if LOCK_NEW in pathlib.Path(path).read_text(encoding="utf-8"):
                    return True
            except OSError:
                pass
            time.sleep(0.02)
        return False


class TestTheSnapshotIsNotRestoredOverSomebodyElsesWork(_InterlockFixture):
    """The sixth hole: the EXIT trap used to put its `.bak` back no matter what happened."""

    def test_a_third_party_edit_during_the_run_is_refused_not_overwritten(self):
        theirs = 'MARKER = "work by somebody who was not this run"\n'
        proc = self._run_with_hook(HOOK_THIRD_PARTY_EDIT % (theirs,))
        out = proc.stdout + proc.stderr

        self.assertEqual(4, proc.returncode,
                         "the harness restored over an edit made underneath it, or reported the "
                         "refusal as success. A SILENT SKIP is the same defect wearing a "
                         f"different mask — it has to be loud and non-zero. Got: {out!r}")
        self.assertEqual(theirs, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "the third party's work was overwritten by the restore — the exact "
                         "failure this guard exists to prevent, and it leaves `git status` clean")
        preserved = self.victim + ".REFUSED-RESTORE.bak"
        self.assertTrue(os.path.exists(preserved),
                        "the pristine copy was dropped. BOTH survive, or the refusal has merely "
                        "traded one silent data loss for another")
        self.assertEqual(LOCK_VICTIM, pathlib.Path(preserved).read_text(encoding="utf-8"),
                         "the preserved copy is not the pristine source")
        self.assertFalse(os.path.exists(self.victim + ".bak"),
                         "the `.bak` was left under its ordinary name, where the next run will "
                         "silently clobber it")
        self.assertIn(os.path.basename(self.victim), out,
                      "the failure did not name the file it is about")
        self.assertIn(hashlib.sha256(self._mutant_text().encode("utf-8")).hexdigest(), out,
                      "the failure did not report the EXPECTED hash")
        self.assertIn(hashlib.sha256(theirs.encode("utf-8")).hexdigest(), out,
                      "the failure did not report the hash actually FOUND on disk")

    def test_a_size_preserving_third_party_edit_at_an_identical_mtime_is_still_caught(self):
        # THE PIN THAT HOLDS THE IMPLEMENTATION HONEST. The cheap version of this guard is
        # mtime + size, which is exactly the pair CPython uses for `.pyc` validity and exactly
        # the pair holes 1 and 2 above exist because of. This fixture forces the third party's
        # edit onto the same byte count AND the same mtime to the nanosecond, then asserts both
        # facts before asserting the refusal — so a green here is a direct demonstration that an
        # mtime+size guard would have restored straight over the edit.
        proc = self._run_with_hook(HOOK_SIZE_PRESERVING)
        out = proc.stdout + proc.stderr

        self.assertTrue(os.path.exists(self.report),
                        f"the fixture's third-party edit never completed, so this test proves "
                        f"nothing about the guard. Harness said: {out!r}")
        report = json.loads(pathlib.Path(self.report).read_text(encoding="utf-8"))
        self.assertEqual(report["size_before"], report["size_after"],
                         "the fixture edit changed the byte count, so it no longer reproduces "
                         "the condition an mtime+size guard is blind to")
        self.assertEqual(report["mtime_ns_before"], report["mtime_ns_after"],
                         "the fixture edit changed the mtime, so an mtime check would have "
                         "caught it and this test would prove nothing")
        self.assertEqual(4, proc.returncode,
                         "a size-preserving edit at an identical mtime was NOT caught. The "
                         "guard is comparing metadata, not content — which is the same "
                         f"blindness that produced the false GREEN. Got: {out!r}")
        self.assertIn("3rdparty-aa", pathlib.Path(self.victim).read_text(encoding="utf-8"),
                      "the size-preserving edit was overwritten by the restore")

    def test_no_verdict_is_printed_for_a_run_whose_target_moved_under_it(self):
        # The refusal goes to stderr at teardown, which is a moment too late to be the only
        # defence: the verdict has already gone to stdout by then, and stdout is what batch logs
        # and `_run()` read. A GREEN produced while somebody was editing the target is the FALSE
        # GREEN of hole 1 reached by a different cause — the tests graded a tree that was moving.
        # It gets BROKEN, which this file's whole vocabulary reserves for "no verdict was
        # produced", exactly as a pattern matching no tests does.
        proc = self._run_with_hook(HOOK_THIRD_PARTY_EDIT % ('MARKER = "theirs"\n',))
        self.assertIn("BROKEN", proc.stdout,
                      f"stdout carried no BROKEN marker for a run whose target moved: "
                      f"{proc.stdout!r}")
        for verdict in ("GREEN", "RED", "SURVIVOR"):
            self.assertNotIn(verdict, proc.stdout,
                             f"{verdict!r} was printed for a run that graded a moving tree. Any "
                             f"reader of stdout alone — a batch log, a helper that captures only "
                             f"stdout — now has a verdict that belongs to nothing: {proc.stdout!r}")

    def test_a_second_refusal_does_not_clobber_the_first_preserved_copy(self):
        # "Preserve both" has to survive somebody ignoring the first refusal and running again.
        # Otherwise run two's `.bak` — which by then holds run one's THIRD PARTY content — lands
        # on the preserved name and the original pristine file is gone: this stage's own failure
        # mode, committed by the code written to prevent it.
        # Both edits keep the mutable marker, or the second run's mutation cannot apply and it
        # never gets as far as a second refusal to test.
        first = LOCK_VICTIM + 'EXTRA = "the first third party"\n'
        second = LOCK_VICTIM + 'EXTRA = "the second third party"\n'
        self._run_with_hook(HOOK_THIRD_PARTY_EDIT % (first,))
        self._run_with_hook(HOOK_THIRD_PARTY_EDIT % (second,))

        preserved = sorted(f for f in os.listdir(self.tmp) if "REFUSED-RESTORE" in f)
        self.assertEqual(2, len(preserved),
                         f"the second refusal overwrote the first one's preserved copy, so the "
                         f"pristine source is gone. Found: {preserved!r}")
        kept = {pathlib.Path(os.path.join(self.tmp, f)).read_text(encoding="utf-8")
                for f in preserved}
        self.assertIn(LOCK_VICTIM, kept, "the original pristine source was the copy that was lost")
        self.assertIn(first, kept, "the first third party's work was the copy that was lost")

    def test_an_edit_reverted_to_byte_identical_content_does_not_trip_the_guard(self):
        # The other side of the boundary: the trigger is CHANGED BYTES, not "was written to".
        # Somebody who edited and put it back exactly has cost nothing and must not be handed a
        # failure.
        proc = self._run_with_hook(HOOK_EDIT_THEN_REVERT)
        out = proc.stdout + proc.stderr

        self.assertEqual(0, proc.returncode,
                         f"the guard refused although the bytes on disk were identical to what "
                         f"the harness wrote — nothing was at risk. Got: {out!r}")
        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "the restore did not happen even though it was safe")
        self.assertFalse(os.path.exists(self.victim + ".bak"), "a .bak was left behind")
        self.assertFalse(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"),
                         "a refusal artefact was left behind by a run that did not refuse")


class TestTheKilledRunStillRestores(_InterlockFixture):
    """The case most likely to regress — do not let the new check break the old trap."""

    def test_a_run_killed_mid_flight_still_restores_cleanly(self):
        # A run killed at a timeout restored correctly at 0.0.17 stage 1a-FU, and that is the
        # trap doing its job. Killed mid-run the file on disk IS what the harness last wrote, so
        # the hash matches and the restore proceeds exactly as before. If the integrity check
        # ever starts refusing here, every interrupted run leaves the tree MUTATED — strictly
        # worse than the hazard this stage set out to close.
        proc = self._spawn_holder()
        self.assertTrue(self._await_snapshot_window(),
                        "the harness never applied its mutation, so nothing was killed mid-flight")
        os.killpg(proc.pid, signal.SIGTERM)
        # 60, not 120, and the number is load-bearing (KILLED-RUN-TEST-TIMEOUT-RACE). The hook
        # this run is parked in sleeps for 120s, so a 120s timeout expires in the same instant as
        # the hook's own natural exit and the winner is scheduler noise — green on an idle laptop,
        # red on a CI runner. At 60 a timeout can only mean ONE thing, which is the thing this
        # test exists to assert: the SIGTERM was not honoured. A process that honours it dies in
        # milliseconds, so the margin costs nothing. Matches `_reap`'s own 60 above.
        proc.communicate(timeout=60)

        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "a killed run left the source MUTATED — the next mutant in the batch "
                         "would be graded as a compound mutant nobody designed")
        self.assertFalse(os.path.exists(self.victim + ".bak"), "a .bak was left behind")
        self.assertFalse(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"),
                         "the integrity check refused on the kill path; every interrupted run "
                         "now leaves the tree mutated, which is worse than what it replaced")


class TestTheSnapshotWindowHasNoBlindSpot(_InterlockFixture):
    """0.0.17 stage 18c — the run knows BOTH states it authored before the target can hold either.

    THE DEFECT, and it was live in `scripts/mutate.sh` for three stages. The script recorded what
    it had written only AFTER the mutator's command substitution returned. But the mutant bytes
    reach disk INSIDE that substitution, and bash defers a pending TERM trap to the end of the
    enclosing compound command — so a run killed in that gap ran `restore()` while the recorded
    hash was still the PRISTINE one. The content-identity check compared the mutant on disk
    against the pristine hash, concluded a third party had written, REFUSED, and exited 4.

    Every consequence is the opposite of what the check exists to do:

      * the tree is left MUTATED, which is hole 3 — the exact hazard the trap closes;
      * a `.REFUSED-RESTORE.bak` is left beside it, so the next run's snapshot is displaced;
      * the message accuses a third party — "Something wrote to <file> while this run held it
        snapshotted" — when nothing did. The run wrote it, itself, moments earlier;
      * exit 4 is the batch drivers' STOP-DEAD status, so an interrupted batch aborts citing a
        collision that never happened.

    It read as a flake for three stages because the window is short: racing it at the shipped
    20 ms poll almost never lands in it, at 1 ms about 90% of the time, and on a loaded CI runner
    often enough to redden `master`. `WINDOW_HOLDER` removes the race — it parks the run in the
    window, so these tests fail deterministically against the old script and pass against the new.

    THE FIX is not a wider window but no window at all: the mutation is PLANNED (hashed in memory,
    nothing written), both authored hashes are recorded, and only then is it APPLIED. The restore
    accepts either state this run authored and nothing else, so hole 6 is unweakened — a third
    party's bytes still match neither."""

    def _spawn_with_holder(self, when, hold="30", label="window"):
        holder = os.path.join(self.tmp, "python-window-holder")
        pathlib.Path(holder).write_text(WINDOW_HOLDER, encoding="utf-8")
        os.chmod(holder, 0o755)
        proc = subprocess.Popen(self._argv(label), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=ROOT,
                                start_new_session=True,
                                env=self._env(PYTHON=holder, MUTATE_REAL_PYTHON=sys.executable,
                                              MUTATE_WINDOW_HOLD=hold, MUTATE_WINDOW_WHEN=when))
        self.addCleanup(self._reap, proc)
        return proc

    def _spawn_parked_in_the_window(self, hold="30"):
        """Start a run and block until it is parked with the mutant on disk, mid-mutator."""
        proc = self._spawn_with_holder("after-apply", hold=hold)
        self.assertTrue(self._await_snapshot_window(),
                        "the run never applied its mutation — it is not parked in the window")
        return proc

    def _spawn_parked_before_the_apply(self, hold="30"):
        """Parked on the OTHER side: the mutant hash is recorded, nothing is written yet."""
        proc = self._spawn_with_holder("before-apply", hold=hold)
        # There is no on-disk change to wait for by definition, so wait for the .bak — the
        # snapshot is taken before the plan, so its presence means the run is past it.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not os.path.exists(self.victim + ".bak"):
            time.sleep(0.02)
        self.assertTrue(os.path.exists(self.victim + ".bak"),
                        "the run never took its snapshot — it is not parked before the apply")
        time.sleep(0.5)   # let it get past the plan and into the pre-apply hold
        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "the target was already written — this is not the pre-apply window")
        return proc

    def test_a_run_killed_in_the_MIRROR_window_restores_too(self):
        """The window a one-value fix opens while closing the other, and the reason the restore
        accepts a SET rather than a single hash.

        The run has recorded the mutant hash but has not written it yet, so the bytes on disk are
        the PRISTINE ones. A restore that accepts only "the last thing I wrote" refuses here for
        the mirror-image reason the shipped defect refused on the other side — and would leave the
        `.bak` displaced into a `.REFUSED-RESTORE.bak` over a target nobody had touched.

        ⚠ THIS TEST EXISTS BECAUSE A MUTANT SURVIVED. Narrowing `we_authored` to `expected_sha`
        alone left the whole suite green: every other kill test lands where the two hashes agree
        or where the target holds the mutation. The window was closed in the code and unpinned."""
        proc = self._spawn_parked_before_the_apply()
        os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=60)

        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "the target is not pristine after a kill that wrote nothing")
        self.assertFalse(os.path.exists(self.victim + ".bak"),
                         "a .bak was left behind — the restore did not run")
        self.assertFalse(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"),
                         "the restore refused over a target this run had not yet written to")
        self.assertNotIn("REFUSED-RESTORE", out or "")

    def test_the_apply_refuses_bytes_the_plan_never_saw(self):
        """The plan and the apply are two reads of one file, so the apply proves it is mutating
        what was planned. Without that, a write landing between them is read as the source, and
        the resulting mutant is one nobody described — while the recorded hash describes bytes
        that never existed on disk, so the restore would refuse on a target this run corrupted.

        Unlike the kill tests this one lets the run CONTINUE: the interesting outcome is what the
        apply does, not what a trap does.

        ⚠ THE THIRD PARTY'S EDIT STILL CONTAINS THE PATTERN, and that is the whole design of this
        test. A MUTANT TAUGHT IT: the first version wrote bytes that did not contain `old`, so
        deleting the staleness check entirely left the suite GREEN — the apply still refused, but
        on the pattern COUNT (`occurs 0 times`), which was never the thing under test. The test
        asserted "BROKEN appeared" and BROKEN appeared, for a reason that had nothing to do with
        the guard. Keeping the pattern intact means the count check passes and ONLY the hash
        comparison can catch it — and the assertion that matters is not the word BROKEN, it is
        that their bytes are still on disk."""
        proc = self._spawn_parked_before_the_apply(hold="3")
        # Contains `"pristine-aa"` exactly once, so the pattern check is satisfied — and differs
        # from what the plan hashed, so only the staleness check can refuse it.
        theirs = 'MARKER = "pristine-aa"\n# somebody saved while the run was between steps\n'
        pathlib.Path(self.victim).write_text(theirs, encoding="utf-8")
        out, _ = proc.communicate(timeout=60)

        self.assertEqual(theirs, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "THE THIRD PARTY'S EDIT WAS OVERWRITTEN by a mutation derived from "
                         "source that no longer existed — the apply mutated bytes the plan had "
                         "never seen, and the hash it recorded describes a file that never was")
        self.assertNotEqual(0, proc.returncode, "a run that produced no verdict exited 0")
        self.assertNotIn("RED", out or "", "a mutant nobody designed was graded as a verdict")
        self.assertNotIn("GREEN", out or "", "a mutant nobody designed was graded as a verdict")

    def test_a_run_killed_inside_the_mutator_still_restores(self):
        """THE STAGE. Deterministic where the shipped test was probabilistic."""
        proc = self._spawn_parked_in_the_window()
        os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=60)

        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "a run killed INSIDE the mutator left the source MUTATED — the restore "
                         "refused over bytes this very run had written seconds earlier")
        self.assertFalse(os.path.exists(self.victim + ".bak"), "a .bak was left behind")
        self.assertFalse(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"),
                         "the integrity check refused on a run that authored every byte on disk")

    def test_the_refusal_does_not_accuse_a_third_party_that_does_not_exist(self):
        """A false accusation is worse than a silent bug: it sends the reader hunting a collision
        that never happened, and REFUSED-RESTORE's remedy tells them to preserve and diff an edit
        nobody made."""
        proc = self._spawn_parked_in_the_window()
        os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=60)
        self.assertNotIn("REFUSED-RESTORE", out or "",
                         "the run accused a third party of writing bytes it wrote itself")

    def test_a_real_third_party_write_in_that_same_window_is_still_refused(self):
        """The fix must not buy the kill path its clean restore by weakening hole 6. Same window,
        same kill — but this time somebody really does write, and the refusal MUST stand."""
        proc = self._spawn_parked_in_the_window()
        theirs = 'MARKER = "work by somebody who was not this run"\n'
        pathlib.Path(self.victim).write_text(theirs, encoding="utf-8")
        os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=60)

        self.assertEqual(theirs, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "a third party's edit was silently reverted — the restore adopted bytes "
                         "this run never authored")
        self.assertIn("REFUSED-RESTORE", out or "",
                      "a genuine third-party write in the window was not refused")
        self.assertTrue(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"),
                        "the pristine copy was not preserved for the human to diff")

    def test_a_signalled_run_TERMINATES_instead_of_resuming_its_wait(self):
        """THE SECOND DEFECT stage 18c found, and it is the one that reddens CI.

        `trap restore EXIT INT TERM` ran `restore` and then RETURNED — straight back into the
        `wait` for the test-run command substitution. So a signalled run restored correctly and
        then went on living for as long as its inner tests took. A `ps` on the group 20s after
        the SIGTERM showed all four processes still alive and sleeping: the outer bash, the
        command-substitution SUBSHELL, the `unittest discover` inside it, and the `grep` on the
        other end of the pipe. Only the outer shell honours the signal; the subshell and its
        children do not die with it, so the shell it must wait for never finishes.

        Two consequences, and the second is the serious one:

          * the run hangs for the duration of the inner suite, which is what times the shipped
            killed-run test out on a loaded runner — the assertion it actually failed on was
            never the restore, which had already happened correctly;
          * `restore` RELEASED THE LOCK on its way through, so from that moment a second run may
            take a snapshot while this one's test process is still executing. That is precisely
            the interleave stage 18's interlock exists to make impossible.

        A signal handler must END the run. EXIT keeps the plain `restore` (nothing to terminate);
        INT and TERM restore and then exit 128+signum, the conventional shell status."""
        hook = os.path.join(self.tmp, "announce.py")
        pathlib.Path(hook).write_text(HOOK_ANNOUNCE_THEN_HOLD, encoding="utf-8")
        proc = subprocess.Popen(self._argv("announce"), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=ROOT,
                                start_new_session=True, env=self._env(MUTATE_HOOK=hook))
        self.addCleanup(self._reap, proc)
        # Park the signal in the TEST phase, deterministically. On an idle machine the kill
        # otherwise lands in the mutator and the run dies promptly for an unrelated reason —
        # which is exactly why this failure only ever showed up on loaded runners.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not os.path.exists(self.report):
            time.sleep(0.02)
        self.assertTrue(os.path.exists(self.report),
                        "the run never reached its test phase, so the signal cannot land there")
        started = time.monotonic()
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            self.fail("the run did not terminate 45s after SIGTERM — it restored and then went "
                      "back to waiting for a 120s inner test run, holding a released lock")
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 45,
                        f"the run took {elapsed:.1f}s to die after SIGTERM")
        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "terminating on the signal skipped the restore")
        self.assertEqual(proc.returncode, 128 + signal.SIGTERM,
                         "a signalled run must report 128+signum, so a batch driver reads it as "
                         "a HARNESS FAILURE and stops rather than grading the next mutant")

    def test_a_run_killed_BEFORE_the_mutation_lands_restores_too(self):
        """The other side of the window. Killed after the snapshot but before anything is written,
        the target is still pristine — which is also a state this run authored, and must restore
        as cleanly as the mutated one. Pinned because a fix that only accepts the MUTANT hash
        would trade this failure for the one above and look green from the stage's own tests."""
        holder = os.path.join(self.tmp, "python-window-holder")
        pathlib.Path(holder).write_text(WINDOW_HOLDER, encoding="utf-8")
        os.chmod(holder, 0o755)
        # A pattern that matches nothing: the run takes its snapshot, then the mutator refuses,
        # so the target is never written and the script is on its way out with a pristine tree.
        argv = [MUTATE_SH, "prewindow", self.victim, '"no-such-marker"', '"x"', "test_window.py"]
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT,
                              env=self._env(PYTHON=holder, MUTATE_REAL_PYTHON=sys.executable))

        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "an unapplied mutation did not leave the target pristine")
        self.assertFalse(os.path.exists(self.victim + ".bak"), "a .bak was left behind")
        self.assertFalse(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"),
                         "the restore refused over a target nobody had touched at all")
        self.assertIn("BROKEN", proc.stdout, "a mutation that cannot be applied is not a verdict")


class TestTwoRunsCannotInterleave(_InterlockFixture):
    """A run starting while another holds a snapshot is the collision, mechanised away."""

    def _second_victim(self):
        other = os.path.join(self.tmp, "other.py")
        pathlib.Path(other).write_text(LOCK_VICTIM, encoding="utf-8")
        return other

    def _probe_against_holder(self, other, **env_extra):
        """Race a second run against the live holder and return (proc, elapsed, output)."""
        self.assertTrue(self._await_snapshot_window(),
                        "the first run never reached its snapshot window")
        started = time.monotonic()
        proc = subprocess.run(self._argv("second", target=other), capture_output=True,
                              text=True, env=self._env(**env_extra), cwd=ROOT)
        return proc, time.monotonic() - started, proc.stdout + proc.stderr

    def _assert_failed_fast_naming(self, proc, elapsed, out, holder, other):
        self.assertEqual(5, proc.returncode,
                         f"a second run was allowed to start while another held a snapshot. It "
                         f"must not queue and it must not proceed. Got: {out!r}")
        self.assertIn(str(holder.pid), out,
                      f"the refusal did not name the pid holding the lock, so there is nothing "
                      f"to go and look at. Got: {out!r}")
        self.assertIn(self.victim, out,
                      f"the refusal did not name the file the other run holds. Got: {out!r}")
        self.assertLess(elapsed, 30.0,
                        "the second run QUEUED behind the first instead of failing fast")
        self.assertEqual(LOCK_VICTIM, pathlib.Path(other).read_text(encoding="utf-8"),
                         "the second run mutated its target before refusing")
        self.assertFalse(os.path.exists(other + ".bak"),
                         "the second run took a snapshot before refusing")

    def test_a_second_run_fails_fast_naming_the_run_that_holds_the_lock(self):
        # The pid-bearing lockfile — the fallback, and the path that actually runs on any host
        # without util-linux `flock`, stock macOS included.
        other = self._second_victim()
        holder = self._spawn_holder(MUTATE_LOCK_IMPL="pidfile")
        proc, elapsed, out = self._probe_against_holder(other, MUTATE_LOCK_IMPL="pidfile")
        self._assert_failed_fast_naming(proc, elapsed, out, holder, other)

    def test_the_flock_branch_takes_a_real_kernel_lock(self):
        # Without this the flock branch is dead code on every developer machine here, and dead
        # code in a guardrail is a comment. See FLOCK_SHIM for why the stand-in is faithful.
        bindir = os.path.join(self.tmp, "bin")
        os.mkdir(bindir)
        shim = os.path.join(bindir, "flock")
        pathlib.Path(shim).write_text(FLOCK_SHIM % (sys.executable,), encoding="utf-8")
        os.chmod(shim, 0o755)
        path = bindir + os.pathsep + os.environ.get("PATH", "")

        other = self._second_victim()
        holder = self._spawn_holder(MUTATE_LOCK_IMPL="flock", PATH=path)
        proc, elapsed, out = self._probe_against_holder(other, MUTATE_LOCK_IMPL="flock", PATH=path)
        self._assert_failed_fast_naming(proc, elapsed, out, holder, other)

    def test_the_lock_is_released_when_a_run_ends_normally(self):
        # A lock taken and not released bricks the harness for every later run, which would be a
        # worse outage than the hazard. Behavioural rather than implementation-shaped: run twice
        # back to back and require the second to get in.
        first = self._run_with_hook("")
        second = self._run_with_hook("")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode,
                         "the second run could not acquire the lock the first one left behind: "
                         + (second.stdout + second.stderr))


class TestEveryHasherAgreesWithHashlib(unittest.TestCase):
    """The interlock is only ever as good as the digest it compares.

    A hasher whose output the script MIS-PARSES is the worst case in this whole stage, and it is
    not a hypothetical one. If a parse grabbed the FILENAME instead of the digest — `shasum`
    prints both, on one line, in that order — then `expected` and `found` would be the same wrong
    string on every single run, the comparison would always hold, and the guard would be a silent
    no-op. It would be indistinguishable from a guard that works, which is precisely the class of
    failure the five holes above were.

    So every form the script can select is pulled straight out of the script and RUN, against a
    file with non-ASCII bytes in it, and checked against hashlib. Pulling them out rather than
    restating them here means the pin cannot drift away from the implementation it guards.
    """

    #     sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
    FORM = re.compile(r'^\s*sha256_of\(\) \{ (.+?); \}\s*$', re.M)

    def setUp(self):
        # Skipped per-test rather than at class level, so the Windows matrix leg still COUNTS
        # these — a class-level skip collapses to one and quietly shrinks `Ran N`
        # (see test_suite_count_integrity.py).
        if sys.platform.startswith("win"):
            self.skipTest("POSIX shell harness; the Windows legs do not run mutation passes")
        if shutil.which("bash") is None:
            self.skipTest("no bash on PATH to run the hasher forms with")

    def test_each_hasher_the_script_can_select_returns_the_real_sha256(self):
        forms = self.FORM.findall(_read(MUTATE_SH))
        self.assertEqual(4, len(forms),
                         f"expected 4 selectable hashers (shasum, sha256sum, openssl, and the "
                         f"PYTHON fallback); found {len(forms)}. If a form was added it is "
                         f"unvalidated, and if one was removed some host now has no hasher.")

        path = os.path.join(tempfile.mkdtemp(), "bytes.bin")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        pathlib.Path(path).write_bytes(b"hole six\n\x00\xff not ascii, not newline-terminated")
        want = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

        checked = []
        for body in forms:
            env = dict(os.environ)
            tool = body.split()[0]
            if tool == '"$PYTHON"':
                env["PYTHON"] = sys.executable
            elif shutil.which(tool) is None:
                continue                      # not installed here; another host's branch
            proc = subprocess.run(["bash", "-c", body, "_", path],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(0, proc.returncode, f"{tool} form failed: {proc.stderr!r}")
            self.assertEqual(want, proc.stdout.strip(),
                             f"the script's {tool} form does not produce a real sha256. If it "
                             f"produces the same WRONG string every time — a filename, say — the "
                             f"interlock's comparison always holds and the whole guard is a "
                             f"silent no-op that looks exactly like a working one.")
            checked.append(tool)
        self.assertIn('"$PYTHON"', checked, "the last-resort hasher was not exercised")


class TestTheExitContract(_Fixture):
    """0.0.17 stage 18b — the contract `tests/_run_mutants.sh` is written against.

    Stage 18 added two hard failures (4 refused-restore, 5 lock-busy) to a script whose only
    production consumer inspected no status at all. Fixing the consumer meant first asking what
    the statuses MEAN, and that question had never been answered: `grep -i "exit code"
    scripts/mutate.sh` returned nothing before this stage. A caller cannot be written against an
    undocumented contract, so the contract now lives in the script's header and is pinned here.

    THE ONE RULE, and every test below is a way of asking it:

        exit 0  IF AND ONLY IF  a VERDICT was produced (RED or GREEN).
        anything else means NO VERDICT, and the tree may not be trustworthy.

    IT DID NOT HOLD WHEN THIS CLASS WAS WRITTEN, in two independent places, and both are the
    same defect the five holes in the header are: the absence of evidence read as evidence.

      (1) A test pattern matching NO FILES printed `BROKEN!! ... PATTERN MATCHED NO TESTS` and
          exited **0** — the identical status a real RED or GREEN exits with. Hole 5 stopped
          that non-result being printed as a verdict; nothing stopped it being RETURNED as one,
          so the fix was invisible to every caller that reads a status rather than a log.

      (2) A test run producing NO `Ran` LINE AT ALL was printed as `GREEN ... <-- SURVIVOR`,
          at exit 0. Point `MUTATE_TESTS_DIR` at a directory unittest cannot import — or break
          discovery any other way — and the grep pipeline yields nothing, no `FAILED` is found,
          no `Ran 0 tests` is found either, and the else-branch calls it a survivor. Hole 5
          greps for `^Ran 0 tests`, which is the shape unittest prints when it ran and found
          nothing; it cannot see the shape unittest prints when it never ran at all. This is
          hole 5 reached through the door hole 5 left open, and it is worse: a GREEN is the
          verdict that makes somebody go and weaken a guard.
    """

    def _rc_and_verdict(self, proc):
        first = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        return proc.returncode, first

    def test_a_verdict_and_a_harness_failure_never_share_an_exit_code(self):
        # Table-driven over every outcome reachable from this fixture. The per-row expected
        # status is the contract; the invariant asserted after it is the REASON for the contract,
        # and it is what a caller actually relies on.
        rows = [
            # (why, kwargs, expected status, is a verdict?)
            ("a mutation the tests catch",
             dict(old='return "yes"', new='return "no!"'), 0, True),
            ("a mutation nothing exercises",
             dict(old='return "no"', new='return "NO"'), 0, True),
            ("a pattern that is not in the file",
             dict(old="THIS_TEXT_IS_NOT_IN_THE_FILE", new="x"), 3, False),
            ("a pattern that hits several sites",
             dict(old="    return ", new="    return  "), 3, False),
            ("a test pattern matching no files",
             dict(old='return "yes"', new='return "no!"',
                  pattern="test_no_such_file_xyz.py"), 6, False),
            ("a test tree unittest cannot even import",
             dict(old='return "yes"', new='return "no!"',
                  MUTATE_TESTS_DIR="/no/such/directory"), 6, False),
            ("a lock implementation that does not exist",
             dict(old='return "yes"', new='return "no!"',
                  MUTATE_LOCK_IMPL="not-a-lock-impl"), 2, False),
        ]
        for why, kwargs, want_rc, is_verdict in rows:
            with self.subTest(why):
                rc, line = self._rc_and_verdict(_run_proc(self.tmp, why, **kwargs))
                self.assertEqual(want_rc, rc,
                                 f"{why!r} exited {rc}, not the documented {want_rc}. The "
                                 f"contract in scripts/mutate.sh's header is what "
                                 f"tests/_run_mutants.sh dispatches on. Said: {line!r}")
                spoke = line.startswith("RED") or line.startswith("GREEN")
                self.assertEqual(is_verdict, spoke,
                                 f"{why!r} {'produced no' if is_verdict else 'produced a'} "
                                 f"verdict line: {line!r}")
                self.assertEqual(is_verdict, rc == 0,
                                 f"{why!r} broke the one rule the caller depends on: exit 0 iff "
                                 f"a verdict was produced. It exited {rc} and said {line!r}, so "
                                 f"no caller reading a status can tell a graded mutant from a "
                                 f"harness failure.")

    def test_a_test_run_that_produced_no_result_line_is_not_called_a_SURVIVOR(self):
        # (2) above, on its own, because it is the false-GREEN half and the more dangerous one.
        # A survivor sends somebody to go and look at a guard that "does not bite"; here no test
        # ever ran, so there is nothing to look at and the guard is fine.
        proc = _run_proc(self.tmp, "unimportable tree", 'return "yes"', 'return "no!"',
                         MUTATE_TESTS_DIR="/no/such/directory")
        self.assertNotIn("GREEN", proc.stdout,
                         f"a test run that never executed a single test was reported as a "
                         f"SURVIVOR. Nothing ran, so nothing survived — this is hole 5 reached "
                         f"through the gap hole 5 left, and GREEN is the verdict that makes "
                         f"somebody go and weaken a real guard: {proc.stdout!r}")
        self.assertNotIn("SURVIVOR", proc.stdout, proc.stdout)
        self.assertIn("BROKEN", proc.stdout,
                      f"no verdict was produced and the harness did not say so: {proc.stdout!r}")

    def test_every_exit_status_the_script_can_take_is_documented_in_its_header(self):
        # The header is the SINGLE SOURCE the caller is written against, so a code added without
        # a line in the contract is a caller writing fiction. Derived from the script rather than
        # restated, so the pin cannot drift away from what it guards.
        src = _read(MUTATE_SH)
        self.assertTrue(
            "EXIT CONTRACT" in src,
            "scripts/mutate.sh documents no EXIT CONTRACT section. Before stage 18b "
            '`grep -i "exit code" scripts/mutate.sh` returned nothing, and '
            "tests/_run_mutants.sh now dispatches on these codes.")
        contract = src[src.index("EXIT CONTRACT"):]
        body = src[src.index("set -euo pipefail"):]
        codes = set(re.findall(r'^\s*(?:if .*; then )?exit (\d+)', body, re.M))
        codes |= set(re.findall(r'sys\.exit\((\d+)\)', body))
        self.assertTrue(codes, "found no exit statuses in the script at all — the pin is vacuous")
        for code in sorted(codes):
            # re.M matters: the contract is a block of `#` lines, so an unanchored search would
            # be satisfied by the digit appearing anywhere in the prose above it.
            self.assertRegex(contract, re.compile(rf'^\s*#\s*{code}\s', re.M),
                             f"scripts/mutate.sh can exit {code} but its EXIT CONTRACT does not "
                             f"document it, so tests/_run_mutants.sh cannot handle it and will "
                             f"report it as an unknown failure")


class TestTheHarnessIsShippable(unittest.TestCase):
    """It is a dev tool and ships PUBLIC — no internal-only exclusion, so nothing to keep in step."""

    def test_it_exists_and_is_executable(self):
        self.assertTrue(os.path.isfile(MUTATE_SH), "missing scripts/mutate.sh")
        self.assertTrue(os.access(MUTATE_SH, os.X_OK), "scripts/mutate.sh is not executable")

    def test_it_documents_the_defect_it_exists_to_prevent(self):
        src = _read(MUTATE_SH)
        for needle in ("PYTHONDONTWRITEBYTECODE", "trap restore EXIT INT TERM",
                       "size-preserving", "CONTRAPOSITIVE", "MIRROR HAZARD",
                       # Hole 6. "WHY THE CHECK IS A CONTENT HASH" is the load-bearing one: the
                       # next person to make this guard cheaper will reach for mtime + size,
                       # which is the very comparison it exists to defeat.
                       "SNAPSHOT INTERLOCK", "WHY THE CHECK IS A CONTENT HASH",
                       "REFUSED-RESTORE", "THE KILL PATH IS UNAFFECTED"):
            self.assertIn(needle, src,
                          f"{needle!r} is gone from the header — the reasoning that makes the "
                          f"fixes non-negotiable must not be simplified away")

    def test_it_hardcodes_no_developer_path(self):
        src = _read(MUTATE_SH)
        self.assertNotIn("/Users/", src, "a developer-specific path leaked into a shipped script")
        self.assertNotIn("jsvenv", src, "a developer-specific virtualenv leaked into the script")


if __name__ == "__main__":
    unittest.main()
