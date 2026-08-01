"""SPEC-REEMIT-CLOBBER — a second `spec_emit` must not silently destroy the first spec (0.0.16).

Jas, live, 2026-07-27: "the same brainstorm might have multiple specs as we review and refine
code." That flow is real and it is supported — but until this stage it was DESTRUCTIVE. `spec_emit`
had no check for an existing `emitted_spec`, and `mcp/tools_spec.py` called
`spec_commit(store, spec)` with neither `version` nor `archive_key`. `engine/emit.py:spec_commit`
archives the prior spec ONLY when a caller PASSES `archive_key` — which `spec_amend`'s
`finish_amend` does and `spec_emit` never did. So a second emit OVERWROTE `emitted_spec`: no
version bump, no archive, the prior spec's history simply gone.

Three measured losses, reproduced below rather than assumed (all RED before the fix):

  1. THE PRIOR SPEC IS UNRECOVERABLE. After emit A then emit B, nothing on disk holds A.
  2. THE VERSION DOES NOT BUMP — worse, `spec_commit`'s `version=1` DEFAULT actively REWINDS it, so
     a re-emit onto a v3 spec writes "v1" over it.
  3. THAT REWIND POISONS AMEND'S ARCHIVE. Because the counter is back at 1, the NEXT `spec_amend`
     archives at `spec_archive__<run>__v1` — the key an earlier amendment already used — so the
     re-emit destroys a spec a second time, this time one that had been correctly preserved.

Urgency: RE-ENTRY + STATE-SCOPE just made this call succeed from a SECOND session, so the clobber
path is about to get more traffic than it has ever had.

THE CONSENT LINE THIS FILE PINS
-------------------------------
A re-emit that REPLACES a spec is a bigger act than the first emit, and it must not become a way to
change scope around `spec_amend`'s ladder (completeness · blast-radius-on-widen · DeviationGate
(SCOPE) · the `red_owed` high-water mark). The line is drawn at WORK IN FLIGHT, because that is
precisely what every rung of that ladder protects:

  (a) REPLACE, previewed — while the run is still in the SPEC phase (no test has ever been recorded
      RED for it and no amendment is open). Nothing has been built against the spec, so replacing it
      costs only history, and the archive keeps that. It stays ONE human-gated commit, but the
      proposal NAMES what it replaces (version, title change, AC delta, scope delta) so the human
      approves the replacement knowingly rather than approving "an emit".
  (b) REFUSE and route to `spec_amend` — once the run has produced a RED, or an amendment is already
      open. A red test IS work in flight against vN; changing the spec now is a scope change against
      a boundary already in force, and amend is the tool that regresses the run, re-computes the
      blast radius when scope widens, ledgers the diff, and makes new criteria owe a RED. A re-emit
      would land the same change with none of it.

Belt and braces: the ARCHIVE lives in the one committer (`spec_commit` derives it when a caller
says nothing), so even a surface that forgets the refusal cannot lose a spec.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval as A
from mokata import session as S
from mokata import tdd_state as TD
from mokata.brainstorm import (Approach, BrainstormSession, save_brainstorm_progress)
from mokata.brainstorm_impact import DesignFitVerdict
from mokata.config import Surface
from mokata.engine.emit import REEMIT_GATE, reemit_verdict, spec_version
from mokata.engine.spec_gate import SPEC_STATE_KEY, load_emitted_spec
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.knowledge.query import QueryResult, Reference
from mokata.mcp import tools_read as TR
from mokata.mcp import tools_spec as TS
from mokata.mcp.consent import _evidence_store
from mokata.session_save import register_run
from mokata.spec_scope import archive_key

# Spec A (one AC) and spec B (two) — a genuine refinement of the same brainstorm, not a rename.
A_C = [{"id": "AC1", "text": "slugify keeps unicode"}]
A_T = [{"name": "test_unicode", "ac_ids": ["AC1"]}]
B_C = A_C + [{"id": "AC2", "text": "slugify trims"}]
B_T = A_T + [{"name": "test_trims", "ac_ids": ["AC2"]}]
C_C = B_C + [{"id": "AC3", "text": "slugify of empty raises"}]
C_T = B_T + [{"name": "test_empty", "ac_ids": ["AC3"]}]


# --------------------------------------------------------------------------- fixtures
def _enter_session(session_id):
    """Become a NEW MCP process (mirrors `test_state_scope._enter_session`): a freshly minted
    session identity, deliberately with no `MOKATA_SESSION_ID` pin — pinning would hand every
    resolver the answer and hide exactly the run-identity churn under test."""
    S.set_for_test(S.Session(session_id=session_id, started_at="2026-07-27T00:00:00Z",
                             started_monotonic=0.0, pid=os.getpid()))


def _human_approves(root, pid):
    """The out-of-band act: what `mokata approve <id>` does, in the human's own process."""
    return A.approve(root, pid, actor="human",
                     ledger=AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")))


def _handoff(prior_art_ran=True):
    return {"schema_version": 1, "phase": "brainstorm", "topic": "re-emit",
            "approach": {"name": "a", "summary": "do it", "tradeoffs": [], "decisions": []},
            "answered_questions": [], "grounding": {}, "approver": "jas",
            "approved_at": "2026-07-27T00:00:00Z",
            "prior_art": {"ran": prior_art_ran, "approach": "a", "findings": [],
                          "verdict": "none"},
            "domains": []}


def _repo(run_id="runA", *, prior_art_ran=True):
    """A repo carrying a REAL pipeline run: registered, an approved-approach Handoff on record,
    and NO spec yet."""
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    _enter_session(run_id)
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, run_id).ensure_registered()
    surface.state.write("approved_approach", _handoff(prior_art_ran))
    return d


def _emit(root, criteria, tests, **kw):
    """One full human round-trip through `spec_emit` — propose, a HUMAN approves out-of-band, the
    model redeems by id. Returns the committed result (or the non-proposal outcome as-is)."""
    args = dict(path=root, title="slugify", criteria=criteria, tests=tests, approach="a")
    args.update(kw)
    out = TS.spec_emit(**args)
    if out.get("status") != "proposed":
        return out
    _human_approves(root, out["proposal_id"])
    return TS.spec_emit(proposal_id=out["proposal_id"], **args)


def _store(root):
    return _evidence_store(Surface.load(root), root)


def _re_enter(root, session_id):
    """What `/mokata:brainstorm`'s protocol-start does on RE-ENTRY: a new process, which registers
    its own bare `pipeline_run__<sid>` checkpoint (RUN-REG)."""
    _enter_session(session_id)
    register_run(Surface.load(root))


def _go_red(root, run_id, test_name="test_unicode"):
    """WORK IN FLIGHT: a test recorded FAILING for this run — SI.2's own high-water mark, and the
    evidence the (b) branch of the consent line keys on."""
    store, _rid = _store(root)
    TD.record(store, run_id, red=[test_name])


class _Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(S.reset_for_test)


# ======================================================================================
# 1 — REPRO: the clobber (all three legs RED before the fix)
# ======================================================================================

class TestReproTheClobber(_Base):

    def test_the_first_spec_is_unrecoverable_after_a_second_emit(self):
        """THE defect, as a test. Emit A, emit a DIFFERENT spec B on the same run, then go looking
        for A: today it is nowhere — not at `emitted_spec`, not in any archive."""
        d = _repo("runA")
        self.assertTrue(_emit(d, A_C, A_T)["committed"])
        self.assertTrue(_emit(d, B_C, B_T)["committed"])

        store, run_id = _store(d)
        self.assertEqual(len(load_emitted_spec(store).criteria), 2, "B is live — that much worked")
        archived = store.read(archive_key(run_id, 1))
        self.assertIsNotNone(archived,
                             "the spec A that B replaced must still exist — a re-emit that "
                             "silently destroys the run's prior spec is data loss, not a refinement")
        self.assertEqual([c["id"] for c in archived["criteria"]], ["AC1"],
                         "the archive must hold the spec that WAS there, byte for byte")

    def test_the_version_bumps_instead_of_rewinding(self):
        """Leg 2. `spec_commit`'s `version=1` default does not merely fail to bump — it REWINDS,
        so a re-emit onto a v3 spec writes "v1" over it."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _emit(d, B_C, B_T)
        store, _rid = _store(d)
        self.assertEqual(spec_version(store), 2,
                         "the second spec on a run is v2 — a re-emit that leaves the counter at 1 "
                         "makes every later version claim a lie")

    def test_a_later_amend_no_longer_overwrites_an_earlier_archive(self):
        """Leg 3 — the compounding loss. The rewind poisons AMEND's archive key: with the counter
        back at 1, the next amendment archives at `spec_archive__<run>__v1`, the very key the
        re-emit's own supersede used. One spec is then destroyed twice."""
        d = _repo("runA")
        _emit(d, A_C, A_T)                                   # v1 = A
        _emit(d, B_C, B_T)                                   # v2 = B, A archived at v1
        out = TS.spec_amend(path=d, title="slugify", criteria=C_C, tests=C_T, approach="a",
                            reason="release the empty-string case")
        self.assertEqual(out["status"], "proposed")
        _human_approves(d, out["proposal_id"])
        out = TS.spec_amend(path=d, title="slugify", criteria=C_C, tests=C_T, approach="a",
                            reason="release the empty-string case",
                            proposal_id=out["proposal_id"])
        self.assertTrue(out["committed"])
        self.assertEqual(out["version"], 3, "the amendment follows the re-emit's v2, not a reset v1")

        store, run_id = _store(d)
        v1 = store.read(archive_key(run_id, 1))
        v2 = store.read(archive_key(run_id, 2))
        self.assertEqual([c["id"] for c in v1["criteria"]], ["AC1"],
                         "v1 (spec A) must survive the amendment — the rewind used to make the "
                         "amendment archive B on top of A")
        self.assertEqual([c["id"] for c in v2["criteria"]], ["AC1", "AC2"], "v2 is spec B")
        self.assertEqual(len(load_emitted_spec(store).criteria), 3, "v3 is live")


# ======================================================================================
# 2 — the consent line, branch (a): REPLACE, and NAME what is replaced
# ======================================================================================

class TestTheReplacementIsNamed(_Base):
    """A re-emit before any work is in flight stays ONE human-gated commit — but the human must be
    approving a REPLACEMENT, not "an emit"."""

    def test_a_re_emit_before_any_red_still_reaches_the_human_gate(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        self.assertEqual(out["status"], "proposed",
                         "refining the spec of a brainstorm that has produced no code yet is the "
                         "flow Jas named — it must not be refused")

    def test_the_proposal_names_the_version_the_title_and_the_ac_delta(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        out = TS.spec_emit(path=d, title="slugify v2", criteria=B_C, tests=B_T, approach="a")
        # The preview lives on the PROPOSAL — it is what `mokata approve <id>` renders into the
        # human's own terminal (approval.render), which is the surface that has to carry this.
        preview = A.load(d, out["proposal_id"]).preview
        self.assertIn("REPLACES v1", preview, "the human must see WHICH version this destroys")
        self.assertIn("slugify", preview, "…and the title it is replacing")
        self.assertIn("AC2", preview, "…and the criteria delta")
        self.assertIn(archive_key("runA", 1), preview,
                      "…and where the superseded spec is kept, so 'nothing is lost' is checkable")
        self.assertEqual(out.get("replaces_version"), 1)
        self.assertEqual(out.get("to_version"), 2)
        self.assertIn("replac", A.load(d, out["proposal_id"]).summary.lower(),
                      "the one-line summary a human reads at the terminal must say REPLACE")

    def test_the_committed_result_names_the_archive(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        out = _emit(d, B_C, B_T)
        self.assertTrue(out["committed"])
        self.assertEqual(out.get("version"), 2)
        self.assertEqual(out.get("superseded"), archive_key("runA", 1))

    def test_the_supersede_is_on_the_audit_ledger(self):
        """Reachability (deliverable 5): no archive-READ tool ships in this stage, so the audit
        trail is what points at the archive — exactly as `spec_amend` already does."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _emit(d, B_C, B_T)
        kinds = [e for e in AuditLedger.from_mokata_dir(os.path.join(d, ".mokata")).entries()
                 if e.get("kind") == "spec_reemit"]
        self.assertTrue(kinds, "a superseded spec must be findable from `mokata audit`")
        self.assertEqual(kinds[-1]["superseded"], archive_key("runA", 1))
        self.assertEqual((kinds[-1]["from_version"], kinds[-1]["to_version"]), (1, 2))

    def test_the_human_sees_the_replacement_named_in_their_own_terminal(self):
        """The preview must survive `approval.render`'s 20-line truncation — which is exactly why
        the REPLACES block leads rather than trails the spec body."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        out = TS.spec_emit(path=d, title="slugify", criteria=C_C, tests=C_T, approach="a")
        rendered = A.render(A.load(d, out["proposal_id"]))
        self.assertIn("REPLACES v1", rendered,
                      "a human reading the approve screen must see they are replacing a spec")

    def test_a_first_emit_is_untouched(self):
        """The (a) path must be byte-identical for the FIRST emit — no version noise, no archive
        key, no replacement wording on a run that has no prior spec."""
        d = _repo("runA")
        out = TS.spec_emit(path=d, title="slugify", criteria=A_C, tests=A_T, approach="a")
        self.assertNotIn("REPLACES", A.load(d, out["proposal_id"]).preview)
        self.assertIsNone(out.get("replaces_version"))
        committed = _emit(d, A_C, A_T)
        self.assertTrue(committed["committed"])
        self.assertIsNone(committed.get("superseded"))
        store, _rid = _store(d)
        self.assertEqual(spec_version(store), 1)


# ======================================================================================
# 3 — the consent line, branch (b): work in flight routes to `spec_amend`
# ======================================================================================

class TestWorkInFlightRoutesToAmend(_Base):

    def test_a_re_emit_after_a_red_is_refused(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _go_red(d, "runA")
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        self.assertEqual(out["gate"], REEMIT_GATE)
        self.assertFalse(out["committed"])
        self.assertNotIn("proposal_id", out, "there is nothing here for a human to approve — the "
                                             "road forward is a different tool, not a signature")
        self.assertIn("spec_amend", out["reason"] + out.get("hint", ""),
                      "the refusal must NAME the road back (P16)")

    def test_the_refused_re_emit_wrote_nothing(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _go_red(d, "runA")
        TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        store, _rid = _store(d)
        self.assertEqual([c.id for c in load_emitted_spec(store).criteria], ["AC1"],
                         "the spec in force is untouched by a refused re-emit")
        self.assertEqual(spec_version(store), 1)

    def test_a_scope_widening_re_emit_cannot_walk_around_the_ladder(self):
        """The exact abuse this branch exists for: an agent blocked by the scope hook re-emitting
        with a wider `authorized` list. That is a scope change under the EMIT trust dial, with no
        blast radius, no ledgered diff, and no RED owed."""
        d = _repo("runA")
        _emit(d, A_C, A_T, scope={"authorized": ["src/slug.py"]})
        _go_red(d, "runA")
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a",
                           scope={"authorized": ["src/slug.py", "src/*"]})
        self.assertEqual(out["gate"], REEMIT_GATE)
        store, _rid = _store(d)
        self.assertEqual(load_emitted_spec(store).scope.authorized, ("src/slug.py",),
                         "the authorized surface the hook enforces must not widen through emit")

    def test_a_re_emit_while_an_amendment_is_open_is_refused(self):
        """A re-emit mid-amendment would land the very change the amendment is being judged for,
        around the judgement — and would leave the regression marker pointing at a spec that is no
        longer there."""
        from mokata.engine.amend import open_amend
        d = _repo("runA")
        _emit(d, A_C, A_T)
        store, run_id = _store(d)
        open_amend(store, run_id, reason="widen", from_version=1)
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        self.assertEqual(out["gate"], REEMIT_GATE)
        self.assertTrue(out["amend_open"])
        self.assertIn("--abort", out.get("hint", ""), "name BOTH ways out of an open amendment")

    def test_amend_still_lands_exactly_where_the_re_emit_refused(self):
        """The refusal is a re-route, not a wedge: the road it names actually works, and it still
        carries the whole ladder (vN+1, the ledgered diff, RED owed for the new criteria)."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _go_red(d, "runA")
        args = dict(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a",
                    reason="the review turned up a trimming case")
        out = TS.spec_amend(**args)
        self.assertEqual(out["status"], "proposed")
        _human_approves(d, out["proposal_id"])
        out = TS.spec_amend(proposal_id=out["proposal_id"], **args)
        self.assertTrue(out["committed"])
        self.assertEqual(out["version"], 2)
        self.assertEqual(list(out["red_owed"]), ["test_trims"],
                         "the new criterion still OWES a failing test — the rung a re-emit skipped")
        store, run_id = _store(d)
        self.assertEqual([c["id"] for c in store.read(archive_key(run_id, 1))["criteria"]], ["AC1"])

    def test_the_verdict_is_the_one_seam_both_surfaces_read(self):
        """One decision function, so the CLI and the MCP tool cannot drift into different answers
        about the same run."""
        d = _repo("runA")
        store, run_id = _store(d)
        self.assertEqual(reemit_verdict(store, run_id).kind, "first")
        _emit(d, A_C, A_T)
        store, run_id = _store(d)
        v = reemit_verdict(store, run_id)
        self.assertEqual((v.kind, v.from_version, v.version), ("replace", 1, 2))
        self.assertEqual(v.archive, archive_key(run_id, 1))
        _go_red(d, "runA")
        self.assertTrue(reemit_verdict(*_store(d)).refused)


# ======================================================================================
# 4 — the gates that already fire on an emit still fire on a RE-emit
# ======================================================================================

class _Layer:
    """A stand-in graph pinned to the GREP FLOOR — `graph_degraded` is the query-level signal the
    GR.S3 refusal keys on (mirrors `test_state_scope._Layer`)."""

    uses_graph = False
    backend_name = "grep"

    def blast_radius(self, symbol, depth=2):
        return QueryResult("blast_radius", symbol,
                           references=[Reference("app/pay.py", 5, snippet="", symbol="charge")],
                           backend=self.backend_name, degraded=True)


def _degraded_brainstorm():
    s = BrainstormSession("how to slug")
    s.propose_approaches([Approach("a", "approach a", pros=["fast"], cons=["risky"],
                                   targets=["slug"]),
                          Approach("b", "approach b", pros=["safe"], cons=["slow"],
                                   targets=["slug"])])
    s.assess_impacts(layer=_Layer())
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    s.approve("jas", "a")
    return s


class TestTheEmitGatesStillFireOnAReEmit(_Base):
    """A second emit is not a WEAKER path than the first. Each gate is verified with a spec already
    on record — the state that used to walk straight past all of them into a clobber."""

    def test_completeness_still_refuses_an_unmapped_criterion(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=A_T, approach="a")
        self.assertEqual(out["gate"], "completeness")
        self.assertEqual(list(out["unmapped"]), ["AC2"])
        self.assertEqual(spec_version(_store(d)[0]), 1, "a refused re-emit changes nothing")

    def test_prior_art_still_refuses(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        store, _rid = _store(d)
        store.write("approved_approach", _handoff(prior_art_ran=False))
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        self.assertEqual(out["gate"], "prior-art")
        self.assertFalse(out["committed"])

    def test_graph_required_still_refuses(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        store, _rid = _store(d)
        save_brainstorm_progress(_degraded_brainstorm(), store)
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        self.assertEqual(out["gate"], "graph-required")
        self.assertFalse(out["committed"])

    def test_the_human_gate_is_still_the_only_way_in(self):
        """`approve=true` is a parameter a MODEL types. It committed nothing before this stage and
        it commits nothing now — a re-emit is not a softer consent boundary."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a",
                           approve=True, confirm=True)
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])
        self.assertEqual(spec_version(_store(d)[0]), 1)


# ======================================================================================
# 5 — cross-session: the archive keys to the PIPELINE's run, not a phantom
# ======================================================================================

class TestTheReEmitIsRunScoped(_Base):

    def test_a_re_emit_from_a_second_session_archives_the_pipelines_prior_spec(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _re_enter(d, "runB")
        out = _emit(d, B_C, B_T)
        self.assertTrue(out["committed"])
        self.assertEqual(out.get("superseded"), archive_key("runA", 1),
                         "the archived spec is the PIPELINE's v1 — an archive keyed to the writing "
                         "session would name a run that never had a spec")

        store, run_id = _store(d)
        self.assertEqual(run_id, "runA")
        self.assertEqual([c["id"] for c in store.read(archive_key("runA", 1))["criteria"]], ["AC1"])
        self.assertEqual(len(load_emitted_spec(store).criteria), 2)
        self.assertIsNone(store._base.read(archive_key("runB", 1)),
                          "no phantom archive under the writing session")

    def test_no_second_evidence_run_is_minted_by_the_re_emit(self):
        """STATE-SCOPE's single-shot lesson: a commit landing under the WRITING session's scope
        creates a second `emitted_spec__<run>`, after which run resolution degrades and the human is
        walked to a terminal on every call. A re-emit must not re-open that."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _re_enter(d, "runB")
        _emit(d, B_C, B_T)
        specs = [f for f in os.listdir(os.path.join(d, ".mokata", "temp_local", "state"))
                 if f.startswith("emitted_spec__")]
        self.assertEqual(specs, ["emitted_spec__runA.json"],
                         f"exactly one evidence run must carry a spec, found {specs}")

    def test_the_red_that_refuses_is_the_pipelines_red_not_the_processs(self):
        """The (b) branch reads run-scoped evidence too: a RED recorded on the pipeline still
        refuses a re-emit raised from a different session."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _go_red(d, "runA")
        _re_enter(d, "runB")
        out = TS.spec_emit(path=d, title="slugify", criteria=B_C, tests=B_T, approach="a")
        self.assertEqual(out["gate"], REEMIT_GATE,
                         "a gate that quietly is not there is worse than one that blocks — the run "
                         "has work in flight, whichever process is asking")


# ======================================================================================
# 6 — read-back: every reader sees the LATEST, and nothing is ever lost
# ======================================================================================

class TestReadBack(_Base):

    def test_every_reader_sees_the_latest_version(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _emit(d, B_C, B_T)
        store, _rid = _store(d)
        self.assertEqual([c.id for c in load_emitted_spec(store).criteria], ["AC1", "AC2"],
                         "the spec-persisted gate reads exactly this key")
        out = TR.decompose(path=d)
        self.assertTrue(out.get("available"))
        self.assertEqual(len(out["subtasks"]), 2,
                         "decompose derives its split from the LIVE acceptance criteria")
        show = TR.spec_show(path=d)
        self.assertEqual([c["id"] for c in show["criteria"]], ["AC1", "AC2"],
                         "spec_show reports the version in force, not the superseded one")

    def test_the_corpus_still_supersedes_by_title_rather_than_duplicating(self):
        """The corpus's own supersede-by-title (SI-DEV.0) is unchanged — the archive is a SECOND,
        per-run history, not a replacement for it."""
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _emit(d, B_C, B_T)
        raw = Surface.load(d).state.read("spec_corpus")
        self.assertEqual(len(raw), 1)
        self.assertIn("trims", json.dumps(raw))

    def test_a_chain_of_re_emits_keeps_every_version(self):
        d = _repo("runA")
        _emit(d, A_C, A_T)
        _emit(d, B_C, B_T)
        _emit(d, C_C, C_T)
        store, run_id = _store(d)
        self.assertEqual(spec_version(store), 3)
        self.assertEqual([c["id"] for c in store.read(archive_key(run_id, 1))["criteria"]],
                         ["AC1"])
        self.assertEqual([c["id"] for c in store.read(archive_key(run_id, 2))["criteria"]],
                         ["AC1", "AC2"])
        self.assertEqual(len(load_emitted_spec(store).criteria), 3)


if __name__ == "__main__":
    unittest.main()
