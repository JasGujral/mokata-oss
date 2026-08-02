"""SPEC-STANDALONE-SILENT — a spec with no upstream brainstorm must not read as a silent failure.

Jas, live, 2026-07-27: "what happens when spec is called without brainstorm attached... system
should not silently fail or get stuck — it should specifically ASK the user."

WHAT IS ACTUALLY TRUE (re-derived live, and NOT what the filing assumed)
-----------------------------------------------------------------------
Standalone spec is an INTENTIONAL, supported path (`skills/spec/SKILL.md`: "Standalone: this
command runs on its own — no upstream pipeline phase is required"), and `run_completeness_gate`
degrades clean for it: `approach_present=False`, `refinements_present=False`, and the gate still
judges on AC→test mapping alone and PASSES. Nothing blocks and nothing hangs. That half of the
filing holds.

The filing then called this a CLI/MCP PARITY hole — "the CLI's `GateResult.render()` prints the
informational line, the MCP surface does not". Measured, that is FALSE, and the correction is the
reason this stage exists at all: `GateResult.render()` has **no production caller on either emit
path**. `cli_commands/spec.py:cmd_spec_emit` goes through `engine/emit.py:emit_spec`, which builds
the `GateResult` and DISCARDS it on the PASS path — `EmitOutcome` carried no approach fields at
all — and `mcp/tools_spec.py:spec_emit` kept `gr` but threaded only `gr.reason`. So BOTH surfaces
were silent, and `render()`'s standalone line was reachable only from tests.

    CLI:  "spec emitted: 'slugify' — 1 acceptance criteria, all mapped to tests." (and nothing else)
    MCP:  the standalone proposal + committed dicts were BYTE-IDENTICAL to the brainstorm-attached
          ones, modulo the proposal_id.

So this is not "restore parity between a loud surface and a quiet one" — it is "both surfaces are
quiet, make them both say the same thing, once". `test_repro_*` below pins the measurement rather
than the assumption.

THE CONSENT SHAPE THIS FILE PINS (Jas's "it should ASK", decided 2026-07-27)
---------------------------------------------------------------------------
Not a second gate. `spec_emit` is ALREADY human-gated (SI.3): it returns a proposal and writes
NOTHING until a human runs `mokata approve <id>` at their own terminal. Adding a standalone
CONFIRMATION on top would be a second trip to the same human for the same write — consent theatre,
and exactly the P16 fatigue `awaiting.py` exists to prevent. So the ask is satisfied through the
gate that is already there: the standalone fact LEADS the proposal preview, which is what
`mokata approve <id>` prints, so the human approving the emit sees it before they say yes. An
agent therefore cannot drift into standalone mode without the human reading it — and the emit
still SUCCEEDS, because standalone is supported, not wrong.

ONE WORDING, ONE SOURCE. The sentence lives at `engine.completeness.STANDALONE_NOTE` and both
surfaces return it VERBATIM (`test_same_sentence_*`). That is the R3/R4 discipline `awaiting.py`
established for the AWAITING head: no tool writes its own copy, so no surface can drift from
another. `GateResult.render()` reads the same constant rather than keeping the second copy it had.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit

from mokata import approval as A                                    # noqa: E402
from mokata import session as S                                     # noqa: E402
from mokata.config import Surface                                   # noqa: E402
from mokata.engine.completeness import run_completeness_gate        # noqa: E402
from mokata.engine.emit import spec_from_payload                    # noqa: E402
from mokata.engine.spec_gate import load_emitted_spec               # noqa: E402
from mokata.govern.ledger import AuditLedger                        # noqa: E402
from mokata.govern.resume import PipelineCheckpoint                 # noqa: E402
from mokata.init import init_repo                                   # noqa: E402
from mokata.mcp import tools_spec as TS                             # noqa: E402
from mokata.mcp.consent import _evidence_store                      # noqa: E402
from mokata.refine import Refinement, RefineSession, persist_refinements   # noqa: E402

TITLE = "slugify"
CRITERIA = [{"id": "AC1", "text": "slugify keeps unicode"}]
TESTS = [{"name": "test_unicode", "ac_ids": ["AC1"]}]


# --------------------------------------------------------------------------- fixtures
def _enter_session(session_id):
    """Become a NEW MCP process (mirrors `test_spec_reemit._enter_session`)."""
    S.set_for_test(S.Session(session_id=session_id, started_at="2026-07-27T00:00:00Z",
                             started_monotonic=0.0, pid=os.getpid()))


def _handoff():
    return {"schema_version": 1, "phase": "brainstorm", "topic": "standalone",
            "approach": {"name": "a", "summary": "do it", "tradeoffs": [], "decisions": []},
            "answered_questions": [], "grounding": {}, "approver": "jas",
            "approved_at": "2026-07-27T00:00:00Z",
            "prior_art": {"ran": True, "approach": "a", "findings": [], "verdict": "none"},
            "domains": []}


def _repo(run_id, *, brainstorm=False, refine=False):
    """A repo carrying a registered pipeline run, with or without an approved DIRECTION on record.

    `brainstorm=False, refine=False` IS the case under test: a spec called with no upstream phase.
    """
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    _enter_session(run_id)
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, run_id).ensure_registered()
    if brainstorm:
        surface.state.write("approved_approach", _handoff())
    if refine:
        session = RefineSession("src/slug.py")
        session.propose([Refinement(title="extract slugify", rationale="one boundary")])
        session.approve(["extract slugify"], approver="jas")
        persist_refinements(session, surface.state)
    return d


def _emit_args(root, **kw):
    args = dict(path=root, title=TITLE, criteria=CRITERIA, tests=TESTS)
    args.update(kw)
    return args


def _propose(root, **kw):
    """The FIRST call only — the proposal, before any human has approved anything."""
    return TS.spec_emit(**_emit_args(root, **kw))


def _pending_preview(root, proposal_id):
    """The preview text `mokata approve <id>` prints to the human at their own terminal."""
    for p in A.pending(root):
        if p.proposal_id == proposal_id:
            return p.preview
    raise AssertionError(f"no pending proposal {proposal_id}")


def _cli_emit(root, *, yes=True):
    """Drive the CLI `mokata spec emit` in-process; return `(returncode, stdout)`."""
    from mokata.cli_commands.spec import cmd_spec_emit
    payload = {"title": TITLE, "criteria": CRITERIA, "tests": TESTS}
    fp = os.path.join(root, "spec.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    args = types.SimpleNamespace(path=root, file=fp, yes=yes)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_spec_emit(args)
    return rc, buf.getvalue()


# ======================================================================================
# Deliverable 1 — THE REPRO. Measured, not assumed: both surfaces were silent.
# ======================================================================================

class TestReproBothSurfacesWereSilent(unittest.TestCase):
    """RED BEFORE THE FIX. Deliberately asserts on OBSERVABLE output only (no new symbol is
    imported here), so these two fail on BEHAVIOUR against the pre-stage tree rather than on an
    ImportError — a red that proves the defect, not a red that proves the file is new."""

    def test_repro_mcp_standalone_emit_is_distinguishable_from_an_attached_one(self):
        """A standalone emit must not be byte-identical to a brainstorm-attached one. Before this
        stage it was: same keys, same values, modulo the proposal_id — the harness had NOTHING to
        read, which is what made a supported path read as a silent failure."""
        alone = _propose(_repo("run-repro-alone"))
        attached = _propose(_repo("run-repro-attached", brainstorm=True), approach="a")

        def _shape(d):
            # Drop the two id-derived keys; everything else must differ somewhere if the standalone
            # condition reaches the caller at all.
            return {k: v for k, v in d.items()
                    if k not in ("proposal_id", "awaiting", "awaiting_proposal_id",
                                 "awaiting_approve_command", "hint", "expires_in")}

        self.assertNotEqual(
            _shape(alone), _shape(attached),
            "REPRO: the standalone spec_emit result is indistinguishable from the "
            "brainstorm-attached one — nothing tells the caller no approach was attached.")

    def test_repro_cli_standalone_emit_says_something_about_the_missing_direction(self):
        """The CLI must say it too. Before this stage `emit_spec` built the `GateResult` and threw
        it away on the PASS path, so `mokata spec emit` printed three lines that were identical
        whether or not a brainstorm had ever run."""
        rc, out = _cli_emit(_repo("run-repro-cli"))
        self.assertEqual(rc, 0, out)
        self.assertIn(
            "stands alone", out.lower(),
            "REPRO: the CLI standalone emit says nothing about the absent approved direction.")


# ======================================================================================
# Deliverable 2 + 4 — the SAME sentence, from ONE shared source, on BOTH surfaces
# ======================================================================================

class TestOneSentenceOneSource(unittest.TestCase):

    def test_same_sentence_mcp_and_cli(self):
        """The MCP result and the CLI stdout carry the SAME sentence, VERBATIM, and it is the
        shared constant — not two paraphrases that can drift apart."""
        from mokata.engine.completeness import STANDALONE_NOTE

        mcp = _propose(_repo("run-same-mcp"))
        self.assertEqual(mcp.get("standalone"), STANDALONE_NOTE)

        rc, cli = _cli_emit(_repo("run-same-cli"))
        self.assertEqual(rc, 0, cli)
        self.assertIn(STANDALONE_NOTE, cli)

    def test_the_note_is_derived_from_the_gate_not_written_by_a_tool(self):
        """`standalone_note` is a pure read of the gate's own verdict: the note appears exactly
        when the gate saw NEITHER an approved approach NOR an approved refinement set. No surface
        re-derives 'is this standalone' for itself."""
        from mokata.engine.completeness import STANDALONE_NOTE, standalone_note

        spec, tests = spec_from_payload({"title": TITLE, "criteria": CRITERIA, "tests": TESTS})

        alone = Surface.load(_repo("run-note-alone")).state
        self.assertEqual(standalone_note(run_completeness_gate(spec, tests, store=alone)),
                         STANDALONE_NOTE)

        attached = Surface.load(_repo("run-note-attached", brainstorm=True)).state
        self.assertEqual(standalone_note(run_completeness_gate(spec, tests, store=attached)), "")

    def test_a_refine_front_end_is_not_standalone(self):
        """An approved REFINEMENT SET is an approved direction too (Stage 26). A spec that follows
        `/mokata:refine` must NOT be told it stands alone — the note tracks
        `approach_present OR refinements_present`, exactly as `render()` always did."""
        from mokata.engine.completeness import standalone_note

        spec, tests = spec_from_payload({"title": TITLE, "criteria": CRITERIA, "tests": TESTS})
        store = Surface.load(_repo("run-note-refine", refine=True)).state
        result = run_completeness_gate(spec, tests, store=store)
        self.assertTrue(result.refinements_present)
        self.assertEqual(standalone_note(result), "")
        self.assertNotIn("standalone", _propose(_repo("run-mcp-refine", refine=True)))

    def test_render_reads_the_shared_constant(self):
        """`GateResult.render()` must not keep a SECOND copy of the wording. It kept one
        ("approved direction: none on record (brainstorm/refine not run)") that no production
        caller ever printed — the drift this stage removes rather than reproduces."""
        from mokata.engine.completeness import STANDALONE_NOTE

        spec, tests = spec_from_payload({"title": TITLE, "criteria": CRITERIA, "tests": TESTS})
        store = Surface.load(_repo("run-render")).state
        self.assertIn(STANDALONE_NOTE, run_completeness_gate(spec, tests, store=store).render())


# ======================================================================================
# Deliverable 3 — the CONSENT decision: the EXISTING human gate carries it, no second gate
# ======================================================================================

class TestTheHumanSeesItAtTheGateThatAlreadyExists(unittest.TestCase):

    def test_the_standalone_fact_leads_the_proposal_preview(self):
        """`mokata approve <id>` prints the proposal's preview (truncated to its first 20 lines,
        `approval.py`). The standalone note LEADS it, so the human approving a standalone spec
        reads that fact before they say yes — which is Jas's "ASK", answered by the gate that is
        already there rather than by a second one."""
        from mokata.engine.completeness import STANDALONE_NOTE

        d = _repo("run-preview")
        out = _propose(d)
        preview = _pending_preview(d, out["proposal_id"])
        self.assertTrue(preview.startswith(STANDALONE_NOTE),
                        f"the standalone note must LEAD the preview, got: {preview[:200]!r}")
        self.assertIn(STANDALONE_NOTE, "\n".join(preview.splitlines()[:20]),
                      "the note must survive `mokata approve`'s 20-line preview truncation")
        self.assertIn("spec: slugify", preview, "the spec itself is still previewed")

    def test_no_second_gate_one_approval_still_commits(self):
        """Standalone adds NO gate. One proposal, one human approval, one commit — the same single
        round-trip a brainstorm-attached emit takes. A standalone emit is not asked to be approved
        twice."""
        d = _repo("run-one-gate")
        first = _propose(d)
        self.assertEqual(first["status"], "proposed")
        self.assertFalse(first["committed"])

        A.approve(d, first["proposal_id"], actor="test-human",
                  ledger=AuditLedger.from_mokata_dir(os.path.join(d, ".mokata")))
        committed = TS.spec_emit(**_emit_args(d, proposal_id=first["proposal_id"]))
        self.assertEqual(committed["status"], "committed", committed)
        self.assertTrue(committed["committed"])

    def test_the_completeness_verdict_is_untouched(self):
        """Deliverable 5 — standalone changes NO gate verdict. The gate still passes on AC→test
        mapping alone, and it is still the ONLY thing the emit's `gate`/`completeness` keys report.
        """
        d = _repo("run-verdict")
        out = _propose(d)
        self.assertEqual(out["gate"], "completeness")
        self.assertEqual(out["completeness"],
                         "all 1 acceptance criteria map to tests (RED-before-GREEN traceability)")


# ======================================================================================
# Deliverable 2 — the emit still SUCCEEDS (this is a supported path, not a new refusal)
# ======================================================================================

class TestStandaloneEmitStillSucceeds(unittest.TestCase):

    def test_mcp_standalone_emit_commits_and_persists_the_spec(self):
        from mokata.engine.completeness import STANDALONE_NOTE

        d = _repo("run-succeeds")
        out = mcp_commit(TS.spec_emit, **_emit_args(d))
        self.assertEqual(out["status"], "committed", out)
        self.assertTrue(out["committed"])
        self.assertEqual(out["ac_count"], 1)
        # informational on the COMMITTED result too — the agent that just emitted standalone can
        # still read that it did, not only the agent that read the proposal.
        self.assertEqual(out.get("standalone"), STANDALONE_NOTE)

        store, _run = _evidence_store(Surface.load(d), d)
        spec = load_emitted_spec(store)
        self.assertIsNotNone(spec, "the standalone spec must actually be on record")
        self.assertEqual(spec.title, TITLE)

    def test_cli_standalone_emit_exits_zero_and_persists(self):
        d = _repo("run-succeeds-cli")
        rc, out = _cli_emit(d)
        self.assertEqual(rc, 0, out)
        self.assertIn("spec emitted", out)
        self.assertIsNotNone(load_emitted_spec(Surface.load(d).state))


# ======================================================================================
# Deliverable 4 — NO NEW NOISE on the normal path (brainstorm-attached is byte-identical)
# ======================================================================================

class TestTheAttachedPathIsUnchanged(unittest.TestCase):

    def test_mcp_attached_emit_carries_no_standalone_key(self):
        d = _repo("run-attached-mcp", brainstorm=True)
        proposed = _propose(d, approach="a")
        self.assertNotIn("standalone", proposed)
        committed = mcp_commit(TS.spec_emit, **_emit_args(d, approach="a"))
        self.assertEqual(committed["status"], "committed", committed)
        self.assertNotIn("standalone", committed)

    def test_mcp_attached_preview_is_the_bare_spec_preview(self):
        """The attached proposal's preview is EXACTLY `_spec_preview` — no head spliced in front,
        byte for byte as before this stage."""
        from mokata.mcp.tools_spec import _spec_preview

        d = _repo("run-attached-preview", brainstorm=True)
        out = _propose(d, approach="a")
        spec, tests = spec_from_payload({"title": TITLE, "criteria": CRITERIA, "tests": TESTS,
                                         "approach": "a"})
        self.assertEqual(_pending_preview(d, out["proposal_id"]), _spec_preview(spec, tests))

    def test_cli_attached_emit_output_is_byte_identical(self):
        """The CLI's brainstorm-attached emit prints EXACTLY the three lines it shipped at 0.0.15.
        The standalone line is the ONLY behaviour this stage adds to that surface."""
        d = _repo("run-attached-cli", brainstorm=True)
        rc, out = _cli_emit(d)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out, (
            "spec emitted: 'slugify' — 1 acceptance criteria, all mapped to tests.\n"
            f"  saved as this run's spec (run {'run-attached-cli'[:8]}), and recorded in the "
            "shared spec corpus (1 spec(s)).\n"
            "  implementation is unblocked once a failing test is on record (/mokata:test).\n"))


if __name__ == "__main__":
    unittest.main()
