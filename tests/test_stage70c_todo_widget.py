"""Stage 70c — render the run-progress as the harness's NATIVE to-do widget.

NOT a new progress system — one MORE renderer over the SAME single source. These tests assert:
  * `progress.build_todo_items(surface)` yields `{summary, items:[{step, status}]}` with the
    ordered phases marked done / in_progress / pending, correct across a fresh / mid-run /
    complete run;
  * it DERIVES from `build_progress` (it is a projection of RunProgress, not a second
    computation) — the items match RunProgress.steps one-for-one, and mokata NEVER recomputes;
  * it is read-only + deterministic + degrade-clean (no run / unreadable surface -> empty
    summary + [] items);
  * the SINGLE `PROGRESS_INSTRUCTION` now also tells the agent to render the native to-do list,
    DERIVE the items from run-state, and FALL BACK to the printed block — and there is still
    exactly ONE instruction constant (no parallel one, no second progress path);
  * every pipeline command template is regenerated from `command_markdown` (drift guard green).

Both jsonschema states (the discover runner exercises absent-jsonschema separately).
"""

import os
import tempfile
import types
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import progress, skills
from mokata.brainstorm import PIPELINE_PHASES
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.progress import DONE, CURRENT, PENDING, build_progress, build_todo_items
from mokata.skills import command_markdown, get_skill
from mokata.state import StateStore

# RunProgress status -> native-widget status (the one mapping under test).
_EXPECT = {DONE: "done", CURRENT: "in_progress", PENDING: "pending"}


def _store(d):
    return StateStore(os.path.join(d, "state"))


def _surface(store):
    """A minimal surface: build_todo_items only reads `.state` (like build_stage_badge)."""
    return types.SimpleNamespace(state=store)


def _checkpoint(store, run_id, passed_phases):
    cp = PipelineCheckpoint(store, run_id)
    if not passed_phases:
        store.write(CHECKPOINT_PREFIX + run_id, {"run_id": run_id, "passed": []})
    for p in passed_phases:
        cp.mark_passed(p)
    return cp


# ------------------------------------------------------------------- the projection
class TestBuildTodoItems(unittest.TestCase):
    def test_no_run_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            td = build_todo_items(_surface(_store(d)))
            self.assertEqual(td, {"summary": "", "items": []})

    def test_fresh_run_first_phase_in_progress_rest_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", [])
            td = build_todo_items(_surface(store), run_id="r1")
            steps = [i["step"] for i in td["items"]]
            self.assertEqual(steps, list(PIPELINE_PHASES))          # ordered, all phases
            statuses = [i["status"] for i in td["items"]]
            self.assertEqual(statuses[0], "in_progress")
            self.assertTrue(all(s == "pending" for s in statuses[1:]))
            self.assertIn("0/7 done", td["summary"])

    def test_mid_run_marks_done_current_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", ["brainstorm", "analysis"])
            td = build_todo_items(_surface(store), run_id="r1")
            by = {i["step"]: i["status"] for i in td["items"]}
            self.assertEqual(by["brainstorm"], "done")
            self.assertEqual(by["analysis"], "done")
            self.assertEqual(by["strawman"], "in_progress")        # first unpassed = current
            self.assertEqual(by["emit"], "pending")
            self.assertIn("2/7 done", td["summary"])
            self.assertIn("current: strawman", td["summary"])

    def test_complete_run_all_done(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", list(PIPELINE_PHASES))
            td = build_todo_items(_surface(store), run_id="r1")
            self.assertTrue(all(i["status"] == "done" for i in td["items"]))
            self.assertIn("7/7 done", td["summary"])
            self.assertIn("complete", td["summary"])

    def test_derives_from_build_progress_not_a_second_computation(self):
        """The items ARE a projection of RunProgress.steps — same order, same statuses."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", ["brainstorm", "analysis", "strawman"])
            prog = build_progress(store, run_id="r1")              # the single source
            td = build_todo_items(_surface(store), run_id="r1")
            # one item per RunProgress step, in the same order, with the mapped status.
            self.assertEqual([i["step"] for i in td["items"]],
                             [s.phase for s in prog.steps])
            self.assertEqual([i["status"] for i in td["items"]],
                             [_EXPECT[s.status] for s in prog.steps])
            # the summary counts come straight from RunProgress (no recompute).
            self.assertIn(f"{prog.done}/{prog.total} done", td["summary"])

    def test_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            _checkpoint(store, "r1", ["brainstorm"])
            before = sorted(os.listdir(store.root))
            first = build_todo_items(_surface(store), run_id="r1")
            second = build_todo_items(_surface(store), run_id="r1")
            after = sorted(os.listdir(store.root))
            self.assertEqual(first, second)                        # deterministic
            self.assertEqual(before, after)                        # wrote nothing

    def test_unreadable_surface_degrades_never_raises(self):
        class Boom:
            @property
            def state(self):
                raise RuntimeError("no state")
        self.assertEqual(build_todo_items(Boom()), {"summary": "", "items": []})


# ------------------------------------------------------------------- the single instruction
class TestSingleProgressInstruction(unittest.TestCase):
    def test_instruction_covers_native_todo_derive_and_fallback(self):
        text = skills.PROGRESS_INSTRUCTION.lower()
        self.assertIn("to-do", text)                               # render the native to-do list
        self.assertIn("in-progress", text)
        self.assertIn("derive", text)                              # from run-state, not invented
        self.assertIn("run-state", text)
        self.assertIn("fall back", text)                           # to the printed block
        self.assertIn("cannot call the to-do tool itself", text)   # honest: agent renders it

    def test_still_exactly_one_instruction_constant(self):
        """No parallel to-do instruction — one driver, one progress path."""
        inst = [n for n in dir(skills)
                if n.endswith("_INSTRUCTION") and isinstance(getattr(skills, n), str)]
        self.assertEqual(inst, ["PROGRESS_INSTRUCTION"])
        self.assertFalse([n for n in dir(skills) if "TODO" in n.upper()])

    def test_instruction_flows_into_pipeline_skills(self):
        for name in ("spec", "test", "develop", "review", "ship"):
            with self.subTest(skill=name):
                md = command_markdown(get_skill(name)).lower()
                self.assertIn("native to-do list", md)


# ------------------------------------------------------------------- drift guard
class TestTemplatesRegenerated(unittest.TestCase):
    def _path(self, name):
        return os.path.join(os.path.dirname(__file__), "..", "templates",
                            "commands", f"{name}.md")

    def test_pipeline_templates_match_source(self):
        for name in ("refine", "spec", "test", "develop", "review", "ship"):
            with self.subTest(command=name):
                with open(self._path(name), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), command_markdown(get_skill(name)))


if __name__ == "__main__":
    unittest.main()
