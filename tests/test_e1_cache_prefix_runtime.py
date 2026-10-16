"""0.0.23 E1 — HP.S5: the cache-prefix fingerprint gets a RUNTIME caller.

`prefix_fingerprint` existed and was asserted stable by unit tests, but nothing at runtime ever
computed it. These pin the runtime path end to end, through the hooks as the harness runs them:

  * SessionStart records the session's baseline fingerprint and the briefing is byte-identical.
  * An unchanged turn says NOTHING and WRITES nothing (the per-turn lane must not become a write
    per turn).
  * A changed prefix (the constitution edited mid-session) yields ONE notice line naming the
    segment that moved, said once, and logged as data PIPE-TOKEN-AUDIT reads.
  * The notice is costed inside the turn budget, and every failure degrades to silence.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok, tree_snapshot

from mokata import hook_cli
from mokata.bootstrap import build_bootstrap, estimate_tokens
from mokata.config import Surface
from mokata.govern import cache
from mokata.govern.cache import (PREFIX_WATCH_DIRNAME, observe_prefix, prefix_fingerprint,
                                 read_prefix_observations, stable_prefix_for)
from mokata.init import init_repo
from mokata.memory import MemoryStore
from mokata.memory.item import MemoryItem

SID = "s-e1"
QUERY = "how do I deploy the release pipeline?"
_WATCH_REL = "/".join((".mokata", "temp_local", PREFIX_WATCH_DIRNAME)) + "/"


def _repo(d, items=()):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
    surface = Surface.load(d)
    if items:
        store = MemoryStore.from_surface(surface)
        for i, (subject, value) in enumerate(items):
            store.backend.put(MemoryItem.create(subject, value, kind="context", id=f"c{i}"))
    return surface


def _edit_constitution(d, extra="\nArticle 99: a new article.\n"):
    path = os.path.join(d, ".mokata", "constitution.md")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(extra)


def _hook(sub, payload):
    out, err = io.StringIO(), io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hook_cli.main([sub])
    finally:
        sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


def _turn(d, prompt=QUERY):
    return _hook("user-prompt-submit", {"prompt": prompt, "cwd": d, "session_id": SID})


def _start(d):
    return _hook("session-start", {"cwd": d, "session_id": SID, "source": "startup"})


def _context(stdout):
    if not stdout.strip():
        return None
    return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


class TestObservePrefixUnit(unittest.TestCase):
    def test_first_sighting_records_and_is_not_a_change(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            obs = observe_prefix(surface, d, SID)
            self.assertIsNotNone(obs)
            self.assertFalse(obs.changed)
            self.assertTrue(obs.recorded)
            self.assertEqual("", obs.notice())
            # the runtime fingerprint IS prefix_fingerprint of the briefing's own prefix
            self.assertEqual(prefix_fingerprint(stable_prefix_for(surface).text()),
                             obs.fingerprint)
            self.assertTrue(build_bootstrap(surface).text.startswith(
                stable_prefix_for(surface).text()))

    def test_unchanged_is_silent_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            observe_prefix(surface, d, SID)
            before = tree_snapshot(d)
            for _ in range(3):
                obs = observe_prefix(surface, d, SID)
                self.assertFalse(obs.changed)
                self.assertFalse(obs.recorded)
            self.assertEqual(before, tree_snapshot(d))

    def test_a_change_names_the_segment_and_is_said_once(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            first = observe_prefix(surface, d, SID)
            _edit_constitution(d)
            changed = observe_prefix(Surface.load(d), d, SID)
            self.assertTrue(changed.changed)
            self.assertEqual(["constitution"], changed.changed_segments)
            self.assertEqual(first.fingerprint, changed.previous)
            line = changed.notice()
            self.assertIn("cache prefix changed", line)
            self.assertIn("constitution", line)
            self.assertNotIn("\n", line)
            self.assertFalse(observe_prefix(Surface.load(d), d, SID).changed, "said once")

    def test_sessions_are_independent(self):
        with tempfile.TemporaryDirectory() as d:
            observe_prefix(_repo(d), d, "a")
            _edit_constitution(d)
            self.assertFalse(observe_prefix(Surface.load(d), d, "b").changed,
                             "a new session's first sighting is a baseline, not a change")
            self.assertTrue(observe_prefix(Surface.load(d), d, "a").changed)

    def test_the_log_is_data_for_the_audit(self):
        with tempfile.TemporaryDirectory() as d:
            observe_prefix(_repo(d), d, SID, event="session-start")
            _edit_constitution(d)
            observe_prefix(Surface.load(d), d, SID)
            recs = read_prefix_observations(d)
            self.assertEqual(2, len(recs))
            self.assertEqual(["session-start", "turn"], [r["event"] for r in recs])
            self.assertEqual([False, True], [r["changed"] for r in recs])
            for r in recs:
                for key in ("ts", "session", "fingerprint", "segments", "prefix_bytes",
                            "prefix_tokens_est", "previous", "changed_segments"):
                    self.assertIn(key, r)

    def test_a_missing_or_corrupt_log_reads_empty_or_skips(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([], read_prefix_observations(d))
            os.makedirs(cache.prefix_watch_dir(d))
            with open(os.path.join(cache.prefix_watch_dir(d), cache.PREFIX_LOG_NAME), "wb") as fh:
                fh.write(b'not json\n{"changed": false}\n')
            self.assertEqual([{"changed": False}], read_prefix_observations(d))

    def test_the_log_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            with mock.patch.object(cache, "_LOG_MAX_BYTES", 600):
                for n in range(12):
                    observe_prefix(surface, d, f"s{n}")
            size = os.path.getsize(os.path.join(cache.prefix_watch_dir(d), cache.PREFIX_LOG_NAME))
            self.assertLess(size, 1200)
            self.assertTrue(read_prefix_observations(d))

    def test_failure_degrades_to_none(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            with mock.patch.object(cache, "stable_prefix_for", side_effect=RuntimeError("x")):
                self.assertIsNone(observe_prefix(surface, d, SID))

    def test_an_unwritable_state_still_reports_but_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            with mock.patch.object(cache, "_record_observation", side_effect=OSError("ro")):
                obs = observe_prefix(surface, d, SID)
            self.assertIsNotNone(obs)
            self.assertFalse(obs.recorded)


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TestThroughTheHooks(unittest.TestCase):
    def test_session_start_records_and_the_briefing_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with mock.patch("mokata.govern.cache.observe_prefix", return_value=None):
                _c0, base, _e0 = _start(d)
            code, out, err = _start(d)
            self.assertEqual((0, ""), (code, err))
            self.assertEqual(base, out, "recording the baseline must not touch the briefing")
            recs = read_prefix_observations(d)
            self.assertEqual(["session-start"], [r["event"] for r in recs])

    def test_unchanged_turns_are_byte_identical_and_write_nothing_new(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=[("deploy", "the deploy release pipeline runs on friday")])
            _start(d)
            # the S4 already-injected ledger would dedup turn 2; hold it empty to compare bytes
            with mock.patch("mokata.injection_ledger.already_injected", return_value=set()):
                with mock.patch.object(hook_cli, "_prefix_notice", return_value=""):
                    _c, without, _e = _turn(d)
                _c, with_watch, _e = _turn(d)
            self.assertIsNotNone(_context(without), "the fixture must inject")
            self.assertEqual(without, with_watch)
            before = {k: v for k, v in tree_snapshot(d).items() if _WATCH_REL in k}
            _turn(d)
            after = {k: v for k, v in tree_snapshot(d).items() if _WATCH_REL in k}
            self.assertEqual(before, after)

    def test_a_mid_session_change_is_one_line_once(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                                   # empty memory: no pack at all
            _start(d)
            self.assertEqual("", _turn(d)[1], "unchanged + nothing relevant = no channel")
            _edit_constitution(d)
            code, out, err = _turn(d)
            self.assertEqual((0, ""), (code, err))
            ctx = _context(out)
            self.assertIsNotNone(ctx)
            self.assertEqual(1, len(ctx.splitlines()))
            self.assertIn("cache prefix changed", ctx)
            self.assertEqual("", _turn(d)[1], "said once, then silent again")

    def test_the_notice_rides_the_pack_inside_the_turn_budget(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=[(f"ctx{n}", "the deploy release pipeline runs on friday " * 6)
                            for n in range(6)])
            _start(d)
            _edit_constitution(d)
            ctx = _context(_turn(d)[1])
            self.assertTrue(ctx.startswith("mokata: cache prefix changed"))
            self.assertGreater(len(ctx.splitlines()), 1, "the pack still rides with the notice")
            self.assertLessEqual(estimate_tokens(ctx), 300)

    def test_a_watch_failure_is_silence_never_a_lost_turn(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _start(d)
            _edit_constitution(d)
            with mock.patch("mokata.govern.cache.observe_prefix",
                            side_effect=RuntimeError("boom")):
                self.assertEqual((0, "", ""), _turn(d))


if __name__ == "__main__":
    unittest.main()
