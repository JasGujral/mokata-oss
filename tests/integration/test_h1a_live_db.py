"""H-1a grounding — JIT-RECALL-UNSCOPED against a REAL Postgres with TWO REAL SEATS.

Why this leg exists rather than the unit file alone. The defect is a CROSS-SEAT LEAK: an item one
teammate may not read reaching a surface mokata injects AUTOMATICALLY, every turn, without anyone
asking for it. The unit floor proves the filter runs; it cannot prove the filter runs against the
rows a real engine hands back — and this is precisely the family doc 84 files as SHIM-FALSE-GREEN
(DB.S7c1's own live leg was written because an in-Python double "was" the mechanism it checked).
Two things only a real engine shows here:

  * the scope PUSHDOWN is in play. `peek_active(scope_context=…)` sends the broad→narrow union
    down as a WHERE for any backend advertising `supports_scope_pushdown`, which Postgres does and
    the SQLite floor does not. So the live leg exercises a DIFFERENT code path to the same claim —
    and the pushdown is exactly where a wrong predicate would silently widen the result;

  * the rows cross a process/seat boundary for real. Seat B builds its own Surface, its own
    identity (`MOKATA_ACTOR`) and its own policy from its own manifest, then reads rows seat A
    wrote to the SHARED table. Nothing in the read is stubbed.

Gate is the same explicit contract as every other live-DB leg: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

LIVE = os.environ.get("MOKATA_LIVE_DB") == "1"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pg_dsn():
    return os.environ.get("MOKATA_PG_DSN") or os.environ.get("MOKATA_TEST_PG_DSN")


_PG_LIVE = LIVE and _have("psycopg") and bool(_pg_dsn())
_PG_REASON = "live PG off (need MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + reachable DB)"

_PROJECT = "h1a-live"
_QUERY = "deploy the release pipeline on friday"

# bob is a project VIEWER and nothing else. Alice's personal items are hers.
_GRANTS = {"project": {"viewer": ["bob", "alice"]}}


def _seat(d, dsn):
    """One INDEPENDENT seat — its own repo, its own manifest, its own identity, with
    `memory_store` WIRED TO POSTGRES so the read really does cross the shared table
    (`test_db_s7c1_live_db._writer`'s precedent, and for the same stated reason: on the default
    wiring the store would read the LOCAL SQLite floor and prove nothing about a teammate)."""
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    settings = data.setdefault("settings", {})
    settings["mode"] = "team"
    settings.setdefault("project", {})["id"] = _PROJECT
    settings.setdefault("access", {})["grants"] = _GRANTS
    data.setdefault("capabilities", {}).setdefault("memory_store", {})["fallback"] = [
        "postgres", "sqlite"]
    data.setdefault("tools", {})["postgres"] = {
        "provides": "memory_store", "kind": "external", "version": None,
        "detect": {"type": "python_module", "name": "psycopg"}, "enabled": True,
        "config": {"dsn_env": "MOKATA_PG_DSN"}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.environ["MOKATA_PG_DSN"] = dsn
    return Surface.load(d)


def _store_as(surface, actor):
    """A store built AS `actor` — the identity `_identity_and_access_for` resolves from the run
    environment, so it is set around construction, exactly as a second person's session would."""
    from mokata.memory import MemoryStore
    saved = os.environ.get("MOKATA_ACTOR")
    os.environ["MOKATA_ACTOR"] = actor
    try:
        return MemoryStore.from_surface(surface)
    finally:
        if saved is None:
            os.environ.pop("MOKATA_ACTOR", None)
        else:
            os.environ["MOKATA_ACTOR"] = saved


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestJitInjectionIsScopedOnALiveEngine(unittest.TestCase):
    def setUp(self):
        from mokata import teamdb
        from mokata.memory import _pg
        self.dsn = _pg_dsn()
        self._saved_dsn = os.environ.get("MOKATA_PG_DSN")
        self._saved_actor = os.environ.get("MOKATA_ACTOR")
        teamdb.provision(self.dsn)
        conn = _pg.get_connection(self.dsn, RuntimeError)
        conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()
        for var, val in (("MOKATA_PG_DSN", self._saved_dsn), ("MOKATA_ACTOR", self._saved_actor)):
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val

    def _seed(self, store, subject, value, *, kind, item_id, **scope):
        """Seed straight onto the SHARED table (bypassing the write gate) — the fixture under
        test is the READ, and a gated write would only prove the gate."""
        from mokata.memory.item import MemoryItem
        store.backend.put(MemoryItem.create(subject, value, kind=kind, id=item_id, **scope))

    def _two_seats(self, stack):
        da = stack.enter_context(tempfile.TemporaryDirectory())
        db = stack.enter_context(tempfile.TemporaryDirectory())
        return _seat(da, self.dsn), _seat(db, self.dsn)

    def test_a_teammates_private_item_never_returns_from_jit_recall(self):
        """THE claim, on the engine: alice writes a personal item; bob's per-turn JIT recall must
        not see it. It shares the query's every keyword, so relevance is not what excludes it."""
        import contextlib
        from mokata.memory.brain import jit_recall
        with contextlib.ExitStack() as stack:
            sa, sb = self._two_seats(stack)
            alice = _store_as(sa, "alice")
            self._seed(alice, "shared-note", "deploy the release pipeline on friday",
                       kind="context", item_id="project-visible",
                       scope_level="project", scope_id=_PROJECT)
            self._seed(alice, "alice-note",
                       "deploy the release pipeline on friday — alice private",
                       kind="context", item_id="alice-private",
                       scope_level="personal", scope_id="alice")

            bob = _store_as(sb, "bob")
            ids = {i.id for i in jit_recall(bob, _QUERY, top_k=10)}
            self.assertIn("project-visible", ids,
                          "the fixture must actually retrieve, or this pins nothing")
            self.assertNotIn("alice-private", ids,
                             "a teammate's PRIVATE item reached bob's per-turn injection path "
                             "on a live engine — DB.S8 S-2 (zero cross-seat leaks)")

    def test_a_teammates_private_rule_never_reaches_the_always_on_lines(self):
        """The always-on half is already LIVE in the SessionStart briefing, so on a team seat this
        leak ships today. Same claim, same engine, the other injection surface."""
        import contextlib
        from mokata.memory.brain import always_on_items, always_on_lines
        with contextlib.ExitStack() as stack:
            sa, sb = self._two_seats(stack)
            alice = _store_as(sa, "alice")
            self._seed(alice, "team-rule", "always run the tests before pushing",
                       kind="rule", item_id="rule-visible",
                       scope_level="project", scope_id=_PROJECT)
            self._seed(alice, "alice-rule", "alice's private rule: skip the slow suite",
                       kind="rule", item_id="rule-private",
                       scope_level="personal", scope_id="alice")

            bob = _store_as(sb, "bob")
            ids = {i.id for i in always_on_items(bob)}
            self.assertIn("rule-visible", ids)
            self.assertNotIn("rule-private", ids)
            lines, _overflow = always_on_lines(bob, 12)
            self.assertNotIn("alice", " ".join(lines).lower())

    def test_the_owner_still_sees_her_own_private_item(self):
        """The filter must not be a blunt "drop every personal item" — alice's own recall keeps
        hers, or the fix would have traded a leak for a silent loss of memory."""
        import contextlib
        from mokata.memory.brain import jit_recall
        with contextlib.ExitStack() as stack:
            sa, _sb = self._two_seats(stack)
            alice = _store_as(sa, "alice")
            self._seed(alice, "alice-note",
                       "deploy the release pipeline on friday — alice private",
                       kind="context", item_id="alice-private",
                       scope_level="personal", scope_id="alice")
            self.assertIn("alice-private",
                          {i.id for i in jit_recall(alice, _QUERY, top_k=10)})

    def test_the_emitted_per_turn_pack_carries_no_private_item(self):
        """S2/S3's P4 on the engine: the C1 guarantee has to survive the trip through the pack.
        A filter that is correct one call down and lost on the way out is not a filter — and this
        is the surface the human actually reads."""
        import contextlib
        from mokata.bootstrap import INJECTION_TOKEN_BUDGET, build_injection, estimate_tokens
        with contextlib.ExitStack() as stack:
            sa, sb = self._two_seats(stack)
            alice = _store_as(sa, "alice")
            self._seed(alice, "shared-note", "deploy the release pipeline on friday",
                       kind="context", item_id="project-visible",
                       scope_level="project", scope_id=_PROJECT)
            self._seed(alice, "alice-note",
                       "deploy the release pipeline on friday — alice private",
                       kind="context", item_id="alice-private",
                       scope_level="personal", scope_id="alice")

            saved = os.environ.get("MOKATA_ACTOR")
            os.environ["MOKATA_ACTOR"] = "bob"
            try:
                pack = build_injection(sb, _QUERY)
            finally:
                if saved is None:
                    os.environ.pop("MOKATA_ACTOR", None)
                else:
                    os.environ["MOKATA_ACTOR"] = saved

        self.assertIn("project-visible", pack.item_ids,
                      "the fixture must actually inject, or this pins nothing")
        self.assertNotIn("alice-private", pack.item_ids)
        self.assertNotIn("alice private", pack.text)
        # P1 holds on a live engine too — the budget is not a local-mode-only property.
        self.assertLessEqual(estimate_tokens(pack.text), INJECTION_TOKEN_BUDGET)

    def test_the_injection_read_writes_nothing_to_the_shared_tables(self):
        """P1 on the engine that matters: a per-turn injection must not mutate the shared store.
        A read that writes would be a cross-writer bug of its own, once per turn per seat."""
        import contextlib
        from mokata import teamdb
        from mokata.memory import _pg
        from mokata.memory.brain import always_on_items, jit_recall
        with contextlib.ExitStack() as stack:
            sa, sb = self._two_seats(stack)
            alice = _store_as(sa, "alice")
            self._seed(alice, "shared-note", "deploy the release pipeline on friday",
                       kind="context", item_id="project-visible",
                       scope_level="project", scope_id=_PROJECT)
            bob = _store_as(sb, "bob")
            conn = _pg.get_connection(self.dsn, RuntimeError)

            def _snapshot():
                rows = conn.execute(
                    f"SELECT id, {teamdb.MEMORY_REVISION_COLUMN}, doc FROM "
                    f"{teamdb.MEMORY_TABLE} ORDER BY id").fetchall()
                return [tuple(r) for r in rows]

            jit_recall(bob, _QUERY)                       # warm
            before = _snapshot()
            for _ in range(3):
                jit_recall(bob, _QUERY)
                always_on_items(bob)
            self.assertEqual(before, _snapshot(),
                             "per-turn injection mutated the shared store on a live engine")


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
