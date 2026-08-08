"""H-6 S4 — STALE-REF's CODE-ANCHOR half: an `about_code` anchor cited on moved code fails loud.

MOVED here from DB.S7 at that stage's plan of record (decision #3, 2026-07-30), because it needs
the DURABLE anchor→fingerprint record H-6 S1 creates and DB.S7 does not. The MEMORY-HANDLE half
shipped at DB.S7c2 and is untouched.

WHAT IT REFUSES: approving an approach whose prior-art citations are anchored to code that has
CHANGED since those decisions were recorded. The decision you were shown is about code that is no
longer the code in front of you.

P4 IS THE NON-NEGOTIABLE PIN, and it is the reason this file is longer than the gate:

    THE REFUSAL AND THE PROPOSAL ARM MUST DECLINE UNDER THE SAME CONDITIONS.

A refusal costs more than a proposal — it stops a human's approval — so a bridge that fails LOUD
where H-6 itself declines is strictly worse than no bridge. "Refuse whenever unsure" is the false
claim `about_code.py:8-11` forbids, wearing a safety costume. Both surfaces therefore consume the
ONE `AnchorVerdict`, and the equality is asserted across the FULL verdict matrix rather than at the
two or three points that happen to be convenient.

The DB.S7c2 shape is followed deliberately: a BOUND CHECK computed by the caller and passed into
`BrainstormSession.approve`, refusing through the same `BrainstormGateError` — **not an eleventh
backed gate** (doc 85 §4 is unchanged). Absent, approval is byte-identical.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import inspect
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401

from mokata.brainstorm import (Approach, BrainstormGateError, BrainstormSession,
                               DesignFitVerdict)
from mokata.govern import code_anchor_gate as G
from mokata.knowledge import anchor_fingerprints as AF
from mokata.memory.healing import AnchorStaleness, detect_code_anchor_staleness
from mokata.prior_art import PriorArtResult, RelatedDecision


def _lensed():
    """A session with both pre-spec lenses on the table for approach `a` — the DB.S7c2 fixture,
    so the approve seam is exercised at the same point its sibling half is."""
    s = BrainstormSession("add a retry helper")
    s.propose_approaches([
        Approach("a", "write a new retry loop", pros=["simple"], cons=["dup"], targets=["retry"]),
        Approach("b", "extend utils.retry", pros=["reuse"], cons=["coupling"], targets=["retry"]),
    ])
    s.assess_impacts(layer=None)
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    return s


class _Ref:
    def __init__(self, path):
        self.path = path


class _Result:
    def __init__(self, refs, degraded=False):
        self.references = refs
        self.degraded = degraded


class _Graph:
    is_graph = True

    def __init__(self, defs, kinds=("defs",), raises=False, degraded=False):
        self._defs, self._kinds = defs, kinds
        self._raises, self._degraded = raises, degraded

    def supports_kind(self, kind):
        return kind in self._kinds

    def query(self, kind, target, depth=1):
        if self._raises:
            raise RuntimeError("graph hiccup")
        return _Result([_Ref(p) for p in self._defs.get(target, [])], degraded=self._degraded)


class _Floor:
    is_graph = False

    def __init__(self, defs=None):
        self._defs = defs or {}

    def supports_kind(self, kind):
        return True

    def query(self, kind, target, depth=1):
        return _Result([_Ref(p) for p in self._defs.get(target, [])])


class _Layer:
    def __init__(self, primary=None):
        self.primary = primary


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel, text):
        ab = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)

    def cite(self, *anchors, ident="d1"):
        return RelatedDecision(id=ident, subject="a decision", kind="decision",
                               about_code=list(anchors))


# ================================================================ the verdict
class Verdict(_Base):

    def test_a_moved_path_anchor_refuses(self):
        self.write("src/a.py", "v = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "v = 2\n")
        out = G.check_code_anchors(decisions=[self.cite("src/a.py")], root=self.root)
        self.assertTrue(out.refused)
        self.assertEqual(["src/a.py"], out.moved_anchors)
        self.assertIn("d1", out.reason)
        # The road out is RE-READ THE CODE, not "re-run the prior-art pass" — that is the sibling
        # half's fix and it would be useless here: the memory index never moved, the code did.
        self.assertIn("re-read the changed code", out.reason.lower())
        self.assertNotIn("re-run the prior-art pass", out.reason.lower())

    def test_an_unmoved_anchor_allows(self):
        self.write("src/a.py", "v = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        out = G.check_code_anchors(decisions=[self.cite("src/a.py")], root=self.root)
        self.assertTrue(out.allowed)
        self.assertEqual("", out.reason)

    def test_an_unrecorded_anchor_allows(self):
        # P6 reaching the gate: no baseline is no opinion, and this one REFUSES a human's approval.
        self.write("src/a.py", "v = 1\n")
        self.write("src/a.py", "v = 2\n")
        self.assertTrue(G.check_code_anchors(decisions=[self.cite("src/a.py")],
                                             root=self.root).allowed)

    def test_no_record_means_no_anchor_evaluation_at_all(self):
        # The early-out is an OPTIMISATION, not a correctness guard — with no baselines every
        # anchor declines anyway. So it has to be pinned as one: an emptiness assertion cannot tell
        # "we looked and found nothing" from "we never looked", and the first mutation pass proved
        # exactly that here (the same shape of vacuous pin S3 hit three times).
        original = AF.evaluate_anchors
        calls = []
        AF.evaluate_anchors = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        self.addCleanup(setattr, AF, "evaluate_anchors", original)

        self.write("src/a.py", "v = 1\n")
        self.assertFalse(os.path.exists(AF.record_path(self.root)))
        self.assertTrue(G.check_code_anchors(decisions=[self.cite("src/a.py")],
                                             root=self.root).allowed)
        self.assertEqual([], calls)

        AF.record_anchors(self.root, ["src/a.py"])         # ...the control
        G.check_code_anchors(decisions=[self.cite("src/a.py")], root=self.root)
        self.assertEqual(1, len(calls))

    def test_no_citations_is_no_opinion(self):
        # "The prior-art step never ran" is GR-PA's verdict. Answering here too would give one
        # omission two different messages — the DB.S7c2 boundary, restated for this half.
        self.assertTrue(G.check_code_anchors(decisions=[], root=self.root).allowed)
        self.assertTrue(G.check_code_anchors(decisions=[self.cite()], root=self.root).allowed)

    def test_it_names_every_moved_anchor_and_every_citation(self):
        self.write("src/a.py", "v = 1\n")
        self.write("src/b.py", "v = 1\n")
        AF.record_anchors(self.root, ["src/a.py", "src/b.py"])
        self.write("src/a.py", "v = 2\n")
        self.write("src/b.py", "v = 2\n")
        out = G.check_code_anchors(
            decisions=[self.cite("src/a.py", ident="d1"), self.cite("src/b.py", ident="d2")],
            root=self.root)
        self.assertEqual(["src/a.py", "src/b.py"], out.moved_anchors)
        self.assertEqual(["d1", "d2"], out.stale_ids)
        # ...and the RENDERED refusal names them too. The fields are for code; the reason is what
        # a human reads, and a refusal that says only "something moved" is not actionable.
        for named in ("src/a.py", "src/b.py", "d1", "d2"):
            self.assertIn(named, out.reason)


# ================================================================ P4 — THE NON-NEGOTIABLE
class SameConditions(_Base):
    """The refusal and the proposal arm decline TOGETHER, across the whole verdict matrix."""

    SYM = "MemoryStore.remember"

    def _arm(self, anchor, layer):
        """Does the PROPOSAL arm fire? (H-6 S3's path, through its own detector.)"""
        item = type("I", (), {"subject": "s", "mtype": "persistent",
                              "about_code": [anchor]})()
        moved = [AnchorStaleness(item=item, anchor=v.anchor, shape=v.shape, path=v.path)
                 for v in AF.evaluate_anchors([anchor], root=self.root, layer=layer) if v.moved]
        return bool(detect_code_anchor_staleness(moved))

    def _gate(self, anchor, layer):
        """Does the REFUSAL fire? (H-6 S4's path, through the gate.)"""
        return G.check_code_anchors(decisions=[self.cite(anchor)], root=self.root,
                                    layer=layer).refused

    def _matrix(self):
        """Every state an anchor can be in, both shapes. Each entry is (label, anchor, layer)."""
        self.write("src/store.py", "a = 1\n")
        self.write("src/twin.py", "a = 1\n")
        good = _Layer(_Graph({self.SYM: ["src/store.py"]}))
        AF.record_anchors(self.root, ["src/store.py", "recorded-only.py"])
        AF.record_anchors(self.root, [self.SYM], layer=good)
        self.write("src/store.py", "a = 2\n")           # BOTH shapes now point at moved bytes
        return [
            ("path · moved",              "src/store.py",   None),
            ("path · moved, on the floor", "src/store.py",  _Layer(_Floor())),
            ("path · unmoved",            "src/twin.py",    None),
            ("path · unrecorded",         "src/twin.py",    good),
            ("path · absent from tree",   "recorded-only.py", None),
            ("symbol · moved, graph",     self.SYM,         good),
            ("symbol · no layer",         self.SYM,         None),
            ("symbol · floor",            self.SYM,         _Layer(_Floor({self.SYM: ["src/store.py"]}))),
            ("symbol · graph maps no defs", self.SYM,
             _Layer(_Graph({self.SYM: ["src/store.py"]}, kinds=()))),
            ("symbol · graph hiccup",     self.SYM,         _Layer(_Graph({}, raises=True))),
            ("symbol · degraded answer",  self.SYM,
             _Layer(_Graph({self.SYM: ["src/store.py"]}, degraded=True))),
            ("symbol · graph names nothing", self.SYM,      _Layer(_Graph({}))),
        ]

    def test_the_two_surfaces_agree_on_every_state(self):
        fired = {}
        for label, anchor, layer in self._matrix():
            arm, gate = self._arm(anchor, layer), self._gate(anchor, layer)
            self.assertEqual(arm, gate,
                             f"P4 VIOLATED at [{label}]: proposal={arm} refusal={gate} — the "
                             f"bridge must never fail loud where H-6 itself declines")
            fired[label] = gate
        # The matrix must actually EXERCISE both outcomes, or agreement is agreement on nothing.
        self.assertTrue(any(fired.values()), "no state fired — the matrix proves nothing")
        self.assertFalse(all(fired.values()), "every state fired — the matrix proves nothing")

    def test_the_gate_derives_no_predicate_of_its_own(self):
        # STRUCTURAL: the refusal reads `AnchorVerdict.moved` and nothing else. A second
        # implementation that agrees today is not the contract — one implementation is.
        tree = ast.parse(inspect.getsource(G))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                  for a in n.names}
        self.assertIn("evaluate_anchors", names)
        # `read_record` is NOT banned: reading the record once is the cost model (one cheap read,
        # N verdicts), not a predicate. What is banned is deciding for itself what "moved" means.
        for banned in ("fingerprint_forces_refresh", "file_fingerprint", "classify_anchor",
                       "current_evidence", "hashlib", "_hash_file", "split_line_ref"):
            self.assertNotIn(banned, names)

    def test_the_govern_module_never_touches_the_tripwire_directly(self):
        # The DB.S7c2 boundary (`TestH6BoundaryHolds.test_new_modules_skip_the_bridge`) extended to
        # this half: govern consumes verdicts, never the raw predicate. STRUCTURAL (AST) rather
        # than a source grep — the module docstring EXPLAINS the boundary by name and must pass.
        tree = ast.parse(inspect.getsource(G))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.func.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("fingerprint_forces_refresh", called)


# ================================================================ P7 — loud, never silent-correct
class NeverRestamps(_Base):

    def test_the_refusal_repairs_nothing(self):
        self.write("src/a.py", "v = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        before = AF.read_record(self.root)["src/a.py"]["fingerprint"]
        self.write("src/a.py", "v = 2\n")
        for _ in range(3):
            self.assertTrue(G.check_code_anchors(decisions=[self.cite("src/a.py")],
                                                 root=self.root).refused)
        self.assertEqual(before, AF.read_record(self.root)["src/a.py"]["fingerprint"])

    def test_the_reason_names_the_road_out_and_refuses_to_relabel(self):
        self.write("src/a.py", "v = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "v = 2\n")
        reason = G.check_code_anchors(decisions=[self.cite("src/a.py")], root=self.root).reason
        self.assertIn("will NOT", reason)
        self.assertIn("re-read", reason.lower())

    def test_the_module_calls_no_writer(self):
        tree = ast.parse(inspect.getsource(G))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        called |= {n.func.id for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        for banned in ("record_anchors", "open", "write", "remember", "_write_record"):
            self.assertNotIn(banned, called)


# ================================================================ the citation carries its anchors
class Citation(_Base):

    def test_about_code_rides_the_run_state_round_trip(self):
        # The DB.S7c2 reason, restated: this citation is persisted into brainstorm run state and
        # read back after the pass that made it is gone. An anchor that did not survive
        # `to_dict`/`from_dict` could never be checked at approve time.
        d = RelatedDecision(id="d1", subject="s", about_code=["src/a.py", "Foo.bar"])
        self.assertEqual(["src/a.py", "Foo.bar"],
                         RelatedDecision.from_dict(d.to_dict()).about_code)

    def test_a_recalled_item_carries_its_anchors(self):
        item = type("I", (), {"id": "m1", "subject": "s", "kind": "decision",
                              "about_code": ["src/a.py"]})()
        self.assertEqual(["src/a.py"], RelatedDecision.from_item(item).about_code)

    def test_a_legacy_citation_reads_back_empty_not_broken(self):
        self.assertEqual([], RelatedDecision.from_dict({"id": "d1"}).about_code)


# ================================================================ the in-session verdict
class InSession(_Base):

    def _session(self, *anchors):
        s = _lensed()
        s.prior_art = {"a": PriorArtResult(approach="a", ran=True,
                                           decisions=[self.cite(*anchors)])}
        return s

    def test_it_judges_the_named_approach(self):
        self.write("src/a.py", "v = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "v = 2\n")
        s = self._session("src/a.py")
        self.assertTrue(G.brainstorm_code_anchor_gate(s, "a", root=self.root).refused)
        # ...and only that one: a sibling approach with no evidence is untouched.
        self.assertTrue(G.brainstorm_code_anchor_gate(s, "b", root=self.root).allowed)

    def test_an_approach_with_no_evidence_yields_no_refusal(self):
        self.assertTrue(G.brainstorm_code_anchor_gate(_lensed(), "a", root=self.root).allowed)


# ================================================================ the approve seam
class ApproveSeam(_Base):

    def test_a_refusal_blocks_the_approval(self):
        s = _lensed()
        out = G.CodeAnchorOutcome(consumer=G.CONSUMER, refused=True,
                                  moved_anchors=["src/a.py"], stale_ids=["d1"],
                                  reason="REFUSED: the code moved")
        with self.assertRaises(BrainstormGateError):
            s.approve("jas", "a", code_anchor_gate=out)
        self.assertFalse(s.approved)

    def test_an_allowing_verdict_approves(self):
        s = _lensed()
        s.approve("jas", "a", code_anchor_gate=G.CodeAnchorOutcome(consumer=G.CONSUMER,
                                                                   refused=False))
        self.assertTrue(s.approved)

    def test_absent_is_byte_identical(self):
        s = _lensed()
        s.approve("jas", "a")
        self.assertTrue(s.approved)

    def test_it_is_not_an_eleventh_backed_gate(self):
        # doc 85 §4 is UNCHANGED: this is a caller-computed bound check riding the existing
        # approve seam, exactly as DB.S7c2's half is. A new BACKED gate would need an enforcement
        # point in the registry, and this deliberately has none.
        from mokata import skill_contracts as sc
        self.assertNotIn(G.GATE_ID, sc.GATES)
        backed = sorted(k for k, v in sc.GATES.items() if getattr(v, "backed", False))
        # The exact set, frozen — a count would let one addition hide one removal, and in 0.0.17
        # stage 5 exactly that happened: `self-protect` was ADDED and `ship-readiness` REMOVED in
        # the same stage, so the count stayed 9 while the set changed twice. This assertion is why
        # that was visible rather than silent.
        #   ⚠ THIS COMMENT USED TO SAY "two registries, not a discrepancy" — that doc 85 §4's TEN
        # were these nine plus `self-protect`, which was "enforced in code and lives in no contract
        # registry". That reasoning was overturned (Jas, 0.0.17 stage 5): §4 was right and the
        # registry was INCOMPLETE. `self-protect` is layer 0 of `WriteGate.submit`, ahead of the
        # trust dial, the secret scan and the human gate, and measured REACHABLE — a gate that runs
        # first on every durable write is backed by any definition, so its absence from the table
        # claiming to enumerate backed gates was the defect, not a second registry.
        self.assertEqual(
            ["approach-approval", "completeness", "deviation", "hard-rule",
             "no-code-without-failing-test", "secret-guard", "self-protect",
             "spec-persisted", "write-gate"], backed)

    def test_its_id_is_distinct_from_the_memory_handle_half(self):
        from mokata.govern.stale_ref_gate import GATE_ID as MEMORY_HALF
        self.assertNotEqual(MEMORY_HALF, G.GATE_ID)


if __name__ == "__main__":
    unittest.main()
