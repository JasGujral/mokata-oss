"""0.0.25 Lane D2 — the frontend/backend DOMAIN gates.

What must be true for the user:
  * Each backed domain gate FIRES on a bad repo (exit 1, naming what is wrong) and PASSES a good one
    (exit 0) — a detection fixture per gate, per package manager where it matters.
  * A repo that is not set up for a gate (no budget declared, tool not installed, no build output)
    gets NOT CONFIGURED — exit 3, its own state — and never a pass.
  * Tool-backed gates propagate the tool's exit code. CI has no node toolchain guarantee, so those
    fixtures put a STUB executable on the search path; the stub proves argv + exit propagation, not
    the real tool's semantics (those were verified against the real tools when this was built — see
    the lane report).
  * `route-guarded` says out loud that a guard's POLICY is not judged.
  * `render-regression` is advisory and no skill Contract cites it.
  * `mokata gate check <id>` is the surface that runs them, and its exit code is the verdict.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import skill_contracts
from mokata.govern import domain_gates as dg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))
    return path


def _stub(bin_dir, name, exit_code):
    """A cross-platform stand-in executable that records its argv and exits `exit_code`."""
    os.makedirs(bin_dir, exist_ok=True)
    impl = os.path.join(bin_dir, f"{name}_impl.py")
    record = os.path.join(bin_dir, f"{name}.argv.json")
    with open(impl, "w", encoding="utf-8") as fh:
        fh.write("import json, sys\n"
                 f"open({record!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
                 f"print('stub {name} exiting {exit_code}')\n"
                 f"sys.exit({exit_code})\n")
    if os.name == "nt":
        with open(os.path.join(bin_dir, f"{name}.cmd"), "w", encoding="utf-8") as fh:
            fh.write(f'@"{sys.executable}" "{impl}" %*\r\n@exit /b %ERRORLEVEL%\r\n')
    else:
        launcher = os.path.join(bin_dir, name)
        with open(launcher, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{impl}" "$@"\n')
        os.chmod(launcher, 0o755)
    return record


def _argv(record):
    with open(record, encoding="utf-8") as fh:
        return json.load(fh)


class _Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mokata-d2-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.bin = os.path.join(self.root, "_stubbin")
        # An EMPTY search path beyond the stub dir: the real tools on this machine must never be
        # what a fixture measures.
        self.path = self.bin

    def run_gate(self, gid, **kw):
        kw.setdefault("path", self.path)
        return dg.run_domain_gate(gid, self.root, **kw)

    def assertFires(self, run, *needles):
        self.assertEqual(run.exit_code, dg.EXIT_FAIL, run.message)
        self.assertEqual(run.check.state, dg.FAIL, run.check.summary)
        text = dg.render_run(run)
        for n in needles:
            self.assertIn(n, text)

    def assertPasses(self, run):
        self.assertEqual(run.exit_code, dg.EXIT_PASS, dg.render_run(run))
        self.assertEqual(run.check.state, dg.PASS)

    def assertNotConfigured(self, run, needle=""):
        self.assertEqual(run.exit_code, dg.EXIT_NOT_CONFIGURED, dg.render_run(run))
        self.assertEqual(run.check.state, dg.NOT_CONFIGURED)
        self.assertNotEqual(run.exit_code, dg.EXIT_PASS, "not configured is never a pass")
        self.assertIn(needle, run.message)


# ===================================================================== the registry
class TestTheRegistryTellsTheTruth(unittest.TestCase):
    BACKED = ("dep-cooldown", "route-guarded", "bundle-budget", "react-lint",
              "bundle-secret-scan", "dep-vulnerability")

    def test_every_domain_gate_is_backed_by_the_module_that_runs_it(self):
        for gid in self.BACKED:
            ref = skill_contracts.GATES[gid]
            self.assertTrue(ref.backed, gid)
            self.assertEqual(ref.enforcement_point, "src/mokata/govern/domain_gates.py", gid)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, ref.enforcement_point)))
        self.assertEqual(set(dg.DOMAIN_GATE_IDS), set(self.BACKED))

    def test_render_regression_is_advisory_and_no_contract_cites_it(self):
        ref = skill_contracts.GATES["render-regression"]
        self.assertFalse(ref.backed)
        self.assertEqual(ref.enforcement_point, "")
        for name, contract in skill_contracts.CONTRACTS.items():
            cited = {c.gate for c in contract.must_not + contract.depends_on}
            self.assertNotIn("render-regression", cited, name)
            self.assertNotEqual(contract.headline_gate, "render-regression", name)

    def test_render_regression_is_never_run_as_a_check(self):
        run = dg.run_domain_gate("render-regression", ROOT)
        self.assertEqual(run.exit_code, dg.EXIT_NOT_CONFIGURED)
        self.assertIn("ADVISORY", run.message)
        self.assertNotIn("render-regression", dg.DOMAIN_GATE_IDS)

    def test_route_guarded_states_policy_is_not_judged(self):
        self.assertIn("human review", skill_contracts.GATES["route-guarded"].one_line)

    def test_an_unknown_gate_is_refused_not_passed(self):
        with self.assertRaises(ValueError):
            dg.run_domain_gate("no-such-gate", ROOT)


# ===================================================================== dep-cooldown
class TestDepCooldown(_Repo):
    def pkg(self, deps=None, **extra):
        body = {"name": "app", "version": "1.0.0", "dependencies": deps or {"react": "^19.0.0"}}
        body.update(extra)
        _write(self.root, "package.json", json.dumps(body))

    # -- npm
    def test_npm_without_cooldown_fires(self):
        self.pkg()
        _write(self.root, "package-lock.json", "{}")
        self.assertFires(self.run_gate("dep-cooldown"), "npm: no release-age cooldown",
                         ".npmrc min-release-age")

    def test_npm_with_a_cooldown_passes(self):
        self.pkg()
        _write(self.root, "package-lock.json", "{}")
        _write(self.root, ".npmrc", "registry=https://registry.npmjs.org/\nmin-release-age=7\n")
        self.assertPasses(self.run_gate("dep-cooldown"))

    def test_npm_cooldown_below_the_floor_fires(self):
        self.pkg()
        _write(self.root, "package-lock.json", "{}")
        _write(self.root, ".npmrc", "min-release-age=1\n")
        self.assertFires(self.run_gate("dep-cooldown"), "below the 3-day floor")
        self.assertPasses(self.run_gate("dep-cooldown", min_days=1))

    # -- pnpm
    def test_pnpm_without_cooldown_fires(self):
        self.pkg()
        _write(self.root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        self.assertFires(self.run_gate("dep-cooldown"), "pnpm: no release-age cooldown")

    def test_pnpm_workspace_minimum_release_age_in_minutes_passes(self):
        self.pkg()
        _write(self.root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        _write(self.root, "pnpm-workspace.yaml", "packages:\n  - 'apps/*'\nminimumReleaseAge: 10080\n")
        self.assertPasses(self.run_gate("dep-cooldown"))

    def test_pnpm_minutes_are_not_read_as_days(self):
        # 1440 minutes = 1 day: below a 3-day floor. Reading the number as days would pass it.
        self.pkg()
        _write(self.root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        _write(self.root, "pnpm-workspace.yaml", "minimumReleaseAge: 1440\n")
        self.assertFires(self.run_gate("dep-cooldown"), "cooldown 1 day(s)")

    def test_pnpm_npmrc_spelling_is_read(self):
        self.pkg()
        _write(self.root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        _write(self.root, ".npmrc", "minimum-release-age=5000\n")
        self.assertPasses(self.run_gate("dep-cooldown"))

    def test_a_nested_yaml_key_is_not_mistaken_for_the_setting(self):
        self.pkg()
        _write(self.root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        _write(self.root, "pnpm-workspace.yaml", "catalog:\n  minimumReleaseAge: 99999\n")
        self.assertFires(self.run_gate("dep-cooldown"), "pnpm: no release-age cooldown")

    # -- yarn
    def test_yarn_without_cooldown_fires(self):
        self.pkg()
        _write(self.root, "yarn.lock", "# yarn lockfile\n")
        _write(self.root, ".yarnrc.yml", "nodeLinker: node-modules\n")
        self.assertFires(self.run_gate("dep-cooldown"), "yarn: no release-age cooldown")

    def test_yarn_duration_passes(self):
        self.pkg()
        _write(self.root, "yarn.lock", "# yarn lockfile\n")
        _write(self.root, ".yarnrc.yml", "nodeLinker: node-modules\nnpmMinimalAgeGate: 7d\n")
        self.assertPasses(self.run_gate("dep-cooldown"))

    def test_yarn_bare_number_is_minutes(self):
        self.pkg()
        _write(self.root, "yarn.lock", "# yarn lockfile\n")
        _write(self.root, ".yarnrc.yml", "npmMinimalAgeGate: 60\n")
        self.assertFires(self.run_gate("dep-cooldown"), "below the 3-day floor")

    # -- specs that bypass every age gate
    def test_git_and_url_dependencies_fire_even_with_a_cooldown(self):
        self.pkg(deps={"a": "github:acme/a", "b": "git+https://example.com/b.git",
                       "c": "acme/c#main", "d": "https://example.com/d.tgz",
                       "ok-scoped": "npm:@acme/real@^1.0.0", "ok": "^2.0.0",
                       "local": "file:../local"},
                 devDependencies={"e": "gitlab:acme/e"})
        _write(self.root, "package-lock.json", "{}")
        _write(self.root, ".npmrc", "min-release-age=7\n")
        run = self.run_gate("dep-cooldown")
        self.assertFires(run, "dependencies.a", "dependencies.b", "dependencies.c",
                         "dependencies.d", "devDependencies.e")
        text = dg.render_run(run)
        for fine in ("ok-scoped", "dependencies.ok ", "local"):
            self.assertNotIn(fine, text)

    def test_an_override_to_a_git_spec_fires(self):
        self.pkg(overrides={"lodash": "github:evil/lodash"})
        _write(self.root, "package-lock.json", "{}")
        _write(self.root, ".npmrc", "min-release-age=7\n")
        self.assertFires(self.run_gate("dep-cooldown"), "overrides.lodash")

    def test_no_package_json_is_not_configured(self):
        self.assertNotConfigured(self.run_gate("dep-cooldown"), "no readable package.json")


# ===================================================================== route-guarded
_BAD_CONTROLLER = """\
    import { Controller, Get, Post, UseGuards } from '@nestjs/common';
    import { AuthGuard } from './auth.guard';

    @Controller('orders')
    export class OrdersController {
      constructor(private readonly svc: OrdersService) {}

      @Get(':id')
      @UseGuards(AuthGuard)
      findOne(@Param('id') id: string) { return this.svc.find(id); }

      // @UseGuards(AuthGuard)   <- commented out: must NOT count
      @Post()
      async create(@Body() dto: CreateOrderDto) {
        const s = "@UseGuards(fake) in a string { not code }";
        return this.svc.create(dto);
      }
    }
"""


class TestRouteGuarded(_Repo):
    def test_an_unguarded_handler_fires_and_is_named(self):
        _write(self.root, "src/orders/orders.controller.ts", _BAD_CONTROLLER)
        run = self.run_gate("route-guarded")
        self.assertFires(run, "src/orders/orders.controller.ts:14 OrdersController.create",
                         "@Post()", "1 of 2 route handler(s) unguarded")
        self.assertNotIn("findOne", dg.render_run(run))

    def test_the_result_says_policy_is_not_judged(self):
        _write(self.root, "src/orders/orders.controller.ts", _BAD_CONTROLLER)
        self.assertIn("human review", dg.render_run(self.run_gate("route-guarded")))

    def test_a_public_handler_passes(self):
        _write(self.root, "src/orders/orders.controller.ts",
               _BAD_CONTROLLER.replace("    @Post()", "    @Public()\n      @Post()"))
        self.assertPasses(self.run_gate("route-guarded"))

    def test_a_class_level_guard_covers_every_handler(self):
        _write(self.root, "src/a.controller.ts", """\
            @UseGuards(JwtGuard)
            @Controller('a')
            export class AController {
              @Get() list() { return []; }
              @Delete(':id') remove() { return 1; }
            }
        """)
        self.assertPasses(self.run_gate("route-guarded"))

    def test_a_global_app_guard_covers_every_handler(self):
        _write(self.root, "src/orders/orders.controller.ts", _BAD_CONTROLLER)
        _write(self.root, "src/app.module.ts", """\
            @Module({ providers: [{ provide: APP_GUARD, useClass: JwtGuard }] })
            export class AppModule {}
        """)
        self.assertPasses(self.run_gate("route-guarded"))

    def test_use_global_guards_in_main_covers_every_handler(self):
        _write(self.root, "src/orders/orders.controller.ts", _BAD_CONTROLLER)
        _write(self.root, "src/main.ts", "const app = await NestFactory.create(AppModule);\n"
                                         "app.useGlobalGuards(new JwtGuard());\n")
        self.assertPasses(self.run_gate("route-guarded"))

    def test_a_commented_out_global_guard_does_not_count(self):
        _write(self.root, "src/orders/orders.controller.ts", _BAD_CONTROLLER)
        _write(self.root, "src/main.ts", "// app.useGlobalGuards(new JwtGuard());\n")
        self.assertFires(self.run_gate("route-guarded"), "OrdersController.create")

    def test_a_composite_decorator_wrapping_use_guards_counts(self):
        _write(self.root, "src/auth/auth.decorator.ts", """\
            export function Auth(...roles: Role[]) {
              return applyDecorators(SetMetadata('roles', roles), UseGuards(AuthGuard, RolesGuard));
            }
        """)
        _write(self.root, "src/orders/orders.controller.ts",
               _BAD_CONTROLLER.replace("    @Post()", "    @Auth('admin')\n      @Post()"))
        self.assertPasses(self.run_gate("route-guarded"))

    def test_a_spec_file_is_not_scanned(self):
        _write(self.root, "src/orders/orders.controller.ts",
               _BAD_CONTROLLER.replace("    @Post()", "    @Public()\n      @Post()"))
        _write(self.root, "src/orders/orders.controller.spec.ts", _BAD_CONTROLLER)
        self.assertPasses(self.run_gate("route-guarded"))

    def test_no_controller_is_not_configured(self):
        _write(self.root, "src/util.ts", "export const x = 1;\n")
        self.assertNotConfigured(self.run_gate("route-guarded"), "no NestJS @Controller")


# ===================================================================== bundle-budget
class TestBundleBudget(_Repo):
    def sized(self):
        _write(self.root, "package.json", json.dumps(
            {"name": "app", "size-limit": [{"path": "dist/app.js", "limit": "10 kB"}]}))

    def test_no_budget_declared_is_not_configured(self):
        _write(self.root, "package.json", json.dumps({"name": "app"}))
        _stub(self.bin, "size-limit", 0)
        self.assertNotConfigured(self.run_gate("bundle-budget"), "no budget is declared")

    def test_budget_declared_but_tool_absent_is_not_configured(self):
        self.sized()
        self.assertNotConfigured(self.run_gate("bundle-budget"), "not installed")

    def test_over_budget_fires_with_the_tools_exit(self):
        self.sized()
        _stub(self.bin, "size-limit", 1)
        self.assertFires(self.run_gate("bundle-budget"), "over budget", "exit 1",
                         "backing: size-limit")

    def test_within_budget_passes(self):
        self.sized()
        _stub(self.bin, "size-limit", 0)
        self.assertPasses(self.run_gate("bundle-budget"))

    def test_the_repos_own_node_modules_bin_is_found(self):
        self.sized()
        record = _stub(os.path.join(self.root, "node_modules", ".bin"), "size-limit", 0)
        self.assertPasses(dg.run_domain_gate("bundle-budget", self.root, path=""))
        self.assertEqual(_argv(record), [])

    def test_lhci_assert_is_run_when_a_lighthouserc_is_present(self):
        _write(self.root, "lighthouserc.json", "{}")
        record = _stub(self.bin, "lhci", 1)
        self.assertFires(self.run_gate("bundle-budget"), "lhci assert")
        self.assertEqual(_argv(record), ["assert"])

    def test_an_unrecognised_tool_exit_is_never_a_pass(self):
        self.sized()
        _stub(self.bin, "size-limit", 2)
        run = self.run_gate("bundle-budget")
        self.assertEqual(run.check.state, dg.ERROR)
        self.assertEqual(run.exit_code, dg.EXIT_FAIL)


# ===================================================================== react-lint
class TestReactLint(_Repo):
    def hooks_config(self):
        _write(self.root, "eslint.config.js",
               'import reactHooks from "eslint-plugin-react-hooks";\n'
               "export default [reactHooks.configs.flat.recommended];\n")

    def test_no_eslint_config_is_not_configured(self):
        _stub(self.bin, "eslint", 0)
        self.assertNotConfigured(self.run_gate("react-lint"), "no eslint config")

    def test_eslint_without_react_hooks_is_not_configured(self):
        _write(self.root, "eslint.config.js", "export default [];\n")
        _stub(self.bin, "eslint", 0)
        self.assertNotConfigured(self.run_gate("react-lint"), "does not enable")

    def test_a_lint_failure_fires_and_warnings_count(self):
        self.hooks_config()
        record = _stub(self.bin, "eslint", 1)
        self.assertFires(self.run_gate("react-lint"), "errors or warnings")
        self.assertEqual(_argv(record), ["--max-warnings", "0", "."])

    def test_a_clean_lint_passes(self):
        self.hooks_config()
        _stub(self.bin, "eslint", 0)
        self.assertPasses(self.run_gate("react-lint"))

    def test_legacy_eslintrc_in_package_json_is_detected(self):
        _write(self.root, "package.json", json.dumps(
            {"name": "app", "eslintConfig": {"extends": ["plugin:react-hooks/recommended"]}}))
        _stub(self.bin, "eslint", 0)
        self.assertPasses(self.run_gate("react-lint"))

    def test_configured_but_eslint_absent_is_not_configured(self):
        self.hooks_config()
        self.assertNotConfigured(self.run_gate("react-lint"), "not installed")


# ===================================================================== bundle-secret-scan
class TestBundleSecretScan(_Repo):
    def test_no_build_output_is_not_configured(self):
        _stub(self.bin, "gitleaks", 0)
        self.assertNotConfigured(self.run_gate("bundle-secret-scan"), "build first")

    def test_gitleaks_absent_is_not_configured(self):
        _write(self.root, "dist/app.js", "x\n")
        self.assertNotConfigured(self.run_gate("bundle-secret-scan"), "not installed")

    def test_a_leak_fires(self):
        _write(self.root, "dist/app.js", "x\n")
        record = _stub(self.bin, "gitleaks", 1)
        self.assertFires(self.run_gate("bundle-secret-scan"), "credential")
        argv = _argv(record)
        self.assertEqual(argv[0], "dir")
        self.assertEqual(os.path.normpath(argv[1]), os.path.join(self.root, "dist"))
        self.assertIn("--redact", argv)

    def test_clean_output_passes_and_only_existing_dirs_are_scanned(self):
        _write(self.root, ".next/static/chunk.js", "x\n")
        record = _stub(self.bin, "gitleaks", 0)
        self.assertPasses(self.run_gate("bundle-secret-scan"))
        self.assertEqual(os.path.normpath(_argv(record)[1]),
                         os.path.join(self.root, ".next", "static"))

    def test_a_gitleaks_error_is_never_a_pass(self):
        _write(self.root, "dist/app.js", "x\n")
        _stub(self.bin, "gitleaks", 126)
        run = self.run_gate("bundle-secret-scan")
        self.assertEqual(run.check.state, dg.ERROR)
        self.assertEqual(run.exit_code, dg.EXIT_FAIL)


# ===================================================================== dep-vulnerability
class TestDepVulnerability(_Repo):
    def test_no_lockfile_is_not_configured(self):
        _stub(self.bin, "npm", 0)
        self.assertNotConfigured(self.run_gate("dep-vulnerability"), "no JS lockfile")

    def test_npm_audit_high_fires(self):
        _write(self.root, "package-lock.json", "{}")
        record = _stub(self.bin, "npm", 1)
        self.assertFires(self.run_gate("dep-vulnerability"), "high/critical")
        self.assertEqual(_argv(record), ["audit", "--audit-level=high"])

    def test_npm_audit_clean_passes(self):
        _write(self.root, "package-lock.json", "{}")
        _stub(self.bin, "npm", 0)
        self.assertPasses(self.run_gate("dep-vulnerability"))

    def test_the_osv_route_is_advisory_it_reports_but_never_blocks(self):
        _write(self.root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        record = _stub(self.bin, "osv-scanner", 1)
        run = self.run_gate("dep-vulnerability")
        self.assertTrue(run.check.advisory)
        self.assertEqual(run.check.state, dg.FAIL)
        self.assertEqual(run.exit_code, dg.EXIT_PASS, "an unverified route must not block")
        self.assertIn("would have blocked", run.message)
        self.assertEqual(_argv(record), ["scan", "source", "-r", "."])

    def test_the_osv_route_absent_is_not_configured(self):
        _write(self.root, "yarn.lock", "# yarn\n")
        self.assertNotConfigured(self.run_gate("dep-vulnerability"), "osv-scanner is not installed")


# ===================================================================== the surface
class TestTheCliRunsTheGate(_Repo):
    def cli(self, *args):
        from mokata import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["gate", "check", *args, "--path", self.root])
        return code, buf.getvalue()

    def test_exit_code_is_the_verdict(self):
        _write(self.root, "package.json", json.dumps({"name": "app", "dependencies": {"a": "^1"}}))
        code, out = self.cli("dep-cooldown")
        self.assertEqual(code, dg.EXIT_FAIL, out)
        self.assertIn("dep-cooldown blocked", out)
        _write(self.root, ".npmrc", "min-release-age=7\n")
        code, out = self.cli("dep-cooldown")
        self.assertEqual(code, dg.EXIT_PASS, out)
        code, out = self.cli("dep-cooldown", "--min-days", "30")
        self.assertEqual(code, dg.EXIT_FAIL, out)

    def test_not_configured_exits_three(self):
        code, out = self.cli("route-guarded")
        self.assertEqual(code, dg.EXIT_NOT_CONFIGURED)
        self.assertIn("NOT CONFIGURED", out)

    def test_render_regression_is_reported_advisory(self):
        code, out = self.cli("render-regression")
        self.assertEqual(code, dg.EXIT_NOT_CONFIGURED)
        self.assertIn("no Contract may cite", out)


if __name__ == "__main__":
    unittest.main()
