"""Stage 55b — portable sessions: remote/shared TRANSPORT + human-friendly rename.

55a built the gated, machine-path-free, secret-scanned, content-hashed session BUNDLE and a
LOCAL file store. 55b adds (1) a pluggable TRANSPORT so a tagged session can be pushed/pulled
over a committed/synced **vault** dir or a shared **Postgres** table (a teammate pulls it), and
(2) a human-friendly session **rename**. The bundle + ALL its gates are REUSED — only the byte
storage changes; the gated logic (secret-scan + human gate + content-hash verify + fingerprint
check) stays in `session_bundle`, the transport only stores/loads bytes.

Inviolables proven here (identical ABSENT/PRESENT — the transport core is dependency-free; the
Postgres leg degrades clean when psycopg/DSN is absent):
  * a session round-trips through the VAULT transport (push→pull into a DIFFERENT repo; resume
    continues from the right gate);
  * the Postgres transport DEGRADES CLEAN with no psycopg/DSN (a clear message, no crash, and it
    does NOT silently downgrade to a less-secure path) and round-trips behind an injected client;
  * secret-scan + human-gate fire on push AND pull on the REMOTE transports too;
  * the content-hash catches a corrupted REMOTE bundle (not served);
  * rename is idempotent, refuses a silent clobber (collision unless forced), preserves
    provenance, and never crosses content-hash;
  * `list` spans local + remote;
  * the Stage 54e parity guard still passes (now with the gated `session_name` write tool).

Gates are driven by EOF-stdin / confirm callables (the 53b lesson), never real prompts.
"""

import io
import json
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_bundle as SB
from mokata import session_transport as ST
from mokata.brainstorm import APPROACH_STATE_KEY
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint

# Credentials assembled from fragments so no literal secret lives in this file (mokata's own
# secret-guard would otherwise block writing/committing it — exactly the thing under test).
_SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE" + "QWER"
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _seed_run(surface, run_id="auth-refactor", passed=("brainstorm",)):
    cp = PipelineCheckpoint(surface.state, run_id)
    for ph in passed:
        cp.mark_passed(ph)
    return run_id


def _yes(_):
    return True


def _no(_):
    return False


# ----------------------------------------------------------- an in-memory psycopg-like client
class _FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakePg:
    """A tiny stand-in for a psycopg connection — enough to prove the Postgres transport's
    owned/namespaced table round-trips without a live DB. Parses by SQL prefix."""

    def __init__(self):
        self.store = {}            # tag -> blob (single-project view the 55b tests read directly)
        self.proj = {}             # tag -> project (Stage 71a scoping)
        self.closed = False

    def execute(self, sql, params=None):
        head = " ".join(sql.split()).upper()
        if (head.startswith("CREATE TABLE") or head.startswith("ALTER TABLE")
                or head.startswith("CREATE UNIQUE INDEX")):
            return _FakeCursor()                       # DDL / migration — no-op in the fake
        if head.startswith("INSERT"):
            project, tag, blob = params               # Stage 71a — (project, tag, blob)
            self.store[tag] = blob
            self.proj[tag] = project
            return _FakeCursor(rowcount=1)
        if head.startswith("SELECT BLOB"):
            tag = params[0]                            # (tag,) or (tag, project)
            scoped = len(params) > 1
            if tag in self.store and (not scoped or self.proj.get(tag) == params[1]):
                return _FakeCursor([(self.store[tag],)])
            return _FakeCursor([])
        if head.startswith("SELECT TAG"):
            if params:                                 # WHERE project=%s
                tags = [t for t in self.store if self.proj.get(t) == params[0]]
            else:
                tags = list(self.store)
            return _FakeCursor([(t,) for t in sorted(tags)])
        if head.startswith("SELECT DISTINCT PROJECT"):
            return _FakeCursor([(p,) for p in sorted(set(self.proj.values()))])
        if head.startswith("DELETE"):
            tag = params[0]
            scoped = len(params) > 1
            existed = tag in self.store and (not scoped or self.proj.get(tag) == params[1])
            if existed:
                self.store.pop(tag, None)
                self.proj.pop(tag, None)
            return _FakeCursor(rowcount=1 if existed else 0)
        return _FakeCursor()

    def close(self):
        self.closed = True


# ================================================================ vault transport round-trip
class TestVaultRoundTrip(unittest.TestCase):
    def test_push_to_vault_then_pull_into_a_different_repo_resumes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            run = _seed_run(src, passed=("brainstorm", "analysis"))
            t = ST.make_transport("vault", a)
            plan = SB.plan_session_push(a, src, "auth-refactor", transport=t,
                                        now="2026-06-30T00:00:00+00:00")
            self.assertEqual(plan.status, "new")
            res = SB.commit_session_push_gated(plan, confirm=_yes)
            self.assertTrue(res.committed)
            # the bundle lives in the committed/synced vault dir, not temp_local/
            self.assertTrue(os.path.exists(
                os.path.join(a, ".mokata", "vault", "sessions", "auth-refactor.json")))

            dst = _repo(b)
            pull = SB.plan_session_pull(a, "auth-refactor", b,
                                        transport=ST.make_transport("vault", a))
            self.assertEqual(pull.status, "ok")
            self.assertTrue(SB.hydrate_bundle(dst, pull.bundle, confirm=_yes).committed)
            cp = PipelineCheckpoint(dst.state, run)
            self.assertEqual(cp.passed, ["brainstorm", "analysis"])
            self.assertEqual(cp.resume_phase(), "strawman")


# ================================================================ postgres degrade-clean
class TestPostgresDegradeClean(unittest.TestCase):
    def test_no_dsn_raises_a_clear_unavailable_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            saved = {k: os.environ.pop(k, None)
                     for k in ("MOKATA_SESSION_PG_DSN", "MOKATA_PG_DSN")}
            try:
                with self.assertRaises(ST.SessionTransportUnavailable) as ctx:
                    ST.make_transport("postgres", d)
                self.assertIn("DSN", str(ctx.exception))
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

    def test_mcp_push_postgres_without_dsn_degrades_and_does_not_fall_back_to_local(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            saved = {k: os.environ.pop(k, None)
                     for k in ("MOKATA_SESSION_PG_DSN", "MOKATA_PG_DSN")}
            try:
                res = M.session_push(path=d, tag="auth", transport="postgres", approve=True)
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v
            self.assertEqual(res["status"], "unavailable")
            self.assertFalse(res.get("committed"))
            # SECURITY: it must NOT have silently downgraded to the local store
            self.assertFalse(os.path.exists(SB.bundle_path(d, "auth")),
                             "a degraded postgres push silently fell back to local")


# ================================================================ postgres round-trip (fake)
class TestPostgresRoundTripFake(unittest.TestCase):
    def test_push_pull_through_an_injected_client_resumes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            run = _seed_run(src, passed=("brainstorm", "analysis"))
            pg = _FakePg()
            t = ST.make_transport("postgres", a, client=pg)
            self.assertEqual(t.name, "postgres")
            plan = SB.plan_session_push(a, src, "auth-refactor", transport=t,
                                        now="2026-06-30T00:00:00+00:00")
            self.assertTrue(SB.commit_session_push_gated(plan, confirm=_yes).committed)
            self.assertIn("auth-refactor", pg.store)        # stored in the owned table

            dst = _repo(b)
            pull = SB.plan_session_pull(a, "auth-refactor", b,
                                        transport=ST.make_transport("postgres", a, client=pg))
            self.assertEqual(pull.status, "ok")
            self.assertTrue(SB.hydrate_bundle(dst, pull.bundle, confirm=_yes).committed)
            self.assertEqual(PipelineCheckpoint(dst.state, run).resume_phase(), "strawman")


# ================================================================ secret + gate on remotes
class TestRemoteSecretAndGate(unittest.TestCase):
    def _transports(self, root, pg):
        return [("vault", ST.make_transport("vault", root)),
                ("postgres", ST.make_transport("postgres", root, client=pg))]

    def test_secret_is_hard_blocked_on_push_over_every_remote(self):
        for kind in ("vault", "postgres"):
            with tempfile.TemporaryDirectory() as d:
                surface = _repo(d)
                _seed_run(surface)
                surface.state.write(APPROACH_STATE_KEY,
                                    {"approach": {"name": "x", "notes": _SECRET_DSN}})
                pg = _FakePg()
                t = (ST.make_transport("vault", d) if kind == "vault"
                     else ST.make_transport("postgres", d, client=pg))
                plan = SB.plan_session_push(d, surface, "leaky", transport=t,
                                            now="2026-06-30T00:00:00+00:00")
                res = SB.commit_session_push_gated(plan, confirm=_yes)
                self.assertFalse(res.committed, f"{kind}: secret push was committed")
                self.assertTrue(res.findings, f"{kind}: secret not hard-blocked on push")
                self.assertEqual(t.read_bundle("leaky"), None,
                                 f"{kind}: a secret-bearing bundle was stored")

    def test_decline_writes_nothing_on_push_over_every_remote(self):
        for kind in ("vault", "postgres"):
            with tempfile.TemporaryDirectory() as d:
                surface = _repo(d)
                _seed_run(surface)
                pg = _FakePg()
                t = (ST.make_transport("vault", d) if kind == "vault"
                     else ST.make_transport("postgres", d, client=pg))
                plan = SB.plan_session_push(d, surface, "auth", transport=t,
                                            now="2026-06-30T00:00:00+00:00")
                res = SB.commit_session_push_gated(plan, confirm=_no)
                self.assertFalse(res.committed)
                self.assertEqual(t.read_bundle("auth"), None,
                                 f"{kind}: a declined push still stored the bundle")

    def test_secret_in_remote_bundle_is_blocked_on_pull(self):
        for kind in ("vault", "postgres"):
            with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
                src = _repo(a)
                _seed_run(src)
                pg = _FakePg()
                t = (ST.make_transport("vault", a) if kind == "vault"
                     else ST.make_transport("postgres", a, client=pg))
                # craft a nasty (re-sealed, not corrupt) bundle straight into the remote store,
                # bypassing push's gate — the pull side is untrusted and must re-scan it.
                bundle = SB.build_session_bundle(src, now="2026-06-30T00:00:00+00:00")
                bundle["state"][APPROACH_STATE_KEY] = {"secret": _SECRET}
                bundle = SB.reseal_bundle(bundle)
                t.write_bundle("nasty", SB.serialize_bundle(bundle))

                dst = _repo(b)
                t2 = (ST.make_transport("vault", a) if kind == "vault"
                      else ST.make_transport("postgres", a, client=pg))
                pull = SB.plan_session_pull(a, "nasty", b, transport=t2)
                res = SB.hydrate_bundle(dst, pull.bundle, confirm=_yes)
                self.assertFalse(res.committed, f"{kind}: secret pull was hydrated")
                self.assertTrue(res.findings, f"{kind}: secret not hard-blocked on pull")
                self.assertIsNone(dst.state.read(APPROACH_STATE_KEY))


# ================================================================ corruption caught on remote
class TestRemoteCorruption(unittest.TestCase):
    def test_corrupted_remote_bundle_is_caught_by_the_hash(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            _seed_run(src)
            pg = _FakePg()
            t = ST.make_transport("postgres", a, client=pg)
            plan = SB.plan_session_push(a, src, "auth", transport=t,
                                        now="2026-06-30T00:00:00+00:00")
            SB.commit_session_push(plan)
            # tamper with the stored blob's state but keep the (now-wrong) stored hash
            raw = json.loads(pg.store["auth"])
            raw["state"][CHECKPOINT_PREFIX + "auth-refactor"]["passed"].append("ship")
            pg.store["auth"] = json.dumps(raw)

            _repo(b)
            with self.assertRaises(SB.SessionBundleError):
                SB.plan_session_pull(a, "auth", b,
                                     transport=ST.make_transport("postgres", a, client=pg))


# ================================================================ rename
class TestRename(unittest.TestCase):
    def _push(self, root, surface, tag, transport, **kw):
        plan = SB.plan_session_push(root, surface, tag, transport=transport,
                                    now="2026-06-30T00:00:00+00:00", **kw)
        SB.commit_session_push(plan)

    def test_rename_to_same_name_is_an_idempotent_noop(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            t = ST.make_transport("local", d)
            self._push(d, surface, "auth", t)
            plan = SB.plan_session_rename(d, "auth", "auth", transport=t)
            self.assertEqual(plan.status, "noop")
            res = SB.commit_session_rename_gated(plan, confirm=_yes)
            self.assertFalse(res.committed)            # nothing durable to write
            self.assertIsNotNone(t.read_bundle("auth"))

    def test_rename_refuses_a_silent_clobber_unless_forced(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface, passed=("brainstorm",))
            t = ST.make_transport("local", d)
            self._push(d, surface, "auth", t)
            # a SECOND, different session under another tag
            PipelineCheckpoint(surface.state, "auth-refactor").mark_passed("analysis")
            self._push(d, surface, "other", t, force=True)

            plan = SB.plan_session_rename(d, "other", "auth", transport=t)
            self.assertEqual(plan.status, "collision")
            res = SB.commit_session_rename_gated(plan, confirm=_yes)
            self.assertFalse(res.committed)
            self.assertIsNotNone(t.read_bundle("other"), "the source was clobbered on collision")

            forced = SB.plan_session_rename(d, "other", "auth", transport=t, force=True)
            self.assertEqual(forced.status, "ok")
            self.assertTrue(SB.commit_session_rename_gated(forced, confirm=_yes).committed)
            self.assertIsNone(t.read_bundle("other"))   # old name gone after the move

    def test_rename_preserves_provenance_and_does_not_cross_the_content_hash(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            t = ST.make_transport("local", d)
            self._push(d, surface, "auth", t, author="alice")
            before = json.loads(t.read_bundle("auth"))

            plan = SB.plan_session_rename(d, "auth", "auth-refactor", transport=t)
            self.assertEqual(plan.status, "ok")
            self.assertTrue(SB.commit_session_rename_gated(plan, confirm=_yes).committed)

            self.assertIsNone(t.read_bundle("auth"))
            after = json.loads(t.read_bundle("auth-refactor"))
            self.assertEqual(after["content_hash"], before["content_hash"])  # hash intact
            self.assertEqual(after["provenance"]["author"], "alice")          # provenance kept
            self.assertIn("auth", after["provenance"].get("prior_names", []))  # rename trail
            self.assertEqual(after["resume"], before["resume"])               # resume intact

    def test_rename_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            t = ST.make_transport("local", d)
            self._push(d, surface, "auth", t)
            plan = SB.plan_session_rename(d, "auth", "renamed", transport=t)
            res = SB.commit_session_rename_gated(plan, confirm=_no)
            self.assertFalse(res.committed)
            self.assertIsNotNone(t.read_bundle("auth"))
            self.assertIsNone(t.read_bundle("renamed"))

    def test_rename_missing_source_is_a_clean_status(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            t = ST.make_transport("local", d)
            plan = SB.plan_session_rename(d, "ghost", "x", transport=t)
            self.assertEqual(plan.status, "missing")


# ================================================================ list spans local + remote
class TestListSpans(unittest.TestCase):
    def test_list_across_local_and_vault(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface, passed=("brainstorm", "analysis"))
            local = ST.make_transport("local", d)
            vault = ST.make_transport("vault", d)
            SB.commit_session_push(SB.plan_session_push(
                d, surface, "local-tag", transport=local, now="2026-06-30T00:00:00+00:00"))
            SB.commit_session_push(SB.plan_session_push(
                d, surface, "vault-tag", transport=vault, now="2026-06-30T00:00:00+00:00"))

            infos = SB.list_session_bundles_across(d, [local, vault])
            by_tag = {i.tag: i for i in infos}
            self.assertEqual(by_tag["local-tag"].transport, "local")
            self.assertEqual(by_tag["vault-tag"].transport, "vault")
            self.assertEqual(by_tag["vault-tag"].resume_phase, "strawman")

    def test_list_across_skips_an_unavailable_remote_clean(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            local = ST.make_transport("local", d)
            SB.commit_session_push(SB.plan_session_push(
                d, surface, "auth", transport=local, now="2026-06-30T00:00:00+00:00"))

            class _Broken:
                name = "postgres"

                def list_tags(self):
                    raise ST.SessionTransportUnavailable("db down")

            infos = SB.list_session_bundles_across(d, [local, _Broken()])
            self.assertEqual([i.tag for i in infos], ["auth"])   # broken remote skipped clean


# ================================================================ CLI + MCP surfaces (55b)
class TestCliSurfaces(unittest.TestCase):
    def _run(self, argv):
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = so, se
        return rc, out.getvalue(), err.getvalue()

    def test_session_name_cli_renames_gated(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            t = ST.make_transport("local", d)
            SB.commit_session_push(SB.plan_session_push(
                d, surface, "auth", transport=t, now="2026-06-30T00:00:00+00:00"))
            rc, out, err = self._run(["session", "name", "auth", "auth-refactor",
                                      "--yes", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIsNone(t.read_bundle("auth"))
            self.assertIsNotNone(t.read_bundle("auth-refactor"))

    def test_session_push_to_vault_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            rc, out, err = self._run(["session", "push", "auth", "--to", "vault",
                                      "--yes", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertTrue(os.path.exists(
                os.path.join(d, ".mokata", "vault", "sessions", "auth.json")))

    def test_session_push_postgres_no_dsn_degrades_clean_cli(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            saved = {k: os.environ.pop(k, None)
                     for k in ("MOKATA_SESSION_PG_DSN", "MOKATA_PG_DSN")}
            try:
                rc, out, err = self._run(["session", "push", "auth", "--to", "postgres",
                                          "--yes", "--path", d])
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v
            self.assertEqual(rc, 1)                       # clean failure, not a crash
            self.assertIn("DSN", out + err)
            self.assertFalse(os.path.exists(SB.bundle_path(d, "auth")))


class TestMcpSurfaces(unittest.TestCase):
    def test_session_name_is_a_gated_write_tool(self):
        from mokata import mcp_server as M
        self.assertIn("session_name", M.write_tool_names())
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            M.session_push(path=d, tag="auth", approve=True)
            # propose-only without approval
            res = M.session_name(path=d, tag="auth", new="auth-refactor")
            self.assertEqual(res["status"], "proposed")
            self.assertTrue(os.path.exists(SB.bundle_path(d, "auth")))
            # approve → renamed
            res2 = M.session_name(path=d, tag="auth", new="auth-refactor", approve=True)
            self.assertTrue(res2["committed"])
            self.assertFalse(os.path.exists(SB.bundle_path(d, "auth")))
            self.assertTrue(os.path.exists(SB.bundle_path(d, "auth-refactor")))

    def test_session_push_routes_transport_arg(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            res = M.session_push(path=d, tag="auth", transport="vault", approve=True)
            self.assertTrue(res["committed"])
            self.assertTrue(os.path.exists(
                os.path.join(d, ".mokata", "vault", "sessions", "auth.json")))


class TestSlashTemplate(unittest.TestCase):
    def test_session_template_documents_name_and_transports(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "templates", "commands", "session.md"),
                  encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("name <tag>", md)
        self.assertIn("--to", md)
        self.assertIn("vault", md)
        self.assertIn("postgres", md)


# ================================================================ parity stays green
class TestParityStaysGreen(unittest.TestCase):
    def test_session_name_in_matrix_and_parity_passes(self):
        from mokata import parity
        s = parity.SURFACE_MATRIX["session"]
        self.assertIn("session_name", s.mcp_write)
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())


if __name__ == "__main__":
    unittest.main()
