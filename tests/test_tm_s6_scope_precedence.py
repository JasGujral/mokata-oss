"""TM.S6 — scope hierarchy + precedence/merge engine + union read (doc 62 §2–3).

Property + regression coverage for the shared-memory FOUNDATION:

  * scope model: strict linear chain, single-parent categories, cycle-proof BY CONSTRUCTION;
  * union read: the ordered path global→team→project→category→personal, and recall = the UNION
    of items across it — with LOCAL mode (personal-only path) byte-identical to pre-TM.S6;
  * precedence engine (pure): narrowest-wins; broadest-PIN floor (narrower can't override);
    object key-by-key merge; safety-class most-restrictive-wins authoritative at ANY depth;
    priority + deny-overrides tiebreak; determinism (input order never matters);
  * schema v2→v3: idempotent ADD COLUMN scope_level/scope_id/pin/priority + version bump, on a
    mock PG (+ a live-DB leg when $MOKATA_PG_DSN is set) and the local SQLite backend.

Nothing here needs a live database on the default path (mock/in-memory only).
"""

import itertools
import os
import re
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory import item as I
from mokata.memory.item import MemoryItem
from mokata.memory import scope as S
from mokata.memory import precedence as P
from mokata.memory.backends import PostgresBackend, SQLiteBackend
from mokata.memory.store import MemoryStore
from mokata import teamdb


def mk(subject, value, level=S.PERSONAL, scope_id="", *, kind="", enforcement="", pin=False,
       priority=0, id=None):
    """A precedence input item at a chosen scope (id defaults to the subject+level so tests
    that don't care get stable, distinct ids). TM.S7: SAFETY class now needs a HARD rule, so
    safety-intent items pass enforcement=hard (or kind=guardrail, which maps to hard)."""
    return MemoryItem.create(subject, value, kind=kind, enforcement=enforcement,
                             scope_level=level, scope_id=scope_id, pin=pin, priority=priority,
                             id=id or f"{subject}-{level}-{scope_id or 'x'}")


def norm(res):
    """A hashable, order-independent snapshot of a Resolution — for determinism equality."""
    pref = {k: (repr(e.value), e.scope_level, e.pinned, e.merged, tuple(e.source_ids))
            for k, e in res.preferences.items()}
    safe = {k: (repr(e.value), e.scope_level, e.authoritative) for k, e in res.safety.items()}
    items = tuple(sorted((e.key, tuple(e.source_ids)) for e in res.safety_items))
    return (tuple(sorted(pref.items())), tuple(sorted(safe.items())), items)


# ============================================================ scope model + cycle-proof
class TestScopeModel(unittest.TestCase):
    def test_levels_are_the_documented_broad_to_narrow_chain(self):
        self.assertEqual(S.SCOPE_LEVELS,
                         ("global", "team", "project", "category", "personal"))
        # depth strictly increases broad→narrow
        depths = [S.scope_depth(l) for l in S.SCOPE_LEVELS]
        self.assertEqual(depths, sorted(depths))
        self.assertEqual(len(set(depths)), len(depths))

    def test_single_parent_no_diamond(self):
        # each level has exactly ONE strictly-broader parent; global is the root
        self.assertIsNone(S.parent_level(S.GLOBAL))
        for level in S.SCOPE_LEVELS[1:]:
            parent = S.parent_level(level)
            self.assertIsNotNone(parent)
            self.assertLess(S.scope_depth(parent), S.scope_depth(level))

    def test_hierarchy_is_acyclic_by_construction(self):
        # walking parents from the narrowest reaches global with no repeats — proven, never crashes
        S.assert_acyclic()
        seen, level, steps = set(), S.PERSONAL, 0
        while level is not None:
            self.assertNotIn(level, seen)          # no cycle
            seen.add(level)
            level = S.parent_level(level)
            steps += 1 if level is not None else 0
        self.assertEqual(seen, set(S.SCOPE_LEVELS))
        self.assertEqual(steps, len(S.SCOPE_LEVELS) - 1)   # linear chain: exactly len-1 edges

    def test_default_scope_is_personal(self):
        self.assertEqual(S.DEFAULT_SCOPE_LEVEL, S.PERSONAL)
        self.assertEqual(MemoryItem.create("k", "v").scope_level, S.PERSONAL)

    def test_categories_are_free_tags_not_a_fixed_taxonomy(self):
        # conventional defaults are offered, but any string is a valid category id
        self.assertIn("backend", S.CONVENTIONAL_CATEGORIES)
        ctx = S.ScopeContext(project="p", category="a-totally-custom-tag")
        self.assertIn(S.ScopeRef(S.CATEGORY, "a-totally-custom-tag"), S.scope_path(ctx))


# ============================================================ scope path + union read
class TestUnionRead(unittest.TestCase):
    def test_path_is_ordered_broad_to_narrow(self):
        ctx = S.ScopeContext(team="T", project="P", category="C", user="U")
        path = S.scope_path(ctx)
        self.assertEqual([r.level for r in path],
                         ["global", "team", "project", "category", "personal"])
        self.assertEqual([r.id for r in path], [None, "T", "P", "C", "U"])

    def test_absent_levels_are_omitted_personal_always_present(self):
        ctx = S.ScopeContext(project="P")           # no team/category/user
        path = S.scope_path(ctx)
        self.assertEqual([r.level for r in path], ["global", "project", "personal"])
        self.assertEqual(path[-1], S.ScopeRef(S.PERSONAL, None))

    def test_union_returns_only_items_on_the_path(self):
        items = [
            mk("g", "v", S.GLOBAL),
            mk("t", "v", S.TEAM, "T"),
            mk("t2", "v", S.TEAM, "OTHER"),          # different team → off path
            mk("p", "v", S.PROJECT, "P"),
            mk("c", "v", S.CATEGORY, "C"),
            mk("me", "v", S.PERSONAL, "U"),
            mk("other", "v", S.PROJECT, "OTHERPROJ"),  # off path
        ]
        ctx = S.ScopeContext(team="T", project="P", category="C", user="U")
        got = {it.subject for it in S.union_read(items, ctx)}
        self.assertEqual(got, {"g", "t", "p", "c", "me"})

    def test_union_preserves_input_order(self):
        items = [mk("a", "1", S.GLOBAL), mk("b", "2", S.PROJECT, "P"),
                 mk("c", "3", S.PERSONAL, "U")]
        ctx = S.ScopeContext(project="P", user="U")
        self.assertEqual([it.subject for it in S.union_read(items, ctx)], ["a", "b", "c"])

    def test_broader_context_never_silently_dropped(self):
        # a global item is visible from a deep project/category context (additive accumulation)
        items = [mk("org-rule", "v", S.GLOBAL)]
        ctx = S.ScopeContext(team="T", project="P", category="C", user="U")
        self.assertEqual([it.subject for it in S.union_read(items, ctx)], ["org-rule"])


# ============================================================ LOCAL = personal-only, byte-identical
class TestLocalPersonalOnly(unittest.TestCase):
    def test_personal_only_path_is_just_personal(self):
        path = S.scope_path(S.personal_context())
        self.assertEqual(path, [S.ScopeRef(S.PERSONAL, None)])   # no global/team/project/category

    def test_personal_only_union_returns_all_personal_items_identically(self):
        # every legacy / default item is personal → the personal-only union == the full list
        items = [MemoryItem.create(f"k{i}", str(i)) for i in range(6)]
        self.assertEqual(S.union_read(items, S.personal_context()), items)      # byte-identical

    def test_legacy_empty_scope_id_matches_any_personal(self):
        legacy = MemoryItem.create("k", "v")        # scope_id == "" (pre-TM.S6 default)
        self.assertEqual(legacy.scope_id, "")
        self.assertTrue(S.on_path(legacy, S.scope_path(S.personal_context(user="anyone"))))


# ============================================================ precedence: class derivation
class TestPrecedenceClass(unittest.TestCase):
    def test_only_hard_rules_are_safety(self):
        # TM.S7 — the class is ENFORCEMENT-driven: a rule is SAFETY only when hard.
        self.assertEqual(P.precedence_class(mk("k", "v", kind=I.RULE, enforcement=I.HARD)),
                         P.SAFETY)
        self.assertEqual(P.precedence_class(mk("k", "v", kind=I.GUARDRAIL)), P.SAFETY)  # →hard
        # an advisory (default) or soft rule is a preference, as is every fact
        self.assertEqual(P.precedence_class(mk("k", "v", kind=I.RULE)), P.PREFERENCE)
        self.assertEqual(P.precedence_class(mk("k", "v", kind=I.RULE, enforcement=I.SOFT)),
                         P.PREFERENCE)
        for kind in (I.BEST_PRACTICE, I.CONTEXT, I.REFERENCE, ""):
            self.assertEqual(P.precedence_class(mk("k", "v", kind=kind)), P.PREFERENCE)


# ============================================================ precedence: narrowest-wins
class TestNarrowestWins(unittest.TestCase):
    def test_narrower_scope_overrides_broader_scalar(self):
        items = [mk("db", "postgres", S.TEAM, "T"), mk("db", "sqlite", S.PROJECT, "P")]
        res = P.resolve(items)
        entry = res.get("db")
        self.assertEqual(entry.value, "sqlite")
        self.assertEqual(entry.scope_level, S.PROJECT)      # narrowest wins

    def test_full_chain_narrowest_wins(self):
        items = [mk("x", "g", S.GLOBAL), mk("x", "t", S.TEAM, "T"),
                 mk("x", "p", S.PROJECT, "P"), mk("x", "me", S.PERSONAL, "U")]
        self.assertEqual(P.resolve(items).get("x").value, "me")


# ============================================================ precedence: pin floor
class TestPinFloor(unittest.TestCase):
    def test_broader_pin_cannot_be_overridden_by_narrower(self):
        items = [mk("lang", "python", S.GLOBAL, pin=True),
                 mk("lang", "ruby", S.PROJECT, "P")]        # narrower, non-pin
        entry = P.resolve(items).get("lang")
        self.assertEqual(entry.value, "python")             # floor holds
        self.assertTrue(entry.pinned)
        self.assertEqual(entry.scope_level, S.GLOBAL)

    def test_broadest_pin_wins_among_several(self):
        items = [mk("lang", "python", S.GLOBAL, pin=True),
                 mk("lang", "rust", S.TEAM, "T", pin=True),   # narrower pin
                 mk("lang", "ruby", S.PROJECT, "P")]
        self.assertEqual(P.resolve(items).get("lang").value, "python")   # broadest pin

    def test_pin_beats_higher_priority_nonpin_at_same_depth(self):
        # deny-overrides: the restrictive (pinned) floor beats even a higher-priority non-pin
        items = [mk("k", "loose", S.TEAM, "T", priority=99, id="a"),
                 mk("k", "floor", S.TEAM, "T", pin=True, priority=1, id="b")]
        entry = P.resolve(items).get("k")
        self.assertEqual(entry.value, "floor")
        self.assertTrue(entry.pinned)


# ============================================================ precedence: object key-by-key merge
class TestObjectMerge(unittest.TestCase):
    def test_objects_merge_key_by_key_narrower_overrides(self):
        items = [mk("cfg", {"a": 1, "b": 2}, S.TEAM, "T"),
                 mk("cfg", {"b": 3, "c": 4}, S.PROJECT, "P")]
        entry = P.resolve(items).get("cfg")
        self.assertEqual(entry.value, {"a": 1, "b": 3, "c": 4})   # broader 'a' retained
        self.assertTrue(entry.merged)

    def test_object_merge_recurses(self):
        items = [mk("cfg", {"x": {"p": 1, "q": 2}}, S.GLOBAL),
                 mk("cfg", {"x": {"q": 9, "r": 3}}, S.PROJECT, "P")]
        self.assertEqual(P.resolve(items).get("cfg").value,
                         {"x": {"p": 1, "q": 9, "r": 3}})

    def test_scalar_overrides_wholesale(self):
        items = [mk("v", {"a": 1}, S.TEAM, "T"), mk("v", "scalar", S.PROJECT, "P")]
        self.assertEqual(P.resolve(items).get("v").value, "scalar")


# ============================================================ precedence: safety most-restrictive
class TestSafetyMostRestrictive(unittest.TestCase):
    def test_hard_rule_is_authoritative_at_any_depth(self):
        for level in (S.GLOBAL, S.TEAM, S.PROJECT, S.CATEGORY, S.PERSONAL):
            res = P.resolve([mk("secrets", "no plaintext", level, "x", kind=I.RULE,
                                enforcement=I.HARD)])
            self.assertTrue(res.is_authoritative("secrets"))
            self.assertTrue(res.get("secrets").authoritative)

    def test_narrower_scope_cannot_loosen_a_hard_rule(self):
        # a hard rule at team + a narrower (personal) preference for the same key → hard wins
        items = [mk("net", "deny egress", S.TEAM, "T", kind=I.GUARDRAIL),
                 mk("net", "allow egress", S.PERSONAL, "U", kind=I.CONTEXT)]
        entry = P.resolve(items).get("net")
        self.assertTrue(entry.authoritative)
        self.assertEqual(entry.value, "deny egress")        # safety, not the narrower preference

    def test_every_hard_item_is_retained_marked_authoritative(self):
        items = [mk("r", "a", S.GLOBAL, kind=I.RULE, enforcement=I.HARD, id="g"),
                 mk("r", "b", S.PROJECT, "P", kind=I.RULE, enforcement=I.HARD, id="p")]
        res = P.resolve(items)
        self.assertEqual({e.key for e in res.safety_items}, {"r"})
        self.assertEqual(len(res.safety_items), 2)          # nothing dropped
        self.assertTrue(all(e.authoritative for e in res.safety_items))


# ============================================================ precedence: tiebreak ladder
class TestTiebreak(unittest.TestCase):
    def test_higher_priority_wins_at_same_depth(self):
        items = [mk("x", "lo", S.TEAM, "T", priority=1, id="a"),
                 mk("x", "hi", S.TEAM, "T", priority=5, id="b")]
        self.assertEqual(P.resolve(items).get("x").value, "hi")

    def test_equal_priority_stable_by_smallest_id(self):
        items = [mk("x", "B", S.TEAM, "T", priority=0, id="b"),
                 mk("x", "A", S.TEAM, "T", priority=0, id="a")]
        self.assertEqual(P.resolve(items).get("x").value, "A")   # smallest id → deterministic

    def test_safety_representative_by_priority_then_id(self):
        items = [mk("r", "lo", S.TEAM, "T", kind=I.RULE, enforcement=I.HARD, priority=1, id="a"),
                 mk("r", "hi", S.PROJECT, "P", kind=I.RULE, enforcement=I.HARD, priority=9,
                    id="b")]
        self.assertEqual(P.resolve(items).safety["r"].value, "hi")


# ============================================================ precedence: determinism
class TestDeterminism(unittest.TestCase):
    def _mixed(self):
        return [
            mk("db", "postgres", S.TEAM, "T", id="db-t"),
            mk("db", "sqlite", S.PROJECT, "P", id="db-p"),
            mk("lang", "python", S.GLOBAL, pin=True, id="lang-g"),
            mk("lang", "ruby", S.PROJECT, "P", id="lang-p"),
            mk("cfg", {"a": 1}, S.GLOBAL, id="cfg-g"),
            mk("cfg", {"b": 2}, S.PROJECT, "P", id="cfg-p"),
            mk("secrets", "no plaintext", S.TEAM, "T", kind=I.RULE, enforcement=I.HARD,
               id="sec-t"),
        ]

    def test_same_input_same_output_regardless_of_order(self):
        baseline = norm(P.resolve(self._mixed()))
        # exhaustive over many permutations of the input (order must never matter)
        for perm in itertools.islice(itertools.permutations(self._mixed()), 0, 120):
            self.assertEqual(norm(P.resolve(list(perm))), baseline)


# ============================================================ store: union read wiring
class TestStoreScopedRecall(unittest.TestCase):
    def _store(self):
        d = tempfile.mkdtemp()
        return MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))

    def _seed(self, store):
        for it in [mk("g", "gv", S.GLOBAL),
                   mk("p", "pv", S.PROJECT, "P"),
                   mk("q", "qv", S.PROJECT, "OTHER"),
                   mk("me", "mv", S.PERSONAL, "U")]:
            store.remember(it, assume_yes=True)

    def test_no_context_is_byte_identical_to_all_active(self):
        store = self._store()
        self._seed(store)
        self.assertIsNone(store.scope_context)
        # scoped_active with no context == all_active (no filtering at all)
        self.assertEqual([i.subject for i in store.scoped_active()],
                         [i.subject for i in store.all_active()])

    def test_recall_returns_union_across_the_path(self):
        store = self._store()
        self._seed(store)
        store.scope_context = S.ScopeContext(project="P", user="U")
        got = {i.subject for i in store.scoped_active()}
        self.assertEqual(got, {"g", "p", "me"})     # global + project P + personal; NOT 'q'

    def test_recall_by_subject_respects_scope(self):
        store = self._store()
        self._seed(store)
        store.scope_context = S.ScopeContext(project="P", user="U")
        self.assertEqual([i.value for i in store.recall("q")], [])   # off-path project hidden
        self.assertEqual([i.value for i in store.recall("p")], ["pv"])

    def test_recall_relevant_is_scope_filtered(self):
        store = self._store()
        self._seed(store)
        store.scope_context = S.ScopeContext(project="P", user="U")
        subjects = {h.item.subject for h in store.recall_relevant("v", top_k=10)}
        self.assertNotIn("q", subjects)
        self.assertTrue({"g", "p", "me"} <= subjects)


# ============================================================ local-mode regression (byte-identical)
class TestLocalRegression(unittest.TestCase):
    def test_local_recall_matches_a_pre_scope_baseline(self):
        # simulate a personal-only repo: write items with the default (personal) scope, then
        # prove recall is identical whether we (a) don't filter, or (b) filter by personal-only.
        d = tempfile.mkdtemp()
        store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
        for i in range(5):
            store.remember(MemoryItem.create(f"k{i}", f"v{i}"), assume_yes=True)
        unfiltered = [i.to_dict() for i in store.all_active()]
        filtered = [i.to_dict() for i in
                    S.union_read(store.all_active(), S.personal_context())]
        self.assertEqual(unfiltered, filtered)      # personal-only path drops nothing


# ============================================================ schema v2→v3 migration
_SCOPE_COLS = ("scope_level", "scope_id", "pin", "priority")


class _UndefinedColumn(Exception):
    sqlstate = "42703"          # a PRE-D2 artifact has no `min_supported` column


class _FakePg:
    """A tiny PG stand-in that models tables→columns + the schema-version rows, enough to prove
    the provision DDL is idempotent and bumps the version. Understands CREATE TABLE / ALTER ADD
    COLUMN [IF NOT EXISTS] / the version INSERT + SELECT."""

    def __init__(self):
        self.tables = {}                # name -> set(columns)
        self.versions = {}              # D2 — version -> min_supported (the RANGE artifact)

    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

    def seed_table(self, name, cols):
        self.tables[name] = set(cols)

    def execute(self, sql, params=None):
        h = " ".join(sql.split())
        m = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*)\)$", h, re.I)
        if m:
            name = m.group(1)
            if name not in self.tables:
                cols = {c.strip().split()[0] for c in m.group(2).split(",")}
                self.tables[name] = cols
            return self._Cur([])
        m = re.match(r"ALTER TABLE (\w+) ADD COLUMN(?: IF NOT EXISTS)? (\w+)", h, re.I)
        if m:
            self.tables.setdefault(m.group(1), set()).add(m.group(2))
            return self._Cur([])
        # D2 — the artifact carries a RANGE: (version, min_supported).
        m = re.match(r"INSERT INTO mokata_schema_version \(version, min_supported\)"
                     r" VALUES \((\d+), (\d+)\)", h, re.I)
        if m:
            self.versions[int(m.group(1))] = int(m.group(2))   # ON CONFLICT DO UPDATE the floor
            return self._Cur([])
        if h.upper().startswith("SELECT VERSION, MIN_SUPPORTED FROM MOKATA_SCHEMA_VERSION"):
            if not self.versions:
                return self._Cur([])
            if "min_supported" not in self.tables.get("mokata_schema_version", set()):
                raise _UndefinedColumn("column \"min_supported\" does not exist")
            top = max(self.versions)
            return self._Cur([(top, self.versions[top])])
        if h.upper().startswith("SELECT VERSION FROM MOKATA_SCHEMA_VERSION"):
            return self._Cur([(max(self.versions),)] if self.versions else [])
        return self._Cur([])

    def close(self):
        pass


class TestSchemaMigration(unittest.TestCase):
    def test_version_is_bumped_to_v3(self):
        self.assertEqual(teamdb.TEAM_SCHEMA_VERSION, 3)

    def test_provision_sql_adds_idempotent_scope_columns(self):
        sql = " || ".join(teamdb.provision_sql())
        for col in _SCOPE_COLS:
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {col}", sql)
        # D2 — the version row is now the (current, min_supported) RANGE artifact.
        self.assertIn(f"VALUES ({teamdb.TEAM_SCHEMA_VERSION}, "
                      f"{teamdb.TEAM_SCHEMA_MIN_SUPPORTED})", sql)

    def _apply(self, pg):
        for stmt in teamdb.provision_sql():
            pg.execute(stmt)

    def test_fresh_provision_creates_scope_columns_and_v3(self):
        pg = _FakePg()
        self._apply(pg)
        self.assertTrue(set(_SCOPE_COLS) <= pg.tables[teamdb.MEMORY_TABLE])
        self.assertEqual(pg.execute(teamdb._VERSION_SQL).fetchone()[0], 3)

    def test_v2_to_v3_migration_adds_columns_and_bumps_version(self):
        pg = _FakePg()
        # a v2 memory table (no scope columns) + version row = 2
        pg.seed_table(teamdb.MEMORY_TABLE,
                      ["id", "mtype", "subject", "status", "doc", "seq", "project",
                       "revision", "updated_at"])
        pg.versions[2] = 2
        self._apply(pg)
        self.assertTrue(set(_SCOPE_COLS) <= pg.tables[teamdb.MEMORY_TABLE])
        self.assertEqual(pg.execute(teamdb._VERSION_SQL).fetchone()[0], 3)  # 2 → 3

    def test_provision_is_idempotent_on_rerun(self):
        pg = _FakePg()
        self._apply(pg)
        cols_after_first = set(pg.tables[teamdb.MEMORY_TABLE])
        self._apply(pg)                              # re-run team init
        self.assertEqual(pg.tables[teamdb.MEMORY_TABLE], cols_after_first)
        self.assertEqual(pg.versions, {3: teamdb.TEAM_SCHEMA_MIN_SUPPORTED})  # one row

    def test_the_scope_columns_have_ONE_definition_in_the_init_path(self):
        # D1 — `PostgresBackend` used to hand-mirror this DDL and re-run it on every connect.
        # The mirror is deleted; `teamdb.provision_sql` (team init) is the single source of truth.
        sql = " || ".join(teamdb.provision_sql())
        for col in _SCOPE_COLS:
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {col}", sql)
        self.assertFalse(hasattr(PostgresBackend, "_setup_sql"))

    def test_live_pg_leg_when_dsn_present(self):
        dsn = os.environ.get("MOKATA_PG_DSN")
        if not dsn:
            self.skipTest("no live $MOKATA_PG_DSN — mock legs cover the migration")
        res = teamdb.provision(dsn)                  # idempotent; raises on failure
        self.assertEqual(res.version, 3)
        teamdb.provision(dsn)                        # re-run must be clean


# ============================================================ SQLite backend gains the fields
class TestSqliteScopeColumns(unittest.TestCase):
    def test_fresh_db_has_scope_columns(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "m.db")
        SQLiteBackend(path)
        cols = self._cols(path)
        self.assertTrue(set(_SCOPE_COLS) <= cols)

    def test_pre_s6_db_is_migrated_in_place(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "m.db")
        # an OLD (pre-TM.S6) memory table without scope columns + a row
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE memory (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT "
                     "UNIQUE, mtype TEXT, subject TEXT, status TEXT, doc TEXT)")
        conn.execute("INSERT INTO memory (id, mtype, subject, status, doc) "
                     "VALUES ('x','persistent','k','active','{}')")
        conn.commit()
        conn.close()
        backend = SQLiteBackend(path)                # constructing migrates in place
        self.assertTrue(set(_SCOPE_COLS) <= self._cols(path))
        # existing rows survive + a real item round-trips carrying its scope (via doc JSON)
        backend.put(mk("newk", "v", S.PROJECT, "P"))
        got = backend.get("newk-project-P")
        self.assertEqual(got.scope_level, S.PROJECT)
        self.assertEqual(got.scope_id, "P")

    def test_migration_is_idempotent(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "m.db")
        SQLiteBackend(path)
        SQLiteBackend(path)                          # second open must not error
        self.assertTrue(set(_SCOPE_COLS) <= self._cols(path))

    @staticmethod
    def _cols(path):
        conn = sqlite3.connect(path)
        try:
            return {r[1] for r in conn.execute("PRAGMA table_info(memory)").fetchall()}
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
