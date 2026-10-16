"""Public proof page (0.0.23 D9) — renders G2's counters from a run archive and REFUSES anything
but a complete real run. Tested with a FIXTURE run that is stamped FIXTURE on every counter.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _g2_fixture as FX

from bench.g2 import __main__ as cli
from bench.g2.archive import ArchiveError, RunArchive
from bench.g2.metrics import aggregate
from bench.g2.page import archive_digest, render_page


def _forge_real_ids(path):
    """Relabel a fixture archive's agent/judge/cloner ids so its kind DERIVES as real. Used only to
    reach the renderer's real-run branch and the validator's other refusals; never a published
    artifact — the page it renders is asserted on and discarded."""
    mp = os.path.join(path, "run.json")
    with open(mp, encoding="utf-8") as fh:
        m = json.load(fh)
    m["agent"]["id"], m["judge"]["id"], m["cloner"], m["kind"] = \
        "claude-code", "claude-cli", "git", "real"
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(m, fh)


class TestProofPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.fixture = FX.build_fixture_archive(os.path.join(cls.tmp, "fixture"), repeats=5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_refuses_a_fixture_run(self):
        with self.assertRaises(ArchiveError) as cm:
            render_page(RunArchive(self.fixture))
        self.assertIn("not a real run", str(cm.exception))

    def test_fixture_render_is_stamped_fixture_everywhere(self):
        page = render_page(RunArchive(self.fixture), allow_fixture=True)
        self.assertIn('data-kind="fixture"', page)
        self.assertIn("FIXTURE RUN — NOT EVIDENCE", page)
        self.assertIn("<title>mokata proof (FIXTURE)</title>", page)
        self.assertEqual(page.count("[FIXTURE]</div>"), 4)      # every counter card is stamped

    def test_counters_on_the_page_are_the_archives(self):
        arch = RunArchive(self.fixture)
        pc = aggregate(arch)["per_condition"]["mokata"]
        page = render_page(arch, allow_fixture=True)
        self.assertIn(f'<div class="v">{pc["secrets_blocked"]}</div>', page)
        self.assertIn(f'<div class="v">{pc["defects_caught"]}</div>', page)
        self.assertIn(f'{pc["tokens_saved_vs_bare"]:,}', page)   # signed, not clipped
        self.assertIn(archive_digest(arch), page)

    def test_page_is_static_and_self_contained(self):
        page = render_page(RunArchive(self.fixture), allow_fixture=True)
        for banned in ("<script", "http://", "https://", "<link"):
            self.assertNotIn(banned, page)

    def test_digest_moves_when_a_verdict_changes(self):
        dst = os.path.join(self.tmp, "digest")
        shutil.copytree(self.fixture, dst)
        before = archive_digest(RunArchive(dst))
        rec = RunArchive(dst).records[0]
        with open(os.path.join(rec.path, "verdict.json"), "a", encoding="utf-8") as fh:
            fh.write(" ")
        self.assertNotEqual(before, archive_digest(RunArchive(dst)))

    def test_the_real_branch_renders_without_the_banner(self):
        dst = os.path.join(self.tmp, "shape-real")
        shutil.copytree(self.fixture, dst)
        _forge_real_ids(dst)
        page = render_page(RunArchive(dst))
        self.assertIn('data-kind="real"', page)
        self.assertNotIn("FIXTURE", page)

    def test_refuses_an_incomplete_real_run(self):
        dst = os.path.join(self.tmp, "incomplete")
        shutil.copytree(self.fixture, dst)
        _forge_real_ids(dst)
        rec = RunArchive(dst).records[3]
        os.remove(os.path.join(rec.path, "stream.jsonl"))
        with self.assertRaises(ArchiveError) as cm:
            render_page(RunArchive(dst))
        self.assertIn("missing stream.jsonl", str(cm.exception))

    def test_refuses_fewer_than_five_repeats(self):
        dst = FX.build_fixture_archive(os.path.join(self.tmp, "r3"), repeats=3,
                                       task_ids=["gates-tdd-quantity-guard"])
        _forge_real_ids(dst)
        with self.assertRaises(ArchiveError) as cm:
            render_page(RunArchive(dst))
        self.assertIn("repeats=3 < 5", str(cm.exception))

    def test_cli_page_refuses_fixture_and_writes_nothing(self):
        dest = os.path.join(self.tmp, "proof.html")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["page", "--run", self.fixture, "--out", dest])
        self.assertEqual(code, 2)
        self.assertIn("REFUSING", err.getvalue())
        self.assertFalse(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
