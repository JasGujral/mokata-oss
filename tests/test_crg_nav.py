"""CRG-NAV — route ALL code navigation through the code-review-graph chain (0.0.16).

The gap this closes was NOT missing capability. The navigation ops existed (`crg_client.py` maps
callers/callees/imports/implementers; CRG is auto-adopted through the GR.S2 chain) and the layer
already degraded CRG -> serena -> AST -> grep. What was missing:

  (a) the skill PROSE never stated an ORDER. develop/refine/debug/optimize said "run structural
      queries" alongside "read or grep the code" as peers, with no rule that the graph goes FIRST
      and no instruction to mark a lexical answer degraded — so an agent with a live CRG index
      still reached for Read/grep;
  (b) two navigation intents the rule names — "where is this symbol DEFINED" and "everywhere it
      is REFERENCED" — had NO op behind them at all: `QUERY_KINDS` carried no `defs`/`refs`, so
      the prose would have been a promise with no instrument;
  (d) the grep floor said "lexical fallback ... approximate" but never named the ONE step that
      buys the full chain, and a graph that simply has no MAPPING for a kind was indistinguishable
      from a graph that had FAILED.

What is under test:
  1. the navigation rule is SINGLE-SOURCED (`NAVIGATION_GRAPH_FIRST`) and reaches develop /
     refine / debug / optimize + every grounded skill through the ONE grounding block — no
     per-skill copies, and the shipped templates + SKILL.md carry it;
  2. every navigation intent named in the prose has a real op: `defs` and `refs` answer through
     `mokata query` / the `query` MCP tool on the graph, the AST floor and the grep floor;
  3. the degrade binds to THE CHAIN: an unmapped kind routes to the floor with an honest note
     (not a failure alarm), and a lexical navigation answer carries the floor note;
  4. NEGATIVES — impact (`blast_radius`) is untouched, non-navigation answers are byte-identical,
     no second freshness path and no new MCP tool.

Business-level asserts: what an agent/user actually observes — the prose it is handed, the
`QueryResult` a query returns, the note printed on the answer.
"""

import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import crg_client, graph_adopt
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.graph_backend import CodeReviewGraphBackend
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.layer import KnowledgeLayer
from mokata.knowledge.query import (GREP_FLOOR_NAV_NOTE, NAVIGATION_KINDS, PREFERRED_GRAPH_TOOL,
                                    QUERY_KINDS, BackendError)
from mokata.skills import (GROUNDING_DISCIPLINE, GROUNDING_MARKER, NAVIGATION_GRAPH_FIRST,
                           command_markdown, get_skill, render_skill)

_SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"
_TEMPLATES = _SRC / "templates" / "commands"
_SKILLS = _SRC / "skills"

# The four skills CRG-NAV names, plus the shared grounding block they all pull from.
NAV_SKILLS = ("develop", "refine", "debug", "optimize")


def _repo(d, files):
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    return d


SAMPLE = {
    "svc/core.py": (
        "def target():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Target:\n"
        "    pass\n"
    ),
    "svc/caller.py": (
        "from svc.core import target\n"
        "\n"
        "\n"
        "def uses():\n"
        "    return target()\n"
        "\n"
        "\n"
        "HANDLERS = [target]\n"          # a reference that is NOT a call — refs must see it
    ),
}


# ======================================================================================
# 1 · THE REGRESSION — the prose stated no order, and two intents had no op
# ======================================================================================

class TestCrgNavRegression(unittest.TestCase):

    def test_crg_nav_regression(self):
        """Both halves of the gap, in one test.

        BEFORE (a): the shared grounding block listed the structural queries and "read or grep
        the code" as peers. Reconstruct that world by stripping the navigation rule out of the
        block: what remains states no ORDER — no "FIRST", no "fallback", no instruction to mark
        a lexical answer degraded. That silence IS the defect: an agent reading it had no reason
        to prefer the graph over grep.

        BEFORE (b): the typed API carried five kinds, none of which answered "where is this
        defined" or "everywhere is this referenced" — the two intents the rule leads with.

        AFTER: the rule is in the block, and both intents are kinds."""
        pre_nav = GROUNDING_DISCIPLINE.replace(NAVIGATION_GRAPH_FIRST, "")
        self.assertNotIn("GRAPH-FIRST", pre_nav,
                         "the pre-CRG-NAV grounding block stated no navigation order — that IS "
                         "the gap this row closes")
        self.assertNotIn("Read and grep are the FALLBACK", pre_nav)

        pre_kinds = tuple(k for k in QUERY_KINDS if k not in ("defs", "refs"))
        self.assertEqual(pre_kinds,
                         ("callers", "callees", "implementers", "imports", "blast_radius"),
                         "the pre-CRG-NAV kind set — no defs, no refs")

        # AFTER — the rule is stated, and every intent it names has a kind behind it.
        self.assertIn(NAVIGATION_GRAPH_FIRST, GROUNDING_DISCIPLINE)
        for kind in ("defs", "refs", "callers", "callees", "implementers", "imports"):
            self.assertIn(kind, QUERY_KINDS)


# ======================================================================================
# 2 · (a) THE PROSE — graph-first, stated ONCE, reaching all four skills
# ======================================================================================

class TestNavigationProseIsSingleSourced(unittest.TestCase):

    def test_rule_states_order_fallback_and_degraded_marking(self):
        """The rule is not "use the graph too" — it is an ORDER with a named fallback and an
        honesty obligation. All three must be in the one wording."""
        rule = NAVIGATION_GRAPH_FIRST
        self.assertIn("GRAPH-FIRST", rule)
        self.assertIn("FIRST", rule)
        self.assertIn("FALLBACK", rule)
        self.assertIn("DEGRADED", rule)
        self.assertIn("`mokata query", rule)
        for intent in ("DEFINITION", "CALLERS", "REFERENCED"):
            self.assertIn(intent, rule)

    def test_rule_lives_in_the_one_grounding_block(self):
        """Single-sourced the way the G1/SK.S2 prose is: composed INTO the canonical grounding
        block, so the four skills share one wording instead of four copies."""
        self.assertIn(NAVIGATION_GRAPH_FIRST, GROUNDING_DISCIPLINE)

    def test_every_nav_skill_carries_the_rule_through_the_block(self):
        for name in NAV_SKILLS:
            with self.subTest(skill=name):
                skill = get_skill(name)
                self.assertTrue(skill.ground, f"{name} must carry the grounding discipline")
                self.assertIn(NAVIGATION_GRAPH_FIRST, render_skill(skill))

    def test_no_template_embeds_a_literal_copy_of_the_rule(self):
        """No per-skill drift: a template carries the MARKER, never the wording. If a copy ever
        lands in a template, this fails — which is the whole point of single-sourcing."""
        for name in NAV_SKILLS:
            with self.subTest(skill=name):
                text = (_TEMPLATES / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn(GROUNDING_MARKER, text)
                self.assertNotIn("Navigate GRAPH-FIRST: to find a symbol", text,
                                 f"{name}.md must not embed a copy of the navigation rule")

    def test_shipped_skill_md_carries_the_expanded_rule(self):
        """What the agent is actually handed (the rendered SKILL.md) carries the rule."""
        for name in NAV_SKILLS:
            with self.subTest(skill=name):
                text = (_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("Navigate GRAPH-FIRST", text)
                self.assertIn(GREP_FLOOR_NAV_NOTE, text)

    def test_each_nav_skill_prose_points_at_the_rule_in_its_own_voice(self):
        """Each of the four also names graph-first at the point of use — a pointer, not a
        restatement (the wording still lives in one place)."""
        for name in NAV_SKILLS:
            with self.subTest(skill=name):
                prompt = get_skill(name).prompt
                self.assertIn("GRAPH-FIRST", prompt,
                              f"{name}'s own prose must route navigation graph-first")
                self.assertIn("Grounding discipline", prompt,
                              f"{name} must REFERENCE the single-source rule, not restate it")

    def test_generated_templates_match_their_single_source(self):
        """skills.py -> templates/commands/<n>.md: a stage that edits the source but ships a
        stale template hands the agent the OLD instruction."""
        for name in NAV_SKILLS:
            with self.subTest(skill=name):
                self.assertEqual((_TEMPLATES / f"{name}.md").read_text(encoding="utf-8"),
                                 command_markdown(get_skill(name)))


# ======================================================================================
# 3 · (b) EVERY NAVIGATION INTENT HAS AN OP
# ======================================================================================

class TestEveryNavIntentHasAnOp(unittest.TestCase):

    def test_nav_kinds_are_query_kinds(self):
        for kind in NAVIGATION_KINDS:
            self.assertIn(kind, QUERY_KINDS)

    def test_mcp_query_tool_accepts_the_new_kinds_without_a_new_tool(self):
        """(b) rides the EXISTING `query` tool — its enum derives from QUERY_KINDS, so no second
        MCP tool was added for navigation."""
        from mokata.mcp import registry as REG
        from mokata.mcp.tools_read import QUERY_TOOL_KINDS
        for kind in ("defs", "refs"):
            self.assertIn(kind, QUERY_TOOL_KINDS)
        nav_tools = [t.name for t in REG.TOOLS if t.name in ("defs", "refs", "navigate",
                                                             "find_symbol")]
        self.assertEqual(nav_tools, [], "navigation rides `query`; no new MCP tool")

    def test_cli_query_accepts_the_new_kinds(self):
        from mokata.cli import build_parser
        args = build_parser().parse_args(["query", "defs", "target"])
        self.assertEqual(args.kind, "defs")
        args = build_parser().parse_args(["query", "refs", "target"])
        self.assertEqual(args.kind, "refs")

    def test_grep_floor_answers_defs_and_refs(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            g = GrepBackend(root=d)
            defs = g.query("defs", "target")
            self.assertEqual(sorted({r.path for r in defs.references}),
                             [os.path.join("svc", "core.py")])
            refs = g.query("refs", "target")
            # the lexical superset sees the def, the import, the call AND the bare-name use
            self.assertEqual(sorted({r.path for r in refs.references}),
                             [os.path.join("svc", "caller.py"), os.path.join("svc", "core.py")])
            self.assertGreaterEqual(len(refs.references), 4)

    def test_ast_floor_answers_defs_exactly(self):
        """The AST floor is the most precise `defs` available on a Python repo — and it is NOT
        degraded, because it is a real def edge, not a text match."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            a = AstBackend(root=d, grep=GrepBackend(root=d))
            res = a.query("defs", "target")
            self.assertFalse(res.degraded)
            self.assertEqual([(r.path, r.line) for r in res.references],
                             [(os.path.join("svc", "core.py"), 1)])
            self.assertEqual(res.references[0].metadata.get("kind"), "function")

    def test_ast_floor_refs_delegates_to_the_lexical_superset(self):
        """The AST edge index holds def/call/import edges only — no general name-reference
        index. Answering `refs` from those alone would be a PARTIAL set wearing a structural
        label, so it delegates to the lexical floor and says so."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            a = AstBackend(root=d, grep=GrepBackend(root=d))
            res = a.query("refs", "target")
            self.assertTrue(res.degraded)
            self.assertIn(GREP_FLOOR_NAV_NOTE, res.note)
            # the bare-name reference the AST call/import edges cannot see IS in the answer
            self.assertTrue(any(r.line == 8 and r.path.endswith("caller.py")
                                for r in res.references))

    def test_crg_backs_refs_from_its_own_inbound_patterns(self):
        """`refs` is CRG-backed: the union of the INBOUND patterns the grounded interface
        exposes (who calls it, who imports it, who inherits it) — composed from documented
        patterns, not invented."""
        calls = []

        def call_tool(tool, params):
            calls.append((tool, params["pattern"]))
            return {"results": [{"qualified_name": f"m.{params['pattern']}",
                                 "file_path": "a.py", "line_start": 3}]}

        c = crg_client.CodeReviewGraphClient(call_tool=call_tool)
        rows = c.query("refs", "target", root=".")
        self.assertEqual([p for _t, p in calls],
                         ["callers_of", "importers_of", "inheritors_of"])
        self.assertEqual(len(rows), 3)

    def test_crg_refs_dedupes_a_symbol_seen_by_two_patterns(self):
        def call_tool(_tool, _params):
            return {"results": [{"qualified_name": "m.f", "file_path": "a.py",
                                 "line_start": 3}]}
        c = crg_client.CodeReviewGraphClient(call_tool=call_tool)
        self.assertEqual(len(c.query("refs", "target", root=".")), 1)

    def test_crg_declares_it_cannot_back_defs(self):
        """The ONE navigation intent code-review-graph has no op for. It is declared, not
        guessed at — mapping "where is X defined" onto semantic search would be a fuzzy answer
        wearing a structural label."""
        c = crg_client.CodeReviewGraphClient(call_tool=lambda _t, _p: {})
        self.assertFalse(c.supports_kind("defs"))
        for kind in ("callers", "callees", "imports", "implementers", "refs", "blast_radius"):
            self.assertTrue(c.supports_kind(kind), kind)


# ======================================================================================
# 4 · (d) DEGRADE-CLEAN DOWN THE CHAIN, bound to THE CHAIN
# ======================================================================================

class _FakeClient:
    """A graph client double: answers the kinds it declares, raises on the rest."""

    supports_semantic = False

    def __init__(self, unmapped=(), fail=False):
        self.unmapped = tuple(unmapped)
        self.fail = fail
        self.calls = []

    def supports_kind(self, kind):
        return kind not in self.unmapped

    def query(self, kind, target, root, depth=1):
        self.calls.append(kind)
        if self.fail or kind in self.unmapped:
            raise crg_client.CrgUnavailable(f"no mapping for {kind}")
        return [{"path": "graph.py", "line": 1, "snippet": "", "symbol": target}]


class TestDegradeCleanDownTheChain(unittest.TestCase):

    def _layer(self, root, client):
        return KnowledgeLayer(CodeReviewGraphBackend(name=PREFERRED_GRAPH_TOOL, root=root,
                                                     client=client),
                              AstBackend(root=root, grep=GrepBackend(root=root)))

    def test_unmapped_kind_routes_to_the_floor_without_calling_the_graph(self):
        """A MAPPING gap is not a FAILURE: the graph is never asked, so no recovery attempt is
        burned and no failure notice is raised against a healthy tool."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            client = _FakeClient(unmapped=("defs",))
            res = self._layer(d, client).defs("target")
            self.assertEqual(client.calls, [], "the graph must not be asked a kind it cannot map")
            self.assertIn("exposes no 'defs' op", res.note)
            self.assertIn("floor", res.note)
            # the AST floor answered it EXACTLY — a mapping gap is not a degraded answer
            self.assertFalse(res.degraded)
            self.assertEqual([r.path for r in res.references], [os.path.join("svc", "core.py")])

    def test_a_mapped_kind_still_goes_to_the_graph(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            client = _FakeClient(unmapped=("defs",))
            res = self._layer(d, client).refs("target")
            self.assertEqual(client.calls, ["refs"])
            self.assertFalse(res.degraded)
            self.assertNotIn("exposes no", res.note)

    def test_a_real_graph_failure_still_degrades_down_the_chain(self):
        """The unmapped-kind shortcut does NOT replace the failure path: a graph that FAILS on a
        kind it claims to map still degrades to the floor, loudly."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            res = self._layer(d, _FakeClient(fail=True)).refs("target")
            self.assertTrue(res.degraded)
            self.assertIn("did not recover", res.note)
            self.assertIn(GREP_FLOOR_NAV_NOTE, res.note)

    def test_lexical_navigation_answers_carry_the_honest_floor_note(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            g = GrepBackend(root=d)
            for kind in NAVIGATION_KINDS:
                with self.subTest(kind=kind):
                    self.assertIn(GREP_FLOOR_NAV_NOTE, g.query(kind, "target").note)

    def test_the_floor_note_binds_to_the_chain_not_to_a_product(self):
        """If the chain is re-ordered later (backlog GRAPH-SWEEP), the note follows: it and the
        adoption chain read the SAME symbol, so they cannot disagree about the preferred graph."""
        self.assertIn(PREFERRED_GRAPH_TOOL, GREP_FLOOR_NAV_NOTE)
        self.assertEqual(graph_adopt.ADOPTABLE_GRAPH_TOOLS[0], PREFERRED_GRAPH_TOOL)

    def test_the_full_chain_is_named_in_the_rule(self):
        for hop in ("code-review-graph", "serena", "AST floor", "grep"):
            self.assertIn(hop, NAVIGATION_GRAPH_FIRST)


# ======================================================================================
# 5 · (c) INTEGRATION IS REUSED, NOT REBUILT
# ======================================================================================

class TestIntegrationReusesExistingMachinery(unittest.TestCase):

    def test_navigation_answers_ride_the_existing_freshness_path(self):
        """GR.S4 freshness-before-answer covers the new kinds for free — they go through the
        SAME `_run`, so there is no second freshness mechanism to maintain."""
        seen = []

        class _Fresh:
            def ensure_fresh(self, _layer):
                seen.append("ensure")
                return None

            def recheck_after_answer(self, _layer, _result):
                return False

        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            layer = KnowledgeLayer(AstBackend(root=d, grep=GrepBackend(root=d)),
                                   freshness=_Fresh())
            layer.defs("target")
            layer.refs("target")
            self.assertEqual(seen, ["ensure", "ensure"])

    def test_crg_calls_stay_bounded(self):
        """MCP-ROBUST: the CRG transport carries an explicit timeout (no unbounded hang), and the
        served surface caps every tool call — reused, not re-invented."""
        from mokata.mcp.server import MCP_SURFACE_TIMEOUT_SECONDS
        self.assertGreater(crg_client.CodeReviewGraphClient().timeout, 0)
        self.assertGreater(MCP_SURFACE_TIMEOUT_SECONDS, 0)

    def test_setup_still_offers_crg_adoption(self):
        """(c) CRG adopt at setup: the offer is the GR.S2 seam, reached from the adoption mode —
        this row reuses it and adds no parallel adoption path."""
        from mokata.adoption_modes import mode_spec
        self.assertTrue(mode_spec("full").offers_graph)
        self.assertTrue(callable(graph_adopt.offer_graph_at_setup))


# ======================================================================================
# 6 · NEGATIVES — what this row must NOT have moved
# ======================================================================================

class TestNegatives(unittest.TestCase):

    def test_blast_radius_is_untouched(self):
        """Impact is GRAPH-FIRST-IMPACT's surface (0.0.17), not this row's: `blast_radius` is not
        a navigation kind and its answer carries no navigation floor note."""
        self.assertNotIn("blast_radius", NAVIGATION_KINDS)
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            res = GrepBackend(root=d).query("blast_radius", "target")
            self.assertNotIn(GREP_FLOOR_NAV_NOTE, res.note)
            self.assertEqual(res.note,
                             "lexical fallback (no structural graph; results are approximate)")

    def test_existing_kinds_answer_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, SAMPLE)
            g = GrepBackend(root=d)
            res = g.query("callers", "target")
            self.assertTrue(res.degraded)
            self.assertEqual([(r.path, r.line) for r in res.references],
                             [(os.path.join("svc", "caller.py"), 5)])

    def test_crg_kind_mapping_for_existing_kinds_is_unchanged(self):
        calls = []
        c = crg_client.CodeReviewGraphClient(
            call_tool=lambda t, p: (calls.append(p.get("pattern")), {})[1])
        for kind, pattern in (("callers", "callers_of"), ("callees", "callees_of"),
                              ("imports", "imports_of"), ("implementers", "inheritors_of")):
            c.query(kind, "x", root=".")
            self.assertEqual(calls[-1], pattern)

    def test_an_unknown_kind_is_still_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                GrepBackend(root=d).query("nope", "x")

    def test_a_client_without_the_declaration_is_assumed_to_answer_everything(self):
        """Back-compat: an injected double or an alternative adopted tool that predates
        `supports_kind` keeps its old routing."""
        class _Old:
            def query(self, kind, target, root, depth=1):
                return [{"path": "g.py", "line": 1, "snippet": "", "symbol": target}]

        with tempfile.TemporaryDirectory() as d:
            b = CodeReviewGraphBackend(name="serena", root=d, client=_Old())
            self.assertTrue(b.supports_kind("defs"))
            self.assertFalse(b.query("defs", "x").degraded)

    def test_backend_error_class_is_still_what_the_layer_catches(self):
        self.assertTrue(issubclass(crg_client.CrgUnavailable, Exception))
        self.assertTrue(issubclass(BackendError, Exception))


if __name__ == "__main__":
    unittest.main()
