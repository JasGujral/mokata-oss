"""0.0.29 E `IDE-TS-SEARCH-FIRST-HIT-2P2S` — a rare name's first hit is on the page as soon as rg has
found it, and the pages themselves (order, offsets, the complete answer) are what they were.

Two causes, both measured on the 136k-file TypeScript stand-in (`0029-evidence/E/search-first-hit.txt`):
rg block-buffers its output into a pipe (the first hit arrived at EOF), and a lone first hit waited
for a 0.3 s batch timer. Asserted here without a wall clock: the fake rg below behaves like rg (its
output reaches the pipe only when it flushes, which it does per line only under
`--line-buffered`), and "too late" is counted in the fake rg's own scan ticks after it wrote the hit.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import search as SR
from mokata.init import init_repo
from mokata.knowledge import cancel

#: Scan ticks the fake rg makes AFTER writing the hit before the test calls the hit late. A count of
#: the tool's own progress, not a clock.
LATE_TICKS = 300

FAKE_RG = r'''#!{py}
import json, os, sys, time
argv = sys.argv[1:]
buffered = "--line-buffered" not in argv
ticks = os.environ["E_FAKE_RG_TICKS"]
line = json.dumps({{"type": "match", "data": {{"path": {{"text": "./src/early.ts"}},
    "lines": {{"text": "  getTypeOfSymbol(x)\n"}}, "line_number": 7,
    "submatches": [{{"match": {{"text": "getTypeOfSymbol"}}, "start": 2, "end": 17}}]}}}}, separators=(",", ":"))
sys.stdout.write(line + "\n")
if not buffered:
    sys.stdout.flush()
n = 0
while True:                       # "scanning the rest of the tree" until killed
    n += 1
    with open(ticks, "w") as fh:
        fh.write(str(n))
    time.sleep(0.01)
'''


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _ticks(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read() or 0)
    except (OSError, ValueError):
        return 0


@unittest.skipIf(os.name == "nt", "the fake rg is a POSIX script; the real-rg cell below runs on "
                                  "Windows (rg.exe)")
class FirstHitIsPublishedFakeRg(unittest.TestCase):

    def test_the_first_hit_is_a_partial_while_rg_still_scans(self):
        d = tempfile.mkdtemp(prefix="e-sfh-")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        _write(d, "src/early.ts", "\n" * 6 + "  getTypeOfSymbol(x)\n")
        fake = os.path.join(tempfile.mkdtemp(prefix="e-rg-"), "rg")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE_RG.format(py=sys.executable))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        ticks = os.path.join(os.path.dirname(fake), "ticks")
        flag = threading.Event()
        seen = {}

        def rep(**kw):
            p = kw.get("partial")
            if p and p.get("files") and "ticks" not in seen:
                seen["ticks"] = _ticks(ticks)
                seen["partial"] = p
                flag.set()                               # got it: Cancel kills the fake rg

        out = {}

        def run():
            with cancel.scope(flag), cancel.reporting(rep):
                try:
                    out["res"] = SR.text(d, "getTypeOfSymbol")
                except cancel.Cancelled:
                    out["res"] = "cancelled"
        with mock.patch.object(SR, "rg_path", return_value=fake), \
                mock.patch.dict(os.environ, {"E_FAKE_RG_TICKS": ticks}):
            t = threading.Thread(target=run)
            t.start()
            while t.is_alive() and not flag.is_set() and _ticks(ticks) < LATE_TICKS:
                t.join(0.01)
            late = not flag.is_set()
            flag.set()                                   # end the fake rg either way
            t.join()
        self.assertFalse(late, f"no partial after the fake rg scanned {LATE_TICKS} ticks past "
                               "the hit it wrote: the first hit waits for EOF")
        files = seen["partial"]["files"]
        self.assertEqual([f["path"] for f in files], ["src/early.ts"])
        self.assertEqual(files[0]["matches"][0]["line"], 7)
        self.assertTrue(seen["partial"].get("partial"))
        self.assertEqual(out["res"], "cancelled")


@unittest.skipUnless(SR.rg_path(), "needs rg (the floor has its own first-hit path)")
class FirstHitIsPublishedRealRg(unittest.TestCase):

    def test_a_lone_early_hit_is_a_partial_before_the_complete_answer(self):
        d = tempfile.mkdtemp(prefix="e-sfh-real-")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        _write(d, "a/early.ts", "export const x = getTypeOfSymbol(1)\n")
        for i in range(5000):
            _write(d, f"z/d{i // 100}/f{i}.ts", f"export const v{i} = {i}\n")
        events = []

        def rep(**kw):
            if kw.get("partial") and kw["partial"].get("files"):
                events.append(("partial", [f["path"] for f in kw["partial"]["files"]]))
        with cancel.reporting(rep):
            res = SR.text(d, "getTypeOfSymbol")
        events.append(("complete", [f["path"] for f in res["files"]]))
        self.assertEqual(res["engine"], "rg")
        self.assertEqual(events[0], ("partial", ["a/early.ts"]), events)
        self.assertEqual(events[-1], ("complete", ["a/early.ts"]))


def _strip_line_buffered(real):
    def popen(cmd, *a, **kw):
        return real([c for c in cmd if c != "--line-buffered"], *a, **kw)
    return popen


@unittest.skipUnless(SR.rg_path(), "needs rg")
class SameOrder(unittest.TestCase):
    """Pages 1..3 of a common word: byte-identical with and without the change's flag."""

    def test_pages_are_identical_to_the_unbuffered_answer(self):
        d = tempfile.mkdtemp(prefix="e-sfh-order-")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        for i in range(500):
            _write(d, f"p{i % 7}/q{i % 13}/f{i}.ts", "type T = 1\n" * 100)   # 50,000 hits
        pages_new, pages_old = [], []
        for pages, ctx in ((pages_new, None),
                           (pages_old, mock.patch.object(
                               SR.subprocess, "Popen", _strip_line_buffered(subprocess.Popen)))):
            if ctx:
                ctx.start()
            try:
                off = 0
                for _ in range(3):
                    res = SR.text(d, "type", offset=off)
                    pages.append(json.dumps(res, sort_keys=True))
                    off = res["next_offset"]
            finally:
                if ctx:
                    ctx.stop()
        self.assertEqual(pages_new, pages_old)
        self.assertTrue(json.loads(pages_new[2])["more"])


class TheFlagIsOnlyOnTheFirstPage(unittest.TestCase):

    def test_line_buffered_on_page_one_only(self):
        d = tempfile.mkdtemp(prefix="e-sfh-flag-")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        _write(d, "a.ts", "x\n")
        cmds = []

        class _P:
            def __init__(self, cmd, *a, **kw):
                cmds.append(list(cmd))
                raise OSError("recorded")
        with mock.patch.object(SR, "rg_path", return_value="rg"), \
                mock.patch.object(SR.subprocess, "Popen", _P):
            SR.text(d, "x")
            SR.text(d, "x", offset=200)
        self.assertIn("--line-buffered", cmds[0])
        self.assertNotIn("--line-buffered", cmds[1])
        self.assertLess(cmds[0].index("--line-buffered"), cmds[0].index("-e"))


if __name__ == "__main__":
    unittest.main()
