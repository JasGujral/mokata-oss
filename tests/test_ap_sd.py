"""AP-SD — the approved approach gains a machine-readable `decisions[]` block (0.0.14 Phase 1).

What bare Claude Code approves is prose the next turn can drift from. mokata's approved approach
already carried typed lenses (impact/design-fit) and prior-art; AP-SD adds the SUBSTRATE two dormant
hooks were waiting on: each decision is `{id, statement, rationale_ref, about_code:[anchors],
deferred:[...]}`, stamped with a `schema_version` on the approach. The block is the CONTRACT review
verifies the diff against (GR.S2(m) pass-1) and prior-art recalls (GR-PA enrichment), and its
`deferred` list is the ONE truth the spec's scope section derives from at emit — never hand-written
twice.

Adoption is gradual: an approach with no `decisions[]` is byte-identical to a pre-AP-SD one, and the
completeness gate WARNS (never blocks) when none are recorded. This is the release regression + the
hook activations + the round-trips + the backward-compat negatives.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit

from mokata import gate_hook as G                                  # noqa: E402
from mokata import session, tdd_state as T                         # noqa: E402
from mokata.brainstorm import (                                    # noqa: E402
    Approach, BrainstormSession, Decision, DecisionDeferral, Handoff,
    load_approved_approach, load_decisions, persist_approach,
)
from mokata.brainstorm_impact import FITS, DesignFitVerdict        # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.engine import AcceptanceCriterion, ChangeSet, Spec, TestRef   # noqa: E402
from mokata.engine.completeness import run_completeness_gate       # noqa: E402
from mokata.execmode.review import two_stage_review                # noqa: E402
from mokata.execmode.review_graph import graph_verify              # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.knowledge.query import QueryResult, Reference          # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.state import StateStore                                # noqa: E402


# ---------------------------------------------------------------- fixtures
def _decision(did="D1", *, statement="retry lives in utils.retry",
              about=("retry",), deferred=()):
    return Decision(id=did, statement=statement, rationale_ref="doc-63 §4",
                    about_code=list(about),
                    deferred=[DecisionDeferral(**dd) for dd in deferred])


def _deferral(did="F1", item="batch update/delete",
              paths=("src/api/batch*.py",), markers=("batch_update", "bulk_delete")):
    return dict(id=did, item=item, paths=list(paths), markers=list(markers))


class _Layer:
    """A uses_graph layer with blast_radius + callers, recording the depth it was called with."""
    uses_graph = True

    def __init__(self, radius=None, callers=None):
        self._radius = radius or {}
        self._callers = callers or {}
        self.depths = []

    def blast_radius(self, sym, depth=2):
        self.depths.append(depth)
        refs = [Reference(**r) for r in self._radius.get(sym, [])]
        return QueryResult(kind="blast_radius", target=sym, references=refs,
                           backend="code-review-graph")

    def callers(self, sym):
        refs = [Reference(**r) for r in self._callers.get(sym, [])]
        return QueryResult(kind="callers", target=sym, references=refs,
                           backend="code-review-graph")


def _session_with_decisions(decisions=None, *, approach="a"):
    """A genuinely-approved brainstorm session whose CHOSEN approach carries `decisions[]`."""
    s = BrainstormSession("add a retry helper")
    s.propose_approaches([
        Approach("a", "write a retry loop", pros=["simple"], cons=["dup"], targets=["retry"]),
        Approach("b", "extend utils.retry", pros=["reuse"], cons=["coupling"], targets=["retry"]),
    ])
    s.assess_impacts(layer=None, memory_items=[])
    for a in s.approaches:
        s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
    s.assess_prior_art(layer=None)          # GR-PA-WIRE — the bound step ran before approval
    for d in (decisions or []):
        s.propose_decision(approach, d)
    s.approve("jas", approach)
    return s


# ================================================================ 1 — the typed dataclass round-trips
class TestDecisionRoundTrips(unittest.TestCase):

    def test_decision_to_from_dict(self):
        d = _decision(deferred=[_deferral()])
        back = Decision.from_dict(d.to_dict())
        self.assertEqual(back.id, d.id)
        self.assertEqual(back.statement, d.statement)
        self.assertEqual(back.rationale_ref, d.rationale_ref)
        self.assertEqual(back.about_code, ["retry"])
        self.assertEqual(len(back.deferred), 1)
        self.assertEqual(back.deferred[0].markers, ["batch_update", "bulk_delete"])

    def test_decision_dict_shape_is_what_the_hooks_read(self):
        # the GR.S2 + GR-PA hooks duck-type over `about_code`/`id`/`statement`; the dict MUST carry them.
        d = _decision().to_dict()
        self.assertIn("about_code", d)
        self.assertIn("id", d)
        self.assertIn("statement", d)

    def test_approach_carries_decisions_and_schema_version(self):
        a = Approach("a", "s", pros=["p"], cons=["c"], targets=["retry"],
                     decisions=[_decision(deferred=[_deferral()])])
        back = Approach.from_dict(a.to_dict())
        self.assertEqual(back.schema_version, 1)
        self.assertEqual(len(back.decisions), 1)
        self.assertEqual(back.decisions[0].about_code, ["retry"])
        self.assertEqual(back.decisions[0].deferred[0].id, "F1")

    def test_handoff_round_trip_preserves_decisions(self):
        s = _session_with_decisions([_decision(deferred=[_deferral()])])
        ho = s.handoff()
        back = Handoff.from_dict(ho.to_dict())
        self.assertEqual(len(back.approach.decisions), 1)
        self.assertEqual(back.approach.decisions[0].deferred[0].item, "batch update/delete")

    def test_session_dict_round_trip_preserves_decisions(self):
        s = _session_with_decisions([_decision(deferred=[_deferral()])])
        back = BrainstormSession.from_dict(s.to_dict())
        chosen = next(a for a in back.approaches if a.name == "a")
        self.assertEqual(len(chosen.decisions), 1)


# ================================================================ 2 — backward compat (the negatives)
class TestBackwardCompat(unittest.TestCase):

    def test_pre_apsd_approach_dict_loads_clean(self):
        # a persisted approach from before AP-SD carries neither key.
        old = {"name": "a", "summary": "s", "pros": ["p"], "cons": ["c"], "targets": ["retry"]}
        a = Approach.from_dict(old)
        self.assertEqual(a.decisions, [])
        self.assertEqual(a.schema_version, 1)

    def test_pre_apsd_handoff_dict_loads_and_hands_off(self):
        s = _session_with_decisions([])                 # no decisions — a legacy-shaped approach
        d = s.handoff().to_dict()
        d["approach"].pop("decisions", None)            # simulate a truly pre-AP-SD record
        d["approach"].pop("schema_version", None)
        ho = Handoff.from_dict(d)
        self.assertEqual(ho.approach.decisions, [])
        self.assertEqual(ho.approach.schema_version, 1)


# ================================================================ 3 — capture path (no new write path)
class TestCapturePath(unittest.TestCase):

    def test_propose_decision_records_on_the_named_approach(self):
        s = BrainstormSession("t")
        s.propose_approaches([
            Approach("a", "s", pros=["p"], cons=["c"]),
            Approach("b", "s", pros=["p"], cons=["c"]),
        ])
        s.propose_decision("a", _decision())
        self.assertEqual(len(s.approaches[0].decisions), 1)
        self.assertEqual(s.approaches[1].decisions, [])

    def test_propose_decision_unknown_approach_raises(self):
        from mokata.brainstorm import BrainstormError
        s = BrainstormSession("t")
        s.propose_approaches([Approach("a", "s", pros=["p"], cons=["c"]),
                              Approach("b", "s", pros=["p"], cons=["c"])])
        with self.assertRaises(BrainstormError):
            s.propose_decision("nope", _decision())

    def test_decisions_persist_only_through_the_approval_flow(self):
        # proposing a decision writes NOTHING; it lands only when the approved approach is persisted.
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            s = _session_with_decisions([_decision()])
            self.assertIsNone(load_approved_approach(store))     # nothing written by proposing
            persist_approach(s, store)                           # the EXISTING gated approval flow
            ho = load_approved_approach(store)
            self.assertEqual(len(ho.approach.decisions), 1)


# ================================================================ 4 — persist / load the decisions
class TestPersistLoad(unittest.TestCase):

    def test_persist_load_preserves_decisions_and_schema_version(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(_session_with_decisions([_decision(deferred=[_deferral()])]), store)
            ho = load_approved_approach(store)
            self.assertEqual(ho.approach.schema_version, 1)
            self.assertEqual(ho.approach.decisions[0].about_code, ["retry"])

    def test_load_decisions_returns_the_persisted_block(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(_session_with_decisions([_decision(), _decision("D2", about=("x",))]),
                             store)
            dec = load_decisions(store)
            self.assertEqual(len(dec), 2)
            self.assertEqual(dec[0].id, "D1")

    def test_load_decisions_empty_when_no_approach(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_decisions(StateStore(root)), [])

    def test_load_decisions_empty_for_legacy_approach(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(_session_with_decisions([]), store)   # no decisions recorded
            self.assertEqual(load_decisions(store), [])


# ================================================================ 5 — completeness WARNS, never blocks
class TestCompletenessWarn(unittest.TestCase):

    def _spec(self):
        return Spec("t", [AcceptanceCriterion("AC1", "does x")])

    def _tests(self):
        return [TestRef("test_x", ["AC1"])]

    def test_zero_decisions_warns_but_passes(self):
        ho = _session_with_decisions([]).handoff()          # approach present, NO decisions
        gr = run_completeness_gate(self._spec(), self._tests(), handoff=ho)
        self.assertTrue(gr.passed)                          # never blocks
        self.assertTrue(gr.warnings)                        # but names the gap
        self.assertTrue(any("decision" in w.lower() for w in gr.warnings))

    def test_with_decisions_no_warning(self):
        ho = _session_with_decisions([_decision()]).handoff()
        gr = run_completeness_gate(self._spec(), self._tests(), handoff=ho)
        self.assertTrue(gr.passed)
        self.assertFalse([w for w in gr.warnings if "decision" in w.lower()])

    def test_no_approach_no_decisions_warning(self):
        # with no approved direction at all there is no approach to carry decisions — no nag.
        gr = run_completeness_gate(self._spec(), self._tests(), handoff=None)
        self.assertFalse([w for w in gr.warnings if "decision" in w.lower()])


# ================================================================ 6 — ONE truth: emit derives deferred
RUN = "run-apsd"
TITLE = "add a retry helper"
ACS = [("AC1", "retry wraps a failing call"), ("AC2", "retry gives up after N")]
ES_TESTS = [("test_retry_wraps", ["AC1"]), ("test_retry_gives_up", ["AC2"])]


class _Repo:
    def __init__(self, run_id=RUN):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self.run_id = run_id
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run_id})
        self._env.start()
        session.reset_for_test()
        self.surface = Surface.load(self.path)

    def close(self):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _emit_args(path, scope=None):
    a = dict(path=path, title=TITLE,
             criteria=[{"id": i, "text": t} for i, t in ACS],
             tests=[{"name": n, "ac_ids": list(x)} for n, x in ES_TESTS],
             approach="a")
    if scope is not None:
        a["scope"] = scope
    return a


def _emitted_spec(repo):
    raw = StateStore(T.state_dir(repo.path))
    return Spec.from_dict(raw.read(G.SPEC_PREFIX + RUN))


class TestOneTruthEmit(unittest.TestCase):

    def test_ap_sd_regression(self):
        """THE release regression. An approach approved WITH decisions[] persists them, and the
        emitted spec's DEFERRED scope DERIVES from decisions[].deferred — the human never hand-writes
        the deferred list twice. Fails on pre-AP-SD code (no Decision block, no derivation)."""
        with _Repo() as repo:
            s = _session_with_decisions([_decision(deferred=[_deferral()])])
            persist_approach(s, repo.surface.state)

            # emit through the REAL MCP surface, declaring only the AUTHORIZED surface — the deferred
            # list is NOT hand-written here; it must come from the approved decisions.
            res = mcp_commit(TW.spec_emit,
                             **_emit_args(repo.path, scope={"authorized": ["src/retry.py"]}))
            self.assertTrue(res["committed"], f"emit did not commit: {res}")

            scope = _emitted_spec(repo).scope
            self.assertIsNotNone(scope)
            ids = {d.id for d in scope.deferred}
            self.assertIn("F1", ids, "the spec's deferred scope must DERIVE from decisions[].deferred")
            derived = next(d for d in scope.deferred if d.id == "F1")
            self.assertEqual(derived.markers, ("batch_update", "bulk_delete"))
            self.assertEqual(derived.paths, ("src/api/batch*.py",))

    def test_emit_is_byte_identical_when_no_decisions(self):
        """SI-DEV boundary: an approach with NO decisions[] leaves the spec's scope EXACTLY as the
        payload declared it — the derive step is a no-op, so pre-AP-SD behaviour is unchanged."""
        with _Repo() as repo:
            persist_approach(_session_with_decisions([]), repo.surface.state)
            hand_written = {"authorized": ["src/retry.py"],
                            "deferred": [{"id": "H1", "item": "hand", "paths": ["x*.py"]}]}
            res = mcp_commit(TW.spec_emit, **_emit_args(repo.path, scope=hand_written))
            self.assertTrue(res["committed"], f"emit did not commit: {res}")
            scope = _emitted_spec(repo).scope
            self.assertEqual({d.id for d in scope.deferred}, {"H1"})   # exactly what was written


# ================================================================ 7 — WAKE GR.S2(m): review pass-1
class TestWakeGrS2Review(unittest.TestCase):
    """The dormant pass-1 anchor comparison lights up when REAL decisions[] are sourced from the
    persisted approach: the diff's blast radius vs decisions[].about_code — undeclared reach diverges.
    Scope-binding honesty: pass-1 compares ANCHORS (symbol/path strings), never semantics."""

    def test_undeclared_reach_is_a_divergence_when_decisions_sourced(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            # the approach DECLARED it would only touch `declared.thing`.
            persist_approach(
                _session_with_decisions([_decision(about=("declared.thing",))]), store)
            layer = _Layer(radius={"handle": [
                {"path": "reached.py", "line": 1, "symbol": "reached.thing"}]})
            change = ChangeSet(symbols=["handle"], files=[])
            res = graph_verify(layer, change, decisions=load_decisions(store))
            undeclared = [f for f in res.findings if f.kind == "undeclared-reach"]
            self.assertTrue(undeclared, "the diff reaches an anchor the approach never declared")
            self.assertEqual(undeclared[0].pass_no, 1)

    def test_review_surface_reports_the_divergence(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(
                _session_with_decisions([_decision(about=("declared.thing",))]), store)
            layer = _Layer(radius={"handle": [
                {"path": "reached.py", "line": 1, "symbol": "reached.thing"}]})
            change = ChangeSet(symbols=["handle"], files=[])

            class _T:
                output, ok = "did it", True
            rev = two_stage_review(_T(), _T(), layer=layer, change=change,
                                   decisions=load_decisions(store))
            self.assertIn("reaches", rev.stages[0].notes)   # pass-1 finding surfaced to the review

    def test_dormant_without_decisions_degrades_clean(self):
        # no decisions sourced -> pass 1 stays dormant, review is byte-identical to today.
        layer = _Layer(radius={"handle": [
            {"path": "reached.py", "line": 1, "symbol": "reached.thing"}]})
        change = ChangeSet(symbols=["handle"], files=[])
        res = graph_verify(layer, change, decisions=None)
        self.assertEqual([f for f in res.findings if f.kind == "undeclared-reach"], [])


# ================================================================ 8 — WAKE GR-PA: recall enrichment
class TestWakeGrPa(unittest.TestCase):
    """decisions-aware recall enrichment lights up: related decisions from the approach surface in the
    prior-art findings (source == 'decisions[]'), without the caller hand-passing a dict fixture."""

    def test_assess_prior_art_enriches_from_the_approachs_own_decisions(self):
        s = BrainstormSession("add a retry helper")
        s.propose_approaches([
            Approach("a", "retry loop", pros=["p"], cons=["c"], targets=["retry"],
                     decisions=[_decision(about=("retry",))]),
            Approach("b", "extend", pros=["p"], cons=["c"], targets=["retry"]),
        ])
        s.assess_prior_art(layer=None)                 # NO explicit decisions arg — derived per-approach
        enriched = [d for d in s.prior_art["a"].decisions if d.source == "decisions[]"]
        self.assertTrue(enriched, "the approach's own decisions[] must enrich its prior-art recall")
        self.assertIn("retry", enriched[0].matched)

    def test_approach_without_decisions_stays_dormant(self):
        s = BrainstormSession("add a retry helper")
        s.propose_approaches([
            Approach("a", "retry loop", pros=["p"], cons=["c"], targets=["retry"]),
            Approach("b", "extend", pros=["p"], cons=["c"], targets=["retry"]),
        ])
        s.assess_prior_art(layer=None)
        self.assertFalse([d for d in s.prior_art["a"].decisions if d.source == "decisions[]"])


# ================================================================ 9 — scope-binding honesty + secrets
class TestScopeBindingHonesty(unittest.TestCase):

    def test_derived_deferral_carries_paths_and_markers_only_never_semantics(self):
        # SI-DEV boundary restated: a decision deferral binds by PATH globs + LITERAL markers only —
        # the tokens the human named — never by interpreting what the diff means. No semantic matching.
        from mokata.spec_scope import classify, SpecScope, DeferredItem
        deferral = _deferral()
        item = DeferredItem(id=deferral["id"], item=deferral["item"],
                            paths=tuple(deferral["paths"]), markers=tuple(deferral["markers"]))
        scope = SpecScope(authorized=("src/retry.py",), deferred=(item,))
        # a literal marker present -> refused; the same content without the token -> allowed.
        self.assertFalse(classify(scope, "src/retry.py", content="x = batch_update()").allowed)
        self.assertTrue(classify(scope, "src/retry.py", content="x = normal_call()").allowed)


class TestSecretSafety(unittest.TestCase):

    def test_decisions_ride_the_single_existing_durable_write(self):
        # decision statements/anchors are serialized INTO the one approved_approach payload the
        # existing gated flow already writes + scans — no second, unscanned durable channel.
        writes = []

        class _RecordingStore:
            def write(self, key, value):
                writes.append((key, value))
                return f"/state/{key}.json"

            def read(self, key):
                return None

        s = _session_with_decisions([_decision()])
        persist_approach(s, _RecordingStore())
        self.assertEqual([k for k, _ in writes], ["approved_approach"])   # exactly one durable write
        payload = writes[0][1]
        self.assertIn("decisions", payload["approach"])                   # inside the scanned payload


if __name__ == "__main__":
    unittest.main()
