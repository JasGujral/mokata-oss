"""Stage 54f — assisted task decomposition + parallel-plan confirm.

The fan-out engine already exists (E2/E3 + E8 + the orchestrator). This stage adds the
SPLITTER and a human-gated confirm in front of it, reusing the existing flow. These tests
prove:

  * ACs → one independent subtask each (Task objects), with the surface they touch;
  * ACs that share a symbol/file → kept SEQUENTIAL (a depends_on edge);
  * the code graph catches a dependency the lexical floor would miss;
  * NO graph → conservative: independence unverified, fan-out withheld, sequential
    recommended (never silently parallel);
  * the split is PRESENTED and requires confirmation — nothing runs unconfirmed; the
    decision is ledger-logged; the safe default (no asker / EOF) is "not confirmed";
  * a confirmed plan flows into resolve_execution_choice → run_tasks;
  * the fan-out safety backstop disables concurrent fan-out for an unsafe plan;
  * no spec/ACs and subagents-unavailable both degrade clean;
  * the new surface keeps the 54e parity guard green.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import mcp_server as M
from mokata import parity
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.execmode import ExecutionChoice, SEQUENTIAL, Task
from mokata.execmode.decompose import (DecompositionPlan, Subtask,
                                       confirm_decomposition, decompose,
                                       extract_refs, run_decomposition)
from mokata.govern import AuditLedger
from mokata.knowledge.query import QueryResult, Reference

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS_DIR = os.path.join(ROOT, "templates", "commands")


def _spec(*pairs):
    """A spec from (ac_id, text) pairs."""
    return Spec(title="T", criteria=[AcceptanceCriterion(id=i, text=t) for i, t in pairs])


class _FakeGraphLayer:
    """A KnowledgeLayer stand-in: `uses_graph` True, blast_radius returns scripted links."""

    def __init__(self, links=None, degraded=False):
        self.uses_graph = True
        self._links = links or {}
        self._degraded = degraded

    def blast_radius(self, sym, depth=2):
        refs = [Reference(path="x.py", line=1, symbol=s)
                for s in self._links.get(sym, [])]
        return QueryResult(kind="blast_radius", target=sym, references=refs,
                           degraded=self._degraded)


class _GrepLayer:
    uses_graph = False


class _ScriptAsker:
    """Answers questions from a queue; EOF-safe — returns the default once exhausted."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, question, default):
        self.prompts.append(question)
        return self.answers.pop(0) if self.answers else default


def _eof_asker(question, default):
    # mimics _cli_ask's EOF behaviour (53b lesson): no input → the default
    return default


# ============================================================= extract_refs
class TestExtractRefs(unittest.TestCase):
    def test_pulls_code_symbols_and_files(self):
        syms, files = extract_refs("update `parse_config` in auth.py and helpers/util.py")
        self.assertIn("parse_config", syms)
        self.assertIn("auth.py", files)
        self.assertIn("helpers/util.py", files)

    def test_dotted_symbol_yields_head_component(self):
        syms, _ = extract_refs("call `User.save` then User.delete")
        self.assertIn("User", syms)          # the shared head — so the two overlap

    def test_plain_english_is_not_a_symbol(self):
        syms, files = extract_refs("the user should be able to log in")
        self.assertEqual(syms, ())
        self.assertEqual(files, ())

    def test_camelcase_is_a_symbol(self):
        syms, _ = extract_refs("render the UserProfile widget")
        self.assertIn("UserProfile", syms)


# ============================================================= decompose: independence
class TestDecomposeIndependent(unittest.TestCase):
    def test_one_subtask_per_ac(self):
        plan = decompose(_spec(("AC1", "add `parse_config`"),
                               ("AC2", "add `render_view`")))
        self.assertEqual(len(plan.subtasks), 2)
        self.assertEqual([s.ac_id for s in plan.subtasks], ["AC1", "AC2"])
        self.assertEqual(plan.dependency_count, 0)      # distinct symbols → independent

    def test_subtasks_become_engine_tasks(self):
        plan = decompose(_spec(("AC1", "touch `parse_config` in conf.py")))
        tasks = plan.tasks()
        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], Task)
        self.assertIn("parse_config", tasks[0].context)
        self.assertIn("conf.py", tasks[0].context)


# ============================================================= decompose: dependencies
class TestDecomposeDependencies(unittest.TestCase):
    def test_shared_symbol_keeps_sequential(self):
        plan = decompose(_spec(("AC1", "add `parse_config`"),
                               ("AC2", "also change `parse_config` callers")))
        self.assertEqual(plan.dependency_count, 1)
        self.assertEqual(plan.subtasks[1].depends_on, ("task-AC1",))

    def test_shared_file_keeps_sequential(self):
        plan = decompose(_spec(("AC1", "edit auth.py for login"),
                               ("AC2", "edit auth.py for logout")))
        self.assertEqual(plan.subtasks[1].depends_on, ("task-AC1",))

    def test_graph_catches_a_link_lexical_would_miss(self):
        # Distinct symbols (no lexical overlap) but the graph links them → dependency.
        layer = _FakeGraphLayer(links={"parse_config": ["load_settings"]})
        plan = decompose(_spec(("AC1", "add `parse_config`"),
                               ("AC2", "add `load_settings`")), layer=layer)
        self.assertTrue(plan.graph_backed)
        self.assertEqual(plan.subtasks[1].depends_on, ("task-AC1",))

    def test_graph_verified_independence_allows_fanout(self):
        layer = _FakeGraphLayer(links={"parse_config": [], "render_view": []})
        plan = decompose(_spec(("AC1", "add `parse_config`"),
                               ("AC2", "add `render_view`")), layer=layer)
        self.assertTrue(plan.graph_backed)
        self.assertEqual(plan.dependency_count, 0)
        self.assertTrue(plan.fanout_safe)
        self.assertTrue(plan.recommended_parallel)


# ============================================================= conservative no-graph
class TestConservativeNoGraph(unittest.TestCase):
    def test_no_graph_is_not_silently_parallel(self):
        plan = decompose(_spec(("AC1", "add `parse_config`"),
                               ("AC2", "add `render_view`")), layer=_GrepLayer())
        self.assertFalse(plan.graph_backed)
        self.assertFalse(plan.fanout_safe)              # independent lexically, but UNVERIFIED
        self.assertFalse(plan.recommended_parallel)
        self.assertTrue(any("UNVERIFIED" in w or "unverified" in w for w in plan.warnings))

    def test_degraded_graph_does_not_count_as_backed(self):
        layer = _FakeGraphLayer(links={"parse_config": ["load_settings"]}, degraded=True)
        plan = decompose(_spec(("AC1", "add `parse_config`"),
                               ("AC2", "add `load_settings`")), layer=layer)
        self.assertFalse(plan.graph_backed)             # degraded query ≠ verified
        self.assertFalse(plan.fanout_safe)


# ============================================================= no spec/ACs
class TestDegradeClean(unittest.TestCase):
    def test_no_spec(self):
        plan = decompose(None)
        self.assertEqual(plan.subtasks, [])
        self.assertTrue(plan.warnings)
        self.assertIn("nothing to split", plan.render())

    def test_empty_criteria(self):
        plan = decompose(Spec(title="T", criteria=[]))
        self.assertEqual(plan.subtasks, [])


# ============================================================= confirm (gated + logged)
class TestConfirm(unittest.TestCase):
    def _plan(self):
        return decompose(_spec(("AC1", "add `parse_config`"), ("AC2", "add `render_view`")))

    def test_safe_default_no_asker_is_not_confirmed(self):
        led = AuditLedger.from_mokata_dir(tempfile.mkdtemp())
        out = []
        res = confirm_decomposition(self._plan(), ask=None, ledger=led, out=out.append)
        self.assertFalse(res.confirmed)                 # nothing runs unconfirmed
        self.assertTrue(any("decompose" in "".join(out) for _ in [0]))
        kinds = [e["kind"] for e in led.entries()]
        self.assertIn("decompose_confirm", kinds)       # decision logged
        rec = [e for e in led.entries() if e["kind"] == "decompose_confirm"][-1]
        self.assertFalse(rec["confirmed"])

    def test_eof_asker_is_not_confirmed(self):
        res = confirm_decomposition(self._plan(), ask=_eof_asker)
        self.assertFalse(res.confirmed)

    def test_presents_the_split(self):
        out = []
        confirm_decomposition(self._plan(), ask=_eof_asker, out=out.append)
        self.assertIn("subtask", "\n".join(out))

    def test_yes_confirms(self):
        led = AuditLedger.from_mokata_dir(tempfile.mkdtemp())
        res = confirm_decomposition(self._plan(), assume_yes=True, ledger=led)
        self.assertTrue(res.confirmed)
        rec = [e for e in led.entries() if e["kind"] == "decompose_confirm"][-1]
        self.assertTrue(rec["confirmed"])

    def test_edit_keeps_a_subset(self):
        plan = self._plan()
        res = confirm_decomposition(plan, ask=_ScriptAsker(["task-AC1"]))
        self.assertTrue(res.confirmed)
        self.assertTrue(res.edited)
        self.assertEqual([s.id for s in res.plan.subtasks], ["task-AC1"])

    def test_unrecognized_answer_is_safe_default(self):
        res = confirm_decomposition(self._plan(), ask=_ScriptAsker(["maybe-later"]))
        self.assertFalse(res.confirmed)


# ============================================================= run wiring (existing flow)
class TestRunWiring(unittest.TestCase):
    def _plan(self):
        return decompose(_spec(("AC1", "add `parse_config`"), ("AC2", "add `render_view`")))

    def test_confirmed_plan_flows_into_run_tasks_sequential(self):
        led = AuditLedger.from_mokata_dir(tempfile.mkdtemp())
        out = []
        result = run_decomposition(self._plan(), manifest=None, ask=_eof_asker,
                                   ledger=led, out=out.append, runner=None)
        self.assertEqual(result.choice.mode, SEQUENTIAL)
        self.assertEqual(len(result.results), 2)        # both tasks ran
        self.assertFalse(result.degraded)
        kinds = [e["kind"] for e in led.entries()]
        self.assertIn("exec_estimate", kinds)           # the estimate was surfaced/logged
        self.assertIn("sequential", kinds)

    def test_subagents_unavailable_degrades_to_sequential(self):
        result = run_decomposition(self._plan(), manifest=None, ask=_eof_asker,
                                   runner=None, subagents_available=False)
        self.assertEqual(result.choice.mode, SEQUENTIAL)
        self.assertEqual(len(result.results), 2)

    def test_fanout_withheld_when_not_safe(self):
        # User asks for parallel + fan-out, but the no-graph plan is NOT fanout_safe.
        led = AuditLedger.from_mokata_dir(tempfile.mkdtemp())
        out = []
        asker = _ScriptAsker(["parallel", "y", "y"])    # mode, isolation, fanout
        plan = self._plan()
        self.assertFalse(plan.fanout_safe)
        result = run_decomposition(plan, manifest=None, ask=asker, ledger=led,
                                   out=out.append, runner=None)
        self.assertFalse(result.choice.fanout)          # fan-out disabled (never silent)
        kinds = [e["kind"] for e in led.entries()]
        self.assertIn("decompose_fanout_guard", kinds)
        self.assertIn("disabling concurrent fan-out", "\n".join(out))


# ============================================================= MCP read tool
class TestDecomposeMCPTool(unittest.TestCase):
    def _repo(self, d):
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        return Surface.load(d)

    def _emit_spec(self, surface, *pairs):
        surface.state.write("emitted_spec", _spec(*pairs).to_dict())

    def test_registered_read_only(self):
        self.assertIn("decompose", M.read_tool_names())
        self.assertNotIn("decompose", M.write_tool_names())

    def test_proposes_split_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._repo(d)
            self._emit_spec(surface, ("AC1", "add `parse_config`"),
                            ("AC2", "add `render_view`"))
            before = sorted(os.listdir(surface.state.root))
            res = M.decompose(path=d)
            self.assertTrue(res["available"])
            self.assertEqual(len(res["subtasks"]), 2)
            self.assertIn("block", res)
            self.assertEqual(before, sorted(os.listdir(surface.state.root)))  # read-only

    def test_no_spec_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            res = M.decompose(path=d)
            self.assertFalse(res["available"])
            self.assertIn("note", res)


# ============================================================= parity + slash template
class TestSurfaceParity(unittest.TestCase):
    def test_decompose_declared_and_parity_green(self):
        self.assertIn("decompose", parity.SURFACE_MATRIX)
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())

    def test_slash_template_exists_namespaced(self):
        path = os.path.join(COMMANDS_DIR, "decompose.md")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("name: decompose", md)
        self.assertIn("description: mokata ·", md)


if __name__ == "__main__":
    unittest.main()
