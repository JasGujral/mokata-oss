"""0.0.27 P28 `ID-BENCH-PX0-CORPUS` — the benchmark's own logic, on a tiny local fixture.

`scripts/bench_corpus.py` is the reproducible record the Rust exit (`NATIVE-CORE`) is judged
against, so the parts a reader takes on trust are pinned here, without the network:

  * the numbers: median and p95 (nearest rank — with 5 samples the p95 is the slowest run, never an
    interpolated value no run produced);
  * the pin: a corpus is measured at the SHA named, not at whatever a tag or branch points to now,
    and a checkout at another commit is refused rather than silently measured;
  * the shape: a result whose measured SHA is not the pinned one, or whose sample count disagrees
    with its `n`, is refused by `table` instead of rendered as evidence;
  * the line count the report quotes: text files by language, binaries apart, a last line without a
    newline still a line.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bench_corpus as B  # noqa: E402

SCRIPT_RELPATH = "scripts/bench_corpus.py"
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
HAVE_GIT = shutil.which("git") is not None


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.invalid",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.invalid"))


def _write(root, rel, data):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)


def _doc(**over):
    doc = {"schema": B.SCHEMA, "failures": [],
           "corpus": {"name": "tiny", "url": "file:///x", "sha": "a" * 40, "measured_sha": "a" * 40},
           "metrics": {"ide.quick_open_s": B.summarize([0.3, 0.1, 0.2, 0.5, 0.4])}, "facts": {}}
    doc.update(over)
    return doc


class TheNumbers(unittest.TestCase):
    def test_p95_of_five_runs_is_the_slowest_run(self):
        self.assertEqual(5.0, B.nearest_rank([3, 1, 5, 2, 4], 0.95))

    def test_p95_of_twenty_runs_is_the_nineteenth(self):
        self.assertEqual(19.0, B.nearest_rank(list(range(20, 0, -1)), 0.95))

    def test_p95_is_always_a_sample_a_run_produced(self):
        xs = [0.11, 0.42, 0.13, 0.9, 0.27, 0.31, 0.05]
        self.assertIn(B.nearest_rank(xs, 0.95), xs)

    def test_median_of_an_even_count_is_the_mean_of_the_middle_two(self):
        s = B.summarize([4, 1, 3, 2])
        self.assertEqual(2.5, s["median"])
        self.assertEqual((4, 1.0, 4.0), (s["n"], s["min"], s["max"]))

    def test_every_sample_is_kept_as_evidence(self):
        self.assertEqual([0.3, 0.1, 0.2], B.summarize([0.3, 0.1, 0.2])["samples"])

    def test_no_samples_is_an_error_not_a_zero(self):
        with self.assertRaises(ValueError):
            B.summarize([])
        with self.assertRaises(ValueError):
            B.nearest_rank([1.0], 0)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class ThePin(unittest.TestCase):
    """The corpus is the commit named — even when the branch has since moved on."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="p28-pin-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.up = os.path.join(cls.tmp, "upstream")
        os.makedirs(cls.up)
        _git(cls.up, "init", "-q")
        _write(cls.up, "a.py", b"one\n")
        _git(cls.up, "add", "-A")
        _git(cls.up, "commit", "-q", "-m", "first")
        cls.first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cls.up, capture_output=True,
                                   text=True, check=True).stdout.strip()
        _write(cls.up, "a.py", b"one\ntwo\n")
        _git(cls.up, "commit", "-q", "-am", "second")
        # GitHub serves any commit by SHA; a local upstream has to be told to
        _git(cls.up, "config", "uploadpack.allowAnySHA1InWant", "true")
        cls.url = "file://" + cls.up.replace(os.sep, "/")

    def test_the_pinned_commit_is_what_is_checked_out_not_the_branch_tip(self):
        dest = os.path.join(self.tmp, "c1")
        # the corpus is the commit's BYTES on every machine: a git that converts line endings on
        # checkout (Windows' stock `core.autocrlf=true`) must not change what is measured
        hostile = os.path.join(self.tmp, "hostile.gitconfig")
        with open(hostile, "w", encoding="utf-8") as fh:
            fh.write("[core]\n\tautocrlf = true\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": hostile}):
            self.assertEqual(self.first, B.clone_pinned(self.url, self.first, dest))
        self.assertEqual(self.first, B.head_sha(dest))
        with open(os.path.join(dest, "a.py"), "rb") as fh:
            self.assertEqual(b"one\n", fh.read())

    def test_a_checkout_at_another_commit_is_refused_not_measured(self):
        dest = os.path.join(self.tmp, "c2")
        B.clone_pinned(self.url, self.first, dest)
        _write(dest, "b.py", b"x\n")
        _git(dest, "add", "-A")
        _git(dest, "commit", "-q", "-m", "local")
        with self.assertRaises(RuntimeError) as cm:
            B.clone_pinned(self.url, self.first, dest)
        self.assertIn("not the pinned", str(cm.exception))

    def test_an_existing_checkout_at_the_pin_is_reused(self):
        dest = os.path.join(self.tmp, "c3")
        B.clone_pinned(self.url, self.first, dest)
        self.assertEqual(self.first, B.clone_pinned(self.url, self.first, dest))

    def test_a_pin_must_be_a_full_sha_not_a_tag_or_prefix(self):
        for bad in ("v1.0", self.first[:12], "", "g" * 40):
            with self.assertRaises(ValueError):
                B.clone_pinned(self.url, bad, os.path.join(self.tmp, "never"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "never")))

    def test_every_shipped_corpus_is_pinned_by_full_sha(self):
        for name, spec in B.CORPORA.items():
            self.assertRegex(spec["sha"], r"^[0-9a-f]{40}$", name)
            for k in ("url", "tag", "hub", "define", "search", "common", "fuzzy", "fuzzy_path", "write"):
                self.assertTrue(spec.get(k), f"{name}.{k}")


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TheLineCount(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="p28-count-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q")
        _write(self.repo, "pkg/main.go", b"package main\n\nfunc main() {}\n")
        _write(self.repo, "lib/x.py", b"a = 1\nb = 2\nc = 3")            # no final newline
        _write(self.repo, "web/app.ts", b"export const a = 1;\n" * 10)
        _write(self.repo, "img/logo.png", b"\x89PNG\r\n\x1a\n\0\0\0data\n")
        _write(self.repo, "untracked.py", b"x\n" * 999)
        _git(self.repo, "add", "pkg", "lib", "web", "img")

    def test_lines_per_language_over_tracked_text_files(self):
        c = B.count_lines(self.repo)
        self.assertEqual({"files": 1, "lines": 3}, c["by_language"]["Go"])
        self.assertEqual({"files": 1, "lines": 3}, c["by_language"]["Python"])
        self.assertEqual({"files": 1, "lines": 10}, c["by_language"]["TypeScript"])
        self.assertEqual(16, c["total_lines"])
        self.assertEqual((3, 1), (c["text_files"], c["binary_files"]))

    def test_the_largest_file_is_named_with_its_size(self):
        c = B.count_lines(self.repo)
        self.assertEqual("web/app.ts", c["largest_file"]["path"])
        self.assertEqual((200, 10), (c["largest_file"]["bytes"], c["largest_file"]["lines"]))

    def test_languages_are_ordered_by_lines(self):
        self.assertEqual("TypeScript", next(iter(B.count_lines(self.repo)["by_language"])))


class TheShape(unittest.TestCase):
    def test_a_complete_result_is_valid(self):
        self.assertEqual([], B.validate_result(_doc()))

    def test_a_result_measured_at_another_sha_is_refused(self):
        d = _doc()
        d["corpus"]["measured_sha"] = "b" * 40
        self.assertTrue(any("not the pinned" in m for m in B.validate_result(d)))

    def test_a_short_sha_is_refused(self):
        d = _doc()
        d["corpus"]["sha"] = d["corpus"]["measured_sha"] = "abc123"
        self.assertTrue(B.validate_result(d))

    def test_a_count_that_disagrees_with_its_samples_is_refused(self):
        d = _doc()
        d["metrics"]["ide.quick_open_s"]["n"] = 7
        self.assertTrue(any("samples" in m for m in B.validate_result(d)))

    def test_the_json_round_trips_and_the_table_carries_median_and_p95(self):
        d = json.loads(json.dumps(_doc(failures=[{"step": "ide", "error": "boom"}])))
        t = B.render_table([d])
        self.assertIn("| ide.quick_open_s | 300 ms / 500 ms (n=5) |", t)
        self.assertIn("`aaaaaaaaaaaa`", t)
        self.assertIn("tiny · ide: boom", t)

    def test_table_refuses_a_foreign_document(self):
        tmp = tempfile.mkdtemp(prefix="p28-table-")
        self.addCleanup(shutil.rmtree, tmp, True)
        good, bad = os.path.join(tmp, "g.json"), os.path.join(tmp, "b.json")
        with open(good, "w", encoding="utf-8") as fh:
            json.dump(_doc(), fh)
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump(_doc(schema="something/else"), fh)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(0, B.main(["table", good]))
            self.assertEqual(2, B.main(["table", good, bad]))
        self.assertIn("ide.quick_open_s", out.getvalue())
        self.assertIn("not a mokata-bench-corpus/1 result", err.getvalue())


class ShipSafe(unittest.TestCase):
    def test_the_script_never_reads_internal_build_docs(self):
        with open(os.path.join(ROOT, *SCRIPT_RELPATH.split("/")), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("docs/build", text)
        self.assertNotIn("docs" + os.sep + "build", text)


@unittest.skipUnless(os.path.exists(SYNC_SH), "sync-public.sh is internal-only; absent on the mirror")
class ShipSafeAgainstTheMirrorControls(unittest.TestCase):
    def test_the_script_ships_because_this_test_imports_it(self):
        import _mirror_bookkeeping as MB
        with open(SYNC_SH, encoding="utf-8") as fh:
            script = fh.read()
        self.assertNotIn(SCRIPT_RELPATH, MB.exclude_entries(script))
        guarded = MB.guard_entries(script)
        self.assertIsNotNone(guarded, "INTERNAL_PATHS could not be located")
        self.assertNotIn(SCRIPT_RELPATH, guarded)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
