"""A2a — `the-second-silent-allow` (0.0.19 stage 03a). The other half of #28's window claim.

WHAT THIS STAGE IS, IN ONE PARAGRAPH
------------------------------------
Stage 03 gave `gate_hook.py:495` — *no run has state in this repo* — a once-per-window notice, and
closed #28 on *"a user can always tell whether the seatbelt is on."* `:527` is a **different** silent
allow: a run RESOLVES, but no `pipeline_run__` checkpoint backs it, so nothing is policed and nothing
said so. It is arguably the worse of the two — a user whose run id resolves reasonably expects
gating, so the silence reads as approval more strongly here. This file pins the notice, and pins
that the VERDICT did not move to get it.

⛔ THE VERDICT DOES NOT MOVE. `allowed` stays True on every branch, `reason` stays byte-identical,
`gate` stays None. `TheVerdictsAreIdentical` below pins that independently of any wording, and
`test_a2a_the_floor_pin_is_load_bearing` reproduces the build that BLOCKS-and-notices so a
notice-only assertion cannot be mistaken for a floor pin (doc 85 §7f). Stages 01, 02 and 03 each
set that bar.

HOW `:527` IS ACTUALLY REACHED — DERIVED FROM `run_resolver`, NOT ASSUMED
-------------------------------------------------------------------------
The branch needs a RESOLVED run with no approach, no spec and no readable checkpoint. Reading the
resolver's ladder, exactly three routes get there, and the tests below drive all three rather than
manufacturing one:

  * EXPLICIT — a caller named the run (`mokata … --run <id>`, the MCP stamp). Rung (i).
  * PINNED   — `MOKATA_SESSION_ID`, the documented "I am telling you which run" control. Rung (ii).
  * a CANDIDATE-bearing repo whose checkpoint cannot be READ — `run_ids` lists it from `scandir`
    (no stat), so the run resolves at SINGLE while `_run_registered` degrades to False. Rung (vi).

⚠ A FOURTH ROUTE LOOKS AVAILABLE AND IS NOT, AND IT IS A FINDING RATHER THAN A FIXTURE CHOICE.
`run_resolver.RUN_STATE_PREFIXES` names `tdd_state__` as a candidate prefix; the key TDD phase is
actually written under is `tdd_state.TDD_STATE_PREFIX` == `tdd_phase__`. Nothing in `src/` writes
`tdd_state__` at all. So a run holding ONLY TDD phase state is not a candidate for the resolver
that every surface reads. Out of this stage's scope and reported to the coordinator; recorded here
because the first draft of this file used it as a fixture and would have been grading `:495` while
claiming to grade `:527`.

THE §7g HAZARD THAT LIVES INSIDE THE LINE BEING GIVEN A VOICE
-------------------------------------------------------------
`_run_registered` is DEGRADE-CLEAN by design: an UNREADABLE checkpoint returns the same `False` as
an ABSENT one, so the gate falls open on a state it cannot read (correct — a broken checkpoint must
never manufacture a block). The consequence is that this one branch already carries two facts:

    the checkpoint is not there          -> "no run is registered"     TRUE
    the checkpoint could not be read     -> "no run is registered"     A LIE mokata cannot detect

A notice asserting the first while the second is true is mokata stating its own state as fact when
it does not know it — exactly the harm #28 is about, committed by the fix for #28. So the notice
carries THREE states, never two: registered (silent), absent (one sentence), unverifiable (a
different sentence). `hook_wiring.unverifiable` is the shape and it was built one stage ago.

⭐ THE SHARPEST CASE, AND IT IS THE ONE THE TESTS BELOW DRIVE: a run that IS registered, whose
checkpoint mokata cannot stat. The old code told that user "no active mokata run" and waved the
write through. The new code still waves it through — the floor is the floor — but it says it could
not read, not that there is nothing there.

ONE NOTICE PER WINDOW, ACROSS BOTH BRANCHES
-------------------------------------------
`:495` and `:527` share `NOTICE_PREFIX`, so a window is told once and once only, INCLUDING the
cross-branch case: a user whose run starts resolving mid-window has already been told the gates are
off and is not told again by a different sentence. That is deliberate and it is asserted, because
"the first unpoliced window speaks once" is the property, not "each branch speaks once".

⚠ THIS FILE AVOIDS THE VOCABULARY THAT PUTS A TEST IN `_deprecation_removal.notice_pins`' DOMAIN,
for the same reason stage 03's file does.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager

import _support  # noqa: F401  (puts src/ on the path)

from mokata import gate_hook as G                                    # noqa: E402
from mokata import tdd_state as T                                    # noqa: E402
from mokata.init import init_repo                                    # noqa: E402
from mokata.run_resolver import PIN_ENV                              # noqa: E402
from mokata.state import StateStore                                  # noqa: E402

RUN = "run0123456789abcdef"
SRC_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

# The one opening BOTH uncertainty branches share. Retyped from stage 03 on purpose: two spellings
# of "your gates are off right now" is how a user learns to read one of them as a different fact.
NOTICE_OPENING = "mokata: run-state gates are OFF for this window"

# ⛔ STAGE 03's NOTICE, BYTE FOR BYTE. The brief binds this stage to leave `:495` alone, and a
# substring check would not notice a reworded middle. This literal IS the pin.
STAGE_03_NOTICE = (
    "mokata: run-state gates are OFF for this window — no mokata run has state in "
    "this repo, so mokata is not policing these edits (hand-editing outside a run "
    "is never blocked, by design). Start a tracked run with /mokata:brainstorm to "
    "turn enforcement on.")

# The reason string every existing caller reads as this branch's identity (`tests/_skill_prose.py`
# and `test_run_reg_prose_pin.py` both quote it). doc 85 §4: it does not move.
UNREGISTERED_REASON = "no active mokata run — not policed"

_POSIX_PERMS = os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0


# ======================================================================================
# fixtures
# ======================================================================================
def _silent(_s):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return d


def _raw_store(root):
    return StateStore(T.state_dir(root))


def _register(root, run=RUN):
    """Give the run its `pipeline_run__` CHECKPOINT — the one thing `_run_registered` reads."""
    _raw_store(root).write(G.CHECKPOINT_PREFIX + run, {"phase": "brainstorm"})


@contextmanager
def _checkpoint_unstattable(root):
    """THE PRIMARY unreadable fixture: the checkpoint path exists as a name and cannot be stat'd,
    and NOTHING ELSE IN THE DIRECTORY IS AFFECTED. A self-referential symlink at exactly
    `pipeline_run__<run>.json` gives `ELOOP` on stat, `False` on `os.path.exists` (so
    `_run_registered` degrades exactly as designed), and a name `run_ids` still lists — a run that
    resolves, is not policed, and whose registration mokata cannot determine.

    ⭐ WHY THIS SHAPE AND NOT ONLY THE PERMISSION ONE. `_state_dir_unstattable` below makes the
    WHOLE directory untraversable, so every path inside it raises — which means it cannot tell
    whether the probe asks about the CHECKPOINT or about some other key, and a mutant swapping the
    key would survive it. This one isolates the checkpoint, so the key is graded. Both are kept:
    they are two different real `OSError`s (`ELOOP` and `EACCES`), and the branch must report
    ignorance for either rather than for one spelling of it.

    ⚠ §7e — the stand-in FAILS LOUD rather than falling through. If it did not actually make the
    checkpoint unstattable, every test using it would grade the ABSENT path while claiming to grade
    UNREADABLE, and go green having tested nothing."""
    from mokata.run_resolver import run_ids
    d = T.state_dir(root)
    os.makedirs(d, exist_ok=True)
    target = os.path.join(d, G.CHECKPOINT_PREFIX + RUN + ".json")
    loop = os.path.join(d, ".loop-" + RUN)
    if os.path.lexists(target):
        os.unlink(target)
    os.symlink(os.path.basename(loop), target)
    os.symlink(os.path.basename(target), loop)
    try:
        try:
            os.stat(target)
        except OSError:
            pass
        else:
            raise AssertionError(
                "the unstattable-checkpoint stand-in did NOT make the checkpoint unstattable — "
                "every test using it would grade the ABSENT path while claiming UNREADABLE")
        if os.path.exists(target):
            raise AssertionError("`_run_registered` would read this as REGISTERED, not degraded")
        if RUN not in run_ids(d):
            raise AssertionError(
                "the run stopped being a CANDIDATE under the stand-in — this fixture no longer "
                "reaches `:527` and every test on it is grading a different branch")
        yield
    finally:
        for link in (target, loop):
            if os.path.lexists(link):
                os.unlink(link)


@contextmanager
def _state_dir_unstattable(root):
    """Make the state directory LISTABLE but not TRAVERSABLE, so every path inside it can be named
    and none of it can be stat'd. That is precisely the shape `_run_registered` degrades on, and it
    is a real permission story (OSS #47 reports run-state degrading on disk live), not a mock.

    ⚠ §7e — a stand-in must FAIL LOUD rather than fall through. If the chmod did not actually make
    the checkpoint unstattable, every test using this would grade the ABSENT path while claiming to
    grade UNREADABLE and go green having tested nothing. So it asserts its own effect before
    yielding, and it also asserts the run is still a CANDIDATE — the whole fixture rests on
    `run_ids` seeing the name without stat'ing it, and a platform where that stops being true must
    stop the test rather than quietly re-route it."""
    from mokata.run_resolver import run_ids
    d = T.state_dir(root)
    probe = os.path.join(d, G.CHECKPOINT_PREFIX + RUN + ".json")
    before = stat.S_IMODE(os.stat(d).st_mode)
    os.chmod(d, 0o600)
    try:
        try:
            os.stat(probe)
        except PermissionError:
            pass
        else:
            raise AssertionError(
                "the unreadable-state stand-in did NOT make the checkpoint unstattable — every "
                "test using it would grade the ABSENT path while claiming to grade UNREADABLE")
        if RUN not in run_ids(d):
            raise AssertionError(
                "the run stopped being a CANDIDATE under the stand-in — `run_ids` is stat'ing "
                "where it documents a scandir, and this fixture no longer reaches `:527`")
        yield
    finally:
        os.chmod(d, before)


def _envelope(path, cwd, tool="Write", session_id="cc-window-1"):
    return json.dumps({
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": path, "content": "def login(): return True\n"},
    })


def _hook(path, cwd, tool="Write", session_id="cc-window-1", pin=None, timeout=60):
    """The hook in its real situation: a subprocess, the envelope on stdin, exit code + stderr.

    `pin` sets `MOKATA_SESSION_ID` — the resolver's rung (ii), and the only route by which a HOOK
    (which is handed no run id) reaches `:527` with the checkpoint simply absent."""
    e = dict(os.environ)
    e.pop(PIN_ENV, None)
    e["PYTHONPATH"] = SRC_ROOT
    if pin:
        e[PIN_ENV] = pin
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from mokata.hook_cli import gate_guard_main; sys.exit(gate_guard_main([]))"],
        input=_envelope(path, cwd, tool, session_id), text=True, capture_output=True,
        env=e, timeout=timeout, cwd=cwd,
    )
    return proc.returncode, proc.stderr


def _impl(root):
    return os.path.join(root, "src", "app.py")


# ======================================================================================
# THE FLOOR HOLDS ON THE SECOND BRANCH TOO, and it now says so.
# ======================================================================================
class TheSecondFloorHolds(unittest.TestCase):

    def _assert_floor_held(self, outcome):
        """THE pin, in one place so the load-bearing negative runs the SAME assertions. Order
        matters: the allow is the contract (`gate_hook.py:17-19`, P14) and the notice is the new
        legibility. A build satisfying only the second has reversed an argued design decision."""
        self.assertTrue(outcome.allowed, "the SI.1 floor was reversed — this write must ALLOW")
        self.assertEqual(outcome.exit_code, 0, "an allow that does not exit 0 is not an allow")
        self.assertIsNone(outcome.gate, "an allow names no gate")
        self.assertIsNotNone(outcome.notice, "the allow is silent again — that IS the defect")
        self.assertIn(NOTICE_OPENING, outcome.notice)

    def test_a2a_regression_a_resolved_but_unregistered_write_still_allows_and_now_says_so(self):
        """The whole of the stage: same verdict, new sentence. RED before it — the branch returned
        a notice-less `GateOutcome`."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self._assert_floor_held(G.check_write(d, _impl(d), run_id=RUN))

    def test_a2a_the_floor_pin_is_load_bearing(self):
        """DEFEAT the design and watch the wrong build get through the weaker half.

        The build reproduced is the one the backlog row's one-line framing invites: treat the
        unregistered allow as a fail-open bug and BLOCK it, while still carrying the notice.
        Without this, a notice-only assertion would pass against that build (§7f)."""
        wrong = G.GateOutcome(False, "unregistered — blocked", gate=G.GATE_PHASE,
                              notice=NOTICE_OPENING + " — and this build blocks anyway")
        with self.assertRaises(AssertionError):
            self._assert_floor_held(wrong)
        self.assertIn(NOTICE_OPENING, wrong.notice)   # the weak half cannot tell them apart

    def test_a2a_the_hook_exits_zero_and_puts_the_notice_on_stderr(self):
        """End to end through the real subprocess, via the PINNED route — a notice that never
        surfaces is not a notice."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            code, err = _hook(_impl(d), d, pin=RUN)
            self.assertEqual(code, 0, f"the floor was reversed at the hook: {err}")
            self.assertIn(NOTICE_OPENING, err)

    def test_a2a_the_notice_copies_stage_03s_shape_rather_than_inventing_a_second(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertTrue(G.check_write(d, _impl(d), run_id=RUN).notice.startswith(
                NOTICE_OPENING))

    def test_a2a_the_notice_names_how_to_turn_enforcement_on(self):
        """A notice that reports a problem and no exit is the shape that gets tools uninstalled."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = _impl(d)
            self.assertIn("/mokata:brainstorm", G.check_write(d, target, run_id=RUN).notice)

    def test_a2a_the_notice_carries_no_path_and_no_credential(self):
        """Secret-safety: a hook message can land in a retained transcript. The notice is about the
        WINDOW, so it needs no target path — and the path is the only thing on this code path that
        could carry a user's directory names or a credential-bearing filename."""
        secretish = "".join(("post", "gres", "://", "admin", ":", "hunt", "er2", "@", "h", "/p"))
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            leaf = "app_" + secretish.replace("/", "_").replace(":", "_") + ".py"
            target = os.path.join(d, "src", leaf)
            notice = G.check_write(d, target, run_id=RUN).notice
            self.assertNotIn(leaf, notice)
            self.assertNotIn("hunt" + "er2", notice)
            self.assertNotIn(d, notice)

    def test_a2a_the_notice_never_names_the_run_id(self):
        """A run id is a SESSION id (`session.py`: "run_id == session_id"). It is not a credential,
        but it is an identifier the window's own notice has no use for, and the cheapest way to
        keep this branch's message as path-free and id-free as `:495`'s is to assert it."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = _impl(d)
            self.assertNotIn(RUN, G.check_write(d, target, run_id=RUN).notice)

    # ---- the negatives. A notice on every edit is the noise that gets the hook uninstalled,
    # ---- which is `gate_hook.py:19` costing the whole gate to fix a smaller problem.

    def test_a2a_a_test_path_is_never_noticed(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = os.path.join(d, "tests", "test_app.py")
            out = G.check_write(d, target, run_id=RUN)
            self.assertTrue(out.allowed)
            self.assertIsNone(out.notice)

    def test_a2a_a_non_implementation_path_is_never_noticed(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for name in ("README.md", "data.json", "pyproject.toml"):
                target = os.path.join(d, name)
                out = G.check_write(d, target, run_id=RUN)
                self.assertTrue(out.allowed, name)
                self.assertIsNone(out.notice, name)

    def test_a2a_a_non_mokata_repo_is_never_noticed(self):
        """No `.mokata/` at all — the hook returns before `check_write` is ever called, PIN or no."""
        with tempfile.TemporaryDirectory() as d:
            code, err = _hook(_impl(d), d, pin=RUN)
            self.assertEqual(code, 0)
            self.assertEqual(err.strip(), "")

    def test_a2a_a_registered_run_is_never_told_the_gates_are_off(self):
        """The notice is about the UNPOLICED window. A run being gated must never get it — it would
        say enforcement is off in the very moment it is on."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)
            target = _impl(d)
            blocked = G.check_write(d, target, run_id=RUN)
            self.assertFalse(blocked.allowed, "the phase gate stopped firing — a real regression")
            self.assertIsNone(blocked.notice)

    def test_a2a_the_p14_override_path_is_untouched(self):
        """A registered run with the phase gate OVERRIDDEN allows — and is not handed the window
        notice, because its gates are not off: one of them was explicitly, ledgerably switched."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)
            _raw_store(d).write(G.override_key(RUN), {"scopes": [G.GATE_PHASE], "reason": "why"})
            target = _impl(d)
            out = G.check_write(d, target, run_id=RUN)
            self.assertTrue(out.allowed)
            self.assertTrue(out.overridden)
            self.assertIsNone(out.notice)


# ======================================================================================
# §7g — THREE STATES, NEVER TWO. Same verdict; different sentences.
# ======================================================================================
@unittest.skipUnless(_POSIX_PERMS, "needs POSIX permissions and a non-root euid")
class AbsentAndUnreadableAreNotTheSameFact(unittest.TestCase):
    """`_run_registered` returns the SAME False for both, by design, and that design stays. What
    must not stay is a notice reporting the first as fact when the second is true."""

    @contextmanager
    def _both(self):
        """Yield `(absent_notice, unreadable_notice, absent_outcome, unreadable_outcome)`.

        The UNREADABLE fixture is the sharp one: the checkpoint IS there and mokata cannot stat it,
        so "no active mokata run" would be flatly false about a REGISTERED run."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = _impl(d)
            absent = G.check_write(d, target, run_id=RUN)
            with _checkpoint_unstattable(d):
                unreadable = G.check_write(d, target, run_id=RUN)
            yield absent.notice, unreadable.notice, absent, unreadable

    def test_a2a_the_two_states_produce_the_SAME_verdict(self):
        """The floor is the floor. Only the wording differs — doc 85 §4, in one assertion."""
        with self._both() as (_a, _u, absent, unreadable):
            self.assertEqual(
                (absent.allowed, absent.gate, absent.overridden, absent.reason),
                (unreadable.allowed, unreadable.gate, unreadable.overridden, unreadable.reason),
                "the two states diverged on the VERDICT — this stage changes messages only")
            self.assertEqual((absent.allowed, absent.gate, absent.overridden), (True, None, False))

    def test_a2a_the_two_states_do_not_share_a_sentence(self):
        """THE test that fails if someone later collapses them into one message."""
        with self._both() as (absent, unreadable, _a, _u):
            self.assertIsNotNone(absent, "the absent branch went silent again")
            self.assertIsNotNone(unreadable, "the unreadable branch went silent again")
            self.assertNotEqual(
                absent, unreadable,
                "an absent checkpoint and an unreadable one are being reported with ONE sentence "
                "— that is §7g inside the very line this stage exists to give a voice to")

    def test_a2a_the_unreadable_notice_never_asserts_that_there_is_no_run(self):
        """The lie the old code told, named as an assertion. It must not claim absence; it must
        claim ignorance, and it must say which."""
        with self._both() as (_a, unreadable, _ao, _uo):
            self.assertTrue(unreadable.startswith(NOTICE_OPENING))
            lowered = unreadable.lower()
            self.assertIn("could not read", lowered)
            self.assertIn("unknown", lowered)
            self.assertNotIn("no mokata run", lowered)
            self.assertNotIn("is not registered", lowered)

    def test_a2a_the_absent_notice_says_absent_and_names_the_way_back_on(self):
        with self._both() as (absent, _u, _ao, _uo):
            lowered = absent.lower()
            self.assertIn("registered", lowered)
            self.assertIn("/mokata:brainstorm", absent)
            self.assertNotIn("could not read", lowered)

    def test_a2a_the_unreadable_notice_names_a_remedy_that_exists(self):
        """§7g's second half: a disclosure pointing at a remedy nobody built is the defect in a new
        costume. `mokata doctor` is a real subcommand; asserted from the parser, not assumed."""
        from mokata.cli import build_parser
        with self._both() as (_a, unreadable, _ao, _uo):
            self.assertIn("mokata doctor", unreadable)
        choices = set()
        for action in build_parser()._subparsers._group_actions:
            choices |= set(getattr(action, "choices", {}) or {})
        self.assertIn("doctor", choices, "the notice names a command mokata does not have")

    def test_a2a_the_unreadable_notice_carries_no_path_and_no_credential(self):
        """The unreadable branch is the one holding a live `OSError`, and an OSError is the classic
        place a full filesystem path leaks into a user-visible string."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = _impl(d)
            with _checkpoint_unstattable(d):
                unreadable = G.check_write(d, target, run_id=RUN).notice
            self.assertNotIn(d, unreadable)
            self.assertNotIn(".json", unreadable)
            self.assertNotIn(RUN, unreadable)

    def test_a2a_a_permission_error_tells_the_same_story_as_a_resolution_error(self):
        """Two different real `OSError`s — `EACCES` here, `ELOOP` above — reaching one branch. The
        notice must report IGNORANCE for either; a build recognising only the spelling it was
        written against would be reporting absence as fact on the other."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)
            target = _impl(d)
            with _state_dir_unstattable(d):
                out = G.check_write(d, target, run_id=RUN)
            self.assertTrue(out.allowed, "the floor was reversed on an unreadable state dir")
            self.assertIn("could not read", out.notice.lower())
            self.assertNotIn(d, out.notice)


# ======================================================================================
# ONE NOTICE PER WINDOW — ACROSS BOTH BRANCHES, not once per branch.
# ======================================================================================
class TheWindowIsToldOnce(unittest.TestCase):

    def test_a2a_the_second_write_in_the_same_window_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = _hook(_impl(d), d, session_id="w1", pin=RUN)
            second = _hook(os.path.join(d, "src", "other.py"), d, session_id="w1", pin=RUN)
            self.assertIn(NOTICE_OPENING, first[1])
            self.assertNotIn(NOTICE_OPENING, second[1],
                             "once-per-window is firing every write — that is the noise that gets "
                             "the hook uninstalled")
            self.assertEqual((first[0], second[0]), (0, 0), "neither write may be blocked")

    def test_a2a_a_window_already_told_by_the_other_branch_is_not_told_twice(self):
        """THE CROSS-BRANCH CASE. A window is told the gates are off ONCE. A user whose run starts
        resolving mid-window has already been told, and a second sentence saying the same thing a
        different way trains them to stop reading both."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = _hook(_impl(d), d, session_id="w-cross")                       # `:495`
            second = _hook(os.path.join(d, "src", "other.py"), d,
                           session_id="w-cross", pin=RUN)                          # `:527`
            self.assertIn(NOTICE_OPENING, first[1])
            self.assertNotIn(NOTICE_OPENING, second[1],
                             "the second branch opened its own window budget — the marker is "
                             "supposed to be SHARED")
            self.assertEqual((first[0], second[0]), (0, 0))

    def test_a2a_the_cross_branch_case_runs_in_the_other_order_too(self):
        """`:527` first, then `:495`. The property is "the first unpoliced window speaks once",
        which is direction-free; a marker written by only one of the two branches would pass the
        test above and fail this one."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = _hook(_impl(d), d, session_id="w-back", pin=RUN)                # `:527`
            second = _hook(os.path.join(d, "src", "other.py"), d, session_id="w-back")  # `:495`
            self.assertIn(NOTICE_OPENING, first[1])
            self.assertNotIn(NOTICE_OPENING, second[1])
            self.assertEqual((first[0], second[0]), (0, 0))

    def test_a2a_a_new_window_is_told_again(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _hook(_impl(d), d, session_id="w1", pin=RUN)
            _code, err = _hook(_impl(d), d, session_id="w2", pin=RUN)
            self.assertIn(NOTICE_OPENING, err)


# ======================================================================================
# §4 — CHANGE THE MESSAGE, NEVER THE VERDICT.
# ======================================================================================
class TheVerdictsAreIdentical(unittest.TestCase):
    """Stage 03's own table test is re-run in the validation block; this is the half about THIS
    branch, plus the byte-identical pin on the notice this stage must not touch."""

    def _matrix(self, root):
        rows = {}
        impl, test, doc = (os.path.join(root, "src", "app.py"),
                           os.path.join(root, "tests", "test_app.py"),
                           os.path.join(root, "README.md"))
        for label, target in (("impl", impl), ("test", test), ("doc", doc), ("none", "")):
            out = G.check_write(root, target, run_id=RUN)
            rows[label] = (out.allowed, out.gate)
        return rows

    def test_a2a_the_documented_decision_table_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertEqual(self._matrix(d),
                             {"impl": (True, None), "test": (True, None),
                              "doc": (True, None), "none": (True, None)})
            _register(d)
            self.assertEqual(self._matrix(d),
                             {"impl": (False, G.GATE_PHASE), "test": (True, None),
                              "doc": (True, None), "none": (True, None)})
            _raw_store(d).write(G.APPROACH_PREFIX + RUN, {"approach": "the chosen one"})
            self.assertEqual(self._matrix(d)["impl"], (False, G.GATE_SPEC))
            _raw_store(d).write(G.SPEC_PREFIX + RUN,
                                {"criteria": [{"id": "AC1", "text": "it works"}]})
            self.assertEqual(self._matrix(d)["impl"], (False, G.GATE_TDD))
            _raw_store(d).write(T.state_key(RUN), T.to_state(RUN, red=["test_login"], green=[]))
            self.assertEqual(self._matrix(d)["impl"], (True, None))

    def test_a2a_the_only_thing_that_moved_is_a_notice(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out = G.check_write(d, _impl(d), run_id=RUN)
            self.assertEqual((out.allowed, out.gate, out.overridden), (True, None, False))
            self.assertEqual(out.reason, UNREGISTERED_REASON)

    @unittest.skipUnless(_POSIX_PERMS, "needs POSIX permissions and a non-root euid")
    def test_a2a_the_unreadable_state_keeps_that_same_reason(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = _impl(d)
            with _checkpoint_unstattable(d):
                out = G.check_write(d, target, run_id=RUN)
            self.assertEqual((out.allowed, out.gate, out.overridden), (True, None, False))
            self.assertEqual(out.reason, UNREGISTERED_REASON)

    def test_a2a_stage_03s_notice_is_byte_identical(self):
        """`:495` is not this stage's to touch. Pinned as a LITERAL, not a substring: a reworded
        middle would slip past every `assertIn` in either file."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            target = _impl(d)
            out = G.check_write(d, target)          # no run state, no pin, no id -> `:495`
            self.assertEqual(out.notice, STAGE_03_NOTICE)
            self.assertEqual(out.reason, "no mokata run has state in this repo")


if __name__ == "__main__":
    unittest.main()
