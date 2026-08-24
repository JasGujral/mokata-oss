"""F13 — the Windows AUDIO arm. One channel of two, and the notice that says so.

0.0.19 stage 09a. `docs/reference/manifest.md` documents `ux.notify` and `ux.notify_audio` as
defaulting **true** with no Windows caveat, and a Windows user reading that table got neither a
notification nor a sound. This row builds the half of that promise Windows can keep without a
dependency — `winsound`, stdlib, lazily imported — and leaves the visual half absent and NAMED.

WHAT EACH DELIVERABLE IS GRADED BY, named so a reader can check the mapping rather than trust it:

    1  the arm plays the system sound       TestTheWindowsSoundArm
       ...and the WAIT reaches it at all    TestTheWaitReachesTheArm     ← the real first seam
    2  the shipped notice became TRUE       TestTheShippedNoticeIsTrue
    3  three states stay three              TestThreeStatesStayThree
    4  `ux.notify_audio: false` silences    TestTheAudioSettingStillMeansWhatItSaid
       macOS and Linux are byte-identical   TestMacOSAndLinuxAreUnchanged  ← LOAD-BEARING NEGATIVE
       no new dependency                    TestNoNewDependency
       the notice is secret-free            TestSecretSafety

🔴 WHAT THIS FILE CANNOT GRADE, STATED HERE RATHER THAN DISCOVERED LATER. It grades that
`winsound.MessageBeep` is REACHED and CALLED WITH THE RIGHT FLAG. It does not grade that a human
hears anything, and no headless runner can: `MessageBeep` reports nothing about a muted device, an
absent endpoint or a session with no mixer. The audibility of this arm carries a MANUAL dimension
recorded in the stage report and on the cut checklist, and until a human has run it on a real
Windows machine the arm is NOT verified — only called.

⚠ THE FAKE `winsound` IS THE POINT, not a convenience. `winsound` is a C extension built into
CPython on Windows and absent from every other build, so a test that imported the real one would
grade the maintainer's OS rather than the arm. Every leg below declares the module — present,
absent, or exploding — exactly as `_Which` declares the POSIX binaries.

Pure/offline. Imports via `_support`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import os
import re
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Set
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade as D
from mokata import notify as N


# ======================================================================================
# instruments — every axis DECLARED, nothing inherited from the host
# ======================================================================================

class _Spy:
    """A runner that records argv instead of executing it. Stands in for a HEALTHY OS call."""

    def __init__(self, returns: bool = True):
        self.calls: List[List[str]] = []
        self._returns = returns

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        return self._returns


class _FakeWinsound:
    """`winsound` as far as `_windows_sound` is concerned.

    ⚠ THE FLAG CONSTANTS CARRY DELIBERATELY NON-REAL VALUES. If this fake spelled
    `MB_ICONASTERISK = 0x40`, a test asserting `calls == [0x40]` would pass against an arm that
    hard-coded the integer and never read the module's own constant — which is the same defect as
    an argv assembled from a literal instead of `_CANBERRA_SOUND_ID`. Sentinel values make
    "it passed the module's flag" and "it passed a number that happens to match" different
    outcomes rather than the same green."""

    MB_ICONASTERISK = "SENTINEL-ICONASTERISK"
    MB_OK = "SENTINEL-MB-OK"

    def __init__(self, explode: Optional[BaseException] = None):
        self.calls: List[object] = []
        self._explode = explode

    def MessageBeep(self, flag):        # noqa: N802 — this is the stdlib's own spelling
        self.calls.append(flag)
        if self._explode is not None:
            raise self._explode


@contextlib.contextmanager
def _winsound(module):
    """Declare what `import winsound` finds: a fake module, or ABSENT (`None`).

    `None` in `sys.modules` is how Python spells "this import is blocked" — `import winsound`
    raises ImportError against it, which is exactly the state a CPython build without the
    extension presents."""
    with mock.patch.dict(sys.modules, {"winsound": module}):
        yield module


class _Which:
    """`shutil` as far as `_audio_argv` is concerned: a DECLARED set of binaries this box has."""

    def __init__(self, present):
        self._present = frozenset(present)

    def which(self, name):
        return "/usr/bin/%s" % name if name in self._present else None


@contextlib.contextmanager
def _sounds(present: bool):
    """Whether the two fixed sound FILES exist, without lying about any other path."""
    real = os.path.exists

    def fake(path):
        if path in (N._MACOS_SOUND, N._FREEDESKTOP_SOUND):
            return present
        return real(path)

    with mock.patch.object(N.os.path, "exists", fake):
        yield


def _desktop_env() -> Dict[str, str]:
    """An environment a human is demonstrably sitting at: not CI, a display, not over SSH.

    `MOKATA_NOTIFY=1` is the DELIBERATE opt-in every emit-path test must give — this module
    refuses to touch a desktop from a process with a test framework loaded."""
    return {"DISPLAY": ":0", N.NOTIFY_ENV: "1"}


def _settings(**kw) -> "N.NotifySettings":
    base = dict(enabled=True, audio=True, level=N.ALL_PROMPTS)
    base.update(kw)
    return N.NotifySettings(**base)


class _Base(unittest.TestCase):
    """A fresh degrade memory and a fresh repo root per test.

    ⚠ BOTH ARE REQUIRED, and the first one is not hygiene. `_degrade` is once-per-subsystem PER
    PROCESS, so without the reset the second test to touch a subsystem sees no notice and its
    "the notice was named" assertion passes on the FIRST test's emission — a green that means
    "already said" wearing the costume of a green that means "said here"."""

    def setUp(self):
        D.reset_degrade_notices()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, ".mokata"))
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(D.reset_degrade_notices)

    def fire(self, kind=N.KIND_WAIT, **kw):
        """Drive the seam with the RATE LIMIT OFF — the limiter is a separate fact, graded
        elsewhere, and leaving it on here would let a second assertion pass because the
        notification was DEBOUNCED rather than because the logic under test was right."""
        kw.setdefault("root", self.root)
        kw.setdefault("settings", _settings())
        kw.setdefault("env", _desktop_env())
        kw.setdefault("debounce", False)
        kw.setdefault("out", self.notices.append)
        return N.notify(kind, **kw)

    @property
    def notices(self) -> List[str]:
        if not hasattr(self, "_notices"):
            self._notices: List[str] = []
        return self._notices

    def subsystems(self) -> Set[str]:
        """The EXACT set of D5 subsystems this process has degraded. Read from the register
        rather than by substring-matching the rendered text, because `DEGRADE_SUBSYSTEM` is
        `"notify"` and is a substring of both of the others — a `in` check would report all three
        every time one fired, which is §7g collapsed inside the assertion instead of the code."""
        return {n.subsystem for n in D.emitted_notices()}


# ======================================================================================
# 1. THE ARM ITSELF
# ======================================================================================

class TestTheWindowsSoundArm(_Base):

    def test_the_windows_wait_plays_the_system_notification_sound(self):
        """★ DELIVERABLE 1. The MCP gated write on Windows — no TTY, no visual arm — now makes a
        noise, and it makes it IN PROCESS: no subprocess is spawned, so there is no binary to be
        absent and nothing to hang."""
        spy, fake = _Spy(), _FakeWinsound()
        with _winsound(fake):
            fired = self.fire(N.KIND_WAIT, runner=spy, is_tty=False, platform="win32")
        self.assertTrue(fired, "the sound IS a channel; a channel that emitted means fired")
        self.assertEqual([fake.MB_ICONASTERISK], fake.calls,
                         "the arm must call MessageBeep exactly once, with the module's own flag")
        self.assertEqual([], spy.calls,
                         "winsound is stdlib and in-process — no argv, no subprocess, no timeout")

    def test_the_windows_prompt_plays_it_too(self):
        """The other kind. A CLI y/N on Windows is a human at a terminal, and the manifest
        promises them the same sound it promises everyone else."""
        fake = _FakeWinsound()
        with _winsound(fake):
            fired = self.fire(N.KIND_PROMPT, runner=_Spy(), is_tty=True, platform="win32")
        self.assertTrue(fired)
        self.assertEqual([fake.MB_ICONASTERISK], fake.calls)

    def test_the_flag_is_the_notification_sound_and_not_a_bare_beep(self):
        """★ "with the right arguments" — the automated half of the bar, stated exactly.

        `MB_OK` is the default-button ding; `MB_ICONASTERISK` is the user's own configured
        "something wants you" sound, which is what `_CANBERRA_SOUND_ID` and `_MACOS_SOUND` are on
        the other two platforms. An arm that beeps the wrong one is a different feature, and
        without this assertion `calls == 1` would accept it."""
        fake = _FakeWinsound()
        with _winsound(fake):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        self.assertEqual([fake.MB_ICONASTERISK], fake.calls)
        self.assertNotIn(fake.MB_OK, fake.calls)

    def test_a_winsound_that_raises_can_never_become_the_wait_it_announces(self):
        """The bar above all others: a notification may not fail the thing it is announcing.
        A `MessageBeep` that raises must land as "no sound was made", not as an exception."""
        for boom in (OSError("no device"), RuntimeError("mixer"), ValueError("flag")):
            with self.subTest(boom=type(boom).__name__):
                D.reset_degrade_notices()
                fake = _FakeWinsound(explode=boom)
                with _winsound(fake):
                    fired = self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
                self.assertEqual(1, len(fake.calls), "it must have been TRIED")
                self.assertFalse(fired, "nothing emitted: no visual arm, and the sound refused")
                self.assertIn(N.DEGRADE_AUDIO, self.subsystems(),
                              "a refused sound with audio ON is a fact about this box, and named")


# ======================================================================================
# 2. THE SEAM THE BRIEF'S GROUND LIST DID NOT NAME
# ======================================================================================

class TestTheWaitReachesTheArm(_Base):
    """⚠ `notify.py:506` was NOT the first thing Windows hit. `_human_present` runs before it and
    answered "no human on Windows" for `KIND_WAIT` — so the arm below it would have been alive for
    CLI prompts and DEAD for the MCP gated write, which is the case the whole module exists for.
    The bypass and the presence test agreed while Windows shipped nothing; only one of them was
    the reason."""

    def test_a_windows_wait_has_a_human_present(self):
        self.assertTrue(N._human_present(N.KIND_WAIT, is_tty=False, env=_desktop_env(),
                                         platform="win32"))

    def test_the_other_platforms_answer_exactly_as_they_did(self):
        """The contrast that stops the fix above from being "return True for everything"."""
        env = _desktop_env()
        self.assertTrue(N._human_present(N.KIND_WAIT, is_tty=False, env=env, platform="darwin"))
        self.assertTrue(N._human_present(N.KIND_WAIT, is_tty=False, env=env, platform="linux"))
        self.assertFalse(N._human_present(N.KIND_WAIT, is_tty=False, env={N.NOTIFY_ENV: "1"},
                                          platform="linux"),
                         "linux still consults DISPLAY/WAYLAND_DISPLAY")
        self.assertFalse(N._human_present(N.KIND_WAIT, is_tty=False, env=env, platform="freebsd12"),
                         "an unknown platform still ships no arm and must still answer no")

    def test_ci_still_silences_windows_like_everywhere_else(self):
        """A beep on a headless runner is noise at best. The CI guard sits ABOVE the platform
        arms and must keep doing so, or this row makes every Windows CI job beep."""
        fake = _FakeWinsound()
        with _winsound(fake):
            fired = self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32",
                              env=dict(_desktop_env(), CI="1"))
        self.assertFalse(fired)
        self.assertEqual([], fake.calls)

    def test_f13_regression_the_mcp_wait_is_not_silent_on_windows(self):
        """★ THE REGRESSION, end to end and on the exact shape the row was filed for: a gated
        write, no TTY, Windows. On the old code this returned False having emitted nothing at all
        — `_human_present` refused it, and the bypass below would have refused it again."""
        fake = _FakeWinsound()
        with _winsound(fake):
            fired = self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        self.assertTrue(fired, "0.0.19 F13: a Windows gated write announces itself")
        self.assertEqual([fake.MB_ICONASTERISK], fake.calls)


# ======================================================================================
# 3. THE STRING THAT BECAME FALSE
# ======================================================================================

class TestTheShippedNoticeIsTrue(_Base):
    """⭐ THE `RESTORE_ROW` CLASS, in `notify.py`. A user-visible runtime constant made a claim
    about a release — *"no desktop notification and no sound on Windows this release"* — and the
    moment the arm landed, half of it was false and the other half was scheduling.

    🔴 AND B5 DOES NOT SEE IT. Every `CLAIM_PATTERN` requires a VERSION LITERAL, and this claim
    named none: it said *"this release"*, which re-targets itself at whatever is being cut and can
    therefore never be resolved against a plan. Measured, not read — see the stage report. So the
    fix is not to point the promise at 0.0.20; it is to stop making one."""

    def _windows_notice(self) -> str:
        for n in D.emitted_notices():
            if n.subsystem == N.DEGRADE_WINDOWS:
                return n.render()
        self.fail("the Windows degrade was not emitted at all")

    def test_f13_regression_the_notice_no_longer_says_there_is_no_sound(self):
        """★ THE REGRESSION on deliverable 2. On the old code this text was emitted verbatim and
        every clause below was in it."""
        fake = _FakeWinsound()
        with _winsound(fake):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        text = self._windows_notice()
        self.assertEqual([fake.MB_ICONASTERISK], fake.calls,
                         "fixture check: a sound really was made, so 'no sound' really is false")
        self.assertNotIn("no sound", text)
        self.assertNotIn("this release", text)
        self.assertIn("sound-only", text)
        self.assertIn("no desktop notification", text,
                      "the half that is still TRUE must still be said")

    def test_the_notice_names_no_release_at_all(self):
        """★ THE CLASS, not the instance (§7i). A shipped runtime constant in this module may not
        carry a scheduling claim, and the pin is over EVERY non-docstring string constant rather
        than over the one that was wrong — prose in a comment is not a promise, but a string a
        user can be shown is."""
        source = inspect.getsource(N)
        offenders = [(line, value) for line, value in _printable_constants(source)
                     if re.search(r"\d+\.\d+\.\d+", value) or "this release" in value.lower()]
        self.assertEqual([], offenders,
                         "a release named in a shipped constant is a promise nothing resolves")

    def test_the_failure_class_describes_the_channel_and_not_the_host(self):
        """`"unverifiable-platform"` was a fact about the maintainer's hardware. What ships now is
        a fact about Windows: there is no argv-only visual notifier there. The class name is read
        by doctor, so it is part of the notice."""
        fake = _FakeWinsound()
        with _winsound(fake):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        classes = {n.failure_class for n in D.emitted_notices()
                   if n.subsystem == N.DEGRADE_WINDOWS}
        self.assertEqual({"no-visual-notifier"}, classes)


def _printable_constants(source: str):
    """Every NON-docstring string constant in a module. The same reading `disclosure` does, done
    here without importing it, so this pin does not inherit that module's declared vocabulary —
    the question here is "does any shipped string name a release", which is wider than "does any
    shipped string match a claim pattern", and that difference is the finding this row reports."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


# ======================================================================================
# 4. THREE STATES, STILL THREE
# ======================================================================================

class TestThreeStatesStayThree(_Base):
    """§7g. *One channel of two* · *the arm ran and failed* · *this machine can make no sound* are
    three different facts with three different remedies, and a partial arm is exactly where they
    get collapsed into "Windows is weird"."""

    def test_the_three_subsystem_names_are_three(self):
        names = (N.DEGRADE_SUBSYSTEM, N.DEGRADE_WINDOWS, N.DEGRADE_AUDIO)
        self.assertEqual(3, len(set(names)), names)

    def test_audio_played_no_visual_names_only_the_visual_gap(self):
        """STATE 1. The sound worked. The only thing missing is the banner, and only that is
        said — a DEGRADE_AUDIO here would tell a Windows user their machine cannot make a sound
        seconds after it made one."""
        fake = _FakeWinsound()
        with _winsound(fake):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        self.assertEqual({N.DEGRADE_WINDOWS}, self.subsystems())

    def test_the_arm_ran_and_failed_is_its_own_name(self):
        """STATE 2. Linux, `notify-send` present and refusing. Nothing about Windows, nothing
        about this box's speakers — the arm exists and did not work."""
        with _sounds(True), mock.patch.object(N, "shutil", _Which({"canberra-gtk-play"})):
            self.fire(N.KIND_WAIT, runner=_Spy(returns=False), is_tty=False, platform="linux")
        self.assertIn(N.DEGRADE_SUBSYSTEM, self.subsystems())
        self.assertNotIn(N.DEGRADE_WINDOWS, self.subsystems())

    def test_no_sound_possible_is_its_own_name(self):
        """STATE 3. The banner went out; the box has no player and no terminal to ring."""
        with _sounds(False), mock.patch.object(N, "shutil", _Which({"notify-send"})):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="linux")
        self.assertEqual({N.DEGRADE_AUDIO}, self.subsystems())

    def test_two_true_facts_emit_two_notices_and_neither_masks_the_other(self):
        """★ THE STRONGEST FORM OF §7g HERE, and the case a collapsed taxonomy gets wrong:
        Windows on a build with no `winsound`, off a terminal. BOTH facts are true — there is no
        visual arm, AND this machine can make no sound — and a reader who is told only one of
        them is told either "install a player" (there is nothing to install) or "watch the
        statusline" (while silently also getting no sound)."""
        with _winsound(None):
            fired = self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        self.assertFalse(fired)
        self.assertEqual({N.DEGRADE_WINDOWS, N.DEGRADE_AUDIO}, self.subsystems())

    def test_the_windows_no_sound_fix_names_no_package_windows_does_not_have(self):
        """A fix line that cannot be followed is a degrade notice that degrades. Telling a Windows
        user to install `libcanberra-gtk3-bin` is not merely unhelpful — it is false."""
        with _winsound(None):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32")
        text = "".join(n.render() for n in D.emitted_notices()
                       if n.subsystem == N.DEGRADE_AUDIO)
        self.assertNotIn("libcanberra", text)
        self.assertNotIn("pulseaudio", text)
        self.assertIn("winsound", text)
        self.assertIn("settings.ux.notify_audio false", text,
                      "the off switch must survive into the Windows wording")

    def test_a_windows_build_without_winsound_still_reaches_the_bell(self):
        """The ladder below the arm is the same one every platform walks: no player, but a
        terminal — ring it. Without this the Windows branch would be a dead end rather than a rung.
        """
        with _winsound(None):
            fired = self.fire(N.KIND_PROMPT, runner=_Spy(), is_tty=True, platform="win32")
        self.assertTrue(fired, "the bell is the universal floor and Windows has a terminal too")
        self.assertIn("\a", "".join(self.notices))
        self.assertNotIn(N.DEGRADE_AUDIO, self.subsystems(),
                         "a sound WAS made; only the banner is missing")


# ======================================================================================
# 5. THE USER-FACING SETTING KEEPS ITS MEANING
# ======================================================================================

class TestTheAudioSettingStillMeansWhatItSaid(_Base):
    """`ux.notify_audio: false` is documented and shipped as *"keeps the notification, silent"*.
    A new sound channel must obey it on day one, or the row shipped an off switch that stopped
    working on the one platform it just started mattering on."""

    def test_notify_audio_false_silences_the_windows_sound(self):
        fake = _FakeWinsound()
        with _winsound(fake):
            fired = self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32",
                              settings=_settings(audio=False))
        self.assertEqual([], fake.calls, "audio OFF must never reach winsound")
        self.assertFalse(fired, "Windows has no visual channel, so audio off is silence")

    def test_audio_off_is_a_choice_and_not_a_degrade(self):
        """The contrast that stops the notice above from becoming alert fatigue: a user who turned
        audio off is not degraded and must not be told they are. The VISUAL gap is still named,
        because that one is not their choice."""
        with _winsound(_FakeWinsound()):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32",
                      settings=_settings(audio=False))
        self.assertEqual({N.DEGRADE_WINDOWS}, self.subsystems())

    def test_notify_audio_false_still_leaves_the_notification_where_one_exists(self):
        """★ THE ANTI-VACUITY LEG. Without it, "audio off silences the sound" would pass against
        a build where `audio: false` disabled the whole feature — which is the documented contract
        it would be breaking, not keeping."""
        spy = _Spy()
        with _sounds(True), mock.patch.object(N, "shutil", _Which({"notify-send"})):
            fired = self.fire(N.KIND_WAIT, runner=spy, is_tty=False, platform="linux",
                              settings=_settings(audio=False))
        self.assertTrue(fired)
        self.assertEqual(1, len(spy.calls))
        self.assertEqual("notify-send", spy.calls[0][0],
                         "the banner survives; only the sound was silenced")

    def test_notify_false_silences_windows_entirely(self):
        """The outer switch still outranks the inner one on the new platform too."""
        fake = _FakeWinsound()
        with _winsound(fake):
            fired = self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32",
                              settings=_settings(enabled=False))
        self.assertFalse(fired)
        self.assertEqual([], fake.calls)
        self.assertEqual(set(), self.subsystems(), "a feature turned off degrades nothing")


# ======================================================================================
# 6. THE LOAD-BEARING NEGATIVE
# ======================================================================================

class TestMacOSAndLinuxAreUnchanged(_Base):
    """🔴 A test that only checks Windows passes against a build that broke the other two. This
    class is the reason the row can claim "audio arm added" rather than "audio rewritten"."""

    #: The POSIX no-audio notice, PINNED VERBATIM as it stood before this row. `_no_audio_notice`
    #: put a fork in front of these two strings; it did not rewrite them, and a reworded Linux
    #: degrade is a behaviour change on Linux however well-meant.
    POSIX_FALLBACK = ("the notification was raised, but this machine offers no way to "
                      "make a sound (no audio player found, and no terminal to ring)")
    POSIX_FIX = ("Install `libcanberra-gtk3-bin` or `pulseaudio-utils`, or set "
                 "`settings.ux.notify_audio false` to stop asking for it")

    def test_the_posix_no_audio_notice_is_byte_identical_and_shared(self):
        for platform in ("darwin", "linux", "linux2", "freebsd12"):
            with self.subTest(platform=platform):
                self.assertEqual((self.POSIX_FALLBACK, self.POSIX_FIX),
                                 N._no_audio_notice(platform))

    def test_winsound_is_never_touched_off_windows(self):
        """★ The arm must be UNREACHABLE elsewhere. Injected everywhere, called nowhere."""
        matrix = [("darwin", sounds, tty, audio, bins)
                  for sounds in (True, False) for tty in (True, False)
                  for audio in (True, False) for bins in ({"notify-send"},)]
        matrix += [("linux", sounds, tty, audio, bins)
                   for sounds in (True, False) for tty in (True, False)
                   for audio in (True, False)
                   for bins in ({"notify-send", "canberra-gtk-play"}, {"notify-send"})]
        for platform, sounds, tty, audio, bins in matrix:
            with self.subTest(platform=platform, sounds=sounds, tty=tty, audio=audio):
                D.reset_degrade_notices()
                fake = _FakeWinsound()
                with _winsound(fake), _sounds(sounds), \
                        mock.patch.object(N, "shutil", _Which(bins)):
                    self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=tty, platform=platform,
                              settings=_settings(audio=audio))
                self.assertEqual([], fake.calls)
                self.assertNotIn(N.DEGRADE_WINDOWS, self.subsystems())

    def test_the_posix_channel_matrix_is_what_it_was(self):
        """★ THE BEHAVIOURAL PIN, as a DECLARED table: for each POSIX scenario, exactly which
        argv were built, whether anything emitted, and which subsystems degraded. A change to any
        non-Windows path lands here rather than on a user."""
        # (platform, binaries, sound-files-exist, is_tty, audio) -> (fired, argv0s, subsystems)
        table = [
            ("linux", {"notify-send", "canberra-gtk-play"}, True, True, True,
             True, ["notify-send", "canberra-gtk-play"], set()),
            ("linux", {"notify-send"}, False, True, True,
             True, ["notify-send"], set()),
            ("linux", {"notify-send"}, False, False, True,
             True, ["notify-send"], {N.DEGRADE_AUDIO}),
            ("linux", {"notify-send"}, False, False, False,
             True, ["notify-send"], set()),
            ("linux", {"notify-send", "paplay"}, True, False, True,
             True, ["notify-send", "paplay"], set()),
            ("darwin", set(), True, False, True,
             True, ["osascript", "afplay"], set()),
            ("darwin", set(), False, False, True,
             True, ["osascript"], {N.DEGRADE_AUDIO}),
            ("darwin", set(), False, True, True,
             True, ["osascript"], set()),
        ]
        for platform, bins, sounds, tty, audio, fired_x, argv_x, subs_x in table:
            with self.subTest(platform=platform, bins=sorted(bins), sounds=sounds, tty=tty,
                              audio=audio):
                D.reset_degrade_notices()
                spy = _Spy()
                with _sounds(sounds), mock.patch.object(N, "shutil", _Which(bins)):
                    fired = self.fire(N.KIND_WAIT, runner=spy, is_tty=tty, platform=platform,
                                      settings=_settings(audio=audio))
                self.assertEqual(fired_x, fired)
                self.assertEqual(argv_x, [c[0] for c in spy.calls])
                self.assertEqual(subs_x, self.subsystems())


# ======================================================================================
# 7. NO NEW DEPENDENCY
# ======================================================================================

class TestNoNewDependency(_Base):
    """⛔ Doc 00 requires an external dependency to be optional with a fallback, and a toast
    library would fail that outright. `winsound` is not a dependency at all — it is stdlib, on
    Windows — and the pins below are what make that a structural fact rather than a claim."""

    def test_winsound_is_imported_lazily_and_only_inside_the_arm(self):
        """★ AST, not grep. A module-level `import winsound` would raise on every non-Windows box
        at `import mokata.notify`, i.e. it would break the seam everywhere to serve one platform.
        """
        tree = ast.parse(inspect.getsource(N))
        top_level = {alias.name for node in tree.body if isinstance(node, ast.Import)
                     for alias in node.names}
        self.assertNotIn("winsound", top_level, "the import must live INSIDE the arm")

        arm = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_windows_sound")
        inside = {alias.name for node in ast.walk(arm) if isinstance(node, ast.Import)
                  for alias in node.names}
        self.assertIn("winsound", inside)

        everywhere = [node for node in ast.walk(tree) if isinstance(node, ast.Import)
                      for alias in node.names if alias.name == "winsound"]
        self.assertEqual(1, len(everywhere),
                         "exactly one import site, and it is the one asserted above")

    def test_the_modules_top_level_imports_are_stdlib_and_mokata_only(self):
        """★ THE CLASS. The row is graded on adding NO dependency, so the pin is over the whole
        import set rather than over `winsound` — a toast library added next to it would pass a
        winsound-shaped assertion and fail this one."""
        tree = ast.parse(inspect.getsource(N))
        plain = {alias.name.split(".")[0] for node in tree.body if isinstance(node, ast.Import)
                 for alias in node.names}
        froms = {node.module.split(".")[0] for node in tree.body
                 if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module}
        self.assertEqual({"os", "shutil", "subprocess", "sys", "time"}, plain)
        self.assertEqual({"__future__", "dataclasses", "typing"}, froms)

    def test_an_absent_winsound_is_a_state_and_not_a_crash(self):
        """The consequence of the lazy import, exercised at the arm rather than at the module.

        ⚠ THIS DELIBERATELY DOES NOT `importlib.reload(N)`. An earlier draft did, to "prove the
        module imports with no winsound" — but that proof is already standing: every runner this
        suite has ever run on lacks the extension, so the import at the top of this file IS the
        assertion, and a reload mid-suite would rebind the module's classes and process-lifetime
        markers underneath every other test in the tree for no coverage that is not already here.
        A pin that can only be trusted at one position in the run order is not a pin."""
        with _winsound(None):
            self.assertFalse(N._windows_sound(), "absent winsound is a state, not a failure")


# ======================================================================================
# 8. SECRET SAFETY
# ======================================================================================

class TestSecretSafety(_Base):

    def test_the_windows_notice_carries_no_path_no_tool_and_no_proposal_id(self):
        """The notice is a diagnostic that lands in a scrollback a screen-share retains. It states
        a fact about the platform and nothing about this run."""
        with _winsound(None):
            self.fire(N.KIND_WAIT, runner=_Spy(), is_tty=False, platform="win32",
                      tool="mcp__mokata__memory_put", proposal_id="prop-7f3a91c4-secret")
        text = "".join(n.render() for n in D.emitted_notices())
        self.assertNotIn(self.root, text)
        self.assertNotIn("prop-7f3a91c4-secret", text)
        self.assertNotIn("mcp__mokata__memory_put", text)
        self.assertTrue(text.strip(), "fixture check: there WAS a notice to inspect")


if __name__ == "__main__":
    unittest.main()
