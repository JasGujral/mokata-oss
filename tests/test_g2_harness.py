"""G2 proof engine (0.0.23 D7) — the eval harness, exercised end to end with a FAKE agent and a
FAKE judge (no network, no `claude`, no API key), plus the packaging boundary that keeps it out of
the wheel. Exit criterion 5: "the eval harness reproduces from a fresh clone" + NO-API-KEY-GUARD
green with G2 present (see test_no_api_key_guard.py).
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _g2_fixture as FX

from bench.g2 import __main__ as cli
from bench.g2 import conditions as C
from bench.g2.agent import SCRUBBED_ENV, ClaudeCodeAgent, scrubbed_env
from bench.g2.archive import ArchiveError, RunArchive
from bench.g2.judge import (JUDGE_KEY_ENV, AnthropicApiJudge, JudgeUnavailable, make_judge,
                            parse_verdict)
from bench.g2.metrics import aggregate
from bench.g2.streamparse import parse_stream
from bench.g2.study import FIXTURE_BANNER, render_study
from bench.g2.tasks import MECHANISMS, TaskError, load_tasks, task_from_dict
from bench.g2.workspace import GitCloner

ROOT = FX.ROOT



def _can_build_wheels():
    """`pip wheel --no-build-isolation` uses THIS interpreter's setuptools. setuptools < 70.1 has
    no `bdist_wheel` of its own and needs the `wheel` package — absent on e.g. the Windows
    py3.10 runner image (found at the 0.0.23 cut). Without either, the build cannot run here."""
    import importlib.util
    if importlib.util.find_spec("setuptools") is None:
        return False
    if importlib.util.find_spec("wheel") is not None:
        return True
    try:
        from importlib.metadata import version
        parts = [int(x) for x in version("setuptools").split(".")[:2]]
    except Exception:  # noqa: BLE001 — unknown version: cannot promise a build
        return False
    return tuple(parts) >= (70, 1)

class TestStreamParse(unittest.TestCase):
    def test_turns_group_by_message_id_and_context_is_summed_usage(self):
        ev = [FX.init_event("bare"),
              FX.assistant("a", 1000, text="hi", cache_read=600),
              FX.assistant("a", 1000, tools=[{"name": "Read"}], cache_read=600),  # same call, split
              FX.tool_result("a-t0", "file body"),
              {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"pre_tokens": 9}},
              FX.assistant("b", 1500), "not json", {"type": "mystery"}, FX.result_event(2)]
        tr = parse_stream([e if isinstance(e, str) else json.dumps(e) for e in ev])
        self.assertEqual(tr.num_turns, 2)
        self.assertEqual(tr.context_per_turn, [1000, 1500])
        self.assertEqual(tr.total_context_tokens, 2500)
        self.assertEqual(tr.growth_per_turn, 500.0)
        self.assertEqual(tr.compactions, [1])
        self.assertEqual(tr.unparsed, 2)                       # counted, never silently dropped
        self.assertEqual(tr.tool_names, ["Read"])
        self.assertIn("file body", tr.transcript())

    def test_summary_names_cost_notional_and_flags_a_missing_result(self):
        tr = parse_stream([json.dumps(FX.assistant("a", 10))])
        s = tr.summary()
        self.assertIn("notional_cost_usd", s)
        self.assertTrue(s["is_error"])                         # no result event = not a success


class TestMatrixRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.agent_calls = []

        def script(workdir, prompt, condition, repeat):
            cls.agent_calls.append({"workdir": workdir, "files": sorted(os.listdir(workdir))})
            return FX.default_script(workdir, prompt, condition, repeat)
        cls.out = FX.build_fixture_archive(os.path.join(cls.tmp, "run"), repeats=5, script=script)
        cls.arch = RunArchive(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_layout_is_condition_task_runN_with_raw_stream_and_verdict(self):
        for c in ("bare", "mokata"):
            for t in self.arch.task_ids:
                for n in range(1, 6):
                    d = os.path.join(self.out, c, t, f"run{n}")
                    for f in ("stream.jsonl", "trajectory.json", "verdict.json", "judge_raw.txt",
                              "meta.json"):
                        self.assertTrue(os.path.exists(os.path.join(d, f)), (d, f))

    def test_fixture_kind_is_derived_not_declared(self):
        self.assertEqual(self.arch.kind, "fixture")
        self.assertFalse(self.arch.is_real)
        self.assertEqual(self.arch.validate(require_real=False), [])
        self.assertTrue(any("not a real run" in p for p in self.arch.validate(require_real=True)))
        with self.assertRaises(ArchiveError):
            self.arch.require_real()

    def test_a_hand_edited_real_kind_is_caught(self):
        tmp = tempfile.mkdtemp()
        try:
            dst = os.path.join(tmp, "run")
            shutil.copytree(self.out, dst)
            with open(os.path.join(dst, "run.json"), encoding="utf-8") as fh:
                m = json.load(fh)
            m["kind"] = "real"
            with open(os.path.join(dst, "run.json"), "w", encoding="utf-8") as fh:
                json.dump(m, fh)
            problems = RunArchive(dst).validate(require_real=True)
            self.assertTrue(any("agent/judge/cloner make it 'fixture'" in p for p in problems))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_run_got_its_own_fresh_workdir_removed_after(self):
        workdirs = [c["workdir"] for c in self.agent_calls]
        self.assertEqual(len(workdirs), len(set(workdirs)))
        self.assertEqual(len(workdirs), 2 * len(self.arch.task_ids) * 5)
        self.assertFalse(any(os.path.exists(w) for w in workdirs))

    def test_repeats_are_interleaved_across_conditions(self):
        # every run1 (of every cell) is executed before any run2, and so on
        order = sorted(self.arch.records, key=lambda r: r.meta["seq"])
        self.assertEqual([r.repeat for r in order], sorted(r.repeat for r in order))
        self.assertEqual([r.condition for r in order[:2]], ["bare", "mokata"])

    def test_the_judge_sees_the_full_trajectory_not_the_condition_label(self):
        rec = self.arch.cell("mokata", self.arch.task_ids[0])[0]
        self.assertEqual(rec.verdict["status"], "graded")
        self.assertEqual(rec.verdict["judge_id"], "fake-judge")

    def test_counters_derive_from_the_archive(self):
        agg = aggregate(self.arch)
        pc = agg["per_condition"]
        n_tasks = len(self.arch.task_ids)
        self.assertEqual(pc["mokata"]["secrets_blocked"], n_tasks)       # one block in each run1
        self.assertEqual(pc["bare"]["secrets_blocked"], 0)
        # one task has a `defect` criterion; the fixture judge passes it in all 5 repeats × 2 conds
        self.assertEqual(pc["mokata"]["defects_caught"], 5)
        # bare re-sends 9000+11000+13000=33000/run, mokata 12000+12900+13800=38700 → negative, signed
        self.assertEqual(pc["mokata"]["tokens_saved_vs_bare"], (33000 - 38700) * n_tasks)
        self.assertIsNone(pc["bare"]["tokens_saved_vs_bare"])
        self.assertEqual(set(agg["per_mechanism"]), set(MECHANISMS))

    def test_study_md_is_generated_and_bannered_as_fixture(self):
        text = render_study(self.arch)
        self.assertTrue(text.startswith("# G2 study"))
        self.assertIn(FIXTURE_BANNER.strip(), text)
        self.assertIn("## Per cell (medians over repeats)", text)
        self.assertIn("python -m bench.g2 study --run", text)
        self.assertIn("not a real run", text)                 # validation problems printed IN it

    def test_an_archive_is_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            FX.build_fixture_archive(self.out, repeats=1)


class TestContamination(unittest.TestCase):
    def test_bare_run_with_mokata_present_is_refused(self):
        def leaky(workdir, prompt, condition, repeat):
            ev = FX.default_script(workdir, prompt, condition, repeat)
            ev[0] = FX.init_event("mokata")                  # mokata leaked into the bare run
            return ev
        tmp = tempfile.mkdtemp()
        try:
            out = FX.build_fixture_archive(os.path.join(tmp, "run"), repeats=1, script=leaky,
                                           task_ids=["gates-tdd-quantity-guard"])
            problems = RunArchive(out).validate(require_real=False)
            self.assertTrue(any("CONTAMINATED" in p and p.startswith("bare/") for p in problems),
                            problems)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mokata_condition_without_the_server_is_contamination(self):
        tr = parse_stream([json.dumps(FX.init_event("bare"))])
        self.assertTrue(C.mokata_on().check(tr))
        self.assertEqual(C.bare().check(tr), [])

    def test_bare_is_required(self):
        with self.assertRaises(ValueError):
            C.resolve(["mokata"])

    def test_conditions_isolate_user_scope(self):
        tmp = tempfile.mkdtemp()
        try:
            wd, rd = os.path.join(tmp, "w"), os.path.join(tmp, "r")
            os.makedirs(wd)
            os.makedirs(rd)
            args = C.bare().prepare(wd, rd, runner=lambda a, c: 0)
            self.assertIn("--strict-mcp-config", args)
            self.assertEqual(args[args.index("--setting-sources") + 1], "project,local")
            with open(os.path.join(wd, "CLAUDE.md"), encoding="utf-8") as fh:
                self.assertIn("Working agreement", fh.read())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestJudgeAndAgentBoundary(unittest.TestCase):
    def test_api_judge_refuses_loudly_without_its_key(self):
        with self.assertRaises(JudgeUnavailable) as cm:
            AnthropicApiJudge.from_env("some-model", env={})
        msg = str(cm.exception)
        self.assertIn(JUDGE_KEY_ENV, msg)
        self.assertIn("mokata itself never needs a key", msg)

    def test_api_judge_never_reads_the_generic_anthropic_key(self):
        with self.assertRaises(JudgeUnavailable):
            AnthropicApiJudge.from_env("m", env={"ANTHROPIC_API_KEY": "sk-x"})

    def test_api_judge_must_be_declared(self):
        with self.assertRaises(JudgeUnavailable):
            AnthropicApiJudge.from_env(None, env={JUDGE_KEY_ENV: "k"})
        j = make_judge("anthropic-api", "m", env={JUDGE_KEY_ENV: "k"})
        self.assertEqual((j.id, j.model), ("anthropic-api", "m"))

    def test_unparsable_judge_output_is_ungradable_not_fail(self):
        task = load_tasks(only=["gates-tdd-quantity-guard"])[0]
        v = parse_verdict(task, "I think it went well!", "x", "m")
        self.assertEqual(v.status, "ungradable")
        self.assertIsNone(v.overall_pass)

    def test_overall_pass_is_derived_from_outcome_criteria(self):
        task = load_tasks(only=["gates-tdd-quantity-guard"])[0]
        raw = json.dumps({"criteria": {"rejects-negative": {"pass": True},
                                       "red-before-green": {"pass": False}}, "overall_pass": False})
        v = parse_verdict(task, raw, "x", "m")
        self.assertTrue(v.overall_pass)                       # process miss does not fail outcome

    def test_agent_env_is_scrubbed_of_api_keys(self):
        env = scrubbed_env({k: "x" for k in SCRUBBED_ENV} | {"PATH": "/bin"})
        self.assertEqual(env, {"PATH": "/bin"})

    def test_agent_runs_claude_headless_with_stream_json(self):
        argv = ClaudeCodeAgent().argv("do it", 12, ["--x"])
        self.assertEqual(argv[:3], ["claude", "-p", "do it"])
        for flag in ("--output-format", "stream-json", "--verbose", "--max-turns", "12", "--x"):
            self.assertIn(flag, argv)


class TestTasks(unittest.TestCase):
    def test_shipped_tasks_cover_every_mechanism(self):
        tasks = load_tasks()
        self.assertEqual({t.mechanism for t in tasks}, set(MECHANISMS))
        for t in tasks:
            self.assertTrue(os.path.isdir(t.template_dir()), t.id)

    def test_an_unpinned_url_repo_is_rejected(self):
        with self.assertRaises(TaskError):
            task_from_dict({"id": "x", "mechanism": "graph", "prompt": "p",
                            "repo": {"url": "https://example.invalid/r.git"},
                            "rubric": [{"id": "a", "kind": "outcome", "text": "t"}]})


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class TestFreshClone(unittest.TestCase):
    def test_each_run_gets_an_independent_clone_of_the_same_commit(self):
        tmp = tempfile.mkdtemp()
        try:
            task = load_tasks(only=["graph-rename-line-total"])[0]
            cl = GitCloner(os.path.join(tmp, "cache"))
            a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
            sha_a, sha_b = cl.fresh_clone(task, a), cl.fresh_clone(task, b)
            self.assertEqual(sha_a, sha_b)
            self.assertEqual(len(sha_a), 40)
            with open(os.path.join(a, "shop", "pricing.py"), "a", encoding="utf-8") as fh:
                fh.write("# bleed?\n")
            with open(os.path.join(b, "shop", "pricing.py"), encoding="utf-8") as fh:
                self.assertNotIn("bleed", fh.read())
        finally:
            from bench.g2.runner import _rmtree
            _rmtree(tmp)


class TestCli(unittest.TestCase):
    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_plan_prints_cost_drivers_and_runs_nothing(self):
        code, out, _ = self._run("plan", "--repeats", "5")
        self.assertEqual(code, 0)
        self.assertIn("agent runs        : 40", out)
        self.assertIn("API-key env is scrubbed", out)

    def test_run_refuses_without_yes(self):
        tmp = tempfile.mkdtemp()
        try:
            dest = os.path.join(tmp, "out")
            code, _, err = self._run("run", "--out", dest)
            self.assertEqual(code, 2)
            self.assertIn("REFUSING", err)
            self.assertFalse(os.path.exists(dest))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_refuses_without_claude(self):
        code, _, err = self._run("run", "--out", "x", "--yes", "--claude-bin", "no-such-claude-xyz")
        self.assertEqual(code, 2)
        self.assertIn("not on PATH", err)

    def test_validate_reports_a_fixture_archive_as_not_real(self):
        tmp = tempfile.mkdtemp()
        try:
            out = FX.build_fixture_archive(os.path.join(tmp, "run"), repeats=1,
                                           task_ids=["gates-tdd-quantity-guard"])
            code, text, _ = self._run("validate", "--run", out)
            self.assertEqual(code, 1)
            self.assertIn("not a real run", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNotShippedInTheWheel(unittest.TestCase):
    def test_bench_lives_outside_src(self):
        self.assertTrue(os.path.isdir(os.path.join(ROOT, "bench", "g2")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "src", "bench")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "src", "mokata", "bench")))

    def test_pyproject_finds_packages_only_under_src_and_excludes_bench(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
            text = fh.read()
        import tomllib
        find = tomllib.loads(text)["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(find["where"], ["src"])
        self.assertIn("bench", find["exclude"])
        self.assertIn("bench.*", find["exclude"])
        pkg_data = tomllib.loads(text)["tool"]["setuptools"].get("package-data", {})
        self.assertNotIn("bench", pkg_data)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "MANIFEST.in"))
                         and "bench" in open(os.path.join(ROOT, "MANIFEST.in"), encoding="utf-8").read())

    @unittest.skipUnless(_can_build_wheels(),
                         "this interpreter cannot build a wheel without isolation (setuptools "
                         "missing, or older than 70.1 without the `wheel` package)")
    def test_a_built_wheel_contains_no_bench_file(self):
        import zipfile
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src")
            shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "build", "dist", "*.egg-info", "site"))
            proc = subprocess.run([sys.executable, "-m", "pip", "wheel", src, "--no-deps",
                                   "--no-build-isolation", "-q", "-w", os.path.join(tmp, "whl")],
                                  capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 0,
                             "pip wheel failed:\n%s" % (proc.stderr or proc.stdout)[-3000:])
            whl = [f for f in os.listdir(os.path.join(tmp, "whl")) if f.endswith(".whl")][0]
            names = zipfile.ZipFile(os.path.join(tmp, "whl", whl)).namelist()
            self.assertTrue(any(n.startswith("mokata/") for n in names))
            self.assertEqual([n for n in names if n.split("/")[0] == "bench"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
