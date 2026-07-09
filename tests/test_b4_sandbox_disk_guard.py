"""Stage B4 (0.0.12) — the sandbox-disk-artifact guard for the 2 SQLite playbook tests.

`test_govern_wiring.TestPlaybookDenseSurface.{test_dense_flag_runs_clean,test_default_runs_clean}`
build a file-backed SQLiteBackend (via run_playbook → MemoryStore.from_surface). On a native
filesystem (CI + any normal dev machine) that works and the tests RUN + PASS. In the mokata
build sandbox the overlay FS raises `sqlite3.OperationalError: disk I/O error` when SQLite opens
the on-disk DB (doc 60 / doc 02 0.0.11 caveat) — a broken-disk artifact, unrelated to the code
under test.

`sqlite_disk_ok()` is the PRECISE guard: it performs the SAME create-table + write a file-backed
SQLiteBackend does, so it returns False EXACTLY (and only) when that real on-disk artifact
genuinely can't exist — the sandbox broken-disk case. On native fs it returns True, so the tests
RUN. It never weakens the tests into always-skip, and it never silently skips on an unrelated
error.

B4-FU: the probe MUST exercise the SAME on-disk location `SQLiteBackend` writes to (the repo
`.mokata/temp_local/memory` dir), NOT a system /tmp dir — a split-disk sandbox backs /tmp SQLite
fine while the repo dir raises `disk I/O error`. `TestProbeTargetsBackendLocation` locks that so
the fix can't regress to probing /tmp.
"""

import os
import sqlite3
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok


class TestSqliteDiskProbe(unittest.TestCase):
    @unittest.skipUnless(sqlite_disk_ok(), "sandbox broken-disk — the native-fs assertion runs on CI/dev")
    def test_native_fs_reports_disk_ok(self):
        # On a native fs (CI / dev) the probe must let the tests RUN. Guarded so it skips cleanly
        # on the broken-disk sandbox rather than failing there (same degrade-clean posture).
        self.assertTrue(sqlite_disk_ok())

    def test_disk_io_error_reports_not_ok(self):
        # The sandbox broken-disk case: SQLite raises OperationalError('disk I/O error').
        # The probe must report NOT ok so the 2 tests skip CLEANLY (never error).
        from _support import _probe_sqlite_disk
        with mock.patch("sqlite3.connect",
                        side_effect=sqlite3.OperationalError("disk I/O error")):
            self.assertFalse(_probe_sqlite_disk())

    def test_unrelated_operational_error_still_runs(self):
        # PRECISION: a non-disk OperationalError must NOT trigger a spurious skip — default to
        # running so a real problem surfaces as a real failure, never a silent skip.
        from _support import _probe_sqlite_disk
        with mock.patch("sqlite3.connect",
                        side_effect=sqlite3.OperationalError("near \"x\": syntax error")):
            self.assertTrue(_probe_sqlite_disk())

    def test_probe_is_cached_and_boolean(self):
        self.assertIsInstance(sqlite_disk_ok(), bool)
        self.assertEqual(sqlite_disk_ok(), sqlite_disk_ok())


class TestProbeTargetsBackendLocation(unittest.TestCase):
    """B4-FU: the probe must exercise the SAME on-disk location `SQLiteBackend` writes to — the
    repo `.mokata/temp_local/memory` dir — NOT a system /tmp dir. Locks the fix so it can't
    regress to probing /tmp (where a split-disk sandbox reports a false "disk ok")."""

    def _backend_memory_dir(self):
        """The directory the guarded playbook tests' SQLiteBackend actually writes into, derived
        the SAME way `store.select_memory_backend` derives it for `root="."` (make_surface's root).
        Realpath so the comparison is mount-exact, not string-exact."""
        from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
        from mokata.memory.store import MEMORY_DIRNAME
        mokata_dir = os.path.join(os.getcwd(), MOKATA_DIR)   # Surface(root=".").mokata_dir
        return os.path.realpath(os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, MEMORY_DIRNAME))

    def test_probe_dir_is_the_backend_memory_dir(self):
        from _support import _sqlite_probe_dir
        self.assertEqual(os.path.realpath(_sqlite_probe_dir()), self._backend_memory_dir())

    def test_probe_dir_is_under_the_repo_not_an_independent_tempdir(self):
        # The original bug: probing a cwd-INDEPENDENT system temp dir (tempfile.gettempdir()), which
        # can be on a different disk than the backend. The invariant is that the probe lives under
        # the repo's OWN cwd (its `.mokata`), so it tracks the backend's disk. This must hold even
        # when the repo itself is checked out under the system temp dir — as it is on CI and in the
        # public-subset preflight's throwaway mirror checkout (a "not under /tmp" check wrongly fails
        # there). Realpath both sides so the comparison is mount-exact.
        from _support import _sqlite_probe_dir
        probe = os.path.realpath(_sqlite_probe_dir())
        cwd = os.path.realpath(os.getcwd())
        self.assertTrue(probe.startswith(cwd + os.sep),
                        f"probe dir {probe} is not under the repo cwd {cwd} — it must track the "
                        f"backend's .mokata dir, never a cwd-independent system temp location")

    def test_probe_connects_under_backend_dir_not_tmp(self):
        # Capture the exact path the probe hands to sqlite3.connect and prove it lives under the
        # backend's .mokata memory dir, never the system temp dir. makedirs is stubbed so this
        # stays hermetic (creates no real .mokata).
        from _support import _probe_sqlite_disk
        seen = []
        with mock.patch("os.makedirs"), \
             mock.patch("sqlite3.connect", side_effect=lambda p, *a, **k: seen.append(p)):
            _probe_sqlite_disk()
        self.assertTrue(seen, "probe never opened a DB")
        connected = os.path.realpath(seen[0])
        self.assertTrue(connected.startswith(self._backend_memory_dir() + os.sep),
                        f"probe connected at {connected}, not under the backend .mokata dir")

    def test_broken_repo_disk_only_still_reports_not_ok(self):
        # The EXACT audit scenario: a split disk where /tmp SQLite works but the repo `.mokata`
        # location raises `disk I/O error`. connect raises ONLY for paths under the backend dir —
        # so a probe that (wrongly) targeted /tmp would see "ok" and RUN; the correct probe targets
        # the repo dir, sees the broken-disk marker, and reports NOT ok → the 2 tests skip cleanly.
        from _support import _probe_sqlite_disk
        backend_dir = self._backend_memory_dir()

        def split_disk(path, *a, **k):
            if os.path.realpath(path).startswith(backend_dir + os.sep):
                raise sqlite3.OperationalError("disk I/O error")
            return sqlite3.connect(":memory:")  # /tmp etc. would work fine

        with mock.patch("os.makedirs"), \
             mock.patch("sqlite3.connect", side_effect=split_disk):
            self.assertFalse(_probe_sqlite_disk())


class TestGuardedTestsRunOnNativeFs(unittest.TestCase):
    @unittest.skipUnless(sqlite_disk_ok(), "sandbox broken-disk — the run-not-skip check is a native-fs claim")
    def test_the_two_playbook_tests_are_not_skipped_here(self):
        # On native fs the 2 guarded tests must actually RUN (not skip): load them and assert
        # each executes to a PASS under a fresh result.
        import test_govern_wiring as G
        loader = unittest.TestLoader()
        names = ["test_dense_flag_runs_clean", "test_default_runs_clean"]
        for name in names:
            case = G.TestPlaybookDenseSurface(name)
            res = unittest.TestResult()
            case.run(res)
            self.assertEqual(res.skipped, [], f"{name} was skipped on native fs")
            self.assertTrue(res.wasSuccessful(), f"{name} did not pass: {res.errors or res.failures}")


if __name__ == "__main__":
    unittest.main()
