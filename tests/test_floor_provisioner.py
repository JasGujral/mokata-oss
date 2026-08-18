"""THE FLOOR INTERPRETER IS PROVISIONED BY A COMMAND IN THE REPO, not by a venv in someone's home.

0.0.18 stage 17, closing the second half of `CI-MATRIX-FLOOR-GAP` (doc 84 §1). The row's own
words: the interpreter used to verify the declared floor during 0.0.16 was an ad-hoc venv at
`/Users/jas/jsvenv_mk310`, built by hand, living OUTSIDE the repo. A machine-specific artifact in
one person's home directory is not a floor gate — it cannot be checked out, it cannot be reviewed,
and it silently stops existing on any other machine. Every "the floor is green" in this release
was, strictly, a claim about one laptop.

⚠ AND THAT VENV HAD ALREADY BEEN WRONG ONCE. Doc 84 records it carrying `mcp 2.0.0` at stage 3a,
so a run of stages reported "3.10 floor green" with every MCP gate silently skipped. A hand-built
environment is not merely unreproducible; it drifts, and nothing can tell you when.

WHAT IS GRADED HERE, AND WHY IT IS NOT "the venv exists"
--------------------------------------------------------
Provisioning downloads an interpreter, which no test may do on every run. So the script is split:
the parts that can ROT are pure, and they are the parts run here as a real subprocess.

  * THE DERIVATION. The floor comes from `pyproject.toml`'s `requires-python`. Nothing in the
    script may name a version — `test_the_script_hard_codes_no_version` reads the file and reds on
    the literal, because the failure this whole stage is about is a floor claim that stopped
    tracking the floor. That is stage 20's lesson (`test_pg_floor_drift.py`) one file over.
  * THE GRADE, IN FOUR OUTCOMES, NEVER TWO (doc 85 §7g). "at the floor", "below it", "above it"
    and "no interpreter provisioned at all" are four different facts, and the last two are the
    ones a two-state check destroys: an interpreter ABOVE the floor passes a `>=` test while
    proving nothing about the floor, and an ABSENT venv answering "not at the floor" reads as a
    failed check rather than an un-run one.

⚠ THE VERSIONS BELOW ARE COMPUTED FROM THE DECLARED FLOOR, never written down. A guard that
grades a floor-deriving script against a literal is the same defect wearing the other hat.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

import _support  # noqa: F401 - puts src/ on the path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: ⚠ A `/`-SPELLED NAME, not `os.path.join`. This string is searched for inside CONTRIBUTING.md
#: and the developer guide, which tell a human to run `scripts/floor-python.sh` — nobody documents
#: `scripts\floor-python.sh`, so an OS-joined value made `test_every_doc_surface_names_the_script`
#: false on Windows and only on Windows (tests/test_windows_shell_and_paths.py, cause B).
SCRIPT_RELPATH = "scripts/floor-python.sh"
SCRIPT = os.path.join(ROOT, *SCRIPT_RELPATH.split("/"))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")

# The docs that promise the command. Both ship; neither is cited by line number.
# `/`-spelled for the same reason `SCRIPT_RELPATH` is — these are names the repo uses in prose.
DOC_SURFACES = ("CONTRIBUTING.md", "docs/developer-guide.md")


def _repo_path(relpath):
    """A `/`-spelled repo name → a path this OS can open."""
    return os.path.join(ROOT, *relpath.split("/"))

#: The default venv location the script provisions into, as a repo-relative path.
#:
#: `build/` and NOT a new `.venv-floor/`, deliberately. A venv in the working tree is copied to the
#: public mirror by `sync-public.sh` unless a control excludes it — and `.gitignore` is NOT that
#: control (rsync does not read it; CLAUDE.md carries the warning, `NODE_MODULES-LEAK` carries the
#: precedent). `build/` is already excluded by BOTH sync controls and already gitignored, so this
#: adds no fourteenth entry to a list whose completeness is itself a tracked defect class
#: (`CLAUDE-MD-SHIPS-LIST-INCOMPLETE`). Reusing a control beats adding one.
DEFAULT_VENV = "build/floor-venv"

# Exit codes the script's own header defines. Named here so a mutant that collapses two of them
# into one has something to fail against.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CANNOT_PROVISION = 2
EXIT_BELOW = 3
EXIT_ABOVE = 4
EXIT_NOT_PROVISIONED = 5

# ⭐ THE TREE'S ONE BASH, and this file is why there is one: it was the only module that resolved
# the interpreter through PATH instead of leaving argv[0] to CreateProcess, and it was the only
# module whose shell script actually RAN on the three Windows legs of the halted 0.0.18 cut. The
# resolver moved to `_support` so the rest of the tree inherits that; the alias keeps the
# decorator below spelled `(BASH, NO_BASH)`, which `test_the_guard_is_the_class_decorator_shape`
# reads literally.
BASH = _support.BASH
NO_BASH = ("no bash on PATH — the provisioner is a shell script and cannot be RUN here. This is "
           "an un-run check, not a passing one (doc 85 §7g).")
NOT_POSIX = (NO_BASH + " (or: the synthetic-venv shim below is a POSIX construct — a `bin/python` "
             "shell script is not how a venv presents an interpreter on Windows.)")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def declared_floor() -> str:
    """The floor `pyproject.toml` promises, e.g. '3.10' — read, never assumed."""
    match = re.search(r'requires-python\s*=\s*["\']>=\s*([0-9]+\.[0-9]+)', _read(PYPROJECT))
    assert match, 'pyproject.toml declares no `requires-python = ">=X.Y"` floor'
    return match.group(1)


def _offset(floor: str, minors: int) -> str:
    major, minor = floor.split(".")
    return "%s.%d" % (major, int(minor) + minors)


#: A PATH that certainly holds the shell's own utilities (`grep`, `sed`) and certainly holds
#: neither `uv` nor a floor interpreter — the deterministic "this machine offers no route"
#: fixture, on POSIX and inside Git Bash's msys tree alike.
NO_ROUTE_PATH = "/usr/bin:/bin"


def run(*args, cwd=ROOT, env=None):
    return subprocess.run(_support.bash_argv(_support.as_posix(SCRIPT), *args),
                          stdin=subprocess.DEVNULL,
                          cwd=cwd, capture_output=True, text=True,
                          env=None if env is None else dict(os.environ, **env))


def provision_line(stdout, prefix="provision"):
    """The one line of a dry run that starts with `prefix`. Asserting against the whole output
    would let a heading satisfy a claim about the COMMAND (see the note in the dry-run test)."""
    lines = [line for line in stdout.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, "expected exactly one %r line:\n%s" % (prefix, stdout)
    return lines[0]


class TheScriptIsInTheRepoAndIsRunnable(unittest.TestCase):
    """The whole point of the row: it is checked out, not remembered."""

    def test_the_script_is_tracked_at_a_stable_path(self):
        self.assertTrue(os.path.isfile(SCRIPT),
                        "%s is missing — the floor is back to being a property of one laptop"
                        % SCRIPT_RELPATH)

    def test_the_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK),
                        "%s is not executable, so the documented command does not work as "
                        "documented" % SCRIPT_RELPATH)

    def test_the_script_hard_codes_no_version(self):
        """★ THE ONE THAT MATTERS IN A YEAR. The floor moves; a literal does not follow it, and a
        provisioner that keeps building the OLD floor while `pyproject.toml` promises a new one is
        a floor gate that verifies the wrong thing and says nothing."""
        floor = declared_floor()
        offenders = [(n, line) for n, line in enumerate(_read(SCRIPT).splitlines(), 1)
                     if floor in line]
        self.assertEqual([], offenders,
                         "%s names the floor %r literally — it must derive it from "
                         "pyproject.toml:\n%s"
                         % (SCRIPT_RELPATH, floor,
                            "\n".join("  %d: %s" % (n, l) for n, l in offenders)))

    def test_the_default_venv_lives_where_the_mirror_controls_already_reach(self):
        """A provisioned venv is hundreds of megabytes of interpreter in the working tree, and
        `sync-public.sh` mirrors the WORKING TREE. Putting it under a path both controls already
        exclude is what keeps this stage from quietly adding a leak to fix a gap."""
        self.assertIn(DEFAULT_VENV, _read(SCRIPT),
                      "the script no longer defaults to %s — if the default moved, the mirror "
                      "exclusion that covers it has to move with it" % DEFAULT_VENV)
        self.assertEqual("build", DEFAULT_VENV.split("/")[0])


@unittest.skipUnless(BASH, NO_BASH)
class TheFloorIsDerivedFromTheManifest(unittest.TestCase):
    """RUN, not read. Every assertion here is against the script's real stdout."""

    def test_print_floor_answers_what_pyproject_declares(self):
        got = run("--print-floor")
        self.assertEqual(EXIT_OK, got.returncode, got.stderr)
        self.assertEqual(declared_floor(), got.stdout.strip(), got.stderr)

    def test_the_dry_run_names_the_derived_floor_and_the_default_venv(self):
        """The provisioning command itself, printed without executing it — so the one part that
        cannot be run on every machine is still graded for what it WOULD do.

        ⚠ THE ASSERTION IS AGAINST THE ROUTE LINES, not the whole output. The dry-run also prints
        `floor : X.Y` as a heading, so an `assertIn(floor, stdout)` would stay green against a
        command that had stopped ASKING for the floor and would build whatever interpreter came to
        hand — which is the home-directory venv all over again.

        ⚠ AND AGAINST BOTH ROUTES, NOT THE CHOSEN ONE. That is the 0.0.18 cut-halt repair: this
        assertion used to read the `provision` line, which on a machine with neither tool says
        `<NO ROUTE>` and carries no command at all. It passed on the one Mac that had a hand-built
        python3.10 and failed on every CI leg that did not.
        """
        got = run("--dry-run")
        self.assertIn(DEFAULT_VENV, got.stdout)
        for prefix in ("route uv", "route python"):
            line = provision_line(got.stdout, prefix)
            self.assertIn(declared_floor(), line,
                          "the %s route does not request the declared floor: %s" % (prefix, line))
            self.assertIn(DEFAULT_VENV, line)

    def test_BOTH_provisioning_routes_REBUILD_rather_than_inheriting_a_venv(self):
        """★ FOUND BY RUNNING IT TWICE, WHICH IS THE FIRST THING ANYONE DOES.

        `uv venv` REFUSES an existing directory — measured, not assumed: the second invocation of
        this script (switching to the `absent` leg) exited 2 with *"A virtual environment already
        exists at: build/floor-venv"*. A command a contributor cannot run twice is not a
        documented bootstrap.

        And of the two fixes, REUSE is the wrong one. An inherited environment is exactly how
        `/Users/jas/jsvenv_mk310` came to be carrying `mcp 2.0.0` while every stage reported the
        floor green (doc 84, stage 3a). The provisioner must own what it grades, so both routes
        clear first.

        ⚠ BOTH, AND ON EVERY MACHINE. The previous form graded whichever route the ambient PATH
        happened to offer and graded NOTHING where it offered neither — so `--clear` could have
        been dropped from the `uv` route and stayed green on a box that only had python3.10.
        """
        stdout = run("--dry-run").stdout
        for prefix in ("route uv", "route python"):
            line = provision_line(stdout, prefix)
            self.assertIn("--clear", line,
                          "the %s route reuses whatever is already at the venv path: %s"
                          % (prefix, line))

    def test_a_dry_run_with_NO_ROUTE_is_not_a_green(self):
        """★ THE §7g HALF OF THE SAME DEFECT, and the one that made it invisible. `--dry-run` used
        to print `<NO ROUTE>` and exit 0: the same machine and the same fact reported as a failure
        by `provision` mode and as a success by `dry-run`. A mode that says "I cannot do this"
        must not answer with the status that means "I did".

        Driven at a PATH that certainly has the shell's own tools and certainly has neither
        route — so this grades the routeless branch on a machine that HAS a route."""
        got = run("--dry-run", env={"PATH": NO_ROUTE_PATH})
        self.assertIn("<NO ROUTE>", got.stdout, got.stdout + got.stderr)
        self.assertEqual(EXIT_CANNOT_PROVISION, got.returncode, got.stdout + got.stderr)
        self.assertIn("CANNOT PROVISION", got.stderr,
                      "the routeless dry-run must NAME the verdict, not only mark a line")

    def test_the_dry_runs_verdict_agrees_with_whether_a_route_actually_exists(self):
        """The other side of the same rule, on whatever machine this is: a dry run that DID find a
        route must still exit 0, or the repair above would have turned every dry run into a
        failure. Both branches are covered across the CI matrix — the ubuntu-3.12 and Windows legs
        have no route, the macOS/ubuntu-3.10 ones do."""
        got = run("--dry-run")
        has_route = "<NO ROUTE>" not in got.stdout
        self.assertEqual(EXIT_OK if has_route else EXIT_CANNOT_PROVISION, got.returncode,
                         got.stdout + got.stderr)

    def test_the_availability_marker_agrees_with_an_INDEPENDENT_probe(self):
        """`command -v` inside the script, graded against `shutil.which` outside it. A marker that
        always said `available` would satisfy every assertion above and describe nothing."""
        stdout = run("--dry-run").stdout
        for prefix, tool in (("route uv", "uv"), ("route python", "python" + declared_floor())):
            line = provision_line(stdout, prefix)
            expected = "available" if shutil.which(tool) else "absent"
            self.assertIn("[%s: %s]" % (tool, expected), line,
                          "the %s marker disagrees with shutil.which(%r): %s"
                          % (prefix, tool, line))

    def test_an_unknown_option_is_a_usage_error_not_a_provisioning_run(self):
        """A provisioner that shrugs at a typo and provisions anyway is how someone ends up
        believing they ran `--jsonschema absent` when they ran the default."""
        got = run("--jsonshema", "absent")
        self.assertEqual(EXIT_USAGE, got.returncode, got.stdout + got.stderr)


@unittest.skipUnless(BASH, NO_BASH)
class TheGradeHasFourOutcomes(unittest.TestCase):
    """§7g, and each outcome is a version this repo could plausibly hand it.

    `3.9.6` is not hypothetical: it is what `python3` resolves to on the machine this stage ran
    on, BELOW the floor the project declares. A `>=` check would have called an above-floor
    interpreter a pass, and a two-state check would have called this machine's default a floor.
    """

    def setUp(self):
        self.floor = declared_floor()

    def test_the_floor_itself_is_accepted(self):
        got = run("--grade", self.floor + ".20")
        self.assertEqual(EXIT_OK, got.returncode, got.stdout + got.stderr)

    def test_below_the_floor_is_refused_and_SAYS_below(self):
        got = run("--grade", _offset(self.floor, -1) + ".6")
        self.assertEqual(EXIT_BELOW, got.returncode, got.stdout + got.stderr)
        self.assertIn("below", (got.stdout + got.stderr).lower())

    def test_above_the_floor_is_refused_and_SAYS_above(self):
        """★ THE OUTCOME A `>=` CHECK CANNOT HAVE. Running the suite on 3.12 and calling it a
        floor run is the exact false claim this script exists to make impossible; it has to red,
        and it has to red DIFFERENTLY from below-the-floor, because the remedies are opposite."""
        got = run("--grade", _offset(self.floor, +2) + ".0")
        self.assertEqual(EXIT_ABOVE, got.returncode, got.stdout + got.stderr)
        self.assertIn("above", (got.stdout + got.stderr).lower())

    def test_the_three_verdicts_do_not_share_a_status(self):
        codes = {run("--grade", v).returncode for v in
                 (self.floor + ".20", _offset(self.floor, -1) + ".6", _offset(self.floor, +2) + ".0")}
        self.assertEqual(3, len(codes), "the grade collapsed two verdicts into one status: %s" % codes)

    def test_a_version_that_is_not_a_version_is_a_usage_error(self):
        got = run("--grade", "python3")
        self.assertEqual(EXIT_USAGE, got.returncode, got.stdout + got.stderr)

    def test_no_interpreter_provisioned_is_its_own_answer(self):
        """The fourth outcome, and the one a two-state check silently converts into a failure:
        `--check` against a venv that was never built must not report "below the floor"."""
        got = run("--venv", "build/floor-venv-that-does-not-exist", "--check")
        self.assertEqual(EXIT_NOT_PROVISIONED, got.returncode, got.stdout + got.stderr)
        self.assertNotIn(EXIT_NOT_PROVISIONED, (EXIT_BELOW, EXIT_ABOVE, EXIT_OK))


@unittest.skipUnless(BASH and os.name == "posix", NOT_POSIX)
class TheProvisionedInterpreterIsTheOneThatRuns(unittest.TestCase):
    """★ THE HALF THAT WOULD OTHERWISE GO UNGRADED: `--check` and `--exec` against a real venv.

    Provisioning downloads an interpreter, so no test may do it — which is exactly how the two
    modes that CONSUME the venv end up covered by nothing, and `--exec` silently running the
    system Python is the same false "I ran it on the floor" this stage exists to end, arriving
    through the tool built to prevent it.

    So the venv is SYNTHETIC: a `bin/python` shim that reports whatever version the test wants.
    That grades the consuming half end-to-end — the script locates the interpreter, asks it its
    version, and grades the answer — without a download, and it lets the BELOW case be driven by
    the version this machine's own `python3` actually reports.
    """

    def _venv(self, reports):
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "bin"))
        shim = os.path.join(tmp, "bin", "python")
        with open(shim, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\nif [ "$1" = "-c" ]; then echo "%s"; else echo "SHIM $*"; fi\n'
                     % reports)
        os.chmod(shim, 0o755)
        return tmp

    def test_check_grades_the_interpreter_the_venv_holds(self):
        got = run("--venv", self._venv(declared_floor() + ".20"), "--check")
        self.assertEqual(EXIT_OK, got.returncode, got.stdout + got.stderr)

    def test_check_refuses_a_venv_below_the_floor(self):
        """3.9.6 is what `python3` resolves to on the machine this stage ran on — below the floor
        the project declares, so a bare `python -m unittest` here was never a floor run."""
        got = run("--venv", self._venv("3.9.6"), "--check")
        self.assertEqual(EXIT_BELOW, got.returncode, got.stdout + got.stderr)

    def test_check_refuses_a_venv_above_the_floor(self):
        got = run("--venv", self._venv(_offset(declared_floor(), +2) + ".13"), "--check")
        self.assertEqual(EXIT_ABOVE, got.returncode, got.stdout + got.stderr)

    def test_exec_runs_the_venvs_interpreter_and_not_the_one_on_PATH(self):
        """The mutation this exists for: `exec "$PY"` becoming `exec python3`. Both "work"; only
        one of them runs the floor, and nothing else in the suite could tell them apart."""
        got = run("--venv", self._venv("unused"), "--exec", "-V")
        self.assertEqual(EXIT_OK, got.returncode, got.stdout + got.stderr)
        self.assertIn("SHIM -V", got.stdout,
                      "--exec did not run the provisioned interpreter: %r" % got.stdout)


class TheDocumentedCommandIsTheCommandThatExists(unittest.TestCase):
    """A documented bootstrap nobody can copy-paste is the home-directory venv with extra steps.

    Both surfaces already tell a contributor the supported range is 3.10–3.13 and then hand them
    `python -m unittest`, which runs on whatever interpreter they happen to have — 3.9.6 on the
    machine this stage ran on. Naming the script is what closes that.
    """

    def test_every_doc_surface_names_the_script(self):
        missing = [d for d in DOC_SURFACES if SCRIPT_RELPATH not in _read(_repo_path(d))]
        self.assertEqual([], missing,
                         "these surfaces document how to run the tests without naming the "
                         "provisioner: %s" % missing)


class TheGuardsThatCouldNotRunSayWhyRatherThanNothing(unittest.TestCase):
    """§7g's companion, unguarded on purpose: "bash was absent" and "bash graded the script" must
    not read alike in a run log. This asserts the reason exists and is about bash — the classes
    above are class-decorator guarded so their tests still appear in `Ran N` either way."""

    def test_the_absent_bash_reason_names_bash_and_says_it_is_unrun(self):
        self.assertIn("bash", NO_BASH)
        self.assertIn("un-run", NO_BASH)

    def test_the_guard_is_the_class_decorator_shape_and_not_a_setUpClass_skip(self):
        """The distinction `_shipped_reads` measured and encoded: a class decorator still COLLECTS
        every test and reports each as a skip, so `Ran N` is the same on a machine with bash and
        one without. A `setUpClass` raising `SkipTest` collapses the class into a single skip and
        silently diverges the count `tests/test_suite_count_integrity.py` pins."""
        src = _read(os.path.join(ROOT, "tests", "test_floor_provisioner.py"))
        self.assertIn("@unittest.skipUnless(BASH, NO_BASH)", src)
        self.assertNotIn("def setUp" + "Class", src)   # split so this line is not its own offender


if __name__ == "__main__":
    unittest.main()
