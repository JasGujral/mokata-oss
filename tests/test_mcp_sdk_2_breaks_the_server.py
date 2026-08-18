"""MCP-SDK-2-BREAKS-THE-SERVER — the shipped MCP server could not start, and CI said OK.

THE DEFECT (verified live 2026-08-12, fifteen days after it went live on PyPI):

  `pyproject.toml:30` declared `dependencies = ['mcp>=1.2']` with NO upper bound. `mcp` 2.0.0
  shipped 2026-07-28 and removed `mcp.server.fastmcp` — the module `build_server()` imports —
  so from that day a fresh `pip install mokata` resolved an SDK the server cannot use. v0.0.17
  released 2026-08-08, eleven days into the break. All 61 MCP tools, gone.

  It went undetected because nine `@skipUnless(mcp_available())` decorators turned "the product's
  largest surface is dead" into `OK (skipped=8)`. `mcp_available()` caught bare `ImportError`, so
  it could not tell an SDK that is ABSENT from one that is PRESENT AT THE WRONG MAJOR — doc 85
  §7g in the guard whose only job was to answer that question.

  And the error path was wrong in every clause. It said "the MCP SDK is not installed" (it IS
  installed) and prescribed `pip install -U mokata` (which re-resolves to the same 2.0.0 and
  reproduces the break) — a confident diagnosis of the wrong cause with a remedy that cannot
  work, i.e. §7g inside the message that exists to end the confusion.

WHAT THIS FILE GRADES

  The bound            : test_pyproject_* — `<2` in the dependency AND the `[mcp]` alias
  Bound-vs-code drift  : test_declared_bound_matches_the_code_constants
  The four states      : TestFourStatesFourRepresentations — absent/broken/incompatible/supported
  The messages         : TestTheRemedyMustActuallyWork — no "not installed", no `-U mokata` loop
  ⭐ THE LOAD-BEARING  : TestTheInstalledSdkIsWithinTheBound — RED against a real `mcp` 2.0.0
  The gate itself      : TestTheGateRefusesRatherThanSkips (§7i, synthetic dispositions)
  The sweep            : TestNoTestReturnsToTheTwoMeaningsGate (+ its planted offender)

Secret-safety: n/a — this stage reads package metadata and a version string; no path, DSN or
credential is handled, so there is no value to leak.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import re
import unittest
from contextlib import contextmanager, redirect_stderr

import _support  # noqa: F401 - puts src/ on the path

import _mcp_sdk                                              # noqa: E402
from mokata.mcp import server as MS                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def _read(*rel):
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as fh:
        return fh.read()


def _disposition(state, version=None, detail=""):
    """A synthetic disposition — the planted offender the gate is graded against (§7i)."""
    return MS.SdkDisposition(state=state, version=version, detail=detail)


@contextmanager
def _patched_sdk_state(state):
    """Patch `sdk_state` on EVERY module the production code might read it through.

    `mokata.mcp.server` and the `mokata.mcp_server` shim are two names for one function, and
    `_public_module()` picks between them by what is in `sys.modules`. Patching one is a coin
    flip decided by test import order; patching the set is deterministic.
    """
    targets = {MS, MS._public_module()}
    originals = [(mod, mod.sdk_state) for mod in targets]
    for mod in targets:
        mod.sdk_state = lambda: state
    try:
        yield
    finally:
        for mod, original in originals:
            mod.sdk_state = original


# --- 1. the bound ------------------------------------------------------------------------

class TestPyprojectBoundsTheBreakingMajor(unittest.TestCase):
    """`mcp>=1.2` with no ceiling is what shipped the dead server. 2.x is a PORT, not a bump:
    `mcp.server.fastmcp` does not exist there at all, so no amount of resolution luck helps."""

    def setUp(self):
        self.pyproject = _read("pyproject.toml")

    def test_default_dependency_excludes_the_breaking_major(self):
        m = re.search(r"(?m)^dependencies = \[(?P<body>.*)\]", self.pyproject)
        self.assertIsNotNone(m, "pyproject.toml has no top-level [project].dependencies array")
        self.assertIn("mcp>=1.2,<2", m.group("body"),
                      "the MCP SDK must be bounded BELOW 2.0.0 — 2.x removed `mcp.server.fastmcp` "
                      "and a fresh resolve picks it, shipping a server that cannot start")

    def test_the_mcp_extra_alias_carries_the_same_bound(self):
        # The `[mcp]` extra is an alias of the default dependency. An alias that resolves
        # DIFFERENTLY is not an alias — `pip install "mokata[mcp]"` would still get 2.0.0.
        m = re.search(r"(?m)^mcp = \[(?P<body>.*)\]", self.pyproject)
        self.assertIsNotNone(m, "the [mcp] compat-alias extra is gone")
        self.assertIn("mcp>=1.2,<2", m.group("body"),
                      "the [mcp] alias must carry the SAME bound as the default dependency")

    def test_declared_bound_matches_the_code_constants(self):
        """The guard must be pinned to the REAL bound, not to a string someone typed twice.

        `pyproject.toml` is what pip enforces; the constants are what the running server checks.
        If they drift, the server refuses a version pip happily installed, or blesses one it
        should have refused — so the two are asserted equal here rather than trusted."""
        m = re.search(r"mcp>=(?P<min>[0-9.]+),<(?P<max>[0-9]+)", self.pyproject)
        self.assertIsNotNone(m, "could not read the declared `mcp` bound out of pyproject.toml")
        declared_min = tuple(int(p) for p in m.group("min").split("."))
        declared_ceiling = int(m.group("max"))
        self.assertEqual(MS.SDK_MIN_VERSION, declared_min,
                         "SDK_MIN_VERSION drifted from pyproject's declared floor")
        self.assertEqual(MS.SDK_MAJOR_CEILING, declared_ceiling,
                         "SDK_MAJOR_CEILING drifted from pyproject's declared ceiling")


# --- 2. four states, four representations (doc 85 §7g) ------------------------------------

class TestFourStatesFourRepresentations(unittest.TestCase):
    """The whole stage. `mcp_available()` returned a BOOLEAN, so `absent`, `incompatible` and
    `broken` all collapsed to False and the caller could not tell which one it had."""

    def test_supported_sdk_reports_supported(self):
        st = MS.classify_sdk(version="1.29.0", import_error=None)
        self.assertEqual(st.state, MS.SDK_SUPPORTED)
        self.assertEqual(st.version, "1.29.0")

    def test_incompatible_sdk_is_not_reported_as_absent(self):
        """THE REGRESSION. Under `mcp` 2.0.0 the old code returned False == "not installed"."""
        st = MS.classify_sdk(version="2.0.0",
                             import_error=ModuleNotFoundError("No module named 'mcp.server.fastmcp'"))
        self.assertEqual(st.state, MS.SDK_INCOMPATIBLE,
                         "an SDK installed at an unsupported major must NOT read as absent")
        self.assertEqual(st.version, "2.0.0",
                         "the disposition must carry WHICH version it found (§7g provenance)")

    def test_absent_sdk_reports_absent(self):
        st = MS.classify_sdk(version=None,
                             import_error=ModuleNotFoundError("No module named 'mcp'"))
        self.assertEqual(st.state, MS.SDK_ABSENT)
        self.assertIsNone(st.version)

    def test_present_but_unimportable_at_a_supported_major_is_broken(self):
        """Reachable TODAY on CI's `jsonschema: absent` leg — `mcp` hard-requires
        `jsonschema>=4.20.0`, and that leg uninstalls it. Neither absent nor incompatible."""
        st = MS.classify_sdk(version="1.29.0",
                             import_error=ModuleNotFoundError("No module named 'jsonschema'"))
        self.assertEqual(st.state, MS.SDK_BROKEN)
        self.assertIn("jsonschema", st.detail,
                      "a broken SDK must carry the underlying import error, not swallow it")

    def test_a_version_below_the_floor_is_incompatible_not_supported(self):
        st = MS.classify_sdk(version="1.1.0", import_error=None)
        self.assertEqual(st.state, MS.SDK_INCOMPATIBLE)

    def test_the_floor_itself_is_INSIDE_the_bound(self):
        """BOTH boundaries, because a mutant proved one of them was ungraded: flipping the floor
        comparison from `>=` to `>` SURVIVED the first batch (M04). `mcp>=1.2` admits 1.2 exactly,
        and no test used exactly 1.2 — so the code could have started refusing the oldest version
        it advertises support for and every test would still have passed."""
        for version in ("1.2", "1.2.0"):
            st = MS.classify_sdk(version=version, import_error=None)
            self.assertEqual(st.state, MS.SDK_SUPPORTED,
                             f"`mcp {version}` is the declared FLOOR and must be supported")

    def test_an_sdk_lacking_the_module_we_build_on_is_incompatible_whatever_its_version(self):
        """🔴 FOUND BY PROBING THE 2026-07-28 WORLD — and it was a hole in this stage's own fix.

        With the bound widened so it no longer excludes 2.0.0 (i.e. the world as it was before
        this stage), `mcp` 2.0.0 classified as **BROKEN** — a version inside the bound whose
        import failed — and BROKEN is skippable. The guard reported `OK (skipped=2)`: the exact
        false green this stage exists to end, reconstructed inside the fix for it.

        `No module named 'mcp.server.fastmcp'` is NOT "the SDK's own dependency is missing". It
        is the SDK failing to provide the module mokata builds on, which is what *incompatible*
        means. Distinguishing them by the NAME of the missing module makes the detection
        independent of anyone having predicted which version would break."""
        st = MS.classify_sdk(
            version="1.99.0",                          # inside the bound — the check cannot help
            import_error=ModuleNotFoundError("No module named 'mcp.server.fastmcp'",
                                             name="mcp.server.fastmcp"))
        self.assertEqual(st.state, MS.SDK_INCOMPATIBLE,
                         "an SDK without `mcp.server.fastmcp` is unusable, not merely broken")

    def test_a_missing_THIRD_PARTY_dep_is_still_broken_not_incompatible(self):
        # The other side of that discrimination: `jsonschema` is not part of the `mcp` namespace,
        # so its absence is a broken machine, not an unusable SDK. Conflating the two would send
        # a user with a repairable env the "your SDK is wrong" remedy.
        st = MS.classify_sdk(
            version="1.29.0",
            import_error=ModuleNotFoundError("No module named 'jsonschema'", name="jsonschema"))
        self.assertEqual(st.state, MS.SDK_BROKEN)

    def test_a_totally_absent_sdk_is_still_absent_not_incompatible(self):
        # `No module named 'mcp'` names the `mcp` namespace too, so the discrimination above must
        # not swallow the ABSENT case.
        st = MS.classify_sdk(
            version=None, import_error=ModuleNotFoundError("No module named 'mcp'", name="mcp"))
        self.assertEqual(st.state, MS.SDK_ABSENT)

    def test_the_ceiling_itself_is_OUTSIDE_the_bound(self):
        # The other boundary: `<2` excludes 2.0.0 exactly, which is the version that shipped.
        self.assertEqual(MS.classify_sdk("2.0.0", None).state, MS.SDK_INCOMPATIBLE)
        self.assertEqual(MS.classify_sdk("1.99.99", None).state, MS.SDK_SUPPORTED)

    def test_no_metadata_but_importable_is_supported(self):
        # A vendored / path-installed SDK has no distribution metadata. It imports, so it works.
        st = MS.classify_sdk(version=None, import_error=None)
        self.assertEqual(st.state, MS.SDK_SUPPORTED)

    def test_mcp_available_is_true_only_for_supported(self):
        self.assertTrue(MS.mcp_available(state=_disposition(MS.SDK_SUPPORTED, "1.29.0")))
        for bad in (MS.SDK_ABSENT, MS.SDK_BROKEN, MS.SDK_INCOMPATIBLE):
            self.assertFalse(MS.mcp_available(state=_disposition(bad, "2.0.0")), bad)


# --- 3. the message: the remedy must exist and must work ----------------------------------

class TestTheRemedyMustActuallyWork(unittest.TestCase):
    """doc 85 §7g's corollary: "AND WHEN YOU REFUSE, THE REMEDY MUST EXIST." The shipped message
    named a remedy that reproduces the break — worse than no remedy, because it loops."""

    def _incompatible(self):
        return _disposition(MS.SDK_INCOMPATIBLE, "2.0.0", "No module named 'mcp.server.fastmcp'")

    def test_incompatible_message_does_not_claim_the_sdk_is_missing(self):
        msg = self._incompatible().diagnosis()
        self.assertNotRegex(msg.lower(), r"is not installed|missing the `mcp` package",
                            "the SDK IS installed — saying otherwise sends the user to the wrong fix")

    def test_incompatible_message_names_the_version_it_found(self):
        self.assertIn("2.0.0", self._incompatible().diagnosis(),
                      "name the version actually installed, so the user can check it themselves")

    def test_incompatible_message_prescribes_a_remedy_that_works(self):
        msg = self._incompatible().diagnosis()
        self.assertIn("pip install 'mcp<2'", msg,
                      "the ONLY remedy that restores the server is pinning the SDK below 2")

    def test_incompatible_message_warns_off_the_reinstall_loop(self):
        """`pip install -U mokata` re-resolves to the same 2.0.0. If the text mentions
        reinstalling at all, it must say it does NOT help."""
        msg = self._incompatible().diagnosis()
        if re.search(r"-U mokata|upgrade mokata|reinstall mokata", msg):
            self.assertRegex(msg.lower(), r"does not|will not|won'?t|do not|don'?t",
                             "naming the reinstall without warning it is a no-op recreates the loop")

    def test_absent_message_still_prescribes_the_reinstall(self):
        # For a genuinely ABSENT SDK, reinstalling mokata IS the right remedy — the two states
        # must not be given the same text just because they now differ in code.
        msg = _disposition(MS.SDK_ABSENT).diagnosis()
        self.assertRegex(msg, r"pip install -U mokata")
        self.assertNotIn("pip install 'mcp<2'", msg,
                         "the absent remedy must not be the incompatible one")

    def test_the_two_messages_are_different(self):
        self.assertNotEqual(_disposition(MS.SDK_ABSENT).diagnosis(),
                            self._incompatible().diagnosis(),
                            "absent and incompatible must not share one message (§7g)")

    def test_broken_message_names_the_underlying_import_error(self):
        msg = _disposition(MS.SDK_BROKEN, "1.29.0", "No module named 'jsonschema'").diagnosis()
        self.assertIn("jsonschema", msg)


class TestMainAndBuildServerUseTheRightDiagnosis(unittest.TestCase):

    def _main_stderr(self, state):
        # ⚠ Patched on the module `_public_module()` RESOLVES TO, not merely on `mokata.mcp.server`.
        # `main` and `build_server` both read the SDK through that seam, and it returns
        # `mokata.mcp_server` whenever that shim has been imported — which it has, in any full-suite
        # run. Patching only this module left `main` clearing its gate against the REAL SDK and
        # `build_server` raising against the fake one, so these two tests passed alone and errored
        # under `discover`. A test that is green only because of import order grades nothing.
        with _patched_sdk_state(state):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = MS.main([])
        return rc, err.getvalue()

    def test_main_exits_nonzero_and_names_the_incompatible_sdk(self):
        rc, msg = self._main_stderr(
            _disposition(MS.SDK_INCOMPATIBLE, "2.0.0", "No module named 'mcp.server.fastmcp'"))
        self.assertNotEqual(rc, 0)
        self.assertIn("2.0.0", msg)
        self.assertIn("pip install 'mcp<2'", msg)
        self.assertNotRegex(msg.lower(), r"is not installed",
                            "main() repeated the wrong cause on stderr — the user's first read")

    def test_main_still_fails_loud_when_the_sdk_is_genuinely_absent(self):
        rc, msg = self._main_stderr(_disposition(MS.SDK_ABSENT))
        self.assertNotEqual(rc, 0)
        self.assertIn("mokata-mcp", msg)
        self.assertRegex(msg, r"pip install -U mokata")

    def test_build_server_error_names_the_incompatible_major(self):
        with _patched_sdk_state(
                _disposition(MS.SDK_INCOMPATIBLE, "2.0.0", "No module named 'mcp.server.fastmcp'")):
            with self.assertRaises(RuntimeError) as caught:
                MS.build_server()
        self.assertIn("2.0.0", str(caught.exception))
        self.assertIn("pip install 'mcp<2'", str(caught.exception))


class TestThePoseDoesNotOutliveTheFix(unittest.TestCase):
    """doc 85 §7h — prose that JUSTIFIES a behaviour is a claim, and this one became false on
    2026-07-28. `build_server`'s docstring said the SDK "is an unconditional dependency, so this
    succeeds on any healthy `pip install mokata`" — a healthy pip install was exactly what broke
    it. Leaving the sentence behind re-teaches the false rule to the next reader."""

    def test_build_server_docstring_no_longer_promises_a_healthy_install_succeeds(self):
        doc = MS.build_server.__doc__ or ""
        self.assertNotRegex(
            doc, r"succeeds on any healthy",
            "the docstring still claims a healthy `pip install mokata` cannot fail here")


# --- 4. ⭐ THE LOAD-BEARING GUARD ----------------------------------------------------------

class TestTheInstalledSdkIsWithinTheBound(unittest.TestCase):
    """⭐ The deliverable. NO MOCKS: this reads the SDK actually installed in the interpreter
    running the suite and refuses if it is one mokata cannot use.

    Under `mcp` 2.0.0 this FAILS — which is the whole point, because under the old code the same
    environment produced `OK (skipped=8)`. Under `mcp` 1.x it passes. A guard that can only run
    in the environment where the defect is absent is the defect wearing the fix's clothes."""

    def test_installed_sdk_is_usable_or_the_reason_is_loud(self):
        _mcp_sdk.check_sdk_or_raise()          # AssertionError on INCOMPATIBLE, always
        self.assertTrue(MS.mcp_available(),
                        "reached the assertion with an SDK the server cannot use")

    def test_the_real_symbols_build_server_imports_are_importable(self):
        """The version check is a proxy; THIS is the fact users care about. A future 1.x that
        moved `fastmcp` would satisfy the bound and still be dead — so import the real symbols."""
        _mcp_sdk.check_sdk_or_raise()
        from mcp.server.fastmcp import FastMCP           # noqa: F401
        from mcp.types import ToolAnnotations            # noqa: F401

    def test_the_server_actually_builds_with_its_full_tool_surface(self):
        """`pip install mokata` + `mokata-mcp` is the user's path, and nothing in CI ever walked
        it. 61 tools is mokata's largest surface; a resolve that silently removes all of them
        must redden something."""
        _mcp_sdk.check_sdk_or_raise()
        server = MS.build_server()
        self.assertGreaterEqual(len(MS.TOOLS), 61,
                                "the tool registry shrank — this guard is sized to the surface")
        self.assertIsNotNone(server)


# --- 5. the gate itself, graded against synthetic offenders (doc 85 §7i) -------------------

class TestTheGateRefusesRatherThanSkips(unittest.TestCase):
    """§7i: a gate written in the stage that removes every offender passes whether or not it
    works. So the gate is a pure function and is fed dispositions the tree does not contain."""

    def test_incompatible_raises_an_assertion_not_a_skip(self):
        with self.assertRaises(AssertionError) as caught:
            _mcp_sdk.check_sdk_or_raise(
                state=_disposition(MS.SDK_INCOMPATIBLE, "2.0.0", "no fastmcp"), require=False)
        self.assertIn("2.0.0", str(caught.exception))

    def test_incompatible_is_loud_even_without_the_require_env(self):
        # The env var raises the bar for absent/broken; it must not be what makes the REAL
        # defect loud, or a dev run would go back to reporting nothing.
        with self.assertRaises(AssertionError):
            _mcp_sdk.check_sdk_or_raise(
                state=_disposition(MS.SDK_INCOMPATIBLE, "2.0.0"), require=False)

    def test_absent_skips_when_not_required(self):
        with self.assertRaises(unittest.SkipTest):
            _mcp_sdk.check_sdk_or_raise(state=_disposition(MS.SDK_ABSENT), require=False)

    def test_broken_skips_when_not_required(self):
        with self.assertRaises(unittest.SkipTest):
            _mcp_sdk.check_sdk_or_raise(
                state=_disposition(MS.SDK_BROKEN, "1.29.0", "no jsonschema"), require=False)

    def test_absent_FAILS_when_the_leg_declared_the_sdk_required(self):
        """THE POSITIVE CONTROL. Two states still skip, so on a leg that installs the SDK a skip
        is itself the bug. `MOKATA_REQUIRE_MCP_SDK=1` asserts the check RAN."""
        with self.assertRaises(AssertionError):
            _mcp_sdk.check_sdk_or_raise(state=_disposition(MS.SDK_ABSENT), require=True)

    def test_broken_FAILS_when_the_leg_declared_the_sdk_required(self):
        with self.assertRaises(AssertionError):
            _mcp_sdk.check_sdk_or_raise(
                state=_disposition(MS.SDK_BROKEN, "1.29.0", "no jsonschema"), require=True)

    def test_supported_returns_quietly_in_both_modes(self):
        for require in (True, False):
            self.assertIsNone(_mcp_sdk.check_sdk_or_raise(
                state=_disposition(MS.SDK_SUPPORTED, "1.29.0"), require=require))

    def test_required_reads_the_declared_env_var(self):
        original = os.environ.get(_mcp_sdk.REQUIRE_ENV)
        try:
            os.environ[_mcp_sdk.REQUIRE_ENV] = "1"
            self.assertTrue(_mcp_sdk.required())
            os.environ[_mcp_sdk.REQUIRE_ENV] = ""
            self.assertFalse(_mcp_sdk.required())
        finally:
            os.environ.pop(_mcp_sdk.REQUIRE_ENV, None)
            if original is not None:
                os.environ[_mcp_sdk.REQUIRE_ENV] = original

    def test_the_decorator_runs_the_body_when_supported(self):
        calls = []

        @_mcp_sdk.requires_mcp_sdk
        def body():
            calls.append(1)

        original = MS.sdk_state
        MS.sdk_state = lambda: _disposition(MS.SDK_SUPPORTED, "1.29.0")
        try:
            body()
        finally:
            MS.sdk_state = original
        self.assertEqual(calls, [1])

    def test_the_decorator_evaluates_at_call_time_not_import_time(self):
        """`skipUnless` fixed its verdict when the module was imported. A gate that decides then
        cannot see a state that changed after — and cannot be graded by a test like this one."""
        @_mcp_sdk.requires_mcp_sdk
        def body():
            return "ran"

        original = MS.sdk_state
        MS.sdk_state = lambda: _disposition(MS.SDK_INCOMPATIBLE, "2.0.0")
        try:
            with self.assertRaises(AssertionError):
                body()
            MS.sdk_state = lambda: _disposition(MS.SDK_SUPPORTED, "1.29.0")
            self.assertEqual(body(), "ran")
        finally:
            MS.sdk_state = original


# --- 6. the sweep: no test may go back to the two-meanings gate ---------------------------

class TestNoTestReturnsToTheTwoMeaningsGate(unittest.TestCase):

    OFFENDER = (
        "import unittest\n"
        "class T(unittest.TestCase):\n"
        "    @unittest.skipUnless(MS.mcp_available(), 'optional MCP SDK not installed')\n"
        "    def test_x(self): pass\n"
    )

    def test_the_sweep_catches_a_planted_offender(self):
        """§7i — the tree has no offenders after this stage, so the sweep is graded against one
        that never existed in it. Without this the sweep passes whether or not it works."""
        found = _mcp_sdk.skip_unless_offenders(self.OFFENDER)
        self.assertEqual(len(found), 1, f"the sweep missed the planted offender: {found}")
        self.assertIn("skipUnless", found[0][1])

    def test_the_sweep_catches_the_bare_and_dotted_spellings(self):
        for call in ("mcp_available()", "MS.mcp_available()", "M.mcp_available()"):
            src = f"import unittest\n@unittest.skipUnless({call}, 'x')\ndef t(): pass\n"
            self.assertEqual(len(_mcp_sdk.skip_unless_offenders(src)), 1, call)

    def test_the_sweep_catches_skipIf_too(self):
        src = "import unittest\n@unittest.skipIf(not MS.mcp_available(), 'x')\ndef t(): pass\n"
        self.assertEqual(len(_mcp_sdk.skip_unless_offenders(src)), 1)

    def test_the_sweep_does_not_fire_on_unrelated_skips(self):
        src = "import unittest\n@unittest.skipUnless(HAVE_YAML, 'no pyyaml')\ndef t(): pass\n"
        self.assertEqual(_mcp_sdk.skip_unless_offenders(src), [])

    def test_no_test_file_gates_the_mcp_surface_on_a_boolean(self):
        offenders = []
        # CORPUS: THE WORKING TREE. `sync-public.sh` rsyncs `tests/` to the public mirror, so an
        # untracked test file ships and must be held to this rule. Converting to the index here is
        # what would reintroduce `SHIPPED-TEST-READS-INTERNAL-FILE` from the blind side.
        for dirpath, _dirs, files in os.walk(TESTS):
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    hits = _mcp_sdk.skip_unless_offenders(fh.read(), filename=path)
                offenders += [(os.path.relpath(path, ROOT), ln, txt) for ln, txt in hits]
        self.assertEqual(offenders, [], (
            "a test still gates MCP coverage on a boolean that cannot distinguish an ABSENT SDK "
            "from an INCOMPATIBLE one — use `@_mcp_sdk.requires_mcp_sdk`, which fails loud on the "
            f"second: {offenders}"))

    def test_the_converted_sites_are_all_still_gated(self):
        """The conversion must not have DROPPED the gate — an ungated MCP test in a stripped env
        errors on `import mcp` instead of skipping, which is a different false signal."""
        expected = {
            "tests/integration/test_mcp_server.py": 1,
            "tests/test_mcp_r_d0_serve.py": 1,
            "tests/test_mcp_r_d1a_annotations.py": 2,
            "tests/test_mcp_r_d1b_response_format.py": 1,
            "tests/test_mcp_r_d1c_pagination.py": 2,
            "tests/test_mcp_r_d3_eval.py": 2,
        }
        for rel, count in expected.items():
            src = _read(*rel.split("/"))
            self.assertEqual(src.count("@_mcp_sdk.requires_mcp_sdk"), count,
                             f"{rel} lost an MCP gate in the conversion")


# --- 7. BREW-15: the OTHER place the bound is written down ---------------------------------

class TestTheHomebrewLockAgreesWithTheBound(unittest.TestCase):
    """Homebrew installs Python formulae with `--no-deps`, so `packaging/homebrew/` vendors the
    dependency tree itself. It pins `mcp==1.28.1`, which means **Homebrew would have shipped a
    WORKING server through the entire fifteen days pip shipped a dead one** — the lockfile was
    accidentally right.

    Accidentally, because the bound it resolves against was a hand-typed second copy. Nothing read
    pyproject, so `refresh-lock` run on any day after 2026-07-28 would have resolved `mcp` 2.0.0,
    vendored its sdist, and carried the defect into Homebrew too — past a bound that by then
    excluded it."""

    def setUp(self):
        import sys as _sys
        _sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import fill_homebrew_formula as FF
        self.FF = FF
        self.lock = json.loads(_read("packaging", "homebrew", "resources.lock.json"))

    def test_the_pinned_sdk_satisfies_the_declared_bound(self):
        pinned = [r for r in self.lock["resources"] if r["name"] == "mcp"]
        self.assertEqual(len(pinned), 1, "the lockfile does not pin exactly one `mcp` resource")
        release = MS._parse_version(pinned[0]["version"])
        self.assertTrue(
            MS._within_declared_bound(release),
            f"the Homebrew lockfile vendors `mcp {pinned[0]['version']}`, outside "
            f"`{MS.SDK_REQUIREMENT}` — brew would ship a server pip refuses to build")

    def test_refresh_lock_resolves_against_pyprojects_bound_not_a_typed_copy(self):
        """THE ACTUAL DEFECT HERE. `--requirement` defaulted to the literal `"mcp>=1.2"` while
        the line above it claimed it "resolve[s] the SAME requirement pyproject declares, so the
        lock can't drift from it". Nothing read pyproject — so the comment was an argument, not a
        description, and it was false (doc 85 §7h)."""
        declared = self.FF.declared_mcp_requirement()
        self.assertIn("<2", declared,
                      "refresh-lock would resolve an unbounded `mcp` and could vendor 2.0.0")
        m = re.search(r"mcp>=[0-9.]+,<[0-9]+", _read("pyproject.toml"))
        self.assertEqual(declared, m.group(0),
                         "the refresh-lock requirement drifted from pyproject's declared bound")

    def test_the_parser_default_is_the_derived_requirement(self):
        parser = self.FF.build_parser()
        default = None
        for action in parser._subparsers._group_actions[0].choices["refresh-lock"]._actions:
            if action.dest == "requirement":
                default = action.default
        self.assertEqual(default, self.FF.declared_mcp_requirement(),
                         "`refresh-lock --requirement` still defaults to a hand-typed bound")


if __name__ == "__main__":
    unittest.main()
