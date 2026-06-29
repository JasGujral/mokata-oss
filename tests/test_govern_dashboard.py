"""Stage 48 — memory & governance dashboard (`mokata govern`).

A consolidated, clickable, SELF-CONTAINED local HTML view of the GOVERNED STATE — reusing
the Stage-40 dashboard engine + the memory primitives. Read-only, frugal, local-first,
degrade-clean. It shows: the always-on rules/guardrails (with line-budget usage), memory
grouped by kind (subject/value/provenance + the gated `mokata memory edit` manage path),
the read/write ratio, and pending self-healing proposals. It never writes durable state and
never leaves the machine.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.config import Surface
from mokata.dashboard import (
    build_governance_view,
    governance_path,
    render_governance_html,
    write_governance_dashboard,
)
from mokata.memory import MemoryItem, MemoryStore


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _seed(surface, items):
    store = MemoryStore.from_surface(surface)
    for it in items:
        store.backend.put(it)
    return store


class TestGovernanceDashboard(unittest.TestCase):
    def test_writes_self_contained_html_under_temp_local(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            path = write_governance_dashboard(surface)
            self.assertEqual(path, governance_path(surface.mokata_dir))
            self.assertTrue(os.path.exists(path))
            self.assertIn(os.path.join(MOKATA_DIR, "temp_local"), path)
            text = _read(path)
            self.assertIn("<!doctype html>", text.lower())
            self.assertIn("<style>", text)                 # inline CSS
            # self-contained / local-first: no external assets, scripts, or network
            self.assertNotIn("http://", text)
            self.assertNotIn("https://", text)
            self.assertNotIn("<script", text.lower())

    def test_shows_rules_and_line_budget(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            text = _read(write_governance_dashboard(surface))
            self.assertIn("Human-gate", text)              # an always-on inviolable rule
            self.assertIn("/ 60", text)                    # the always-on line budget (cap 60)

    def test_shows_memory_by_kind_with_provenance_and_manage_path(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [
                MemoryItem.create("db.engine", "postgres", kind="decision",
                                  author="alice", source="meeting"),
                MemoryItem.create("style.tabs", "use spaces", kind="best-practice",
                                  author="bob", source="lint"),
            ])
            text = _read(write_governance_dashboard(surface))
            self.assertIn("decision", text)
            self.assertIn("best-practice", text)
            self.assertIn("db.engine", text)
            self.assertIn("postgres", text)
            self.assertIn("alice", text)                   # provenance author
            self.assertIn('mokata memory edit', text)      # the gated manage path
            self.assertIn("db.engine", text)

    def test_shows_read_write_ratio(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            text = _read(write_governance_dashboard(surface))
            self.assertIn("read", text.lower())
            self.assertIn("write", text.lower())

    def test_shows_pending_self_healing_proposal(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # two active items, same subject, different value -> a contradiction proposal
            _seed(surface, [
                MemoryItem.create("db.engine", "postgres", kind="decision",
                                  created_at="2026-01-01T00:00:00+00:00"),
                MemoryItem.create("db.engine", "mysql", kind="decision",
                                  created_at="2026-02-01T00:00:00+00:00"),
            ])
            text = _read(write_governance_dashboard(surface))
            self.assertIn("proposal", text.lower())
            self.assertIn("-&gt;", text)                   # the old -> new diff (escaped)

    def test_degrades_clean_with_no_memory(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)                             # standard, no items captured
            text = _read(write_governance_dashboard(surface))
            self.assertIn("<!doctype html>", text.lower())  # still a valid self-contained page
            self.assertIn("no", text.lower())               # a friendly empty state

    def test_is_read_only_writes_nothing_durable(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            store = _seed(surface, [MemoryItem.create("x", "y", kind="rule")])
            before = len(store.backend.all())
            write_governance_dashboard(surface)
            self.assertEqual(len(MemoryStore.from_surface(surface).backend.all()), before)

    def test_render_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [MemoryItem.create("a.b", "c", kind="context")])
            view = build_governance_view(surface)
            self.assertEqual(render_governance_html(view), render_governance_html(view))

    def test_govern_does_not_count_reads_or_mutate_stats(self):
        # Read-only: rendering the dashboard must NOT bump the read counter (which would
        # also persist durably). It snapshots stats and reads via the non-counting path.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [
                MemoryItem.create("r1", "x", kind="rule"),
                MemoryItem.create("db.engine", "postgres", kind="decision"),
            ])
            before = MemoryStore.from_surface(surface).stats.reads
            write_governance_dashboard(surface)
            after = MemoryStore.from_surface(surface).stats.reads
            self.assertEqual(after, before)            # no counter moved

    def test_two_govern_runs_are_byte_identical(self):
        # Deterministic + read-only: because nothing is counted/persisted, repeated renders
        # of an unchanged store produce the exact same bytes (the stats no longer drift).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [
                MemoryItem.create("r1", "x", kind="rule"),
                MemoryItem.create("db.engine", "postgres", kind="decision"),
            ])
            first = _read(write_governance_dashboard(surface))
            second = _read(write_governance_dashboard(surface))
            self.assertEqual(first, second)

    def test_cmd_govern_writes_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out = run_cli(["govern", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("govern.html", out)
            self.assertTrue(os.path.exists(governance_path(os.path.join(d, MOKATA_DIR))))


if __name__ == "__main__":
    unittest.main()
