"""SI.3 — VERIFIABLE MCP APPROVAL: the model can no longer mint its own consent (0.0.13, stage 3).

The bug this closes (doc 74 C-class; flagged again by the SS.S5 audit): on the MCP path, mokata's
human gate reduced to `approve=true` — A PARAMETER THE MODEL TYPES. `_approved(approve, confirm)`
returned `bool(approve) or bool(confirm)`, and every write tool then handed `assume_yes=True` to the
WriteGate. So "every durable write is human-gated" (P2, THE principle) was true only in the sense
that the model had *decided to say* it was approved. Prompt injection, a confused model, or plain
eagerness minted consent. SI.1 closed exactly this class of hole for the gate OVERRIDE by giving it
no MCP surface ("a model-invocable override would let the model clear its own gate"); this stage
applies the same logic to the approval itself.

The fix: approval is HUMAN-MINTED, out-of-band, and the model can only REFERENCE it.

    1. model calls the write tool            -> status "proposed" + a proposal_id (NOTHING commits)
    2. HUMAN runs `mokata approve <id>`      -> TTY re-confirm, ledgered  (a separate process)
    3. model re-calls with proposal_id=<id>  -> verified, then committed, then BURNED (single-use)

The proposal id is DERIVED from (run_id, tool, args), so it is deterministic — an identical re-call
resolves to the same proposal (the store cannot be spammed), and a changed argument resolves to a
DIFFERENT hash, which is why "approve X, then commit Y" is structurally impossible rather than
merely discouraged.

What these tests pin:
  (a) THE regression: approve=true / confirm=true / an assume_yes-style param commit NOTHING — for
      EVERY write tool in the registry, not just a sampled one. The full human round-trip commits.
  (b) Injection honesty: unknown id / content changed under an approval / expired / already-used /
      other-session / not-yet-approved are each REFUSED with their own named reason code.
  (c) The model cannot mint: a behavioural sweep over every consent-ish parameter combination, plus
      a grep-guard that no write tool reaches a commit without an on-disk human approval record.
  (d) The approve CLI: non-TTY fails CLOSED; a TTY yes approves; the CI path works by pre-minting
      the approval out-of-band (the human's environment), never by a tool parameter.
  (e) The ledger carries the whole chain: proposal hash + who approved + the commit it licensed.
  (f) Untouched by design: read tools, the ungated SS.S0 save path, and SI.1's override.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import approval as A                                   # noqa: E402
from mokata import session                                         # noqa: E402
from mokata.cli import build_parser, main as cli_main              # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.mcp.registry import TOOLS                              # noqa: E402


def _write_tools():
    """Every tool the registry exposes as a durable write — the exact surface SI.3 must cover."""
    return [t for t in TOOLS if t.kind == "write"]


class _Repo:
    """An initialized mokata repo on a pinned run id (so the tool process and the CLI process agree
    on the session, exactly as MOKATA_SESSION_ID does in the field)."""

    def __init__(self, run_id="run-si3"):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self.run_id = run_id
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run_id})
        self._env.start()
        session.reset_for_test()

    def close(self):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    def ledger(self):
        return AuditLedger.from_mokata_dir(os.path.join(self.path, ".mokata"))

    def entries(self, kind=None):
        led = self.ledger()
        rows = led.entries() if hasattr(led, "entries") else list(led.read())
        return [e for e in rows if kind is None or e.get("kind") == kind]

    def human_approves(self, pid, actor="human"):
        """The out-of-band act: what `mokata approve <id>` does, in the human's own process."""
        return A.approve(self.path, pid, actor=actor, ledger=self.ledger())


class TestTheRegression(unittest.TestCase):
    """(a) approve=true no longer commits — for every write tool. The human round-trip does."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_model_typed_approve_commits_nothing_and_returns_a_proposal(self):
        res = TW.remember(path=self.repo.path, subject="db", value="postgres", approve=True)
        self.assertEqual(res["status"], "proposed", "approve=true MUST NOT commit (P2)")
        self.assertFalse(res.get("committed", False))
        self.assertTrue(res.get("proposal_id", "").startswith("p-"))
        self.assertIn("mokata approve", res.get("hint", ""),
                      "the model must be told the approval is a HUMAN act, not a param")
        # nothing was written
        from mokata.config import Surface
        from mokata.memory import MemoryStore
        store = MemoryStore.from_surface(Surface.load(self.repo.path))
        self.assertEqual([i for i in store.scoped_active() if i.subject == "db"], [])

    def test_confirm_true_the_deprecated_alias_also_commits_nothing(self):
        res = TW.remember(path=self.repo.path, subject="db", value="pg", confirm=True)
        self.assertEqual(res["status"], "proposed")
        self.assertFalse(res.get("committed", False))

    def test_the_full_human_round_trip_commits(self):
        proposed = TW.remember(path=self.repo.path, subject="db", value="postgres")
        pid = proposed["proposal_id"]
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_PROPOSED)

        self.repo.human_approves(pid)                       # <-- the out-of-band human act
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_APPROVED)

        res = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(res["status"], "committed", res)
        self.assertTrue(res["committed"])

        from mokata.config import Surface
        from mokata.memory import MemoryStore
        store = MemoryStore.from_surface(Surface.load(self.repo.path))
        self.assertEqual([i.value for i in store.scoped_active() if i.subject == "db"],
                         ["postgres"])

    def test_every_write_tool_refuses_a_model_typed_approval(self):
        """The sweep. Not one sampled tool — EVERY tool the registry calls a write."""
        for spec in _write_tools():
            with self.subTest(tool=spec.name):
                res = spec.fn(path=self.repo.path, approve=True, confirm=True)
                self.assertIsInstance(res, dict)
                self.assertFalse(
                    res.get("committed", False),
                    f"{spec.name} COMMITTED on a model-typed approve=true — P2 violated")
                self.assertNotEqual(
                    res.get("status"), "committed",
                    f"{spec.name} reported 'committed' on a model-typed approve=true")


class TestInjectionHonesty(unittest.TestCase):
    """(b) Every way a model could try to reuse or bend an approval — refused, each by name."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def _propose(self, **kw):
        kw.setdefault("subject", "db")
        kw.setdefault("value", "postgres")
        return TW.remember(path=self.repo.path, **kw)["proposal_id"]

    def test_unknown_proposal_id(self):
        res = TW.remember(path=self.repo.path, subject="db", value="postgres",
                          proposal_id="p-deadbeefcafe")
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_UNKNOWN)
        self.assertFalse(res.get("committed", False))

    def test_content_changed_under_the_approval(self):
        """The whole point: approve X, then try to commit Y with X's approval."""
        pid = self._propose(subject="db", value="postgres")
        self.repo.human_approves(pid)
        res = TW.remember(path=self.repo.path, subject="db", value="MYSQL — swapped after approval",
                          proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_CONTENT_CHANGED)
        self.assertFalse(res.get("committed", False))

    def test_an_approval_minted_for_a_different_proposal(self):
        other = self._propose(subject="cache", value="redis")
        self.repo.human_approves(other)
        res = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=other)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_CONTENT_CHANGED)

    def test_not_yet_approved(self):
        pid = self._propose()
        res = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_NOT_APPROVED)

    def test_expired(self):
        pid = self._propose()
        self.repo.human_approves(pid)
        stale = time.time() + A.DEFAULT_TTL_SECONDS + 1
        with mock.patch.object(A.time, "time", return_value=stale):
            res = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_EXPIRED)

    def test_already_used_single_use(self):
        pid = self._propose()
        self.repo.human_approves(pid)
        first = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(first["status"], "committed")
        again = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(again["status"], "refused")
        self.assertEqual(again["reason_code"], A.REFUSED_USED)

    def test_other_session(self):
        """An approval minted in run A cannot be redeemed by a tool call in run B."""
        pid = self._propose()
        self.repo.human_approves(pid)
        with mock.patch.dict(os.environ, {session.SESSION_ID_ENV: "a-different-run"}):
            session.reset_for_test()
            res = TW.remember(path=self.repo.path, subject="db", value="postgres",
                              proposal_id=pid)
        session.reset_for_test()
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_OTHER_SESSION)

    def test_a_forged_approval_file_still_needs_the_content_to_match(self):
        """Belt and braces: even a doctored record on disk cannot license a different write."""
        pid = self._propose()
        self.repo.human_approves(pid)
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir
        store = StateStore(state_dir(self.repo.path))
        rec = store.read(A.state_key(pid))
        rec["content_hash"] = "0" * 64                      # forged
        store.write(A.state_key(pid), rec)
        res = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_CONTENT_CHANGED)


class TestTheModelCannotMint(unittest.TestCase):
    """(c) No parameter combination reaches a commit without a human record on disk."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_no_consent_param_combination_commits(self):
        combos = [
            {"approve": True},
            {"confirm": True},
            {"approve": True, "confirm": True},
            {"approve": True, "force": True},
            {"assume_yes": True},                # the SS.S5-audited shape, if a tool still took it
            {"yes": True},
            {"approve": True, "assume_yes": True, "yes": True, "force": True},
        ]
        for spec in _write_tools():
            for combo in combos:
                with self.subTest(tool=spec.name, combo=sorted(combo)):
                    try:
                        res = spec.fn(path=self.repo.path, **combo)
                    except TypeError:
                        continue          # the tool does not accept that param at all — even better
                    self.assertFalse(
                        isinstance(res, dict) and res.get("committed", False),
                        f"{spec.name} committed on {combo} with NO human approval on disk")

    def test_no_write_tool_takes_an_assume_yes_parameter(self):
        """The model-settable blanket consent (`assume_yes`) is not a tool parameter. Anywhere."""
        import inspect
        for spec in _write_tools():
            params = inspect.signature(spec.fn).parameters
            self.assertNotIn("assume_yes", params,
                             f"{spec.name} exposes assume_yes to the model — blanket consent")

    def test_grep_guard_no_bare_assume_yes_gate_remains(self):
        """The `_approved(approve, confirm)` gate is GONE from the source, not merely bypassed."""
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "mokata", "mcp", "tools_write.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("def _approved(", text,
                         "the model-settable gate function still exists in tools_write")
        # every `assume_yes=True` handed downstream must sit under a granted consent, which is only
        # obtainable from `_consent(...)`. Pin that the consent helper is what every tool calls.
        self.assertIn("def _consent(", text)
        for spec in _write_tools():
            import inspect
            body = inspect.getsource(spec.fn)
            self.assertIn("_consent(", body,
                          f"{spec.name} does not route through the human-minted consent check")

    def test_a_commit_requires_the_record_on_disk_not_just_the_id(self):
        """Delete the approval record and the same call stops committing — the DISK is the truth."""
        pid = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]
        self.repo.human_approves(pid)
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir
        StateStore(state_dir(self.repo.path)).delete(A.state_key(pid))
        res = TW.remember(path=self.repo.path, subject="db", value="pg", proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_UNKNOWN)


class TestApproveCli(unittest.TestCase):
    """(d) The out-of-band command: fail-closed off a TTY, re-confirmed on one."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.pid = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]

    def _run(self, argv, tty=True, answer="y"):
        out = io.StringIO()
        with mock.patch("mokata.prompt._stdin_is_tty", return_value=tty), \
             mock.patch("builtins.input", return_value=answer), \
             mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            code = cli_main(argv)
        return code, out.getvalue()

    def test_the_command_is_registered(self):
        parser = build_parser()
        self.assertIn("approve", parser._subparsers._group_actions[0].choices)

    def test_non_tty_fails_closed(self):
        code, out = self._run(["approve", self.pid, "--path", self.repo.path], tty=False)
        self.assertEqual(code, 1, "a non-TTY approve MUST fail closed")
        self.assertEqual(A.load(self.repo.path, self.pid).status, A.STATUS_PROPOSED)

    def test_tty_yes_approves_and_ledgers(self):
        code, out = self._run(["approve", self.pid, "--path", self.repo.path], tty=True, answer="y")
        self.assertEqual(code, 0, out)
        self.assertEqual(A.load(self.repo.path, self.pid).status, A.STATUS_APPROVED)
        self.assertTrue(self.repo.entries(A.LEDGER_KIND))

    def test_tty_no_declines(self):
        code, _ = self._run(["approve", self.pid, "--path", self.repo.path], tty=True, answer="n")
        self.assertEqual(code, 1)
        self.assertEqual(A.load(self.repo.path, self.pid).status, A.STATUS_PROPOSED)

    def test_the_command_shows_what_is_being_approved(self):
        _, out = self._run(["approve", self.pid, "--path", self.repo.path], tty=True)
        self.assertIn("remember", out, "the human must see WHICH write they are approving")

    def test_listing_pending_proposals(self):
        code, out = self._run(["approve", "--path", self.repo.path], tty=True)
        self.assertEqual(code, 0)
        self.assertIn(self.pid, out)

    def test_the_ci_path_pre_minted_approval_then_a_non_interactive_commit(self):
        """The genuinely non-interactive HUMAN flow: consent comes from the environment the human
        owns (the approve command, run beforehand with --yes), never from a tool parameter."""
        code, _ = self._run(["approve", self.pid, "--yes", "--path", self.repo.path], tty=False)
        self.assertEqual(code, 0, "an explicit --yes IS the human's own non-interactive act")
        res = TW.remember(path=self.repo.path, subject="db", value="pg", proposal_id=self.pid)
        self.assertEqual(res["status"], "committed")

    def test_approve_mcp_tool_is_a_default_off_opt_in(self):
        """AMENDED by AP-MCP (doc 85 §5 D26 amendment): an in-chat `approve` MCP tool NOW EXISTS,
        but as a DEFAULT-OFF, ledgered opt-in — so the SI.1 lesson still holds out of the box. The
        model may reference a proposal; it cannot mint consent unless a human first enables the
        opt-in (itself a gated config write) AND approves each call (the tool never rides the
        auto-grant). This pins the amendment's binding shape, not the tool's absence."""
        from mokata.mcp.registry import TOOLS, tool_names
        self.assertIn("approve", tool_names(), "AP-MCP: the in-chat approve tool is registered")
        # it is its OWN kind — never a propose-only 'write' — so the SI.3 write-tool sweeps in this
        # file (which prove no write tool commits on a model-typed param) do not touch it.
        spec = next(t for t in TOOLS if t.name == "approve")
        self.assertNotEqual(spec.kind, "write")
        # DEFAULT-OFF is the load-bearing negative: with the setting absent it refuses and approves
        # nothing — the model cannot get an in-chat approve surface out of a default install.
        pid = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]
        from mokata.mcp import tools_approve as TA
        off = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertEqual(off["status"], "disabled")
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_PROPOSED,
                         "a default (opt-in OFF) install must not let the tool mint consent")


class TestTheLedgerChain(unittest.TestCase):
    """(e) proposal hash -> human approval -> the commit it licensed, all on the chain."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_the_chain_links_hash_human_and_commit(self):
        proposed = TW.remember(path=self.repo.path, subject="db", value="postgres")
        pid = proposed["proposal_id"]
        rec = A.load(self.repo.path, pid)
        self.repo.human_approves(pid, actor="jas")
        res = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(res["status"], "committed")

        rows = self.repo.entries(A.LEDGER_KIND)
        approved = [e for e in rows if e.get("decision") == "approved"]
        redeemed = [e for e in rows if e.get("decision") == "redeemed"]
        self.assertTrue(approved, "the human approval is not on the ledger")
        self.assertTrue(redeemed, "the commit is not linked back to its approval")
        self.assertEqual(approved[0]["proposal"], pid)
        self.assertEqual(approved[0]["actor"], "jas")
        self.assertEqual(approved[0]["content_hash"], rec.content_hash)
        self.assertEqual(redeemed[0]["proposal"], pid)
        self.assertEqual(redeemed[0]["content_hash"], rec.content_hash)
        self.assertIsNotNone(redeemed[0].get("write_seq"),
                             "the redemption does not name the write_gate entry it licensed")


class TestUntouchedByDesign(unittest.TestCase):
    """(f) Read tools, the ungated save, and SI.1's override are NOT changed by this stage."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_read_tools_need_no_approval(self):
        from mokata.mcp import tools_read as TR
        res = TR.status(path=self.repo.path)
        self.assertIsInstance(res, dict)
        self.assertNotIn("proposal_id", res)

    def test_the_save_path_stays_ungated_by_design(self):
        """SS.S0: `save` is local, transient, and deliberately ungated. SI.3 must not gate it."""
        import inspect
        from mokata import session_save
        self.assertNotIn("proposal_id", inspect.signature(session_save.save_session).parameters)

    def test_si_1_override_is_a_separate_mechanism(self):
        from mokata import gate_hook
        self.assertTrue(gate_hook.OVERRIDE_PREFIX.startswith("gate_override"))
        self.assertNotEqual(gate_hook.OVERRIDE_PREFIX, A.PROPOSAL_PREFIX)


class TestTheStoreItself(unittest.TestCase):
    """The proposal store's own contract: atomic, session-scoped, content-hashed, bounded."""

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_the_id_is_derived_from_the_content_so_a_re_call_is_idempotent(self):
        a = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]
        b = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]
        self.assertEqual(a, b, "an identical re-call must not spam the store with new proposals")
        c = TW.remember(path=self.repo.path, subject="db", value="mysql")["proposal_id"]
        self.assertNotEqual(a, c, "different content MUST be a different proposal")

    def test_the_store_is_bounded(self):
        for i in range(A.MAX_PROPOSALS + 8):
            TW.remember(path=self.repo.path, subject=f"k{i}", value="v")
        self.assertLessEqual(len(A.pending(self.repo.path)), A.MAX_PROPOSALS)

    def test_expired_proposals_are_pruned(self):
        pid = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]
        stale = time.time() + A.DEFAULT_TTL_SECONDS + 1
        with mock.patch.object(A.time, "time", return_value=stale):
            A.prune(self.repo.path)
        self.assertIsNone(A.load(self.repo.path, pid))

    def test_a_corrupt_record_degrades_clean_never_grants(self):
        pid = TW.remember(path=self.repo.path, subject="db", value="pg")["proposal_id"]
        from mokata.tdd_state import state_dir
        path = os.path.join(state_dir(self.repo.path), A.state_key(pid) + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ truncated")
        res = TW.remember(path=self.repo.path, subject="db", value="pg", proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertFalse(res.get("committed", False))


if __name__ == "__main__":
    unittest.main()
