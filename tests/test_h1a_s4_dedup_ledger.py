"""H-1a S4 — the per-session already-injected ledger.

Relevance is stable, and that is the problem: the item that best matched turn 3 is very likely
the item that best matches turn 4 and turn 20. Without a memory of what it has already said, the
per-turn injection re-hands the model the same items every turn for a whole session — spending
the entire 300-token budget on context the model was given twenty turns ago, while whatever is
genuinely new sits below the cut.

What is pinned here:

  * the same item does NOT re-inject across a ~20-turn session, and the budget it frees goes to
    items that had not surfaced yet;
  * RULES still re-inject every turn. The always-on set is reserved precisely because it is what
    the turn must not VIOLATE; a guardrail is not "already known" because it scrolled out of the
    window an hour ago. Deduping it would be the one place this feature could cause harm;
  * only what SURVIVED the budget is recorded. Recording a dropped item would suppress it for the
    rest of the session without the model ever having seen it — losing memory in the name of not
    repeating it;
  * APPEND-ONLY and SESSION-SCOPED. A new session starts clean, because its model has none of the
    previous session's context and suppressing there would withhold exactly what it most needs;
  * it NEVER raises and never costs a turn — an unwritable ledger degrades to a repeated item,
    which is the state the feature exists to improve on, never worse.

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
from _support import sqlite_disk_ok

from mokata import hook_cli, injection_ledger
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryStore
from mokata.memory.item import MemoryItem

SUB = "user-prompt-submit"
QUERY = "how do I deploy the release pipeline?"


def _repo(d, *, rules=(), items=()):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    surface = Surface.load(d)
    store = MemoryStore.from_surface(surface)
    for i, (subject, value) in enumerate(rules):
        store.backend.put(MemoryItem.create(subject, value, kind="rule", id=f"r{i}"))
    for i, (subject, value) in enumerate(items):
        store.backend.put(MemoryItem.create(subject, value, kind="context", id=f"c{i}"))
    return surface


def _turn(prompt, cwd, session="s-1"):
    payload = json.dumps({"prompt": prompt, "cwd": cwd, "session_id": session})
    out, err = io.StringIO(), io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(payload)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hook_cli.main([SUB])
    finally:
        sys.stdin = saved
    text = ""
    if out.getvalue().strip():
        text = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    return code, text, err.getvalue()


_ITEMS = [(f"ctx{n}", f"the deploy release pipeline runs on friday — note {n} " * 3)
          for n in range(12)]
_RULES = [("deploy-rule", "always run the deploy pipeline tests before releasing")]


# ============================================================================ the ledger itself
class TestTheLedgerPrimitive(unittest.TestCase):
    def test_a_fresh_session_has_injected_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(set(), injection_ledger.already_injected(d, session_id="s"))

    def test_it_is_append_only_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as d:
            injection_ledger.record_injected(d, ["a", "b"], session_id="s")
            injection_ledger.record_injected(d, ["c"], session_id="s")
            injection_ledger.record_injected(d, ["a"], session_id="s")   # a repeat is APPENDED
            self.assertEqual(["a", "b", "c", "a"],
                             injection_ledger.read_injected(d, session_id="s"))
            self.assertEqual({"a", "b", "c"},
                             injection_ledger.already_injected(d, session_id="s"))

    def test_it_is_session_scoped(self):
        """A NEW session must start clean — its model has none of the previous one's context."""
        with tempfile.TemporaryDirectory() as d:
            injection_ledger.record_injected(d, ["a", "b"], session_id="session-one")
            self.assertEqual({"a", "b"},
                             injection_ledger.already_injected(d, session_id="session-one"))
            self.assertEqual(set(),
                             injection_ledger.already_injected(d, session_id="session-two"))

    def test_it_lives_in_transient_run_state_never_in_committed_config(self):
        with tempfile.TemporaryDirectory() as d:
            injection_ledger.record_injected(d, ["a"], session_id="s")
            rel = os.path.relpath(injection_ledger.ledger_dir(d), d)
            self.assertEqual(os.path.join(".mokata", "temp_local", "injection_ledger"), rel)
            self.assertTrue(os.path.isdir(injection_ledger.ledger_dir(d)))

    def test_a_session_id_with_a_separator_cannot_escape_the_ledger_dir(self):
        with tempfile.TemporaryDirectory() as d:
            injection_ledger.record_injected(d, ["a"], session_id="../../evil")
            written = []
            for base, _dirs, files in os.walk(d):
                written += [os.path.join(base, f) for f in files]
            for path in written:
                self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(d)))
            self.assertEqual({"a"}, injection_ledger.already_injected(d, session_id="../../evil"))

    def test_recording_nothing_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            injection_ledger.record_injected(d, [], session_id="s")
            self.assertFalse(os.path.exists(injection_ledger.ledger_dir(d)))

    def test_it_never_raises_on_an_unwritable_location(self):
        """A bookkeeping failure must never cost a turn. Worst case: a repeated item."""
        with mock.patch("os.makedirs", side_effect=OSError("read-only fs")):
            injection_ledger.record_injected("/nonexistent/x", ["a"], session_id="s")
        self.assertEqual(set(), injection_ledger.already_injected("/nonexistent/x",
                                                                 session_id="s"))

    def test_a_corrupt_ledger_degrades_to_empty_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(injection_ledger.ledger_dir(d), exist_ok=True)
            with open(os.path.join(injection_ledger.ledger_dir(d), "injected__s.log"), "wb") as f:
                f.write(b"\xff\xfe not utf-8 at all \x00")
            self.assertEqual(set(), injection_ledger.already_injected(d, session_id="s"))


# =============================================================== the ledger on the injection path
@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TestTheSameItemDoesNotReInjectAcrossASession(unittest.TestCase):
    def test_no_item_is_injected_twice_across_twenty_turns(self):
        """THE pin. Twenty turns of the same question; every id appears at most once."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_RULES, items=_ITEMS)
            seen = []
            for n in range(20):
                code, text, err = _turn(QUERY, d)
                self.assertEqual(0, code)
                self.assertEqual("", err)
                seen += [line for line in text.splitlines()
                         if line.startswith("- [context]")]
            subjects = [ln.split(":")[0] for ln in seen]
            self.assertTrue(subjects, "nothing was ever injected — this pins nothing")
            self.assertEqual(len(subjects), len(set(subjects)),
                             "an item was re-injected: the budget is being spent re-handing the "
                             "model context it was given earlier in the SAME session")

    def test_the_freed_budget_goes_to_items_that_had_not_surfaced_yet(self):
        """Dedup that only SHRINKS the pack would be a regression dressed as a feature: the point
        is that turn 2 says something NEW, not that it says less."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_ITEMS)
            first = [ln for ln in _turn(QUERY, d)[1].splitlines()
                     if ln.startswith("- [context]")]
            second = [ln for ln in _turn(QUERY, d)[1].splitlines()
                      if ln.startswith("- [context]")]
        self.assertTrue(first and second, "one of the turns injected nothing")
        self.assertEqual(set(), set(first) & set(second))

    def test_a_rule_still_re_injects_every_turn(self):
        """The one place this feature could do harm. A guardrail is not 'already known' because
        it scrolled out of the window — the reserved always-on slice is never deduped."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_RULES, items=_ITEMS)
            for _n in range(5):
                _code, text, _err = _turn(QUERY, d)
                self.assertIn("[rule] deploy-rule", text,
                              "the always-on guardrail stopped being injected — dedup reached "
                              "the reserved slice")

    def test_a_new_session_starts_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_ITEMS)
            first = _turn(QUERY, d, session="session-one")[1]
            fresh = _turn(QUERY, d, session="session-two")[1]
        self.assertTrue(first)
        self.assertEqual(first, fresh,
                         "a NEW session inherited the previous session's ledger — its model has "
                         "none of that context and would be told none of it")

    def test_only_what_survived_the_budget_is_recorded(self):
        """Recording a DROPPED item would suppress it for the rest of the session without the
        model ever having seen it — losing memory in the name of not repeating it."""
        fat = [(f"ctx{n}", f"the deploy release pipeline runs on friday — note {n} " * 12)
               for n in range(12)]
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, items=fat)
            from mokata.bootstrap import build_injection
            pack = build_injection(surface, QUERY)
            self.assertGreater(pack.dropped, 0, "the fixture must overflow, or this pins nothing")
            _turn(QUERY, d)
            recorded = injection_ledger.already_injected(d, session_id="s-1")
        self.assertEqual(set(pack.item_ids), recorded)
        self.assertEqual(pack.items_shown, len(recorded))

    def test_the_session_eventually_falls_silent_rather_than_repeating_itself(self):
        """Once everything relevant has been said, the honest answer is nothing — not the same
        list again."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_ITEMS)
            texts = [_turn(QUERY, d)[1] for _n in range(20)]
        self.assertTrue(texts[0], "the first turn injected nothing")
        self.assertEqual("", texts[-1],
                         "after 20 turns the corpus is exhausted and the hook should be silent")

    def test_a_broken_ledger_costs_context_repetition_never_a_turn(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_ITEMS)
            with mock.patch.object(injection_ledger, "record_injected",
                                   side_effect=RuntimeError("boom")):
                code, text, err = _turn(QUERY, d)
        self.assertEqual(0, code, "a ledger failure ate the human's turn")
        self.assertEqual("", err)
        self.assertTrue(text, "a ledger failure suppressed the injection entirely")


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
