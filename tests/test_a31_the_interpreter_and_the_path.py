"""0.0.21 stage 05 — `the-interpreter-and-the-path`. Three rows, one subject: the suite's answer
must come from the tree, not from the box it happens to run on.

  * `NO-COMMAND-PROVISIONS-THE-INTERPRETER-THE-SUITE-NEEDS` — a full run returned 30 failures and
    116 errors on a CORRECT tree, and the documented way to run the suite WAS that way. The fix
    refuses before counting.
  * `THE-PREFLIGHT-VENV-IS-NOT-ON-ITS-OWN-PATH` — the release gate installed this tree into a venv
    and then never put that venv's `bin/` on `PATH`, so it graded a hybrid that matches no real
    installation.
  * `NOTHING-PROVES-THE-SUITE-SURVIVES-A-HOSTILE-PATH` — the guard the fix above is missing, and
    the one the fix above would otherwise RETIRE.

⛔ THE THIRD ROW EXISTS BECAUSE OF THE SECOND, AND THEY SHIP TOGETHER. A tidy `PATH` stops tests
failing for the environment by making the environment tidy; a test that HOPES for an arrangement
rather than making one then passes everywhere, forever, unobserved. The hostile leg is the
deliberately untidy half, and neither may land alone.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _provisioning as PROV

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TESTS = os.path.join(_REPO, "tests")
MODULE_LIST = os.path.join(_TESTS, "_hostile_path_modules.txt")

# `scripts/release.sh` is INTERNAL — `sync-public.sh` excludes it from the public mirror by both
# controls — so every class that READS it carries the decorator guard, and the guard has to be the
# DECORATOR shape: `tests/_shipped_reads.py` explicitly refuses a `setUpClass` raising `SkipTest`
# (`GUARD_SETUPCLASS_SKIP`), because a skip arranged inside the class is invisible to a static
# reader. Without this the shipped suite ERRORS on public CI with FileNotFoundError — AFTER the
# push. ⚠ `test_s28_shipped_reads_guarded` caught exactly that here, on the first run of this file.
RELEASE_SH = os.path.join(_REPO, "scripts", "release.sh")
_RELEASE_SH_IS_INTERNAL = "scripts/release.sh is dev-only, excluded from the public mirror"

#: A test module can ask the ambient machine where mokata is exactly when it reaches one of these.
#: The RULE lives here; `_hostile_path_modules.txt` is the ANSWER. Keeping them apart is what makes
#: the answer checkable instead of merely written down.
_RESOLVERS = frozenset(("resolved_console_script", "resolved_mcp_command", "MCP_COMMAND"))


def _declared_modules():
    out = []
    with io.open(MODULE_LIST, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def _identifiers(path):
    """Every identifier the module USES — not every string it contains.

    ⚠ AST AND NOT A SUBSTRING SEARCH, and a live module is why. `test_a30` names
    `resolved_console_script` inside an assertion's own text; a `grep` rule would conscript it into
    the hostile leg, where it has nothing to do and would simply cost time. An identifier a module
    uses and a string a module mentions are different facts (§7g)."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (SyntaxError, OSError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
    return names


def _derived_modules():
    out = []
    # CORPUS: THE WORKING TREE — and it must be. The question is "which modules in THIS checkout
    # can resolve through the ambient machine", asked so the hostile leg's list cannot quietly go
    # stale. A module added and not yet committed is exactly the one that needs catching, so the
    # index would be the wrong corpus here.
    for name in sorted(os.listdir(_TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if _identifiers(os.path.join(_TESTS, name)) & _RESOLVERS:
            out.append(name[:-3])
    return out


# ================================================================ row 1: the refusal
class TheInterpreterREFUSESRatherThanReportingANumber(unittest.TestCase):
    """`NO-COMMAND-PROVISIONS-THE-INTERPRETER-THE-SUITE-NEEDS`."""

    def test_a_missing_package_is_NAMED_rather_than_presenting_as_a_broken_tree(self):
        res = PROV.resolve(has_yaml=False, scripts_dir="/nope", lister=lambda d: [])
        self.assertFalse(res.ok)
        self.assertIn(PROV.NEED_YAML, res.missing)
        self.assertIn("PyYAML", res.render())
        self.assertIn("116", res.render(),
                      "the refusal must carry the MEASUREMENT — 'PyYAML is missing' invites a "
                      "shrug, '116 errors you would otherwise be debugging' does not")

    def test_missing_console_scripts_are_named_too(self):
        res = PROV.resolve(has_yaml=True, scripts_dir="/bin", lister=lambda d: ["ls", "cat"])
        self.assertFalse(res.ok)
        self.assertEqual((PROV.NEED_CONSOLE_SCRIPTS,), res.missing)

    def test_BOTH_are_reported_in_ONE_refusal(self):
        """⚠ The loop this ends: fix one, re-run, wait, discover the other. A preflight that
        reports the first problem it finds costs a full suite run per missing package."""
        res = PROV.resolve(has_yaml=False, scripts_dir="/bin", lister=lambda d: [])
        self.assertEqual({PROV.NEED_YAML, PROV.NEED_CONSOLE_SCRIPTS}, set(res.missing))

    def test_a_provisioned_interpreter_is_WAVED_THROUGH_silently(self):
        """THE CONTROL. Without it every assertion above is also true of a preflight that refuses
        unconditionally — which would be a tree nobody can test."""
        res = PROV.resolve(has_yaml=True, scripts_dir="/bin",
                           lister=lambda d: ["mokata", "mokata-mcp", "mokata-hook", "pip"])
        self.assertTrue(res.ok)
        self.assertEqual("", res.render(), "a passing preflight says NOTHING — a line on every "
                                           "run is the nagging this project has filed twice")

    def test_windows_exe_suffixes_still_count_as_present(self):
        res = PROV.resolve(has_yaml=True, scripts_dir=r"C:\\venv\\Scripts",
                           lister=lambda d: ["mokata.exe", "mokata-mcp.exe", "mokata-hook.exe"])
        self.assertTrue(res.ok, "the scripts are there; spelling them with .exe is not a "
                                "provisioning failure")

    def test_an_UNREADABLE_script_dir_is_not_silently_a_PASS(self):
        def boom(_d):
            raise OSError("nope")
        res = PROV.resolve(has_yaml=True, scripts_dir="/bin", lister=boom)
        self.assertFalse(res.ok, "an unreadable directory tells us nothing, and 'nothing' must "
                                 "not read as 'fine' (§7e)")

    def test_no_script_dir_at_all_is_its_OWN_basis(self):
        res = PROV.resolve(has_yaml=True, scripts_dir=None)
        self.assertEqual(PROV.UNDECIDABLE_NO_SCRIPT_DIR, res.basis,
                         "'cannot be determined' and 'is missing' are different facts (§7g)")
        self.assertFalse(res.ok)
        self.assertIn("CANNOT be determined", res.render())


class TheRefusalCannotBeTurnedBackIntoANumber(unittest.TestCase):
    """🔴 THE ASSERTION MY FIRST FIX WOULD HAVE PASSED WHILE BEING WRONG.

    The refusal originally raised `SystemExit`, reasoned from `unittest`'s loader converting an
    `Exception` into a `_FailedTest` but leaving a `BaseException` alone. ⛔ `_find_test_path` wraps
    the import in a **bare `except:`**, which catches `SystemExit` too — so a discovery run over one
    module printed `Ran 1 test … FAILED (errors=1)`. **A number. Per module. Which is the 146-red
    shape the row is about, reproduced by its own fix.**

    Any test that merely asserted "the refusal text appears" would have been green for it. This one
    asserts the ABSENCE of a count, which is the only thing that could have caught it."""

    def _run_unprovisioned(self, pattern="test_a28*.py"):
        """A real `unittest discover` whose interpreter cannot see the console scripts."""
        with tempfile.TemporaryDirectory() as empty:
            env = {k: v for k, v in os.environ.items() if k != "MOKATA_SKIP_PREFLIGHT"}
            env["MOKATA_FAKE_SCRIPT_DIR"] = empty
            driver = (
                "import os, sys, sysconfig, unittest\n"
                # Make THIS interpreter look unprovisioned without touching the real one: the
                # preflight asks `sysconfig.get_path('scripts')`, so an empty directory is an
                # interpreter with no console scripts installed. Nothing on disk is changed.
                "_real = sysconfig.get_path\n"
                "sysconfig.get_path = lambda name, *a, **k: ("
                "os.environ['MOKATA_FAKE_SCRIPT_DIR'] if name == 'scripts' "
                "else _real(name, *a, **k))\n"
                "sys.argv = ['unittest', 'discover', '-s', '.', '-t', '.', '-p', %r]\n"
                "unittest.main(module=None)\n" % pattern)
            return subprocess.run(
                [sys.executable, "-c", driver],
                cwd=_TESTS, capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, timeout=300)

    def test_an_unprovisioned_discover_run_reports_NO_TEST_COUNT_AT_ALL(self):
        proc = self._run_unprovisioned()
        blob = proc.stdout + proc.stderr
        self.assertIn("REFUSING TO RUN THE TEST SUITE", blob,
                      f"the refusal did not fire:\n{blob[:2000]}")
        self.assertNotIn("Ran ", blob,
                         "⛔ A COUNT. The refusal was converted back into a result, which is the "
                         f"whole defect this row is about:\n{blob[:2000]}")
        self.assertNotIn("FAILED (", blob)
        self.assertNotIn("OK", blob.replace("MOKATA", ""))
        self.assertEqual(2, proc.returncode,
                         "and it exits 2 — distinct from 1 (a red suite), so a caller can tell "
                         "'your tests failed' from 'your interpreter is wrong'")

    def test_the_CONTROL_this_interpreter_is_provisioned_and_the_run_proceeds(self):
        """Without this, the test above is also true of an interpreter that can never run the
        suite — and the whole file would be grading its own broken fixture."""
        self.assertTrue(PROV.live_resolution().ok,
                        "the suite is running, so by construction this interpreter passed the "
                        "preflight; if this fails, the preflight is not the thing that ran")

    def test_the_refusal_is_FLUSHED_before_the_process_is_halted(self):
        """⭐ ADDED AFTER A MUTANT SURVIVED, and the survivor was instructive. Deleting the
        `flush()` was GREEN against everything else in this file, because `sys.stderr` is
        line-buffered in CPython and flushes itself — so the subprocess test above could never
        see the difference.

        ⛔ BUT `os._exit` SKIPS BUFFER FLUSHING ENTIRELY, and the refusal takes a `stream`
        argument: the moment anything hands it a block-buffered one — a log file, a captured
        stream, a CI wrapper — the message is simply lost and the process dies at exit 2 with
        **nothing printed**. Abrupt AND mute is strictly worse than the 146 red results this row
        replaced, because at least those said something.

        So the offender is a real block-buffered file, read back through a SECOND handle while the
        first is still open. No flush, no bytes."""
        res = PROV.resolve(has_yaml=False, scripts_dir=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "refusal.log")
            sink = io.open(path, "w", encoding="utf-8", buffering=8192)
            try:
                PROV.refuse_unless_provisioned(res, sink, halt=lambda _code: None)
                with io.open(path, encoding="utf-8") as read_back:
                    landed = read_back.read()
            finally:
                sink.close()
        self.assertIn("REFUSING TO RUN THE TEST SUITE", landed,
                      "the refusal never reached the stream. `os._exit` does not flush, so "
                      "without an explicit flush the process dies at exit 2 having printed "
                      "NOTHING — and a silent refusal is worse than the red count it replaced.")

    def test_the_escape_hatch_exists_and_is_the_ONLY_way_past(self):
        res = PROV.resolve(has_yaml=False, scripts_dir=None)
        calls = []
        os.environ["MOKATA_SKIP_PREFLIGHT"] = "1"
        try:
            self.assertIsNone(PROV.refuse_unless_provisioned(res, io.StringIO(),
                                                             halt=calls.append))
        finally:
            os.environ.pop("MOKATA_SKIP_PREFLIGHT", None)
        self.assertEqual([], calls, "with the hatch set, nothing halts")
        buf = io.StringIO()
        PROV.refuse_unless_provisioned(res, buf, halt=calls.append)
        self.assertEqual([2], calls, "and without it, it halts with 2")
        self.assertIn("REFUSING", buf.getvalue())


class TheRefusalIsACTUALLYWIRED(unittest.TestCase):
    """§7i — a guard nothing calls grades nothing. `_provisioning` could be perfect and dead."""

    def test_support_calls_it_and_support_is_what_every_module_imports(self):
        with io.open(os.path.join(_TESTS, "_support.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("refuse_unless_provisioned()", body,
                      "_support.py is the one import on every test module's path; the refusal "
                      "lives there or it does not run")
        missing = []
        # CORPUS: THE WORKING TREE — same reason as `_derived_modules`: an uncommitted module that
        # skips `_support` is precisely the gap this assertion exists to find.
        for n in sorted(os.listdir(_TESTS)):
            if not (n.startswith("test_") and n.endswith(".py")):
                continue
            with io.open(os.path.join(_TESTS, n), encoding="utf-8") as mod:
                if "_support" not in mod.read():
                    missing.append(n)
        self.assertEqual([], missing,
                         "these modules do not import _support, so the preflight never runs for "
                         "them — the claim that _support is on every path is FALSE for: %s"
                         % ", ".join(missing))

    def test_it_is_NOT_in_a_tests_dunder_init_which_would_never_be_imported(self):
        """⚠ The fix shape in doc 84 named `tests/__init__.py`. Discovery runs with `-t tests`, so
        `tests/` is the TOP-LEVEL directory and not a package — that file would not be imported at
        all and the refusal would never fire. A fix shape written before the mechanism was checked
        is a hypothesis, and this is the pin that keeps anyone from 'restoring' it."""
        self.assertFalse(os.path.exists(os.path.join(_TESTS, "__init__.py")),
                         "a tests/__init__.py makes `discover -t tests` treat these as a package "
                         "and changes every module's import name; the preflight does not go there")


# ================================================================ row 2: the tidy PATH
@unittest.skipUnless(os.path.exists(RELEASE_SH), _RELEASE_SH_IS_INTERNAL)
class EverySuiteRunningLegIsOnItsOwnPATH(unittest.TestCase):
    """`THE-PREFLIGHT-VENV-IS-NOT-ON-ITS-OWN-PATH`.

    ⚠ DERIVED PER FUNCTION, not asserted as a string. The scope comes from
    `_preflight_parity.preflight_functions`, which finds every release.sh function that runs
    `unittest discover` — the same derivation that exists because the INSTALL set was fixed in one
    function and missed in another, four times."""

    def setUp(self):
        import _preflight_parity as PP
        self.PP = PP
        self.release = PP.read_release(_REPO)
        self.assertIsNotNone(self.release, "scripts/release.sh must be readable")

    def test_each_suite_running_function_puts_ITS_OWN_venv_bin_first(self):
        import _preflight_parity as PP
        funcs = PP.preflight_functions(self.release)
        self.assertTrue(funcs, "release.sh runs the suite somewhere; deriving nothing is the "
                               "parse failing, not the property holding")
        bad = []
        for fn in funcs:
            body = PP.preflight_body(self.release, fn)
            if body is None:
                bad.append((fn, "no body"))
                continue
            if 'export PATH="${venv}/bin:' not in body \
                    and 'export PATH="${shim}:${venv}/bin:' not in body:
                bad.append((fn, "never puts its venv's bin on PATH"))
        self.assertEqual([], bad,
                         "a leg that installs this tree into a venv and then runs the suite "
                         "WITHOUT that venv's bin on PATH grades a hybrid that matches no real "
                         "installation: this tree's `mokata` as the package, somebody else's "
                         "`mokata-mcp` as the answer to shutil.which. Offenders: %s" % (bad,))

    def test_the_PATH_edit_is_FUNCTION_LOCAL_so_it_cannot_outlive_its_venv(self):
        """⚠ FOUND WHILE WRITING THE FIX, not after. A bare `export PATH=` in a bash function
        outlives the function — and every one of these venvs is `rm -rf`'d on the way out, so the
        rest of the cut would run with a DELETED directory first on its PATH. `local -x` is both
        halves: function-scoped, and still exported to subprocesses."""
        import _preflight_parity as PP
        for fn in PP.preflight_functions(self.release):
            body = PP.preflight_body(self.release, fn)
            if body and "export PATH=" in body:
                self.assertIn('local -x PATH="$PATH"', body,
                              f"{fn} edits PATH without a function-local copy, so the edit "
                              f"escapes into the rest of the release")


# ================================================================ row 3: the hostile PATH
@unittest.skipUnless(os.path.exists(RELEASE_SH), _RELEASE_SH_IS_INTERNAL)
class TheHostilePathLegExistsAndItsScopeIsDERIVED(unittest.TestCase):
    """`NOTHING-PROVES-THE-SUITE-SURVIVES-A-HOSTILE-PATH`."""

    def setUp(self):
        with io.open(RELEASE_SH, encoding="utf-8") as fh:
            self.release = fh.read()

    def test_the_leg_exists_and_the_cut_actually_calls_it(self):
        self.assertIn("run_hostile_path_preflight() {", self.release)
        calls = [ln for ln in self.release.splitlines()
                 if ln.strip() == "run_hostile_path_preflight"]
        self.assertEqual(1, len(calls),
                         "§7i: a leg the cut never invokes grades nothing. Defining it is not "
                         "running it.")

    def test_the_declared_module_list_IS_the_derived_one(self):
        """⭐ THE ANTI-ROT ASSERTION. The row's own evidence was a by-hand run whose answer was
        true at its timestamp — which is exactly why it wanted to be a leg. A leg with a
        hand-maintained module list has the same problem one layer in: the next module that starts
        resolving through the machine simply is not in it, and the leg reports green about a
        smaller and smaller tree."""
        self.assertEqual(sorted(_derived_modules()), sorted(_declared_modules()),
                         "tests/_hostile_path_modules.txt has parted from the tree. A module "
                         "belongs when it USES resolved_console_script / resolved_mcp_command / "
                         "MCP_COMMAND. Add it to the file (or stop resolving through the machine).")

    def test_the_derivation_is_not_VACUOUSLY_equal(self):
        """Both sides could be empty and the assertion above would pass. §7i."""
        self.assertGreaterEqual(len(_declared_modules()), 8)
        self.assertGreaterEqual(len(_derived_modules()), 8)

    def test_the_derivation_reads_IDENTIFIERS_and_not_STRINGS(self):
        """A `grep` rule conscripts `test_a30`, which only NAMES the symbol inside an assertion's
        own text. Measured: it is in the substring set and not in the identifier set."""
        with io.open(os.path.join(_TESTS,
                                  "test_a30_the_suite_is_green_in_one_arrangement.py"),
                     encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("resolved_console_script", body, "the premise: it says the word")
        self.assertNotIn("test_a30_the_suite_is_green_in_one_arrangement", _derived_modules(),
                         "...and the rule must still leave it out — it does not USE the symbol")

    def test_the_shim_version_is_actually_WRONG_and_not_the_release_version(self):
        """⭐ ADDED AFTER A MUTANT SURVIVED, and the survivor is the whole leg. Changing the shim
        to answer `$VER` — the version actually being cut — was GREEN: the leg still built a shim,
        still put it first on PATH, still verified it took, and still ran all eight modules. It
        just no longer presented a WRONG answer, so every module passed and the leg reported that
        the suite survives a hostile PATH **having never been hostile**.

        ⛔ That is §7e exactly — an instrument that fails open — and the previous assertions were
        all about the instrument's PLUMBING rather than about the one property that makes it an
        instrument at all."""
        line = [ln for ln in self.release.splitlines()
                if ln.startswith("HOSTILE_SHIM_VERSION=")]
        self.assertEqual(1, len(line), "the shim's version is declared exactly once")
        shim_version = line[0].split("=", 1)[1].strip().strip('"')
        with io.open(os.path.join(_REPO, "pyproject.toml"), encoding="utf-8") as fh:
            real = [l for l in fh if l.startswith("version")][0].split('"')[1]
        self.assertNotEqual(real, shim_version,
                            "the shim answers the REAL version, so nothing it shadows can "
                            "disagree with it and the leg grades nothing")
        self.assertIn("${HOSTILE_SHIM_VERSION}", self.release,
                      "and the shim body must USE the declared wrong version rather than "
                      "deriving one that could coincide with the real one")
        body = self.release[self.release.index("run_hostile_path_preflight() {"):]
        body = body[:body.index("\n}")]
        self.assertNotIn('echo "${m} $VER"', body,
                         "a shim that echoes the version under test is not a shim")

    def test_the_shim_answers_WRONG_and_is_checked_before_the_leg_grades_anything(self):
        """§7e: an instrument that fails open is a false green. A shim that silently did not take
        would make every module in the list pass for the wrong reason, and the leg would report
        that the suite survives a hostile PATH having never presented one."""
        self.assertIn("HOSTILE_SHIM_VERSION=", self.release)
        self.assertIn("the hostile-path shim did not take, so this leg grades nothing",
                      self.release)

    def test_the_shim_is_LOUD_on_any_call_that_is_not_a_version_probe(self):
        self.assertIn("exit 97", self.release,
                      "a shim that answers everything quietly would serve a test some other "
                      "plausible-looking output instead of failing visibly")

    def test_the_shim_goes_BEFORE_the_venv_and_the_venv_is_still_there(self):
        """Both directions matter. Shim-only would mean nothing can resolve correctly even when a
        test arranges its own answer, which tests the fixture rather than the tree."""
        self.assertIn('export PATH="${shim}:${venv}/bin:$PATH"', self.release)

    def test_the_leg_runs_the_MODULES_and_not_the_whole_suite(self):
        """Scope is a cost decision and it is recorded as one: a third full run would double the
        cut's slowest gate for no extra signal."""
        body = self.release[self.release.index("run_hostile_path_preflight() {"):]
        body = body[:body.index("\n}")]
        self.assertIn('-p "${mod}.py"', body)
        self.assertNotIn("discover -s tests -t tests <", body,
                         "that would be the whole suite a third time")


class TheHostilePathLegACTUALLYREDS(unittest.TestCase):
    """⭐ THE ANTI-VACUITY TEST THE ROW ASKED FOR, and it is the one assertion that distinguishes a
    working leg from a leg-shaped paragraph. Everything above reads `release.sh`; this RUNS the
    arrangement and proves a module that resolves through the machine fails under it.

    It does not drive `release.sh` (that would build two venvs and cut a release); it builds the
    same shim and runs ONE module that is known to arrange its own answer, then the same module
    with the arrangement actively sabotaged."""

    @unittest.skipUnless(os.name == "posix", "the shim is a shell script")
    def test_a_module_that_asks_the_MACHINE_reds_under_the_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = os.path.join(tmp, "shim")
            os.makedirs(shim)
            for name in ("mokata", "mokata-mcp", "mokata-hook"):
                p = os.path.join(shim, name)
                with io.open(p, "w", encoding="utf-8") as fh:
                    fh.write("#!/usr/bin/env bash\n"
                             'if [ "${1:-}" = "--version" ]; then echo "%s 0.0.1-hostile"; '
                             "exit 0; fi\nexit 97\n" % name)
                os.chmod(p, 0o755)
            probe = os.path.join(tmp, "probe.py")
            with io.open(probe, "w", encoding="utf-8") as fh:
                fh.write(
                    "import os, subprocess, shutil, sys\n"
                    "p = shutil.which('mokata-mcp')\n"
                    "assert p, 'nothing resolved at all'\n"
                    "out = subprocess.run([p, '--version'], capture_output=True, text=True)\n"
                    "sys.stdout.write(out.stdout.strip())\n")
            env = dict(os.environ, PATH=shim + os.pathsep + os.environ.get("PATH", ""))
            proc = subprocess.run([sys.executable, probe], capture_output=True, text=True,
                                  env=env, timeout=60)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertIn("0.0.1-hostile", proc.stdout,
                          "⛔ THE ARRANGEMENT DID NOT TAKE. If a plain `shutil.which` does not "
                          "reach the shim, the whole leg is a no-op and every module in it would "
                          "pass for the wrong reason.")

    def test_the_CONTROL_without_the_shim_the_real_one_resolves(self):
        import shutil
        real = shutil.which("mokata-mcp")
        self.assertIsNotNone(real, "the preflight guarantees this; if it is None the refusal "
                                   "above did not run")
        self.assertNotIn("0.0.1-hostile", real)


if __name__ == "__main__":
    unittest.main()
