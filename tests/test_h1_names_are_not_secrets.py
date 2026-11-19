"""0.0.29 H1 — `SECRET-GUARD-ENTROPY-ON-NAMES`: the project's own names are not secrets.

THE BUG. Mokata names lanes, rows and acceptance criteria as a number, a short upper-case TAG
(`I`, `B5`, `W5`, `AC10`) and lower-case word pieces joined by `-`/`_`. Written bare — in
prose, or as a `-k`/dotted unittest id — the entropy backstop blocked them, because the
lower-case-slug exemption refused ANY upper-case character. The guard blocked the very note
that filed the row, and two writes of its refute.

THE RULE (design note §3a H1, binding). A `-`/`_`-separated token is exempt when every piece
is lower-case alphanumeric or a TAG `[A-Z]{1,2}[0-9]{0,2}`, at most two pieces are tags, a tag
token has at least one wordish lower-case piece, and not every non-tag piece is hex. A
mixed-case piece, a 3-letter or 5-char tag, a third tag, and a tag token with no word piece
still block. The known-shape floor runs first.

Every name and key here is ASSEMBLED at runtime from pieces, so this file carries no literal
the live guard would block (the convention of `test_secret_corpus.py`). Seeded, deterministic.
"""

import os
import random
import re
import string
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from _support import iter_worktree_files, sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import secrets as S

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOOK = os.path.join(REPO, "src", "mokata", "hooks", "secret_guard.py")


def kebab(*parts):
    return "-".join(parts)


def snake(*parts):
    return "_".join(parts)


def _entropy_blocks(s):
    return any(f.layer == "entropy" for f in S.scan(text=s))


def _master_is_lowercase_slug(tok):
    """The predicate exactly as master `4635a89` shipped it — the reference the
    with-and-without-tags rows compare against."""
    if any(c.isupper() for c in tok):
        return False
    pieces = [p for p in S._SLUG_SEP_RE.split(tok) if p]
    if len(pieces) < 2:
        return False
    return not all(S._is_pure_hex(p) for p in pieces)


def _as_master():
    return mock.patch.object(S, "_is_slug", _master_is_lowercase_slug)


# ── The project's own names (the three reproduced in evidence/H/h1-entropy-on-names.txt,
#    plus the generated family) ────────────────────────────────────────────────────────────
REPRODUCED = [
    kebab("0028", "I", "mcp", "reliability"),
    kebab("0027", "B5", "win", "lsp", "reaper"),
    kebab("0027", "W5", "lsp", "bin", "pick"),
    snake("test", "AC10", "a", "round", "trip", "of", "the", "plan"),
    snake("test", "R7", "dry", "run", "writes", "nothing"),
]

LANE_LETTERS = list("ABCDEFGHI")
WORD_PIECES = ["mcp", "reliability", "lsp", "reaper", "bin", "pick", "hook", "budget",
               "security", "residue", "gates", "recording", "cancel", "process", "door",
               "release", "hygiene", "graph", "perf", "win"]


def _generated_names():
    """Lane letters x {1,2}-letter/{0,2}-digit tags x word pieces, in both separators."""
    rng = random.Random(0x0029)
    out = []
    for lane in LANE_LETTERS:
        for tag in (lane, lane + "1", lane + "12", "A" + lane, "A" + lane + "9", lane + "C10"[:1]):
            for _ in range(6):
                words = rng.sample(WORD_PIECES, rng.randint(2, 4))
                num = "%04d" % rng.randint(10, 9999)
                sep = rng.choice("-_")
                out.append(sep.join([num, tag] + words))
    return [n for n in out if len(n) >= 20]


class TheProjectsOwnNames(unittest.TestCase):
    def test_reproduced_names_pass_scan_bare(self):
        for name in REPRODUCED:
            with self.subTest(name=name):
                self.assertGreaterEqual(len(name), 20)
                self.assertFalse(_entropy_blocks(name), name)

    def test_reproduced_names_blocked_on_master(self):
        """The RED anchor: each reproduced name blocks under the master predicate (bare)."""
        with _as_master():
            for name in REPRODUCED[:3]:
                with self.subTest(name=name):
                    self.assertTrue(_entropy_blocks(name), name)

    def test_generated_lane_names_pass_scan(self):
        names = _generated_names()
        self.assertGreater(len(names), 200)
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(_entropy_blocks(name), name)

    def _hook(self, tool, tool_input):
        import json
        payload = json.dumps({"session_id": "s", "hook_event_name": "PreToolUse",
                              "tool_name": tool, "tool_input": tool_input})
        return subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                              text=True).returncode

    def test_the_hooks_bash_path_passes_them(self):
        for name in REPRODUCED:
            with self.subTest(name=name):
                cmd = "python -m unittest -k " + name + " && ls docs/" + name + ".md"
                self.assertEqual(self._hook("Bash", {"command": cmd}), 0, name)

    def test_the_hooks_write_path_passes_them(self):
        for name in REPRODUCED:
            with self.subTest(name=name):
                body = "See " + name + " for the stage that filed this row.\n"
                self.assertEqual(self._hook("Write", {"file_path": "notes.md", "content": body}),
                                 0, name)

    def test_a_real_secret_of_the_same_shape_still_blocks_through_the_hook(self):
        """A key chunked to look like a lane name, but with no word piece, still blocks
        through the same hook path."""
        rng = random.Random(7)
        key = kebab("0028", "B5", *("".join(rng.choice("bcdfghjklmnpqrstvwxz0123456789")
                                            for _ in range(7)) for _ in range(4)))
        self.assertTrue(_entropy_blocks(key), key)
        self.assertEqual(self._hook("Bash", {"command": "echo " + key}), 2, key)


# ── The tree's own names ─────────────────────────────────────────────────────────────────
_DECL = re.compile(r"\b(?:def|class)\s+[A-Za-z_][A-Za-z0-9_]*")


_SKIP = {"__pycache__", "node_modules", ".git"}


# CORPUS: THE WORKING TREE — the names that SHIP (`sync-public.sh` rsyncs the working tree).
def _source_files():
    for top in ("src", "tests"):
        for rel, ab in iter_worktree_files(os.path.join(REPO, top), skip_dirs=_SKIP):
            if rel.endswith(".py"):
                yield ab


class TheTreesOwnNamesPass(unittest.TestCase):
    """Derived from the tree, never from `docs/build/` (an internal path the mirror lacks)."""

    def test_every_def_and_class_line_gives_no_entropy_finding(self):
        bad, lines = [], 0
        for path in _source_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                for no, line in enumerate(fh, 1):
                    if not _DECL.search(line):
                        continue
                    lines += 1
                    hits = [f for f in S._scan_entropy(line)
                            if f.kind == "high-entropy-token"]
                    if hits:
                        bad.append("%s:%d %s" % (os.path.relpath(path, REPO), no, line.strip()))
        self.assertGreater(lines, 10_000)
        self.assertEqual(bad, [])

    def test_every_file_stem_bare_gives_no_entropy_finding(self):
        bad, stems = [], 0
        # CORPUS: THE WORKING TREE — every file name that ships.
        for top in ("src", "tests"):
            for rel, ab in iter_worktree_files(os.path.join(REPO, top), skip_dirs=_SKIP):
                stems += 1
                stem = os.path.splitext(os.path.basename(rel))[0]
                if any(f.kind == "high-entropy-token" for f in S._scan_entropy(stem)):
                    bad.append(os.path.relpath(ab, REPO))
        self.assertGreater(stems, 1_000)
        self.assertEqual(bad, [])

    def test_the_corpus_never_reads_internal_paths(self):
        for path in _source_files():
            parts = Path(path).relative_to(REPO).parts
            self.assertNotEqual(("docs", "build"), parts[:2], path)


# ── Controls ─────────────────────────────────────────────────────────────────────────────
class ControlsStillBlock(unittest.TestCase):
    def test_mixed_case_after_a_tag(self):
        self.assertTrue(_entropy_blocks(snake("R18", "A" + "Pathological", "thing", "x12")))

    def test_tag_only_token_with_no_word_piece(self):
        self.assertTrue(_entropy_blocks(kebab("0028", "B5", "x7q2", "zk9w", "m3p8", "r4t1")))

    def test_a_three_letter_tag(self):
        self.assertTrue(_entropy_blocks(kebab("x7q2zk", "ABC", "reliability", "m3p8r4t1")))

    def test_a_five_char_tag(self):
        self.assertTrue(_entropy_blocks(kebab("x7q2zk", "AB123", "reliability", "m3p8r4t1")))

    def test_three_tags(self):
        self.assertTrue(_entropy_blocks(kebab("x7q2", "B5", "W5", "I", "reliability", "m3p8r4")))

    def test_tags_and_hex_pieces(self):
        rng = random.Random(5)
        tok = kebab("B5", _rs(rng, HEX, 10), "I", _rs(rng, HEX, 9), _rs(rng, HEX, 8))
        self.assertGreaterEqual(S._shannon(tok), 3.5, tok)
        self.assertTrue(_entropy_blocks(tok), tok)

    def test_a_floor_shape_chunked_around_a_tag(self):
        aws = "AK" + "IA" + "IOSF" + "ODNN7" + "EXAM" + "PLE"
        chunked = snake(aws[:8], "B5", aws[8:])
        self.assertTrue(any(f.kind == "known-secret-shape" for f in S.scan(text=aws)))
        self.assertTrue(S.has_secrets(S.scan(text=chunked)), chunked)

    def test_no_tag_slugs_are_byte_identical_to_master(self):
        rng = random.Random(3)
        for _ in range(5000):
            tok = "_".join("".join(rng.choice(string.ascii_lowercase + string.digits)
                                   for _ in range(rng.randint(1, 9)))
                           for _ in range(rng.randint(1, 5)))
            self.assertEqual(S._is_slug(tok), _master_is_lowercase_slug(tok), tok)

    def test_macos_temp_dir_still_passes(self):
        p = "/var/folders/y9/" + "r2xr67z10lb6p5n3t" + "_m1_c740000gn/T/"
        self.assertFalse(_entropy_blocks(p))


# ── Measured families ────────────────────────────────────────────────────────────────────
B64 = string.ascii_letters + string.digits + "+/"
B64URL = string.ascii_letters + string.digits + "-_"
HEX = "0123456789abcdef"
ALNUM = string.ascii_letters + string.digits


def _rs(rng, alphabet, n):
    return "".join(rng.choice(alphabet) for _ in range(n))


def _family(name, rng):
    if name == "aws-id":
        return "AK" + "IA" + _rs(rng, string.ascii_uppercase + string.digits, 16)
    if name == "aws-secret":
        return _rs(rng, ALNUM + "/+", 40)
    if name == "ghp":
        return "gh" + "p_" + _rs(rng, ALNUM, 36)
    if name == "xoxb":
        return "xo" + "xb-" + "-".join(_rs(rng, string.digits, 12) for _ in range(2)) \
            + "-" + _rs(rng, ALNUM, 24)
    if name == "jwt":
        return ".".join("ey" + _rs(rng, B64URL, 30) for _ in range(3))
    if name == "b64-32":
        return _rs(rng, B64, 43) + "="
    if name == "urlsafe-32":
        return _rs(rng, B64URL, 43)
    if name == "hex40":
        return _rs(rng, HEX, 40)
    if name == "hex64":
        return _rs(rng, HEX, 64)
    raise KeyError(name)


FAMILIES = ("aws-id", "aws-secret", "ghp", "xoxb", "jwt", "b64-32", "urlsafe-32",
            "hex40", "hex64")
TAGS = ["I", "B5", "W5", "AC10", "G", "H2", "Q", "D13"]


def _inject_tags(key, rng):
    """Insert 1-2 tag pieces at random cut points, separated by `_` or `-`."""
    out = key
    for _ in range(rng.randint(1, 2)):
        cut = rng.randrange(1, len(out))
        sep = rng.choice("_-")
        out = out[:cut] + sep + rng.choice(TAGS) + sep + out[cut:]
    return out


REAL_SEEDS = int(os.environ.get("MOKATA_H1_REAL_SEEDS") or 30_000)
INJECTED_SEEDS = int(os.environ.get("MOKATA_H1_INJECTED_SEEDS") or 30_000)


class RealShapesUnchanged(unittest.TestCase):
    """Every real-shaped family: the verdict (any finding) is EQUAL to master's, with and
    without injected tags. 30k seeded each (x 9 families x 2 forms = 540k scans)."""

    def test_verdicts_equal_master_with_and_without_tags(self):
        for fam in FAMILIES:
            rng = random.Random("real-" + fam)
            diffs = []
            for _ in range(REAL_SEEDS):
                key = _family(fam, rng)
                tagged = _inject_tags(key, rng)
                for form in (key, tagged):
                    now = S.has_secrets(S.scan(text=form))
                    if not now:                         # only a pass can differ from master
                        with _as_master():
                            was = S.has_secrets(S.scan(text=form))
                        if was:
                            diffs.append(form)
            with self.subTest(family=fam):
                self.assertEqual(diffs, [])


def _no_word_chunk(rng):
    while True:
        c = _rs(rng, string.ascii_lowercase + string.digits, rng.randint(4, 8))
        if not S._is_wordish(c) and not S._is_pure_hex(c):
            return c


def _wordish_chunk(rng):
    while True:
        c = _rs(rng, string.ascii_lowercase, rng.randint(6, 8))
        if S._is_wordish(c):
            return c


class TagInjectedKeys(unittest.TestCase):
    """Chunked lower-alnum keys with 1-2 injected tags. 120k seeded in all."""

    def _key(self, rng, with_word):
        chunks = [_no_word_chunk(rng) for _ in range(rng.randint(3, 5))]
        if with_word:
            chunks.insert(rng.randrange(len(chunks) + 1), _wordish_chunk(rng))
        tags = [rng.choice(TAGS) for _ in range(rng.randint(1, 2))]
        untagged = list(chunks)
        for t in tags:
            chunks.insert(rng.randrange(len(chunks) + 1), t)
        sep = rng.choice("_-")
        return sep.join(chunks), sep.join(untagged)

    def _rate(self, with_word, n, seed):
        """(tagged pass rate, untagged pass rate, rate the NEW predicate alone unblocked:
        tagged passes now and blocked under master's predicate)."""
        rng = random.Random(seed)
        tagged_pass = untagged_pass = by_tag_rule = 0
        for _ in range(n):
            tagged, untagged = self._key(rng, with_word)
            if not _entropy_blocks(tagged):
                tagged_pass += 1
                with _as_master():
                    if _entropy_blocks(tagged):
                        by_tag_rule += 1
            if not _entropy_blocks(untagged):
                untagged_pass += 1
        return tagged_pass / n, untagged_pass / n, by_tag_rule / n

    def test_no_word_piece_family_never_unblocks(self):
        """The tag rule unblocks NONE of them (some pass on master already: under 20 chars or
        entropy < 3.5 after chunking — not this rule's doing, and unchanged)."""
        for seed in ("nw-1", "nw-2"):
            _, _, by_rule = self._rate(False, INJECTED_SEEDS, seed)
            self.assertEqual(by_rule, 0.0, seed)

    def test_word_piece_family_unblocks_no_more_than_the_untagged_key(self):
        """The stated figure (the `_is_slug` docstring): a tag never makes a key pass that its
        untagged form did not already pass under the 0.0.14 lower-case half."""
        for seed in ("w-1", "w-2"):
            tagged, untagged, by_rule = self._rate(True, INJECTED_SEEDS, seed)
            sys.stderr.write("\n[H1 TagInjectedKeys %s] tagged pass %.4f%%, untagged pass "
                             "%.4f%%, unblocked by the tag rule %.4f%%\n"
                             % (seed, tagged * 100, untagged * 100, by_rule * 100))
            self.assertLessEqual(tagged, untagged, seed)


# ── Cost ─────────────────────────────────────────────────────────────────────────────────
SCAN_LINES = int(os.environ.get("MOKATA_H1_SCAN_LINES") or 1_000_000)


class ScanCost(unittest.TestCase):
    """A 1M-line file: `_shannon` and `_candidate_runs` calls counted, against master's
    predicate on the same text. Equal on the tag-free lines; the tag rows can only REMOVE
    `_shannon` calls (an exempt run is never scored)."""

    def _text(self, tag_rows):
        rng = random.Random(11)
        lines = []
        for i in range(SCAN_LINES):
            k = i % 4
            if k == 0:
                lines.append("    value_%d = compute(alpha, beta) + %d" % (i, i))
            elif k == 1:
                lines.append("# " + _rs(rng, ALNUM, 32))
            elif k == 2:
                lines.append("path = '/var/cache/" + snake("build", "cache", str(i), "entry") + "'")
            else:
                lines.append("    return x")
        for j in range(tag_rows):
            lines[j * 7 + 3] = "see " + kebab("%04d" % j, "B5", "win", "lsp", "reaper")
        return "\n".join(lines)

    def _count(self, text, master):
        calls = {"shannon": 0, "runs": 0}
        real_sh, real_runs = S._shannon, S._candidate_runs

        def sh(s):
            calls["shannon"] += 1
            return real_sh(s)

        def runs(line):
            calls["runs"] += 1
            return real_runs(line)

        with mock.patch.object(S, "_shannon", sh), mock.patch.object(S, "_candidate_runs", runs):
            if master:
                with _as_master():
                    S._scan_entropy(text)
            else:
                S._scan_entropy(text)
        return calls

    def test_cost_equals_master_plus_minus_the_tag_rows(self):
        tag_rows = 1000
        text = self._text(tag_rows)
        now, was = self._count(text, False), self._count(text, True)
        self.assertEqual(now["runs"], was["runs"])
        self.assertLessEqual(now["shannon"], was["shannon"])
        self.assertLessEqual(was["shannon"] - now["shannon"], tag_rows)


if __name__ == "__main__":
    unittest.main()
