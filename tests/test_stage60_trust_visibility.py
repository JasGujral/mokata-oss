"""Stage 60 — trust & visibility polish.

Both jsonschema states (no jsonschema imported here — these exercise the dashboard / visibility
/ ship / bootstrap layers, which are dependency-free, so behaviour is identical ABSENT/PRESENT).

Covers the three Stage-60 touches, all READ-ONLY / DERIVED / HUMAN-GATED:
  * LIVE govern — `render_governance_html(view, refresh_secs=…)` adds a self meta-refresh (static
    when not live); 0 network refs; byte-identical deterministic render; writes nothing durable.
  * "WHAT CHANGED SINCE LAST SESSION" — a read-only diff listing new/changed memory + rules +
    decisions vs a lightweight snapshot; first-session degrade-clean; the diff never bumps stats
    and the snapshot write is read-only-safe (transient temp_local).
  * END-OF-RUN "WHAT I CHANGED AND WHY" — the ship step folds in a bounded `audit --why` recap
    of THIS run and stays human-gated (never auto-merges).
"""

import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.bootstrap import BOOTSTRAP_TOKEN_BUDGET, build_bootstrap
from mokata.config import Surface
from mokata.dashboard import (
    build_governance_view,
    render_governance_html,
    write_governance_dashboard,
)
from mokata.engine.ship import (
    MERGE,
    build_finish_summary,
    record_finish_decision,
)
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.memory import (
    CONTEXT,
    CONTRADICTION,
    PERSISTENT,
    RULE,
    HealingProposal,
    MemoryItem,
    MemoryStore,
)
from mokata.visibility import (
    capture_session_snapshot,
    changed_since_line,
    compute_session_diff,
    build_state_fingerprint,
)


def _silent(_):
    pass


def _repo():
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    return d


def _store(d):
    return MemoryStore.from_surface(Surface.load(d))


def _ctx(subject, value, kind=CONTEXT):
    return MemoryItem.create(subject, value, mtype=PERSISTENT, kind=kind)


# ----------------------------------------------------------------- live / auto-refresh govern

class TestLiveGovern(unittest.TestCase):
    def test_refresh_secs_adds_meta_refresh_static_when_not(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            view = build_governance_view(Surface.load(d))
            live = render_governance_html(view, refresh_secs=2)
            static = render_governance_html(view)
            self.assertIn('http-equiv="refresh"', live)
            self.assertIn('content="2"', live)
            self.assertNotIn("http-equiv", static)       # static snapshot has no refresh

    def test_zero_network_refs(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            view = build_governance_view(Surface.load(d))
            for html_text in (render_governance_html(view),
                              render_governance_html(view, refresh_secs=2)):
                # no external resource loads — self-contained, never leaves the machine
                self.assertNotIn("http://", html_text)
                self.assertNotIn("https://", html_text)
                self.assertNotIn("//cdn", html_text)
                self.assertNotIn("src=", html_text)

    def test_byte_identical_deterministic_render(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            a = render_governance_html(build_governance_view(Surface.load(d)))
            b = render_governance_html(build_governance_view(Surface.load(d)))
            self.assertEqual(a, b)                        # pure function of state (no wall-clock)

    def test_writes_nothing_durable(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("a", "alpha"), assume_yes=True)
            reads_before, writes_before = store.stats.reads, store.stats.writes
            write_governance_dashboard(Surface.load(d), refresh_secs=2)
            write_governance_dashboard(Surface.load(d))
            after = _store(d)
            self.assertEqual((after.stats.reads, after.stats.writes),
                             (reads_before, writes_before))   # no stat bump from the view

    def test_cli_govern_live_off_dashboard_tier_degrades_to_static(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["govern", "--live", "--path", d])    # default tier = terminal
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("static snapshot", out)         # honours settings.ux.progress
            path = re.search(r"wrote (\S+)", out).group(1)
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn("http-equiv", fh.read())


# ----------------------------------------------------------------- since last session diff

class TestSinceLastSession(unittest.TestCase):
    def test_first_session_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            diff = compute_session_diff(Surface.load(d))   # no snapshot captured yet
            self.assertTrue(diff.first_session)
            self.assertFalse(diff.has_changes)
            self.assertIn("first session", diff.summary_line())
            self.assertIsNone(changed_since_line(Surface.load(d)))   # no briefing noise

    def test_diff_lists_new_changed_memory_rules_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("keep", "v1"), assume_yes=True)
            store.remember(_ctx("changeme", "old"), assume_yes=True)
            store.close()
            capture_session_snapshot(Surface.load(d))      # baseline = {keep, changeme}

            # now mutate: add memory, add a rule, change a value, append a gate decision
            store = _store(d)
            store.remember(_ctx("brandnew", "v"), assume_yes=True)
            store.remember(_ctx("no-net", "no network in parser", kind=RULE), assume_yes=True)
            store.apply_proposal(
                HealingProposal(kind=CONTRADICTION, subject="changeme", mtype=PERSISTENT,
                                old=store.recall("changeme")[0], new=_ctx("changeme", "new"),
                                rationale="user edit"),
                "approve", assume_yes=True)
            store.close()
            AuditLedger.from_mokata_dir(Surface.load(d).mokata_dir).record(
                "write_gate", target="src/x.py", decision="declined", reason="risky")

            diff = compute_session_diff(Surface.load(d))
            self.assertFalse(diff.first_session)
            self.assertIn("brandnew", diff.new_memory)
            self.assertIn("no-net", diff.new_rules)
            self.assertIn("changeme", diff.changed_memory)
            self.assertGreaterEqual(diff.decision_count, 1)
            self.assertTrue(any("write gate" in ln for ln in diff.new_decisions))
            self.assertNotIn("keep", diff.new_memory + diff.changed_memory)   # unchanged

    def test_diff_is_read_only_no_stat_bump(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("a", "alpha"), assume_yes=True)
            store.close()
            capture_session_snapshot(Surface.load(d))
            before = _store(d).stats
            r0, w0 = before.reads, before.writes
            compute_session_diff(Surface.load(d))
            compute_session_diff(Surface.load(d))
            after = _store(d).stats
            self.assertEqual((after.reads, after.writes), (r0, w0))   # derive, never bump

    def test_snapshot_write_is_read_only_safe(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("a", "alpha"), assume_yes=True)
            store.close()
            w0 = _store(d).stats.writes
            path = capture_session_snapshot(Surface.load(d))
            # the snapshot lands in transient gitignored temp_local/, NOT the committed state
            self.assertIn("temp_local", path)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(_store(d).stats.writes, w0)   # capture bumps no memory stat

    def test_diff_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            _store(d).remember(_ctx("a", "alpha"), assume_yes=True)
            capture_session_snapshot(Surface.load(d))
            _store(d).remember(_ctx("b", "bravo"), assume_yes=True)
            a = compute_session_diff(Surface.load(d)).detail_lines()
            b = compute_session_diff(Surface.load(d)).detail_lines()
            self.assertEqual(a, b)

    def test_govern_view_carries_the_diff_and_renders_it(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            _store(d).remember(_ctx("a", "alpha"), assume_yes=True)
            capture_session_snapshot(Surface.load(d))
            _store(d).remember(_ctx("brandnew", "v"), assume_yes=True)
            view = build_governance_view(Surface.load(d))
            self.assertIsNotNone(view.session_diff)
            html_text = render_governance_html(view)
            self.assertIn("what changed since last session", html_text)
            self.assertIn("brandnew", html_text)

    def test_briefing_line_present_after_change_within_budget(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            _store(d).remember(_ctx("a", "alpha"), assume_yes=True)
            capture_session_snapshot(Surface.load(d))
            _store(d).remember(_ctx("brandnew", "v"), assume_yes=True)
            line = changed_since_line(Surface.load(d))
            self.assertIsNotNone(line)
            self.assertIn("since last session", line)
            briefing = build_bootstrap(Surface.load(d))
            self.assertIn("since last session", briefing.text)
            self.assertTrue(briefing.within_budget)        # frugal: still under 2k
            self.assertLessEqual(briefing.token_estimate, BOOTSTRAP_TOKEN_BUDGET)

    def test_fingerprint_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            _store(d).remember(_ctx("a", "alpha"), assume_yes=True)
            w0 = _store(d).stats.writes
            fp = build_state_fingerprint(Surface.load(d))
            self.assertIn("a", fp["memory"])
            self.assertEqual(_store(d).stats.writes, w0)


# ----------------------------------------------------------------- end-of-run why summary

class TestShipWhySummary(unittest.TestCase):
    def test_finish_decision_shows_why_summary_for_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            led.record("write_gate", target="src/auth.py", decision="approved",
                       reason="the auth refactor")
            led.record("deviation", target="plan", decision="approved",
                       reason="scope grew with the user's ok")
            decision = record_finish_decision(led, MERGE, approve=True)
            out = decision.render()
            self.assertIn("what I changed and why", out)
            self.assertIn("write gate", out)
            self.assertIn("the auth refactor", out)        # the WHY surfaces
            self.assertIn("merge", out)

    def test_ship_stays_human_gated_never_auto_merges(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            # no explicit approval -> not approved; mokata lands nothing
            decision = record_finish_decision(led, MERGE)
            self.assertFalse(decision.approved)
            self.assertIn("NOT approved", decision.render())
            self.assertIn("never", decision.render().lower())
            # the ledger recorded the decision but with approved=False
            finish = [e for e in led.entries() if e["kind"] == "finish"][0]
            self.assertFalse(finish["approved"])

    def test_summary_is_bounded_and_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            for i in range(40):
                led.record("write_gate", target=f"f{i}.py", decision="approved", reason="x")
            before = len(led.entries())
            summary = build_finish_summary(led)
            self.assertLessEqual(len(summary), 20)         # bounded tail (frugal), not all 40
            self.assertEqual(len(led.entries()), before)   # read-only — appended nothing

    def test_summary_degrades_clean_on_empty_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            decision = record_finish_decision(led, MERGE, approve=True)
            # only the finish row exists -> recap still renders, never raises
            self.assertIn("what I changed and why", decision.render())


if __name__ == "__main__":
    unittest.main()
