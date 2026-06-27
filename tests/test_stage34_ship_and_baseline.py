"""Stage 34 — `ship` (finish) skill + clean-test-baseline check.

Both jsonschema states. `ship` is in the catalog (mokata · prefix, grounding clause);
`mokata run ship` emits the verify/summarize/present-options protocol; readiness BLOCKS when
tests/ACs/review are unsatisfied and is READY when they're met; the protocol never instructs
an unconfirmed merge/PR/delete; the finish decision is recorded in the ledger; the baseline
check reports green/red and degrades cleanly with no command.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import _support  # noqa: F401  (puts src/ on the path)

from mokata.baseline import (
    GREEN,
    RED,
    UNKNOWN,
    baseline_command,
    baseline_status,
)
from mokata.cli import main
from mokata.config import Surface
from mokata.engine import (
    LANDING_OPTIONS,
    AcceptanceCriterion,
    Spec,
    check_ship_readiness,
    record_finish_decision,
)
from mokata.engine.spec_gate import SPEC_STATE_KEY
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.manifest import Manifest
from mokata.skills import command_markdown, get_skill, skill_names
from mokata.state import StateStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silent(_):
    pass


def _store(d):
    return StateStore(os.path.join(d, "state"))


def _persist_spec(store, n=2):
    spec = Spec(title="x", criteria=[AcceptanceCriterion(f"AC-{i}", f"c{i}")
                                     for i in range(1, n + 1)])
    store.write(SPEC_STATE_KEY, spec.to_dict())


# ----------------------------------------------------------------- the skill

class TestShipSkill(unittest.TestCase):
    def test_ship_in_catalog_with_prefix(self):
        self.assertIn("ship", skill_names())
        skill = get_skill("ship")
        self.assertTrue(skill.summary.startswith("mokata ·"))
        self.assertTrue(skill.ground)                       # carries the grounding clause

    def test_run_ship_emits_protocol_and_grounding(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["run", "ship"])
        self.assertEqual(rc, 0)
        text = out.getvalue().lower()
        for token in ("verify", "summarize", "merge", "open a pr", "discard",
                      "grounding discipline"):
            self.assertIn(token, text)

    def test_protocol_never_instructs_unconfirmed_landing(self):
        prompt = get_skill("ship").prompt
        low = prompt.lower()
        # explicitly human-owned, only on confirmation
        self.assertIn("never merges", low)
        self.assertIn("explicit confirmation", low)
        self.assertIn("let the human choose", low)
        # the generated template carries the same (single source)
        with open(os.path.join(ROOT, "templates", "commands", "ship.md"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), command_markdown(get_skill("ship")))


# ----------------------------------------------------------------- readiness

class TestShipReadiness(unittest.TestCase):
    def test_blocks_when_tests_red(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _persist_spec(store)
            r = check_ship_readiness(store, tests_green=False, review_passed=True)
            self.assertFalse(r.ready)
            self.assertTrue(any("test suite" in b for b in r.blockers))

    def test_blocks_when_review_not_passed(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _persist_spec(store)
            r = check_ship_readiness(store, tests_green=True, review_passed=False)
            self.assertFalse(r.ready)
            self.assertTrue(any("review" in b for b in r.blockers))

    def test_blocks_when_no_spec(self):
        with tempfile.TemporaryDirectory() as d:
            r = check_ship_readiness(_store(d), tests_green=True, review_passed=True)
            self.assertFalse(r.ready)
            self.assertTrue(any("spec" in b for b in r.blockers))

    def test_ready_when_all_satisfied(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _persist_spec(store, n=3)
            r = check_ship_readiness(store, tests_green=True, review_passed=True)
            self.assertTrue(r.ready)
            self.assertEqual(r.blockers, [])
            self.assertEqual(r.spec_acs, 3)

    def test_readiness_and_finish_are_audited(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _persist_spec(store)
            led = AuditLedger(os.path.join(d, "ledger.jsonl"))
            check_ship_readiness(store, tests_green=True, review_passed=True, ledger=led)
            dec = record_finish_decision(led, "keep", approve=True, note="WIP branch")
            self.assertEqual(dec.choice, "keep")
            self.assertTrue(dec.approved)
            kinds = [e["kind"] for e in led.entries()]
            self.assertIn("ship", kinds)        # readiness recorded
            self.assertIn("finish", kinds)      # landing decision recorded
            finish = [e for e in led.entries() if e["kind"] == "finish"][-1]
            self.assertEqual(finish["choice"], "keep")
            self.assertTrue(finish["approved"])
            # H3 back-compat: the deprecated `confirmed=` alias still works
            self.assertTrue(record_finish_decision(None, "keep", confirmed=True).approved)

    def test_finish_rejects_unknown_choice(self):
        self.assertEqual(set(LANDING_OPTIONS), {"merge", "pr", "keep", "discard"})
        with self.assertRaises(ValueError):
            record_finish_decision(None, "rm-rf", confirmed=True)


# ----------------------------------------------------------------- baseline (Part B)

class TestBaseline(unittest.TestCase):
    def test_green_on_passing_command(self):
        r = baseline_status("true")
        self.assertEqual(r.state, GREEN)
        self.assertTrue(r.ok)
        self.assertIn("GREEN", r.render())

    def test_red_on_failing_command(self):
        r = baseline_status("false")
        self.assertEqual(r.state, RED)
        self.assertFalse(r.ok)
        self.assertIn("RED", r.render())

    def test_unknown_degrades_clean(self):
        r = baseline_status(None)
        self.assertEqual(r.state, UNKNOWN)
        self.assertTrue(r.ok)                    # degrade-clean: not a hard failure
        self.assertIn("no test command known", r.render())

    def test_command_resolution_prefers_override_then_settings(self):
        m = Manifest.from_dict({
            "manifest_version": 1, "mokata": {"version": "0"}, "profile": "custom",
            "layers": {"engine": {"enabled": True}}, "capabilities": {}, "tools": {},
            "settings": {"baseline": {"test_command": "pytest -q"}},
        })
        self.assertEqual(baseline_command(m), "pytest -q")
        self.assertEqual(baseline_command(m, override="make test"), "make test")
        self.assertIsNone(baseline_command(None))

    def test_cli_baseline_green_red_unknown(self):
        def run(argv):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = main(argv)
            return rc, out.getvalue()
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            rc, out = run(["baseline", "--cmd", "true", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("GREEN", out)
            rc, out = run(["baseline", "--cmd", "false", "--path", d])
            self.assertEqual(rc, 1)
            self.assertIn("RED", out)
            rc, out = run(["baseline", "--path", d])     # no command known
            self.assertEqual(rc, 0)
            self.assertIn("no test command known", out)


class TestDevelopBaselineClause(unittest.TestCase):
    def test_develop_prompt_references_green_baseline(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("green test baseline", low)
        self.assertIn("mokata baseline", low)


if __name__ == "__main__":
    unittest.main()
