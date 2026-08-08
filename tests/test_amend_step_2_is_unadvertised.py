"""AMEND-STEP-2-IS-UNADVERTISED (doc 84) — the other half of B-AMEND-STUCK.

"spec amend mostly gets stuck and never runs properly." The engine is not broken. The flow is:

    spec_amend  ->  a proposal  ->  the human runs `mokata approve <id>`  ->  NOTHING HAPPENS
                                    until the amendment is FINISHED by re-running the flow.

That third step is real and it was stated in exactly ONE place — `gate_hook`'s regressed-run
refusal — which fires only when the user attempts a DEVELOPMENT WRITE against the regressed run.
Follow the amend flow itself and you never see it. `pending_lines` said "amendment pending —
approve {pid} or --abort"; `awaiting_block` carried `awaiting_abort_command` and no finish
command at all. So the menu a user was shown had exactly one way FORWARD (approve — which does
not land it) and one way BACK (abort), and the step that actually completes the thing was
advertised nowhere on the path they were walking.

B-AMEND-STUCK made the proposal id loud. This closes what it left: what to do once you have
approved. Compounded with APPROVED-STILL-READS-AS-AWAITING — approve, watch nothing change, and be
told to approve again — it is exactly what "wedged" looked like.

THE GENERAL PIN lives here too: a gated flow that advertises a way to ABANDON must advertise a way
to FINISH. An escape hatch offered without a completion step is a menu whose only exit is giving up.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import tempfile
import unittest

import _support  # noqa: F401

from mokata import approval                                        # noqa: E402
from mokata import awaiting as A                                   # noqa: E402
from mokata.init import init_repo                                  # noqa: E402

RUN = "run-amend-step-2"


class _Repo:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.dir.cleanup()

    def amend_proposal(self):
        return approval.propose(self.path, tool=A.AMEND_TOOL_NAME,
                                args={"item": "D1"}, run_id=RUN,
                                target="state/emitted_spec.json",
                                summary="amend v1 -> v2", preview="the diff")


class TestTheFinishCommandExists(unittest.TestCase):

    def test_the_finish_command_is_one_string_in_one_place(self):
        """`AMEND_ABORT_CMD`'s sibling. Named in the `awaiting` module for the same reason the
        abort is: so doctor, the tool result and the gate refusal cannot disagree about what the
        step is called."""
        self.assertTrue(A.AMEND_FINISH_CMD)
        self.assertIn("AMEND_FINISH_CMD", A.__all__)
        self.assertNotEqual(A.AMEND_FINISH_CMD, A.AMEND_ABORT_CMD)

    def test_the_finish_command_runs_rather_than_erroring(self):
        """The finish command is the one string a STUCK user copies to a shell, so it must be a
        command that works. `"mokata spec amend"` bare exits 1 with "--file is required" — a
        remedy that names a remedy that fails, which is the one thing a refusal may not do.

        `--file` is genuinely required and that was CHECKED, not assumed: `approval.Proposal`
        stores a `content_hash`, never the args, and `approval.redeem` re-derives the hash from
        arguments the caller supplies. Content-bound consent is exactly why the payload must come
        back with the redemption, so the approved proposal cannot carry the spec home. The string
        was wrong; the CLI was right."""
        from mokata.cli_commands import spec as spec_cli
        self.assertIn("--file", A.AMEND_FINISH_CMD)
        # The flag the constant advertises must be one the parser actually defines, so a rename
        # of the CLI option cannot leave this string quietly pointing at nothing.
        import inspect
        self.assertIn('"--file"', inspect.getsource(spec_cli),
                      "the finish command advertises a flag `spec amend` does not define")

    def test_the_prefix_trap_is_gone_rather_than_merely_avoided(self):
        """`"mokata spec amend"` USED TO BE a substring of `"mokata spec amend --abort"`, so a
        naive `assertIn(AMEND_FINISH_CMD, text)` passed on a surface rendering ONLY the abort.
        Rendering the required argument dissolves that: neither command is a prefix of the other
        any more. Kept as a pin — if the placeholder is ever dropped, the trap returns silently and
        several assertions in this file go back to grading nothing."""
        self.assertNotIn(A.AMEND_FINISH_CMD, A.AMEND_ABORT_CMD,
                         "the finish command is a prefix of the abort again — the trap is back")
        self.assertNotIn(A.AMEND_ABORT_CMD, A.AMEND_FINISH_CMD)
        self.assertNotIn(f"`{A.AMEND_FINISH_CMD}`", f"`{A.AMEND_ABORT_CMD}`")

    def test_the_gate_refusal_reads_the_constant_rather_than_repeating_it(self):
        """`gate_hook`'s regressed-run refusal was the ONE place step 2 was stated, and it stated
        it as a LITERAL. A second copy of a string is a second thing to forget to change — so the
        literal must be gone, not merely accompanied by an import."""
        import inspect

        from mokata import gate_hook
        src = inspect.getsource(gate_hook)
        self.assertIn("{AMEND_FINISH_CMD}", src,
                      "the gate refusal must INTERPOLATE the shared constant")
        # ★ STRENGTHENED AT 0.0.17 STAGE 6. This asserted `assertNotIn(f"`{AMEND_FINISH_CMD}`")` —
        # it could only ever catch a hardcode matching the constant's CURRENT text. When D2's rider
        # re-rendered the constant with its `--file` placeholder, stage 19b's own M07 mutant (which
        # plants the OLD bare literal) went from RED to GREEN: the pin silently stopped grading the
        # thing it exists for. `gate_hook` has NO legitimate raw `mokata spec amend` literal — the
        # two shared constants are the only source — so the honest assertion is that no such
        # literal appears AT ALL, in any spelling, present or future.
        self.assertNotIn("mokata spec amend", src,
                         "gate_hook hardcodes a `mokata spec amend ...` literal instead of "
                         "interpolating AMEND_FINISH_CMD / AMEND_ABORT_CMD — the two surfaces "
                         "can drift apart again")
        self.assertNotIn(f'"{A.AMEND_ABORT_CMD}"', src,
                         "gate_hook still hardcodes the abort command")


class TestAwaitingBlockAdvertisesTheFinish(unittest.TestCase):

    def test_the_amend_block_names_the_finish_beside_the_abort(self):
        block = A.awaiting_block("p-amend", A.AMEND_TOOL_NAME)
        self.assertEqual(block.get("awaiting_abort_command"), A.AMEND_ABORT_CMD)
        self.assertEqual(block.get("awaiting_finish_command"), A.AMEND_FINISH_CMD,
                         "the amend block offered a way to abandon and no way to complete")

    def test_the_approved_head_says_approval_alone_does_not_land_it(self):
        """THE sentence the whole flow was missing. A human who has approved has done everything
        asked of them and the amendment still is not in — so the head must say so, not merely
        instruct the model."""
        block = A.awaiting_block("p-amend", A.AMEND_TOOL_NAME, approved=True)
        head = block["awaiting"]
        self.assertIn(f"`{A.AMEND_FINISH_CMD}`", head)
        self.assertRegex(head, r"does NOT land|not land|still not")

    def test_a_non_amend_tool_grows_no_amend_keys(self):
        """The finish command belongs to the flow that has one. A memory write's redeem step is
        the re-call the head already names, and inventing a CLI command for it would be a lie."""
        block = A.awaiting_block("p-mem", "memory_add")
        self.assertNotIn("awaiting_finish_command", block)
        self.assertNotIn("awaiting_abort_command", block)


class TestPendingLinesAdvertiseTheFinish(unittest.TestCase):

    def test_an_approved_amendment_is_told_how_to_finish(self):
        """`doctor`'s answer to "I approved it and nothing happened"."""
        with _Repo() as repo:
            p = repo.amend_proposal()
            approval.approve(repo.path, p.proposal_id, actor="jas")
            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertIn(p.proposal_id, text)
            self.assertIn(f"`{A.AMEND_FINISH_CMD}`", text,
                          "approved, blocking development, and no way forward named")
            self.assertRegex(text, r"does NOT land|not land|still not")

    def test_a_pending_amendment_still_names_both_roads(self):
        """Before approval the menu must be complete too: approve, finish, or abandon."""
        with _Repo() as repo:
            p = repo.amend_proposal()
            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertIn(A.APPROVE_CMD.format(proposal_id=p.proposal_id), text)
            self.assertIn(f"`{A.AMEND_ABORT_CMD}`", text)
            self.assertIn(f"`{A.AMEND_FINISH_CMD}`", text,
                          "the PENDING menu names the abort but not the finish")

    def test_an_approved_non_amend_write_is_not_given_an_amend_command(self):
        with _Repo() as repo:
            p = approval.propose(repo.path, tool="memory_add", args={"k": "v"}, run_id=RUN,
                                 target="memory:x", summary="s", preview="p")
            approval.approve(repo.path, p.proposal_id, actor="jas")
            text = "\n".join(A.pending_lines(repo.path, quiet_when_ok=False))
            self.assertNotIn(A.AMEND_FINISH_CMD, text)
            self.assertNotIn(A.AMEND_ABORT_CMD, text)


class TestNoFlowAdvertisesFewerCommandsThanItNeeds(unittest.TestCase):
    """THE GENERAL PIN, and the reason this is a stage rather than a string edit.

    A gated flow that can be ABANDONED can also be COMPLETED. Advertising only the abort — which is
    what the amend path did — presents a menu whose sole exit is giving up, on a surface a user
    reaches precisely because they cannot tell a wait from a hang. This binds every tool, present
    and future, not just `spec_amend`."""

    # Every tool whose wait needs a step beyond `mokata approve <id>`. A new one added to
    # `awaiting_block` without its finish command fails here.
    FLOWS = [A.AMEND_TOOL_NAME, "memory_add", "spec_emit", "init"]

    def test_an_advertised_abort_is_never_offered_without_a_finish(self):
        for tool in self.FLOWS:
            for approved in (False, True):
                block = A.awaiting_block("p-x", tool, approved=approved)
                with self.subTest(tool=tool, approved=approved):
                    if "awaiting_abort_command" in block:
                        self.assertIn("awaiting_finish_command", block,
                                      f"{tool} offers a way to abandon and no way to complete")

    def test_every_wait_names_at_least_one_way_forward(self):
        """The floor beneath the pin above: no state of any flow may render with nothing but an
        id. Proposed -> approve it; approved -> the step that lands it."""
        for tool in self.FLOWS:
            proposed = A.awaiting_block("p-x", tool)["awaiting"]
            approved = A.awaiting_block("p-x", tool, approved=True)["awaiting"]
            with self.subTest(tool=tool):
                self.assertIn(A.APPROVE_CMD.format(proposal_id="p-x"), proposed)
                self.assertRegex(approved, r"Re-call|re-run",
                                 f"{tool}'s approved state names no way to land the write")

    def test_the_commands_a_flow_advertises_never_shrink_after_approval(self):
        """Approval must not REMOVE a road. The abort stays available after approval — an approved
        amendment that turns out wrong is still abandonable — so a block that dropped it would
        strand a user who changed their mind at the last step."""
        for tool in self.FLOWS:
            before = {k for k in A.awaiting_block("p-x", tool) if k.endswith("_command")}
            after = {k for k in A.awaiting_block("p-x", tool, approved=True)
                     if k.endswith("_command")}
            with self.subTest(tool=tool):
                self.assertTrue(before <= after,
                                f"{tool} advertises fewer commands once approved: "
                                f"{sorted(before - after)}")


if __name__ == "__main__":
    unittest.main()
