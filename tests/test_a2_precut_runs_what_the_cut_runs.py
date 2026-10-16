"""0.0.23 stage A2 · `precut-runs-what-the-cut-runs`.

  * `TESTS-INTEGRATION-OUTSIDE-THE-PRECUT-AND-DISCOVER` — `unittest discover -s tests` does not
    recurse into `tests/integration`. `precut.py` ran an integration module only when one was
    TOUCHED. So the 0.0.22 cut's D5 containment change (a `src/` edit) reached `release.sh`'s
    preflight with 6 integration reds. precut now runs the WHOLE integration suite, every time, on
    release.sh's own argv.
  * `PLUGIN-CACHE-KEYS-ON-THE-VERSION` — CONTRIBUTING says the installed plugin is cached per
    version and how to test a checkout instead (`--plugin-dir`).
  * `NEW-TESTS-MEET-WINDOWS-FIRST-ON-THE-MIRROR` lives in `test_windows_shell_and_paths.py`
    (`TestNewTestsMeetWindowsFirst`), beside the sweep it extends.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")

#: release.sh's integration preflight, as it spells it (`run_test_preflight`).
_RELEASE_SH_INTEGRATION = "-m unittest discover -s tests/integration -t tests/integration"


def _precut():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        import precut
    finally:
        sys.path.pop(0)
    return precut


class _Done:
    def __init__(self, rc):
        self.returncode = rc


class PrecutRunsTheWholeIntegrationSuite(unittest.TestCase):

    def setUp(self):
        self.p = _precut()

    def drive(self, unit_rc=0, integration_rc=0):
        """`main([])` with nothing touched and the subprocess layer recorded, not run."""
        calls = []

        def fake_run(argv, **kw):
            calls.append((list(argv), kw))
            is_integration = "tests/integration" in argv
            return _Done(integration_rc if is_integration else unit_rc)

        wide = ["test_m%02d.py" % i for i in range(self.p.MIN_TREE_WIDE_MODULES)]
        with mock.patch.object(self.p, "tree_wide_modules", return_value=wide), \
                mock.patch.object(self.p, "touched_modules", return_value=[]), \
                mock.patch.object(self.p.subprocess, "run", side_effect=fake_run), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            rc = self.p.main([])
        return rc, calls, out.getvalue() + err.getvalue()

    def test_a2_precut_runs_the_integration_suite_with_nothing_touched(self):
        """⭐ THE 0.0.22 CUT: a `src/` change, no integration file touched. FAIL-ON-OLD: the old
        `main` spawned only the unit modules, so the integration suite never ran."""
        _rc, calls, _out = self.drive()
        integ = [(argv, kw) for argv, kw in calls if "tests/integration" in argv]
        self.assertEqual(1, len(integ), "the integration suite must run exactly once: %r" % calls)
        argv, kw = integ[0]
        self.assertEqual([sys.executable, "-m", "unittest", "discover", "-s", "tests/integration",
                          "-t", "tests/integration"], argv, "the WHOLE suite — no -p narrowing")
        self.assertEqual(self.p.REPO, kw.get("cwd"), "run from the repo root, as release.sh does")
        self.assertIs(subprocess.DEVNULL, kw.get("stdin"), "release.sh runs it `< /dev/null`")

    def test_a2_a_red_integration_suite_reds_the_precut(self):
        rc, _calls, out = self.drive(unit_rc=0, integration_rc=1)
        self.assertNotEqual(0, rc)
        self.assertIn("integration suite is RED", out)

    def test_a2_a_green_run_still_says_it_is_not_the_gate(self):
        rc, _calls, out = self.drive()
        self.assertEqual(0, rc)
        self.assertIn("THIS IS NOT THE RELEASE GATE", out)
        self.assertIn("jsonschema present AND absent", out,
                      "precut runs one environment; it must say release.sh runs two")

    def test_a2_the_argv_is_release_sh_s_own(self):
        argv = self.p.integration_argv()
        self.assertEqual(_RELEASE_SH_INTEGRATION, " ".join(argv[1:]))


@unittest.skipUnless(os.path.exists(RELEASE_SH), "release.sh is dev-only, excluded from the public mirror")
class ReleaseShStillSpellsTheIntegrationRunThatWay(unittest.TestCase):

    def test_a2_release_sh_runs_the_integration_suite_on_the_same_argv(self):
        with io.open(RELEASE_SH, encoding="utf-8") as fh:
            self.assertIn(_RELEASE_SH_INTEGRATION, fh.read(),
                          "release.sh changed its integration invocation — re-derive precut's")


class ContributingSaysThePluginCacheKeysOnTheVersion(unittest.TestCase):
    """`PLUGIN-CACHE-KEYS-ON-THE-VERSION`. Developer-only, so it is documentation, and the
    documentation is pinned on the three facts a contributor needs, not on a heading."""

    def test_a2_contributing_names_the_cache_the_disable_and_plugin_dir(self):
        with io.open(os.path.join(ROOT, "CONTRIBUTING.md"), encoding="utf-8") as fh:
            text = fh.read()
        start = text.find("the cache keys on the version")
        self.assertNotEqual(-1, start, "CONTRIBUTING has no plugin-cache paragraph")
        para = text[start:text.find("\n## ", start)]
        self.assertIn("per `version`", para)
        self.assertIn("Disable", para)
        self.assertIn("claude --plugin-dir", para)


if __name__ == "__main__":
    unittest.main()
