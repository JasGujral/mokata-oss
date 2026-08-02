"""STATE-SCOPE — the MCP surface reads and writes the PIPELINE's state, not the process's (0.0.16).

RE-ENTRY made the approval KEY pipeline-scoped (`badge_run.resolve_run_for_evidence`), and closed
the loop it was filed for. It also filed its own residual, which this stage closes: `Surface.state`
is still scoped to `session.current_session_id()`, so the MCP surface — unlike the CLI, which has
had `cli_commands/spec.py:_run_scoped_store` since HANDOFF.G1 — cannot SEE the run it just resolved.

Three measured facts, reproduced below rather than assumed (all RED before the fix):

  1. THE HANDOFF IS INVISIBLE. After a re-entry, `load_approved_approach(surface.state)` is None —
     the Handoff is filed under sessA — so `spec_amend` from sessB never reaches consent: it blocks
     at `spec-persisted`, the spec it would amend being filed under the other session's id.
  2. BOTH `spec_emit` REFUSAL GATES GO SILENT. Neither MIS-fires; both stop firing. `prior_art.ran
     = False` REFUSES same-session and is SILENT cross-session; `graph.required` on with a degraded
     chosen radius REFUSES same-session and is SILENT cross-session. A gate that quietly isn't
     there is worse than one that blocks.
  3. THE KEYING FIX IS SINGLE-SHOT. The first cross-session commit lands under the WRITING
     session's scope, which creates a SECOND evidence run — after which `_evidence_run` sees 2+,
     resolution degrades back to per-process keying, and the human is walked to a terminal again.

Fact 3 is why the fix must be a read/write PAIR. Fixing only the reads leaves the write landing in
the wrong scope, so the very first cross-session commit re-creates the ambiguity the reads were
taught to see through, and the second call is back where RE-ENTRY started.

What this file pins:
  (a) the three repro legs, red before the fix;
  (b) both refusal gates answering the SAME verdict same-session and cross-session, with their
      DELIBERATE seam difference (`brainstorm_progress` vs the durable `approved_approach` Handoff)
      preserved — each is driven by its own evidence and neither is collapsed into the other;
  (c) a SECOND cross-session commit still keying to the same pipeline (the single-shot is gone);
  (d) FAIL-CLOSED: an ambiguous repo still refuses to guess, still needs a human, and still keys to
      the narrowest id there is — and no gate is weaker than it is same-session.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval as A
from mokata import session as S
from mokata.badge_run import resolve_run_for_evidence
from mokata.brainstorm import (Approach, BrainstormSession, load_approved_approach,
                               save_brainstorm_progress)
from mokata.brainstorm_impact import DesignFitVerdict
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.knowledge.query import QueryResult, Reference
from mokata.mcp import tools_read as TR
from mokata.mcp import tools_spec as TS
from mokata.mcp.consent import _evidence_store
from mokata.session_save import register_run

C1 = [{"id": "AC1", "text": "the thing works"}]
T1 = [{"name": "test_thing", "ac_ids": ["AC1"]}]
C2 = C1 + [{"id": "AC2", "text": "and the other thing"}]
T2 = T1 + [{"name": "test_other", "ac_ids": ["AC2"]}]


# --------------------------------------------------------------------------- fixtures
def _enter_session(session_id):
    """Become a NEW MCP process: a freshly minted session identity, no `MOKATA_SESSION_ID` pin.
    The pin is deliberately unused — pinning would hand every resolver the answer and hide exactly
    the churn under test."""
    S.set_for_test(S.Session(session_id=session_id, started_at="2026-07-27T00:00:00Z",
                             started_monotonic=0.0, pid=os.getpid()))


def _human_approves(root, pid):
    """The out-of-band act: what `mokata approve <id>` does, in the human's own process."""
    return A.approve(root, pid, actor="human",
                     ledger=AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")))


def _handoff(prior_art_ran=True):
    return {"schema_version": 1, "phase": "brainstorm", "topic": "state-scope",
            "approach": {"name": "a", "summary": "do it", "tradeoffs": [], "decisions": []},
            "answered_questions": [], "grounding": {}, "approver": "jas",
            "approved_at": "2026-07-27T00:00:00Z",
            "prior_art": {"ran": prior_art_ran, "approach": "a", "findings": [],
                          "verdict": "none"},
            "domains": []}


def _pipeline_repo(run_id="runA", *, prior_art_ran=True, emit=True):
    """A repo carrying a REAL pipeline run: registered, an approved-approach Handoff on record, and
    (by default) a spec emitted through the full human round-trip. Exactly the state a user is
    sitting on when they go back to brainstorm."""
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    _enter_session(run_id)
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, run_id).ensure_registered()
    surface.state.write("approved_approach", _handoff(prior_art_ran))
    if emit:
        out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a")
        _human_approves(d, out["proposal_id"])
        TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a",
                     proposal_id=out["proposal_id"])
    return d


def _re_enter(root, session_id):
    """What `/mokata:brainstorm`'s protocol-start does on RE-ENTRY: a new process, which registers
    its own bare `pipeline_run__<sid>` checkpoint (RUN-REG)."""
    _enter_session(session_id)
    register_run(Surface.load(root))


def _commit(root, **kw):
    """One full human round-trip through `spec_emit` — propose, a HUMAN approves out-of-band,
    re-call with the id. Returns the committed result."""
    out = TS.spec_emit(path=root, **kw)
    if out.get("status") != "proposed":
        return out
    _human_approves(root, out["proposal_id"])
    return TS.spec_emit(path=root, proposal_id=out["proposal_id"], **kw)


# ---------------------------------------------------------------- a degraded chosen radius
class _Layer:
    """A stand-in graph pinned to the GREP FLOOR — `graph_degraded` is the query-level signal the
    GR.S3 refusal keys on (mirrors `test_gr_s3_consumers._Layer`)."""

    uses_graph = False
    backend_name = "grep"

    def blast_radius(self, symbol, depth=2):
        return QueryResult("blast_radius", symbol,
                           references=[Reference("app/pay.py", 5, snippet="", symbol="charge")],
                           backend=self.backend_name, degraded=True)


def _degraded_brainstorm():
    s = BrainstormSession("how to bill")
    s.propose_approaches([Approach("a", "approach a", pros=["fast"], cons=["risky"],
                                   targets=["pay"]),
                          Approach("b", "approach b", pros=["safe"], cons=["slow"],
                                   targets=["pay"])])
    s.assess_impacts(layer=_Layer())
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    s.approve("jas", "a")
    return s


class _Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(S.reset_for_test)


# ======================================================================================
# (a) leg 1 — the Handoff is invisible across a session boundary
# ======================================================================================

class TestReproHandoffInvisible(_Base):

    def test_the_resolved_store_sees_the_pipelines_own_handoff(self):
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        store, run_id = _evidence_store(Surface.load(d), d)
        self.assertEqual(run_id, "runA")
        self.assertIsNotNone(load_approved_approach(store),
                             "the pipeline's approved approach is the evidence every downstream "
                             "gate reads — a re-entered session must not be blind to it")

    def test_amend_from_a_second_session_reaches_the_consent_boundary(self):
        """The live wedge: the spec `spec_amend` would amend is filed under sessA, so sessB blocked
        at `spec-persisted` and never got as far as a proposal a human could approve."""
        d = _pipeline_repo("runY")
        _re_enter(d, "runZ")
        out = TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="a", reason="r")
        self.assertEqual(out["status"], "proposed",
                         "there IS a spec on this pipeline to amend — the amendment must reach the "
                         "human gate, not die on a state-scope miss")
        self.assertIn("proposal_id", out)

    def test_the_abort_road_out_clears_an_amendment_raised_cross_session(self):
        """P16 — the regressed answer NAMES `mokata spec amend --abort`, so the amendment record
        must land on the run that command resolves. Opened on the writing PROCESS it would block
        nothing and could never be closed by the road out the answer names."""
        from mokata.cli_commands.spec import _run_scoped_store
        from mokata.engine.amend import abort_amend
        from mokata.spec_scope import amend_from_state, amend_key

        d = _pipeline_repo("runY")
        _re_enter(d, "runZ")
        self.assertEqual(TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="a",
                                       reason="r")["status"], "proposed")
        store, run_id, err = _run_scoped_store(Surface.load(d))
        self.assertIsNone(err)
        self.assertEqual(run_id, "runY")
        self.assertTrue(amend_from_state(store.read(amend_key(run_id))).is_open,
                        "the amendment regressed the PIPELINE — that is the enforcement")
        self.assertTrue(abort_amend(store, run_id))
        self.assertFalse(amend_from_state(store.read(amend_key(run_id))).is_open)

    def test_decompose_reads_the_pipelines_spec_after_a_re_entry(self):
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        out = TR.decompose(path=d)
        self.assertTrue(out.get("available"),
                        "decompose derives its split from the run's emitted ACs — a re-entered "
                        "session must see the same spec the gates enforce")


# ======================================================================================
# (a) leg 2 — BOTH refusal gates go silent across a session boundary
# ======================================================================================

class TestReproRefusalGatesGoSilent(_Base):
    """Neither gate MIS-fires; both stop firing. Each is verified same-session AND cross-session,
    so the pin is the EQUALITY of the two verdicts, not just the cross-session one."""

    # --- GR-PA-WIRE: the durable `approved_approach` Handoff -----------------------------------
    def test_prior_art_refuses_same_session(self):
        d = _pipeline_repo("runA", prior_art_ran=False, emit=False)
        out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a")
        self.assertEqual(out["gate"], "prior-art")
        self.assertFalse(out["committed"])

    def test_prior_art_still_refuses_cross_session(self):
        d = _pipeline_repo("runA", prior_art_ran=False, emit=False)
        _re_enter(d, "runB")
        out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a")
        self.assertEqual(out.get("gate"), "prior-art",
                         "a gate that quietly is not there is worse than one that blocks — the "
                         "prior-art step did not run for the approach this spec comes from")
        self.assertFalse(out["committed"])

    # --- GR.S3: the resume-state `brainstorm_progress` (the DELIBERATE seam difference) ---------
    def test_graph_required_refuses_same_session(self):
        d = _pipeline_repo("runA", emit=False)
        save_brainstorm_progress(_degraded_brainstorm(), Surface.load(d).state)
        out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a")
        self.assertEqual(out["gate"], "graph-required")
        self.assertFalse(out["committed"])

    def test_graph_required_still_refuses_cross_session(self):
        d = _pipeline_repo("runA", emit=False)
        save_brainstorm_progress(_degraded_brainstorm(), Surface.load(d).state)
        _re_enter(d, "runB")
        out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a")
        self.assertEqual(out.get("gate"), "graph-required",
                         "the chosen approach's blast radius is still a lexical estimate after a "
                         "re-entry — the run did not change, only the process did")
        self.assertFalse(out["committed"])

    def test_the_two_gates_keep_their_separate_evidence(self):
        """The seam difference is PRESERVED, not collapsed. GR.S3 reads `brainstorm_progress`;
        GR-PA-WIRE reads the durable `approved_approach` Handoff (GR.S3-HOLE is why: the progress
        key survives to emit only because `clear_brainstorm_progress` has no production caller).
        Each fires on ITS OWN evidence with the other's absent."""
        # progress present + a HEALTHY handoff -> only graph-required fires.
        d = _pipeline_repo("runA", emit=False)
        save_brainstorm_progress(_degraded_brainstorm(), Surface.load(d).state)
        _re_enter(d, "runB")
        self.assertEqual(TS.spec_emit(path=d, title="s", criteria=C1, tests=T1,
                                      approach="a")["gate"], "graph-required")

        # a not-ran handoff + NO progress at all -> only prior-art fires.
        d2 = _pipeline_repo("runA", prior_art_ran=False, emit=False)
        _re_enter(d2, "runB")
        self.assertEqual(TS.spec_emit(path=d2, title="s", criteria=C1, tests=T1,
                                      approach="a")["gate"], "prior-art")


# ======================================================================================
# (a) leg 3 / (c) — the keying fix is no longer single-shot
# ======================================================================================

class TestReproSingleShotKeying(_Base):

    def test_the_first_cross_session_commit_does_not_create_a_second_evidence_run(self):
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        out = _commit(d, title="s2", criteria=C2, tests=T2, approach="a")
        self.assertEqual(out["status"], "committed")
        self.assertEqual(resolve_run_for_evidence(d), "runA",
                         "the commit must land under the run it was keyed to — a spec written into "
                         "the WRITING session's scope is a second pipeline mokata then cannot "
                         "choose between")

    def test_a_second_cross_session_emit_still_keys_to_the_same_pipeline(self):
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        self.assertEqual(_commit(d, title="s2", criteria=C2, tests=T2,
                                 approach="a")["status"], "committed")

        _re_enter(d, "runC")
        first = TS.spec_emit(path=d, title="s3", criteria=C2, tests=T2,
                             approach="a")["proposal_id"]
        _re_enter(d, "runD")
        second = TS.spec_emit(path=d, title="s3", criteria=C2, tests=T2,
                              approach="a")["proposal_id"]
        self.assertEqual(first, second,
                         "one write, one proposal — after the FIRST cross-session commit the key "
                         "used to degrade back to per-process, which is the loop returning")
        self.assertEqual(len([p for p in A.pending(d) if p.tool == "spec_emit"]), 1)

    def test_a_second_cross_session_commit_lands_on_the_same_run(self):
        from mokata.engine.spec_gate import load_emitted_spec
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        self.assertEqual(_commit(d, title="s2", criteria=C2, tests=T2,
                                 approach="a")["status"], "committed")
        _re_enter(d, "runC")
        self.assertEqual(_commit(d, title="s3", criteria=C2, tests=T2,
                                 approach="a")["status"], "committed")

        self.assertEqual(resolve_run_for_evidence(d), "runA")
        store, run_id = _evidence_store(Surface.load(d), d)
        self.assertEqual(run_id, "runA")
        self.assertEqual(load_emitted_spec(store).title, "s3",
                         "both cross-session commits landed on the pipeline's own run, so the run "
                         "carries the LATEST spec — not one spec per window")

    def test_spec_show_agrees_with_what_the_write_landed(self):
        """G1's seam and the write seam must answer about the SAME run — a reader handed a foreign
        spec is the failure G1 exists to close."""
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        self.assertEqual(_commit(d, title="s2", criteria=C2, tests=T2,
                                 approach="a")["status"], "committed")
        shown = TR.spec_show(path=d)
        self.assertTrue(shown["available"])
        self.assertEqual(shown["run"], "runA")
        self.assertEqual(shown["title"], "s2")


# ======================================================================================
# (d) fail-CLOSED — nothing here may let a write through, or weaken a gate
# ======================================================================================

class TestFailClosed(_Base):

    def _two_pipelines(self):
        d = _pipeline_repo("runA")
        _enter_session("runB")                        # a SECOND real pipeline, evidence and all
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, "runB").ensure_registered()
        surface.state.write("emitted_spec", {"title": "other", "criteria": [], "version": 1})
        return d

    def test_an_ambiguous_repo_resolves_to_nothing(self):
        d = self._two_pipelines()
        self.assertIsNone(resolve_run_for_evidence(d),
                          "two runs hold evidence and neither is pinned — mokata refuses rather "
                          "than picks a window")

    def test_an_ambiguous_repo_scopes_to_the_narrowest_id_and_still_needs_a_human(self):
        d = self._two_pipelines()
        _, run_id = _evidence_store(Surface.load(d), d)
        self.assertEqual(run_id, "runB",
                         "unresolvable means this PROCESS's own id — the narrowest scope there "
                         "is, and exactly today's behaviour. Never a guessed pipeline")
        out = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="a")
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])
        self.assertEqual(A.load(d, out["proposal_id"]).run_id, "runB")

    def test_the_store_scope_and_the_approval_key_are_one_answer(self):
        """The invariant the pair rests on: whatever run the proposal is filed under is the run the
        state is scoped to. If these could disagree, a gate would read one pipeline's evidence and
        license another's write."""
        from mokata.mcp.consent import _approval_run
        for setup in (lambda: _pipeline_repo("runA"),
                      lambda: self._two_pipelines(),
                      lambda: _pipeline_repo("runA", emit=False)):
            d = setup()
            _, run_id = _evidence_store(Surface.load(d), d)
            self.assertEqual(run_id, _approval_run(d))

    def test_an_explicit_pin_still_wins(self):
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "pinned"}):
            _, run_id = _evidence_store(Surface.load(d), d)
            self.assertEqual(run_id, "pinned")

    def test_a_standalone_repo_is_byte_identical_to_the_process_scope(self):
        """No run state at all — the honest standalone spec. The resolved store must be exactly
        `surface.state`, so a repo that never brainstormed is untouched by this stage."""
        d = tempfile.mkdtemp()
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
        _enter_session("solo")
        surface = Surface.load(d)
        store, run_id = _evidence_store(surface, d)
        self.assertEqual(run_id, "solo")
        self.assertEqual(store.path("emitted_spec"), surface.state.path("emitted_spec"))

    def test_the_model_still_cannot_mint_consent_across_a_boundary(self):
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        out = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="a",
                           approve=True, confirm=True)
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])

    def test_a_cross_session_approval_still_licenses_exactly_one_write(self):
        d = _pipeline_repo("runA")
        pid = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2,
                           approach="a")["proposal_id"]
        _human_approves(d, pid)
        _re_enter(d, "runB")
        first = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="a",
                             proposal_id=pid)
        self.assertEqual(first["status"], "committed")
        second = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="a",
                              proposal_id=pid)
        self.assertEqual(second["status"], "refused")
        self.assertEqual(second["reason_code"], A.REFUSED_USED,
                         "with the write landing on the pipeline's own run, the burn is now the "
                         "SPECIFIC answer — RE-ENTRY could only promise the write was refused")

    def test_a_healthy_pipeline_is_not_refused_by_either_gate(self):
        """The negative that keeps the two gates honest: a prior-art-ran Handoff and no degraded
        radius must still emit cleanly after a re-entry."""
        d = _pipeline_repo("runA", emit=False)
        _re_enter(d, "runB")
        out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="a")
        self.assertEqual(out["status"], "proposed")
        self.assertNotIn(out.get("gate"), ("prior-art", "graph-required"))


if __name__ == "__main__":
    unittest.main()
