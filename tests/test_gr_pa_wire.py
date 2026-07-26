"""GR-PA-WIRE — the prior-art step-ran gate, WIRED into the production spec-emit surfaces.

GR-PA (0.0.14) shipped the pure step (`prior_art.py`), the verdict
(`govern/prior_art_gate.check_prior_art_ran`), session recording, and bound skill prose — but with
ZERO production callers: `approve(prior_art_gate=)` was passed only in `tests/test_gr_pa.py`, so
enforcement was protocol prose + test pins (a RUN-REG-class gap: prose already failed to hold once).
This stage gives the gate real teeth at the ONE seam where an approved approach is CONSUMED into a
spec — `spec_emit` (MCP) and `mokata spec emit` (CLI) — reading the chosen approach's step-ran
evidence from the DURABLE `approved_approach` Handoff (deliberately NOT `brainstorm_progress`; the
Handoff is guaranteed present exactly when there is an approved approach to gate — see
`govern/prior_art_gate.handoff_prior_art_gate` and the call-site notes).

Business-level (what the approving human observes): emitting a spec for an approach whose prior-art
step never ran is REFUSED with a rendered verdict that names the road back; an approach that DID run
prior-art (any tier, including the degraded 'absent' floor) emits byte-identically to before; and a
standalone spec (no brainstorm) is never touched. The two surfaces compute the SAME shared verdict
from the SAME persisted key, so the refusal is identical across MCP and CLI.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit

from mokata import session                                          # noqa: E402
from mokata.brainstorm import (Approach, BrainstormSession,         # noqa: E402
                               load_approved_approach)
from mokata.brainstorm_impact import FITS, DesignFitVerdict         # noqa: E402
from mokata.config import Surface                                   # noqa: E402
from mokata.engine.spec_gate import check_spec_persisted            # noqa: E402
from mokata.govern.prior_art_gate import handoff_prior_art_gate     # noqa: E402
from mokata.init import init_repo                                   # noqa: E402
from mokata.mcp import tools_write as TW                            # noqa: E402
from mokata.session_save import save_session                       # noqa: E402

RUN = "run-grpawire"
TITLE = "slugify keeps unicode intact"
ACS = [("AC1", "slugify of a unicode string returns a url-safe slug"),
       ("AC2", "slugify of an empty string raises ValueError")]
TESTS = [("test_slugify_unicode", ["AC1"]), ("test_slugify_empty", ["AC2"])]


class _Repo:
    """An initialized mokata repo pinned to one run id (== session id: run_id == session_id)."""

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


def _approved_session(*, prior_art_ran):
    """An approved brainstorm built the way `playbook.py` builds it — both P21 lenses on the table,
    then an explicit approval. `prior_art_ran` toggles ONLY whether the bound prior-art step ran, so
    the two fixtures differ in exactly the thing this gate reads. Approaches carry NO targets, so the
    Lens-1 radius is not graph-degraded and the GR.S3 graph gate never fires — this isolates the
    prior-art verdict as the sole variable."""
    s = BrainstormSession("slugify")
    s.propose_approaches([
        Approach(name="normalize-then-slug", summary="NFKD then strip",
                 pros=["keeps unicode intent"], cons=["slower on long input"]),
        Approach(name="transliterate", summary="map to ascii",
                 pros=["ascii-only output"], cons=["lossy for CJK"]),
    ])
    s.assess_impacts(layer=None, memory_items=[])
    for a in s.approaches:
        s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
    if prior_art_ran:
        s.assess_prior_art(layer=None)          # the BOUND STEP runs (tier 'absent', ran=True)
    s.approve("jas", "normalize-then-slug")
    return s


def _persist(repo, *, prior_art_ran):
    """Persist the approved approach through the PRODUCTION seam (`save_session`, what the MCP
    `session_save` tool routes through), so what lands on disk is what a real session leaves."""
    return save_session(repo.surface, brainstorm=_approved_session(prior_art_ran=prior_art_ran).to_dict())


def _args(path):
    return dict(path=path, title=TITLE,
                criteria=[{"id": i, "text": t} for i, t in ACS],
                tests=[{"name": n, "ac_ids": list(a)} for n, a in TESTS],
                approach="normalize-then-slug")


def _cli_emit(repo, *, yes=True):
    """Drive the CLI `mokata spec emit` in-process; return `(returncode, stdout)`."""
    from mokata.cli_commands.spec import cmd_spec_emit
    payload = {"title": TITLE, "approach": "normalize-then-slug",
               "criteria": [{"id": i, "text": t} for i, t in ACS],
               "tests": [{"name": n, "ac_ids": list(a)} for n, a in TESTS]}
    fp = os.path.join(repo.path, "spec.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    args = types.SimpleNamespace(path=repo.path, file=fp, yes=yes)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_spec_emit(args)
    return rc, buf.getvalue()


# ======================================================================================
# Deliverable 1 — the production MCP approve path REFUSES when the step didn't run
# ======================================================================================

class TestMcpRefusesUnran(unittest.TestCase):

    def test_gr_pa_wire_mcp_refuses_unran(self):
        """A spec emitted for an approved approach whose prior-art step never ran is REFUSED at the
        MCP `spec_emit` surface, with the rendered verdict surfaced — and NOTHING is written."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)
            res = mcp_commit(TW.spec_emit, **_args(repo.path))
            self.assertFalse(res["committed"], f"emit must be refused, got: {res}")
            self.assertEqual(res.get("gate"), "prior-art")
            self.assertIn("REFUSED", res["reason"])
            self.assertIn("prior-art", res["reason"].lower())
            self.assertNotIn("proposal_id", res, "a refusal must not stage a proposal")
            self.assertFalse(check_spec_persisted(repo.surface.state).passed,
                             "a refused emit must persist no spec")


# ======================================================================================
# Deliverable 2 — the production CLI approve path REFUSES when the step didn't run
# ======================================================================================

class TestCliRefusesUnran(unittest.TestCase):

    def test_gr_pa_wire_cli_refuses_unran(self):
        """`mokata spec emit` refuses the same emit at the CLI surface (exit 1, verdict printed),
        reading the same durable Handoff via the run-scoped store (run_id == session_id)."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)
            rc, out = _cli_emit(repo)
            self.assertEqual(rc, 1, f"CLI emit must refuse (exit 1); stdout was:\n{out}")
            self.assertIn("prior-art", out.lower())
            self.assertIn("REFUSED", out)
            self.assertFalse(check_spec_persisted(repo.surface.state).passed)


# ======================================================================================
# The RELEASE REGRESSION — fails on the pre-wire code (emit succeeded with no prior-art)
# ======================================================================================

class TestRegression(unittest.TestCase):

    def test_gr_pa_wire_regression(self):
        """On the pre-GR-PA-WIRE code, emitting a spec for an un-researched approved approach
        COMMITTED happily (the gate had zero production callers). Now BOTH production surfaces refuse
        and no spec lands. This assertion is what fails on the old code and passes now."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)
            mcp = mcp_commit(TW.spec_emit, **_args(repo.path))
            self.assertFalse(mcp["committed"])
            self.assertFalse(check_spec_persisted(repo.surface.state).passed,
                             "the un-researched approach must NOT reach a persisted spec (the bug)")
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)
            rc, _out = _cli_emit(repo)
            self.assertEqual(rc, 1)
            self.assertFalse(check_spec_persisted(repo.surface.state).passed)


# ======================================================================================
# Cross-surface parity (Jas condition #1) — same persisted state ⇒ same shared verdict
# ======================================================================================

class TestSurfacesAgree(unittest.TestCase):

    def test_gr_pa_wire_mcp_and_cli_agree(self):
        """MCP and CLI compute the IDENTICAL verdict from the SAME persisted Handoff: the exact
        rendered refusal both surface is the one shared `check_prior_art_ran` verdict."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)
            expected = handoff_prior_art_gate(load_approved_approach(repo.surface.state)).render()
            mcp = mcp_commit(TW.spec_emit, **_args(repo.path))
            rc, cli_out = _cli_emit(repo)
        self.assertEqual(mcp["reason"], expected)      # MCP surfaces the shared verdict verbatim
        self.assertEqual(rc, 1)
        self.assertIn(expected, cli_out)               # CLI surfaces the SAME verdict


# ======================================================================================
# Fail-CLOSED on a legacy / missing Handoff prior-art (Jas condition #2)
# ======================================================================================

class TestLegacyFailsClosed(unittest.TestCase):

    def test_gr_pa_wire_legacy_missing_prior_art_fails_closed(self):
        """A legacy Handoff with no `prior_art` at all reads as not-run and REFUSES — never a silent
        pass — and the verdict names the road back (re-run the pass, then approve), never a dead-end."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)
            handoff = load_approved_approach(repo.surface.state)
            self.assertIsNone(handoff.prior_art, "fixture precondition: no prior-art recorded")
            gate = handoff_prior_art_gate(handoff)
            self.assertTrue(gate.refused)
            road = gate.render().lower()
            self.assertIn("run the prior-art pass", road)
            self.assertIn("approve", road)


# ======================================================================================
# NEGATIVES — the step-RAN path and the standalone-spec path are byte-identical
# ======================================================================================

class TestStepRanIsByteIdentical(unittest.TestCase):

    def test_gr_pa_wire_step_ran_emit_is_unchanged(self):
        """When the prior-art step RAN (any tier), the emit proceeds exactly as before — it commits,
        with NO new gate, marker, or notice added to the output. Non-negotiable no-behavior-change."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=True)
            res = mcp_commit(TW.spec_emit, **_args(repo.path))
            self.assertTrue(res["committed"], f"a researched approach must emit: {res}")
            self.assertNotIn("prior-art", json.dumps(res),
                             "the passing gate must add NOTHING to the emit output (byte-identical)")
            self.assertTrue(check_spec_persisted(repo.surface.state).passed)

    def test_gr_pa_wire_step_ran_cli_emit_is_unchanged(self):
        with _Repo() as repo:
            _persist(repo, prior_art_ran=True)
            rc, out = _cli_emit(repo)
            self.assertEqual(rc, 0, f"a researched approach must emit at the CLI; stdout:\n{out}")
            self.assertNotIn("prior-art", out.lower(),
                             "the passing gate must print no prior-art marker")


class TestStandaloneSpecUntouched(unittest.TestCase):

    def test_gr_pa_wire_standalone_spec_no_refusal(self):
        """No brainstorm persisted (a standalone spec) ⇒ the gate degrades to a no-op: the emit is
        byte-identical, exactly as before this stage. Nothing to gate, nothing refused."""
        with _Repo() as repo:
            self.assertIsNone(load_approved_approach(repo.surface.state))   # no approach at all
            res = mcp_commit(TW.spec_emit, **_args(repo.path))
            self.assertTrue(res["committed"], f"a standalone spec must emit: {res}")
            self.assertNotIn("prior-art", json.dumps(res))
            self.assertTrue(check_spec_persisted(repo.surface.state).passed)


# ======================================================================================
# Scope guard (deliverable 4) — the proposal/memory approve surface is UNTOUCHED
# ======================================================================================

class TestProposalApproveUnchanged(unittest.TestCase):

    def test_gr_pa_wire_proposal_approve_surface_unaffected(self):
        """The gate binds APPROACH approval (spec-emit) only. A memory write proposal (`remember`)
        still commits through its own human gate even while an un-researched approved approach is
        persisted — the prior-art gate does not leak onto the proposal-approve surfaces."""
        with _Repo() as repo:
            _persist(repo, prior_art_ran=False)          # an un-researched approach is on disk
            res = mcp_commit(TW.remember, path=repo.path,
                             subject="retry", value="retry lives in utils.retry")
            self.assertTrue(res.get("committed"),
                            f"a memory write must be unaffected by the prior-art gate: {res}")


if __name__ == "__main__":
    unittest.main()
