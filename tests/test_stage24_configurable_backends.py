"""Stage 24 Part A — configurable backends & paths + Obsidian detection fix.

Covers (both jsonschema states, like the rest of the suite):
  - build_backend honors tools.<id>.config (vault / path); defaults unchanged when absent.
  - Obsidian detection: a configured vault, the real OS locations, absent when none.
  - Postgres: degrades to the SQLite floor (dsn_env unset / psycopg absent / unreachable);
    network_capable_tools lists it; the secret-guard blocks an inline DSN.
  - `mokata config get/set`: round-trip, human-gated (preview+confirm), secret-blocked.
"""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock

_HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None

import _support  # noqa: F401  (puts src/ on the path)

from mokata.detect import Detector
from mokata.manifest import Manifest
from mokata.memory.backends import (
    ObsidianBackend,
    PostgresUnavailable,
    SQLiteBackend,
    build_postgres_backend,
)
from mokata.memory.store import build_backend, select_memory_backend
from mokata.netguard import network_capable_tools
from mokata.govern.secrets import scan
from mokata import config_cmd


# --------------------------------------------------------------------------- helpers

def _manifest(tools, *, memory_chain=("sqlite",)):
    return Manifest.from_dict({
        "manifest_version": 1,
        "mokata": {"version": "0.0.0"},
        "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {
            "memory_store": {"description": "where memory lives", "layer": "memory",
                             "fallback": list(memory_chain)},
        },
        "tools": tools,
    })


# --------------------------------------------------------------- 1. build_backend config

class TestBuildBackendConfig(unittest.TestCase):
    def test_sqlite_path_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            custom = os.path.join(d, "nested", "custom.db")
            be = build_backend("sqlite", d, config={"path": custom})
            self.assertIsInstance(be, SQLiteBackend)
            self.assertEqual(be.path, custom)
            self.assertTrue(os.path.exists(custom))  # created on connect

    def test_sqlite_default_unchanged_when_no_config(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("sqlite", d)
            self.assertEqual(be.path,
                             os.path.join(d, "temp_local", "memory", "memory.db"))

    def test_obsidian_vault_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            vault = os.path.join(d, "my-vault")
            be = build_backend("obsidian", d, config={"vault": vault})
            self.assertIsInstance(be, ObsidianBackend)
            self.assertEqual(be.vault, vault)

    def test_obsidian_default_unchanged_when_no_config(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("obsidian", d)
            self.assertEqual(be.vault,
                             os.path.join(d, "temp_local", "memory", "vault"))

    def test_config_path_expands_user(self):
        with tempfile.TemporaryDirectory() as home:
            # `~` expands via HOME on POSIX and USERPROFILE on Windows — set both so the test
            # is cross-platform. Compare normalized paths: expanduser only rewrites the `~`, so
            # the input's "/" survives on Windows ("home/db.sqlite" vs "home\\db.sqlite").
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
                be = build_backend("sqlite", "/ignored",
                                   config={"path": "~/db.sqlite"})
                self.assertEqual(os.path.normpath(be.path),
                                 os.path.normpath(os.path.join(home, "db.sqlite")))

    def test_select_memory_backend_threads_tool_config(self):
        with tempfile.TemporaryDirectory() as d:
            custom = os.path.join(d, "routed.db")
            m = _manifest({
                "sqlite": {"provides": "memory_store", "kind": "library",
                           "detect": {"type": "python_module", "name": "sqlite3"},
                           "enabled": True, "config": {"path": custom}},
            })
            from mokata.router import Router
            be = select_memory_backend(Router(m, Detector()), d)
            self.assertEqual(be.path, custom)


# ----------------------------------------------------------------- 2. Obsidian detection

class TestObsidianDetection(unittest.TestCase):
    def _tool(self, **config):
        t = {"provides": "memory_store", "kind": "external",
             "detect": {"type": "obsidian"}}
        if config:
            t["config"] = config
        return t

    def test_configured_vault_present(self):
        with tempfile.TemporaryDirectory() as vault:
            det = Detector(cache=False)
            self.assertTrue(det.is_present("obsidian", self._tool(vault=vault)))

    def test_configured_vault_absent_when_missing(self):
        det = Detector(cache=False)
        with mock.patch("mokata.detect._obsidian_config_dirs", return_value=[]):
            self.assertFalse(
                det.is_present("obsidian", self._tool(vault="/nope/not/here")))

    def test_real_os_location_present(self):
        with tempfile.TemporaryDirectory() as appdir:
            det = Detector(cache=False)
            with mock.patch("mokata.detect._obsidian_config_dirs",
                            return_value=[appdir]):
                self.assertTrue(det.is_present("obsidian", self._tool()))

    def test_absent_when_no_vault_and_no_app_dir(self):
        det = Detector(cache=False)
        with mock.patch("mokata.detect._obsidian_config_dirs", return_value=[]):
            self.assertFalse(det.is_present("obsidian", self._tool()))

    def test_candidate_dirs_cover_each_platform(self):
        from mokata import detect
        with mock.patch.dict(os.environ, {"APPDATA": "C:\\Users\\me\\AppData\\Roaming"}):
            dirs = detect._obsidian_config_dirs()
        joined = " ".join(dirs)
        self.assertIn(os.path.join("Library", "Application Support", "obsidian"),
                      joined)                                   # macOS
        self.assertIn(os.path.join(".config", "obsidian"), joined)  # Linux
        self.assertTrue(any("AppData" in d and "obsidian" in d for d in dirs))  # Windows


# ------------------------------------------------------------------------- 3. Postgres

class TestPostgresBackend(unittest.TestCase):
    def test_degrades_when_dsn_env_unset(self):
        self.assertIsNone(build_postgres_backend({}))
        self.assertIsNone(build_postgres_backend({"dsn_env": "MOKATA_TEST_DSN_UNSET"}))

    def test_degrades_when_env_present_but_psycopg_absent(self):
        # Even with the env var set, if psycopg can't import (CI default) or the DB is
        # unreachable, the builder returns None so the caller falls to the SQLite floor.
        with mock.patch.dict(os.environ, {"MOKATA_TEST_DSN": "postgresql://x/db"}):
            self.assertIsNone(build_postgres_backend({"dsn_env": "MOKATA_TEST_DSN"}))

    def test_build_backend_postgres_falls_to_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("postgres", d, config={"dsn_env": "MOKATA_TEST_DSN_UNSET"})
            self.assertIsInstance(be, SQLiteBackend)
            self.assertEqual(be.path,
                             os.path.join(d, "temp_local", "memory", "memory.db"))

    @unittest.skipIf(_HAS_PSYCOPG,
                     "psycopg installed — skip the absent-dependency degrade check")
    def test_postgres_backend_raises_unavailable_without_psycopg(self):
        # Direct construction surfaces the typed degrade signal (caught by the builder)
        # when the optional psycopg extra isn't installed.
        from mokata.memory.backends import PostgresBackend
        with self.assertRaises(PostgresUnavailable):
            PostgresBackend("postgresql://x/db")

    def test_network_capable_tools_includes_postgres(self):
        m = _manifest({
            "sqlite": {"provides": "memory_store", "kind": "library",
                       "detect": {"type": "python_module", "name": "sqlite3"},
                       "enabled": True},
            "postgres": {"provides": "memory_store", "kind": "external",
                         "detect": {"type": "python_module", "name": "psycopg"},
                         "enabled": True, "config": {"dsn_env": "MOKATA_PG_DSN"}},
        }, memory_chain=("postgres", "sqlite"))
        self.assertIn("postgres", network_capable_tools(m))
        self.assertNotIn("sqlite", network_capable_tools(m))  # library = local-only

    def test_secret_guard_blocks_inline_dsn(self):
        # A plaintext DSN with credentials must be caught — manifests are committed.
        findings = scan(text='"dsn": "postgresql://user:s3cr3tpw@db.example.com:5432/app"')
        self.assertTrue(findings, "secret-guard must flag an inline DSN with credentials")

    def test_env_var_reference_is_not_a_secret(self):
        # The supported form — naming an env var — carries no credential, so it's clean.
        findings = scan(text='"dsn_env": "MOKATA_PG_DSN"')
        self.assertEqual(findings, [])


# ------------------------------------------------------------------- 4. mokata config

def _init(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


class TestConfigGetSet(unittest.TestCase):
    def test_get_reads_dotted_key(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            found, val = config_cmd.config_get(d, "profile")
            self.assertTrue(found)
            self.assertEqual(val, "standard")

    def test_get_missing_key(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            found, val = config_cmd.config_get(d, "tools.sqlite.config.path")
            self.assertFalse(found)

    def test_set_round_trip_human_gated(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = config_cmd.config_set(
                d, "tools.sqlite.config.path", "/data/custom.db",
                confirm=lambda _: True, out=lambda _: None)
            self.assertTrue(res.committed)
            found, val = config_cmd.config_get(d, "tools.sqlite.config.path")
            self.assertTrue(found)
            self.assertEqual(val, "/data/custom.db")
            # the manifest still validates after the write
            from mokata.config import Surface
            Surface.load(d)

    def test_set_declined_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = config_cmd.config_set(
                d, "tools.sqlite.config.path", "/data/custom.db",
                confirm=lambda _: False, out=lambda _: None)
            self.assertFalse(res.committed)
            self.assertTrue(res.aborted)
            found, _ = config_cmd.config_get(d, "tools.sqlite.config.path")
            self.assertFalse(found)  # nothing was written

    def test_set_blocks_inline_dsn_secret(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = config_cmd.config_set(
                d, "tools.postgres.config.dsn",
                "postgresql://user:s3cr3tpw@db.example.com/app",
                confirm=lambda _: True, out=lambda _: None)
            self.assertFalse(res.committed)
            self.assertTrue(res.findings)  # secret-guard blocked it
            found, _ = config_cmd.config_get(d, "tools.postgres.config.dsn")
            self.assertFalse(found)

    def test_set_coerces_types(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            config_cmd.config_set(d, "tools.sqlite.enabled", "false",
                                  assume_yes=True, out=lambda _: None)
            _, val = config_cmd.config_get(d, "tools.sqlite.enabled")
            self.assertIs(val, False)


class TestConfigCLI(unittest.TestCase):
    def _run(self, argv):
        import io
        from contextlib import redirect_stdout
        from mokata.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_cli_set_then_get(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            rc, _ = self._run(["config", "set", "tools.sqlite.config.path",
                               "/data/x.db", "--yes", "--path", d])
            self.assertEqual(rc, 0)
            rc, out = self._run(["config", "get", "tools.sqlite.config.path",
                                 "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("/data/x.db", out)

    def test_cli_get_unset_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            rc, _ = self._run(["config", "get", "tools.sqlite.config.path", "--path", d])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
