"""0.0.26 O6 — WINDOWS-CI-TIME: the unit suite runs as N shards, split by MEASURED time.

What must be true (for every contributor waiting on the PR gate, and the owner cutting a release):

  * every test module runs in EXACTLY ONE shard — none dropped, none run twice — so N green
    shards mean what one green leg used to mean;
  * the split is deterministic (each leg computes it alone) and BALANCED on the measured record,
    so the gate's wall-clock is ~1/N of the suite, not one fat shard;
  * a module the record has never seen still runs (costed at the median), and the record is
    re-measured before too much of the suite is unknown;
  * ci.yml's matrix lists every shard 1..N, the unit step asks for the same N, and the once-per-
    leg steps run on shard 1 only (the balancer preloads shard 1 with their measured time);
  * the job ceiling is DERIVED from the measured record (worst shard x the runner's scale), and
    the tag gate's ceiling is re-assembled from the ubuntu shards (`ci_ceiling --from-shards`);
  * the invocation-parity check reads the shard call as the `discover` it partitions.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _workflow_pins import safe_load

import _invocation_parity as ip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ci_ceiling as CC  # noqa: E402
import shard_tests as ST  # noqa: E402

#: How much of the suite may be costed at the median before the record must be re-measured.
MAX_UNRECORDED_SHARE = 0.15
#: The worst shard may exceed the mean by at most this much on the recorded box.
MAX_IMBALANCE = 1.10


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _quiet(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(*a)
    return code, out.getvalue(), err.getvalue()


class TheAssignmentIsAPartition(unittest.TestCase):
    COSTS = {"test_%02d" % i: float((i * 37) % 23 + 1) for i in range(40)}

    def test_every_module_lands_in_exactly_one_shard(self):
        for n in (1, 2, 3, 4, 7):
            with self.subTest(n=n):
                shards = ST.assign(self.COSTS, n)
                flat = [m for s in shards for m in s]
                self.assertEqual(sorted(flat), sorted(self.COSTS), "a module dropped or doubled")
                self.assertEqual(len(flat), len(set(flat)))
                self.assertEqual(n, len(shards))

    def test_it_is_deterministic_whatever_order_the_modules_arrive_in(self):
        rev = dict(reversed(list(self.COSTS.items())))
        self.assertEqual(ST.assign(self.COSTS, 4), ST.assign(rev, 4))

    def test_it_balances_by_cost_not_by_count(self):
        costs = {"test_big": 100.0}
        costs.update({"test_small_%02d" % i: 1.0 for i in range(100)})
        shards = ST.assign(costs, 2)
        loads = sorted(sum(costs[m] for m in s) for s in shards)
        self.assertEqual([100.0, 100.0], loads)
        big = next(s for s in shards if "test_big" in s)
        self.assertEqual(["test_big"], big, "the heavy module should ride alone")

    def test_a_preload_takes_work_off_that_shard(self):
        costs = {"test_%d" % i: 10.0 for i in range(10)}
        shards = ST.assign(costs, 2, [50.0])
        self.assertEqual([3, 7], [len(s) for s in shards])

    def test_an_unrecorded_module_is_costed_at_the_median_and_still_runs(self):
        costs = ST.cost_of(["test_a", "test_b", "test_c", "test_new"],
                           {"test_a": 1.0, "test_b": 5.0, "test_c": 9.0})
        self.assertEqual(5.0, costs["test_new"])
        flat = [m for s in ST.assign(costs, 3) for m in s]
        self.assertIn("test_new", flat)


class TheRealRecordBalancesTheRealSuite(unittest.TestCase):
    def setUp(self):
        self.record = ST.load_record()
        self.modules = ST.list_modules(TESTS)
        self.n = _ci_shards()

    def test_the_record_still_describes_this_suite(self):
        known = set(self.record["modules"])
        unrecorded = [m for m in self.modules if m not in known]
        stale = sorted(known - set(self.modules))
        self.assertLessEqual(
            len(unrecorded), MAX_UNRECORDED_SHARE * len(self.modules),
            "%d of %d modules are not in scripts/shard_durations.json — re-measure it: "
            "`python scripts/shard_tests.py measure -s tests -t tests` (%s...)"
            % (len(unrecorded), len(self.modules), unrecorded[:5]))
        self.assertLessEqual(len(stale), MAX_UNRECORDED_SHARE * len(self.modules),
                             "the record names deleted modules — re-measure it: %s" % stale[:5])

    def test_every_real_module_is_in_exactly_one_shard(self):
        shards, _p = ST.plan(self.modules, self.record, self.n)
        flat = [m for s in shards for m in s]
        self.assertEqual(sorted(self.modules), sorted(flat))
        self.assertEqual(len(flat), len(set(flat)))

    def test_the_shards_are_balanced_on_the_measured_record(self):
        _s, predicted = ST.plan(self.modules, self.record, self.n)
        mean = sum(predicted) / len(predicted)
        self.assertLessEqual(max(predicted), MAX_IMBALANCE * mean,
                             "worst shard %.0fs vs mean %.0fs: %s" % (max(predicted), mean,
                                                                      [round(p) for p in predicted]))


class TheRunnerRunsTheSameSuiteDiscoverWould(unittest.TestCase):
    """A planted tree, run through the real CLI: what discover loads is what the shards run."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        body = textwrap.dedent('''
            import unittest
            class T(unittest.TestCase):
                def test_one(self):
                    {stmt}
            ''')
        for i in range(6):
            name = "test_mod%d.py" % i
            stmt = "self.fail('planted red')" if i == 4 else "pass"
            with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
                fh.write(body.format(stmt=stmt))
        with open(os.path.join(self.tmp, "_helper.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")
        os.makedirs(os.path.join(self.tmp, "sub"))
        with open(os.path.join(self.tmp, "sub", "test_not_a_package.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(body.format(stmt="pass"))
        self.addCleanup(self._unimport)
        self.rec = os.path.join(self.tmp, "durations.json")
        with open(self.rec, "w", encoding="utf-8") as fh:
            json.dump({"modules": {"test_mod0": 5.0, "test_mod1": 1.0}, "fixed": {}}, fh)

    def _unimport(self):
        while self.tmp in sys.path:
            sys.path.remove(self.tmp)
        for name in [m for m in sys.modules if m.startswith("test_mod")]:
            del sys.modules[name]

    def _run(self, k, n):
        out = os.path.join(self.tmp, "rec%d.json" % k)
        code, _o, _e = _quiet(ST.main, ["run", "--shard", str(k), "--of", str(n), "-s", self.tmp,
                                        "-t", self.tmp, "--durations", self.rec, "--record", out])
        with open(out, encoding="utf-8") as fh:
            return code, json.load(fh)

    def test_the_cheap_module_list_agrees_with_discovery(self):
        self.assertEqual(ST.discovered_modules(self.tmp, self.tmp), ST.list_modules(self.tmp))
        self.assertEqual(["test_mod%d" % i for i in range(6)], ST.list_modules(self.tmp))

    def test_the_shards_together_run_every_test_once_and_the_red_reds_its_shard(self):
        ran, codes = 0, {}
        for k in (1, 2, 3):
            code, rec = self._run(k, 3)
            ran += rec["tests"]
            codes[k] = code
            self.assertEqual(sorted(rec["per_module"]), sorted(
                ST.plan(ST.list_modules(self.tmp), ST.load_record(self.rec), 3)[0][k - 1]))
        self.assertEqual(6, ran)
        red = [k for k, c in codes.items() if c == ST.RED]
        self.assertEqual(1, len(red), codes)
        self.assertTrue(all(c == ST.GREEN for k, c in codes.items() if k not in red))

    def test_warnings_are_on_as_python_m_unittest_turns_them_on(self):
        # `python -m unittest` runs with warnings 'default' (unless -W); a bare TextTestRunner
        # does not — a shard must not change what a warning-reading test sees.
        for name in os.listdir(self.tmp):
            if name.startswith("test_mod"):
                os.remove(os.path.join(self.tmp, name))
        with open(os.path.join(self.tmp, "test_mod0.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest, warnings\n"
                     "class T(unittest.TestCase):\n"
                     "    def test_w(self):\n"
                     "        self.assertTrue(any(f[0] == 'default' and f[2] is Warning\n"
                     "                            for f in warnings.filters), warnings.filters[:3])\n")
        code, rec = self._run(1, 1)
        self.assertEqual(ST.GREEN, code)
        self.assertEqual(1, rec["tests"])

    def test_an_out_of_range_shard_cannot_run(self):
        code, _o, err = _quiet(ST.main, ["run", "--shard", "4", "--of", "3", "-s", self.tmp,
                                         "--durations", self.rec])
        self.assertEqual(ST.CANNOT, code)
        self.assertIn("not in 1..3", err)

    def test_more_shards_than_modules_is_refused_not_reported_green(self):
        code, _o, err = _quiet(ST.main, ["run", "--shard", "7", "--of", "7", "-s", self.tmp,
                                         "--durations", self.rec])
        self.assertEqual(ST.CANNOT, code)
        self.assertIn("EMPTY", err)


class AbsorbRebasesOnARealCiLeg(unittest.TestCase):
    """`absorb` puts the balancer on the slow OS's OWN per-module seconds (O6 owes it: the record
    is measured on Linux, and Windows is not uniformly slower)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="absorb-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.rec = os.path.join(self.dir, "durations.json")
        with open(self.rec, "w", encoding="utf-8") as fh:
            json.dump({"modules": {"test_a": 1.0, "test_b": 1.0}, "fixed": {"integration": 10.0},
                       "ci_scale": {"per_os": {"Linux": 2.0, "Windows": 4.0},
                                    "leg_overhead_seconds": 300,
                                    "provisional_imbalance": 1.8}}, fh)
        self.clock = os.path.join(self.dir, "clock")

    def _shard(self, group, k, of, per_module):
        d = os.path.join(self.clock, "ci-clock-%s-%d" % (group, k))
        os.makedirs(d)
        with open(os.path.join(d, "job.json"), "w", encoding="utf-8") as fh:
            json.dump({"group": group, "seconds": 999.0}, fh)
        with open(os.path.join(d, "unit.json"), "w", encoding="utf-8") as fh:
            json.dump({"shard": k, "of": of, "unit_seconds": 1.0, "per_module": per_module}, fh)

    def _absorb(self, group="windows-latest-present"):
        return _quiet(ST.main, ["absorb", self.clock, "--group", group, "--os", "Windows",
                                "--out", self.rec])

    def test_the_legs_own_seconds_become_the_record_and_the_scale_rebases(self):
        self._shard("windows-latest-present", 1, 2, {"test_a": 30.0})
        self._shard("windows-latest-present", 2, 2, {"test_b": 5.0})
        self._shard("ubuntu-latest-present", 1, 2, {"test_a": 1.0})      # another leg: ignored
        code, _o, err = self._absorb()
        self.assertEqual(ST.GREEN, code, err)
        rec = ST.load_record(self.rec)
        self.assertEqual({"test_a": 30.0, "test_b": 5.0}, rec["modules"])
        self.assertEqual({"Linux": 0.5, "Windows": 1.0}, rec["ci_scale"]["per_os"])
        self.assertNotIn("provisional_imbalance", rec["ci_scale"], "real clocks end the allowance")
        self.assertEqual({"integration": 40.0}, rec["fixed"], "fixed must move to Windows seconds")
        shards = ST.assign(ST.cost_of(rec["modules"], rec["modules"]), 2)
        self.assertIn(["test_a"], shards, "the balance did not follow the absorbed times")

    def test_an_incomplete_leg_is_refused_and_the_record_untouched(self):
        self._shard("windows-latest-present", 2, 2, {"test_b": 5.0})
        with open(self.rec, encoding="utf-8") as fh:
            before = fh.read()
        code, _o, err = self._absorb()
        self.assertEqual(ST.CANNOT, code)
        self.assertIn("not a complete", err)
        with open(self.rec, encoding="utf-8") as fh:
            self.assertEqual(before, fh.read())


def _ci():
    return safe_load(_read(CI_YML), "grade the sharded test job")


def _ci_shards():
    return len(_ci()["jobs"]["test"]["strategy"]["matrix"]["shard"])


class CiYmlRunsEveryShard(unittest.TestCase):
    def setUp(self):
        self.ci = _ci()
        self.job = self.ci["jobs"]["test"]
        self.matrix = self.job["strategy"]["matrix"]
        self.n = len(self.matrix["shard"])
        self.steps = self.job["steps"]

    def _step(self, needle):
        hits = [s for s in self.steps if needle in str(s.get("run", ""))]
        self.assertEqual(1, len(hits), needle)
        return hits[0]

    def test_the_matrix_lists_every_shard_and_the_runner_agrees(self):
        self.assertGreater(self.n, 1)
        self.assertEqual(list(range(1, self.n + 1)), list(self.matrix["shard"]))
        unit = self._step("scripts/shard_tests.py run")["run"]
        self.assertIn("--of %d " % self.n, unit + " ")
        self.assertIn("--shard ${{ matrix.shard }}", unit)
        self.assertIn("/%d" % self.n, self.job["name"])

    def test_the_unit_step_partitions_the_same_selection_as_before(self):
        unit = self._step("scripts/shard_tests.py run")["run"]
        self.assertIn("-s tests -t tests", unit)
        self.assertFalse([s for s in self.steps if "unittest discover -s tests -t tests"
                          in str(s.get("run", "")) and "-p" not in str(s.get("run", ""))],
                         "an unsharded whole-suite step is still there: the suite runs N+1 times")

    def test_every_macos_include_names_its_shard_and_they_cover_1_to_n(self):
        mac = [e for e in self.matrix.get("include") or [] if e.get("os") == "macos-latest"]
        self.assertEqual(list(range(1, self.n + 1)), sorted(int(e["shard"]) for e in mac))

    def test_the_once_per_leg_steps_run_on_shard_1_only(self):
        for needle in ("discover -s tests/integration", "test_ms_s7_stress.py",
                       "test_mcp_sdk_2_breaks_the_server.py", "mokata playbook"):
            with self.subTest(step=needle):
                self.assertIn("matrix.shard == 1", str(self._step(needle).get("if", "")))

    def test_the_job_ceiling_is_derived_from_the_measured_worst_shard(self):
        record = ST.load_record()
        _s, predicted = ST.plan(ST.list_modules(TESTS), record, self.n)
        scale = record["ci_scale"]
        imbalance = float(scale.get("provisional_imbalance", 1.0))
        worst = (max(predicted) * imbalance * max(scale["per_os"].values())
                 + scale["leg_overhead_seconds"])
        ceiling = CC.job_ceiling(_read(CI_YML), "test")
        if imbalance > 1.0:
            # An allowance for an UNMEASURED imbalance must say it is provisional, and how it ends.
            self.assertIn("provisional until `shard_tests.py absorb`", _read(CI_YML))
        self.assertEqual(CC.CLEARS, CC.verdict(worst, ceiling),
                         "the test job's ceiling %dm does not clear the predicted worst shard "
                         "(%.0fs); the rule asks for %dm" % (ceiling, worst,
                                                            CC.derive_minutes(worst)))
        self.assertLessEqual(ceiling, CC.derive_minutes(worst) + 5,
                             "the ceiling is far above what the record derives — re-derive it")

    def test_the_tag_ceiling_job_collects_exactly_what_the_shards_upload(self):
        upload = next(s for s in self.steps if "upload-artifact" in str(s.get("uses", "")))
        self.assertNotIn("if", upload, "every leg uploads its clock (absorb reads Windows')")
        name = upload["with"]["name"]
        self.assertTrue(name.startswith("ci-clock-${{ matrix.os }}-"))
        tag = self.ci["jobs"]["tag-ceiling"]
        self.assertEqual("test", tag["needs"])
        self.assertEqual("ubuntu-latest", tag["runs-on"])
        dl = next(s for s in tag["steps"] if "download-artifact" in str(s.get("uses", "")))
        self.assertEqual("ci-clock-ubuntu-latest-*", dl["with"]["pattern"])
        runs = " ".join(str(s.get("run", "")) for s in tag["steps"])
        self.assertIn("--from-shards ci-clock --workflow .github/workflows/release.yml --job test",
                      runs)


class TheParityCheckReadsTheShardCall(unittest.TestCase):
    PRE = "python -m unittest discover -s tests -t tests < /dev/null"

    def test_a_shard_call_is_the_discover_it_partitions(self):
        ci = "python scripts/shard_tests.py run --shard ${{ matrix.shard }} --of 4 -s tests -t tests"
        res = ip.resolve(self.PRE, ci)
        self.assertEqual(ip.GREEN, res.verdict, res.render())

    def test_a_shard_call_over_another_tree_is_still_a_different_selection(self):
        ci = "python scripts/shard_tests.py run --shard 1 --of 4 -s tests/other -t tests/other"
        self.assertEqual(ip.RED, ip.resolve(self.PRE, ci).verdict)

    def test_the_real_ci_yml_has_a_sharded_counterpart_for_the_unit_suite(self):
        invs = ip.invocations(_read(CI_YML), "ci.yml")
        self.assertIn(("tests", "tests", ip.DEFAULT_PATTERN, None),
                      [i.selection for i in invs if "sharded" in i.where])


class TheTagCeilingIsReassembledNotGuessed(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ci-clock-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _shard(self, group, k, of, job, unit):
        d = os.path.join(self.dir, "ci-clock-%s-%d" % (group, k))
        os.makedirs(d)
        with open(os.path.join(d, "job.json"), "w", encoding="utf-8") as fh:
            json.dump({"group": group, "seconds": job}, fh)
        with open(os.path.join(d, "unit.json"), "w", encoding="utf-8") as fh:
            json.dump({"shard": k, "of": of, "unit_seconds": unit}, fh)

    def test_per_leg_steps_once_plus_every_shards_unit_time(self):
        self._shard("u-present", 1, 2, 500.0, 300.0)      # 200 s of per-leg steps on shard 1
        self._shard("u-present", 2, 2, 380.0, 320.0)
        self._shard("u-absent", 1, 2, 400.0, 300.0)
        self._shard("u-absent", 2, 2, 350.0, 300.0)
        seconds, detail = CC.unsharded_seconds(self.dir)
        self.assertEqual(200.0 + 300.0 + 320.0, seconds)
        self.assertIn("u-present", detail)

    def test_a_missing_shard_cannot_answer_and_never_clears(self):
        self._shard("u-present", 1, 3, 500.0, 300.0)
        self._shard("u-present", 3, 3, 400.0, 320.0)
        seconds, why = CC.unsharded_seconds(self.dir)
        self.assertIsNone(seconds)
        self.assertIn("not a complete", why)
        fd, wf = tempfile.mkstemp(suffix=".yml", dir=self.dir)
        os.close(fd)
        with open(wf, "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  test:\n    timeout-minutes: 30\n")
        code, out, err = _quiet(CC.main, ["--from-shards", self.dir, "--workflow", wf,
                                          "--job", "test"])
        self.assertEqual(CC.CANNOT_ANSWER, code)
        self.assertNotIn("CLEARS", out)

    def test_the_command_verdicts_the_reassembled_time(self):
        self._shard("u", 1, 2, 900.0, 500.0)
        self._shard("u", 2, 2, 700.0, 600.0)                # 400 + 1100 = 1500 s
        wf = os.path.join(self.dir, "release.yml")
        with open(wf, "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  test:\n    timeout-minutes: 32\n")
        code, out, _e = _quiet(CC.main, ["--from-shards", self.dir, "--workflow", wf,
                                         "--job", "test"])
        self.assertEqual(CC.CLEARS, code, out)               # 1920 s >= 1.2 x 1500
        with open(wf, "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  test:\n    timeout-minutes: 29\n")
        code, out, _e = _quiet(CC.main, ["--from-shards", self.dir, "--workflow", wf,
                                         "--job", "test"])
        self.assertEqual(CC.TOO_TIGHT, code)
        self.assertIn("timeout-minutes: %d" % CC.derive_minutes(1500), out)

    def test_record_writes_what_from_shards_reads(self):
        wf = os.path.join(self.dir, "ci.yml")
        with open(wf, "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  test:\n    timeout-minutes: 60\n")
        rec = os.path.join(self.dir, "s1", "job.json")
        _quiet(CC.main, ["--since", "1000", "--now", "1600", "--workflow", wf, "--job", "test",
                         "--record", rec, "--group", "ubuntu-latest-present"])
        with open(rec, encoding="utf-8") as fh:
            self.assertEqual({"group": "ubuntu-latest-present", "seconds": 600.0}, json.load(fh))


if __name__ == "__main__":
    unittest.main()
