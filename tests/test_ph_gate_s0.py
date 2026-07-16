"""PH-GATE.S0 — the PHASE-write gate + RUN-REG protocol-start (0.0.14 Phase 0).

Two bound-together holes, closed here:

  (1) FU-1 — the SI.1 hook enforced the TDD/spec/scope run-state gates but NOT the PHASE
      boundary: with a run REGISTERED and its persisted phase still `brainstorm` (no approach
      approved, no spec emitted), a native Write/Edit to an implementation file sailed through.
      A jump straight from idea to code was a physical exit-0. It must now exit 2 —
      "brainstorm in progress — approve an approach first" — with the P14 override as the only
      escape. This is what makes doc-76 FU-1's `approach-approval` boundary a BACKED gate.

  (2) RUN-REG (live repro 2026-07-15) — a brainstorm run through the skill's prose in chat never
      registered a run, so `progress` reported no run, `spec` had nothing to attach to, and the
      phase gate above had no state to bind on (structurally fail-open on exactly that path).
      Protocol-start is now a state write: the first step registers the run; `spec`/`progress`
      with no tracked run answer with LEGIBLE recovery guidance, never a bare "no run".

The fail-open floor is untouched: no `.mokata`, no registered run, or an ambiguous multi-run
repo ⇒ byte-identical to before. The approved-approach and post-approval paths are unchanged.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import gate_hook as G                                  # noqa: E402
from mokata import progress as P                                   # noqa: E402
from mokata import tdd_state as T                                  # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.govern.resume import CHECKPOINT_PREFIX                 # noqa: E402

RUN = "run0123456789abcdef"


# --------------------------------------------------------------------------- helpers
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _raw_store(root):
    from mokata.state import StateStore
    return StateStore(T.state_dir(root))


def _register(root, run=RUN, passed=None):
    """Simulate a REGISTERED run — the `pipeline_run__<run>` checkpoint the hook now binds on."""
    _raw_store(root).write(CHECKPOINT_PREFIX + run, {"run_id": run, "passed": passed or []})


def _approve(root, run=RUN):
    _raw_store(root).write(G.APPROACH_PREFIX + run, {"approach": "the chosen one"})


def _emit_spec(root, run=RUN):
    _raw_store(root).write(G.SPEC_PREFIX + run, {"criteria": [{"id": "AC1", "text": "it works"}]})


def _record_red(root, run=RUN, test_id="test_login"):
    _raw_store(root).write(T.state_key(run), T.to_state(run, red=[test_id], green=[]))


def _impl(d):
    return os.path.join(d, "src", "auth.py")


# ======================================================================================
# (1) THE regression — the phase-write gate now blocks the idea→code jump
# ======================================================================================
class TestPhaseWriteGate(unittest.TestCase):

    def test_ph_gate_s0_regression(self):
        """A REGISTERED brainstorm run (checkpoint on disk), no approved approach, no spec — a
        native Write to an implementation file must now be BLOCKED. This is the 0.0.13 hole."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)                                   # brainstorm run is registered
            out = G.check_write(d, _impl(d), RUN)
            self.assertFalse(out.allowed, "the brainstorm-phase impl write was NOT blocked")
            self.assertEqual(out.exit_code, G.BLOCK_EXIT)
            self.assertEqual(out.gate, G.GATE_PHASE)
            self.assertIn("approve an approach first", out.reason)   # WHAT is owed
            self.assertIn("/mokata:brainstorm", out.reason)          # the fix
            self.assertIn("mokata gate override", out.reason)        # the P14 escape

    def test_the_gate_id_is_the_backed_approach_boundary(self):
        """The hook's phase-gate id IS the pipeline's brainstorm boundary — a net under it, not a
        second opinion (the same discipline as GATE_TDD/GATE_SPEC)."""
        from mokata.pipeline import PHASE_GATES
        self.assertEqual(G.GATE_PHASE, PHASE_GATES["brainstorm"].id)
        self.assertIn(G.GATE_PHASE, G.GATES)

    def test_test_and_plan_and_doc_writes_pass_during_brainstorm(self):
        """The gate must never block the writes brainstorm legitimately makes: the failing test, a
        plan/spec/memory file, or any non-source doc. Only implementation is premature here."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)
            for ok in ("tests/test_auth.py", "docs/design.md", "plans/x.md",
                       "spec.json", "notes.txt", "config.yaml"):
                out = G.check_write(d, os.path.join(d, ok), RUN)
                self.assertTrue(out.allowed, f"{ok} was blocked during brainstorm: {out.reason!r}")


# ======================================================================================
# (1b) negatives — every other path is byte-identical to SI.1
# ======================================================================================
class TestPhaseGateNegatives(unittest.TestCase):

    def test_no_registered_run_still_fails_open(self):
        """The floor SI.1 guaranteed: a mokata repo with NO registered run (and no approach/spec)
        is ordinary hand-editing — never policed. This is the line the phase gate must not cross."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                                        # initialized, but no run registered
            out = G.check_write(d, _impl(d), RUN)
            self.assertTrue(out.allowed, "hand-editing outside a run was blocked")

    def test_approved_approach_path_is_unchanged(self):
        """With an approach approved (but no spec), the EXISTING spec-persisted gate fires — NOT
        the new phase gate. The approved-approach path is byte-identical to before."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d), _approve(d)
            out = G.check_write(d, _impl(d), RUN)
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_SPEC)         # spec-persisted, not the phase gate
            self.assertNotEqual(out.gate, G.GATE_PHASE)

    def test_post_approval_full_pipeline_unchanged(self):
        """Approach + spec + a failing test on record — implementation is licensed, exactly as
        SI.1 already allowed. The phase gate adds nothing to a run past the spec."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d, passed=["brainstorm"]), _approve(d), _emit_spec(d), _record_red(d)
            out = G.check_write(d, _impl(d), RUN)
            self.assertTrue(out.allowed, f"post-approval impl write was blocked: {out.reason!r}")

    def test_registered_run_is_now_resolvable(self):
        """The gate can only bind if a checkpoint-only run RESOLVES. A run with only a checkpoint
        (no approach/spec/tdd yet) must resolve to that run, not fall to 'no run'."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)
            self.assertEqual(G.resolve_run(d).run_id, RUN)


# ======================================================================================
# (2) P14 override — the ledgered escape covers the new gate
# ======================================================================================
class TestPhaseGateOverride(unittest.TestCase):

    def _override(self, d, gate, reason="explicitly starting from code"):
        from mokata.cli_commands.gate import cmd_gate_override
        args = types.SimpleNamespace(path=d, gate=gate, reason=reason, run=RUN,
                                     actor="human", yes=True)
        return cmd_gate_override(args)

    def test_ledgered_override_unblocks_exactly_this_session(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d)
            self.assertFalse(G.check_write(d, _impl(d), RUN).allowed)   # blocked first
            self.assertEqual(self._override(d, G.GATE_PHASE), 0)        # ledgered override
            out = G.check_write(d, _impl(d), RUN)
            self.assertTrue(out.allowed, "the override did not unblock the phase gate")
            self.assertTrue(out.overridden)
            # the decision is in the audit ledger
            surface = Surface.load(d)
            recs = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
            self.assertTrue(any(r.get("gate") == G.GATE_PHASE and r.get("kind") == "gate_override"
                                for r in recs), "the override was not ledgered")

    def test_override_is_scoped_to_the_phase_gate_only(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register(d), _approve(d)                        # now the SPEC gate would fire
            self.assertEqual(self._override(d, G.GATE_PHASE), 0)
            out = G.check_write(d, _impl(d), RUN)
            self.assertFalse(out.allowed, "overriding the phase gate wrongly cleared spec-persisted")
            self.assertEqual(out.gate, G.GATE_SPEC)


# ======================================================================================
# (3)/(4) RUN-REG — protocol-start registers the run; no-run reads are legible
# ======================================================================================
class TestRunRegistration(unittest.TestCase):

    def test_run_reg_regression(self):
        """The conversational-brainstorm repro: before any registration, `progress` finds no run
        and answers with legible guidance; after the protocol-start registration step, run state
        exists (progress goes active) and a spec can attach to the run."""
        from mokata.session_save import register_run
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)

            # BEFORE — no tracked run: progress is inactive and its message names the recovery
            before = P.build_progress(surface.state)
            self.assertFalse(before.active)
            self.assertIn("no run in progress", before.message)
            self.assertIn("/mokata:brainstorm", before.message)      # what to run to start one
            # spec has nothing to attach to (the resolver reports no run)
            self.assertIsNone(G.resolve_run(d).run_id)

            # PROTOCOL-START — the run-registering step
            rid = register_run(surface, run_id=RUN)
            self.assertEqual(rid, RUN)

            # AFTER — the run is tracked: progress is active and spec attaches to it
            after = P.build_progress(surface.state)
            self.assertTrue(after.active, "the run did not become tracked after registration")
            self.assertEqual(after.run_id, RUN)
            self.assertEqual(G.resolve_run(d).run_id, RUN)           # spec now has a run to attach

    def test_register_run_is_idempotent_and_never_clobbers_progress(self):
        from mokata.govern.resume import PipelineCheckpoint
        from mokata.session_save import register_run
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _register(d, passed=["brainstorm"])              # already progressed one gate
            register_run(surface, run_id=RUN)                # re-registering must not reset it
            self.assertEqual(PipelineCheckpoint(surface.state, RUN).passed, ["brainstorm"])

    def test_progress_no_run_message_is_legible(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            msg = P.build_progress(surface.state).message
            # names the two entry points AND how to resume — not a bare "no run in progress"
            self.assertIn("/mokata:brainstorm", msg)
            self.assertIn("/mokata:resume", msg)

    def test_spec_show_with_no_tracked_run_gives_named_guidance(self):
        from mokata.cli_commands.spec import cmd_spec_show
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                                         # no run registered
            args = types.SimpleNamespace(path=d)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cmd_spec_show(args)
            out = buf.getvalue().lower()
            self.assertEqual(rc, 0)
            self.assertIn("no tracked run", out)             # names the real condition
            self.assertIn("/mokata:brainstorm", out)         # how to start/attach a tracked run

    def test_session_save_tool_can_register_the_run(self):
        """The tool the prose routes through — `session_save` with register=True registers the run
        (rides the same write path; existing calls with register unset are unchanged)."""
        from mokata.mcp.tools_read import session_save
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            res = session_save(path=d, register=True, run_id=RUN)
            self.assertEqual(res.get("registered"), RUN)
            self.assertEqual(P.build_progress(surface.state).run_id, RUN)


# ======================================================================================
# (FU-1) the brainstorm boundary is now BACKED, and the prose carries the protocol hints
# ======================================================================================
class TestBackedBoundaryAndProse(unittest.TestCase):

    def test_approach_approval_is_a_backed_gate(self):
        from mokata import skill_contracts as sc
        ref = sc.GATES["approach-approval"]
        self.assertTrue(ref.backed, "the brainstorm boundary is still advisory")
        self.assertTrue(os.path.exists(ref.enforcement_point),
                        "the backed gate's enforcement point does not exist on disk")

    def test_no_contract_cites_an_unbacked_gate(self):
        from mokata import skill_contracts as sc
        self.assertEqual(sc.unbacked_citations(), [])
        self.assertIn("approach-approval", sc.cited_gate_ids())

    def _brainstorm_template(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "src", "mokata", "templates", "commands", "brainstorm.md")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_prose_registers_the_run_as_a_first_step(self):
        text = self._brainstorm_template().lower()
        self.assertIn("register", text)
        self.assertIn("session_save", text)

    def test_prose_has_the_declined_permission_hint(self):
        text = self._brainstorm_template().lower()
        self.assertIn("mokata mcp status", text)             # where to check the grant
        self.assertIn("declined", text)                     # the silent "stuck" it explains


if __name__ == "__main__":
    unittest.main()
