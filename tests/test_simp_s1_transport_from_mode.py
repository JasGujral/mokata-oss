"""SIMP.S1 — TRANSPORT-FROM-MODE.

The session transport is DERIVED from the repo's MODE, never picked from a channel zoo:
  * a LOCAL (solo) repo → the `local` SQLite+files transport;
  * a TEAM-CONNECTED repo (the CM.S1 one-DSN resolver reports a wired team DSN via
    `team.connect_status`, the SAME signal shared memory/journal key on) → `postgres` (the ONE
    configured Postgres DSN) — automatically, with NO transport argument.

Escape hatch: `--file` (CLI) / an explicit `transport="local"` (MCP) forces the local file
transport regardless of mode. Explicitly-passed kinds stay BYTE-IDENTICAL to before (no
warnings — SIMP.S2 owns deprecation). Degrade-clean is preserved: team mode with an
unreachable/missing DSN REFUSES with the existing `SessionTransportUnavailable` message (clear,
rc 1, never a silent downgrade to a local file — a teammate must never write local thinking it
shared). Secret-safety: a derivation/refusal never contains a DSN VALUE (env-var NAME only).

Gates are driven non-interactively (`--yes` / the mcp_commit round-trip), never real prompts.
Clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit

from mokata import session_transport as ST
from mokata import team
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint

# A recognizable DSN VALUE assembled from fragments so no literal secret lives in this file.
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"
_DSN_ENV = "MOKATA_PG_DSN"


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _seed_run(surface, run_id="auth-refactor", passed=("brainstorm", "analysis")):
    cp = PipelineCheckpoint(surface.state, run_id)
    for ph in passed:
        cp.mark_passed(ph)
    return run_id


def _connect_team(d, surface):
    """Wire the repo to the team's managed Postgres (persists the env-var NAME pointer). The DSN
    VALUE is present only during the connect; callers pop it afterwards to model the real 'team
    member without the DSN exported yet' case."""
    os.environ[_DSN_ENV] = _SECRET_DSN
    try:
        res = team.team_connect(d, surface, _DSN_ENV, assume_yes=True, out=lambda _m: None)
    finally:
        os.environ.pop(_DSN_ENV, None)
    assert res.connected, "team_connect should wire the pointer"
    assert team.connect_status(Surface.load(d)) == _DSN_ENV


def run_cli(argv):
    """Drive the real CLI, capturing rc + stdout + stderr (EOF stdin — never a real prompt)."""
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(out), redirect_stderr(err):
            from mokata.cli import main
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, out.getvalue(), err.getvalue()


def run_cli_exit(argv):
    """Like `run_cli`, but treats a `SystemExit` (how the CLI's `_load_surface` exits rc 1) as the
    return code — the real interpreter entrypoint does the same, so this captures true CLI behavior
    on a repo whose surface can't load."""
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            from mokata.cli import main
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.stdin = old
    return code, out.getvalue(), err.getvalue()


def _local_bundle_path(root, tag):
    return os.path.join(root, ".mokata", ST.LOCAL_DIRNAME, f"{tag}.json")


def _no_local_write(root, tag):
    return not os.path.exists(_local_bundle_path(root, tag))


# ---------------------------------------------------------------- an in-memory psycopg-like client
class _FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakePg:
    """A tiny psycopg-connection stand-in so the team-store leg round-trips without a live DB."""

    def __init__(self):
        self.store = {}
        self.proj = {}

    def execute(self, sql, params=None):
        head = " ".join(sql.split()).upper()
        if head.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE UNIQUE INDEX")):
            return _FakeCursor()
        if head.startswith("INSERT"):
            project, tag, blob = params
            self.store[tag] = blob
            self.proj[tag] = project
            return _FakeCursor(rowcount=1)
        if head.startswith("SELECT BLOB"):
            tag = params[0]
            scoped = len(params) > 1
            if tag in self.store and (not scoped or self.proj.get(tag) == params[1]):
                return _FakeCursor([(self.store[tag],)])
            return _FakeCursor([])
        if head.startswith("SELECT TAG"):
            tags = ([t for t in self.store if self.proj.get(t) == params[0]]
                    if params else list(self.store))
            return _FakeCursor([(t,) for t in sorted(tags)])
        if head.startswith("SELECT DISTINCT PROJECT"):
            return _FakeCursor([(p,) for p in sorted(set(self.proj.values()))])
        if head.startswith("DELETE"):
            tag = params[0]
            existed = tag in self.store
            self.store.pop(tag, None)
            self.proj.pop(tag, None)
            return _FakeCursor(rowcount=1 if existed else 0)
        return _FakeCursor()

    def close(self):
        pass


def _route_postgres_to(fake):
    """A make_transport wrapper injecting `fake` as the postgres client (no live DB), delegating
    every other kind to the real factory unchanged."""
    orig = ST.make_transport

    def wrapped(kind, root, **kw):
        if (kind or "").lower() == "postgres":
            return orig(kind, root, client=fake)
        return orig(kind, root, **kw)

    return wrapped


def _record_kinds():
    """A make_transport recorder that captures the kind each surface derives, then short-circuits
    (so the surface returns its clean-degrade path without a live DB). Returns (calls, wrapped)."""
    calls = []

    def wrapped(kind, root, **kw):
        calls.append((kind or "").lower())
        raise ST.SessionTransportUnavailable("recorded — no live backend in this test")

    return calls, wrapped


# ================================================================= Deliverable 1 — derive-from-mode
class TestDeriveFromMode(unittest.TestCase):
    def test_simp_s1_derives_local_no_team(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            # a solo repo is NOT team-connected → the mode derives the local transport.
            self.assertIsNone(team.connect_status(Surface.load(d)))
            self.assertEqual(ST.transport_kind_for_mode(d), "local")
            # business-level: a push with NO transport arg lands in the LOCAL file store.
            from mokata.mcp import tools_write as TW
            res = mcp_commit(TW.session_push, path=d, tag="auth")
            self.assertTrue(res["committed"], res)
            self.assertTrue(os.path.exists(_local_bundle_path(d, "auth")))

    def test_simp_s1_derives_postgres_team(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            # a team-connected repo derives the postgres (one shared DSN) transport.
            self.assertEqual(ST.transport_kind_for_mode(d), "postgres")
            # business-level: a push with NO transport arg lands in the TEAM store, not local.
            from mokata.mcp import tools_write as TW
            fake = _FakePg()
            os.environ[_DSN_ENV] = _SECRET_DSN
            try:
                with patch.object(ST, "make_transport", _route_postgres_to(fake)):
                    res = mcp_commit(TW.session_push, path=d, tag="auth")
            finally:
                os.environ.pop(_DSN_ENV, None)
            self.assertTrue(res["committed"], res)
            self.assertIn("auth", fake.store)                 # landed in the team store
            self.assertTrue(_no_local_write(d, "auth"))       # NOT a local file


# ==================================================================== Deliverable 2 — escape hatch
class TestFileEscapeHatch(unittest.TestCase):
    def test_simp_s1_file_escape_hatch(self):
        # a team repo + `--file` forces the LOCAL file transport (explicit override of the mode).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            rc, out, err = run_cli(["session", "push", "auth", "--path", d, "--yes", "--file"])
            self.assertEqual(rc, 0, err)
            self.assertTrue(os.path.exists(_local_bundle_path(d, "auth")), err)
            self.assertIn("local", (out + err))

    def test_simp_s1_file_escape_hatch_mcp_twin(self):
        # the MCP escape hatch: an explicit transport="local" on a team repo forces the file store.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            from mokata.mcp import tools_write as TW
            res = mcp_commit(TW.session_push, path=d, tag="auth", transport="local")
            self.assertTrue(res["committed"], res)
            self.assertTrue(os.path.exists(_local_bundle_path(d, "auth")))


# ================================================= Deliverable 3 — explicit kinds byte-identical
class TestExplicitKindsUnchanged(unittest.TestCase):
    def test_make_transport_signature_and_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            # make_transport's own default stays "local"; explicit kinds build the same classes.
            self.assertIsInstance(ST.make_transport(None, d), ST.LocalTransport)
            self.assertIsInstance(ST.make_transport("local", d), ST.LocalTransport)
            self.assertIsInstance(ST.make_transport("vault", d), ST.VaultTransport)

    def test_simp_s1_explicit_kind_honored_and_vault_now_warns(self):
        # an explicitly-passed kind is honored verbatim on a team repo — the derivation NEVER
        # overrides it. SIMP.S2 now OWNS deprecation: `--to vault` selects the DEPRECATED vault
        # transport, so it keeps working (the shim writes the bundle) AND emits the once-per-repo
        # deprecation warn (this is the S1→S2 handoff the original pin anticipated).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            rc, out, err = run_cli(["session", "push", "auth", "--path", d, "--yes", "--to", "vault"])
            self.assertEqual(rc, 0, err)
            # the S1 invariant: the explicit `vault` kind was honored, NOT derived to postgres —
            # proven structurally by the bundle landing in the vault path (never a local/pg one).
            self.assertTrue(os.path.exists(
                os.path.join(d, ".mokata", "vault", "sessions", "auth.json")), err)
            blob = (out + err).lower()
            self.assertIn("deprecated", blob)           # …and the vault channel now warns (S2)
            self.assertIn("mokata migrate vault", blob)


# ==================================================== Deliverable 4 — team + missing DSN refuses
class TestTeamMissingDsnRefuses(unittest.TestCase):
    def test_simp_s1_team_dsn_missing_refuses(self):
        # team-connected but the DSN is NOT exported → refuse (rc 1), write NOTHING locally.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            os.environ.pop(_DSN_ENV, None)
            os.environ.pop("MOKATA_SESSION_PG_DSN", None)
            rc, out, err = run_cli(["session", "push", "auth", "--path", d, "--yes"])
            self.assertEqual(rc, 1, (out, err))
            self.assertTrue(_no_local_write(d, "auth"), "must NOT silently downgrade to local")
            # secret-safety: the refusal names the env VAR only, never a DSN VALUE.
            self.assertIn(_DSN_ENV, err)
            self.assertNotIn(_SECRET_DSN, err)
            self.assertNotIn("tok3n_pw", err)

    def test_simp_s1_team_dsn_missing_refuses_mcp_twin(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            os.environ.pop(_DSN_ENV, None)
            os.environ.pop("MOKATA_SESSION_PG_DSN", None)
            from mokata.mcp import tools_write as TW
            res = mcp_commit(TW.session_push, path=d, tag="auth")
            self.assertEqual(res.get("status"), "unavailable", res)
            self.assertFalse(res.get("committed"))
            self.assertTrue(_no_local_write(d, "auth"))
            self.assertNotIn(_SECRET_DSN, str(res))


# ================================================================= CLI ↔ MCP derivation parity
class TestCliMcpParity(unittest.TestCase):
    def _cli_derived_kind(self, d):
        calls, wrapped = _record_kinds()
        with patch.object(ST, "make_transport", wrapped):
            run_cli(["session", "push", "auth", "--path", d, "--yes"])
        return calls

    def _mcp_derived_kind(self, d):
        calls, wrapped = _record_kinds()
        from mokata.mcp import tools_write as TW
        with patch.object(ST, "make_transport", wrapped):
            mcp_commit(TW.session_push, path=d, tag="auth")
        return calls

    def test_simp_s1_cli_mcp_same_derivation(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            self.assertEqual(self._cli_derived_kind(d), ["local"])
            self.assertEqual(self._mcp_derived_kind(d), ["local"])
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _connect_team(d, surface)
            self.assertEqual(self._cli_derived_kind(d), ["postgres"])
            self.assertEqual(self._mcp_derived_kind(d), ["postgres"])


# ============================================= Deliverable 4 (revision) — corrupt manifest refuses
def _corrupt_manifest(root):
    """Truncate the repo's committed manifest to invalid JSON — the repo stays INITIALIZED (the
    file is present) but its mode can no longer be read. Returns the manifest path."""
    from mokata import MOKATA_DIR, MANIFEST_FILENAME
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"stack": "python", "profil')   # truncated mid-token → torn JSON
    return path


class TestCorruptManifestRefuses(unittest.TestCase):
    """A PRESENT-but-unreadable manifest means the mode is UNKNOWN. Deriving 'local' here would
    reintroduce the silent downgrade deliverable 4 forbids — through the config-read door: a
    team-connected repo whose manifest is torn would write a PRIVATE local file while the human
    believes it reached the shared store. The derivation must FAIL CLOSED (a loud refusal), and the
    explicit escape hatches (`--file` / transport="local") must still recover such a repo."""

    def test_simp_s1_corrupt_manifest_refuses(self):
        # CLI: initialized repo + torn manifest → no-arg push REFUSES (rc 1), nothing written local.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            self.assertTrue(Surface.is_initialized(d))     # still initialized (manifest present)
            _corrupt_manifest(d)
            # the derivation itself fails closed, not a guess.
            with self.assertRaises(ST.SessionTransportUnavailable) as ctx:
                ST.transport_kind_for_mode(d)
            msg = str(ctx.exception)
            self.assertIn("manifest", msg)                 # names the manifest
            self.assertIn("--file", msg)                   # names the escape hatch
            self.assertNotIn(_SECRET_DSN, msg)             # no secret VALUE

            rc, out, err = run_cli(["session", "push", "auth", "--path", d, "--yes"])
            self.assertEqual(rc, 1, (out, err))
            self.assertTrue(_no_local_write(d, "auth"),
                            "a torn-manifest repo must NOT silently downgrade to a local file")
            self.assertIn("manifest", err)
            self.assertIn("--file", err)
            self.assertNotIn(_SECRET_DSN, out + err)
            self.assertNotIn("tok3n_pw", out + err)

    def test_simp_s1_corrupt_manifest_refuses_mcp_twin(self):
        # MCP: torn manifest + no-arg push → clean status 'unavailable', nothing written local.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _corrupt_manifest(d)
            from mokata.mcp import tools_write as TW
            res = mcp_commit(TW.session_push, path=d, tag="auth")
            self.assertEqual(res.get("status"), "unavailable", res)
            self.assertFalse(res.get("committed"))
            self.assertTrue(_no_local_write(d, "auth"))
            self.assertIn("manifest", res.get("message", ""))
            self.assertIn("--file", res.get("message", ""))
            self.assertNotIn(_SECRET_DSN, str(res))

    def test_simp_s1_corrupt_manifest_file_hatch_overrides_transport_not_the_load(self):
        # The `--file` escape hatch overrides the TRANSPORT CHOICE (skips the mode derivation), but a
        # torn manifest bricks the whole push regardless: `plan_session_push` re-loads the surface via
        # the SAME `Surface.load` the derivation uses, so the bundle can't be built. The push refuses
        # (rc 1) pointing at the manifest, and — the invariant that actually matters — writes NOTHING
        # locally. (See DEVIATION in the stage report: the coordinator's "--file still WORKS on a torn
        # repo" is not reachable in this stage's scope; the recovery is `fix .mokata/manifest.json`.)
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _corrupt_manifest(d)
            rc, out, err = run_cli_exit(["session", "push", "auth", "--path", d, "--yes", "--file"])
            self.assertEqual(rc, 1, (out, err))
            self.assertTrue(_no_local_write(d, "auth"),
                            "a torn manifest must not silently write a local bundle, --file or not")
            self.assertIn("manifest", err)
            self.assertNotIn(_SECRET_DSN, out + err)

    def test_simp_s1_corrupt_manifest_explicit_local_mcp_needs_readable_manifest(self):
        # MCP twin: explicit transport="local" is byte-identical to before (deliverable 4 pins it), so
        # a torn manifest still fails in the unchanged downstream `Surface.load` — it does NOT silently
        # write a local bundle. Documented here so the limitation is guarded, not assumed to "work".
        from mokata.config import ConfigError
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            _corrupt_manifest(d)
            from mokata.mcp import tools_write as TW
            with self.assertRaises(ConfigError):
                TW.session_push(path=d, tag="auth", transport="local")
            self.assertTrue(_no_local_write(d, "auth"))


if __name__ == "__main__":
    unittest.main()
