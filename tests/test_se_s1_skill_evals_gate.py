"""SE.S1/S2 (0.0.26 L5) — skill evals GATE CI: a broken skill trigger reds the build.

Exit criterion 4 of the 0.0.26 plan: "Skill evals gate CI (a broken skill trigger reds the cut)".
The gate is `scripts/skill_evals.py gate` (ci.yml's `skill-evals` job): every shipped skill and
model-invocable command is graded against the near-miss corpus (`evals/skill_triggers/corpus/`)
and the replay mocks (`evals/skill_triggers/replay/decisions.json`) — no model, no key, no network.
The live tier (`scripts/skill_evals_live.py`) runs `claude plugin eval` / `claude -p` on the
owner's own login at the cut and REFUSES under CI; only its zero-cost `validate` engine runs in CI.

Every behavioural test below plants the breakage on a COPY of the shipped tree and requires a red,
so a gate that stopped grading would fail here, not pass silently.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _workflow_pins import safe_load

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import skill_evals as SE  # noqa: E402
import skill_evals_live as LIVE  # noqa: E402

CLAUDE = shutil.which("claude")


def _copy_tree(dst: str) -> str:
    """The minimum the gate reads: the plugin manifest, the skills + commands it names, the evals."""
    for rel in (".claude-plugin", os.path.join("src", "mokata", "skills"),
                os.path.join("src", "mokata", "templates", "commands"),
                os.path.join("evals", "skill_triggers")):
        shutil.copytree(os.path.join(ROOT, rel), os.path.join(dst, rel))
    return dst


def _frontmatter_set(path: str, key: str, value: str) -> None:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    new, n = re.subn(rf"(?m)^{key}: .*$", f"{key}: {value}", text, count=1)
    assert n == 1, (path, key)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)


def _debug_twins(root: str):
    return (os.path.join(root, "src", "mokata", "skills", "debug", "SKILL.md"),
            os.path.join(root, "src", "mokata", "templates", "commands", "debug.md"))


def _checks(rep, unit=None):
    return {f.check for f in rep.findings if unit is None or f.unit == unit}


class TestTheShippedTreeIsGreen(unittest.TestCase):
    def test_SE_S1_gate_is_green_on_the_tree_and_covers_every_unit(self):
        rep = SE.gate(ROOT)
        self.assertTrue(rep.ok, "\n".join(f.line() for f in rep.findings))
        units = SE.load_units(ROOT)
        corpus = SE.load_corpus(ROOT)
        # Every shipped skill + command has a corpus — derived from the plugin manifest, not typed.
        self.assertEqual(set(units), set(corpus))
        # Both kinds are graded: Agent Skills and model-invocable slash commands.
        kinds = {k for u in units.values() for k in u.kinds}
        self.assertEqual(kinds, {"skill", "command"})
        for name, entry in corpus.items():
            self.assertGreaterEqual(len(entry["positive"]), SE.MIN_POSITIVE, name)
            self.assertGreaterEqual(len(entry["near_miss"]), SE.MIN_NEAR_MISS, name)
            self.assertTrue(any(n.get("expect") for n in entry["near_miss"]), name)
        # The replay mocks cover the whole corpus.
        self.assertEqual(rep.stats["replay_unrecorded_cases"], 0)

    def test_SE_S1_cli_exits_zero_on_the_tree(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "skill_evals.py"),
                            "gate"], capture_output=True, text=True, encoding="utf-8",
                           stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("GREEN", p.stdout)


class TestABrokenTriggerRedsTheGate(unittest.TestCase):
    """The business-level firing tests: each plants one breakage and demands a red."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _copy_tree(self._tmp.name)
        self.assertTrue(SE.gate(self.root).ok, "the copied tree must start green")

    def tearDown(self):
        self._tmp.cleanup()

    def test_SE_S1_description_that_no_longer_fires_reds(self):
        # `debug` rewritten so it no longer says what it is for: its positives stop firing.
        for path in _debug_twins(self.root):
            _frontmatter_set(path, "description", "mokata · A helper for writing short poems.")
            _frontmatter_set(path, "when_to_use", "Engage when the user asks for a haiku about "
                             "autumn. Do NOT engage for anything else.")
        rep = SE.gate(self.root)
        self.assertFalse(rep.ok)
        got = _checks(rep, "debug")
        self.assertIn("D5-trigger-removed", got)          # the corpus trigger phrase is gone
        self.assertIn("P1-positive-unmatched", got)       # its positives no longer reach it
        self.assertIn("R2-relied-phrase-removed", got)    # a replayed decision relied on it
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "skill_evals.py"),
                            "--root", self.root, "gate"], capture_output=True, text=True,
                           encoding="utf-8", stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("RED", p.stdout)

    def test_SE_S1_description_that_fires_on_a_near_miss_reds(self):
        # `debug` widened to claim new-feature work: the near-miss "Add a new export format for
        # XML" (which must go to develop) now routes to debug.
        skill, _cmd = _debug_twins(self.root)
        with open(skill, encoding="utf-8") as fh:
            wtu = re.search(r"(?m)^when_to_use: (.*)$", fh.read()).group(1)
        widened = wtu.replace("Engage when", "Engage to add a new export format, a new XML "
                              "export, or any new format; and when", 1)
        for path in _debug_twins(self.root):
            _frontmatter_set(path, "when_to_use", widened)
        rep = SE.gate(self.root)
        self.assertFalse(rep.ok)
        bad = [f for f in rep.findings if f.unit == "debug" and "XML" in f.detail]
        self.assertTrue(bad, "\n".join(f.line() for f in rep.findings))
        self.assertTrue({"N2-confusion", "R4-recorded-near-miss-drifted"} & {f.check for f in bad})

    def test_SE_S1_twins_that_disagree_red(self):
        _frontmatter_set(_debug_twins(self.root)[1], "description",
                         "mokata · Reproduce first, then fix (edited on one side only).")
        self.assertIn("D4-twins", _checks(SE.gate(self.root), "debug"))

    def test_SE_S1_a_new_skill_without_a_corpus_reds(self):
        d = os.path.join(self.root, "src", "mokata", "skills", "brand-new")
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nname: brand-new\ndescription: mokata · Draw architecture diagrams "
                     "of the repository.\nwhen_to_use: Engage when the user wants a diagram. "
                     "Do NOT engage for prose docs.\n---\n\nbody\n")
        self.assertIn("C1-no-corpus", _checks(SE.gate(self.root), "brand-new"))

    def test_SE_S1_a_unit_cloned_into_another_vocabulary_reds(self):
        with open(_debug_twins(self.root)[0], encoding="utf-8") as fh:
            text = fh.read()
        desc = re.search(r"(?m)^description: (.*)$", text).group(1)
        wtu = re.search(r"(?m)^when_to_use: (.*)$", text).group(1)
        for path in (os.path.join(self.root, "src", "mokata", "skills", "bug", "SKILL.md"),
                     os.path.join(self.root, "src", "mokata", "templates", "commands", "bug.md")):
            _frontmatter_set(path, "description", desc)
            _frontmatter_set(path, "when_to_use", wtu)
        rep = SE.gate(self.root)
        self.assertTrue(any(f.check == "D6-overlap" and f.unit in ("bug~debug", "debug~bug")
                            for f in rep.findings), "\n".join(f.line() for f in rep.findings))

    def test_SE_S2_a_renamed_unit_reds_instead_of_crashing(self):
        # The real 0.0.26 case: `upgrade` became `update` and the replay still recorded `upgrade`.
        # The gate must report it as stale (R1), not die with a KeyError.
        os.rename(os.path.join(self.root, "src", "mokata", "templates", "commands", "update.md"),
                  os.path.join(self.root, "src", "mokata", "templates", "commands", "upgrade2.md"))
        rep = SE.gate(self.root)
        got = _checks(rep)
        self.assertIn("C1-no-corpus", got)
        self.assertIn("C2-stale-corpus", got)
        self.assertIn("R1-stale-replay", got)

    def test_SE_S2_a_replay_record_whose_phrase_was_removed_reds(self):
        # A replay mock relying on a phrase the listing no longer carries reds even when the
        # lexical score survives: the recorded decision is no longer reproducible.
        path = os.path.join(self.root, SE.REPLAY_FILE)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        rec = next(d for d in data["decisions"] if d["target"] == "debug"
                   and d["kind"] == "positive")
        rec["relied_on"] = ["a phrase debug never said"]
        SE.write_json(path, data)
        self.assertIn("R2-relied-phrase-removed", _checks(SE.gate(self.root), "debug"))


class TestThePluginEvalSuite(unittest.TestCase):
    def test_SE_S2_generated_cases_use_only_known_keys_and_one_per_prompt(self):
        corpus = SE.load_corpus(ROOT)
        with tempfile.TemporaryDirectory() as d:
            dirs = SE.generate_plugin_eval(corpus, d)
            self.assertEqual(len(dirs), sum(1 for _ in SE.iter_cases(corpus)))
            for case_dir in dirs[:40]:
                with open(os.path.join(case_dir, "prompt.md"), encoding="utf-8") as fh:
                    head = fh.read().split("---")[1]
                keys = {ln.split(":", 1)[0] for ln in head.strip().splitlines()}
                self.assertTrue(keys <= SE.PROMPT_KEYS, keys - SE.PROMPT_KEYS)
                with open(os.path.join(case_dir, "graders", "trigger.md"), encoding="utf-8") as fh:
                    gh = fh.read().split("---")[1]
                gkeys = {ln.split(":", 1)[0] for ln in gh.strip().splitlines()}
                self.assertTrue(gkeys <= SE.GRADER_KEYS, gkeys - SE.GRADER_KEYS)

    def test_SE_S2_aggregate_maps_back_to_replay_decisions(self):
        corpus = {"debug": SE.load_corpus(ROOT)["debug"]}
        cases = list(SE.iter_cases(corpus))
        agg = {"cases": [{"name": "debug--pos-01", "aggregates": {"score": 1.0}},
                         {"name": "debug--pos-02", "aggregates": {"score": 0.0}},
                         {"name": "debug--nm-01", "aggregates": {"score": 1.0}}]}
        out = LIVE.decisions_from_aggregate(agg, cases, ROOT)
        self.assertEqual([d["chosen"] for d in out], ["debug", None, None])
        self.assertTrue(all(d["source"] == "live" for d in out))


class TestTheLiveTierNeverRunsInCI(unittest.TestCase):
    def test_SE_S2_record_and_plugin_eval_refuse_under_ci(self):
        def runner(*_a, **_k):
            raise AssertionError("the live tier ran a model under CI")
        for engine in ("record", "plugin-eval"):
            self.assertEqual(LIVE.main([engine, "--root", ROOT], runner=runner,
                                       env={"CI": "true"}), 2)

    def test_SE_S2_validate_runs_under_ci_and_reds_on_a_case_claude_rejects(self):
        def fake(rejects):
            def runner(argv, cwd, env, timeout):
                self.assertIn("--max-cost-usd", argv)
                self.assertEqual(argv[argv.index("--max-cost-usd") + 1], "0")
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                out = argv[argv.index("--output-dir") + 1]
                os.makedirs(out, exist_ok=True)
                with open(os.path.join(out, "aggregate-result.json"), "w", encoding="utf-8") as fh:
                    json.dump({"cases": [], "costUsd": 0, "partial": True,
                               "partialReason": "cost_ceiling"}, fh)
                text = ("✗ x: prompt.md: unknown frontmatter key\n1 case file(s) failed to "
                        "load\n") if rejects else "0 case(s)\n"
                return 2, text, ""
            return runner
        with tempfile.TemporaryDirectory() as d:
            root = _copy_tree(d)
            self.assertEqual(LIVE.main(["validate", "--root", root], runner=fake(False),
                                       env={"CI": "true"}), 0)
            self.assertEqual(LIVE.main(["validate", "--root", root], runner=fake(True),
                                       env={"CI": "true"}), 1)

    @unittest.skipUnless(CLAUDE, "Claude Code is not installed (cc-compat runs the live check)")
    def test_SE_S2_the_real_claude_plugin_eval_loads_the_suite_and_rejects_a_bad_case(self):
        with tempfile.TemporaryDirectory() as d:
            root = _copy_tree(d)
            self.assertEqual(LIVE.run_validate(root=root, runner=LIVE.real_runner,
                                               claude=CLAUDE), [])
            real_generate = SE.generate_plugin_eval

            def planted(corpus, out_dir, **kw):
                dirs = real_generate(corpus, out_dir, **kw)
                with open(os.path.join(dirs[0], "prompt.md"), encoding="utf-8") as fh:
                    text = fh.read()
                with open(os.path.join(dirs[0], "prompt.md"), "w", encoding="utf-8",
                          newline="\n") as fh:
                    fh.write(text.replace("runs:", "bogus_key: 1\nruns:", 1))
                return dirs
            SE.generate_plugin_eval = planted
            try:
                problems = LIVE.run_validate(root=root, runner=LIVE.real_runner, claude=CLAUDE)
            finally:
                SE.generate_plugin_eval = real_generate
            self.assertTrue(any("bogus_key" in p for p in problems), problems)


class TestTheCIJob(unittest.TestCase):
    def test_SE_S1_ci_runs_the_gate_on_every_push_with_no_credential(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
            wf = safe_load(fh.read())
        job = wf["jobs"]["skill-evals"]
        runs = [s.get("run", "") for s in job["steps"]]
        self.assertIn("python scripts/skill_evals.py gate", runs)
        self.assertLess(job["timeout-minutes"], 360)
        text = json.dumps(job)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("ANTHROPIC", text)
        self.assertNotIn("claude ", " ".join(runs))      # no live model in PR CI

    def test_SE_S2_cc_compat_validates_the_suite_against_the_latest_claude(self):
        path = os.path.join(ROOT, ".github", "workflows", "cc-compat.yml")
        with open(path, encoding="utf-8") as fh:
            wf = safe_load(fh.read())
        runs = [s.get("run", "") for s in wf["jobs"]["cc-compat"]["steps"]]
        self.assertIn("python scripts/skill_evals_live.py validate", runs)


if __name__ == "__main__":
    unittest.main()
