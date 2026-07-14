"""SS.S3 — FORCE-SHARE: gated sharing of a SAVED (including in-progress) session.

Autosaved in-flight state exists (SS.S0–S2) but the share path predates it. This stage
exercises the saved-state → bundle → teammate → resume chain END TO END under the gates and
closes the receiver-side approval leak. The THREE binding guards under test:

  * save UNGATED / share GATED — the share rides the SAME `approve` WriteGate path; the local
    save/autosave is untouched;
  * HARD-GATE survives persistence round-trips — an approved source session's approval NEVER
    crosses the bundle; the receiver's slot stays empty until THEIR gate passes;
  * content-hashed, repo-fingerprinted gated bundles — a tampered bundle is refused by hash, a
    wrong-repo bundle by fingerprint.

Plus P15/P23: every bundled value is secret-scanned; a hit hard-blocks the share, the refusal
NAMES THE KEY (never the secret value), and nothing secret reaches any output.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _support import approve_with_lenses, mcp_commit

from mokata import session as S
from mokata import session_bundle as SB
from mokata import session_save as SAVE
from mokata import mcp_server as M
from mokata.brainstorm import (
    APPROACH_STATE_KEY,
    BRAINSTORM_PROGRESS_KEY,
    Approach,
    BrainstormSession,
    load_approved_approach,
    restore_brainstorm_progress,
)
from mokata.config import Surface
from mokata.init import init_repo

# A DSN assembled from fragments so no literal secret lives in this file (mokata's own guard
# would otherwise block writing/committing it — exactly the thing under test).
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _pin(sid):
    """Pin THIS 'window' to a stable session identity (a fresh receiver is a DIFFERENT id)."""
    os.environ[S.SESSION_ID_ENV] = sid
    S.reset_for_test()


def _in_progress(topic="redesign checkout retries", n=3):
    """A NOT-approved, in-flight brainstorm with `n` answered turns (no approval)."""
    s = BrainstormSession(topic)
    for i in range(n):
        s.ask(f"question {i}?")
        s.answer(f"answer {i}")
    assert s.approved is False
    return s


def _approved(topic="add a caching layer"):
    """An APPROVED brainstorm (answered turn + candidate approaches + a chosen/approved one)."""
    s = BrainstormSession(topic)
    s.ask("read/write ratio?")
    s.answer("read-heavy")
    s.propose_approaches([
        Approach("cache-aside", "read-through cache", pros=["simple"], cons=["stale"]),
        Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow writes"]),
    ])
    approve_with_lenses(s, "jas", "cache-aside")
    return s


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()


# ===================================================================== THE headline test
class TestHalfBrainstormHandoff(_Base):
    def test_ss_s3_regression(self):
        """Session A autosaves mid-brainstorm (k turns, NO approval) → session_push through the FULL
        human gate (SI.3: propose → a human mints the approval out-of-band → the model redeems it)
        → bundle → a fresh RECEIVER surface pulls → the receiver has all k turns, can continue the
        brainstorm, and has NO approved approach until their own gate passes."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            # --- sender: autosave mid-brainstorm through the REAL (ungated) save path ---
            _pin("sessA")
            src = _repo(a)
            s = _in_progress(n=3)
            saved = SAVE.save_session(src, brainstorm=s.to_dict())   # UNGATED local save
            self.assertFalse(saved.empty)

            # --- the share is GATED and rides the HUMAN-MINTED approval (SS.S4: in-progress needs
            # explicit consent; SI.3: `approve=true` is not consent — a human approves out-of-band) ---
            res = mcp_commit(M.session_push, path=a, tag="handoff", allow_in_progress=True)
            self.assertEqual(res["status"], "committed", res)
            self.assertTrue(res["committed"])

            # --- receiver: a DIFFERENT window/session pulls into a DIFFERENT repo ---
            _pin("sessB")
            _repo(b)
            pres = mcp_commit(M.session_pull, path=a, tag="handoff", into=b)
            self.assertTrue(pres["committed"], pres)

            # the receiver has ALL k answered turns and can continue the brainstorm
            _pin("sessB")
            fresh = Surface.load(b)
            bs = restore_brainstorm_progress(fresh.state)
            self.assertIsNotNone(bs)
            self.assertEqual(len(bs.answered_questions), 3)
            self.assertEqual(bs.topic, "redesign checkout retries")

            # ...but NO approved approach until THEIR gate passes (HARD-GATE survives)
            self.assertFalse(bs.approved, "HARD-GATE breached: approval crossed the bundle")
            self.assertIsNone(load_approved_approach(fresh.state),
                              "an approved-approach record was conjured on the far side")


# ===================================================================== gate honesty
class TestGateHonesty(_Base):
    def test_push_without_approve_is_proposed_only_and_labels_in_progress(self):
        """push without approve → proposed-only, nothing written/exported; the proposal summary
        labels the in-progress state truthfully (answered turns + no approved approach)."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=3).to_dict())

            # SS.S4: consent to share the in-progress session is explicit; without approve it's
            # still a proposed-only gate (the tightening only refuses the flag-less case).
            res = M.session_push(path=a, tag="handoff", allow_in_progress=True)   # NO approve
            self.assertEqual(res["status"], "proposed")
            self.assertNotEqual(res.get("committed"), True)
            self.assertFalse(os.path.exists(SB.bundle_path(a, "handoff")),
                             "a proposed-only push wrote a bundle")

            label = res["in_progress"]
            self.assertEqual(label["answered"], 3)
            self.assertIs(label["approved"], False)
            self.assertIn("no approved approach", label["label"].lower())

    def test_completed_session_proposal_is_labeled_complete(self):
        """A COMPLETED (approved) session is not mislabeled as in-progress."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_approved().to_dict(), passed=["brainstorm"])
            res = M.session_push(path=a, tag="done")
            self.assertEqual(res["status"], "proposed")
            self.assertIs(res["in_progress"]["approved"], True)


# ===================================================================== secret scan (P15/P23)
class TestSecretScan(_Base):
    def test_secret_in_a_turn_answer_is_blocked_named_by_key(self):
        """A planted secret in a turn answer → the share is hard-blocked, the offending KEY is
        named (never the value), nothing leaks, and nothing secret reaches any output."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            s = _in_progress(n=2)
            s.questions[-1].answer = _SECRET_DSN     # a secret hidden in the transcript
            SAVE.save_session(src, brainstorm=s.to_dict())

            # even WITH a real human approval, the WriteGate hard-blocks the secret
            res = mcp_commit(M.session_push, path=a, tag="leaky", allow_in_progress=True)
            self.assertEqual(res["status"], "blocked")
            self.assertFalse(res["committed"])
            # the refusal NAMES the offending state key — not the secret content
            self.assertIn(BRAINSTORM_PROGRESS_KEY, res["blocked_keys"])
            # nothing leaked to disk
            self.assertFalse(os.path.exists(SB.bundle_path(a, "leaky")),
                             "a secret-bearing bundle was written")
            # nothing secret in ANY output surface
            self.assertNotIn(_SECRET_DSN, json.dumps(res))
            self.assertNotIn("tok3n_pw", json.dumps(res))


# ===================================================================== approval non-transfer
class TestApprovalNonTransfer(_Base):
    def test_approved_session_does_not_populate_receiver_approval_slot(self):
        """A bundle from an APPROVED session → the receiver's approval slot is NOT auto-populated;
        the receiver-side gate owns it. Progress DOES cross; approval authority does NOT."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_approved().to_dict(),
                              passed=["brainstorm", "analysis"])
            # locally the approval is real
            self.assertIsNotNone(load_approved_approach(src.state))

            res = mcp_commit(M.session_push, path=a, tag="done")
            self.assertTrue(res["committed"], res)

            _pin("sessB")
            _repo(b)
            pres = mcp_commit(M.session_pull, path=a, tag="done", into=b)
            self.assertTrue(pres["committed"], pres)

            _pin("sessB")
            fresh = Surface.load(b)
            # the receiver-side gate OWNS approval — nothing auto-approved here
            self.assertIsNone(load_approved_approach(fresh.state),
                              "approval slot auto-populated across the machine boundary")
            bs = restore_brainstorm_progress(fresh.state)
            self.assertIsNotNone(bs)
            self.assertFalse(bs.approved, "approval crossed the machine boundary")
            # progress still crossed — the answered turn is present
            self.assertEqual(len(bs.answered_questions), 1)


# ===================================================================== integrity
class TestIntegrity(_Base):
    def test_tampered_bundle_is_refused_by_content_hash(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(topic="tamper-target", n=2).to_dict())
            self.assertTrue(mcp_commit(M.session_push, path=a, tag="t",
                                       allow_in_progress=True)["committed"])

            # flip a byte INSIDE the hashed payload (a state value) — the stored hash no longer matches
            p = SB.bundle_path(a, "t")
            with open(p, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertIn("tamper-target", raw)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(raw.replace("tamper-target", "tampered-XXXXX", 1))

            _pin("sessB")
            _repo(b)
            res = mcp_commit(M.session_pull, path=a, tag="t", into=b)
            self.assertEqual(res["status"], "error")
            self.assertRegex(res["message"].lower(), r"hash|corrupt")

    def test_wrong_repo_bundle_surfaces_a_fingerprint_mismatch(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=2).to_dict())
            self.assertTrue(mcp_commit(M.session_push, path=a, tag="fp",
                                       allow_in_progress=True)["committed"])

            _pin("sessB")
            _repo(b)
            # make repo B a DIFFERENT codebase (distinctive top-level layout)
            os.makedirs(os.path.join(b, "src"), exist_ok=True)
            with open(os.path.join(b, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("# a totally different project\n")

            res = M.session_pull(path=a, tag="fp", into=b)   # mismatch surfaces before any write
            self.assertEqual(res["status"], "mismatch")
            self.assertNotEqual(res["bundle_fingerprint"], res["target_fingerprint"])


# ===================================================================== completed share unchanged
class TestCompletedShareUnchanged(_Base):
    def test_completed_session_share_is_byte_identical_and_still_carries_the_approval(self):
        """Sharing a COMPLETED session is unchanged: the bundle FORMAT gains no label field, the
        build is deterministic (byte-identical), and the SHARE still carries the approval — only
        the RECEIVER neutralizes it (SS.S3's fix is receiver-side, not push-side)."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_approved().to_dict(), passed=["brainstorm"])

            b1 = SB.build_session_bundle(src, author="jas", now="2026-07-13T00:00:00+00:00")
            b2 = SB.build_session_bundle(src, author="jas", now="2026-07-13T00:00:00+00:00")
            # format unchanged: no in-progress label injected into the BUNDLE (labels live in the
            # gate summary, not the wire format — that is SS.S4's job)
            self.assertNotIn("in_progress", b1)
            # deterministic → byte-identical build→build
            self.assertEqual(SB.serialize_bundle(b1), SB.serialize_bundle(b2))
            # the push still SHARES the approved approach; the receiver — not the sender — drops it
            self.assertIn(APPROACH_STATE_KEY, b1["state"])


if __name__ == "__main__":
    unittest.main()
