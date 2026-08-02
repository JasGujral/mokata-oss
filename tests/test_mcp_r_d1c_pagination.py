"""MCP-R.D1c — pagination on the list tools: a bounded default + an honest cursor.

Before D1c the list tools returned their WHOLE list in one call, and `audit` was the poster child:
`limit=0` was its DEFAULT, i.e. "return the entire append-only ledger" (tools_read.py:135). On a
long-lived repo that is an unbounded token payload in a single read (P14), and the client had no way
to know more existed (P16). D1c adds `limit`/`offset` inputs + `total`/`has_more`/`next_offset`
outputs to the eight list tools, all routed through ONE shared helper (`mcp.pagination.paginate`).

GROUNDED ordering (verified from code, doc 88 §D1):
  * `AuditLedger.entries()` (govern/ledger.py:276) reads the JSONL in FILE order → OLDEST-FIRST
    (append-only, `seq` ascending). Pre-D1c `limit>0` took `entries[-limit:]` — the most-recent N,
    still chronological WITHIN the slice. D1c PRESERVES that: `audit` pages from the NEWEST END
    (`from_end=True`), so `offset=0` is the most recent window and paging forward walks BACK in
    time, each page chronological inside itself. The order is never silently flipped.
  * The other seven page from the START of their own natural order (sessions/windows/bundles/vault
    entries name-sorted, vault+stacks hits rank-sorted best-first) — offset-from-start preserves it.

`count` means the PAGE length (one meaning, everywhere); `total` is the FULL length.

TOTAL honesty (verified from code): all eight sources are MATERIALIZED in-memory lists —
`AuditLedger.entries()` (list), `team_audit_view().entries` (`log.read()` → List[dict]),
`progress.list_sessions` / `session_registry.list_sessions` / `session_bundle.
list_session_bundles_across` / `vault.vault_list|vault_search` / `stacks.list_stacks|search_stacks`
(all return `List[...]`; `stacks.load_index` reads a LOCAL bundled/dir index.json — there is no
remote streamed catalog). So `total` is genuinely KNOWN for all eight and is a real `len()`. The
helper still carries an honest UNKNOWN path (`total=None`) for a future streamed source, and it is
guarded here directly — `total` is null, never a fabricated number (P16).

These guards:
  HELPER          : slice math + cursor at first/middle/last page, both orientations, limit=0,
                    unknown total, defensive negatives.
  PER TOOL        : each of the eight slices correctly and reports total/has_more/next_offset.
  AUDIT DEFAULT   : no `limit` arg → bounded (≤50) with has_more; `limit=0` stays an EXPLICIT
                    all-opt-out (opt-in only, never the default).
  ORDERING        : audit offset paging walks back in time with no gaps and no dupes.
  SHARED MECHANISM: the slice/cursor math lives in exactly ONE place (source scan).
  COMPOSE         : pagination + D1b response_format compose with no interaction bug.
  SCHEMA          : limit/offset are the ONLY inputSchema change, on exactly the eight.

Secret-safety: N/A — pagination is pure SLICING of a list the tool already returned. It adds no new
data source and no arg that could carry a secret; it can only ever return FEWER entries than before,
so it opens no leak surface (the D0 served-path secret guard is unaffected).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import mcp_server as M
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.mcp import pagination as PG
from mokata.mcp import response_format as RF
from mokata.mcp import server as MS
from mokata.mcp import tools_read as TR
from mokata.mcp.registry import TOOLS

# The eight list tools D1c touches — the schema-diff contract: exactly these gain limit/offset.
PAGINATED = {"audit", "sessions", "session_windows", "session_list",
             "vault_list", "vault_search", "stacks_list", "stacks_search"}


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _seed_ledger(surface, n):
    """n REAL ledger entries, appended oldest-first (the grounded on-disk order)."""
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    for i in range(n):
        led.record("phase", phase=f"p{i:02d}")
    return led


def _code_without_docstrings(module) -> str:
    """`module`'s source with every docstring removed. The shared-mechanism scan must look at the
    CODE only: the tool docstrings legitimately NAME `has_more`/`next_offset` (they document the
    paged result shape to the agent) — that is documentation, not hand-rolled cursor math."""
    import ast

    class _Strip(ast.NodeTransformer):
        def _drop(self, node):
            self.generic_visit(node)
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
            return node

        visit_Module = visit_ClassDef = visit_FunctionDef = _drop
        visit_AsyncFunctionDef = _drop

    return ast.unparse(ast.fix_missing_locations(_Strip().visit(
        ast.parse(inspect.getsource(module)))))


# ======================================================================================
# HELPER — the one place the slice/cursor math lives
# ======================================================================================

class TestHelper(unittest.TestCase):

    def test_mcp_r_d1c_helper_pages_from_start(self):
        xs = list(range(10))
        first = PG.paginate(xs, key="items", limit=4, offset=0)
        self.assertEqual(first, {"count": 4, "items": [0, 1, 2, 3], "total": 10,
                                 "has_more": True, "next_offset": 4})
        middle = PG.paginate(xs, key="items", limit=4, offset=4)
        self.assertEqual(middle["items"], [4, 5, 6, 7])
        self.assertEqual((middle["has_more"], middle["next_offset"]), (True, 8))
        last = PG.paginate(xs, key="items", limit=4, offset=8)
        self.assertEqual(last, {"count": 2, "items": [8, 9], "total": 10,
                                "has_more": False, "next_offset": None})

    def test_mcp_r_d1c_helper_pages_from_end(self):
        # audit's orientation: offset 0 is the NEWEST window; each page stays chronological.
        xs = list(range(10))
        first = PG.paginate(xs, key="entries", limit=4, offset=0, from_end=True)
        self.assertEqual(first, {"count": 4, "entries": [6, 7, 8, 9], "total": 10,
                                 "has_more": True, "next_offset": 4})
        middle = PG.paginate(xs, key="entries", limit=4, offset=4, from_end=True)
        self.assertEqual(middle["entries"], [2, 3, 4, 5])
        last = PG.paginate(xs, key="entries", limit=4, offset=8, from_end=True)
        self.assertEqual(last, {"count": 2, "entries": [0, 1], "total": 10,
                                "has_more": False, "next_offset": None})

    def test_mcp_r_d1c_helper_limit_zero_is_the_explicit_all_optout(self):
        xs = list(range(10))
        allp = PG.paginate(xs, key="items", limit=PG.ALL, offset=0)
        self.assertEqual(allp["items"], xs)
        self.assertEqual((allp["count"], allp["total"]), (10, 10))
        self.assertEqual((allp["has_more"], allp["next_offset"]), (False, None))

    def test_mcp_r_d1c_helper_offset_past_the_end_is_an_honest_empty_page(self):
        for from_end in (False, True):
            with self.subTest(from_end=from_end):
                p = PG.paginate(list(range(3)), key="items", limit=2, offset=99,
                                from_end=from_end)
                self.assertEqual(p["items"], [])
                self.assertEqual((p["count"], p["total"]), (0, 3))
                self.assertEqual((p["has_more"], p["next_offset"]), (False, None))

    def test_mcp_r_d1c_helper_negatives_fall_back_to_the_bounded_default(self):
        # Loud typed validation is D1d; here a negative must never become an UNBOUNDED payload.
        xs = list(range(200))
        p = PG.paginate(xs, key="items", limit=-1, offset=-5)
        self.assertEqual(p["count"], PG.DEFAULT_PAGE_LIMIT)
        self.assertEqual(p["items"], xs[:PG.DEFAULT_PAGE_LIMIT])

    def test_mcp_r_d1c_unknown_total_is_null_never_faked(self):
        # A source whose full length is genuinely unknown: `total` is null and `has_more` is still
        # correct — derived from whether a FULL page came back. No fabricated number (P16).
        full = PG.paginate([1, 2, 3], key="items", limit=3, offset=0, total=PG.UNKNOWN_TOTAL)
        self.assertIsNone(full["total"])
        self.assertEqual((full["count"], full["has_more"], full["next_offset"]), (3, True, 3))
        short = PG.paginate([1, 2], key="items", limit=3, offset=6, total=PG.UNKNOWN_TOTAL)
        self.assertIsNone(short["total"])
        self.assertEqual((short["count"], short["has_more"], short["next_offset"]),
                         (2, False, None))

    def test_mcp_r_d1c_helper_key_order_is_count_then_list(self):
        # The pre-D1c leading shape ({count, <list>}) is preserved; the cursor fields follow.
        p = PG.paginate([1, 2], key="entries", limit=1, offset=0)
        self.assertEqual(list(p), ["count", "entries", "total", "has_more", "next_offset"])


# ======================================================================================
# AUDIT — the poster child: real ledger, bounded default, preserved newest-end orientation
# ======================================================================================

class TestAuditPaginates(unittest.TestCase):

    def test_mcp_r_d1c_audit_paginates(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_ledger(surface, 12)
            total = len(AuditLedger.from_mokata_dir(surface.mokata_dir).entries())

            first = M.audit(path=d, limit=4, offset=0)
            self.assertEqual(first["count"], 4)
            self.assertEqual(first["total"], total)
            self.assertTrue(first["has_more"])
            self.assertEqual(first["next_offset"], 4)

            middle = M.audit(path=d, limit=4, offset=4)
            self.assertEqual((middle["count"], middle["has_more"]), (4, True))
            self.assertEqual(middle["next_offset"], 8)

            last = M.audit(path=d, limit=4, offset=total - 4)
            self.assertEqual(last["count"], 4)
            self.assertFalse(last["has_more"])
            self.assertIsNone(last["next_offset"])

    def test_mcp_r_d1c_audit_default_bounded(self):
        # No `limit` arg → the bounded default, NOT the whole ledger (the point of D1c).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_ledger(surface, PG.DEFAULT_PAGE_LIMIT + 20)
            total = len(AuditLedger.from_mokata_dir(surface.mokata_dir).entries())
            self.assertGreater(total, PG.DEFAULT_PAGE_LIMIT)

            default = M.audit(path=d)
            self.assertEqual(default["count"], PG.DEFAULT_PAGE_LIMIT)
            self.assertLessEqual(len(default["entries"]), PG.DEFAULT_PAGE_LIMIT)
            self.assertEqual(default["total"], total)
            self.assertTrue(default["has_more"])          # the client KNOWS there is more (P16)
            self.assertEqual(default["next_offset"], PG.DEFAULT_PAGE_LIMIT)

            # …and the explicit all-opt-out is still available — OPT-IN only, never the default.
            everything = M.audit(path=d, limit=PG.ALL)
            self.assertEqual(everything["count"], total)
            self.assertEqual(len(everything["entries"]), total)
            self.assertFalse(everything["has_more"])
            self.assertIsNone(everything["next_offset"])

    def test_mcp_r_d1c_audit_offset_order(self):
        # The grounded orientation: offset walks BACK from the newest end; each page is
        # chronological inside itself; two consecutive pages have no gaps and no dupes.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_ledger(surface, 12)
            entries = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()

            p1 = M.audit(path=d, limit=4, offset=0)["entries"]
            p2 = M.audit(path=d, limit=4, offset=4)["entries"]
            # page 1 is the newest 4 (byte-identical to the pre-D1c `entries[-4:]` tail) …
            self.assertEqual(p1, entries[-4:])
            # … page 2 is the 4 before those, so chronologically page2 PRECEDES page1 …
            self.assertEqual(p2 + p1, entries[-8:])
            # … and no entry is repeated across the two pages.
            seqs1 = [e["seq"] for e in p1]
            seqs2 = [e["seq"] for e in p2]
            self.assertEqual(seqs1, sorted(seqs1))               # chronological within the page
            self.assertEqual(set(seqs1) & set(seqs2), set())     # no dupes
            self.assertEqual(max(seqs2) + 1, min(seqs1))         # no gap

    def test_mcp_r_d1c_audit_team_paginates(self):
        # The team branch (tools_read.py:147) gets the SAME treatment — same helper, same fields.
        from mokata import team_audit as TA
        rows = [{"actor": "alice", "kind": "phase", "phase": f"p{i}", "at": "2026-07-20"}
                for i in range(9)]
        view = TA.TeamAuditView(True, entries=rows, actors=["alice"], message="9 entries")
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            real = TA.team_audit_view
            TA.team_audit_view = lambda *a, **k: view
            try:
                first = M.audit(path=d, team=True, limit=4, offset=0)
                last = M.audit(path=d, team=True, limit=4, offset=8)
            finally:
                TA.team_audit_view = real
        self.assertEqual(first["count"], 4)                      # count is the PAGE
        self.assertEqual(first["total"], 9)                      # total is the FULL length
        self.assertEqual(first["entries"], rows[-4:])            # newest-end, like the local branch
        self.assertEqual((first["has_more"], first["next_offset"]), (True, 4))
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (1, False, None))
        self.assertTrue(last["available"])                       # the pre-D1c keys survive
        self.assertEqual(last["actors"], ["alice"])


# ======================================================================================
# THE OTHER SEVEN — each slices correctly and reports an honest cursor
# ======================================================================================

class TestSessionsPaginate(unittest.TestCase):

    def test_mcp_r_d1c_sessions_paginates(self):
        from mokata.progress import CHECKPOINT_PREFIX
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            for i in range(5):
                surface.state.write(f"{CHECKPOINT_PREFIX}run{i}", {"passed": []})
            first = M.sessions(path=d, limit=2, offset=0)
            self.assertEqual(first["count"], 2)
            self.assertEqual(first["total"], 5)
            self.assertEqual([s["run_id"] for s in first["sessions"]], ["run0", "run1"])
            self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
            middle = M.sessions(path=d, limit=2, offset=2)
            self.assertEqual([s["run_id"] for s in middle["sessions"]], ["run2", "run3"])
            last = M.sessions(path=d, limit=2, offset=4)
            self.assertEqual([s["run_id"] for s in last["sessions"]], ["run4"])
            self.assertEqual((last["count"], last["has_more"], last["next_offset"]),
                             (1, False, None))


# PAGE-SELFTOUCH — `session_windows` is a READ that performs registry UPKEEP: it calls `SR.touch`
# to self-register the CALLING window (`mcp/tools_read.py:491-508`, deliberate and documented).
# So this test's own process is a SIXTH row on top of the five seeded below, and `touch` restamps
# that row's `last_seen` to NOW on every call (`session_registry.py:112`; `started_at` is
# explicitly PRESERVED at `:109`, and `pid`/`repo_root`/`phase`/`scope` are stable — `last_seen` is
# the ONLY field a read mutates).
#
# Comparing a page read at T against the full listing read at T+δ therefore compares that one row's
# `last_seen` ACROSS A CLOCK TICK, and any second boundary between the two reads makes them differ.
# That is what reddened `ubuntu · jsonschema=present` on an otherwise-green proof run.
#
# The fix is not to loosen the claim — it is to assert the claim the tool actually makes. Pagination
# is SLICING: no gaps, no dupes, same order, same rows. It promises nothing about a field the read
# itself rewrites. So the comparison is made over everything EXCEPT that field.
_MUTATED_BY_READ = ("last_seen",)


def _sliceable(window):
    """A window projected to what SLICING is a claim about — every field except the ones the read
    itself mutates. Deliberately a REMOVE-list, not a keep-list: a new field is compared by default,
    so this can never quietly stop asserting something."""
    return {k: v for k, v in window.items() if k not in _MUTATED_BY_READ}


class TestSessionWindowsPaginate(unittest.TestCase):

    def test_the_projection_drops_only_the_field_the_read_mutates(self):
        # The guard on the guard: `_sliceable` exists to remove ONE volatile field, and a projection
        # that removed more (or everything) would turn the slice assertions below into a test that
        # passes on any listing at all. Pinned so the fix cannot decay into asserting nothing.
        window = {"session_id": "w0", "short_id": "w0", "started": "2026-07-20T00:00:00+00:00",
                  "last_seen": "2026-07-20T00:00:00+00:00", "alive": True, "phase": "spec",
                  "worktree": "main", "scope": "s0"}
        self.assertEqual(set(window) - set(_sliceable(window)), {"last_seen"})
        self.assertNotIn("last_seen", _sliceable(window))
        # identity + order + payload all survive the projection — it drops one field, not the row.
        self.assertEqual(_sliceable(window)["session_id"], "w0")
        self.assertGreaterEqual(len(_sliceable(window)), len(window) - 1)

    def test_mcp_r_d1c_session_windows_paginates(self):
        from mokata import session_registry as SR
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            rows = {f"w{i}": {"session_id": f"w{i}", "pid": os.getpid(),
                              "started_at": f"2026-07-20T00:00:0{i}+00:00",
                              "last_seen": f"2026-07-20T00:00:0{i}+00:00",
                              "repo_root": os.path.abspath(d), "phase": "spec",
                              "scope": f"s{i}"} for i in range(5)}
            surface.state.write(SR.SESSION_REGISTRY_KEY, {"sessions": rows})
            first = M.session_windows(path=d, limit=2, offset=0)
            total = first["total"]
            self.assertGreaterEqual(total, 5)
            self.assertEqual(first["count"], 2)
            self.assertEqual(len(first["windows"]), 2)
            self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
            last = M.session_windows(path=d, limit=2, offset=total - 2)
            self.assertEqual((last["count"], last["has_more"], last["next_offset"]),
                             (2, False, None))
            # the page is a real slice of the full listing — no gaps, no dupes. Compared on
            # `_sliceable` (see PAGE-SELFTOUCH above): this listing contains the READING process's
            # own window, whose `last_seen` this very call restamps, so byte-equality across two
            # reads is a clock race and not a pagination claim.
            everything = M.session_windows(path=d, limit=PG.ALL)["windows"]
            self.assertEqual([_sliceable(w) for w in everything[:2]],
                             [_sliceable(w) for w in first["windows"]])
            self.assertEqual([_sliceable(w) for w in everything[total - 2:]],
                             [_sliceable(w) for w in last["windows"]])
            # …and the ordered identity sequence is asserted in its own right, so a slice that
            # returned the right COUNT of the wrong rows cannot hide inside the projection.
            self.assertEqual([w["session_id"] for w in everything[:2]],
                             [w["session_id"] for w in first["windows"]])
            self.assertEqual([w["session_id"] for w in everything[total - 2:]],
                             [w["session_id"] for w in last["windows"]])
            self.assertEqual(len({w["session_id"] for w in everything}), total)   # no dupes


class TestSessionListPaginates(unittest.TestCase):

    def test_mcp_r_d1c_session_list_paginates(self):
        from mokata import session_bundle as SB

        class _Info:
            def __init__(self, i):
                self.tag = f"t{i}"
                self.author = "jas"
                self.created = "2026-07-20"
                self.source = "local"
                self.run_id = f"r{i}"
                self.resume_phase = "spec"
                self.done = 1
                self.total = 7
                self.transport = "local"

        infos = [_Info(i) for i in range(5)]
        real = SB.list_session_bundles_across
        SB.list_session_bundles_across = lambda *a, **k: infos
        try:
            with tempfile.TemporaryDirectory() as d:
                _repo(d)
                first = M.session_list(path=d, limit=2, offset=0)
                middle = M.session_list(path=d, limit=2, offset=2)
                last = M.session_list(path=d, limit=2, offset=4)
        finally:
            SB.list_session_bundles_across = real
        self.assertEqual((first["count"], first["total"]), (2, 5))
        self.assertEqual([b["tag"] for b in first["bundles"]], ["t0", "t1"])
        self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
        self.assertEqual([b["tag"] for b in middle["bundles"]], ["t2", "t3"])
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (1, False, None))


class TestVaultPaginate(unittest.TestCase):

    def _entries(self, n):
        from mokata.vault import VaultEntry
        return [VaultEntry(name=f"e{i}", kind="brainstorm", title=f"T{i}", author="jas",
                           source="local", content_hash=f"h{i}", created_at="2026-07-20",
                           updated_at="2026-07-20", version=1) for i in range(n)]

    def test_mcp_r_d1c_vault_list_paginates(self):
        from mokata import vault as V
        entries = self._entries(5)
        real = V.vault_list
        V.vault_list = lambda root: entries
        try:
            with tempfile.TemporaryDirectory() as d:
                _repo(d)
                first = M.vault_list(path=d, limit=2, offset=0)
                last = M.vault_list(path=d, limit=2, offset=4)
        finally:
            V.vault_list = real
        self.assertEqual((first["count"], first["total"]), (2, 5))
        self.assertEqual([e["name"] for e in first["entries"]], ["e0", "e1"])
        self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (1, False, None))

    def test_mcp_r_d1c_vault_search_paginates(self):
        from mokata import vault as V
        hits = [V.VaultHit(entry=e, score=1.0 - i / 10.0)
                for i, e in enumerate(self._entries(5))]
        real = V.vault_search
        V.vault_search = lambda root, query: hits
        try:
            with tempfile.TemporaryDirectory() as d:
                _repo(d)
                first = M.vault_search(path=d, query="x", limit=2, offset=0)
                last = M.vault_search(path=d, query="x", limit=2, offset=4)
        finally:
            V.vault_search = real
        self.assertEqual((first["count"], first["total"]), (2, 5))
        # rank order (best first) is preserved — offset walks DOWN the ranking
        self.assertEqual([h["name"] for h in first["hits"]], ["e0", "e1"])
        self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (1, False, None))


class TestStacksPaginate(unittest.TestCase):
    """Real catalogs — `stacks.load_index` reads a LOCAL index.json, so a 5-stack fixture is real."""

    def _catalog(self, d, n):
        import json as _json
        from mokata.stacks import INDEX_FILENAME, INDEX_KIND
        cat = os.path.join(d, "catalog")
        os.makedirs(cat, exist_ok=True)
        stacks = [{"name": f"s{i}", "framework": "python", "summary": "python web stack",
                   "tags": ["python"], "guardrails": [], "skills": []} for i in range(n)]
        with open(os.path.join(cat, INDEX_FILENAME), "w", encoding="utf-8") as fh:
            _json.dump({"kind": INDEX_KIND, "version": 1, "stacks": stacks}, fh)
        return cat

    def test_mcp_r_d1c_stacks_list_paginates(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            src = self._catalog(d, 5)
            first = M.stacks_list(path=d, source=src, limit=2, offset=0)
            middle = M.stacks_list(path=d, source=src, limit=2, offset=2)
            last = M.stacks_list(path=d, source=src, limit=2, offset=4)
        self.assertEqual((first["count"], first["total"]), (2, 5))
        self.assertEqual([s["name"] for s in first["stacks"]], ["s0", "s1"])
        self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
        self.assertEqual([s["name"] for s in middle["stacks"]], ["s2", "s3"])
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (1, False, None))
        self.assertEqual(last["status"], "ok")            # the pre-D1c keys survive
        self.assertIs(last["hosted"], False)

    def test_mcp_r_d1c_stacks_search_paginates(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            src = self._catalog(d, 5)
            first = M.stacks_search(path=d, query="python", source=src, limit=2, offset=0)
            last = M.stacks_search(path=d, query="python", source=src, limit=2, offset=4)
        self.assertEqual((first["count"], first["total"]), (2, 5))
        self.assertEqual((first["has_more"], first["next_offset"]), (True, 2))
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (1, False, None))


# ======================================================================================
# SHARED MECHANISM — the slice/cursor math lives in EXACTLY ONE place
# ======================================================================================

class TestSharedMechanism(unittest.TestCase):

    def test_mcp_r_d1c_single_pagination_site(self):
        code = _code_without_docstrings(TR)
        for needle in ("has_more", "next_offset", "[-limit:]", "[offset:", "offset +",
                       "offset:offset"):
            self.assertNotIn(needle, code,
                             f"tools_read hand-rolls the cursor math ({needle!r})")
        self.assertIn("paginate(", code)                      # …it defers to the helper
        # and the ONE site that computes the cursor is the helper itself
        helper = inspect.getsource(PG.paginate)
        self.assertIn("has_more", helper)
        self.assertIn("next_offset", helper)

    def test_mcp_r_d1c_new_tool_gets_pagination_for_free(self):
        def newtool(limit=PG.DEFAULT_PAGE_LIMIT, offset=0):
            return {"status": "ok", **PG.paginate(list(range(3)), key="rows",
                                                  limit=limit, offset=offset)}

        self.assertEqual(newtool(limit=2), {"status": "ok", "count": 2, "rows": [0, 1],
                                            "total": 3, "has_more": True, "next_offset": 2})


# ======================================================================================
# COMPOSE — pagination + D1b response_format, no interaction bug
# ======================================================================================

class TestComposesWithResponseFormat(unittest.TestCase):

    def test_mcp_r_d1c_pagination_with_response_format(self):
        # The eight paginated tools and the eight D1b response_format tools are DISJOINT sets today,
        # so composition is proven where it lives: on the two shared mechanisms. A tool that uses
        # BOTH pages identically under concise and detailed — the format toggle only adds/drops the
        # render, it never touches the page or the cursor.
        def both(limit=PG.DEFAULT_PAGE_LIMIT, offset=0, response_format="concise"):
            page = PG.paginate(list(range(10)), key="rows", limit=limit, offset=offset)
            return RF.apply_response_format(
                response_format, {**page, "block": RF.LazyRender(lambda: "RENDERED")})

        concise = both(limit=4, offset=4)
        detailed = both(limit=4, offset=4, response_format="detailed")
        self.assertNotIn("block", concise)                    # concise default still applies …
        self.assertEqual(detailed["block"], "RENDERED")       # … detailed still adds the render …
        for field in ("count", "rows", "total", "has_more", "next_offset"):
            self.assertEqual(concise[field], detailed[field])  # … and the PAGE is identical in both
        self.assertEqual(concise["rows"], [4, 5, 6, 7])
        self.assertEqual((concise["has_more"], concise["next_offset"]), (True, 8))


# ======================================================================================
# SCHEMA — limit/offset are the ONLY inputSchema change, on exactly the eight
# ======================================================================================

class TestSchema(unittest.TestCase):

    @unittest.skipUnless(MS.mcp_available(), "optional MCP SDK not installed")
    def test_mcp_r_d1c_schema_adds_only_limit_and_offset(self):
        import asyncio

        built = {t.name: t.inputSchema for t in asyncio.run(MS.build_server().list_tools())}

        # `offset` is D1c's fingerprint (audit already had `limit`): exactly the eight carry it.
        have_offset = {name for name, schema in built.items()
                       if "offset" in (schema.get("properties") or {})}
        self.assertEqual(have_offset, PAGINATED)

        fn_by_name = {s.name: s.fn for s in TOOLS}
        for name in PAGINATED:
            with self.subTest(tool=name):
                props = built[name].get("properties") or {}
                self.assertEqual(props["offset"].get("default"), 0)
                self.assertEqual(props["limit"].get("default"), PG.DEFAULT_PAGE_LIMIT)
                # the schema is exactly the signature — the diff is confined to limit/offset
                sig = set(inspect.signature(fn_by_name[name]).parameters)
                self.assertEqual(set(props), sig)

    @unittest.skipUnless(MS.mcp_available(), "optional MCP SDK not installed")
    def test_mcp_r_d1c_other_tools_byte_identical(self):
        import asyncio

        from mcp.server.fastmcp import FastMCP

        def _raw(fn, name):
            srv = FastMCP("parity")
            srv.add_tool(fn, name=name)
            return {t.name: t.inputSchema for t in asyncio.run(srv.list_tools())}[name]

        built = {t.name: t.inputSchema for t in asyncio.run(MS.build_server().list_tools())}
        untouched = [s for s in TOOLS if s.name not in PAGINATED]
        self.assertTrue(untouched)
        for spec in untouched:
            with self.subTest(tool=spec.name):
                self.assertEqual(built[spec.name], _raw(spec.fn, spec.name))
                props = built[spec.name].get("properties") or {}
                self.assertNotIn("offset", props)


# ======================================================================================
# BOUNDED BY DEFAULT — no list tool returns an unbounded payload without being asked (P14)
# ======================================================================================

class TestBoundedByDefault(unittest.TestCase):

    def test_mcp_r_d1c_every_list_tool_defaults_to_the_bounded_limit(self):
        fn_by_name = {s.name: s.fn for s in TOOLS}
        for name in sorted(PAGINATED):
            with self.subTest(tool=name):
                params = inspect.signature(fn_by_name[name]).parameters
                self.assertEqual(params["limit"].default, PG.DEFAULT_PAGE_LIMIT)
                self.assertEqual(params["offset"].default, 0)
        # …and the constant is a sane bound, not a disguised "everything".
        self.assertEqual(PG.DEFAULT_PAGE_LIMIT, 50)
        self.assertNotEqual(PG.DEFAULT_PAGE_LIMIT, PG.ALL)

    def test_mcp_r_d1c_audit_result_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_ledger(surface, 3)
            json.dumps(M.audit(path=d))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
