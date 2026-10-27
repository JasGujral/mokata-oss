"""INT-2a (0.0.26) — B-F4: a session is gated only by the run it is bound to.

Audit B (`sim.log`): with S02's brainstorm run open, the unbound sessions S03/S04/S06 were BLOCKED
`[approach-approval]` on S02's run — `check_write` fell through to the resolver, whose SINGLE rung
handed every window the one run with state. This pins the fix and its narrow scope:

  * an unbound (claim NONE) session is NOT policed by a run another session's registration claims,
    and the allow is disclosed (a notice naming the other run);
  * the bound session is still policed by its own run;
  * a run NO session claims (CLI-registered) still polices as before;
  * a MOKATA_SESSION_ID pin is honoured (a human binding).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import gate_hook as G
from mokata import run_intent as RI
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo

IMPL = "src/app.py"


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()


class BF4UnboundSessionIsNotPolicedByAnotherSessionsRun(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_BF4_parallel_sessions_only_the_bound_one_is_gated(self):
        """S02 runs brainstorm (bound to rA, no approach yet). S03/S04/S06 never asked. Only S02 is
        blocked. (Firing control: delete the INT-2a block in `check_write` and S03 is BLOCKED
        `[approach-approval]` by rA — the Audit B symptom.)"""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.record_intent(d, "S02", "prompt:brainstorm")
            self.assertTrue(RI.bind_registered(d, "S02", "rA"))

            mine = G.check_write(d, IMPL, session_id="S02")
            self.assertFalse(mine.allowed, "the bound session must still be gated by its run")

            for sid in ("S03", "S04", "S06"):
                out = G.check_write(d, IMPL, session_id=sid)
                self.assertTrue(out.allowed, f"{sid} was policed by another session's run: "
                                             f"{out.gate} {out.reason}")
                self.assertIn("rA", out.notice)
                self.assertIn("another session", out.notice)

    def test_BF4_unclaimed_run_still_polices(self):
        """A run no session's registration claims (CLI / terminal flow) keeps today's policing."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rCLI")
            out = G.check_write(d, IMPL, session_id="S09")
            self.assertFalse(out.allowed)

    def test_BF4_pinned_run_is_honoured(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.record_intent(d, "S02", "prompt:brainstorm")
            RI.bind_registered(d, "S02", "rA")
            with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "rA"}):
                out = G.check_write(d, IMPL, session_id="S03")
            self.assertFalse(out.allowed, "a MOKATA_SESSION_ID pin is a human binding")

    def test_BF4_no_session_id_is_todays_gate(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.record_intent(d, "S02", "prompt:brainstorm")
            RI.bind_registered(d, "S02", "rA")
            self.assertFalse(G.check_write(d, IMPL).allowed)

    def test_BF4_run_claimants_reads_only_claims(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            RI.record_intent(d, "S1", "prompt:brainstorm")
            RI.bind_registered(d, "S1", "rA")
            RI.record_intent(d, "S2", "prompt:brainstorm")          # open, not bound
            self.assertEqual(frozenset({"S1"}), RI.run_claimants(d, "rA"))
            self.assertEqual(frozenset(), RI.run_claimants(d, "rB"))
            self.assertEqual(frozenset(), RI.run_claimants(d, None))


if __name__ == "__main__":
    unittest.main()
