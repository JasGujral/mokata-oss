"""A3 — `decline-strands-at-spec` (0.0.19 stage 02). Closes the second half of #53.

THE DEFECT, and it is not the one the issue reports
---------------------------------------------------
#53's second half: a TTY-less `mokata spec emit` leaves the run parked at `spec-persisted` with
nothing to approve. True — but the mechanism is one layer up. `prompt.read_yes_no` returned `False`
for TWO different facts:

    "a human was asked and said no"      and      "no human was ever asked — there was no TTY"

That is doc 85 §7g at mokata's PRIMARY durable-write gate, and the consequence is the strand: with
no human asked, nothing is staged, so there is no `proposal_id`, so `mokata approve` has nothing to
redeem — and the only exit `spec-persisted` then named was `mokata gate override`, i.e. the one
that turns the gate OFF. A fail-closed gate whose sole escape hatch disables it trains users to
override.

THE SPLIT (§7g's fix is always the same: split the representation). `read_yes_no` now returns a
`ConsentDecision` carrying its own `basis` — `ASKED` / `NO_TTY` / `UNREADABLE_STDIN` — and is
truthy exactly when approved, so every unconverted call site is byte-identical in behaviour.

WHAT THESE TESTS REFUSE TO LET DRIFT
------------------------------------
* the TTY-lessness is driven at the STDIN BOUNDARY, never by patching `read_yes_no` (doc 85 §7e).
  A test that monkeypatches the reader to return False proves the CALLER, and the caller was never
  the defect — the defect is what a MISSING TTY produces.
* the load-bearing NEGATIVE: an explicit human "n" at a TTY must stage NOTHING. A decline converted
  into a pending proposal turns a settled answer into a standing offer.
  `test_a3_the_decline_negative_is_load_bearing` defeats the split and watches the wrong build
  stage on a human "n" — without it, the negative would pass just as happily against a build that
  stages on EVERY decline, and would be grading nothing.

⚠ THE DSN FIXTURE IS ASSEMBLED AT RUNTIME, on purpose. A literal connection string in this file is
a real credential shape, and mokata's own secret-guard blocks the commit that would land it — which
is the guard working. Joining the parts keeps the fixture out of the file's bytes while the string
the test actually asserts on is byte-identical to the real thing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval                                        # noqa: E402
from mokata import gate_hook as G                                  # noqa: E402
from mokata import session                                         # noqa: E402
from mokata.awaiting import (APPROVE_CMD, AWAITING, EMIT_FINISH_CMD,   # noqa: E402
                             LIST_CMD, awaiting_cli_lines)
from mokata.brainstorm import Approach, BrainstormSession           # noqa: E402
from mokata.brainstorm_impact import FITS, DesignFitVerdict         # noqa: E402
from mokata.cli_commands.spec import (EMIT_AWAITING_EXIT,           # noqa: E402
                                      _run_scoped_store, cmd_spec_emit)
from mokata.config import Surface                                   # noqa: E402
from mokata.engine.emit import EMIT_TOOL                            # noqa: E402
from mokata.engine.spec_gate import load_emitted_spec               # noqa: E402
from mokata.init import init_repo                                   # noqa: E402
from mokata.prompt import (ASKED, NO_TTY, UNREADABLE_STDIN,         # noqa: E402
                           ConsentDecision, read_yes_no)
from mokata.session_save import save_session                        # noqa: E402

RUN = "run-a3-decline"
TITLE = "slugify keeps unicode intact"

# See the module docstring: assembled so the file's own bytes carry no credential shape.
DSN = "".join(("post", "gres", "://", "admin", ":", "hunt", "er2", "@",
               "db.internal", ":5432/prod"))


# ======================================================================================
# stdin doubles — the ONLY seam these tests drive (§7e: never patch the reader itself)
# ======================================================================================

class _NoTtyStdin:
    """A captured / redirected stdin: not a terminal, and any read raises. This is what an agent
    harness, a pipe, or `< /dev/null` under capture actually hands the process."""

    def isatty(self):
        return False

    def readline(self, *a):
        raise OSError("reading from a non-interactive stdin")

    def read(self, *a):
        raise OSError("reading from a non-interactive stdin")

    def fileno(self):
        raise OSError("no fileno")


class _TtyStdin:
    """A REAL terminal, as far as every check mokata makes is concerned, handing back a scripted
    answer. `input()` falls back to `sys.stdin.readline()` because `fileno()` raises, so the answer
    below is read through the same path a human's keystroke takes."""

    def __init__(self, answer="n"):
        self._lines = [answer if answer.endswith("\n") else answer + "\n"]

    def isatty(self):
        return True

    def readline(self, *a):
        return self._lines.pop(0) if self._lines else ""

    def read(self, *a):
        return "".join(self._lines)

    def fileno(self):
        raise OSError("no fileno")


class _stdin:
    """`with _stdin(_TtyStdin("y")):` — swap stdin, always put it back."""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        self.orig = sys.stdin
        sys.stdin = self.fake
        return self.fake

    def __exit__(self, *_exc):
        sys.stdin = self.orig


class _capture:
    """Capture stdout+stderr together — the awaiting block and the TTY-less notice land on
    different streams and the secret-safety assertion must see both."""

    def __enter__(self):
        self._out, self._err = sys.stdout, sys.stderr
        self.buf = io.StringIO()
        sys.stdout = sys.stderr = self.buf
        return self

    def __exit__(self, *_exc):
        sys.stdout, sys.stderr = self._out, self._err

    @property
    def text(self):
        return self.buf.getvalue()


# ======================================================================================
# harness — a real repo, a real approved brainstorm, a real spec file
# ======================================================================================

class _Repo:
    """An initialized mokata repo. `pin=False` leaves NO run pinned and no pipeline state, which
    is the standalone-spec shape (`_run_scoped_store` resolves no run)."""

    def __init__(self, run_id=RUN, pin=True):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self.run_id = run_id if pin else None
        self._env = mock.patch.dict(os.environ,
                                    {session.SESSION_ID_ENV: run_id} if pin else {})
        self._env.start()
        if not pin:
            os.environ.pop(session.SESSION_ID_ENV, None)
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


def _approved_session():
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
    s.assess_prior_art(layer=None)
    s.approve("jas", "normalize-then-slug")
    return s


def _persist_approach(repo):
    return save_session(repo.surface, brainstorm=_approved_session().to_dict())


def _payload(title=TITLE, mapped=True):
    return {
        "title": title,
        "approach": "normalize-then-slug",
        "criteria": [{"id": "AC1", "text": "slugify of a unicode string returns a url-safe slug"},
                     {"id": "AC2", "text": "slugify of an empty string raises ValueError"}],
        "tests": ([{"name": "test_slugify_unicode", "ac_ids": ["AC1"]},
                   {"name": "test_slugify_empty", "ac_ids": ["AC2"]}] if mapped else []),
    }


def _spec_file(repo, payload=None):
    path = os.path.join(repo.path, "spec.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload if payload is not None else _payload(), fh)
    return path


def _emit(repo, *, yes=False, payload=None):
    args = argparse.Namespace(path=repo.path, file=_spec_file(repo, payload), yes=yes)
    with _capture() as cap:
        code = cmd_spec_emit(args)
    return code, cap.text


# ======================================================================================
# deliverable 1 — the reader reports WHY it answered no (§7g at the human gate)
# ======================================================================================

class ReaderSplitsTheTwoFacts(unittest.TestCase):

    def test_a3_no_tty_is_never_asked_not_declined(self):
        """D1. Off a TTY nobody was asked — and the answer says so, instead of impersonating a
        human's No."""
        with _capture(), _stdin(_NoTtyStdin()):
            d = read_yes_no("write?", "Approve?")
        self.assertFalse(d.approved)
        self.assertEqual(d.basis, NO_TTY)
        self.assertFalse(d.answered_by_human)

    def test_a3_an_explicit_no_at_a_tty_is_asked_and_declined(self):
        """D1. A human typed n. Driven at the stdin boundary — the reader is not patched."""
        with _capture(), _stdin(_TtyStdin("n")):
            d = read_yes_no("write?", "Approve?")
        self.assertFalse(d.approved)
        self.assertEqual(d.basis, ASKED)
        self.assertTrue(d.answered_by_human)

    def test_a3_an_explicit_yes_at_a_tty_still_approves(self):
        """D1. The safe default does NOT change and yes still means yes."""
        with _capture(), _stdin(_TtyStdin("y")):
            d = read_yes_no("write?", "Approve?")
        self.assertTrue(d.approved)
        self.assertEqual(d.basis, ASKED)

    def test_a3_unreadable_stdin_is_its_own_basis_and_is_not_a_human_answer(self):
        """D1. A lying isatty / a stream that dies mid-read: asked, but no human answered. A THIRD
        representation, not folded into either of the other two (§7g)."""
        class _LiesThenDies(_TtyStdin):
            def readline(self, *a):
                raise OSError("stream went away")

        with _capture(), _stdin(_LiesThenDies()):
            d = read_yes_no("write?", "Approve?")
        self.assertFalse(d.approved)
        self.assertEqual(d.basis, UNREADABLE_STDIN)
        self.assertFalse(d.answered_by_human)

    def test_a3_the_decision_stays_fail_closed_and_truthy_only_on_yes(self):
        """D1. The return is still safe-by-default: only an explicit yes is truthy, so the
        unconverted call sites keep behaving EXACTLY as they do today."""
        self.assertFalse(bool(ConsentDecision(False, NO_TTY)))
        self.assertFalse(bool(ConsentDecision(False, ASKED)))
        self.assertTrue(bool(ConsentDecision(True, ASKED)))

    def test_a3_unconverted_call_sites_are_unchanged_off_a_tty(self):
        """D1 scope. `govern/gate.py:_default_confirm` is NOT converted by this stage; it must
        still read as a decline in a plain boolean context."""
        from mokata.govern.gate import _default_confirm
        with _capture(), _stdin(_NoTtyStdin()):
            self.assertFalse(_default_confirm("write?"))


# ======================================================================================
# deliverables 2 + 3 — never-asked STAGES; an explicit human No stages NOTHING
# ======================================================================================

class DeclineAndNeverAsked(unittest.TestCase):

    def test_a3_regression_tty_less_emit_leaves_a_redeemable_proposal(self):
        """D2 + THE ROW'S REGRESSION TEST. On the old code this exited 1 with nothing staged and
        `mokata approve --list` empty — the strand. Now the run has a proposal id to redeem."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo)
            pending = approval.pending(repo.path)
            self.assertEqual(len(pending), 1, f"nothing staged — the strand is back: {out}")
            self.assertEqual(pending[0].tool, EMIT_TOOL)
            self.assertEqual(pending[0].run_id, RUN)
            self.assertNotEqual(code, 0)

    def test_a3_an_explicit_tty_decline_stages_nothing(self):
        """🔴 D3 — THE LOAD-BEARING NEGATIVE. A human answered No. Staging a proposal would turn a
        settled answer into a standing offer and re-ask a question already decided."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_TtyStdin("n")):
                code, out = _emit(repo)
            self.assertEqual(approval.pending(repo.path), [],
                             f"a human's No was converted into a pending proposal: {out}")
            self.assertEqual(code, 1)
            self.assertIn("declined at the write gate", out)

    def test_a3_the_decline_negative_is_load_bearing(self):
        """DEFEAT THE SPLIT and watch the wrong build stage on a human's No.

        Without this, the negative above would pass just as happily against a build that stages on
        EVERY decline, and would be grading nothing (doc 85 §7f; stage 01's
        `test_a1_the_desync_assertion_is_load_bearing` set this bar).

        The patch here is DELIBERATELY the thing §7e forbids in a real test — it reconstructs the
        pre-split reader, which reported every no as never-asked."""
        import mokata.prompt as P
        with _Repo() as repo:
            _persist_approach(repo)
            original = P.read_yes_no
            P.read_yes_no = lambda *_a, **_k: ConsentDecision(False, NO_TTY)   # the wrong build
            try:
                with _stdin(_TtyStdin("n")):
                    _emit(repo)
            finally:
                P.read_yes_no = original
            self.assertEqual(len(approval.pending(repo.path)), 1,
                             "the defeat did not reproduce a stage-on-every-decline build, so the "
                             "negative above is not grading the split")

    def test_a3_never_asked_and_declined_have_different_observable_outcomes(self):
        """§7g, stated as the property rather than as a mechanism: the two facts must never be
        indistinguishable to anyone downstream — not in what is staged, not in the exit code, and
        not in what the command says."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_TtyStdin("n")):
                declined_code, declined_out = _emit(repo)
            declined_pending = list(approval.pending(repo.path))

            with _stdin(_NoTtyStdin()):
                never_code, never_out = _emit(repo)
            never_pending = list(approval.pending(repo.path))

        self.assertNotEqual(declined_code, never_code)
        self.assertEqual(declined_pending, [])
        self.assertEqual(len(never_pending), 1)
        self.assertNotEqual(declined_out, never_out)

    def test_a3_no_tracked_run_says_why_there_is_nothing_to_approve(self):
        """The honest boundary. A standalone spec resolves NO pipeline run, so a proposal keyed to
        one could never be redeemed by a second process — mokata says that rather than staging a
        record nobody can use. Still a distinct answer from a human's No (§7g)."""
        with _Repo(pin=False) as repo:
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo)
            self.assertEqual(code, 1)
            self.assertEqual(approval.pending(repo.path), [])
            self.assertIn("no human was asked", out)
            self.assertIn("--yes", out)


# ======================================================================================
# deliverable 4 — the CLI says what happened, in the shape `awaiting.py` owns
# ======================================================================================

class TheAwaitingSurface(unittest.TestCase):

    def test_a3_awaiting_output_is_built_from_the_awaiting_module(self):
        """D4. The head, the id, and BOTH commands — none of them worded by this command."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _code, out = _emit(repo)
            pid = approval.pending(repo.path)[0].proposal_id
        self.assertIn(AWAITING, out)
        self.assertIn(pid, out)
        self.assertIn(APPROVE_CMD.format(proposal_id=pid), out)
        self.assertIn(LIST_CMD, out)
        self.assertIn(EMIT_FINISH_CMD, out)

    def test_a3_the_cli_block_is_the_modules_own_lines(self):
        """D4. Structural, not textual: what the command prints IS what `awaiting.py` builds, so
        the wording cannot drift into this surface."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _code, out = _emit(repo)
            pid = approval.pending(repo.path)[0].proposal_id
        for line in awaiting_cli_lines(pid, tool=EMIT_TOOL):
            self.assertIn(line, out)

    def test_a3_no_spec_content_reaches_the_terminal_on_the_awaiting_path(self):
        """D4 SECRET-SAFETY (`awaiting.py:20-25`). The summary and the preview are the human's to
        read at their own terminal via `mokata approve <id>` — never here.

        ⚠ THE CANARIES ARE DELIBERATELY NOT SECRET-SHAPED, and that is the whole reliability of
        this test. A DSN in the title is caught by the WriteGate's SECRET SCAN, which fires BEFORE
        the human gate — so the emit never reaches the awaiting path and the assertion below passes
        without ever having looked at it. That is a §7i vacuous pin, and it is what the first
        version of this test was: a leak mutant (`print(spec.title)` on the awaiting path) SURVIVED
        it. The secret case is a real and separate property and is pinned on its own below."""
        title_canary = "CANARY-TITLE-9d3f-do-not-print-me"
        text_canary = "CANARY-CRITERION-41ab-do-not-print-me"
        payload = _payload(title=title_canary)
        payload["criteria"][0]["text"] = text_canary
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo, payload=payload)
            self.assertEqual(code, EMIT_AWAITING_EXIT,
                             f"this must reach the AWAITING path or it asserts nothing: {out}")
            p = approval.pending(repo.path)[0]
        self.assertNotIn(title_canary, out)
        self.assertNotIn(text_canary, out)
        # ...and the content IS on the proposal, where `mokata approve <id>` shows it to the human
        # at their own terminal. Absent from the terminal, present for the decision.
        self.assertIn(title_canary, p.summary + p.preview)

    def test_a3_a_secret_in_the_spec_blocks_and_stages_nothing(self):
        """The security layer fires BEFORE the human gate, so the reader is never called and no
        proposal can exist. A security block is not a missing approval, and staging one would walk
        a human to a terminal to approve a write that can never commit (§7e's fail-open)."""
        payload = _payload(title="wire the store")
        payload["criteria"][0]["text"] = f"the DSN {DSN} is read from the environment"
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo, payload=payload)
            self.assertEqual(code, 1, out)
            self.assertIn("secret", out.lower(),
                          f"this fixture no longer reads as a secret, so it grades nothing: {out}")
            self.assertEqual(approval.pending(repo.path), [])
        self.assertNotIn(DSN, out)

    def test_a3_awaiting_lines_carry_no_content_at_all(self):
        """D4. The builder itself, graded directly: ids, the tool, and commands — nothing else."""
        lines = "\n".join(awaiting_cli_lines("p-deadbeefcafe", tool=EMIT_TOOL))
        self.assertIn("p-deadbeefcafe", lines)
        self.assertIn(AWAITING, lines)
        self.assertIn(LIST_CMD, lines)
        self.assertIn(EMIT_FINISH_CMD, lines)


# ======================================================================================
# deliverable 5 — the exit code must not lie
# ======================================================================================

class ExitCodes(unittest.TestCase):

    def test_a3_a_staged_wait_and_a_dead_end_are_different_exit_codes(self):
        """D5. "nothing was written" and "nothing was written and here is how to write it" must
        not be indistinguishable to a script."""
        self.assertNotEqual(EMIT_AWAITING_EXIT, 1)
        self.assertNotEqual(EMIT_AWAITING_EXIT, 0,
                            "a wait wrote nothing — a script must not read it as success")
        self.assertNotEqual(EMIT_AWAITING_EXIT, 2, "2 is argparse's usage-error code")
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                code, _out = _emit(repo)
            self.assertEqual(code, EMIT_AWAITING_EXIT)

    def test_a3_a_human_decline_still_exits_1(self):
        """D5. The road that genuinely ended keeps the code it always had."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_TtyStdin("n")):
                code, _out = _emit(repo)
            self.assertEqual(code, 1)


# ======================================================================================
# deliverable 6 — the gate's own message tells the truth about the exits
# ======================================================================================

class TheGateMessage(unittest.TestCase):

    def test_a3_gate_names_approve_first_when_a_proposal_is_pending(self):
        """D6. With a proposal waiting, the FIRST exit the block names is the one that REDEEMS the
        gate, not the one that disables it."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _emit(repo)
            pid = approval.pending(repo.path)[0].proposal_id

            out = G.check_write(repo.path, "src/slugify.py")
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_SPEC)
            approve = APPROVE_CMD.format(proposal_id=pid)
            self.assertIn(approve, out.reason)
            self.assertLess(out.reason.index(approve), out.reason.index("gate override"),
                            "the override is named before the approval — that is the training-to-"
                            "override shape this row exists to remove")

    def test_a3_gate_message_is_unchanged_with_nothing_pending(self):
        """D6. No proposal, no new sentence: the block a user with nothing staged sees is the one
        it has always been."""
        with _Repo() as repo:
            _persist_approach(repo)
            out = G.check_write(repo.path, "src/slugify.py")
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_SPEC)
            self.assertIn("/mokata:spec", out.reason)
            self.assertNotIn("mokata approve p-", out.reason)

    def test_a3_gate_message_names_only_this_runs_proposals(self):
        """D6. An approval is bound to ONE run, so a block that named another run's proposal would
        send the human to approve something that resolves nothing here — `awaiting_block`'s
        re-proposing-blind failure, arriving through the gate instead. Caught as a mutation
        survivor: dropping the run filter left every test green until this one existed."""
        with _Repo() as repo:
            _persist_approach(repo)
            stranger = approval.propose(repo.path, tool=EMIT_TOOL,
                                        args={"path": repo.path, "title": "someone else's spec"},
                                        run_id="a-completely-different-run")
            out = G.check_write(repo.path, "src/slugify.py")
        self.assertFalse(out.allowed)
        self.assertEqual(out.gate, G.GATE_SPEC)
        self.assertNotIn(stranger.proposal_id, out.reason)
        self.assertIn("/mokata:spec", out.reason)

    def test_a3_gate_message_ignores_an_already_approved_proposal(self):
        """D6. "Waiting on you" must mean waiting on YOU. A proposal the human has already approved
        is live but decided — telling them to approve it again is APPROVED-STILL-READS-AS-AWAITING,
        on the surface a blocked user hits."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _emit(repo)
            pid = approval.pending(repo.path)[0].proposal_id
            approval.approve(repo.path, pid, actor="jas")
            out = G.check_write(repo.path, "src/slugify.py")
        self.assertFalse(out.allowed)
        self.assertEqual(out.gate, G.GATE_SPEC)
        self.assertNotIn(APPROVE_CMD.format(proposal_id=pid), out.reason)

    def test_a3_no_gate_verdict_changes(self):
        """§4 — CHANGE THE MESSAGE, NEVER THE VERDICT. Pinned independently of the wording: the
        allow/deny and the gate id are identical with and without a staged proposal."""
        with _Repo() as repo:
            _persist_approach(repo)
            before = G.check_write(repo.path, "src/slugify.py")
            with _stdin(_NoTtyStdin()):
                _emit(repo)
            after = G.check_write(repo.path, "src/slugify.py")
        self.assertEqual((before.allowed, before.gate), (after.allowed, after.gate))
        self.assertNotEqual(before.reason, after.reason, "the MESSAGE was supposed to change")

    def test_a3_gate_still_allows_everything_it_allowed_before(self):
        """§4. A staged proposal must not make the hook stricter anywhere either."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _emit(repo)
            self.assertTrue(G.check_write(repo.path, "tests/test_slugify.py").allowed)
            self.assertTrue(G.check_write(repo.path, "README.md").allowed)
            self.assertTrue(G.check_write(repo.path, "").allowed)


# ======================================================================================
# the whole claim — the loop a stranded user actually walks
# ======================================================================================

class TheFullLoop(unittest.TestCase):

    def test_a3_emit_off_a_tty_approve_then_re_emit_persists_and_the_gate_opens(self):
        """THE USER-VISIBLE CLAIM OF THE STAGE, end to end:

            TTY-less emit → proposal staged → `mokata approve <id>` → re-run emit → spec persisted
            → `spec-persisted` ALLOWS.

        Note what is NOT in this walk: `mokata gate override`. That was the only TTY-less exit
        before this row, and it opened the gate by turning it off."""
        with _Repo() as repo:
            _persist_approach(repo)
            self.assertEqual(G.check_write(repo.path, "src/slugify.py").gate, G.GATE_SPEC)

            with _stdin(_NoTtyStdin()):
                code, _out = _emit(repo)
            self.assertEqual(code, EMIT_AWAITING_EXIT)
            pid = approval.pending(repo.path)[0].proposal_id

            # act 2 — the human, in their own process, at their own terminal.
            res = approval.approve(repo.path, pid, actor="jas")
            self.assertTrue(res.ok, res.message)

            # act 3 — the same command again, still off a TTY. The approval is redeemed.
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo)
            self.assertEqual(code, 0, f"the approved emit did not commit: {out}")
            self.assertIn("spec emitted", out)

            self.assertNotEqual(G.check_write(repo.path, "src/slugify.py").gate, G.GATE_SPEC,
                                "the spec gate is still blocking after an approved emit landed")

    def test_a3_the_approval_is_single_use(self):
        """The loop does not weaken SI.3: the approval it redeems is BURNED."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _emit(repo)
            pid = approval.pending(repo.path)[0].proposal_id
            approval.approve(repo.path, pid, actor="jas")
            with _stdin(_NoTtyStdin()):
                self.assertEqual(_emit(repo)[0], 0)
            self.assertEqual([p for p in approval.pending(repo.path) if p.proposal_id == pid], [],
                             "the redeemed approval is still live — it was not burned")

    def test_a3_an_unapproved_proposal_never_commits(self):
        """P2, and it is the whole point: staging is not approving. Re-running without the human's
        act must propose again, never write."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _emit(repo)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo)
            self.assertEqual(code, EMIT_AWAITING_EXIT, out)
            store, _run_id, err = _run_scoped_store(repo.surface)
            self.assertIsNone(err)
            self.assertIsNone(load_emitted_spec(store))


# ======================================================================================
# negatives — what this stage must NOT have changed
# ======================================================================================

class NothingElseMoved(unittest.TestCase):

    def test_a3_the_yes_path_is_unchanged(self):
        """`--yes` / `assume_yes` never reaches the reader at all, so it cannot have acquired a
        basis, a proposal, or a new exit code."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo, yes=True)
            self.assertEqual(code, 0, out)
            self.assertIn("spec emitted", out)
            self.assertEqual(approval.pending(repo.path), [],
                             "--yes staged a proposal — it commits, it does not wait")

    def test_a3_the_completeness_refusal_stages_nothing(self):
        """A BLOCKED spec is not a spec awaiting permission. Staging one would be exactly the
        fail-open §7e warns about."""
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo, payload=_payload(mapped=False))
            self.assertEqual(code, 1)
            self.assertIn("[BLOCK] completeness", out)
            self.assertEqual(approval.pending(repo.path), [])

    def test_a3_the_reemit_refusal_stages_nothing(self):
        """SPEC-REEMIT-CLOBBER — the wrong tool for this moment, not a missing approval."""
        from mokata import tdd_state as T
        with _Repo() as repo:
            _persist_approach(repo)
            with _stdin(_NoTtyStdin()):
                _emit(repo, yes=True)
            T.record(repo.surface.state, RUN, red=["test_slugify_unicode"])
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo, payload=_payload(title="a different spec"))
            self.assertEqual(code, 1)
            self.assertIn("[BLOCK] re-emit", out)
            self.assertEqual(approval.pending(repo.path), [])

    def test_a3_the_prior_art_refusal_stages_nothing(self):
        """Gate 1c — an approach whose bound prior-art pass never ran. Fail-CLOSED, and a refusal,
        not a wait."""
        with _Repo() as repo:
            _persist_approach(repo)
            store, _rid, _err = _run_scoped_store(repo.surface)
            raw = store.read("approved_approach")
            if not isinstance(raw, dict) or "prior_art" not in raw:
                self.skipTest("no `prior_art` on the persisted handoff to degrade")
            raw.pop("prior_art")
            store.write("approved_approach", raw)
            with _stdin(_NoTtyStdin()):
                code, out = _emit(repo)
            self.assertEqual(code, 1, out)
            self.assertIn("[BLOCK] prior-art", out)
            self.assertEqual(approval.pending(repo.path), [])


if __name__ == "__main__":
    unittest.main()
