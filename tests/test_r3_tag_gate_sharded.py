"""0.0.27 R3 — TAG-GATE-UNSHARDED (ruling V3: shard it, because tag time must test what it publishes).

`release.yml`'s `test` job ran the whole unit + integration suite in ONE job per jsonschema leg.
The suite grows every release; v0.0.23's tag build was cancelled at that job's ceiling with no
test failing, and the 0.0.26 re-assembly put it at 1799 s — 1.07x of the next cliff. It is now
sharded exactly as the PR gate (`ci.yml` `test`) is. What must be true, for the owner pushing a
tag and for every user who then installs what it published:

  * every unit-test module runs in EXACTLY ONE tag shard per jsonschema leg — a module the tag
    gate drops is published untested, and one it runs twice is wall-clock the ceiling pays for;
  * the integration suite runs exactly ONCE per jsonschema leg;
  * nothing publishes unless EVERY shard of every leg is green (`build` needs `test`);
  * the tag gate selects the SAME tests as the PR gate, so a green PR means the tag will test
    what the PR tested — and nothing less;
  * the tag shard's ceiling is DERIVED from the measured record for its own runner, and reds when
    the record no longer clears it (a ceiling the suite can reach is a red that says nothing
    about the code — or, at a tag, a release that silently never happens).

Each rule is graded on the real tree AND on a planted violation, so a check that cannot fail is
caught too.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import collections
import itertools
import os
import re
import sys
import unittest

from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
RELEASE_YML = os.path.join(WORKFLOWS, "release.yml")
CI_YML = os.path.join(WORKFLOWS, "ci.yml")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ci_ceiling as CC  # noqa: E402
import shard_tests as ST  # noqa: E402

_SHARD_STEP = re.compile(r"shard_tests\.py\s+run\b(?P<tail>[^\n;|&)]*)")
_WHOLE_UNIT = re.compile(r"unittest\s+discover\s+-s\s+tests\s+-t\s+tests(?P<tail>[^\n;|&)]*)")
_INTEGRATION = "unittest discover -s tests/integration -t tests/integration"
_COND = re.compile(r"^\s*matrix\.(\w+)\s*==\s*'?([^'\s]+)'?\s*$")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _doc(text):
    return safe_load(text, "grade the sharded tag gate")


def legs(job):
    """Every leg the job's matrix runs, as dicts, crossed the way GitHub crosses axes and with
    `include` applied. MULTIPLICITY IS KEPT: a shard listed twice is two legs here, so the
    exactly-once check can see it."""
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    axes = {k: [str(v) for v in vals] for k, vals in matrix.items()
            if k not in ("include", "exclude")}
    names = sorted(axes)
    combos = [dict(zip(names, vs)) for vs in itertools.product(*[axes[n] for n in names])]
    for entry in matrix.get("include") or []:
        entry = {k: str(v) for k, v in entry.items()}
        hits = [c for c in combos if all(c.get(k) == v for k, v in entry.items() if k in c)]
        for c in hits:
            c.update(entry)
        if not hits:
            combos.append(dict(entry))
    return combos or [{}]


def runs_on(job, leg):
    label = str(job.get("runs-on", ""))
    return leg.get("os", "") if "${{" in label else label


def step_runs_on(step, leg):
    """Evaluate a test step's `if:` for one leg. Only `matrix.X == V` terms joined by `&&` are
    understood; anything else fails LOUDLY so the grader is taught, never guessed past."""
    cond = step.get("if")
    if cond is None:
        return True
    for term in str(cond).split("&&"):
        m = _COND.match(term)
        if not m:
            raise AssertionError("a test step's `if: %s` is beyond this grader — teach it" % cond)
        if leg.get(m.group(1)) != m.group(2):
            return False
    return True


def _flag(tail, flag):
    m = re.search(r"(?:^|\s)%s\s+(\S+)" % re.escape(flag), tail)
    return m.group(1) if m else None


def modules_run(job, leg, modules, record):
    """The unit-test modules ONE leg runs: its shard of `shard_tests.py run` plus any whole-suite
    `discover -s tests -t tests` (a `-p`/`-k` step selects a few by name and is not counted)."""
    ran = collections.Counter()
    for step in job.get("steps") or []:
        text = str(step.get("run", ""))
        m = _SHARD_STEP.search(text)
        w = _WHOLE_UNIT.search(text)
        if not (m or (w and not re.search(r"\s-[pk]\s", w.group("tail") + " "))):
            continue
        if not step_runs_on(step, leg):
            continue
        if w and not m:
            ran.update(modules)
            continue
        tail = m.group("tail")
        n = int(_flag(tail, "--of"))
        shard = _flag(tail, "--shard")
        k = int(leg["shard"]) if shard == "${{" else int(shard)   # `${{ matrix.shard }}` splits
        if (_flag(tail, "-s"), _flag(tail, "-t")) != ("tests", "tests"):
            continue                                            # another tree: not this suite
        if 1 <= k <= n:
            ran.update(ST.plan(modules, record, n)[0][k - 1])
    return ran


def per_leg_coverage(job, modules, record, group=("jsonschema",)):
    """{leg key: Counter(module -> times run)} summed over the shards of each leg."""
    out = collections.defaultdict(collections.Counter)
    for leg in legs(job):
        out[tuple(leg.get(g) for g in group)].update(modules_run(job, leg, modules, record))
    return dict(out)


def coverage_faults(job, modules, record):
    """Every (leg, module, times) where a module does not run exactly once. Empty == the rule."""
    faults = []
    for key, ran in sorted(per_leg_coverage(job, modules, record).items()):
        for m in modules:
            if ran[m] != 1:
                faults.append((key, m, ran[m]))
    return faults


def integration_runs(job):
    """{jsonschema leg: how many of its shard legs run the integration suite}."""
    out = collections.Counter()
    for leg in legs(job):
        out[leg.get("jsonschema")] += sum(
            1 for s in job.get("steps") or []
            if _INTEGRATION in str(s.get("run", "")) and step_runs_on(s, leg))
    return dict(out)


def ceiling_fault(release_text, record, modules):
    """None while release.yml `test`'s ceiling is what the record derives for ONE shard on its
    own runner, else why. Same rule as ci.yml's job: CLEARS at 1.2x, and not >5 above 1.5x."""
    job = _doc(release_text)["jobs"]["test"]
    n = CC.job_shard_count(release_text, "test")
    if n is None:
        return "release.yml `test` does not shard"
    worst = ST.worst_shard_ci_seconds(modules, record, n, [ST.runner_os(job["runs-on"])])
    ceiling = CC.job_ceiling(release_text, "test")
    if CC.verdict(worst, ceiling) != CC.CLEARS:
        return ("the tag shard ceiling %dm does not clear the record's worst shard (%.0fs); the "
                "rule asks for %dm" % (ceiling, worst, CC.derive_minutes(worst)))
    if ceiling > CC.derive_minutes(worst) + 5:
        return "the tag shard ceiling %dm is far above the %dm the record derives" % (
            ceiling, CC.derive_minutes(worst))
    return None


def selection(text, job_id="test"):
    """What a gate's unit step selects: (start, top, N, shard list, durations override)."""
    job = _doc(text)["jobs"][job_id]
    unit = [str(s["run"]) for s in job["steps"] if _SHARD_STEP.search(str(s.get("run", "")))]
    assert len(unit) == 1, unit
    tail = _SHARD_STEP.search(unit[0]).group("tail")
    return (_flag(tail, "-s"), _flag(tail, "-t"), _flag(tail, "--of"),
            [str(v) for v in job["strategy"]["matrix"]["shard"]],
            "--durations" in tail or re.search(r"\s-[pk]\s", tail + " ") is not None)


_EXPR = re.compile(r"^\$\{\{\s*matrix\.(\w+)\s*==\s*'([^']*)'\s*&&\s*'([^']*)'\s*\|\|\s*'([^']*)'\s*\}\}$")


def env_value(raw, leg):
    """A step env value as the runner sees it on `leg`. Literals, and the one expression shape
    the gates use (`matrix.X == 'v' && 'a' || 'b'`); anything else fails LOUDLY — teach it."""
    raw = str(raw)
    if "${{" not in raw:
        return raw
    m = _EXPR.match(raw.strip())
    if not m:
        raise AssertionError("env expression %r is beyond this grader — teach it" % raw)
    key, want, yes, no = m.groups()
    return yes if leg.get(key) == want else no


def gate_steps(job):
    """{'unit': step, 'integration': step} — the steps that run the suites the two gates share."""
    out = {}
    for s in job.get("steps") or []:
        run = str(s.get("run", ""))
        if _SHARD_STEP.search(run):
            out["unit"] = s
        elif _INTEGRATION in run:
            out["integration"] = s
    return out


def env_faults(ci_job, tag_job):
    """Every (step, var, leg, PR value, tag value) where the tag gate's suite step does not set
    what the PR gate's matching step sets, on the same leg. The var list is READ from ci.yml."""
    faults = []
    ci_steps, tag_steps = gate_steps(ci_job), gate_steps(tag_job)
    for kind, ci_step in sorted(ci_steps.items()):
        tag_env = (tag_steps.get(kind) or {}).get("env") or {}
        for var, raw in sorted((ci_step.get("env") or {}).items()):
            for leg in legs(tag_job):
                want = env_value(raw, leg)
                got = env_value(tag_env[var], leg) if var in tag_env else None
                if got != want:
                    faults.append((kind, var, leg.get("jsonschema"), want, got))
    return faults


class _Tree(unittest.TestCase):
    def setUp(self):
        self.release_text = _read(RELEASE_YML)
        self.release = _doc(self.release_text)
        self.job = self.release["jobs"]["test"]
        self.ci_text = _read(CI_YML)
        self.ci = _doc(self.ci_text)
        self.record = ST.load_record()
        self.modules = ST.list_modules(TESTS)

    def _mutant(self, old, new):
        self.assertEqual(1, self.release_text.count(old), "the planted mutation's anchor moved")
        return self.release_text.replace(old, new)


class EveryModuleRunsInExactlyOneTagShard(_Tree):
    def test_the_real_tag_gate_runs_every_module_once_per_jsonschema_leg(self):
        cover = per_leg_coverage(self.job, self.modules, self.record)
        self.assertEqual({("absent",), ("present",)}, set(cover))
        self.assertEqual([], coverage_faults(self.job, self.modules, self.record))

    def test_a_shard_missing_from_the_matrix_drops_its_modules_and_reds(self):
        bad = _doc(self._mutant("shard: [1, 2, 3, 4]", "shard: [1, 2, 3]"))["jobs"]["test"]
        faults = coverage_faults(bad, self.modules, self.record)
        dropped = {m for _k, m, times in faults if times == 0}
        self.assertTrue(dropped, "a whole shard's modules went untested and nothing noticed")
        self.assertEqual(set(ST.plan(self.modules, self.record, 4)[0][3]), dropped)

    def test_a_split_that_disagrees_with_the_matrix_reds(self):
        bad = _doc(self._mutant("--shard ${{ matrix.shard }} --of 4 -s tests", "--shard ${{ matrix.shard }} --of 5 -s tests"))["jobs"]["test"]
        self.assertTrue(coverage_faults(bad, self.modules, self.record))

    def test_a_module_run_twice_reds(self):
        twice = self._mutant(
            "        run: python scripts/shard_tests.py run --shard ${{ matrix.shard }} --of 4 -s tests -t tests\n",
            "        run: python scripts/shard_tests.py run --shard ${{ matrix.shard }} --of 4 -s tests -t tests\n"
            "      - run: python -m unittest discover -s tests -t tests\n")
        faults = coverage_faults(_doc(twice)["jobs"]["test"], self.modules, self.record)
        self.assertTrue(faults)
        self.assertTrue(all(times == 5 for _k, _m, times in faults),
                        "an extra whole-suite step re-runs every module in all four shard legs")

    def test_no_whole_suite_step_remains_beside_the_shards(self):
        runs = [str(s.get("run", "")) for s in self.job["steps"]]
        self.assertEqual([], [r for r in runs if _WHOLE_UNIT.search(r)])


class IntegrationRunsOncePerLeg(_Tree):
    def test_the_real_tag_gate_runs_integration_once_per_jsonschema_leg(self):
        self.assertEqual({"absent": 1, "present": 1}, integration_runs(self.job))

    def test_an_unconditioned_integration_step_runs_it_in_every_shard_and_reds(self):
        bad = self._mutant("        if: matrix.shard == 1\n", "")
        self.assertEqual({"absent": 4, "present": 4}, integration_runs(_doc(bad)["jobs"]["test"]))

    def test_a_condition_no_shard_meets_runs_it_nowhere_and_reds(self):
        bad = self._mutant("        if: matrix.shard == 1\n", "        if: matrix.shard == 5\n")
        self.assertEqual({"absent": 0, "present": 0}, integration_runs(_doc(bad)["jobs"]["test"]))


class NothingPublishesUnlessEveryShardIsGreen(_Tree):
    def test_build_needs_the_whole_test_matrix_and_cannot_run_past_a_red_leg(self):
        """`needs: [test]` on a matrix job waits for EVERY leg and skips `build` if any leg is not
        a success — unless `build`'s `if:` overrides the implicit `success()` with a status
        function, or a leg may fail without failing (`continue-on-error`)."""
        build = self.release["jobs"]["build"]
        self.assertEqual({"test", "validate"}, set(build["needs"]))
        self.assertEqual("github.repository == 'JasGujral/mokata-oss'", build["if"])
        for fn in ("always()", "failure()", "cancelled()", "success()"):
            self.assertNotIn(fn, str(build.get("if", "")))
        self.assertNotIn("continue-on-error", self.job)
        self.assertFalse([s for s in self.job["steps"] if "continue-on-error" in s])
        for publisher in ("github-release", "pypi"):
            self.assertEqual(["build"], self.release["jobs"][publisher]["needs"])

    def test_the_test_job_keeps_least_privilege_and_its_guard(self):
        self.assertEqual({"contents": "read"}, self.job["permissions"])
        self.assertEqual("github.repository == 'JasGujral/mokata-oss'", self.job["if"])


class TheTagGateSelectsWhatThePrGateSelects(_Tree):
    def test_the_same_tree_the_same_split_and_the_same_shards(self):
        self.assertEqual(selection(self.ci_text), selection(self.release_text))
        self.assertEqual(("tests", "tests"), selection(self.release_text)[:2])
        self.assertFalse(selection(self.release_text)[4], "no private record and no filter")

    def test_the_same_jsonschema_legs_and_python(self):
        ci_m, rel_m = self.ci["jobs"]["test"]["strategy"]["matrix"], self.job["strategy"]["matrix"]
        self.assertEqual(ci_m["jsonschema"], rel_m["jsonschema"])
        self.assertEqual([str(v) for v in ci_m["python"]], [str(v) for v in rel_m["python"]])

    def test_each_leg_runs_exactly_the_modules_the_ubuntu_pr_leg_ran(self):
        ci_cover = per_leg_coverage(self.ci["jobs"]["test"], self.modules, self.record,
                                    group=("os", "jsonschema"))
        rel_cover = per_leg_coverage(self.job, self.modules, self.record)
        for (schema,), ran in rel_cover.items():
            self.assertEqual(ci_cover[("ubuntu-latest", schema)], ran, schema)

    def test_the_same_integration_command(self):
        def integ(job):
            return sorted(str(s["run"]) for s in job["steps"] if _INTEGRATION in str(s.get("run", "")))
        self.assertEqual(integ(self.ci["jobs"]["test"]), integ(self.job))

    def test_a_tag_gate_over_a_narrower_selection_reds(self):
        unit = "--shard ${{ matrix.shard }} --of 4 -s tests -t tests"
        for old, new in ((unit, unit.replace("-s tests ", "-s tests/ide ")),
                         (unit, unit.replace("--of 4", "--of 3")),
                         ("shard: [1, 2, 3, 4]", "shard: [1, 2, 4]")):
            with self.subTest(mutant=new):
                self.assertNotEqual(selection(self.ci_text),
                                    selection(self._mutant(old, new)))


class TheTagGateRequiresWhatThePrGateRequires(_Tree):
    """V3, the other half of "the same test set": the PR gate sets `MOKATA_REQUIRE_MCP_SDK` so
    an MCP gate on a leg that installed the SDK FAILS instead of skipping. A tag gate without it
    can publish with those gates skipped — green, and testing less than the PR did."""

    def test_every_env_var_the_pr_gate_sets_on_its_suites_the_tag_gate_sets_per_leg(self):
        ci_job = self.ci["jobs"]["test"]
        self.assertTrue(any((s.get("env") or {}) for s in gate_steps(ci_job).values()),
                        "the PR gate sets nothing — this check would be vacuous")
        self.assertEqual([], env_faults(ci_job, self.job))

    def test_the_mcp_requirement_is_on_for_present_and_off_for_absent(self):
        unit = gate_steps(self.job)["unit"]
        raw = (unit.get("env") or {}).get("MOKATA_REQUIRE_MCP_SDK")
        self.assertIsNotNone(raw)
        self.assertEqual("1", env_value(raw, {"jsonschema": "present"}))
        self.assertEqual("", env_value(raw, {"jsonschema": "absent"}))

    def test_a_tag_gate_that_drops_or_inverts_it_reds(self):
        ci_job = self.ci["jobs"]["test"]
        dropped = _doc(self.release_text)["jobs"]["test"]
        for s in dropped["steps"]:
            s.pop("env", None)
        self.assertTrue(env_faults(ci_job, dropped))
        inverted = _doc(self.release_text)["jobs"]["test"]
        for s in gate_steps(inverted).values():
            s["env"] = {"MOKATA_REQUIRE_MCP_SDK": "${{ matrix.jsonschema == 'absent' && '1' || '' }}"}
        self.assertTrue(env_faults(ci_job, inverted))


class TheAbsentLegReallyHasNoJsonschema(_Tree):
    """The `absent` leg exists to prove mokata works WITHOUT jsonschema. `mcp>=1.30` requires
    jsonschema>=4.20, so `pip install -e .` pulls it in, and a leg that only skips the explicit
    install still HAS it: green, and testing the `present` case twice (0.0.27 R3 verify F1).
    `ci.yml` removes it on that leg; the tag gate must too, before any suite runs."""

    _REMOVE = re.compile(r"pip\s+uninstall\s+-y\s+jsonschema")

    def _removes_before_the_suites(self, job):
        steps = job["steps"]
        first_suite = min(i for i, s in enumerate(steps)
                          if _SHARD_STEP.search(s.get("run") or "")
                          or _INTEGRATION in (s.get("run") or ""))
        for s in steps[:first_suite]:
            run = s.get("run") or ""
            for line in run.splitlines():
                if self._REMOVE.search(line) and ("absent" in line or "absent" in str(s.get("if"))):
                    return True
        return False

    def test_the_pr_gate_removes_it_so_the_check_is_not_vacuous(self):
        self.assertTrue(self._removes_before_the_suites(self.ci["jobs"]["test"]))

    def test_the_tag_gate_removes_it_on_the_absent_leg_before_any_suite(self):
        self.assertTrue(self._removes_before_the_suites(self.job))

    def test_a_tag_gate_without_the_removal_reds(self):
        line = next(l for l in self.release_text.splitlines() if self._REMOVE.search(l))
        mutant = _doc(self._mutant(line + "\n", ""))["jobs"]["test"]
        self.assertFalse(self._removes_before_the_suites(mutant))


class TheTagShardCeilingIsDerivedFromTheRecord(_Tree):
    def test_the_real_ceiling_is_what_the_record_derives_for_one_ubuntu_shard(self):
        self.assertIsNone(ceiling_fault(self.release_text, self.record, self.modules))
        if float(self.record["ci_scale"].get("provisional_imbalance", 1.0)) > 1.0:
            self.assertIn("provisional imbalance", self.release_text,
                          "an allowance for an UNMEASURED imbalance must say it is provisional")

    def test_a_record_the_ceiling_no_longer_clears_reds(self):
        grown = dict(self.record, modules={m: t * 1.6 for m, t in self.record["modules"].items()})
        why = ceiling_fault(self.release_text, grown, self.modules)
        self.assertIsNotNone(why, "the suite grew 60% and the tag ceiling still read CLEARS")
        self.assertIn("does not clear", why)

    def test_an_inflated_ceiling_reds_too(self):
        fat = self._mutant("    timeout-minutes: 26\n", "    timeout-minutes: 45\n")
        self.assertIn("far above", ceiling_fault(fat, self.record, self.modules))

    def test_the_pr_gate_grades_this_ceiling_per_shard(self):
        self.assertEqual(4, CC.job_shard_count(self.release_text, "test"))
        tag = self.ci["jobs"]["tag-ceiling"]
        runs = " ".join(str(s.get("run", "")) for s in tag["steps"])
        self.assertIn("--from-shards ci-clock --workflow .github/workflows/release.yml --job test",
                      runs)


if __name__ == "__main__":
    unittest.main()
