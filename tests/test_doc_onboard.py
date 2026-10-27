"""DOC-ONBOARD — self-healing onboarding: stale wiring is LOUD
where users actually look, and the fix is findable by the error string.

THE LOAD-BEARING CONSTRAINT these tests encode: when the harness wiring is a dead bare name,
mokata's own code NEVER RUNS. So remediation cannot live only inside mokata — it has to ride
the channels that still execute:

  1. the UPDATE CLI (run by the shell, not the hook) — `mokata update` prints the upgrade line
     AND its follow-up `mokata setup claude` (0.0.26 L2: print-only; tests/test_l2_mok_update.py);
  2. the SessionStart BRIEFING (when the harness IS alive) — one line, only when drifted;
  3. the MCP STATUS surface (covers hooks-dead-but-MCP-alive) — the same verdict, structured;
  4. DOCS keyed to the literal error string (found by search, not by mokata).

And the G1 property throughout: ONE function decides "is the wiring stale"; three surfaces
render it. Plus the negative that keeps it usable — CURRENT wiring nags NOBODY.
"""

import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _support  # noqa: F401  (puts src/ on the import path)

from mokata import harness_setup, hook_wiring
from mokata.cli import main
from mokata.govern.doctor import (
    hook_resolution_findings,
    wiring_check_lines,
    wiring_drift_findings,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# The literal string a user pastes into a search box when the wiring is a dead bare name.
ERROR_STRING = "mokata-hook: command not found"


def run_cli(argv, stdin=""):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


# --------------------------------------------------------------------------------------
# fixtures — settings.json wiring, built from mokata's OWN single source
# --------------------------------------------------------------------------------------
def _settings_path(root, scope_home=None):
    base = Path(scope_home) if scope_home else Path(root)
    p = base / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _wire_current(root):
    """Write EXACTLY the wiring today's `mokata setup claude` writes — the same
    `_merge_hooks` call `apply_setup` makes, from the same plan. This is the "current"
    fixture: whatever setup writes, this writes, so it can never drift from the product."""
    path = _settings_path(root)
    plan = harness_setup.plan_setup("claude", root=str(root))
    harness_setup._merge_hooks(path, plan.hook_commands)
    return path


def _mutate_wiring(root, fn):
    """Write current wiring, then let `fn` mutate the parsed settings dict in place."""
    path = _wire_current(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _drift(root, home):
    return hook_wiring.wiring_drift(str(root), str(home))


class WiringCase(unittest.TestCase):
    """A temp repo + an EMPTY temp home, so the user-scope surface is always deterministic
    (never this machine's real ~/.claude/settings.json)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.home = Path(self._tmp.name) / "home"
        self.root.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)


# =====================================================================================
# 2 — ONE verdict, three surfaces (and NO NAG when the wiring is current)
# =====================================================================================
class TestWiringDriftVerdict(WiringCase):
    def test_current_wiring_is_not_drifted(self):
        _wire_current(self.root)
        drift = _drift(self.root, self.home)
        self.assertFalse(drift.drifted, drift.reasons)

    def test_a_missing_hook_is_drift(self):
        # The real 0.0.x shape: wiring written before an event existed (PostToolUse
        # dirty-track) keeps working and stays silently incomplete forever.
        _mutate_wiring(self.root, lambda d: d["hooks"].pop("PostToolUse"))
        drift = _drift(self.root, self.home)
        self.assertTrue(drift.drifted)
        self.assertIn("missing", drift.codes)
        self.assertIn("PostToolUse", " ".join(drift.reasons))

    def test_a_stale_matcher_is_drift(self):
        def stale(d):
            for block in d["hooks"]["PreToolUse"]:
                block["matcher"] = "Write|Edit"      # pre-Bash matcher
        _mutate_wiring(self.root, stale)
        drift = _drift(self.root, self.home)
        self.assertTrue(drift.drifted)
        self.assertIn("matcher", drift.codes)

    def test_shell_form_wiring_is_drift(self):
        # Pre-HOOK-SHELL-AGNOSTIC wiring: a command string with no `args`. It resolves fine
        # on POSIX, so nothing else in mokata calls it out — but it is stale wiring.
        def to_shell_form(d):
            for blocks in d["hooks"].values():
                for block in blocks:
                    for h in block["hooks"]:
                        args = h.pop("args", None)
                        if args:
                            h["command"] = '"%s" %s' % (h["command"], " ".join(args))
        _mutate_wiring(self.root, to_shell_form)
        drift = _drift(self.root, self.home)
        self.assertTrue(drift.drifted)
        self.assertIn("shell-form", drift.codes)

    def test_a_dead_command_is_drift_too(self):
        # The hooks-dead-but-MCP-alive case: mokata's hooks never run, so only a surface
        # that is NOT a hook can tell the user. The verdict must carry it.
        def kill(d):
            for blocks in d["hooks"].values():
                for block in blocks:
                    for h in block["hooks"]:
                        h["command"] = "/nowhere/bin/mokata-hook"
        _mutate_wiring(self.root, kill)
        drift = _drift(self.root, self.home)
        self.assertTrue(drift.drifted)
        self.assertIn("dead", drift.codes)

    def test_nothing_wired_is_not_drift(self):
        # Silence here has always meant "no mokata hooks are wired", never "the wiring is
        # fine". An unwired repo (or `setup --no-hooks`) must not be nagged.
        drift = _drift(self.root, self.home)
        self.assertFalse(drift.drifted)
        self.assertFalse(drift.checked)

    def test_a_users_own_hooks_are_never_judged(self):
        path = _settings_path(self.root)
        path.write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Write", "hooks": [{"type": "command",
                                            "command": "/nowhere/their-linter.sh"}]}]}}),
            encoding="utf-8")
        self.assertFalse(_drift(self.root, self.home).drifted)

    def test_the_verdict_never_raises_on_a_broken_surface(self):
        path = _settings_path(self.root)
        path.write_text("{not json at all", encoding="utf-8")
        self.assertFalse(_drift(self.root, self.home).drifted)

    def test_a_check_that_blows_up_is_not_a_clean_bill_of_health(self):
        with mock.patch("mokata.hook_wiring._settings_hooks",
                        side_effect=RuntimeError("boom")):
            drift = _drift(self.root, self.home)
        self.assertFalse(drift.drifted)     # doctor's hooks-unverifiable owns the loud report
        self.assertFalse(drift.checked)

    def test_the_expected_wiring_is_single_sourced_with_what_setup_writes(self):
        # The G1 property at the root: the shape drift CHECKS is the shape setup WRITES.
        plan = harness_setup.plan_setup("claude", root=str(self.root))
        expected = harness_setup.expected_hook_wiring()
        self.assertEqual(sorted(expected), sorted(plan.hook_commands))
        for event, specs in expected.items():
            wired = plan.hook_commands[event]
            self.assertEqual([s["matcher"] for s in specs],
                             [w.get("matcher") for w in wired])
            self.assertEqual([s["subcommand"] for s in specs],
                             [w["args"][0] for w in wired])


class TestThreeSurfacesOneSource(WiringCase):
    def test_doctor_names_the_drift_finding(self):
        _mutate_wiring(self.root, lambda d: d["hooks"].pop("PostToolUse"))
        findings = wiring_drift_findings(SimpleNamespace(root=str(self.root)),
                                         str(self.home))
        self.assertEqual([f.code for f in findings], ["hooks-wiring-stale"])
        self.assertIn("mokata setup claude", findings[0].detail)

    def test_doctor_is_quiet_when_the_wiring_is_current(self):
        _wire_current(self.root)
        self.assertEqual(wiring_drift_findings(SimpleNamespace(root=str(self.root)),
                                               str(self.home)), [])

    def test_doctor_does_not_say_a_dead_hook_twice(self):
        # `hooks-not-firing` already owns the dead-command story with its exact command and
        # remedy. Repeating it as "stale" would bury both.
        def kill(d):
            for blocks in d["hooks"].values():
                for block in blocks:
                    for h in block["hooks"]:
                        h["command"] = "/nowhere/bin/mokata-hook"
        _mutate_wiring(self.root, kill)
        shim = SimpleNamespace(root=str(self.root))
        self.assertIn("hooks-not-firing",
                      [f.code for f in hook_resolution_findings(shim, str(self.home))])
        self.assertEqual([f.code for f in wiring_drift_findings(shim, str(self.home))], [])

    def test_the_prior_hook_resolution_findings_are_untouched(self):
        # The existing named findings keep their exact behaviour: current wiring, no findings.
        _wire_current(self.root)
        self.assertEqual(hook_resolution_findings(SimpleNamespace(root=str(self.root)),
                                                  str(self.home)), [])

    def test_all_three_surfaces_read_the_same_function(self):
        from mokata import bootstrap
        from mokata.govern import doctor
        from mokata.mcp import tools_read
        for src in (inspect.getsource(doctor.wiring_drift_findings),
                    inspect.getsource(bootstrap._wiring_drift_line),
                    inspect.getsource(tools_read.status)):
            self.assertIn("wiring_drift", src)
        self.assertIn("wiring_drift_findings", inspect.getsource(doctor.diagnose))

    def test_the_briefing_carries_one_line_when_drifted(self):
        from mokata.bootstrap import build_bootstrap
        from test_bootstrap import make_surface
        drifted = hook_wiring.WiringDrift(
            items=[("missing", "the PostToolUse dirty-track hook is not wired")])
        with mock.patch("mokata.hook_wiring.wiring_drift", return_value=drifted):
            text = build_bootstrap(make_surface()).text
        # ONE line, not a section — the briefing has a 2k-token budget and a stale-wiring
        # warning is not the thing the session is about.
        added = [ln for ln in text.splitlines() if "PostToolUse" in ln]
        self.assertEqual(len(added), 1, text)
        self.assertIn("mokata setup claude", added[0])

    def test_the_briefing_is_byte_identical_when_the_wiring_is_current(self):
        # THE no-nag property. A current install must see EXACTLY the briefing it saw before.
        from mokata.bootstrap import build_bootstrap
        from test_bootstrap import make_surface
        clean = hook_wiring.WiringDrift()
        with mock.patch("mokata.hook_wiring.wiring_drift", return_value=clean):
            with_check = build_bootstrap(make_surface()).text
        with mock.patch("mokata.bootstrap._wiring_drift_line", return_value=None):
            without = build_bootstrap(make_surface()).text
        self.assertEqual(with_check, without)

    def test_the_briefing_stays_within_budget_with_the_line(self):
        from mokata.bootstrap import build_bootstrap
        from test_bootstrap import make_surface
        drifted = hook_wiring.WiringDrift(items=[("dead", "the wired hooks do not resolve")])
        with mock.patch("mokata.hook_wiring.wiring_drift", return_value=drifted):
            self.assertTrue(build_bootstrap(make_surface()).within_budget)

    def test_the_briefing_never_breaks_on_a_drift_check_that_raises(self):
        from mokata.bootstrap import build_bootstrap
        from test_bootstrap import make_surface
        with mock.patch("mokata.hook_wiring.wiring_drift",
                        side_effect=RuntimeError("boom")):
            self.assertIn("Active gates", build_bootstrap(make_surface()).text)

    def _init(self):
        from mokata.init import init_repo
        init_repo(root=str(self.root), profile="standard", assume_yes=True,
                  out=lambda _s: None)

    def test_mcp_status_reports_the_stale_wiring(self):
        # The hooks-dead-but-MCP-alive channel. mokata's hooks never run, so this surface is
        # the only one that can still speak.
        from mokata.mcp import tools_read
        self._init()
        drifted = hook_wiring.WiringDrift(
            items=[("dead", "the wired hooks do not resolve")], where="/x/settings.json")
        with mock.patch("mokata.hook_wiring.wiring_drift", return_value=drifted):
            resp = tools_read.status(path=str(self.root))
        self.assertTrue(resp["wiring"]["stale"])
        self.assertIn("dead", resp["wiring"]["codes"])
        self.assertIn("mokata setup claude", json.dumps(resp["wiring"]))

    def test_mcp_status_has_no_wiring_key_when_current(self):
        from mokata.mcp import tools_read
        self._init()
        with mock.patch("mokata.hook_wiring.wiring_drift",
                        return_value=hook_wiring.WiringDrift()):
            resp = tools_read.status(path=str(self.root))
        self.assertNotIn("wiring", resp)


class TestDoctorWiringFlag(WiringCase):
    def test_doctor_wiring_reports_current_wiring_as_ok(self):
        _wire_current(self.root)
        ok, lines = wiring_check_lines(root=str(self.root), home=str(self.home))
        self.assertTrue(ok, lines)
        self.assertTrue(lines)

    def test_doctor_wiring_fails_and_names_the_fix_when_stale(self):
        _mutate_wiring(self.root, lambda d: d["hooks"].pop("PostToolUse"))
        ok, lines = wiring_check_lines(root=str(self.root), home=str(self.home))
        self.assertFalse(ok)
        self.assertIn("mokata setup claude", "\n".join(lines))

    def test_an_unresolvable_plugin_mcp_server_does_not_fail_the_wiring_check(self):
        # Caught on-device: a correctly re-wired repo still exited 1 because this machine's
        # plugin manifest registers a bare `mokata-mcp`. That warning is about the MCP SERVER
        # on the plugin route — a different axis, informational everywhere else in doctor — and
        # letting it decide here would have `mokata upgrade` report a problem straight after a
        # re-wire that worked. It must be REPORTED and not counted.
        from test_hook_resolve import _fake_plugin
        _wire_current(self.root)
        _fake_plugin(self.home, ship_shim=True, mcp_command="definitely-not-on-any-path-xyz")
        ok, lines = wiring_check_lines(root=str(self.root), home=str(self.home))
        text = "\n".join(lines)
        self.assertIn("plugin-mcp-unresolvable", text)   # still told
        self.assertTrue(ok, text)                        # but it isn't THIS check's verdict

    def test_the_flag_works_on_an_uninitialized_repo(self):
        # The upgrade tail runs it right after a pip upgrade — it must not require `.mokata/`.
        # Plain `mokata doctor` REFUSES there ("not initialized"); `--wiring` must still return
        # a wiring VERDICT. The exit code is left to the machine's real wiring (this asserts the
        # flag answers, not what this particular box happens to be wired to).
        self.assertFalse((self.root / ".mokata").exists())
        rc, out = run_cli(["doctor", "--wiring", "--path", str(self.root)])
        self.assertIn(rc, (0, 1))
        self.assertNotIn("not initialized", out)
        self.assertIn("hooks:", out)


# =====================================================================================
# 3 + 4 — the DOCS: found by the error string, and unambiguous about which command when
# =====================================================================================
class TestTroubleshootingDocs(unittest.TestCase):
    PAGE = DOCS / "how-to" / "fix-mokata-hook-command-not-found.md"

    def test_the_page_is_TITLED_by_the_literal_error_string(self):
        # A web/LLM search for the error must land here — so the error IS the H1.
        self.assertTrue(self.PAGE.exists(), self.PAGE)
        first = self.PAGE.read_text(encoding="utf-8").lstrip().splitlines()[0]
        self.assertTrue(first.startswith("# "), first)
        self.assertIn(ERROR_STRING, first)

    def test_the_page_carries_the_fix_and_the_verification(self):
        text = self.PAGE.read_text(encoding="utf-8")
        self.assertIn("mokata setup claude", text)
        self.assertIn("mokata doctor --wiring", text)

    def test_the_page_is_in_the_nav(self):
        nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        self.assertIn("how-to/fix-mokata-hook-command-not-found.md", nav)

    def test_the_release_notes_point_at_it(self):
        notes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        head = notes.split("## [0.0.15]")[0]
        self.assertIn(ERROR_STRING, head)


class TestWhichCommandWhenDocs(unittest.TestCase):
    PAGE = DOCS / "how-to" / "which-setup-command.md"

    def test_the_decision_table_names_all_three_routes(self):
        self.assertTrue(self.PAGE.exists(), self.PAGE)
        text = self.PAGE.read_text(encoding="utf-8")
        self.assertIn("mokata init", text)
        self.assertIn("mokata setup claude", text)
        self.assertIn("/plugin install", text)
        self.assertIn("|", text)                       # an actual table, not prose

    def test_it_carries_the_upgrade_runbook(self):
        text = self.PAGE.read_text(encoding="utf-8").lower()
        self.assertIn("upgrade", text)
        self.assertIn("mokata update", text)

    def test_it_is_in_the_nav(self):
        nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        self.assertIn("how-to/which-setup-command.md", nav)

    def test_the_canonical_pages_do_not_conflate_init_with_setup(self):
        # The live confusion: docs that present `mokata init` as if it wired the harness.
        # Every canonical entry page must point at the decision table so the two never blur.
        for name in ("getting-started.md", "quickstart.md"):
            text = (DOCS / name).read_text(encoding="utf-8")
            self.assertIn("which-setup-command", text, name)


if __name__ == "__main__":
    unittest.main()
