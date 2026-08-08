"""RE-ENTRY — the approval chain keys to the PIPELINE's run, not to the OS process (0.0.16).

The live bug (Jas, 2026-07-27): go back to brainstorm on an EXISTING run, re-emit the spec, and
every request walks the human to a terminal again. The MCP "goes in hand state, doesn't return
anything" — a healthy WAIT that has become an endless one.

The root, reproduced below rather than assumed:

    proposal_id = "p-" + sha256(run_id \\0 content_hash)[:12]      and      run_id == session_id

`session.current_run_id()` mints a fresh `uuid4` per PROCESS. So the id under which a proposal is
recorded is a function of which MCP process happened to be alive when the model called. Restart the
server — or re-enter the pipeline from a second window — and the SAME human, in the SAME repo,
approving the SAME content, produces a DIFFERENT id every time:

  * a genuinely human-approved write is `REFUSED_OTHER_SESSION` on the re-call (leg A);
  * re-proposing it mints a new id per session, so the pending store fills with N proposals for ONE
    write and the human is asked N times (leg A);
  * meanwhile an amendment in flight has REGRESSED the run, and the answer to the next call never
    says that another proposal is already the one blocking it (leg B).

This is the residual REVIEW-FIX.R1 filed. R1 fixed exactly this class for the review verdict by
making the record-key and the read-key one session-aware resolution
(`run_resolver.resolve_verdict_run`); this stage extends that seam to the approval chain.

What these tests pin:
  (a) the two repro legs, red before the fix;
  (b) same content + same pipeline + a NEW session -> the SAME proposal id (the fix);
  (c) different content -> a DIFFERENT id (content-binding is UNTOUCHED — the whole point);
  (d) an approval still licenses EXACTLY ONE write (single-use is UNTOUCHED);
  (e) an unresolvable run still refuses — nothing here lets a write through without a human;
  (f) the regressed-run answer NAMES the wedge and both roads out, from the ONE shared builder.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval as A
from mokata import session as S
from mokata.awaiting import (AMEND_ABORT_CMD, AMEND_REGRESSED_NOTE, APPROVE_CMD, AWAITING,
                             awaiting_block)
from mokata.run_resolver import resolve_run_id
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.mcp import tools_spec as TS
from mokata.session_save import register_run
from mokata.spec_scope import amend_from_state, amend_key
from mokata.state import StateStore
from mokata.tdd_state import state_dir

# One spec, and a widened one — the shapes the re-entry flow actually sends.
C1 = [{"id": "AC1", "text": "the thing works"}]
T1 = [{"name": "test_thing", "ac_ids": ["AC1"]}]
C2 = C1 + [{"id": "AC2", "text": "and the other thing"}]
T2 = T1 + [{"name": "test_other", "ac_ids": ["AC2"]}]


# --------------------------------------------------------------------------- fixtures
def _enter_session(session_id):
    """Become a NEW MCP process: a freshly minted session identity, and NO `MOKATA_SESSION_ID`
    pin — the field default. The pin is deliberately NOT used here: pinning it would hand every
    resolver the answer and hide the exact churn under test (the SI.3 fixture pins on purpose, for
    the opposite reason — it needs the tool process and the CLI process to agree)."""
    S.set_for_test(S.Session(session_id=session_id, started_at="2026-07-27T00:00:00Z",
                             started_monotonic=0.0, pid=os.getpid()))


def _human_approves(root, pid):
    """The out-of-band act: what `mokata approve <id>` does, in the human's own process."""
    return A.approve(root, pid, actor="human",
                     ledger=AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")))


def _pipeline_repo(run_id="runA"):
    """A repo carrying a REAL pipeline run: registered, an approved-approach Handoff on record, and
    a spec emitted through the full human round-trip. Exactly the state a user is sitting on when
    they go back to brainstorm."""
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    _enter_session(run_id)
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, run_id).ensure_registered()
    surface.state.write("approved_approach", {
        "schema_version": 1, "phase": "brainstorm", "topic": "re-entry",
        "approach": {"name": "A1", "summary": "do it", "tradeoffs": [], "decisions": []},
        "answered_questions": [], "grounding": {}, "approver": "jas",
        "approved_at": "2026-07-27T00:00:00Z",
        "prior_art": {"ran": True, "approach": "A1", "findings": [], "verdict": "none"},
        "domains": []})
    out = TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="A1")
    _human_approves(d, out["proposal_id"])
    TS.spec_emit(path=d, title="s", criteria=C1, tests=T1, approach="A1",
                 proposal_id=out["proposal_id"])
    return d


def _re_enter(root, session_id):
    """What `/mokata:brainstorm`'s protocol-start does on RE-ENTRY: a new process, which registers
    its own bare `pipeline_run__<sid>` checkpoint (RUN-REG). That extra checkpoint is precisely what
    makes a checkpoint-counting resolver lose the pipeline."""
    _enter_session(session_id)
    register_run(Surface.load(root))


class _Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(S.reset_for_test)


# ======================================================================================
# (a) the repro — RED before the fix
# ======================================================================================

class TestReproFreshSessionReEntry(_Base):
    """Leg (a) — an existing pipeline, then a NEW session. Today: a fresh run-bound id, and the
    human's own approval is refused as a stranger's."""

    def test_a_human_approval_survives_an_mcp_restart(self):
        d = _pipeline_repo()
        out = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1")
        pid = out["proposal_id"]
        self.assertEqual(_human_approves(d, pid).code, "approved")

        _re_enter(d, "runB")                      # the MCP server restarted / a second window
        redeemed = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1",
                                proposal_id=pid)
        self.assertEqual(redeemed.get("status"), "committed",
                         "the human APPROVED this exact content in this exact repo; an MCP "
                         "restart is not a change of human, of repo, or of content")
        self.assertNotEqual(redeemed.get("reason_code"), A.REFUSED_OTHER_SESSION)

    def test_re_proposing_the_same_content_does_not_mint_a_new_id_per_session(self):
        d = _pipeline_repo()
        first = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2,
                             approach="A1")["proposal_id"]
        _re_enter(d, "runB")
        second = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2,
                              approach="A1")["proposal_id"]
        _re_enter(d, "runC")
        third = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2,
                             approach="A1")["proposal_id"]

        self.assertEqual([first, first], [second, third],
                         "one write, one proposal — a new id per session is what walks the human "
                         "to the terminal again and again")
        self.assertEqual(len([p for p in A.pending(d) if p.tool == "spec_emit"]), 1,
                         "the pending store must not fill with one proposal per restart")


class TestReproAmendDismissLoop(_Base):
    """Leg (b) — propose an amendment, do NOT approve, re-call. The run is REGRESSED throughout,
    and today the next answer re-proposes blind: it never says another proposal is already the one
    blocking the run."""

    def test_a_re_call_names_the_proposal_already_awaiting_the_human(self):
        d = _pipeline_repo("runX")
        first = TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="A1",
                              reason="need more")
        self.assertEqual(first["status"], "proposed")
        pid1 = first["proposal_id"]

        store = Surface.load(d).state
        self.assertTrue(amend_from_state(store.read(amend_key("runX"))).is_open,
                        "the amendment regressed the run — that is the enforcement, not a bug")

        # The model re-calls with a REWORDED reason (a different write, so a different id — that is
        # content-binding working). The wedge is that the answer says nothing about pid1, which is
        # the proposal actually holding the run regressed.
        again = TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="A1",
                              reason="need more (rephrased)")
        self.assertNotEqual(again["proposal_id"], pid1)
        self.assertIn(pid1, again.get("awaiting_also_pending", []),
                      "re-proposing blind is the loop: the answer must NAME the write already "
                      "awaiting the human")
        self.assertIn("ALREADY", again["awaiting"],
                      "the shared head must say another write is already waiting")

    def test_the_regressed_answer_names_both_roads_out(self):
        d = _pipeline_repo("runX")
        out = TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="A1", reason="r")
        self.assertEqual(out["awaiting_blocks"], AMEND_REGRESSED_NOTE)
        self.assertEqual(out["awaiting_abort_command"], AMEND_ABORT_CMD)
        self.assertEqual(out["awaiting_approve_command"],
                         APPROVE_CMD.format(proposal_id=out["proposal_id"]))
        self.assertIn(AWAITING, out["awaiting"])


# ======================================================================================
# (b)/(c) the fix — a stable key that is still content-bound
# ======================================================================================

class TestTheKeyIsThePipelineNotTheProcess(_Base):
    """The resolver: which run does an approval belong to?"""

    def test_re_entry_resolves_the_run_that_holds_the_evidence(self):
        d = _pipeline_repo()
        _re_enter(d, "runB")
        self.assertEqual(resolve_run_id(d), "runA",
                         "a re-entered session registers a BARE checkpoint; the pipeline is the "
                         "run holding the approved approach and the emitted spec")

    def test_an_explicit_pin_still_wins(self):
        d = _pipeline_repo()
        _re_enter(d, "runB")
        with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "pinned"}):
            self.assertEqual(resolve_run_id(d), "pinned")

    def test_a_repo_with_no_pipeline_resolves_to_nothing(self):
        d = tempfile.mkdtemp()
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
        _enter_session("solo")
        self.assertIsNone(resolve_run_id(d),
                          "no pipeline evidence and no run state — there is nothing to resolve, "
                          "and the caller must not be handed a guess")

    def test_two_pipelines_are_ambiguous_and_resolve_to_nothing(self):
        d = _pipeline_repo("runA")
        _enter_session("runB")                      # a SECOND real pipeline, evidence and all
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, "runB").ensure_registered()
        surface.state.write("emitted_spec", {"title": "other", "criteria": [], "version": 1})
        self.assertIsNone(resolve_run_id(d),
                          "two runs hold evidence and neither is pinned — mokata refuses rather "
                          "than picks a window (the R1 discipline)")


class TestPropertiesPreserved(_Base):
    """A stable id must not cost a single existing property of the consent."""

    def test_different_content_is_a_different_proposal(self):
        d = _pipeline_repo()
        a = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1")["proposal_id"]
        _re_enter(d, "runB")
        b = TS.spec_emit(path=d, title="DIFFERENT", criteria=C2, tests=T2,
                         approach="A1")["proposal_id"]
        self.assertNotEqual(a, b, "change an argument, change the hash, change the id — "
                                  "'approve X then commit Y' stays arithmetically impossible")

    def test_an_approval_licenses_exactly_one_write(self):
        """Single-use, in the ordinary single-session shape where `already-used` is the specific
        answer. The burn is enforced by the store lock at redemption, not by the key, so a stable
        key cannot reach it — but it is the property the whole gate rests on, so it is pinned
        against the change that touches its substrate."""
        d = _pipeline_repo()
        pid = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2,
                           approach="A1")["proposal_id"]
        _human_approves(d, pid)
        first = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1",
                             proposal_id=pid)
        self.assertEqual(first["status"], "committed")
        second = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1",
                              proposal_id=pid)
        self.assertEqual(second["status"], "refused")
        self.assertEqual(second["reason_code"], A.REFUSED_USED)

    def test_a_cross_session_approval_still_licenses_exactly_one_write(self):
        d = _pipeline_repo()
        pid = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2,
                           approach="A1")["proposal_id"]
        _human_approves(d, pid)
        _re_enter(d, "runB")
        first = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1",
                             proposal_id=pid)
        self.assertEqual(first["status"], "committed")
        second = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1",
                              proposal_id=pid)
        self.assertEqual(second["status"], "refused")
        self.assertFalse(second["committed"])
        # It refuses as `already-used` — the SPECIFIC answer. Under RE-ENTRY alone this could only
        # promise "refused": the commit landed under THIS session's state scope, the repo then held
        # two evidence runs, and the resolver correctly declined to pick one, so the burn showed up
        # as `other-session` instead. STATE-SCOPE lands the commit on the pipeline's own run, so
        # there is still exactly one pipeline and the burn is what the human is told about.
        self.assertEqual(second["reason_code"], A.REFUSED_USED)


class TestTheRoadsOutActuallyWork(_Base):
    """Naming a road out that errors is the P16 failure the awaiting head exists to prevent. Both
    named roads are exercised for real after a re-entry."""

    def _abort(self, root):
        from mokata.cli_commands.spec import _run_scoped_store
        from mokata.engine.amend import abort_amend
        store, run_id, err = _run_scoped_store(Surface.load(root))
        return store, run_id, err, (abort_amend(store, run_id) if not err and run_id else None)

    def test_abort_resolves_the_pipeline_after_a_re_entry(self):
        d = _pipeline_repo("runY")
        out = TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="A1", reason="r")
        self.assertEqual(out["status"], "proposed")
        _re_enter(d, "runZ")                       # the bare checkpoint that used to break this
        _enter_session("runY")                     # back in the pipeline's own window
        store, run_id, err, aborted = self._abort(d)
        self.assertIsNone(err, "`mokata spec amend --abort` is the escape hatch the regressed-run "
                               "answer NAMES — it must not die on the re-entry checkpoint")
        self.assertEqual(run_id, "runY")
        self.assertTrue(aborted)
        self.assertFalse(amend_from_state(store.read(amend_key("runY"))).is_open,
                         "abort clears the regression — development writes are unblocked")

    def test_two_real_pipelines_still_refuse_rather_than_guess(self):
        d = _pipeline_repo("runA")
        _enter_session("runB")
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, "runB").ensure_registered()
        surface.state.write("emitted_spec", {"title": "other", "criteria": [], "version": 1})
        _, _, err, _ = self._abort(d)
        self.assertIsNotNone(err, "de-ambiguating the re-entry shape must not become guessing "
                                  "between two genuine pipelines")
        self.assertIn("will not guess", err)

    def test_an_explicit_run_id_still_short_circuits(self):
        from mokata.cli_commands.spec import _run_scoped_store
        d = _pipeline_repo("runA")
        _re_enter(d, "runB")
        _, run_id, err, = _run_scoped_store(Surface.load(d), "named-run")
        self.assertIsNone(err)
        self.assertEqual(run_id, "named-run", "HANDOFF.G1: naming a run and inferring one still "
                                              "travel one code path")


class TestTheFiledResidual(_Base):
    """RE-ENTRY.FU — filed here, BUILT by STATE-SCOPE (0.0.16, tracker row 9c). What RE-ENTRY left
    open was that `Surface.state` is scoped to `current_session_id()`, so a re-entered session
    resolved the pipeline's run and then read a DIFFERENT run's state. Two consequences were pinned
    here as visible facts; both are now closed, and the pins are kept — inverted — so a regression
    lands back on the class that predicted it:

      1. `spec_amend` from a second session could not reach the consent boundary at all — it
         blocked at `spec-persisted` ("no spec is emitted for this run"), because the spec it would
         amend was filed under the first session's id;
      2. the keying fix was therefore SINGLE-SHOT across a boundary: the first cross-session commit
         created a second evidence run, and resolution degraded back to per-process keying.

    The remedy was the discipline `cli_commands/spec.py:_run_scoped_store` already applied on the
    CLI side — resolve the run, then scope the store to it. `mcp/consent.py:_evidence_store` is the
    MCP twin, keyed off the SAME `_approval_run` seam, so the run a proposal is filed under IS the
    run the state is scoped to. The full behaviour is pinned in `test_state_scope.py`; what stays
    here is the RE-ENTRY-side claim each fact belonged to.

    MS.S2 scoping itself is UNTOUCHED — `Surface.state` still addresses this process's own scope,
    and that is still what an unresolvable repo falls back to."""

    def test_amend_from_a_second_session_now_reaches_the_consent_boundary(self):
        d = _pipeline_repo("runY")
        _re_enter(d, "runZ")
        out = TS.spec_amend(path=d, title="s", criteria=C2, tests=T2, approach="A1", reason="r")
        self.assertEqual(out["status"], "proposed",
                         "STATE-SCOPE: the amendment resolves the pipeline's own spec, so it "
                         "reaches the human gate instead of dying at `spec-persisted`")
        self.assertIn("proposal_id", out)

    def test_the_prior_art_emit_gate_no_longer_goes_silent_across_a_boundary(self):
        """Deliverable-5's finding, closed. `_prior_art_emit_refusal` reads the DURABLE
        `approved_approach` Handoff; read through the PROCESS's scope a second session saw no
        Handoff, read that as 'a standalone spec', and skipped the gate — it did not mis-fire, it
        stopped firing. It is now read through the resolved run, so the Handoff is visible."""
        from mokata.brainstorm import load_approved_approach
        from mokata.mcp.consent import _evidence_store
        d = _pipeline_repo("runY")
        self.assertIsNotNone(load_approved_approach(Surface.load(d).state))
        _re_enter(d, "runZ")
        # The RAW session-scoped store still cannot see it — MS.S2 is deliberately unchanged...
        self.assertIsNone(load_approved_approach(Surface.load(d).state))
        # ...but the store the MCP gates actually read is scoped to the PIPELINE's run.
        store, run_id = _evidence_store(Surface.load(d), d)
        self.assertEqual(run_id, "runY")
        self.assertIsNotNone(load_approved_approach(store),
                             "the gate must see the approach it gates on, from any session")

    def test_the_model_still_cannot_mint_consent(self):
        d = _pipeline_repo()
        _re_enter(d, "runB")
        out = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1",
                           approve=True, confirm=True)
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])

    def test_an_unresolvable_run_still_refuses_without_a_human(self):
        """FAIL-CLOSED: two pipelines on disk means no resolvable evidence run. The write must
        still need a human — it may never fall through to a commit."""
        d = _pipeline_repo("runA")
        _enter_session("runB")
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, "runB").ensure_registered()
        surface.state.write("emitted_spec", {"title": "other", "criteria": [], "version": 1})
        self.assertIsNone(resolve_run_id(d))

        out = TS.spec_emit(path=d, title="s2", criteria=C2, tests=T2, approach="A1")
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])
        # ...and the proposal is keyed to this PROCESS (the narrowest key there is), never to a run
        # mokata guessed: an unresolvable pipeline may not silently widen who can redeem.
        self.assertEqual(A.load(d, out["proposal_id"]).run_id, "runB")


# ======================================================================================
# (f) the ONE builder — no tool writes its own wording
# ======================================================================================

class TestTheSharedAwaitingHead(_Base):
    def test_also_pending_is_built_once_for_every_site(self):
        block = awaiting_block("p-new", "spec_amend", blocked_note=AMEND_REGRESSED_NOTE,
                               also_pending=["p-old1", "p-old2"])
        self.assertEqual(block["awaiting_also_pending"], ["p-old1", "p-old2"])
        self.assertIn("ALREADY", block["awaiting"])
        self.assertIn("p-old1", block["awaiting_also_pending_approve_commands"][0])
        self.assertEqual(block["awaiting_abort_command"], AMEND_ABORT_CMD)

    def test_a_lone_wait_says_nothing_about_others(self):
        block = awaiting_block("p-new", "memory_add")
        self.assertNotIn("awaiting_also_pending", block)
        self.assertNotIn("ALREADY", block["awaiting"])

    def test_no_spec_tool_writes_its_own_awaiting_wording(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "src", "mokata", "mcp", "tools_spec.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(AWAITING, text,
                         "the awaiting head is built in ONE place (awaiting.awaiting_block) so "
                         "all propose sites inherit it — a tool must never reword it")


if __name__ == "__main__":
    unittest.main()
