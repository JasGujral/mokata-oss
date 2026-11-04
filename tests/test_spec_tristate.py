"""0.0.27 SPEC-TRISTATE -- `ci_check`'s spec legs are pass / fail / ABSENT, everywhere they are read.

The rule: a leg with nothing to check is ABSENT. It is never counted, rendered or exported as a
pass. It also never fails the job (exit 0: a PR gate must not false-block a repo that simply has no
committed specs) -- "did not fail" and "passed" are different facts, and every surface says which.

Surfaces graded: the CLI render, the PR comment, the evidence file, the count summary a long comment
falls back to, the MCP `ci_check` structure, the exit code. Plus the cancel path (no timer).

Business-level, over real repos (`test_l7b_committed_specs._Repo`):
  * no committed specs        -> absent, visibly, on every surface
  * committed and clean       -> pass
  * committed and drifted     -> fail
  * absent mixed with a pass  -> PASSED (PARTIAL ...), never a bare PASSED / green tick

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import threading
import unittest
from unittest import mock

import _support  # noqa: F401
from test_l7b_committed_specs import _Repo

from mokata import ci_check as CI
from mokata import ci_evidence as CE
from mokata.ci_check import CheckLeg, CICheckResult, run_ci_check
from mokata.knowledge import cancel as C
from mokata.mcp import tools_read as TR

NOT_CHECKED = "no committed specs — spec drift not checked"


def _drift(res):
    return next(leg for leg in res.legs if leg.name == "spec-drift")


def _surfaces(res):
    """Every rendering of one result, by name."""
    return {"render": res.render(), "ascii": res.render(ascii_only=True),
            "comment": res.comment_body(), "evidence": res.evidence_markdown(),
            "counts": CE.verdict_counts(res, "ptr")}


class TestNoCommittedSpecsIsAbsentEverywhere(unittest.TestCase):

    def _fresh(self):
        repo = _Repo()
        self.addCleanup(repo.__exit__, None, None, None)
        repo.emit()
        repo.fresh_checkout()           # what CI sees: no run state, nothing committed
        return repo

    def test_the_leg_is_absent_not_pass_and_the_headline_is_not_a_pass(self):
        res = run_ci_check(self._fresh().path, ["src/pay.py"])
        self.assertEqual("absent", _drift(res).status)
        self.assertFalse(_drift(res).passed)
        self.assertTrue(all(leg.status == "absent" for leg in res.legs))
        self.assertFalse(res.checked)
        self.assertEqual("NOT CHECKED", res.overall)
        self.assertNotIn("PASSED", res.overall)
        self.assertEqual(0, res.exit_code, "absent never fails the job")
        self.assertFalse(res.blocked)

    def test_every_surface_says_so_and_none_says_pass(self):
        res = run_ci_check(self._fresh().path, ["src/pay.py"])
        for name, text in _surfaces(res).items():
            self.assertNotIn("PASSED", text, name)
            self.assertNotIn("✅", text, name)
            self.assertNotIn("Nothing for mokata to flag", text, name)
            self.assertIn("NOT CHECKED", text, name)
        for name in ("render", "ascii", "comment", "evidence"):
            self.assertIn(NOT_CHECKED, _surfaces(res)[name], name)
        self.assertIn("[absent]", res.render(ascii_only=True))
        self.assertIn("**ABSENT**", CE.verdict_counts(res, "ptr"))

    def test_mcp_exports_absent_and_checked_false(self):
        repo = self._fresh()
        out = TR.ci_check(path=repo.path, files="src/pay.py")
        self.assertEqual("NOT CHECKED", out["overall"])
        self.assertIs(False, out["checked"])
        self.assertIs(False, out["blocked"])
        self.assertEqual(0, out["exit_code"])
        self.assertIn("spec-drift", out["absent"])
        self.assertEqual({"absent"}, {leg["status"] for leg in out["legs"]})
        self.assertNotIn("PASSED", out["comment_body"])

    def test_uninitialized_repo_is_not_checked_either(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            res = run_ci_check(d, ["a.py"])
        self.assertEqual("NOT CHECKED", res.overall)
        self.assertEqual(0, res.exit_code)
        for name, text in _surfaces(res).items():
            if name != "counts":
                self.assertNotIn("PASSED", text, name)
                self.assertNotIn("✅", text, name)
                self.assertNotIn("Passing", text, name)


class TestPassFailAbsentAreThreeDifferentThings(unittest.TestCase):

    def test_committed_and_clean_is_pass(self):
        with _Repo(commit=True) as repo:
            repo.emit()
            repo.fresh_checkout()
            res = run_ci_check(repo.path, ["src/pay.py"])
        self.assertEqual("pass", _drift(res).status)
        self.assertTrue(_drift(res).passed)
        self.assertTrue(res.checked)
        self.assertNotIn("spec-drift", res.absent_legs)
        self.assertNotIn(NOT_CHECKED, res.render())

    def test_committed_and_drifted_is_fail(self):
        with _Repo(commit=True) as repo:
            repo.emit()
            repo.fresh_checkout()
            with open(os.path.join(repo.path, "src", "pay.py"), "w", encoding="utf-8") as fh:
                fh.write("def bill(card):\n    return card\n")
            res = run_ci_check(repo.path, ["src/pay.py"])
        self.assertEqual("block", _drift(res).status)
        self.assertEqual("BLOCKED", res.overall)
        self.assertEqual(1, res.exit_code)
        self.assertEqual("🛑", res.icon)

    def test_a_pass_beside_an_absent_leg_is_PARTIAL_never_a_clean_pass(self):
        res = CICheckResult(legs=[CheckLeg("completeness", "pass", "every AC maps to a test"),
                                  CheckLeg("spec-drift", "absent", NOT_CHECKED)],
                            initialized=True, root=".")
        self.assertTrue(res.checked)
        self.assertEqual(["spec-drift"], res.absent_legs)
        self.assertEqual("PASSED (PARTIAL: spec-drift not checked)", res.overall)
        self.assertNotEqual("✅", res.icon)
        self.assertEqual(0, res.exit_code)
        for name, text in _surfaces(res).items():
            self.assertNotIn("✅", text, name)
            self.assertNotIn("Nothing for mokata to flag", text, name)
        self.assertIn("Not checked", res.comment_body())
        self.assertIn("**ABSENT**", CE.verdict_counts(res, "ptr"))

    def test_a_full_pass_keeps_the_green_tick(self):
        res = CICheckResult(legs=[CheckLeg("completeness", "pass", "ok"),
                                  CheckLeg("spec-drift", "pass", "ok")], initialized=True, root=".")
        self.assertEqual("PASSED", res.overall)
        self.assertEqual("✅", res.icon)
        self.assertIn("Nothing for mokata to flag", res.comment_body())

    def test_absent_beside_a_block_still_blocks_and_still_names_the_absent_leg(self):
        res = CICheckResult(legs=[CheckLeg("completeness", "block", "AC1 has no test"),
                                  CheckLeg("spec-drift", "absent", NOT_CHECKED)],
                            initialized=True, root=".")
        self.assertEqual("BLOCKED", res.overall)
        self.assertEqual(1, res.exit_code)
        self.assertIn("Not checked", res.comment_body())


def _git(path, *args):
    import subprocess
    subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)


def _awareness(res):
    return next(leg for leg in res.legs if leg.name == "spec-awareness")


class TestNoChangeSetIsNeverAGreenTick(unittest.TestCase):
    """H1 -- an empty / unresolved change set used to grade spec-awareness "pass" and show PASSED."""

    def _repo(self):
        repo = _Repo(commit=True)
        self.addCleanup(repo.__exit__, None, None, None)
        repo.emit()
        repo.fresh_checkout()
        return repo

    def _not_a_pass(self, text, where):
        self.assertNotIn("PASSED ✅", text, where)
        self.assertNotIn("✅", text, where)
        self.assertNotIn("Nothing for mokata to flag", text, where)

    def test_no_files_at_all_makes_the_spec_leg_absent_and_the_run_not_a_clean_pass(self):
        res = run_ci_check(self._repo().path, [])
        leg = _awareness(res)
        self.assertEqual("absent", leg.status)
        self.assertIn("no changed files supplied", leg.summary)
        self.assertIn("spec-awareness", res.absent_legs)
        self.assertIn("PARTIAL", res.overall)
        for name, text in _surfaces(res).items():
            self._not_a_pass(text, name)
        self.assertIn("no changed files supplied", res.render())

    def test_with_files_the_leg_is_still_graded(self):
        res = run_ci_check(self._repo().path, ["src/pay.py"])
        self.assertNotEqual("absent", _awareness(res).status)

    def test_explicit_symbols_without_files_are_still_a_change_set(self):
        res = run_ci_check(self._repo().path, [], changed_symbols=["charge"])
        self.assertNotEqual("absent", _awareness(res).status)

    def test_mcp_with_empty_files_is_absent_not_passed(self):
        out = TR.ci_check(path=self._repo().path, files="")
        self.assertIn("spec-awareness", out["absent"])
        self.assertNotIn("PASSED ✅", out["overall"])
        self.assertIn("PARTIAL", out["overall"])
        self.assertNotIn("✅", out["comment_body"])
        leg = next(x for x in out["legs"] if x["name"] == "spec-awareness")
        self.assertEqual("absent", leg["status"])

    def test_cli_without_files_or_base_is_partial_and_exits_zero(self):
        code, out = self._repo().cli("ci-check")
        self.assertEqual(0, code, out)
        self.assertIn("PARTIAL", out)
        self._not_a_pass(out, "cli")

    def test_cli_unresolvable_base_is_a_refusal_naming_the_ref_and_the_fix(self):
        repo = self._repo()
        _git(repo.path, "init", "-q")
        for extra in ([], ["--no-fail"]):
            code, out = repo.cli("ci-check", "--base", "nonexistent-ref-xyz", *extra)
            self.assertEqual(2, code, out)
            self.assertIn("nonexistent-ref-xyz", out)
            self.assertIn("git fetch", out)
            self.assertNotIn("mokata PR check", out, "it must not reach grading")
            self.assertNotIn("PASSED", out)

    def test_cli_base_outside_a_git_repo_is_a_refusal_too(self):
        code, out = self._repo().cli("ci-check", "--base", "origin/main")
        self.assertEqual(2, code, out)
        self.assertIn("origin/main", out)

    def test_git_missing_or_timing_out_is_a_refusal_too(self):
        import subprocess
        repo = self._repo()
        for exc in (OSError("no git"), subprocess.TimeoutExpired("git", 30)):
            with mock.patch("subprocess.run", side_effect=exc):
                code, out = repo.cli("ci-check", "--base", "main")
            self.assertEqual(2, code, out)
            self.assertNotIn("PASSED", out)

    def test_a_resolved_but_empty_diff_is_ABSENT_by_choice(self):
        """CHOICE (c): a genuinely empty diff is made absent rather than a pass. Spec-awareness
        compares a change set with the corpus; with no change there is nothing it compared, so
        "no spec is affected" would be a claim about work it did not do."""
        repo = self._repo()
        _git(repo.path, "init", "-q")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-qm", "base")
        code, out = repo.cli("ci-check", "--base", "HEAD")
        self.assertEqual(0, code, out)
        self.assertIn("no changed files supplied", out)
        self.assertIn("PARTIAL", out)
        self._not_a_pass(out, "cli")

    def test_a_resolved_diff_with_a_change_is_graded(self):
        repo = self._repo()
        _git(repo.path, "init", "-q")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-qm", "base")
        with open(os.path.join(repo.path, "src", "pay.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# edit\n")
        _git(repo.path, "commit", "-qam", "edit")
        code, out = repo.cli("ci-check", "--base", "HEAD~1")
        self.assertNotIn("no changed files supplied", out)


class TestTheLegIsCancellableWithoutATimer(unittest.TestCase):

    def test_a_flag_set_before_the_run_ends_it_at_the_first_checkpoint(self):
        with _Repo() as repo:
            flag = threading.Event()
            flag.set()
            with C.scope(flag), mock.patch.object(CI, "_completeness_leg") as leg:
                with self.assertRaises(C.Cancelled):
                    run_ci_check(repo.path, ["src/pay.py"])
            leg.assert_not_called()

    def test_a_cancel_between_legs_stops_the_remaining_legs_and_returns_no_result(self):
        with _Repo() as repo:
            flag = threading.Event()
            ran = []

            def first(*_a):
                ran.append("completeness")
                flag.set()                      # the user presses Cancel mid-leg
                return CheckLeg("completeness", "pass", "ok")

            with C.scope(flag), mock.patch.object(CI, "_completeness_leg", first), \
                    mock.patch.object(CI, "_spec_awareness_leg") as aware, \
                    mock.patch.object(CI, "_committed_specs_leg") as drift:
                with self.assertRaises(C.Cancelled):
                    run_ci_check(repo.path, ["src/pay.py"])
            self.assertEqual(["completeness"], ran)
            aware.assert_not_called()
            drift.assert_not_called()

    def test_a_cancel_inside_the_test_walk_stops_it(self):
        from mokata.engine.acmapper import scan_tests
        with _Repo() as repo:
            flag = threading.Event()
            flag.set()
            with C.scope(flag):
                with self.assertRaises(C.Cancelled):
                    scan_tests(repo.path, ["AC1"])

    def test_outside_a_job_nothing_changes(self):
        with _Repo() as repo:
            self.assertEqual(0, run_ci_check(repo.path, ["src/pay.py"]).exit_code)


# ===================================================== R2-M1: the refusal (exit 2) downstream
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTION = os.path.join(ROOT, ".github", "actions", "mokata-check", "action.yml")
HOWTO = os.path.join(ROOT, "docs", "how-to", "mokata-as-a-pr-check.md")
CLI_REF = os.path.join(ROOT, "docs", "reference", "cli.md")
EXIT2_PHRASE = "unless the check could not run"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestTheRefusalIsPublishedAsItsOwnOutput(unittest.TestCase):
    """action.yml runs the real script with a stub `mokata` that exits with a chosen code."""

    def _run(self, rc, *, write_comment):
        import re
        import shutil
        import subprocess
        import tempfile
        bash = shutil.which("bash") if os.name != "nt" else None
        if not bash:
            self.skipTest("the composite step is a bash script")
        script = _read(ACTION).split("      run: |\n", 1)[1]
        script = "\n".join(ln[8:] for ln in script.splitlines())
        with tempfile.TemporaryDirectory() as tmp:
            vals = {"inputs.install": "", "inputs.base": "no-such-ref", "inputs.path": tmp,
                    "inputs.comment-file": os.path.join(tmp, "comment.md"),
                    "inputs.evidence-file": os.path.join(tmp, "evidence.md"),
                    "inputs.fail-on-block": "true"}
            script = re.sub(r"\$\{\{\s*([\w.\-]+)\s*\}\}", lambda m: vals[m.group(1)], script)
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)
            stub = os.path.join(bindir, "mokata")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n"
                         + ('echo "REASON-FROM-CLI no-such-ref fetch-depth: 0" > "%s"\n' % vals["inputs.comment-file"]
                            if write_comment else "")
                         + "exit %d\n" % rc)
            os.chmod(stub, 0o755)
            out, summ = os.path.join(tmp, "out"), os.path.join(tmp, "summary")
            env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"],
                       GITHUB_OUTPUT=out, GITHUB_STEP_SUMMARY=summ)
            proc = subprocess.run([bash, "-c", script], env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, cwd=tmp)
            outputs = dict(ln.split("=", 1) for ln in _read(out).splitlines())
            return proc.returncode, outputs, (_read(summ) if os.path.exists(summ) else "")

    def test_a_verdict_is_neither_blocked_nor_refused(self):
        rc, out, _ = self._run(0, write_comment=False)
        self.assertEqual((0, "false", "false"), (rc, out["blocked"], out["refused"]))

    def test_a_real_block_is_blocked_and_not_refused(self):
        rc, out, _ = self._run(1, write_comment=False)
        self.assertEqual((1, "true", "false"), (rc, out["blocked"], out["refused"]))

    def test_a_refusal_is_refused_never_blocked_and_still_fails_the_step(self):
        rc, out, _ = self._run(2, write_comment=True)
        self.assertEqual(2, rc, "a check that could not run must not look like a pass")
        self.assertEqual("true", out["refused"])
        self.assertEqual("false", out["blocked"])

    def test_the_step_summary_names_the_ref_and_the_fix_in_plain_words(self):
        for write in (True, False):               # the CLI's comment, or the script's own fallback
            _, _, summary = self._run(2, write_comment=write)
            self.assertIn("NOT RUN", summary)
            self.assertIn("no-such-ref", summary)
            self.assertIn("fetch-depth: 0", summary)
            self.assertIn("REASON-FROM-CLI" if write else "nothing was checked", summary)

    def test_the_action_declares_the_refused_output(self):
        self.assertRegex(_read(ACTION), r"(?m)^  refused:\n")


class TestTheCliRefusalWritesTheCommentAndSaysTheFix(unittest.TestCase):

    def test_the_comment_file_names_the_ref_and_the_shallow_checkout_fix(self):
        repo = _Repo(commit=True)
        self.addCleanup(repo.__exit__, None, None, None)
        cf = os.path.join(repo.path, "c.md")
        code, out = repo.cli("ci-check", "--base", "nope-ref", "--comment-file", cf, "--no-fail")
        self.assertEqual(2, code, out)
        body = _read(cf)
        for text in (body, out):
            self.assertIn("nope-ref", text)
            self.assertIn("fetch-depth: 0", text)
            self.assertIn("NOTHING was checked", text)
            self.assertNotIn("PASSED", text)
        self.assertIn("NOT RUN", body)


class TestNoFailWordingSaysExitTwo(unittest.TestCase):

    def test_help_and_both_docs_carry_the_exit_two_rule(self):
        import io
        from contextlib import redirect_stdout
        from mokata.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            main(["ci-check", "--help"])
        help_text = " ".join(buf.getvalue().split())
        self.assertIn(EXIT2_PHRASE, help_text)
        self.assertIn("exits 2", help_text)
        for path in (CLI_REF, HOWTO):
            self.assertIn(EXIT2_PHRASE, " ".join(_read(path).split()), path)

    def test_nothing_still_promises_no_fail_always_exits_zero(self):
        import re
        bad = re.compile(r"always exit 0|never fails the job|\(0 with `--no-fail`\)", re.I)
        for path in (CLI_REF, HOWTO, ACTION,
                     os.path.join(ROOT, "src", "mokata", "cli_commands", "knowledge.py")):
            self.assertIsNone(bad.search(_read(path)), path)


if __name__ == "__main__":
    unittest.main()
