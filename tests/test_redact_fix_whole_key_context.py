"""0.0.27 REDACT-FIX: a private key never reaches a Claude session, whole or in part.

The context the human sends from the IDE ("send to session") is read server-side, redacted and
injected into Claude's next turn. Until this stage it was redacted LINE BY LINE: the shared scanner
flags a key's BEGIN line, and a body line wrapped narrow is too short for its entropy layer, so a
key wrapped at 24 columns leaked 5 of its 34 body lines and one wrapped at 16 leaked all 50
(reports/P4.verify.md, round 2, R2-A). Copy with Context already treated a key as one unit; the two
redactors now share that one implementation (`ide.usages.pem_lines`).

The promises a user relies on:

  * Every line of a private key, BEGIN through END, is replaced before anything is stored or sent,
    whatever its type, wrap width, line endings, quoting or comment prefix, and however the marker
    is spelled (case, spacing, ENCRYPTED, RSA, PGP BLOCK, SSH2).
  * A selection or a diff hunk that starts or ends INSIDE a key carries none of it.
  * A BEGIN with no END is a key to the end of the text (fail closed).
  * The redaction is counted: the item says how many lines were replaced.
  * Anything key-shaped left after redaction refuses the item, in both callers.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401

from test_p4_copy_context_usages import _Api, _answer

from mokata.ide import context as CX
from mokata.ide import usages as U

_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_DASH = "-" * 5
_KEY = " KEY"            # markers are assembled at run time, so this file is not itself a finding


def _markers(label, dash=_DASH, lead="", case=str):
    """(BEGIN, END) for a key type `label` ("RSA PRIVATE"), spelled with `dash`/`lead`/`case`."""
    return (case(f"{dash}{lead}BEGIN {label}{_KEY}{lead}{dash}"),
            case(f"{dash}{lead}END {label}{_KEY}{lead}{dash}"))


#: every key shape P4 covers, and the marker spellings a hand-edited or tool-written key uses
SHAPES = {
    "plain": _markers("PRIVATE"),
    "rsa": _markers("RSA PRIVATE"),
    "ec": _markers("EC PRIVATE"),
    "dsa": _markers("DSA PRIVATE"),
    "openssh": _markers("OPENSSH PRIVATE"),
    "encrypted": _markers("ENCRYPTED PRIVATE"),
    "pgp-block": (f"{_DASH}BEGIN PGP PRIVATE{_KEY} BLOCK{_DASH}",
                  f"{_DASH}END PGP PRIVATE{_KEY} BLOCK{_DASH}"),
    "lowercase": _markers("RSA PRIVATE", case=str.lower),
    "mixed-case": _markers("Encrypted Private", case=lambda s: s.replace("BEGIN", "Begin")
                           .replace("END", "End").replace("KEY", "Key")),
    "double-spaces": tuple(m.replace(" ", "  ") for m in _markers("RSA PRIVATE")),
    "spaced-dashes": _markers("RSA PRIVATE", lead=" "),
    "ssh2": _markers("SSH2 ENCRYPTED PRIVATE", dash="-" * 4, lead=" "),
}
WIDTHS = (16, 24, 64)


def _body(width, lines=34, seed=11):
    rnd = random.Random(f"{width}-{lines}-{seed}")
    return ["".join(rnd.choice(_ALPHA) for _ in range(width)) for _ in range(lines)]


def _tmp(test, prefix="mk-redact-"):
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return d


def _put(d, rel, text, newline="\n"):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)


def _git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


class _Leak(unittest.TestCase):

    def assertNoKey(self, text, body, begin, end, msg=""):
        leaked = [b for b in body if b in text]
        self.assertEqual([], leaked, f"{msg}: {len(leaked)} of {len(body)} body lines leaked")
        self.assertNotIn(begin, text, f"{msg}: the BEGIN line leaked")
        self.assertNotIn(end, text, f"{msg}: the END line leaked")


# ============================================================ session context (ide/context.py)

class AKeyInTheContextSentToASessionIsReplacedWhole(_Leak):

    def setUp(self):
        self.d = _tmp(self)

    def test_every_key_shape_at_every_width_is_replaced_and_counted(self):
        """The verifier's probe, over every shape and spelling: a key in a triple-quoted string,
        sent as the whole file. No line of it survives, every one is counted, and the code around
        it is untouched."""
        for name, (begin, end) in SHAPES.items():
            for width in WIDTHS:
                body = _body(width)
                rel = f"src/{name}_{width}.py"
                _put(self.d, rel, 'def key():\n    return """' + begin + "\n" + "\n".join(body)
                     + "\n" + end + '\n"""\n\n\nx = 1\n')
                item = CX.build_item(self.d, "file", rel)
                self.assertNoKey(item["text"], body, begin, end, f"{name} width {width}")
                self.assertEqual(len(body) + 2, item["redacted"], f"{name} width {width}")
                self.assertIn("def key():", item["text"])
                self.assertIn("x = 1", item["text"])
                self.assertIn("redacted by mokata's secret scan: private-key", item["text"])

    def test_crlf_line_endings(self):
        for width in WIDTHS:
            begin, end = SHAPES["openssh"]
            body = _body(width)
            _put(self.d, "src/crlf.py", "K = '''\n" + begin + "\n" + "\n".join(body) + "\n" + end
                 + "\n'''\ny = 2\n", newline="\r\n")
            item = CX.build_item(self.d, "file", "src/crlf.py")
            self.assertNoKey(item["text"], body, begin, end, f"CRLF width {width}")
            self.assertEqual(len(body) + 2, item["redacted"])
            self.assertIn("y = 2", item["text"])

    def test_a_key_in_concatenated_strings_or_in_comments(self):
        begin, end = SHAPES["rsa"]
        body = _body(16, lines=20)
        shapes = {
            "src/strings.py": "KEY = (\n" + "".join(f'    "{ln}\\n"\n' for ln in [begin, *body, end])
                              + ")\nz = 3\n",
            "src/hash.py": "".join(f"# {ln}\n" for ln in [begin, *body, end]) + "z = 3\n",
            "src/slash.js": "".join(f"// {ln}\n" for ln in [begin, *body, end]) + "z = 3;\n",
            "src/star.c": "/*\n" + "".join(f" * {ln}\n" for ln in [begin, *body, end])
                          + " */\nint z = 3;\n",
        }
        for rel, src in shapes.items():
            _put(self.d, rel, src)
            item = CX.build_item(self.d, "file", rel)
            self.assertNoKey(item["text"], body, begin, end, rel)
            self.assertEqual(len(body) + 2, item["redacted"], rel)
            self.assertIn("z = 3", item["text"], rel)

    def test_a_one_line_key_with_escaped_newlines_is_its_line_only(self):
        begin, end = SHAPES["plain"]
        body = _body(64, lines=6)
        _put(self.d, "cfg/key.json", '{"key": "' + begin + "\\n" + "\\n".join(body) + "\\n" + end
             + '\\n",\n "port": 5432}\n')
        item = CX.build_item(self.d, "file", "cfg/key.json")
        self.assertNoKey(item["text"], body, begin, end, "one-line key")
        self.assertEqual(1, item["redacted"])
        self.assertIn('"port": 5432', item["text"])

    def test_a_selection_that_starts_or_ends_inside_a_key_carries_none_of_it(self):
        """The BEGIN or the END (or both) is outside the selection. The body lines carry a
        comment prefix, so nothing about them LOOKS like a key: only the file around the selection
        says they are one."""
        begin, end = SHAPES["encrypted"]
        body = _body(16, lines=30)
        src = "a = 1\n" + "".join(f"# {ln}\n" for ln in [begin, *body, end]) + "b = 2\nc = 3\n"
        _put(self.d, "src/win.py", src)
        lines = src.split("\n")
        b_ln = lines.index(f"# {begin}") + 1
        e_ln = lines.index(f"# {end}") + 1
        windows = {
            "inside the body": (b_ln + 5, b_ln + 15, 11),
            "starts inside, ends after END": (b_ln + 5, e_ln + 1, e_ln - b_ln - 4),
            "starts before BEGIN, ends inside": (1, b_ln + 10, 11),
            "the END line alone": (e_ln, e_ln, 1),
            "the last body line alone": (e_ln - 1, e_ln - 1, 1),
        }
        for what, (lo, hi, n) in windows.items():
            item = CX.build_item(self.d, "selection", "src/win.py", f"{lo}-{hi}")
            self.assertNoKey(item["text"], body, begin, end, what)
            self.assertEqual(n, item["redacted"], what)
        after = CX.build_item(self.d, "selection", "src/win.py", f"{e_ln + 1}-{e_ln + 2}")
        self.assertEqual(("b = 2\nc = 3", 0), (after["text"], after["redacted"]),
                         "the lines after the END are not key material")

    def test_an_unterminated_begin_redacts_to_the_end(self):
        begin, end = SHAPES["dsa"]
        body = _body(24, lines=12)
        src = "a = 1\n" + begin + "\n" + "\n".join(body) + "\nb = 2\n"
        _put(self.d, "src/open.py", src)
        item = CX.build_item(self.d, "file", "src/open.py")
        self.assertNoKey(item["text"], body, begin, end, "unterminated")
        self.assertNotIn("b = 2", item["text"], "fail closed: everything after the BEGIN goes")
        self.assertIn("a = 1", item["text"])
        self.assertEqual(len(body) + 2, item["redacted"])
        sel = CX.build_item(self.d, "selection", "src/open.py", "5-8")
        self.assertNoKey(sel["text"], body, begin, end, "unterminated, selected inside")
        self.assertEqual(4, sel["redacted"])

    def test_the_block_claude_receives_carries_no_key_and_says_what_was_redacted(self):
        begin, end = SHAPES["rsa"]
        body = _body(16, lines=50)
        _put(self.d, "src/k.py", 'KEY = """' + begin + "\n" + "\n".join(body) + "\n" + end
             + '\n"""\n')
        q = CX.send(self.d, "selection", "src/k.py", "1-53")
        self.assertEqual(52, q["redacted"])
        block, ids = CX.pending_block(self.d, None, 100000)
        self.assertEqual(1, len(ids))
        self.assertNoKey(block, body, begin, end, "the injected block")
        self.assertIn("selection src/k.py:1-53 (52 line(s) redacted)", block)

    def test_key_material_left_after_redaction_refuses_the_item(self):
        """Defence in depth: should the whole-key pass ever miss a key, the final check still
        sees its markers (which the shared scanner alone does not, for a lowercase one)."""
        begin, end = SHAPES["lowercase"]
        body = _body(16, lines=8)
        _put(self.d, "src/lc.py", begin + "\n" + "\n".join(body) + "\n" + end + "\n")
        with mock.patch.object(U, "pem_lines", lambda lines, *a, **k: iter(lines)):
            with self.assertRaises(CX.ContextRefused):
                CX.build_item(self.d, "file", "src/lc.py")


@unittest.skipUnless(shutil.which("git"), "git is needed for a live diff")
class AKeyInADiffSentToASessionIsReplacedWhole(_Leak):

    def setUp(self):
        self.d = _tmp(self)
        _git(self.d, "init", "-q")
        self.begin, self.end = SHAPES["openssh"]
        self.body = _body(16, lines=40)
        self.src = "a = 1\nK = '''\n" + self.begin + "\n" + "\n".join(self.body) + "\n" + \
                   self.end + "\n'''\nb = 2\n"
        _put(self.d, "src/k.py", self.src)
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "k")

    def test_a_hunk_inside_a_key_carries_none_of_it(self):
        """One body line changed in the middle: the hunk holds neither marker, only body lines
        on both sides."""
        new_body = list(self.body)
        new_body[20] = _body(16, lines=1, seed=99)[0]
        _put(self.d, "src/k.py", self.src.replace(self.body[20], new_body[20]))
        item = CX.build_item(self.d, "diff", "src/k.py")
        self.assertNoKey(item["text"], self.body + new_body, self.begin, self.end, "mid-key hunk")
        self.assertEqual(8, item["redacted"], "3 context lines either side, one removed, one added")
        header = [ln for ln in item["text"].split("\n") if ln.startswith("@@")]
        self.assertEqual(1, len(header))
        self.assertRegex(header[0], r"^@@ [^@]* @@$",
                         "git's function context is the nearest line above that starts with a "
                         "letter: here a key body line, so it is dropped")

    def test_a_removed_key_carries_none_of_it(self):
        _put(self.d, "src/k.py", "a = 1\nb = 2\n")
        item = CX.build_item(self.d, "diff", "src/k.py")
        self.assertNoKey(item["text"], self.body, self.begin, self.end, "removed key")
        self.assertIn("\n a = 1", item["text"])
        self.assertIn("\n-K = '''", item["text"])
        self.assertEqual(len(self.body) + 2, item["redacted"])

    def test_an_added_key_in_a_new_file_carries_none_of_it(self):
        body = _body(24, lines=10, seed=5)
        _put(self.d, "src/new.py", "x = 1\n" + self.begin + "\n" + "\n".join(body) + "\n"
             + self.end + "\ny = 2\n")
        item = CX.build_item(self.d, "diff", "src/new.py")
        self.assertNoKey(item["text"], body, self.begin, self.end, "added key")
        self.assertIn("+y = 2", item["text"])
        self.assertEqual(len(body) + 2, item["redacted"])


# ============================================================ Copy with Context (ide/usages.py)

class EveryKeySpellingIsOneUnitInCopyWithContext(_Leak):
    """R2-C: the marker pins. Narrowing the key marker, or dropping the markers from the residual
    check, must go RED here, not just in a probe."""

    def setUp(self):
        self.d = _tmp(self, "mk-redact-u-")

    def _scan(self, text):
        src = os.path.join(self.d, "raw")
        with open(src, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        w = U.SpillWriter(self.d, "full")
        self.addCleanup(w.discard)
        got = U.scan_spill(src, w, "x.py")
        with open(w.path, encoding="utf-8", newline="") as fh:
            return got, fh.read()

    def test_every_shape_whole_and_unterminated(self):
        for name, (begin, end) in SHAPES.items():
            body = _body(16, lines=12)
            got, out = self._scan("a\n" + begin + "\n" + "\n".join(body) + "\n" + end + "\nb\n")
            self.assertNoKey(out, body, begin, end, name)
            self.assertEqual(len(body) + 2, got["replaced"], name)
            self.assertTrue(out.startswith("a\n") and out.endswith("b\n"), name)
            got, out = self._scan("a\n" + begin + "\n" + "\n".join(body) + "\nnot base64 here\n")
            self.assertNoKey(out, body, begin, end, f"{name} unterminated")
            self.assertNotIn("not base64 here", out, f"{name} unterminated")

    def test_every_shape_marks_the_lines_below_it_as_inside(self):
        for name, (begin, end) in SHAPES.items():
            body = _body(16, lines=6)
            rel = f"{name}.txt"
            _put(self.d, rel, "a\n" + begin + "\n" + "\n".join(body) + "\n" + end + "\nb\n")
            self.assertTrue(U.pem_state_before(self.d, rel, 5), name)
            self.assertFalse(U.pem_state_before(self.d, rel, 10), name)
            self.assertFalse(U.pem_state_before(self.d, rel, 2), name)

    def test_a_lone_marker_of_any_shape_refuses_the_assembled_block(self):
        for name, (begin, end) in SHAPES.items():
            for marker in (begin, end):
                path = os.path.join(self.d, "assembled")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("x\nsee " + marker + " here\ny\n")
                with self.assertRaises(U.RefRefused, msg=f"{name}: {marker}"):
                    U.verify_assembled(path, "x.py")

    def test_the_end_of_one_key_and_the_begin_of_the_next_on_one_line(self):
        b1, e1 = SHAPES["rsa"]
        b2, e2 = SHAPES["ec"]
        body1, body2 = _body(16, lines=5, seed=1), _body(16, lines=5, seed=2)
        got, out = self._scan(b1 + "\n" + "\n".join(body1) + "\n" + e1 + " " + b2 + "\n"
                              + "\n".join(body2) + "\nx = (1)\n")
        self.assertNoKey(out, body1 + body2, b2, e2, "the second key, unterminated")
        self.assertEqual(len(body1) + len(body2) + 3, got["replaced"])

    def test_a_key_closed_and_the_next_opened_on_the_line_after_a_headless_body(self):
        """The text starts inside a key (its BEGIN is not in it): its body is held back, and the
        line that ENDs it and BEGINs the next releases it as key material, not as text."""
        _b1, e1 = SHAPES["rsa"]
        b2, e2 = SHAPES["ec"]
        body1, body2 = _body(16, lines=5, seed=3), _body(16, lines=5, seed=4)
        got, out = self._scan("\n".join(body1) + "\n" + e1 + " " + b2 + "\n"
                              + "\n".join(body2) + "\n" + e2 + "\nafter = (1)\n")
        self.assertNoKey(out, body1 + body2, b2, e2, "headless body, then a second key")
        self.assertEqual(len(body1) + len(body2) + 2, got["replaced"])
        self.assertIn("after = (1)", out)

    def test_a_one_line_key_then_the_begin_of_the_next_on_one_line(self):
        b1, e1 = SHAPES["dsa"]
        b2, e2 = SHAPES["openssh"]
        body = _body(16, lines=5, seed=6)
        got, out = self._scan(b1 + " QUJD " + e1 + " " + b2 + "\n" + "\n".join(body)
                              + "\nx = (1)\n")
        self.assertNoKey(out, body, b2, e2, "the second key, opened on the first key's line")
        self.assertEqual(len(body) + 2, got["replaced"], "unterminated: to the end")

    def test_a_selection_starting_inside_a_lowercase_key_copies_none_of_it(self):
        """Through the route the page calls, for a spelling the shared scanner does not flag."""
        api = _Api("run")
        api.setUp()
        self.addCleanup(api.doCleanups)
        begin, end = SHAPES["lowercase"]
        body = _body(16, lines=30)
        src = "K = '''\n" + begin + "\n" + "\n".join(body) + "\n" + end + "\n'''\n"
        _put(api.d, "src/app/lk.py", src)
        head, _j = _answer(api.c, "/api/copy-context", {"path": "src/app/lk.py", "lo": 6,
                                                        "hi": 20})
        out, off = "", 0
        while off is not None:
            r = api.c.get("/api/copy-context/text", params={"id": head["id"], "shape": "full",
                                                            "offset": off}).json()
            out += r["text"]
            off = r["next"]
        self.assertNoKey(out, body, begin, end, "selection inside a lowercase key")


# ===================================== round 2: markers glued on one line (REDACT-FIX.verify M1)
# `cat a b` with no newline at the end of `a`: key A's END and key B's BEGIN share one line with
# no separator. The state machine must see every marker on a line, however they abut.

def _glued(n_keys, unterminated_last=False, width=16, lines=12):
    """`n_keys` keys, each END glued to the next BEGIN, then `x = (1)`. Returns (text, bodies,
    markers). With `unterminated_last`, the last key has no END."""
    names = ["rsa", "ec", "openssh", "lowercase", "pgp-block"]
    bodies = [_body(width, lines=lines, seed=40 + i) for i in range(n_keys)]
    marks = [SHAPES[names[i % len(names)]] for i in range(n_keys)]
    out = marks[0][0] + "\n"
    for i in range(n_keys):
        out += "\n".join(bodies[i]) + "\n"
        last = i == n_keys - 1
        if last:
            out += "" if unterminated_last else marks[i][1] + "\n"
        else:
            out += marks[i][1] + marks[i + 1][0] + "\n"
    return "a = 0\n" + out + "x = (1)\n", bodies, marks


class _GluedCases(_Leak):

    def assertNoBodies(self, text, bodies, marks, msg):
        for i, (body, (begin, end)) in enumerate(zip(bodies, marks)):
            self.assertNoKey(text, body, begin, end, f"{msg}, key {i + 1}")


class GluedMarkersInTheSessionContext(_GluedCases):

    def setUp(self):
        self.d = _tmp(self)

    def test_glued_end_and_begin(self):
        src, bodies, marks = _glued(2)
        _put(self.d, "src/g.py", src)
        item = CX.build_item(self.d, "file", "src/g.py")
        self.assertNoBodies(item["text"], bodies, marks, "glued, terminated")
        self.assertEqual(sum(map(len, bodies)) + 3, item["redacted"])
        self.assertIn("x = (1)", item["text"])

    def test_glued_with_the_second_key_unterminated(self):
        src, bodies, marks = _glued(2, unterminated_last=True)
        _put(self.d, "src/g.py", src)
        item = CX.build_item(self.d, "file", "src/g.py")
        self.assertNoBodies(item["text"], bodies, marks, "glued, second unterminated")
        self.assertNotIn("x = (1)", item["text"], "unterminated: to the end")

    def test_a_window_ending_inside_the_second_key(self):
        src, bodies, marks = _glued(2)
        _put(self.d, "src/g.py", src)
        lines = src.split("\n")
        glue = next(i for i, ln in enumerate(lines, 1) if marks[1][0] in ln)
        for lo in (1, 3, glue):
            item = CX.build_item(self.d, "selection", "src/g.py", f"{lo}-{glue + 6}")
            self.assertNoBodies(item["text"], bodies, marks, f"window {lo}-{glue + 6}")
            self.assertEqual(glue + 6 - max(lo, 2) + 1, item["redacted"], lo)

    def test_three_keys_glued_in_a_row(self):
        for unterminated in (False, True):
            src, bodies, marks = _glued(3, unterminated_last=unterminated)
            _put(self.d, "src/g3.py", src)
            item = CX.build_item(self.d, "file", "src/g3.py")
            self.assertNoBodies(item["text"], bodies, marks, f"three, unterminated={unterminated}")

    def test_an_orphan_end_glued_to_a_begin_with_the_next_key_open(self):
        """The text starts inside a key whose BEGIN it does not hold, and the line that ENDs it
        BEGINs another, which is never closed."""
        b2_body = _body(16, lines=9, seed=77)
        b1_body = _body(16, lines=9, seed=78)
        _e1 = SHAPES["rsa"][1]
        b2 = SHAPES["ec"][0]
        for sep in ("", " "):
            _put(self.d, "src/o.py", "\n".join(b1_body) + "\n" + _e1 + sep + b2 + "\n"
                 + "\n".join(b2_body) + "\nx = (1)\n")
            item = CX.build_item(self.d, "file", "src/o.py")
            self.assertNoKey(item["text"], b1_body + b2_body, b2, _e1, f"sep={sep!r}")


class GluedMarkersInCopyWithContext(_GluedCases):

    def setUp(self):
        self.d = _tmp(self, "mk-redact-g-")

    def _scan(self, text):
        return EveryKeySpellingIsOneUnitInCopyWithContext._scan(self, text)

    def test_glued_end_and_begin(self):
        src, bodies, marks = _glued(2)
        got, out = self._scan(src)
        self.assertNoBodies(out, bodies, marks, "glued, terminated")
        self.assertEqual(sum(map(len, bodies)) + 3, got["replaced"])

    def test_glued_with_the_second_key_unterminated(self):
        src, bodies, marks = _glued(2, unterminated_last=True)
        got, out = self._scan(src)
        self.assertNoBodies(out, bodies, marks, "glued, second unterminated")
        self.assertNotIn("x = (1)", out)

    def test_glued_short_dash_runs(self):
        """The pattern accepts runs of three or more dashes, so a 4-dash END glued to a 3-dash
        BEGIN (7 dashes in all) must still leave the BEGIN its three."""
        e1 = _markers("SSH2 ENCRYPTED PRIVATE", dash="-" * 4)[1]
        b2, e2 = _markers("RSA PRIVATE", dash="-" * 3)
        body1, body2 = _body(16, lines=6, seed=90), _body(16, lines=6, seed=91)
        text = "\n".join(body1) + "\n" + e1 + b2 + "\n" + "\n".join(body2) + "\nx = (1)\n"
        _got, out = self._scan(text)
        self.assertNoKey(out, body1 + body2, b2, e2, "4+3 dashes glued")
        d = _tmp(self)
        _put(d, "src/s.py", text)
        item = CX.build_item(d, "file", "src/s.py")
        self.assertNoKey(item["text"], body1 + body2, b2, e2, "4+3 dashes glued, context")

    def test_three_keys_glued_in_a_row(self):
        for unterminated in (False, True):
            src, bodies, marks = _glued(3, unterminated_last=unterminated)
            _got, out = self._scan(src)
            self.assertNoBodies(out, bodies, marks, f"three, unterminated={unterminated}")

    def test_the_state_below_a_glued_line_is_inside(self):
        src, _bodies, marks = _glued(3)
        _put(self.d, "g.txt", src)
        lines = src.split("\n")
        for i, ln in enumerate(lines, 1):
            if marks[1][0] in ln or marks[2][0] in ln:
                self.assertTrue(U.pem_state_before(self.d, "g.txt", i + 1), i)

    def test_an_orphan_end_glued_to_a_begin_with_the_next_key_open(self):
        b1_body = _body(16, lines=9, seed=78)
        b2_body = _body(16, lines=9, seed=77)
        e1, b2 = SHAPES["rsa"][1], SHAPES["ec"][0]
        for sep in ("", " "):
            got, out = self._scan("\n".join(b1_body) + "\n" + e1 + sep + b2 + "\n"
                                  + "\n".join(b2_body) + "\nx = (1)\n")
            self.assertNoKey(out, b1_body + b2_body, b2, e1, f"sep={sep!r}")
            self.assertEqual(len(b1_body) + len(b2_body) + 2, got["replaced"], sep)

    def test_a_window_ending_inside_the_second_key_through_the_route(self):
        api = _Api("run")
        api.setUp()
        self.addCleanup(api.doCleanups)
        src, bodies, marks = _glued(2, width=16, lines=30)
        _put(api.d, "src/app/glued.py", src)
        glue = next(i for i, ln in enumerate(src.split("\n"), 1) if marks[1][0] in ln)
        for lo in (1, glue + 2):
            head, _j = _answer(api.c, "/api/copy-context", {"path": "src/app/glued.py",
                                                            "lo": lo, "hi": glue + 10})
            out, off = "", 0
            while off is not None:
                r = api.c.get("/api/copy-context/text", params={
                    "id": head["id"], "shape": "full", "offset": off}).json()
                out += r["text"]
                off = r["next"]
            self.assertNoBodies(out, bodies, marks, f"route window from {lo}")


class TheKeyPassStreamsAtScale(unittest.TestCase):
    """A key spanning a huge file is handled by streaming: no cap, no whole-file read, memory
    flat. (Timings are on a shared machine; the bound is generous and only catches a quadratic.)"""

    def test_two_million_lines_inside_one_key(self):
        begin, end = SHAPES["rsa"]
        n = 2_000_000
        counts = {}

        def lines():
            yield "a\n"
            yield begin + "\n"
            for i in range(n):
                yield f"QUJD{i:012d}\n"
            yield "tail\n"
        t = time.monotonic()
        survived = sum(1 for ln in U.pem_lines(lines(), counts, "x") if ln.startswith("QUJD"))
        self.assertEqual(0, survived)
        self.assertEqual(n + 2, counts["replaced"], "BEGIN, every body line and the tail")
        self.assertLess(time.monotonic() - t, 60)

    def test_a_long_run_of_dashes_is_one_linear_try(self):
        """A marker starts with dashes. Tried at every dash of a 60k-dash line (a minified file, a
        rule drawn wide), the marker search is quadratic: ~16 s measured, ~1 ms anchored."""
        d = _tmp(self)
        line = "-" * 60_000
        _put(d, "src/rule.txt", "a\n" + line + "\nb\n")
        t = time.monotonic()
        item = CX.build_item(d, "file", "src/rule.txt")
        self.assertEqual(0, item["redacted"])
        self.assertFalse(U.pem_state_before(d, "src/rule.txt", 3))
        self.assertLess(time.monotonic() - t, 3.0)

    def test_a_marker_split_by_the_read_block_is_still_seen(self):
        """`pem_state_before` reads a long line in blocks: a BEGIN that straddles two blocks is
        still the start of a key, and a one-line key that straddles them is still closed."""
        begin, end = SHAPES["rsa"]
        d = _tmp(self)
        _put(d, "a.txt", "x\n" + "y" * 10 + " " + begin + "\nQUJDQUJD\nQUJDQUJD\nz\n")
        _put(d, "b.txt", "x\n" + "y" * 10 + " " + begin + " QUJD " + end + "\nQUJD\n")
        for block in (5, 7, 16, 33):
            with mock.patch.object(U, "_block", lambda b=block: b):
                self.assertTrue(U.pem_state_before(d, "a.txt", 4), block)
                self.assertFalse(U.pem_state_before(d, "b.txt", 3), block)

    def test_inside_state_a_million_lines_below_the_begin(self):
        begin, _end = SHAPES["openssh"]
        d = tempfile.mkdtemp(prefix="mk-redact-big-")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "big.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(begin + "\n")
            for i in range(1_000_000):
                fh.write(f"QUJD{i:012d}\n")
        self.assertTrue(U.pem_state_before(d, "big.txt", 1_000_001))


if __name__ == "__main__":
    unittest.main()
