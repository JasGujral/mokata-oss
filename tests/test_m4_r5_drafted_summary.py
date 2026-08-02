"""M-4/R5 — a REAL drafted summary replaces the f-string placeholder, via an INJECTED drafter.

Before this stage a SUMMARIZE proposal's `new` item was
`value=f"summary of {n} episodic turns in '{session}'"` with a bare `PERSISTENT` default kind — a
mechanical f-string that summarizes nothing. This stage replaces that ONE value (and that ONE
kind) with text a drafter produces, and changes nothing else: the >=3-turn trigger, the grouping,
the secret-scan, the human gate, the audit ledger and the apply path are all correct and stay.

D9 — WHO DRAFTS: the harness agent, through the propose flow. mokata never calls an LLM itself, so
the mechanism is an INJECTED CALLABLE (a seam), not a model call: mokata hands the drafter the turn
cluster + session, the harness agent produces the text, and it returns through the existing
propose/handback path to become the proposal's value. Propose-only + human-gated means P2 holds
regardless of who drafts — the drafted summary is reviewed before it is ever applied.

CONTRACTS
  1. NO drafter => byte-identical to today: the exact f-string placeholder, and the bare kind.
  2. A drafter's text becomes the SUMMARIZE proposal's `new.value`.
  3. The drafter is handed the turn cluster and the session it belongs to.
  4. A drafted summary carries a REAL kind (REFERENCE), not the bare PERSISTENT default.
  5. A drafter that RAISES degrades to the placeholder — it never breaks the consolidation pass.
  6. A drafter that returns None / "" / whitespace degrades to the placeholder.
  7. A drafter failure does not lose the OTHER proposals in the same pass.
  8. A bare string return is accepted (the harness agent's natural shape) and typed REFERENCE.
  9. Injecting a drafter is still PROPOSE-ONLY: the pass writes nothing.
 10. The trigger is untouched: <3 turns proposes no summary, drafter or not.
 11. The drafted value is SECRET-SCANNED — a drafted summary quoting a credential is hard-blocked.
 12. The drafted value is shown in the human gate's render.
 13. The drafted value is ledgered (proposal + decision) exactly as the placeholder was.
 14. The store threads the drafter through its single consolidate call site.
 15. A drafted summary is a governance FACT carrying no enforcement — never an always-on rule.
 16. A drafter MAY name the kind, but an always-on/unknown kind is CLAMPED, not honoured.
 17. D5 — a MALFUNCTIONING drafter degrades LOUDLY: the fallback still falls back, but it says so,
     because the placeholder line is visually indistinguishable from a real summary at the gate.
 18. The quiet paths stay quiet: NO drafter (the documented default) and an explicit None decline
     emit nothing — a notice on every default install is noise.
"""

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import AuditLedger
from mokata.memory import MemoryItem, MemoryStore, SQLiteBackend
from mokata.memory.consolidation import (DRAFTER_SUBSYSTEM, SUMMARIZE, SummaryDraft,
                                         propose_consolidations)
from mokata.memory.item import (CONTEXT, EPISODIC, FACT, GUARDRAIL, PERSISTENT, REFERENCE, RULE,
                                effective_enforcement, governance_kind)


def fake_secret() -> str:
    """A secret literal assembled at runtime — mokata's own secret-guard hook blocks writing one
    into a file, so a test for the hard-block cannot spell it out (the SI.4 convention)."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def turns(session="sess-1", n=3):
    return [MemoryItem.create(session, f"turn {i}", mtype=EPISODIC,
                              created_at=f"2026-01-0{i + 1}T00:00:00+00:00")
            for i in range(n)]


def store_with_turns(d, session="sess-1", n=3):
    store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
    for it in turns(session, n):
        store.backend.put(it)
    return store


def only_summary(proposals):
    summaries = [p for p in proposals if p.kind == SUMMARIZE]
    assert len(summaries) == 1, f"expected exactly 1 summarize proposal, got {len(summaries)}"
    return summaries[0]


class TestDegradeCleanDefault(unittest.TestCase):
    """Contracts 1, 5, 6, 7 — back-compat is the bar: no drafter => today, byte-for-byte."""

    def test_no_drafter_is_byte_identical_to_todays_placeholder(self):
        p = only_summary(propose_consolidations(turns("sess-1", 3)))
        self.assertEqual(p.new.value, "summary of 3 episodic turns in 'sess-1'")
        self.assertEqual(p.new.mtype, PERSISTENT)
        self.assertEqual(p.new.kind, "", "no drafter => the bare default kind, as today")

    def test_a_raising_drafter_degrades_to_the_placeholder(self):
        def boom(cluster, session):
            raise RuntimeError("the drafter died (or timed out)")

        p = only_summary(propose_consolidations(turns("sess-1", 3), drafter=boom))
        self.assertEqual(p.new.value, "summary of 3 episodic turns in 'sess-1'")
        self.assertEqual(p.new.kind, "", "a failed draft falls back to the untyped placeholder")

    def test_empty_returns_degrade_to_the_placeholder(self):
        for empty in (None, "", "   ", "\n\t "):
            with self.subTest(empty=repr(empty)):
                p = only_summary(propose_consolidations(
                    turns("sess-1", 3), drafter=lambda c, s, e=empty: e))
                self.assertEqual(p.new.value, "summary of 3 episodic turns in 'sess-1'")

    def test_a_drafter_failure_never_loses_the_other_proposals(self):
        def boom(cluster, session):
            raise ValueError("nope")

        dupes = [MemoryItem.create("db.engine", "postgres",
                                   created_at="2026-01-01T00:00:00+00:00"),
                 MemoryItem.create("db.engine", "postgres",
                                   created_at="2026-02-01T00:00:00+00:00")]
        props = propose_consolidations(turns("sess-1", 3) + dupes, drafter=boom)
        kinds = sorted({p.kind for p in props})
        self.assertEqual(kinds, ["merge", "summarize"],
                         "a dead drafter must not take the rest of the pass down with it")


class TestTheFailureIsLoud(unittest.TestCase):
    """Contracts 17, 18 — D5: the fallback stops being a secret, but only when it is a MALFUNCTION.

    The subsystem is asserted via `emitted_notices()` rather than captured stderr because
    `note_degraded` fires once per subsystem per PROCESS: a second test in the same run would see
    silence and read it as a regression. The recorded list is order-independent, and nothing else
    in mokata emits this subsystem.
    """

    def _emitted(self):
        from mokata.degrade import emitted_notices
        return [n.subsystem for n in emitted_notices()]

    def test_a_raising_drafter_says_so(self):
        def boom(cluster, session):
            raise RuntimeError("the drafter died")

        with contextlib.redirect_stderr(io.StringIO()):
            propose_consolidations(turns("sess-loud-1", 3), drafter=boom)
        self.assertIn(DRAFTER_SUBSYSTEM, self._emitted(),
                      "a human must not approve a placeholder believing it was drafted")

    def test_a_garbage_return_says_so(self):
        with contextlib.redirect_stderr(io.StringIO()):
            propose_consolidations(turns("sess-loud-2", 3), drafter=lambda c, s: 42)
        self.assertIn(DRAFTER_SUBSYSTEM, self._emitted())

    def test_the_notice_names_the_placeholder_fallback(self):
        """The notice has to say WHAT the user is now looking at, not merely that something broke."""
        from mokata import degrade
        from mokata.memory.consolidation import _note_drafter_degraded
        buf = io.StringIO()
        with mock.patch.object(degrade, "_EMITTED", set()):    # unspend the once-per-process guard
            with contextlib.redirect_stderr(buf):
                _note_drafter_degraded("a test detail")
        text = buf.getvalue().lower()
        self.assertIn("placeholder", text, "the notice must name what the human is looking at")
        self.assertIn("drafter", text, "and point at the thing to check — mokata drafts nothing")

    def test_no_drafter_and_an_explicit_decline_stay_quiet(self):
        """The default path must not print on every run — and a decline is an answer, not a fault."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            propose_consolidations(turns("sess-quiet-1", 3))                       # no drafter
            propose_consolidations(turns("sess-quiet-2", 3), drafter=lambda c, s: None)
        self.assertEqual(buf.getvalue(), "",
                         "the zero-config default must be silent — a notice per install is noise")


class TestTheDraftedSummary(unittest.TestCase):
    """Contracts 2, 3, 4, 8, 15 — the drafted value, and the kind it is born with."""

    def test_the_drafters_text_becomes_the_proposals_value(self):
        drafted = "Chose Postgres over SQLite for the team store; migration lands in 0.0.16."
        p = only_summary(propose_consolidations(turns("sess-1", 3),
                                                drafter=lambda c, s: drafted))
        self.assertEqual(p.new.value, drafted)
        self.assertNotIn("episodic turns in", p.new.value)

    def test_the_drafter_is_handed_the_cluster_and_the_session(self):
        seen = {}

        def spy(cluster, session):
            seen["cluster"] = list(cluster)
            seen["session"] = session
            return "drafted"

        cluster = turns("sess-1", 4)
        propose_consolidations(cluster, drafter=spy)
        self.assertEqual(seen["session"], "sess-1")
        self.assertEqual([i.id for i in seen["cluster"]], [i.id for i in cluster])
        self.assertEqual([i.value for i in seen["cluster"]],
                         ["turn 0", "turn 1", "turn 2", "turn 3"],
                         "the drafter needs the turns' CONTENT to summarize anything")

    def test_a_drafted_summary_carries_a_real_kind(self):
        p = only_summary(propose_consolidations(turns("sess-1", 3),
                                                drafter=lambda c, s: "a real summary"))
        self.assertEqual(p.new.kind, REFERENCE,
                         "a drafted summary is distilled key points from a source + a pointer")
        self.assertEqual(p.new.mtype, PERSISTENT, "the STORAGE type is unchanged")

    def test_a_drafted_summary_is_a_fact_and_never_an_enforced_rule(self):
        p = only_summary(propose_consolidations(turns("sess-1", 3),
                                                drafter=lambda c, s: "a real summary"))
        self.assertEqual(governance_kind(p.new.kind), FACT)
        self.assertNotEqual(effective_enforcement(p.new), "hard",
                            "a machine-drafted summary must never be born a hard rule")

    def test_a_bare_string_return_is_accepted_and_typed(self):
        p = only_summary(propose_consolidations(turns("sess-1", 3),
                                                drafter=lambda c, s: "just text"))
        self.assertEqual(p.new.value, "just text")
        self.assertEqual(p.new.kind, REFERENCE)

    def test_a_drafter_may_name_a_drafted_kind(self):
        p = only_summary(propose_consolidations(
            turns("sess-1", 3),
            drafter=lambda c, s: SummaryDraft("a domain constraint emerged", kind=CONTEXT)))
        self.assertEqual(p.new.kind, CONTEXT, "a legitimate non-always-on kind is honoured")

    def test_an_always_on_or_unknown_kind_is_clamped_not_honoured(self):
        """A drafter is MODEL-WRITTEN text. It must not be able to mint itself an always-on,
        hard-enforced category: the human gate renders the VALUE, so nobody approving a paragraph
        is thereby approving a new project rule that blocks work."""
        for bogus in (RULE, GUARDRAIL, "not-a-kind", "", None, 42):
            with self.subTest(kind=bogus):
                p = only_summary(propose_consolidations(
                    turns("sess-1", 3),
                    drafter=lambda c, s, k=bogus: SummaryDraft("drafted", kind=k)))
                self.assertEqual(p.new.value, "drafted", "the summary still lands")
                self.assertEqual(p.new.kind, REFERENCE)
                self.assertNotEqual(effective_enforcement(p.new), "hard")


class TestNothingElseMoved(unittest.TestCase):
    """Contracts 9, 10, 14 — trigger, propose-only posture and the store thread."""

    def test_injecting_a_drafter_is_still_propose_only(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            before = len(store.backend.all(statuses=("active",)))
            store.propose_consolidations(drafter=lambda c, s: "drafted")
            self.assertEqual(len(store.backend.all(statuses=("active",))), before)

    def test_the_trigger_is_untouched(self):
        props = propose_consolidations(turns("sess-1", 2), drafter=lambda c, s: "drafted")
        self.assertEqual([p for p in props if p.kind == SUMMARIZE], [],
                         "<3 turns proposes no summary, drafter or not")

    def test_the_store_threads_the_drafter_through(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations(drafter=lambda c, s: "from the store"))
            self.assertEqual(p.new.value, "from the store")

    def test_the_store_without_a_drafter_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations())
            self.assertEqual(p.new.value, "summary of 3 episodic turns in 'sess-1'")


class TestTheDraftedValueRidesTheSameGates(unittest.TestCase):
    """Contracts 11, 12, 13 — the drafter produces a PROPOSAL, never a durable write."""

    def test_a_drafted_summary_quoting_a_credential_is_hard_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations(
                drafter=lambda c, s: f"the team agreed the prod key is {fake_secret()}"))
            res = store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertFalse(res.changed, "a drafted secret must HARD-BLOCK the consolidation")
            self.assertTrue(res.blocked)
            values = [i.value for i in store.backend.all()]
            self.assertFalse(any(fake_secret() in v for v in values),
                             "the drafted secret must not reach the store")

    def test_the_drafted_value_is_shown_in_the_human_gate_render(self):
        drafted = "Postgres chosen; migration in 0.0.16."
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations(drafter=lambda c, s: drafted))
            render = store.render_consolidation(p)
            self.assertIn(drafted, render,
                          "the human approves on the strength of the DRAFTED text")

    def test_the_gate_can_still_refuse_a_drafted_summary(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations(drafter=lambda c, s: "drafted"))
            res = store.apply_consolidation(p, "approve", confirm=lambda _t: False)
            self.assertFalse(res.changed)
            self.assertFalse(any(i.value == "drafted" for i in store.backend.all()))

    def test_the_drafted_summary_is_ledgered_like_any_proposal(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations(ledger=led,
                                                          drafter=lambda c, s: "drafted"))
            store.apply_consolidation(p, "approve", assume_yes=True, ledger=led)
            kinds = [e["kind"] for e in led.entries()]
            self.assertIn("consolidation_proposal", kinds)
            self.assertIn("consolidation_decision", kinds)

    def test_an_approved_drafted_summary_lands_with_its_kind(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_with_turns(d)
            p = only_summary(store.propose_consolidations(drafter=lambda c, s: "drafted"))
            res = store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertTrue(res.changed)
            landed = [i for i in store.backend.all(statuses=("active",)) if i.value == "drafted"]
            self.assertEqual(len(landed), 1)
            self.assertEqual(landed[0].kind, REFERENCE, "the kind survives the gated write")


if __name__ == "__main__":
    unittest.main()
