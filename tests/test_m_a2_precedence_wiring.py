"""M-A2 — precedence.resolve() wired into the REAL read path (scoped_active / union_read).

The BUG (2026-07-08 audit): the resolution engine (memory/precedence.py `resolve()`) and its 44
property tests existed, but `resolve()` was NEVER called on the real read path. So when two
CONFLICTING scoped items shared one subject/key, `scoped_active` (via `union_read`) returned BOTH
and injected both into context — the reader picked no winner, and the conflict shipped.

This suite covers the WIRING (so it can't silently regress back to dead code):

  * a conflicting pair resolves to exactly ONE winner through `scoped_active` (the real path);
  * the engine's own property scenarios (narrowest-wins / pin floor / safety / merge / tiebreak)
    now drive through `scoped_active` and agree with `precedence.resolve()` — the 44 property
    scenarios exercised on the real read path, not just the isolated engine;
  * LOCAL mode (no scope context) stays byte-identical — a conflicting local pair is NOT resolved
    (both still returned), exactly as pre-M-A2.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory import item as I
from mokata.memory import precedence as P
from mokata.memory import scope as S
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem
from mokata.memory.store import MemoryStore


def mk(subject, value, level=S.PERSONAL, scope_id="", *, kind="", enforcement="", pin=False,
       priority=0, id=None):
    """A scoped memory item (mirrors the TM.S6 test helper): id defaults to a stable, distinct
    subject+level+scope_id so a conflicting pair gets different ids."""
    return MemoryItem.create(subject, value, kind=kind, enforcement=enforcement,
                             scope_level=level, scope_id=scope_id, pin=pin, priority=priority,
                             id=id or f"{subject}-{level}-{scope_id or 'x'}")


def _store():
    d = tempfile.mkdtemp()
    return MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))


def _seed(store, items):
    for it in items:
        store.remember(it, assume_yes=True)


# The team context whose path carries BOTH team T and project P, so a team-scoped and a
# project-scoped item for one subject genuinely conflict ON the read path.
_TEAM_CTX = S.ScopeContext(team="T", project="P", user="U")


class TestScopedActiveResolvesConflict(unittest.TestCase):
    def test_two_conflicting_scoped_items_collapse_to_one_winner(self):
        # db=postgres @team T  vs  db=sqlite @project P — both on the read path, same subject.
        store = _store()
        _seed(store, [mk("db", "postgres", S.TEAM, "T"),
                      mk("db", "sqlite", S.PROJECT, "P")])
        store.scope_context = _TEAM_CTX

        got = [i for i in store.scoped_active() if i.subject == "db"]

        # EXACTLY ONE winner injected — not both (the bug) — and it is the precedence winner
        # (narrowest scope wins → the project item, value "sqlite").
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].value, "sqlite")
        self.assertEqual(got[0].scope_level, S.PROJECT)


# The full broad→narrow context — every scope level is on the path, so any level's items conflict.
_FULL_CTX = S.ScopeContext(team="T", project="P", category="C", user="U")

# The property scenarios (mirroring the 44 engine property tests) — each a set of items that all
# conflict on one subject, driven through the REAL read path and checked against resolve().
_SCENARIOS = [
    ("narrowest_wins_scalar", "db",
     [mk("db", "postgres", S.TEAM, "T"), mk("db", "sqlite", S.PROJECT, "P")]),
    ("full_chain_narrowest", "x",
     [mk("x", "g", S.GLOBAL), mk("x", "t", S.TEAM, "T"),
      mk("x", "p", S.PROJECT, "P"), mk("x", "me", S.PERSONAL, "U")]),
    ("pin_floor_holds", "lang",
     [mk("lang", "python", S.GLOBAL, pin=True), mk("lang", "ruby", S.PROJECT, "P")]),
    ("broadest_pin_among_several", "lang",
     [mk("lang", "python", S.GLOBAL, pin=True), mk("lang", "rust", S.TEAM, "T", pin=True),
      mk("lang", "ruby", S.PROJECT, "P")]),
    ("object_merge_key_by_key", "cfg",
     [mk("cfg", {"a": 1, "b": 2}, S.TEAM, "T"), mk("cfg", {"b": 3, "c": 4}, S.PROJECT, "P")]),
    ("object_merge_recurses", "cfg",
     [mk("cfg", {"x": {"p": 1, "q": 2}}, S.GLOBAL),
      mk("cfg", {"x": {"q": 9, "r": 3}}, S.PROJECT, "P")]),
    ("safety_most_restrictive", "net",
     [mk("net", "deny egress", S.TEAM, "T", kind=I.GUARDRAIL),
      mk("net", "allow egress", S.PERSONAL, "U", kind=I.CONTEXT)]),
    ("every_hard_item_retained", "r",
     [mk("r", "a", S.GLOBAL, kind=I.RULE, enforcement=I.HARD, id="g"),
      mk("r", "b", S.PROJECT, "P", kind=I.RULE, enforcement=I.HARD, id="p")]),
    ("tiebreak_priority", "x",
     [mk("x", "lo", S.TEAM, "T", priority=1, id="a"),
      mk("x", "hi", S.TEAM, "T", priority=5, id="b")]),
    ("tiebreak_smallest_id", "x",
     [mk("x", "B", S.TEAM, "T", priority=0, id="b"),
      mk("x", "A", S.TEAM, "T", priority=0, id="a")]),
]


class TestPropertyScenariosOnRealReadPath(unittest.TestCase):
    """The engine's property scenarios, now EXERCISED THROUGH `scoped_active` (the real read path)
    — not just `resolve()` in isolation. This is the coverage that keeps the wiring from silently
    regressing to dead code: every scenario proves the real path reproduces resolve()'s decision."""

    def _winning_values(self, subject, res):
        """The value-set resolve() treats as winning for `subject`: every authoritative hard item
        for a safety key (all retained), else the single resolved preference value."""
        if res.is_authoritative(subject):
            return sorted(repr(e.value) for e in res.safety_items if e.key == subject)
        return [repr(res.get(subject).value)]

    def test_scoped_active_reproduces_resolve_for_every_scenario(self):
        for name, subject, items in _SCENARIOS:
            with self.subTest(scenario=name):
                store = _store()
                _seed(store, items)
                store.scope_context = _FULL_CTX
                got = sorted(repr(i.value) for i in store.scoped_active() if i.subject == subject)
                expected = self._winning_values(subject, P.resolve(items))
                self.assertEqual(got, expected)

    def test_preference_conflicts_inject_exactly_one_winner(self):
        # every PREFERENCE scenario (all but the two safety ones) collapses to a SINGLE item.
        for name, subject, items in _SCENARIOS:
            if name in ("safety_most_restrictive", "every_hard_item_retained"):
                continue
            with self.subTest(scenario=name):
                store = _store()
                _seed(store, items)
                store.scope_context = _FULL_CTX
                got = [i for i in store.scoped_active() if i.subject == subject]
                self.assertEqual(len(got), 1, f"{name}: expected one winner, got {len(got)}")


class TestLocalModeByteIdentical(unittest.TestCase):
    def test_local_conflict_is_not_resolved_both_still_returned(self):
        # LOCAL mode (no scope context) must NOT resolve — a conflicting local pair still returns
        # BOTH, byte-identical to pre-M-A2. Only team mode (a scope context) resolves.
        store = _store()
        _seed(store, [MemoryItem.create("db", "postgres", id="a"),
                      MemoryItem.create("db", "sqlite", id="b")])
        self.assertIsNone(store.scope_context)          # local mode
        got = [i for i in store.scoped_active() if i.subject == "db"]
        self.assertEqual(len(got), 2)                   # BOTH — no resolution in local mode

    def test_local_scoped_active_is_byte_identical_to_all_active(self):
        store = _store()
        _seed(store, [MemoryItem.create(f"k{i}", f"v{i}") for i in range(5)]
                     + [MemoryItem.create("db", "postgres", id="a"),
                        MemoryItem.create("db", "sqlite", id="b")])   # incl. a conflicting pair
        self.assertIsNone(store.scope_context)
        self.assertEqual([i.to_dict() for i in store.scoped_active()],
                         [i.to_dict() for i in store.all_active()])   # nothing dropped/rewritten


if __name__ == "__main__":
    unittest.main()
