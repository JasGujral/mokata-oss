"""GR.S3 — the named regression + the end-to-end escape (default graph.required, real surface).

`test_gr_s3_regression` is the release regression: with `graph.required` UNSET (so the default TRUE
applies to this project on upgrade), a degraded blast radius is REFUSED as decision input — the
behavior that fails on the pre-GR.S3 code, which presented the degraded radius silently. The rest
covers the honesty negative (the degraded marking survives the override), the once-per-repo upgrade
notice, and the CLI `--allow-degraded` escape end-to-end through `mokata spec-check`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401

from mokata.brainstorm import Approach, BrainstormGateError, BrainstormSession
from mokata.brainstorm_impact import DesignFitVerdict
from mokata.knowledge.query import QueryResult, Reference
from mokata.knowledge.query import BASIS_LEXICAL, BASIS_STRUCTURAL


class _Layer:
    def __init__(self, *, uses_graph=False, backend_name="grep", degraded=True):
        self.uses_graph = uses_graph
        self.backend_name = backend_name
        self._degraded = degraded

    def blast_radius(self, symbol, depth=2):
        refs = [Reference("app/pay.py", 5, "", "charge"), Reference("app/api.py", 9, "", "h")]
        return QueryResult("blast_radius", symbol, references=refs,
                           backend=self.backend_name,
                           basis=(BASIS_LEXICAL if self._degraded else BASIS_STRUCTURAL))


def _init(root):
    from mokata.init import init_repo
    from mokata.config import Surface
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(root)


def _session(layer):
    s = BrainstormSession("how to bill")
    s.propose_approaches([
        Approach("a", "a", pros=["fast"], cons=["risky"], targets=["charge"]),
        Approach("b", "b", pros=["safe"], cons=["slow"], targets=["charge"]),
    ])
    s.assess_impacts(layer=layer)
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    return s


class TestRegression(unittest.TestCase):

    def test_gr_s3_regression(self):
        """graph.required UNSET (default true) + a degraded (grep-floor) chain ⇒ Lens-1 REFUSES
        the approval with the informative message. Fails on pre-GR.S3 code (which approved it)."""
        from mokata.govern import graph_required as GR
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            self.assertTrue(GR.graph_required_enabled(surface))     # default true, no migration
            layer = _Layer(degraded=True)                           # the grep floor
            s = _session(layer)
            gate = GR.brainstorm_impact_gate(s, "a", surface=surface, run_id="run-1", layer=layer)
            self.assertTrue(gate.refused)
            with self.assertRaises(BrainstormGateError) as ctx:
                s.approve("jas", "a", graph_gate=gate)
            msg = str(ctx.exception)
            self.assertIn("REFUSED", msg)
            self.assertIn("graph adopt", msg)
            self.assertIn("allow-degraded", msg)


class TestFirstRefusalNotice(unittest.TestCase):

    def test_notice_is_loud_once_then_never_repeats(self):
        from mokata.govern import graph_required as GR
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            layer = _Layer(degraded=True)
            s = _session(layer)
            g1 = GR.brainstorm_impact_gate(s, "a", surface=surface, run_id="r1", layer=layer)
            self.assertIsNotNone(g1.notice)
            self.assertIn("graph.required", g1.render())
            # a later refusal (even a new session/run) never repeats the banner for this repo.
            g2 = GR.brainstorm_impact_gate(s, "a", surface=surface, run_id="r2", layer=layer)
            self.assertIsNone(g2.notice)


class TestOverrideHonesty(unittest.TestCase):

    def test_degraded_marking_survives_the_override(self):
        # honesty over convenience (P22): once allowed, the guard proceeds BUT the output still
        # says the touch-set was degraded — the caveat is never stripped.
        from mokata.engine.spec_awareness import ChangeSet, guard_change
        from mokata.engine.spec import AcceptanceCriterion, Spec
        # a spec that does NOT overlap the change (no conflict) — so the only thing under test is
        # that the DEGRADED marking survives the override, not the deviation gate.
        specs = [Spec(title="login cookie lifetime", source="s",
                      criteria=[AcceptanceCriterion("AC1", "the session token rotates")])]
        change = ChangeSet(symbols=["charge"], files=["app/billing.py"])
        outcome = guard_change(change, specs=specs, decisions=[], layer=None,
                               graph_required=True, graph_overridden=True)
        self.assertTrue(outcome.proceeded)
        self.assertIn("no graph", outcome.render())         # the degraded marking survives


class TestCliEscape(unittest.TestCase):

    def _args(self, root, **kw):
        ns = argparse.Namespace(path=root, symbols="charge", files="app/pay.py", text=None,
                                phase="develop", yes=False, allow_degraded=False, reason="")
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _seed_corpus(self, surface):
        from mokata.engine.spec import AcceptanceCriterion, Spec
        spec = Spec(title="charge flow", source="s",
                    criteria=[AcceptanceCriterion("AC1", "the charge symbol matters")])
        surface.state.write("spec_corpus", [spec.to_dict()])

    def test_cli_refuses_then_allow_degraded_proceeds_and_ledgers(self):
        from mokata.cli_commands.knowledge import cmd_spec_check
        from mokata.govern import AuditLedger, graph_required as GR
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)                          # empty repo → grep floor (degraded)
            self._seed_corpus(surface)

            # (1) no escape → REFUSED (rc 1), the degraded touch-set is not a decision input.
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_spec_check(self._args(root))
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED", buf.getvalue())

            # (2) --allow-degraded --yes → the ledgered escape proceeds (rc 0).
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc2 = cmd_spec_check(self._args(root, allow_degraded=True, yes=True,
                                                reason="ci accepts it"))
            self.assertEqual(rc2, 0)
            self.assertNotIn("REFUSED", buf2.getvalue())

            # the override was recorded to the audit ledger with the reason.
            entries = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
            recs = [e for e in entries if e.get("kind") == GR.GRAPH_OVERRIDE_KIND]
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["reason"], "ci accepts it")


if __name__ == "__main__":
    unittest.main()
