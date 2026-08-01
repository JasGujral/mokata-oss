"""SELF-PROTECT (0.0.16 stage 3) — installed / out-of-workspace code is NEVER writable.

THE LIVE BUG (re-groom #17, Jas 2026-07-18): an agent edited mokata's OWN code, in `site-packages`.
Grounded before building: NO path-based block existed at ANY layer. And the bypass was NOT the Bash
side door everyone assumed — it was cwd/run gating:

    hook_cli.gate_guard_main  exits 0 when `find_mokata_root(cwd)` is None  (a non-mokata cwd)
    gate_hook.check_write     allows when no run is registered              (outside a pipeline run)

`site-packages` is never inside a mokata repo, and a self-edit is never inside a run, so both doors
stood open at once. `test_self_protect_regression` pins exactly that: the OLD decision surface still
allows the write (proven live, not asserted from memory), and the hook now exits 2 — from a cwd with
no `.mokata` at all.

What these tests pin, by deliverable:
  1 · the verdict helper       `TestTheVerdict`, `TestWorkspaceRootDefinition`, `TestNoRootIsFailOpenForContainmentOnly`
  2 · the gate-guard lane      `TestTheHookLane` (fires before find_mokata_root; NO `_SOURCE_EXTS`
                               filter, so `.json` targets are covered; exit 2 + legible refusal)
  3 · the Bash lane            `TestTheBashLane` + `PARSER_BLIND_SPOTS` (what the parser cannot see
                               is FILED, never claimed covered)
  4 · the WriteGate lane       `TestTheWriteGateLane` (ahead of the trust dial AND of approval)
  5 · non-overridable          `TestNotOverridable`
  6 · zero-bypass register     `TestZeroBypassRegistration`
  7 · no behaviour change      `TestNoBehaviourChange`
  + secret-safety              `TestRefusalsNameNoContent`

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (import side effect: puts src/ on the path)

from mokata import gate_hook as G
from mokata import selfprotect as SP


# ======================================================================================
# fixtures
# ======================================================================================

def _fake_site_packages(root, pkg="thirdparty", mod="core.py"):
    """A throwaway tree shaped exactly like an installed venv — the REAL path shape rule (a) keys
    on (`.../lib/python3.12/site-packages/<pkg>/<mod>`), built on disk so nothing is mocked."""
    d = os.path.join(root, "venv", "lib", "python3.12", "site-packages", pkg)
    os.makedirs(d, exist_ok=True)
    target = os.path.join(d, mod)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("def installed(): return 1\n")
    return target


def _dev_checkout(root):
    """A mokata DEV CHECKOUT: a repo root whose own `src/mokata` is the package dir. Editing it is
    the work, and the spec says it stays WRITABLE."""
    pkg = os.path.join(root, "src", "mokata")
    os.makedirs(pkg, exist_ok=True)
    for name in ("gate_hook.py", "__init__.py"):
        with open(os.path.join(pkg, name), "w", encoding="utf-8") as fh:
            fh.write("# source\n")
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    return pkg


def _mokata_repo(d):
    """An INITIALIZED mokata repo (so `find_mokata_root` resolves and the run-state gates engage)."""
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return d


def _envelope(cwd, path=None, command=None, tool=None, session_id="cc-session-sp"):
    tool_input = {}
    if path is not None:
        tool_input["file_path"] = path
        tool_input["content"] = "def login(): return True\n"
    if command is not None:
        tool_input["command"] = command
    return json.dumps({
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool or ("Bash" if command is not None else "Write"),
        "tool_input": tool_input,
    })


def _hook(cwd, path=None, command=None, tool=None, env=None, run_cwd=None):
    """Invoke gate-guard the way Claude Code does — a REAL subprocess, the envelope on stdin, the
    exit code and stderr as the only outputs. `run_cwd` is the PROCESS cwd, deliberately allowed to
    differ from the envelope's: a hook's own cwd is whatever launched it."""
    e = dict(os.environ)
    e.pop("MOKATA_SESSION_ID", None)
    e["PYTHONPATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from mokata.hook_cli import gate_guard_main; sys.exit(gate_guard_main([]))"],
        input=_envelope(cwd, path=path, command=command, tool=tool), text=True,
        # `text=True` alone decodes with the LOCALE encoding — cp1252 on a Windows console — so
        # mokata's UTF-8 refusal text came back mojibake ('—' as 'â€"') and a byte-identical
        # comparison failed on content that was in fact correct. mokata writes UTF-8 everywhere
        # (see docs/reference/platform-support.md); the test must read it as UTF-8 too.
        encoding="utf-8",
        capture_output=True, env=e, timeout=60, cwd=run_cwd or cwd)
    return proc.returncode, proc.stderr


# ======================================================================================
# THE regression — deliverable 1+2, and the whole reason the stage exists
# ======================================================================================

class TestTheRegression(unittest.TestCase):

    def test_self_protect_regression(self):
        """A native Write to a REAL site-packages path, from a cwd with NO `.mokata` repo.

        Both halves are proven live:
          OLD behaviour — the pre-stage decision surface (`find_mokata_root` + `check_write`, the
          only two things that decided a native write before this stage) ALLOWS it. That is not an
          assertion about history from memory: both functions are still here, unchanged, and are
          called here to show what they answer.
          NEW behaviour — the hook exits 2 and names the path.

        The cwd carries no `.mokata`, which is the point: it proves the check fires BEFORE
        run-gating. Under the old code this write was not merely un-blocked, it was never EXAMINED —
        `gate_guard_main` returned 0 at `find_mokata_root(cwd) is None`."""
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)

            # --- the OLD decision surface, live: no repo, and no gate verdict against it.
            self.assertIsNone(G.find_mokata_root(d),
                              "fixture invalid: the cwd must NOT be a mokata repo")
            self.assertTrue(G.check_write(d, target).allowed,
                            "the pre-stage run-state gate never blocked an installed-tree write — "
                            "if this ever fails the regression has changed shape")

            # --- the NEW decision: exit 2, and the refusal names the path.
            code, err = _hook(d, path=target)
            self.assertEqual(code, 2, f"a site-packages write must exit 2 — stderr: {err}")
            self.assertIn(SP.GATE_SELF_PROTECT, err)
            self.assertIn(SP.RULE_INSTALLED, err)
            self.assertIn(os.path.realpath(target), err)


# ======================================================================================
# deliverable 1 — the verdict helper
# ======================================================================================

class TestTheVerdict(unittest.TestCase):

    def test_site_packages_write_is_blocked(self):
        """SPEC CASE 1."""
        with tempfile.TemporaryDirectory() as d:
            out = SP.check_target(_fake_site_packages(d), workspace_root=d)
            self.assertTrue(out.blocked)
            self.assertEqual(out.rule, SP.RULE_INSTALLED)

    def test_dist_packages_is_blocked_too(self):
        out = SP.check_target("/usr/lib/python3/dist-packages/apt/cache.py", workspace_root="/srv/x")
        self.assertEqual(out.rule, SP.RULE_INSTALLED)

    def test_any_installed_package_not_just_mokata(self):
        """The spec says ANY installed package: installed code is never an edit target."""
        for pkg in ("requests", "numpy", "mokata"):
            out = SP.check_target(f"/venv/lib/python3.12/site-packages/{pkg}/__init__.py",
                                  workspace_root="/repo")
            self.assertEqual(out.rule, SP.RULE_INSTALLED, pkg)

    def test_out_of_root_write_is_blocked(self):
        """SPEC CASE 2.

        Deliberately NOT under `tempfile` — the temp root is the ONE named allowance on rule (c)
        (see `TestTheScratchAllowance`), so a tmpdir fixture would exempt itself and the test would
        pass for the wrong reason. `realpath` does not require existence, so unreal paths are the
        honest fixture for a location question."""
        out = SP.check_target("/opt/elsewhere/notes.py", workspace_root="/srv/project")
        self.assertTrue(out.blocked)
        self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT)

    def test_a_sibling_project_is_out_of_root(self):
        """STATED CONSEQUENCE, pinned so nobody is surprised by it: with the agent run from inside
        one project, a write into a SIBLING project is refused. That is what "outside the repo /
        workspace root" means. The remedy is to run from the directory that spans them (which then
        becomes the root, since it holds no repo marker), not an override — there is none."""
        out = SP.check_target("/work/other-project/x.py", workspace_root="/work/this-project")
        self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT)

    def test_home_dotfiles_are_out_of_root(self):
        """The high-value target this also closes: the agent rewriting the harness config that
        wires mokata's own hooks (`~/.claude/settings.json`) — i.e. switching its guardrails off."""
        out = SP.check_target("~/.claude/settings.json", workspace_root="/srv/project")
        self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT)

    def test_repo_tree_write_is_allowed(self):
        """SPEC CASE 3 — an ordinary in-repo source write is untouched."""
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "src", "api", "items.py")
            self.assertTrue(SP.check_target(target, workspace_root=d).allowed)

    def test_mokata_own_install_is_blocked(self):
        """Rule (b): a `/plugin install` tree is NOT in site-packages, so rule (a) misses it."""
        with tempfile.TemporaryDirectory() as d:
            plugin_pkg = os.path.join(d, "plugins", "mokata", "src", "mokata")
            os.makedirs(plugin_pkg)
            out = SP.check_target(os.path.join(plugin_pkg, "gate_hook.py"),
                                 workspace_root=os.path.join(d, "work"),
                                 package_root=plugin_pkg)
            self.assertTrue(out.blocked)
            self.assertEqual(out.rule, SP.RULE_MOKATA_INSTALL)

    def test_dev_checkout_src_mokata_write_is_allowed(self):
        """NEGATIVE (spec's explicit carve): when mokata's package dir IS inside the workspace root,
        it is a dev checkout and editing mokata's own source is the work."""
        with tempfile.TemporaryDirectory() as d:
            pkg = _dev_checkout(d)
            out = SP.check_target(os.path.join(pkg, "gate_hook.py"),
                                 workspace_root=d, package_root=pkg)
            self.assertTrue(out.allowed, out.reason)

    def test_a_symlink_cannot_launder_an_escape(self):
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            root = os.path.join(d, "repo")
            os.makedirs(root)
            link = os.path.join(root, "sneaky.py")
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertEqual(SP.check_target(link, workspace_root=root).rule, SP.RULE_INSTALLED)

    def test_expanduser_is_applied_before_the_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            out = SP.check_target("~/anything.py", workspace_root=d)
            self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT)
            self.assertNotIn("~", out.target or "")

    def test_installed_beats_the_temp_allowance(self):
        """Rule ORDER: the named scratch allowance relaxes rule (c) ONLY. A throwaway venv lives in
        temp and is still installed code — which is exactly what the regression fixture is."""
        target = _fake_site_packages(tempfile.mkdtemp())
        self.assertEqual(SP.check_target(target, workspace_root="/nowhere").rule,
                         SP.RULE_INSTALLED)

    def test_the_temp_scratch_allowance_is_real_and_named(self):
        scratch = os.path.join(tempfile.gettempdir(), "mokata-scratch-note.txt")
        out = SP.check_target(scratch, workspace_root="/srv/project")
        self.assertTrue(out.allowed, out.reason)
        self.assertIn("temp", out.reason)

    def test_the_scratch_allowance_reaches_only_the_temp_roots(self):
        """The allowance's exact reach, stated: `/tmp`, `/private/tmp`, `/var/tmp` and TMPDIR — and
        nothing else. `/tmpfoo` is not `/tmp`."""
        self.assertEqual(SP.check_target("/tmpfoo/x.py", workspace_root="/srv/p").rule,
                         SP.RULE_OUT_OF_ROOT)
        self.assertEqual(SP.check_target("/var/log/x.py", workspace_root="/srv/p").rule,
                         SP.RULE_OUT_OF_ROOT)
        self.assertTrue(SP.check_target("/tmp/claude-501/scratch.md",
                                       workspace_root="/srv/p").allowed)

    def test_a_bare_label_is_not_judged_for_containment(self):
        """GROUNDED: `WriteRequest.target` is not always a path — `engine/emit.py` submits
        `"spec:emit"`. Treating a label as a relative path would fabricate a rule-(c) violation for
        every gated write in the codebase (measured: 24 test failures before this was fixed)."""
        self.assertFalse(SP.is_path_like("spec:emit"))
        self.assertTrue(SP.check_target("spec:emit", workspace_root="/repo").allowed)

    def test_version_py_single_sources_the_predicate(self):
        """`version.detect_install_method` was the codebase's ONLY site-packages predicate. The
        stage brief says extract, don't duplicate — so version.py must now CALL the helper."""
        from mokata import version
        src = inspect.getsource(version.detect_install_method)
        self.assertIn("in_installed_tree", src)
        self.assertNotIn('"site-packages"', src, "the predicate was duplicated, not extracted")
        self.assertEqual(
            version.detect_install_method(
                package_file="/venv/lib/python3.12/site-packages/mokata/version.py"),
            "pip", "the extraction changed version.py's answer")

    def test_the_helper_is_total_and_never_raises(self):
        for bad in ("", "\x00nul", "relative", "~", "//", "." * 300):
            for root in (None, "/repo", "\x00"):
                SP.check_target(bad, workspace_root=root)      # must not raise


class TestWorkspaceRootDefinition(unittest.TestCase):
    """THE definition, stated and pinned: the nearest ancestor of the envelope's `cwd` holding
    `.mokata/manifest.json` or `.git`, else `cwd` itself."""

    def test_a_mokata_manifest_marks_the_root(self):
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            sub = os.path.join(d, "src", "deep")
            os.makedirs(sub)
            self.assertEqual(SP.workspace_root_for(sub), os.path.realpath(d))

    def test_a_git_dir_marks_the_root(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            sub = os.path.join(d, "a", "b")
            os.makedirs(sub)
            self.assertEqual(SP.workspace_root_for(sub), os.path.realpath(d))

    def test_a_git_FILE_marks_the_root_so_a_worktree_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".git"), "w", encoding="utf-8") as fh:
                fh.write("gitdir: /elsewhere/.git/worktrees/wt\n")
            self.assertEqual(SP.workspace_root_for(d), os.path.realpath(d))

    def test_the_nearest_marker_wins(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            inner = os.path.join(d, "vendor", "sub")
            os.makedirs(os.path.join(inner, ".git"))
            self.assertEqual(SP.workspace_root_for(inner), os.path.realpath(inner))

    def test_no_marker_anywhere_falls_back_to_cwd_itself(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "plain")
            os.makedirs(sub)
            root = SP.workspace_root_for(sub)
            # `/tmp` has no marker, so the fallback is cwd — the narrowest honest answer.
            self.assertEqual(root, os.path.realpath(sub))
            self.assertTrue(SP.check_target(os.path.join(sub, "a.py"), workspace_root=root).allowed)


class TestNoRootIsFailOpenForContainmentOnly(unittest.TestCase):
    """STATED POLICY: no resolvable root ⇒ rule (c) fails OPEN; rules (a)/(b) still fire.

    Fail-closed on (c) with no root would refuse EVERY write in an unreadable-cwd process — the
    "gate that makes the editor unusable" gate_hook.py:16-19 forbids. Fail-OPEN on (a)/(b) would
    surrender the actual incident, and they need no root, so they never do."""

    def test_containment_is_skipped_with_no_root(self):
        self.assertTrue(SP.check_target("/somewhere/else/x.py", workspace_root=None).allowed)

    def test_the_absolute_rules_still_fire_with_no_root(self):
        self.assertEqual(
            SP.check_target("/venv/lib/python3.12/site-packages/x/y.py", workspace_root=None).rule,
            SP.RULE_INSTALLED)

    def test_an_unresolvable_cwd_yields_no_root(self):
        self.assertIsNone(SP.workspace_root_for("\x00"))


# ======================================================================================
# `84:69` — the refusal on a HOST-HARNESS capability path says what actually happened
# ======================================================================================

def _harness(*parts):
    """A path under the host harness's own per-user state directory."""
    return os.path.join(os.path.expanduser("~"), ".claude", *parts)


class TestTheHarnessCapabilityMessage(unittest.TestCase):
    """`84:69`, the HONESTY half — message only, no security effect.

    Claude Code keeps its own per-user state under `~/.claude`, which is outside every
    workspace root, so rule (c) refuses a write to it. The rule is doing exactly what it
    promises; the problem is that it SAYS 'a path outside it is not this project's code',
    which reads like mokata caught a rogue write. What actually happened is that a host
    capability was turned off by containment, and the user was never told.

    Everything about the verdict is unchanged here — same rule id, same blocked/allowed, same
    non-overridability. Only the prose moves."""

    def _out(self, path):
        return SP.check_target(path, workspace_root="/srv/project")

    # NOTE: the MEMORY leaf is deliberately absent from every row below — it is no longer
    # blocked at all, it is carved out (`TestTheHarnessMemoryCarveOut`). The reword covers the
    # capability locations that REMAIN refused, which is the whole set of them.

    def test_the_message_names_the_capability_not_a_rogue_write(self):
        out = self._out(_harness("projects", "-srv-project", "abc-123.jsonl"))
        self.assertTrue(out.blocked)
        self.assertIn("transcript", out.reason.lower())
        self.assertIn("containment", out.reason.lower())
        self.assertNotIn("not this project's code", out.reason,
                         "the generic containment prose still implies a rogue write")

    def test_every_recognised_capability_location_is_named(self):
        for path, word in (
            (_harness("projects", "-srv-project", "abc.jsonl"), "transcript"),
            (_harness("settings.json"), "setting"),
            (_harness("settings.local.json"), "setting"),
            (_harness("plugins", "cache", "x.js"), "plugin"),
            (_harness("hooks", "x.sh"), "hook"),
            (_harness("history.jsonl"), "state"),
        ):
            with self.subTest(path=path):
                out = self._out(path)
                self.assertTrue(out.blocked, path)
                self.assertIn(word, out.reason.lower(), out.reason)

    def test_the_verdict_itself_is_UNCHANGED(self):
        """The scope pin. Reword only: the rule id, the blocked verdict and the resolved
        target must all be exactly what they were."""
        for path in (_harness("projects", "-srv-project", "abc.jsonl"),
                     _harness("settings.json"),
                     _harness("plugins", "cache", "x.js")):
            with self.subTest(path=path):
                out = self._out(path)
                self.assertTrue(out.blocked)
                self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT)
                self.assertEqual(out.target, SP.resolve_target(path))

    def test_it_still_says_there_is_no_override(self):
        out = self._out(_harness("settings.json"))
        self.assertIn("NOT overridable", SP.render_refusal(out))

    def test_an_ordinary_out_of_root_path_keeps_the_ORIGINAL_message(self):
        """The reword is confined to recognised harness locations. Anything else must keep the
        containment prose — widening the friendly message to every refusal would be the
        opposite of legibility."""
        for path in ("/opt/not-my-project/x.py", os.path.expanduser("~/elsewhere/notes.md"),
                     os.path.expanduser("~/.claudius/settings.json")):
            with self.subTest(path=path):
                out = self._out(path)
                self.assertTrue(out.blocked)
                self.assertIn("OUTSIDE the workspace root", out.reason)
                self.assertNotIn("containment", out.reason.lower())

    def test_the_path_shapes_are_single_sourced_from_harness_paths(self):
        """`selfprotect` must not carry its own copy of `~/.claude/...` — the installer and the
        MCP admin already resolve those, and two spellings of one layout is how they drift.

        Matched on a string LITERAL, not the bare substring, in the same shape (and for the
        same reason) as `test_no_env_var_can_bypass_it`: the module's prose legitimately names
        `.claude` when explaining where the layout is single-sourced FROM."""
        src = inspect.getsource(SP)
        for literal in ('".claude"', "'.claude'", '".claude/', "'.claude/",
                        '"projects"', "'projects'", '"settings.json"', "'settings.json'"):
            self.assertNotIn(literal, src,
                             f"selfprotect spells a harness path itself ({literal}) — "
                             f"single-source it from harness_paths")
        from mokata import harness_paths
        self.assertTrue(hasattr(harness_paths, "harness_path_kind"))
        # And the layout really is only spelled in ONE module.
        owners = []
        for mod in (SP, harness_paths):
            if '".claude"' in inspect.getsource(mod) or "'.claude'" in inspect.getsource(mod):
                owners.append(mod.__name__)
        self.assertEqual(owners, ["mokata.harness_paths"])


class TestTheHarnessMemoryCarveOut(unittest.TestCase):
    """`84:69`, the CARVE-OUT half — the ONE leaf `~/.claude/projects/<slug>/memory/**` becomes
    writable, and nothing else does.

    This widens a SECURITY-class, non-overridable rule, so the pins below are deliberately
    asymmetric: the carve-out gets ONE test, and every sibling gets its OWN named assertion
    rather than a loop — a loop that stops early hides which sibling regressed, and on this
    rule that is the difference between a scoped concession and an open door.

    WHY THIS LEAF AND NOT THE PARENT. Memory is a store of the user's own notes that the host
    harness writes on their behalf; the surrounding directory holds session TRANSCRIPTS, and
    the directories beside it hold `settings.json` (which switches mokata's own enforcement
    off), `hooks/`, and `plugins/` (which holds mokata's OWN launcher — a write there rewrites
    the thing doing the blocking). Carving the parent would hand all of that away to buy the
    leaf.

    THE CONCESSION, NAMED. Memory is read back into a later session's context, so a writable
    memory directory IS a persistence vector: content written now can influence a future
    session. That is conceded knowingly and is the reason the carve-out stops at this one
    leaf — it is the narrowest shape that restores the capability, and every path that could
    change what mokata or the harness EXECUTES stays blocked."""

    ROOT = "/srv/project"

    def _out(self, path):
        return SP.check_target(path, workspace_root=self.ROOT)

    def _assert_blocked(self, path):
        out = self._out(path)
        self.assertTrue(out.blocked, f"NO LONGER BLOCKED — the carve-out leaked: {path}")
        self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT, path)
        return out

    # ── the carve-out itself ───────────────────────────────────────────────────────────
    def test_the_memory_directory_is_writable(self):
        for path in (_harness("projects", "-srv-project", "memory", "note.md"),
                     _harness("projects", "-srv-project", "memory", "MEMORY.md"),
                     _harness("projects", "-srv-project", "memory", "sub", "deep.md"),
                     _harness("projects", "-other-slug", "memory", "note.md")):
            with self.subTest(path=path):
                self.assertTrue(self._out(path).allowed, path)

    # ── every sibling, asserted SEPARATELY and still blocking ──────────────────────────
    def test_settings_json_still_blocks(self):
        """The file that switches mokata's own enforcement off."""
        self._assert_blocked(_harness("settings.json"))

    def test_settings_local_json_still_blocks(self):
        self._assert_blocked(_harness("settings.local.json"))

    def test_the_hooks_directory_still_blocks(self):
        self._assert_blocked(_harness("hooks", "anything.sh"))

    def test_the_plugins_directory_still_blocks(self):
        """`plugins/` holds mokata's OWN launcher — a write here rewrites the blocker."""
        self._assert_blocked(_harness("plugins", "cache", "claude-plugins-official", "x.js"))

    def test_the_session_transcripts_still_block(self):
        self._assert_blocked(_harness("projects", "-srv-project", "abc-123.jsonl"))

    def test_the_slug_directory_ITSELF_still_blocks(self):
        """The carve-out is the `memory` leaf, NOT the project slug that contains it."""
        self._assert_blocked(_harness("projects", "-srv-project", "anything.txt"))
        self._assert_blocked(_harness("projects", "-srv-project"))

    def test_the_projects_directory_ITSELF_still_blocks(self):
        self._assert_blocked(_harness("projects", "loose-file.json"))

    def test_the_claude_root_itself_still_blocks(self):
        self._assert_blocked(_harness("history.jsonl"))
        self._assert_blocked(_harness("todos", "x.json"))

    def test_an_ordinary_out_of_workspace_path_still_blocks(self):
        self._assert_blocked("/opt/not-my-project/x.py")
        self._assert_blocked(os.path.expanduser("~/elsewhere/notes.md"))
        self._assert_blocked(os.path.expanduser("~/.ssh/authorized_keys"))

    def test_a_lookalike_memory_directory_still_blocks(self):
        """The shape is `<claude>/projects/<slug>/memory`, matched on COMPONENTS — a `memory`
        directory anywhere else buys nothing."""
        self._assert_blocked(_harness("memory", "note.md"))
        self._assert_blocked(_harness("projects", "memory", "note.md"))
        self._assert_blocked(_harness("plugins", "-slug", "memory", "note.md"))
        self._assert_blocked(os.path.expanduser("~/.claude-memory/note.md"))
        self._assert_blocked(os.path.expanduser("~/memory/note.md"))

    def test_traversal_out_of_the_carve_out_still_blocks(self):
        """`realpath` runs BEFORE the shape match (`check_target` resolves first), so a path
        that merely passes through the memory directory cannot buy the exemption."""
        self._assert_blocked(_harness("projects", "-srv-project", "memory", "..", "x.jsonl"))
        self._assert_blocked(_harness("projects", "-srv-project", "memory", "..", "..",
                                      "..", "settings.json"))

    # ── the ABSOLUTE rules are untouched by the carve-out ──────────────────────────────
    def test_the_carve_out_never_reaches_rules_a_or_b(self):
        """Rules (a) and (b) run BEFORE containment and take no allowance. A site-packages tree
        that somehow sat inside the memory directory is still refused — the same ordering the
        temp-scratch allowance already relies on."""
        installed = _harness("projects", "-srv-project", "memory",
                             "venv", "lib", "python3.12", "site-packages", "x.py")
        out = self._out(installed)
        self.assertTrue(out.blocked)
        self.assertEqual(out.rule, SP.RULE_INSTALLED)

    # ── shape, not existence; and single-sourced ───────────────────────────────────────
    def test_the_carve_out_is_shape_based_not_existence_based(self):
        """A memory file that does not exist yet is exactly the write being judged."""
        path = _harness("projects", "-no-such-slug-" + "x" * 12, "memory", "new.md")
        self.assertFalse(os.path.exists(path), "fixture invalid: the path must NOT exist")
        self.assertTrue(self._out(path).allowed)

    def test_the_carve_out_fails_SAFE_when_the_layout_cannot_be_loaded(self):
        """The direction a degrade must take on a security rule. `_is_harness_memory` narrows
        its import the way `mokata_package_root` narrows its own — and if that import fails,
        the answer must be 'not carved out' (BLOCK), never 'carved out'. Falling open here
        would not merely lose the carve-out: it would exempt EVERY out-of-workspace path,
        because the carve-out is consulted for all of them."""
        import mokata.harness_paths          # noqa: F401  (ensure the key exists to restore)
        saved = sys.modules["mokata.harness_paths"]
        sys.modules["mokata.harness_paths"] = None      # makes `from … import` raise ImportError
        try:
            self.assertFalse(
                SP._is_harness_memory(_harness("projects", "-srv-project", "memory", "n.md")))
            for path in (_harness("projects", "-srv-project", "memory", "n.md"),
                         _harness("settings.json"),
                         "/opt/not-my-project/x.py"):
                with self.subTest(path=path):
                    self.assertTrue(self._out(path).blocked,
                                    f"a failed layout import fell OPEN on {path}")
        finally:
            sys.modules["mokata.harness_paths"] = saved
        # and the carve-out is restored once the layout loads again
        self.assertTrue(
            self._out(_harness("projects", "-srv-project", "memory", "n.md")).allowed)

    def test_harness_path_kind_resolves_its_own_input(self):
        """`selfprotect` hands it a realpath, so `check_target` is safe either way — but
        `harness_path_kind` is a shared surface and promises to resolve, so it is pinned HERE
        rather than resting on one caller's discipline. Without it, a traversal string reaches
        a `memory` component and reads as the carve-out."""
        from mokata import harness_paths as HP
        traversal = _harness("projects", "-srv-project", "memory", "..", "..", "..",
                             "settings.json")
        self.assertEqual(HP.harness_path_kind(traversal), HP.HARNESS_SETTINGS)
        self.assertEqual(
            HP.harness_path_kind(_harness("projects", "-s", "memory", "..", "a.jsonl")),
            HP.HARNESS_TRANSCRIPTS)

    def test_the_carve_out_keys_on_the_MEMORY_kind_alone(self):
        """Single-sourced, and narrow: of the six kinds `harness_path_kind` distinguishes,
        exactly ONE is writable."""
        from mokata import harness_paths as HP
        writable = []
        for kind, sample in (
            (HP.HARNESS_MEMORY, _harness("projects", "-srv-project", "memory", "n.md")),
            (HP.HARNESS_TRANSCRIPTS, _harness("projects", "-srv-project", "a.jsonl")),
            (HP.HARNESS_SETTINGS, _harness("settings.json")),
            (HP.HARNESS_PLUGINS, _harness("plugins", "x.js")),
            (HP.HARNESS_HOOKS, _harness("hooks", "x.sh")),
            (HP.HARNESS_STATE, _harness("history.jsonl")),
        ):
            self.assertEqual(HP.harness_path_kind(sample), kind, sample)
            if self._out(sample).allowed:
                writable.append(kind)
        self.assertEqual(writable, [HP.HARNESS_MEMORY])


# ======================================================================================
# deliverable 2 — the gate-guard PreToolUse lane
# ======================================================================================

class TestTheHookLane(unittest.TestCase):

    def test_it_fires_before_find_mokata_root(self):
        """The REAL bypass. `gate_guard_main` returns 0 on a non-mokata cwd (hook_cli.py:274-275),
        so a check placed after it can never see a site-packages write."""
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            self.assertIsNone(G.find_mokata_root(d))
            self.assertEqual(_hook(d, path=target)[0], 2)

    def test_it_fires_before_the_run_state_gates(self):
        """Inside a real mokata repo with NO registered run — where `check_write` allows
        (gate_hook.py:552) — the self-protect verdict still blocks."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            target = _fake_site_packages(d)
            self.assertTrue(G.check_write(d, target).allowed)
            code, err = _hook(d, path=target)
            self.assertEqual(code, 2)
            self.assertIn(SP.RULE_INSTALLED, err)

    def test_json_targets_are_covered_no_source_exts_filter(self):
        """`_SOURCE_EXTS` excludes `.json` (gate_hook.py:159-163), so `settings.json` /
        `hooks.json` / `manifest.json` never reached gate-guard. Those are the files that switch
        mokata's own enforcement OFF — the block must not inherit that filter."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            pkg = os.path.join(d, "venv", "lib", "python3.12", "site-packages", "mokata", "hooks")
            os.makedirs(pkg)
            target = os.path.join(pkg, "hooks.json")
            self.assertFalse(G.is_implementation_path(target),
                             "fixture invalid: .json must be OUTSIDE _SOURCE_EXTS")
            self.assertEqual(_hook(d, path=target)[0], 2)

    def test_out_of_root_native_write_is_blocked_through_the_hook(self):
        """The target is outside BOTH the repo and the temp root, so rule (c) really decides. The
        hook only inspects, so an unreal destination is the right fixture — nothing is written."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            code, err = _hook(d, path="/opt/not-my-project/x.py")
            self.assertEqual(code, 2, err)
            self.assertIn(SP.RULE_OUT_OF_ROOT, err)

    def test_a_relative_target_is_resolved_against_the_ENVELOPE_cwd(self):
        """A hook is a separate process; its own cwd is whatever launched it. Resolving a relative
        `file_path` against THAT would judge a path the write never touches — and would refuse a
        legal in-project write (measured: this broke test_si_dev_scope_binding's end-to-end)."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            elsewhere = tempfile.mkdtemp()
            code, err = _hook(d, path=os.path.join("src", "api", "items.py"), run_cwd=elsewhere)
            self.assertEqual(code, 0, err)

    def test_notebook_edit_targets_are_covered(self):
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d, mod="nb.ipynb")
            self.assertEqual(_hook(d, path=target, tool="NotebookEdit")[0], 2)


# ======================================================================================
# deliverable 3 — the Bash lane
# ======================================================================================
# HONEST BOUNDS, FILED. Each entry is a construct the parser CANNOT see, with why. These are
# residual holes, recorded rather than claimed covered — an overclaimed guard is worse than a
# stated partial one. `test_the_filed_blind_spots_are_really_blind` asserts they are still blind,
# so this register cannot silently drift into fiction either.
PARSER_BLIND_SPOTS = {
    "variable expansion":      'T=/venv/lib/python3.12/site-packages/x.py; tee "$T" </dev/null',
    "command substitution":    'tee "$(cat /tmp/where-to-write)/evil.py" </dev/null',
    "eval":                    'eval "tee /venv/lib/python3.12/site-packages/x.py </dev/null"',
    "runtime-built path":      ('python3 -c "import os;'
                                'open(os.sep.join([\'\',\'venv\',\'site-packages\',\'x\']),\'w\')"'),
    "heredoc body":            "cat <<'EOF'\n/venv/lib/python3.12/site-packages/x.py\nEOF",
    "find -exec":              "find . -name '*.py' -exec sed -i s/a/b/ {} +",
    "xargs":                   "echo /venv/lib/python3.12/site-packages/x.py | xargs -I{} touch {}",
    "an unlisted interpreter": "node -e \"require('fs').writeFileSync('/venv/site-packages/x','')\"",
}


class TestTheBashLane(unittest.TestCase):

    def test_bash_sed_i_is_blocked(self):
        """SPEC CASE 4 — the known, documented hole (harness_setup.py:101-104), closed."""
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            code, err = _hook(d, command=f"sed -i 's/installed/hacked/' {target}")
            self.assertEqual(code, 2, err)
            self.assertIn(SP.RULE_INSTALLED, err)

    def test_the_write_verbs_the_parser_does_see(self):
        blocked = "/venv/lib/python3.12/site-packages/pkg/mod.py"
        for cmd in (
            f"sed -i '' s/a/b/ {blocked}",
            f"sed --in-place s/a/b/ {blocked}",
            f"sed -ri s/a/b/ {blocked}",
            f"perl -i -pe s/a/b/ {blocked}",
            f"echo hacked > {blocked}",
            f"echo hacked >> {blocked}",
            f"echo hacked >{blocked}",
            f"echo hacked 2>> {blocked}",
            f"echo hacked | tee {blocked}",
            f"cp /tmp/evil.py {blocked}",
            f"mv /tmp/evil.py {blocked}",
            f"install -m 644 /tmp/evil.py {blocked}",
            f"ln -sf /tmp/evil.py {blocked}",
            f"dd if=/tmp/evil.py of={blocked}",
            f"truncate -s 0 {blocked}",
            f"python3 -c \"open('{blocked}','w').write('x')\"",
            f"python3 -c \"from pathlib import Path; Path('{blocked}').write_text('x')\"",
            f"python3 -c \"import shutil; shutil.copy('/tmp/e.py','{blocked}')\"",
            f"true && sed -i s/a/b/ {blocked}",
            f"cd /tmp; tee {blocked} < /dev/null",
            f"sudo tee {blocked} < /dev/null",
            f"sh -c 'sed -i s/a/b/ {blocked}'",
        ):
            with self.subTest(cmd=cmd):
                out = SP.check_command(cmd, workspace_root="/repo")
                self.assertTrue(out.blocked, f"parser missed a write destination: {cmd}")
                self.assertEqual(out.rule, SP.RULE_INSTALLED)

    def test_reading_from_a_blocked_tree_is_never_refused(self):
        """A read is not a write. `cp <installed> .` and `grep` must stay allowed, or the parser is
        a false-positive machine and gets uninstalled."""
        installed = "/venv/lib/python3.12/site-packages/pkg/mod.py"
        for cmd in (f"cat {installed}",
                    f"grep -rn secret {installed}",
                    f"cp {installed} /repo/copy.py",
                    f"diff {installed} /repo/mine.py",
                    f"python3 -c \"print(open('{installed}').read())\"",
                    "pip list | grep site-packages"):
            with self.subTest(cmd=cmd):
                self.assertTrue(SP.check_command(cmd, workspace_root="/repo").allowed, cmd)

    def test_ordinary_in_repo_shell_writes_are_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            for cmd in (f"echo hi > {d}/notes.md",
                        f"sed -i s/a/b/ {d}/src/app.py",
                        "make build",
                        "git commit -m 'x'",
                        "pytest -q"):
                with self.subTest(cmd=cmd):
                    self.assertTrue(SP.check_command(cmd, workspace_root=d).allowed, cmd)

    def test_an_unparseable_command_fails_open(self):
        self.assertEqual(SP.bash_write_targets('sed -i "unbalanced'), [])
        self.assertTrue(SP.check_command('sed -i "unbalanced', workspace_root="/repo").allowed)

    def test_the_filed_blind_spots_are_really_blind(self):
        """The register is honest in BOTH directions: every filed hole is still a hole (so the file
        is not fiction), and any that closes must be REMOVED from the register deliberately."""
        for name, cmd in PARSER_BLIND_SPOTS.items():
            with self.subTest(blind_spot=name):
                # `base_dir` matters here: it is what the HOOK passes (the envelope's cwd), so an
                # unexpanded `$VAR`/`$(…)` token resolves INSIDE the workspace and is allowed — the
                # hole is real under real conditions, which is the only way to file it honestly.
                self.assertTrue(
                    SP.check_command(cmd, workspace_root="/srv/project",
                                     base_dir="/srv/project").allowed,
                    f"'{name}' is now COVERED — delete it from PARSER_BLIND_SPOTS (good news, but "
                    f"the register must not claim a hole that no longer exists)")

    def test_a_literal_blocked_path_inside_a_substitution_is_still_caught(self):
        """Measured while filing the blind spots, and worth pinning: the parser does not UNDERSTAND
        `$(…)`, but if the blocked path appears LITERALLY in the token, rule (a) still matches on the
        path component. Only a path the command COMPUTES (the filed cases) escapes."""
        out = SP.check_command(
            'tee "$(printf /venv/lib/python3.12/site-packages/x.py)" < /dev/null',
            workspace_root="/srv/project", base_dir="/srv/project")
        self.assertEqual(out.rule, SP.RULE_INSTALLED)

    def test_the_character_devices_are_not_write_targets(self):
        """LIVE FRICTION (`84:67`, 10 sightings across 5 sessions): `2>/dev/null` was read as an
        out-of-workspace WRITE and aborted the whole compound command — on read-only `grep`/`find`
        reads and on the `initdb` that stands up the live-DB verification.

        A redirection to a character device does not create or modify a file, so it is not a write
        target at all. `2>&1` is a file-descriptor DUP, not a path: `>&` is an output redirect
        operator, so the bare fd number after it was being offered as a destination."""
        for cmd in ("ls X 2>/dev/null",
                    "grep -rn foo . 2>/dev/null",
                    "find . -name '*.py' 2>/dev/null",
                    "git stash push -m x >/dev/null 2>&1",
                    "initdb -D /tmp/pgdata >/dev/null",
                    "echo hi > /dev/stdout",
                    "echo hi 2> /dev/stderr",
                    "echo hi > /dev/tty",
                    "echo hi >/dev/fd/3",
                    "echo hi > /dev/null"):
            with self.subTest(cmd=cmd):
                self.assertEqual(SP.bash_write_targets(cmd), [],
                                 f"a character device is not a write target: {cmd}")
                self.assertTrue(
                    SP.check_command(cmd, workspace_root="/srv/project",
                                     base_dir="/srv/project").allowed, cmd)

    def test_the_device_exemption_narrows_nothing_else(self):
        """The other half of the pin. The exemption is a CLOSED set of character devices — every
        real write destination this gate exists to refuse still blocks, including the `/dev`-
        adjacent shapes an over-broad `/dev/*` exemption would have surrendered."""
        for cmd, rule in (
            ("tee /etc/passwd", SP.RULE_OUT_OF_ROOT),
            ("sed -i s/a/b/ /etc/hosts", SP.RULE_OUT_OF_ROOT),
            ("echo x > ~/elsewhere/notes.md", SP.RULE_OUT_OF_ROOT),
            ("echo x > ~/.claude/settings.json", SP.RULE_OUT_OF_ROOT),
            ("cp /tmp/evil.py /venv/lib/python3.12/site-packages/pkg/mod.py", SP.RULE_INSTALLED),
            # NOT a character device: a real file that merely lives under /dev.
            ("echo x > /dev/shm/payload", SP.RULE_OUT_OF_ROOT),
            ("tee /devious/plan.txt", SP.RULE_OUT_OF_ROOT),
            ("tee /dev/null/../../etc/passwd", SP.RULE_OUT_OF_ROOT),
            # `/dev/fd` is the one exempt member with a variable tail, so its shape is ANCHORED to
            # a descriptor NUMBER. A path merely beginning `/dev/fd/` is not a descriptor.
            ("echo x > /dev/fd/3/../../../etc/passwd", SP.RULE_OUT_OF_ROOT),
            ("echo x > /dev/fdisk/notes", SP.RULE_OUT_OF_ROOT),
        ):
            with self.subTest(cmd=cmd):
                out = SP.check_command(cmd, workspace_root="/srv/project",
                                       base_dir="/srv/project")
                self.assertTrue(out.blocked, f"the device exemption swallowed a real write: {cmd}")
                self.assertEqual(out.rule, rule, cmd)

    def test_a_device_redirect_does_not_hide_a_real_target_beside_it(self):
        """The exemption drops the DEVICE operand only — it never short-circuits the segment, so a
        genuine destination in the same command is still judged."""
        blocked = "/venv/lib/python3.12/site-packages/pkg/mod.py"
        for cmd in (f"sed -i s/a/b/ {blocked} 2>/dev/null",
                    f"tee {blocked} </dev/null >/dev/null",
                    f"cp /tmp/evil.py {blocked} >/dev/null 2>&1"):
            with self.subTest(cmd=cmd):
                out = SP.check_command(cmd, workspace_root="/srv/project",
                                       base_dir="/srv/project")
                self.assertTrue(out.blocked, cmd)
                self.assertEqual(out.rule, SP.RULE_INSTALLED, cmd)

    def test_the_run_state_gates_do_not_read_the_command(self):
        """A Bash call reaches the run-state gates with NO path and exits before them — the gates
        are unchanged, and a heuristic target must never drive a methodology block."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            code, err = _hook(d, command=f"sed -i s/a/b/ {d}/src/app.py")
            self.assertEqual(code, 0, err)
            self.assertEqual(err.strip(), "")


# ======================================================================================
# deliverable 4 — the WriteGate lane
# ======================================================================================

class TestTheWriteGateLane(unittest.TestCase):

    def _gate(self):
        from mokata.govern import WriteGate
        return WriteGate()

    def _req(self, target):
        from mokata.govern import WriteRequest
        return WriteRequest(kind="code", target=target, content="x = 1\n")

    def test_writegate_refuses_an_installed_target(self):
        with tempfile.TemporaryDirectory() as d:
            out = self._gate().submit(self._req(_fake_site_packages(d)),
                                      commit=lambda: self.fail("committed a blocked write"),
                                      assume_yes=True)
            self.assertFalse(out.committed)
            self.assertTrue(out.aborted)
            self.assertIn(SP.RULE_INSTALLED, out.reason)

    def test_approval_cannot_whitelist_a_blocked_tree(self):
        """Mirrors the secret hard block (gate.py:126-132): approval is not authority over WHERE."""
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            for kwargs in ({"assume_yes": True},
                           {"human_approved": True},
                           {"confirm": lambda _t: True}):
                with self.subTest(**kwargs):
                    out = self._gate().submit(
                        self._req(target), commit=lambda: self.fail("committed"), **kwargs)
                    self.assertFalse(out.committed)

    def test_it_fires_ahead_of_the_trust_dial(self):
        """Ordering, asserted on OUTCOME not on source: a read-only trust dial would answer
        'read-only trust'. The self-protect reason is what comes back, so it decided first."""
        from mokata.govern import WriteGate, WriteRequest
        from mokata.govern.trust import TrustPolicy
        with tempfile.TemporaryDirectory() as d:
            gate = WriteGate(trust=TrustPolicy(default="read_only"))
            req = WriteRequest(kind="code", target=_fake_site_packages(d), content="x",
                               tool="whatever", surface="mcp")
            out = gate.submit(req, commit=lambda: self.fail("committed"), assume_yes=True)
            self.assertFalse(out.committed)
            self.assertIn(SP.GATE_SELF_PROTECT, out.reason)
            self.assertNotIn("read-only trust", out.reason)

    def test_the_block_is_ledgered(self):
        from mokata.govern.ledger import AuditLedger
        from mokata.govern import WriteGate
        with tempfile.TemporaryDirectory() as d:
            ledger = AuditLedger.from_mokata_dir(os.path.join(_mokata_repo(d), ".mokata"))
            WriteGate(ledger=ledger).submit(self._req(_fake_site_packages(d)), assume_yes=True)
            entries = [e for e in ledger.entries() if e.get("kind") == "write_gate"]
            self.assertTrue(any(e.get("decision") == "blocked"
                                and "self-protect" in str(e.get("reason")) for e in entries),
                            entries)

    def test_containment_applies_when_a_root_is_supplied(self):
        out = self._gate().submit(self._req("/opt/outside/x.py"),
                                  commit=lambda: self.fail("committed"),
                                  assume_yes=True, workspace_root="/srv/project")
        self.assertIn(SP.RULE_OUT_OF_ROOT, out.reason)

    def test_containment_is_off_when_no_root_is_supplied(self):
        """Every pre-0.0.16 caller passes no root, so rule (c) does not apply to them — which is why
        a human-typed `mokata … --out ~/elsewhere.json` still works. Rules (a)/(b) still do."""
        box = []
        out = self._gate().submit(self._req("/opt/outside/x.py"),
                                  commit=lambda: box.append(1), assume_yes=True)
        self.assertTrue(out.committed, out.reason)
        self.assertEqual(box, [1])

    def test_the_mcp_write_lane_supplies_the_root(self):
        """The agent-facing lane knows its root as a FACT (every MCP tool takes a `path`), so
        rule (c) is live there. Asserted on the source of the one wiring site."""
        from mokata.mcp import consent
        src = inspect.getsource(consent._gated_write)
        self.assertIn("workspace_root=", src)


# ======================================================================================
# deliverable 5 — non-overridable
# ======================================================================================

class TestNotOverridable(unittest.TestCase):
    """SPEC CASE 5. Security class, like secret-guard (hook_cli.py:21-24): no P14 override, no env
    kill switch. Enforced by CONSTRUCTION — the gate is not in the only set `read_override` honours
    — and pinned here so it cannot be added by accident."""

    def test_the_gate_is_not_in_the_override_plumbing(self):
        self.assertNotIn(SP.GATE_SELF_PROTECT, G.GATES)

    def test_the_cli_refuses_to_mint_an_override_for_it(self):
        from mokata.cli_commands import gate as gate_cmd
        self.assertNotIn(SP.GATE_SELF_PROTECT, G.GATES)
        self.assertIn("GATES", inspect.getsource(gate_cmd))    # the CLI validates against GATES

    def test_a_hand_written_override_file_does_not_unblock(self):
        """Even forging the on-disk artifact `mokata gate override` writes changes nothing:
        `read_override` filters to `GATES` (gate_hook.py:436), and this gate decides before any
        override is even read."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            from mokata.state import StateStore
            store = StateStore(G.state_dir(d))
            run_id = "run-forged"
            store.write(G.override_key(run_id),
                        {"scopes": [SP.GATE_SELF_PROTECT, "spec-persisted"]})
            self.assertNotIn(SP.GATE_SELF_PROTECT, G.read_override(d, run_id))
            target = _fake_site_packages(d)
            self.assertEqual(_hook(d, path=target,
                                   env={"MOKATA_SESSION_ID": run_id})[0], 2)

    def test_no_env_var_can_bypass_it(self):
        """A grep-guard, in the shape test_si_1 already uses for the run-state gates: the module
        reads NO environment variable, so there is no name to try. Matched on the CALL, not the
        substring — the module's prose legitimately says "environment switch"."""
        src = inspect.getsource(SP)
        for call in ("os.environ", "os.getenv", "environb", "getenv("):
            self.assertNotIn(call, src, f"selfprotect must not read the environment: {call}")

    def test_named_env_switches_are_inert(self):
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            for name in ("MOKATA_SELF_PROTECT", "MOKATA_DISABLE_SELF_PROTECT",
                         "MOKATA_NO_SELF_PROTECT", "MOKATA_SKIP_GATES", "MOKATA_ALLOW_INSTALLED"):
                with self.subTest(env=name):
                    self.assertEqual(_hook(d, path=target, env={name: "1"})[0], 2)


# ======================================================================================
# deliverable 6 — the zero-bypass register
# ======================================================================================

class TestZeroBypassRegistration(unittest.TestCase):
    """`TestZeroBypass` (test_si_6_writegate_side_doors.py) fails CI on any durable-write call site
    in `src/` that nobody classified. This stage adds a CHECKER, not a writer — so the honest
    registration is the absence of one, ASSERTED rather than assumed."""

    def test_self_protect_adds_no_durable_write_site(self):
        import test_si_6_writegate_side_doors as SI6
        sites = SI6._durable_write_sites()
        mine = sorted(k for k in sites if k[0] in ("selfprotect.py",))
        self.assertEqual(mine, [], f"selfprotect.py now WRITES — register it: {mine}")

    def test_the_sweep_still_has_nothing_unregistered(self):
        import test_si_6_writegate_side_doors as SI6
        registered = (set(SI6.GATED) | set(SI6.UNGATED_BY_DESIGN) | set(SI6.LEDGERED)
                      | set(SI6.KNOWN_BYPASS))
        self.assertEqual(sorted(set(SI6._durable_write_sites()) - registered), [])

    def test_the_known_bypass_register_is_still_empty(self):
        import test_si_6_writegate_side_doors as SI6
        self.assertEqual(SI6.KNOWN_BYPASS, {},
                         "this stage must not need a filed bypass")


# ======================================================================================
# deliverable 7 — no behaviour change
# ======================================================================================

class TestNoBehaviourChange(unittest.TestCase):

    def test_a_json_write_inside_the_repo_is_allowed(self):
        """NEGATIVE: dropping `_SOURCE_EXTS` widens WHAT is judged, not WHERE. An in-repo
        `manifest.json` edit stays allowed."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            target = os.path.join(d, ".mokata", "manifest.json")
            code, err = _hook(d, path=target)
            self.assertEqual(code, 0, err)

    def test_in_repo_verdicts_are_byte_identical_in_a_run_gated_repo(self):
        """A repo with a REGISTERED run and no approved approach: the phase gate must answer exactly
        as it did before — same exit code, same gate name, same text — for an in-repo write. The
        self-protect lane is additive, not a re-ordering of the existing verdicts."""
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            from mokata.state import StateStore
            run_id = "run-gated"
            StateStore(G.state_dir(d)).write(G.CHECKPOINT_PREFIX + run_id, {"phase": "brainstorm"})
            impl = os.path.join(d, "src", "app.py")

            decision = G.check_write(d, impl, run_id=run_id)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.gate, G.GATE_PHASE)

            code, err = _hook(d, path=impl, env={"MOKATA_SESSION_ID": run_id})
            self.assertEqual(code, 2)
            self.assertIn(G.GATE_PHASE, err)
            self.assertNotIn(SP.GATE_SELF_PROTECT, err,
                             "an in-repo write must be decided by the RUN-STATE gate, not by "
                             "self-protect — the ordering must not swallow the existing verdict")
            self.assertEqual(err.strip(), f"BLOCKED [{decision.gate}] {decision.reason}")

    def test_a_test_path_write_is_still_always_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            _mokata_repo(d)
            self.assertEqual(_hook(d, path=os.path.join(d, "tests", "test_x.py"))[0], 0)

    def test_writegate_approval_flow_is_unchanged_for_an_allowed_path(self):
        from mokata.govern import WriteGate, WriteRequest
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "notes.txt")
            box = []
            out = WriteGate().submit(
                WriteRequest(kind="code", target=target, content="hello\n"),
                commit=lambda: box.append(1), assume_yes=True)
            self.assertTrue(out.committed, out.reason)
            self.assertEqual(out.reason, "committed")
            self.assertEqual(box, [1])

            declined = WriteGate().submit(
                WriteRequest(kind="code", target=target, content="hello\n"),
                commit=lambda: self.fail("committed a declined write"),
                confirm=lambda _t: False)
            self.assertFalse(declined.committed)
            self.assertEqual(declined.reason, "declined at the human gate")

    def test_secret_guard_behaviour_is_untouched(self):
        """The other sync security hook shares the event and must be unaffected."""
        from mokata.hook_cli import secret_guard_main
        self.assertEqual(secret_guard_main(["--text", "just some prose", "--path", "a.md"]), 0)

    def test_the_gate_matcher_widened_to_bash_on_both_wiring_sites(self):
        """hooks.json (plugin route) and harness_setup (pip/setup route) must agree — the D-CMDNS
        two-route rule. `test_hook_resolve` pins hooks.json AGAINST this constant."""
        from mokata import harness_setup
        self.assertIn("Bash", harness_setup.HOOK_GATE_MATCHER)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            self.assertIn(tool, harness_setup.HOOK_GATE_MATCHER)
        from mokata import package_data_root
        hooks = json.loads(
            (package_data_root() / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        gate = [block for block in hooks["hooks"]["PreToolUse"]
                if any("gate-guard" in h["command"] for h in block["hooks"])][0]
        self.assertEqual(gate["matcher"], harness_setup.HOOK_GATE_MATCHER)


# ======================================================================================
# secret-safety
# ======================================================================================

class TestRefusalsNameNoContent(unittest.TestCase):

    def test_the_refusal_names_the_path_and_never_the_content(self):
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            secret = "sk-live-DEADBEEFdeadbeef0123456789abcdef"
            envelope = json.dumps({
                "session_id": "cc", "cwd": d, "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": target, "content": f"TOKEN = '{secret}'\n"},
            })
            e = dict(os.environ)
            e["PYTHONPATH"] = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
            proc = subprocess.run(
                [sys.executable, "-c", "import sys; from mokata.hook_cli import gate_guard_main; "
                                       "sys.exit(gate_guard_main([]))"],
                input=envelope, text=True, encoding="utf-8",   # not the cp1252 console locale
                capture_output=True, env=e, timeout=60, cwd=d)
            self.assertEqual(proc.returncode, 2)
            self.assertIn(os.path.realpath(target), proc.stderr)
            self.assertNotIn(secret, proc.stderr)
            self.assertNotIn(secret, proc.stdout)

    def test_the_outcome_carries_no_content_field(self):
        fields = set(SP.ProtectOutcome.__dataclass_fields__)
        self.assertEqual(fields, {"allowed", "reason", "rule", "target"})

    def test_the_bash_refusal_names_only_the_destination(self):
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            code, err = _hook(d, command=f"echo 'AWS_SECRET=abc123XYZ' > {target}")
            self.assertEqual(code, 2)
            self.assertNotIn("AWS_SECRET", err)


# ======================================================================================
# WINDOWS — a separator is a separator. ONE defect that broke containment BOTH ways.
# ======================================================================================

class TestWindowsSeparatorsSurviveTokenization(unittest.TestCase):
    r"""`\` is Windows' PATH SEPARATOR but posix-mode `shlex`'s ESCAPE character.

    Eating it broke rule (a) and rule (c) in OPPOSITE directions on Windows (see
    `selfprotect._tokenize`). These pin the tokenizer on BOTH platforms, so the POSIX escape
    semantics can never be quietly traded away to buy the Windows fix."""

    WIN_TARGET = r"C:\repo\venv\lib\python3.12\site-packages\pkg\mod.py"

    def test_a_windows_path_keeps_its_separators_on_windows(self):
        tokens = SP._tokenize(f"sed -i s/a/b/ {self.WIN_TARGET}")
        if os.name == "nt":
            self.assertIn(self.WIN_TARGET, tokens)
            # …and with the separators intact there IS a `site-packages` component for rule (a).
            self.assertIn("site-packages", SP._components(self.WIN_TARGET))
        else:
            # POSIX: `\` is a REAL shell escape and stays one — byte-identical, deliberately.
            self.assertIn("C:repovenvlibpython3.12site-packagespkgmod.py", tokens)

    def test_posix_escape_semantics_are_untouched(self):
        if os.name == "nt":
            self.skipTest(r"`\ ` is not an escape on Windows — the platform's own rule")
        # An escaped space is ONE file. The Windows fix must never turn this into two tokens.
        self.assertEqual(SP._tokenize(r"tee /tmp/a\ b.py"), ["tee", "/tmp/a b.py"])

    def test_quotes_and_operators_tokenize_identically_on_both_platforms(self):
        self.assertEqual(SP._tokenize("echo a && echo b; echo c 2>&1"),
                         ["echo", "a", "&&", "echo", "b", ";", "echo", "c", "2", ">&", "1"])
        self.assertEqual(SP._tokenize("sed -i 's/x/y/' out.py"),
                         ["sed", "-i", "s/x/y/", "out.py"])


class TestContainmentHoldsOnTheRunningPlatform(unittest.TestCase):
    r"""BOTH DIRECTIONS, on whatever path shape the running OS actually produces.

    On the Windows CI leg every path below is a real `C:\…` path with backslashes — precisely
    the shape that used to defeat the parser — so these run as Windows pins there and as POSIX
    pins on Linux/macOS."""

    def test_an_ordinary_in_repo_write_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            for cmd in (f"echo hi > {os.path.join(d, 'notes.md')}",
                        f"sed -i s/a/b/ {os.path.join(d, 'src', 'app.py')}"):
                with self.subTest(cmd=cmd):
                    self.assertTrue(SP.check_command(cmd, workspace_root=d).allowed, cmd)

    def test_an_installed_tree_write_is_still_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            target = _fake_site_packages(d)
            out = SP.check_command(f"sed -i s/installed/hacked/ {target}", workspace_root=d)
            self.assertFalse(out.allowed, target)
            self.assertEqual(out.rule, SP.RULE_INSTALLED)

    def test_a_real_out_of_root_write_is_still_blocked(self):
        # Deliberately NOT under the system temp root: that carries a documented scratch
        # allowance, so a tempdir target would prove nothing about containment. An absolute
        # path on the current drive is out-of-root on every platform.
        outside = os.path.abspath(os.path.join(os.sep, "mokata-out-of-root-pin", "x.py"))
        with tempfile.TemporaryDirectory() as d:
            out = SP.check_command(f"echo hi > {outside}", workspace_root=d)
            self.assertFalse(out.allowed, outside)
            self.assertEqual(out.rule, SP.RULE_OUT_OF_ROOT)


class TestTheNulByteIsRejectedOnEveryPlatform(unittest.TestCase):
    """A cwd that cannot be resolved must yield NO root on BOTH platforms.

    `posixpath.realpath` raises on an embedded NUL; `ntpath.realpath` SWALLOWS it and falls back.
    Leaning on that difference let rule (c) judge containment against a fabricated root on
    Windows — a fail-OPEN that POSIX never had."""

    def test_resolve_target_refuses_a_nul_byte(self):
        self.assertIsNone(SP.resolve_target("\x00"))
        self.assertIsNone(SP.resolve_target("some/path\x00.py"))

    def test_workspace_root_for_yields_no_root_for_a_nul_byte(self):
        self.assertIsNone(SP.workspace_root_for("\x00"))

    def test_a_nul_target_is_declined_rather_than_mis_judged(self):
        outcome = SP.check_target("\x00", workspace_root=os.getcwd())
        self.assertTrue(outcome.allowed)     # declines to guess (the OS refuses it anyway)…
        self.assertIsNone(outcome.target)    # …and never reports a resolved path it invented


if __name__ == "__main__":
    unittest.main()
