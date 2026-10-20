"""0.0.25 I-01 — extras are installed by the installer that OWNS mokata's environment.

Business rule (doc 112 Lane I): a stranger who chose `uv tool install mokata` (the documented
primary route) and then a full setup gets the extras — not `No module named pip` and a silent fall
back to the floor tiers. Each installer is named by a marker its installer writes; each plan must
keep the extras across that installer's own upgrade and must never change mokata's version.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import unittest.mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import extras_install, install_route as R

REQUIRES = [
    'mcp<2,>=1.2',
    'model2vec>=0.3; extra == "embeddings"',
    'numpy>=1.24; extra == "embeddings"',
    'tree-sitter>=0.23; extra == "graph-ts"',
    'tomli>=2; python_version < "3.11" and extra == "embeddings"',
]

RECEIPT = """[tool]
requirements = [{ name = "mokata", specifier = "==0.0.24" }, { name = "httpie" }]
entrypoints = [{ name = "mokata", install-path = "/x/bin/mokata", from = "mokata" }]
"""


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


class _Env:
    def __init__(self, marker=None, body=""):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = self.tmp.name
        if marker:
            with open(os.path.join(self.prefix, marker), "w", encoding="utf-8") as fh:
                fh.write(body)

    def __enter__(self):
        return self.prefix

    def __exit__(self, *a):
        self.tmp.cleanup()


class TestDetection(unittest.TestCase):
    def test_uv_tool_env_is_named_by_its_receipt(self):
        with _Env("uv-receipt.toml", RECEIPT) as p:
            self.assertEqual(R.detect_installer(p, which=_which(), has_pip=True), R.UV_TOOL)

    def test_pipx_env_is_named_by_its_metadata(self):
        with _Env("pipx_metadata.json", "{}") as p:
            self.assertEqual(R.detect_installer(p, which=_which(), has_pip=True), R.PIPX)

    def test_plain_venv_with_pip(self):
        with _Env() as p:
            self.assertEqual(R.detect_installer(p, which=_which("uv"), has_pip=True), R.PIP)

    def test_uv_venv_without_pip_uses_uv_pip(self):
        with _Env() as p:
            self.assertEqual(R.detect_installer(p, which=_which("uv"), has_pip=False), R.UV_PIP)

    def test_nothing_usable_is_said_not_guessed(self):
        with _Env() as p:
            self.assertEqual(R.detect_installer(p, which=_which(), has_pip=False),
                             R.NO_INSTALLER)


class TestExtraRequirements(unittest.TestCase):
    def test_reads_only_the_named_extra_and_keeps_other_markers(self):
        self.assertEqual(R.extra_requirements("embeddings", REQUIRES),
                         ["model2vec>=0.3", "numpy>=1.24", 'tomli>=2; python_version < "3.11"'])

    def test_live_metadata_matches_pyproject(self):
        reqs = R.extra_requirements("embeddings")
        self.assertTrue(any(r.startswith("model2vec") for r in reqs), reqs)


class TestUvToolPlan(unittest.TestCase):
    def _plan(self, receipt=RECEIPT, platform="linux", which=_which("uv")):
        with _Env("uv-receipt.toml", receipt) as p:
            return R.plan_install(["mokata[embeddings]", "code-review-graph"], prefix=p,
                                  which=which, platform=platform, requires=REQUIRES)

    def test_restates_the_receipt_and_adds_the_extras_in_one_run(self):
        plan = self._plan()
        self.assertTrue(plan.runnable)
        self.assertEqual(plan.argv, ["/usr/bin/uv", "tool", "install", "mokata==0.0.24",
                                     "--with", "httpie",
                                     "--with", "mokata[embeddings]",
                                     "--with", "code-review-graph",
                                     "--with-executables-from", "code-review-graph"])

    def test_executables_already_exposed_are_kept(self):
        rec = RECEIPT.replace(
            'from = "mokata" }]',
            'from = "mokata" }, { name = "http", install-path = "/x/bin/http", from = "httpie" }]')
        argv = self._plan(rec).argv
        self.assertIn("httpie", argv[argv.index("--with-executables-from") + 1:])

    def test_never_upgrades_mokata_under_the_user(self):
        self.assertNotIn("--upgrade", self._plan().argv)

    def test_carries_the_receipt_python(self):
        plan = self._plan(RECEIPT.replace("entrypoints", 'python = "3.12"\nentrypoints'))
        self.assertIn("--python", plan.argv)
        self.assertEqual(plan.argv[plan.argv.index("--python") + 1], "3.12")

    def test_an_already_recorded_extra_is_not_doubled(self):
        rec = RECEIPT.replace('{ name = "httpie" }',
                              '{ name = "mokata", extras = ["embeddings"] }')
        argv = self._plan(rec).argv
        self.assertEqual(argv.count("mokata[embeddings]"), 1)

    def test_constraints_mean_show_not_run(self):
        plan = self._plan(RECEIPT.replace("entrypoints", 'constraints = [{ name = "x" }]\n'
                                                         'entrypoints'))
        self.assertFalse(plan.runnable)
        self.assertIn("constraints", plan.reason)
        self.assertTrue(plan.display.startswith("uv tool install mokata"))

    def test_editable_source_means_show_not_run(self):
        plan = self._plan(RECEIPT.replace('specifier = "==0.0.24"',
                                          'editable = "/src/mokata"'))
        self.assertFalse(plan.runnable)

    def test_windows_defers_the_self_rebuild(self):
        plan = self._plan(platform="win32")
        self.assertTrue(plan.deferred)
        self.assertFalse(plan.runnable)
        self.assertIn("after setup exits", plan.reason)
        self.assertTrue(plan.display.startswith("uv tool install"))

    def test_no_uv_on_path_shows_the_command(self):
        plan = self._plan(which=_which())
        self.assertFalse(plan.runnable)
        self.assertIn("uv", plan.reason)


RICH_RECEIPT = """[tool]
requirements = [
    { name = "mokata", specifier = "==0.0.24" },
    { name = "mokata", extras = ["embeddings"] },
    { name = "code-review-graph", git = "https://x/y?rev=1" },
    { name = "m", directory = "C:\\\\Users\\\\j\\\\src" },
]
python = "3.12"
entrypoints = [
    { name = "mokata", install-path = "C:\\\\x\\\\mokata.exe", from = "mokata" }, # c
]

[tool.options]
exclude-newer = "2026-01-01T00:00:00Z"
"""


class TestReceiptOnThePythonFloor(unittest.TestCase):
    """Measured at I-03: `uv tool install` built the env on Python 3.10 (no tomllib), and every
    extra failed with "could not read uv's tool receipt". uv's receipt subset is read without it."""

    def test_the_floor_reader_agrees_with_tomllib(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib is the reference and needs 3.11+")
        for text in (RECEIPT, RICH_RECEIPT):
            self.assertEqual(R._ReceiptReader(text).document(), tomllib.loads(text))

    def test_a_plan_is_made_without_tomllib(self):
        import builtins
        real = builtins.__import__

        def no_tomllib(name, *a, **kw):
            if name == "tomllib":
                raise ImportError("3.10")
            return real(name, *a, **kw)
        with _Env("uv-receipt.toml", RECEIPT) as p, \
                unittest.mock.patch.object(builtins, "__import__", no_tomllib):
            plan = R.plan_install(["mokata[embeddings]"], prefix=p, which=_which("uv"),
                                  platform="linux", requires=REQUIRES)
        self.assertTrue(plan.runnable, plan.reason)
        self.assertEqual(plan.argv[3], "mokata==0.0.24")

    def test_outside_the_subset_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            R._ReceiptReader("[[tool]]\nx = 1\n").document()


class TestOtherRoutes(unittest.TestCase):
    SPECS = ["mokata[embeddings]", "code-review-graph"]

    def test_pipx_injects_the_extras_requirements_not_mokata_itself(self):
        with _Env("pipx_metadata.json", "{}") as p:
            plan = R.plan_install(self.SPECS, prefix=p, which=_which("pipx"), requires=REQUIRES)
        self.assertEqual(plan.argv[1:4], ["inject", "--include-apps", "mokata"])
        self.assertIn("--include-apps", plan.argv, "CRG's command must reach PATH")
        self.assertIn("model2vec>=0.3", plan.argv)
        self.assertNotIn("mokata[embeddings]", plan.argv)

    def test_pip_route_uses_this_interpreter(self):
        with _Env() as p:
            plan = R.plan_install(self.SPECS, prefix=p, executable="/v/bin/python",
                                  which=_which(), has_pip=True, requires=REQUIRES)
        self.assertEqual(plan.argv[:4], ["/v/bin/python", "-m", "pip", "install"])
        self.assertIn("code-review-graph", plan.argv)

    def test_uv_pip_route_targets_this_interpreter(self):
        with _Env() as p:
            plan = R.plan_install(self.SPECS, prefix=p, executable="/v/bin/python",
                                  which=_which("uv"), has_pip=False, requires=REQUIRES)
        self.assertEqual(plan.argv[1:5], ["pip", "install", "--python", "/v/bin/python"])

    def test_no_installer_gives_a_fix_not_an_argv(self):
        with _Env() as p:
            plan = R.plan_install(self.SPECS, prefix=p, executable="/v/bin/python",
                                  which=_which(), has_pip=False, requires=REQUIRES)
        self.assertIsNone(plan.argv)
        self.assertIn("ensurepip", plan.display)


class TestInstallExtrasRunsThePlan(unittest.TestCase):
    def _plan(self, **kw):
        return R.InstallPlan(R.UV_TOOL, ["x"], ["uv", "tool", "install", "mokata"],
                             "uv tool install mokata", **kw)

    def test_one_installer_run_for_several_specs(self):
        calls = []
        res = extras_install.install_extras(
            ["a", "b"], plan=self._plan(), out=lambda s: None,
            runner=lambda argv, **kw: calls.append(argv) or
            subprocess.CompletedProcess(argv, 0, "", ""))
        self.assertEqual(len(calls), 1)
        self.assertTrue(res.ok)
        self.assertEqual(res.installer, R.UV_TOOL)
        self.assertEqual(res.fix, "", "a success carries no fix line")

    def test_unrunnable_plan_spawns_nothing_and_carries_the_fix(self):
        calls = []
        res = extras_install.install_extras(
            ["a"], plan=self._plan(deferred=True, reason="Windows"), out=lambda s: None,
            runner=lambda argv, **kw: calls.append(argv))
        self.assertEqual(calls, [])
        self.assertFalse(res.ok)
        self.assertTrue(res.deferred)
        self.assertEqual(res.fix, "uv tool install mokata")

    def test_failure_names_the_installer_and_keeps_the_fix(self):
        res = extras_install.install_extras(
            ["a"], plan=self._plan(), out=lambda s: None,
            runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 2, "", "boom\nlast"))
        self.assertFalse(res.ok)
        self.assertEqual(res.message, "uv-tool exited 2: last")
        self.assertEqual(res.fix, "uv tool install mokata")


if __name__ == "__main__":
    unittest.main()
