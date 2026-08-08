"""APPROVED-STILL-READS-AS-AWAITING (doc 84) — a decision the human ALREADY made is not a wait.

`approval.pending` returns every LIVE proposal — unexpired and unredeemed — which correctly
includes one the human has already approved and the model has not yet redeemed. That set is right.
What was wrong is what two of its three consumers then SAID about it:

  * `awaiting.statusline_segment` rendered `⏳ awaiting approval <id>` for an approved proposal;
  * `awaiting.pending_lines` (what `doctor` prints) counted it into "N write(s) awaiting YOUR
    approval" and handed back `Fix: mokata approve <pid>` — telling the human to go and redo a
    decision they had already made, on the exact surface they run when they think they are stuck.

`cli_commands/approve.py::_list` was already right: it reads `p.approved` and prints "APPROVED
(redeemable once)" vs "awaiting your approval". So the state needed was never missing — two call
sites simply did not consult it. These tests pin all THREE surfaces against all THREE statuses
(proposed · approved · used), so the surface that was already correct cannot regress either.

This compounds with AMEND-STEP-2-IS-UNADVERTISED: approve, see nothing change, be told to approve
again. Together they are what "spec amend gets stuck" actually looked like.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import contextlib
import io
import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata import approval                                        # noqa: E402
from mokata import awaiting as A                                   # noqa: E402
from mokata.init import init_repo                                  # noqa: E402

RUN = "run-approved-vs-awaiting"

# The phrases that mean "a human still owes a decision". None of them may appear about a proposal
# whose human decision is already recorded.
WAITING_ON_YOU = ("awaiting YOUR approval", "awaiting your approval", "awaiting approval",
                  "waiting for your approval", "waiting on you")


class _Repo:
    """A real initialized repo — the approval store is on disk, exactly as the surfaces read it."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.dir.cleanup()

    def propose(self, tool="memory_add", args=None, target="memory:x"):
        return approval.propose(self.path, tool=tool, args=args or {"k": tool}, run_id=RUN,
                                target=target, summary="a write", preview="content")

    def approve(self, pid):
        return approval.approve(self.path, pid, actor="jas")


def _approve_list(root):
    """`mokata approve --list` — the third surface, driven exactly as the CLI drives it."""
    from mokata.cli_commands.approve import cmd_approve
    args = argparse.Namespace(path=root, list_pending=True, proposal_id="", actor="human",
                              yes=False, home=None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_approve(args)
    return buf.getvalue()


class TestApprovedIsNotAWait(unittest.TestCase):

    def test_pending_lines_do_not_ask_for_an_approval_already_given(self):
        """THE bug. An approved-unredeemed proposal must not be counted into "awaiting YOUR
        approval", and doctor must not hand back `mokata approve <pid>` as the fix — that is an
        instruction to redo a decision the human already made."""
        with _Repo() as repo:
            p = repo.propose()
            repo.approve(p.proposal_id)
            self.assertTrue(approval.load(repo.path, p.proposal_id).approved)

            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            for phrase in WAITING_ON_YOU:
                self.assertNotIn(phrase, text,
                                 f"an already-approved write is rendered as {phrase!r}")
            self.assertNotIn(A.APPROVE_CMD.format(proposal_id=p.proposal_id), text,
                             "doctor tells the human to redo a decision they already made")

    def test_pending_lines_render_the_approved_write_distinctly(self):
        """Not-a-wait must not mean invisible: the write is still unwritten, so it is still LIVE
        state. It renders — named, with its id — as waiting on the MODEL, not on the human."""
        with _Repo() as repo:
            p = repo.propose()
            repo.approve(p.proposal_id)
            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertIn(p.proposal_id, text, "the id must still be findable")
            self.assertIn("APPROVED", text.upper())
            self.assertNotIn("nothing is waiting on you", text,
                             "a live unredeemed write is not an all-clear")

    def test_statusline_does_not_call_an_approved_proposal_a_wait(self):
        """The statusline is mokata's OWN wait channel (UX-NOTIFY). It said `⏳ awaiting approval
        <id>` about a proposal the human had approved — a standing, false ask."""
        with _Repo() as repo:
            p = repo.propose()
            repo.approve(p.proposal_id)
            seg = A.statusline_segment(repo.path)
            self.assertNotIn("awaiting approval", seg)
            self.assertIn(p.proposal_id, seg, "the id stays visible — it is still unwritten")

    def test_a_mixed_set_counts_only_the_undecided_as_awaiting(self):
        """Two proposed + one approved is TWO things waiting on the human, not three. The count is
        the number a user checks against `mokata approve --list`, so an inflated one is its own
        small lie."""
        with _Repo() as repo:
            a = repo.propose(args={"k": "a"})
            b = repo.propose(args={"k": "b"})
            c = repo.propose(args={"k": "c"})
            repo.approve(c.proposal_id)
            self.assertEqual(len(approval.pending(repo.path)), 3, "all three are still LIVE")

            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertIn("2 write(s) awaiting YOUR approval", text)
            self.assertNotIn("3 write(s) awaiting YOUR approval", text)
            for p in (a, b):
                self.assertIn(A.APPROVE_CMD.format(proposal_id=p.proposal_id), text)
            self.assertNotIn(A.APPROVE_CMD.format(proposal_id=c.proposal_id), text)

            # and the statusline's "+N more" counts waits, not everything live
            seg = A.statusline_segment(repo.path)
            self.assertIn("⏳ awaiting approval", seg)
            self.assertNotIn("+2 more", seg)

    def test_approve_list_keeps_the_distinction_it_already_had(self):
        """`approve --list` was only HALF right. Its per-row mark read `p.approved` and printed
        "APPROVED (redeemable once)" — but the HEADING counted every live proposal as "waiting for
        your approval", and every row, approved or not, closed with "approve it with: mokata approve
        <id>". So the one surface that knew the difference still asked for the decision again."""
        with _Repo() as repo:
            waiting = repo.propose(args={"k": "waiting"})
            done = repo.propose(args={"k": "done"})
            repo.approve(done.proposal_id)
            out = _approve_list(repo.path)

            self.assertIn("1 durable write(s) waiting for your approval", out)
            self.assertNotIn("2 durable write(s) waiting for your approval", out)
            self.assertIn("ALREADY APPROVED", out)
            # the row marks, unchanged
            rows = {ln.split()[0]: ln for ln in out.splitlines()
                    if ln.startswith("  p-")}
            self.assertIn("APPROVED (redeemable once)", rows[done.proposal_id])
            self.assertIn("awaiting your approval", rows[waiting.proposal_id])
            # and only the UNDECIDED one is handed the approve command
            self.assertIn(A.APPROVE_CMD.format(proposal_id=waiting.proposal_id), out)
            self.assertNotIn(A.APPROVE_CMD.format(proposal_id=done.proposal_id), out)


class TestTheThirdStatus(unittest.TestCase):
    """USED and EXPIRED — the statuses that are not live at all. No surface may name them."""

    def test_a_redeemed_proposal_leaves_every_surface(self):
        with _Repo() as repo:
            p = repo.propose()
            repo.approve(p.proposal_id)
            res = approval.redeem(repo.path, p.proposal_id, tool=p.tool,
                                  args={"k": p.tool}, run_id=RUN)
            self.assertTrue(res.granted, res.reason)

            self.assertEqual(A.statusline_segment(repo.path), "")
            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertNotIn(p.proposal_id, text)
            self.assertIn("nothing is waiting on you", text)
            self.assertNotIn(p.proposal_id, _approve_list(repo.path))

    def test_an_expired_proposal_leaves_every_surface(self):
        with _Repo() as repo:
            p = repo.propose()
            repo.approve(p.proposal_id)
            key = approval.state_key(p.proposal_id)
            store = approval.StateStore(approval.state_dir(repo.path))
            record = dict(store.read(key))
            record["expires_at"] = 1.0                      # long past
            store.write(key, record)

            self.assertEqual(A.statusline_segment(repo.path), "")
            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertNotIn(p.proposal_id, text)
            self.assertNotIn(p.proposal_id, _approve_list(repo.path))


class TestTheSplitDegradesTowardsAsking(unittest.TestCase):
    """`by_decision` reads `approved` off each record with a DEFAULT, and which default it picks is
    a consent decision, not a style one. A record whose decision cannot be read must be treated as
    UNDECIDED — over-asking wastes a human's keystroke, under-asking silently drops a write out of
    the set of things anyone is being asked about."""

    def test_a_record_with_no_readable_decision_counts_as_waiting(self):
        class _Opaque:                       # a degraded / duck-typed record: no `approved` at all
            proposal_id = "p-opaque"
            tool = "memory_add"

        waiting, approved = A.by_decision([_Opaque()])
        self.assertEqual(len(waiting), 1, "an unreadable decision must read as STILL WAITING")
        self.assertEqual(approved, [])

    def test_the_split_is_exhaustive(self):
        """Every record lands in exactly one bucket — nothing is dropped by the split itself."""
        with _Repo() as repo:
            a = repo.propose(args={"k": "a"})
            b = repo.propose(args={"k": "b"})
            repo.approve(b.proposal_id)
            waiting, approved = A.by_decision(approval.pending(repo.path))
            self.assertEqual(sorted(p.proposal_id for p in waiting + approved),
                             sorted([a.proposal_id, b.proposal_id]))
            self.assertEqual([p.proposal_id for p in waiting], [a.proposal_id])
            self.assertEqual([p.proposal_id for p in approved], [b.proposal_id])


class TestThreeSurfacesAgree(unittest.TestCase):
    """The cross-surface pin: for one proposal, the three surfaces must not disagree about who is
    being waited on. This is the invariant the two broken call sites violated."""

    def test_no_surface_asks_for_a_decision_already_recorded(self):
        with _Repo() as repo:
            p = repo.propose(tool="spec_amend", target="spec")
            renders = {
                "pending_lines": lambda: "\n".join(
                    A.pending_lines(repo.path, quiet_when_ok=False)),
                "statusline": lambda: A.statusline_segment(repo.path),
                "approve --list": lambda: _approve_list(repo.path),
            }
            # PROPOSED — every surface may (and does) ask.
            for name, render in renders.items():
                self.assertIn(p.proposal_id, render(), f"{name} hides a real wait")

            repo.approve(p.proposal_id)
            # APPROVED — no surface may ask again.
            for name, render in renders.items():
                text = render()
                self.assertNotIn(A.APPROVE_CMD.format(proposal_id=p.proposal_id), text,
                                 f"{name} re-asks for an approval the human already gave")


if __name__ == "__main__":
    unittest.main()
