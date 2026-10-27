"""0.0.22 stage A2 — `the-cut-findings-close`. One regression per row the 0.0.21 cut filed.

Each class is named for its doc 84 row. Every regression here was run against the base tree
(0.0.22 A1) first and seen RED; the report records it.
"""
import ast
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "mokata")


# ----------------------------------------------------------------- MCP-DURATION-ON-THE-WINDOWS-TICK

class McpDurationOnTheWindowsTick(unittest.TestCase):
    """`duration_ms` is an interval, so it is measured on the interval clock. Windows' `monotonic`
    ticks at ~15.6 ms and stamped a 30 ms call `16`. The worst case of a coarse tick is a clock
    that does not move across the call, and that is the stand-in here, on any host."""

    def test_mcp_duration_on_the_windows_tick_survives_a_monotonic_that_does_not_move(self):
        import shutil
        from test_a39_the_event_stream_is_typed_and_derived import a_repo, query
        from mokata.mcp.server import _serve
        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        frozen = time.monotonic()
        with mock.patch("time.monotonic", lambda: frozen):
            _serve(lambda path=".": (time.sleep(0.03), {"status": "ok"})[1],
                   name="slow_tool", kind="read")(path=root)
        rows = [r for r in query(root, types=["tool_call"]) if r.data["tool"] == "slow_tool"]
        self.assertEqual(1, len(rows))
        self.assertGreaterEqual(rows[0].duration_ms, 20,
                                "the dispatch timer reads the coarse clock again")


# ---------------------------------------------------------------- GIT-READERS-DECODE-WITH-THE-LOCALE

_TEXT_KW = ("text", "universal_newlines")
_CALLS = {"run", "Popen", "check_output", "check_call", "call"}


def _external_tool(call):
    """`"git"` / `"gh"` when the call's argv is a list literal starting with that constant."""
    if not call.args:
        return None
    argv = call.args[0]
    if isinstance(argv, ast.List) and argv.elts and isinstance(argv.elts[0], ast.Constant):
        if argv.elts[0].value in ("git", "gh"):
            return argv.elts[0].value
    return None


def locale_decoded_tool_readers(tree_root):
    """`[(relpath, line, tool)]` — every `subprocess` call in `tree_root` that spawns git or gh
    with `text=True` and no `encoding=`, i.e. decodes the tool's UTF-8 with the LOCALE codec."""
    out = []
    for dirpath, dirnames, files in os.walk(tree_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _CALLS):
                    continue
                tool = _external_tool(node)
                kws = {k.arg: k.value for k in node.keywords if k.arg}
                texty = any(isinstance(kws.get(k), ast.Constant) and kws[k].value is True
                            for k in _TEXT_KW)
                if tool and texty and "encoding" not in kws:
                    out.append((os.path.relpath(path, tree_root), node.lineno, tool))
    return sorted(out)


class GitReadersDecodeWithTheLocale(unittest.TestCase):

    def test_git_readers_decode_with_the_locale_none_left_in_src(self):
        self.assertEqual([], locale_decoded_tool_readers(SRC),
                         "a git/gh reader decodes with the locale codec (cp1252 on Windows)")

    def test_the_sweep_convicts_a_planted_offender(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "bad.py").write_text(
                "import subprocess\n"
                "subprocess.run(['git', 'status'], capture_output=True, text=True)\n"
                "subprocess.run(['git', *a], text=True, encoding='utf-8')\n"
                "subprocess.run(['ls'], text=True)\n", encoding="utf-8")
            self.assertEqual([("bad.py", 2, "git")], locale_decoded_tool_readers(d))

    # ⚠ No behavioural round-trip here, and that is MEASURED, not skipped: a cp1252 decode cannot
    # be forced on this Linux host from inside the process (`text=True` resolves the locale codec
    # in C, below any patch), and a draft that patched `locale.getpreferredencoding` PASSED on the
    # unfixed tree. The Windows legs are where the behaviour is graded; this sweep is the pin.


# ------------------------------------------------------------------ WINDOWS-CTIME-IS-CREATION-TIME
# Graded in test_a28 (both platform arms + the disclosure); nothing here.


# ------------------------------------------------------ QUALITY-AT-SCALE-REDS-EVERY-WEDNESDAY-ON-THE-MIRROR

class QualityAtScaleRedsEveryWednesday(unittest.TestCase):

    def setUp(self):
        import _preflight_parity as pp
        self.pp = pp

    def test_quality_at_scale_every_suite_importing_job_installs_the_suite_requirements(self):
        self.assertEqual([], self.pp.workflow_jobs_missing(self.pp.read_workflows(ROOT)),
                         "a workflow job imports the suite (and so `_support`) without PyYAML")

    def test_the_lint_convicts_the_shape_that_shipped(self):
        shipped = (
            "jobs:\n"
            "  quality-at-scale:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: pip install -e .\n"
            "      - working-directory: tests\n"
            "        run: |\n"
            "          python - <<'PY'\n"
            "          import _support\n"
            "          PY\n"
            "      - run: python -m unittest -v test_db_s8g_quality_at_scale\n")
        self.assertEqual([("quality-at-scale.yml", "quality-at-scale")],
                         self.pp.workflow_jobs_missing({"quality-at-scale.yml": shipped}))
        fixed = shipped.replace(
            "      - run: pip install -e .\n",
            "      - run: pip install -e .\n"
            "      - run: pip install --require-hashes -r requirements/ci.txt\n")
        self.assertEqual([], self.pp.workflow_jobs_missing({"q.yml": fixed}))

    def test_a_job_that_never_imports_the_suite_is_not_asked_to(self):
        docs = ("jobs:\n  docs:\n    steps:\n      - run: pip install -r requirements/docs.txt\n"
                "      - run: mkdocs build --strict\n")
        self.assertEqual([], self.pp.workflow_jobs_missing({"docs.yml": docs}))


# ----------------------------------------------------- MUTANT-DRIVER-WINDOW-FAILS-OPEN-ON-A-NON-INTEGER

# CORPUS: THE WORKING TREE — the drivers a person can run from this checkout, tracked or not.
def windowed_drivers():
    return sorted(
        n for n in os.listdir(os.path.join(ROOT, "tests"))
        if n.startswith("_") and n.endswith("_mutants.sh")
        and 'FROM="${FROM:-1}"' in pathlib.Path(ROOT, "tests", n).read_text(encoding="utf-8"))


class MutantDriverWindowFailsOpenOnANonInteger(unittest.TestCase):

    def setUp(self):
        if os.name == "nt":
            self.skipTest("POSIX shell drivers; the Windows legs do not run mutation passes")

    def test_mutant_driver_window_fails_open_on_a_non_integer_every_windowed_driver_refuses(self):
        drivers = windowed_drivers()
        self.assertGreaterEqual(len(drivers), 5, "the driver set shrank — re-derive it")
        for name in drivers:
            for frm, to in (("1 10", ""), ("x", "3"), ("5", "2"), ("0", "4"), ("2", "a")):
                with self.subTest(driver=name, FROM=frm, TO=to):
                    env = dict(os.environ, FROM=frm, TO=to)
                    if to == "":
                        env.pop("TO")
                    p = subprocess.run(_support.bash_argv(os.path.join("tests", name)),
                                       cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, timeout=60)
                    self.assertEqual(3, p.returncode, p.stdout[-400:] + p.stderr[-400:])
                    self.assertIn("WINDOW REFUSED", p.stderr)
                    self.assertNotIn("mutant", p.stdout.lower(), "a refused window graded something")


# ------------------------------------------------ MUTATE-SH-OVERWRITES-A-PRE-EXISTING-SNAPSHOT-WITHOUT-LOOKING

from test_mutation_harness import (  # noqa: E402
    _Fixture, _InterlockFixture, _run_proc, FLUSH_OLD, FLUSH_NEW, LOCK_VICTIM)


class MutateShOverwritesAPreExistingSnapshot(_Fixture):

    def test_mutate_sh_overwrites_a_pre_existing_snapshot_refuses_with_8_and_leaves_it(self):
        stranger = b"A SNAPSHOT THIS RUN NEVER TOOK\n"
        bak = self.victim + ".bak"
        pathlib.Path(bak).write_bytes(stranger)
        before = pathlib.Path(self.victim).read_bytes()
        p = _run_proc(self.tmp, "pre-existing .bak", FLUSH_OLD, FLUSH_NEW)
        self.assertEqual(8, p.returncode, p.stdout + p.stderr)
        self.assertIn("SNAPSHOT-EXISTS", p.stderr)
        self.assertEqual(stranger, pathlib.Path(bak).read_bytes(), "the stranger's .bak was touched")
        self.assertEqual(before, pathlib.Path(self.victim).read_bytes(), "the target was touched")
        self.assertFalse(any(n.endswith("REFUSED-RESTORE.bak") for n in os.listdir(self.tmp)))
        self.assertNotIn("RED", p.stdout)
        self.assertNotIn("GREEN", p.stdout)

    def test_without_a_pre_existing_snapshot_the_run_still_grades(self):
        p = _run_proc(self.tmp, "no .bak", FLUSH_OLD, FLUSH_NEW)
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        self.assertFalse(os.path.exists(self.victim + ".bak"))


class ALockBusyRunDoesNotTouchTheHoldersSnapshot(_InterlockFixture):
    """Found by stage 00 (g4 surprise 4): LOCK BUSY exits through the EXIT trap, whose restore keyed
    on `.bak` PRESENCE — so the second run renamed the live holder's snapshot to
    `.REFUSED-RESTORE.bak` and exited 4, leaving the holder unable to restore its own target."""

    def test_mutate_sh_lock_busy_leaves_the_holders_snapshot_where_it_is(self):
        holder = self._spawn_holder()
        self.assertTrue(self._await_snapshot_window(), "the holder never took its snapshot")
        bak = self.victim + ".bak"
        held = pathlib.Path(bak).read_bytes()
        p = subprocess.run(self._argv("second"), capture_output=True, text=True,
                           env=self._env(), cwd=ROOT, timeout=60)
        self.assertEqual(5, p.returncode, p.stdout + p.stderr)
        self.assertTrue(os.path.exists(bak), "the second run moved the holder's snapshot")
        self.assertEqual(held, pathlib.Path(bak).read_bytes())
        self.assertFalse(os.path.exists(self.victim + ".REFUSED-RESTORE.bak"))
        os.killpg(holder.pid, signal.SIGTERM)
        holder.communicate(timeout=60)
        self.assertEqual(LOCK_VICTIM, pathlib.Path(self.victim).read_text(encoding="utf-8"),
                         "the holder could not restore its own target")


class TheExitContractNamesStatusEight(unittest.TestCase):

    def test_the_header_and_the_batch_dispatch_both_define_8(self):
        import _mutant_driver_contract as MDC
        text = pathlib.Path(ROOT, "scripts", "mutate.sh").read_text(encoding="utf-8")
        self.assertIn(8, MDC.documented_statuses(MDC.contract_header(text)))
        run = pathlib.Path(ROOT, "tests", "_run_mutants.sh").read_text(encoding="utf-8")
        self.assertIn('8) meaning="A SNAPSHOT ALREADY EXISTS', run)


# ------------------------------------------------------- PRECUT-TOUCHED-SET-EXPLODES-ON-A-SHORT-STEM

def _precut():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        import precut
    finally:
        sys.path.pop(0)
    return precut


class PrecutTouchedSetExplodesOnAShortStem(unittest.TestCase):

    def setUp(self):
        self.p = _precut()

    def test_precut_touched_set_explodes_on_a_short_stem_too_wide_is_a_share(self):
        self.assertTrue(self.p.too_wide(105, 456), "`release` (23% of the suite) is no token")
        self.assertFalse(self.p.too_wide(56, 456), "`sync-public` (12%) still is")
        self.assertFalse(self.p.too_wide(0, 0))

    def test_a_src_module_falls_back_to_how_its_importers_spell_it(self):
        self.assertEqual({"mokata.govern.gate"}, self.p.import_tokens("src/mokata/govern/gate.py"))
        self.assertEqual({"mokata.knowledge"},
                         self.p.import_tokens("src/mokata/knowledge/__init__.py"))
        self.assertEqual(set(), self.p.import_tokens("scripts/release.sh"))
        pat = self.p.token_pattern("mokata.govern.gate")
        self.assertTrue(pat.search("from mokata.govern.gate import Gate"))
        self.assertTrue(pat.search("from mokata.govern import compress, gate\n"))
        self.assertFalse(pat.search("from mokata.govern import gatekeeper\n"))
        self.assertFalse(self.p.token_pattern("ci.yml").search("decision_yml"))

    def test_editing_release_sh_no_longer_selects_a_quarter_of_the_suite(self):
        """The measured case: the stem `release` is dropped and `release.sh` is kept."""
        p = self.p
        with mock.patch.object(p, "_git", side_effect=lambda *a: (
                ["scripts/release.sh"] if a[:2] == ("diff", "--name-only") else [])):
            picked = p.touched_modules()
        total = len([e for e in os.listdir(p.TESTS) if e.startswith("test_") and e.endswith(".py")])
        self.assertTrue(picked, "editing release.sh must still select its readers")
        # The defect was the bare stem `release` matching a QUARTER of the suite. Graded against
        # that measured defect, not against `MAX_STEM_SHARE` (the precut's own tuning knob for
        # dropping a stem): the selection legitimately grows as more modules read sync-public.sh
        # (O16: three guards now derive "what ships" from it), and a share sitting exactly ON the
        # knob is not the explosion this row closed.
        self.assertLess(len(picked) / total, 0.25)


class PrecutRunsIntegrationTheWayReleaseShDoes(unittest.TestCase):
    """THE-FIRST-CI-RUN-SINCE-THE-REFUSAL-WAS-THE-CUT ③ — the precut masked the release path."""

    def setUp(self):
        self.p = _precut()

    def test_the_first_ci_run_a_touched_integration_module_runs_on_release_sh_s_argv(self):
        argv = self.p.integration_argv("tests/integration/test_cm_s5_live_db.py")
        self.assertEqual(["-m", "unittest", "discover", "-s", "tests/integration",
                          "-t", "tests/integration", "-p", "test_cm_s5_live_db.py"], argv[1:])
        rel = pathlib.Path(ROOT, "scripts", "release.sh")
        if rel.exists():                       # internal; absent on the mirror
            self.assertIn("-m unittest discover -s tests/integration -t tests/integration",
                          rel.read_text(encoding="utf-8"),
                          "release.sh changed its integration invocation — re-derive this argv")

    def test_only_the_integration_suite_is_routed_there(self):
        self.assertTrue(self.p.is_integration_module("tests/integration/test_x.py"))
        self.assertFalse(self.p.is_integration_module("tests/test_x.py"))
        self.assertFalse(self.p.is_integration_module("tests/integration/sub/test_x.py"))
        self.assertFalse(self.p.is_integration_module("tests/integration/_helper.py"))


if __name__ == "__main__":
    unittest.main()
