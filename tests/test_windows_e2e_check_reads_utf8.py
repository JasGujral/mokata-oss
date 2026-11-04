"""`tests/windows_e2e_check.py` — the Windows end-to-end job that hung for its whole ten minutes.

The MCP server speaks UTF-8 on stdio. The check read it with `text=True` and nothing else, which on
Windows decodes with the ANSI code page: `tools/list` (it has em dashes in its descriptions) killed
the reader thread with UnicodeDecodeError, that read as end-of-file, and the check then sat on a
server that was alive and well. The check must read what a UTF-8 server says, and when a read
fails it must say so and finish — never wait out the job's timeout.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import json
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import windows_e2e_check as W  # noqa: E402

#: A "server" that answers one request with a line holding non-ASCII text, as UTF-8 BYTES.
ECHO = ("import sys\n"
        "sys.stdin.readline()\n"
        "sys.stdout.buffer.write(('{\"id\": 1, \"result\": \"a \\u2014 b \\u00e9\"}\\n')"
        ".encode('utf-8'))\n"
        "sys.stdout.buffer.flush()\n")


class TheCheckReadsWhatAUtf8ServerSays(unittest.TestCase):
    def test_an_em_dash_in_an_answer_comes_back_intact(self):
        srv = W.Server(tempfile.gettempdir(), argv=[sys.executable, "-c", ECHO])
        self.addCleanup(srv.close)
        reply = srv.call("tools/list")
        self.assertEqual("a \u2014 b \u00e9", reply["result"])

    def test_a_server_that_exits_without_answering_is_a_named_failure_not_a_hang(self):
        srv = W.Server(tempfile.gettempdir(), argv=[sys.executable, "-c",
                       "import sys; sys.stdin.readline(); sys.stderr.write('boom\\n')"])
        self.addCleanup(srv.close)
        with self.assertRaises(W.StepFailed) as cm:
            srv.call("tools/list")
        self.assertIn("boom", str(cm.exception), "the server's stderr is in the failure")

    def test_bytes_that_are_not_utf8_on_the_protocol_channel_fail_the_check_by_name(self):
        """A server that puts invalid UTF-8 on its MCP channel is broken; reading it leniently
        (U+FFFD) would let the check pass over that. It fails, and says what was wrong."""
        bad = ("import sys\n"
               "sys.stdin.readline()\n"
               "sys.stdout.buffer.write(b'{\"id\": 1, \"result\": \"a \\xff\\xfe b\"}\\n')\n"
               "sys.stdout.buffer.flush()\n")
        srv = W.Server(tempfile.gettempdir(), argv=[sys.executable, "-c", bad])
        self.addCleanup(srv.close)
        with self.assertRaises(W.StepFailed) as cm:
            srv.call("tools/list")
        self.assertIn("UnicodeDecodeError", str(cm.exception))

    def test_invalid_bytes_on_stderr_are_shown_not_fatal(self):
        srv = W.Server(tempfile.gettempdir(), argv=[sys.executable, "-c",
                       "import sys; sys.stdin.readline(); sys.stderr.buffer.write(b'x\\xffy\\n'); "
                       "sys.stderr.flush()"])
        self.addCleanup(srv.close)
        with self.assertRaises(W.StepFailed) as cm:
            srv.call("tools/list")
        self.assertIn("x", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
