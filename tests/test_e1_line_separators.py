"""0.0.28 E1 (REDACT-MANGLED-KEY) — line separators, and `ground` fails closed.

`ground.redact` used to split with `text.split("\\n")` while the scanner numbered lines with
`str.splitlines`, which also breaks on CR, VT, FF, FS, GS, RS, NEL, U+2028 and U+2029: a separator
above a secret made the WRONG line replaced, the secret went out and the count read 1 (refute H2,
probed 6/6). Both redactors are now thin callers of `usages.redact_text`, which splits with the
scanner's own `splitlines(keepends=True)` and re-joins each line with its own separator.

Rows: E1.LineSeparators, E1.GroundFailClosed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import unittest
from unittest import mock

import _support  # noqa: F401

import _e1_keys as K

from mokata.explain import ground as G
from mokata.ide import context as CX
from mokata.ide import usages as U

SEPARATORS = {"LF": "\n", "CRLF": "\r\n", "CR": "\r", "VT": "\v", "FF": "\f", "FS": "\x1c",
              "GS": "\x1d", "RS": "\x1e", "NEL": "\x85", "LS": " ", "PS": " "}
#: a token-SHAPED string (the signature layer's github-token shape), low in entropy on purpose
TOKEN = K.w(103, 104, 112, 95) + K.w(65, 98) * 12


def _secrets():
    """(name, lines, secret strings) — a strict key, a mangled key, a one-line token."""
    sb, se = K.pair()
    mb, me = K.pair(K.RSA, lead="", trail="")
    body = K.random_body(16, 5, "sep")
    return [("strict-key", [sb, *body, se], body + [sb]),
            ("mangled-key", [mb, *body, me], body + [mb]),
            ("token", ["token = " + TOKEN], [TOKEN])]


class LineSeparators(unittest.TestCase):
    """E1.LineSeparators — each of the 11 separators above a strict key, a mangled key and a
    one-line token: 0 survivors, the count right, `ground` and `context` agree, the line count
    and every separator kept."""

    def test_every_separator_above_every_secret(self):
        for sname, sep in SEPARATORS.items():
            for name, lines, secrets in _secrets():
                # every line ends in THIS separator — the secret lines too, so a replacement
                # that re-joins with a plain LF is seen
                text = sep.join(["head", "above", *lines, "after"]) + sep
                c_out, c_n = CX._redact(text, "x.txt")
                g_out, g_n = G.redact(text)
                for out, who in ((c_out, "context"), (g_out, "ground")):
                    self.assertEqual([], [s for s in secrets if s in out], f"{sname}/{name}/{who}")
                    self.assertEqual(len(text.splitlines()), len(out.splitlines()),
                                     f"{sname}/{name}/{who}: line count")
                    self.assertEqual(text.count(sep), out.count(sep), f"{sname}/{name}/{who}")
                self.assertEqual(len(lines), c_n, f"{sname}/{name}: count")
                self.assertEqual((c_out, c_n), (g_out, g_n), f"{sname}/{name}: disagree")

    def test_the_head_and_tail_survive_untouched(self):
        text = "keep me " + K.marker(K.BEGIN) + "\nAAAA\n" + K.marker(K.END) + "\x85tail\n"
        out, n = U.redact_text(text, "x.txt")
        self.assertTrue(out.startswith("keep me "))
        self.assertTrue(out.endswith("\x85tail\n"))
        self.assertEqual(3, n)


class GroundFailClosed(unittest.TestCase):
    """E1.GroundFailClosed — `ground.redact` re-checks its own output: a residual replaces every
    line (fail closed), keeping the line count so cites still resolve."""

    def test_a_residual_replaces_every_line(self):
        begin, end = K.pair(K.RSA, lead="", trail="")
        body = K.random_body(16, 4)
        text = "\n".join(["a = 1", begin, *body, end, "b = 2"]) + "\n"
        with mock.patch.object(U, "pem_lines", lambda lines, *a, **k: iter(lines)):
            out, n = G.redact(text)
        self.assertEqual(len(text.splitlines()), n)
        self.assertEqual([], [b for b in body if b in out])
        self.assertNotIn("a = 1", out)
        self.assertEqual(len(text.splitlines()), len(out.splitlines()))

    def test_context_refuses_on_the_same_residual(self):
        begin, end = K.pair(K.RSA, lead="", trail="")
        text = "\n".join([begin, *K.random_body(16, 4), end]) + "\n"
        with mock.patch.object(U, "pem_lines", lambda lines, *a, **k: iter(lines)):
            with self.assertRaises(CX.ContextRefused):
                CX._redact(text, "x.txt")

    def test_a_residual_check_runs_on_the_output(self):
        """With the replacement made to miss a TOKEN line, the output check still sees it and
        `ground` replaces every line."""
        text = "a\ntoken = " + TOKEN + "\nb\n"
        real = U._replace
        with mock.patch.object(U, "_replace", lambda line, kind, mark=U.REPLACED: line
                               if TOKEN in line else real(line, kind, mark)):
            out, n = G.redact(text)
        self.assertNotIn(TOKEN, out)
        self.assertEqual(3, n)


if __name__ == "__main__":
    unittest.main()
