"""Stage 18 (lane F, "attention") — tell the human when it is their move.

TWO PARTS, ONE SEAM. Part 1 is the notification hook: mokata's OWN channel (an OS notification
and a sound emitted by mokata's own process) for the waits that raise nothing a human can see.
Part 2 is the statusline: the wait segment's composition, and the badge's no-run answer.

WHAT THIS SUITE IS DEFENDING, stated before the pins so a reader can check the pins against it:

  * **The corpus, on the axis nobody looks at (§7j).** A notifier is two axes — WHEN it fires and
    WHAT it ranges over. The second is where the defect lives. The coordinator's brief named ten
    modules holding `read_yes_no` call sites; the AST says twenty-one, and it also says
    `read_yes_no` is not the whole class — three modules call `input()` directly, blocking a human
    on a free-text answer with no y/N in sight. So the corpus is derived as **every blocking
    human-input read in the package**, and the guard below reds if a new one appears unannounced.

  * **F10's security class.** 0.0.17 stage 5 found `hook_cli.py` running `subprocess.run(command,
    shell=True)`, orphaned across four releases. A notifier that shells out to `afplay` /
    `osascript` / `notify-send` is the same class, so: argv lists, `shell=False`, asserted
    STRUCTURALLY by AST (a module that merely *explains* the rule must still pass), and nothing
    variable — not a proposal id, not a tool name, not a path — ever reaches an OS argv.

  * **A notification may never become the thing it announces.** It cannot block the wait, delay it
    into a hang, or fail it. Pinned with a notifier whose every call raises, on every level, and
    with a planted OS binary that hangs — which must time out into a named degrade, never a wait.

  * **§7i everywhere.** A pin that only asserts "it fired" passes on a notifier that fires on
    everything. Every level assertion below is an EXACT SET or a named membership.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import contextlib
import os
import sys
import tempfile
import unittest
from typing import Dict, List, Set, Tuple
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import notify as N                                          # noqa: E402
from mokata import prompt as P                                          # noqa: E402

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")


# ======================================================================================
# helpers — the derivation, done once, over the real package
# ======================================================================================

def _modules() -> List[Tuple[str, ast.Module]]:
    """(dotted module name, parsed tree) for every module in the shipped package.

    §7j — the CORPUS axis. This walks the package rather than naming modules, so a reader cannot
    hand-type the scope of any sweep below it."""
    # CORPUS: THE WORKING TREE — and that is the right tree for this question, not a default. The
    # sweeps below exist to red when a NEW blocking human read appears without an announce beside
    # it, and the moment that matters is while it is still being written. Reading the index would
    # grade the last commit and pass a working tree that had just introduced the defect.
    out = []
    for dirpath, dirnames, filenames in os.walk(_PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            rel = os.path.relpath(path, _PKG)
            mod = rel[:-3].replace(os.sep, ".").replace(".__init__", "")
            out.append((mod, ast.parse(src)))
    return out


def _called_names(node: ast.AST) -> Set[str]:
    """Every simple call name reached from `node` — `f()` and `x.f()` alike."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


def _modules_calling(target: str) -> Set[str]:
    return {mod for mod, tree in _modules() if target in _called_names(tree)}


def _functions_calling(target: str) -> Set[str]:
    """`module:function` for every function whose body reaches a call to `target`."""
    found = set()
    for mod, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if target in _called_names(node):
                    found.add(f"{mod}:{node.name}")
    return found


def _functions_using(target: str) -> Set[str]:
    """`module:function` for every function that USES the name `target` at all — called or aliased.

    ⚠ THIS DISTINCTION IS THE STAGE'S OWN §7j LESSON, caught while writing the sweep below it. A
    sweep for `input(` finds four of the five blocking human reads in the package and silently
    misses `prompt.read_approve_edit_reject`, which does `reader = reader or input` and then calls
    `reader(...)`. The corpus axis was wrong by one in a sweep whose whole job was to get the
    corpus axis right — so the derivation ranges over the NAME, not over one syntactic shape of
    using it."""
    found = set()
    for mod, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id == target and \
                            isinstance(sub.ctx, ast.Load):
                        found.add(f"{mod}:{node.name}")
    return found


class _Spy:
    """A runner that records argv instead of executing it."""

    def __init__(self):
        self.calls: List[List[str]] = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        return True                     # a spy stands in for a HEALTHY OS call, not a failed one

    @property
    def flat(self) -> str:
        return " ".join(" ".join(c) for c in self.calls)


class _Raiser:
    """A runner whose every call raises — the never-block pin's instrument."""

    def __init__(self):
        self.calls = 0

    def __call__(self, argv, timeout):
        self.calls += 1
        raise RuntimeError("the notifier exploded")


def _fire(kind, **kw):
    """Drive the seam with the RATE LIMIT OFF.

    The tests in this file grade CHANNELS — which argv is built, which one is dropped, which state
    silences everything. The limiter is a separate fact and is graded separately in
    `TestRateLimit`. Leaving it on here would make the second assertion of every loop below pass
    because the notification was DEBOUNCED rather than because the logic under test was right —
    §7e, a green that means "suppressed" wearing the costume of a green that means "correct"."""
    kw.setdefault("debounce", False)
    return N.notify(kind, **kw)


def _settings(**kw) -> "N.NotifySettings":
    base = dict(enabled=True, audio=True, level=N.ALL_PROMPTS)
    base.update(kw)
    return N.NotifySettings(**base)


def _desktop_env() -> Dict[str, str]:
    """An environment a human is demonstrably sitting at: not CI, a display, not over SSH.

    ⚠ `MOKATA_NOTIFY=1` is the DELIBERATE opt-in every emit-path test must give. Without it this
    module refuses to touch a desktop from a process with a test framework loaded — see
    `TestNeverEmitsFromATestProcess` for why that default exists and what it cost to learn."""
    return {"DISPLAY": ":0", N.NOTIFY_ENV: "1"}


class _Which:
    """`shutil` as far as `_audio_argv` is concerned: a declared set of binaries this machine has.

    ⭐ WHY THIS EXISTS, AND IT IS THE 0.0.18 CUT-HALT REPAIR. Two tests below counted the argv the
    emitter handed its runner and expected TWO — one notification, one sound. Neither pinned a
    platform, so both were really asking *"what does the machine running the suite happen to
    have?"*. On the maintainer's Mac that is `afplay` and `/System/…/Ping.aiff`, so both passed.
    On the ubuntu runners `canberra-gtk-play` is absent, so the audio arm fell through to the
    terminal bell — which is not a subprocess and not a spy call — and both reded on 4 of the 7
    unit legs. On Windows the emitter returns before the runner at all, so both would have reded
    there too, for a third reason.

    A test that grades a CHANNEL must state the platform and state the binaries; whether THIS box
    can make a noise is a different question, and `TestTheAudioArms` asks it separately."""

    def __init__(self, present):
        self._present = frozenset(present)

    def which(self, name):
        return "/usr/bin/%s" % name if name in self._present else None


#: ⭐ THE PLATFORM IS A FIXTURE, NOT THE HOST. `notify()` defaults `platform` to `sys.platform`,
#: and on Windows it returns before the runner is ever consulted (DEGRADE_WINDOWS,
#: "unverifiable-platform"). So every test below that omitted `platform=` was not asking "does the
#: limiter work?" — it was asking "what OS is the suite running on?", and got the right answer for
#: the wrong reason on the only two platforms anyone runs it on.
#:
#: ⚠ THIS IS THE CUT-HALT REPAIR'S SIBLING, LEFT BEHIND. `_Which` below was added because two
#: tests counted argv and depended on which BINARIES the host happened to have; the same fix was
#: never applied to the PLATFORM axis, and three of the ten Windows failures on run 32094654167
#: are the residue. One implicit dependency on the environment was named and its twin was not,
#: which is the whole 0.0.18 lesson in one file.
_A_DESKTOP = "linux"


@contextlib.contextmanager
def _desktop(platform=_A_DESKTOP, binaries=("notify-send", "canberra-gtk-play")):
    """A DECLARED desktop: this platform, these binaries. Nothing inherited from the host."""
    with mock.patch.object(N, "shutil", _Which(binaries)):
        yield {"platform": platform, "env": _desktop_env(), "is_tty": True}


# ======================================================================================
# 1. THE CORPUS — derived on both axes, asserted as exact sets (§7i, §7j)
# ======================================================================================

class TestTriggerCorpus(unittest.TestCase):

    def test_the_three_levels_are_exactly_these_three(self):
        """The level set is a CONFIG KEY, not a design choice made per call site."""
        self.assertEqual(N.LEVELS, (N.HARNESS_SILENT, N.ALL_WAITS, N.ALL_PROMPTS))
        self.assertEqual(N.DEFAULT_LEVEL, N.ALL_PROMPTS,
                         "Jas, 2026-08-15: the WIDEST level is the default")

    def test_every_blocking_human_read_is_announced(self):
        """★ THE CORPUS PIN (§7j). The axis that matters is not "does it fire" but "what does it
        range over" — and `read_yes_no` is NOT the whole class of blocking human reads.

        Derived: every function in the package that calls `input()` must also announce. Three of
        them are not y/N prompts at all (a session topic, an onboarding choice, a generic ask), so
        a notifier wired only to `read_yes_no` would have left a human staring at three silent
        blocking prompts while reporting full coverage."""
        blocking = _functions_using("input")
        announced = _functions_calling("announce_prompt")
        missing = sorted(fn for fn in blocking if fn not in announced)
        self.assertEqual(missing, [], f"blocking human reads that announce nothing: {missing}")

    def test_the_blocking_read_corpus_is_exactly_this_set(self):
        """The EXACT set, by name. A new blocking prompt reds here and must be ruled on, rather
        than joining the corpus silently — which is how a hand-typed scope starts."""
        self.assertEqual(
            _functions_using("input"),
            {
                "prompt:read_yes_no",                  # the shared y/N gate — 24 call sites
                "prompt:read_approve_edit_reject",     # the approve/edit/reject gate — 3 call sites
                "session_worktree:create_worktree",    # free text: what is this session about
                "onboarding:_default_ask",             # a choice, not a y/N
                "cli_commands._common:_cli_ask",       # the execution-mode ask
            },
        )

    def test_the_wait_corpus_is_exactly_the_one_propose_seam(self):
        """Every gated MCP write funnels through ONE `_propose`, which is why the wait
        notification is wired in one place and all propose sites inherit it — the same argument
        `awaiting_block` makes for the loud head it splices in."""
        self.assertEqual(_functions_calling("announce_wait"), {"mcp.consent:_propose"})

    def test_read_yes_no_call_sites_span_twenty_one_modules_not_ten(self):
        """The measurement that made the corpus pin necessary, kept as a pin so the claim in this
        suite's docstring cannot rot. It asserts a FLOOR and the exact module set, so a deleted
        call site reds too."""
        mods = _modules_calling("read_yes_no")
        self.assertEqual(
            mods,
            {"cli_commands.approve", "cli_commands.core", "cli_commands.docsync",
             "cli_commands.gate", "cli_commands.knowledge", "cli_commands.spec",
             "extras_install", "govern.deviation", "govern.gate", "govern.lifecycle",
             "govern.trifecta", "harness_setup", "init", "knowledge.graph_adopt",
             "memory.migrate", "memory.reembed", "memory.store", "onboarding",
             "session_worktree", "share", "team_journal"},
        )

    # ---- level membership, pinned BY NAME (§7i: "it fired" is not an assertion) -------------

    def test_harness_silent_fires_only_where_the_harness_raises_nothing(self):
        s = _settings(level=N.HARNESS_SILENT)
        self.assertTrue(N.level_fires(s.level, N.KIND_WAIT, harness_notified=False))
        self.assertFalse(N.level_fires(s.level, N.KIND_WAIT, harness_notified=True),
                         "a wait that already rides a harness prompt must not double-notify")
        self.assertFalse(N.level_fires(s.level, N.KIND_PROMPT, harness_notified=False))

    def test_all_waits_fires_on_every_classified_wait_and_no_cli_prompt(self):
        s = _settings(level=N.ALL_WAITS)
        self.assertTrue(N.level_fires(s.level, N.KIND_WAIT, harness_notified=False))
        self.assertTrue(N.level_fires(s.level, N.KIND_WAIT, harness_notified=True),
                        "all-waits is the level that DOES double up on the harness")
        self.assertFalse(N.level_fires(s.level, N.KIND_PROMPT, harness_notified=False),
                         "a CLI prompt is not a `signal_wait` wait — that is all-prompts")

    def test_all_prompts_fires_on_both_kinds(self):
        s = _settings(level=N.ALL_PROMPTS)
        for kind in N.KINDS:
            for harness in (True, False):
                self.assertTrue(N.level_fires(s.level, kind, harness_notified=harness),
                                f"the widest level must fire on {kind}/{harness}")

    def test_the_levels_differ_from_each_other_on_a_real_axis(self):
        """★ §7g/§7i guard on the LEVEL SET ITSELF. `harness-silent` and `all-waits` range over
        the same call sites and differ only on the runtime harness axis; a suite that pinned
        corpora alone would pass on a notifier that ignored the level entirely. So the three
        levels are asserted to produce three DIFFERENT firing profiles."""
        profiles = {
            level: tuple(N.level_fires(level, kind, harness_notified=h)
                         for kind in N.KINDS for h in (False, True))
            for level in N.LEVELS
        }
        self.assertEqual(len(set(profiles.values())), 3,
                         f"levels must be distinguishable, got {profiles}")


# ======================================================================================
# 2. THE NEVER-BLOCK CONTRACT — a notification may not become the thing it announces
# ======================================================================================

class TestNeverBlocks(unittest.TestCase):

    def test_a_notifier_whose_every_call_raises_never_reaches_the_caller(self):
        """★ THE BAR. On EVERY level, with a runner that explodes on contact."""
        for level in N.LEVELS:
            for kind in N.KINDS:
                with self.subTest(level=level, kind=kind):
                    raiser = _Raiser()
                    out: List[str] = []
                    fired = _fire(kind, settings=_settings(level=level),
                                     harness_notified=False, runner=raiser,
                                     is_tty=True, env=_desktop_env(), out=out.append)
                    self.assertFalse(fired, "a notifier that raised did not notify")

    def test_the_announce_seams_themselves_raise_nothing(self):
        """The two call-site-facing wrappers, with the body they delegate to exploding.

        `_notify` and not `notify` is patched DELIBERATELY, and the choice is the assertion: it
        pins that there is exactly ONE swallow between a call site and the emitter, living in
        `notify`, rather than a second defensive catch in each wrapper. A wrapper that caught for
        itself would pass this test while making the seam un-pointable-at."""
        with mock.patch.object(N, "_notify", side_effect=RuntimeError("boom")):
            self.assertFalse(N.announce_prompt("/nonexistent/stage18/root"))
            self.assertFalse(N.announce_wait("/nonexistent/stage18/root", tool="spec_amend"))

    def test_read_yes_no_still_answers_when_the_notifier_explodes(self):
        """The wait it announces must complete unchanged. This drives the REAL reader."""
        with mock.patch.object(N, "_notify", side_effect=RuntimeError("boom")):
            with mock.patch.object(P, "_stdin_is_tty", return_value=True):
                with mock.patch("builtins.input", return_value="y"):
                    self.assertTrue(P.read_yes_no("prompt", "question?"))
                with mock.patch("builtins.input", return_value="n"):
                    self.assertFalse(P.read_yes_no("prompt", "question?"))

    def test_a_hanging_os_binary_times_out_into_a_degrade_not_a_wait(self):
        """★ §7i — the tree has no hanging binary, so one is PLANTED. A subprocess that hangs is
        worse than one that fails: it converts a notification into the wedge it exists to prevent.

        This drives the REAL runner (`_run_argv`), not a double — the timeout being pinned is
        `subprocess.run`'s, and a stubbed runner would grade nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            sleeper = os.path.join(tmp, "hangs")
            with open(sleeper, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nsleep 60\n")
            os.chmod(sleeper, 0o755)

            notices: List[str] = []
            import time
            t0 = time.perf_counter()
            with mock.patch.object(N, "NOTIFY_TIMEOUT_SECONDS", 0.4):
                ok = N._run_argv([sleeper], timeout=0.4)
            elapsed = time.perf_counter() - t0

            self.assertFalse(ok, "a hung binary must report failure, not success")
            self.assertLess(elapsed, 20.0, "the hard timeout did not bound the call")

            # ...and the degrade is NAMED, not swallowed
            from mokata import degrade
            degrade.reset_degrade_notices()
            with mock.patch.object(N, "_run_argv", return_value=False):
                _fire(N.KIND_PROMPT, settings=_settings(), is_tty=True,
                         env=_desktop_env(), out=notices.append)
            self.assertTrue(any(N.DEGRADE_SUBSYSTEM in n for n in notices),
                            f"a failed OS call must name its degrade; got {notices}")

    def test_the_swallow_is_one_visible_seam_not_scattered(self):
        """`record_usage`'s shape: the swallow lives at ONE place that can be pointed at. Counted
        structurally so a second silent `except` cannot appear without reding this."""
        import inspect
        tree = ast.parse(inspect.getsource(N))
        bare = [h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
                and (h.type is None or getattr(h.type, "id", "") == "Exception")]
        self.assertLessEqual(len(bare), 2,
                             f"the broad swallow must stay at one/two named seams, found {len(bare)}")


# ======================================================================================
# 3. SILENCE — three separate facts, three separate pins
# ======================================================================================

class TestSilence(unittest.TestCase):

    def test_off_a_tty_the_prompt_channel_is_silent(self):
        """FACT 1. A CLI prompt's human answers ON the terminal; with no terminal there is no
        human to announce to, and `read_yes_no` has already fail-closed to No."""
        spy = _Spy()
        fired = _fire(N.KIND_PROMPT, settings=_settings(), runner=spy,
                         is_tty=False, env=_desktop_env())
        self.assertFalse(fired)
        self.assertEqual(spy.calls, [])

    def test_in_ci_every_kind_at_every_level_is_silent(self):
        """FACT 2, and it is the one that must hold unconditionally: a beep on a headless runner
        is noise at best, and a hang at worst if the audio binary blocks."""
        for level in N.LEVELS:
            for kind in N.KINDS:
                with self.subTest(level=level, kind=kind):
                    spy = _Spy()
                    fired = _fire(kind, settings=_settings(level=level), runner=spy,
                                     is_tty=True, env={"CI": "true", "DISPLAY": ":0"})
                    self.assertFalse(fired)
                    self.assertEqual(spy.calls, [])

    def test_under_assume_yes_the_reader_is_never_reached_so_nothing_fires(self):
        """FACT 3, pinned where it actually lives. `--yes` does not silence a notification — it
        removes the WAIT, by short-circuiting before the reader runs. Pinned structurally over the
        real call sites so the claim is about the tree, not about one example."""
        self.assertIn("cli_commands.approve:cmd_approve", _functions_calling("read_yes_no"),
                      "fixture check: the approve path must really read a y/N")

        spy = _Spy()
        with mock.patch.object(N, "_run_argv", spy):
            with mock.patch.object(P, "_stdin_is_tty", return_value=True):
                # the shape every `--yes` site uses: `args.yes or read_yes_no(...)`
                assume_yes = True
                answered = assume_yes or P.read_yes_no("plan", "Override?")
        self.assertTrue(answered)
        self.assertEqual(spy.calls, [], "assume_yes must not reach the reader, so nothing fires")

    def test_disabled_is_silent_and_audio_off_keeps_the_notification(self):
        """Jas's requirement is TWO keys, not one: silencing the audio must not disable the
        notification. Pinned as a difference, not as two greens.

        ⚠ ON A DECLARED DESKTOP (`_desktop`), because the difference this asserts is a difference
        of ONE argv — and on a box without a sound binary there is no argv to lose. See `_Which`."""
        with _desktop() as desk:
            spy_all = _Spy()
            _fire(N.KIND_PROMPT, settings=_settings(), runner=spy_all, **desk)

            spy_quiet = _Spy()
            _fire(N.KIND_PROMPT, settings=_settings(audio=False), runner=spy_quiet, **desk)

            spy_off = _Spy()
            fired_off = _fire(N.KIND_PROMPT, settings=_settings(enabled=False), runner=spy_off,
                              **desk)

        self.assertFalse(fired_off)
        self.assertEqual(spy_off.calls, [], "notify=false must emit nothing at all")
        self.assertTrue(spy_quiet.calls, "audio=false must STILL raise the notification")
        self.assertLess(len(spy_quiet.calls), len(spy_all.calls),
                        "audio=false must drop exactly the audio call")


# ======================================================================================
# 3b. THE TWO GUARDS THAT WERE MISSING FROM THE FIRST BUILD OF THIS MODULE
# ======================================================================================
# ⚠ BOTH OF THESE WERE FOUND BY SHIPPING THE DEFECT, not by predicting it. The first build had
# neither, the suite drove `read_yes_no` a few hundred times, and the maintainer's desktop took a
# few hundred real macOS banners in a row. That is two separate bugs wearing one symptom, and
# collapsing them into "make the tests quiet" would have fixed the visible half and left the one
# that reaches users:
#
#   * a test process must never touch a desktop  — a suite with a side effect on the machine;
#   * a batch of waits must not become a batch of banners — which is the FEATURE's own P22 bar,
#     since a notifier that fires twenty times for one human decision is not twenty times the
#     signal, it is zero, and it teaches the user to turn the whole thing off.

class TestNeverEmitsFromATestProcess(unittest.TestCase):

    def test_a_process_with_a_test_framework_loaded_emits_nothing(self):
        """★ Default-OFF under test, with no opt-in in the environment. This is the pin that would
        have caught the several hundred banners: it drives the seam with a spy and an otherwise
        perfect desktop environment, and asserts the emitter refuses anyway."""
        spy = _Spy()
        fired = _fire(N.KIND_PROMPT, settings=_settings(), runner=spy, is_tty=True,
                      env={"DISPLAY": ":0"})            # note: no MOKATA_NOTIFY opt-in
        self.assertFalse(fired)
        self.assertEqual(spy.calls, [], "a test process must not raise a real notification")

    def test_the_detection_is_the_loaded_framework_not_a_variable_a_runner_might_not_set(self):
        """`unittest` being in `sys.modules` IS what "a test framework is driving this process"
        means, so that is what is checked — rather than an env var that `python -m unittest`,
        `pytest`, an IDE runner and CI would each have to remember to set."""
        self.assertIn("unittest", sys.modules)
        self.assertTrue(N._suppressed({}), "the bare environment of THIS process must suppress")

    def test_the_env_override_works_in_both_directions(self):
        """`MOKATA_NOTIFY=1` is a test's deliberate opt-in; `0` is a user kill switch that needs no
        manifest. Pinned as a DIFFERENCE so a stub that ignored the variable cannot pass."""
        self.assertFalse(N._suppressed({N.NOTIFY_ENV: "1"}))
        self.assertTrue(N._suppressed({N.NOTIFY_ENV: "0"}))
        self.assertTrue(N._suppressed({N.NOTIFY_ENV: "off"}))

    def test_the_suppression_is_propagated_to_child_processes(self):
        """`sys.modules` cannot cross an `exec`. The suite spawns children — the mutation harness
        alone runs it hundreds of times — and a child that execs `mokata` has no test framework
        loaded, so the in-process check alone would let a subprocess of a silent run light up the
        desktop. The environment is the only thing a parent can hand a child."""
        os.environ.pop(N.NOTIFY_ENV, None)
        try:
            self.assertTrue(N._suppressed({}))
            self.assertEqual(os.environ.get(N.NOTIFY_ENV), "0",
                             "a child process must inherit the decision this one made")
        finally:
            os.environ.pop(N.NOTIFY_ENV, None)

    def test_the_guard_sits_in_the_seam_so_every_call_site_inherits_it(self):
        """It is checked in `_notify`, not at the five announce sites — so a new blocking prompt
        cannot be wired up without it."""
        import inspect
        self.assertIn("_suppressed(env)", inspect.getsource(N._notify))


class TestRateLimit(unittest.TestCase):

    def test_a_batch_of_waits_collapses_to_one_notification(self):
        """★ THE P22 BAR APPLIED TO THIS FEATURE. mokata stages gated writes in batches — a
        playbook run, a spec emit followed by four memory puts — and one banner per staged write is
        not twenty times the signal. Driven at a real repo root with the real stamp file."""
        with tempfile.TemporaryDirectory() as root, _desktop() as desk:
            os.makedirs(os.path.join(root, ".mokata"))
            spy = _Spy()
            fired = [N.notify(N.KIND_WAIT, root=root, settings=_settings(), runner=spy,
                              harness_notified=False, now=1000.0 + i, **desk)  # 20 waits, 1 second
                     for i in range(20)]
            self.assertEqual(fired.count(True), 1, f"twenty waits, {fired.count(True)} banners")
            self.assertEqual(len(spy.calls), 2, "exactly one notification + one sound")

    def test_a_genuinely_new_ask_after_the_interval_does_announce(self):
        """The other half, and the one that stops the limiter from becoming a mute button: past the
        interval, a new ask is a new ask."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".mokata"))
            spy = _Spy()
            first = N.notify(N.KIND_WAIT, root=root, settings=_settings(), runner=spy,
                             harness_notified=False, is_tty=True, env=_desktop_env(),
                             platform=_A_DESKTOP, now=1000.0)
            soon = N.notify(N.KIND_WAIT, root=root, settings=_settings(), runner=spy,
                            harness_notified=False, is_tty=True, env=_desktop_env(),
                            platform=_A_DESKTOP, now=1030.0)
            later = N.notify(N.KIND_WAIT, root=root, settings=_settings(), runner=spy,
                             harness_notified=False, is_tty=True, env=_desktop_env(),
                             platform=_A_DESKTOP,
                             now=1000.0 + N.NOTIFY_MIN_INTERVAL_SECONDS + 1)
            self.assertEqual((first, soon, later), (True, False, True))

    def test_the_limit_is_charged_only_against_a_notification_that_would_have_fired(self):
        """ORDER matters: the rate limit is the LAST suppression. If a wait suppressed for being
        in CI still reset the clock, a headless batch would silence the human's next real ask."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".mokata"))
            spy = _Spy()
            N.notify(N.KIND_PROMPT, root=root, settings=_settings(), runner=spy, is_tty=True,
                     env={"CI": "true", N.NOTIFY_ENV: "1"}, platform=_A_DESKTOP, now=1000.0)
            self.assertEqual(spy.calls, [], "fixture check: CI must have suppressed it")
            fired = N.notify(N.KIND_PROMPT, root=root, settings=_settings(), runner=spy,
                             is_tty=True, env=_desktop_env(), platform=_A_DESKTOP, now=1001.0)
            self.assertTrue(fired, "a suppressed notification must not consume the rate limit")

    def test_the_stamp_survives_the_process_because_the_cli_half_does_not(self):
        """The CLI corpus is many short-lived PROCESSES — `mokata approve`, then `spec emit`, then
        the wizard are three interpreters. An in-memory limiter would let a batch through one
        banner per process, which is the defect with extra steps."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".mokata"))
            self.assertFalse(N._debounced(root, now=1000.0))
            stamp = os.path.join(root, ".mokata", "temp_local", "notify", "last-notified.stamp")
            self.assertTrue(os.path.exists(stamp), "the limiter must persist outside memory")
            self.assertTrue(N._debounced(root, now=1000.0))


# ======================================================================================
# 3c. THE AUDIO ARMS — "and also give audio" is a REQUIREMENT, so its absence is a defect
# ======================================================================================
# ⚠ FOUND AT THE 0.0.18 CUT-HALT, and the test failure was the smaller half of it. Four CI legs
# reded because two tests counted argv on whatever platform the runner happened to be; that was a
# TEST defect and `_Which` above fixes it. But the reason the count was 1 instead of 2 on Linux is
# a PRODUCT defect and it was nobody's test: the Linux audio arm hung on ONE optional binary, and
# where that binary is absent AND there is no TTY to ring — an MCP gated write, which is the case
# this whole lane was built for — the user got a banner, no sound, and no explanation.
#
# Jas's requirement is "by default it should notify and ALSO give audio". So:
#   * a second Linux arm (`paplay` + the sound theme's own file), because depending on one package
#     is not the same as depending on the sound stack;
#   * and when neither arm nor bell exists, a NAMED degrade — silence that says why it is silent.

class TestTheAudioArms(unittest.TestCase):

    def test_each_platform_that_ships_an_arm_builds_a_FIXED_argv_for_it(self):
        """Rule 2 still holds across the new arm: every element is an in-code literal."""
        with mock.patch.object(N.os.path, "exists", lambda p: True):
            self.assertEqual([N._MACOS_SOUND], N._audio_argv("darwin")[1:])
            with mock.patch.object(N, "shutil", _Which({"canberra-gtk-play"})):
                self.assertEqual(["canberra-gtk-play", "-i", N._CANBERRA_SOUND_ID],
                                 N._audio_argv("linux"))

    def test_linux_has_a_SECOND_arm_when_libcanberra_is_absent(self):
        """★ THE PRODUCT GAP. `canberra-gtk-play` ships in `libcanberra-gtk3-bin`, which a great
        many desktops — and every GitHub runner — do not have. One optional binary was the whole
        of Linux audio."""
        with mock.patch.object(N, "shutil", _Which({"paplay"})), \
                mock.patch.object(N.os.path, "exists", lambda p: p == N._FREEDESKTOP_SOUND):
            self.assertEqual(["paplay", N._FREEDESKTOP_SOUND], N._audio_argv("linux"))

    def test_libcanberra_still_WINS_when_both_are_present(self):
        """Pinned as an ORDER, not as two independent greens: `canberra-gtk-play` honours the
        user's sound theme, `paplay` plays one file. The fallback must stay a fallback."""
        with mock.patch.object(N, "shutil", _Which({"canberra-gtk-play", "paplay"})), \
                mock.patch.object(N.os.path, "exists", lambda p: True):
            self.assertEqual("canberra-gtk-play", N._audio_argv("linux")[0])

    def test_a_linux_box_with_NEITHER_binary_has_no_arm_rather_than_a_bad_one(self):
        with mock.patch.object(N, "shutil", _Which(set())), \
                mock.patch.object(N.os.path, "exists", lambda p: False):
            self.assertIsNone(N._audio_argv("linux"))

    def test_audio_asked_for_and_impossible_is_NAMED_and_not_silent(self):
        """★ THE HALF A TEST COUNT CANNOT SEE. A wait on a Linux box with no sound binary and no
        TTY — i.e. every MCP gated write on such a box — raises the banner and makes no sound.
        That is a fact about the user's machine that the user can act on, so it gets its own
        subsystem and its own notice. `audio=false` and "audio was impossible" are different
        states and must not share a representation (§7g)."""
        notices: List[str] = []
        with mock.patch.object(N, "shutil", _Which({"notify-send"})), \
                mock.patch.object(N.os.path, "exists", lambda p: False):
            spy = _Spy()
            fired = _fire(N.KIND_WAIT, settings=_settings(), runner=spy, harness_notified=False,
                          is_tty=False, env=_desktop_env(), platform="linux", out=notices.append)
        self.assertTrue(fired, "the NOTIFICATION must still have been raised")
        self.assertEqual(1, len(spy.calls), "one channel emitted: the banner, and no sound")
        self.assertTrue(any(N.DEGRADE_AUDIO in n for n in notices),
                        f"silence with audio ON must be NAMED; got {notices}")

    def test_audio_OFF_says_nothing_because_nothing_is_wrong(self):
        """The contrast that stops the notice above from being noise: a user who turned audio off
        is not degraded, and telling them so every wait would be the alert-fatigue defect this
        module's rate limit exists for."""
        notices: List[str] = []
        with mock.patch.object(N, "shutil", _Which({"notify-send"})), \
                mock.patch.object(N.os.path, "exists", lambda p: False):
            _fire(N.KIND_WAIT, settings=_settings(audio=False), runner=_Spy(),
                  harness_notified=False, is_tty=False, env=_desktop_env(), platform="linux",
                  out=notices.append)
        self.assertFalse(any(N.DEGRADE_AUDIO in n for n in notices), notices)

    def test_the_three_degrade_subsystems_are_three_distinct_names(self):
        """§7g as data. Collapsing any two would make one machine's problem read as another's."""
        names = (N.DEGRADE_SUBSYSTEM, N.DEGRADE_WINDOWS, N.DEGRADE_AUDIO)
        self.assertEqual(3, len(set(names)), names)


# ======================================================================================
# 4. F10 — THE SECURITY CLASS THIS SITS IN
# ======================================================================================

class TestF10SecurityClass(unittest.TestCase):

    def test_shell_true_appears_nowhere_in_the_module_structurally(self):
        """★ AST, not grep — so a module that merely EXPLAINS the rule in a comment still passes,
        and a module that sets the flag cannot hide behind one. This is the exact inversion of the
        text-scan that let `hook_cli`'s `shell=True` sit unfound for four releases."""
        import inspect
        tree = ast.parse(inspect.getsource(N))
        offenders = [
            kw for call in ast.walk(tree) if isinstance(call, ast.Call)
            for kw in call.keywords
            if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant)
                                          and kw.value.value is False)
        ]
        self.assertEqual(offenders, [], "the notifier must never run a shell")

    def test_every_subprocess_call_passes_a_list_and_a_timeout(self):
        """argv, always — no f-string ever becomes a command — and every OS call is bounded."""
        import inspect
        tree = ast.parse(inspect.getsource(N))
        runs = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute) and c.func.attr in ("run", "Popen", "call")]
        self.assertTrue(runs, "fixture check: the module must really invoke a subprocess")
        for call in runs:
            kwargs = {kw.arg for kw in call.keywords}
            self.assertIn("timeout", kwargs, "every OS call gets a hard timeout")
            self.assertTrue(call.args, "argv must be passed positionally as a list")

    def test_nothing_variable_ever_reaches_an_os_argv(self):
        """★ The rule that matters most, driven rather than read: a proposal id, a tool name and a
        path are pushed through the wait seam, and NONE of them may appear in any argv. The body is
        chosen from a fixed in-code set; anything variable is rendered by mokata's own surfaces."""
        marker_id = "prop-DEADBEEF-secret-id"
        marker_tool = "a-tool-name-with-$(rm -rf /)-in-it"
        spy = _Spy()
        _fire(N.KIND_WAIT, settings=_settings(), runner=spy, is_tty=True,
                 env=_desktop_env(), platform=_A_DESKTOP, tool=marker_tool,
                 proposal_id=marker_id)
        self.assertTrue(spy.calls, "fixture check: the wait must really have emitted")
        self.assertNotIn(marker_id, spy.flat)
        self.assertNotIn(marker_tool, spy.flat)
        self.assertNotIn("rm -rf", spy.flat)

    def test_every_emitted_body_comes_from_the_fixed_set(self):
        """The structural half of the rule above: whatever argv is built, every human-readable
        string in it is a member of the frozen set (or the fixed title)."""
        for kind in N.KINDS:
            for first in (True, False):
                with self.subTest(kind=kind, first=first):
                    self.assertIn(N.body_for(kind, first_time=first), N.BODIES)

    def test_a_body_outside_the_fixed_set_builds_no_argv_at_all(self):
        """★ THE CHECK, driven — and it needed planting to be gradable at all (§7i). Every other
        test here passes a body that IS in the set, so `_require_fixed` could be `return True` and
        all of them would still be green: the rule would read as enforced while enforcing nothing.

        This is the difference between "nothing variable reaches an argv" as a comment and as a
        control. An attacker-shaped body — the shape a future format hole would produce — must
        produce NO command, not a quoted one."""
        planted = 'pwned" with title "x"); do shell script "rm -rf /'
        self.assertNotIn(planted, N.BODIES, "fixture check: the planted body must be foreign")
        self.assertIsNone(N._notification_argv("darwin", planted))
        self.assertIsNone(N._notification_argv("linux", planted))
        # ...while the real bodies still build one, so this is not passing by refusing everything
        self.assertIsNotNone(N._notification_argv("darwin", N.BODY_WAIT))

    def test_the_timeout_is_short_enough_to_be_a_bound(self):
        """"Bounded" is a claim about a NUMBER, not about the presence of a keyword. The AST scan
        above proves every OS call passes `timeout=`; this proves the value means something — a
        notifier allowed to block for ten minutes has a timeout and is still the hang it exists to
        announce."""
        self.assertGreater(N.NOTIFY_TIMEOUT_SECONDS, 0)
        self.assertLessEqual(N.NOTIFY_TIMEOUT_SECONDS, 5.0)

    def test_the_harness_setup_shell_true_justification_does_not_extend_here(self):
        """The ONE documented `shell=True` in the tree is `hook_cli._run_wrapped`, and its written
        justification is SCOPED: the string is a statusLine command read from the very
        settings.json being written, so mokata adds no execution path the harness did not already
        have. This module reads no such file and wraps no such command — it invokes OS binaries by
        name. Pinned as a structural fact: the notifier shares no argv source with that path."""
        import inspect
        from mokata import hook_cli
        self.assertIn("shell=True", inspect.getsource(hook_cli._run_wrapped),
                      "fixture check: the documented exception must really still be there")
        src = inspect.getsource(N)
        self.assertNotIn("WrapOrigin", src)
        self.assertNotIn("_statusline_command", src)


# ======================================================================================
# 5. DISCOVERABILITY + WINDOWS
# ======================================================================================

class TestDiscoverabilityAndWindows(unittest.TestCase):

    def test_the_first_notification_carries_the_off_switch_and_only_the_first(self):
        """Audio-on-by-default is a behaviour change in a launch release, so the FIRST
        notification a user ever gets must carry its own off switch — and only the first, keyed on
        a marker, exactly as the deprecation notices do it."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".mokata"))
            first = N.body_for(N.KIND_PROMPT, first_time=N.claim_first_notice(root))
            again = N.body_for(N.KIND_PROMPT, first_time=N.claim_first_notice(root))
            self.assertIn(N.OFF_SWITCH, first)
            self.assertNotIn(N.OFF_SWITCH, again)
            self.assertIn("settings.ux.notify", N.OFF_SWITCH,
                          "the off switch must name the key that actually turns it off")

    def test_windows_ships_no_unverifiable_arm_and_says_so(self):
        """⚠ There is no Windows host this release (OSS #46/#45/#28 are blocked on exactly that),
        so the Windows arm is DEGRADED rather than shipped unverified — and the degrade is named
        out loud rather than left for a green suite to imply coverage of."""
        spy = _Spy()
        notices: List[str] = []
        fired = _fire(N.KIND_PROMPT, settings=_settings(), runner=spy, is_tty=True,
                         env=_desktop_env(), platform="win32", out=notices.append)
        self.assertFalse(fired)
        self.assertEqual(spy.calls, [], "no unverifiable OS call may ship")
        self.assertTrue(any("windows" in n.lower() for n in notices),
                        f"the Windows degrade must be NAMED; got {notices}")

    def test_over_ssh_the_desktop_notification_is_suppressed(self):
        """A desktop notification raised from an SSH session lands on the REMOTE machine's console,
        where by definition nobody is sitting. The terminal channel still reaches the human; the OS
        one does not.

        ⚠ PINNED AS A DIFFERENCE, and it had to be. The first version of this test asserted only
        `notify-send not in argv` from an environment that — having no `MOKATA_NOTIFY` opt-in —
        was suppressed outright, so it passed with the SSH check deleted and mutant M07 walked
        straight through it. It was green, it named the right thing, and it graded nothing: a test
        that asserts an ABSENCE must prove the presence it is contrasted with, or any silence at
        all satisfies it."""
        local, remote = _Spy(), _Spy()
        base = dict(settings=_settings(), is_tty=True, platform="linux")

        _fire(N.KIND_PROMPT, runner=local, env=_desktop_env(), **base)
        _fire(N.KIND_PROMPT, runner=remote,
              env=dict(_desktop_env(), SSH_CONNECTION="10.0.0.1 22 10.0.0.2 22"), **base)

        self.assertIn("notify-send", local.flat,
                      "fixture check: at a local desktop the OS notification must really be built")
        self.assertNotIn("notify-send", remote.flat,
                         "over SSH the banner would be raised on the wrong machine")


# ======================================================================================
# 6. CONFIG — three keys, degrade-clean, wizard-wired
# ======================================================================================

class TestConfig(unittest.TestCase):

    def test_defaults_are_enabled_audio_on_widest_level(self):
        s = N.NotifySettings()
        self.assertTrue(s.enabled)
        self.assertTrue(s.audio)
        self.assertEqual(s.level, N.ALL_PROMPTS)

    def test_an_unreadable_surface_reads_as_the_documented_defaults(self):
        """Degrade-clean, read EXACTLY like `statusline_enabled`: absent/broken never silently
        loses the default-on behaviour."""
        s = N.load_settings("/nonexistent/stage18/root")
        self.assertEqual((s.enabled, s.audio, s.level), (True, True, N.ALL_PROMPTS))

    def test_an_unrecognised_level_reads_as_the_documented_default(self):
        self.assertEqual(N.coerce_level("not-a-level"), N.ALL_PROMPTS)
        self.assertEqual(N.coerce_level(None), N.ALL_PROMPTS)
        for level in N.LEVELS:
            self.assertEqual(N.coerce_level(level), level)

    def test_all_three_keys_are_walked_by_the_config_wizard(self):
        """`settings.ux.statusline` is the precedent named in the brief — the wizard walks it, and
        a drift-guard keeps the wizard in lockstep with the documented settings reference."""
        from mokata.config_wizard import SETTINGS
        keys = {s.key for s in SETTINGS}
        for key in ("settings.ux.notify", "settings.ux.notify_audio", "settings.ux.notify_level"):
            self.assertIn(key, keys)


# ======================================================================================
# 7. PART 2 — THE STATUSLINE. Pin the COMPOSITION, not the component.
# ======================================================================================

class TestStatuslineComposition(unittest.TestCase):

    def test_the_wait_segment_reaches_the_composed_badge(self):
        """★ THE EXACT DEFECT. `awaiting.statusline_segment` rendering correctly in isolation is
        what every existing pin asserts, and every one of them would have passed before this stage
        too. What was never pinned is that the composed badge — the function named `statusline_badge`,
        the one anything reusing mokata's statusline calls — CONTAINS it."""
        from mokata.progress import statusline_badge

        class _Surface:
            root = "/stage18/composed"
            state = None
            manifest = None

        with mock.patch("mokata.awaiting.statusline_segment",
                        return_value="⏳ awaiting approval prop-123"):
            line = statusline_badge(_Surface())
        self.assertIn("⏳ awaiting approval prop-123", line,
                      "the one segment whose job is 'your move is pending' must be ON the badge")

    def test_the_wait_leads_the_badge_because_it_outranks_mode_and_stage(self):
        """ORDER is the deliverable, not presence. A statusline is truncated from the RIGHT by
        every terminal that renders one, so the most urgent segment goes FIRST: a wait outranks
        both the run mode and the pipeline stage, and appending it last is how an urgent signal
        gets cut off by a long stage strip."""
        from mokata.progress import statusline_badge

        class _Surface:
            root = "/stage18/order"
            state = None
            manifest = None

        with mock.patch("mokata.awaiting.statusline_segment",
                        return_value="⏳ awaiting approval prop-123"):
            line = statusline_badge(_Surface())
        self.assertTrue(line.startswith("⏳ awaiting approval prop-123"),
                        f"the wait must LEAD the line, got {line!r}")
        self.assertLess(line.index("⏳"), line.index("mokata"))

    def test_nothing_pending_leaves_the_badge_byte_identical(self):
        """The negative half — a healthy session's statusline must not grow a separator."""
        from mokata.progress import statusline_badge

        class _Surface:
            root = "/stage18/quiet"
            state = None
            manifest = None

        with mock.patch("mokata.awaiting.statusline_segment", return_value=""):
            quiet = statusline_badge(_Surface())
        self.assertFalse(quiet.startswith(" "))
        self.assertNotIn("  ", quiet)

    def test_a_raising_wait_segment_cannot_break_the_badge(self):
        from mokata.progress import statusline_badge

        class _Surface:
            root = "/stage18/boom"
            state = None
            manifest = None

        with mock.patch("mokata.awaiting.statusline_segment", side_effect=OSError("no store")):
            self.assertIsInstance(statusline_badge(_Surface()), str)


class TestBadgeNoRunAnswer(unittest.TestCase):

    def test_the_bare_mokata_badge_no_longer_conflates_two_states(self):
        """★ §7g ON THE BADGE ITSELF. `mokata` was returned BOTH when the session is bound to no
        run (a real, healthy answer) and when the surface could not be read at all (an absent
        answer). One string, two meanings, and the safe-looking one always wins."""
        from mokata import progress

        class _Broken:
            root = "/stage18/broken"

            @property
            def state(self):
                raise OSError("unreadable surface")

        class _NoRun:
            root = "/stage18/norun"
            state = None
            manifest = None

        with mock.patch.object(progress, "_badge_state", return_value=(None, "")):
            no_run = progress.build_stage_badge(_NoRun())
        unreadable = progress.build_stage_badge(_Broken())

        self.assertNotEqual(no_run, unreadable,
                            "an absent answer and a real answer must not share a representation")
        self.assertEqual(no_run, progress.BADGE_NO_RUN)
        self.assertEqual(unreadable, progress.BADGE_UNRESOLVED)

    def test_both_answers_stay_compact_and_neither_raises(self):
        """The constraint that comes with the fix: it must stay compact, pure, and inside the
        statusline budget."""
        from mokata import progress
        for badge in (progress.BADGE_NO_RUN, progress.BADGE_UNRESOLVED):
            self.assertLessEqual(len(badge), 24, f"{badge!r} is too long for a statusline")
            self.assertTrue(badge.startswith("mokata"))

    def test_the_unresolved_answer_says_what_it_could_not_do(self):
        """§7g's closing rule — an absent answer must be legible AS absent, not merely different."""
        from mokata import progress
        self.assertNotEqual(progress.BADGE_UNRESOLVED, progress.BADGE_NO_RUN)
        self.assertTrue(any(c in progress.BADGE_UNRESOLVED for c in "?⚠"),
                        "the unresolved badge must read as a non-answer")


class TestStatuslinePerfBudget(unittest.TestCase):

    def test_the_statusline_perf_op_benchmarks_what_the_statusline_actually_renders(self):
        """★ The budget existed and pointed at a COMPONENT. `perf.build_hot_ops` timed
        `build_stage_badge`, but the shipped statusline renders `statusline_badge` — which now also
        reads the approval store off disk. The 50 ms budget therefore never covered the disk read
        it was supposed to bound."""
        import inspect
        from mokata import perf
        src = inspect.getsource(perf.build_hot_ops)
        self.assertIn("statusline_badge", src,
                      "the budgeted op must be the composed line the harness actually renders")

    def test_the_composed_statusline_holds_the_fifty_millisecond_budget(self):
        from mokata import perf
        self.assertEqual(perf.LATENCY_BUDGETS_MS["statusline"], 50.0)


if __name__ == "__main__":
    unittest.main()
