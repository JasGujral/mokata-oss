"""DB.S7c1 — K2: EDGE-AWARE HEALING. The four pins, each with the mutation that proves it.

K2 (doc 55:45) is three things. TWO are built here:

  (1) a proposal SHOWS THE SUBGRAPH IT REWRITES — the open relations a resolution re-projects;
  (2) CAS COVERS EDGES — the losing writer projects no edge, the winner's edges land with it.

The third — "contradiction detection queries `contradicts` structure instead of full scans" — is
NOT built and is not silently missing: `contradicts` has no producer (`edges.py:80`), so there is
nothing to query. `TestTheUnbuildableThirdIsFiled` asserts the ABSENCE is deliberate, which is the
only way a not-built thing stays honest rather than becoming a quiet gap.

THE PINS, and what each one would let through if it were not here:

  P1  SURFACE-ONLY / PROPOSE-ONLY  — a detect run writes NOTHING. Without it, "healing" acquires a
      write path and P2 (human-gated durable writes) is decided by a detector.
  P2  ADDITIVE                     — the DB.S6 corpus with no edge context yields BYTE-IDENTICAL
      proposals, and `detect_issues` is unchanged. Without it, K2 is a rewrite wearing an
      extension's clothes and every DB.S6 contract is re-litigated.
  P3  NO L3 IMPORT                 — `healing.py` reaches nothing in the collab layer. Without it,
      the L2 domain module depends on the journal and the layering argument is prose only.
  P4  BOUNDED                      — the read rides DB.S7b's cap and REPORTS truncation. Without
      it, a bounded read reads as a complete one and a human resolves a conflict believing they
      have been shown every relation it touches.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.edges import ABOUT_CODE, DEPENDS_ON, SUPERSEDES, MemoryEdge
from mokata.memory.healing import (CROSS_WRITER, ConflictRecord, ConflictSubgraph,
                                   detect_cross_writer, detect_issues, prune_subgraph,
                                   render_proposal)
from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem


def _item(subject, value, item_id="x", **kw):
    return MemoryItem(subject=subject, value=value, id=item_id, mtype=PERSISTENT,
                      status=ACTIVE, **kw)


def _edge(src, dst, kind=DEPENDS_ON):
    return MemoryEdge(src_id=src, dst_id=dst, kind=kind, valid_from="2026-07-01T00:00:00+00:00")


# ===========================================================================================
# (1) THE PROPOSAL SHOWS THE SUBGRAPH IT REWRITES
# ===========================================================================================
class TestSubgraphIsWhatResolutionReprojects(unittest.TestCase):
    """"Rewrites" is literal. `edges.project_edges` maintains the projection keyed on `src_id`, so
    the set a resolution changes is the OPEN edges OUT of the conflicted item — one hop, forward."""

    def test_relations_named_in_rationale(self):
        sub = ConflictSubgraph(edges=(_edge("x", "a"), _edge("x", "b")))
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d",
                             local=_item("db host", "mine"), remote=_item("db host", "theirs"),
                             subgraph=sub)
        p = detect_cross_writer([rec])[0]
        self.assertIn("re-projects", p.rationale)
        self.assertIn("depends_on", p.rationale)

    def test_render_lists_each_relation(self):
        sub = ConflictSubgraph(edges=(_edge("x", "a"), _edge("x", "b", SUPERSEDES)))
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d",
                             local=_item("db host", "mine"), remote=_item("db host", "theirs"),
                             subgraph=sub)
        text = render_proposal(detect_cross_writer([rec])[0])
        self.assertIn("depends_on → a", text)
        self.assertIn("supersedes → b", text)

    def test_subgraph_precedes_the_choices(self):
        """Evidence precedes the decision. A human who reads "approve / discard / defer" first has
        already begun deciding, and the relations are what should inform that."""
        sub = ConflictSubgraph(edges=(_edge("x", "a"),))
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d",
                             local=_item("db host", "mine"), remote=_item("db host", "theirs"),
                             subgraph=sub)
        text = render_proposal(detect_cross_writer([rec])[0])
        # Anchored on the CHOICE line, not the bare word: the rationale already contains
        # "your approved write", so `index("approve")` would match the prose and pass vacuously.
        self.assertLess(text.index("depends_on → a"),
                        text.index("  approve — keep YOURS"))

    def test_empty_subgraph_renders_as_pre_k2(self):
        """The common case is an item with no relations. Announcing "0 relations" at every gate is
        noise that trains the reader to skim the line that matters when it is not zero."""
        rec_none = ConflictRecord(conflict_id="j1", key="x", detail="d",
                                  local=_item("db host", "mine"),
                                  remote=_item("db host", "theirs"))
        rec_empty = ConflictRecord(conflict_id="j1", key="x", detail="d",
                                   local=_item("db host", "mine"),
                                   remote=_item("db host", "theirs"),
                                   subgraph=ConflictSubgraph())
        self.assertEqual(render_proposal(detect_cross_writer([rec_none])[0]),
                         render_proposal(detect_cross_writer([rec_empty])[0]))

    def test_carries_given_subgraph_not_inline(self):
        """DB.S7b's rule, applied to the gate: the inline `depends_on` list would render a fluent,
        plausible sentence about relations the shared table may not hold. An item whose inline
        field and given subgraph DISAGREE must show the given one."""
        local = _item("db host", "mine", depends_on=["inline-only"])
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d", local=local,
                             remote=_item("db host", "theirs"),
                             subgraph=ConflictSubgraph(edges=(_edge("x", "from-the-table"),)))
        text = render_proposal(detect_cross_writer([rec])[0])
        self.assertIn("from-the-table", text)
        self.assertNotIn("inline-only", text)

    def test_no_subgraph_means_no_relations_claimed(self):
        """The other half, and the one that actually bites: when the store supplied NO subgraph,
        the proposal must claim NOTHING — not fall back to the item's inline `depends_on`. A
        fallback here is the most tempting possible bug, because it makes the feature look like it
        works on every backend; what it really does is show a human relations the SHARED table may
        never have held, at the moment they are deciding whether to overwrite it."""
        local = _item("db host", "mine", depends_on=["inline-only"], item_id="x")
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d", local=local,
                             remote=_item("db host", "theirs"))
        p = detect_cross_writer([rec])[0]
        self.assertIsNone(p.subgraph)
        self.assertNotIn("inline-only", render_proposal(p))


class TestScopePrunesTheSubgraph(unittest.TestCase):
    """An id in a rendered relation is a disclosure even when no content is read — DB.S7b's
    decision 4, at the gate instead of in the ranking."""

    def test_invisible_item_target_dropped(self):
        sub = prune_subgraph([_edge("x", "seen"), _edge("x", "hidden")], {"seen"}, 100)
        self.assertEqual(["seen"], [e.dst_id for e in sub.edges])

    def test_about_code_target_not_pruned(self):
        """`about_code` points at a code path, never a memory item. Pruning it against a set of
        item ids would delete every code anchor from the display while looking like a scope rule."""
        sub = prune_subgraph([_edge("x", "src/app.py", ABOUT_CODE)], {"other"}, 100)
        self.assertEqual(["src/app.py"], [e.dst_id for e in sub.edges])

    def test_no_scope_context_prunes_nothing(self):
        sub = prune_subgraph([_edge("x", "a"), _edge("x", "b")], None, 100)
        self.assertEqual(2, len(sub.edges))


# ===========================================================================================
# P4 — BOUNDED, AND THE BOUND REPORTS ITSELF
# ===========================================================================================
class TestP4Bounded(unittest.TestCase):

    def test_cap_is_db_s7b_constant(self):
        """One bounded-edge-read budget for the package. A second constant is how half a knob gets
        turned."""
        from mokata.memory.expansion import MAX_WALKED_EDGES
        import mokata.memory.store as store_mod
        src = open(store_mod.__file__, encoding="utf-8").read()
        self.assertIn("MAX_WALKED_EDGES", src)
        self.assertIsInstance(MAX_WALKED_EDGES, int)

    def test_truncation_is_counted(self):
        sub = prune_subgraph([_edge("x", f"d{i}") for i in range(10)], None, 4)
        self.assertEqual(4, len(sub.edges))
        self.assertEqual(6, sub.truncated)

    def test_truncation_is_said_out_loud(self):
        """A bounded read that trims silently reads as a complete one."""
        sub = prune_subgraph([_edge("x", f"d{i}") for i in range(10)], None, 4)
        self.assertIn("+6 more", sub.summary())
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d", local=_item("s", "mine"),
                             remote=_item("s", "theirs"), subgraph=sub)
        self.assertIn("not shown", render_proposal(detect_cross_writer([rec])[0]))

    def test_kept_set_is_deterministic(self):
        """Two runs (or two engines) that trim the same over-long set must trim it the same way,
        or the human sees a different subgraph each time they look."""
        edges = [_edge("x", f"d{i}") for i in range(10)]
        a = prune_subgraph(list(reversed(edges)), None, 4)
        b = prune_subgraph(edges, None, 4)
        self.assertEqual([e.dst_id for e in a.edges], [e.dst_id for e in b.edges])


# ===========================================================================================
# P2 — ADDITIVE
# ===========================================================================================
class TestP2Additive(unittest.TestCase):

    def test_detect_issues_is_unchanged(self):
        """The strongest form available without a git call: the function's own source must contain
        no reference to the K2 concepts. If K2 ever reaches into it, this fails."""
        import inspect
        src = inspect.getsource(detect_issues)
        for token in ("subgraph", "edge", "open_edges", "ConflictSubgraph"):
            self.assertNotIn(token, src,
                             f"detect_issues mentions '{token}' — K2 must not rewrite it")

    def test_no_edge_context_is_the_db_s6_proposal(self):
        local, remote = _item("db host", "mine"), _item("db host", "theirs")
        rec = ConflictRecord(conflict_id="j1", key="x", detail="remote revision advanced",
                             local=local, remote=remote, remote_revision=4)
        p = detect_cross_writer([rec])[0]
        self.assertEqual(CROSS_WRITER, p.kind)
        self.assertEqual("j1", p.conflict_id)
        self.assertEqual(4, p.remote_revision)
        self.assertIsNone(p.subgraph)
        self.assertEqual("a teammate changed this row while your approved write was waiting to "
                         "land (remote revision advanced)", p.rationale)

    def test_db_s6_corpus_is_byte_identical(self):
        """The DB.S6 arm's own inputs, run through the K2 build, must render exactly as before."""
        items = [_item("db host", "10.0.0.1", item_id="a"),
                 _item("db host", "10.0.0.2", item_id="b"),
                 _item("ttl", "v", item_id="c", expires_at="2020-01-01T00:00:00+00:00")]
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d",
                             local=_item("s", "mine"), remote=_item("s", "theirs"))
        before = [render_proposal(p) for p in
                  detect_issues(items, now="2026-07-01T00:00:00+00:00", conflicts=[rec])]
        rec.subgraph = ConflictSubgraph()          # empty == "nothing to say"
        after = [render_proposal(p) for p in
                 detect_issues(items, now="2026-07-01T00:00:00+00:00", conflicts=[rec])]
        self.assertEqual(before, after)

    def test_every_new_field_is_defaulted(self):
        """A DB.S6 construction site must build both dataclasses without knowing K2 exists."""
        rec = ConflictRecord(conflict_id="j", key="k", detail="d", local=_item("s", "v"))
        self.assertIsNone(rec.subgraph)
        from mokata.memory.healing import HealingProposal
        p = HealingProposal(kind=CROSS_WRITER, subject="s", mtype=PERSISTENT,
                            old=_item("s", "v"), new=None, rationale="r")
        self.assertIsNone(p.subgraph)


# ===========================================================================================
# P3 — NO L3 IMPORT
# ===========================================================================================
class TestP3NoL3Import(unittest.TestCase):

    def test_no_collab_layer_import(self):
        """DB.S6's pin, re-run against the K2 build — the import graph, not the prose."""
        import mokata.memory.healing as healing
        with open(healing.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        forbidden = [m for m in imported
                     if "team_journal" in m or "teamdb" in m or "ConflictView" in m]
        self.assertEqual([], forbidden, f"healing.py reached the collab layer: {forbidden}")

    def test_healing_opens_no_connection(self):
        """K2's edge context is ATTACHED by the store. If healing ever grew a backend call it
        would be an L2 module reaching for a connection, which is the same error one layer down."""
        import mokata.memory.healing as healing
        src = open(healing.__file__, encoding="utf-8").read()
        for token in ("open_edges(", "expand_from(", "_connect(", "psycopg", "sqlite3"):
            self.assertNotIn(token, src, f"healing.py performs its own read: {token}")


# ===========================================================================================
# THE UNBUILDABLE THIRD, FILED RATHER THAN QUIETLY MISSING
# ===========================================================================================
class TestTheUnbuildableThirdIsFiled(unittest.TestCase):

    def test_contradicts_has_no_producer(self):
        """The REASON K2's third part is unbuilt, asserted rather than asserted-about. The day a
        producer lands this fails, which is the signal to build the query arm."""
        from mokata.memory.edges import CONTRADICTS, WIRED_KINDS
        self.assertNotIn(CONTRADICTS, WIRED_KINDS,
                         "a `contradicts` producer exists now — K2's query arm is buildable; "
                         "see K2-CONTRADICTS-UNBUILDABLE in doc 84")

    def test_no_contradicts_edge_is_invented(self):
        """The failure mode this guards is not omission, it is over-delivery: writing `contradicts`
        edges from read-time detection to make the query arm buildable. That would make the
        detector a producer, and a producer on a READ path is a durable write nobody gated.

        Checked on the CODE, with docstrings and comments stripped — the first cut of this test
        matched the healing module's own prose explaining why the third is unbuildable, and a test
        that a doc comment can fail is a test that will be silenced rather than believed."""
        import mokata.memory.healing as healing
        import mokata.memory.store as store_mod
        for mod in (healing, store_mod):
            tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
            for node in ast.walk(tree):                    # drop every docstring
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    body = getattr(node, "body", [])
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        body.pop(0)
            code = ast.unparse(tree)                       # comments are already gone
            self.assertNotIn("CONTRADICTS", code,
                             f"{os.path.basename(mod.__file__)} writes/uses a contradicts edge")


# ===========================================================================================
# LIVE STORE — P1 (propose-only) and the real read, on a real team repo
# ===========================================================================================
def _team_repo(d):
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Surface.load(d)


def _plant_conflict(surface, *, key="m1", mine="mine", theirs="theirs", depends_on=()):
    """DB.S6's fixture: journal an approved write, plant a diverged remote, flush ⇒ a lost CAS."""
    from mokata import team_health, team_journal, teamdb
    from test_tm_s5_journal import _FakeMemPg
    doc = json.dumps(MemoryItem(subject=key, value=mine, id=key, mtype=PERSISTENT,
                                status=ACTIVE, depends_on=list(depends_on)).to_doc())
    team_journal.record_team_write(
        surface, op="memory_put", table=teamdb.MEMORY_TABLE, key=key,
        payload={"id": key, "mtype": PERSISTENT, "subject": key, "status": ACTIVE,
                 "doc": doc, "project": "p1"},
        ledger_id=5, project="p1", actor="alice", base_revision=1)
    pg = _FakeMemPg()
    pg.plant(key, json.dumps(MemoryItem(subject=key, value=theirs, id=key, mtype=PERSISTENT,
                                        status=ACTIVE).to_doc()), revision=2)
    team_journal.flush(surface,
                       health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                       connect=lambda *a, **k: pg)
    return pg


def _store(surface):
    from mokata.memory import MemoryStore
    return MemoryStore.from_surface(surface)


class TestP1ProposeOnly(unittest.TestCase):
    """P1 — a detect run writes NOTHING. Not "no memory rows": nothing, anywhere under the root.
    DB.S6 proved this for the conflict arm; K2 added a READ to that path and must not have added
    a write with it."""

    def _tree(self, root):
        out = {}
        for base, dirs, files in os.walk(root):
            for name in dirs:
                out[os.path.join(base, name)] = None
            for name in files:
                path = os.path.join(base, name)
                try:
                    with open(path, "rb") as fh:
                        out[path] = fh.read()
                except OSError:                      # pragma: no cover
                    out[path] = b"<unreadable>"
        return out

    def test_detection_changes_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface, depends_on=["dep-a"])
            store = _store(surface)
            store.detect_issues()                    # warm every lazy construction
            before = self._tree(d)
            for _ in range(3):
                props = [p for p in store.detect_issues() if p.kind == CROSS_WRITER]
                self.assertTrue(props)
            self.assertEqual(before, self._tree(d),
                             "the K2 edge read mutated the tree — detection must stay pure")

    def test_subgraph_read_creates_no_edges(self):
        """The read must not become a producer: asking "what relations does this hold" cannot be
        what materializes them."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface, depends_on=["dep-a"])
            store = _store(surface)
            rows = getattr(store.backend, "open_edges", lambda _i: [])("m1")
            store.detect_issues()
            store.detect_issues()
            after = getattr(store.backend, "open_edges", lambda _i: [])("m1")
            self.assertEqual(len(rows), len(after))


class TestLocalModeIsUntouched(unittest.TestCase):

    def test_zero_config_does_no_edge_read(self):
        """The conflict arm is empty in local mode, so the K2 read must never be reached — a
        zero-config user pays nothing for a team feature."""
        from mokata.config import Surface
        from mokata.init import init_repo
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            store = _store(Surface.load(d))
            calls = []
            real = store.backend.open_edges

            def _spy(item_id):
                calls.append(item_id)
                return real(item_id)

            store.backend.open_edges = _spy          # type: ignore[method-assign]
            self.assertEqual([], [p for p in store.detect_issues() if p.kind == CROSS_WRITER])
            self.assertEqual([], calls, "local mode performed a K2 edge read")


class TestDegradesLoudNotSilent(unittest.TestCase):
    """The subgraph is context; the CONFLICT is the thing that must not be lost."""

    def test_failing_read_still_shows_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            store = _store(surface)

            def _boom(_item_id):
                raise RuntimeError("edge table is gone")

            store.backend.open_edges = _boom          # type: ignore[method-assign]
            props = [p for p in store.detect_issues() if p.kind == CROSS_WRITER]
            self.assertEqual(1, len(props), "a failed subgraph read dropped the conflict")
            self.assertIsNone(props[0].subgraph)

    def test_the_failure_is_announced(self):
        """Silence is uniquely misleading here: "no relations" is the common case, so a broken
        read would read to a human as reassurance."""
        from mokata import degrade
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            store = _store(surface)

            def _boom(_item_id):
                raise RuntimeError("edge table is gone")

            store.backend.open_edges = _boom          # type: ignore[method-assign]
            degrade.reset_degrade_notices()
            store.detect_issues()
            self.assertIn("memory-subgraph",
                          [n.subsystem for n in degrade.emitted_notices()])


class TestTheSubgraphIsReadFromTheStore(unittest.TestCase):
    """The store-side wiring: `_attach_subgraph` reads the backend, prunes by the visible set, and
    hands the result to the detector.

    NOTE ON WHAT IS PROVEN WHERE, because the first cut of this class got it wrong: asserting
    `assertIsNotNone(subgraph)` over the `_FakeMemPg` fixture is a FALSE GREEN — an empty subgraph
    is not None, and in that fixture the conflicted item's edges are not in the local floor at all,
    so the assertion passed while the feature did nothing. The end-to-end claim ("the loser is
    shown the shared table's relations") is proven on a LIVE engine in
    `tests/integration/test_db_s7c1_live_db.py`, which is the only place the shared edge table is
    real. What is proven HERE is the wiring, against a backend double with known edges."""

    class _Backend:
        """A minimal backend double: known edges, nothing else."""

        def __init__(self, edges):
            self._edges = edges

        def open_edges(self, item_id):
            return [e for e in self._edges if e.src_id == item_id]

    def _wire(self, store, edges, visible):
        store.backend = self._Backend(edges)             # type: ignore[assignment]
        store._subgraph_visible = lambda: visible        # type: ignore[method-assign]
        return store

    def test_attach_reads_the_backend_and_prunes(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            store = _store(surface)
            self._wire(store, [_edge("m1", "dep-a"), _edge("m1", "dep-hidden")], {"dep-a"})
            rec = ConflictRecord(conflict_id="j1", key="m1", detail="d",
                                 local=_item("s", "mine", item_id="m1"),
                                 remote=_item("s", "theirs", item_id="m1"))
            [out] = store._attach_subgraph([rec])
            self.assertEqual(["dep-a"], [e.dst_id for e in out.subgraph.edges])

    def test_an_item_with_no_edges_gets_an_empty_subgraph_not_none(self):
        """The distinction the render depends on: EMPTY means "asked, nothing there" and renders
        like the pre-K2 proposal; NONE means "could not ask" and is what a failed read leaves."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            store = _store(surface)
            self._wire(store, [], None)
            rec = ConflictRecord(conflict_id="j1", key="m1", detail="d",
                                 local=_item("s", "mine", item_id="m1"),
                                 remote=_item("s", "theirs", item_id="m1"))
            [out] = store._attach_subgraph([rec])
            self.assertIsNotNone(out.subgraph)
            self.assertEqual((), out.subgraph.edges)


if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
