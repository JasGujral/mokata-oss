"""0.0.28 E1 (REDACT-MANGLED-KEY) — scale is a gate, not a note (E1.Scale, E1.NoSlowdown).

A literal transcription of the tolerant marker grammar in `re` is quadratic (refute M1: a dash run,
a space run, `BEGIN` + blanks, blanks + `BEGIN` grew 4x per 2x of input). The detector is a hand
scanner with O(1) bounded work per keyword, so every cell here is graded by a RATIO — time(2N) /
time(N) < 2.5 — never by a wall clock, and the 200 MB single-line cell by peak RSS against the
same walk over a one-window line.

Env-gated: `MOKATA_E1_SCALE=1` (run on the lane and at the cut). Without it every cell SKIPS and
is recorded not-run — never green.

E1.NoSlowdown: lines/s of `pem_lines` over a strict-key corpus (PEM files: keys of every type word,
64-wide bodies) within 1.3x of master's `pem_lines`, which is copied below as the baseline (its
marker words assembled at run time).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401

import _e1_keys as K

from mokata.govern import secrets as S
from mokata.ide import usages as U

GATED = os.environ.get("MOKATA_E1_SCALE") != "1"
WHY = "E1.Scale is env-gated: set MOKATA_E1_SCALE=1 (recorded not-run)"
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
NL = chr(10)


def _best(fn, repeat=3):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


def _corpus(n, wrap=False):
    """`n` lines of strict keys (every type word), lazily."""
    i = 0
    made = 0
    bodies = [[x + NL for x in K.random_body(64, 25, str(j))] for j in range(7)]
    while made < n:
        b, e = K.pair(K.TYPE_LABELS[i % len(K.TYPE_LABELS)])
        chunk = ((["def f():" + NL, "    return '''" + NL] if wrap else [])
                 + [b + NL] + bodies[i % 7] + [e + NL]
                 + (["'''" + NL, "x = 1" + NL] if wrap else [NL]))
        for ln in chunk:
            if made >= n:
                return
            made += 1
            yield ln
        i += 1


# ------------------------------------------------------- master e4e1463 `pem_lines` (baseline)
_W = {"b": K.BEGIN, "e": K.END, "p": K.PRIVATE, "k": K.KEY}
_BASE = (r"-{{3}}[ \t]*{word}[ \t]+(?:[A-Z0-9]+[ \t]+)*" + _W["p"] + r"[ \t]+" + _W["k"]
         + r"(?:[ \t]+BLOCK)?[ \t]*-{{3}}")
_B_BEGIN = re.compile(_BASE.format(word=_W["b"]), re.IGNORECASE)
_B_END = re.compile(_BASE.format(word=_W["e"]), re.IGNORECASE)
_B_BODY = re.compile(r"[A-Za-z0-9+/]{4,}={0,2}")


def _base_b64(line):
    t = line.strip().replace("\\n", "").strip("\"'`,+;() ").strip()
    return bool(t) and _B_BODY.fullmatch(t) is not None


def _base_walk(inside, text):
    pos = 0
    while True:
        m = (_B_END if inside else _B_BEGIN).search(text, pos)
        if m is None:
            return inside
        inside = not inside
        pos = m.end()


def base_pem_lines(lines, counts, inside=False, hold=4096):
    pending = []

    def rep(line):
        counts["replaced"] = counts.get("replaced", 0) + 1
        return "X" + line[len(line.rstrip("\r\n")):]
    for ln in lines:
        body = ln.rstrip("\r\n")
        if inside:
            yield rep(ln)
            inside = _base_walk(True, body)
            continue
        b = _B_BEGIN.search(body)
        e = _B_END.search(body)
        if e and (b is None or e.start() < b.start()):
            for p in pending:
                yield rep(p)
            pending = []
            yield rep(ln)
            inside = _base_walk(False, body[e.end():])
            continue
        if b:
            yield from pending
            pending = []
            yield rep(ln)
            inside = _base_walk(False, body)
            continue
        if _base_b64(body):
            pending.append(ln)
            if len(pending) > hold:
                yield from pending
                pending = []
            continue
        yield from pending
        pending = []
        yield ln
    yield from pending


def _drain(gen):
    for _ in gen:
        pass


def _record(cell, **kw):
    """One line per cell on stderr: the evidence log greps these."""
    sys.stderr.write("E1.SCALE " + json.dumps({"cell": cell, **kw}) + NL)


def _select(case):
    """`MOKATA_E1_SCALE_CELLS=a,b` runs only the cells whose names hold `a` or `b` (a mutant's
    run of one cell); unset, every cell runs."""
    only = os.environ.get("MOKATA_E1_SCALE_CELLS")
    if only and not any(c in case._testMethodName for c in only.split(",")):
        case.skipTest("not selected by MOKATA_E1_SCALE_CELLS")


@unittest.skipIf(GATED, WHY)
class NoSlowdown(unittest.TestCase):

    def setUp(self):
        _select(self)

    def test_strict_corpus_within_1_3x_of_master(self):
        n = int(os.environ.get("MOKATA_E1_NOSLOW_LINES", "1000000"))
        lines = list(_corpus(n))
        base = _best(lambda: _drain(base_pem_lines(lines, {})), repeat=5)
        new = _best(lambda: _drain(U.pem_lines(lines, {}, "x")), repeat=5)
        c1, c2 = {}, {}
        _drain(base_pem_lines(lines, c1))
        _drain(U.pem_lines(lines, c2, "x"))
        self.assertEqual(c1.get("replaced"), c2.get("replaced"), "the two disagree on the corpus")
        wrapped = list(_corpus(n // 4, wrap=True))
        wb = _best(lambda: _drain(base_pem_lines(wrapped, {})))
        wn = _best(lambda: _drain(U.pem_lines(wrapped, {}, "x")))
        _record("NoSlowdown", lines=n, base_s=round(base, 3), new_s=round(new, 3),
                ratio=round(new / base, 3), lines_per_s=int(n / new),
                wrapped_lines=n // 4, wrapped_ratio=round(wn / wb, 3))
        self.assertLess(new / base, 1.3)


@unittest.skipIf(GATED, WHY)
class Scale(unittest.TestCase):

    def setUp(self):
        _select(self)

    def test_line_count_is_linear(self):
        """1M -> 2M lines (ratio < 2.5), and 2M -> 7M lines (ratio < 3.5 x 1.25)."""
        t = {}
        for n in (1_000_000, 2_000_000, 7_000_000):
            t[n] = _best(lambda: _drain(U.pem_lines(_corpus(n), {}, "x")), repeat=1 if n > 2e6 else 3)
        _record("lines", seconds={str(k): round(v, 3) for k, v in t.items()},
                r_1m_2m=round(t[2_000_000] / t[1_000_000], 3),
                r_2m_7m=round(t[7_000_000] / t[2_000_000], 3))
        self.assertLess(t[2_000_000] / t[1_000_000], 2.5)
        self.assertLess(t[7_000_000] / t[2_000_000], 3.5 * 1.25)

    def test_whole_text_scan_is_linear_in_lines(self):
        texts = {n: "".join(_corpus(n)) for n in (250_000, 500_000)}
        t = {n: _best(lambda: S._scan_key_material(texts[n])) for n in texts}
        _record("scan-lines", r=round(t[500_000] / t[250_000], 3))
        self.assertLess(t[500_000] / t[250_000], 2.5)

    PATHOLOGICAL = {
        "spaces": lambda n: " " * n,
        "tabs": lambda n: "\t" * n,
        "begin-then-blanks": lambda n: K.BEGIN + " " * n,
        "blanks-then-begin": lambda n: " " * n + K.BEGIN,
        "dash-space": lambda n: "- " * (n // 2),
        "dashes": lambda n: "-" * n,
        "dash-begin-repeats": lambda n: ("---" + K.BEGIN + " ") * (n // 9),
        "end-repeats": lambda n: (K.END.lower() + " ") * (n // 4),
        # dash-less upper-case keywords: every one is parsed (look-back AND look-forward)
        "begin-repeats": lambda n: (K.BEGIN + " ") * (n // 6),
        # … with a PRIVATE at the end, so the pre-filter lets every keyword through to the parse,
        # whose look-forward then sees a word run as long as the line
        "begin-repeats-private": lambda n: (K.BEGIN + " ") * (n // 6) + K.PRIVATE,
        "dashes-begin-words": lambda n: ("-" * 64 + K.BEGIN + " " + K.RSA + " ") * (n // 74),
        "unicode-dash-begin": lambda n: ("———" + K.BEGIN + " ") * (n // 9),
        "zero-width": lambda n: ("B​EGIN​ ") * (n // 9),
        "quotes-junctions": lambda n: ('"' + K.BEGIN + ' " + "') * (n // 12),
    }

    def test_every_pathological_line_is_linear(self):
        n = int(os.environ.get("MOKATA_E1_PATH_N", "200000"))
        for name, make in self.PATHOLOGICAL.items():
            small, big = make(n), make(2 * n)
            ts = _best(lambda: (S.key_markers(small), U.redact_text(small, "x")), repeat=5)
            tb = _best(lambda: (S.key_markers(big), U.redact_text(big, "x")), repeat=5)
            _record("line", name=name, n=len(small), small_s=round(ts, 4), big_s=round(tb, 4),
                    ratio=round(tb / ts, 3))
            self.assertLess(tb / ts, 2.5, name)

    def test_seeded_windows_in_900k_lines(self):
        """E1.Windows at scale: a 900k-line file holding mangled and unmarked keys at seeded
        places; Copy with Context of a window that STARTS inside each key (its marker or DER head
        above it) carries none of the key. (The session-context site refuses a file this size
        whole — `_read_text`'s cap — so the streamed copy path is the site that meets it.)"""
        import random
        rnd = random.Random(int(os.environ.get("MOKATA_E1_SEED", "28")))
        d = tempfile.mkdtemp(prefix="mk-e1-900k-")
        self.addCleanup(shutil.rmtree, d, True)
        lines, keys = [], []
        filler = ["x_%d = %d" % (i, i) for i in range(64)]
        while len(lines) < 900_000:
            gap = rnd.randrange(5_000, 40_000)
            lines.extend(filler[i % 64] for i in range(gap))
            if rnd.random() < 0.5:
                b, e = K.pair(K.RSA, lead="", trail="")
                body = K.random_body(16, rnd.randrange(8, 40), str(len(lines)))
                keys.append((len(lines) + 2, body, 0))       # 1-based number of the first body line
                lines.extend([b, *body, e])
            else:
                body = K.b64_lines(K.KEY_BODIES["rsa2048"], 16)
                keys.append((len(lines) + 1, body, 9))       # start past its DER head
                lines.extend(body)
        rel = "big.txt"
        with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        t0 = time.perf_counter()
        for first, body, skip in keys:
            lo = first + rnd.randrange(skip, len(body) - 2)
            hi = min(lo + 3, first + len(body) - 1)
            meta, files, _sizes = U.copy_context(d, d, rel, lo, hi, d)
            try:
                with open(files["full"], encoding="utf-8") as fh:
                    got = fh.read()
            finally:
                for p in files.values():
                    os.remove(p)
            self.assertEqual([], [b for b in body if b in got], (lo, hi))
            self.assertGreater(meta["shapes"]["full"]["replaced"], 0, (lo, hi))
        _record("windows-900k", lines=len(lines), keys=len(keys),
                seconds=round(time.perf_counter() - t0, 2))

    def test_a_200mb_single_line_is_streamed_in_windows(self):
        """`pem_state_before` over ONE 200 MB line (ASCII and non-ASCII): wall time recorded and
        graded by ratio against 100 MB; peak RSS growth graded twice — 200 MB against 100 MB under
        1.25x (it does not grow with the line: the grade that says "never held whole"), and
        against a line exactly one window (`KEY_WINDOW` characters) long under 2x. The note's
        bound was 1.5x of one window; measured 1.19x ASCII and 1.51x non-ASCII, because a
        one-window line's FIRST read block has no carried tail to prepend, while every block of
        a long line holds one block-sized `carry + block` copy (bounded by the block, not the
        line). Recorded in the report as a place the note was wrong."""
        d = tempfile.mkdtemp(prefix="mk-e1-200mb-")
        self.addCleanup(shutil.rmtree, d, True)
        probe = (
            "import resource, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from mokata.ide import usages as U\n"
            "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "t0 = time.perf_counter()\n"
            "inside = U.pem_state_before(sys.argv[2], sys.argv[3], 2)\n"
            "dt = time.perf_counter() - t0\n"
            "after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "print(dt, after - before, inside)\n")
        if sys.platform == "win32":
            self.skipTest("no `resource` on Windows: the RSS half is recorded not-run there")
        for kind, unit in (("ascii", "abc --- def " + K.BEGIN + " xyz "),
                           ("non-ascii", "été —— " + K.BEGIN + " x ")):
            res = {}
            data = unit.encode("utf-8")
            one = S.KEY_WINDOW // len(unit) * len(data)        # ONE window of characters
            for size in (one, 100 << 20, 200 << 20):
                rel = f"{kind}-{size}.txt"
                units = size // len(data)                      # whole characters, never split
                with open(os.path.join(d, rel), "wb") as fh:
                    block = data * 4096
                    for _ in range(units // 4096):
                        fh.write(block)
                    fh.write(data * (units % 4096))
                    fh.write(b"\n")
                out = subprocess.run([sys.executable, "-c", probe, SRC, d, rel],
                                     capture_output=True, text=True, check=True).stdout.split()
                res[size] = (float(out[0]), max(int(out[1]), 1), out[2])
                os.remove(os.path.join(d, rel))
            one, half, full = res[one], res[100 << 20], res[200 << 20]
            _record("200MB", kind=kind, wall_s=round(full[0], 2), t_ratio=round(full[0] / half[0], 3),
                    rss_growth=full[1], rss_growth_100mb=half[1], rss_growth_one_window=one[1],
                    rss_ratio_200_100=round(full[1] / half[1], 3),
                    rss_ratio_one_window=round(full[1] / one[1], 3), inside=full[2])
            self.assertEqual("False", full[2], kind)
            self.assertLess(full[0] / half[0], 2.5, kind)
            self.assertLess(full[1] / half[1], 1.25, f"{kind}: RSS grew with the line")
            self.assertLess(full[1] / one[1], 2, f"{kind}: RSS is not bounded by one window")


if __name__ == "__main__":
    unittest.main()
