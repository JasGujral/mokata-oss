"""A test class whose setup fails half way must not leave the process's HOME pointed at its
sandbox. The p39 browser class once did (Chromium could not launch under the sandbox HOME), and
every later module in the same process (30 `test_self_protect` tests) then failed for a reason
nobody could see in their own output."""
import os
import subprocess
import sys
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

#: Every variable that points a process at a per-user directory: HOME/USERPROFILE, the Windows
#: profile dirs, and the whole XDG family (0.0.27 SEC-HARDEN, PREEXIST L3 — the guard used to
#: watch HOME and USERPROFILE only, so a module that left XDG_CONFIG_HOME pointed at its sandbox
#: went unseen).
_NAMED = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def per_user_dirs(environ=None):
    """The watched variables, as {name: value} — every `XDG_*` present plus the named ones."""
    env = os.environ if environ is None else environ
    names = set(_NAMED) | {k for k in env if k.startswith("XDG_")}
    return {k: env.get(k) for k in sorted(names)}


_HOME_AT_IMPORT = per_user_dirs()

_SCRIPT = textwrap.dedent('''
    import io, os, sys, unittest
    sys.path.insert(0, %(here)r)
    import _support  # noqa: F401
    import test_p29_ide_uninitialized_repo as P29
    import test_p39_ide_first_run as P39
    import test_setupclass_never_leaks_home as G
    before = G.per_user_dirs()
    def boom(_d):
        raise RuntimeError("setup failed after HOME was patched")
    P29.stranger_repo = boom
    P39.P29.stranger_repo = boom
    suite = unittest.TestSuite()
    for cls in (P39.InTheBrowser_InProcess, P29.EveryRouteAnswersByContract):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    res = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    after = G.per_user_dirs()
    print("RESTORED" if before == after else "LEAKED %%r -> %%r" %% (before, after))
''')


class SetupThatFailsHalfWayLeavesHomeAlone(unittest.TestCase):
    def test_a_failing_setUpClass_restores_every_per_user_dir_variable(self):
        """Whatever step of a browser/IDE class's setup raises, HOME is the user's again."""
        out = subprocess.run([sys.executable, "-c", _SCRIPT % {"here": HERE}], capture_output=True,
                             text=True, cwd=HERE, timeout=300)
        self.assertIn("RESTORED", out.stdout, out.stdout + out.stderr)


class NoModuleLeavesHomeChanged(unittest.TestCase):
    def test_HOME_is_what_it_was_when_the_suite_was_loaded(self):
        """The guard for the whole process: a module that leaves HOME (or any XDG_*/APPDATA/
        LOCALAPPDATA) patched reds here instead of reddening unrelated modules. (Discovery imports
        every module before any runs, so the value captured at import is the user's own.)"""
        self.assertEqual(_HOME_AT_IMPORT, per_user_dirs())


if __name__ == "__main__":
    unittest.main()
