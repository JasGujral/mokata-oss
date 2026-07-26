"""SIMP.S2 — the deprecation-notice primitive (warn ONCE per repo per channel).

The shim layer keeps every deprecated channel working EXACTLY as today but emits ONE
deprecation warning per repo per channel — a state marker (atomic O_EXCL under temp_local,
the `graph_adopt.disclose_first_use` precedent), never a nag on every call. The notice names
WHAT is deprecated, the canonical replacement, the one-time migration command, and WHEN it
disappears (0.0.17). This stage WARNS + SHIMS + MIGRATES only — it deletes nothing.
"""

import io
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from mokata import deprecation as D


MIGRATABLE = ("obsidian", "native-memory", "vault", "memory-share")
ALL_CHANNELS = MIGRATABLE + ("neo4j",)


def _mokata_dir(d):
    md = os.path.join(d, MOKATA_DIR)
    os.makedirs(md, exist_ok=True)
    return md


class TestNoticeText(unittest.TestCase):
    def test_every_channel_has_a_notice(self):
        for ch in ALL_CHANNELS:
            self.assertIn(ch, D.CHANNELS)
            self.assertIsInstance(D.CHANNELS[ch], D.DeprecationNotice)

    def test_removal_release_is_0_0_17(self):
        # doc 84:145/:189 CONFIRMED — removal rides 0.0.17, not 0.0.15/0.0.14 (stale labels).
        self.assertEqual(D.REMOVAL_RELEASE, "0.0.17")
        for ch in ALL_CHANNELS:
            self.assertIn("0.0.17", D.CHANNELS[ch].render())

    def test_notice_states_what_replaces_and_when(self):
        for ch in MIGRATABLE:
            text = D.CHANNELS[ch].render()
            self.assertIn("REMOVED", text)
            self.assertIn("0.0.17", text)
            # names the one-time migration command
            self.assertIn(f"mokata migrate {ch}", text)

    def test_neo4j_notice_needs_no_migration_command(self):
        text = D.CHANNELS["neo4j"].render()
        self.assertNotIn("mokata migrate", text)   # graph is derived data — re-index, don't migrate
        self.assertIn("re-index", text.lower())

    def test_notice_never_leaks_a_secret_or_dsn(self):
        # Static text only — no item content, no DSN value, no connection string.
        for ch in ALL_CHANNELS:
            text = D.CHANNELS[ch].render()
            self.assertNotIn("://", text)
            self.assertNotIn("password", text.lower())


class TestWarnOncePerRepo(unittest.TestCase):
    def test_first_use_warns_and_ledgers_second_is_silent(self):
        for ch in ALL_CHANNELS:
            with tempfile.TemporaryDirectory() as d:
                md = _mokata_dir(d)
                out = []
                first = D.warn_deprecated(ch, md, out=out.append)
                self.assertTrue(first, f"{ch}: first use must warn")
                self.assertEqual(len(out), 1)
                self.assertIn("0.0.17", out[0])

                out2 = []
                second = D.warn_deprecated(ch, md, out=out2.append)
                self.assertFalse(second, f"{ch}: second use must be silent (ledgered marker)")
                self.assertEqual(out2, [])

    def test_marker_persists_as_state_under_temp_local(self):
        with tempfile.TemporaryDirectory() as d:
            md = _mokata_dir(d)
            D.warn_deprecated("obsidian", md, out=lambda _m: None)
            marker_dir = os.path.join(md, TEMP_LOCAL_DIRNAME, "deprecations")
            self.assertTrue(os.path.isdir(marker_dir))
            self.assertTrue(os.listdir(marker_dir), "a state marker must be written")

    def test_default_out_is_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            md = _mokata_dir(d)
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                D.warn_deprecated("obsidian", md)
            self.assertIn("0.0.17", buf.getvalue())      # diagnostics go to stderr, not stdout

    def test_records_to_ledger_when_given(self):
        from mokata.govern import AuditLedger
        with tempfile.TemporaryDirectory() as d:
            md = _mokata_dir(d)
            ledger = AuditLedger.from_mokata_dir(md)
            D.warn_deprecated("vault", md, out=lambda _m: None, ledger=ledger)
            kinds = [e.get("kind") for e in ledger.entries()]
            self.assertIn(D.DEPRECATION_LEDGER_KIND, kinds)

    def test_degrade_clean_on_unwritable_marker_dir(self):
        # A best-effort notice: if the marker can't be written we do not crash the caller.
        res = D.warn_deprecated("obsidian", "/nonexistent/\x00bad", out=lambda _m: None)
        self.assertIn(res, (True, False))            # never raises


if __name__ == "__main__":
    unittest.main()
