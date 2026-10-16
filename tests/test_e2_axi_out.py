"""0.0.23 E2 — AXI-OUT items 2, 3, 7, 9, 10 + MOK-HOME + UX-STUCK (e).

  MOK-HOME  bare `mokata` is a home screen (live state + next step, exit 0), not an argparse error.
  (7)       agent-facing CLI errors (unknown command, an unloadable repo) are on STDOUT with the fix.
  (10)      every non-success MCP result / degraded marker carries a machine-readable
            `remedyCommand` derived from the text it already says; a success is byte-identical.
  (2)/(3)   list-shaped reads carry their count; an EMPTY one says why (the empty-state lint).
  (9)       a command template that reads `$ARGUMENTS` states its empty branch; every
            `mokata <cmd> [<sub>]` in a skill or template resolves in the real CLI parser.
  UX-STUCK  every shipped SKILL.md (curated + domain) carries `## If this appears stuck` with a
            runnable recovery command — enforced by the skill lints.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok

from mokata import axi_out as AX
from mokata import cli
from mokata import skill_lints as SL
from mokata.agent_skills import CURATED_SKILLS, RESILIENCE_HEADING
from mokata.domains import shipped_domain_skills
from mokata.init import init_repo


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
    finally:
        sys.stdin = saved
    return rc, out.getvalue(), err.getvalue()


def _init(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)


# ================================================================================ MOK-HOME
class TestHome(unittest.TestCase):
    def test_bare_mokata_in_an_uninitialized_dir_is_a_home_screen_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run(["--path", d])
        self.assertEqual((0, ""), (rc, err))
        self.assertIn("not set up", out)
        self.assertIn("mokata init", out)
        self.assertNotIn("arguments are required", out + err)

    def test_bare_mokata_in_an_initialized_repo_shows_live_state_and_next(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            rc, out, err = _run([f"--path={d}"])
        self.assertEqual((0, ""), (rc, err))
        for want in ("mode: local", "profile: standard", "run:", "approvals waiting on you: 0",
                     "gates:", "next:", "mokata doctor", "mokata --help"):
            self.assertIn(want, out)

    def test_pending_approvals_are_counted_and_point_at_the_list(self):
        from mokata import approval
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            approval.propose(d, tool="reset", args={"x": 1}, run_id="r1")
            rc, out, _e = _run(["--path", d])
        self.assertEqual(0, rc)
        self.assertIn("approvals waiting on you: 1", out)
        self.assertIn("mokata approve --list", out)

    def test_one_unreadable_fact_does_not_take_the_screen_down(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            with mock.patch("mokata.approval.pending", side_effect=RuntimeError("boom")):
                rc, out, _e = _run(["--path", d])
        self.assertEqual(0, rc)
        self.assertIn("approvals: could not be read", out)

    def test_help_and_version_are_unchanged(self):
        rc, out, _e = _run(["--help"])
        self.assertEqual(0, rc)
        self.assertIn("usage: mokata", out)
        rc, out, _e = _run(["--version"])
        self.assertEqual(0, rc)
        self.assertIn("mokata", out)


# ================================================================================ (7)
class TestErrorsOnStdout(unittest.TestCase):
    def test_unknown_command_is_on_stdout_with_the_fix_and_exit_2(self):
        rc, out, err = _run(["statuss", "--path", "."])
        self.assertEqual(2, rc)
        self.assertEqual("", err)
        self.assertIn("Did you mean 'status'", out)
        self.assertIn("Next:", out)

    def test_an_unloadable_repo_says_its_fix_on_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run(["status", "--path", d])
        self.assertEqual(1, rc)
        self.assertEqual("", err)
        self.assertIn("mokata init", out)


# ================================================================================ (10)
class TestRemedyCommand(unittest.TestCase):
    def test_extracts_the_exact_command(self):
        self.assertEqual("mokata approve --list",
                         AX.remedy_command("run `mokata approve --list` and say what it shows"))
        self.assertEqual("/mokata:mcp-repair", AX.remedy_command("try `/mokata:mcp-repair`."))
        self.assertEqual("mokata doctor", AX.remedy_command("then run mokata doctor."))
        self.assertIsNone(AX.remedy_command("nothing to run here"))
        self.assertIsNone(AX.remedy_command(None))

    def test_added_to_non_success_never_to_success(self):
        bad = AX.with_remedy({"status": "refused", "message": "run `mokata worktree list`"})
        self.assertEqual("mokata worktree list", bad["remedyCommand"])
        ok = {"status": "ok", "message": "run `mokata doctor`"}
        self.assertEqual({"status": "ok", "message": "run `mokata doctor`"}, AX.with_remedy(ok))
        explicit = AX.with_remedy({"status": "error", "message": "`mokata a`",
                                   "remedyCommand": "mokata b"})
        self.assertEqual("mokata b", explicit["remedyCommand"])
        self.assertNotIn("remedyCommand", AX.with_remedy({"status": "error", "message": "x"}))

    def test_degraded_marker_gets_one(self):
        out = AX.with_remedy({"items": [], "degraded": {"fix": "re-run `mokata sync` later"}})
        self.assertEqual("mokata sync", out["degraded"]["remedyCommand"])

    def test_through_the_mcp_dispatch_wrapper(self):
        from mokata.mcp.server import _serve

        def refuse(path="."):
            return {"status": "refused", "message": "fix it with `mokata doctor`"}

        def fine(path="."):
            return {"status": "ok", "message": "see `mokata doctor`"}
        with mock.patch("mokata.mcp.server._spawn_registration"), \
                mock.patch("mokata.mcp.server._emit_tool_call"):
            self.assertEqual("mokata doctor", _serve(refuse, "refuse")()["remedyCommand"])
            self.assertEqual({"status": "ok", "message": "see `mokata doctor`"},
                             _serve(fine, "fine")())

    def test_never_raises(self):
        class Weird(dict):
            def get(self, *_a, **_k):
                raise RuntimeError("x")
        w = Weird(status="error")
        self.assertIs(w, AX.with_remedy(w))


# ================================================================================ (2)/(3)
class TestEmptyStateLint(unittest.TestCase):
    def test_planted_offenders_are_found(self):
        self.assertTrue(AX.empty_state_findings({"items": []}, "items"))           # no count
        self.assertTrue(AX.empty_state_findings({"items": [], "count": 0}, "items"))  # silent
        self.assertTrue(AX.empty_state_findings({"items": [1, 2], "count": 1}, "items"))
        self.assertTrue(AX.empty_state_findings({"other": []}, "items"))
        self.assertEqual([], AX.empty_state_findings(
            {"items": [], "count": 0, "message": "nothing yet"}, "items"))
        self.assertEqual([], AX.empty_state_findings({"items": [1], "count": 1}, "items"))
        self.assertEqual([], AX.empty_state_findings(
            {"items": [], "count": 0, "degraded": {"fix": "x"}}, "items"))

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_the_list_reads_pass_on_an_empty_repo(self):
        from mokata.mcp import tools_read as R
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            cases = [
                (R.recall(path=d), "items"),
                (R.recall(path=d, query="deploy"), "items"),
                (R.recall(path=d, subject="nope"), "items"),
                (R.skills(path=d, query="zzz-no-such"), "skills"),
                (R.skills(path=d), "skills"),
                (R.suggest(path=d), "suggestions"),
                (R.sessions(path=d), "sessions"),
                (R.plan_list(path=d), "plans"),
                (R.session_list(path=d), "bundles"),
                (R.session_windows(path=d), "windows"),
                (R.audit(path=d), "entries"),
            ]
            for payload, key in cases:
                with self.subTest(key=key, payload=str(payload)[:80]):
                    self.assertEqual([], AX.empty_state_findings(payload, key))
            self.assertIn("proposal_count", R.rules(path=d))

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_a_non_empty_read_is_unchanged_but_counted(self):
        from mokata.mcp import tools_read as R
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            out = R.skills(path=d)
        self.assertEqual(len(out["skills"]), out["count"])
        self.assertNotIn("message", out)


# ================================================================================ (9)
class TestTemplatesAndCliParity(unittest.TestCase):
    def test_the_real_templates_and_skills_are_clean(self):
        findings = SL.run_template_lints()
        self.assertEqual([], findings, "\n".join(f.render() for f in findings))

    def test_a_template_without_an_empty_branch_is_flagged(self):
        bad = {"x": "run\n```bash\neval \"$ENGINE thing ${ARGUMENTS}\"\n```\n"}
        self.assertIn(SL.LINT_EMPTY_ARGS, {f.lint for f in SL.run_template_lints(bad)})
        good = {"x": bad["x"] + "If `$ARGUMENTS` is empty, infer it from the conversation.\n"}
        self.assertNotIn(SL.LINT_EMPTY_ARGS, {f.lint for f in SL.run_template_lints(good)})

    def test_cli_parity_flags_a_drifted_command_and_skips_output_lines(self):
        tree = {"worktree": frozenset({"list", "create"}), "doctor": frozenset()}
        self.assertEqual([], SL.cli_parity_findings("s", "run `mokata doctor`", tree))
        self.assertEqual([], SL.cli_parity_findings("s", "`mokata worktree list`", tree))
        self.assertEqual([], SL.cli_parity_findings("s", "it says `mokata review: FAILED`", tree))
        self.assertTrue(SL.cli_parity_findings("s", "run `mokata worktree nuke`", tree))
        self.assertTrue(SL.cli_parity_findings("s", "```bash\nmokata frobnicate\n```", tree))
        self.assertEqual([], SL.cli_parity_findings("s", "```bash\n# wire mokata into it\n```",
                                                    tree))

    def test_skill_lints_include_cli_parity(self):
        good = SL.load_shipped_skill_texts(("spec",))["spec"]
        bad = good + "\nAlso run `mokata frobnicate --now`.\n"
        self.assertIn(SL.LINT_CLI_PARITY, {f.lint for f in SL.run_lints({"spec": bad})})


# ================================================================================ UX-STUCK (e)
class TestResilienceLine(unittest.TestCase):
    def test_every_shipped_skill_md_carries_it(self):
        names = tuple(CURATED_SKILLS) + tuple(shipped_domain_skills())
        texts = SL.load_shipped_skill_texts(names)
        for name, text in texts.items():
            with self.subTest(skill=name):
                self.assertIn(RESILIENCE_HEADING, text)
        findings = [f for f in SL.run_lints(texts) if f.lint == SL.LINT_RESILIENCE]
        self.assertEqual([], findings)

    def test_curated_skills_render_it_from_one_source_with_their_own_command(self):
        texts = SL.load_shipped_skill_texts()
        for name, text in texts.items():
            with self.subTest(skill=name):
                self.assertIn(f"`/mokata:{name}`", text.split(RESILIENCE_HEADING, 1)[1])

    def test_a_missing_or_commandless_section_is_flagged(self):
        good = SL.load_shipped_skill_texts(("spec",))["spec"]
        stripped = good.replace(RESILIENCE_HEADING, "## Something else")
        self.assertIn(SL.LINT_RESILIENCE, {f.lint for f in SL.run_lints({"spec": stripped})})
        section = good.split(RESILIENCE_HEADING, 1)[1].split("\n## ", 1)[0]
        hollow = good.replace(section, "\n\nIf stuck, wait.\n")
        self.assertIn(SL.LINT_RESILIENCE, {f.lint for f in SL.run_lints({"spec": hollow})})

    def test_no_self_exemption_for_the_new_lints(self):
        self.assertEqual(frozenset(), SL.EXEMPTIONS[SL.LINT_RESILIENCE])
        self.assertEqual(frozenset(), SL.EXEMPTIONS[SL.LINT_CLI_PARITY])


if __name__ == "__main__":
    unittest.main()
