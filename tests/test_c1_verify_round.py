"""0.0.29 C1 verify round — one designed matrix for the refuting verifier's High, two Mediums and
the three byte-identity Lows (`docs/build/handoff/0029-C1.verify.report.md`).

  * InitNeverFetches (H1) — `init --mode full` / `init --profile full` with every package PRESENT
    downloads nothing and builds no index: the plan the human approves says "init downloads
    nothing", so it must be true (§3a NEVER, C-Q1/Q2). Measured at the LOWEST seams — the model
    loader, the meaning-index build, the CRG build — plus the bytes under a fresh HF_HOME and the
    index files on disk. `setup --mode full` (the separate, explicit yes) still fetches and builds.
  * InitProfileFullEndsInAShape (M1) — every full-mode init door ends in one of the two #86
    shapes, and that block is the LAST thing printed (after "Restart Claude Code"): never a close
    that reads fully wired while packages are missing.
  * DoctorNeverFetches (M2) — read-only `doctor` (through `tier_report`) and every other
    read-only store opener never load the model: a model not on this machine is REPORTED. The
    sweep is structural: every `MemoryStore.from_surface(` that may load a model is named here
    with why it ranks by meaning.
  * UnwireIsByteIdentical (L1-L3) — wire then unwire hands back the bytes it found: an empty
    `.gitignore` stays an empty file, a missing trailing newline stays missing, a user's own JSON
    style (4-space, tab, compact, no final newline, CRLF, non-ASCII) survives the merge.

Every wait is an event the test controls (a spy); no wall clock, no network.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import adoption_modes as A
from mokata.memory import embed as E

SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"

ALL = {"embeddings": lambda: True, "code-graph": lambda: True, "graph-ts": lambda: True,
       "lexical": lambda: True}
NONE = {"embeddings": lambda: False, "code-graph": lambda: False, "graph-ts": lambda: False,
        "lexical": lambda: False}


def _tree_bytes(root: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


class _FakeModel:
    def encode(self, texts):
        return [[0.6, 0.8, 0.0] for _ in texts]


def _fake_model2vec(loads):
    """A stand-in for the installed extra: `StaticModel.from_pretrained` records what it was
    handed. The network itself is `embed._hub_fetch`, recorded separately."""
    import types
    mod = types.ModuleType("model2vec")

    class StaticModel:
        @staticmethod
        def from_pretrained(path):
            loads.append(path)
            return _FakeModel()
    mod.StaticModel = StaticModel
    return mod


class _Seams:
    """The lowest fetch/build seams. `calls["model"]` is the NETWORK (`embed._hub_fetch`, the one
    function that reaches huggingface.co): recorded and refused, so a green here cannot come from a
    higher stub hiding the call. `calls["load"]` is what `from_pretrained` was handed (a local
    directory only, ever)."""

    def __init__(self, stack: ExitStack, *, present=ALL, cached=None, hub_ok=False):
        import sys
        self.calls = {"model": [], "search": [], "crg": [], "load": []}

        def hub(name):
            self.calls["model"].append(name)
            if hub_ok:
                return "/fetched/potion"
            raise E.ModelUnavailable("test: the network fetch was reached")

        stack.enter_context(mock.patch.object(E, "_hub_fetch", hub, create=True))
        stack.enter_context(mock.patch.dict(sys.modules,
                                            {"model2vec": _fake_model2vec(self.calls["load"])}))
        # the once-per-process degrade notices these runs raise stay in THIS test: a leaked "already
        # announced" silenced test_db_s4's notice check on the windows probe (run 44056866278)
        from mokata import degrade
        stack.enter_context(mock.patch.object(degrade, "_EMITTED", set()))
        stack.enter_context(mock.patch.object(degrade, "_NOTICES", type(degrade._NOTICES)()))
        stack.enter_context(mock.patch.object(
            A, "_build_search_index",
            lambda root, out: self.calls["search"].append(root) or (True, "built", False)))
        from mokata.knowledge.crg_build import BuildResult
        stack.enter_context(mock.patch.object(
            A, "_build_index",
            lambda root, out: self.calls["crg"].append(root) or BuildResult(ok=True, summary="1")))
        stack.enter_context(mock.patch.dict(A._PRESENT, present))
        stack.enter_context(mock.patch.object(A, "_crg_command", lambda: "/x/bin/code-review-graph"))
        stack.enter_context(mock.patch.object(E, "would_load_model",
                                              lambda name: name in E.MODEL_EMBEDDER_NAMES))
        stack.enter_context(mock.patch.object(E, "cached_model_dir", lambda _m: cached))


class _Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.hf = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.hf, True)

    def cli(self, *argv, present=ALL, cached=None, hub_ok=False):
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            seams = _Seams(stack, present=present, cached=cached, hub_ok=hub_ok)
            stack.enter_context(mock.patch.dict(os.environ, {"HF_HOME": self.hf}))
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(list(argv) + ["--path", self.root])
        return rc, out.getvalue(), seams.calls


# ------------------------------------------------------------------ H1
class InitNeverFetches(_Repo):
    def _assert_nothing_fetched(self, calls, text):
        self.assertEqual([], calls["model"], "init loaded (would download) the ~30 MB model")
        self.assertEqual([], calls["search"], "init built the meaning-search index")
        self.assertEqual([], calls["crg"], "init built the code-review-graph index")
        self.assertEqual(0, _tree_bytes(self.hf), "bytes landed under HF_HOME during init")
        self.assertFalse(Path(self.root, ".code-review-graph").exists())
        self.assertIn("init downloads nothing", text, "the plan the human approves says so")

    def test_init_mode_full_with_the_packages_present(self):
        rc, text, calls = self.cli("init", "--mode", "full", "--yes")
        self.assertEqual(0, rc, text)
        self._assert_nothing_fetched(calls, text)
        self.assertIn("everything wired", text)

    def test_init_profile_full_with_the_packages_present(self):
        rc, text, calls = self.cli("init", "--profile", "full", "--yes")
        self.assertEqual(0, rc, text)
        self._assert_nothing_fetched(calls, text)

    def test_init_mode_memory_fetches_nothing_either(self):
        rc, text, calls = self.cli("init", "--mode", "memory", "--yes")
        self.assertEqual(0, rc, text)
        self.assertEqual([], calls["model"])

    def test_the_rows_say_where_the_fetch_happens(self):
        res = A.wire_mode(self.root, "full", out=lambda _s: None, present=ALL, fetch=False,
                          warm_fn=lambda: self.fail("warm under fetch=False"),
                          crg_command_fn=lambda: "/x/crg", graph_pinned_fn=lambda _r: False,
                          embedder_wired_fn=lambda _r: True,
                          adopt_fn=lambda root, **k: mock.Mock(committed=True),
                          build_index_fn=lambda *a: self.fail("CRG build under fetch=False"),
                          build_search_fn=lambda *a: self.fail("search build under fetch=False"),
                          rg_command_fn=lambda: "/x/rg")
        self.assertTrue(res.complete)
        e = res.row("embeddings")
        self.assertIn("after you approve its plan", e.detail)
        self.assertIn(A.setup_command_for("full"), e.detail, "names the explicit-yes door")
        self.assertIsNone(res.row(A.SEARCH_ROW_KEY))
        self.assertIsNone(res.row(A.INDEX_ROW_KEY))
        self.assertIn("first structural query", res.row("code-graph").detail)

    def test_setup_mode_full_is_the_door_that_fetches(self):
        self.cli("init", "--profile", "full", "--yes")
        rc, text, calls = self.cli("setup", "claude", "--mode", "full", "--yes")
        self.assertEqual(1, len(calls["model"]), "setup --mode full is the explicit yes: it warms")
        self.assertEqual(1, len(calls["crg"]), "and builds the code-review-graph index")


# ------------------------------------------------------------------ M1
class InitProfileFullEndsInAShape(_Repo):
    def _closing(self, text):
        self.assertNotIn("then:", text)
        self.assertNotIn("PARTIALLY", text)
        restart = text.rfind("Restart Claude Code")
        block = max(text.rfind("one command left"), text.rfind("everything wired"))
        self.assertGreater(block, -1, "no #86 closing block:\n" + text)
        self.assertGreater(block, restart, "the closing block must come after the restart line")
        return text[block:]

    def _one_install_line(self, tail):
        line = A._install_line("full")
        self.assertEqual(1, tail.count(line), tail)

    def test_profile_full_packages_absent_names_the_one_command(self):
        rc, text, _ = self.cli("init", "--profile", "full", "--yes", present=NONE)
        self.assertEqual(0, rc, text)
        tail = self._closing(text)
        self.assertIn("one command", tail)
        self.assertEqual(1, text.count(A._install_line("full")), text)

    def test_mode_full_packages_absent_names_the_one_command(self):
        rc, text, _ = self.cli("init", "--mode", "full", "--yes", present=NONE)
        self.assertEqual(0, rc, text)
        self.assertIn("one command", self._closing(text))
        self.assertEqual(1, text.count(A._install_line("full")), text)

    def test_profile_full_packages_present_is_everything_wired_last(self):
        rc, text, _ = self.cli("init", "--profile", "full", "--yes")
        self.assertIn("everything wired", self._closing(text))

    def test_profile_standard_prints_no_mode_block(self):
        rc, text, _ = self.cli("init", "--profile", "standard", "--yes")
        self.assertNotIn("one command left", text)
        self.assertNotIn("everything wired", text)


# ------------------------------------------------------------------ M2
class DoctorNeverFetches(_Repo):
    def test_doctor_reports_the_model_it_does_not_have(self):
        self.cli("init", "--profile", "full", "--yes")
        rc, text, calls = self.cli("doctor")
        self.assertEqual([], calls["model"], "read-only doctor loaded (would fetch) the model")
        self.assertEqual(0, _tree_bytes(self.hf))
        self.assertIn("model2vec:" + E.MODEL2VEC_MODEL, text)
        self.assertIn("not downloaded yet", text)
        self.assertIn("never fetches it unasked", text)
        self.assertIn(A.setup_command_for("full"), text)

    def test_a_cached_model_is_loaded_locally_and_reported(self):
        from mokata.memory import tier_report as T
        surface = mock.Mock()
        surface.manifest.setting.return_value = {"embedder": "auto"}
        with ExitStack() as stack:
            seams = _Seams(stack, cached="/cache/potion")
            got = T._semantic_engine(surface)
        self.assertEqual([], seams.calls["model"], "cached -> no network")
        self.assertEqual(["/cache/potion"], seams.calls["load"], "cached -> a local load")
        self.assertEqual("model2vec:" + E.MODEL2VEC_MODEL, got)

    def test_the_tier_report_never_loads_an_uncached_model(self):
        from mokata.memory import tier_report as T
        surface = mock.Mock()
        surface.manifest.setting.return_value = {"embedder": "auto"}
        with ExitStack() as stack:
            seams = _Seams(stack)
            got = T.resolve_stack(surface)
        self.assertEqual([], seams.calls["model"])
        self.assertEqual("model2vec:" + E.MODEL2VEC_MODEL, got.semantic)
        self.assertTrue(any("not downloaded yet" in ln for ln in got.render_lines()))

    def test_govern_never_loads_the_model(self):
        self.cli("init", "--profile", "full", "--yes")
        from mokata.config import Surface
        from mokata.governance_view import build_governance_view
        with ExitStack() as stack:
            seams = _Seams(stack)
            build_governance_view(Surface.load(self.root))
        self.assertEqual([], seams.calls["model"])


def _calls(fn_names, kw_names):
    """Every call in src/ to one of `fn_names` (by attribute or bare name), or carrying one of
    `kw_names` with a value that is not the constant False — keyed by CALL SITE: (file, enclosing
    function, the call's source). No file is exempt as a whole."""
    found = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        rel = "/".join(path.relative_to(SRC).parts)

        def walk(node, fn):
            for child in ast.iter_child_nodes(node):
                name = fn
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = child.name
                if isinstance(child, ast.Call):
                    f = child.func
                    called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    hit = called in fn_names or any(
                        k.arg in kw_names and not (isinstance(k.value, ast.Constant)
                                                   and k.value.value is False)
                        for k in child.keywords)
                    if hit:
                        found.add((rel, fn, ast.get_source_segment(text, child).replace("\n", " ")))
                walk(child, name)
        walk(tree, "<module>")
    return found


class TheModelHasOneDoor(_Repo):
    """0.0.29 C1 re-verify M2, structural: the network is ONE function, and fetch consent is passed
    at exactly the named call sites — each behind a human yes. A new site reds here."""

    def test_only_the_door_reaches_the_hub(self):
        hub = _calls({"hf_hub_download", "snapshot_download", "_hub_fetch"}, set())
        self.assertEqual({("memory/embed.py", "_hub_fetch", c) for _f, fn, c in hub
                          if fn == "_hub_fetch"} | {("memory/embed.py", "download_model",
                                                     "_hub_fetch(model_name)")},
                         hub)

    def test_from_pretrained_is_only_ever_handed_a_local_directory(self):
        self.assertEqual({
            ("memory/embed.py", "_load_model2vec",
             "StaticModel.from_pretrained(download_model(model_name, consent=fetch_consent))"),
            ("ide/search.py", "_load_embedder", "StaticModel.from_pretrained(path)"),
        }, {c for c in _calls({"from_pretrained"}, set())})

    def test_fetch_consent_is_given_at_exactly_the_named_call_sites(self):
        self.assertEqual({
            # `setup --mode`, after the human approved a plan that states the download (M3)
            ("adoption_modes.py", "_warm_model", "Model2VecEmbedder(fetch_consent=True)"),
            # the IDE's explicit "fetch the model" click
            ("ide/search.py", "fetch_model", "_load_embedder(allow_network=True)"),
            ("ide/search.py", "_model_dir", "download_model(MODEL2VEC_MODEL, consent=True)"),
            # pass-throughs of a consent their caller already holds
            ("memory/embed.py", "__init__",
             "_load_model2vec(             model_name, fetch_consent=fetch_consent)"),
            ("memory/embed.py", "_load_model2vec",
             "download_model(model_name, consent=fetch_consent)"),
        }, {c for c in _calls(set(), {"consent", "fetch_consent", "allow_network"})})


class ReadOnlyPathsNeverFetch(_Repo):
    """The verifier's measured paths (packages present, model not cached): each reports and
    degrades — the network is never reached and HF_HOME stays at 0 bytes."""

    def setUp(self):
        super().setUp()
        self.cli("init", "--profile", "full", "--yes")

    def _measure(self, fn):
        with ExitStack() as stack:
            seams = _Seams(stack)
            stack.enter_context(mock.patch.dict(os.environ, {"HF_HOME": self.hf}))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                fn()
        self.assertEqual([], seams.calls["model"], "the network was reached")
        self.assertEqual([], seams.calls["load"], "an uncached model was loaded")
        self.assertEqual(0, _tree_bytes(self.hf))

    def test_the_memory_cli(self):
        from mokata import cli
        for argv in (["memory"], ["memory", "review"], ["memory", "export"],
                     ["memory", "harvest"], ["memory", "refresh"]):
            with self.subTest(argv=argv):
                self._measure(lambda: cli.main(argv + ["--path", self.root]))

    def test_the_mcp_reads(self):
        from mokata.mcp import tools_read as TR
        self._measure(lambda: TR.recall(path=self.root, subject="x"))
        self._measure(lambda: TR.recall(path=self.root))
        self._measure(lambda: TR.recall(path=self.root, query="payment retries"))
        self._measure(lambda: TR.consolidate_proposals(path=self.root))

    def test_the_degrade_notices_these_runs_raise_do_not_leak(self):
        """Windows probe 44056866278: the probe runs its modules in ONE process, this file before
        test_db_s4, and a leaked once-per-process "announced" record silenced db_s4's notice check.
        Not an OS behaviour: an order one. Pinned here on every OS by measuring the leak itself."""
        from mokata import degrade
        before = (set(degrade._EMITTED), dict(degrade._NOTICES))
        from mokata.memory.embed import detect_embedder
        with ExitStack() as stack:
            _Seams(stack)
            with mock.patch.object(E, "_extra_is_installed", lambda: True):
                detect_embedder(degrade_out=lambda _m: None)
        self.assertEqual(before, (set(degrade._EMITTED), dict(degrade._NOTICES)))

    def test_a_degraded_recall_names_the_one_command(self):
        from mokata.memory.embed import ModelNotCached, Model2VecEmbedder
        with ExitStack() as stack:
            _Seams(stack)
            with self.assertRaises(ModelNotCached) as cm:
                Model2VecEmbedder()
        self.assertIn(A.setup_command_for("full"), str(cm.exception))


# ------------------------------------------------------------------ M3
#: The words in the approved plan that state each action `wire_mode` can take.
_STATED = {"embedder-setting": "set `settings.memory.embedder = auto`",
           "model-fetch": "DOWNLOAD the semantic model",
           "model-load": "from this machine's Hugging Face cache (no download)",
           "search-index": "build the meaning-search index",
           "graph-pin": "pin code-review-graph as the code graph",
           "graph-index": "build code-review-graph's index"}


class SetupPlanStatesWhatItDoes(_Repo):
    """`setup claude --mode full`: every action the run takes is stated in the plan printed BEFORE
    it (the text the human approves), and nothing is stated that the run does not do."""

    def _setup(self, *extra, present=ALL, cached=None, plugin=False):
        seen = []
        real = A.wire_mode

        def spy(*a, **k):
            res = real(*a, **k)
            seen.append(res)
            return res
        argv = ["setup", "claude", "--mode", "full", *(["--plugin"] if plugin else []), *extra]
        with mock.patch.object(A, "wire_mode", spy), \
                mock.patch("mokata.cli_commands.setup.wire_mode", spy, create=True):
            rc, text, calls = self.cli(*argv, present=present, cached=cached, hub_ok=True)
        return rc, text, calls, (seen[0] if seen else None)

    def _assert_plan_is_the_run(self, text, res):
        self.assertIsNotNone(res, text)
        head = text[:text.index("Mode 'full' will also:")] if "Mode 'full' will also:" in text \
            else ""
        plan = text[len(head):text.index("── full mode")]
        for key, words in _STATED.items():
            self.assertEqual(key in res.actions, words in plan, f"{key}: plan vs run\n{plan}")

    def test_packages_present_model_not_cached(self):
        rc, text, calls, res = self._setup("--yes")
        self._assert_plan_is_the_run(text, res)
        self.assertIn("model-fetch", res.actions)
        self.assertIn("~30 MB", text)
        self.assertIn("huggingface.co", text)
        # a fresh repo: the init in the same plan writes the setting and the pin — and says so
        self.assertIn("settings.memory.embedder = auto", text)
        self.assertIn("code-review-graph the code graph", text)

    def test_packages_present_model_cached(self):
        rc, text, calls, res = self._setup("--yes", cached="/cache/potion")
        self._assert_plan_is_the_run(text, res)
        self.assertIn("model-load", res.actions)
        self.assertNotIn("DOWNLOAD", text)

    def test_packages_absent(self):
        rc, text, calls, res = self._setup("--yes", present=NONE)
        self._assert_plan_is_the_run(text, res)
        self.assertIn("install NOTHING", text)

    def test_after_init_the_setting_and_pin_are_not_restated(self):
        self.cli("init", "--profile", "full", "--yes")
        rc, text, calls, res = self._setup("--yes")
        self._assert_plan_is_the_run(text, res)

    def test_a_standard_repo_gets_the_setting_and_pin_stated_then_written(self):
        self.cli("init", "--profile", "standard", "--yes")
        rc, text, calls, res = self._setup("--yes")
        self._assert_plan_is_the_run(text, res)
        self.assertIn("embedder-setting", res.actions)
        self.assertIn("graph-pin", res.actions)

    def test_the_plugin_route_states_it_too(self):
        rc, text, calls, res = self._setup("--yes", plugin=True)
        self._assert_plan_is_the_run(text, res)

    def test_a_decline_fetches_nothing(self):
        rc, text, calls, res = self._setup()           # stdin is not a tty: the gate says no
        self.assertNotEqual(0, rc)
        self.assertEqual([], calls["model"])
        self.assertIn("DOWNLOAD the semantic model", text, "the plan was shown before the no")


# ------------------------------------------------------------------ L1-L3
class UnwireIsByteIdentical(unittest.TestCase):
    """setup claude then unsetup claude: every pre-existing file comes back byte for byte, and a
    file that did not exist does not exist."""

    def _round_trip(self, files):
        from mokata.harness_setup import setup_harness, unsetup_harness
        quiet = lambda _s: None  # noqa: E731
        with tempfile.TemporaryDirectory() as d:
            for rel, data in files.items():
                p = Path(d, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            setup_harness("claude", root=d, assume_yes=True, out=quiet)
            unsetup_harness("claude", root=d, assume_yes=True, out=quiet)
            after = {}
            for rel in list(files) + [".gitignore", ".worktreeinclude", ".mcp.json",
                                      ".claude/settings.json"]:
                p = Path(d, rel)
                after[rel] = p.read_bytes() if p.is_file() else None
        for rel in after:
            self.assertEqual(files.get(rel), after[rel], rel)

    # L1 / L2 — the two marked text files
    def test_an_empty_gitignore_stays_an_empty_file(self):
        self._round_trip({".gitignore": b""})

    def test_a_gitignore_without_a_final_newline_gets_none_back(self):
        self._round_trip({".gitignore": b"node_modules"})

    def test_a_crlf_gitignore_without_a_final_newline(self):
        self._round_trip({".gitignore": b"a\r\nnode_modules"})

    def test_an_empty_worktreeinclude_stays_an_empty_file(self):
        self._round_trip({".worktreeinclude": b""})

    def test_a_worktreeinclude_without_a_final_newline(self):
        self._round_trip({".worktreeinclude": b".env.local"})

    def test_nothing_there_leaves_nothing_there(self):
        self._round_trip({})

    # L3 — the user's own JSON style
    def test_settings_four_space(self):
        self._round_trip({".claude/settings.json": b'{\n    "model": "opus"\n}\n'})

    def test_settings_tab_indent(self):
        self._round_trip({".claude/settings.json": b'{\n\t"model": "opus"\n}\n'})

    def test_settings_compact_no_final_newline(self):
        self._round_trip({".claude/settings.json": b'{"model":"opus"}'})

    def test_settings_four_space_crlf(self):
        self._round_trip({".claude/settings.json": b'{\r\n    "model": "opus"\r\n}\r\n'})

    def test_settings_non_ascii_kept_literal(self):
        self._round_trip({".claude/settings.json": '{\n  "name": "café"\n}\n'.encode("utf-8")})

    def test_mcp_json_compact_beside_another_server(self):
        self._round_trip({".mcp.json": b'{"mcpServers": {"other": {"command": "x", "args": []}}}'})


class JsonStyleIsDetectedNotGuessed(unittest.TestCase):
    def test_unknown_style_falls_back_to_two_space(self):
        from mokata.harness_setup import _json_style
        self.assertEqual(2, _json_style(b'{ "a" :1}').indent)
        self.assertEqual(2, _json_style(b"").indent)

    def test_each_style_reproduces_its_own_bytes(self):
        import json
        from mokata.harness_setup import _json_style
        for raw in (b'{\n    "a": [\n        1\n    ]\n}', b'{"a":[1]}\n', b'{"a": [1]}',
                    b'{\n\t"a": 1\n}\n'):
            st = _json_style(raw)
            self.assertEqual(raw.decode(), st.render(json.loads(raw)), raw)


if __name__ == "__main__":
    unittest.main()
