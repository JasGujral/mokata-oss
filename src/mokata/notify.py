"""Lane F — mokata's OWN attention channel: an OS notification and a sound, when it is your move.

WHY THIS EXISTS. `awaiting.py` already owns the question *"which channels does this wait raise?"*
and its answer, for the common case, is uncomfortable: a gated write returns a proposal and stops,
the `mcp__mokata__*` allow-grant means Claude Code raises **no** permission prompt, and so the
harness fires no `Notification` event. The wait is real, healthy, and completely silent — legible
only to someone already looking at the statusline or reading the tool result. A human who walked
away comes back to a session that looks wedged (P16), which is the failure `awaiting` was built to
kill and could only half-kill: it can make a wait LEGIBLE, but it cannot make anyone LOOK.

WHAT THIS IS NOT. It is not a synthesized harness event. The `UX-NOTIFY` doctrine in `awaiting.py`
rules that *"mokata must not synthesize a harness notification — it is not the harness, and a tool
that fakes a harness notification lies about who is asking"*, and that rule STANDS: what this
module emits is mokata's own process making its own noise on its own behalf, under a setting the
user owns. Nor does it grow a firing side inside `signal_wait`, which is documented pure — it
*CONSUMES* that classification from here, which is why this is a separate module and not ten lines
added to `awaiting`.

⚠ THE SECURITY CLASS, NAMED BEFORE THE FIRST LINE OF CODE. 0.0.17 stage 5 was **F10**:
`hook_cli.py` running `subprocess.run(command, shell=True)`, orphaned across four releases and
reachable at the default scope. A notifier that shells out to `afplay` / `osascript` /
`notify-send` is the SAME CLASS, so this module holds to three rules that are structurally
enforced, not merely intended:

  1. **`shell=False`, argv lists, always.** No f-string ever becomes a command.
  2. **Nothing variable reaches an OS argv.** Not a proposal id, not a tool name, not a path. The
     body is chosen from `BODIES`, a frozen in-code set, and `_require_fixed` REFUSES to build an
     argv around anything else — so the safety is a check, not an argument in a comment.
  3. **Every OS call is bounded.** A subprocess that hangs is strictly worse than one that fails:
     it converts a notification into the wedge it exists to announce.

⚠ AND THE BAR ABOVE ALL OTHERS: **a notification may never block, delay, or fail the wait it
announces.** The seam is `notify()`, it returns a bool, and it raises nothing — the `record_usage`
shape, with the swallow at one visible place rather than scattered through the callers. The OS
calls it makes are bounded by `NOTIFY_TIMEOUT_SECONDS`, which is a bound on a call that precedes a
human-paced wait, not a delay imposed on one.

WHERE THE TWO KINDS OF HUMAN ACTUALLY ARE, because they are not in the same place and one presence
test for both would be wrong in one of them:

  * `KIND_PROMPT` — a CLI y/N or free-text gate. The human answers ON THE TERMINAL, so the terminal
    is the presence test: no TTY, no human, and `read_yes_no` has already fail-closed to No. This
    is structural rather than checked twice — the announce call sits AFTER the TTY guard.
  * `KIND_WAIT` — an MCP gated write. The MCP server is a stdio subprocess of the harness and
    **never has a TTY**, so a TTY test here would silence the notification in every case it exists
    for. The human is at a DESKTOP, not on this pipe, so that is what is tested for.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — argv-only, shell=False, timeout-bounded; see the rules above
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME

# ======================================================================================
# the three levels — a CONFIG KEY, not a per-call-site design choice
# ======================================================================================
# `harness-silent` is the no-double-notify level: it fires on exactly the waits that are silent
# today, i.e. the ones where `signal_wait` does NOT return HARNESS_NOTIFICATION. `all-waits` adds
# the waits the harness already announces. `all-prompts` adds every CLI human gate on top.
#
# ⭐ `all-prompts` is the DEFAULT (Jas, 2026-08-15) — the WIDEST level, deliberately: the failure
# this lane exists to fix is a human who did not know it was their move, and the cost of the wrong
# default in that direction is one extra sound, not a missed turn.

HARNESS_SILENT = "harness-silent"
ALL_WAITS = "all-waits"
ALL_PROMPTS = "all-prompts"
LEVELS: Tuple[str, ...] = (HARNESS_SILENT, ALL_WAITS, ALL_PROMPTS)
DEFAULT_LEVEL = ALL_PROMPTS

# The two kinds of "it is your move", which differ in WHERE the human is (see the module docstring).
KIND_WAIT = "wait"
KIND_PROMPT = "prompt"
KINDS: Tuple[str, ...] = (KIND_WAIT, KIND_PROMPT)


# ======================================================================================
# the fixed body set — rule 2, as data
# ======================================================================================
# Every human-readable string that can reach an OS binary is declared HERE, as a literal. There is
# no formatting hole, no id, no path and no tool name in any of them: `_require_fixed` refuses to
# build an argv around a string that is not a member of `BODIES`, so "nothing variable reaches the
# argv" is enforced by a check at the boundary rather than asserted by the comment above it.
#
# What is genuinely variable about a wait — WHICH proposal, WHICH command resolves it — is rendered
# by mokata's own surfaces, which is what they are for: the `awaiting` head in the tool result and
# the statusline segment both name the id. The notification's whole job is to make someone LOOK at
# those; it does not need to repeat them, and repeating them would put a proposal id on a desktop
# notification banner that a screen-share or a screenshot retains (the `awaiting` secret-safety
# rule, one surface further out).

TITLE = "mokata"
BODY_WAIT = "your move — a write is waiting for your approval"
BODY_PROMPT = "your move — a prompt is waiting for your answer"

# Discoverability is part of the deliverable: audio-on-by-default is a behaviour change in a launch
# release, so the FIRST notification a user ever gets carries its own off switch — and only the
# first, keyed on a marker, exactly as the deprecation notices do it.
OFF_SWITCH = "turn this off: mokata config set settings.ux.notify false"
BODY_WAIT_FIRST = f"{BODY_WAIT} ({OFF_SWITCH})"
BODY_PROMPT_FIRST = f"{BODY_PROMPT} ({OFF_SWITCH})"

BODIES = frozenset({BODY_WAIT, BODY_PROMPT, BODY_WAIT_FIRST, BODY_PROMPT_FIRST})

# The hard bound on every OS call this module makes. Generous enough that a healthy `osascript` or
# `notify-send` never trips it, short enough that a wedged one is not mistaken for the wait.
NOTIFY_TIMEOUT_SECONDS = 2.0

# ⭐ THE RATE LIMIT, and it is not a nicety — it is the FEATURE'S OWN P22 BAR. The brief opens with
# "a notification that fires when nobody is waiting trains the human to ignore it", and the same is
# true, faster, of one that fires twenty times when the human is waiting ONCE. mokata stages gated
# writes in batches (a playbook run, a spec emit followed by four memory puts, a config wizard
# walking eleven settings), and one banner per staged write is not twenty times the signal — it is
# zero times the signal, plus an angry user who turns the whole thing off.
#
# ⚠ THIS WAS FOUND THE HONEST WAY: the first build of this module had no limit, the suite drove
# `read_yes_no` a few hundred times, and every one of them raised a real macOS banner on the
# maintainer's desktop. The defect it exposed is not "tests are noisy" — it is that ONE wait and
# TWENTY waits were emitting the same number of notifications as twenty separate humans-are-needed
# events, which is the shape of every alert system anyone has ever learned to ignore.
#
# One minute: long enough that a batch collapses to a single "it's your move", short enough that a
# genuinely new ask an hour later still announces itself.
NOTIFY_MIN_INTERVAL_SECONDS = 60.0

# D5 subsystems. THREE, not one, so "this platform ships no arm", "the arm ran and failed" and
# "audio was asked for and this machine can make no sound" each get their own once-per-process
# notice instead of one masking the others (§7g: distinct states, distinct representations).
DEGRADE_SUBSYSTEM = "notify"
DEGRADE_WINDOWS = "notify-windows"
DEGRADE_AUDIO = "notify-audio"

_MARKER_DIRNAME = "notify"
_OFF_SWITCH_MARKER = "off-switch-shown.marker"
_LAST_STAMP = "last-notified.stamp"

# The env override, which is BOTH a user kill-switch that needs no manifest and the explicit opt-IN
# a test must give before this module is allowed to touch a desktop. See `_suppressed`.
NOTIFY_ENV = "MOKATA_NOTIFY"

# Process-lifetime fallbacks for the two markers, used when there is no `.mokata` to hold them.
_PROCESS_LAST_NOTIFIED = 0.0

# The macOS system sound. A PATH, and therefore never passed as a BODY — it is an argv element of a
# fixed in-code value, checked for existence before use, and it names no user content.
_MACOS_SOUND = "/System/Library/Sounds/Ping.aiff"

# The freedesktop sound-theme id for "something needs you". `canberra-gtk-play -i <id>` is argv-only
# and needs no file path; when it is absent the terminal bell is the floor, which needs no binary at
# all and therefore cannot be the thing that hangs.
_CANBERRA_SOUND_ID = "message"

# ⚠ THE SECOND LINUX ARM, ADDED AT THE 0.0.18 CUT-HALT. The first build of this module hung Linux
# audio on ONE optional binary — `canberra-gtk-play`, which ships in `libcanberra-gtk3-bin` and is
# absent on a great many desktops and on every GitHub runner. Where it is missing and there is no
# TTY to ring — which is EXACTLY the MCP gated write this lane exists for — the user got a banner
# and no sound, against Jas's stated requirement that it "notify and also give audio", and got it
# SILENTLY. `paplay` is part of the same freedesktop stack, takes a file, and is argv-only; the
# file is the sound theme's own sample, a fixed in-code path checked for existence exactly as the
# macOS arm checks its own. Two arms is not two guesses — it is the difference between a feature
# that depends on one package and one that depends on the sound stack being present at all.
_FREEDESKTOP_SOUND = "/usr/share/sounds/freedesktop/stereo/message.oga"

# Set once per process when no `.mokata` was available to hold the marker (see `claim_first_notice`).
_PROCESS_FIRST_SHOWN = False


def body_for(kind: str, *, first_time: bool = False) -> str:
    """The fixed body for `kind`. Always a member of `BODIES`."""
    if kind == KIND_WAIT:
        return BODY_WAIT_FIRST if first_time else BODY_WAIT
    return BODY_PROMPT_FIRST if first_time else BODY_PROMPT


# ======================================================================================
# settings — three keys, because silencing the audio must not disable the notification
# ======================================================================================

@dataclass(frozen=True)
class NotifySettings:
    """`settings.ux.notify` · `settings.ux.notify_audio` · `settings.ux.notify_level`.

    THREE keys and not two, which is Jas's requirement stated as a data shape: "the user can
    disable it" and "the user can silence the audio while keeping the notification" are different
    wishes, and a single `notify: quiet|loud|off` enum would have made the second one a mode of the
    first. Both channels default ON; the level defaults to the widest."""

    enabled: bool = True
    audio: bool = True
    level: str = DEFAULT_LEVEL


def coerce_level(value: Any) -> str:
    """A manifest value → a level. Degrade-clean and opt-DOWN, read exactly like
    `progress.badge_verbosity`: an absent, broken or unrecognised value reads as the documented
    default, so the shipped behaviour is never silently narrowed by a typo."""
    return value if value in LEVELS else DEFAULT_LEVEL


def load_settings(root: str = ".") -> NotifySettings:
    """This repo's notify settings, or the documented defaults.

    ⚠ READS THE MANIFEST, NOT A `Surface`, and that is a correctness point rather than a
    micro-optimisation. These are three manifest SCALARS; `Surface.load` additionally builds a
    `Router` and a `Detector`, and the wait seam this sits on is `mcp.consent._propose` — the exact
    path MCP-SURF drove from three `Surface.load`s per tool call down to one. Reading the settings
    through a Surface would have quietly put the second one back, on every gated write, forever.
    `Manifest.load` is the whole of what is needed.

    The root is resolved through `resolve_mokata_root` — the one resolver that knows a linked
    worktree is not a different repository — so a session inside a worktree honours the repo's real
    setting instead of silently reading the default.

    Degrade-clean in the same direction as `progress.statusline_enabled`: an absent or unreadable
    manifest reads as the DEFAULTS (on, audio on, widest), so an uninitialized repo — which is
    exactly where `mokata init` and the setup wizard prompt a human — still announces. Never
    raises; this sits on the path of a wait it must not be able to break."""
    try:
        from . import MANIFEST_FILENAME
        from .manifest import Manifest
        from .repo_identity import resolve_mokata_root
        config_root = resolve_mokata_root(root).root or root
        path = os.path.join(config_root, MOKATA_DIR, MANIFEST_FILENAME)
        if not os.path.exists(path):
            return NotifySettings()
        ux = Manifest.load(path).setting("ux", {}) or {}
        return NotifySettings(
            enabled=bool(ux.get("notify", True)),
            audio=bool(ux.get("notify_audio", True)),
            level=coerce_level(ux.get("notify_level", DEFAULT_LEVEL)),
        )
    except Exception:            # ← SWALLOW SEAM 1 of 2 (see `notify`) — a settings read may not
        return NotifySettings()  # break a prompt, and losing the default-on behaviour is a bug


# ======================================================================================
# the level predicate — pure, and the ONLY thing that decides WHETHER a kind fires
# ======================================================================================

def level_fires(level: str, kind: str, *, harness_notified: bool) -> bool:
    """Does `level` fire for this `kind`? Pure, total, and deliberately the whole of the level
    logic, so a test can pin membership by name rather than by observing an emitter.

    `harness_notified` is `signal_wait`'s classification, CONSUMED here — the one axis on which
    `harness-silent` and `all-waits` differ, since they range over the identical call sites."""
    level = coerce_level(level)
    if kind == KIND_PROMPT:
        return level == ALL_PROMPTS
    if kind != KIND_WAIT:
        return False
    if level == HARNESS_SILENT:
        return not harness_notified
    return True                                     # all-waits and all-prompts both cover waits


# ======================================================================================
# presence — is there a human to announce TO?
# ======================================================================================

def _in_ci(env: Dict[str, str]) -> bool:
    """CI, on the same key `perf.py` already trusts. A beep on a headless runner is noise at best
    and a hang at worst if the audio binary blocks, so this is checked for BOTH kinds and at every
    level, unconditionally."""
    return bool(env.get("CI") or env.get("GITHUB_ACTIONS") or env.get("BUILD_NUMBER"))


def _over_ssh(env: Dict[str, str]) -> bool:
    """True on a remote shell. A desktop notification raised from an SSH session is delivered to
    the REMOTE machine's console, where by definition nobody is sitting — so the OS-notification
    channel is dropped while the terminal channel, which is the one the human is actually reading,
    is kept."""
    return bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY") or env.get("SSH_CLIENT"))


def _suppressed(env: Dict[str, str]) -> bool:
    """Is this module forbidden to touch a desktop at all?

    ⭐ DEFAULT-OFF UNDER TEST, and this is a CONTRACT, not caution. A test suite that raises real
    OS notifications is a suite with a side effect on the machine running it — and mokata's own
    suite drives `read_yes_no` several hundred times, so the side effect is several hundred banners
    and several hundred sounds. Every notifier test in the tree injects a `runner` and grades argv,
    which is the right way to grade an emitter; nothing is lost by refusing to spawn the real one.

    The check is on `sys.modules` rather than on an env var a runner might not set, because the
    thing being detected is "a test framework is driving this process", and that is exactly what
    importing it means. `MOKATA_NOTIFY` overrides in BOTH directions — `1` lets a test opt in to
    the emit path deliberately, and `0` is a kill switch a user can set without touching a manifest.
    """
    override = env.get(NOTIFY_ENV)
    if override is not None:
        return override.strip().lower() in ("0", "false", "no", "off")
    under_test = ("pytest" in sys.modules or "unittest" in sys.modules
                  or bool(env.get("PYTEST_CURRENT_TEST")))
    if not under_test:
        return False
    # ⚠ AND PROPAGATE IT, which is the half `sys.modules` cannot do on its own. The suite spawns
    # child processes (the mutation harness alone runs it hundreds of times), and a child that
    # execs `mokata` directly has no test framework loaded — so the in-process check would read
    # "not a test" and the desktop would light up from a subprocess of a run that was supposed to
    # be silent. Stamping the environment is the only thing a parent can hand a child.
    #
    # The side effect is deliberate and one-directional: it can only ever make mokata QUIETER, and
    # it is written at the exact moment the decision is made rather than at import, so a process
    # that never notifies never touches its own environment.
    os.environ.setdefault(NOTIFY_ENV, "0")
    return True


def _debounced(root: str, *, now: float) -> bool:
    """True when a notification fired too recently to fire again — see NOTIFY_MIN_INTERVAL_SECONDS.

    Persisted as a file mtime rather than held in memory, because the CLI half of the corpus is
    many short-lived PROCESSES: `mokata approve`, then `mokata spec emit`, then the wizard are three
    interpreters, and an in-memory limiter would let a batch through one banner at a time. The MCP
    half is one long-lived process and would have been fine either way; the file covers both.

    Degrade-clean and BIASED TOWARD SILENCE: a stamp that cannot be read or written falls back to a
    process-lifetime timestamp. Getting this wrong in the quiet direction costs one missed banner;
    getting it wrong in the loud direction is the defect this whole constant exists for."""
    global _PROCESS_LAST_NOTIFIED
    stamp = os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, _MARKER_DIRNAME, _LAST_STAMP)
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        try:
            last = os.path.getmtime(stamp)
        except OSError:
            last = 0.0
        if now - last < NOTIFY_MIN_INTERVAL_SECONDS:
            return True
        with open(stamp, "w", encoding="utf-8"):
            pass
        os.utime(stamp, (now, now))
        return False
    except (OSError, ValueError):
        if now - _PROCESS_LAST_NOTIFIED < NOTIFY_MIN_INTERVAL_SECONDS:
            return True
        _PROCESS_LAST_NOTIFIED = now
        return False


def _human_present(kind: str, *, is_tty: bool, env: Dict[str, str], platform: str) -> bool:
    """Is there a human to announce to, for THIS kind? See the module docstring for why the two
    kinds do not share a presence test."""
    if _in_ci(env):
        return False
    if kind == KIND_PROMPT:
        return is_tty                       # the terminal IS the answer channel
    if platform == "darwin":
        return True
    if platform.startswith("linux"):
        return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return False


# ======================================================================================
# the OS calls — argv only, bounded, and refusing anything not in the fixed set
# ======================================================================================

def _require_fixed(body: str) -> bool:
    """RULE 2, as a check. An argv is built only around a member of the frozen body set; anything
    else — a caller that grew a format hole, a future body added without being declared — is
    refused rather than shelled out with."""
    return body in BODIES


def _run_argv(argv: List[str], timeout: float = NOTIFY_TIMEOUT_SECONDS) -> bool:
    """Run `argv` with NO shell, bounded by a hard timeout. True only on a clean exit.

    Every failure class lands the same way — False — because there is exactly one thing a caller
    can do about a notifier that did not notify, and it is to carry on with the wait. The classes
    are named rather than caught broadly so that a genuinely unexpected exception still surfaces to
    `notify`'s one seam instead of being silently absorbed here."""
    try:
        proc = subprocess.run(argv, shell=False, timeout=timeout,   # nosec B603 — argv, no shell
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              check=False)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return False        # a hung binary is bounded HERE; the degrade is named by the caller
    except (OSError, ValueError, subprocess.SubprocessError):
        return False        # absent binary / bad argv / spawn failure — all "it did not notify"


def _notification_argv(platform: str, body: str) -> Optional[List[str]]:
    """The desktop-notification argv for `platform`, or None when this platform ships no arm.

    ⚠ WINDOWS SHIPS NO ARM, and the reason is stronger than "there is no Windows host to verify it
    on" (there isn't — OSS #46/#45/#28 are blocked on exactly that, and stage 17 carries the floor
    gap). It is that Windows has **no argv-only notification binary**: a toast requires handing
    PowerShell a SCRIPT STRING, which is rule 1's forbidden shape — an f-string becoming a command —
    and no amount of quoting makes that the same kind of call as `notify-send TITLE BODY`. Shipping
    it would trade the one guarantee this module is built on for an arm nobody could verify."""
    if not _require_fixed(body):
        return None
    if platform == "darwin":
        # `osascript -e` takes ONE argv element that is an AppleScript expression. That element is
        # composed here from two in-code literals and nothing else — `_require_fixed` above is what
        # makes that true rather than hoped, and `TITLE`/`BODIES` contain no quote or backslash for
        # the expression to be broken out of.
        return ["osascript", "-e",
                f'display notification "{body}" with title "{TITLE}"']
    if platform.startswith("linux"):
        # Pure argv — libnotify takes the summary and body as separate arguments, so there is no
        # expression for content to escape from at all.
        return ["notify-send", TITLE, body]
    return None


def _audio_argv(platform: str) -> Optional[List[str]]:
    """The sound argv for `platform`, or None when the terminal bell is the floor. Carries no body
    at all — a sound has no text — so rule 2 is satisfied by construction here.

    Both arms name a FIXED sound: a system file on macOS, a freedesktop sound-theme id on Linux.
    Neither is derived from anything, and `_CANBERRA_SOUND_ID` is a literal for the same reason the
    bodies are — an argv element chosen at runtime is the shape this module exists to avoid."""
    if platform == "darwin" and os.path.exists(_MACOS_SOUND):
        return ["afplay", _MACOS_SOUND]
    if platform.startswith("linux"):
        if shutil.which("canberra-gtk-play"):
            return ["canberra-gtk-play", "-i", _CANBERRA_SOUND_ID]
        if shutil.which("paplay") and os.path.exists(_FREEDESKTOP_SOUND):
            return ["paplay", _FREEDESKTOP_SOUND]
    return None


def _bell(out: Callable[[str], None]) -> bool:
    """The universal audio floor: BEL on the terminal. No subprocess, no binary to be absent, and
    nothing to hang — which is why it is the fallback rather than a second OS call."""
    out("\a")
    return True


# ======================================================================================
# THE SEAM
# ======================================================================================

def notify(kind: str, *, root: str = ".",
           settings: Optional[NotifySettings] = None,
           harness_notified: Optional[bool] = None,
           is_tty: Optional[bool] = None,
           env: Optional[Dict[str, str]] = None,
           platform: Optional[str] = None,
           runner: Optional[Callable[..., bool]] = None,
           out: Optional[Callable[[str], None]] = None,
           tool: str = "", proposal_id: str = "",
           debounce: bool = True, now: Optional[float] = None) -> bool:
    """Announce that it is the human's move. Returns True when a channel actually emitted.

    ⭐ RAISES NOTHING. This is the whole contract, and it is the reason the body below is one
    `try` with one `except` rather than a defensive check at every step: a notification that can
    fail its own wait is worse than no notification, and a swallow scattered across ten call sites
    is a swallow nobody can point at. `record_usage` is the precedent — a seam that returns a bool
    and cannot break the operation it rides on.

    `tool` and `proposal_id` are accepted, used to CLASSIFY (via `signal_wait`), and never rendered
    — see the `BODIES` comment for why a proposal id does not belong on a desktop banner."""
    try:
        return _notify(kind, root=root, settings=settings, harness_notified=harness_notified,
                       is_tty=is_tty, env=env, platform=platform, runner=runner, out=out,
                       tool=tool, proposal_id=proposal_id, debounce=debounce, now=now)
    except Exception:        # ← SWALLOW SEAM 2 of 2. THE one place a notification stops being able
        return False         # to become the thing it announces. Nothing below here may reach a caller.


def _notify(kind: str, *, root: str, settings: Optional[NotifySettings],
            harness_notified: Optional[bool], is_tty: Optional[bool],
            env: Optional[Dict[str, str]], platform: Optional[str],
            runner: Optional[Callable[..., bool]], out: Optional[Callable[[str], None]],
            tool: str, proposal_id: str, debounce: bool, now: Optional[float]) -> bool:
    env = dict(os.environ) if env is None else env
    platform = sys.platform if platform is None else platform
    runner = _run_argv if runner is None else runner
    out = _stderr if out is None else out
    settings = load_settings(root) if settings is None else settings

    if not settings.enabled:
        return False
    if _suppressed(env):
        return False

    # The harness classification is derived ONLY where it can change the answer — i.e. for a WAIT
    # at `harness-silent`, the one level that consults it. `signal_wait` probes the grant state and
    # the statusline wiring, and at `all-waits` / `all-prompts` (the default) its answer is
    # discarded: work whose result cannot affect the outcome, charged to every gated write.
    if harness_notified is None:
        harness_notified = (kind == KIND_WAIT and settings.level == HARNESS_SILENT
                            and _rides_harness(root, tool, proposal_id))
    if not level_fires(settings.level, kind, harness_notified=harness_notified):
        return False

    if is_tty is None:
        from .prompt import _stdin_is_tty
        is_tty = _stdin_is_tty()
    if not _human_present(kind, is_tty=is_tty, env=env, platform=platform):
        return False

    if platform.startswith("win"):
        # Named, not silent — a green suite must not be allowed to imply Windows coverage.
        _degrade(DEGRADE_WINDOWS, "unverifiable-platform", out,
                 fallback=("no desktop notification and no sound on Windows this release "
                           "(no argv-only notifier exists there, and no Windows host verifies one)"),
                 fix="Watch mokata's statusline segment, which carries the same wait")
        return False

    # LAST of the suppressions, and deliberately so: the rate limit is charged only against a
    # notification that would OTHERWISE have fired. Charging it earlier would let a wait suppressed
    # for being off-TTY reset the clock and silence the next real one.
    if debounce and _debounced(root, now=time.time() if now is None else now):
        return False

    body = body_for(kind, first_time=claim_first_notice(root))
    fired = False

    argv = _notification_argv(platform, body)
    if argv is not None and not _over_ssh(env):
        if runner(argv, NOTIFY_TIMEOUT_SECONDS):
            fired = True
        else:
            _degrade(DEGRADE_SUBSYSTEM, "os-notifier-failed", out,
                     fallback=f"`{argv[0]}` did not run (absent, refused, or timed out)",
                     fix="Install it, or set `settings.ux.notify false` to stop trying")

    if settings.audio:
        sound = _audio_argv(platform)
        if sound is not None:
            fired = runner(sound, NOTIFY_TIMEOUT_SECONDS) or fired
        elif is_tty:
            fired = _bell(out) or fired
        else:
            # ⚠ NAMED, NOT SILENT — its own subsystem, and the third one for the same reason the
            # other two are separate (§7g). Audio is ON, the platform ships an arm, and this
            # machine has neither the binary nor a terminal to ring: the user asked for a sound
            # and is getting none. That is a fact about their box, and a fact they can act on, so
            # it must not read the same as "audio is off". The MCP wait is precisely where this
            # lands — an MCP server has no TTY, so the bell floor does not exist there.
            _degrade(DEGRADE_AUDIO, "no-audio-channel", out,
                     fallback="the notification was raised, but this machine offers no way to "
                              "make a sound (no audio player found, and no terminal to ring)",
                     fix="Install `libcanberra-gtk3-bin` or `pulseaudio-utils`, or set "
                         "`settings.ux.notify_audio false` to stop asking for it")

    return fired


def _rides_harness(root: str, tool: str, proposal_id: str) -> bool:
    """Does this wait ALREADY raise a harness notification? CONSUMED from `awaiting.signal_wait`,
    which is documented pure and stays that way — the classification is read here, and the firing
    lives here, and neither module grows the other's job."""
    try:
        from .awaiting import HARNESS_NOTIFICATION, signal_wait
        return HARNESS_NOTIFICATION in signal_wait(root, tool=tool, proposal_id=proposal_id)
    except (ImportError, OSError, ValueError):
        return False


def _degrade(subsystem: str, failure_class: str, out: Callable[[str], None], *,
             fallback: str = "", fix: str = "") -> None:
    """One D5 notice, once per subsystem per process. Best-effort by definition: a degrade notice
    that could itself fail the wait would be the bug it is reporting."""
    try:
        from .degrade import note_degraded
        note_degraded(subsystem, failure_class, fallback=fallback, fix=fix, out=out)
    except (ImportError, OSError, ValueError):
        pass


def _stderr(message: str) -> None:
    """Notices and the terminal bell go to STDERR — they are diagnostics and signals, and either
    on stdout would corrupt the JSON a script is parsing (mirrors `degrade._stderr`)."""
    print(message, end="" if message == "\a" else "\n", file=sys.stderr, flush=True)


# ======================================================================================
# the first-notification marker — the off switch rides the first one, and only the first
# ======================================================================================

def claim_first_notice(root: str = ".") -> bool:
    """True exactly once: on the first notification this repo has ever raised.

    Backed by the same atomic `O_EXCL` marker `deprecation.warn_deprecated` uses, under
    `temp_local/` (run-state, ungated, per-repo-ephemeral).

    ⚠ THE DEGRADE INVERTS THE DEPRECATION ONE, on purpose. `warn_deprecated` SUPPRESSES its notice
    when the marker cannot be written, because there the marker guards a message. Here the marker
    guards only the off-switch RIDER on a message that fires regardless, and the failure mode to
    avoid is a user who is being notified with no way to discover how to stop. So an unavailable
    marker — no `.mokata` at all, which is the state `mokata init` prompts a human FROM — falls
    back to a process-lifetime flag: at most one rider per CLI invocation, never zero."""
    global _PROCESS_FIRST_SHOWN
    marker = os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME,
                          _MARKER_DIRNAME, _OFF_SWITCH_MARKER)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except (OSError, ValueError):
        if _PROCESS_FIRST_SHOWN:
            return False
        _PROCESS_FIRST_SHOWN = True
        return True


# ======================================================================================
# the two call-site-facing wrappers — the names the corpus sweep looks for
# ======================================================================================

def announce_prompt(root: str = ".") -> bool:
    """A CLI human gate is about to BLOCK on an answer. Called from inside each blocking reader,
    AFTER its TTY guard — so "never fire off a TTY" is structural at the prompt kind rather than a
    second check that could drift from the first."""
    return notify(KIND_PROMPT, root=root)


def announce_wait(root: str = ".", *, tool: str = "", proposal_id: str = "") -> bool:
    """A gated MCP write has staged a proposal and is now waiting on a human."""
    return notify(KIND_WAIT, root=root, tool=tool, proposal_id=proposal_id)


__all__ = ["ALL_PROMPTS", "ALL_WAITS", "BODIES", "BODY_PROMPT", "BODY_PROMPT_FIRST",
           "BODY_WAIT", "BODY_WAIT_FIRST", "DEFAULT_LEVEL", "DEGRADE_AUDIO", "DEGRADE_SUBSYSTEM",
           "DEGRADE_WINDOWS", "HARNESS_SILENT", "KINDS", "KIND_PROMPT", "KIND_WAIT", "LEVELS",
           "NOTIFY_ENV", "NOTIFY_MIN_INTERVAL_SECONDS", "NOTIFY_TIMEOUT_SECONDS",
           "OFF_SWITCH", "TITLE", "NotifySettings",
           "announce_prompt", "announce_wait", "body_for", "claim_first_notice",
           "coerce_level", "level_fires", "load_settings", "notify"]
