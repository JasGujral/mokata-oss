"""Stage 32 — the spec-persisted precondition for implementation.

Both jsonschema states. `develop`/`test` are blocked until a persisted spec with ≥1
acceptance criterion exists (`emitted_spec.json`); absent / empty / AC-less all block with
the actionable message; ≥1 AC passes. The block + pass are recorded in the audit ledger, and
the develop/test prompts + templates carry the "emit + save the spec first" clause.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import _support  # noqa: F401  (puts src/ on the path)

from mokata.cli import main
from mokata.config import Surface
from mokata.engine import (
    SPEC_PERSISTED_GATE_ID,
    SPEC_PERSISTED_MESSAGE,
    SPEC_STATE_KEY,
    AcceptanceCriterion,
    Spec,
    check_spec_persisted,
)
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.skills import command_markdown, get_skill
from mokata.state import StateStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silent(_):
    pass


def _store(d):
    return StateStore(os.path.join(d, "state"))


def _persist_spec(store, criteria):
    spec = Spec(title="login",
                criteria=[AcceptanceCriterion(i, t) for i, t in criteria])
    store.write(SPEC_STATE_KEY, spec.to_dict())


# ----------------------------------------------------------------- the gate function

class TestSpecPersistedGate(unittest.TestCase):
    def test_blocks_when_no_spec(self):
        with tempfile.TemporaryDirectory() as d:
            res = check_spec_persisted(_store(d))
            self.assertFalse(res.passed)
            self.assertEqual(res.reason, SPEC_PERSISTED_MESSAGE)
            self.assertEqual(res.ac_count, 0)
            self.assertEqual(res.gate_id, SPEC_PERSISTED_GATE_ID)

    def test_blocks_when_store_is_none(self):
        res = check_spec_persisted(None)
        self.assertFalse(res.passed)
        self.assertIn("no saved spec", res.reason)

    def test_blocks_on_empty_ac_less_spec(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _persist_spec(store, [])               # persisted but zero ACs
            res = check_spec_persisted(store)
            self.assertFalse(res.passed)
            self.assertEqual(res.reason, SPEC_PERSISTED_MESSAGE)

    def test_passes_with_at_least_one_ac(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _persist_spec(store, [("AC-1", "log in")])
            res = check_spec_persisted(store)
            self.assertTrue(res.passed)
            self.assertEqual(res.ac_count, 1)

    def test_block_and_pass_are_audited(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            led = AuditLedger(os.path.join(d, "ledger.jsonl"))
            blocked = check_spec_persisted(store, ledger=led, phase="develop")
            self.assertFalse(blocked.passed)
            _persist_spec(store, [("AC-1", "log in")])
            passed = check_spec_persisted(store, ledger=led, phase="develop")
            self.assertTrue(passed.passed)

            gate_entries = [e for e in led.entries()
                            if e["kind"] == "gate" and e["gate"] == SPEC_PERSISTED_GATE_ID]
            decisions = [e["decision"] for e in gate_entries]
            self.assertIn("blocked", decisions)
            self.assertIn("passed", decisions)


# ----------------------------------------------------------------- the prompts/templates

class TestImplementationPromptsClause(unittest.TestCase):
    def _both(self, name):
        prompt = get_skill(name).prompt
        with open(os.path.join(ROOT, "templates", "commands", f"{name}.md"),
                  encoding="utf-8") as fh:
            template = fh.read()
        return prompt, template

    def test_develop_and_test_say_emit_spec_first(self):
        for name in ("develop", "test"):
            prompt, template = self._both(name)
            for text in (prompt, template):
                low = text.lower()
                self.assertIn("emitted_spec.json", low)
                self.assertIn("emit the spec first", low.replace("produce + ", ""))
                self.assertIn("stop", low)
            # the rendered template also carries the spec-persisted precondition section
            self.assertIn("spec-persisted", template)

    def test_requires_spec_flag_only_on_impl_skills(self):
        self.assertTrue(get_skill("develop").requires_spec)
        self.assertTrue(get_skill("test").requires_spec)
        for name in ("brainstorm", "refine", "spec", "review", "debug", "optimize", "bug"):
            self.assertFalse(get_skill(name).requires_spec)

    def test_clause_is_single_source(self):
        for name in ("develop", "test"):
            with open(os.path.join(ROOT, "templates", "commands", f"{name}.md"),
                      encoding="utf-8") as fh:
                self.assertEqual(fh.read(), command_markdown(get_skill(name)))


# ----------------------------------------------------------------- CLI enforcement

class TestRunImplementationBlocked(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_run_develop_blocked_without_spec(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            rc, _out, err = self._run(["run", "develop", "--path", d])
            self.assertEqual(rc, 1)
            self.assertIn("spec-persisted", err)
            self.assertIn("/mokata:spec", err)

    def test_run_test_blocked_without_spec(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            rc, _out, err = self._run(["run", "test", "--path", d])
            self.assertEqual(rc, 1)
            self.assertIn("spec-persisted", err)

    def test_run_develop_passes_with_persisted_spec(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            surface = Surface.load(d)
            _persist_spec(surface.state, [("AC-1", "log in")])
            rc, out, _err = self._run(["run", "develop", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("develop", out.lower())

    def test_run_review_is_not_gated_by_spec(self):
        # a non-implementation skill runs without the precondition
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            rc, out, _err = self._run(["run", "review", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("review", out.lower())


if __name__ == "__main__":
    unittest.main()
