"""DB.S6 — cross-writer healing: the pinned invariants (I1–I8).

Writer divergence used to surface in exactly ONE place: a flush-time CAS conflict, resolved by a
prompt inside `mokata sync`. It never became a `HealingProposal`, so a conflict was invisible to
every surface a user actually reads, and the local approved write it stranded was invisible to
recall. This module pins the stage's eight invariants:

  I1  a heal that spans several durable writes is applied ALL-OR-NOTHING — a partial apply, which
      would leave a subject with no active fact at all, is unreachable rather than merely unlikely;
  I2a a cross-writer conflict is visible in a surface the user already reads (`detect_issues` /
      memory health / the governance view) WITHOUT running `mokata sync`;
  I2b an approved-but-conflicted write is never SILENTLY absent from recall — the reader is told,
      loudly, that N approved writes are not in active memory;
  I3  the no-clobber CAS regression: a conflicted write never overwrites the other writer's row
      (the on-device half of this, with two REAL writers, is `integration/test_db_s6_live_db.py`);
  I4  canonicalization groups the SAME subject written differently, and — the half that matters —
      does NOT group merely similar subjects (the near-miss table);
  I5  with no embedder configured, detection is BYTE-IDENTICAL to the pre-stage behaviour;
  I6  the detection arm is PROPOSE-ONLY: it writes nothing at all, not even a directory;
  I7  staleness compares parsed INSTANTS, not ISO strings — mixed offsets no longer mis-rank;
  I8  ONE resolver: resolving through `sync` and resolving through `apply_proposal` converge.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.healing import (CONTRADICTION, CROSS_WRITER, NEAR_DUP, STALE,
                                   ConflictRecord, canonical_subject, detect_issues)
from mokata.memory.item import ACTIVE, PERSISTENT, SUPERSEDED, MemoryItem


def _item(subject, value, *, item_id=None, created_at="2026-07-01T00:00:00+00:00",
          expires_at=None, embedding=None, mtype=PERSISTENT, status=ACTIVE):
    prov = {"source": "test", "author": "t", "created_at": created_at}
    if embedding is not None:
        prov["_embedding"] = list(embedding)
    return MemoryItem(subject=subject, value=value, id=item_id or f"{subject}-{value}",
                      mtype=mtype, status=status, expires_at=expires_at, provenance=prov)


# --------------------------------------------------------------------------- I7
class TestI7MixedOffsetIso(unittest.TestCase):
    """I7 — staleness compared raw ISO STRINGS (`healing.py`'s `it.expires_at < now`). Two stamps
    for the same instant written by writers in different timezones sort by their TEXT, so a fact
    that expired an hour ago can read as live (and a live one as expired) purely because of the
    offset its writer happened to use. The compare is on INSTANTS now."""

    def test_an_expired_fact_in_a_positive_offset_is_detected_as_stale(self):
        # 2026-07-01T09:00:00+02:00 == 07:00Z, which is BEFORE `now` (08:00Z) → expired.
        # As strings, "2026-07-01T09:00:00+02:00" > "2026-07-01T08:00:00+00:00" → missed.
        it = _item("db host", "10.0.0.1", expires_at="2026-07-01T09:00:00+02:00")
        kinds = [p.kind for p in detect_issues([it], now="2026-07-01T08:00:00+00:00")]
        self.assertIn(STALE, kinds,
                      "an expired fact stamped in +02:00 must be surfaced as stale")

    def test_a_live_fact_in_a_negative_offset_is_not_called_stale(self):
        # 2026-07-01T05:00:00-05:00 == 10:00Z, which is AFTER `now` (08:00Z) → still live.
        # As strings, "2026-07-01T05:00:00-05:00" < "2026-07-01T08:00:00+00:00" → false stale.
        it = _item("db host", "10.0.0.1", expires_at="2026-07-01T05:00:00-05:00")
        kinds = [p.kind for p in detect_issues([it], now="2026-07-01T08:00:00+00:00")]
        self.assertNotIn(STALE, kinds,
                         "a fact that is still live must NOT be proposed stale because its "
                         "writer used a negative offset")

    def test_a_naive_stamp_is_read_as_utc_not_dropped(self):
        it = _item("db host", "10.0.0.1", expires_at="2026-07-01T07:00:00")
        kinds = [p.kind for p in detect_issues([it], now="2026-07-01T08:00:00+00:00")]
        self.assertIn(STALE, kinds)

    def test_an_unparseable_expiry_is_never_a_stale_proposal(self):
        """Degrade direction: garbage in the column must not RETIRE a live fact."""
        it = _item("db host", "10.0.0.1", expires_at="not-a-timestamp")
        kinds = [p.kind for p in detect_issues([it], now="2026-07-01T08:00:00+00:00")]
        self.assertNotIn(STALE, kinds)


# --------------------------------------------------------------------------- I4
class TestI4Canonicalization(unittest.TestCase):
    """I4 — exact-subject-string matching is "useless across 5 writers" (doc 52 #5): each writer
    types the same fact slightly differently and the contradiction never surfaces. Canonicalization
    closes that. The NEAR-MISS table is the half that keeps it honest: subjects that merely LOOK
    alike must not be merged into one fact, because a false contradiction proposes retiring a fact
    that was never wrong."""

    def test_case_and_separator_variants_group_into_one_contradiction(self):
        items = [_item("DB Host", "10.0.0.1", item_id="a"),
                 _item("db_host", "10.0.0.2", item_id="b")]
        kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
        self.assertIn(CONTRADICTION, kinds,
                      "'DB Host' and 'db_host' are the same subject written by two writers")

    def test_whitespace_and_punctuation_variants_group(self):
        items = [_item("  deploy   target ", "staging", item_id="a"),
                 _item("deploy-target", "production", item_id="b")]
        kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
        self.assertIn(CONTRADICTION, kinds)

    def test_the_near_miss_table_must_not_group(self):
        """Pairs that are CLOSE but are genuinely different subjects. Each must stay separate —
        one false contradiction here means mokata proposes retiring a correct fact."""
        near_misses = [
            ("db host", "db hosts"),            # singular vs plural = different facts
            ("staging db host", "db host"),      # a qualified subject is not the bare one
            ("api key", "api keys"),
            ("db host", "db port"),
            ("retry limit", "retry limits"),
            ("prod region", "prod regions"),
            ("user id", "user ids"),
            ("db host 2", "db host"),            # a numbered sibling is its own subject
        ]
        for left, right in near_misses:
            with self.subTest(pair=(left, right)):
                self.assertNotEqual(
                    canonical_subject(left), canonical_subject(right),
                    f"'{left}' and '{right}' are different subjects and must not be grouped")
                items = [_item(left, "one", item_id="a"), _item(right, "two", item_id="b")]
                kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
                self.assertNotIn(CONTRADICTION, kinds,
                                 f"'{left}' vs '{right}' was wrongly called a contradiction")

    def test_the_proposal_keeps_the_subject_the_human_typed(self):
        """The canonical form is a GROUPING key, never a rewrite: the rendered proposal must show
        the writer's own words, not a normalized string the human never wrote."""
        items = [_item("DB Host", "10.0.0.1", item_id="a"),
                 _item("db_host", "10.0.0.2", item_id="b")]
        p = next(p for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")
                 if p.kind == CONTRADICTION)
        self.assertIn(p.subject, ("DB Host", "db_host"))


# --------------------------------------------------------------------------- I5 / near-dup (R5)
class TestI5NearDupAndDegrade(unittest.TestCase):
    """R5 — a near-dup candidate requires an IDENTICAL canonical subject AND a near-duplicate
    value at a conservative high threshold, read off the embedding the gated write already
    stamped. Never near-dup ALONE: value similarity by itself must never group two facts.
    I5 — with no embedding stamped, detection degrades to exactly the pre-stage behaviour."""

    def test_two_restatements_of_one_fact_are_near_dup_not_contradiction(self):
        items = [_item("db host", "the db host is 10.0.0.1", item_id="a",
                       embedding=[1.0, 0.0, 0.0]),
                 _item("db host", "db host: 10.0.0.1", item_id="b",
                       embedding=[0.999, 0.0447, 0.0])]
        kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
        self.assertIn(NEAR_DUP, kinds,
                      "a restatement of the same fact is a duplicate, not a disagreement")
        self.assertNotIn(CONTRADICTION, kinds)

    def test_a_genuine_disagreement_stays_a_contradiction(self):
        items = [_item("db host", "10.0.0.1", item_id="a", embedding=[1.0, 0.0, 0.0]),
                 _item("db host", "10.9.9.9", item_id="b", embedding=[0.0, 1.0, 0.0])]
        kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
        self.assertIn(CONTRADICTION, kinds)
        self.assertNotIn(NEAR_DUP, kinds)

    def test_near_dup_value_alone_never_groups_different_subjects(self):
        """The R5 guard. Two DIFFERENT subjects whose values embed identically must produce no
        proposal at all — near-dup is a refinement INSIDE a subject group, never a grouper."""
        items = [_item("db host", "10.0.0.1", item_id="a", embedding=[1.0, 0.0, 0.0]),
                 _item("cache host", "10.0.0.1", item_id="b", embedding=[1.0, 0.0, 0.0])]
        self.assertEqual([], detect_issues(items, now="2026-07-01T00:00:00+00:00"))

    def test_the_threshold_is_conservative(self):
        """Merely RELATED values (cosine well below the bar) stay a contradiction — the near-dup
        arm must not swallow a real disagreement because two values share vocabulary."""
        items = [_item("db host", "10.0.0.1", item_id="a", embedding=[1.0, 0.0, 0.0]),
                 _item("db host", "10.0.0.2", item_id="b", embedding=[0.9, 0.436, 0.0])]
        kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
        self.assertIn(CONTRADICTION, kinds)
        self.assertNotIn(NEAR_DUP, kinds)

    def test_no_embedder_is_byte_identical_to_the_pre_stage_behaviour(self):
        """I5 — items with no `_embedding` (no embedder consented) must produce exactly the
        contradiction the pre-DB.S6 detector produced: same kind, same old/new, same order."""
        items = [_item("db host", "the db host is 10.0.0.1", item_id="a"),
                 _item("db host", "db host: 10.0.0.1", item_id="b")]
        props = detect_issues(items, now="2026-07-01T00:00:00+00:00")
        self.assertEqual([CONTRADICTION], [p.kind for p in props])
        self.assertEqual("a", props[0].old.id)
        self.assertEqual("b", props[0].new.id)

    def test_one_side_missing_an_embedding_degrades_to_contradiction(self):
        """A half-embedded corpus (a store mid-`memory reembed`) must not silently near-dup."""
        items = [_item("db host", "the db host is 10.0.0.1", item_id="a",
                       embedding=[1.0, 0.0, 0.0]),
                 _item("db host", "db host: 10.0.0.1", item_id="b")]
        kinds = [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")]
        self.assertEqual([CONTRADICTION], kinds)


# --------------------------------------------------------------------------- R3 / R6 / I2a
class TestCrossWriterDetectionArm(unittest.TestCase):
    """R3 — the detector takes PLAIN fields (`ConflictRecord`), never the journal's `ConflictView`:
    `memory/healing.py` imports nothing from the collab layer. R6 — the arm is additive, so
    DB.S7/K2 can extend it without rewriting it."""

    def test_healing_module_does_not_import_the_journal_type(self):
        """R3 — checked on the IMPORT GRAPH (every import statement in the module, including the
        function-local ones), not on the file's prose: an L2 domain module must not reach up to
        the L3 collab layer, and a comment naming `ConflictView` is not a dependency."""
        import ast
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
        self.assertEqual([], forbidden,
                         f"memory/healing.py must not import the collab layer: {forbidden}")

    def test_a_conflict_record_becomes_a_cross_writer_proposal(self):
        local = _item("db host", "10.0.0.1", item_id="x")
        remote = _item("db host", "10.9.9.9", item_id="x")
        rec = ConflictRecord(conflict_id="j1", key="x", detail="remote revision advanced",
                             local=local, remote=remote, remote_revision=4)
        props = detect_issues([], now="2026-07-01T00:00:00+00:00", conflicts=[rec])
        self.assertEqual([CROSS_WRITER], [p.kind for p in props])
        self.assertEqual("j1", props[0].conflict_id)
        self.assertEqual("10.0.0.1", props[0].old.value)
        self.assertEqual("10.9.9.9", props[0].new.value)

    def test_the_rendered_proposal_names_both_writers_values(self):
        from mokata.memory.healing import render_proposal
        local = _item("db host", "10.0.0.1", item_id="x")
        remote = _item("db host", "10.9.9.9", item_id="x")
        rec = ConflictRecord(conflict_id="j1", key="x", detail="remote revision advanced",
                             local=local, remote=remote, remote_revision=4)
        text = render_proposal(detect_issues([], conflicts=[rec])[0])
        self.assertIn("10.0.0.1", text)
        self.assertIn("10.9.9.9", text)

    def test_no_conflicts_is_byte_identical_to_the_pre_stage_call(self):
        items = [_item("db host", "10.0.0.1", item_id="a"),
                 _item("db host", "10.0.0.2", item_id="b")]
        self.assertEqual([p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00")],
                         [p.kind for p in detect_issues(items, now="2026-07-01T00:00:00+00:00",
                                                        conflicts=[])])

    def test_memory_health_counts_cross_writer_conflicts(self):
        """I2a (half) — the health surface the dashboard and `mokata memory` already render must
        carry the conflict count, so divergence is visible without running `sync`."""
        from mokata.memory.intelligence import memory_health
        local = _item("db host", "10.0.0.1", item_id="x")
        remote = _item("db host", "10.9.9.9", item_id="x")
        rec = ConflictRecord(conflict_id="j1", key="x", detail="d", local=local, remote=remote)
        health = memory_health(detect_issues([], conflicts=[rec]), reads=1, writes=1)
        self.assertEqual(1, health.cross_writer)
        self.assertIn("cross-writer", health.nudge())


# --------------------------------------------------------------------------- team-mode fixtures
def _team_repo(d):
    """A real initialized repo switched to TEAM mode — the only mode with a journal."""
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


def _plant_conflict(surface, *, key="m1", mine="mine", theirs="theirs", base_revision=1,
                    remote_revision=2, ledger_id=5):
    """Journal a team write, then plant a DIVERGED remote row and flush — which produces exactly
    the state this stage is about: an approved local write that lost the CAS."""
    from mokata import team_health, team_journal, teamdb
    from test_tm_s5_journal import _FakeMemPg
    doc = json.dumps(MemoryItem(subject=key, value=mine, id=key, mtype=PERSISTENT,
                                status=ACTIVE).to_doc())
    team_journal.record_team_write(
        surface, op="memory_put", table=teamdb.MEMORY_TABLE, key=key,
        payload={"id": key, "mtype": PERSISTENT, "subject": key, "status": ACTIVE,
                 "doc": doc, "project": "p1"},
        ledger_id=ledger_id, project="p1", actor="alice", base_revision=base_revision)
    pg = _FakeMemPg()
    pg.plant(key, json.dumps(MemoryItem(subject=key, value=theirs, id=key, mtype=PERSISTENT,
                                        status=ACTIVE).to_doc()), revision=remote_revision)
    team_journal.flush(surface,
                       health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                       connect=lambda *a, **k: pg)
    return pg


def _store(surface):
    from mokata.memory import MemoryStore
    return MemoryStore.from_surface(surface)


class TestI2aVisibleWithoutSync(unittest.TestCase):
    """I2a — divergence used to exist ONLY inside `mokata sync`'s prompt. A user who never ran
    sync had no way to learn that an approved write of theirs was not in the shared store."""

    def test_the_conflict_is_a_proposal_on_the_normal_detect_path(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            props = _store(surface).detect_issues()
            cross = [p for p in props if p.kind == CROSS_WRITER]
            self.assertEqual(1, len(cross), "the CAS conflict must surface without running sync")
            self.assertEqual("mine", cross[0].old.value)
            self.assertEqual("theirs", cross[0].new.value)

    def test_the_governance_view_renders_it(self):
        """The dashboard reads `detect_issues` + `memory_health` — both must carry the conflict."""
        from mokata.dashboard import build_governance_view
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            view = build_governance_view(surface)
            self.assertEqual(1, view.health.cross_writer)
            self.assertIn("cross-writer", view.health.nudge())

    def test_local_mode_is_untouched(self):
        """The arm must be invisible to a zero-config user: no journal, no conflicts, no cost."""
        from mokata.config import Surface
        from mokata.init import init_repo
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            props = _store(Surface.load(d)).detect_issues()
            self.assertEqual([], [p for p in props if p.kind == CROSS_WRITER])


class TestI6ProposeOnly(unittest.TestCase):
    """I6 — the detection arm is PROPOSE-ONLY. Not "writes no memory rows": writes NOTHING."""

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
                except OSError:                      # pragma: no cover - unreadable is still a key
                    out[path] = b"<unreadable>"
        return out

    def test_detection_over_a_conflicted_journal_changes_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            store = _store(surface)
            store.detect_issues()                    # warm any lazy construction
            before = self._tree(d)
            for _ in range(3):
                self.assertTrue([p for p in store.detect_issues() if p.kind == CROSS_WRITER])
            self.assertEqual(before, self._tree(d),
                             "detection wrote to disk — it must be pure")

    def test_the_detection_arm_does_not_create_the_journal_directory(self):
        """`TeamJournal.__init__` creates its parent directory, so merely CONSTRUCTING a journal
        to ask "any conflicts?" is a write. Exercised on the arm itself, with the directory
        removed, so nothing else in the store can create it first and mask the mutation."""
        from mokata.memory.team_writer import TeamWriter
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            temp_local = os.path.join(surface.mokata_dir, "temp_local")
            shutil.rmtree(temp_local, ignore_errors=True)
            self.assertEqual([], TeamWriter().conflicts(surface))
            self.assertFalse(os.path.exists(temp_local),
                             "the detection arm created `.mokata/temp_local/` — it must not write")

    def test_detection_in_a_team_repo_with_no_journal_yet_writes_nothing(self):
        """The same invariant through the real entry point, in the state most team repos are in
        for most of their life: team mode is on, but no team write has happened yet. (The SQLite
        floor lives under `temp_local/` too, so the directory cannot simply be removed here —
        the arm-level test above is what pins the no-makedirs half.)"""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            store = _store(surface)
            store.detect_issues()                    # warm any lazy construction
            before = self._tree(d)
            self.assertEqual([], [p for p in store.detect_issues() if p.kind == CROSS_WRITER])
            self.assertEqual(before, self._tree(d))


class TestI8OneResolver(unittest.TestCase):
    """I8 — R4's invariant, stated as a test: `sync` and `apply_proposal` are ONE resolver, so
    the two entry points converge on the same end state (journal, remote row and ledger)."""

    def _end_state(self, surface, pg):
        from mokata import team_journal
        from mokata.govern.ledger import AuditLedger
        j = team_journal.TeamJournal.for_surface(surface)
        return {
            "pending": [(e.key, e.base_revision) for e in j.pending()],
            "conflicts": [c.key for c in j.conflicts()],
            "remote": {k: json.loads(v["doc"])["value"] for k, v in pg.rows.items()},
            "revisions": {k: v["revision"] for k, v in pg.rows.items()},
            "healing": [(e.get("op"), e.get("decision"), e.get("changed"))
                        for e in AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
                        if e.get("kind") == "healing_decision"],
        }

    def _via_sync(self, d, keep_local):
        from mokata import team_health, team_journal
        surface = _team_repo(d)
        pg = _plant_conflict(surface)
        team_journal.sync(surface,
                          health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                          connect=lambda *a, **k: pg, confirm=lambda _p: keep_local)
        return self._end_state(surface, pg)

    def _via_apply_proposal(self, d, keep_local):
        surface = _team_repo(d)
        pg = _plant_conflict(surface)
        store = _store(surface)
        proposal = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
        store.apply_proposal(proposal, "approve" if keep_local else "discard", assume_yes=True)
        if keep_local:
            from mokata import team_health, team_journal
            team_journal.flush(surface,
                               health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                               connect=lambda *a, **k: pg)
        return self._end_state(surface, pg)

    def test_keeping_yours_converges(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            self.assertEqual(self._via_sync(d1, True), self._via_apply_proposal(d2, True))

    def test_keeping_theirs_converges(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            self.assertEqual(self._via_sync(d1, False), self._via_apply_proposal(d2, False))

    def test_sync_holds_no_second_resolver(self):
        """The structural half: `team_journal` must not settle a conflict itself. `resolve` is the
        journal's own primitive; the MODULE-level sync path may not call it."""
        import ast
        import mokata.team_journal as tj
        with open(tj.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "sync")
        calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        self.assertNotIn("journal.resolve", calls,
                         "sync resolves conflicts itself — R4 requires ONE resolver")


class TestI3NoClobber(unittest.TestCase):
    """I3 — the CAS regression. A conflicted write must never reach the shared row by accident;
    only an explicit 'keep yours' may overwrite a teammate's value."""

    def test_a_deferred_conflict_never_touches_the_remote_row(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _plant_conflict(surface)
            store = _store(surface)
            proposal = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            for decision in ("defer", "reject"):
                res = store.apply_proposal(proposal, decision, assume_yes=True)
                self.assertFalse(res.changed)
            self.assertEqual("theirs", json.loads(pg.rows["m1"]["doc"])["value"])
            self.assertEqual(2, pg.rows["m1"]["revision"], "no CAS bump on a non-decision")
            self.assertEqual(1, len(_store(surface).cross_writer_proposals()),
                             "the conflict is still open")

    def test_declining_at_the_gate_leaves_the_remote_row_alone(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _plant_conflict(surface)
            store = _store(surface)
            proposal = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            res = store.apply_proposal(proposal, "approve", confirm=lambda _p: False)
            self.assertFalse(res.changed)
            self.assertEqual("theirs", json.loads(pg.rows["m1"]["doc"])["value"])
            self.assertEqual(1, len(_store(surface).cross_writer_proposals()))

    def test_keeping_yours_rebases_on_the_current_remote_revision(self):
        """The no-clobber mechanism itself: the re-queued write carries the CURRENT remote
        revision as its CAS base, so it overwrites the row it was actually shown — never a
        blind write at a base the human never saw."""
        from mokata import team_journal
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface, base_revision=1, remote_revision=7)
            store = _store(surface)
            proposal = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            store.apply_proposal(proposal, "approve", assume_yes=True)
            pend = team_journal.TeamJournal.for_surface(surface).pending()
            self.assertEqual([7], [e.base_revision for e in pend])

    def test_editing_a_conflict_is_refused_not_half_applied(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _plant_conflict(surface)
            store = _store(surface)
            proposal = next(p for p in store.detect_issues() if p.kind == CROSS_WRITER)
            edited = MemoryItem(subject="m1", value="merged", id="m1", mtype=PERSISTENT,
                                status=ACTIVE)
            res = store.apply_proposal(proposal, "edit", edited=edited, assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.aborted)
            self.assertEqual("theirs", json.loads(pg.rows["m1"]["doc"])["value"])

    def test_discard_is_rejected_on_a_non_conflict_proposal(self):
        """`discard` throws away an approved write; it must not be reachable on an ordinary
        healing proposal, where callers legitimately pass a decision through from a prompt."""
        from mokata.memory.store import MemoryError as StoreMemoryError
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            store = _store(surface)
            p = detect_issues([_item("s", "a", item_id="a"), _item("s", "b", item_id="b")])[0]
            with self.assertRaises(StoreMemoryError):
                store.apply_proposal(p, "discard", assume_yes=True)


# --------------------------------------------------------------------------- I1
class _TxnPg:
    """The fake shared table WITH real transaction semantics — snapshot on enter, restore on
    rollback. `_FakeMemPg` alone cannot show I1: with no transaction, every apply is its own
    commit and "all-or-nothing" has nothing to be nothing about."""

    def __init__(self):
        from test_tm_s5_journal import _FakeMemPg
        self._pg = _FakeMemPg()
        self.txns = 0

    @property
    def rows(self):
        return self._pg.rows

    def execute(self, sql, params=()):
        return self._pg.execute(sql, params)

    def plant(self, rid, doc, revision):
        self._pg.plant(rid, doc, revision)

    def transaction(self):
        import contextlib
        import copy
        pg = self._pg
        self.txns += 1

        @contextlib.contextmanager
        def _txn():
            snapshot = copy.deepcopy(pg.rows)
            try:
                yield
            except BaseException:
                pg.rows.clear()
                pg.rows.update(snapshot)
                raise

        return _txn()


def _journal(surface, key, value, *, ledger_id, base_revision, status=ACTIVE):
    from mokata import team_journal, teamdb
    doc = json.dumps(MemoryItem(subject=key, value=value, id=key, mtype=PERSISTENT,
                                status=status).to_doc())
    team_journal.record_team_write(
        surface, op="memory_update", table=teamdb.MEMORY_TABLE, key=key,
        payload={"id": key, "mtype": PERSISTENT, "subject": key, "status": status,
                 "doc": doc, "project": "p1"},
        ledger_id=ledger_id, project="p1", actor="alice", base_revision=base_revision)


def _flush(surface, pg):
    from mokata import team_health, team_journal
    return team_journal.flush(
        surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
        connect=lambda *a, **k: pg)


class TestI1GroupAtomicApply(unittest.TestCase):
    """I1 — `apply_proposal` on a contradiction makes TWO durable writes under ONE approval:
    retire the old fact, install the new one. Applied independently, the first can land while the
    second loses its CAS — and the shared store is then left with the old fact RETIRED and the new
    one ABSENT. The subject has no active value at all, and nothing anywhere says so.

    That is the silent fact-loss this pins shut: the two writes are one transaction, and a conflict
    in either rolls back both."""

    def _diverged_group(self, surface):
        """Two writes under ONE approval (ledger_id 7). `a` will apply; `b`'s base revision is
        stale, so it loses its CAS — the partial-apply trigger."""
        pg = _TxnPg()
        pg.plant("a", json.dumps({"id": "a", "value": "old"}), revision=1)
        pg.plant("b", json.dumps({"id": "b", "value": "theirs"}), revision=9)
        _journal(surface, "a", "retired", ledger_id=7, base_revision=1)
        _journal(surface, "b", "mine", ledger_id=7, base_revision=1)
        return pg

    def test_a_conflict_in_one_write_rolls_back_the_whole_approval(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = self._diverged_group(surface)
            res = _flush(surface, pg)
            self.assertEqual(0, res.flushed, "no half of the approval may land")
            self.assertEqual(2, res.conflicts, "the whole approval is conflicted, not half of it")
            self.assertEqual("old", json.loads(pg.rows["a"]["doc"])["value"],
                             "the sibling write was rolled back — the row is untouched")
            self.assertEqual(1, pg.rows["a"]["revision"], "and its revision did not bump")

    def test_the_human_sees_the_whole_approval_as_one_unit(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            self._flushed = _flush(surface, self._diverged_group(surface))
            props = _store(surface).cross_writer_proposals()
            self.assertEqual(2, len(props))
            for p in props:
                self.assertIn("1 of 2 approved together", p.rationale)
                self.assertIn("resolve all 2 together", p.rationale)

    def test_a_clean_group_still_commits_every_member(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            pg.plant("a", json.dumps({"id": "a", "value": "old"}), revision=1)
            pg.plant("b", json.dumps({"id": "b", "value": "old"}), revision=1)
            _journal(surface, "a", "new-a", ledger_id=7, base_revision=1)
            _journal(surface, "b", "new-b", ledger_id=7, base_revision=1)
            res = _flush(surface, pg)
            self.assertEqual(2, res.flushed)
            self.assertEqual(0, res.conflicts)
            self.assertEqual("new-a", json.loads(pg.rows["a"]["doc"])["value"])
            self.assertEqual("new-b", json.loads(pg.rows["b"]["doc"])["value"])

    def test_a_single_write_opens_no_transaction_at_all(self):
        """The common case must stay byte-identical — one write is its own atomic unit."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            pg.plant("a", json.dumps({"id": "a", "value": "old"}), revision=1)
            _journal(surface, "a", "new-a", ledger_id=7, base_revision=1)
            self.assertEqual(1, _flush(surface, pg).flushed)
            self.assertEqual(0, pg.txns, "a solo write must not open a transaction")

    def test_unrelated_writes_are_not_grouped(self):
        """`None` (no ledger) and the shared `floor-recovery` marker must NOT collapse unrelated
        writes into one transaction — a conflict in one would then block every other."""
        from mokata import team_journal
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _journal(surface, "a", "v", ledger_id=None, base_revision=1)
            _journal(surface, "b", "v", ledger_id=None, base_revision=1)
            _journal(surface, "c", "v", ledger_id="floor-recovery", base_revision=1)
            _journal(surface, "e", "v", ledger_id="floor-recovery", base_revision=1)
            pend = team_journal.TeamJournal.for_surface(surface).pending()
            groups = team_journal._approval_groups(pend)
            self.assertEqual([1, 1, 1, 1], [len(g) for g in groups])

    def test_a_secret_in_one_member_publishes_no_member(self):
        """Publishing the innocent half of a blocked approval is the same partial apply by
        another route."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            pg.plant("a", json.dumps({"id": "a", "value": "old"}), revision=1)
            pg.plant("b", json.dumps({"id": "b", "value": "old"}), revision=1)
            _journal(surface, "a", "clean", ledger_id=7, base_revision=1)
            _journal(surface, "b", "secret", ledger_id=7, base_revision=1)
            from mokata import team_health, team_journal
            res = team_journal.flush(
                surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                connect=lambda *a, **k: pg,
                scan=lambda e: ["finding"] if e.key == "b" else [])
            self.assertEqual(0, res.flushed, "the clean sibling must not publish alone")
            self.assertEqual(1, res.blocked)
            self.assertEqual("old", json.loads(pg.rows["a"]["doc"])["value"])
            self.assertEqual(1, len(team_journal.TeamJournal.for_surface(surface).pending()),
                             "the clean sibling stays pending, to retry once the secret is gone")

    def test_a_partial_apply_without_transaction_support_is_loud(self):
        """The DETECT half of the locked decision. A connection that cannot open a transaction
        gets the pre-DB.S6 per-entry behaviour — but a partial outcome is announced through the
        degrade channel instead of returning a clean-looking verdict."""
        from mokata import degrade
        from test_tm_s5_journal import _FakeMemPg
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _FakeMemPg()                        # NO `transaction` attribute
            pg.plant("a", json.dumps({"id": "a", "value": "old"}), revision=1)
            pg.plant("b", json.dumps({"id": "b", "value": "theirs"}), revision=9)
            _journal(surface, "a", "retired", ledger_id=7, base_revision=1)
            _journal(surface, "b", "mine", ledger_id=7, base_revision=1)
            degrade.reset_degrade_notices()
            lines = []
            from mokata import team_health, team_journal
            res = team_journal.flush(
                surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                connect=lambda *a, **k: pg, out=lines.append)
            self.assertEqual((1, 1), (res.flushed, res.conflicts), "partial, as this path admits")
            notices = degrade.emitted_notices()
            self.assertTrue(any("PARTIAL state" in (n.fallback or "") for n in notices),
                            f"a partial apply was not announced: {notices}")
            self.assertTrue(any("PARTIAL state" in ln for ln in lines))


class TestI1TheHealPathIsOneGroup(unittest.TestCase):
    """I1's wiring half: the grouping boundary (`ledger_id`) must actually be the boundary the
    HEAL path writes on, or the transaction protects nothing that matters."""

    def test_approving_a_contradiction_journals_one_approval_group(self):
        from mokata import team_journal
        from mokata.memory.healing import CONTRADICTION, HealingProposal
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            store = _store(surface)
            old = _item("db host", "10.0.0.1", item_id="old")
            new = _item("db host", "10.9.9.9", item_id="new")
            store.remember(old, assume_yes=True)
            store.remember(new, assume_yes=True)
            before = {e.id for e in team_journal.TeamJournal.for_surface(surface).pending()}
            store.apply_proposal(
                HealingProposal(kind=CONTRADICTION, subject="db host", mtype=PERSISTENT,
                                old=old, new=new, rationale="test"),
                "approve", assume_yes=True)
            pend = [e for e in team_journal.TeamJournal.for_surface(surface).pending()
                    if e.id not in before]
            self.assertEqual(2, len(pend), "a contradiction heal is two durable writes")
            self.assertEqual(1, len({e.ledger_id for e in pend}),
                             "both writes must carry ONE approval id — that is the atomic unit")
            self.assertEqual([2], [len(g) for g in team_journal._approval_groups(pend)],
                             "and the flush must see them as ONE group")


class TestI1bTheGroupSurvivesResolution(unittest.TestCase):
    """I1b — the hole I1's transaction leaves OPEN.

    I1 makes the FLUSH all-or-nothing: a heal that retires a fact and installs its replacement can
    no longer half-land. But when it rolls back, both writes surface as SEPARATE conflicts, and the
    resolver settles them ONE AT A TIME. So the exact end state the transaction prevents is still
    reachable by hand, one prompt at a time: approve the retirement, discard (or simply never
    decide) the replacement, and the shared store is left with the fact retired and nothing in its
    place. Atomic apply, then non-atomic resolution — the loss just moved downstream.

    This pins the ONE direction that loses a fact: retire-without-replace. The guard is minimal and
    deliberately so — it refuses, it does not orchestrate. The other direction (both sides kept, so
    the subject ends up with two active facts) is visible, reviewable and loses nothing, and the
    real group-decision surface — deciding a whole approval in one prompt — is DB.S7/K2."""

    def _retire_and_replace(self, surface):
        """The exact shape `apply_proposal` on a contradiction produces: retire `old-fact`
        (status SUPERSEDED) and install `new-fact`, both under ONE approval id. `new-fact`'s CAS
        base is stale, so the group rolls back and BOTH members surface as conflicts."""
        pg = _TxnPg()
        pg.plant("old-fact", json.dumps({"id": "old-fact", "value": "the old value"}), revision=1)
        pg.plant("new-fact", json.dumps({"id": "new-fact", "value": "theirs"}), revision=9)
        _journal(surface, "old-fact", "the old value", ledger_id=7, base_revision=1,
                 status=SUPERSEDED)
        _journal(surface, "new-fact", "the new value", ledger_id=7, base_revision=1)
        res = _flush(surface, pg)
        assert (0, 2) == (res.flushed, res.conflicts), res
        return pg

    @staticmethod
    def _member(surface, rid):
        return next(p for p in _store(surface).cross_writer_proposals() if p.old.id == rid)

    def test_approving_the_retirement_while_its_replacement_is_undecided_is_refused(self):
        """MUTATION: drop the guard and this goes RED — the split resolution applies, the
        retirement is re-queued, and the replacement is still nobody's decision."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            self._retire_and_replace(surface)
            res = _store(surface).apply_proposal(self._member(surface, "old-fact"), "approve",
                                                 assume_yes=True)
            self.assertFalse(res.changed,
                             "a fact was retired while its replacement was still undecided — "
                             "the subject is one flush away from having no active value")
            self.assertTrue(res.refused)
            self.assertIn("1 of 2", res.message)
            self.assertIn("resolve them together", res.message)
            self.assertEqual(2, len(_store(surface).cross_writer_proposals()),
                             "both members stay conflicted — nothing was decided")

    def test_approving_the_retirement_after_discarding_its_replacement_is_refused(self):
        """The worst ordering, and the one a human reaches naturally: keep THEIR version of the
        replacement row, then approve your own retirement. Nothing is left holding the fact."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = self._retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_proposal(self._member(surface, "new-fact"), "discard",
                                            assume_yes=True).changed)
            res = _store(surface).apply_proposal(self._member(surface, "old-fact"), "approve",
                                                 assume_yes=True)
            self.assertFalse(res.changed)
            self.assertIn("resolve them together", res.message)
            _flush(surface, pg)
            self.assertEqual("the old value",
                             json.loads(pg.rows["old-fact"]["doc"])["value"],
                             "the fact was retired and nothing replaced it")

    def test_keeping_the_replacement_first_lets_the_retirement_through(self):
        """The guard REFUSES a losing order; it does not forbid the heal. Decide the replacement
        first and the retirement resolves normally — otherwise this would be a deadlock dressed up
        as a safety property."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = self._retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_proposal(self._member(surface, "new-fact"), "approve",
                                            assume_yes=True).changed)
            self.assertTrue(_store(surface)
                            .apply_proposal(self._member(surface, "old-fact"), "approve",
                                            assume_yes=True).changed)
            self.assertEqual(2, _flush(surface, pg).flushed)
            self.assertEqual("the new value", json.loads(pg.rows["new-fact"]["doc"])["value"])
            self.assertEqual(SUPERSEDED, json.loads(pg.rows["old-fact"]["doc"])["status"])

    def test_the_replacement_side_is_never_blocked_by_the_guard(self):
        """Only the retiring member is dangerous. Resolving the REPLACEMENT while the retirement is
        undecided loses nothing — the old fact is still active — so the guard must stay out of it."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            self._retire_and_replace(surface)
            for decision in ("approve", "discard"):
                with tempfile.TemporaryDirectory() as d2:
                    s2 = _team_repo(d2)
                    self._retire_and_replace(s2)
                    self.assertTrue(_store(s2)
                                    .apply_proposal(self._member(s2, "new-fact"), decision,
                                                    assume_yes=True).changed,
                                    f"the guard blocked '{decision}' on the replacement member")

    def test_a_solo_conflict_is_untouched_by_the_guard(self):
        """The regression half: an ordinary one-write conflict has no group, so the guard must be
        invisible to it — including when that lone write is itself a retirement."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            pg.plant("solo", json.dumps({"id": "solo", "value": "theirs"}), revision=9)
            _journal(surface, "solo", "mine", ledger_id=3, base_revision=1, status=SUPERSEDED)
            _flush(surface, pg)
            p = self._member(surface, "solo")
            self.assertTrue(_store(surface).apply_proposal(p, "approve", assume_yes=True).changed)


class TestTheRollbackSignalIsControlFlowNotAnError(unittest.TestCase):
    """`_GroupRollback` is a private CONTROL-FLOW signal — the thing that makes the `with txn:`
    block exit so the transaction rolls back. It is NOT a failure mokata is reporting: nothing
    broke, nothing degraded, the CAS did precisely its job.

    While it carried `MokataError` as its base, that distinction was only a comment. A caller
    doing the very thing `MokataError` was introduced for — `except MokataError` to catch "a
    failure mokata DEFINED" — would swallow a rollback signal mid-transaction and leave the
    group half-applied with no marker. The base is what makes that reachable, so the base is
    what this pins."""

    def test_a_broad_except_MokataError_does_not_catch_the_rollback_signal(self):
        """MUTATION: re-base `_GroupRollback` on `MokataError` and this goes RED."""
        from mokata.errors import MokataError
        from mokata.team_journal import _GroupRollback
        reached = []
        try:
            try:
                raise _GroupRollback
            except MokataError:
                reached.append("MokataError")
        except _GroupRollback:
            reached.append("_GroupRollback")
        self.assertEqual(
            ["_GroupRollback"], reached,
            "`except MokataError` swallowed a control-flow signal — a domain-error handler "
            "must never be able to intercept the group rollback")

    def test_it_is_outside_the_error_taxonomy_entirely(self):
        from mokata.errors import ControlSignal, DegradedCapability, MokataError
        from mokata.team_journal import _GroupRollback
        self.assertTrue(issubclass(_GroupRollback, ControlSignal),
                        "a control-flow signal carries the control-signal base, so the D5 sweep "
                        "still SEES it and a new one cannot be added unclassified")
        self.assertFalse(issubclass(_GroupRollback, MokataError))
        self.assertFalse(issubclass(_GroupRollback, DegradedCapability))
        self.assertFalse(issubclass(ControlSignal, MokataError),
                         "the signal base is a sibling of the error base, not a child of it")


# --------------------------------------------------------------------------- I2b
class TestI2bConflictedWritesAreNeverSilent(unittest.TestCase):
    """I2b — the read-your-writes overlay merges PENDING entries only, and a write that loses its
    CAS is marked CONFLICT. So the user's own approved fact quietly vanishes from every recall,
    while the teammate's value shows in its place with no indication anything is missing. An
    approved write that is neither in memory nor mentioned is indistinguishable from one that was
    never made — that is what this closes."""

    def setUp(self):
        from mokata import degrade
        degrade.reset_degrade_notices()

    def test_a_recall_over_a_conflicted_write_says_so_loudly(self):
        from mokata import degrade
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            store = _store(surface)
            store.all_active()                       # an ordinary read — no sync, no govern
            notices = [n for n in degrade.emitted_notices()
                       if n.subsystem == "memory-conflicted-writes"]
            self.assertEqual(1, len(notices), "a conflicted write was silently absent from recall")
            self.assertIn("NOT in active memory", notices[0].fallback)
            self.assertIn("1 approved write", notices[0].fallback)
            self.assertIn("mokata sync", notices[0].fix)

    def test_the_conflicted_value_is_not_smuggled_into_recall(self):
        """The other half of the choice: recall must keep telling the truth about the SHARED
        store. Overlaying the losing local value would hide the teammate's actual fact behind a
        value that is not in the database at all."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _plant_conflict(surface)
            values = [i.value for i in _store(surface).all_active() if i.id == "m1"]
            self.assertNotIn("mine", values,
                             "a conflicted write must not be presented as if it were memory")

    def test_a_clean_store_stays_silent(self):
        from mokata import degrade
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _store(surface).all_active()
            self.assertEqual([], [n for n in degrade.emitted_notices()
                                  if n.subsystem == "memory-conflicted-writes"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
