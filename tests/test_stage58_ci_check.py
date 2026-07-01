"""Stage 58 — CI / PR check ("mokata-as-a-check").

Run mokata's COMPLETENESS gate + SPEC-AWARENESS regression guard over a PR's changed files and
report PASS/BLOCK (non-zero exit on a real block) + a review-comment body. It REUSES the existing
engines (`run_completeness_gate`, `check_change`, `scan_tests`) — no logic is duplicated.

Inviolables proven here:
  * DEGRADE-CLEAN — with nothing to check (no saved spec, no corpus, an uninitialized repo, or a
    repo that doesn't tag tests with AC ids) the check PASSES; it NEVER false-blocks;
  * a REAL completeness gap (a saved spec with an AC that has no test) BLOCKS, with the single
    unblock action; a REAL spec-awareness conflict (the PR touches a saved spec) BLOCKS;
  * the comment body names what PASSED / what's BLOCKED + the unblock action (legibility verdicts);
  * the reusable composite action.yml is valid + the example workflow is least-privilege;
  * the new `ci-check` CLI surface is parity-clean (54e SURFACE_MATRIX + a read MCP tool).

Gates need no human here — the check is read-only (it SURFACES blocks for the reviewer; it never
posts to GitHub itself). EOF-safe; no real prompts.
"""

import io
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import ci_check as CI
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.engine.spec_gate import SPEC_STATE_KEY

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _init(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _save_spec(surface, title, ac_texts, *, symbol=""):
    """Persist an emitted spec (title + ACs). `symbol` is woven into the title so spec-awareness
    has a concrete surface to match a changed file/symbol against."""
    crit = [AcceptanceCriterion(id=f"AC-{i+1}", text=t) for i, t in enumerate(ac_texts)]
    spec = Spec(title=f"{title} ({symbol})" if symbol else title, criteria=crit)
    surface.state.write(SPEC_STATE_KEY, spec.to_dict())
    return spec


def _write(d, rel, text):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p) or d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel


# ============================================================ degrade-clean (never false-block)
class TestDegradeClean(unittest.TestCase):
    def test_no_saved_spec_no_corpus_passes(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            _write(d, "checkout.py", "def checkout(cart):\n    return sum(cart)\n")
            res = CI.run_ci_check(d, ["checkout.py"])
            self.assertFalse(res.blocked, "false block when there was nothing to check")
            self.assertEqual(res.exit_code, 0)
            self.assertTrue(all(leg.status in ("pass", "skip") for leg in res.legs))

    def test_uninitialized_repo_passes(self):
        with tempfile.TemporaryDirectory() as d:
            res = CI.run_ci_check(d, ["anything.py"])
            self.assertFalse(res.initialized)
            self.assertFalse(res.blocked)
            self.assertEqual(res.exit_code, 0)

    def test_saved_spec_but_no_ac_tagged_tests_skips_completeness(self):
        # A repo that doesn't use the AC-id-in-test convention must NOT be false-blocked.
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments", ["charge is idempotent", "fee is 2.9%"])
            _write(d, "test_pay.py", "def test_charge():\n    assert True\n")  # no AC ids
            res = CI.run_ci_check(d, ["test_pay.py"])
            comp = next(leg for leg in res.legs if leg.name == "completeness")
            self.assertEqual(comp.status, "skip")
            self.assertFalse(res.blocked)


# ============================================================ completeness gate (real block)
class TestCompletenessLeg(unittest.TestCase):
    def test_partial_coverage_blocks_with_unblock_action(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments", ["charge is idempotent", "fee is 2.9%"])
            # a test tags AC-1 only → AC-2 is unmapped → a REAL completeness gap
            _write(d, "test_pay.py",
                   "def test_idempotent():\n    'AC-1'\n    assert True\n")
            res = CI.run_ci_check(d, ["test_pay.py"])
            comp = next(leg for leg in res.legs if leg.name == "completeness")
            self.assertEqual(comp.status, "block")
            self.assertTrue(res.blocked)
            self.assertEqual(res.exit_code, 1)
            self.assertTrue(comp.unblock)

    def test_full_coverage_passes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments", ["charge is idempotent", "fee is 2.9%"])
            _write(d, "test_pay.py",
                   "def test_idempotent():\n    'AC-1'\n    assert True\n\n"
                   "def test_fee():\n    'AC-2'\n    assert True\n")
            res = CI.run_ci_check(d, ["test_pay.py"])
            comp = next(leg for leg in res.legs if leg.name == "completeness")
            self.assertEqual(comp.status, "pass")
            self.assertFalse(res.blocked)


# ============================================================ spec-awareness (real conflict)
class TestSpecAwarenessLeg(unittest.TestCase):
    def test_change_touching_a_saved_spec_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments spec", ["process_payment stays idempotent"],
                       symbol="process_payment")
            _write(d, "payments.py",
                   "def process_payment(amount):\n    return amount\n")
            res = CI.run_ci_check(d, ["payments.py"], changed_symbols=["process_payment"])
            sa = next(leg for leg in res.legs if leg.name == "spec-awareness")
            self.assertEqual(sa.status, "block")
            self.assertTrue(res.blocked)
            self.assertTrue(sa.unblock)
            self.assertTrue(any("Payments" in line for line in sa.detail))

    def test_unrelated_change_passes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments spec", ["process_payment stays idempotent"],
                       symbol="process_payment")
            _write(d, "ui.py", "def render_sidebar():\n    return ''\n")
            res = CI.run_ci_check(d, ["ui.py"], changed_symbols=["render_sidebar"])
            self.assertFalse(res.blocked)


# ============================================================ the review comment body
class TestCommentBody(unittest.TestCase):
    def test_comment_names_pass_block_and_unblock(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments", ["charge is idempotent", "fee is 2.9%"])
            _write(d, "test_pay.py", "def test_idempotent():\n    'AC-1'\n    assert True\n")
            body = CI.run_ci_check(d, ["test_pay.py"]).comment_body()
            self.assertIn("mokata", body)
            self.assertIn("BLOCKED", body)
            self.assertIn("completeness", body)
            self.assertIn("to unblock", body)

    def test_clean_pass_comment_is_reassuring_not_a_block(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            _write(d, "checkout.py", "def checkout(c):\n    return c\n")
            body = CI.run_ci_check(d, ["checkout.py"]).comment_body()
            self.assertIn("PASSED", body)
            self.assertNotIn("BLOCKED", body)


# ============================================================ symbols extracted from files
class TestSymbolExtraction(unittest.TestCase):
    def test_extracts_defs_and_classes(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "m.py", "import os\ndef alpha():\n    pass\nclass Beta:\n    pass\n")
            syms = CI.symbols_in_files(d, ["m.py"])
            self.assertIn("alpha", syms)
            self.assertIn("Beta", syms)


# ============================================================ CLI surface
class TestCliSurface(unittest.TestCase):
    def _run(self, argv):
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = so, se
        return rc, out.getvalue(), err.getvalue()

    def test_cli_passes_on_nothing_to_check(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            _write(d, "checkout.py", "def checkout(c):\n    return c\n")
            rc, out, err = self._run(["ci-check", "--files", "checkout.py", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("PASSED", out)

    def test_cli_blocks_and_writes_comment_file(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments", ["charge is idempotent", "fee is 2.9%"])
            _write(d, "test_pay.py", "def test_idempotent():\n    'AC-1'\n    assert True\n")
            cfile = os.path.join(d, "comment.md")
            rc, out, err = self._run(["ci-check", "--files", "test_pay.py",
                                      "--comment-file", cfile, "--path", d])
            self.assertEqual(rc, 1)
            self.assertTrue(os.path.exists(cfile))
            with open(cfile, encoding="utf-8") as fh:
                self.assertIn("BLOCKED", fh.read())

    def test_cli_no_fail_flag_exits_zero_even_on_block(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d)
            _save_spec(surface, "Payments", ["charge is idempotent", "fee is 2.9%"])
            _write(d, "test_pay.py", "def test_idempotent():\n    'AC-1'\n    assert True\n")
            rc, out, err = self._run(["ci-check", "--files", "test_pay.py",
                                      "--no-fail", "--path", d])
            self.assertEqual(rc, 0)              # report-only mode never fails the job
            self.assertIn("BLOCKED", out)


# ============================================================ the reusable action + workflow
class TestAction(unittest.TestCase):
    def test_action_yml_is_a_valid_composite_action(self):
        import yaml
        path = os.path.join(ROOT, ".github", "actions", "mokata-check", "action.yml")
        self.assertTrue(os.path.exists(path), "action.yml missing")
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        self.assertEqual(data["runs"]["using"], "composite")
        self.assertIn("inputs", data)
        self.assertIn("base", data["inputs"])

    def test_example_workflow_is_least_privilege(self):
        import yaml
        path = os.path.join(ROOT, ".github", "actions", "mokata-check", "example-pr-check.yml")
        self.assertTrue(os.path.exists(path), "example workflow missing")
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        perms = data["permissions"]
        self.assertEqual(perms.get("contents"), "read")
        self.assertEqual(perms.get("pull-requests"), "write")     # to post the comment
        self.assertNotIn("write-all", str(perms))


# ============================================================ parity
class TestParity(unittest.TestCase):
    def test_ci_check_in_matrix_and_read_tool_and_parity_passes(self):
        from mokata import parity, mcp_server as M
        self.assertIn("ci-check", parity.SURFACE_MATRIX)
        self.assertIn("ci_check", parity.SURFACE_MATRIX["ci-check"].mcp_read)
        self.assertIn("ci_check", M.read_tool_names())
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())


if __name__ == "__main__":
    unittest.main()
