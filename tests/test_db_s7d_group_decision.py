"""DB.S7d — I1b's two owed halves: duplicate-both-active, and the one-prompt group decision.

DB.S6 shipped I1b as a REFUSAL and nothing more (`team_journal.retire_without_replace_refusal`):
a `kept-local` resolution that retires a fact is refused while its replacement sibling in the same
approval group is undecided, discarded or blocked. Its own docstring names what it deliberately did
NOT build, and `02:231` owes both to this stage:

  (a) the DUPLICATE-BOTH-ACTIVE direction. The mirror image of retire-without-replace: DROP the
      retirement (`kept-remote`) while its replacement lands, and the subject ends up with TWO
      active facts. DB.S6 left it alone on the grounds that it is visible and loses nothing — but
      "loses nothing" is not "is decided". Left as-is it is a group half-decided in silence, and
      the reason it could not ship at DB.S6 is that refusing it there would have been a deadlock:
      there was no way to decide the group as a unit. (b) is what makes (a) refusable.

  (b) the GROUP-DECISION ERGONOMICS — deciding a whole approval in ONE prompt.

The pinned contracts, and the mutation that turns each one RED:

  P1  dropping a retirement whose replacement is landing is REFUSED — mutation: return None from
      `duplicate_both_active_refusal` and the two-active end state commits in silence.
  P2  the refusal fires in BOTH orders, so whichever member the human decides second is caught.
  P3  DB.S6's deliberate allowance survives: the replacement is decidable while the retirement is
      UNDECIDED (that direction loses nothing and is not this guard's business).
  P4  the group surface decides every CONFLICT member of one approval in ONE gate prompt —
      mutation: gate per member and the prompt count goes to N.
  P5  THE LOAD-BEARING ONE. The group surface CALLS `retire_without_replace_refusal`; it does not
      re-implement its predicate. Mutation: bypass the guard in the group path and a stranded
      retirement (its replacement discarded in an earlier one-at-a-time pass) becomes approvable in
      ONE prompt — precisely the regression the DB.S6 docstring warns of ("a half-built group
      verdict is one a human would trust").
  P6  a group decision is ATOMIC: a refusal on ANY member leaves ZERO members resolved — mutation:
      commit the members that passed and the group is left half-decided, which is the very state
      the surface exists to prevent.
  P7  the group resolve reaches disk as ONE append — mutation: loop `resolve()` per member and a
      crash between two appends leaves a half-resolved approval on disk.
  P8  a uniform verdict cannot produce EITHER failure: keep-local lands retirement + replacement
      together, keep-remote drops both. Neither stranding nor duplication is reachable through it.
  P9  regression: a solo conflict, and a group with nothing retiring in it, are untouched.
  P10 the ASKERS settle nothing, pinned by BEHAVIOUR: `_ask_group` (and the `_ask_conflict` sibling
      its docstring defines it by) leaves the repo byte-identical on every decision path — approve,
      discard, defer-under-assume_yes, and the fail-closed non-interactive defer. Mutation: any
      durable write in the asker — `journal._append`, `journal.resolve_group`, `open(...,"w")` — goes
      RED. This was PROSE-ONLY before: D5 classifies only the broad `except`, and SI.6's sweep is a
      static scan over write NAMES, so it catches a direct `open(...,"w")` and is blind to the
      delegated write a real regression makes. Verified: an `_append` inside `_ask_group` passed all
      4972 unit tests before P10 existed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.item import ACTIVE, SUPERSEDED
from test_db_s6_cross_writer_healing import (_TxnPg, _flush, _journal, _store,  # noqa: F401
                                             _team_repo)

KEEP_LOCAL = "kept-local"
KEEP_REMOTE = "kept-remote"


def _retire_and_replace(surface):
    """The exact shape `apply_proposal` on a contradiction produces, and the shape DB.S6's own I1b
    tests use: retire `old-fact` (status SUPERSEDED) and install `new-fact`, both under ONE approval
    id. `new-fact`'s CAS base is stale, so the group rolls back and BOTH surface as conflicts."""
    pg = _TxnPg()
    pg.plant("old-fact", json.dumps({"id": "old-fact", "value": "the old value"}), revision=1)
    pg.plant("new-fact", json.dumps({"id": "new-fact", "value": "theirs"}), revision=9)
    _journal(surface, "old-fact", "the old value", ledger_id=7, base_revision=1,
             status=SUPERSEDED)
    _journal(surface, "new-fact", "the new value", ledger_id=7, base_revision=1)
    res = _flush(surface, pg)
    assert (0, 2) == (res.flushed, res.conflicts), res
    return pg


def _member(surface, rid):
    return next(p for p in _store(surface).cross_writer_proposals() if p.old.id == rid)


def _statuses(surface):
    from mokata import team_journal
    _e, status, _c, _o = team_journal.TeamJournal.for_surface(surface)._replay()
    return status


# --------------------------------------------------------------------------- (a) P1–P3
class TestDuplicateBothActiveIsRefused(unittest.TestCase):
    """(a) — the direction DB.S6 left open.

    A rolled-back approval surfaces as N separate conflicts. Discard the RETIREMENT (keep their
    row, which is still the old fact, ACTIVE) and approve the REPLACEMENT (which lands ACTIVE), and
    the subject now carries two active facts from one approval that was never decided as a whole.

    DB.S6 called this benign because nothing is lost. Nothing IS lost — but the approval is left
    half-decided with no record that it was, and the human who made the two decisions never saw
    them as one. This refuses that end state and points at the group surface, which is the only
    reason refusing it is not a deadlock."""

    def test_dropping_the_retirement_while_its_replacement_is_landing_is_refused(self):
        """MUTATION: return None from `duplicate_both_active_refusal` — this goes RED and the
        two-active state commits with no prompt, no record, and no way to notice."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_proposal(_member(surface, "new-fact"), "approve",
                                            assume_yes=True).changed)
            res = _store(surface).apply_proposal(_member(surface, "old-fact"), "discard",
                                                 assume_yes=True)
            self.assertFalse(res.changed,
                             "the retirement was dropped while its replacement was landing — the "
                             "subject now has TWO active facts and nobody decided that")
            self.assertTrue(res.refused)
            self.assertIn("two active", res.message.lower())

    def test_it_fires_in_the_other_order_too(self):
        """P2 — the human can reach the same end state either way round. Drop the retirement
        first, then approve the replacement: whichever decision is SECOND is the one that creates
        the duplicate, so the guard has to catch both, not just the ordering it was written for."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_proposal(_member(surface, "old-fact"), "discard",
                                            assume_yes=True).changed)
            res = _store(surface).apply_proposal(_member(surface, "new-fact"), "approve",
                                                 assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.refused)
            self.assertIn("two active", res.message.lower())

    def test_the_refusal_names_the_one_prompt_way_out(self):
        """A refusal with no exit is a deadlock. This one is only defensible because (b) exists,
        so it has to SAY so — the message names the group decision as the way to settle it."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            _store(surface).apply_proposal(_member(surface, "new-fact"), "approve",
                                           assume_yes=True)
            msg = _store(surface).apply_proposal(_member(surface, "old-fact"), "discard",
                                                 assume_yes=True).message
            self.assertIn("whole approval", msg.lower())

    def test_the_db_s6_allowance_is_preserved(self):
        """P3 — the REGRESSION half, and the reason this guard is narrow. DB.S6 pinned that the
        replacement side is decidable while the retirement is UNDECIDED. That is still true: an
        undecided retirement is not a dropped one, nothing is duplicated yet, and blocking it here
        would break `test_the_replacement_side_is_never_blocked_by_the_guard`."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_proposal(_member(surface, "new-fact"), "approve",
                                            assume_yes=True).changed,
                            "the new guard blocked a decision DB.S6 deliberately allows")

    def test_dropping_both_sides_is_never_a_duplicate(self):
        """Keep THEIR row for both members: the retirement is dropped and the replacement is
        dropped, so the shared store is exactly the teammate's state. Nothing is duplicated and
        nothing is lost — the guard must stay out of it."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            for rid in ("new-fact", "old-fact"):
                self.assertTrue(_store(surface)
                                .apply_proposal(_member(surface, rid), "discard",
                                                assume_yes=True).changed,
                                f"the guard blocked a clean discard of '{rid}'")


# --------------------------------------------------------------------------- (b) P4–P8
class TestTheOnePromptGroupDecision(unittest.TestCase):
    """(b) — deciding a whole approval in ONE prompt.

    This is the ergonomic debt `02:231` records: until it exists, a human resolving a rolled-back
    approval in journal order pays an extra `mokata sync` pass, because the retirement is refused
    and only becomes allowable once its replacement is decided.

    It is also what makes (a) refusable rather than a deadlock. Both halves are one stage for that
    reason: shipping the refusal without the surface would have left a human with a decision they
    could not make."""

    def test_it_decides_every_conflicted_member_of_the_approval(self):
        """P4 — one call, one verdict, every member settled. The whole point."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            res = _store(surface).apply_group_decision(_member(surface, "old-fact"), "approve",
                                                       assume_yes=True)
            self.assertTrue(res.changed)
            self.assertEqual([], _store(surface).cross_writer_proposals(),
                             "a member of the approval was left conflicted after a GROUP decision")

    def test_it_asks_exactly_once_for_the_whole_group(self):
        """P4's teeth. MUTATION: gate per member and this goes RED at 2 — which is the current
        one-at-a-time behaviour wearing a group-shaped name, and the thing this stage exists to
        remove."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            prompts = []

            def _confirm(text):
                prompts.append(text)
                return True

            _store(surface).apply_group_decision(_member(surface, "old-fact"), "approve",
                                                 confirm=_confirm)
            self.assertEqual(1, len(prompts),
                             f"the group decision asked {len(prompts)} times for ONE approval")

    def test_the_one_prompt_names_every_member_it_decides(self):
        """A single prompt that hides what it covers is worse than two honest ones: the human is
        approving N writes and must see N writes."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            prompts = []
            _store(surface).apply_group_decision(
                _member(surface, "old-fact"), "approve",
                confirm=lambda t: (prompts.append(t), True)[1])
            self.assertIn("old-fact", prompts[0])
            self.assertIn("new-fact", prompts[0])

    def test_a_keep_local_group_lands_retirement_and_replacement_together(self):
        """P8 — the end state a uniform verdict produces. Both writes land, so the fact is retired
        AND replaced: neither the stranding I1b refuses nor the duplication (a) refuses is
        reachable through this path at all."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_group_decision(_member(surface, "old-fact"), "approve",
                                                  assume_yes=True).changed)
            self.assertEqual(2, _flush(surface, pg).flushed)
            self.assertEqual("the new value", json.loads(pg.rows["new-fact"]["doc"])["value"])
            self.assertEqual(SUPERSEDED, json.loads(pg.rows["old-fact"]["doc"])["status"])

    def test_a_keep_remote_group_drops_every_member(self):
        """P8's other half: keep THEIRS for the whole approval. Both local writes are dropped, the
        shared store is untouched, and — the part that matters — the old fact is still ACTIVE with
        no replacement beside it."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_group_decision(_member(surface, "old-fact"), "discard",
                                                  assume_yes=True).changed)
            self.assertEqual([], _store(surface).cross_writer_proposals())
            self.assertEqual(0, _flush(surface, pg).flushed, "a dropped write still flushed")
            self.assertEqual("the old value", json.loads(pg.rows["old-fact"]["doc"])["value"])
            self.assertEqual("theirs", json.loads(pg.rows["new-fact"]["doc"])["value"])

    def test_it_works_from_any_member_of_the_group(self):
        """The human reaches the group through whichever conflict they happened to be looking at.
        Entering from the replacement must decide the same group as entering from the retirement."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_group_decision(_member(surface, "new-fact"), "approve",
                                                  assume_yes=True).changed)
            self.assertEqual([], _store(surface).cross_writer_proposals())


# --------------------------------------------------------------------------- P5
class TestTheShippedGuardIsStillOnThePath(unittest.TestCase):
    """P5 — THE regression this stage is most able to introduce, and the one DB.S6's docstring
    names in advance: "building half of it here would be worse than the gap, because a half-built
    group verdict is one a human would trust".

    A group decision looks safe by construction — one verdict for every member, so no member can
    strand another. That reasoning is only true for members that are STILL CONFLICTED. A member
    decided in an EARLIER one-at-a-time pass is already out of the group's reach: discard the
    replacement on Monday, and Tuesday's "keep local for the whole approval" has exactly one member
    left to decide — the retirement — and lands it with nothing holding the fact.

    So the group surface has to run the SAME guard the single-member path runs. Not a group-shaped
    re-derivation of it: the same function, on the same predicate."""

    def test_a_group_verdict_cannot_land_a_retirement_whose_replacement_was_discarded(self):
        """MUTATION: skip `retire_without_replace_refusal` in the group path and this goes RED —
        the retirement lands in ONE prompt and the fact is gone."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _retire_and_replace(surface)
            self.assertTrue(_store(surface)
                            .apply_proposal(_member(surface, "new-fact"), "discard",
                                            assume_yes=True).changed)
            res = _store(surface).apply_group_decision(_member(surface, "old-fact"), "approve",
                                                       assume_yes=True)
            self.assertFalse(res.changed,
                             "a GROUP verdict retired a fact whose replacement had already been "
                             "discarded — the one-prompt surface became a way around the guard")
            self.assertTrue(res.refused)
            self.assertIn("resolve them together", res.message)
            _flush(surface, pg)
            self.assertEqual("the old value", json.loads(pg.rows["old-fact"]["doc"])["value"],
                             "the fact was retired and nothing replaced it")

    def test_the_group_path_calls_the_shipped_guard_rather_than_its_own_copy(self):
        """The STRUCTURAL half. The behavioural pin above can be satisfied by a second, parallel
        implementation of the same predicate — and two implementations of "does this strand a
        sibling" drift, which is the exact failure `_approval_key`'s docstring was written to
        prevent. This asserts the group path is routed through the SHIPPED function: neutralise
        `retire_without_replace_refusal` and the group decision must stop refusing."""
        import mokata.team_journal as tj
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            _store(surface).apply_proposal(_member(surface, "new-fact"), "discard",
                                           assume_yes=True)
            original = tj.retire_without_replace_refusal
            calls = []
            try:
                tj.retire_without_replace_refusal = (
                    lambda *a, **k: calls.append(a) or original(*a, **k))
                res = _store(surface).apply_group_decision(_member(surface, "old-fact"), "approve",
                                                           assume_yes=True)
            finally:
                tj.retire_without_replace_refusal = original
            self.assertTrue(calls, "the group path never called the shipped I1b guard")
            self.assertTrue(res.refused)


# --------------------------------------------------------------------------- P6–P7
class TestTheGroupDecisionIsAtomic(unittest.TestCase):
    """P6/P7 — "decide the whole approval" has to mean the whole approval or none of it.

    A group surface that settles the members it can and skips the ones it cannot is not a group
    decision; it is the one-at-a-time path with a single prompt in front of it, and it produces
    exactly the half-decided approval both guards exist to prevent — except now the human believes
    they settled it."""

    def _three_member_group(self, surface):
        """Three writes under ONE approval. `keeper` will apply; `old-fact`/`new-fact` are the
        retire+replace pair. All three lose the CAS together, so all three surface as conflicts."""
        pg = _TxnPg()
        for rid, val in (("old-fact", "the old value"), ("new-fact", "theirs"),
                         ("keeper", "theirs")):
            pg.plant(rid, json.dumps({"id": rid, "value": val}), revision=9)
        _journal(surface, "old-fact", "the old value", ledger_id=7, base_revision=1,
                 status=SUPERSEDED)
        _journal(surface, "new-fact", "the new value", ledger_id=7, base_revision=1)
        _journal(surface, "keeper", "kept", ledger_id=7, base_revision=1)
        res = _flush(surface, pg)
        assert (0, 3) == (res.flushed, res.conflicts), res
        return pg

    def test_a_refusal_on_one_member_leaves_zero_members_resolved(self):
        """P6. MUTATION: resolve the members that passed and this goes RED — two of three land, the
        approval is half-decided, and the prompt said it was settled."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            self._three_member_group(surface)
            _store(surface).apply_proposal(_member(surface, "new-fact"), "discard",
                                           assume_yes=True)
            before = _statuses(surface)
            res = _store(surface).apply_group_decision(_member(surface, "keeper"), "approve",
                                                       assume_yes=True)
            self.assertTrue(res.refused)
            self.assertEqual(before, _statuses(surface),
                             "a refused group decision still changed some members' state")

    def test_nothing_is_written_on_the_way_to_refusing(self):
        """The guard runs BEFORE the gate, for the same reason `_downgrade_refusal` does: a
        resolution mokata will not commit must not cost the human an approval prompt."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            self._three_member_group(surface)
            _store(surface).apply_proposal(_member(surface, "new-fact"), "discard",
                                           assume_yes=True)
            prompts = []
            _store(surface).apply_group_decision(
                _member(surface, "keeper"), "approve",
                confirm=lambda t: (prompts.append(t), True)[1])
            self.assertEqual([], prompts,
                             "the human was asked to approve a decision that was then refused")

    def test_the_whole_group_reaches_disk_in_one_append(self):
        """P7. The journal is an append-only log replayed on every read, so N separate appends is
        N-1 windows in which a crash leaves an approval partly resolved on disk — the durable
        version of the half-decided state. MUTATION: loop `resolve()` per member and the funnel
        count goes to 3."""
        import mokata.team_journal as tj
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            self._three_member_group(surface)
            appends = []
            original = tj.TeamJournal._append_all

            def _spy(self, recs):
                sizes = sum(1 for r in recs if r.get("kind") == "resolved")
                if sizes:
                    appends.append(sizes)
                return original(self, recs)

            try:
                tj.TeamJournal._append_all = _spy
                self.assertTrue(_store(surface)
                                .apply_group_decision(_member(surface, "old-fact"), "approve",
                                                      assume_yes=True).changed)
            finally:
                tj.TeamJournal._append_all = original
            self.assertEqual([3], appends,
                             f"the group's 3 resolutions reached disk as {appends} appends")


# --------------------------------------------------------------------------- P10
def _tree_snapshot(root):
    """Every byte under the repo — journal, SQLite store, ledger, manifest, artifacts.

    Deliberately WHOLE-TREE rather than journal-only: the write that breaks the asker's charter is
    by definition one nobody anticipated, so a snapshot scoped to the file the pin's author thought
    of would miss it. Bytes, not mtimes — a re-read that rewrites identical content is not a write
    in the sense this pins."""
    snap = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            p = os.path.join(base, name)
            with open(p, "rb") as fh:
                snap[os.path.relpath(p, root)] = fh.read()
    return snap


class TestTheAskersAreBehaviourallyWriteFree(unittest.TestCase):
    """P10 — `_ask_group` ASKS and settles NOTHING, pinned by behaviour rather than by register.

    The gap this closes: `_ask_group`'s write-freedom was asserted three times in PROSE (its own
    docstring, `_ask_conflict`'s, and the D5 register entry) and nowhere in a contract. D5 classifies
    only its broad `except`; SI.6's zero-bypass sweep is a STATIC scan over a fixed vocabulary of
    write names (`atomic_write_text`, `write_text`, `open(w)`, `backend.put/update/delete`), so it
    sees a direct `open(...,"w")` in the asker — and is BLIND to the delegated write that a real
    regression takes: `journal._append`, `journal.resolve_group`, `store.apply_proposal`. Verified,
    not assumed: an `_append` of one record inside `_ask_group` passes all 4972 unit tests on the
    pre-P10 suite, SI.6 and D5 included.

    That matters here more than for an ordinary helper, because R4's ONE-resolver split is the thing
    being protected. `sync` asks, the STORE settles. An asker that also writes recreates the second
    resolver R4 deleted — and it does so where nothing is watching, since the write would be made
    with no WriteGate, no ledger record, and no approval group.

    MUTATION for every test below: add ANY durable write to the asker (`_MUT.resolve_group(...)`,
    `_append(...)`, an `open(...,"w")`) and the byte-comparison goes RED — on all four decision
    paths, including the two that never reach a human."""

    def _group(self, surface):
        from mokata import team_journal
        conflicts = team_journal.TeamJournal.for_surface(surface).conflicts()
        self.assertEqual(2, len(conflicts))
        return conflicts

    def _assert_write_free(self, ask, *, expected, **kw):
        """Run one asker down one decision path and assert (a) it produced a real verdict, (b) it
        emitted its prompt, and (c) it changed not one byte on disk.

        (a) and (b) are not padding — without them a stubbed-out asker that does nothing at all
        would satisfy (c) vacuously, and a pin that a no-op passes is not a pin."""
        from mokata import team_journal
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            _retire_and_replace(surface)
            group = self._group(surface)
            said = []
            before = _tree_snapshot(d)
            decision = ask(surface, group, emit=said.append, **kw)
            after = _tree_snapshot(d)

            self.assertEqual(expected, decision)
            self.assertIn(decision, ("approve", "discard", "defer"),
                          "the verdict must be the healing path's vocabulary")
            self.assertTrue(said and "sync conflict" in said[0],
                            "the asker must actually ASK — a silent no-op passes the byte "
                            "comparison for the wrong reason")
            changed = sorted(k for k in set(before) | set(after)
                             if before.get(k) != after.get(k))
            self.assertEqual(
                [], changed,
                f"the asker WROTE while only asking — {changed}. `sync` asks and the STORE settles "
                f"(R4's one resolver); a write from here has no WriteGate, no ledger record and no "
                f"approval group behind it. Hand the decision to "
                f"`MemoryStore.apply_group_decision` instead.")
            # and the conflicts are all still conflicted — nothing was settled behind the prompt
            self.assertEqual(2, len(team_journal.TeamJournal.for_surface(surface).conflicts()),
                             "both members must still be CONFLICTED after merely being asked about")

    # --- the group asker: all four decision paths -------------------------------------------
    def _ask_group(self, surface, group, *, emit, **kw):
        from mokata import team_journal
        return team_journal._ask_group(group, emit=emit, **kw)

    def test_the_group_asker_writes_nothing_when_the_human_keeps_local(self):
        self._assert_write_free(self._ask_group, expected="approve",
                                assume_yes=False, confirm=lambda _t: True)

    def test_the_group_asker_writes_nothing_when_the_human_keeps_remote(self):
        """The `discard` path is the one that DROPS the local writes, so "it settles nothing here"
        is load-bearing: the drop must happen inside the store's gate, not as a side effect of the
        question."""
        self._assert_write_free(self._ask_group, expected="discard",
                                assume_yes=False, confirm=lambda _t: False)

    def test_the_group_asker_writes_nothing_when_it_defers_under_assume_yes(self):
        """`assume_yes` cannot decide a conflict, so the asker defers — and must not "helpfully"
        record the deferral either. Deferring IS the absence of a durable act."""
        self._assert_write_free(self._ask_group, expected="defer",
                                assume_yes=True, confirm=None)

    def test_the_group_asker_writes_nothing_on_the_fail_closed_path(self):
        """The D5 handler's own path, now pinned on BOTH axes: non-interactive stdin fails closed to
        `defer` (D5) *and* leaves the tree untouched (here). A half-written group on the way to
        failing closed would be the worst of the two outcomes."""
        import mokata.prompt as prompt

        def _raise(*_a, **_kw):
            raise OSError("captured stdin")     # pytest's shape; EOFError is unittest's

        original = prompt.read_yes_no
        try:
            prompt.read_yes_no = _raise
            self._assert_write_free(self._ask_group, expected="defer",
                                    assume_yes=False, confirm=None)
        finally:
            prompt.read_yes_no = original

    # --- the sibling it defines itself by --------------------------------------------------
    def _ask_conflict(self, surface, group, *, emit, **kw):
        from mokata import team_journal
        return team_journal._ask_conflict(group[0], emit=emit, **kw)

    def test_the_single_asker_is_write_free_too(self):
        """`_ask_group`'s docstring defines its charter BY REFERENCE — "settles nothing, exactly as
        `_ask_conflict` settles nothing". A reference to an unpinned contract is not a contract, so
        the sibling is swept here as well, and the two cannot drift apart."""
        for expected, kw in (("approve", dict(assume_yes=False, confirm=lambda _t: True)),
                             ("discard", dict(assume_yes=False, confirm=lambda _t: False)),
                             ("defer", dict(assume_yes=True, confirm=None))):
            with self.subTest(decision=expected):
                self._assert_write_free(self._ask_conflict, expected=expected, **kw)


# --------------------------------------------------------------------------- P9
class TestRegressionTheGuardsStayNarrow(unittest.TestCase):
    """P9 — both new behaviours must be invisible to everything that is not a rolled-back
    retire+replace approval."""

    def test_a_solo_conflict_is_untouched(self):
        """An ordinary one-write conflict has no group. Both new guards must be invisible to it,
        including when that lone write is itself a retirement."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            pg.plant("solo", json.dumps({"id": "solo", "value": "theirs"}), revision=9)
            _journal(surface, "solo", "mine", ledger_id=3, base_revision=1, status=SUPERSEDED)
            _flush(surface, pg)
            for decision in ("approve", "discard"):
                with tempfile.TemporaryDirectory() as d2:
                    s2 = _team_repo(d2)
                    pg2 = _TxnPg()
                    pg2.plant("solo", json.dumps({"id": "solo", "value": "theirs"}), revision=9)
                    _journal(s2, "solo", "mine", ledger_id=3, base_revision=1, status=SUPERSEDED)
                    _flush(s2, pg2)
                    self.assertTrue(_store(s2)
                                    .apply_proposal(_member(s2, "solo"), decision,
                                                    assume_yes=True).changed,
                                    f"a guard blocked '{decision}' on a solo conflict")

    def test_a_group_with_no_retirement_in_it_is_untouched(self):
        """Two plain updates under one approval: nothing retires, so neither guard has anything to
        say. Members stay independently decidable in any order."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            for rid in ("a", "b"):
                pg.plant(rid, json.dumps({"id": rid, "value": "theirs"}), revision=9)
                _journal(surface, rid, "mine", ledger_id=7, base_revision=1, status=ACTIVE)
            _flush(surface, pg)
            self.assertTrue(_store(surface)
                            .apply_proposal(_member(surface, "a"), "discard",
                                            assume_yes=True).changed)
            self.assertTrue(_store(surface)
                            .apply_proposal(_member(surface, "b"), "approve",
                                            assume_yes=True).changed)

    def test_a_group_decision_on_a_solo_conflict_decides_just_it(self):
        """The group surface must degrade to the single-member case rather than refuse: a conflict
        with no approval group is a group of one."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _TxnPg()
            pg.plant("solo", json.dumps({"id": "solo", "value": "theirs"}), revision=9)
            _journal(surface, "solo", "mine", ledger_id=None, base_revision=1, status=ACTIVE)
            _flush(surface, pg)
            self.assertTrue(_store(surface)
                            .apply_group_decision(_member(surface, "solo"), "approve",
                                                  assume_yes=True).changed)
            self.assertEqual([], _store(surface).cross_writer_proposals())


# --------------------------------------------------------------------------- deadlock freedom
class TestNoStateIsADeadEnd(unittest.TestCase):
    """Adding a SECOND refusal to a two-member approval is exactly how a safety property turns into
    a deadlock: I1b refuses `kept-local` on a stranded retirement, and (a) refuses `kept-remote` on
    one whose replacement is landing. If both fire on the same member, the human is holding a
    conflict with no legal move and no way to make progress.

    So this walks the reachable states and asserts every one of them has a way out. It is the
    executable form of the argument that lets (a) exist at all."""

    def _legal_moves(self, build):
        """The decisions that would be ALLOWED from the state `build` leaves behind, asked without
        committing anything: each is tried on its own throwaway repo."""
        moves = []
        for rid in ("old-fact", "new-fact"):
            for decision in ("approve", "discard"):
                with tempfile.TemporaryDirectory() as d:
                    surface = _team_repo(d)
                    _retire_and_replace(surface)
                    build(surface)
                    live = [p.old.id for p in _store(surface).cross_writer_proposals()]
                    if rid not in live:
                        continue
                    if _store(surface).apply_proposal(_member(surface, rid), decision,
                                                      assume_yes=True).changed:
                        moves.append((rid, decision))
        return moves

    def test_every_reachable_state_has_a_legal_move(self):
        states = {
            "nothing decided yet": lambda _s: None,
            "replacement approved": lambda s: _store(s).apply_proposal(
                _member(s, "new-fact"), "approve", assume_yes=True),
            "replacement discarded": lambda s: _store(s).apply_proposal(
                _member(s, "new-fact"), "discard", assume_yes=True),
            "retirement discarded": lambda s: _store(s).apply_proposal(
                _member(s, "old-fact"), "discard", assume_yes=True),
        }
        for name, build in states.items():
            with self.subTest(state=name):
                self.assertTrue(self._legal_moves(build),
                                f"'{name}' is a DEAD END — both guards refuse every remaining "
                                f"decision, so the human cannot make progress at all")

    def test_the_group_decision_is_a_way_out_of_the_worst_state(self):
        """And the state with the FEWEST one-at-a-time moves — a discarded replacement, where I1b
        refuses the retirement outright — is still resolvable in one prompt, by discarding the
        whole approval. A guard whose only escape is another guard's blind spot is not a design."""
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _retire_and_replace(surface)
            _store(surface).apply_proposal(_member(surface, "new-fact"), "discard",
                                           assume_yes=True)
            self.assertTrue(_store(surface)
                            .apply_group_decision(_member(surface, "old-fact"), "discard",
                                                  assume_yes=True).changed)
            self.assertEqual([], _store(surface).cross_writer_proposals())
            self.assertEqual(0, _flush(surface, pg).flushed)
            self.assertEqual("the old value", json.loads(pg.rows["old-fact"]["doc"])["value"],
                             "the fact survived the whole exchange")


# --------------------------------------------------------------------------- the reachable surface
class TestSyncOffersTheGroupDecision(unittest.TestCase):
    """`mokata sync` is where a human actually meets a conflict, so it is where the group decision
    has to be offered. A store method nobody can reach is not a surface, and the debt `02:231`
    records is stated in `sync`'s own units: "a human resolving in journal order pays one extra
    `mokata sync` pass"."""

    def test_sync_settles_a_rolled_back_approval_in_one_question(self):
        """MUTATION: leave `sync` on the per-conflict loop and this goes RED at 2 questions — and
        the first of them is the retirement, which is refused, which is the extra pass."""
        from mokata import team_health, team_journal
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _retire_and_replace(surface)
            asked = []
            res = team_journal.sync(
                surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                connect=lambda *a, **k: pg,
                confirm=lambda t: (asked.append(t), True)[1])
            self.assertEqual(1, len(asked),
                             f"sync asked {len(asked)} questions to settle ONE approval")
            self.assertEqual(2, res.resolved_local)
            self.assertEqual(0, res.deferred)
            self.assertEqual([], team_journal.TeamJournal.for_surface(surface).conflicts())

    def test_the_extra_sync_pass_is_gone(self):
        """The ergonomic debt itself, measured. Resolving in journal order used to REFUSE the
        retirement (it comes first) and need a second pass. One pass now leaves the fact retired
        and replaced on the shared row."""
        from mokata import team_health, team_journal
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _retire_and_replace(surface)
            team_journal.sync(
                surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                connect=lambda *a, **k: pg, confirm=lambda _t: True)
            self.assertEqual("the new value", json.loads(pg.rows["new-fact"]["doc"])["value"])
            self.assertEqual(SUPERSEDED, json.loads(pg.rows["old-fact"]["doc"])["status"])

    def test_sync_still_holds_no_second_resolver(self):
        """R4's structural pin, extended to the group path. DB.S6 asserts `sync` never calls
        `journal.resolve`; the group primitive is the same kind of thing and must be just as
        off-limits — `sync` asks, the STORE settles, and there is still exactly one resolver."""
        import ast

        import mokata.team_journal as tj
        with open(tj.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "sync")
        calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        self.assertNotIn("journal.resolve_group", calls)
        self.assertNotIn("journal.resolve", calls)

    def test_a_solo_conflict_still_asks_the_ordinary_question(self):
        """Regression: the group path must not reshape the ONE-conflict prompt every existing
        sync test (and every user) already knows."""
        from mokata import team_health, team_journal
        from test_db_s6_cross_writer_healing import _plant_conflict
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            pg = _plant_conflict(surface)
            asked = []
            team_journal.sync(
                surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                connect=lambda *a, **k: pg,
                confirm=lambda t: (asked.append(t), True)[1])
            self.assertEqual(1, len(asked))
            self.assertIn("sync conflict on", asked[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
