"""0.0.25 Lane D1 stage 3 — the `react-web` and `nestjs-api` stacks, and toolchain detection.

What must be true for the user:
  * two new starter stacks ship next to python-web / node-ts / go-service: `react-web` (tagged
    FRONTEND) and `nestjs-api` (tagged BACKEND), each a schema-valid governed manifest that names the
    domain skills it brings and the toolchain candidates it understands;
  * in a real repo, mokata tells you — per package — which of them the package looks like and which
    test runner (vitest / jest), linters (oxlint / eslint) and type checker (tsc) it actually uses,
    from the package's own scripts, config files and dependencies;
  * a React Native app, an Express server and a framework-less TS library are NOT suggested a
    framework stack;
  * detection is suggest-only on BOTH surfaces (`mokata stacks list`, MCP `stacks_list`): it writes
    nothing, and adopting stays the gated `stacks install` path.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import domains as dk
from mokata import mcp_server as M
from mokata import stacks as ST
from mokata.schema import validate_manifest


def _write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _pkg(root, rel_dir, deps=None, dev=None, scripts=None, extra=None):
    data = {"name": os.path.basename(rel_dir) or "root"}
    if deps:
        data["dependencies"] = {d: "1.0.0" for d in deps}
    if dev:
        data["devDependencies"] = {d: "1.0.0" for d in dev}
    if scripts:
        data["scripts"] = scripts
    data.update(extra or {})
    _write(root, os.path.join(rel_dir, "package.json"), json.dumps(data))


def _monorepo(d):
    _pkg(d, "apps/web", deps=["react", "react-dom"], dev=["vite", "vitest", "eslint", "typescript"],
         scripts={"test": "vitest run", "lint": "eslint ."})
    _pkg(d, "apps/api", deps=["@nestjs/core", "@nestjs/common"], dev=["jest", "typescript"],
         scripts={"test": "jest"})
    _write(d, "apps/api/eslint.config.mjs", "export default [];\n")
    _pkg(d, "apps/mobile", deps=["react", "react-native", "expo"], dev=["jest"])
    _pkg(d, "services/legacy", deps=["express"], dev=["mocha"])
    _pkg(d, "packages/utils", dev=["typescript", "vitest"])
    _pkg(d, "node_modules/react-dom", deps=["react-dom"])     # a dependency, never a project


class TheTwoStacksShip(unittest.TestCase):

    def test_both_are_valid_governed_manifests_tagged_by_side(self):
        for name, kind in (("react-web", "frontend"), ("nestjs-api", "backend")):
            with self.subTest(stack=name):
                data = ST.build_stack_manifest(name)
                self.assertEqual([], validate_manifest(data))
                stack = data["settings"]["stack"]
                self.assertEqual(kind, stack["kind"])
                self.assertIn(kind, stack["tags"])
                self.assertEqual(4, len(stack["guardrails"]))
                self.assertEqual({"test_runner": ["vitest", "jest"], "linter": ["oxlint", "eslint"],
                                  "type_checker": ["tsc"]}, stack["toolchain"])
                for dom in stack["domains"]:
                    self.assertIn(dom, dk.DOMAIN_SKILLS, f"{name} names unshipped domain {dom}")

    def test_the_frontend_stack_brings_the_react_pack_and_the_backend_one_the_nest_pack(self):
        self.assertIn("frontend-react", ST.build_stack_manifest("react-web")["settings"]["stack"]
                      ["domains"])
        self.assertIn("backend-nestjs", ST.build_stack_manifest("nestjs-api")["settings"]["stack"]
                      ["domains"])
        self.assertNotIn("frontend", ST.show_stack("nestjs-api")["tags"])
        self.assertNotIn("backend", ST.show_stack("react-web")["tags"])

    def test_the_committed_catalog_mirrors_the_code(self):
        d = os.path.join(os.path.dirname(ST.__file__), "stacks")
        for name in ("react-web", "nestjs-api"):
            with open(os.path.join(d, f"{name}.json"), encoding="utf-8") as fh:
                self.assertEqual(ST.build_stack_manifest(name), json.load(fh))
        with open(os.path.join(d, "index.json"), encoding="utf-8") as fh:
            self.assertEqual(ST.build_index(), json.load(fh))

    def test_installing_react_web_goes_through_the_gated_path(self):
        with tempfile.TemporaryDirectory() as d:
            declined = ST.install_stack(d, "react-web", confirm=lambda _t: False, out=lambda *_: None)
            self.assertFalse(declined.installed)
            ok = ST.install_stack(d, "react-web", assume_yes=True, out=lambda *_: None)
            self.assertTrue(ok.installed)


class TheToolchainIsReadFromThePackage(unittest.TestCase):

    def _tc(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            files = kw.pop("files", ())
            _pkg(d, ".", **kw)
            for f in files:
                _write(d, f, "\n")
            return ST.detect_toolchain(d)

    def test_the_test_script_names_the_runner(self):
        self.assertEqual("vitest", self._tc(dev=["jest", "vitest"],
                                            scripts={"test": "vitest run"}).test_runner)
        self.assertEqual("jest", self._tc(dev=["vitest", "jest"],
                                          scripts={"test": "jest --ci"}).test_runner)

    def test_a_config_file_beats_a_bare_dependency(self):
        self.assertEqual("jest", self._tc(dev=["vitest"], files=["jest.config.ts"]).test_runner)
        self.assertEqual("jest", self._tc(extra={"jest": {"testEnvironment": "node"}},
                                          dev=["vitest"]).test_runner)

    def test_a_dependency_alone_is_enough_and_none_is_none(self):
        self.assertEqual("vitest", self._tc(dev=["vitest"]).test_runner)
        tc = self._tc(deps=["lodash"])
        self.assertEqual(("", (), ""), (tc.test_runner, tc.linters, tc.type_checker))

    def test_linters_are_all_reported_and_typescript_means_tsc(self):
        tc = self._tc(dev=["oxlint", "eslint", "typescript"], scripts={"lint": "oxlint && eslint ."})
        self.assertEqual(("oxlint", "eslint"), tc.linters)
        self.assertEqual("tsc", tc.type_checker)
        self.assertEqual(("eslint",), self._tc(files=["eslint.config.js"]).linters)
        self.assertEqual("tsc", self._tc(files=["tsconfig.json"]).type_checker)


class DetectionSuggestsPerPackage(unittest.TestCase):

    def test_a_monorepo_gets_one_suggestion_per_framework_package(self):
        with tempfile.TemporaryDirectory() as d:
            _monorepo(d)
            got = {(x.stack, x.path, x.kind): x.toolchain for x in ST.detect_stacks(d)}
            self.assertEqual({("react-web", "apps/web", "frontend"),
                              ("nestjs-api", "apps/api", "backend")}, set(got))
            web = got[("react-web", "apps/web", "frontend")]
            self.assertEqual(("vitest", ("eslint",), "tsc"),
                             (web.test_runner, web.linters, web.type_checker))
            api = got[("nestjs-api", "apps/api", "backend")]
            self.assertEqual(("jest", ("eslint",), "tsc"),
                             (api.test_runner, api.linters, api.type_checker))

    def test_near_misses_are_not_suggested_a_framework_stack(self):
        with tempfile.TemporaryDirectory() as d:
            _pkg(d, "mobile", deps=["react", "react-native"])
            _pkg(d, "server", deps=["express"])
            _pkg(d, "lib", dev=["typescript"])
            self.assertEqual([], ST.detect_stacks(d))

    def test_both_surfaces_report_it_and_neither_writes(self):
        from mokata.cli import cmd_stacks
        with tempfile.TemporaryDirectory() as d:
            _monorepo(d)
            before = sorted(os.walk(d))

            class A:
                path, action, target, source, yes, force = d, "list", None, None, False, False
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(0, cmd_stacks(A()))
            out = buf.getvalue()
            self.assertIn("detected in this repo (suggestion only", out)
            self.assertIn("react-web (frontend) at apps/web · test: vitest · lint: eslint · "
                          "types: tsc", out)
            res = M.stacks_list(path=d)
            self.assertEqual({"react-web", "nestjs-api"}, {x["stack"] for x in res["detected"]})
            self.assertEqual(before, sorted(os.walk(d)), "detection is read-only")


if __name__ == "__main__":
    unittest.main()
