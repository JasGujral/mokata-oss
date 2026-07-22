"""GR-PA — the prior-art bound step: brainstorm graph-queries existing implementations + recalls
related decisions BEFORE approach approval; findings surface as an "extend, don't re-implement"
row in the tradeoff table (doc 02 GR-PA; doc 84 GR-PA; canonical spec = the GR-PA row).

`test_gr_pa_regression` is the release regression: approving an approach WITHOUT the prior-art
step having run is REFUSED with the named message — the behavior that fails on the pre-GR-PA code
(which approved happily, no prior-art awareness). This is a step-RAN check (a bound step, not a new
hard gate), distinct from GR.S3's graph-quality gate: it never requires that prior art was FOUND,
only that the step ran; a degraded/absent graph still RUNS the step (evidence-gathering) and is
NEVER refused here — GR.S3 owns the degraded-radius refusal, and this stage adds no duplicate.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import (Approach, BrainstormGateError, BrainstormSession,
                               save_brainstorm_progress, restore_brainstorm_progress)
from mokata.brainstorm_impact import DesignFitVerdict
from mokata.knowledge.query import QueryResult, Reference


# ---------------------------------------------------------------- deterministic fake layer
class _Layer:
    """A stand-in graph. `supports_semantic` marks a CRG-adopted semantic-capable graph; `uses_graph`
    a real adopted structural graph; `backend_name="ast"` the AST name-resolution floor; else the
    lexical grep floor. `table` maps a target → the references a query returns. `calls` records every
    structural/semantic query for the budget asserts."""

    def __init__(self, table=None, *, uses_graph=False, backend_name="grep",
                 supports_semantic=False, degraded=False):
        self.table = table or {}
        self.uses_graph = uses_graph
        self.backend_name = backend_name
        self.supports_semantic = supports_semantic
        self._degraded = degraded
        self.calls = []

    def implementers(self, name):
        self.calls.append(("implementers", name))
        return QueryResult("implementers", name, references=list(self.table.get(name, [])),
                           backend=self.backend_name, degraded=self._degraded)

    def callers(self, name):
        self.calls.append(("callers", name))
        return QueryResult("callers", name, references=list(self.table.get(name, [])),
                           backend=self.backend_name, degraded=self._degraded)

    def semantic(self, query, kind=None, limit=20):
        self.calls.append(("semantic", query))
        refs = [r for refs in self.table.values() for r in refs][:limit]
        return QueryResult("semantic", query, references=refs,
                           backend=self.backend_name, degraded=self._degraded)


def _ref(path, line, sym):
    return Reference(path, line, snippet="", symbol=sym)


_TABLE = {"retry": [_ref("utils/retry.py", 5, "retry"), _ref("app/net.py", 9, "backoff")]}


class _Item:
    """A duck-typed memory item for the recall channel."""
    def __init__(self, id, subject, value="", kind="rule", about_code=None):
        self.id = id
        self.subject = subject
        self.value = value
        self.kind = kind
        self.effective_kind = kind
        self.about_code = about_code or []


def _recall_one(_query):
    return [_Item("d1", "retries use exponential backoff", kind="rule",
                  about_code=["retry"])]


# ================================================================ the pure prior-art step
class TestPriorArtStep(unittest.TestCase):

    def test_semantic_tier_when_crg_adopted(self):
        from mokata.prior_art import run_prior_art, TIER_SEMANTIC
        layer = _Layer(_TABLE, uses_graph=True, backend_name="code-review-graph",
                       supports_semantic=True)
        res = run_prior_art("a", ["retry"], layer=layer, query="add retry")
        self.assertTrue(res.ran)
        self.assertEqual(res.tier, TIER_SEMANTIC)
        self.assertIn("semantic", res.tier)
        self.assertIn("semantic", [c[0] for c in layer.calls])   # used semantic search

    def test_name_resolution_tier_for_ast(self):
        from mokata.prior_art import run_prior_art, TIER_NAME_RESOLUTION
        layer = _Layer(_TABLE, uses_graph=False, backend_name="ast")
        res = run_prior_art("a", ["retry"], layer=layer)
        self.assertEqual(res.tier, TIER_NAME_RESOLUTION)
        self.assertIn("name-resolution", res.tier)
        self.assertIn("implementers", [c[0] for c in layer.calls])   # structural, not semantic

    def test_lexical_tier_grep_floor(self):
        from mokata.prior_art import run_prior_art, TIER_LEXICAL
        layer = _Layer(_TABLE, uses_graph=False, backend_name="grep", degraded=True)
        res = run_prior_art("a", ["retry"], layer=layer)
        self.assertEqual(res.tier, TIER_LEXICAL)

    def test_absent_when_no_layer_but_still_runs(self):
        from mokata.prior_art import run_prior_art, TIER_ABSENT
        res = run_prior_art("a", ["retry"], layer=None, recall=_recall_one)
        self.assertTrue(res.ran)                       # evidence-gathering runs regardless
        self.assertEqual(res.tier, TIER_ABSENT)
        self.assertEqual(res.implementations, [])      # no structural findings without a layer

    def test_findings_are_normalized(self):
        from mokata.prior_art import run_prior_art
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        res = run_prior_art("a", ["retry"], layer=layer)
        self.assertTrue(res.implementations)
        f = res.implementations[0]
        self.assertTrue(f.path and f.symbol)           # normalized to a typed finding
        self.assertEqual(f.symbol, "retry")

    def test_empty_findings_is_a_first_class_honest_outcome(self):
        from mokata.prior_art import run_prior_art
        layer = _Layer({}, uses_graph=True, backend_name="serena")   # table empty → no hits
        res = run_prior_art("a", ["nothing_here"], layer=layer)
        self.assertTrue(res.ran)
        self.assertEqual(res.implementations, [])
        self.assertEqual(res.decisions, [])
        self.assertIn("no prior art found", res.note)
        self.assertIn(res.tier, res.note)              # names the tier looked through

    def test_round_trips(self):
        from mokata.prior_art import run_prior_art, PriorArtResult
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        res = run_prior_art("a", ["retry"], layer=layer, recall=_recall_one)
        back = PriorArtResult.from_dict(res.to_dict())
        self.assertEqual(back.ran, res.ran)
        self.assertEqual(back.tier, res.tier)
        self.assertEqual(len(back.implementations), len(res.implementations))
        self.assertEqual(len(back.decisions), len(res.decisions))


# ================================================================ the step-ran gate
class TestPriorArtGate(unittest.TestCase):

    def test_gate_refuses_when_step_not_run(self):
        from mokata.govern.prior_art_gate import check_prior_art_ran
        gate = check_prior_art_ran(ran=False)
        self.assertTrue(gate.refused)
        self.assertIn("REFUSED", gate.render())
        self.assertIn("prior-art", gate.render().lower())

    def test_gate_allows_when_step_ran(self):
        from mokata.govern.prior_art_gate import check_prior_art_ran
        self.assertFalse(check_prior_art_ran(ran=True, tier="semantic").refused)

    def test_gate_allows_empty_findings(self):
        # ran with ZERO findings is a pass — the check is that you LOOKED, not that you found.
        from mokata.govern.prior_art_gate import check_prior_art_ran
        self.assertFalse(check_prior_art_ran(ran=True, tier="lexical").refused)


# ---------------------------------------------------------------- session helper
def _lensed_session(layer):
    s = BrainstormSession("add a retry helper")
    s.propose_approaches([
        Approach("a", "write a new retry loop", pros=["simple"], cons=["dup"], targets=["retry"]),
        Approach("b", "extend utils.retry", pros=["reuse"], cons=["coupling"], targets=["retry"]),
    ])
    s.assess_impacts(layer=layer)
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    return s


# ================================================================ the approve bound step
class TestApproveBoundStep(unittest.TestCase):

    def test_gr_pa_regression(self):
        """approach approval WITHOUT the prior-art step ⇒ REFUSED with the named message. Fails on
        pre-GR-PA code (which had no prior-art awareness and approved happily)."""
        from mokata.govern.prior_art_gate import brainstorm_prior_art_gate
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        s = _lensed_session(layer)                      # lenses on the table, but NO prior-art run
        gate = brainstorm_prior_art_gate(s, "a")
        self.assertTrue(gate.refused)
        with self.assertRaises(BrainstormGateError) as ctx:
            s.approve("jas", "a", prior_art_gate=gate)
        msg = str(ctx.exception)
        self.assertIn("REFUSED", msg)
        self.assertIn("prior-art", msg.lower())
        self.assertFalse(s.approved)

    def test_step_ran_then_approval_proceeds_with_evidence_in_run_state(self):
        from mokata.govern.prior_art_gate import brainstorm_prior_art_gate
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        s = _lensed_session(layer)
        s.assess_prior_art(layer=layer, recall=_recall_one)     # the step RUNS
        gate = brainstorm_prior_art_gate(s, "a")
        self.assertFalse(gate.refused)
        s.approve("jas", "a", prior_art_gate=gate)              # proceeds
        self.assertTrue(s.approved)
        # evidence recorded in run state: ran / tier / finding-count.
        ev = s.prior_art["a"]
        self.assertTrue(ev.ran)
        self.assertTrue(ev.tier)
        self.assertGreaterEqual(len(ev.implementations), 1)

    def test_no_gate_is_byte_identical_old_behavior(self):
        # a legacy caller passing no prior_art_gate approves exactly as before (graph.required=false
        # path / pre-GR-PA callers) — additive, non-breaking.
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        s = _lensed_session(layer)
        s.approve("jas", "a")
        self.assertTrue(s.approved)

    def test_evidence_persists_in_session_dict(self):
        from mokata.brainstorm import BrainstormSession as BS
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        s = _lensed_session(layer)
        s.assess_prior_art(layer=layer, recall=_recall_one)
        d = s.to_dict()
        self.assertIn("prior_art", d)
        self.assertIn("a", d["prior_art"])
        self.assertTrue(d["prior_art"]["a"]["ran"])
        # restore keeps the evidence (the step-ran gate stays satisfied on resume).
        s2 = BS.from_dict(d)
        self.assertTrue(s2.prior_art_ran("a"))


# ================================================================ tradeoff-table render
class TestTradeoffTableRender(unittest.TestCase):

    def test_findings_rendered_as_extend_row(self):
        from mokata.prior_art import run_prior_art, render_prior_art
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        res = run_prior_art("a", ["retry"], layer=layer)
        out = render_prior_art([res])
        self.assertIn("existing", out)
        self.assertIn("utils/retry.py", out)           # mokata-rendered symbol/file, not paraphrase
        self.assertIn("extend", out)

    def test_empty_findings_row_is_honest(self):
        from mokata.prior_art import run_prior_art, render_prior_art
        layer = _Layer({}, uses_graph=True, backend_name="serena")
        res = run_prior_art("a", ["nothing"], layer=layer)
        out = render_prior_art([res])
        self.assertIn("no prior art found", out)
        self.assertIn(res.tier, out)

    def test_design_writeup_includes_prior_art(self):
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        s = _lensed_session(layer)
        s.assess_prior_art(layer=layer)
        writeup = s.design_writeup()
        self.assertIn("Prior art", writeup)
        self.assertIn("extend", writeup.lower())


# ================================================================ tier honesty + no duplicate refusal
class TestTierHonestyNoDuplicateRefusal(unittest.TestCase):

    def test_crg_adopted_says_semantic(self):
        from mokata.prior_art import run_prior_art, TIER_SEMANTIC, render_prior_art
        layer = _Layer(_TABLE, uses_graph=True, backend_name="code-review-graph",
                       supports_semantic=True)
        res = run_prior_art("a", ["retry"], layer=layer)
        self.assertEqual(res.tier, TIER_SEMANTIC)
        self.assertIn("semantic", render_prior_art([res]))

    def test_ast_only_says_name_resolution(self):
        from mokata.prior_art import run_prior_art, TIER_NAME_RESOLUTION
        layer = _Layer(_TABLE, uses_graph=False, backend_name="ast")
        res = run_prior_art("a", ["retry"], layer=layer)
        self.assertEqual(res.tier, TIER_NAME_RESOLUTION)

    def test_degraded_graph_adds_no_duplicate_refusal(self):
        """A degraded/absent graph still RUNS the prior-art step (evidence-gathering); the prior-art
        gate NEVER refuses on graph quality — GR.S3 owns that refusal, this stage duplicates none."""
        from mokata.prior_art import run_prior_art
        from mokata.govern.prior_art_gate import brainstorm_prior_art_gate
        layer = _Layer(_TABLE, uses_graph=False, backend_name="grep", degraded=True)   # grep floor
        s = _lensed_session(layer)
        s.assess_prior_art(layer=layer, recall=_recall_one)
        gate = brainstorm_prior_art_gate(s, "a")
        self.assertFalse(gate.refused)                 # degraded, but the STEP RAN → not refused here
        s.approve("jas", "a", prior_art_gate=gate)
        self.assertTrue(s.approved)
        # the step itself records the degraded tier honestly, it just does not refuse.
        self.assertTrue(s.prior_art["a"].degraded)


# ================================================================ budget: bounded, single channel
class TestBudgetSingleChannel(unittest.TestCase):

    def test_queries_are_bounded_top_n(self):
        from mokata.prior_art import run_prior_art
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        many = [f"sym{i}" for i in range(20)]
        res = run_prior_art("a", many, layer=layer, top_n=3)
        structural = [c for c in layer.calls if c[0] in ("implementers", "callers", "semantic")]
        self.assertLessEqual(len(structural), 3)       # bounded top-N, no unbounded fan-out
        self.assertLessEqual(len(res.implementations), 3)

    def test_recall_is_the_single_shared_channel(self):
        # the recall channel is called ONCE for the approach (a shared query), not per-item —
        # no second injection channel.
        from mokata.prior_art import run_prior_art
        calls = []

        def _recall(q):
            calls.append(q)
            return [_Item("d1", "prior decision", about_code=["retry"])]

        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        run_prior_art("a", ["retry"], layer=layer, recall=_recall, top_n=3)
        self.assertEqual(len(calls), 1)

    def test_module_opens_no_second_retrieval_channel(self):
        # structural assert: the pure step opens NO retrieval surface of its own — it uses the
        # injected layer + recall callable only (shares the existing budget, no new channel).
        import mokata.prior_art as pa
        src = _read_source(pa)
        self.assertNotIn("MemoryStore", src)
        self.assertNotIn("KnowledgeLayer(", src)
        self.assertNotIn("recall_relevant(", src)      # the pure module never calls recall itself


def _read_source(mod):
    import inspect
    return inspect.getsource(mod)


# ================================================================ MCP parity twin
class TestMcpParity(unittest.TestCase):
    """The refusal behaves IDENTICALLY through the MCP loop as through the CLI: the MCP loop restores
    the persisted brainstorm and computes the SAME step-ran verdict via the shared helper, so a
    persisted session whose chosen approach never ran prior-art is refused at approval either way."""

    def test_restored_session_refuses_without_prior_art(self):
        from mokata.govern.prior_art_gate import brainstorm_prior_art_gate
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
            s = _lensed_session(layer)
            save_brainstorm_progress(s, surface.state)
            restored = restore_brainstorm_progress(surface.state)     # the MCP-loop read
            gate = brainstorm_prior_art_gate(restored, "a")
            self.assertTrue(gate.refused)
            with self.assertRaises(BrainstormGateError):
                restored.approve("jas", "a", prior_art_gate=gate)

    def test_restored_session_proceeds_when_prior_art_ran(self):
        from mokata.govern.prior_art_gate import brainstorm_prior_art_gate
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
            s = _lensed_session(layer)
            s.assess_prior_art(layer=layer, recall=_recall_one)
            save_brainstorm_progress(s, surface.state)
            restored = restore_brainstorm_progress(surface.state)
            gate = brainstorm_prior_art_gate(restored, "a")
            self.assertFalse(gate.refused)
            restored.approve("jas", "a", prior_art_gate=gate)
            self.assertTrue(restored.approved)


# ================================================================ dormant AP-SD decisions[] hook
class TestDormantApSdHook(unittest.TestCase):
    """decisions[] recall-enrichment activates ONLY when AP-SD's structured decisions are supplied
    (capability check, GR.S2(m) / review_graph precedent). Until AP-SD ships, recall works over
    current memory items only — the dormant path."""

    def test_decisions_none_is_dormant(self):
        from mokata.prior_art import run_prior_art
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        res = run_prior_art("a", ["retry"], layer=layer, recall=_recall_one, decisions=None)
        self.assertTrue(all(d.source != "decisions[]" for d in res.decisions))

    def test_decisions_supplied_enriches(self):
        from mokata.prior_art import run_prior_art
        layer = _Layer(_TABLE, uses_graph=True, backend_name="serena")
        structured = [{"id": "AP1", "statement": "retry lives in utils.retry",
                       "about_code": ["retry"]}]
        res = run_prior_art("a", ["retry"], layer=layer, recall=None, decisions=structured)
        self.assertTrue(any(d.source == "decisions[]" for d in res.decisions))
        enriched = [d for d in res.decisions if d.source == "decisions[]"][0]
        self.assertIn("retry", enriched.matched)


# ================================================================ graph.required=false still runs
class TestRunsRegardlessOfGraphRequired(unittest.TestCase):

    def test_prior_art_runs_even_with_no_graph(self):
        # prior-art is evidence-gathering, not the refusal gate: it runs on the absent tier and the
        # gate passes because the STEP RAN (graph.required governs GR.S3's radius, never this step).
        from mokata.prior_art import run_prior_art
        from mokata.govern.prior_art_gate import check_prior_art_ran
        res = run_prior_art("a", ["retry"], layer=None, recall=_recall_one)
        self.assertTrue(res.ran)
        self.assertFalse(check_prior_art_ran(ran=res.ran, tier=res.tier).refused)


# ---------------------------------------------------------------- init helper
def _init(root):
    from mokata.init import init_repo
    from mokata.config import Surface
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(root)


if __name__ == "__main__":
    unittest.main()
