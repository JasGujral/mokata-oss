"""Stage 1d (0.0.9) — prompt-call-site sweep: the non-interactive-stdin bug class.

Stages 1a (`read_yes_no`), 1b (`_cli_ask` / `select_execution_mode`) fixed three
reproduced crashes. This stage sweeps for the SAME bug class elsewhere — any prompt
that calls `input()` unconditionally, catches only `EOFError` (not `OSError`), or skips
`sys.stdin.isatty()` where a safe default exists. Two stragglers were found and fixed:

  * `mokata.prompt.read_approve_edit_reject` — caught only `EOFError`; on a captured /
    redirected stdin `input()` raises `OSError`, which crashed `mokata memory edit`.
  * `mokata.onboarding._default_ask` — raw `input()`, no `isatty()` guard, caught only
    `EOFError`.

Both now fail closed to their documented SAFE DEFAULT (reject / the given default),
never raise, and (for the interactive readers) skip the prompt entirely when stdin is
not a TTY. A CLI regression test drives the prompting entrypoints with a non-interactive
stdin and asserts they complete without raising.
"""

import contextlib
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.config import Surface
from mokata.memory import CONTEXT, PERSISTENT, MemoryItem, MemoryStore
from mokata.onboarding import _default_ask
from mokata.prompt import read_approve_edit_reject, read_yes_no


class _NonInteractiveStdin:
    """Emulates a captured / redirected stdin (pytest's DontReadFromInput, a closed pipe,
    `< /dev/null` under capture): not a TTY, and any read raises OSError."""

    def isatty(self):
        return False

    def readline(self, *a):
        raise OSError("reading from a non-interactive stdin")

    def read(self, *a):
        raise OSError("reading from a non-interactive stdin")

    def fileno(self):
        raise OSError("no fileno")


@contextlib.contextmanager
def noninteractive_stdin():
    orig = sys.stdin
    sys.stdin = _NonInteractiveStdin()
    try:
        yield
    finally:
        sys.stdin = orig


# =============================================================== 1. read_approve_edit_reject
class TestApproveEditRejectFailsClosedOnOSError(unittest.TestCase):
    """OSError (captured stdin) must fail closed to reject, exactly like EOF (Stage 54c
    already pins the EOF path)."""

    def test_oserror_on_first_read_is_reject(self):
        def boom(_prompt=""):
            raise OSError("captured stdin")
        r = read_approve_edit_reject("change X", "NEW", reader=boom)
        self.assertEqual(r.action, "reject")
        self.assertFalse(r.is_change)

    def test_oserror_while_reading_edited_value_is_reject(self):
        answers = iter(["e"])                       # choose edit, then the value read blows up

        def reader(_prompt=""):
            try:
                return next(answers)
            except StopIteration:
                raise OSError("captured stdin")
        r = read_approve_edit_reject("change X", "NEW", reader=reader)
        self.assertEqual(r.action, "reject")        # safe default — no change


# =============================================================== 1b. read_yes_no (Stage 3c.4)
class TestReadYesNoNonTtyNeverHangs(unittest.TestCase):
    """Stage 3c.4 (audit #4): `read_yes_no` is the primary durable-write gate, and its primary
    runtime is an agent harness that leaves stdin CONNECTED but silent. A bare `input()` there
    blocks forever. The guard must take the safe default (No) WITHOUT calling `input()` on any
    non-TTY — and never fail open to Yes."""

    def test_non_tty_returns_no_without_ever_calling_input(self):
        # An OPEN non-interactive stdin: if `input()` were called it would block forever, so we
        # make the mock raise instead — the test proves `input()` is never reached.
        with mock.patch.object(sys, "stdin", _NonInteractiveStdin()), \
             mock.patch("builtins.input",
                        side_effect=AssertionError("input() called on a non-TTY — would hang")):
            self.assertFalse(read_yes_no("proceed?", "really?"))

    def test_missing_stdin_is_non_interactive_no(self):
        # A harness may detach stdin entirely (sys.stdin is None) — still No, still no crash.
        with mock.patch.object(sys, "stdin", None):
            self.assertFalse(read_yes_no("proceed?"))

    def test_isatty_that_lies_then_fails_still_fails_closed(self):
        # Belt-and-suspenders (Stage 1a defense-in-depth): even if isatty() says True, an
        # unreadable read (OSError/EOF) must still default to No — never hang, never fail open.
        class _TTYButUnreadable(_NonInteractiveStdin):
            def isatty(self):
                return True
        with mock.patch.object(sys, "stdin", _TTYButUnreadable()), \
             mock.patch("builtins.input", side_effect=OSError("captured stdin")):
            self.assertFalse(read_yes_no("proceed?"))

    def test_interactive_tty_still_reads_yes(self):
        # The interactive path is preserved exactly: a real TTY that answers "y" → True.
        class _TTY(_NonInteractiveStdin):
            def isatty(self):
                return True
        with mock.patch.object(sys, "stdin", _TTY()), \
             mock.patch("builtins.input", return_value="y"):
            self.assertTrue(read_yes_no("proceed?"))


# =============================================================== 2. onboarding _default_ask
class TestDefaultAskFailsClosed(unittest.TestCase):
    def test_non_tty_returns_default_without_prompting(self):
        with mock.patch.object(sys, "stdin", _NonInteractiveStdin()), \
             mock.patch("builtins.input") as fake_input:
            ans = _default_ask("pick a profile", ["minimal", "standard"], "standard")
        self.assertEqual(ans, "standard")
        fake_input.assert_not_called()              # skipped the prompt — not a TTY

    def test_oserror_falls_back_to_default(self):
        # belt-and-suspenders: even if isatty() lied (True) and the read then failed.
        class _TTYButUnreadable(_NonInteractiveStdin):
            def isatty(self):
                return True
        with mock.patch.object(sys, "stdin", _TTYButUnreadable()), \
             mock.patch("builtins.input", side_effect=OSError("captured stdin")):
            ans = _default_ask("pick a profile", ["minimal", "standard"], "minimal")
        self.assertEqual(ans, "minimal")


# =============================================================== 3. CLI entrypoint regression
class TestPromptingEntrypointsWithClosedStdin(unittest.TestCase):
    """Every interactive CLI entrypoint must complete (take its safe default) when stdin is
    non-interactive — never crash with an OSError from a captured/closed stream."""

    def _seed_memory(self, d):
        from mokata.init import init_repo
        init_repo(root=d, profile="full", assume_yes=True, out=lambda _: None)
        store = MemoryStore.from_surface(Surface.load(d))
        store.remember(MemoryItem.create("tax_rate", "0.2", mtype=PERSISTENT, kind=CONTEXT),
                       assume_yes=True)
        store.close()

    def test_memory_edit_oserror_stdin_makes_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_memory(d)
            with mock.patch("builtins.input", side_effect=OSError("captured stdin")):
                rc = cli.main(["memory", "edit", "tax_rate", "--value", "0.25", "--path", d])
            self.assertEqual(rc, 0)                         # reject -> no change, no crash
            items = MemoryStore.from_surface(Surface.load(d)).backend.all()
            values = {i.value for i in items if i.subject == "tax_rate"}
            self.assertNotIn("0.25", values)                # safe default held

    def test_prompting_entrypoints_complete(self):
        # Each subcommand that can prompt, driven with a non-interactive stdin. Others
        # (session push/rename, share apply, team join, memory approve, migrate) route their
        # confirm through read_yes_no / _cli_ask, already covered by Stages 1a/1b.
        with tempfile.TemporaryDirectory() as seeded:
            self._seed_memory(seeded)
            with tempfile.TemporaryDirectory() as fresh:
                entrypoints = [
                    ["exec"],                                       # Stage 1b (_cli_ask)
                    ["init", "--path", fresh],                      # wizard gate (isatty)
                    ["memory", "edit", "tax_rate", "--value", "0.9", "--path", seeded],
                ]
                for argv in entrypoints:
                    with self.subTest(argv=argv):
                        with noninteractive_stdin():
                            try:
                                rc = cli.main(argv)
                            except (EOFError, OSError) as exc:
                                self.fail(f"{argv} raised on non-interactive stdin: {exc!r}")
                        self.assertIsInstance(rc, int)


if __name__ == "__main__":
    unittest.main()
