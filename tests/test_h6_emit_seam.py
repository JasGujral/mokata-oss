"""H-6 S4 WIRE — the code-anchor refusal reaches the two PRODUCTION emit surfaces.

**THIS IS A DELIBERATE CONTRACT CHANGE, approved before it was built (Jas 2026-08-01).**
`mokata spec emit` and the `spec_emit` MCP tool can now REFUSE on a stale code anchor — a failure
mode neither had before. It is recorded in the CHANGELOG for that reason, not only in doc 02.

S4 shipped the verdict and left it armed and unused: `brainstorm_code_anchor_gate` rides
`BrainstormSession.approve(code_anchor_gate=)`, and `approve()` has exactly ONE caller in `src/`
(`playbook.py:171`, which passes no gates). The approve seam does not run in production. The seam
that DOES is EMIT — `handoff_prior_art_gate` is wired at both surfaces and reads the durable
`approved_approach` Handoff — so this follows that pattern exactly rather than inventing one.

Every test here drives a PRODUCTION entry point (the argparse CLI command, the registered MCP tool).
That is the REACHABILITY-PINS-MISSING lesson taken rather than restated: a refusal proven only by
calling `check_code_anchors` directly is a refusal nobody has shown a user can ever meet.

WHAT IS PINNED
  * it FIRES through the real CLI and the real MCP tool on a stale PATH anchor;
  * it does NOT fire where H-6 itself declines (P4) — no baseline, and a symbol anchor with no
    authoritative graph;
  * the path/symbol WORDING distinction survives the trip to the surface (P8);
  * a clean anchor emits completely unchanged — byte-identical to pre-H-6 on the happy path;
  * FAIL-OPEN, unlike its prior-art sibling: a legacy Handoff with no citations must not refuse.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401

from mokata.brainstorm import Approach, BrainstormSession, DesignFitVerdict
from mokata.knowledge import anchor_fingerprints as AF
from mokata.prior_art import PriorArtResult, RelatedDecision


class _Ref:
    def __init__(self, path):
        self.path = path


class _Result:
    def __init__(self, refs):
        self.references = refs
        self.degraded = False


class _Graph:
    is_graph = True

    def __init__(self, defs):
        self._defs = defs

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
        from mokata.init import init_repo
        self.write("src/pay.py", "RATE = 1\n")
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda _: None)
        self.run_id = "run-h6-emit"

    def write(self, rel, text):
        ab = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)

    # --- the durable Handoff a real approval leaves behind -------------------
    def persist_handoff(self, *anchors, ran=True, decisions=True):
        """Approve an approach whose prior-art pass cited a decision naming `anchors`, and persist
        the resulting Handoff exactly as the product does (`save_approved_approach`)."""
        from mokata.brainstorm import persist_approach
        from mokata.cli_commands.spec import _run_scoped_store
        from mokata.config import Surface
        s = BrainstormSession("charge correctly")
        s.propose_approaches([
            Approach("a", "extend the charger", pros=["reuse"], cons=["coupling"],
                     targets=["Pay"]),
            Approach("b", "write a new charger", pros=["clean"], cons=["dup"], targets=["Pay"]),
        ])
        s.assess_impacts(layer=None)
        s.record_design_fit("a", DesignFitVerdict("a", "fits"))
        cited = [RelatedDecision(id="d1", subject="payment rule", kind="decision",
                                 about_code=list(anchors))] if decisions else []
        s.prior_art = {"a": PriorArtResult(approach="a", ran=ran, decisions=cited)}
        s.approve("jas", "a")
        surface = Surface.load(self.root)
        # Persist through BOTH surfaces' own run resolution. They genuinely differ — the CLI runs
        # in a separate process and resolves via `gate_hook.resolve_run`, the MCP tool via
        # `_evidence_store` — and each gate reads the Handoff on ITS run. Writing only one would
        # test one surface and silently skip the gate on the other.
        from mokata.mcp.consent import _evidence_store
        cli_store, _rid, err = _run_scoped_store(surface, self.run_id)
        self.assertIsNone(err, f"could not resolve the CLI run: {err}")
        persist_approach(s, cli_store)      # the EXISTING gated approval flow, not a hand-write
        mcp_store, _mrid = _evidence_store(surface, self.root)
        persist_approach(s, mcp_store)
        return surface

    def spec_payload(self):
        p = os.path.join(self.root, "spec.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"title": "charge correctly",
                       "criteria": [{"id": "AC1", "text": "it charges"}],
                       "tests": [{"name": "test_charges", "ac_ids": ["AC1"]}]}, fh)
        return p

    # --- the two PRODUCTION entry points ------------------------------------
    def cli_emit(self):
        from mokata.cli_commands.spec import cmd_spec_emit
        args = argparse.Namespace(path=self.root, file=self.spec_payload(), yes=True,
                                  run_id=self.run_id, json=None, stdin=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cmd_spec_emit(args)
        return code, buf.getvalue()

    def mcp_emit(self):
        from mokata.mcp.tools_spec import spec_emit
        return spec_emit(path=self.root, title="charge correctly",
                         criteria=[{"id": "AC1", "text": "it charges"}],
                         tests=[{"name": "test_charges", "ac_ids": ["AC1"]}])

    def stale_path_anchor(self):
        """A baseline minted for `src/pay.py`, then the file moves."""
        AF.record_anchors(self.root, ["src/pay.py"])
        self.write("src/pay.py", "RATE = 2\n")


# ================================================================ it FIRES, through both surfaces
class ItFires(_Base):

    def test_the_cli_refuses(self):
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        code, out = self.cli_emit()
        self.assertEqual(1, code)
        self.assertIn("[BLOCK] code-anchor", out)
        self.assertIn("src/pay.py", out)
        self.assertIn("Nothing was written", out)

    def test_the_mcp_tool_refuses(self):
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        res = self.mcp_emit()
        self.assertEqual("blocked", res.get("status"))
        self.assertFalse(res.get("committed"))
        self.assertEqual("code-anchor-ref", res.get("gate"))
        self.assertIn("src/pay.py", res.get("reason", ""))

    def test_the_refusal_writes_nothing(self):
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        before = _snapshot(self.root)
        self.mcp_emit()
        self.assertEqual(before, _snapshot(self.root))

    def test_both_surfaces_give_the_same_verdict(self):
        # One shared verdict, two surfaces — the `handoff_prior_art_gate` contract, kept.
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        code, out = self.cli_emit()
        res = self.mcp_emit()
        self.assertEqual(1, code)
        self.assertEqual("blocked", res.get("status"))
        self.assertIn(res["reason"].splitlines()[0], out)


# ================================================================ P4 — it does NOT over-refuse
class ItDeclinesWhereH6Declines(_Base):
    """The non-negotiable, at the surface: the bridge must never fail LOUD where H-6 declines."""

    def test_no_baseline_emits_cleanly(self):
        self.persist_handoff("src/pay.py")
        self.write("src/pay.py", "RATE = 2\n")          # moved, but NEVER recorded
        self.assertFalse(os.path.exists(AF.record_path(self.root)))
        self.assertIsNone(_gate_result(self.mcp_emit()))
        self.assertEqual(0, self.cli_emit()[0])

    def test_a_symbol_anchor_with_no_graph_emits_cleanly(self):
        # The graph mints the baseline; the emit seam then has no adopted graph, so H-6 declines —
        # and so must the refusal. This is the state P4 exists for.
        graph = _Layer(_Graph({"Pay.charge": ["src/pay.py"]}))
        AF.record_anchors(self.root, ["Pay.charge"], layer=graph)
        self.persist_handoff("Pay.charge")
        self.write("src/pay.py", "RATE = 2\n")
        self.assertEqual(AF.MOVED, AF.evaluate_anchor("Pay.charge", root=self.root,
                                                      layer=graph).verdict)   # it DID move
        self.assertIsNone(_gate_result(self.mcp_emit()))                      # ...and we say nothing
        self.assertEqual(0, self.cli_emit()[0])

    def test_a_legacy_handoff_with_NO_citations_at_all_emits_cleanly(self):
        # FAIL-OPEN, and the asymmetry with the prior-art sibling is the point: an absent record
        # THERE means "the step never ran"; HERE it means "no citations to judge", which is no
        # opinion. `decisions=False` gives a genuinely EMPTY citation list — a citation carrying an
        # empty `about_code` (the first version of this test) does not exercise the branch at all.
        self.persist_handoff(decisions=False)
        self.stale_path_anchor()
        self.assertIsNone(_gate_result(self.mcp_emit()))
        self.assertEqual(0, self.cli_emit()[0])

    def test_the_layer_really_is_threaded_through_to_the_gate(self):
        # P4's other half, and it cannot be shown with a repo that has no adopted graph — with no
        # graph, `layer=None` and a real layer behave identically, which is exactly how the first
        # version of this pin survived the mutation. So the graph is injected at the seam's own
        # layer builder and the SYMBOL arm is made to fire through the real MCP tool.
        from mokata.mcp import tools_spec
        graph = _Layer(_Graph({"Pay.charge": ["src/pay.py"]}))
        AF.record_anchors(self.root, ["Pay.charge"], layer=graph)
        self.persist_handoff("Pay.charge")
        self.write("src/pay.py", "RATE = 2\n")

        original = tools_spec._knowledge_layer
        tools_spec._knowledge_layer = lambda surface: graph
        self.addCleanup(setattr, tools_spec, "_knowledge_layer", original)

        res = self.mcp_emit()
        self.assertIsNotNone(_gate_result(res))
        self.assertIn("Pay.charge", res["reason"])
        self.assertIn("DEFINITION SITES", res["reason"])     # the SYMBOL wording...
        self.assertNotIn("no longer the files they were", res["reason"])  # ...not the path one

    def test_a_clean_anchor_emits_unchanged(self):
        self.persist_handoff("src/pay.py")
        AF.record_anchors(self.root, ["src/pay.py"])     # recorded, never moved
        res = self.mcp_emit()
        self.assertIsNone(_gate_result(res))
        self.assertNotEqual("blocked", res.get("status"))


# ================================================================ P8 — the wording survives
class WordingSurvives(_Base):

    SYMBOL_CLAIMS = ("symbol", "defines", "definition", "declares")

    def test_a_path_refusal_never_claims_the_symbol_moved(self):
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        reason = self.mcp_emit()["reason"].lower()
        for word in self.SYMBOL_CLAIMS:
            self.assertNotIn(word, reason)

    def test_the_road_out_is_re_read_the_code_not_re_run_prior_art(self):
        # The sibling gate's fix would be useless here — the memory index never moved, the code did.
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        res = self.mcp_emit()
        self.assertIn("re-read the changed code", res["reason"].lower())
        self.assertNotIn("re-run the prior-art pass", res["reason"].lower())
        self.assertIn("re-read", res["hint"].lower())

    def test_it_is_a_distinct_gate_id_from_its_prior_art_sibling(self):
        self.persist_handoff("src/pay.py")
        self.stale_path_anchor()
        self.assertEqual("code-anchor-ref", self.mcp_emit()["gate"])


# ================================================================ the CHANGELOG records it
class ContractChangeIsRecorded(unittest.TestCase):

    def test_the_changelog_names_the_new_failure_mode(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "CHANGELOG.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("code-anchor-ref", text)
        self.assertIn("spec emit", text)


def _gate_result(res):
    """The refusal, or None — so a test reads as "did the gate fire" rather than "what happened"."""
    return res if res.get("gate") == "code-anchor-ref" else None


def _snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            ab = os.path.join(dirpath, fn)
            try:
                with open(ab, "rb") as fh:
                    out[os.path.relpath(ab, root)] = fh.read()
            except OSError:
                out[os.path.relpath(ab, root)] = b"<unreadable>"
    return out


if __name__ == "__main__":
    unittest.main()
