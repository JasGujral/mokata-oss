"""Stage 39 — binding / integration review evidence.

Both jsonschema states. Asserts the Part A remediation behaviours and the Part B binding /
frugality invariants hold together (evidence, not claims).
"""

import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.bootstrap import BOOTSTRAP_TOKEN_BUDGET, build_bootstrap
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import (
    CONTRADICTION,
    DECISION,
    DEFAULT_TOP_K,
    PERSISTENT,
    RULE,
    HealingProposal,
    MemoryItem,
    MemoryStore,
)

SECRET = "AKIAIOSFODNN7EXAMPLE"   # an AWS access key id — the scanner hard-blocks it


def _silent(_):
    pass


def _repo(d):
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    return MemoryStore.from_surface(Surface.load(d))


# ----------------------------------------------------------------- M2: one gated write path

class TestUnifiedGate(unittest.TestCase):
    def test_remember_hard_blocks_a_secret(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            res = store.remember(MemoryItem.create("creds", f"key {SECRET}"), assume_yes=True)
            self.assertFalse(res.committed)
            self.assertTrue(res.blocked)                       # secret → hard-blocked
            self.assertEqual(store.all_active(), [])           # nothing stored

    def test_remember_blocks_a_secret_in_the_subject(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            res = store.remember(MemoryItem.create(f"k-{SECRET}", "ok"), assume_yes=True)
            self.assertTrue(res.blocked)

    def test_clean_remember_still_commits_and_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            res = store.remember(MemoryItem.create("api", "REST", mtype=DECISION),
                                 assume_yes=True)
            self.assertTrue(res.committed)
            # M2: the memory write is now on the universal audit ledger (review every decision)
            kinds = {e["kind"] for e in store._ledger.entries()}
            self.assertIn("write_gate", kinds)

    def test_healing_routes_through_the_gate_and_blocks_a_secret(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("db", "postgres", mtype=DECISION),
                           assume_yes=True)
            old = store.recall("db")[0]
            poisoned = MemoryItem.create("db", f"mysql {SECRET}", mtype=DECISION)
            p = HealingProposal(kind=CONTRADICTION, subject="db", mtype=DECISION,
                                old=old, new=poisoned, rationale="x")
            hr = store.apply_proposal(p, "approve", assume_yes=True)
            self.assertFalse(hr.changed)
            self.assertTrue(hr.blocked)                        # secret in the new value blocked
            self.assertEqual([i.value for i in store.recall("db")], ["postgres"])  # unchanged

    def test_decline_preserves_the_rich_surface(self):
        # the human gate still shows render_write (M2 preserved the surface via prompt override)
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            seen = {}

            def _decline(text):
                seen["t"] = text
                return False
            res = store.remember(MemoryItem.create("k", "v"), confirm=_decline)
            self.assertFalse(res.committed)
            self.assertIn("propose to remember", seen["t"])    # the rich render_write surface


# ----------------------------------------------------------------- M3: one shared reader

class TestSharedConfirm(unittest.TestCase):
    def test_read_yes_no_defaults_to_no_on_eof(self):
        from mokata.prompt import read_yes_no
        # no stdin → EOFError → False (never auto-approve)
        self.assertFalse(read_yes_no("approve?", "really?"))


# ----------------------------------------------------------------- M6: memory_type on MCP

class TestMcpMemoryType(unittest.TestCase):
    def test_remember_takes_memory_type_and_mtype_alias(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            self.assertEqual(
                M.remember(path=d, subject="a", value="1", memory_type=DECISION,
                           approve=True)["status"], "committed")
            # deprecated `mtype` alias still routes
            self.assertEqual(
                M.remember(path=d, subject="b", value="2", mtype=DECISION,
                           approve=True)["status"], "committed")

    def test_recall_exposes_memory_type_with_mtype_alias(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("a", "1", mtype=DECISION), assume_yes=True)
            store.close()
            item = M.recall(path=d)["items"][0]
            self.assertEqual(item["memory_type"], DECISION)
            self.assertEqual(item["mtype"], DECISION)          # back-compat alias


# ----------------------------------------------------------------- namespaced Postgres table

class TestPostgresNamespace(unittest.TestCase):
    def test_table_is_namespaced(self):
        from mokata.memory import PostgresBackend
        self.assertEqual(PostgresBackend.TABLE, "mokata_memory")


# ----------------------------------------------------------------- DEFAULT_TOP_K

class TestDefaultTopK(unittest.TestCase):
    def test_default_top_k_is_used(self):
        self.assertEqual(DEFAULT_TOP_K, 5)
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            for i in range(12):
                store.remember(MemoryItem.create(f"s{i}", f"value {i}"), assume_yes=True)
            self.assertLessEqual(len(store.recall_relevant("value")), DEFAULT_TOP_K)


# ----------------------------------------------------------------- Part B: frugality binding

class TestFrugalityBinding(unittest.TestCase):
    def test_briefing_stays_under_budget_with_many_rules(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            for i in range(200):     # a large captured rule set
                store.remember(MemoryItem.create(f"rule-{i:03d}", f"hard rule number {i}",
                                                 mtype=PERSISTENT, kind=RULE), assume_yes=True)
            store.close()
            res = build_bootstrap(Surface.load(d))
            self.assertTrue(res.within_budget)
            self.assertLess(res.token_estimate, BOOTSTRAP_TOKEN_BUDGET)   # well under ~2k


# ----------------------------------------------------------------- Stage 28 / 29 still landed

class TestEarlierStagesLanded(unittest.TestCase):
    def test_stage28_53b_hooks_use_console_entry_point(self):
        # Stage 53b (supersedes Stage 28's sys.executable wiring): hooks are launched via
        # the `mokata-hook` console entry point — never a bare `python3` (cross-platform).
        from mokata.harness_setup import _hook_command
        cmd = _hook_command("session_start.py")
        self.assertIn("mokata-hook", cmd)
        self.assertIn("session-start", cmd)
        self.assertNotRegex(cmd, r'(^|\s)python3(\s|$)')

    def test_stage29_brainstorm_autotrigger_and_banner(self):
        from mokata.skills import get_skill
        from mokata.progress import active_banner
        self.assertTrue(get_skill("brainstorm").when_to_use)   # model auto-engage trigger
        self.assertIn("(engaged)", active_banner("brainstorm", state="engaged"))
        self.assertIn("[2/3]", active_banner("develop", sub_done=2, sub_total=3))


if __name__ == "__main__":
    unittest.main()
