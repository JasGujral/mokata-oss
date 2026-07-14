"""CM.S1 — the ONE DSN resolver (C-1 CRITICAL live bug).

Before this stage, memory READS honored the team-connect-configured
`tools.postgres.config.dsn_env` while team HEALTH / FLUSH / SYNC / preflight were hardwired
to the literal default env-var name. A team on `team connect --dsn-env CUSTOM` read fine, but
health reported OFFLINE and the flush loop skipped every write forever — silent data loss.

These tests pin the fix: ONE resolver (`mokata.dsn`) is the only place the DSN env-var NAME
is obtained, so reads and writes can NEVER resolve different vars again. The DSN VALUE is
never persisted or logged; resolution failure surfaces the env-var NAME (never the value).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import re
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, dsn, run_mode, team, team_health, team_journal
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory.backends import build_postgres_backend

# A custom env-var NAME that does NOT contain the default-name substring — so a test that
# greps for the literal default can never confuse it with the configured custom name.
CUSTOM = "MOKATA_TEAM_CUSTOM_DSN"
_DSN = "postgres://h/db"


def _set_mode(root, mode):
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = mode
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _team_repo(root, dsn_env=CUSTOM, mode="team"):
    """A repo wired to a CUSTOM shared-DB env var (via the real `team connect` flow), then
    switched to team mode — exactly the situation that used to strand health/flush at C-1."""
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    team.team_connect(root, Surface.load(root), dsn_env, assume_yes=True, out=lambda _m: None)
    _set_mode(root, mode)
    return Surface.load(root)


# --------------------------------------------------------------------------- the resolver unit
class TestResolver(unittest.TestCase):
    def test_default_when_nothing_configured(self):
        self.assertEqual(dsn.resolve_dsn_env(), dsn.DEFAULT_DSN_ENV)
        self.assertEqual(dsn.resolve_dsn_env(None), dsn.DEFAULT_DSN_ENV)
        self.assertEqual(dsn.resolve_dsn_env({}), dsn.DEFAULT_DSN_ENV)

    def test_explicit_override_wins(self):
        data = {"settings": {"team": {"dsn_env": "PERSISTED"}}}
        self.assertEqual(dsn.resolve_dsn_env(data, override="OVERRIDE"), "OVERRIDE")

    def test_persisted_team_pointer_wins_over_default(self):
        data = {"settings": {"team": {"dsn_env": CUSTOM}}}
        self.assertEqual(dsn.resolve_dsn_env(data), CUSTOM)

    def test_tool_config_used_when_team_pointer_absent(self):
        data = {"tools": {"postgres": {"config": {"dsn_env": CUSTOM}}}}
        self.assertEqual(dsn.resolve_dsn_env(data), CUSTOM)

    def test_team_pointer_precedes_tool_config(self):
        data = {"settings": {"team": {"dsn_env": "TEAM_PTR"}},
                "tools": {"postgres": {"config": {"dsn_env": "TOOL_CFG"}}}}
        self.assertEqual(dsn.resolve_dsn_env(data), "TEAM_PTR")

    def test_resolve_reads_value_from_environ_only(self):
        data = {"settings": {"team": {"dsn_env": CUSTOM}}}
        res = dsn.resolve_dsn(data, environ={CUSTOM: _DSN})
        self.assertEqual(res.env_name, CUSTOM)
        self.assertEqual(res.dsn, _DSN)
        self.assertTrue(res.is_set)

    def test_resolve_unset_is_none_not_error(self):
        res = dsn.resolve_dsn({"settings": {"team": {"dsn_env": CUSTOM}}}, environ={})
        self.assertEqual(res.env_name, CUSTOM)
        self.assertIsNone(res.dsn)
        self.assertFalse(res.is_set)

    def test_require_dsn_raises_naming_the_env_not_the_value(self):
        with self.assertRaises(dsn.DsnUnset) as cm:
            dsn.require_dsn({"settings": {"team": {"dsn_env": CUSTOM}}}, environ={})
        self.assertEqual(cm.exception.env_name, CUSTOM)
        self.assertIn(CUSTOM, str(cm.exception))     # the NAME is surfaced
        self.assertNotIn("postgres:", str(cm.exception))   # never a secret VALUE

    def test_degrade_clean_on_broken_surface(self):
        class _Broken:
            @property
            def manifest(self):
                raise RuntimeError("boom")
        self.assertEqual(dsn.resolve_dsn_env(_Broken()), dsn.DEFAULT_DSN_ENV)


# ------------------------------------------------------------ C-1 regression: reads == writes
class TestC1Split(unittest.TestCase):
    """The release exit criterion: with a custom env name configured and a reachable backend,
    health reports CONNECTED and journaled writes flush — reads and writes resolve the SAME
    env var. Only the custom var is exported (NEVER the default name)."""

    def _healthy_probe(self, dsn_value):
        from mokata import teamdb
        return teamdb.ProbeResult(driver_present=True, reachable=True, schema_present=True,
                                  schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                                  elapsed_ms=9.0, detail="reachable")

    def test_the_manifest_persists_the_custom_env_name(self):
        with tempfile.TemporaryDirectory() as d:
            _team_repo(d)
            data = Surface.load(d).manifest.data
            # reads AND the resolver derive the SAME configured name — never a split.
            self.assertEqual(dsn.resolve_dsn_env(data), CUSTOM)
            self.assertEqual(data["tools"]["postgres"]["config"]["dsn_env"], CUSTOM)

    def test_health_is_connected_via_the_custom_env(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            env = {CUSTOM: _DSN}                        # ONLY the custom var — no default name
            v = team_health.check(surface, probe=self._healthy_probe, environ=env, force=True)
            self.assertEqual(v.state, team_health.HEALTHY,
                             "health must resolve the CONFIGURED env, not the hardwired default")
            self.assertTrue(v.ok)

    def test_health_offline_message_names_the_custom_env(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            v = team_health.check(surface, probe=self._healthy_probe, environ={}, force=True)
            self.assertEqual(v.state, team_health.OFFLINE)
            self.assertIn(CUSTOM, v.detail)            # the NAME the user actually configured

    def test_preflight_credentials_resolve_the_custom_env(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            report = run_mode.team_preflight(surface, environ={CUSTOM: _DSN},
                                             probe=self._healthy_probe)
            cred = [c for c in report.checks if c.name == "credentials"]
            self.assertTrue(cred and cred[0].ok,
                            "preflight must check the CONFIGURED env for the DSN")
            self.assertIn(CUSTOM, cred[0].detail)

    def test_flush_connects_through_the_custom_env(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            # a pending journaled write exists.
            team_journal.record_team_write(
                surface, op=team_journal.OP_PUT, table="mokata_memory", key="k1",
                payload={"id": "k1", "mtype": "note", "subject": "s", "status": "active",
                         "doc": "{}", "project": None}, ledger_id="led-1")

            # capture the DSN the journal's default connect resolves — proving it uses CUSTOM,
            # not the hardwired default (which is unset here → would return None → skip forever).
            seen = {}
            import mokata.memory._pg as _pg

            class _FakeConn:
                def execute(self, *a, **k):
                    class _Cur:
                        rowcount = 1
                    return _Cur()

            def _fake_get_connection(dsn_value, unavailable, **kw):
                seen["dsn"] = dsn_value
                return _FakeConn()

            orig = _pg.get_connection
            _pg.get_connection = _fake_get_connection
            try:
                conn = team_journal._default_connect(surface, {CUSTOM: _DSN})
            finally:
                _pg.get_connection = orig
            self.assertIsNotNone(conn, "flush must find the DSN under the CONFIGURED env")
            self.assertEqual(seen.get("dsn"), _DSN)

    def test_memory_read_backend_resolves_the_same_custom_env(self):
        # the reads side already honored config.dsn_env; assert it now routes the SAME resolver
        # value — so reads and writes provably share one env var (unreachable host → None).
        env_backup = os.environ.pop(CUSTOM, None)
        os.environ[CUSTOM] = _DSN
        try:
            be = build_postgres_backend({"dsn_env": CUSTOM})
            self.assertIsNone(be)          # unreachable/no-driver → degrade-clean, never crash
        finally:
            if env_backup is None:
                os.environ.pop(CUSTOM, None)
            else:
                os.environ[CUSTOM] = env_backup


# ---------------------------------------- C-1 amendment: subsystem chains honor the team name
class TestC1Subsystems(unittest.TestCase):
    """The send-back: audit + session fallback chains must NOT skip the CONFIGURED team DSN
    name. With `settings.team.dsn_env=CUSTOM` and ONLY $CUSTOM exported, both subsystems resolve
    $CUSTOM; when a subsystem-specific override var is ALSO exported, IT wins (unchanged)."""

    def _data(self):
        return {"settings": {"team": {"dsn_env": CUSTOM}}}

    def test_audit_resolves_the_configured_team_env(self):
        from mokata import team_audit
        self.assertEqual(team_audit.resolve_dsn(data=self._data(), environ={CUSTOM: _DSN}), _DSN)

    def test_audit_override_var_still_wins_when_set(self):
        from mokata import team_audit
        env = {CUSTOM: _DSN, team_audit.AUDIT_OVERRIDE_ENV: "postgres://a/db"}
        self.assertEqual(team_audit.resolve_dsn(data=self._data(), environ=env),
                         "postgres://a/db")

    def test_audit_display_name_is_the_team_env(self):
        from mokata import team_audit
        self.assertEqual(team_audit.dsn_env_name(self._data()), CUSTOM)

    def test_session_resolves_the_configured_team_env(self):
        from mokata import session_transport
        self.assertEqual(session_transport.resolve_pg_dsn(data=self._data(), environ={CUSTOM: _DSN}),
                         _DSN)

    def test_session_override_var_still_wins_when_set(self):
        from mokata import session_transport
        env = {CUSTOM: _DSN, session_transport.SESSION_OVERRIDE_ENV: "postgres://s/db"}
        self.assertEqual(session_transport.resolve_pg_dsn(data=self._data(), environ=env),
                         "postgres://s/db")

    def test_end_to_end_via_team_connect_manifest(self):
        from mokata import session_transport, team_audit
        with tempfile.TemporaryDirectory() as d:
            data = _team_repo(d).manifest.data     # settings.team.dsn_env + tool config = CUSTOM
            self.assertEqual(team_audit.resolve_dsn(data=data, environ={CUSTOM: _DSN}), _DSN)
            self.assertEqual(
                session_transport.resolve_pg_dsn(data=data, environ={CUSTOM: _DSN}), _DSN)

    def test_session_root_threading_reads_manifest_not_cwd(self):
        # make_transport threads `root`; the resolver loads the manifest AT root (never cwd).
        from mokata import session_transport
        with tempfile.TemporaryDirectory() as d:
            _team_repo(d)
            self.assertEqual(
                session_transport.resolve_pg_dsn(root=d, environ={CUSTOM: _DSN}), _DSN)


# ----------------------------------------------------- the literal lives ONLY in the resolver
class TestLiteralGuard(unittest.TestCase):
    """No module except the resolver may reference the DSN env-var NAME literal directly — the
    guard that keeps the split from ever re-appearing (a new call site can't hardwire it)."""

    def test_only_the_resolver_module_names_the_literal(self):
        src_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src", "mokata")
        resolver_rel = os.path.join("mokata", "dsn.py")
        needle = dsn.DEFAULT_DSN_ENV
        offenders = []
        for dirpath, _dirs, files in os.walk(src_root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, os.path.dirname(src_root))
                if rel == resolver_rel:
                    continue
                with open(full, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        if re.search(re.escape(needle), line):
                            offenders.append(f"{rel}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "the DSN env-var NAME literal must live ONLY in mokata/dsn.py:\n"
                         + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
