"""Stage 16 (MIGRATE-DROP-SOURCE-UNLEDGERED) — `--drop-source` is a DESTRUCTIVE delete with real
consent and NO audit record. This pins the record, and pins the extraction that lets the register
describe it.

THE DEFECT, from doc 84: `source.delete(it.id)` destroys every migrated item from the source store.
Its consent is a bespoke TTY prompt (`_default_drop_confirm` -> `read_yes_no`) and `assume_yes`
skips even that; no `ledger.record` fires for the drop (the ledger calls in `migrate_memory` are the
batch anchor and the per-item write-gate ones). Bespoke consent + NO record is the KNOWN_BYPASS
shape, and KNOWN_BYPASS is asserted EMPTY by the KB.S1 exit criterion.

THE RULING (Jas, doc 84): add the ledger record -> LEDGERED. The consent is REAL and DELIBERATE — a
batch drop is not a per-item write — so the consent is NOT redesigned here. Only the audit trail was
missing.

WHAT ELSE THIS FILE PINS, and why it is not scope creep. Adding the record makes the drop
LEDGERED-SHAPED but does not fix why it hid: `migrate_memory` contains a genuinely GATED write
(`dest.put` inside the gate's commit closure) AND this ungoverned one, and one register key cannot
be true of two regimes (REGISTER-KEY-COLLISION). So the drop is extracted into `_drop_source`, which
earns its own key and its own reason. `test_the_source_delete_has_its_own_register_key` is that
claim as a mechanical assertion rather than prose.

SECRET-SAFETY IS THE LOAD-BEARING PIN HERE (`test_the_drop_record_never_carries_item_content`).
This path DELETES memory items; a record that echoed what was deleted would be a secret leak wearing
an audit badge. The record carries counts and the source TOOL NAME, never an item's subject or
value — `run_reembed`'s rule, verbatim.
"""

import ast
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo
from mokata.memory import MemoryItem
from mokata.memory import migrate as migrate_mod
from mokata.memory.migrate import _default_drop_confirm, _drop_source, migrate_memory

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
MIGRATE_PY = os.path.join(SRC, "mokata", "memory", "migrate.py")

DROP_KIND = "migrate_drop_source"


def _silent(*_a):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(d)


class _FakeBackend:
    """A minimal in-memory backend double. `fail_delete_after` makes the Nth delete raise, which is
    how the PARTIAL drop is exercised — the case the record must not report as a completed one."""

    def __init__(self, items=(), fail_delete_after=None):
        self.rows = {i.id: i for i in items}
        self.deleted = []
        self.fail_delete_after = fail_delete_after

    def all(self, mtype=None, statuses=None):
        return list(self.rows.values())

    def get(self, item_id):
        return self.rows.get(item_id)

    def put(self, item):
        self.rows[item.id] = item

    def delete(self, item_id):
        if self.fail_delete_after is not None and len(self.deleted) >= self.fail_delete_after:
            raise OSError("source store went away mid-drop")
        self.deleted.append(item_id)
        return self.rows.pop(item_id, None) is not None

    def close(self):
        pass


class _Harness:
    """A real repo/surface with both backends replaced by doubles, so the drop path runs end to end
    without a live store. `to_backend` is a LOCAL destination (obsidian) — the team funnel is not
    what this stage is about."""

    def __init__(self, d, items=(), fail_delete_after=None):
        self.surface = _repo(d)
        self.src = _FakeBackend(items, fail_delete_after=fail_delete_after)
        self.dest = _FakeBackend()

    def _build(self, tool, root, config=None, clients=None, project=None):
        return self.dest if tool == "obsidian" else self.src

    def run(self, **kw):
        kw.setdefault("assume_yes", True)
        with mock.patch("mokata.memory.migrate.build_named_backend", self._build):
            return migrate_memory(self.surface, to_backend="obsidian", from_backend="sqlite",
                                  out=_silent, **kw)


def _drop_records(led):
    return [e for e in led.entries() if e.get("kind") == DROP_KIND]


# ------------------------------------------------------------------ the record itself

class TestTheDropLeavesAnAuditRecord(unittest.TestCase):

    def test_a_confirmed_drop_leaves_one_batch_record_naming_the_count_and_the_source(self):
        """THE STAGE. ONE record for the batch — not one per item, which would misrepresent what the
        human actually approved (a batch drop, on one decision)."""
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _Harness(d, items=[MemoryItem.create("db", "postgres"),
                                   MemoryItem.create("cache", "redis")])
            res = h.run(drop_source=True, ledger=led)

            self.assertEqual(res.dropped, 2)
            recs = _drop_records(led)
            self.assertEqual(len(recs), 1, "ONE batch record — a per-item record for a batch "
                                           "consent would misrepresent the human's decision")
            self.assertEqual(recs[0]["items"], 2)
            self.assertEqual(recs[0]["subject"], "sqlite", "the SOURCE TOOL the items were "
                                                           "destroyed from")
            self.assertTrue(recs[0]["complete"])

    def test_the_drop_record_never_carries_item_content(self):
        """SECRET-SAFETY, `run_reembed`'s rule verbatim. This path DELETES memory items; a record
        that echoed what was deleted is a secret leak wearing an audit badge. Counts and the source
        tool name only — never a subject, never a value."""
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _Harness(d, items=[MemoryItem.create("aws-root-key", "AKIA-SUPERSECRET-VALUE")])
            h.run(drop_source=True, ledger=led)

            recs = _drop_records(led)
            self.assertEqual(len(recs), 1, "assert the record EXISTS before asserting what it "
                                           "omits — a leak test that passes on an empty ledger "
                                           "proves nothing and would never go red")
            blob = repr(recs)
            self.assertNotIn("aws-root-key", blob, "the record must not echo an item's SUBJECT")
            self.assertNotIn("AKIA-SUPERSECRET-VALUE", blob,
                             "the record must not echo an item's VALUE")

    def test_a_non_interactive_assume_yes_drop_is_recorded_too(self):
        """`assume_yes` skips the prompt — that is the explicit non-interactive approval, the same
        posture as `run_reembed`, and it is DELIBERATELY unchanged. It becomes more defensible once
        the record exists: the approval leaves a trace even when no human was at a TTY."""
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _Harness(d, items=[MemoryItem.create("db", "postgres")])
            h.run(drop_source=True, assume_yes=True, ledger=led)
            self.assertEqual(len(_drop_records(led)), 1)

    def test_a_partial_drop_is_never_recorded_as_a_completed_one(self):
        """The drop dies halfway. Items WERE destroyed, so the ledger must say so — and must say how
        many, not how many were asked for. A record claiming a clean drop of everything would be
        worse than no record: it is an audit trail asserting something false."""
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _Harness(d, items=[MemoryItem.create("a", "1"), MemoryItem.create("b", "2"),
                                   MemoryItem.create("c", "3")], fail_delete_after=1)
            with self.assertRaises(OSError):
                h.run(drop_source=True, ledger=led)

            recs = _drop_records(led)
            self.assertEqual(len(recs), 1, "a destructive act that PARTIALLY happened still has to "
                                           "leave a record — that is the whole point of the row")
            self.assertEqual(recs[0]["items"], 1, "the count that ACTUALLY happened")
            self.assertEqual(recs[0]["attempted"], 3)
            self.assertFalse(recs[0]["complete"])

    def test_the_drop_path_still_works_with_no_ledger(self):
        """`ledger` is optional everywhere else in this function; the record must not make it
        mandatory."""
        with tempfile.TemporaryDirectory() as d:
            h = _Harness(d, items=[MemoryItem.create("db", "postgres")])
            self.assertEqual(h.run(drop_source=True).dropped, 1)


# ------------------------------------------------------------------ what must NOT be recorded

class TestNothingUnDroppedIsRecorded(unittest.TestCase):
    """A record is a claim that something was destroyed. Every path that destroys NOTHING must leave
    the drop ledger silent — otherwise the record means less than no record at all."""

    def test_a_declined_drop_leaves_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _Harness(d, items=[MemoryItem.create("db", "postgres")])
            res = h.run(drop_source=True, assume_yes=False, confirm=lambda _t: True,
                        drop_confirm=lambda _t: False, ledger=led)

            self.assertEqual(res.dropped, 0)
            self.assertEqual(_drop_records(led), [])
            self.assertEqual(h.src.deleted, [], "the source is left intact")

    def test_a_self_migrate_refusal_leaves_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _Harness(d, items=[MemoryItem.create("db", "postgres")])
            with mock.patch("mokata.memory.migrate.build_named_backend", h._build):
                res = migrate_memory(h.surface, to_backend="sqlite", from_backend="sqlite",
                                     assume_yes=True, drop_source=True, ledger=led, out=_silent)
            self.assertEqual(res.dropped, 0)
            self.assertEqual(_drop_records(led), [])

    def test_a_drop_refused_for_pending_writes_leaves_no_record(self):
        """The pending-writes refusal is LOAD-BEARING and unchanged: dropping while writes are
        journaled is the 'partially migrated then lost' case the command promises never to cause.
        Called on `_drop_source` DIRECTLY, so the guard cannot be skipped by a future caller."""
        led = AuditLedger(os.path.join(tempfile.mkdtemp(), "l.jsonl"))
        src = _FakeBackend([MemoryItem.create("db", "postgres")])
        dropped = _drop_source(src, list(src.all()), src_tool="sqlite", same_store=False,
                               pending=1, conflicts=0, assume_yes=True, drop_confirm=None,
                               ledger=led, emit=_silent)
        self.assertEqual(dropped, 0)
        self.assertEqual(src.deleted, [])
        self.assertEqual(_drop_records(led), [])

    def test_an_approved_drop_of_nothing_records_nothing(self):
        """Everything was blocked or refused, so `migrated_items` is empty and the loop destroys
        nothing. An approval is not a destruction: a record here would be the ledger asserting a
        drop that never happened."""
        led = AuditLedger(os.path.join(tempfile.mkdtemp(), "l.jsonl"))
        src = _FakeBackend()
        dropped = _drop_source(src, [], src_tool="sqlite", same_store=False, pending=0,
                               conflicts=0, assume_yes=True, drop_confirm=None, ledger=led,
                               emit=_silent)
        self.assertEqual(dropped, 0)
        self.assertEqual(_drop_records(led), [])

    def test_a_drop_refused_for_an_unresolved_conflict_leaves_no_record(self):
        led = AuditLedger(os.path.join(tempfile.mkdtemp(), "l.jsonl"))
        src = _FakeBackend([MemoryItem.create("db", "postgres")])
        dropped = _drop_source(src, list(src.all()), src_tool="sqlite", same_store=False,
                               pending=0, conflicts=1, assume_yes=True, drop_confirm=None,
                               ledger=led, emit=_silent)
        self.assertEqual(dropped, 0)
        self.assertEqual(src.deleted, [])


# ------------------------------------------------------------------ the extraction

class TestTheDropIsItsOwnRegisterableUnit(unittest.TestCase):
    """REGISTER-KEY-COLLISION, closed for this entry. The sweep keys a durable write by
    (file, QUALIFIED enclosing name), so while `source.delete` lived inside `migrate_memory` the
    same key had to describe BOTH a gate-run `dest.put` and an ungoverned delete. One key cannot be
    true of two regimes — which is exactly what the GATED entry had been getting wrong since SI.6."""

    def _enclosing_qualnames(self, attr):
        """Every qualified scope in migrate.py that contains a `<recv>.<attr>(...)` call."""
        found = []

        class V(ast.NodeVisitor):
            def __init__(self):
                self.stack = []

            def visit_FunctionDef(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_Call(self, node):
                if getattr(node.func, "attr", None) == attr:
                    found.append(".".join(self.stack) if self.stack else "<module>")
                self.generic_visit(node)

        with open(MIGRATE_PY, encoding="utf-8") as fh:
            V().visit(ast.parse(fh.read(), filename=MIGRATE_PY))
        return found

    def test_the_source_delete_has_its_own_register_key(self):
        self.assertEqual(self._enclosing_qualnames("delete"), ["_drop_source"],
                         "the destructive source delete must be the ONLY write in its own named "
                         "function, so its register entry describes one definition and one regime")

    def test_the_gated_destination_write_stays_in_migrate_memory(self):
        """The other half of the split: the extraction must not drag the GATED write out with it."""
        self.assertIn("migrate_memory", self._enclosing_qualnames("put"))
        self.assertNotIn("_drop_source", self._enclosing_qualnames("put"))

    def test_the_refusals_travel_with_the_drop_they_protect(self):
        """The same-store / pending / conflict refusals are guards ON the drop. They live INSIDE
        `_drop_source`, so no future caller can reach the delete without them — a destructive helper
        whose safety lives in its caller is one refactor away from being unsafe."""
        src = _FakeBackend([MemoryItem.create("db", "postgres")])
        self.assertEqual(
            _drop_source(src, list(src.all()), src_tool="sqlite", same_store=True, pending=0,
                         conflicts=0, assume_yes=True, drop_confirm=None, ledger=None,
                         emit=_silent),
            0, "a self-migration must refuse the drop — it would delete the just-written data")
        self.assertEqual(src.deleted, [])


# ------------------------------------------------------------------ the consent, checked not assumed

class TestTheBespokeDropConsentIsFailClosed(unittest.TestCase):
    """`drop_confirm or _default_drop_confirm` is not fail-closed BY CONSTRUCTION the way
    `migrate_channels`' `confirm or (lambda _t: False)` is — so it was checked rather than assumed.
    It IS fail-closed, one layer down: `_default_drop_confirm` calls `read_yes_no`, which returns
    False WITHOUT prompting when stdin is not a TTY (prompt.py, and it never blocks on a silent
    agent-harness stdin either). Pinned here so a future edit to either layer reds.

    NOT CHANGED BY THIS STAGE: the consent design itself. A consent change is not what was ruled."""

    def test_the_default_drop_confirm_declines_off_a_tty(self):
        with mock.patch.object(sys, "stdin", io.StringIO("y\n")):   # a StringIO is not a TTY
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertFalse(_default_drop_confirm("DROP everything?"),
                                 "a destructive drop must never be approved by a non-TTY stdin — "
                                 "non-interactive approval is `assume_yes`, explicitly")

    def test_the_drop_gate_resolves_to_that_fail_closed_default(self):
        """The wiring, not just the default: with no `drop_confirm` injected, an off-TTY run must
        leave the source intact."""
        with tempfile.TemporaryDirectory() as d:
            h = _Harness(d, items=[MemoryItem.create("db", "postgres")])
            with mock.patch.object(sys, "stdin", io.StringIO("y\n")), \
                 mock.patch("sys.stderr", io.StringIO()):
                res = h.run(drop_source=True, assume_yes=False, confirm=lambda _t: True)
            self.assertEqual(res.dropped, 0)
            self.assertEqual(h.src.deleted, [])

    def test_the_module_still_routes_the_drop_through_the_one_shared_reader(self):
        """Guards the finding itself: if `_default_drop_confirm` ever stopped going through the one
        shared fail-closed reader, the two tests above would still pass against a bespoke prompt
        that happened to decline for its own reasons."""
        from mokata.prompt import read_yes_no
        self.assertIs(migrate_mod.read_yes_no, read_yes_no)


if __name__ == "__main__":
    unittest.main()
