"""0.0.26 L1 — FULL-INSTALLS-EVERYTHING-IN-ONE-GO (absorbs WIN-FULL-SEMANTIC-OFF-AFTER-WITH-DEPS).

Jas, 2026-10-21: "make installation simpler — at least for full, install everything at one go."
On his Windows machine 0.0.25's `setup claude --mode full --yes --with-deps` deferred a rebuild of
its own (locked) env, printed a POSIX-quoted line cmd could not run, and ended "PARTIALLY wired"
with semantic memory off. The business rules now:

  * ONE extra, `mokata[full]` = embeddings + graph-ts + code-review-graph + ide — the INSTALLER
    fetches everything; its lines cannot drift from the extras they bundle;
  * `mokata setup claude --mode full` DETECTS and WIRES (embedder, CRG pin, model warm-up) and
    NEVER installs or spawns an installer; `--with-deps` no longer exists;
  * anything missing is named with its CAUSE, and ONE install line for the installer that owns
    the env (uv tool / pipx / pip), quoted so it runs as printed in cmd, PowerShell and POSIX;
  * a blocked model download names WHY (offline / proxy / TLS / timeout) — "semantic off" always
    says why, in setup and in doctor.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import adoption_modes as A
from mokata import extras_install
from mokata import install_route as R
from mokata.cli_commands import setup as S

ROOT = Path(__file__).resolve().parents[1]

ALL = {"embeddings": lambda: True, "code-graph": lambda: True, "graph-ts": lambda: True,
       "lexical": lambda: True}
NONE = {"embeddings": lambda: False, "code-graph": lambda: False, "graph-ts": lambda: False,
        "lexical": lambda: False}


def _boom(*a, **k):
    raise AssertionError(f"setup tried to install or spawn: {a!r} {k!r}")


def _built():
    from mokata.knowledge.crg_build import BuildResult
    return BuildResult(ok=True, summary="1 files")


def _wire(mode="full", **kw):
    calls = {"adopt": [], "wire": [], "lines": []}
    base = dict(
        out=lambda _s: None, present=ALL, warm_fn=lambda: "model2vec:minishlab/potion",
        crg_command_fn=lambda: "/x/bin/code-review-graph",
        install_line_fn=lambda extra: calls["lines"].append(extra) or f"LINE[{extra}]",
        graph_pinned_fn=lambda _r: False, embedder_wired_fn=lambda _r: False,
        wire_embedder_fn=lambda root, **k: calls["wire"].append(root) or True,
        adopt_fn=lambda root, **k: calls["adopt"].append(k) or mock.Mock(committed=True),
        # 0.0.26 O13: full mode also builds CRG's index — graded in test_o13; here, already built.
        build_index_fn=lambda root, out: _built(),
        build_search_fn=lambda root, out: (True, "built", False),
        rg_command_fn=lambda: "/x/bin/rg")
    base.update(kw)
    with tempfile.TemporaryDirectory() as d:
        res = A.wire_mode(d, mode, **base)
    return res, calls


# ------------------------------------------------------------------ the [full] extra
def _extras():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    import tomllib
    return tomllib.loads(text)["project"]["optional-dependencies"]


class TestTheFullExtra(unittest.TestCase):
    def test_full_extra_is_the_union(self):
        """`mokata[full]` = embeddings + graph-ts + code-review-graph + ide, spelled out (never a
        self-reference, which pip/pipx would re-resolve mokata for)."""
        ex = _extras()
        want = set(ex["embeddings"]) | set(ex["graph-ts"]) | set(ex["ide"]) | {"code-review-graph"}
        self.assertEqual(want, set(ex["full"]))
        self.assertFalse(any(r.lower().startswith("mokata") for r in ex["full"]))

    def test_full_mode_installs_the_full_extra(self):
        self.assertEqual("full", A.mode_spec("full").extra)
        self.assertEqual("embeddings", A.mode_spec("memory").extra)
        self.assertIn("graph-ts", A.mode_dependencies("full"), "FULL-MODE-OMITS-GRAPH-TS")


# ------------------------------------------------------------------ the ONE line, per installer
class _Prefix:
    def __init__(self, name=None, body=""):
        self.t = tempfile.TemporaryDirectory()
        if name:
            Path(self.t.name, name).write_text(body, encoding="utf-8")

    def __enter__(self):
        return self.t.name

    def __exit__(self, *a):
        self.t.cleanup()


REQUIRES = ['mcp<2,>=1.2', 'model2vec>=0.3; extra == "full"', 'code-review-graph; extra == "full"',
            'model2vec>=0.3; extra == "embeddings"']


class TestInstallerSpecificLine(unittest.TestCase):
    def test_uv_tool_restates_the_receipt_and_adds_the_extra(self):
        receipt = ('[tool]\nrequirements = [{ name = "mokata", specifier = "==0.0.26" }, '
                   '{ name = "rich" }]\npython = "3.12"\n')
        with _Prefix("uv-receipt.toml", receipt) as p:
            posix = R.plan_extra_install("full", prefix=p, platform="linux")
            win = R.plan_extra_install("full", prefix=p, platform="win32")
        self.assertIsNone(posix.argv, "display only — setup never runs an installer")
        self.assertEqual("uv tool install 'mokata[full]==0.0.26' --python 3.12 --with rich",
                         posix.display)
        self.assertEqual('uv tool install "mokata[full]==0.0.26" --python 3.12 --with rich',
                         win.display)

    def test_uv_tool_with_an_unrestatable_receipt_gets_the_bare_line(self):
        with _Prefix("uv-receipt.toml", "[tool]\nconstraints = ['x']\n") as p:
            plan = R.plan_extra_install("full", prefix=p, platform="win32")
        self.assertEqual('uv tool install "mokata[full]"', plan.display)

    def test_pipx_reinstalls_with_the_extra_so_its_upgrade_keeps_it(self):
        meta = json.dumps({"main_package": {"package_or_url": "mokata"}})
        with _Prefix("pipx_metadata.json", meta) as p:
            plan = R.plan_extra_install("full", prefix=p, platform="win32")
        self.assertEqual((R.PIPX, 'pipx install --force "mokata[full]"'),
                         (plan.installer, plan.display))

    def test_pip_installs_the_requirements_never_mokata_itself(self):
        with _Prefix() as p:
            plan = R.plan_extra_install("full", prefix=p, has_pip=True, executable="/v/bin/python",
                                        platform="linux", requires=REQUIRES)
        self.assertEqual("/v/bin/python -m pip install 'model2vec>=0.3' code-review-graph",
                         plan.display)
        self.assertNotIn("mokata[", plan.display)

    def test_windows_lines_never_carry_single_quotes(self):
        """WIN-FIX-LINE-POSIX-QUOTED: cmd passes '…' through literally; the line failed as printed."""
        with _Prefix() as p:
            plan = R.plan_extra_install("embeddings", prefix=p, has_pip=True,
                                        executable=r"C:\Py 3\python.exe", platform="win32",
                                        requires=REQUIRES)
        self.assertNotIn("'", plan.display)
        self.assertEqual(r'"C:\Py 3\python.exe" -m pip install "model2vec>=0.3"', plan.display)
        self.assertEqual("'mokata[full]'", R.quote_arg("mokata[full]", "linux"))
        self.assertEqual("uv", R.quote_arg("uv", "win32"))


# ------------------------------------------------------------------ detect and wire
class TestOutcomeCauses(unittest.TestCase):
    def test_everything_present_is_wired_warmed_and_complete(self):
        res, c = _wire()
        self.assertTrue(res.complete, A.render_outcome(res))
        self.assertEqual([A.ACTIVE] * len(res.rows), [r.status for r in res.rows])
        self.assertEqual(1, len(c["wire"]), "the semantic tier is switched on")
        self.assertEqual(1, len(c["adopt"]), "code-review-graph is pinned")
        self.assertEqual([], c["lines"], "nothing missing -> no install line")
        self.assertIn("model2vec:", res.row("embeddings").detail, "the warm-up's live id")
        self.assertIn("everything wired", A.render_quickstart("full", res))

    def test_missing_packages_name_the_cause_and_ONE_install_line(self):
        res, c = _wire(present=NONE)
        self.assertEqual(["full"], c["lines"], "one line, for the mode's extra")
        for r in res.rows:
            self.assertEqual(A.MISSING, r.status)
            self.assertIn("not in the environment mokata runs from", r.cause)
        self.assertIn("model2vec", res.row("embeddings").cause)
        self.assertIn("tree-sitter-typescript", res.row("graph-ts").cause)
        table = A.render_outcome(res)
        self.assertEqual(1, table.count("LINE[full]"))
        self.assertIn("then:  mokata setup claude --mode full", table)
        self.assertEqual([], c["adopt"] + c["wire"], "nothing to wire for absent packages")
        self.assertIn("PARTIALLY wired", A.render_quickstart("full", res))

    def test_a_blocked_model_download_says_why_and_still_wires_auto(self):
        from mokata.memory.embed import ModelUnavailable

        def warm():
            raise ModelUnavailable("model2vec model 'm' unavailable: ProxyError: 407 Proxy "
                                   "Authentication Required")
        res, c = _wire(warm_fn=warm)
        e = res.row("embeddings")
        self.assertEqual(A.BLOCKED, e.status)
        self.assertIn("proxy", e.cause.lower())
        self.assertIn("HTTPS_PROXY", e.cause)
        self.assertIn("mokata setup claude --mode full", e.fix)
        self.assertNotIn("install", e.fix, "the package is there; reinstalling is the wrong fix")
        self.assertEqual(1, len(c["wire"]), "auto is wired so it switches on once cached")
        self.assertFalse(res.complete)

    def test_model_fetch_causes(self):
        C = A.model_fetch_cause
        self.assertIn("HF_HUB_OFFLINE=1", C("LocalEntryNotFoundError", {"HF_HUB_OFFLINE": "1"}))
        self.assertIn("unreachable", C("ConnectionError: Max retries exceeded", {}))
        self.assertIn("TLS", C("SSLError: CERTIFICATE_VERIFY_FAILED", {}))
        self.assertIn("did not answer", C("ReadTimeout: timed out", {}))
        self.assertIn("could not be loaded: OSError: x", C("OSError: x", {}))

    def test_crg_installed_without_its_command_is_its_own_cause(self):
        res, c = _wire(crg_command_fn=lambda: None)
        g = res.row("code-graph")
        self.assertEqual(A.BLOCKED, g.status)
        self.assertIn("command is not in mokata's environment", g.cause)
        self.assertEqual([], c["adopt"], "never pin a graph mokata cannot launch")

    def test_a_write_that_does_not_commit_is_not_wired(self):
        res, _ = _wire(wire_embedder_fn=lambda root, **k: False,
                       adopt_fn=lambda root, **k: mock.Mock(committed=False))
        self.assertEqual(A.NOT_WIRED, res.row("embeddings").status)
        self.assertEqual(A.NOT_WIRED, res.row("code-graph").status)
        self.assertEqual("mokata graph adopt", res.row("code-graph").fix)

    def test_memory_mode_wires_only_the_semantic_tier(self):
        res, c = _wire(mode="memory", present=NONE)
        self.assertEqual(["embeddings"], [r.key for r in res.rows])
        self.assertEqual(["embeddings"], c["lines"])

    def test_seatbelt_has_no_dependencies(self):
        res, c = _wire(mode="seatbelt")
        self.assertEqual(([], ""), (res.rows, res.install_line))

    def test_a_step_that_blows_up_degrades_and_is_reported(self):
        def bad():
            raise RuntimeError("probe exploded")
        res, _ = _wire(present={**ALL, "code-graph": bad})
        self.assertFalse(res.complete)
        self.assertIn("probe exploded", A.render_outcome(res))


class TestSetupNeverInstallsItself(unittest.TestCase):
    """The core change: no setup path can reach an installer or spawn one."""

    def _parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--path", default=".")
        S.register(sub, common)
        return p

    def test_setup_full_with_real_detection_spawns_nothing(self):
        """Real probes (whatever this env has), real install-line planning; only the model warm-up
        is stubbed (network). Any installer call or subprocess reds."""
        with tempfile.TemporaryDirectory() as d:
            a = self._parser().parse_args(["setup", "claude", "--mode", "full", "--yes",
                                           "--path", d])
            from mokata.init import init_repo
            init_repo(root=d, profile="full", assume_yes=True, out=lambda *_: None)
            with mock.patch.object(S, "setup_harness", return_value=mock.Mock(aborted=False)), \
                    mock.patch.object(A, "_warm_model", return_value="model2vec:stub"), \
                    mock.patch.object(extras_install, "install_extras", side_effect=_boom), \
                    mock.patch.object(extras_install, "install_extra", side_effect=_boom), \
                    mock.patch.object(subprocess, "run", side_effect=_boom), \
                    mock.patch.object(subprocess, "Popen", side_effect=_boom), \
                    redirect_stdout(io.StringIO()) as out:
                self.assertEqual(0, S.cmd_setup(a))
        text = out.getvalue()
        self.assertIn("full mode: optional dependencies", text)
        self.assertNotIn("installing ", text)

    def test_setup_full_with_everything_missing_prints_the_line_and_spawns_nothing(self):
        """The path 0.0.25 installed on: every package absent. Now: the ONE line, no spawn."""
        absent = {k: (lambda: False) for k in A._PRESENT}
        with tempfile.TemporaryDirectory() as d:
            a = self._parser().parse_args(["setup", "claude", "--mode", "full", "--yes",
                                           "--path", d])
            from mokata.init import init_repo
            init_repo(root=d, profile="full", assume_yes=True, out=lambda *_: None)
            with mock.patch.object(S, "setup_harness", return_value=mock.Mock(aborted=False)), \
                    mock.patch.dict(A._PRESENT, absent), \
                    mock.patch.object(A, "_warm_model", side_effect=_boom), \
                    mock.patch.object(extras_install, "install_extras", side_effect=_boom), \
                    mock.patch.object(extras_install, "install_extra", side_effect=_boom), \
                    mock.patch.object(subprocess, "run", side_effect=_boom), \
                    mock.patch.object(subprocess, "Popen", side_effect=_boom), \
                    redirect_stdout(io.StringIO()) as out:
                self.assertEqual(0, S.cmd_setup(a))
        text = out.getvalue()
        self.assertEqual(1, text.count("one line installs everything full mode needs"), text)
        self.assertIn(R.plan_extra_install("full").display, text)
        self.assertIn("PARTIALLY wired", text)

    def test_with_deps_is_gone(self):
        for cmd in (["setup", "claude"], ["init"]):
            with self.subTest(cmd=cmd), self.assertRaises(SystemExit), \
                    redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                self._parser().parse_args(cmd + ["--mode", "full", "--with-deps"])

    def test_setup_mode_resolves_profile_and_runs_detect_and_wire(self):
        a = self._parser().parse_args(["setup", "claude", "--mode", "full", "--yes"])
        seen = {}
        with mock.patch.object(S, "setup_harness",
                               side_effect=lambda **kw: seen.update(kw) or
                               mock.Mock(aborted=False)), \
             mock.patch.object(S, "wire_mode",
                               return_value=A.ModeWiringResult(mode="full")) as wm, \
             redirect_stdout(io.StringIO()) as out:
            self.assertEqual(S.cmd_setup(a), 0)
        self.assertEqual(seen["profile"], A.profile_for_mode("full"))
        self.assertEqual(("full",), wm.call_args.args[1:])
        self.assertIn("full mode", out.getvalue())

    def test_the_planned_line_never_runs(self):
        with mock.patch.object(subprocess, "run", side_effect=_boom):
            plan = R.plan_extra_install("full")
        self.assertIsNone(plan.argv)
        self.assertFalse(plan.runnable)


# ------------------------------------------------------------------ doctor says why
class TestDoctorSaysWhy(unittest.TestCase):
    def _render(self, semantic, installed):
        from mokata.memory import tier_report as T
        with mock.patch.object(T, "_model2vec_installed", return_value=installed):
            return "\n".join(T.RetrievalStack(semantic=semantic).render_lines())

    def test_off_with_the_package_installed_names_the_setup_run(self):
        """DOCTOR-SEMANTIC-OFF-SAYS-NOT-WHY: Jas's run said 'no embedder configured' and stopped."""
        text = self._render("off", True)
        self.assertIn("IS installed but the semantic tier is not switched on", text)
        self.assertIn("mokata setup claude --mode full", text)

    def test_off_without_the_package_keeps_the_plain_note(self):
        self.assertIn("no embedder configured", self._render("off", False))

    def test_hashing_with_the_package_points_at_the_download(self):
        text = self._render("hashing-v1", True)
        self.assertIn("huggingface.co", text)
        self.assertNotIn("install mokata[embeddings] for real semantics", text)


class TestFirstSessionCommands(unittest.TestCase):
    def test_missing_packages_lead_with_the_install_line_and_never_join_with_ampersands(self):
        cmds = A.finish_commands_for("full", packages_missing=True, mokata_on_path=True,
                                     install_line_fn=lambda e: f"LINE[{e}]")
        self.assertEqual(("LINE[full]", "mokata setup claude --mode full"), cmds)
        line = A.FirstSessionCheck(mode="full", missing=("x",), commands=cmds).line
        self.assertNotIn("&&", line, "PowerShell 5.1 has no &&")

    def test_plugin_only_machine_gets_the_readme_line(self):
        cmds = A.finish_commands_for("full", packages_missing=True, mokata_on_path=False,
                                     platform="win32")
        self.assertEqual('uv tool install "mokata[full]"', cmds[0])

    def test_only_unwired_needs_only_setup(self):
        self.assertEqual(("mokata setup claude --mode memory",),
                         A.finish_commands_for("memory", packages_missing=False,
                                               mokata_on_path=True))


if __name__ == "__main__":
    unittest.main()
