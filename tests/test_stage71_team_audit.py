"""Stage 71 — team audit / shared activity log (shared OR local, conflict-free, NO telemetry).

The existing I3 AuditLedger (append-only local JSONL) stays each dev's default. This stage lets a
TEAM optionally publish those SAME entries to the team's OWN managed Postgres (Stage 69's BYO DB)
so everyone sees who-did-what — WITHOUT anything ever being phoned home to mokata/Anthropic.

Inviolables proven here:
  * the audit log DEFAULTS TO LOCAL (a fresh repo shares nothing);
  * sharing is OPT-IN and publishes to the team backend ONLY when connected;
  * shared entries are APPEND-ONLY + PER-ACTOR + NAMESPACED — two concurrent actors' entries BOTH
    survive (no clobber);
  * a shared write is HUMAN-GATED + SECRET-SCANNED (a secret is a hard block; a decline writes
    nothing);
  * the DSN secret is NEVER persisted (env-var NAME only);
  * degrade-clean with no driver/DSN (stays LOCAL, no crash);
  * NO egress to any mokata/Anthropic endpoint — the team's storage ONLY;
  * the team who-did-what/why read SPANS the shared log;
  * the Stage 54e parity guard stays green (audit is a surface; `audit_share` a gated write).

Gates are driven by confirm callables / assume_yes (never real prompts).
"""

import io
import json
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import config_cmd
from mokata import team_audit as TA
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger

# A secret assembled from fragments so no literal secret lives in this file (mokata's own
# secret-guard would otherwise block writing it — exactly the thing under test).
_SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE" + "QWER"
_SECRET_DSN = "postgres://app_user" + ":" + "tok3n_pw" + "@" + "db.internal:5432/app"

_ACTOR_VARS = ("MOKATA_ACTOR", "USER", "USERNAME", "LOGNAME")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _repo_named(parent, name="acme-app", profile="standard"):
    """A repo whose basename (the shared-log NAMESPACE) is fixed, so two clones on different
    machines share a namespace."""
    root = os.path.join(parent, name)
    os.makedirs(root, exist_ok=True)
    return root, _repo(root, profile=profile)


def _seed_ledger(surface, kinds):
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    for k in kinds:
        led.record(k, target=f"{k}-target", decision="approved", reason=f"{k} happened")
    return led


def _enable_sharing(root, dsn_env="MOKATA_PG_DSN", project="acme-app"):
    """Opt in via the EXISTING gated config path (settings.audit.shared + dsn_env). Stage 71a: the
    shared log is now scoped by the ONE project key — pin settings.project.id so two clones of the
    same project agree on the namespace (the value the pre-71a tests asserted as 'acme-app')."""
    config_cmd.config_set(root, "settings.audit.shared", "true", assume_yes=True,
                          out=lambda _m: None)
    config_cmd.config_set(root, "settings.audit.dsn_env", dsn_env, assume_yes=True,
                          out=lambda _m: None)
    config_cmd.config_set(root, "settings.project.id", project, assume_yes=True,
                          out=lambda _m: None)
    return Surface.load(root)


def _yes(_):
    return True


def _no(_):
    return False


class _EnvActor:
    """Context manager pinning the attribution actor (and clearing the DSN vars)."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self._saved = {k: os.environ.pop(k, None)
                       for k in _ACTOR_VARS + TA.PG_DSN_ENVS}
        os.environ["MOKATA_ACTOR"] = self.name
        return self

    def __exit__(self, *exc):
        for k in _ACTOR_VARS + TA.PG_DSN_ENVS:
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v


# ------------------------------------------------------- an in-memory, append-only psycopg-like DB
class _FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeAuditPg:
    """A tiny stand-in for a psycopg connection backing the shared audit table. INSERT ONLY appends
    a fresh row (a monotonic id) — it can NEVER update/delete, mirroring the append-only DDL, so
    the test proves conflict-freeness structurally."""

    def __init__(self):
        self.rows = []      # each: {id, namespace, actor, seq, kind, at, entry}
        self._id = 0
        self.updates = 0    # asserted to stay 0 — no UPDATE/DELETE ever issued

    def execute(self, sql, params=None):
        head = " ".join(sql.split()).upper()
        if head.startswith("CREATE TABLE"):
            return _FakeCursor()
        if head.startswith("INSERT"):
            ns, act, seq, kind, at, entry = params
            self._id += 1
            self.rows.append({"id": self._id, "namespace": ns, "actor": act,
                              "seq": seq, "kind": kind, "at": at, "entry": entry})
            return _FakeCursor(rowcount=1)
        if head.startswith("SELECT MAX(SEQ)"):
            ns, act = params
            seqs = [r["seq"] for r in self.rows
                    if r["namespace"] == ns and r["actor"] == act]
            return _FakeCursor([(max(seqs) if seqs else None,)])
        if head.startswith("SELECT NAMESPACE, ACTOR, ENTRY"):
            sel = [r for r in self.rows if not params or r["namespace"] == params[0]]
            sel.sort(key=lambda r: r["id"])
            return _FakeCursor([(r["namespace"], r["actor"], r["entry"]) for r in sel])
        if head.startswith("UPDATE") or head.startswith("DELETE"):
            self.updates += 1              # must never happen for an append-only log
            return _FakeCursor(rowcount=0)
        return _FakeCursor()


# ================================================================= LOCAL is the default
class TestDefaultsLocal(unittest.TestCase):
    def test_fresh_repo_shares_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            self.assertFalse(TA.shared_enabled(surface.manifest.data))
            with _EnvActor("alice"):
                view = TA.team_audit_view(d, surface)
            self.assertFalse(view.available)
            self.assertIn("OFF", view.message)

    def test_local_ledger_is_unaffected_by_the_feature(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            led = _seed_ledger(surface, ["phase", "write_gate"])
            # the ordinary local read still works, untouched
            self.assertGreaterEqual(len(led.entries()), 2)


# ================================================================= opt-in + writes only when connected
class TestOptInAndConnected(unittest.TestCase):
    def test_share_is_a_noop_until_opted_in(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase"])
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                res = TA.share_audit(root, surface, assume_yes=True, client=pg)
            self.assertFalse(res.committed)
            self.assertEqual(res.reason, "not enabled")
            self.assertEqual(pg.rows, [])          # nothing written anywhere

    def test_opt_in_then_publish_writes_to_the_team_backend(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase", "write_gate", "deviation"])
            surface = _enable_sharing(root)
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                res = TA.share_audit(root, surface, assume_yes=True, client=pg)
            self.assertTrue(res.committed)
            self.assertGreater(res.published, 0)
            self.assertTrue(all(r["actor"] == "alice" for r in pg.rows))
            self.assertTrue(all(r["namespace"] == "acme-app" for r in pg.rows))

    def test_republish_is_idempotent_in_sync(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase"])
            surface = _enable_sharing(root)
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                TA.share_audit(root, surface, assume_yes=True, client=pg)
                n_after_first = len(pg.rows)
                res = TA.share_audit(root, surface, assume_yes=True, client=pg)
            self.assertTrue(res.committed)          # a clean no-op is success
            self.assertEqual(res.published, 0)
            self.assertEqual(res.reason, "in sync")
            self.assertEqual(len(pg.rows), n_after_first)   # nothing duplicated


# ================================================================= append-only + per-actor + namespaced
class TestConflictFree(unittest.TestCase):
    def test_two_concurrent_actors_both_survive_no_clobber(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            # same repo NAME (namespace) on two machines/clones, one shared team DB
            root_a, surf_a = _repo_named(d1)
            root_b, surf_b = _repo_named(d2)
            _seed_ledger(surf_a, ["phase", "write_gate"])
            _seed_ledger(surf_b, ["deviation", "healing_decision", "phase"])
            surf_a = _enable_sharing(root_a)
            surf_b = _enable_sharing(root_b)
            pg = _FakeAuditPg()

            with _EnvActor("alice"):
                res_a = TA.share_audit(root_a, surf_a, assume_yes=True, client=pg)
            with _EnvActor("bob"):
                res_b = TA.share_audit(root_b, surf_b, assume_yes=True, client=pg)

            self.assertTrue(res_a.committed and res_b.committed)
            actors = {r["actor"] for r in pg.rows}
            self.assertEqual(actors, {"alice", "bob"})
            # BOTH actors' full sets survived — nobody clobbered anybody
            self.assertEqual(len(pg.rows), res_a.published + res_b.published)
            self.assertEqual(pg.updates, 0)          # append-only: no UPDATE/DELETE ever
            # ids are monotonic + distinct → each writer got its own row
            self.assertEqual(len({r["id"] for r in pg.rows}), len(pg.rows))


# ================================================================= human-gated + secret-scanned
class TestGatedAndScanned(unittest.TestCase):
    def test_a_decline_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase", "write_gate"])
            surface = _enable_sharing(root)
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                res = TA.share_audit(root, surface, confirm=_no, client=pg)
            self.assertFalse(res.committed)
            self.assertTrue(res.aborted)
            self.assertEqual(pg.rows, [])            # gate held the line

    def test_a_secret_in_an_entry_is_hard_blocked_on_publish(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            led = AuditLedger.from_mokata_dir(surface.mokata_dir)
            led.record("phase", phase="develop", reason="ok")
            led.record("leak", note=f"token {_SECRET}")   # a secret snuck into an entry
            surface = _enable_sharing(root)
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                res = TA.share_audit(root, surface, confirm=_yes, client=pg)  # approved, yet…
            self.assertFalse(res.committed)          # …a secret is an absolute block
            self.assertTrue(res.findings)
            self.assertEqual(pg.rows, [])            # nothing leaked to the shared store


# ================================================================= DSN secret never persisted
class TestDsnNeverStored(unittest.TestCase):
    def test_manifest_stores_the_env_var_name_not_the_secret(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _enable_sharing(root, dsn_env="MY_TEAM_DSN")
            with open(os.path.join(root, ".mokata", "manifest.json"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("MY_TEAM_DSN", text)       # the NAME (pointer) is recorded
            self.assertNotIn("postgres://", text)    # the DSN secret is NOT
            self.assertNotIn(_SECRET_DSN, text)

    def test_publish_never_writes_the_dsn_anywhere(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase"])
            surface = _enable_sharing(root, dsn_env="MY_TEAM_DSN")
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                # even with a DSN secret in the environment, it is only used to CONNECT,
                # never captured into a row
                os.environ["MY_TEAM_DSN"] = _SECRET_DSN
                TA.share_audit(root, surface, assume_yes=True, client=pg)
                os.environ.pop("MY_TEAM_DSN", None)
            blob = json.dumps(pg.rows)
            self.assertNotIn(_SECRET_DSN, blob)
            self.assertNotIn("tok3n_pw", blob)


# ================================================================= degrade-clean (no driver/DSN)
class TestDegradeClean(unittest.TestCase):
    def test_no_dsn_stays_local_no_crash(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase"])
            surface = _enable_sharing(root)
            with _EnvActor("alice"):        # _EnvActor clears the DSN vars
                res = TA.share_audit(root, surface, assume_yes=True)   # no client, no DSN
            self.assertFalse(res.committed)
            self.assertEqual(res.reason, "unavailable")
            self.assertIn("LOCAL", res.message)

    def test_make_shared_log_without_dsn_raises_clear_unavailable(self):
        with _EnvActor("alice"):
            with self.assertRaises(TA.SharedAuditUnavailable) as ctx:
                TA.make_shared_log()
            self.assertIn("DSN", str(ctx.exception))

    def test_team_view_degrades_clean_when_backend_absent(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            surface = _enable_sharing(root)
            with _EnvActor("alice"):
                view = TA.team_audit_view(root, surface)   # enabled but no DSN/driver
            self.assertFalse(view.available)
            self.assertIn("unavailable", view.message)


# ================================================================= NO telemetry / NO egress
class TestNoTelemetry(unittest.TestCase):
    def test_no_network_egress_mechanism_in_the_module(self):
        with open(TA.__file__, encoding="utf-8") as fh:
            src = fh.read()
        # no HTTP/socket egress mechanism and no URL literal exists at all — the only network
        # target is the team's DSN (built by the user in their env, passed to psycopg).
        for bad in ("urllib", "requests", "httpx", "http.client", "socket.", "urlopen", "://"):
            self.assertNotIn(bad, src, f"unexpected egress mechanism/URL in team_audit: {bad}")

    def test_honest_copy_says_no_telemetry(self):
        note = TA.honest_note()
        self.assertIn("NO telemetry", note)
        self.assertIn("never phones", note)
        self.assertIn("Anthropic", note)
        self.assertIn("never stores the DSN secret", note)


# ================================================================= who-did-what read spans the log
class TestWhoDidWhatSpans(unittest.TestCase):
    def test_read_spans_all_actors_with_attribution(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            root_a, surf_a = _repo_named(d1)
            root_b, surf_b = _repo_named(d2)
            _seed_ledger(surf_a, ["phase", "write_gate"])
            _seed_ledger(surf_b, ["deviation"])
            surf_a = _enable_sharing(root_a)
            surf_b = _enable_sharing(root_b)
            pg = _FakeAuditPg()
            with _EnvActor("alice"):
                TA.share_audit(root_a, surf_a, assume_yes=True, client=pg)
            with _EnvActor("bob"):
                TA.share_audit(root_b, surf_b, assume_yes=True, client=pg)

            # read from a THIRD teammate's view over the same shared log
            with _EnvActor("carol"):
                view = TA.team_audit_view(root_a, surf_a, client=pg)
            self.assertTrue(view.available)
            self.assertEqual(view.actors, ["alice", "bob"])
            lines = TA.render_team_timeline(view)
            joined = "\n".join(lines)
            self.assertIn("alice", joined)          # who-did-what attribution surfaces
            self.assertIn("bob", joined)
            self.assertEqual(len(lines), len(view.entries))


# ================================================================= CLI surface
class TestCliSurface(unittest.TestCase):
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

    def test_audit_team_off_is_a_clean_message(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_named(d)
            root = os.path.join(d, "acme-app")
            with _EnvActor("alice"):
                rc, out, err = self._run(["audit", "--team", "--path", root])
            self.assertEqual(rc, 0, err)
            self.assertIn("OFF", out)

    def test_audit_share_without_opt_in_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_named(d)
            root = os.path.join(d, "acme-app")
            with _EnvActor("alice"):
                rc, out, err = self._run(["audit", "--share", "--yes", "--path", root])
            self.assertEqual(rc, 0, err)
            self.assertIn("local", (out + err).lower())

    def test_audit_share_enabled_no_dsn_degrades_clean_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase"])
            _enable_sharing(root)
            with _EnvActor("alice"):     # clears DSN vars → unavailable, but clean
                rc, out, err = self._run(["audit", "--share", "--yes", "--path", root])
            self.assertEqual(rc, 0, err)                 # clean, not a crash
            self.assertIn("LOCAL", out + err)


# ================================================================= MCP surface
class TestMcpSurface(unittest.TestCase):
    def test_audit_share_is_a_gated_write_tool(self):
        from mokata import mcp_server as M
        self.assertIn("audit_share", M.write_tool_names())

    def test_audit_share_disabled_reports_local_first(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo_named(d)
            root = os.path.join(d, "acme-app")
            res = M.audit_share(path=root)
            self.assertEqual(res["status"], "disabled")
            self.assertFalse(res["committed"])

    def test_audit_read_team_flag_degrades_clean(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo_named(d)
            root = os.path.join(d, "acme-app")
            res = M.audit(path=root, team=True)
            self.assertTrue(res["team"])
            self.assertFalse(res["available"])
            self.assertIn("OFF", res["message"])

    def test_audit_read_local_still_works(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            root, surface = _repo_named(d)
            _seed_ledger(surface, ["phase"])
            res = M.audit(path=root)
            self.assertGreaterEqual(res["count"], 1)
            self.assertNotIn("team", res)


# ================================================================= 54e parity stays green
class TestParityGreen(unittest.TestCase):
    def test_audit_share_in_matrix_and_parity_passes(self):
        from mokata import parity
        s = parity.SURFACE_MATRIX["audit"]
        self.assertIn("audit_share", s.mcp_write)
        self.assertIn("audit", s.mcp_read)
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())


if __name__ == "__main__":
    unittest.main()
