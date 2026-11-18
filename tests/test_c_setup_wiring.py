"""0.0.29 C1 — setup/wiring (note `C-commands-setup.md` §3a, §3b C1 half, §5).

  * FullWiresBeforeInstall / FullInitWires — #86: full mode ends fully wired, or names exactly ONE
    command (the install line). The embedder setting and the code-review-graph pin are written
    before the packages exist; the closing block carries no `then:` re-run.
  * TheScopeDuplicate — two identical registrations are a harmless duplicate, two different ones a
    stale shadow; both name `mokata unsetup claude --scope user`.
  * TheRouteAndMarker — `slash_command` names `/<bare>` only for a command file carrying mokata's
    mark; `localize_commands` is that function per token; `plugin_installed`.
  * TheCliRouteWritesLocalizedFiles — the command/skill files setup writes name the commands as the
    CLI route installed them, and a re-run reads as no drift.

Every wait here is an event the test controls (a spy, a probe); no wall clock.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import adoption_modes as A
from mokata import plugin_runtime as PR

SRC = Path(__file__).resolve().parents[1] / "src"

ALL = {"embeddings": lambda: True, "code-graph": lambda: True, "graph-ts": lambda: True,
       "lexical": lambda: True}
NONE = {"embeddings": lambda: False, "code-graph": lambda: False, "graph-ts": lambda: False,
        "lexical": lambda: False}


def _never(*a, **k):
    raise AssertionError(f"must not run here: {a!r} {k!r}")


def _wire(mode="full", **kw):
    calls = {"adopt": [], "wire": [], "lines": [], "index": [], "search": []}
    base = dict(
        out=lambda _s: None, present=ALL, warm_fn=lambda: "model2vec:minishlab/potion",
        crg_command_fn=lambda: "/x/bin/code-review-graph",
        install_line_fn=lambda extra: calls["lines"].append(extra) or f"LINE[{extra}]",
        graph_pinned_fn=lambda _r: False, embedder_wired_fn=lambda _r: False,
        wire_embedder_fn=lambda root, **k: calls["wire"].append(root) or True,
        adopt_fn=lambda root, **k: calls["adopt"].append(k) or mock.Mock(committed=True),
        build_index_fn=lambda root, out: calls["index"].append(root) or _built(),
        build_search_fn=lambda root, out: calls["search"].append(root) or (True, "built", False),
        rg_command_fn=lambda: "/x/bin/rg")
    base.update(kw)
    with tempfile.TemporaryDirectory() as d:
        res = A.wire_mode(d, mode, **base)
    return res, calls


def _built():
    from mokata.knowledge.crg_build import BuildResult
    return BuildResult(ok=True, summary="1 files")


# ------------------------------------------------------------------ §3a — wire before install
class FullWiresBeforeInstall(unittest.TestCase):
    def test_packages_absent_still_write_the_embedder_and_the_pin(self):
        res, c = _wire(present=NONE, warm_fn=_never)
        self.assertEqual(1, len(c["wire"]), "embedder=auto is written before model2vec exists")
        self.assertEqual(1, len(c["adopt"]), "code-review-graph is pinned before it exists")
        self.assertTrue(res.embedder_wired)
        self.assertTrue(res.graph_adopted)
        self.assertEqual([], c["index"] + c["search"],
                         "no index build without the package: first use builds it")

    def test_the_closing_block_is_the_one_install_line_and_nothing_else(self):
        res, c = _wire(present=NONE, warm_fn=_never)
        table = A.render_outcome(res)
        self.assertEqual(1, table.count("LINE[full]"), table)
        self.assertNotIn("then:", table)
        self.assertNotIn("fix:", table)
        self.assertNotIn("mokata setup", table, "no re-run: the install alone completes it")
        for r in res.rows:
            self.assertEqual(A.MISSING, r.status)
            self.assertEqual("", r.fix, "a missing package's fix IS the shared install line")
        quick = A.render_quickstart("full", res)
        self.assertNotIn("PARTIALLY", quick, "the install line fixes every row: not partial")
        self.assertIn("one command", quick)

    def test_memory_mode_wires_the_embedder_before_the_package(self):
        res, c = _wire(mode="memory", present=NONE, warm_fn=_never)
        self.assertEqual(1, len(c["wire"]))
        self.assertNotIn("then:", A.render_outcome(res))

    def test_only_the_graph_missing_still_pins_and_builds_nothing(self):
        res, c = _wire(present={**ALL, "code-graph": lambda: False})
        self.assertEqual(1, len(c["adopt"]))
        self.assertEqual([], c["index"])
        self.assertIsNone(res.row(A.INDEX_ROW_KEY), "no index row: it builds on first use")
        self.assertEqual(A.MISSING, res.row("code-graph").status)

    def test_a_blocked_model_keeps_its_own_cause_and_fix(self):
        from mokata.memory.embed import ModelUnavailable

        def warm():
            raise ModelUnavailable("ConnectionError: Max retries exceeded")
        res, _ = _wire(warm_fn=warm)
        table = A.render_outcome(res)
        self.assertIn("fix:", table, "a non-install row keeps its own fix")
        self.assertIn("PARTIALLY", A.render_quickstart("full", res))

    def test_everything_present_is_today_s_table(self):
        res, c = _wire()
        self.assertTrue(res.complete)
        self.assertEqual("", res.install_line)
        self.assertIn("everything wired", A.render_quickstart("full", res))


class FullInitWires(unittest.TestCase):
    def _init(self, profile):
        from mokata.init import init_repo
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        seen = []
        with mock.patch.object(A, "_warm_model", _never):
            init_repo(root=d, profile=profile, assume_yes=True, out=seen.append)
        data = json.loads((Path(d) / ".mokata" / "manifest.json").read_text(encoding="utf-8"))
        return data, "\n".join(seen)

    def test_full_profile_writes_the_embedder_and_the_pin_and_shows_them(self):
        data, said = self._init("full")
        self.assertEqual("auto", data["settings"]["memory"]["embedder"])
        from mokata.knowledge.graph_adopt import graph_pinned_tool
        self.assertEqual("code-review-graph", graph_pinned_tool(data))
        self.assertIn("memory.embedder = auto", said, "the preview the human approves names it")

    def test_standard_profile_writes_no_embedder(self):
        data, said = self._init("standard")
        self.assertFalse(((data.get("settings") or {}).get("memory") or {}).get("embedder"))
        self.assertNotIn("memory.embedder", said)

    def test_the_session_check_after_wiring_is_silent_or_one_line(self):
        data = {"profile": "full", "settings": {"memory": {"embedder": "auto"}},
                "capabilities": {"code_graph": {"fallback": ["code-review-graph", "ast"]}}}
        ok = A.first_session_check("/x", data=data, presence=ALL, mokata_on_path=True)
        self.assertIsNone(ok.line)
        miss = A.first_session_check("/x", data=data, presence=NONE, mokata_on_path=True,
                                     install_line_fn=lambda e: f"LINE[{e}]")
        self.assertEqual(("LINE[full]",), miss.commands, "packages missing -> the install alone")

    def test_an_unwired_repo_still_names_setup(self):
        data = {"profile": "full", "settings": {},
                "capabilities": {"code_graph": {"fallback": ["ast"]}}}
        chk = A.first_session_check("/x", data=data, presence=ALL, mokata_on_path=True)
        self.assertEqual(("mokata setup claude --mode full",), chk.commands)


# ------------------------------------------------------------------ §3a — scope duplicate
class TheScopeDuplicate(unittest.TestCase):
    def _two(self, user_args):
        from mokata.mcp_admin import scope_shadow
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        root, home = os.path.join(d, "repo"), os.path.join(d, "home")
        os.makedirs(root)
        os.makedirs(home)
        reg = lambda args: {"mcpServers": {"mokata": {"command": "/v/bin/mokata-mcp", "args": args}}}
        Path(root, ".mcp.json").write_text(json.dumps(reg([])), encoding="utf-8")
        Path(home, ".claude.json").write_text(json.dumps(reg(user_args)), encoding="utf-8")
        return "\n".join(scope_shadow(root, home).render())

    def test_identical_registrations_are_a_harmless_duplicate(self):
        text = self._two([])
        self.assertIn("SCOPE DUPLICATE", text)
        self.assertNotIn("✗", text)
        self.assertIn("mokata unsetup claude --scope user", text)
        self.assertNotIn("remove mokata's entry here", text)

    def test_different_registrations_are_a_stale_shadow_naming_the_command(self):
        text = self._two(["--old"])
        self.assertIn("✗ SCOPE SHADOW", text.replace("SHADOW ✗", "✗ SCOPE SHADOW"))
        self.assertIn("mokata unsetup claude --scope user", text)
        self.assertNotIn("remove mokata's entry here", text)


# ------------------------------------------------------------------ §3b — route and marker
def _cmd(base: Path, stem: str, description: str, body: str = "body\n", crlf: bool = False):
    p = base / ".claude" / "commands" / f"{stem}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\ndescription: {description}\n---\n{body}"
    if crlf:
        text = text.replace("\n", "\r\n")
    p.write_bytes(text.encode("utf-8"))
    return p


class TheRouteAndMarker(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.root, self.home = Path(d, "repo"), Path(d, "home")
        self.root.mkdir()
        self.home.mkdir()

    def s(self, name, env=None):
        return PR.slash_command(name, str(self.root), env or {}, str(self.home))

    def test_a_marked_project_file_is_bare(self):
        _cmd(self.root, "spec", "mokata · write the spec")
        self.assertEqual("/spec", self.s("spec"))

    def test_a_marked_crlf_file_is_bare(self):
        _cmd(self.root, "spec", "mokata · write the spec", crlf=True)
        self.assertEqual("/spec", self.s("spec"))

    def test_a_builtin_name_is_prefixed(self):
        _cmd(self.root, "mokata-review", "mokata · review")
        self.assertEqual("/mokata-review", self.s("review"))

    def test_a_marked_home_file_is_bare(self):
        _cmd(self.home, "spec", "mokata · write the spec")
        self.assertEqual("/spec", self.s("spec"))

    def test_a_foreign_file_is_namespaced(self):
        _cmd(self.root, "explain", "explain the code (another tool)")
        self.assertEqual("/mokata:explain", self.s("explain"))

    def test_a_body_imitating_the_mark_is_foreign(self):
        _cmd(self.root, "explain", "another tool", body="description: mokata · fake\n")
        self.assertEqual("/mokata:explain", self.s("explain"))

    def test_the_plugin_route_is_namespaced_even_beside_a_bare_file(self):
        _cmd(self.root, "spec", "mokata · write the spec")
        self.assertEqual("/mokata:spec", self.s("spec", env={"CLAUDE_PLUGIN_ROOT": "/p"}))

    def test_nothing_installed_is_namespaced(self):
        self.assertEqual("/mokata:spec", self.s("spec"))

    def test_one_call_costs_two_stats_and_one_bounded_read(self):
        _cmd(self.root, "spec", "mokata · write the spec", body="x" * 1_000_000)
        reads = []
        real_open = open

        def counting_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if str(path).endswith("spec.md"):
                real_read = f.read

                def read(n=-1):
                    reads.append(n)
                    return real_read(n)
                f.read = read
            return f
        with mock.patch("builtins.open", counting_open):
            self.assertEqual("/spec", self.s("spec"))
        self.assertEqual(1, len(reads))
        self.assertTrue(0 < reads[0] <= 4096, "one bounded read, never the whole file")

    def test_slash_command_imports_nothing_heavy(self):
        code = ("import sys, mokata.plugin_runtime as p; p.slash_command('spec', '.', {}, '.');"
                "print('mokata.harness_setup' in sys.modules)")
        env = dict(os.environ, PYTHONPATH=str(SRC))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=env, check=True).stdout.strip()
        self.assertEqual("False", out)

    def test_harness_setup_re_exports_the_moved_names(self):
        from mokata import harness_setup as H
        self.assertIs(H.CLAUDE_CODE_BUILTIN_COMMANDS, PR.CLAUDE_CODE_BUILTIN_COMMANDS)
        self.assertIs(H.MOKATA_COMMAND_MARK, PR.MOKATA_COMMAND_MARK)
        self.assertIs(H._frontmatter_description, PR._frontmatter_description)

    def test_localize_renders_each_token_through_slash_command(self):
        _cmd(self.root, "spec", "mokata · write the spec")
        text = "run /mokata:spec then /mokata:test; not /mokata:<next> nor /mokata:{name}"
        got = PR.localize_commands(text, str(self.root), {}, str(self.home))
        self.assertEqual("run /spec then /mokata:test; not /mokata:<next> nor /mokata:{name}", got)

    def test_localize_against_an_installed_set_ignores_the_disk(self):
        got = PR.localize_commands("/mokata:spec /mokata:review /mokata:other",
                                   installed={"spec", "review"})
        self.assertEqual("/spec /mokata-review /mokata:other", got)

    def test_plugin_installed(self):
        self.assertTrue(PR.plugin_installed(str(self.home), {"CLAUDE_PLUGIN_ROOT": "/p"}))
        self.assertFalse(PR.plugin_installed(str(self.home), {}))
        plugin = self.home / "plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"mcpServers": {"mokata": {"command": "uvx"}}}), encoding="utf-8")
        from mokata.plugin_cache import record_plugin_root
        record_plugin_root(str(plugin), home=str(self.home))
        self.assertTrue(PR.plugin_installed(str(self.home), {}))


# ------------------------------------------------------------------ §3b — localized writes
class TheCliRouteWritesLocalizedFiles(unittest.TestCase):
    def test_setup_writes_route_commands_and_a_rerun_is_no_drift(self):
        from mokata import harness_setup as H
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        root, home = Path(d, "repo"), Path(d, "home")
        root.mkdir()
        home.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "", "MOKATA_VIA_PLUGIN": ""}):
            H.setup_harness("claude", root=str(root), scope="project", assume_yes=True,
                            home=str(home), with_hooks=False, out=lambda _s: None)
        cmds = root / ".claude" / "commands"
        written = {p.name: p.read_text(encoding="utf-8") for p in cmds.glob("*.md")}
        joined = "\n".join(written.values())
        self.assertNotIn("/mokata:spec", joined, "the CLI route has no /mokata:spec to type")
        self.assertIn("/spec", joined)
        skills = "\n".join(p.read_text(encoding="utf-8")
                           for p in (root / ".claude" / "skills").glob("*/SKILL.md"))
        self.assertNotRegex(skills, r"/mokata:(spec|test|develop|review)\b")
        drift = [r for r in H.vendored_drift(str(root), home=str(home)) if r["scope"] == "project"]
        self.assertEqual("match", drift[0].get("summary"), drift)


if __name__ == "__main__":
    unittest.main()
