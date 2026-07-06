"""Stage RT.S0 — urgent known-bug regression tests (one class per bug).

Bug 5c (major, contract): ``mokata-hook statusline`` / ``session-start`` hang FOREVER when
stdin is an OPEN non-TTY pipe with no writer — ``_read_statusline_stdin`` /
``_read_cwd_from_stdin`` guarded only ``isatty()`` then called ``sys.stdin.read()``, which
blocks indefinitely on an unclosed pipe. This violates the "hooks never block a session"
contract. The fix routes both readers through a bounded reader (thread + ``join(timeout)``,
the ``mcp_admin.handshake`` pattern) that falls back to the reader's existing default.

These tests are thread-based (no SIGALRM) so they run on the Windows CI leg. Each "hang"
case runs the reader on its own daemon thread with a bounded ``join`` so that, if the bug
is present, the test FAILS (the call is still alive) instead of hanging the whole suite.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata.harness_setup import setup_harness, unsetup_harness


# The generous bound the test allows a reader to take before we call it "hung". The
# production timeout is _STDIN_READ_TIMEOUT_SECS (2.0s); patched down per-test for speed.
_HANG_LIMIT_SECS = 5.0


class _BlockingStdin:
    """A non-TTY stdin whose ``read()`` blocks until released — mimics an OPEN pipe with no
    writer (the repro `sleep 8 | mokata-hook …`). ``release()`` unblocks the reader so the
    daemon thread exits cleanly at teardown."""

    def __init__(self):
        self._gate = threading.Event()

    def isatty(self):
        return False

    def read(self, *args):
        self._gate.wait()          # blocks forever until release()
        return ""

    def release(self):
        self._gate.set()


def _run_bounded(fn, limit=_HANG_LIMIT_SECS):
    """Run ``fn()`` on a daemon thread; return (finished, value). ``finished`` is False iff
    the call was still running after ``limit`` — i.e. it hung. Keeps a buggy reader from
    hanging the whole test process."""
    holder = {}

    def _call():
        holder["value"] = fn()

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(limit)
    return (not t.is_alive()), holder.get("value")


class TestBug5cStdinNeverHangs(unittest.TestCase):
    """5c: the hook stdin readers return their DEFAULT on an open writer-less pipe, fast."""

    def setUp(self):
        self._real_stdin = sys.stdin
        # Shrink the production timeout so the bounded-timeout path resolves quickly.
        self._real_timeout = hook_cli._STDIN_READ_TIMEOUT_SECS
        hook_cli._STDIN_READ_TIMEOUT_SECS = 0.3
        self._blocking = None

    def tearDown(self):
        if self._blocking is not None:
            self._blocking.release()   # let the daemon reader thread finish
        sys.stdin = self._real_stdin
        hook_cli._STDIN_READ_TIMEOUT_SECS = self._real_timeout

    # --- the hang cases (FAIL today: the reader never returns) ------------------------
    def test_statusline_reader_defaults_on_open_pipe(self):
        self._blocking = _BlockingStdin()
        sys.stdin = self._blocking
        finished, value = _run_bounded(hook_cli._read_statusline_stdin)
        self.assertTrue(finished, "statusline stdin read hung on an open writer-less pipe")
        self.assertEqual(value, "")   # its default

    def test_cwd_reader_defaults_on_open_pipe(self):
        self._blocking = _BlockingStdin()
        sys.stdin = self._blocking
        finished, value = _run_bounded(hook_cli._read_cwd_from_stdin)
        self.assertTrue(finished, "session-start cwd read hung on an open writer-less pipe")
        self.assertEqual(value, os.getcwd())   # its default

    # --- the normal Claude Code path (JSON payload + EOF) is unchanged -----------------
    def test_cwd_reader_still_honors_payload_cwd(self):
        sys.stdin = io.StringIO(json.dumps({"cwd": "/tmp/some/project"}))
        self.assertEqual(hook_cli._read_cwd_from_stdin(), "/tmp/some/project")

    def test_cwd_reader_defaults_on_empty_payload(self):
        sys.stdin = io.StringIO("")
        self.assertEqual(hook_cli._read_cwd_from_stdin(), os.getcwd())

    def test_statusline_reader_still_returns_raw_payload(self):
        raw = json.dumps({"workspace": {"current_dir": "/w"}, "session_name": "s"})
        sys.stdin = io.StringIO(raw)
        self.assertEqual(hook_cli._read_statusline_stdin(), raw)


class TestBug5cBoundedReader(unittest.TestCase):
    """5c: the shared bounded reader — instant at EOF, None on timeout, never raises."""

    def setUp(self):
        self._real_stdin = sys.stdin
        self._blocking = None

    def tearDown(self):
        if self._blocking is not None:
            self._blocking.release()
        sys.stdin = self._real_stdin

    def test_returns_content_at_eof(self):
        sys.stdin = io.StringIO("payload")
        self.assertEqual(hook_cli._read_stdin_bounded(1.0), "payload")

    def test_returns_none_on_timeout(self):
        self._blocking = _BlockingStdin()
        sys.stdin = self._blocking
        finished, value = _run_bounded(lambda: hook_cli._read_stdin_bounded(0.3))
        self.assertTrue(finished, "bounded reader hung instead of timing out")
        self.assertIsNone(value)


class TestBug5aRoutingMistakeFailsVisibly(unittest.TestCase):
    """5a: ``mokata-hook`` with a MISSING or UNKNOWN subcommand is a MISCONFIGURATION — it
    must fail VISIBLY with exit 1 (not the old silent exit 0 that made a mis-wired hook look
    successful to Claude Code / scripts), while keeping the helpful stderr message. It must
    NOT be exit 2 — exit 2 is mokata's reserved security-block code (only exit 2 blocks a
    PreToolUse tool call), so exit 1 still honors "hooks never block on a routing mistake".
    A VALID subcommand's exit semantics are unchanged."""

    def _run_main(self, argv):
        """Return (rc, stderr_text) from ``main(argv)`` with stderr captured."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hook_cli.main(argv)
        return rc, err.getvalue()

    def test_missing_subcommand_exits_1_with_message(self):
        rc, err = self._run_main([])
        self.assertEqual(rc, 1)                       # visible failure, not silent 0
        self.assertNotEqual(rc, 2)                    # never the security-block code
        self.assertIn("missing subcommand", err)

    def test_unknown_subcommand_exits_1_with_message(self):
        rc, err = self._run_main(["bogus-sub"])
        self.assertEqual(rc, 1)
        self.assertNotEqual(rc, 2)
        self.assertIn("unknown subcommand", err)
        self.assertIn("bogus-sub", err)               # names the offending subcommand

    def test_valid_subcommand_path_unaffected(self):
        # statusline on EOF stdin returns its normal success exit (0) — the fix must not
        # touch any real subcommand's exit semantics.
        real_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hook_cli.main(["statusline"]), 0)
        finally:
            sys.stdin = real_stdin


class TestBug5bUnsetupLeavesNoEmptyHusks(unittest.TestCase):
    """5b (cosmetic): a full ``setup claude --yes`` -> ``unsetup claude --scope project --yes``
    round-trip must be BYTE-CLEAN — when stripping mokata's entries empties a config, the file
    is DELETED, not left as an empty ``{}`` husk. Merge-safety still holds: a file that carries
    any non-mokata key is kept (non-empty), never deleted."""

    def _paths(self, d):
        return (os.path.join(d, ".claude", "settings.json"),
                os.path.join(d, ".mcp.json"))

    def test_roundtrip_removes_empty_config_files(self):
        # Fresh scratch repo: mokata creates settings.json + .mcp.json; after unsetup they
        # hold nothing but mokata's entries -> both files should be GONE (no {} husk).
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, assume_yes=True, out=lambda _: None)
            settings, mcp = self._paths(d)
            self.assertTrue(os.path.exists(settings), "setup should have written settings.json")
            self.assertTrue(os.path.exists(mcp), "setup should have written .mcp.json")

            res = unsetup_harness("claude", root=d, scope="project",
                                  assume_yes=True, out=lambda _: None)
            self.assertFalse(res.aborted)
            self.assertFalse(os.path.exists(settings),
                             "settings.json left behind as an empty husk")
            self.assertFalse(os.path.exists(mcp),
                             ".mcp.json left behind as an empty husk")

    def test_roundtrip_keeps_files_with_user_content(self):
        # Seed a USER key alongside mokata's entries in BOTH files. After unsetup the files
        # must SURVIVE and retain the user's key (we only delete when empty).
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            settings, mcp = self._paths(d)
            with open(mcp, "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"other": {"command": "other-mcp"}}}, fh)
            with open(settings, "w", encoding="utf-8") as fh:
                json.dump({"permissions": {"allow": ["Read"]}}, fh)

            setup_harness("claude", root=d, assume_yes=True, out=lambda _: None)
            unsetup_harness("claude", root=d, scope="project",
                            assume_yes=True, out=lambda _: None)

            self.assertTrue(os.path.exists(mcp), ".mcp.json wrongly deleted (had user server)")
            self.assertTrue(os.path.exists(settings),
                            "settings.json wrongly deleted (had user permissions)")
            with open(mcp, encoding="utf-8") as fh:
                self.assertEqual(list(json.load(fh)["mcpServers"]), ["other"])
            with open(settings, encoding="utf-8") as fh:
                self.assertIn("permissions", json.load(fh))


if __name__ == "__main__":
    unittest.main()
