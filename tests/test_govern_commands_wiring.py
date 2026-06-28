"""Stage 43 — reachable-command WIRING (E4 model routing + C7 consolidate + G6 skill author).

The last three built-but-unreachable capabilities get a real runtime call-site / command:

  - E4: orchestrator._run_one routes a task through ModelRouter (cheapest capable model,
        escalate on BLOCKED) when a router is configured; without one it's the exact path
        as today (no routing, no crash) — degrade-clean, off by default.
  - C7: `mokata memory consolidate` prints proposal-only consolidations (human-gated;
        proposes, never applies) and writes nothing; silent-clean when none.
  - G6: `mokata skill author` drafts a skill via RED-GREEN-for-docs and human-gates the
        write of the result (RED ⇒ nothing written; GREEN ⇒ gated write).
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.config import Surface
from mokata.execmode import PARALLEL, ExecutionChoice, Task, TaskResult, run_tasks
from mokata.execmode.routing import BLOCKED, ModelRouter
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore


def silent(_):
    pass


def run_cli(argv, stdin=""):
    buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)               # empty -> EOF -> human gate declines
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old_stdin
    return rc, buf.getvalue()


class _PlainRunner:
    """A model-agnostic runner (the existing contract: run(task))."""

    def __init__(self, ok=True):
        self.ok = ok

    def run(self, task):
        return TaskResult(task.id, self.ok, "summary", output="o",
                          input_tokens=1, output_tokens=1, seen_context=task.context)


class _ModelAwareRunner:
    """A model-aware runner: succeeds only at `succeed_at` (and stronger is irrelevant —
    escalation stops at the first non-BLOCKED)."""

    def __init__(self, succeed_at):
        self.succeed_at = succeed_at

    def run(self, task, model=None):
        ok = (model == self.succeed_at)
        return TaskResult(task.id, ok, f"on {model}", output="o",
                          input_tokens=1, output_tokens=1, seen_context=task.context)


def _run(router, runner, d):
    led = AuditLedger(os.path.join(d, "l.jsonl"))
    res = run_tasks([Task("a", "x", context="c")],
                    ExecutionChoice(PARALLEL, isolation=True),
                    runner=runner, router=router, ledger=led)
    return res, led.entries()


# --- E4: per-task model routing wired into the orchestrator ---------------------
class TestModelRoutingWiring(unittest.TestCase):
    def test_no_router_is_passthrough_with_no_route_entry(self):
        with tempfile.TemporaryDirectory() as d:
            res, entries = _run(None, _PlainRunner(ok=True), d)
            self.assertTrue(res.results[0].ok)
            self.assertEqual([e for e in entries if e["kind"] == "model_route"], [])

    def test_router_picks_cheapest_when_unblocked(self):
        with tempfile.TemporaryDirectory() as d:
            res, entries = _run(ModelRouter(), _PlainRunner(ok=True), d)
            routes = [e for e in entries if e["kind"] == "model_route"]
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0]["final_model"], "fast")   # cheapest tier
            self.assertFalse(routes[0]["escalated"])
            self.assertTrue(res.results[0].ok)

    def test_router_escalates_on_blocked_then_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            res, entries = _run(ModelRouter(), _ModelAwareRunner(succeed_at="balanced"), d)
            route = [e for e in entries if e["kind"] == "model_route"][0]
            self.assertTrue(route["escalated"])
            self.assertEqual(route["final_model"], "balanced")
            self.assertTrue(route["resolved"])
            self.assertTrue(res.results[0].ok)        # the resolved attempt's result

    def test_router_exhausts_tiers_when_always_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            _res, entries = _run(ModelRouter(), _ModelAwareRunner(succeed_at="none"), d)
            route = [e for e in entries if e["kind"] == "model_route"][0]
            self.assertEqual(route["final_model"], "deep")        # escalated to the top
            self.assertFalse(route["resolved"])


# --- C7: `mokata memory consolidate` (proposal-only) ----------------------------
class TestMemoryConsolidateCommand(unittest.TestCase):
    def _seed_dupes(self, d):
        store = MemoryStore.from_surface(Surface.load(d))
        store.backend.put(MemoryItem.create("db.engine", "postgres",
                                             created_at="2026-01-01T00:00:00+00:00"))
        store.backend.put(MemoryItem.create("db.engine", "postgres",
                                             created_at="2026-02-01T00:00:00+00:00"))
        return store

    def test_prints_proposals_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            store = self._seed_dupes(d)
            rc, out = run_cli(["memory", "consolidate", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("merge", out.lower())
            # proposal-only: both duplicates are still active (nothing applied/written)
            self.assertEqual(len(store.backend.all(statuses=("active",))), 2)

    def test_empty_is_silent_clean(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            rc, out = run_cli(["memory", "consolidate", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("nothing to propose", out.lower())


# --- G6: `mokata skill author` (RED-GREEN-for-docs; human-gated write) ----------
class TestSkillAuthorCommand(unittest.TestCase):
    def _author(self, d, content, yes, name="mytool"):
        cf = os.path.join(d, "content.md")
        with open(cf, "w", encoding="utf-8") as fh:
            fh.write(content)
        argv = ["skill", "author", name, "--summary", "my tool",
                "--require", "body:MUSTHAVE", "--content-file", cf, "--path", d]
        if yes:
            argv.append("--yes")
        return run_cli(argv, stdin="")

    def _dest(self, d, name="mytool"):
        return os.path.join(d, MOKATA_DIR, "skills", f"{name}.md")

    def test_red_draft_reports_failures_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = self._author(d, "no marker here", yes=True)
            self.assertEqual(rc, 1)
            self.assertIn("red", out.lower())
            self.assertFalse(os.path.exists(self._dest(d)))

    def test_green_draft_is_written_on_human_approval(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = self._author(d, "# tool\nMUSTHAVE is present here.", yes=True)
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(self._dest(d)))
            with open(self._dest(d), encoding="utf-8") as fh:
                self.assertIn("MUSTHAVE", fh.read())

    def test_green_draft_without_approval_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            self._author(d, "# tool\nMUSTHAVE is present here.", yes=False)
            self.assertFalse(os.path.exists(self._dest(d)))   # human-gated: not approved

    def test_surfaced_in_skills_catalog(self):
        rc, out = run_cli(["skills"])
        self.assertEqual(rc, 0)
        self.assertIn("skill author", out.lower())


if __name__ == "__main__":
    unittest.main()
