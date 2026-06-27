"""Stage 36 — guided onboarding + typed project-knowledge memory (the "institutional brain").

Both jsonschema states (no jsonschema imported here — these exercise the memory + skills +
briefing layers, which are dependency-free, so behaviour is identical ABSENT/PRESENT).

Covers: the onboard skill exists (slash + `mokata onboard`), is guided, and persists TYPED
entries human-gated; an entry of each kind round-trips carrying its kind; a conflicting update
routes through self-healing (no silent overwrite); `mokata memory --kind` groups; rule/guardrail
surface in the SessionStart briefing AND respect the line budget (a budget-exceeding set does NOT
blow it); context/reference are JIT-retrievable and NOT all dumped (the frugality bound, P11).
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.bootstrap import BRIEFING_RULES_MAX_LINES, build_bootstrap
from mokata.config import Surface
from mokata.govern.rules import load_rules, validate_caps
from mokata.init import init_repo
from mokata.memory import (
    ALWAYS_ON_KINDS,
    BEST_PRACTICE,
    CONTEXT,
    GUARDRAIL,
    JIT_KINDS,
    MEMORY_KINDS,
    PART_KINDS,
    PERSISTENT,
    REFERENCE,
    RULE,
    MemoryItem,
    MemoryStore,
    always_on_lines,
    group_by_kind,
    jit_recall,
    normalize_kind,
)


def _silent(_):
    pass


def _repo():
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    return d


def _store(d):
    return MemoryStore.from_surface(Surface.load(d))


# ----------------------------------------------------------------- typed item round-trip

class TestTypedItemRoundTrip(unittest.TestCase):
    def test_each_kind_round_trips_carrying_its_kind(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            for kind in PART_KINDS:
                store.remember(
                    MemoryItem.create(f"{kind}.subj", f"value for {kind}",
                                      mtype=PERSISTENT, kind=kind),
                    assume_yes=True)
            # reload from a fresh store (proves persistence carried the kind)
            reread = {i.subject: i for i in _store(d).all_active()}
            for kind in PART_KINDS:
                self.assertEqual(reread[f"{kind}.subj"].kind, kind)
                self.assertEqual(reread[f"{kind}.subj"].effective_kind, kind)

    def test_to_from_dict_preserves_kind(self):
        it = MemoryItem.create("s", "v", kind=RULE)
        self.assertEqual(MemoryItem.from_dict(it.to_dict()).kind, RULE)

    def test_effective_kind_falls_back_to_mtype(self):
        it = MemoryItem.create("s", "v")           # no explicit kind
        self.assertEqual(it.effective_kind, it.mtype)

    def test_normalize_kind_accepts_natural_spellings(self):
        self.assertEqual(normalize_kind("Rules"), RULE)
        self.assertEqual(normalize_kind("guard rail"), GUARDRAIL)
        self.assertEqual(normalize_kind("best practice"), BEST_PRACTICE)
        self.assertEqual(normalize_kind("convention"), BEST_PRACTICE)
        self.assertEqual(normalize_kind("zzz"), "")


# ----------------------------------------------------------------- grouping by kind

class TestGroupByKind(unittest.TestCase):
    def test_group_orders_by_taxonomy_and_buckets(self):
        items = [
            MemoryItem.create("r", "x", kind=RULE),
            MemoryItem.create("c", "y", kind=CONTEXT),
            MemoryItem.create("g", "z", kind=GUARDRAIL),
        ]
        grouped = group_by_kind(items)
        # rule before guardrail before context (declared taxonomy order)
        self.assertEqual(list(grouped), [RULE, GUARDRAIL, CONTEXT])

    def test_cli_memory_kind_filter(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(MemoryItem.create("no-net", "no network in parser",
                                             mtype=PERSISTENT, kind=RULE), assume_yes=True)
            store.remember(MemoryItem.create("money", "use Decimal",
                                             mtype=PERSISTENT, kind=GUARDRAIL), assume_yes=True)
            store.close()

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["memory", "--kind", "rule", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("no-net", out)
            self.assertNotIn("money", out)        # filtered out

            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["memory", "--path", d])
            grouped = buf.getvalue()
            self.assertIn("rule (", grouped)       # grouped headers present
            self.assertIn("guardrail (", grouped)


# ----------------------------------------------------------------- conflicting edit -> healing

class TestEditRoutesThroughHealing(unittest.TestCase):
    def test_memory_edit_supersedes_never_silently_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(MemoryItem.create("tax_rate", "0.2",
                                             mtype=PERSISTENT, kind=CONTEXT), assume_yes=True)
            store.close()

            rc = cli.main(["memory", "edit", "tax_rate", "--value", "0.25",
                           "--yes", "--path", d])
            self.assertEqual(rc, 0)

            backend_items = _store(d).backend.all()
            by_val = {i.value: i for i in backend_items if i.subject == "tax_rate"}
            # old value is NOT gone — it is SUPERSEDED (surfaced, never silent)
            self.assertIn("0.2", by_val)
            self.assertIn("0.25", by_val)
            self.assertEqual(by_val["0.2"].status, "superseded")
            self.assertEqual(by_val["0.25"].status, "active")
            # the active value carries the kind
            self.assertEqual(by_val["0.25"].effective_kind, CONTEXT)

    def test_edit_missing_subject_errors(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            rc = cli.main(["memory", "edit", "nope", "--value", "x", "--yes", "--path", d])
            self.assertEqual(rc, 1)


# ----------------------------------------------------------------- onboard skill surface

class TestOnboardSkill(unittest.TestCase):
    def test_skill_registered_and_guided(self):
        from mokata.skills import get_skill
        skill = get_skill("onboard")
        low = skill.prompt.lower()
        self.assertIn("one focus at a time", low)
        self.assertIn("human-gate", low)
        for kind in PART_KINDS:
            self.assertIn(kind, low)            # names every part it can capture
        # it processes, not stores verbatim
        self.assertIn("not raw", low)

    def test_cli_onboard_prints_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["onboard", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("onboard", buf.getvalue().lower())

    def test_command_template_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertTrue(os.path.exists(
            os.path.join(root, "templates", "commands", "onboard.md")))

    def test_mcp_remember_typed_is_gated(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            # propose-only without confirm — writes nothing
            res = M.remember(path=d, subject="no-net", value="no network in parser",
                             kind="rule")
            self.assertEqual(res["status"], "proposed")
            self.assertEqual(_store(d).all_active(), [])

            res = M.remember(path=d, subject="no-net", value="no network in parser",
                             kind="rule", confirm=True)
            self.assertEqual(res["status"], "committed")
            items = _store(d).all_active()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].kind, RULE)
            self.assertEqual(items[0].mtype, PERSISTENT)   # parts stored as persistent


# ----------------------------------------------------------------- always-on briefing + budget

class TestAlwaysOnSurfacingRespectsBudget(unittest.TestCase):
    def test_rule_guardrail_appear_in_briefing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(MemoryItem.create("no-net", "no network in the parser",
                                             mtype=PERSISTENT, kind=RULE), assume_yes=True)
            store.remember(MemoryItem.create("money", "currency math uses Decimal",
                                             mtype=PERSISTENT, kind=GUARDRAIL), assume_yes=True)
            store.close()

            res = build_bootstrap(Surface.load(d))
            self.assertIn("Project rules & guardrails", res.text)
            self.assertIn("no network in the parser", res.text)
            self.assertIn("currency math uses Decimal", res.text)
            self.assertTrue(res.within_budget)

    def test_context_reference_NOT_in_briefing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(MemoryItem.create("pricing", "price = base * 1.2",
                                             mtype=PERSISTENT, kind=CONTEXT), assume_yes=True)
            store.close()
            # context is JIT, not always-on — it must NOT be dumped into every briefing
            self.assertNotIn("price = base * 1.2", build_bootstrap(Surface.load(d)).text)

    def test_budget_exceeding_set_does_not_blow_the_line_budget(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            for i in range(200):     # far more rules than any budget allows
                store.remember(MemoryItem.create(f"rule-{i:03d}", f"hard rule number {i}",
                                                 mtype=PERSISTENT, kind=RULE), assume_yes=True)
            store.close()
            surface = Surface.load(d)

            # 1) the always-on rules TIER stays within its hard 60-line cap
            rules = load_rules(surface)
            always = rules["always_on"]
            self.assertTrue(always.within_cap, f"{always.line_count} > {always.cap}")
            self.assertEqual(validate_caps(rules), [])          # no cap violations

            # 2) the briefing's rules section is capped and flags the overflow, never dumps 200
            lines, overflow = always_on_lines(_store(d), BRIEFING_RULES_MAX_LINES)
            self.assertLessEqual(len(lines), BRIEFING_RULES_MAX_LINES)
            self.assertGreater(overflow, 0)
            self.assertTrue(any("more project rule" in ln for ln in lines))

            # 3) the whole briefing still fits its token budget
            self.assertTrue(build_bootstrap(surface).within_budget)


# ----------------------------------------------------------------- JIT retrieval (frugality)

class TestJitRetrievalIsFrugal(unittest.TestCase):
    def _seed_many(self, store, n=50):
        for i in range(n):
            store.remember(MemoryItem.create(f"ctx-{i:03d}", f"unrelated fact {i}",
                                             mtype=PERSISTENT, kind=CONTEXT), assume_yes=True)
        # one clearly-relevant context entry + one reference
        store.remember(MemoryItem.create("pricing-formula",
                                         "pricing margin uses a 1.2 multiplier",
                                         mtype=PERSISTENT, kind=CONTEXT), assume_yes=True)
        store.remember(MemoryItem.create("pricing-doc",
                                         "see the pricing architecture reference",
                                         mtype=PERSISTENT, kind=REFERENCE), assume_yes=True)

    def test_jit_returns_only_topk_relevant_never_the_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            self._seed_many(store, n=50)

            hits = jit_recall(store, "how is pricing margin computed", top_k=3)
            self.assertLessEqual(len(hits), 3)                  # the frugality bound
            self.assertTrue(hits)                               # but it DID find the relevant ones
            self.assertIn("pricing-formula", [h.subject for h in hits])
            # only JIT kinds are eligible
            for h in hits:
                self.assertIn(h.effective_kind, JIT_KINDS)

    def test_empty_query_retrieves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            self._seed_many(store, n=10)
            self.assertEqual(jit_recall(store, "", top_k=5), [])

    def test_always_on_kinds_are_not_jit(self):
        # rule/guardrail are always-on, not JIT; they must not surface via jit_recall
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = _store(d)
            store.remember(MemoryItem.create("rule-pricing", "never hardcode pricing",
                                             mtype=PERSISTENT, kind=RULE), assume_yes=True)
            hits = jit_recall(store, "pricing", top_k=5)
            self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
