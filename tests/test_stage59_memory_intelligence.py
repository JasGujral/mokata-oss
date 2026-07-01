"""Stage 59 — memory intelligence (the retention moat).

Both jsonschema states (no jsonschema imported here — these exercise the memory + dashboard +
CLI layers, which are dependency-free, so behaviour is identical ABSENT/PRESENT).

Covers the three Stage-59 touches, all READ-ONLY / PROPOSAL-ONLY / HUMAN-GATED:
  * EXPLAINABLE RETRIEVAL — a recall hit carries a short, correct "why it surfaced" (matched
    token / graph anchor / semantic neighbour / kind); deterministic + read-only; the JIT
    frugality bound (top-k, no corpus dump) still holds.
  * MEMORY-HEALTH NUDGE — counts stale · contradictory · unused, points at the gated review
    path, and is SILENT when healthy; the derivation NEVER edits/prunes memory.
  * AUTO-PROPOSED GUARDRAILS — onboard surfaces the recurring-correction rule PROPOSALS
    (proposal-only; never auto-added).
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.memory import (
    CONTEXT,
    GUARDRAIL,
    PERSISTENT,
    RULE,
    MemoryItem,
    MemoryStore,
    RetrievalHit,
    assess_health,
    explain_recall,
    jit_recall,
    memory_health,
    why_surfaced,
)
from mokata.memory.healing import CONTRADICTION, STALE, HealingProposal
from mokata.memory.intelligence import _unused_count


def _silent(_):
    pass


def _repo():
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    return d


def _store(d):
    return MemoryStore.from_surface(Surface.load(d))


def _ctx(subject, value):
    return MemoryItem.create(subject, value, mtype=PERSISTENT, kind=CONTEXT)


# ----------------------------------------------------------------- explainable retrieval

class TestWhySurfaced(unittest.TestCase):
    def test_lexical_match_names_the_query_token_and_kind(self):
        item = _ctx("auth.policy", "rotate auth tokens daily")
        why = why_surfaced("auth rotate", item)
        self.assertIn("matched", why)
        self.assertIn("auth", why)            # the actual overlapping query token
        self.assertIn("[context]", why)       # carries the kind

    def test_semantic_tier_dominates_when_highest(self):
        item = _ctx("db.engine", "we use postgres")
        why = why_surfaced("unrelated words", item,
                           tiers={"lexical": 0.0, "graph": 0.0, "semantic": 0.9})
        self.assertIn("semantically near", why)
        self.assertIn("[context]", why)

    def test_graph_tier_named_with_its_anchor(self):
        item = _ctx("loader", "config reads app.toml")
        why = why_surfaced("config callers", item,
                           tiers={"lexical": 0.1, "graph": 0.8, "semantic": 0.0})
        self.assertIn("graph-anchored", why)
        self.assertIn("config", why)          # the matched anchor token

    def test_always_on_kind_without_query_signal(self):
        item = MemoryItem.create("no-net", "no network in the parser",
                                 mtype=PERSISTENT, kind=GUARDRAIL)
        why = why_surfaced("totally different", item)   # no token overlap, no tiers
        self.assertIn("always-on", why)
        self.assertIn("[guardrail]", why)

    def test_deterministic_same_input_same_phrase(self):
        item = _ctx("auth.policy", "rotate auth tokens daily")
        a = why_surfaced("auth rotate tokens", item)
        b = why_surfaced("auth rotate tokens", item)
        self.assertEqual(a, b)


class TestExplainRecall(unittest.TestCase):
    def test_retrieval_hit_explains_from_its_own_tiers(self):
        item = _ctx("loader", "load_config reads app.toml")
        hit = RetrievalHit(item=item, score=0.8, lexical=0.1, graph=0.8, semantic=0.0)
        self.assertIn("graph-anchored", hit.explain("load_config"))

    def test_explain_recall_handles_hits_and_bare_items(self):
        item = _ctx("auth.policy", "rotate auth tokens")
        hit = RetrievalHit(item=item, score=0.25, lexical=0.25)
        from_hits = explain_recall("auth tokens", [hit])
        from_items = explain_recall("auth tokens", [item])     # bare MemoryItem (jit_recall)
        self.assertEqual(len(from_hits), 1)
        self.assertEqual(len(from_items), 1)
        for e in (from_hits[0], from_items[0]):
            self.assertIn("matched", e.why)
            self.assertIn("auth", e.why)
        self.assertIn("auth.policy", from_hits[0].line())      # rendered WITH its why

    def test_jit_recall_hit_carries_the_correct_why(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("auth.policy", "rotate auth tokens daily"), assume_yes=True)
            store.remember(_ctx("cache.ttl", "cache entries for 60s"), assume_yes=True)
            hits = jit_recall(store, "auth tokens")
            explained = explain_recall("auth tokens", hits)
            self.assertTrue(explained)
            self.assertEqual(explained[0].item.subject, "auth.policy")
            self.assertIn("auth", explained[0].why)
            self.assertNotIn("cache", explained[0].why)        # only the relevant item surfaced
            store.close()

    def test_explain_is_read_only_no_stat_bump(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("auth.policy", "rotate auth tokens"), assume_yes=True)
            hits = jit_recall(store, "auth tokens")             # recall instrumentation only
            reads_before = store.stats.reads
            # explaining the ALREADY-retrieved hits touches the store not at all
            explain_recall("auth tokens", hits)
            explain_recall("auth tokens", hits)
            self.assertEqual(store.stats.reads, reads_before)
            store.close()

    def test_frugality_bound_holds_no_corpus_dump(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            for i in range(12):
                store.remember(_ctx(f"auth.note{i}", f"auth token note {i}"), assume_yes=True)
            hits = jit_recall(store, "auth token", top_k=3)
            explained = explain_recall("auth token", hits)
            self.assertEqual(len(explained), 3)                 # top-k only
            self.assertLess(len(explained), 12)                 # never the whole corpus
            store.close()


# ----------------------------------------------------------------- memory-health nudge

class TestMemoryHealth(unittest.TestCase):
    def test_counts_stale_contradictory_unused(self):
        stale = HealingProposal(kind=STALE, subject="x", mtype=PERSISTENT,
                                old=_ctx("x", "v"), new=None, rationale="expired")
        contra = HealingProposal(kind=CONTRADICTION, subject="y", mtype=PERSISTENT,
                                 old=_ctx("y", "a"), new=_ctx("y", "b"), rationale="disagree")
        h = memory_health([stale, contra], reads=1, writes=5)
        self.assertEqual(h.stale, 1)
        self.assertEqual(h.contradictory, 1)
        self.assertEqual(h.unused, 4)                           # writes - reads (ratio signal)
        self.assertFalse(h.healthy)

    def test_unused_derivation_from_ratio(self):
        self.assertEqual(_unused_count(reads=0, writes=5), 5)
        self.assertEqual(_unused_count(reads=2, writes=5), 3)
        self.assertEqual(_unused_count(reads=5, writes=5), 0)   # read as often as written
        self.assertEqual(_unused_count(reads=9, writes=5), 0)   # well-used → no nudge
        self.assertEqual(_unused_count(reads=0, writes=0), 0)   # fresh repo → silent

    def test_nudge_points_at_gated_review(self):
        h = memory_health([], reads=0, writes=3)
        nudge = h.nudge()
        self.assertIn("3 unused", nudge)
        self.assertIn("mokata memory", nudge)
        self.assertIn("mokata govern", nudge)

    def test_silent_when_healthy(self):
        h = memory_health([], reads=4, writes=2)               # well-used, no issues
        self.assertTrue(h.healthy)
        self.assertEqual(h.nudge(), "")

    def test_assess_health_is_read_only_and_never_edits_memory(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("a", "alpha"), assume_yes=True)
            store.remember(_ctx("b", "bravo"), assume_yes=True)
            before_items = {i.subject: i.value for i in store.peek_active()}
            writes_before = store.stats.writes
            reads_before = store.stats.reads
            h1 = assess_health(store)
            h2 = assess_health(store)
            # deterministic
            self.assertEqual((h1.stale, h1.contradictory, h1.unused),
                             (h2.stale, h2.contradictory, h2.unused))
            # NEVER edits/prunes memory; no stat bump from the derivation
            after_items = {i.subject: i.value for i in store.peek_active()}
            self.assertEqual(after_items, before_items)
            self.assertEqual(store.stats.writes, writes_before)
            self.assertEqual(store.stats.reads, reads_before)
            store.close()

    def test_assess_health_flags_a_stale_item(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(MemoryItem.create("ttl.fact", "expired already",
                                             mtype=PERSISTENT, kind=CONTEXT, valid_for=-100),
                           assume_yes=True)
            h = assess_health(store)
            self.assertGreaterEqual(h.stale, 1)
            self.assertFalse(h.healthy)
            self.assertIn("stale", h.nudge())
            store.close()

    def test_cmd_memory_surfaces_nudge_when_unhealthy(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            # writes with ~no reads → unused signal fires
            for i in range(4):
                store.remember(_ctx(f"note{i}", f"value {i}"), assume_yes=True)
            store.close()
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["memory", "--path", d])
            out = buf.getvalue()
            self.assertIn("memory health", out)
            self.assertIn("unused", out)
            self.assertIn("mokata govern", out)


# ----------------------------------------------------------------- govern view carries health

class TestGovernViewHealth(unittest.TestCase):
    def test_governance_view_and_html_carry_the_nudge(self):
        from mokata.dashboard import build_governance_view, render_governance_html
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            for i in range(3):
                store.remember(_ctx(f"note{i}", f"value {i}"), assume_yes=True)
            store.close()
            view = build_governance_view(Surface.load(d))
            self.assertIsNotNone(view.health)
            self.assertEqual(view.health.unused, view.writes - view.reads)
            html = render_governance_html(view)
            self.assertIn("memory health", html)
            # render is deterministic (no wall-clock)
            self.assertEqual(html, render_governance_html(build_governance_view(Surface.load(d))))


# ----------------------------------------------------------------- auto-proposed guardrails

class TestOnboardSurfacesLearnedGuardrails(unittest.TestCase):
    def _record_recurring_corrections(self, d, n=3):
        led = AuditLedger.from_mokata_dir(Surface.load(d).mokata_dir)
        for _ in range(n):
            led.record("write_gate", write_kind="code", target="src/x.py",
                       actor="cli", decision="declined", reason="risky")

    def test_onboard_surfaces_proposals_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            self._record_recurring_corrections(d, n=3)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["onboard", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Proposed guardrails", out)
            self.assertIn("NOT auto-added", out)        # proposal-only language

    def test_onboard_silent_when_no_proposals(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["onboard", "--path", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("Proposed guardrails", buf.getvalue())

    def test_onboard_never_auto_adds_a_rule(self):
        from mokata.govern import learn_from_ledger
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            self._record_recurring_corrections(d, n=3)
            rules_before = {i.subject for i in _store(d).all_active()
                            if i.effective_kind in (RULE, GUARDRAIL)}
            with redirect_stdout(io.StringIO()):
                cli.main(["onboard", "--path", d])
            # the surfacing read NOTHING into memory; proposals remain just proposals
            rules_after = {i.subject for i in _store(d).all_active()
                           if i.effective_kind in (RULE, GUARDRAIL)}
            self.assertEqual(rules_after, rules_before)
            led = AuditLedger.from_mokata_dir(Surface.load(d).mokata_dir)
            self.assertTrue(learn_from_ledger(led))     # still proposed, never consumed/added


# ----------------------------------------------------------------- in-harness recall surface

class TestRecallToolExplains(unittest.TestCase):
    def test_recall_mcp_query_returns_why(self):
        import mokata.mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(_ctx("auth.policy", "rotate auth tokens daily"), assume_yes=True)
            store.close()
            res = M.recall(path=d, query="auth tokens")
            self.assertTrue(res["enabled"])
            self.assertTrue(res["items"])
            self.assertEqual(res["items"][0]["subject"], "auth.policy")
            self.assertIn("auth", res["items"][0]["why"])

    def test_govern_mcp_tool_carries_health(self):
        import mokata.mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            for i in range(3):
                store.remember(_ctx(f"note{i}", f"value {i}"), assume_yes=True)
            store.close()
            res = M.govern(path=d)
            self.assertIn("health", res)
            self.assertIn("nudge", res["health"])
            self.assertFalse(res["health"]["healthy"])


if __name__ == "__main__":
    unittest.main()
