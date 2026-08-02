"""H-6 — the code-staleness bridge against a REAL Postgres with TWO REAL WRITERS.

H-6's own mechanism is deliberately engine-independent — a content hash per anchored file, so it
works identically on a solo SQLite install (the anchor-shape split's decision #4: the item scan is
the mechanism, the `*_dst` index is a team-mode optimisation nobody depends on). What is NOT
engine-independent is the thing the bridge is FOR, and it is the only reason this file exists:

  * THE ITEMS BEING JUDGED COME FROM THE SHARED TABLE. `MemoryStore._moved_code_anchors` walks
    `backend.all(statuses=(ACTIVE,))`. On the SQLite floor that is one process reading its own
    file. On Postgres it is a project-scoped query against a table other people write to, and the
    `about_code` list it reads has crossed a JSON round trip through the team journal's CAS. A
    unit test cannot show that the anchors survive that trip, because in a unit test they never
    take it.

  * THE TWO-WRITER STORY IS THE FEATURE. Writer B records a decision anchored to a file. Writer A
    — a different repo, a different checkout — pulls that decision out of the shared store and
    edits the file it is about. A must be told. This is the whole point of an `about_code` anchor
    in a TEAM, and one process cannot stage it honestly.

  * PROJECT SCOPING. Another tenant's anchored decisions must not surface here. Only a shared
    store shows that.

  * THE CONTROL. An untouched anchor on the same live store must stay silent, or "it fired" proves
    only that something fires.

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

_PROJECT = "h6-live"
_OTHER_PROJECT = "h6-other"


def _writer(d, dsn, project=_PROJECT):
    """One INDEPENDENT writer — own repo, journal and ledger, pinned to `project`, with
    `memory_store` WIRED TO POSTGRES. The wiring matters here for the same reason it does at
    DB.S7c1/c2: on the default SQLite wiring the store would read its own local floor and the
    two-writer claim would be a claim about one file."""
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    from mokata.config import Surface
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    data["settings"].setdefault("project", {})["id"] = project
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


def _flush(surface):
    from mokata import team_health, team_journal
    return team_journal.flush(
        surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"))


def _store(surface):
    from mokata.memory import MemoryStore
    return MemoryStore.from_surface(surface)


def _real_backend(store):
    """Unwrap `JournalOverlay` to whatever is actually answering reads.

    DB.S7c1's live leg caught a FALSE GREEN of exactly this shape — the default wiring left the
    store on the SQLite floor while the test believed it was talking to the shared table, and every
    assertion passed for the wrong reason. So every case here asserts the engine it claims to be
    proving something about, rather than trusting the manifest edit above to have taken."""
    b = store.backend
    return getattr(b, "_backend", b)


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestH6Live(unittest.TestCase):

    def setUp(self):
        from mokata import teamdb
        from mokata.memory import _pg
        self.dsn = _pg_dsn()
        self._saved = os.environ.get("MOKATA_PG_DSN")
        teamdb.provision(self.dsn)
        conn = _pg.get_connection(self.dsn, RuntimeError)
        conn.execute(f"DELETE FROM {teamdb.EDGES_TABLE}")
        conn.execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata.memory import _pg
        _pg.reset_manager()
        if self._saved is None:
            os.environ.pop("MOKATA_PG_DSN", None)
        else:
            os.environ["MOKATA_PG_DSN"] = self._saved

    # --- helpers ---------------------------------------------------------
    def _seed(self, surface, rid, anchors, project=_PROJECT):
        """Land an `about_code` item through the REAL gated team-write path (journal + CAS +
        flush), not `backend.put` — so the anchors take the JSON round trip a teammate's read
        actually makes."""
        from mokata import team_journal, teamdb
        from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem
        item = MemoryItem(subject=rid, value="a team decision", id=rid, mtype=PERSISTENT,
                          status=ACTIVE, about_code=list(anchors),
                          provenance={"source": "test", "author": "b",
                                      "created_at": "2026-07-01T00:00:00+00:00"})
        payload = {"id": item.id, "mtype": item.mtype, "subject": item.subject,
                   "status": item.status, "doc": json.dumps(item.to_doc()), "project": project}
        team_journal.record_team_write(
            surface, op=team_journal.OP_UPDATE, table=teamdb.MEMORY_TABLE, key=item.id,
            payload=payload, ledger_id=1, project=project, actor="b", base_revision=None)
        self.assertEqual(1, _flush(surface).flushed)
        return item

    def _write(self, root, rel, text):
        ab = os.path.join(root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _anchor_props(self, store):
        from mokata.memory.healing import CODE_ANCHOR_STALE
        self.assertEqual("PostgresBackend", type(_real_backend(store)).__name__,
                         "this leg is reading the SQLite floor — every assertion below would "
                         "pass for the wrong reason (the DB.S7c1 false green)")
        return [p for p in store.detect_issues() if p.kind == CODE_ANCHOR_STALE]

    # --- 1. the anchors survive the shared round trip ---------------------
    def test_about_code_survives_the_team_write_round_trip(self):
        with tempfile.TemporaryDirectory() as b:
            sb = _writer(b, self.dsn)
            self._seed(sb, "decision-1", ["src/pay.py", "Pay.charge"])
            backend = _real_backend(_store(sb))
            self.assertEqual("PostgresBackend", type(backend).__name__)
            read_back = next(i for i in backend.all() if i.id == "decision-1")
            self.assertEqual(["src/pay.py", "Pay.charge"], read_back.about_code)

    # --- 2. the two-writer story ------------------------------------------
    def test_a_teammates_decision_goes_stale_in_MY_checkout(self):
        from mokata.knowledge import anchor_fingerprints as AF
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as a:
            sb = _writer(b, self.dsn)
            sa = _writer(a, self.dsn)               # a DIFFERENT repo on the SAME store

            # B records the decision; both checkouts hold the same file today.
            for root in (b, a):
                self._write(root, "src/pay.py", "RATE = 1\n")
            self._seed(sb, "decision-1", ["src/pay.py"])

            # A reads the teammate's decision and records what the anchored code looks like NOW.
            self.assertIn("decision-1", [i.id for i in _real_backend(_store(sa)).all()])
            AF.record_anchors(a, ["src/pay.py"])
            self.assertEqual([], self._anchor_props(_store(sa)))     # the control: silent

            # A then edits the file B's decision is about.
            self._write(a, "src/pay.py", "RATE = 2\n")
            props = self._anchor_props(_store(sa))
            self.assertEqual(1, len(props))
            self.assertEqual("decision-1", props[0].old.id)
            self.assertEqual("src/pay.py", props[0].anchor)
            self.assertIn("src/pay.py", props[0].rationale)

            # ...and B, whose checkout did NOT change, is told nothing. Same store, same item.
            AF.record_anchors(b, ["src/pay.py"])
            self.assertEqual([], self._anchor_props(_store(sb)))

    # --- 3. project scoping ------------------------------------------------
    def test_another_tenants_anchored_decision_never_surfaces(self):
        from mokata.knowledge import anchor_fingerprints as AF
        with tempfile.TemporaryDirectory() as o, tempfile.TemporaryDirectory() as a:
            so = _writer(o, self.dsn, project=_OTHER_PROJECT)
            sa = _writer(a, self.dsn)
            for root in (o, a):
                self._write(root, "src/pay.py", "RATE = 1\n")
            self._seed(so, "other-decision", ["src/pay.py"], project=_OTHER_PROJECT)

            AF.record_anchors(a, ["src/pay.py"])
            self._write(a, "src/pay.py", "RATE = 2\n")
            self.assertEqual([], self._anchor_props(_store(sa)))

    # --- 4. the refusal, on shared citations -------------------------------
    def test_the_approve_refusal_fires_on_a_shared_decision(self):
        from mokata.govern.code_anchor_gate import check_code_anchors
        from mokata.knowledge import anchor_fingerprints as AF
        from mokata.prior_art import RelatedDecision
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as a:
            sb = _writer(b, self.dsn)
            sa = _writer(a, self.dsn)
            for root in (b, a):
                self._write(root, "src/pay.py", "RATE = 1\n")
            self._seed(sb, "decision-1", ["src/pay.py"])

            # A cites the teammate's decision the way prior-art does — FROM THE SHARED ITEM.
            item = next(i for i in _real_backend(_store(sa)).all() if i.id == "decision-1")
            citation = RelatedDecision.from_item(item)
            self.assertEqual(["src/pay.py"], citation.about_code)

            AF.record_anchors(a, ["src/pay.py"])
            self.assertTrue(check_code_anchors(decisions=[citation], root=a).allowed)

            self._write(a, "src/pay.py", "RATE = 2\n")
            out = check_code_anchors(decisions=[citation], root=a)
            self.assertTrue(out.refused)
            self.assertEqual(["decision-1"], out.stale_ids)

    # --- 5. P4 on the live path --------------------------------------------
    def test_the_two_surfaces_still_agree_on_the_live_store(self):
        """P4 is a property of the shared verdict, so it cannot break on a different backend — but
        the store path and the gate path reach that verdict by different routes (a scoped
        `backend.all` scan vs a citation list), and only a live store exercises the first one."""
        from mokata.govern.code_anchor_gate import check_code_anchors
        from mokata.knowledge import anchor_fingerprints as AF
        from mokata.prior_art import RelatedDecision
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as a:
            sb = _writer(b, self.dsn)
            sa = _writer(a, self.dsn)
            for root in (b, a):
                self._write(root, "src/pay.py", "RATE = 1\n")
                self._write(root, "src/quiet.py", "X = 1\n")
            # A SYMBOL anchor rides along: no adopted graph in this repo, so H-6 must DECLINE it on
            # both surfaces — the "never fail loud where H-6 declines" direction, live.
            self._seed(sb, "decision-1", ["src/pay.py", "Pay.charge"])
            self._seed(sb, "decision-2", ["src/quiet.py"])

            item = next(i for i in _real_backend(_store(sa)).all() if i.id == "decision-1")
            AF.record_anchors(a, ["src/pay.py", "src/quiet.py"])
            self._write(a, "src/pay.py", "RATE = 2\n")

            props = self._anchor_props(_store(sa))
            self.assertEqual(["src/pay.py"], sorted(p.anchor for p in props))     # NOT Pay.charge
            gate = check_code_anchors(decisions=[RelatedDecision.from_item(item)], root=a)
            self.assertEqual(["src/pay.py"], gate.moved_anchors)                  # the same set

    # --- 6. P7 on the live path --------------------------------------------
    def test_nothing_on_the_live_path_restamps_the_record(self):
        from mokata.knowledge import anchor_fingerprints as AF
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as a:
            sb = _writer(b, self.dsn)
            sa = _writer(a, self.dsn)
            for root in (b, a):
                self._write(root, "src/pay.py", "RATE = 1\n")
            self._seed(sb, "decision-1", ["src/pay.py"])
            AF.record_anchors(a, ["src/pay.py"])
            before = AF.read_record(a)["src/pay.py"]["fingerprint"]
            self._write(a, "src/pay.py", "RATE = 2\n")
            for _ in range(3):
                self.assertEqual(1, len(self._anchor_props(_store(sa))))
            self.assertEqual(before, AF.read_record(a)["src/pay.py"]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
