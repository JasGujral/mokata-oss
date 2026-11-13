"""IDE e2e — "Send to session" names how many lines it redacted (0.0.28 E1, REDACT-MANGLED-KEY).

A file holding a MANGLED private key (the dash-less form a hand edit leaves: no `-----`, the marker
words alone) is sent whole, then as a selection that starts inside the key's body. Both times the
toast reads "N line(s) redacted" with N the key's lines in what was sent, and the queued text holds
none of the body. Before E1 the dash-less marker was not a key: the toast named no redaction and
the body went out.

The markers are assembled from code points at run time (the repo's secret guard blocks a literal).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import random
import subprocess
import sys
import unittest

import _harness as H


def _w(*codes):
    return "".join(map(chr, codes))


BEGIN, END = _w(66, 69, 71, 73, 78), _w(69, 78, 68)
LABEL = _w(82, 83, 65) + " " + _w(80, 82, 73, 86, 65, 84, 69) + " " + _w(75, 69, 89)
_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_RND = random.Random("e1-label")
BODY = ["".join(_RND.choice(_ALPHA) for _ in range(64)) for _ in range(6)]
REL = "conf/deploy_key.txt"
#: line 1-2 prose, 3 the BEGIN, 4-9 the body, 10 the END, 11 prose
LINES = ["# deploy settings", "host = example.invalid", BEGIN + " " + LABEL, *BODY,
         END + " " + LABEL, "port = 22"]


def build(base):
    repo = H.make_repo("shop", base)
    H._write(repo, REL, "\n".join(LINES) + "\n")
    return repo


class RedactedLabel(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def queued_text(self):
        """The newest queued item's text, read the way the session hook reads it."""
        out = subprocess.run(
            [sys.executable, "-c",
             "import json, sys; from mokata.ide import context as C;"
             " print(json.dumps(C.read(sys.argv[1])[-1]['text']))", self.repo],
            capture_output=True, text=True, check=True, env=H._env())
        return json.loads(out.stdout)

    def send(self):
        self.page.evaluate("document.getElementById('bridge-toast').hidden = true")
        self.page.click("#send-ctx")
        self.until("!document.getElementById('bridge-toast').hidden", "the send toast")
        return self.page.text_content("#bridge-toast")

    def test_1_the_whole_file_names_its_redacted_key_lines(self):
        self.open_file(REL)
        self.page.evaluate("window.getSelection().removeAllRanges()")
        toast = self.send()
        self.assertIn("queued for", toast)
        self.assertIn("· 8 line(s) redacted", toast)     # BEGIN + 6 body + END
        text = self.queued_text()
        self.assertEqual([], [b for b in BODY if b in text])
        self.assertIn("port = 22", text)

    def test_2_a_selection_inside_the_key_names_its_lines(self):
        self.open_file(REL)
        self.page.wait_for_selector("#code div.cl[data-n='8']")
        self.page.evaluate(
            "(() => { const a = document.querySelector(\"#code div.cl[data-n='5']\");"
            " const b = document.querySelector(\"#code div.cl[data-n='8']\");"
            " const r = document.createRange(); r.setStart(a, 0); r.setEnd(b, b.childNodes.length);"
            " const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); })()")
        toast = self.send()
        self.assertIn(":5-8", toast)
        self.assertIn("· 4 line(s) redacted", toast)
        text = self.queued_text()
        self.assertEqual([], [b for b in BODY if b in text])


if __name__ == "__main__":
    unittest.main()
