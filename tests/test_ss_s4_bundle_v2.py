"""SS.S4 — BUNDLE V2: session_state + bounded secret-scanned transcript + the three share flags.

SS.S3 shares state honestly but the wire is v1-shaped: no explicit session_state/transcript/meta
sections, no snapshot-then-share, no explicit consent marker for in-progress, no cross-repo
requirements handoff. SS.S4 closes that gap while keeping every SS.S3 invariant intact:

  * bundle v2 = explicit version marker + sections (session_state = the SS.S0 keys as today,
    transcript = the bounded + per-turn-secret-scanned brainstorm Q&A record, meta = the SS.S3
    in-progress label + counts). A v2 bundle presented to a reader that cannot honor its version
    REFUSES LOUDLY (D6-class — never a silent strip). v1 bundles remain pullable unchanged.
  * --save-first  — snapshot through the SS.S0/SS.S2 save path first, then bundle it (atomic).
  * --allow-in-progress — WITHOUT it, pushing an in-progress session REFUSES (honest summary +
    hint); WITH it, proceeds through the gate exactly as SS.S3. Completed sessions unaffected.
  * --requirements-only — bundle ONLY the distilled requirements (anchor + synthesis +
    requirement lines; NO approaches/approval/transcript/checkpoints), marked cross-repo: the
    fingerprint check is replaced by an explicit origin-repo label the receiver sees at the gate.

P15/P23: the transcript is the highest-risk payload — bounded (head+tail, dropped count declared)
and per-turn secret-scanned (a hit hard-blocks + is named by turn index). No DSN value ever
reaches any result, label, or refusal.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
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
from mokata.govern.resume import CHECKPOINT_PREFIX
from mokata.init import init_repo

# A DSN assembled from fragments so no literal secret lives in this file (mokata's own guard
# would otherwise block writing/committing it — exactly the thing under test).
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _pin(sid):
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
class TestFullHandoffV2(_Base):
    def test_ss_s4_regression(self):
        """A autosaves k turns → push --save-first --allow-in-progress → B pulls → B has the k
        turns AND the bounded transcript (truncation declared when k exceeds the cap), no approval."""
        k = SB.TRANSCRIPT_MAX_TURNS + 5           # exceed the cap so truncation is exercised
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=k).to_dict())   # UNGATED autosave

            # one atomic action: snapshot-then-share an in-progress session, consented (SI.3: the
            # consent is a HUMAN-minted approval redeemed by id — never a model-typed flag)
            res = mcp_commit(M.session_push, path=a, tag="handoff",
                             save_first=True, allow_in_progress=True)
            self.assertEqual(res["status"], "committed", res)
            self.assertTrue(res["committed"])
            self.assertEqual(res["bundle_version"], 2)

            # --- receiver: a DIFFERENT window/session pulls into a DIFFERENT repo ---
            _pin("sessB")
            _repo(b)
            pres = mcp_commit(M.session_pull, path=a, tag="handoff", into=b)
            self.assertTrue(pres["committed"], pres)

            # the receiver has ALL k answered turns in resumable state
            _pin("sessB")
            fresh = Surface.load(b)
            bs = restore_brainstorm_progress(fresh.state)
            self.assertIsNotNone(bs)
            self.assertEqual(len(bs.answered_questions), k)
            self.assertEqual(bs.topic, "redesign checkout retries")
            # ...but NO approved approach (HARD-GATE survives — SS.S3 unchanged)
            self.assertFalse(bs.approved)
            self.assertIsNone(load_approved_approach(fresh.state))

            # ...and the bounded transcript travelled, with the drop declared
            pulled = SB.plan_session_pull(a, "handoff", b, force=True).bundle
            tr = pulled["transcript"]
            self.assertTrue(tr["truncated"])
            self.assertEqual(tr["total"], k)
            self.assertEqual(tr["dropped"], k - SB.TRANSCRIPT_MAX_TURNS)
            self.assertEqual(len(tr["turns"]), SB.TRANSCRIPT_MAX_TURNS)


# ===================================================================== flag honesty
class TestAllowInProgressFlag(_Base):
    def test_in_progress_push_without_flag_refuses(self):
        """WITHOUT --allow-in-progress an in-progress push REFUSES: honest summary, nothing
        exported, hint names the flag; nothing secret/content leaks."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=3).to_dict())

            res = mcp_commit(M.session_push, path=a, tag="handoff")   # NO allow_in_progress
            self.assertEqual(res["status"], "in_progress", res)
            self.assertFalse(res.get("committed"))
            self.assertFalse(os.path.exists(SB.bundle_path(a, "handoff")),
                             "a refused in-progress push wrote a bundle")
            # honest summary + a hint that NAMES the flag
            self.assertEqual(res["in_progress"]["answered"], 3)
            self.assertIs(res["in_progress"]["approved"], False)
            self.assertIn("allow", res["hint"].lower())
            self.assertIn("progress", res["hint"].lower())

    def test_in_progress_push_with_flag_is_a_gated_proposal(self):
        """WITH --allow-in-progress (and no approve) the push is a proposed-only gate, as SS.S3."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=3).to_dict())

            res = M.session_push(path=a, tag="handoff", allow_in_progress=True)  # no approve
            self.assertEqual(res["status"], "proposed", res)
            self.assertNotEqual(res.get("committed"), True)
            self.assertFalse(os.path.exists(SB.bundle_path(a, "handoff")))
            self.assertEqual(res["in_progress"]["answered"], 3)

    def test_completed_session_needs_no_flag(self):
        """A COMPLETED (approved) session pushes without any flag — unaffected by the tightening."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_approved().to_dict(), passed=["brainstorm"])
            res = mcp_commit(M.session_push, path=a, tag="done")   # no flag at all
            self.assertEqual(res["status"], "committed", res)
            self.assertTrue(res["committed"])


# ===================================================================== --save-first
class TestSaveFirst(_Base):
    def test_save_first_snapshots_before_bundling(self):
        """--save-first routes through the ungated SS.S0 save path first, then bundles the
        affirmed snapshot — one atomic user action (no gap between save and share)."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=2).to_dict())

            res = mcp_commit(M.session_push, path=a, tag="atomic",
                             save_first=True, allow_in_progress=True)
            self.assertTrue(res["committed"], res)

            _pin("sessB")
            _repo(b)
            self.assertTrue(mcp_commit(M.session_pull, path=a, tag="atomic", into=b)["committed"])
            _pin("sessB")
            bs = restore_brainstorm_progress(Surface.load(b).state)
            self.assertEqual(len(bs.answered_questions), 2)


# ===================================================================== --requirements-only
class TestRequirementsOnly(_Base):
    def test_bundle_holds_only_the_requirements_sections(self):
        """A requirements-only bundle carries ONLY the distilled requirements — NO
        approaches/approval/transcript/checkpoints — and is marked cross-repo."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            # even an APPROVED session with checkpoints distills down to requirements only
            SAVE.save_session(src, brainstorm=_approved().to_dict(),
                              passed=["brainstorm", "analysis"])

            bundle = SB.build_session_bundle(src, requirements_only=True, author="jas",
                                             now="2026-07-13T00:00:00+00:00")
            self.assertEqual(bundle["schema_version"], 2)
            self.assertTrue(bundle["cross_repo"])
            self.assertTrue(bundle["origin_repo"])            # an origin LABEL, not a machine path
            self.assertIn(SB.SESSION_REQUIREMENTS_KEY, bundle["state"])

            # absent: approval, checkpoints, transcript, approaches
            self.assertNotIn(APPROACH_STATE_KEY, bundle["state"])
            self.assertNotIn(BRAINSTORM_PROGRESS_KEY, bundle["state"])
            self.assertFalse([k for k in bundle["state"] if k.startswith(CHECKPOINT_PREFIX)])
            self.assertIsNone(bundle.get("transcript"))
            reqs = bundle["state"][SB.SESSION_REQUIREMENTS_KEY]
            self.assertNotIn("approaches", reqs)
            self.assertIn("requirements", reqs)               # the requirement/constraint lines
            self.assertTrue(bundle["meta"]["requirements_only"])

    def test_requirements_only_pulls_into_a_different_repo_with_origin_label(self):
        """The cross-repo fingerprint exception: a requirements-only bundle hydrates into a
        DIFFERENT codebase, and the origin repo is labelled at the gate (not a silent mis-apply)."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=3).to_dict())
            push = mcp_commit(M.session_push, path=a, tag="reqs", requirements_only=True)
            self.assertTrue(push["committed"], push)

            _pin("sessB")
            _repo(b)
            # make repo B a DEMONSTRABLY different codebase (distinct top-level layout)
            os.makedirs(os.path.join(b, "src"), exist_ok=True)
            with open(os.path.join(b, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("# a totally different project\n")

            # a NORMAL bundle would mismatch here; the requirements-only one is cross-repo by design
            pres = mcp_commit(M.session_pull, path=a, tag="reqs", into=b)
            self.assertTrue(pres["committed"], pres)
            self.assertTrue(pres["cross_repo"])
            self.assertTrue(pres["origin_repo"])              # the receiver SEES the origin label

    def test_secret_in_a_requirement_line_is_hard_blocked(self):
        """A secret hidden in an answer (→ a requirement line) still hard-blocks the share, names
        the offending key, and never leaks."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            s = _in_progress(n=2)
            s.questions[-1].answer = _SECRET_DSN
            SAVE.save_session(src, brainstorm=s.to_dict())

            # even WITH a real human approval, the WriteGate hard-blocks the secret
            res = mcp_commit(M.session_push, path=a, tag="reqleak", requirements_only=True)
            self.assertEqual(res["status"], "blocked", res)
            self.assertFalse(res["committed"])
            self.assertIn(SB.SESSION_REQUIREMENTS_KEY, res["blocked_keys"])
            self.assertFalse(os.path.exists(SB.bundle_path(a, "reqleak")))
            self.assertNotIn(_SECRET_DSN, json.dumps(res))
            self.assertNotIn("tok3n_pw", json.dumps(res))


# ===================================================================== transcript bounds
class TestTranscriptBounds(_Base):
    def test_over_cap_keeps_head_and_tail_and_declares_the_drop(self):
        n = SB.TRANSCRIPT_MAX_TURNS + 7
        state = {BRAINSTORM_PROGRESS_KEY: _in_progress(n=n).to_dict()}
        tr = SB.build_transcript(state)
        self.assertEqual(tr["total"], n)
        self.assertTrue(tr["truncated"])
        self.assertEqual(tr["dropped"], n - SB.TRANSCRIPT_MAX_TURNS)
        self.assertEqual(len(tr["turns"]), SB.TRANSCRIPT_MAX_TURNS)
        # head keeps the opening turns, tail keeps the latest — the middle is dropped
        kept = [t["index"] for t in tr["turns"]]
        self.assertEqual(kept[0], 0)
        self.assertEqual(kept[-1], n - 1)
        self.assertNotIn(SB.TRANSCRIPT_HEAD_TURNS, kept)     # a middle turn is gone

    def test_under_cap_is_untruncated(self):
        state = {BRAINSTORM_PROGRESS_KEY: _in_progress(n=3).to_dict()}
        tr = SB.build_transcript(state)
        self.assertFalse(tr["truncated"])
        self.assertEqual(tr["dropped"], 0)
        self.assertEqual(len(tr["turns"]), 3)

    def test_per_turn_scan_blocks_a_planted_secret_by_turn_index(self):
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            s = _in_progress(n=3)
            s.questions[-1].answer = _SECRET_DSN          # a KEPT (tail) turn, index 2
            SAVE.save_session(src, brainstorm=s.to_dict())

            res = mcp_commit(M.session_push, path=a, tag="tleak", allow_in_progress=True)
            self.assertEqual(res["status"], "blocked", res)
            self.assertIn(2, res["blocked_turns"])          # named by TURN INDEX
            self.assertNotIn(_SECRET_DSN, json.dumps(res))
            self.assertNotIn("tok3n_pw", json.dumps(res))


# ===================================================================== version honesty
class TestVersionHonesty(_Base):
    def _v1_bundle(self, src):
        """Forge a genuine v1-shaped bundle (v1 hash over the v1 fields only)."""
        v2 = SB.build_session_bundle(src, author="jas", now="2026-07-13T00:00:00+00:00")
        v1 = {"schema_version": 1, "kind": v2["kind"],
              "repo_fingerprint": v2["repo_fingerprint"], "run_id": v2["run_id"],
              "state": v2["state"]}
        v1["content_hash"] = SB._hash_core(v1)
        v1["provenance"] = v2["provenance"]
        v1["resume"] = v2["resume"]
        return v1

    def test_v1_bundle_still_pulls_unchanged(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=2).to_dict())
            v1 = self._v1_bundle(src)
            # verify + parse accept it (v1 hash matches; no version refusal)
            SB.verify_bundle(v1)
            self.assertEqual(SB.parse_bundle(SB.serialize_bundle(v1))["schema_version"], 1)
            # and it hydrates through the untouched receiver path
            _pin("sessB")
            _repo(b)
            r = SB.hydrate_bundle(Surface.load(b), v1, assume_yes=True)
            self.assertTrue(r.committed, r)
            _pin("sessB")
            self.assertEqual(len(restore_brainstorm_progress(Surface.load(b).state).answered_questions), 2)

    def test_v2_bundle_to_a_degraded_reader_refuses_loudly(self):
        """A v2 bundle presented to a reader that only knows v1 REFUSES loudly — never a silent
        strip of the newer sections (D6-class)."""
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            SAVE.save_session(src, brainstorm=_in_progress(n=2).to_dict())
            v2 = SB.build_session_bundle(src, author="jas", now="2026-07-13T00:00:00+00:00")
            self.assertEqual(v2["schema_version"], 2)
            with self.assertRaises(SB.SessionBundleError) as ctx:
                SB.verify_bundle(v2, max_version=1)          # a v1-only reader
            self.assertRegex(str(ctx.exception).lower(), r"version|schema|upgrade")
            # the CURRENT reader (max_version defaults to 2) does NOT refuse
            SB.verify_bundle(v2)


# ===================================================================== secret-safety sweep
class TestSecretSafety(_Base):
    def test_no_dsn_value_in_any_result_or_refusal(self):
        with tempfile.TemporaryDirectory() as a:
            _pin("sessA")
            src = _repo(a)
            s = _in_progress(n=2)
            s.questions[-1].answer = _SECRET_DSN
            SAVE.save_session(src, brainstorm=s.to_dict())
            # (is there a HUMAN approval?, the push flags) — the sweep covers the fully-approved
            # paths (which reach the WriteGate and are hard-blocked) AND the propose-only path.
            for approved, kwargs in ((True, {"allow_in_progress": True}),
                                     (False, {"allow_in_progress": True}),
                                     (True, {"requirements_only": True}),
                                     (True, {})):
                res = (mcp_commit(M.session_push, path=a, tag="sweep", **kwargs) if approved
                       else M.session_push(path=a, tag="sweep", **kwargs))
                blob = json.dumps(res)
                self.assertNotIn(_SECRET_DSN, blob)
                self.assertNotIn("tok3n_pw", blob)


if __name__ == "__main__":
    unittest.main()
