"""Stage 25 — agent fan-out UX (ask parallel-vs-sequential first) + code-graph guidance.

Both jsonschema states. Part A: the implementation choice is always offered, defaults to
sequential, honors a saved preference, shows the parallel estimate, and degrades when the
harness has no subagents. Part B: doctor/status emit an actionable hint — the live queries
when a graph is wired, a concrete "how to enable" when only the grep floor is active.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.config import Surface
from mokata.detect import Detector
from mokata.execmode import (
    PARALLEL,
    SEQUENTIAL,
    Task,
    resolve_execution_choice,
    saved_execution_default,
)
from mokata.init import init_repo
from mokata.knowledge import graph_guidance
from mokata.manifest import Manifest


def _silent(_):
    pass


def _manifest(execution_default=None):
    data = {
        "manifest_version": 1, "mokata": {"version": "0.0.0"}, "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {}, "tools": {}, "settings": {},
    }
    if execution_default is not None:
        data["settings"]["execution"] = {"default": execution_default}
    return Manifest.from_dict(data)


class _Asker:
    """Records every prompt; answers from a script (raises if over-asked)."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, question, default):
        self.prompts.append(question)
        return self.answers.pop(0) if self.answers else default


# ------------------------------------------------------------------ Part A: the ask

class TestExecutionChoiceAsk(unittest.TestCase):
    def test_asks_and_no_runs_sequential(self):
        asker = _Asker("sequential")
        choice = resolve_execution_choice(manifest=_manifest(), ask=asker)
        self.assertEqual(choice.mode, SEQUENTIAL)
        self.assertTrue(asker.prompts, "must have asked the user")

    def test_parallel_when_user_picks_it(self):
        asker = _Asker("parallel", "y", "n")   # mode, isolation?, fan-out?
        choice = resolve_execution_choice(manifest=_manifest(), ask=asker)
        self.assertTrue(choice.is_parallel)
        self.assertTrue(choice.isolation)      # maps to fresh-subagent isolation

    def test_no_asker_defaults_to_sequential(self):
        # Never fan out without an explicit choice — no asker means the safe default.
        choice = resolve_execution_choice(manifest=_manifest(), ask=None)
        self.assertEqual(choice.mode, SEQUENTIAL)

    def test_saved_sequential_skips_the_prompt(self):
        def _boom(*_a):
            raise AssertionError("must not prompt when a preference is saved")
        choice = resolve_execution_choice(manifest=_manifest("sequential"), ask=_boom)
        self.assertEqual(choice.mode, SEQUENTIAL)

    def test_saved_parallel_skips_the_prompt(self):
        def _boom(*_a):
            raise AssertionError("must not prompt when a preference is saved")
        choice = resolve_execution_choice(manifest=_manifest("parallel"), ask=_boom)
        self.assertTrue(choice.is_parallel)
        self.assertTrue(choice.isolation)

    def test_default_preference_is_ask(self):
        self.assertEqual(saved_execution_default(_manifest()), "ask")
        self.assertEqual(saved_execution_default(_manifest("bogus")), "ask")

    def test_degrades_to_sequential_without_subagents(self):
        # Even if the user would pick parallel, no subagents -> sequential + a clear note.
        buf = []
        asker = _Asker("parallel")
        choice = resolve_execution_choice(
            manifest=_manifest(), ask=asker, subagents_available=False,
            out=buf.append)
        self.assertEqual(choice.mode, SEQUENTIAL)
        self.assertFalse(asker.prompts)        # didn't even bother asking
        self.assertTrue(any("subagents" in m for m in buf))

    def test_parallel_estimate_is_shown(self):
        buf = []
        tasks = [Task(id="t1", description="add cache", context="x" * 40),
                 Task(id="t2", description="add metrics", context="y" * 40)]
        asker = _Asker("sequential")
        resolve_execution_choice(manifest=_manifest(), ask=asker, tasks=tasks,
                                 out=buf.append)
        self.assertTrue(any("estimate" in m for m in buf),
                        "the parallel cost estimate must be surfaced when offering it")

    def test_choice_logged_to_ledger(self):
        class _Ledger:
            def __init__(self): self.records = []
            def record(self, kind, **f): self.records.append((kind, f))
        led = _Ledger()
        resolve_execution_choice(manifest=_manifest("sequential"), ledger=led)
        self.assertTrue(any(k == "exec_mode" for k, _ in led.records))


# --------------------------------------------------------------- Part B: graph hint

def _graph_surface(d, present):
    # init full (wires code-review-graph in the code_graph chain), then load with detection
    # forced so the graph tool is present/absent deterministically.
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    overrides = {"code-review-graph": present, "serena": False, "ripgrep": False}
    return Surface.load(d, detector=Detector(overrides=overrides, cache=False))


class TestGraphGuidance(unittest.TestCase):
    def test_hint_when_graph_active(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _graph_surface(d, present=True)
            hint = graph_guidance(surface)
            self.assertIn("code graph active", hint)
            self.assertIn("code-review-graph", hint)
            self.assertIn("mokata query", hint)

    def test_hint_when_only_grep_floor(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _graph_surface(d, present=False)   # graph absent -> grep floor
            hint = graph_guidance(surface)
            self.assertIn("no codebase graph wired", hint)
            self.assertIn("--profile full", hint)        # a concrete next step

    def test_hint_reflects_configured_path(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            # configure the graph tool with an endpoint (Stage 24A config block)
            from mokata import config_cmd
            config_cmd.config_set(d, "tools.code-review-graph.config.endpoint",
                                  "http://localhost:7000", assume_yes=True,
                                  out=_silent)
            surface = Surface.load(
                d, detector=Detector(overrides={"code-review-graph": True,
                                                "serena": False, "ripgrep": False},
                                     cache=False))
            hint = graph_guidance(surface)
            self.assertIn("config:", hint)
            self.assertIn("localhost:7000", hint)


class TestDoctorAndStatusHints(unittest.TestCase):
    def test_doctor_report_carries_graph_hint(self):
        from mokata.govern import diagnose
        with tempfile.TemporaryDirectory() as d:
            surface = _graph_surface(d, present=False)
            report = diagnose(surface)
            self.assertIn("no codebase graph wired", report.graph_hint)
            self.assertIn(report.graph_hint, report.render())

    def test_status_cli_emits_hint(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["status", "--path", d])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            # standard profile resolves to the grep floor -> the enable hint
            self.assertIn("grep floor", out)

    def test_doctor_cli_emits_hint(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["doctor", "--path", d])
            self.assertIn("grep floor", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
