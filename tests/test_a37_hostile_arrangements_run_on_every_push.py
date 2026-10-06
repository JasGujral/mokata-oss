"""0.0.21 stage 07 — `WINDOWS-HAS-NO-RUNNER-UNTIL-THE-CUT`. The arrangement runs HERE, not at the cut.

⭐ **THE FINDING IS THE TIMING, NOT THE SIZE.** `ci.yml` is `on: push` and carries Windows legs —
and the private repo's Actions minutes have been exhausted since 2026-08-17, so it never executes
during the build. The local preflight is macOS-only by declaration. There is no Windows host. **So
the first Windows execution of an entire release's code is at the cut**, and five cuts of evidence
say what that costs: 5 failures / 3 causes, then 20/3, 14/2, 10 plus a 54-minute wedge, 11 across
2 modules. **Every batch resolved to defects in the TESTS, none in mokata** — and each cause was
invisible to its author *by construction*, because on POSIX the broken and correct spellings behave
identically. That is why *"review more carefully"* is not a remedy.

⛔ **THE BATCH IS NOT LARGE, IT IS LATE.** What closes that is not a Windows runner — it is making
the arrangement that exposes the class run on a machine this project already has, every push.

🔴 **AND IT FOUND TWO LIVE DEFECTS ON ITS FIRST RUN**, which is the argument for the whole stage:

    $ PYTHONIOENCODING=cp1252 python -m mokata release-notes-check 0.0.20
    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2212' in position 3253

`release-notes-check` is a gate `release.sh` runs, and `mokata tour` died the same way. **A gate
that crashes while printing its verdict has no verdict**, and the crash names an encoding rather
than the thing it was checking.

⚠ THIS IS A SECOND INSTRUMENT, NOT A REPLACEMENT FOR A WINDOWS RUNNER (§7f). A case-insensitive
filesystem, `os.sep`, and `CreateProcess`'s System32 search order are all real causes this project
has paid for and **none can be arranged on POSIX** — those are `tests/_windows_portability.py`'s
static readers. Both halves are named in `tests/_hostile_arrangements.py`.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import sys
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _hostile_arrangements as HA

_TESTS = os.path.abspath(os.path.dirname(__file__))
_REPO = os.path.dirname(_TESTS)
_SRC = os.path.join(_REPO, "src")


def _tree_env(env):
    """Put THIS repo's `src/` ahead of site-packages for the child process.

    🔴 §7c — THE OBSERVER WAS NOT LOOKING AT THE REPO, and this leg had that defect from the day
    it was written (found 2026-10-05, at the 0.0.21 cut). `_run` spawns
    `[sys.executable, "-m", "mokata", …]` with `cwd=_REPO`, and **a working directory does not put
    `src/` on a child's import path.** `_support` inserts it into the TEST process's `sys.path`,
    which the subprocess does not inherit — so `python -m mokata` resolved the INSTALLED package
    and every reading this class produced described `site-packages`, not the tree beside it.

    ⛔ The symptom was indistinguishable from the real defect: with 0.0.20 installed, the sweep
    reported `tour` and `release-notes-check` dying on a cp1252 stdout — which is exactly TRUE of
    0.0.20, the release before `cli._survive_a_narrow_stdio` existed. The fix was present, correct,
    wired into `main()` as its first statement, and verifiable by hand in one line; the leg simply
    never executed it. ⭐ **A red that is true of a DIFFERENT tree is worse than a flake**: it is a
    finding with evidence attached, and it cost this cut most of a day.

    ⚠ `PYTHONPATH` and not an editable install, deliberately: the leg must grade the WORKING TREE,
    including uncommitted changes, on any machine and in CI — an install step would reintroduce a
    second source of truth about which code is under test, which is the whole defect above.
    Whatever `PYTHONPATH` the arrangement already carries is preserved after ours, never dropped.
    """
    out = dict(env)
    prior = out.get("PYTHONPATH", "")
    out["PYTHONPATH"] = _SRC + (os.pathsep + prior if prior else "")
    return out

#: ⭐ THE PRODUCT'S OWN USER-FACING OUTPUT, not the test suite's. The class is "mokata printed
#: something this stdout cannot encode", and the thing a user runs is the thing to arrange. Chosen
#: for breadth of RENDERERS rather than count: a help screen, a version line, three diagnostics,
#: two release gates, and the two that actually died.
#:
#: ⚠ EXIT CODES ARE NOT ASSERTED. Several of these legitimately fail in a repo that is not set up
#: (`status`, `doctor`, `route`), and the subject here is whether the WRITE survives — conflating
#: the two would make the leg red for an unrelated reason and get it disabled (§7g).
_COMMANDS = (
    ("--help",),
    ("version",),
    ("status",),
    ("detect",),
    ("route",),
    ("doctor",),
    ("validate",),
    ("tour",),
    ("release-check", "0.0.20"),
    ("release-notes-check", "0.0.20"),
    ("spec-check",),
    ("memory",),
)

_UNICODE_DEATH = ("UnicodeEncodeError", "UnicodeDecodeError")


def _run(command, env):
    """Run `mokata <command>` and return the child's output as TEXT, decoded loosely.

    ⚠ `text=False` AND A MANUAL DECODE, and my first version did not do that: with `text=True`,
    Python decodes the child's bytes as UTF-8, and a child whose stdout IS cp1252 emits cp1252
    bytes — so the HARNESS died with `UnicodeDecodeError: 0x97 in position 601` while measuring
    whether the child died. ⭐ **An instrument that cannot read a hostile arrangement's output
    cannot grade it**, and the symptom looked exactly like the defect under test."""
    proc = subprocess.run([sys.executable, "-m", "mokata", *command],
                          cwd=_REPO, env=_tree_env(env), capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=120)
    return proc.returncode, (proc.stdout or b"").decode("utf-8", "backslashreplace"), \
        (proc.stderr or b"").decode("utf-8", "backslashreplace")


class TheArrangementsAreDECLAREDWithTheirEvidence(unittest.TestCase):
    """⚠ EVERY ARRANGEMENT NAMES THE WINDOWS FAILURE IT STANDS IN FOR, and that is enforced rather
    than encouraged. An arrangement whose Windows instance nobody can name is a guess — and this
    project has already paid for one of those: the 0.0.20 line-ending theory, written from a
    plausible mechanism and simply wrong."""

    def test_there_are_arrangements_at_all(self):
        self.assertGreaterEqual(len(HA.ARRANGEMENTS), 2)

    def test_each_one_names_what_it_reproduces_its_evidence_and_why_posix(self):
        for item in HA.ARRANGEMENTS:
            for field in ("reproduces", "evidence", "why_posix"):
                value = getattr(item, field)
                self.assertGreater(len(value), 60,
                                   f"{item.key}.{field} is too thin to be a claim: {value!r}")
            self.assertTrue(item.env, f"{item.key} arranges nothing")

    def test_what_CANNOT_be_arranged_on_posix_is_written_down(self):
        """§7f, as prose that has to exist: without it, the next reader takes this leg for a
        Windows runner and stops asking for §5.5's Actions minutes."""
        with io.open(os.path.join(_TESTS, "_hostile_arrangements.py"), encoding="utf-8") as fh:
            body = fh.read()
        # ⚠ WHITESPACE-NORMALISED: the sentence wraps across lines in the source, and my first
        # version asserted the unwrapped spelling. A claim about PROSE has to read prose.
        flat = " ".join(body.split())
        self.assertIn("none of them can be arranged on POSIX", flat)
        self.assertIn("_windows_portability", flat)

    def test_apply_does_NOT_mutate_the_real_environment(self):
        before = dict(os.environ)
        HA.arrangement("cp1252-stdio").apply()
        self.assertEqual(before, dict(os.environ))


# 🔴 WINDOWS CI, 0.0.21 cut (run 37310393789). These arrangements SIMULATE a Windows host's stdio and
# locale on POSIX (see `_hostile_arrangements`: "none of them can be arranged on POSIX" is about the
# OTHER direction). On a Windows runner the UNARRANGED run is already the hostile one — a piped stdout
# is cp1252 and `LC_ALL` does not move `getpreferredencoding` — so the two readings that need a
# FRIENDLY baseline have none to stand on. Skipped there, by name and with the reason, rather than
# asserted false; the windows matrix legs grade the real thing directly.
_NATIVE_WINDOWS = os.name == "nt"
_WINDOWS_REASON = ("a Windows host has no friendly baseline to arrange AWAY from: piped stdout is "
                   "already cp1252 and LC_ALL does not narrow the preferred encoding there")


class TheArrangementsACTUALLYBite(unittest.TestCase):
    """⛔ THE ANTI-VACUITY STEP, AND IT COMES BEFORE THE CLEAN READINGS. The failure family is the
    whole point: a typo in the variable name, a Python that ignores it, a subprocess that does not
    inherit it — **every one of those gives a clean pass over every command**, which is
    indistinguishable from a tree that survives the arrangement."""

    def test_cp1252_stdio_really_refuses_a_character_mokata_prints(self):
        env = HA.arrangement("cp1252-stdio").apply()
        probe = "import sys; sys.stdout.write('\\u2212')"
        proc = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertNotEqual(0, proc.returncode,
                            "cp1252 stdout accepted U+2212 on this host, so this leg grades "
                            "nothing")
        self.assertIn("UnicodeEncodeError", proc.stderr)

    @unittest.skipIf(_NATIVE_WINDOWS, _WINDOWS_REASON)
    def test_the_ascii_locale_really_narrows_the_DEFAULT_file_encoding(self):
        env = HA.arrangement("ascii-locale").apply()
        probe = ("import locale, sys;"
                 "sys.stdout.write(locale.getpreferredencoding(False).lower())")
        proc = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(proc.stdout.strip(), ("ansi_x3.4-1968", "ascii", "us-ascii"),
                      "the ascii-locale arrangement did not narrow the preferred encoding here "
                      f"(got {proc.stdout.strip()!r}), so its reading means nothing")

    @unittest.skipIf(_NATIVE_WINDOWS, _WINDOWS_REASON)
    def test_the_CONTROL_an_unarranged_run_accepts_the_same_character(self):
        """Without this, the two assertions above are also true of a host that refuses U+2212
        everywhere — which would make every reading below a tautology."""
        probe = "import sys; sys.stdout.write('\\u2212')"
        proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "this host refuses U+2212 even unarranged, so the arrangement is not "
                         "what the readings below measure")


class TheChildRunsTHISTreeAndNotTheInstalledPackage(unittest.TestCase):
    """⛔ THE PIN ON §7c, and it exists because this leg failed exactly this way. Everything below
    is a claim about the code in this repository; a child that imported `site-packages` would make
    every one of those claims describe somebody else's tree, and would do it SILENTLY."""

    def _resolved_by(self, env):
        probe = "import mokata, sys; sys.stdout.write(mokata.__file__)"
        proc = subprocess.run([sys.executable, "-c", probe], cwd=_REPO, env=env,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        return proc.returncode, (proc.stdout or "").strip()

    def test_every_arrangement_resolves_mokata_to_this_repos_src(self):
        for item in HA.ARRANGEMENTS:
            rc, where = self._resolved_by(_tree_env(item.apply()))
            self.assertEqual(0, rc, f"{item.key}: the child could not import mokata at all")
            self.assertTrue(
                os.path.abspath(where).startswith(_SRC + os.sep),
                f"{item.key}: the child imported mokata from {where!r}, which is NOT under "
                f"{_SRC!r}. Every reading in this module would then describe that tree instead "
                f"of this one — the §7c defect this pin exists to stop recurring.")

    def test_the_CONTROL_fires__the_pin_beats_a_competing_mokata(self):
        """A pin whose absence changes nothing is not pinning anything (§7i) — so the competitor is
        CONSTRUCTED here, not hoped for.

        ⛔ THE FIRST VERSION OF THIS CONTROL DEPENDED ON THE MACHINE, and it failed exactly where it
        matters most. It asked whether a child WITHOUT the pin resolved somewhere other than `src/`,
        which is only true on a machine with a stale non-editable install — the cut machine that
        found the defect. Inside `release.sh`'s preflight venv the package is installed EDITABLE,
        so an unpinned child resolves to `src/` too, the control "did not fire", and the release
        gate reded on a correct tree (0.0.21 cut, 2026-10-05). That is `test_b_ver_version_parity`'s
        own lesson — *the match is ARRANGED here, it used to be assumed* — arriving one file over.

        So the decoy is built: a throwaway `mokata` package on `PYTHONPATH`. Unpinned, the child must
        import the DECOY (the arrangement is real); pinned, it must import THIS tree (the pin wins).
        Both halves hold on every machine, with or without any install.
        """
        with tempfile.TemporaryDirectory() as decoy_root:
            pkg = os.path.join(decoy_root, "mokata")
            os.mkdir(pkg)
            with io.open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as fh:
                fh.write("# a decoy mokata: NOT this tree\n")
            decoy_env = dict(os.environ, PYTHONPATH=decoy_root)
            # ⚠ realpath on BOTH sides: on macOS a temp dir is reached as /var/… and resolves to
            # /private/var/…, and comparing the two spellings is the bug test_a39 shipped with.
            decoy_real = os.path.realpath(decoy_root) + os.sep

            rc, where = self._resolved_by(decoy_env)
            self.assertEqual(0, rc, "the decoy arrangement could not be imported at all")
            if not os.path.realpath(where).startswith(decoy_real):
                self.skipTest(
                    "CONTROL COULD NOT BE ARRANGED, said rather than passed over: something on this "
                    "interpreter resolves `mokata` AHEAD of PYTHONPATH (it imported %r), so no "
                    "decoy can compete. The positive pin above still ran." % where)

            rc, where = self._resolved_by(_tree_env(decoy_env))
            self.assertEqual(0, rc, "the pinned child could not import mokata")
            self.assertTrue(
                os.path.abspath(where).startswith(_SRC + os.sep),
                "with a competing mokata on PYTHONPATH, the PINNED child imported %r instead of "
                "this repo's src/ — the pin does not win, so every reading in this module could be "
                "describing another tree." % where)


class NoUserFACINGCommandDiesOnANarrowStdio(unittest.TestCase):
    """🔴 THE READING THAT FOUND TWO LIVE DEFECTS. `release-notes-check` — a gate `release.sh`
    runs — and `tour` both died with `UnicodeEncodeError` under cp1252 before this stage."""

    def _sweep(self, key):
        env = HA.arrangement(key).apply()
        dead = []
        for command in _COMMANDS:
            rc, out, err = _run(command, env)
            blob = out + err
            if any(marker in blob for marker in _UNICODE_DEATH):
                dead.append((" ".join(command), rc,
                             next(line for line in blob.splitlines()[::-1]
                                  if any(m in line for m in _UNICODE_DEATH))))
        return dead

    def test_nothing_dies_under_a_cp1252_stdout(self):
        dead = self._sweep("cp1252-stdio")
        self.assertEqual(
            [], dead,
            "%d user-facing command(s) die while PRINTING on a cp1252 stdout — which is what a "
            "GitHub Windows runner hands Python. A gate that crashes while printing its verdict "
            "has no verdict, and the crash names an encoding rather than the thing it checked. "
            "The fix is `cli._survive_a_narrow_stdio`, not a character purge:\n%s"
            % (len(dead), "\n".join("  mokata %s  (rc=%s)\n      %s" % d for d in dead)))

    def test_nothing_dies_under_an_ASCII_locale(self):
        dead = self._sweep("ascii-locale")
        self.assertEqual(
            [], dead,
            "%d user-facing command(s) die under an ASCII locale, which is STRICTER than Windows' "
            "cp1252 — so anything failing here would also fail there:\n%s"
            % (len(dead), "\n".join("  mokata %s  (rc=%s)\n      %s" % d for d in dead)))

    def test_the_FIX_is_wired_into_main_and_not_merely_defined(self):
        """§7i. `_survive_a_narrow_stdio` could be perfect and never called."""
        from mokata import cli
        with io.open(cli.__file__, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("def main(", body)
        main_at = body.index("def main(")
        self.assertIn("_survive_a_narrow_stdio()", body[main_at:main_at + 400],
                      "the narrow-stdio repair is defined but `main` does not call it")

    def test_the_fix_degrades_VISIBLY_rather_than_silently(self):
        """⚠ `backslashreplace` AND NOT `replace`. `\\u2212` tells a reader which character was
        dropped; `?` tells them nothing and is indistinguishable from a literal `?` in the text."""
        from mokata import cli
        with io.open(cli.__file__, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('reconfigure(errors="backslashreplace")', body)
        self.assertNotIn('reconfigure(errors="replace")', body)

    def test_the_fix_is_a_NO_OP_on_a_normal_stdio(self):
        """It must not change what a developer sees. A repair that alters every machine's output to
        protect one platform is a worse trade than the crash."""
        rc, out, err = _run(("version",), dict(os.environ))
        self.assertEqual(0, rc, err)
        self.assertNotIn("\\u", out, "an escape leaked into ordinary output")


class EveryTextOpenNamesItsEncoding(unittest.TestCase):
    """⛔ WHY THIS IS A STATIC RULE AND NOT LEFT TO THE `ascii-locale` ARRANGEMENT: the arrangement
    only convicts a call that actually RUNS and actually reads a non-ASCII byte. Most of these read
    files that happen to be ASCII today, so the arrangement stays silent and the defect waits for
    the first accented filename. **The rule convicts the SPELLING, which is what the author can
    see** — and `open()` with no `encoding=` uses the locale encoding, cp1252 on Windows.

    ⚠ RECURRENT, NOT HISTORICAL: 0.0.21 stage 14 alone shipped seven of these, every one invisible
    on the author's machine."""

    def test_the_tree_names_an_encoding_everywhere(self):
        sources = HA.python_sources(_REPO)
        self.assertGreater(len(sources), 300, "the corpus collapsed: %d files" % len(sources))
        offenders = HA.text_opens_without_encoding(sources)
        self.assertEqual(
            (), offenders,
            "%d text-mode `open()` call(s) name no encoding, so they read in the LOCALE encoding "
            "— cp1252 on Windows, where any non-ASCII byte raises. Pass "
            "`encoding=\"utf-8\"`:\n%s"
            % (len(offenders), "\n".join("  %s:%d" % o for o in offenders)))

    def test_the_RULE_CONVICTS_a_planted_offender(self):
        """§7i: the tree holds ZERO offenders today, measured — so a reader that walked and judged
        in one step would have nothing to be graded against and gutting it would be green."""
        planted = {"x.py": "with open('a.txt') as fh:\n    pass\n",
                   "y.py": "data = io.open('b.txt').read()\n"}
        self.assertEqual({"x.py", "y.py"},
                         {name for name, _line in HA.text_opens_without_encoding(planted)})

    def test_the_rule_does_NOT_convict_the_things_that_merely_END_in_open(self):
        """🔴 MY FIRST VERSION MATCHED ON THE ATTRIBUTE NAME AND REPORTED 14 OFFENDERS — every
        single one of them `os.open`, `webbrowser.open` or `tarfile.open`, **none of which takes an
        `encoding` at all.** A detector whose entire output is false positives is worse than none:
        it teaches the reader that this rule does not mean anything. §7g — a method named `open`
        and the text-file builtin are different facts."""
        safe = {
            "a.py": "fd = os.open(p, os.O_CREAT)\n",
            "b.py": "webbrowser.open('file://' + p)\n",
            "c.py": "src = tarfile.open(fileobj=buf)\n",
            "d.py": "with gzip.open(p) as fh:\n    pass\n",
            "e.py": "with open(p, 'rb') as fh:\n    pass\n",
            "f.py": "with open(p, mode='wb') as fh:\n    pass\n",
            "g.py": "with open(p, encoding='utf-8') as fh:\n    pass\n",
        }
        self.assertEqual((), HA.text_opens_without_encoding(safe))


if __name__ == "__main__":
    unittest.main()
