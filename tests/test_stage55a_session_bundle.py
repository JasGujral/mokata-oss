"""Stage 55a — portable / shareable tagged sessions (bundle + LOCAL file share).

The CURRENT session (resumable run checkpoint(s) + approved approach + emitted spec +
in-progress brainstorm) is packaged into a versioned, MACHINE-PATH-FREE bundle with
provenance + a content-hash + a repo fingerprint, shared as a LOCAL file under a safe tag,
then pulled + re-hydrated into another repo so `mokata resume` continues the work.

Inviolables proven here (both jsonschema states — the bundle is dependency-free, so behaviour
is identical ABSENT/PRESENT):
  * round-trips push→pull into a DIFFERENT repo dir; resume continues from the right gate;
  * the bundle is machine-path-FREE (no absolute paths travel);
  * a secret in session content is HARD-BLOCKED on push AND on pull (untrusted on pull);
  * a corrupted bundle is caught by the content-hash (not served);
  * a cross-codebase fingerprint mismatch is SURFACED (never silently applied);
  * `list` shows tagged bundles with provenance + resume point;
  * push/pull are human-gated (decline → nothing written / nothing hydrated);
  * a not-yet-approved brainstorm stays NOT approved after pull (the HARD-GATE survives);
  * the Stage 54e parity guard still passes.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata import session_bundle as SB
from mokata.brainstorm import (
    APPROACH_STATE_KEY,
    BRAINSTORM_PROGRESS_KEY,
    BrainstormSession,
    save_brainstorm_progress,
    restore_brainstorm_progress,
)
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.engine.spec_gate import SPEC_STATE_KEY

# A credential assembled from fragments so no literal secret lives in this file (mokata's own
# secret-guard would otherwise block writing/committing it — exactly the thing under test).
_SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE" + "QWER"
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _seed_run(surface, run_id="auth-refactor", passed=("brainstorm",)):
    """Give a repo a resumable run checkpoint at the given passed gates."""
    cp = PipelineCheckpoint(surface.state, run_id)
    for ph in passed:
        cp.mark_passed(ph)
    return run_id


def _seed_unapproved_brainstorm(surface):
    """An IN-PROGRESS (NOT approved) brainstorm — the HARD-GATE invariant under test."""
    s = BrainstormSession("redesign the checkout so retries are exactly-once")
    save_brainstorm_progress(s, surface.state)
    assert s.approved is False
    return s


def _yes(_):
    return True


def _no(_):
    return False


# ============================================================ build: deterministic + path-free
class TestBuild(unittest.TestCase):
    def test_collects_checkpoint_spec_brainstorm_and_approach(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            run = _seed_run(surface, passed=("brainstorm", "analysis"))
            surface.state.write(SPEC_STATE_KEY, {"acceptance_criteria": ["AC1"]})
            _seed_unapproved_brainstorm(surface)
            bundle = SB.build_session_bundle(surface, now="2026-06-30T00:00:00+00:00")
            state = bundle["state"]
            self.assertIn(CHECKPOINT_PREFIX + run, state)
            self.assertIn(SPEC_STATE_KEY, state)
            self.assertIn(BRAINSTORM_PROGRESS_KEY, state)
            self.assertEqual(bundle["kind"], SB.BUNDLE_KIND)
            self.assertEqual(bundle["schema_version"], SB.BUNDLE_SCHEMA_VERSION)
            self.assertTrue(bundle["repo_fingerprint"].startswith("sha256:"))
            self.assertTrue(bundle["content_hash"].startswith("sha256:"))
            self.assertEqual(bundle["provenance"]["created"], "2026-06-30T00:00:00+00:00")

    def test_is_deterministic_for_the_same_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            a = SB.build_session_bundle(surface, author="alice", now="2026-06-30T00:00:00+00:00")
            b = SB.build_session_bundle(surface, author="alice", now="2026-06-30T00:00:00+00:00")
            self.assertEqual(SB.serialize_bundle(a), SB.serialize_bundle(b))

    def test_content_hash_ignores_provenance_so_a_later_repush_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            a = SB.build_session_bundle(surface, author="alice", now="2026-06-30T00:00:00+00:00")
            b = SB.build_session_bundle(surface, author="bob", now="2026-07-01T09:00:00+00:00")
            self.assertEqual(a["content_hash"], b["content_hash"])  # same session, diff metadata

    def test_bundle_is_machine_path_free(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            # plant an absolute path inside session state (the kind `source` fields carry)
            surface.state.write(APPROACH_STATE_KEY,
                                {"approach": {"name": "ledger"},
                                 "source": os.path.join(d, "plan.md"),
                                 "root": d})
            bundle = SB.build_session_bundle(surface, now="2026-06-30T00:00:00+00:00")
            blob = SB.serialize_bundle(bundle)
            self.assertNotIn(d, blob, "the source repo's absolute path leaked into the bundle")
            self.assertEqual(SB.find_abs_paths(bundle["state"]), [],
                             "an absolute path value survived in the bundle state")

    def test_empty_session_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            bundle = SB.build_session_bundle(surface, now="2026-06-30T00:00:00+00:00")
            self.assertTrue(SB.is_empty_bundle(bundle))


# ============================================================ hash verify (corruption caught)
class TestHashVerify(unittest.TestCase):
    def test_corrupted_state_is_caught_by_the_hash(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            tag = "auth"
            plan = SB.plan_session_push(d, surface, tag, now="2026-06-30T00:00:00+00:00")
            path = SB.commit_session_push(plan)
            # tamper with the persisted bundle's state (a corruption / a malicious edit)
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            raw["state"][CHECKPOINT_PREFIX + "auth-refactor"]["passed"].append("ship")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            with self.assertRaises(SB.SessionBundleError):
                SB.load_bundle(path)


# ============================================================ round-trip → resume continues
class TestRoundTrip(unittest.TestCase):
    def test_push_then_pull_into_a_different_repo_resumes_from_the_right_gate(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            run = _seed_run(src, run_id="auth-refactor", passed=("brainstorm", "analysis"))
            tag = "auth-refactor"
            plan = SB.plan_session_push(a, src, tag, now="2026-06-30T00:00:00+00:00")
            self.assertEqual(plan.status, "new")
            SB.commit_session_push(plan)

            dst = _repo(b)  # a DIFFERENT repo dir (same empty layout → fingerprints match)
            pull = SB.plan_session_pull(a, tag, b)
            self.assertEqual(pull.status, "ok")
            res = SB.hydrate_bundle(dst, pull.bundle, confirm=_yes)
            self.assertTrue(res.committed)

            # resume in the target now continues from the phase after the last passed gate
            cp = PipelineCheckpoint(dst.state, run)
            self.assertEqual(cp.passed, ["brainstorm", "analysis"])
            self.assertEqual(cp.resume_phase(), "strawman")  # first phase after 'analysis'

    def test_not_yet_approved_brainstorm_stays_not_approved_after_pull(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            _seed_unapproved_brainstorm(src)
            plan = SB.plan_session_push(a, src, "explore", now="2026-06-30T00:00:00+00:00")
            SB.commit_session_push(plan)

            dst = _repo(b)
            pull = SB.plan_session_pull(a, "explore", b)
            SB.hydrate_bundle(dst, pull.bundle, confirm=_yes)

            restored = restore_brainstorm_progress(dst.state)
            self.assertIsNotNone(restored)
            self.assertFalse(restored.approved, "HARD-GATE breached: approval crossed the bundle")
            # and no approved-approach record was conjured on the far side
            self.assertIsNone(dst.state.read(APPROACH_STATE_KEY))


# ============================================================ secret hard-block (push & pull)
class TestSecretHardBlock(unittest.TestCase):
    def test_secret_in_session_is_blocked_on_push(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            surface.state.write(APPROACH_STATE_KEY,
                                {"approach": {"name": "x", "notes": _SECRET_DSN}})
            plan = SB.plan_session_push(d, surface, "leaky", now="2026-06-30T00:00:00+00:00")
            res = SB.commit_session_push_gated(plan, confirm=_yes)
            self.assertFalse(res.committed)
            self.assertTrue(res.findings)
            self.assertFalse(os.path.exists(plan.path), "a secret-bearing bundle was written")

    def test_secret_in_bundle_is_blocked_on_pull(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            _seed_run(src)
            # craft a bundle that carries a secret WITHOUT going through push's gate (untrusted)
            bundle = SB.build_session_bundle(src, now="2026-06-30T00:00:00+00:00")
            bundle["state"][APPROACH_STATE_KEY] = {"secret": _SECRET}
            bundle = SB.reseal_bundle(bundle)            # re-hash so it isn't "corrupt", just nasty
            os.makedirs(SB.bundle_dir(a), exist_ok=True)
            with open(SB.bundle_path(a, "nasty"), "w", encoding="utf-8") as fh:
                fh.write(SB.serialize_bundle(bundle))

            dst = _repo(b)
            pull = SB.plan_session_pull(a, "nasty", b)
            res = SB.hydrate_bundle(dst, pull.bundle, confirm=_yes)
            self.assertFalse(res.committed)
            self.assertTrue(res.findings, "the secret was not hard-blocked on pull")
            self.assertIsNone(dst.state.read(APPROACH_STATE_KEY), "secret content was hydrated")


# ============================================================ cross-codebase mismatch surfaced
class TestFingerprintMismatch(unittest.TestCase):
    def test_mismatch_is_surfaced_and_not_silently_applied(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            _seed_run(src)
            SB.commit_session_push(
                SB.plan_session_push(a, src, "auth", now="2026-06-30T00:00:00+00:00"))

            dst = _repo(b)
            # make repo B a DIFFERENT codebase (distinctive top-level layout)
            os.makedirs(os.path.join(b, "src"), exist_ok=True)
            with open(os.path.join(b, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("# a totally different project\n")

            pull = SB.plan_session_pull(a, "auth", b)
            self.assertEqual(pull.status, "mismatch")
            self.assertNotEqual(pull.bundle_fingerprint, pull.target_fingerprint)
            self.assertEqual(dst.state.read(CHECKPOINT_PREFIX + "auth-refactor"), None)

            # with explicit confirmation (force) it proceeds through the gate
            pull2 = SB.plan_session_pull(a, "auth", b, force=True)
            self.assertEqual(pull2.status, "ok")
            res = SB.hydrate_bundle(dst, pull2.bundle, confirm=_yes)
            self.assertTrue(res.committed)


# ============================================================ list
class TestList(unittest.TestCase):
    def test_list_shows_tagged_bundles_with_resume_point(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface, run_id="auth-refactor", passed=("brainstorm", "analysis"))
            SB.commit_session_push(
                SB.plan_session_push(d, surface, "auth-refactor", author="alice",
                                     now="2026-06-30T00:00:00+00:00"))
            infos = SB.list_session_bundles(d)
            self.assertEqual(len(infos), 1)
            self.assertEqual(infos[0].tag, "auth-refactor")
            self.assertEqual(infos[0].author, "alice")
            self.assertEqual(infos[0].resume_phase, "strawman")

    def test_empty_store_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertEqual(SB.list_session_bundles(d), [])


# ============================================================ human-gate (decline writes nothing)
class TestHumanGate(unittest.TestCase):
    def test_push_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            plan = SB.plan_session_push(d, surface, "auth", now="2026-06-30T00:00:00+00:00")
            res = SB.commit_session_push_gated(plan, confirm=_no)
            self.assertFalse(res.committed)
            self.assertFalse(os.path.exists(plan.path), "a declined push still wrote the bundle")

    def test_pull_decline_hydrates_nothing(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            _seed_run(src)
            SB.commit_session_push(
                SB.plan_session_push(a, src, "auth", now="2026-06-30T00:00:00+00:00"))
            dst = _repo(b)
            pull = SB.plan_session_pull(a, "auth", b)
            res = SB.hydrate_bundle(dst, pull.bundle, confirm=_no)
            self.assertFalse(res.committed)
            self.assertEqual(dst.state.read(CHECKPOINT_PREFIX + "auth-refactor"), None)


# ============================================================ idempotent push (no silent clobber)
class TestIdempotentPush(unittest.TestCase):
    def test_identical_repush_is_unchanged_and_a_changed_repush_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface, passed=("brainstorm",))
            SB.commit_session_push(
                SB.plan_session_push(d, surface, "auth", now="2026-06-30T00:00:00+00:00"))
            # identical session → unchanged (no-op)
            again = SB.plan_session_push(d, surface, "auth", now="2026-07-01T00:00:00+00:00")
            self.assertEqual(again.status, "unchanged")
            # the session moved on → a changed re-push CONFLICTS unless forced
            PipelineCheckpoint(surface.state, "auth-refactor").mark_passed("analysis")
            changed = SB.plan_session_push(d, surface, "auth", now="2026-07-01T00:00:00+00:00")
            self.assertEqual(changed.status, "conflict")
            self.assertTrue(changed.blocked)
            forced = SB.plan_session_push(d, surface, "auth", force=True,
                                          now="2026-07-01T00:00:00+00:00")
            self.assertEqual(forced.status, "version")


# ============================================================ degrade-clean errors
class TestDegradeClean(unittest.TestCase):
    def test_missing_bundle_is_a_clean_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _repo(a)
            _repo(b)
            pull = SB.plan_session_pull(a, "does-not-exist", b)
            self.assertEqual(pull.status, "missing")

    def test_bad_tag_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            with self.assertRaises(SB.SessionBundleError):
                SB.bundle_path(d, "../escape")


# ============================================================ in-harness surfaces (MCP + slash)
class TestMcpSurfaces(unittest.TestCase):
    def test_session_list_is_a_read_tool(self):
        from mokata import mcp_server as M
        self.assertIn("session_list", M.read_tool_names())
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.session_list(path=d)
            self.assertEqual(res["count"], 0)
            self.assertEqual(res["bundles"], [])

    def test_session_push_is_gated_propose_only_without_approval(self):
        from mokata import mcp_server as M
        self.assertIn("session_push", M.write_tool_names())
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            res = M.session_push(path=d, tag="auth")
            self.assertEqual(res["status"], "proposed")
            self.assertNotEqual(res.get("committed"), True)
            self.assertFalse(os.path.exists(SB.bundle_path(d, "auth")))

    def test_session_push_commits_with_approval(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            res = M.session_push(path=d, tag="auth", approve=True)
            self.assertTrue(res["committed"])
            self.assertTrue(os.path.exists(SB.bundle_path(d, "auth")))

    def test_session_pull_is_gated_and_surfaces_mismatch(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            surface = _repo(a)
            _seed_run(surface)
            M.session_push(path=a, tag="auth", approve=True)
            _repo(b)
            os.makedirs(os.path.join(b, "src"), exist_ok=True)  # different codebase
            res = M.session_pull(path=a, tag="auth", into=b, approve=True)
            self.assertEqual(res["status"], "mismatch")

    def test_session_pull_round_trips_with_approval(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            surface = _repo(a)
            _seed_run(surface, passed=("brainstorm", "analysis"))
            M.session_push(path=a, tag="auth", approve=True)
            dst = _repo(b)
            res = M.session_pull(path=a, tag="auth", into=b, approve=True)
            self.assertTrue(res["committed"])
            cp = PipelineCheckpoint(dst.state, "auth-refactor")
            self.assertEqual(cp.resume_phase(), "strawman")


class TestSlashTemplate(unittest.TestCase):
    def test_session_template_exists_and_is_namespaced(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "templates", "commands", "session.md")
        self.assertTrue(os.path.exists(path), "session.md slash template missing")
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("name: session", md)
        self.assertIn("description: mokata ·", md)


# ============================================================ parity guard still green
class TestParityStaysGreen(unittest.TestCase):
    def test_session_is_in_the_matrix_and_parity_passes(self):
        from mokata import parity
        self.assertIn("session", parity.SURFACE_MATRIX)
        s = parity.SURFACE_MATRIX["session"]
        self.assertIn("session", s.slash)
        self.assertIn("session_list", s.mcp_read)
        self.assertIn("session_push", s.mcp_write)
        self.assertIn("session_pull", s.mcp_write)
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())


if __name__ == "__main__":
    unittest.main()
