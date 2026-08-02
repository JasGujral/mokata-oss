"""M-1/R9 (S2) — provenance highlight on the gate/review surfaces (R9, anti-rubber-stamp).

Doc 83's failure-mode review is the origin, and it is specific: mokata's P2 gate is the mitigation
nobody else has for memory poisoning, but it has a hole — "gate UX must show *provenance* of
proposed content (**a poisoned proposal a human rubber-stamps still lands**) — add
source-highlighting to review UI".

Until now the surfaces showed the CHANGE and nothing about what it displaces. `render_write` was
mtype + subject + value; the promotion renders named the binding; the review transition named the
diff and the actor. A human approving an edit to a trusted memory saw the same prompt whether the
item being overwritten was written by their teammate last week under a recorded approval, or by
nobody they can account for.

Scope is the TTY renders only. Doc 87 FR-3's provenance panel (the control-plane UI) is 0.2.0 and
deliberately out of scope here.

The contracts pinned, each with the mutation it catches:

  6. An edit/supersede/promotion/review/rollback surface NAMES the prior item's author and its
     approval. RED when a render drops the block, or renders the INCOMING item's provenance
     instead of the one being changed (which would show the reader their own work every time).
  7. Unknown renders as "unknown", out loud and never guessed. An unstamped item must not borrow
     `created_at` for `approved_at`, or the current actor for `approved_by`. This is
     `why_surfaced`'s discipline ("if no path reached this hit, no path is claimed") applied where
     being plausibly wrong is most expensive. RED when any field falls back to a neighbour.
  8. Rendering is READ-ONLY and TOTAL. A malformed provenance dict — a string, a None, a list, an
     object whose `__str__` raises — degrades to "unknown" and NEVER raises into the gate. A
     render that threw would take out the approval prompt itself, turning a cosmetic defect into
     an inability to approve anything about that item. RED when any read is unguarded.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern.ledger import AuditLedger
from mokata.memory import (
    DECISION, GUARDRAIL, RULE, MemoryItem, MemoryStore, provenance_block, provenance_lines,
)
from mokata.memory.backends import SQLiteBackend
from mokata.memory.intelligence import ADVISORY, UNKNOWN
from mokata.memory.review import render_rollback, render_transition

WHO = "ada"
OTHER = "grace"


def _stamped(subject="db", value="postgres", *, who=OTHER, ledger_id=42, **kw):
    item = MemoryItem.create(subject, value, author=who, source="brainstorm", **kw)
    item.approved_by = who
    item.approved_at = "2026-07-30T11:00:00+00:00"
    item.approval_ledger_id = ledger_id
    return item


def _unstamped(subject="db", value="postgres", **kw):
    """A pre-M-1/R9 item: written and stored before the consent chain existed."""
    return MemoryItem.create(subject, value, **kw)


class _Store:
    def __init__(self, identity=WHO):
        self.identity = identity

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = AuditLedger(os.path.join(self.tmp.name, "audit", "ledger.jsonl"))
        self.backend = SQLiteBackend(os.path.join(self.tmp.name, "memory.db"))
        self.store = MemoryStore(self.backend, enabled_types=(DECISION, "persistent", "episodic"),
                                 ledger=self.ledger, identity=self.identity)
        return self

    def __exit__(self, *exc):
        self.backend.close()
        self.tmp.cleanup()


# ============================================== pin 6 — the surfaces name what is being changed
class TheGateNamesWhatIsBeingDisplaced(unittest.TestCase):

    def test_a_superseding_remember_names_the_prior_items_author_and_approval(self):
        """The poisoning moment: the human is not storing a new fact, they are retiring an
        approved one. RED when `render_write` shows only the incoming value."""
        with _Store() as s:
            prior = _stamped("db", "postgres 14", who=OTHER, ledger_id=7)
            s.store.remember(prior, assume_yes=True)

            incoming = MemoryItem.create("db", "mysql", mtype=DECISION,
                                         supersedes=[prior.id])
            surface = s.store.render_write(incoming)

            self.assertIn("REPLACES an existing memory", surface)
            self.assertIn("postgres 14", surface)
            self.assertIn(OTHER, surface)            # whose item is being retired
            self.assertIn("ledger #", surface)       # and on whose approval it stands

    def test_a_plain_remember_is_unchanged(self):
        """No prior item, no provenance to highlight, no block. A brand-new fact renders exactly
        as it did before R9 — the highlight is for displacement, not decoration."""
        with _Store() as s:
            item = MemoryItem.create("fresh", "value", mtype=DECISION)
            surface = s.store.render_write(item)
            self.assertNotIn("provenance", surface)
            self.assertNotIn("REPLACES", surface)
            self.assertEqual(
                "mokata · propose to remember [decision] fresh = 'value'\n"
                "Nothing is stored unless you approve.", surface)

    def test_the_enforcement_promotion_names_whose_rule_it_is(self):
        """Making a rule HARD is the highest-leverage change in the store."""
        item = _stamped("no raw sql", "use the query builder", who=OTHER, ledger_id=13,
                        kind=RULE)
        with _Store() as s:
            surface = s.store.render_promotion(item, "advisory", "hard", "promote")
        self.assertIn("this rule:", surface)
        self.assertIn(OTHER, surface)
        self.assertIn("ledger #13", surface)

    def test_the_scope_promotion_names_whose_item_is_being_widened(self):
        item = _stamped(who=OTHER, ledger_id=5)
        with _Store() as s:
            surface = s.store.render_scope_promotion(item, "personal", "team")
        self.assertIn("this item:", surface)
        self.assertIn(OTHER, surface)

    def test_the_review_transition_shows_the_BASE_not_the_proposal(self):
        """Separation of duties says the approver is not the proposer. Showing whose work is being
        changed is what makes that check exercisable rather than a role lookup.

        RED when the render passes the proposal instead of the base — the reader would be shown
        their own incoming change's provenance, which tells them nothing."""
        base = _stamped("db", "postgres", who=OTHER, ledger_id=21)
        proposal = _stamped("db", "mysql", who=WHO, ledger_id=99)
        surface = render_transition(proposal, base, "draft", "in-review", WHO)

        self.assertIn("the item being changed:", surface)
        self.assertIn(OTHER, surface)
        self.assertIn("ledger #21", surface)
        self.assertNotIn("ledger #99", surface)

    def test_the_rollback_shows_both_sides(self):
        """A rollback retires a live approved item AND reinstates an older one. Reinstating a prior
        nobody can account for is its own way to poison a store."""
        current = _stamped("db", "mysql", who=WHO, ledger_id=30)
        prior = _stamped("db", "postgres", who=OTHER, ledger_id=11)
        surface = render_rollback(current, prior)

        self.assertIn("discarding (current):", surface)
        self.assertIn("restoring (prior):", surface)
        self.assertIn("ledger #30", surface)
        self.assertIn("ledger #11", surface)

    def test_every_surface_still_says_nothing_changes_without_approval(self):
        """The block is added to the prompt, never in place of the promise that closes it."""
        item = _stamped()
        with _Store() as s:
            for surface in (s.store.render_promotion(item, "advisory", "hard", "promote"),
                            s.store.render_scope_promotion(item, "personal", "team")):
                self.assertTrue(surface.rstrip().endswith(
                    "Nothing changes unless you approve."), surface)


# ================================================================ pin 7 — unknown, said out loud
class UnknownIsSaidNotGuessed(unittest.TestCase):

    def test_an_unstamped_item_reports_no_recorded_approval(self):
        """RED when `approved_by` falls back to the current actor, or `approved_at` borrows
        `created_at` — both would make an item that nobody approved look verified."""
        block = "\n".join(provenance_lines(_unstamped()))
        self.assertIn("approved by: unknown", block)
        self.assertIn("no recorded approval", block)

    def test_the_created_at_is_not_reused_as_an_approval_time(self):
        """When we know WHO approved but not WHEN, the time reports unknown — it does not borrow
        `created_at`, which is when the item was LEARNED and says nothing about when it was let in.

        Exercised on an item that HAS an approver, because that is the branch where the timestamp
        is rendered at all; an unstamped item never reaches it, so the fallback would be invisible
        there (this test was written the wrong way round first and caught nothing)."""
        item = _unstamped()
        item.approved_by = OTHER          # approver known, approved_at deliberately left empty
        created = item.provenance["created_at"]
        self.assertTrue(created)

        approval_line = [ln for ln in provenance_lines(item) if "approved by" in ln][0]
        self.assertIn(OTHER, approval_line)
        self.assertNotIn(created, approval_line)
        self.assertIn(UNKNOWN, approval_line)

    def test_an_approver_with_no_ledger_entry_says_so(self):
        """Half a chain is not a whole one. A name with no id is reported as exactly that, rather
        than rendered identically to an id-backed approval."""
        item = _unstamped()
        item.approved_by = OTHER
        item.approved_at = "2026-07-30T11:00:00+00:00"
        block = "\n".join(provenance_lines(item))
        self.assertIn(OTHER, block)
        self.assertIn("no ledger entry recorded", block)
        self.assertNotIn("ledger #", block)

    def test_the_approver_is_labelled_advisory_and_the_ledger_id_is_not(self):
        """Doc 52 M-1 bound M-1's attribution as advisory and the binding holds: the name is
        environment-derived, so it attributes rather than authenticates. The ledger id carries no
        such hedge — it names a hash-chained entry, and that one is checkable."""
        block = "\n".join(provenance_lines(_stamped()))
        self.assertIn(f"({ADVISORY})", block)
        self.assertIn("ledger #42", block)
        self.assertNotIn(f"ledger #42 ({ADVISORY})", block)

    def test_missing_authorship_fields_each_report_unknown(self):
        """All three authorship fields report independently — one missing field does not blank the
        line, and a present one is never suppressed by a missing neighbour."""
        item = MemoryItem(subject="s", value="v")
        item.provenance = {}
        lines = provenance_lines(item)
        written = [ln for ln in lines if "written by" in ln][0]
        self.assertEqual(3, written.count(UNKNOWN), written)   # author, source, created_at

        item.provenance = {"author": OTHER}
        written = [ln for ln in provenance_lines(item) if "written by" in ln][0]
        self.assertIn(OTHER, written)
        self.assertEqual(2, written.count(UNKNOWN), written)   # source + created_at only

    def test_no_item_renders_no_block_rather_than_a_block_of_unknowns(self):
        self.assertEqual([], provenance_lines(None))
        self.assertEqual("", provenance_block(None))


# ============================================================= pin 8 — read-only and TOTAL
class RenderingNeverBreaksTheGate(unittest.TestCase):

    class _Hostile:
        """A provenance value whose `__str__` raises — the shape a render must survive."""

        def __str__(self):
            raise RuntimeError("boom")

    def test_a_malformed_provenance_degrades_instead_of_raising(self):
        """Doc JSON can be hand-edited, imported, or written by a build that modelled it
        differently, so `provenance` can be ANY shape. RED for any unguarded read: the exception
        would surface from the approval prompt, and the human could no longer approve anything
        about that item — a cosmetic defect escalated into a gate outage."""
        for junk in ("a string", None, [1, 2], 7, {"author": self._Hostile()},
                     {"author": None, "source": [], "created_at": {}}):
            item = MemoryItem(subject="s", value="v")
            item.provenance = junk
            block = "\n".join(provenance_lines(item))
            self.assertIn(UNKNOWN, block)

    def test_a_hostile_approval_id_is_not_rendered_as_a_ledger_entry(self):
        """`True == 1` in Python. A doc carrying `true` must not render as "ledger #1"."""
        for hostile in (True, "12", 1.5, [], object()):
            item = MemoryItem(subject="s", value="v")
            item.approved_by = OTHER
            item.approval_ledger_id = hostile
            self.assertNotIn("ledger #", "\n".join(provenance_lines(item)))

    def test_a_duck_typed_item_without_the_fields_renders(self):
        """Duck-typed on purpose, like `downgrade_refusal`: an item from any backend, or a
        third-party object predating the fields, still renders."""
        class Bare:
            subject = "s"
            value = "v"

        block = "\n".join(provenance_lines(Bare()))
        self.assertIn(UNKNOWN, block)

    def test_rendering_mutates_nothing(self):
        """Read-only: the block is derived, and derives nothing back onto the item."""
        item = _stamped()
        before = json.dumps(item.to_dict(), sort_keys=True)
        provenance_lines(item)
        provenance_block(item)
        self.assertEqual(before, json.dumps(item.to_dict(), sort_keys=True))

    def test_a_broken_prior_lookup_never_breaks_the_write_prompt(self):
        """`render_write` resolves the items a write supersedes. A backend that raises on `get`
        must cost the reader a provenance line, never the ability to approve."""
        with _Store() as s:
            def _boom(_rid):
                raise RuntimeError("backend down")
            s.store.get = _boom

            item = MemoryItem.create("db", "mysql", mtype=DECISION, supersedes=["gone"])
            surface = s.store.render_write(item)
            self.assertIn("Nothing is stored unless you approve.", surface)

    def test_a_supersedes_id_that_no_longer_exists_is_simply_absent(self):
        with _Store() as s:
            item = MemoryItem.create("db", "mysql", mtype=DECISION,
                                     supersedes=["nonexistent-id"])
            surface = s.store.render_write(item)
            self.assertNotIn("REPLACES", surface)
            self.assertIn("Nothing is stored unless you approve.", surface)

    def test_the_gate_still_commits_with_a_malformed_prior(self):
        """The end-to-end shape of pin 8: a poisoned/garbled prior does not stop the human from
        deciding, and the write path is unaffected."""
        with _Store() as s:
            prior = _stamped("db", "postgres 14")
            s.store.remember(prior, assume_yes=True)
            with s.backend._connect() as conn:
                doc = json.loads(conn.execute(
                    "SELECT doc FROM memory WHERE id = ?", (prior.id,)).fetchone()[0])
                doc["provenance"] = "not a dict"
                conn.execute("UPDATE memory SET doc = ? WHERE id = ?",
                             (json.dumps(doc), prior.id))
                conn.commit()

            incoming = MemoryItem.create("db", "mysql", mtype=DECISION, supersedes=[prior.id])
            self.assertTrue(s.store.remember(incoming, assume_yes=True).committed)


if __name__ == "__main__":
    unittest.main()
