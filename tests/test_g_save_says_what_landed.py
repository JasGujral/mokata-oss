"""0.0.29 lane G (#83, #82-2) — a write that did not land is said where it was asked; the TDD block
names the road to RED.

Design: `docs/build/handoff/0029-design/G-gates-mcp-recording.md` §3.2-§3.3 (+ §9 M3, L4). Labels:
**force** = red on the base (`9045ad7`), **guard** = pins behaviour that already held (a road the
new text names). No clocks, no waits: every cell is a synchronous call or one hook process.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _plan import sealed  # noqa: E402
from _support import approve_with_lenses  # noqa: E402

from mokata import gate_hook as G  # noqa: E402
from mokata import session as S  # noqa: E402
from mokata import session_flow as FLOW  # noqa: E402
from mokata import session_save as SS  # noqa: E402
from mokata import tdd_state as T  # noqa: E402
from mokata.awaiting import TEST_RUN_RED_CMD  # noqa: E402
from mokata.brainstorm import (APPROACH_STATE_KEY, BRAINSTORM_PROGRESS_KEY, Approach,  # noqa: E402
                               BrainstormSession)
from mokata.config import Surface  # noqa: E402
from mokata.govern.resume import CHECKPOINT_PREFIX  # noqa: E402
from mokata.mcp import tools_read as TR  # noqa: E402
from mokata.spec_scope import amend_key  # noqa: E402
from mokata.state import StateStore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
RUN = "rung0123456789abcd"
CANARY = "CANARY-SECRET-sk-live-0000"


def _approved(topic="add a caching layer"):
    s = BrainstormSession(topic)
    s.set_anchor(topic)
    s.ask("read/write ratio?")
    s.answer("read-heavy")
    s.propose_approaches([
        Approach("cache-aside", "read-through cache", pros=["simple"], cons=["stale"]),
        Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow writes"]),
    ])
    approve_with_lenses(s, "jas", "cache-aside")
    return s


class _Repo(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        from mokata.init import init_repo
        init_repo(root=self.d, profile="standard", assume_yes=True, out=lambda _: None)
        prev = os.environ.get(S.SESSION_ID_ENV)
        os.environ[S.SESSION_ID_ENV] = RUN
        S.reset_for_test()

        def restore():
            if prev is None:
                os.environ.pop(S.SESSION_ID_ENV, None)
            else:
                os.environ[S.SESSION_ID_ENV] = prev
            S.reset_for_test()
        self.addCleanup(restore)
        FLOW.reset_persist_warnings()
        self.addCleanup(FLOW.reset_persist_warnings)
        self.store = StateStore(T.state_dir(self.d))

    def save(self, **kw):
        with mock.patch("sys.stderr"):                   # the once-per-moment notice
            return TR.session_save(self.d, **kw)


def _fail(exc):
    def boom(*_a, **_k):
        raise exc
    return boom


class LostApprovalIsSaidAtApproval(_Repo):

    def test_a_refused_approval_write_is_ok_false_naming_it_and_its_consequence(self):
        """force — `persist_approach` hits a PermissionError: `ok:false`, `degraded`,
        `approval_persisted:false`, the disk remedy and the gate consequence; progress landed."""
        err = PermissionError(13, "Permission denied: " + CANARY)
        with mock.patch.object(SS, "persist_approach", _fail(err)):
            out = self.save(brainstorm=_approved().to_dict())
        self.assertIs(False, out["ok"])
        self.assertEqual("degraded", out["status"])
        self.assertIs(False, out["approval_persisted"])
        self.assertEqual({APPROACH_STATE_KEY: "PermissionError [errno 13]"}, out["failed"])
        self.assertIn(BRAINSTORM_PROGRESS_KEY, out["wrote"])
        self.assertIs(False, out["empty"])
        msg = out["message"]
        for needle in ("the approach approval is NOT fully on record", "Re-call `session_save`",
                       "mokata doctor", "approach-approval gate blocks implementation"):
            self.assertIn(needle, msg)
        self.assertNotIn("not blocked", json.dumps(out))
        self.assertNotIn("upgrade", msg)


class TheRemedyFitsTheCause(_Repo):

    def test_an_approval_with_no_chosen_approach_is_refused_as_sent(self):
        """force — approved but no chosen approach: the HARD-GATE refuses it, and the reply says
        "refused as sent" (fix the session) — never a retry; on the base it was skipped in silence."""
        s = _approved()
        data = s.to_dict()
        data["chosen"] = None
        out = self.save(brainstorm=data)
        self.assertIs(False, out["ok"])
        self.assertIs(False, out["approval_persisted"])
        self.assertEqual("BrainstormGateError", out["failed"][APPROACH_STATE_KEY])
        self.assertIn("refused as sent", out["message"])
        self.assertIn("fails the same way", out["message"])
        self.assertNotIn("Re-call", out["message"])

    def test_mokatas_own_fault_is_said_as_a_defect(self):
        """force — a TypeError inside the write is a mokata defect, not the user's disk."""
        with mock.patch.object(SS, "persist_approach", _fail(TypeError(CANARY))):
            out = self.save(brainstorm=_approved().to_dict())
        self.assertIn("this is a mokata defect, not your disk", out["message"])
        self.assertEqual("TypeError", out["failed"][APPROACH_STATE_KEY])


class ApproachFirst(_Repo):

    def test_progress_fails_and_the_approval_still_lands(self):
        """force — the approach is written FIRST: a progress failure leaves the approval on record,
        the reply says so (no re-call needed for it), and the gate does not block on approach."""
        with mock.patch.object(SS, "save_brainstorm_progress", _fail(OSError(28, CANARY))):
            out = self.save(brainstorm=_approved().to_dict())
        self.assertIs(False, out["ok"])
        self.assertEqual("degraded", out["status"])
        self.assertIs(True, out["approval_persisted"])
        self.assertIn("the approval IS on record", out["message"])
        self.assertIn("no re-call is needed for the approval", out["message"])
        self.assertTrue(self.store.exists(G.APPROACH_PREFIX + RUN))
        self.store.write(CHECKPOINT_PREFIX + RUN, {"run_id": RUN, "passed": []})
        res = G.check_write(self.d, os.path.join(self.d, "src", "a.py"), RUN)
        self.assertNotEqual(G.GATE_PHASE, res.gate, res.reason)


class EveryLossIsSaid(_Repo):

    def test_a_checkpoint_loss_is_said_without_the_not_blocked_claim(self):
        """force — a `passed` checkpoint that did not land is `ok:false`; the notice names the
        resume consequence, never "your work is not blocked"."""
        notices = []
        flow = FLOW.SessionFlow(Surface.load(self.d), warn=notices.append)
        with mock.patch("mokata.govern.resume.PipelineCheckpoint.mark_passed",
                        _fail(OSError(5, CANARY))):
            res = flow.gate_passed("spec")
        self.assertIsNotNone(res)
        d = res.to_dict()
        self.assertIs(False, d["ok"])
        self.assertIsNone(d.get("approval_persisted"))
        self.assertEqual(1, len(notices))
        self.assertIn("resumes from the last good save", notices[0])
        self.assertNotIn("not blocked", notices[0])

    def test_a_lost_approval_notice_says_the_gate_blocks(self):
        """force — the stderr notice for a lost approval names the gate consequence."""
        notices = []
        flow = FLOW.SessionFlow(Surface.load(self.d), warn=notices.append)
        with mock.patch.object(SS, "persist_approach", _fail(OSError(13, CANARY))):
            flow.approval(_approved().to_dict())
        self.assertEqual(1, len(notices))
        self.assertIn("approach-approval gate blocks implementation", notices[0])

    def test_a_raise_before_any_write_says_nothing_was_saved(self):
        """force — a `brainstorm` that is not a session: `ok:false`, "nothing was saved"."""
        out = self.save(brainstorm={"topic": 7, "approaches": "not-a-list"})
        self.assertIs(False, out["ok"])
        self.assertIn("nothing was saved", out["message"])

    def test_an_unreadable_read_back_leaves_empty_unknown(self):
        """force — the writes landed but what is on disk could not be read back: `ok:false`,
        `empty` is unknown (None), never a confident `true`."""
        with mock.patch.object(SS, "_describe", _fail(OSError(5, CANARY))):
            out = self.save(brainstorm=_approved().to_dict())
        self.assertIs(False, out["ok"])
        self.assertIsNone(out["empty"])
        self.assertIn("read_back", out["failed"])

    def test_a_clean_save_is_unchanged(self):
        """force (the new `approval_persisted` field; the rest guards) — everything landed: `ok:true`,
        `approval_persisted:true`, no `failed`."""
        out = self.save(brainstorm=_approved().to_dict())
        self.assertIs(True, out["ok"])
        self.assertIs(True, out["approval_persisted"])
        self.assertNotIn("failed", out)
        self.assertNotIn("status", out)


class TypeAndErrnoOnly(_Repo):

    def test_the_exception_text_reaches_no_reply_notice_or_gate_text(self):
        """guard (held on the base too; pins it for the new texts) — a canary in every injected
        exception's text appears nowhere the user reads."""
        notices = []
        blobs = []
        for target, exc in (("persist_approach", PermissionError(13, CANARY)),
                            ("persist_approach", TypeError(CANARY)),
                            ("save_brainstorm_progress", OSError(28, CANARY))):
            FLOW.reset_persist_warnings()
            with mock.patch.object(SS, target, _fail(exc)), \
                    mock.patch.object(FLOW, "note_degraded",
                                      side_effect=lambda *a, **k: notices.append(repr((a, k)))):
                blobs.append(json.dumps(TR.session_save(self.d, brainstorm=_approved().to_dict())))
        self.store.write(CHECKPOINT_PREFIX + RUN, {"run_id": RUN, "passed": []})
        blobs.append(G.check_write(self.d, os.path.join(self.d, "src", "a.py"), RUN).reason)
        for b in blobs + notices:
            self.assertNotIn(CANARY, b)


def _gate_repo(test, progress):
    """A registered run with NO approval on record and the given progress record (None: absent;
    bytes: written raw)."""
    st = test.store
    st.write(CHECKPOINT_PREFIX + RUN, {"run_id": RUN, "passed": []})
    path = st.path(G.PROGRESS_PREFIX + RUN)
    if isinstance(progress, bytes):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(progress)
    elif progress is not None:
        st.write(G.PROGRESS_PREFIX + RUN, progress)


class GateSentenceIsAnObservation(_Repo):

    OBS = "records an approved, chosen approach, but no approval is on record"

    def _check(self):
        return G.check_write(self.d, os.path.join(self.d, "src", "a.py"), RUN)

    def test_approved_and_chosen_with_no_approval_is_observed(self):
        """force — the two records disagree: the block says what each shows and the remedy."""
        _gate_repo(self, _approved().to_dict())
        out = self._check()
        self.assertFalse(out.allowed)
        self.assertEqual(G.GATE_PHASE, out.gate)
        self.assertIn(self.OBS, out.reason)
        self.assertIn("Re-call `session_save` with the approved brainstorm", out.reason)
        self.assertIn("mokata gate override", out.reason)

    def test_every_other_state_keeps_the_original_text_and_still_blocks(self):
        """force (chosen-null half; the others guard) — absent, unapproved, chosen null/empty,
        `approved` truthy-but-not-True, corrupt, wrong shape: original text, block."""
        s = _approved().to_dict()
        cases = {
            "absent": None,
            "unapproved": dict(s, approved=False),
            "chosen null": dict(s, chosen=None),
            "chosen empty": dict(s, chosen=""),
            "chosen not an approach": dict(s, chosen="no-such-approach"),
            "chosen a dict": dict(s, chosen={"name": "cache-aside"}),
            "approved string": dict(s, approved="yes"),
            "corrupt": b"{not json",
            "a list": b"[1, 2]",
        }
        for name, progress in cases.items():
            with self.subTest(name):
                try:
                    os.remove(self.store.path(G.PROGRESS_PREFIX + RUN))
                except FileNotFoundError:
                    pass
                _gate_repo(self, progress)
                out = self._check()
                self.assertFalse(out.allowed, name)
                self.assertEqual(G.GATE_PHASE, out.gate)
                self.assertNotIn(self.OBS, out.reason, name)
                self.assertIn("approve an approach first", out.reason, name)

    def test_whole_state_dir_refusal_is_the_original_text(self):
        """guard (the known case, pinned) — when the state directory refused every write, no
        progress record exists either and the gate shows the original text: the approval-time
        reply is the loud channel."""
        _gate_repo(self, None)
        self.assertIn("approve an approach first", self._check().reason)

    def test_an_oversized_record_is_never_parsed(self):
        """force — above the measured knee the read is skipped by size: original text, no parse."""
        _gate_repo(self, _approved().to_dict())
        with mock.patch.object(G, "PROGRESS_READ_MAX_BYTES", 10), \
                mock.patch.object(G, "_read", wraps=G._read) as spy:
            out = self._check()
        self.assertNotIn(self.OBS, out.reason)
        self.assertNotIn(mock.call(mock.ANY, G.PROGRESS_PREFIX + RUN), spy.call_args_list)

    def test_the_real_hook_process_says_it(self):
        """force — the shipped hook (`mokata.hook_cli gate-guard`, one process) blocks with the
        observation sentence."""
        _gate_repo(self, _approved().to_dict())
        payload = json.dumps({"session_id": RUN, "cwd": self.d, "hook_event_name": "PreToolUse",
                              "tool_name": "Write",
                              "tool_input": {"file_path": os.path.join(self.d, "src", "a.py"),
                                             "content": "x = 1\n"}})
        env = dict(os.environ, PYTHONPATH=SRC, MOKATA_SESSION_ID=RUN)
        p = subprocess.run([sys.executable, "-m", "mokata.hook_cli", "gate-guard"], input=payload,
                           cwd=self.d, env=env, text=True, encoding="utf-8", capture_output=True,
                           timeout=600)
        self.assertEqual(G.BLOCK_EXIT, p.returncode, p.stderr)
        self.assertIn(self.OBS, p.stderr)


class GateNamesTheRoad(_Repo):

    def _spec_run(self):
        st = self.store
        st.write(CHECKPOINT_PREFIX + RUN, {"run_id": RUN, "passed": []})
        st.write(G.APPROACH_PREFIX + RUN, {"approach": "the chosen one"})
        st.write(G.SPEC_PREFIX + RUN, sealed({"criteria": [{"id": "AC1", "text": "it works"}]}))

    def _check(self):
        return G.check_write(self.d, os.path.join(self.d, "src", "a.py"), RUN)

    def _names_the_road_first(self, reason):
        self.assertIn(TEST_RUN_RED_CMD, reason)
        self.assertIn("Only if you ran the test and saw it fail", reason)
        self.assertLess(reason.index(TEST_RUN_RED_CMD), reason.index("override"))

    def test_no_red_on_record_names_the_road_before_the_override(self):
        """force — the TDD block names `mokata progress test-run --failed …` before the override;
        the verdict and gate id are unchanged."""
        self._spec_run()
        out = self._check()
        self.assertFalse(out.allowed)
        self.assertEqual(G.GATE_TDD, out.gate)
        self._names_the_road_first(out.reason)
        self.assertIn(f"mokata gate override {G.GATE_TDD}", out.reason)

    def test_the_amend_owed_block_names_it_too(self):
        """force — the amendment's owed-RED block names the same road."""
        self._spec_run()
        self.store.write(T.state_key(RUN), T.to_state(RUN, red=["tests/t.py::old"], green=[]))
        self.store.write(amend_key(RUN), {"status": "closed", "from_version": 1, "to_version": 2,
                                          "red_owed": ["tests/t.py::new"]})
        out = self._check()
        self.assertFalse(out.allowed)
        self.assertEqual(G.GATE_TDD, out.gate)
        self.assertIn("still owe a failing test", out.reason)
        self._names_the_road_first(out.reason)

    def test_the_constant_is_the_hints_remedy(self):
        """force — `awaiting.TEST_RUN_RED_CMD` is exactly the C_NEVER_RUN remedy `hints.py` gives."""
        with open(os.path.join(SRC, "mokata", "hints.py"), encoding="utf-8") as fh:
            text = fh.read()
        spelled = ('"mokata progress test-run --failed --command "\n'
                   '                                   "\\"<your test command>\\" --test <id>"')
        self.assertIn(spelled, text)
        self.assertEqual('mokata progress test-run --failed --command "<your test command>" '
                         '--test <id>', TEST_RUN_RED_CMD)


class TestRunLicensesTheGate(_Repo):

    def test_recording_a_red_through_the_cli_opens_the_gate(self):
        """guard (the road the text names works) — `mokata progress test-run --failed --test <id>`
        then the real gate allows the implementation write; 10^5 ids on record do not change it."""
        self.store.write(CHECKPOINT_PREFIX + RUN, {"run_id": RUN, "passed": []})
        self.store.write(G.APPROACH_PREFIX + RUN, {"approach": "the chosen one"})
        self.store.write(G.SPEC_PREFIX + RUN, sealed({"criteria": [{"id": "AC1", "text": "x"}]}))
        self.assertFalse(G.check_write(self.d, os.path.join(self.d, "src", "a.py"), RUN).allowed)
        env = dict(os.environ, PYTHONPATH=SRC, MOKATA_SESSION_ID=RUN)
        ids = [f"tests/test_x.py::T::test_{i}" for i in range(100000)]
        argv = [sys.executable, "-m", "mokata", "progress", "test-run", "--failed", "--command",
                "pytest -q", "--path", self.d]
        for tid in ids[:3]:
            argv += ["--test", tid]
        p = subprocess.run(argv, cwd=self.d, env=env, text=True, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=600)
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        st = self.store.read(T.state_key(RUN))
        st["red"] = sorted(set(st["red"]) | set(ids))
        self.store.write(T.state_key(RUN), st)
        out = G.check_write(self.d, os.path.join(self.d, "src", "a.py"), RUN)
        self.assertTrue(out.allowed, out.reason)


class NoReaderTakesAFailureForEmpty(_Repo):
    """Round 2 (verifier M-2): every reader of a `SaveResult` (the CLI `mokata session save`, the
    MCP tool, `session push --save-first`) separates "could not be read" from "nothing to save".
    An unknown state never reads as empty, and never exits 0."""

    def cli(self, *argv):
        import contextlib
        import io
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["session", "save", "--path", self.d, *argv])
        return rc, out.getvalue(), err.getvalue()

    def _wrong_shape_progress(self):
        Surface.load(self.d).state.write(BRAINSTORM_PROGRESS_KEY,
                                         {"topic": 7, "questions": "x", "approaches": 5})

    def test_the_cli_says_a_failed_read_back_and_exits_non_zero(self):
        """force — the read-back raises: the CLI names what failed and its cause (type + errno),
        exits non-zero, and never prints "nothing in-flight"."""
        with mock.patch.object(SS, "_describe", _fail(OSError(5, CANARY))):
            rc, out, err = self.cli()
        self.assertNotEqual(0, rc)
        both = out + err
        self.assertNotIn("nothing in-flight", both)
        self.assertIn("could not be read back", err)
        self.assertIn("OSError [errno 5]", err)
        self.assertIn("mokata doctor", err)
        self.assertNotIn(CANARY, both)

    def test_the_cli_on_a_wrong_shape_record_is_not_empty(self):
        """force (unpatched, reachable) — a progress record that is valid JSON of the wrong shape:
        not "nothing in-flight", rc != 0, the cause is said as a defect to report."""
        self._wrong_shape_progress()
        rc, out, err = self.cli()
        self.assertNotEqual(0, rc)
        self.assertNotIn("nothing in-flight", out + err)
        self.assertIn("could not be read back", err)

    def test_the_cli_says_nothing_in_flight_only_when_it_is_known(self):
        """guard — a clean, genuinely empty repo: "nothing in-flight", rc 0."""
        rc, out, _err = self.cli()
        self.assertEqual(0, rc)
        self.assertIn("nothing in-flight", out)

    def test_the_result_itself_says_unknown(self):
        """force — `SaveResult.empty` is None (unknown) when the read-back failed, so no reader can
        take `if res.empty` for "nothing to save"."""
        with mock.patch.object(SS, "_describe", _fail(OSError(5, CANARY))):
            res = SS.save_session(Surface.load(self.d))
        self.assertIsNone(res.empty)
        self.assertFalse(bool(res.empty))

    def test_the_mcp_raise_before_any_write_is_not_empty(self):
        """force — `session_save` that raised before any write: `ok:false` and `empty` unknown
        (None), never `empty:true`."""
        out = self.save(brainstorm={"topic": 7, "approaches": "not-a-list"})
        self.assertIs(False, out["ok"])
        self.assertIsNone(out["empty"])

    def test_push_save_first_refuses_when_the_save_did_not_land(self):
        """force — `session push --save-first`: the save before the push failed, so nothing is
        bundled from a state nobody could read; the refusal names the cause."""
        from mokata import session_bundle as SB
        with mock.patch.object(SS, "_describe", _fail(OSError(5, CANARY))):
            with self.assertRaises(SB.SessionBundleError) as cm:
                SB.plan_session_push(self.d, Surface.load(self.d), "t1", save_first=True)
        self.assertIn("OSError [errno 5]", str(cm.exception))
        self.assertIn("nothing was pushed", str(cm.exception))
        self.assertNotIn(CANARY, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
