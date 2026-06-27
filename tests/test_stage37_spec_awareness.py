"""Stage 37 — spec-awareness / regression guard (don't break saved specs).

Both jsonschema states (no jsonschema imported here — the engine/govern pieces are
dependency-free, so behaviour is identical ABSENT/PRESENT).

Covers: a change that contradicts a saved spec/decision is surfaced + routed through the
deviation gate (BLOCKED until confirmed); a change that touches nothing saved is NO false alarm;
the conflict + the resolution are recorded in the audit ledger; no-saved-specs and no-graph
cases degrade cleanly (and the no-graph fallback announces itself). Frugal: only the touch-set.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.config import Surface
from mokata.engine import (
    ChangeSet,
    SPEC_CORPUS_KEY,
    check_change,
    guard_change,
    load_decisions,
    load_spec_corpus,
)
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.engine.spec_gate import SPEC_STATE_KEY
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.memory import DECISION, MemoryItem, MemoryStore


def _silent(_):
    pass


def _spec(title, *ac_texts):
    return Spec(title=title,
                criteria=[AcceptanceCriterion(id=f"AC{i+1}", text=t)
                          for i, t in enumerate(ac_texts)])


# --------------------------------------------------------------- pure check (no I/O)

class TestCheckChange(unittest.TestCase):
    def test_touching_a_saved_spec_is_flagged(self):
        specs = [_spec("Payments", "process_payment must be idempotent on retry")]
        rep = check_change(ChangeSet(symbols=["process_payment"]), specs, [])
        self.assertTrue(rep.has_conflicts)
        self.assertEqual(rep.conflicts[0].source_kind, "spec")
        self.assertIn("process_payment", rep.conflicts[0].where)

    def test_unrelated_change_is_no_false_alarm(self):
        specs = [_spec("Payments", "process_payment must be idempotent on retry")]
        rep = check_change(ChangeSet(symbols=["render_sidebar"]), specs, [])
        self.assertFalse(rep.has_conflicts)
        self.assertTrue(rep.checked)

    def test_touching_a_decision_is_flagged(self):
        d = MemoryItem.create("retry.policy", "process_payment retries 3x on 500",
                              mtype=DECISION)
        rep = check_change(ChangeSet(symbols=["process_payment"]), [], [d])
        self.assertTrue(rep.has_conflicts)
        self.assertEqual(rep.conflicts[0].source_kind, "decision")

    def test_file_overlap_is_flagged(self):
        specs = [_spec("Parser", "the parser in parse.py never touches the network")]
        rep = check_change(ChangeSet(files=["src/parse.py"]), specs, [])
        self.assertTrue(rep.has_conflicts)

    def test_empty_corpus_is_a_noop_no_false_alarm(self):
        rep = check_change(ChangeSet(symbols=["anything"]), [], [])
        self.assertFalse(rep.checked)           # nothing to guard
        self.assertFalse(rep.has_conflicts)

    def test_no_graph_degrades_and_announces(self):
        # layer=None -> no graph expansion; report must say lexical/file overlap only
        specs = [_spec("Payments", "process_payment idempotent")]
        rep = check_change(ChangeSet(symbols=["process_payment"]), specs, [], layer=None)
        self.assertTrue(rep.degraded)
        self.assertIn("lexical/file overlap", rep.render())

    def test_generic_word_does_not_false_alarm(self):
        # a touched symbol that's only a coincidental generic word shouldn't match prose
        specs = [_spec("Payments", "the process must be safe")]
        rep = check_change(ChangeSet(symbols=["render_sidebar"]), specs, [])
        self.assertFalse(rep.has_conflicts)


# --------------------------------------------------------------- graph-expanded touch-set

class _FakeRef:
    def __init__(self, symbol):
        self.symbol = symbol


class _FakeResult:
    def __init__(self, symbols, degraded=False):
        self.references = [_FakeRef(s) for s in symbols]
        self.degraded = degraded


class _FakeGraphLayer:
    """Stands in for a wired KnowledgeLayer: callers(x) returns process_payment."""
    uses_graph = True

    def callers(self, sym):
        return _FakeResult(["process_payment"]) if sym == "capture" else _FakeResult([])

    def callees(self, sym):
        return _FakeResult([])

    def blast_radius(self, sym, depth=1):
        return _FakeResult([])


class TestTouchSetExpansion(unittest.TestCase):
    def test_graph_expansion_catches_an_impacted_spec(self):
        # change touches `capture`; the graph says process_payment calls it; a spec about
        # process_payment must therefore be flagged (Stage 33 grounding via the graph).
        specs = [_spec("Payments", "process_payment must be idempotent")]
        rep = check_change(ChangeSet(symbols=["capture"]), specs, [],
                           layer=_FakeGraphLayer())
        self.assertFalse(rep.degraded)                 # a real graph was used
        self.assertTrue(rep.has_conflicts)
        self.assertIn("process_payment", rep.conflicts[0].where)


# --------------------------------------------------------------- the guard + ledger

class TestGuardAndLedger(unittest.TestCase):
    def _ledger(self):
        self._d = tempfile.mkdtemp()
        return AuditLedger(os.path.join(self._d, "audit.jsonl"))

    def test_conflict_blocks_until_confirmed_and_is_logged(self):
        ledger = self._ledger()
        specs = [_spec("Payments", "process_payment must be idempotent")]
        change = ChangeSet(symbols=["process_payment"])

        # no confirmation (assume_yes=False, confirm declines) -> BLOCKED
        out = guard_change(change, specs=specs, decisions=[], layer=None, ledger=ledger,
                           confirm=lambda _t: False)
        self.assertTrue(out.blocked)
        self.assertFalse(out.proceeded)

        kinds = [e["kind"] for e in ledger.entries()]
        self.assertIn("spec_conflict", kinds)          # the conflict recorded
        self.assertIn("deviation", kinds)              # routed through the deviation gate
        decisions = [e for e in ledger.entries() if e["kind"] == "deviation"]
        self.assertTrue(any(e["decision"] == "proposed" for e in decisions))
        self.assertTrue(any(e["decision"] == "declined" for e in decisions))

    def test_confirmed_change_proceeds_and_logs_approval(self):
        ledger = self._ledger()
        specs = [_spec("Payments", "process_payment must be idempotent")]
        out = guard_change(ChangeSet(symbols=["process_payment"]), specs=specs, decisions=[],
                           layer=None, ledger=ledger, assume_yes=True)
        self.assertTrue(out.proceeded)
        self.assertFalse(out.blocked)
        self.assertTrue(any(e["kind"] == "deviation" and e["decision"] == "approved"
                            for e in ledger.entries()))

    def test_no_conflict_proceeds_without_a_deviation(self):
        ledger = self._ledger()
        specs = [_spec("Payments", "process_payment idempotent")]
        out = guard_change(ChangeSet(symbols=["unrelated_fn"]), specs=specs, decisions=[],
                           layer=None, ledger=ledger)
        self.assertTrue(out.proceeded)
        self.assertEqual([e for e in ledger.entries() if e["kind"] == "deviation"], [])

    def test_no_corpus_is_a_clean_noop(self):
        ledger = self._ledger()
        out = guard_change(ChangeSet(symbols=["x"]), specs=[], decisions=[], ledger=ledger)
        self.assertTrue(out.proceeded)
        self.assertFalse(out.report.checked)
        self.assertEqual(ledger.entries(), [])         # nothing logged on a no-op


# --------------------------------------------------------------- corpus loading

class TestCorpusLoading(unittest.TestCase):
    def test_loads_emitted_spec_and_archive(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            surface = Surface.load(d)
            surface.state.write(SPEC_STATE_KEY,
                                _spec("Emitted", "ac one").to_dict())
            surface.state.write(SPEC_CORPUS_KEY,
                                [_spec("Archived", "ac two").to_dict()])
            titles = {s.title for s in load_spec_corpus(surface.state)}
            self.assertEqual(titles, {"Emitted", "Archived"})

    def test_load_decisions_returns_decision_memory(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            store = MemoryStore.from_surface(Surface.load(d))
            store.remember(MemoryItem.create("retry", "3x on 500", mtype=DECISION),
                           assume_yes=True)
            store.close()
            ds = load_decisions(MemoryStore.from_surface(Surface.load(d)))
            self.assertEqual([x.subject for x in ds], ["retry"])


# --------------------------------------------------------------- CLI surface

class TestCliSpecCheck(unittest.TestCase):
    def _repo_with_spec(self, d):
        init_repo(root=d, profile="full", assume_yes=True, out=_silent)
        surface = Surface.load(d)
        surface.state.write(SPEC_STATE_KEY,
                            _spec("Payments",
                                  "process_payment must be idempotent on retry").to_dict())
        return surface

    def test_cli_conflict_blocks_without_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo_with_spec(d)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["spec-check", "--symbols", "process_payment", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 1)                      # BLOCKED until confirmed
            self.assertIn("affects", out)
            self.assertIn("lexical/file overlap", out)   # no graph here -> announced

    def test_cli_confirmed_change_proceeds(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo_with_spec(d)
            rc = cli.main(["spec-check", "--symbols", "process_payment", "--yes", "--path", d])
            self.assertEqual(rc, 0)
            entries = AuditLedger.from_mokata_dir(
                os.path.join(d, ".mokata")).entries()
            self.assertTrue(any(e["kind"] == "deviation" and e["decision"] == "approved"
                                for e in entries))

    def test_cli_unrelated_change_no_false_alarm(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo_with_spec(d)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["spec-check", "--symbols", "render_sidebar", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no saved spec or decision is affected", buf.getvalue())

    def test_cli_no_corpus_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["spec-check", "--symbols", "anything", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("nothing to guard", buf.getvalue())


# --------------------------------------------------------------- MCP surface

class TestMcpSpecCheck(unittest.TestCase):
    def _repo_with_spec(self, d):
        init_repo(root=d, profile="full", assume_yes=True, out=_silent)
        Surface.load(d).state.write(
            SPEC_STATE_KEY, _spec("Payments", "process_payment idempotent").to_dict())

    def test_blocked_without_confirm_then_confirmed(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            self._repo_with_spec(d)
            res = M.spec_check(path=d, symbols="process_payment")
            self.assertEqual(res["status"], "blocked")
            self.assertTrue(res["conflicts"])

            res = M.spec_check(path=d, symbols="process_payment", confirm=True)
            self.assertEqual(res["status"], "confirmed")
            self.assertTrue(res["committed"])

    def test_ok_when_unrelated(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            self._repo_with_spec(d)
            res = M.spec_check(path=d, symbols="unrelated_fn")
            self.assertEqual(res["status"], "ok")

    def test_skipped_when_no_corpus(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            res = M.spec_check(path=d, symbols="anything")
            self.assertEqual(res["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
