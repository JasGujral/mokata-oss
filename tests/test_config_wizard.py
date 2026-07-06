"""RT.S3 A3 — `mokata config wizard`: an interactive, GATED settings walk.

The wizard is a FRONT-END, never a second write authority (P2): every change it makes is routed
through the one gated path (`config_cmd.config_set` → WriteGate: secret-scan + schema-validate +
human gate + ledger). It never writes the manifest itself. Fail-closed on a non-TTY / unreadable
stdin (no change, no hang). Reject leaves the manifest byte-unchanged; approve commits + ledgers;
a secret in a value is blocked. The curated setting list mirrors the documented settings reference
(docs/reference/manifest.md) — a drift guard keeps them in lockstep, so it is never invented.
"""

import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, config_cmd, config_wizard
from mokata.init import init_repo

ROOT = os.path.join(os.path.dirname(__file__), "..")
MANIFEST_MD = os.path.join(ROOT, "docs", "reference", "manifest.md")


def _init(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)


def _bytes(path):
    return Path(path).read_bytes()


def _reader(*answers):
    """A fake input() that returns queued answers, then raises EOFError (fail-closed default)."""
    it = iter(answers)

    def r(_prompt=""):
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc
    return r


class FakeLedger:
    def __init__(self):
        self.entries = []

    def record(self, event, **kw):
        self.entries.append((event, kw))


def _one(rel="ux.progress"):
    return tuple(s for s in config_wizard.SETTINGS if s.rel == rel)


# ============================================================= single write path (no raw write)
class TestSingleWritePath(unittest.TestCase):
    def test_a_change_routes_through_config_set_not_a_raw_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = _init(d)
            before = _bytes(path)
            calls = []

            def spy(root, key, raw, **kw):
                calls.append((root, key, raw, kw))
                return config_cmd.ConfigSetResult(False, True, "spied", key=key)

            with mock.patch.object(config_cmd, "config_set", spy):
                config_wizard.run_wizard(d, settings=_one(), reader=_reader("e", "both"),
                                         confirm=lambda _t: True, out=lambda _s: None, is_tty=True)
            # the wizard delegated the write entirely — it never touched the manifest itself
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], "settings.ux.progress")
            self.assertEqual(calls[0][2], "both")
            self.assertEqual(_bytes(path), before)   # byte-unchanged (spy didn't write)

    def test_config_set_receives_the_ledger_so_the_change_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            ledger = FakeLedger()
            config_wizard.run_wizard(d, settings=_one(), reader=_reader("e", "both"),
                                     confirm=lambda _t: True, ledger=ledger,
                                     out=lambda _s: None, is_tty=True)
            kinds = [e for e in ledger.entries if e[0] == "write_gate"]
            self.assertTrue(kinds, "no write_gate ledger entry — the gate/ledger path was bypassed")
            self.assertEqual(kinds[-1][1].get("decision"), "approved")


# ============================================================= approve / reject outcomes
class TestApproveReject(unittest.TestCase):
    def test_approve_commits_the_change(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = config_wizard.run_wizard(d, settings=_one(), reader=_reader("e", "both"),
                                           confirm=lambda _t: True, out=lambda _s: None,
                                           is_tty=True)
            self.assertIn("settings.ux.progress", res.changed)
            found, val = config_cmd.config_get(d, "settings.ux.progress")
            self.assertTrue(found)
            self.assertEqual(val, "both")

    def test_reject_at_the_gate_leaves_manifest_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = _init(d)
            before = _bytes(path)
            res = config_wizard.run_wizard(d, settings=_one(), reader=_reader("e", "both"),
                                           confirm=lambda _t: False,   # decline at the write gate
                                           out=lambda _s: None, is_tty=True)
            self.assertEqual(res.changed, [])
            self.assertEqual(_bytes(path), before)

    def test_skip_choice_leaves_manifest_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = _init(d)
            before = _bytes(path)
            res = config_wizard.run_wizard(d, settings=_one(), reader=_reader("r"),
                                           out=lambda _s: None, is_tty=True)
            self.assertEqual(res.changed, [])
            self.assertEqual(_bytes(path), before)


# ============================================================= security hard-block
class TestSecretBlocked(unittest.TestCase):
    def test_a_secret_value_is_blocked_and_surfaced(self):
        with tempfile.TemporaryDirectory() as d:
            path = _init(d)
            before = _bytes(path)
            buf = io.StringIO()
            # a live DSN with an inline credential is a hard block in config_set
            res = config_wizard.run_wizard(
                d, settings=(config_wizard.Setting("tools.pg.config.dsn", "tools.pg.config.dsn",
                                                   "string", "", "a backend DSN"),),
                reader=_reader("e", "postgresql://user:secretpw@host:5432/db"),
                confirm=lambda _t: True, out=buf.write, is_tty=True)
            self.assertEqual(res.changed, [])
            self.assertEqual(_bytes(path), before)   # nothing written
            self.assertIn("secret", buf.getvalue().lower())     # the block is surfaced


# ============================================================= fail-closed on non-TTY
class TestFailClosed(unittest.TestCase):
    def test_non_tty_makes_no_change_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            path = _init(d)
            before = _bytes(path)
            buf = io.StringIO()
            called = []
            with mock.patch.object(config_cmd, "config_set",
                                   lambda *a, **k: called.append(a)):
                res = config_wizard.run_wizard(d, reader=_reader("e", "both"),
                                               out=buf.write, is_tty=False)
            self.assertFalse(res.ran)
            self.assertEqual(called, [])                        # no write attempted
            self.assertEqual(_bytes(path), before)
            self.assertIn("not a TTY", buf.getvalue())

    def test_unreadable_stdin_during_the_walk_makes_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = _init(d)
            before = _bytes(path)
            # reader raises EOFError immediately -> read_approve_edit_reject -> reject (no change)
            res = config_wizard.run_wizard(d, settings=_one(), reader=_reader(),
                                           out=lambda _s: None, is_tty=True)
            self.assertEqual(res.changed, [])
            self.assertEqual(_bytes(path), before)


# ============================================================= drift guard (sourced from the ref)
class TestSourcedFromReference(unittest.TestCase):
    def _reference_scalar_settings(self):
        """The concrete scalar settings documented in docs/reference/manifest.md's Settings
        table — excludes object rows (`{...}`) and parameterized rows (`<id>`/`<tool>`)."""
        text = Path(MANIFEST_MD).read_text(encoding="utf-8")
        section = text.split("## Settings", 1)[1].split("\n## ", 1)[0]
        found = {}
        for line in section.splitlines():
            m = re.match(r"\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
            if not m:
                continue
            key, shape, default = m.group(1), m.group(2), m.group(3)
            if "<" in key or shape.strip().startswith("`{"):
                continue                                       # parameterized / object row
            found[key] = default.strip().strip("`")
        return found

    def test_every_wizard_setting_is_a_documented_scalar_setting(self):
        ref = self._reference_scalar_settings()
        self.assertTrue(ref, "parsed no scalar settings from manifest.md — parser drifted")
        for s in config_wizard.SETTINGS:
            self.assertIn(s.rel, ref,
                          f"{s.rel} is not in the manifest.md Settings table (invented setting?)")

    def test_wizard_covers_every_documented_scalar_setting(self):
        ref = set(self._reference_scalar_settings())
        have = {s.rel for s in config_wizard.SETTINGS}
        missing = ref - have
        self.assertEqual(missing, set(),
                         f"documented scalar settings missing from the wizard: {missing}")

    def test_full_key_is_the_settings_prefixed_relative_key(self):
        for s in config_wizard.SETTINGS:
            self.assertEqual(s.key, f"settings.{s.rel}")


# ============================================================= CLI wiring
class TestCliWiring(unittest.TestCase):
    def test_config_wizard_action_is_registered(self):
        from mokata import cli
        parser = cli.build_parser()
        # `config` takes an action; `wizard` must be an accepted action value
        # (parse a full argv and confirm it dispatches to the wizard func).
        ns = parser.parse_args(["config", "wizard"])
        self.assertEqual(getattr(ns, "action", None), "wizard")

    def test_wizard_runs_via_cli_main_fail_closed_when_piped(self):
        from mokata import cli
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            buf = io.StringIO()
            # Force a non-TTY / EOF stdin (StringIO reports isatty()==False and yields EOF)
            # so the fail-closed path is exercised regardless of the ambient terminal.
            with mock.patch("sys.stdout", buf), mock.patch("sys.stdin", io.StringIO("")):
                rc = cli.main(["config", "wizard", "--path", d])
            # piped stdin (StringIO) is not a TTY -> fail-closed, no change, clean message
            self.assertIn("not a TTY", buf.getvalue())
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
