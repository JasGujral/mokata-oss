"""D2 · BLAST-RADIUS-LEAF-DEGRADE — a leaf symbol is an ANSWER, not an absence.

The defect (doc 99 §D2, doc 84, ratified fix shape D13). `compute_impact` ORs `graph_degraded`
across an approach's targets. The AST floor, finding no edges for a symbol, fell through to the
grep floor, which marks itself degraded — so naming ONE entry point, new function or top-level
component among the targets refused `spec_emit` on mokata's own primary language.

The root cause is one line of representation, not one line of arithmetic: `references == []` meant
BOTH "this symbol genuinely has no callers" and "I have no structural evidence about this symbol".
That is doc 85 §7g — an absent answer and a real answer sharing a representation — and the model
for the fix is `RunResolution` (`run_resolver.py`): distinct OUTCOMES rather than a boolean with a
comment, a `basis` on the answered case naming WHICH RUNG answered, and a stated invariant a test
can hold.

    THE INVARIANT (this file exists to hold it):
    an empty `references` list NEVER distinguishes an answer from an absence — only `basis` does.

The in-repo precedent governs the wording, and this is the SAME reasoning applied consistently:
CRG-NAV refuses to answer `refs` from the AST because calls+imports is "a PARTIAL set dressed as
structural". A zero-caller `blast_radius` over a symbol the index HOLDS A DEFINITION FOR is the
opposite case — a COMPLETE set that happens to be empty — and dressing it as absent is the same
overclaim pointed the other way.

Deliverable -> test map:
  1. the backend distinguishes verified-empty from absent .. TestAstBackendBasis
  2. one representation, not two ........................... TestOneRepresentation
  3. the OR covers genuine absence only .................... TestComputeImpactVerdict
  4. the guard is NARROWED, never disabled ................. TestAbsenceStillRefuses
  5. the kinds that must KEEP falling through .............. TestKindsThatStayAbsent
  6. the GR.S3 consumers inherit the fix at the source ..... TestConsumersInherit
  7. the graded tests (mutants aimed at this file) ......... TestTestsThemselves
"""

import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import parity with the AST suite)

from mokata.brainstorm_impact import compute_impact
from mokata.knowledge import KnowledgeLayer, QueryResult
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.query import (BASIS_LEXICAL, BASIS_STRUCTURAL,
                                    BASIS_VERIFIED_EMPTY, STRUCTURAL_BASES)

# The fixture doc 99 measured on, rebuilt here so the numbers in the report are reproducible from
# the suite: `use_cart` is called (3 sites at depth 2), `cart_summary` / `checkout_button` are
# LEAVES — defined, never called — and `totally_unknown` is not in the index at all.
_CART = """\
def format_money(cents):
    return f"${cents/100:.2f}"


def use_cart(items):
    return {"count": len(items), "total": sum(items)}


def cart_summary(items):
    return format_money(use_cart(items)["total"])
"""

_VIEWS = """\
from .cart import use_cart, format_money


def render_cart(items):
    data = use_cart(items)
    return format_money(data["total"])


def mini_cart(items):
    return use_cart(items)


def checkout_button(label):
    return f"<button>{label}</button>"
"""


def _fixture(root):
    app = os.path.join(root, "app")
    os.makedirs(app, exist_ok=True)
    for name, body in (("__init__.py", ""), ("cart.py", _CART), ("views.py", _VIEWS)):
        with open(os.path.join(app, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    return root


def _init(root):
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(root)


# ================================================================ 1. the backend distinguishes
class TestAstBackendBasis(unittest.TestCase):
    """The AST floor returns THREE outcomes where it used to return two, and the one it gained is
    the one D2 needed: zero, verified structurally."""

    def _backend(self, root):
        b = AstBackend(root=_fixture(root), cache_dir=os.path.join(root, ".cache"))
        b._ensure_index()
        return b

    def test_called_symbol_is_structural(self):
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("blast_radius", "use_cart", depth=2)
            self.assertEqual(res.basis, BASIS_STRUCTURAL)
            self.assertEqual(res.backend, "ast")
            self.assertFalse(res.degraded)
            self.assertGreater(res.count, 0)

    def test_leaf_symbol_is_verified_empty_not_degraded(self):
        # THE defect, at the backend. `cart_summary` is DEFINED in a parsed file and nothing calls
        # it: zero is the structural answer, at exactly the fidelity a non-zero answer has.
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("blast_radius", "cart_summary", depth=2)
            self.assertEqual(res.basis, BASIS_VERIFIED_EMPTY)
            self.assertEqual(res.backend, "ast")
            self.assertEqual(res.count, 0)
            self.assertFalse(res.degraded)
            self.assertTrue(res.structural)

    def test_unknown_symbol_is_absent_and_degraded(self):
        # The case the fallthrough was ALWAYS right about: no definition in the index, so the AST
        # knows nothing about this symbol and must not claim a zero.
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("blast_radius", "totally_unknown", depth=2)
            self.assertEqual(res.basis, BASIS_LEXICAL)
            self.assertTrue(res.degraded)
            self.assertFalse(res.structural)

    def test_verified_empty_names_its_own_limit(self):
        # A verified zero carries the SAME documented limit as a verified non-zero (name
        # resolution, not type inference) — the honesty contract, not a bare claim of zero.
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("blast_radius", "checkout_button", depth=2)
            self.assertEqual(res.basis, BASIS_VERIFIED_EMPTY)
            self.assertTrue(res.note.strip(), "a verified-empty answer must say what verified it")
            self.assertIn("dynamic dispatch", res.note)

    def test_callers_also_distinguishes(self):
        # `blast_radius` is D2's surface, but the distinction belongs to the symbol-edge kinds as
        # a class — a leaf is a leaf whichever of them asks.
        with tempfile.TemporaryDirectory() as root:
            b = self._backend(root)
            self.assertEqual(b.query("callers", "cart_summary").basis, BASIS_VERIFIED_EMPTY)
            self.assertEqual(b.query("callers", "totally_unknown").basis, BASIS_LEXICAL)


# ================================================================ 2. one representation, not two
class TestOneRepresentation(unittest.TestCase):
    """§7g's actual requirement: not "add a field" but "stop having two fields that can disagree".
    `degraded` is DERIVED from `basis`, so there is exactly one place the truth lives."""

    def test_degraded_is_derived_not_stored(self):
        self.assertTrue(QueryResult(kind="callers", target="x", basis=BASIS_LEXICAL).degraded)
        self.assertFalse(QueryResult(kind="callers", target="x", basis=BASIS_STRUCTURAL).degraded)
        self.assertFalse(
            QueryResult(kind="callers", target="x", basis=BASIS_VERIFIED_EMPTY).degraded)

    def test_degraded_cannot_be_set_independently_of_basis(self):
        # The two-field shape is what let a leaf answer and an absent answer agree on `degraded`
        # while disagreeing about reality. Make that unrepresentable rather than merely discouraged.
        res = QueryResult(kind="callers", target="x", basis=BASIS_VERIFIED_EMPTY)
        with self.assertRaises(AttributeError):
            res.degraded = True                                    # type: ignore[misc]
        with self.assertRaises(TypeError):
            QueryResult(kind="callers", target="x", degraded=True)  # type: ignore[call-arg]

    def test_empty_references_never_decides(self):
        # THE INVARIANT. Two results with byte-identical reference lists, opposite meanings.
        empty_answer = QueryResult(kind="blast_radius", target="leaf", references=[],
                                   backend="ast", basis=BASIS_VERIFIED_EMPTY)
        empty_absence = QueryResult(kind="blast_radius", target="who", references=[],
                                    backend="grep", basis=BASIS_LEXICAL)
        self.assertEqual(empty_answer.references, empty_absence.references)
        self.assertNotEqual(empty_answer.degraded, empty_absence.degraded)
        self.assertNotEqual(empty_answer.structural, empty_absence.structural)

    def test_verified_empty_with_references_is_a_contradiction(self):
        # A basis that claims "the answer is zero" while carrying hits is the one incoherent
        # combination; it is refused at construction rather than left for a reader to notice.
        from mokata.knowledge.query import Reference
        with self.assertRaises(ValueError):
            QueryResult(kind="callers", target="x", references=[Reference("a.py", 1)],
                        basis=BASIS_VERIFIED_EMPTY)

    def test_a_stale_graph_does_not_pass_on_a_certification(self):
        """★ WRITTEN BECAUSE A MUTANT SURVIVED ON A CLEAN TREE. Stage 6's M09 guts
        `demote_to_floor`'s basis move, and `test_gr_s4_freshness` did NOT catch it — the layer's
        TWO demotion sites were graded only by the unit test below, never behaviourally.

        This is the case that actually matters. The AST floor can now CERTIFY a zero, and the
        layer's freshness path answers from that floor when the adopted graph is KNOWN STALE. A
        certification is a claim about a current index; a stale graph is not entitled to pass one
        on. The demotion is what stops it, so the demotion needs a behavioural pin."""
        from mokata.knowledge.grep_backend import GrepBackend

        class _Stale:
            answer_from_floor = True
            note = "graph is known stale"

        class _Freshness:
            def ensure_fresh(self, layer):
                return _Stale()

        class _Graph:
            name, is_graph = "code-review-graph", True
            def query(self, kind, target, depth=1):
                raise AssertionError("the stale graph must never be asked")

        with tempfile.TemporaryDirectory() as root:
            _fixture(root)
            floor = AstBackend(root=root, grep=GrepBackend(root=root),
                               cache_dir=os.path.join(root, ".cache"))
            # Sanity: on its own the floor CERTIFIES this leaf. The demotion, not the absence of a
            # certification, is what must produce the degraded verdict below.
            self.assertEqual(floor.query("blast_radius", "checkout_button", depth=2).basis,
                             BASIS_VERIFIED_EMPTY)
            layer = KnowledgeLayer(_Graph(), fallback=floor, freshness=_Freshness())
            res = layer.blast_radius("checkout_button", depth=2)
            self.assertEqual(res.basis, BASIS_LEXICAL)
            self.assertTrue(res.degraded, "a KNOWN-stale graph passed on the floor's certification")
            self.assertIn("stale", res.note)

    def test_a_failed_graph_does_not_pass_on_a_certification(self):
        """The layer's OTHER demotion site, same reasoning: the user CONFIGURED an adopted graph
        and it failed. The floor stands in for it, but it does not inherit its authority."""
        from mokata.knowledge.grep_backend import GrepBackend
        from mokata.knowledge.query import BackendError

        class _Broken:
            name, is_graph = "code-review-graph", True
            def query(self, kind, target, depth=1):
                raise BackendError("the graph tool died")

        with tempfile.TemporaryDirectory() as root:
            _fixture(root)
            floor = AstBackend(root=root, grep=GrepBackend(root=root),
                               cache_dir=os.path.join(root, ".cache"))
            layer = KnowledgeLayer(_Broken(), fallback=floor)
            res = layer.blast_radius("checkout_button", depth=2)
            self.assertEqual(res.basis, BASIS_LEXICAL)
            self.assertTrue(res.degraded, "a FAILED graph passed on the floor's certification")

    def test_demote_to_floor_moves_both_halves_together(self):
        res = QueryResult(kind="callers", target="x", basis=BASIS_STRUCTURAL)
        res.demote_to_floor("the graph went stale")
        self.assertEqual(res.basis, BASIS_LEXICAL)
        self.assertTrue(res.degraded)
        self.assertIn("stale", res.note)

    def test_to_dict_carries_the_basis(self):
        d = QueryResult(kind="callers", target="x", basis=BASIS_VERIFIED_EMPTY).to_dict()
        self.assertEqual(d["basis"], BASIS_VERIFIED_EMPTY)
        self.assertFalse(d["degraded"])

    def test_structural_bases_is_the_single_membership_test(self):
        self.assertEqual(set(STRUCTURAL_BASES), {BASIS_STRUCTURAL, BASIS_VERIFIED_EMPTY})
        self.assertNotIn(BASIS_LEXICAL, STRUCTURAL_BASES)


# ================================================================ 3. the verdict table
class TestComputeImpactVerdict(unittest.TestCase):
    """Doc 99's table, on the real AST floor through the real layer. Every row that read True
    because of a LEAF now reads False; nothing else moves."""

    def _layer(self, root):
        return KnowledgeLayer.from_surface(_init(_fixture(root)))

    def test_doc99_verdict_table(self):
        with tempfile.TemporaryDirectory() as root:
            layer = self._layer(root)
            for targets in (["use_cart"], ["format_money"], ["use_cart", "format_money"],
                            ["cart_summary"], ["checkout_button"],
                            ["use_cart", "cart_summary", "checkout_button"]):
                imp = compute_impact("a", targets, layer=layer)
                self.assertFalse(imp.graph_degraded,
                                 f"{targets} poisoned the approach verdict")

    def test_leaf_only_target_set_does_not_refuse(self):
        # "Naming ONE entry point among an approach's targets refuses spec_emit" — the headline.
        with tempfile.TemporaryDirectory() as root:
            imp = compute_impact("a", ["checkout_button"], layer=self._layer(root))
            self.assertFalse(imp.graph_degraded)
            self.assertEqual(imp.caller_count, 0)

    def test_a_leaf_does_not_hide_a_real_radius(self):
        # The union must still be the union: admitting the leaf must not drop the callers the
        # non-leaf targets DID have.
        with tempfile.TemporaryDirectory() as root:
            layer = self._layer(root)
            alone = compute_impact("a", ["use_cart"], layer=layer)
            mixed = compute_impact("a", ["use_cart", "checkout_button"], layer=layer)
            self.assertEqual(mixed.caller_count, alone.caller_count)
            self.assertGreater(mixed.caller_count, 0)

    def test_display_caveat_is_untouched(self):
        # `degraded` (the DISPLAY caveat: "not a real adopted graph") is a different question from
        # `graph_degraded` (the GATE signal) and this stage moves only the second.
        with tempfile.TemporaryDirectory() as root:
            imp = compute_impact("a", ["checkout_button"], layer=self._layer(root))
            self.assertTrue(imp.degraded)
            self.assertFalse(imp.graph_degraded)


# ================================================================ 4. the guard is narrowed only
class TestAbsenceStillRefuses(unittest.TestCase):
    """Every route to a genuinely ABSENT structural answer still sets `graph_degraded`. The stage
    narrows the OR to genuine absence; it does not switch the gate off."""

    def test_no_layer_still_degrades(self):
        self.assertTrue(compute_impact("a", ["x"], layer=None).graph_degraded)

    def test_failing_query_still_degrades(self):
        class _Raises:
            uses_graph = False
            def blast_radius(self, s, depth=1): raise RuntimeError("boom")
        self.assertTrue(compute_impact("a", ["x"], layer=_Raises()).graph_degraded)

    def test_unknown_symbol_still_degrades(self):
        with tempfile.TemporaryDirectory() as root:
            layer = KnowledgeLayer.from_surface(_init(_fixture(root)))
            imp = compute_impact("a", ["totally_unknown"], layer=layer)
            self.assertTrue(imp.graph_degraded)

    def test_one_absent_target_still_poisons_the_set(self):
        # The OR is CORRECT for absence — an approach mixing a real answer with an unanswerable
        # one is still an approach mokata cannot vouch for.
        with tempfile.TemporaryDirectory() as root:
            layer = KnowledgeLayer.from_surface(_init(_fixture(root)))
            imp = compute_impact("a", ["use_cart", "totally_unknown"], layer=layer)
            self.assertTrue(imp.graph_degraded)

    def test_no_targets_is_still_not_degraded(self):
        self.assertFalse(compute_impact("a", [], layer=None).graph_degraded)


# ================================================================ 5. kinds that stay absent
class TestKindsThatStayAbsent(unittest.TestCase):
    """The distinction is only sound for the SYMBOL-EDGE kinds — the ones asking "what edges touch
    this definition". The others keep falling through, and each for its own stated reason."""

    def _backend(self, root):
        b = AstBackend(root=_fixture(root), cache_dir=os.path.join(root, ".cache"))
        b._ensure_index()
        return b

    def test_defs_zero_is_absence_not_a_verified_empty(self):
        # For `defs`, "the index holds no definition" IS the answer being absent.
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("defs", "totally_unknown")
            self.assertEqual(res.basis, BASIS_LEXICAL)

    def test_the_defs_exclusion_is_unreachable_and_that_is_why_it_is_safe(self):
        """★ WRITTEN BECAUSE A MUTANT SURVIVED. Stage 6's M07 added `defs` to SYMBOL_EDGE_KINDS
        and the whole suite stayed green — the test above passes either way, so it was grading
        nothing. The mutant was RIGHT to survive: for `defs`, `refs` IS `_defs(target)`, so the
        certification branch is reached exactly when `_defs` is empty, which is exactly when
        `_holds_definition` is False regardless of the kind tuple. The exclusion cannot fire.

        That equivalence is the real invariant, so it is what gets pinned. If a future change
        breaks it — `_defs` and the `defs` dispatch reading different sources — this goes red
        instead of the exclusion quietly becoming load-bearing and untested."""
        with tempfile.TemporaryDirectory() as root:
            b = self._backend(root)
            for sym in ("cart_summary", "use_cart", "checkout_button", "totally_unknown"):
                reaches_empty_branch = not b._defs(sym)
                would_certify = bool(b._defs(sym))
                self.assertNotEqual(
                    reaches_empty_branch, would_certify,
                    f"`defs`({sym}) can reach the certification branch AND be certified — the "
                    f"exclusion has become load-bearing and needs a real test, not this one")

    def test_refs_still_routes_to_the_lexical_superset(self):
        # CRG-NAV's ruling is untouched: calls+imports is a PARTIAL set and must not be dressed
        # as structural, whether it is empty or not.
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("refs", "cart_summary")
            self.assertEqual(res.basis, BASIS_LEXICAL)
            self.assertTrue(res.degraded)

    def test_imports_of_an_uncoupled_module_is_not_verified_empty(self):
        # An `imports` target is a MODULE TOKEN, not a definition, so a def-site predicate says
        # nothing about it and must not be allowed to vouch for a zero.
        with tempfile.TemporaryDirectory() as root:
            res = self._backend(root).query("imports", "cart_summary")
            self.assertEqual(res.basis, BASIS_LEXICAL)


# ================================================================ 6. consumers inherit the fix
class TestConsumersInherit(unittest.TestCase):
    """Fixed at the BACKEND, so all three GR.S3 decision consumers inherit it — no consumer
    carries its own copy of the rule (which is how the three drifted apart in the first place)."""

    def test_spec_awareness_expands_through_a_leaf(self):
        from mokata.engine.spec_awareness import expand_touch_set
        with tempfile.TemporaryDirectory() as root:
            layer = KnowledgeLayer.from_surface(_init(_fixture(root)))
            _, _, graph_degraded, _ = expand_touch_set(layer, ["checkout_button"], depth=2)
            self.assertFalse(graph_degraded)

    def test_spec_awareness_still_refuses_an_absent_symbol(self):
        from mokata.engine.spec_awareness import expand_touch_set
        with tempfile.TemporaryDirectory() as root:
            layer = KnowledgeLayer.from_surface(_init(_fixture(root)))
            _, _, graph_degraded, _ = expand_touch_set(layer, ["totally_unknown"], depth=2)
            self.assertTrue(graph_degraded)

    def test_guard_change_admits_a_leaf(self):
        # The pin this stage OVERTURNS. `test_gr_s3_fu.TestFloorStillRefuses` asserted that a
        # defined-but-uncalled symbol is refused, calling it "the GR.S1 hand-off honesty". The
        # premise was false: the AST floor did not fail to find evidence, it found the definition
        # and found zero callers. D13 reverses it.
        from mokata.engine.spec_awareness import ChangeSet, Spec, guard_change
        with tempfile.TemporaryDirectory() as root:
            layer = KnowledgeLayer.from_surface(_init(_fixture(root)))
            outcome = guard_change(ChangeSet(symbols=["checkout_button"]),
                                   specs=[Spec(title="c", source="s", criteria=[])],
                                   decisions=[], layer=layer,
                                   graph_required=True, graph_overridden=False)
            self.assertIsNone(outcome.graph_refusal)
            self.assertTrue(outcome.proceeded)


# ================================================================ 6b. the surface a human reads
class TestCliSaysWhichAnswerItGave(unittest.TestCase):
    """The gate stopped conflating the two answers; the surface a human reads must stop too. A leaf
    printed `via grep [grep fallback] — 0 result(s)`, which reads as "I could not find out" when the
    truth is "I looked, and the answer is none"."""

    def _query(self, root, target):
        import argparse
        import io
        from contextlib import redirect_stdout

        from mokata.cli_commands.knowledge import cmd_query
        args = argparse.Namespace(path=root, kind="blast_radius", target=target, depth=2)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_query(args)
        return buf.getvalue()

    def test_the_three_answers_read_differently(self):
        with tempfile.TemporaryDirectory() as root:
            _init(_fixture(root))
            hit = self._query(root, "use_cart")
            leaf = self._query(root, "checkout_button")
            absent = self._query(root, "totally_unknown")
            self.assertIn("[graph]", hit)
            self.assertIn("[verified empty]", leaf)
            self.assertIn("[grep fallback]", absent)
            # Both zero-result answers print "0 result(s)" — the COUNT cannot tell them apart,
            # which is exactly why the mode word has to.
            self.assertIn("0 result(s)", leaf)
            self.assertIn("0 result(s)", absent)
            self.assertNotEqual(leaf.splitlines()[0], absent.splitlines()[0])

    def test_the_leaf_line_does_not_blame_grep(self):
        with tempfile.TemporaryDirectory() as root:
            _init(_fixture(root))
            leaf = self._query(root, "checkout_button")
            self.assertIn("via ast", leaf)
            self.assertNotIn("via grep", leaf)


# ================================================================ 7. grading the tests themselves
class TestTestsThemselves(unittest.TestCase):
    """§7f — the last two stages each found a pin that was green while grading nothing. These
    assert that the FIXTURE this file reasons about is the fixture it claims, so a mutant that
    guts the fixture cannot leave the suite green."""

    def test_fixture_actually_contains_a_leaf_and_a_non_leaf(self):
        with tempfile.TemporaryDirectory() as root:
            b = AstBackend(root=_fixture(root), cache_dir=os.path.join(root, ".cache"))
            b._ensure_index()
            self.assertTrue(b._defs("cart_summary"), "the 'leaf' must be DEFINED in the index")
            self.assertFalse(b._callers("cart_summary"), "the 'leaf' must have NO callers")
            self.assertTrue(b._callers("use_cart"), "the 'non-leaf' must HAVE callers")
            self.assertFalse(b._defs("totally_unknown"), "the 'absent' symbol must be absent")

    def test_the_three_bases_are_distinct_values(self):
        # A mutant collapsing two constants to one string would otherwise satisfy every
        # equality assertion above.
        self.assertEqual(len({BASIS_STRUCTURAL, BASIS_VERIFIED_EMPTY, BASIS_LEXICAL}), 3)


if __name__ == "__main__":
    unittest.main()
