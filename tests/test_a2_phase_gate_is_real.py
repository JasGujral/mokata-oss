"""A2 — `phase-gate-is-real` (0.0.19 stage 03). Closes #28.

⚠ THE SLUG OVER-PROMISES AND IS KEPT ANYWAY. It was chosen before doc 104 §9 corrected §2's A2
table in two places. This stage does NOT make the gate enforce where it deliberately does not.
Read §9, never the name.

WHAT #28 ACTUALLY REPORTS, ONCE THE CODE IS READ
------------------------------------------------
#28 says "the phase gate is opt-in by default". The accurate statement is narrower and worse:
**you cannot tell whether you have one.** One principle, two surfaces:

* **The window (F7).** A run with no registered state ALLOWs — correctly. `gate_hook.py:17-19`
  argues that floor ("a gate that makes the editor unusable would be uninstalled, and an
  uninstalled gate enforces nothing") and `check_write` resolves EVERY uncertainty to ALLOW by
  design. What was wrong is that the allow said NOTHING, and a user watching mokata not block
  reads silence as approval. ⛔ **Not one verdict changes here** — `TheVerdictsAreIdentical`
  pins the whole table independently of any wording.
* **The repo (F8).** An interactive init OFFERS to wire the harness; a scripted init never did,
  and a DECLINED offer was silent. The gates run as harness hooks, so a repo with `.mokata/` and
  no wiring records runs and polices none — and said so nowhere.

WHAT THESE TESTS REFUSE TO LET DRIFT
------------------------------------
* **The floor is PINNED, not merely preserved.** `test_a2_regression_*` asserts an unregistered
  implementation write is STILL ALLOWED *and* now carries a notice. A test that only checked the
  notice would pass against a build that BLOCKS — `test_a2_the_floor_pin_is_load_bearing`
  reproduces exactly that build and shows the notice-only half cannot tell them apart. Stage 01
  and stage 02 both set this bar.
* **Once-per-session means once**, and a new window says it again — both asserted, through the
  real hook subprocess, because the marker is a filesystem fact and not a return value.
* **The disclosure is MEASURED, never inferred** from what the caller just did. A plugin-route
  repo enforces with no `.claude/settings.json` at all, and telling that user the gate is off
  would be `DISCLOSURE-PRESENCE-IS-NOT-DISCLOSURE-TRUTH` pointed the other way.
* **Three states, never two (doc 85 §7g).** "not wired" and "could not check" are different
  facts and render as different sentences. `hook_wiring_report` already carries `unverifiable`
  for this reason; collapsing it one layer up would have thrown that away.
* **§7i — the comment fix is GRADED.** Four shipped sites named a wiring release F2(b) moved.
  Correcting four strings and building nothing that keeps them fixed is not a fix, so the sweep
  below reds on any D14-aware `src/` module that names a different release — and on one that
  quietly stops naming a release at all, which is how the first check would be escaped.

⚠ THIS FILE DELIBERATELY AVOIDS THE WORD THAT PUTS A TEST IN `_deprecation_removal`'s DOMAIN.
`notice_pins` sweeps every test file whose text carries that vocabulary and reds on a release
literal inside an assertion argument; the planted corpora below carry release literals on
purpose. They are module constants, never assertion arguments, and this file is out of that
domain — both, so neither is the only thing holding.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import gate_hook as G                                    # noqa: E402
from mokata import harness_setup, hook_wiring, onboarding            # noqa: E402
from mokata import tdd_state as T                                    # noqa: E402
from mokata.cli import main                                          # noqa: E402
from mokata.cli_commands import setup as SETUP                       # noqa: E402
from mokata.hook_wiring import (GATE_ENFORCING, GATE_GUARD_SUBCOMMAND,   # noqa: E402
                                GATE_NOT_WIRED, GATE_UNVERIFIABLE,
                                SETUP_REMEDY, gate_enforcement_state,
                                render_gate_enforcement)
from mokata.init import init_repo                                    # noqa: E402
from mokata.state import StateStore                                  # noqa: E402

RUN = "run0123456789abcdef"
SRC_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

# The one sentence the window's notice must always open with. It is the AMBIGUOUS branch's own
# opening, reused rather than re-invented: two spellings of "your gates are off right now" is
# how a user learns to read one of them as a different fact.
NOTICE_OPENING = "mokata: run-state gates are OFF for this window"


# ======================================================================================
# fixtures
# ======================================================================================
def _silent(_s):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return d


def _raw_store(root):
    return StateStore(T.state_dir(root))


def _approve(root, run=RUN):
    _raw_store(root).write(G.APPROACH_PREFIX + run, {"approach": "the chosen one"})


def _emit_spec(root, run=RUN):
    _raw_store(root).write(G.SPEC_PREFIX + run, {"criteria": [{"id": "AC1", "text": "it works"}]})


def _record_red(root, run=RUN, test_id="test_login"):
    _raw_store(root).write(T.state_key(run), T.to_state(run, red=[test_id], green=[]))


def _register(root, run=RUN):
    """Give the run a CHECKPOINT — what `_run_registered` reads. Without it the run is
    unregistered and the phase gate's own floor (a different branch) applies."""
    _raw_store(root).write(G.CHECKPOINT_PREFIX + run, {"phase": "brainstorm"})


class _TtyStdin:
    """A REAL terminal as far as every check mokata makes is concerned, handing back a scripted
    answer. `input()` falls back to `readline()` because `fileno()` raises, so the answer is read
    through the same path a human's keystroke takes (stage 02's double, same reason)."""

    def __init__(self, answer="y", times=8):
        self._lines = [answer if answer.endswith("\n") else answer + "\n"] * times

    def isatty(self):
        return True

    def readline(self, *_a):
        return self._lines.pop(0) if self._lines else ""

    def read(self, *_a):
        return self.readline()

    def fileno(self):
        raise OSError("no fileno")


def _envelope(path, cwd, tool="Write", session_id="cc-window-1"):
    return json.dumps({
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": path, "content": "def login(): return True\n"},
    })


def _hook(path, cwd, tool="Write", session_id="cc-window-1", env=None, timeout=60):
    """The hook in its real situation: a subprocess, the envelope on stdin, exit code + stderr."""
    e = dict(os.environ)
    e.pop("MOKATA_SESSION_ID", None)
    e["PYTHONPATH"] = SRC_ROOT
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from mokata.hook_cli import gate_guard_main; sys.exit(gate_guard_main([]))"],
        input=_envelope(path, cwd, tool, session_id), text=True, capture_output=True,
        env=e, timeout=timeout, cwd=cwd,
    )
    return proc.returncode, proc.stderr


# ======================================================================================
# F7 — the window. THE FLOOR HOLDS, and it now says so.
# ======================================================================================
class TheFloorHolds(unittest.TestCase):

    def _assert_floor_held(self, outcome):
        """THE pin, in one place so the load-bearing negative can run the SAME assertions.

        Both halves, and the ORDER matters: the allow is the contract (P14, `gate_hook.py:17-19`)
        and the notice is the new legibility. A build that satisfied only the second would have
        reversed an argued design decision."""
        self.assertTrue(outcome.allowed, "the SI.1 floor was reversed — this write must ALLOW")
        self.assertEqual(outcome.exit_code, 0, "an allow that does not exit 0 is not an allow")
        self.assertIsNotNone(outcome.notice, "the allow is silent again — that IS #28")
        self.assertIn(NOTICE_OPENING, outcome.notice)

    def test_a2_regression_an_unregistered_implementation_write_still_allows_and_now_says_so(self):
        """The whole of F7: same verdict, new sentence."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self._assert_floor_held(G.check_write(d, os.path.join(d, "src", "app.py")))

    def test_a2_the_floor_pin_is_load_bearing(self):
        """DEFEAT the design and watch the wrong build get through the weaker half.

        The build being reproduced is the one §2's A2 row literally asked for: treat the
        unregistered allow as a fail-open bug and BLOCK it, while still carrying the notice.
        `_assert_floor_held` must reject it. Without this, a notice-only assertion would pass
        against that build and would be grading nothing (doc 85 §7f)."""
        wrong = G.GateOutcome(False, "unregistered — blocked", gate=G.GATE_PHASE,
                              notice=NOTICE_OPENING + " — and this build blocks anyway")
        with self.assertRaises(AssertionError):
            self._assert_floor_held(wrong)
        # And the reason it is load-bearing, stated as an assertion rather than as prose: the
        # notice half CANNOT tell the two builds apart.
        self.assertIn(NOTICE_OPENING, wrong.notice)

    def test_a2_the_hook_exits_zero_and_puts_the_notice_on_stderr(self):
        """End to end, through the real subprocess — the notice is worthless if it never surfaces."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            code, err = _hook(os.path.join(d, "src", "app.py"), d)
            self.assertEqual(code, 0, f"the floor was reversed at the hook: {err}")
            self.assertIn(NOTICE_OPENING, err)

    def test_a2_the_notice_fires_once_per_window_and_not_on_the_second_write(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = _hook(os.path.join(d, "src", "app.py"), d, session_id="w1")
            second = _hook(os.path.join(d, "src", "other.py"), d, session_id="w1")
            self.assertIn(NOTICE_OPENING, first[1])
            self.assertNotIn(NOTICE_OPENING, second[1],
                             "once-per-session is firing every write — that is the noise that "
                             "gets the hook uninstalled")
            self.assertEqual((first[0], second[0]), (0, 0), "neither write may be blocked")

    def test_a2_a_new_window_is_told_again(self):
        """The marker is keyed by the harness session id, so a fresh window is a fresh user."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _hook(os.path.join(d, "src", "app.py"), d, session_id="w1")
            _code, err = _hook(os.path.join(d, "src", "app.py"), d, session_id="w2")
            self.assertIn(NOTICE_OPENING, err)

    # ---- the negatives. A notice on every edit in every repo is noise, and noise gets the hook
    # ---- uninstalled, which is `gate_hook.py:19` costing us the whole gate to fix a smaller
    # ---- problem. Each of these is a path the notice must never reach.

    def test_a2_a_test_path_is_never_noticed(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out = G.check_write(d, os.path.join(d, "tests", "test_app.py"))
            self.assertTrue(out.allowed)
            self.assertIsNone(out.notice)

    def test_a2_a_non_implementation_path_is_never_noticed(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for name in ("README.md", "data.json", "pyproject.toml", ""):
                out = G.check_write(d, os.path.join(d, name) if name else "")
                self.assertTrue(out.allowed, name)
                self.assertIsNone(out.notice, name)

    def test_a2_a_non_mokata_repo_is_never_noticed(self):
        """No `.mokata/` at all — the hook returns before `check_write` is ever called."""
        with tempfile.TemporaryDirectory() as d:
            code, err = _hook(os.path.join(d, "src", "app.py"), d)
            self.assertEqual(code, 0)
            self.assertEqual(err.strip(), "")

    def test_a2_a_registered_run_is_never_given_the_unregistered_notice(self):
        """The notice is about the UNREGISTERED window. A run that is being gated must not get it
        — it would tell a user enforcement is off in the very moment it is on."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d), _record_red(d), _register(d)
            out = G.check_write(d, os.path.join(d, "src", "app.py"), run_id=RUN)
            self.assertTrue(out.allowed)
            self.assertIsNone(out.notice)

    def test_a2_a_blocked_write_is_not_told_the_gates_are_off(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _register(d)
            out = G.check_write(d, os.path.join(d, "src", "app.py"), run_id=RUN)
            self.assertFalse(out.allowed, "the spec gate stopped firing — that is a real regression")
            self.assertIsNone(out.notice)

    # ---- shape + safety

    def test_a2_the_notice_copies_the_ambiguous_branch_rather_than_inventing_a_second_shape(self):
        """Both uncertainty branches are ALLOW + a once-per-session notice with one opening."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            unregistered = G.check_write(d, os.path.join(d, "src", "app.py"))
            _raw_store(d).write(G.APPROACH_PREFIX + "run-aaaa", {"approach": "a"})
            _raw_store(d).write(G.APPROACH_PREFIX + "run-bbbb", {"approach": "b"})
            ambiguous = G.check_write(d, os.path.join(d, "src", "app.py"))
        self.assertTrue(ambiguous.allowed and unregistered.allowed)
        for out in (unregistered, ambiguous):
            self.assertTrue(out.notice.startswith(NOTICE_OPENING))

    def test_a2_the_notice_names_how_to_turn_enforcement_on(self):
        """A notice that reports a problem and no exit is the shape that gets tools uninstalled."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            # The join is hoisted OUT of the assertion on purpose: an `os.path.join` inside an
            # assertion argument, in a module that posix-wraps elsewhere, is a spelling claim,
            # and `_windows_portability.one_sided_posix_sites` convicts it. It caught this line.
            target = os.path.join(d, "src", "app.py")
            self.assertIn("/mokata:brainstorm", G.check_write(d, target).notice)

    def test_a2_the_notice_carries_no_path_and_no_credential(self):
        """Secret-safety: a hook message can land in a retained transcript. The notice is about
        the WINDOW, so it needs no target path — and a path is the only thing on this code path
        that could carry a user's directory names or a credential-bearing filename."""
        secretish = "".join(("post", "gres", "://", "admin", ":", "hunt", "er2", "@", "h", "/p"))
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            leaf = "app_" + secretish.replace("/", "_").replace(":", "_") + ".py"
            notice = G.check_write(d, os.path.join(d, "src", leaf)).notice
            self.assertNotIn(leaf, notice)
            self.assertNotIn("hunt" + "er2", notice)
            self.assertNotIn(d, notice)


# ======================================================================================
# §4 — CHANGE THE MESSAGE, NEVER THE VERDICT. The whole table, wording-independent.
# ======================================================================================
class TheVerdictsAreIdentical(unittest.TestCase):
    """The binding constraint of the stage, pinned as a TABLE rather than as four spot checks.

    Every row is `(state, path) -> (allowed, gate)`. The expected column is the module docstring's
    own documented decision table, written out here so a change to it has to be a change to this
    file — the notice is not in the tuple at all, so no amount of re-wording can move a row."""

    def _matrix(self, root):
        rows = {}
        impl, test, doc = (os.path.join(root, "src", "app.py"),
                           os.path.join(root, "tests", "test_app.py"),
                           os.path.join(root, "README.md"))
        for label, target in (("impl", impl), ("test", test), ("doc", doc), ("none", "")):
            out = G.check_write(root, target, run_id=RUN)
            rows[label] = (out.allowed, out.gate)
        return rows

    def test_a2_the_documented_decision_table_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            # no run at all — the SI.1 floor
            self.assertEqual(self._matrix(d),
                             {"impl": (True, None), "test": (True, None),
                              "doc": (True, None), "none": (True, None)})
            # registered, brainstorm only -> BLOCK on implementation, nothing else
            _register(d)
            self.assertEqual(self._matrix(d),
                             {"impl": (False, G.GATE_PHASE), "test": (True, None),
                              "doc": (True, None), "none": (True, None)})
            # approach approved, no spec -> the spec gate
            _approve(d)
            self.assertEqual(self._matrix(d)["impl"], (False, G.GATE_SPEC))
            # spec emitted, red set EMPTY -> the TDD gate
            _emit_spec(d)
            self.assertEqual(self._matrix(d)["impl"], (False, G.GATE_TDD))
            # a failing test on record -> ALLOW. ⚠ THE ALLOW CARRIES NO GATE ID — `gate` names
            # the gate that DECIDED a refusal, and an allow on the TDD path names none. This row
            # was written the other way in the first draft of this table and the pin caught it,
            # which is the only reason it is worth pinning: it is now the recorded shape.
            _record_red(d)
            self.assertEqual(self._matrix(d)["impl"], (True, None))

    def test_a2_the_only_thing_that_moved_is_a_notice(self):
        """The changed branch, isolated: same tuple as an allow with no notice would produce."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out = G.check_write(d, os.path.join(d, "src", "app.py"))
            self.assertEqual((out.allowed, out.gate, out.overridden), (True, None, False))
            self.assertEqual(out.reason, "no mokata run has state in this repo",
                             "the REASON is the gate's identity to every existing caller; only "
                             "the notice was supposed to be new")


# ======================================================================================
# F8 — the repo. AN INIT EITHER WIRES, OR SAYS THE GATE IS NOT ENFORCING.
# ======================================================================================
class TheThreeStates(unittest.TestCase):
    """doc 85 §7g. "not wired" and "we could not find out" must never share a representation."""

    def test_a2_an_unwired_repo_measures_not_wired(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            self.assertEqual(gate_enforcement_state(d, home), GATE_NOT_WIRED)

    def test_a2_a_wired_repo_measures_enforcing(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _wire_current(d)
            self.assertEqual(gate_enforcement_state(d, home), GATE_ENFORCING)

    def test_a2_a_wired_but_dead_gate_is_not_enforcing(self):
        """Claude Code silently drops a hook whose command does not resolve. A gate that never
        runs is not a gate, and reporting it as one is the lie this module was built to end.

        ⚠ THE FIXTURE'S DEAD COMMAND IS STILL SPELLED `mokata-hook`, AND THAT IS THE WHOLE TEST.
        The first draft used an arbitrary program name — which strips the marker
        `harness_setup._is_mokata_hook` keys on, so the entry was not recognised as mokata's and
        was dropped from the report ENTIRELY. The assertion passed, for a reason that had nothing
        to do with resolution. A mutant that deleted the `hook.resolves` check SURVIVED it and is
        what said so (doc 85 §7f). This form keeps the hook mokata's and makes only its PATH
        dead, so the assertion now grades exactly the clause it names."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            path = _wire_current(d)
            data = json.loads(path.read_text(encoding="utf-8"))
            dead = os.path.join(d, "no-such-dir", "mokata-hook")
            for blocks in data["hooks"].values():
                for block in blocks:
                    for hook in block.get("hooks", []):
                        hook["command"] = dead
            path.write_text(json.dumps(data), encoding="utf-8")
            report = hook_wiring.hook_wiring_report(d, home)
            self.assertTrue(any(hook_wiring._wired_subcommand(h) == GATE_GUARD_SUBCOMMAND
                                for h in report.hooks),
                            "the fixture stopped being a mokata gate-guard hook at all — it is "
                            "no longer testing resolution")
            self.assertEqual(gate_enforcement_state(d, home), GATE_NOT_WIRED)

    def test_a2_an_unreadable_surface_is_its_own_state_and_not_a_verdict(self):
        broken = hook_wiring.HookWiringReport(unverifiable="OSError: boom")
        with mock.patch.object(hook_wiring, "hook_wiring_report", return_value=broken):
            self.assertEqual(gate_enforcement_state("."), GATE_UNVERIFIABLE)

    def test_a2_the_three_states_render_three_different_things(self):
        enforcing = render_gate_enforcement(GATE_ENFORCING)
        not_wired = render_gate_enforcement(GATE_NOT_WIRED)
        unknown = render_gate_enforcement(GATE_UNVERIFIABLE)
        self.assertIsNone(enforcing, "an enforcing gate has nothing to disclose")
        self.assertIsNotNone(not_wired)
        self.assertIsNotNone(unknown)
        self.assertNotEqual(not_wired, unknown,
                            "a measured 'off' and an unchecked 'off' are different facts")
        self.assertIn("NOT enforcing", not_wired)
        self.assertIn("could not verify", unknown)

    def test_a2_the_disclosure_names_the_one_command_that_fixes_it(self):
        """And it READS it from `hook_wiring` rather than re-typing it — a second spelling of the
        remedy is a second thing to keep in step."""
        for state in (GATE_NOT_WIRED, GATE_UNVERIFIABLE):
            self.assertIn(SETUP_REMEDY, render_gate_enforcement(state))

    def test_a2_the_predicate_is_the_gate_guard_hook_not_the_settings_file(self):
        """A plugin-route user has no `.claude/settings.json` and IS enforcing. Wiring every hook
        EXCEPT gate-guard is the sharper case: the file exists, mokata is set up, and the gate
        still is not there."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            path = _wire_current(d)
            data = json.loads(path.read_text(encoding="utf-8"))
            for event, blocks in list(data["hooks"].items()):
                kept = []
                for block in blocks:
                    hooks = [h for h in block.get("hooks", [])
                             if GATE_GUARD_SUBCOMMAND not in (h.get("args") or [])]
                    if hooks:
                        block["hooks"] = hooks
                        kept.append(block)
                data["hooks"][event] = kept
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(path.is_file(), "the settings file is still here — that is the point")
            self.assertEqual(gate_enforcement_state(d, home), GATE_NOT_WIRED)


def _wire_current(root):
    """EXACTLY the wiring today's `mokata setup claude` writes, from the same plan the product
    uses — so this fixture cannot drift from what setup actually lands."""
    path = Path(root) / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    harness_setup._merge_hooks(path, harness_setup.plan_setup("claude", root=str(root)).hook_commands)
    return path


class AnInitSaysWhetherTheSeatbeltIsOn(unittest.TestCase):
    """F8.3 — the deliverable that actually closes #28, and neither filed sub-row named it."""

    def _init(self, argv, root, home, answer="y"):
        """Run the real CLI at a TTY that answers `init_repo`'s one question.

        ⚠ THE TTY IS NOT A CONVENIENCE. Off a terminal and without `--yes`, `init_repo`'s human
        gate FAILS CLOSED and the init aborts having written nothing — so there is no repo to
        disclose about and the disclosure correctly never fires. The paths that reach a WRITTEN
        manifest without wiring are the consented ones, and this is what consent looks like from
        a script. Driven at the stdin boundary, never by patching the reader (doc 85 §7e)."""
        buf = io.StringIO()
        env = dict(os.environ)
        env["HOME"] = home
        original = sys.stdin
        sys.stdin = _TtyStdin(answer)
        try:
            with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(buf):
                rc = main(list(argv) + ["--path", root])
        finally:
            sys.stdin = original
        return rc, buf.getvalue()

    def test_a2_regression_a_scripted_init_says_the_gate_is_not_enforcing(self):
        """THE #28 closer. Before this stage the whole output ended at "mokata initialized"."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            rc, out = self._init(["init", "--profile", "minimal"], d, home)
            self.assertEqual(rc, 0)
            self.assertIn("NOT enforcing", out)
            self.assertIn(SETUP_REMEDY, out)

    def test_a2_regression_a_mode_init_says_it_too_and_before_the_quickstart(self):
        """`--mode` never routes through the wizard, and its quickstart makes the claim in words.

        ⭐ `mokata init --mode seatbelt` prints a banner reading "the gates are on" over a repo
        whose gate is NOT wired. That is #28's sentence, in our own output, and it is why the
        disclosure has to land BEFORE the quickstart rather than after it: a correction that
        arrives under the claim it corrects is read as a footnote."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            rc, out = self._init(["init", "--mode", "seatbelt"], d, home)
            self.assertEqual(rc, 0)
            self.assertIn("NOT enforcing", out)
            self.assertIn("the gates are on", out, "the mode banner moved — re-anchor this test")
            self.assertLess(out.index("NOT enforcing"), out.index("the gates are on"),
                            "the disclosure arrives under the claim it corrects")

    def test_a2_an_already_enforcing_repo_is_never_told_the_gate_is_off(self):
        """The state is MEASURED. A disclosure that fires regardless of the truth is
        `DISCLOSURE-PRESENCE-IS-NOT-DISCLOSURE-TRUTH` wearing a fix."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _wire_current(d)
            _rc, out = self._init(["init", "--profile", "minimal"], d, home)
            self.assertNotIn("NOT enforcing", out)

    def test_a2_a_yes_init_wires_the_harness_and_prints_that_it_did(self):
        """F8.1. `--yes` is consent to the whole init plan and the gate is part of that plan —
        consent ALREADY GIVEN, never consent assumed."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            calls = []

            def _fake(**kw):
                calls.append(kw)
                _wire_current(kw["root"])
                return harness_setup.SetupResult(touched=[], plan=None)

            with mock.patch.object(SETUP, "setup_harness", _fake):
                rc, out = self._init(["init", "--profile", "minimal", "--yes"], d, home)
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1, "a --yes init did not wire the harness")
            self.assertTrue(calls[0]["assume_yes"], "the wiring must not re-prompt a consented run")
            self.assertEqual(calls[0]["harness"], SETUP.INIT_HARNESS)
            self.assertIn("wired the run-state gate", out)
            self.assertNotIn("NOT enforcing", out, "it wired — saying otherwise would be false")

    def test_a2_an_init_without_yes_wires_nothing(self):
        """P2. The only paths that write `.claude/settings.json` are `--yes` (consent given) and
        the wizard's accepted offer (consent asked). There is no third."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            with mock.patch.object(SETUP, "setup_harness") as spy:
                self._init(["init", "--profile", "minimal"], d, home)
            spy.assert_not_called()
            self.assertFalse((Path(d) / ".claude" / "settings.json").exists())

    def test_a2_a_failed_wiring_never_fails_an_init_that_already_succeeded(self):
        """Degrade-clean — and the disclosure that follows is then TRUE, because nothing wired."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            def _boom(**_kw):
                raise harness_setup.SetupError("no console script here")
            with mock.patch.object(SETUP, "setup_harness", _boom):
                rc, out = self._init(["init", "--profile", "minimal", "--yes"], d, home)
            self.assertEqual(rc, 0, "the manifest is on disk; the init succeeded")
            self.assertIn("NOT enforcing", out)

    def test_a2_init_repo_itself_is_unchanged(self):
        """The manifest write is untouched: `init_repo` still writes `.mokata/` and nothing else.
        Everything this stage added sits ABOVE it, in the command."""
        with tempfile.TemporaryDirectory() as d:
            res = init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            self.assertFalse(res.aborted)
            self.assertFalse((Path(d) / ".claude").exists())
            self.assertTrue((Path(d) / ".mokata" / "manifest.json").exists())


class TheWizardsOfferSurvivesAndADeclineIsNoLongerSilent(unittest.TestCase):
    """F8.2 + F8.3 on the interactive path."""

    def _wizard(self, answers, root, home, wire=None):
        out = []
        asked = []

        def _confirm(prompt):
            asked.append(prompt)
            for needle, value in answers.items():
                if needle in prompt:
                    return value
            return True

        res = onboarding.run_wizard(root=root, confirm=_confirm, out=out.append,
                                    ask=lambda _p, _c, default: default, home=home,
                                    profile="minimal", wire_harness=wire,
                                    offer_embeddings=False)
        return res, "\n".join(out), asked

    def test_a2_the_offer_is_still_asked(self):
        """Removing a consented durable write to a file the user's other tools also read would
        trade P2 for tidiness — and it is the shape that gets a tool uninstalled."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _res, _out, asked = self._wizard({"Wire mokata into": False}, d, home)
            self.assertTrue(any("Wire mokata into" in q for q in asked),
                            "the wizard's harness offer was removed or auto-accepted")

    def test_a2_regression_a_declined_offer_now_says_the_gate_is_not_enforcing(self):
        """THE other #28 closer. A declined offer used to end with a tidy recap and nothing else,
        and the user walked away believing they had a seatbelt."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            res, out, _asked = self._wizard({"Wire mokata into": False}, d, home)
            self.assertFalse(res.harness_wired)
            self.assertIsNotNone(res.gate_notice)
            self.assertIn("NOT enforcing", out)
            self.assertIn(SETUP_REMEDY, out)

    def test_a2_a_decline_still_wires_nothing(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            self._wizard({"Wire mokata into": False}, d, home)
            self.assertFalse((Path(d) / ".claude" / "settings.json").exists())
            self.assertTrue((Path(d) / ".mokata" / "manifest.json").exists())

    def test_a2_an_accepted_offer_is_not_told_the_gate_is_off(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            with mock.patch.object(harness_setup, "setup_harness") as fake:
                fake.side_effect = lambda **kw: (
                    _wire_current(kw["root"]),
                    harness_setup.SetupResult(touched=[], plan=None))[1]
                res, out, _asked = self._wizard({}, d, home, wire=True)
            self.assertTrue(res.harness_wired)
            self.assertIsNone(res.gate_notice)
            self.assertNotIn("NOT enforcing", out)


class TheMcpInitToolAnswersTheSameQuestion(unittest.TestCase):
    """The MCP `init` tool reaches `init_repo` without the wizard and wires nothing either."""

    def test_a2_the_committed_result_carries_the_gate_state(self):
        from mokata.mcp import tools_config
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {**os.environ, "HOME": home}):
                out = _support.mcp_commit(tools_config.init, path=d, profile="minimal")
        result = out.get("result") or {}
        self.assertEqual(result.get("gate_enforcement"), GATE_NOT_WIRED)
        self.assertIn("NOT enforcing", result.get("gate_notice") or "")

    def test_a2_the_tool_and_the_cli_use_the_one_renderer(self):
        """Three surfaces describing the same unwired repo three ways is how they disagree."""
        import inspect
        from mokata.mcp import tools_config
        for src in (inspect.getsource(tools_config.init),
                    inspect.getsource(SETUP._wire_or_disclose),
                    inspect.getsource(onboarding.run_wizard)):
            self.assertIn("render_gate_enforcement", src)


# ======================================================================================
# F2(b)'s comment debt — and §7i: the fix is GRADED, not just made.
# ======================================================================================
# The RULED release, declared once. It is deliberately a LITERAL and not derived from
# `__version__`: F2(b) ruled a SPECIFIC release for D1's wiring, so deriving "the next one" would
# silently re-date every one of these comments the moment the version bumps — which is precisely
# the drift this guard exists to catch.
RULED_WIRING_RELEASE = "0.0.20"
_D14 = "D14"
_RELEASE = re.compile(r"deferred to (\d+\.\d+\.\d+)")


def d14_release_pins(sources, ruled):
    """`[(path, release)]` — a D14-aware `src/` module naming a wiring release that is NOT the
    ruled one. Over a SUPPLIED corpus, never a walk that goes and finds the tree (doc 85 §7i)."""
    found = []
    for path in sorted(sources):
        text = " ".join((sources[path] or "").split())
        if _D14 not in text:
            continue
        for release in _RELEASE.findall(text):
            if release != ruled:
                found.append((path, release))
    return tuple(found)


def d14_modules_naming_no_release(sources):
    """`[path]` — D14-aware modules that name NO wiring release at all.

    The §7j half. `d14_release_pins` derives over "deferred to <release>"; a comment reworded to
    drop that phrase escapes it completely and says nothing, and nothing would ever report it."""
    return tuple(path for path in sorted(sources)
                 if _D14 in " ".join((sources[path] or "").split())
                 and not _RELEASE.search(" ".join((sources[path] or "").split())))


def _src_corpus():
    """The `src/` modules this guard ranges over, as `{posix-relative name: text}`."""
    # CORPUS: THE WORKING TREE. The question is "does any module mokata SHIPS promise a wiring
    # release F2(b) moved?", and what ships is what `sync-public.sh` rsyncs — the files on disk,
    # not the git index. An untracked module sitting in `src/` would reach the mirror and a
    # wheel, so it must be swept; a tracked-but-deleted one would not.
    corpus = {}
    for dirpath, dirnames, filenames in os.walk(SRC_ROOT):
        dirnames[:] = [n for n in dirnames if n != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                p = os.path.join(dirpath, name)
                with open(p, encoding="utf-8") as fh:
                    corpus[_support.posix_rel(p, SRC_ROOT)] = fh.read()
    return corpus


class NoShippedCommentPromisesAWiringReleaseWeRuledAway(unittest.TestCase):

    def test_a2_the_corpus_is_not_empty_and_holds_the_four_sites(self):
        """A sweep over an empty corpus passes having read nothing — the vacuity check the
        assertions below cannot make about themselves. The four are named as FILES, never as a
        count: a count of a moving set is doc 85 §7j."""
        corpus = _src_corpus()
        self.assertGreater(len(corpus), 200)
        for path in ("mokata/adoption_modes.py", "mokata/brainstorm.py",
                     "mokata/mcp/tools_spec.py", "mokata/govern/graph_required.py"):
            self.assertIn(path, corpus)
            self.assertIn(_D14, corpus[path], f"{path} stopped being a site of this guard")

    def test_a2_no_src_module_promises_a_different_wiring_release(self):
        pins = d14_release_pins(_src_corpus(), RULED_WIRING_RELEASE)
        self.assertEqual(pins, (), "a shipped comment promises a wiring release F2(b) moved: "
                                   "%r" % (pins,))

    def test_a2_no_d14_module_quietly_stops_naming_a_release(self):
        silent = d14_modules_naming_no_release(_src_corpus())
        self.assertEqual(silent, (), "a D14 comment names no wiring release at all: %r" % (silent,))

    def test_a2_the_sweep_finds_a_PLANTED_offender_and_leaves_the_rest_alone(self):
        """§7i — the tree holds no offender now, so one is supplied. If this guard were only ever
        run against a tree we just corrected, it would grade nothing."""
        stale = "0.0" + ".19"
        planted = {
            "mokata/bad.py": f"# wiring deferred to {stale} by ruling {_D14}\n",
            "mokata/good.py": f"# wiring deferred to {RULED_WIRING_RELEASE} by F2(b), "
                              f"which supersedes {_D14}\n",
            "mokata/other.py": f"# validation is deferred to {stale} by some other ruling\n",
        }
        self.assertEqual(d14_release_pins(planted, RULED_WIRING_RELEASE),
                         (("mokata/bad.py", stale),))
        self.assertEqual(d14_modules_naming_no_release(planted), ())

    def test_a2_a_reworded_comment_that_drops_the_release_is_caught_by_the_other_half(self):
        planted = {"mokata/quiet.py": f"# wiring is deferred by ruling {_D14}, date TBD\n"}
        self.assertEqual(d14_release_pins(planted, RULED_WIRING_RELEASE), ())
        self.assertEqual(d14_modules_naming_no_release(planted), ("mokata/quiet.py",))

    def test_a2_the_four_sites_cite_the_ruling_that_moved_them(self):
        """A corrected date with no provenance is the next reader's mystery."""
        corpus = _src_corpus()
        for path in ("mokata/adoption_modes.py", "mokata/brainstorm.py",
                     "mokata/mcp/tools_spec.py", "mokata/govern/graph_required.py"):
            self.assertIn("F2(b)", corpus[path], f"{path} does not say who moved the release")


if __name__ == "__main__":
    unittest.main()
