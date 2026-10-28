"""0.0.25 Lane HB — HOOK-BUDGET met for real by the `mokata-hook` command hooks (doc 109 §17.2).

Business rules — what must be true for the user:
  * Every Claude Code tool call runs mokata's gate hooks, and every turn runs its observability hooks,
    each as a fresh Python process. A gate hook answers within doc 109's 150 ms p95 and an
    observability hook within 50 ms p95 (Linux, measured here; the macOS/Windows legs are CI's, with
    `perf.relax_factor()` for shared runners). Where a path still misses its target, the gap is a
    NAMED ceiling in `perf.HOOK_BUDGET_GAPS_MS`, never a silent pass.
  * The speed-up changes no verdict: the gate/secret modules are the contract and stay green; the
    lazy package re-exports give back exactly the objects the eager ones did; the manifest verdict
    is the built-in validator's, with or without `jsonschema`; the session draft is byte-identical.
  * Outside a mokata repo (the plugin installed globally, a user in any other repo), the
    observability hooks that have nothing to do answer from the standard library alone — and only
    when the real resolver would also have said "no mokata repo here".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import copy
import importlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harvest as H
from mokata import perf, schema
from mokata_emit import DEFERRED, QUIET_OUTSIDE_MOKATA, SYNC_OBSERVE
from mokata_emit import surely_not_mokata as _surely_not_mokata
from mokata.init import init_repo
from mokata.quiet_spawn import hidden
from mokata.repo_identity import resolve_mokata_root

SRC = str(Path(__file__).resolve().parents[1] / "src")
LAZY_PACKAGES = ("mokata.govern", "mokata.memory", "mokata.knowledge", "mokata.adapters")


def _silent(*_a, **_k):
    pass


def _fresh(code: str, cwd: str = None, stdin: str = "") -> subprocess.CompletedProcess:
    """Run `code` in a FRESH interpreter with this tree's `src/` first — the hook's own situation."""
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, "-c", code], input=stdin, cwd=cwd, env=env, text=True,
                          encoding="utf-8", capture_output=True, timeout=120, **hidden())


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   **hidden())


def _commit(d: str) -> None:
    _git("-C", d, "-c", "user.email=hb@example.invalid", "-c", "user.name=hb", "commit",
         "--allow-empty", "-qm", "init")


# ======================================================================================
# 1 · the lazy package re-exports give back exactly what the eager ones did
# ======================================================================================

class TheLazyPackagesExportExactlyWhatTheyDid(unittest.TestCase):
    """A caller writing `from mokata.govern import WriteGate` must get the same object as before; the
    only change is WHEN the defining submodule loads."""

    def _eager_list(self, pkg: str):
        path = importlib.import_module(pkg).__file__
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        guard = [n for n in tree.body if isinstance(n, ast.If)
                 and getattr(n.test, "id", "") == "TYPE_CHECKING"]
        self.assertEqual(1, len(guard), f"{pkg}: the eager list must stay under TYPE_CHECKING")
        out = {}
        for node in guard[0].body:
            self.assertIsInstance(node, ast.ImportFrom)
            for alias in node.names:
                out[alias.asname or alias.name] = (node.module, alias.name)
        return out

    def test_the_static_list_the_export_map_and_all_are_one_list(self):
        for pkg in LAZY_PACKAGES:
            mod = importlib.import_module(pkg)
            eager = self._eager_list(pkg)
            self.assertEqual(eager, dict(mod._EXPORTS), pkg)
            self.assertEqual(set(eager), set(mod.__all__), pkg)

    def test_every_name_is_the_defining_modules_own_object(self):
        for pkg in LAZY_PACKAGES:
            mod = importlib.import_module(pkg)
            for name, (sub, attr) in mod._EXPORTS.items():
                want = getattr(importlib.import_module(f"{pkg}.{sub}"), attr)
                self.assertIs(want, getattr(mod, name), f"{pkg}.{name}")

    def test_a_submodule_is_still_reachable_as_an_attribute_and_a_typo_is_still_an_error(self):
        import mokata.memory as memory
        self.assertIs(importlib.import_module("mokata.memory.store"), memory.store)
        with self.assertRaises(AttributeError):
            memory.no_such_name_here            # noqa: B018
        self.assertFalse(hasattr(memory, "__wrapped__"))

    def test_the_per_tool_call_secret_scan_does_not_load_the_governance_layer(self):
        """The business reason for the laziness: `secret-guard` runs on every Write/Edit/Bash and
        needs `govern.secrets` — not karpathy, doctor, the memory package or jsonschema."""
        p = _fresh("import sys, mokata.govern.secrets\n"
                   "print(sorted(m for m in sys.modules if m.startswith(('mokata.', 'jsonschema'))))")
        self.assertEqual(0, p.returncode, p.stderr)
        loaded = set(json.loads(p.stdout.replace("'", '"')))
        for heavy in ("mokata.govern.karpathy", "mokata.govern.doctor", "mokata.memory",
                      "mokata.govern.enforce", "jsonschema"):
            self.assertNotIn(heavy, loaded)


# ======================================================================================
# 2 · the manifest verdict is the built-in validator's — jsonschema only adds phrasing
# ======================================================================================

def _valid():
    from _support import sample_manifest_data
    return sample_manifest_data()


class TheManifestVerdictNeverNeedsJsonschema(unittest.TestCase):
    """Every hook that loads the Surface validates the manifest. Importing jsonschema cost ~60–80 ms
    a hook on every real install (the `mcp` dependency pulls it in), so it now runs only on a
    manifest the built-in pass already rejects. That is only sound if the built-in checks enforce
    EVERY constraint the JSON Schema states — proven here constraint by constraint, and by fuzz."""

    def _builtin_only(self, data):
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            return schema.validate_manifest(data)

    def test_each_schema_constraint_is_enforced_without_jsonschema(self):
        cases = {
            "manifest_version is a bool": lambda d: d.__setitem__("manifest_version", True),
            "manifest_version is a float": lambda d: d.__setitem__("manifest_version", 1.0),
            "mokata.version missing": lambda d: d["mokata"].pop("version"),
            "profile empty": lambda d: d.__setitem__("profile", ""),
            "settings not an object": lambda d: d.__setitem__("settings", []),
            "layer without enabled": lambda d: d.__setitem__("layers", {"x": {}}),
            "capability description not a string": lambda d: next(iter(d["capabilities"].values()))
            .__setitem__("description", 3),
            "capability layer not a string": lambda d: next(iter(d["capabilities"].values()))
            .__setitem__("layer", 3),
            "fallback empty": lambda d: next(iter(d["capabilities"].values()))
            .__setitem__("fallback", []),
            "fallback entry not a string": lambda d: next(iter(d["capabilities"].values()))
            .__setitem__("fallback", [7]),
            "tool version not a string": lambda d: next(iter(d["tools"].values()))
            .__setitem__("version", 2),
            "tool enabled not a bool": lambda d: next(iter(d["tools"].values()))
            .__setitem__("enabled", "yes"),
            "tool kind unknown": lambda d: next(iter(d["tools"].values())).__setitem__("kind", "x"),
            "detect.name not a string": lambda d: next(iter(d["tools"].values()))["detect"]
            .__setitem__("name", 5),
            "detect.type missing": lambda d: next(iter(d["tools"].values()))["detect"].pop("type"),
            "tools missing": lambda d: d.pop("tools"),
            "capabilities missing": lambda d: d.pop("capabilities"),
        }
        self.assertEqual([], self._builtin_only(_valid()))
        for label, mutate in cases.items():
            data = _valid()
            mutate(data)
            self.assertTrue(self._builtin_only(data), f"built-in pass accepted: {label}")

    def test_fuzz_whatever_jsonschema_rejects_the_builtin_pass_rejects(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema absent on this leg — the constraint-by-constraint test covers it")
        validator = jsonschema.Draft202012Validator(schema.MANIFEST_JSON_SCHEMA)
        values = [None, True, False, 0, 1, 2, 1.0, -1, "", "x", "grep", "cli", "command", [], ["x"],
                  [1], {}, {"enabled": True}, {"type": "command"}, {"a": 1}]
        rnd = random.Random(20261019)

        def paths(obj, pre=()):
            yield pre
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield from paths(v, pre + (k,))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    yield from paths(v, pre + (i,))

        base = _valid()
        rejected = 0
        for _ in range(3000):
            data = copy.deepcopy(base)
            for _m in range(rnd.randint(1, 3)):
                where = rnd.choice(list(paths(data))[1:])
                parent = data
                for k in where[:-1]:
                    parent = parent[k]
                if rnd.random() < 0.7:
                    parent[where[-1]] = copy.deepcopy(rnd.choice(values))
                elif isinstance(parent, dict):
                    del parent[where[-1]]
                else:
                    parent.pop(where[-1])
                if rnd.random() < 0.2 and isinstance(parent, dict):
                    parent[rnd.choice(["version", "description", "name", "layer", "enabled"])] = \
                        copy.deepcopy(rnd.choice(values))
            if any(True for _e in validator.iter_errors(data)):
                rejected += 1
                self.assertTrue(schema._builtin_errors(data), json.dumps(data)[:400])
        self.assertGreater(rejected, 500)          # the fuzz really exercised rejection

    def test_a_valid_manifest_loads_without_importing_jsonschema(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            p = _fresh("import sys\nfrom mokata.config import Surface\nSurface.load(%r)\n"
                       "print('jsonschema' in sys.modules)" % d)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual("False", p.stdout.strip())

    def test_a_rejected_manifest_still_gets_jsonschemas_richer_messages_first(self):
        mod = type(sys)("jsonschema")

        class _V:
            def __init__(self, _s):
                pass

            def iter_errors(self, _d):
                return iter([type("E", (), {"path": ["tools"], "message": "rich"})()])

        mod.Draft202012Validator = _V
        bad = _valid()
        bad.pop("tools")
        with mock.patch.dict(sys.modules, {"jsonschema": mod}):
            errs = schema.validate_manifest(bad)
            self.assertEqual("tools: rich", errs[0])
            self.assertIn("tools must be an object", errs)
            self.assertEqual([], schema.validate_manifest(_valid()))


# ======================================================================================
# 3 · the stdlib fast path answers only where the real resolver would say "no mokata repo"
# ======================================================================================

class TheFastPathIsSoundAgainstTheResolver(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        t = os.path.realpath(cls._t.name)
        cls.layouts = {}
        plain = os.path.join(t, "plain", "sub")
        os.makedirs(plain)
        cls.layouts["plain dir"] = plain
        repo = os.path.join(t, "repo")
        os.makedirs(os.path.join(repo, "src"))
        _git("init", "-q", repo)
        _commit(repo)
        cls.layouts["git repo, no mokata"] = os.path.join(repo, "src")
        mrepo = os.path.join(t, "mrepo")
        os.makedirs(os.path.join(mrepo, "src"))
        _git("init", "-q", mrepo)
        init_repo(root=mrepo, profile="standard", assume_yes=True, out=_silent)
        _commit(mrepo)
        cls.layouts["mokata repo subdir"] = os.path.join(mrepo, "src")
        _git("-C", mrepo, "worktree", "add", "-q", os.path.join(t, "wt-m"))
        cls.layouts["worktree of a mokata repo, manifest uncommitted"] = os.path.join(t, "wt-m")
        _git("-C", repo, "worktree", "add", "-q", os.path.join(t, "wt-r"))
        cls.layouts["worktree of a plain repo"] = os.path.join(t, "wt-r")
        os.symlink(mrepo, os.path.join(repo, "src", "inner"))
        cls.layouts["symlink inside a plain repo, into a mokata repo"] = \
            os.path.join(repo, "src", "inner")
        os.symlink(os.path.join(mrepo, "src"), os.path.join(t, "link"))
        cls.layouts["symlinked path to a mokata subdir"] = os.path.join(t, "link")
        gl = os.path.join(t, "gitlink")
        os.makedirs(gl)
        os.symlink(os.path.join(mrepo, ".git"), os.path.join(gl, ".git"))
        cls.layouts[".git is a symlink to a mokata repo's"] = gl
        sub = os.path.join(t, "submodule-like")
        os.makedirs(sub)
        with open(os.path.join(sub, ".git"), "w", encoding="utf-8") as fh:
            fh.write(f"gitdir: {os.path.join(mrepo, '.git')}\n")
        cls.layouts[".git is a file pointing into a mokata repo"] = sub

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def test_it_says_not_mokata_only_where_the_resolver_finds_no_root(self):
        decided = 0
        for label, path in self.layouts.items():
            if _surely_not_mokata(path):
                decided += 1
                self.assertIsNone(resolve_mokata_root(path).root, label)
        self.assertGreaterEqual(decided, 2)            # it really answers the common cases
        self.assertTrue(_surely_not_mokata(self.layouts["plain dir"]))
        self.assertTrue(_surely_not_mokata(self.layouts["git repo, no mokata"]))

    def test_it_never_decides_a_mokata_repo_or_a_worktree(self):
        for label in ("mokata repo subdir", "worktree of a mokata repo, manifest uncommitted",
                      ".git is a file pointing into a mokata repo",
                      ".git is a symlink to a mokata repo's"):
            self.assertFalse(_surely_not_mokata(self.layouts[label]), label)
        self.assertFalse(_surely_not_mokata(None))     # garbage never short-circuits a hook

    def test_outside_mokata_the_quiet_hooks_load_no_engine_and_say_nothing(self):
        """The plugin's hooks fire in every repo the user opens. Outside a mokata repo the quiet
        observability hooks must exit 0 with no output, having imported only the front door — and,
        for the ones the front door spools (0.0.26 O3), not even the dispatcher, and spooling
        nothing (the plugin route; the per-project settings route always reaches its handler)."""
        d = self.layouts["git repo, no mokata"]
        payload = json.dumps({"session_id": "S1", "cwd": d, "hook_event_name": "Stop",
                              "tool_name": "mcp__mokata__session_save",
                              "tool_response": {"registered": "r1"}})
        code = ("import sys, json\nfrom mokata_emit import main\nrc = main(sys.argv[1:])\n"
                "sys.stderr.write('\\nMODS ' + json.dumps(sorted(m for m in sys.modules "
                "if m.startswith('mokata'))) + ' ' + str('dataclasses' in sys.modules))\n"
                "raise SystemExit(rc)")
        spool = os.path.join(self._t.name, "quiet-spool")
        for sub in QUIET_OUTSIDE_MOKATA:
            for extra in (["--via-plugin"],):
                env = dict(os.environ, PYTHONPATH=SRC, MOKATA_HOOK_SPOOL=spool)
                p = subprocess.run([sys.executable, "-c", code, sub, *extra], input=payload,
                                   cwd=d, env=env, text=True, encoding="utf-8",
                                   capture_output=True, timeout=60, **hidden())
                self.assertEqual(0, p.returncode, (sub, p.stderr))
                self.assertEqual("", p.stdout, sub)
                mods, dc = p.stderr.rsplit("MODS ", 1)[1].rsplit(" ", 1)
                want = (["mokata_emit"] if sub in DEFERRED + SYNC_OBSERVE
                        else ["mokata", "mokata.hook_cli", "mokata_emit"])
                self.assertEqual(want, json.loads(mods), sub)
                self.assertEqual("False", dc, sub)
                self.assertFalse(os.path.exists(os.path.join(d, ".mokata")), sub)
                self.assertFalse(os.path.isdir(spool) and os.listdir(spool), sub)

    def test_in_a_mokata_repo_the_same_hooks_still_do_their_work(self):
        """The fast path must not swallow the real case: a bound session's Stop still drafts."""
        from mokata import run_intent as RI
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _git("init", "-q", d)
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            PipelineCheckpoint(Surface.load(d).state, "r1").ensure_registered()
            self.assertTrue(RI.bind_registered(d, "S1", "r1"))
            payload = json.dumps({"session_id": "S1", "cwd": d, "hook_event_name": "Stop",
                                  "last_assistant_message": "done"})
            env = dict(os.environ, PYTHONPATH=SRC)
            env["MOKATA_HOOK_SPOOL"] = os.path.join(d, "spool")
            p = subprocess.run([sys.executable, "-m", "mokata_emit", "stop", "--via-plugin"],
                               input=payload, cwd=d, env=env, text=True, encoding="utf-8",
                               capture_output=True, timeout=60, **hidden())
            self.assertEqual(0, p.returncode, p.stderr)
            _wait_drained(env["MOKATA_HOOK_SPOOL"])
            drafts = H.drafts(d)
            self.assertEqual(1, len(drafts))
            self.assertIn("- last word: done", drafts[0].value)


# ======================================================================================
# 4 · the session draft reads the record once and is byte-identical
# ======================================================================================

class TheHarvestReadsTheRecordOnce(unittest.TestCase):

    def test_the_two_step_note_equals_the_one_pass_note(self):
        def one_pass(lines, last):
            lines = list(lines)
            if isinstance(last, str) and last.strip():
                msg = " ".join(last.split())
                if len(msg) > H.LAST_MESSAGE_CHARS:
                    msg = msg[:H.LAST_MESSAGE_CHARS - 1] + "…"
                lines.append(f"- last word: {msg}")
            note = "\n".join(lines)
            return note[:H.MAX_NOTE_CHARS - 1] + "…" if len(note) > H.MAX_NOTE_CHARS else note

        rnd = random.Random(7)
        for _ in range(5000):
            lines = ["- " + "x" * rnd.randint(0, H.MAX_NOTE_CHARS // 2)
                     for _ in range(rnd.randint(0, 6))]
            last = rnd.choice([None, "", "  ", "thanks", "y " * rnd.randint(1, 400)])
            self.assertEqual(one_pass(lines, last),
                             H._with_last_word(H._bounded_note("\n".join(lines)), last))

    def test_a_harvest_builds_the_note_once(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            with mock.patch("mokata.run_intent.session_claim", return_value=("bound", "r1")), \
                    mock.patch.object(H, "build_note", wraps=H.build_note) as spy:
                H.harvest(d, "S1", "stop", "bye")
            self.assertEqual(1, spy.call_count)


# ======================================================================================
# 5 · HOOK-BUDGET enforced: every wired hook, cold process, p95, against its kind's number
# ======================================================================================

def _payloads(d: str) -> dict:
    base = {"session_id": "S1", "cwd": d, "transcript_path": "/dev/null",
            "permission_mode": "default"}
    write = {"file_path": os.path.join(d, "src", "a.py"), "content": "x = 2\n" * 300}
    return {
        "secret-guard": dict(base, hook_event_name="PreToolUse", tool_name="Write", tool_input=write),
        "gate-guard": dict(base, hook_event_name="PreToolUse", tool_name="Write", tool_input=write),
        "dirty-track": dict(base, hook_event_name="PostToolUse", tool_name="Write",
                            tool_input=write, tool_response={"success": True}),
        # 0.0.26 O3: a REAL registration — run-bind is synchronous, so the timed path is the bind
        # itself (a `session_save` that registered nothing is answered on the standard library).
        "run-bind": dict(base, hook_event_name="PostToolUse", tool_name="mcp__mokata__session_save",
                         tool_input={"register": True}, tool_response={"registered": "r1"}),
        "user-prompt-submit": dict(base, hook_event_name="UserPromptSubmit",
                                   prompt="fix the parser bug"),
        "pre-compact": dict(base, hook_event_name="PreCompact", trigger="auto"),
        "subagent-start": dict(base, hook_event_name="SubagentStart", agent_id="A1",
                               agent_type="general"),
        "subagent-stop": dict(base, hook_event_name="SubagentStop", agent_id="A1"),
        "stop": dict(base, hook_event_name="Stop"),
        # R1 (0.0.26) — the synchronous evidence gate, timed on a completion CLAIM (the path that
        # resolves the run; without a claim it answers sooner).
        "stop-gate": dict(base, hook_event_name="Stop",
                          last_assistant_message="All tests pass — the feature is done."),
        "session-start": dict(base, hook_event_name="SessionStart", source="startup"),
        "session-end": dict(base, hook_event_name="SessionEnd", reason="exit"),
        "statusline": dict(base, model={"display_name": "Opus"}, workspace={"current_dir": d}),
    }


def _wait_drained(spool: str, timeout: float = 60.0) -> None:
    """Block until the O3 drainer has emptied `spool` (entries, in-flight work AND its lock)."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os.path.isdir(spool) or not os.listdir(spool):
            return
        time.sleep(0.02)
    raise AssertionError(f"spool not drained in {timeout}s: {os.listdir(spool)}")


def _wait_refreshed(root: str, timeout: float = 60.0) -> None:
    """Block until SessionStart's detached briefing refresher (0.0.26 O12b) has released its lock.
    Like the drain, it is off the harness's clock, so it is let finish between calls, untimed."""
    import time
    lock = os.path.join(root, ".mokata", "temp_local", "briefing", "refresh.lock")
    deadline = time.monotonic() + 1.0
    while not os.path.exists(lock) and time.monotonic() < deadline:
        if not os.path.isdir(os.path.dirname(lock)):
            break
        time.sleep(0.01)
    deadline = time.monotonic() + timeout
    while os.path.exists(lock) and time.monotonic() < deadline:
        time.sleep(0.02)


def _p95(xs):
    xs = sorted(xs)
    return xs[max(0, int(round(0.95 * len(xs))) - 1)]


class HookBudgetHoldsForEveryWiredHook(unittest.TestCase):
    """doc 109 §17.2, measured the way the harness experiences it: a fresh `mokata-hook <sub>`
    process per call — the console script's own target, the `mokata_emit` front door (0.0.26 O3:
    observe hooks are spooled and drained by a detached process; the drain is let finish between
    calls and is NOT timed, as it is not on the harness's clock) — on the PLUGIN route (`--via-plugin`, the globally installed one — the heavier of
    the two routes, it also checks for a settings-route twin), the payload on stdin, p95 over N calls
    after two warm-ups. Three fixtures:
    OUTSIDE a mokata repo (the plugin installed globally), a mokata REPO with no run, and a session
    BOUND to a run (where the hooks do their real work). Noise-robust without loosening the bound: a
    case passes when ANY of up to three batches meets it — a real regression misses all three. Each
    p95 is divided by `perf.interpreter_factor` of a bare `python -c pass` measured interleaved with
    it, so a box slowed by parallel suites is not read as a slow hook (factor 1 on an idle box)."""

    N = 15
    BATCHES = 3
    FLOOR = (sys.executable, "-c", "pass")

    @classmethod
    def setUpClass(cls):
        from mokata import run_intent as RI
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        cls._t = tempfile.TemporaryDirectory()
        t = os.path.realpath(cls._t.name)
        cls.dirs = {}
        for name in ("outside", "repo", "bound"):
            d = os.path.join(t, name)
            os.makedirs(os.path.join(d, "src"))
            _git("init", "-q", d)
            with open(os.path.join(d, "src", "a.py"), "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            if name != "outside":
                init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            cls.dirs[name] = d
        PipelineCheckpoint(Surface.load(cls.dirs["bound"]).state, "r1").ensure_registered()
        RI.bind_registered(cls.dirs["bound"], "S1", "r1")
        cls.relax = perf.relax_factor()
        cls.rows = []

    @classmethod
    def tearDownClass(cls):
        if cls.rows:
            sys.stderr.write("\n[HOOK-BUDGET] cold-process p95 ms (Linux; relax x%g):\n" % cls.relax
                             + "".join(f"  {r}\n" for r in cls.rows))
        cls._t.cleanup()

    def _batch(self, sub: str, d: str, body: bytes) -> "tuple":
        """(hook p95, bare-interpreter p50) — the floor runs INTERLEAVED with the hook, so both see
        the same box at the same moment (`perf.interpreter_factor`)."""
        import statistics
        import time
        spool = os.path.join(self._t.name, "spool")
        env = dict(os.environ, PYTHONPATH=SRC, CLAUDE_PROJECT_DIR=d, MOKATA_HOOK_SPOOL=spool)
        xs, floor = [], []
        for i in range(self.N + 2):
            t0 = time.perf_counter()
            subprocess.run([sys.executable, "-m", "mokata_emit", sub, "--via-plugin"],
                           input=body, cwd=d,
                           env=env, capture_output=True, timeout=60, **hidden())
            t1 = time.perf_counter()
            _wait_drained(spool)
            if sub == "session-start":
                _wait_refreshed(d)             # O12b: SessionStart's detached refresher, untimed
            t1b = time.perf_counter()
            subprocess.run(self.FLOOR, stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                           **hidden())
            if i >= 2:
                xs.append((t1 - t0) * 1000.0)
                floor.append((time.perf_counter() - t1b) * 1000.0)
        return _p95(xs), statistics.median(floor)

    def _check(self, fixture: str, subs) -> None:
        d = self.dirs[fixture]
        payloads = _payloads(d)
        over = []
        for sub in subs:
            budget, is_target = perf.hook_budget_ms(sub, fixture)
            body = json.dumps(payloads[sub]).encode("utf-8")
            seen = []
            for _ in range(self.BATCHES):
                p95, floor = self._batch(sub, d, body)
                factor = perf.interpreter_factor(floor)
                seen.append((p95 / factor, p95, floor))
                if seen[-1][0] <= budget * self.relax:
                    break
            best = min(seen)
            label = "target" if is_target else "GAP ceiling"
            self.rows.append(f"{fixture:<8} {sub:<19} p95 {best[0]:7.1f} normalised ({best[1]:.1f} "
                             f"raw, floor {best[2]:.1f})  {label} {budget:g}")
            if best[0] > budget * self.relax:
                over.append(f"{sub} @ {fixture}: {best[0]:.1f} > {budget * self.relax:g} {seen}")
        self.assertEqual([], over)

    def test_every_wired_hook_has_a_kind_and_doc_109s_numbers_are_the_targets(self):
        from mokata.hook_cli import _SUBCOMMANDS
        self.assertEqual(set(_SUBCOMMANDS), set(perf.HOOK_KINDS))
        # U14 (0.0.26): the `context` kind is RULED at 150 ms — no longer a proposal.
        self.assertEqual({"observe": 50.0, "gate": 150.0, "context": 150.0},
                         perf.HOOK_BUDGET_TARGETS_MS)
        self.assertEqual({"session-start", "user-prompt-submit", "subagent-start", "statusline"},
                         {s for s, k in perf.HOOK_KINDS.items() if k == "context"})
        # U13: what the front door spools is the observe kind — nothing that answers — minus
        # `run-bind`, which stays synchronous (the gate reads its binding on the next tool call).
        self.assertEqual(set(DEFERRED) | set(SYNC_OBSERVE),
                         {s for s, k in perf.HOOK_KINDS.items() if k == "observe"})
        self.assertEqual(("run-bind",), SYNC_OBSERVE)
        for key, ceiling in perf.HOOK_BUDGET_GAPS_MS.items():
            sub, fixture = key.split("@")
            self.assertIn(fixture, ("outside", "repo", "bound"), key)
            self.assertGreater(ceiling, perf.HOOK_BUDGET_TARGETS_MS[perf.HOOK_KINDS[sub]],
                               f"{key}: a 'gap' at or under its target is just the target")

    def test_outside_a_mokata_repo(self):
        self._check("outside", list(perf.HOOK_KINDS))

    def test_in_a_mokata_repo_with_no_run(self):
        self._check("repo", list(perf.HOOK_KINDS))

    def test_in_a_session_bound_to_a_run(self):
        self._check("bound", list(perf.HOOK_KINDS))


if __name__ == "__main__":
    unittest.main()
