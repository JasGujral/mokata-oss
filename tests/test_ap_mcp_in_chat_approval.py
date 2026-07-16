"""AP-MCP — the in-chat approval MCP tool (0.0.14, re-groom #7).

A DELIBERATE, Jas-ledgered override of the SI.3 "no in-harness approve surface" settled decision
(doc 85 §5 D26 amendment). SI.3 gave the approval NO MCP tool because a model-invocable approve
would let the model approve its own writes. AP-MCP keeps that as the DEFAULT posture and adds one
bounded, opt-in exception: an MCP tool `approve(proposal_id)` that performs the SAME act as
`mokata approve <id>` — for teams who explicitly, ledgered, turn it on.

The binding constraints these tests pin (each of which is the reason the override is acceptable):
  (a) DEFAULT-OFF — `settings.approvals.in_chat` absent/false ⇒ the tool REFUSES and approves
      nothing; the CLI-only flow is byte-identical to before AP-MCP. This is the load-bearing
      negative: out of the box the model still cannot type its own consent.
  (b) The full round trip when ENABLED: propose → human-shown id → approve(proposal_id) →
      redeem commits ONCE → second redeem refused (burned). Single-use survives.
  (c) Every SI.3 refusal survives through the chat-relayed approval: unknown id, expired,
      already-used on the tool; content-hash mismatch and burn on the redeem.
  (d) The ledger records actor="chat-relayed", distinguishable from a TTY approval's "human".
  (e) approve-all is impossible: proposal_id is the ONLY input — no boolean, no batch, no list;
      exactly one existing proposal flips.
  (f) Secret-safety: the chat-relayed ledger entry carries nothing the TTY entry didn't (no
      proposal content / DSN value).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import io
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import approval as A                                   # noqa: E402
from mokata import config_cmd                                      # noqa: E402
from mokata import session                                         # noqa: E402
from mokata.cli import main as cli_main                            # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import tools_approve as TA                         # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.mcp.registry import TOOLS, tool_names                  # noqa: E402


class _Repo:
    """An initialized mokata repo pinned to one run id, so the tool and the CLI agree on the
    session (exactly as MOKATA_SESSION_ID does in the field)."""

    def __init__(self, run_id="run-apmcp"):
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

    def enable_in_chat(self):
        """The human's own gated TTY config write (`--yes` == the human's non-interactive act)."""
        res = config_cmd.config_set(self.path, "settings.approvals.in_chat", "true",
                                    assume_yes=True, out=lambda *_a: None)
        assert res.committed, res.message
        return res

    def ledger(self):
        return AuditLedger.from_mokata_dir(os.path.join(self.path, ".mokata"))

    def approval_entries(self):
        return [e for e in self.ledger().entries() if e.get("kind") == A.LEDGER_KIND]

    def propose_remember(self, subject="db", value="postgres"):
        return TW.remember(path=self.path, subject=subject, value=value)["proposal_id"]


# ======================================================================================
# (a) DEFAULT-OFF — the load-bearing negative
# ======================================================================================
class TestOffByDefault(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_setting_absent_the_tool_refuses_and_approves_nothing(self):
        pid = self.repo.propose_remember()
        res = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertEqual(res["status"], "disabled", res)
        self.assertFalse(res.get("approved", False))
        self.assertFalse(res.get("committed", False))
        # the proposal was NOT flipped
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_PROPOSED)

    def test_the_refusal_names_the_tty_enable_path(self):
        pid = self.repo.propose_remember()
        res = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertIn("settings.approvals.in_chat", res.get("message", ""))
        self.assertIn("mokata config set", res.get("message", ""))

    def test_setting_false_is_the_same_as_absent(self):
        self.repo.enable_in_chat()
        config_cmd.config_set(self.repo.path, "settings.approvals.in_chat", "false",
                              assume_yes=True, out=lambda *_a: None)
        pid = self.repo.propose_remember()
        res = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertEqual(res["status"], "disabled")
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_PROPOSED)

    def test_cli_only_flow_is_byte_identical_when_off(self):
        """The off-switch disables ONLY the MCP surface — the CLI `mokata approve` path, and the
        redeem it licenses, are unchanged."""
        pid = self.repo.propose_remember()
        # the MCP tool refuses...
        self.assertEqual(TA.approve(path=self.repo.path, proposal_id=pid)["status"], "disabled")
        # ...but the out-of-band human CLI still approves, and the redeem still commits.
        out = io.StringIO()
        with mock.patch("mokata.prompt._stdin_is_tty", return_value=True), \
             mock.patch("builtins.input", return_value="y"), \
             mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            code = cli_main(["approve", pid, "--path", self.repo.path])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_APPROVED)
        redeem = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(redeem["status"], "committed", redeem)


# ======================================================================================
# (b) the full round trip when ENABLED
# ======================================================================================
class TestRegressionRoundTrip(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.repo.enable_in_chat()

    def test_propose_approve_redeem_commits_once_then_burned(self):
        pid = self.repo.propose_remember()
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_PROPOSED)

        res = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertTrue(res.get("approved"), res)
        self.assertEqual(A.load(self.repo.path, pid).status, A.STATUS_APPROVED)

        first = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(first["status"], "committed", first)

        again = TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(again["status"], "refused")
        self.assertEqual(again["reason_code"], A.REFUSED_USED)

    def test_result_echoes_the_full_render_and_the_ledger_entry_id(self):
        pid = self.repo.propose_remember(subject="db", value="postgres")
        res = TA.approve(path=self.repo.path, proposal_id=pid)
        # the transcript carries mokata's OWN ground-truth render next to the approval
        self.assertIn("render", res)
        self.assertIn(pid, res["render"])
        self.assertIn("remember", res["render"])
        # and the audit ledger entry id the approval was recorded under
        self.assertIn("ledger_seq", res)
        entries = self.repo.approval_entries()
        self.assertTrue(entries)
        self.assertEqual(res["ledger_seq"], entries[-1]["seq"])

    def test_the_tool_never_creates_a_proposal(self):
        """approve(unknown-id) does not mint a proposal — it approves exactly one EXISTING one."""
        before = A.pending(self.repo.path)
        res = TA.approve(path=self.repo.path, proposal_id="p-doesnotexist1")
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_UNKNOWN)
        self.assertEqual(len(A.pending(self.repo.path)), len(before))


# ======================================================================================
# (c) every SI.3 refusal survives through the chat-relayed approval
# ======================================================================================
class TestRefusalsSurvive(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.repo.enable_in_chat()

    def test_unknown_id_refused(self):
        res = TA.approve(path=self.repo.path, proposal_id="p-deadbeefcafe")
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_UNKNOWN)

    def test_empty_id_refused_never_a_list_or_approve_all(self):
        res = TA.approve(path=self.repo.path, proposal_id="")
        self.assertEqual(res["status"], "refused")
        self.assertFalse(res.get("approved", False))

    def test_expired_refused(self):
        pid = self.repo.propose_remember()
        stale = time.time() + A.DEFAULT_TTL_SECONDS + 1
        with mock.patch.object(A.time, "time", return_value=stale):
            res = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_EXPIRED)

    def test_already_used_refused(self):
        pid = self.repo.propose_remember()
        TA.approve(path=self.repo.path, proposal_id=pid)
        TW.remember(path=self.repo.path, subject="db", value="postgres", proposal_id=pid)  # burns
        res = TA.approve(path=self.repo.path, proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_USED)

    def test_content_hash_mismatch_refused_at_redeem(self):
        """The whole point: chat-approve X, then try to commit Y — refused content-changed."""
        pid = self.repo.propose_remember(subject="db", value="postgres")
        self.assertTrue(TA.approve(path=self.repo.path, proposal_id=pid).get("approved"))
        res = TW.remember(path=self.repo.path, subject="db",
                          value="MYSQL — swapped after approval", proposal_id=pid)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], A.REFUSED_CONTENT_CHANGED)


# ======================================================================================
# (d) the ledger distinguishes chat-relayed from TTY
# ======================================================================================
class TestLedgerActor(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.repo.enable_in_chat()

    def test_actor_is_chat_relayed(self):
        pid = self.repo.propose_remember()
        TA.approve(path=self.repo.path, proposal_id=pid)
        entry = self.repo.approval_entries()[-1]
        self.assertEqual(entry.get("decision"), "approved")
        self.assertEqual(entry.get("actor"), "chat-relayed")
        # and it is stamped on the proposal record too
        self.assertEqual(A.load(self.repo.path, pid).approved_by, "chat-relayed")

    def test_distinguishable_from_a_tty_approval(self):
        pid_chat = self.repo.propose_remember(subject="a", value="1")
        pid_tty = self.repo.propose_remember(subject="b", value="2")
        TA.approve(path=self.repo.path, proposal_id=pid_chat)
        A.approve(self.repo.path, pid_tty, actor="human", ledger=self.repo.ledger())
        actors = {e.get("proposal"): e.get("actor") for e in self.repo.approval_entries()
                  if e.get("decision") == "approved"}
        self.assertEqual(actors[pid_chat], "chat-relayed")
        self.assertEqual(actors[pid_tty], "human")


# ======================================================================================
# (e) approve-all is impossible — proposal_id is the ONLY input
# ======================================================================================
class TestNoApproveAll(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.repo.enable_in_chat()

    def test_signature_has_no_boolean_or_batch_consent_param(self):
        params = set(inspect.signature(TA.approve).parameters)
        for forbidden in ("approve", "confirm", "assume_yes", "yes", "all",
                          "batch", "proposal_ids", "force"):
            self.assertNotIn(forbidden, params,
                             f"approve exposes '{forbidden}' — a way to mint/batch consent")
        self.assertEqual(sorted(params), ["path", "proposal_id"])

    def test_one_call_flips_exactly_one_proposal(self):
        a = self.repo.propose_remember(subject="a", value="1")
        b = self.repo.propose_remember(subject="b", value="2")
        TA.approve(path=self.repo.path, proposal_id=a)
        self.assertEqual(A.load(self.repo.path, a).status, A.STATUS_APPROVED)
        self.assertEqual(A.load(self.repo.path, b).status, A.STATUS_PROPOSED)

    def test_no_source_path_approves_more_than_one(self):
        """A grep-guard: the tool body never iterates pending/approves in a loop."""
        src = inspect.getsource(TA.approve)
        self.assertNotIn("pending(", src, "approve must not enumerate proposals")
        self.assertEqual(src.count("approval.approve("), 1,
                         "approve must mint at most one approval per call")


# ======================================================================================
# (f) secret-safety — the chat-relayed entry carries nothing extra
# ======================================================================================
class TestSecretSafety(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.repo.enable_in_chat()

    def test_ledger_entry_carries_no_proposal_content(self):
        pid = self.repo.propose_remember(subject="db", value="super-secret-value-42")
        TA.approve(path=self.repo.path, proposal_id=pid)
        entry = self.repo.approval_entries()[-1]
        blob = repr(entry)
        self.assertNotIn("super-secret-value-42", blob,
                         "the approval ledger entry must not carry the proposal's content")
        # exactly the fields approve.py already records (no new content-bearing field)
        self.assertEqual(set(entry) - {"seq", "at", "prev_hash", "entry_hash"},
                         {"kind", "decision", "proposal", "tool", "target",
                          "content_hash", "actor", "run", "scope"})


# ======================================================================================
# grant-exclusion — the approve tool NEVER rides the mcp__mokata__* auto-grant
# ======================================================================================
class TestGrantExclusion(unittest.TestCase):
    """The wildcard-covers-it hazard: `mcp__mokata__*` in permissions.allow ALREADY matches
    `mcp__mokata__approve`. Setup must write an explicit `permissions.ask` entry so Claude Code
    prompts on every approve call anyway — and it must do so WHENEVER it writes the allow grant."""

    APPROVE_ASK = "mcp__mokata__approve"
    ALLOW = "mcp__mokata__*"

    def _settings(self, root):
        from pathlib import Path
        return Path(root) / ".claude" / "settings.json"

    def _read(self, root):
        import json
        p = self._settings(root)
        if not p.exists():          # unsetup drops an emptied settings.json — grant fully removed
            return {}
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def test_merge_grant_writes_the_explicit_ask_entry(self):
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            HS._merge_grant(self._settings(d))
            data = self._read(d)
            self.assertIn(self.APPROVE_ASK, data["permissions"]["ask"],
                          "the approve tool must have an explicit prompt entry")

    def test_the_ask_entry_is_present_whenever_the_wildcard_grant_is(self):
        """The named wildcard-covers-it hazard: the two are inseparable. If the allow-wildcard is
        written, the approve ask entry is too — so the wildcard can NEVER silently cover approve."""
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            HS._merge_grant(self._settings(d))
            data = self._read(d)
            allow = data["permissions"].get("allow", [])
            ask = data["permissions"].get("ask", [])
            self.assertIn(self.ALLOW, allow)
            self.assertIn(self.APPROVE_ASK, ask,
                          "allow-wildcard present but approve NOT in ask — the hazard is live")

    def test_setup_plan_preview_names_the_ask_entry(self):
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            plan = HS.plan_setup("claude", root=d, home=d)
            text = HS.render_setup_plan(plan)
            self.assertIn(self.APPROVE_ASK, text)
            self.assertIn("permissions.ask", text)

    def test_setup_writes_the_ask_entry(self):
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda _: None)
            data = self._read(d)
            self.assertIn(self.APPROVE_ASK, data["permissions"]["ask"])
            self.assertIn(self.ALLOW, data["permissions"]["allow"])

    def test_unsetup_removes_the_ask_entry_symmetrically(self):
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda _: None)
            HS.unsetup_harness("claude", root=d, scope="project", home=d,
                               assume_yes=True, out=lambda _: None)
            data = self._read(d)
            self.assertNotIn(self.APPROVE_ASK,
                             (data.get("permissions") or {}).get("ask", []))
            self.assertNotIn(self.ALLOW,
                             (data.get("permissions") or {}).get("allow", []))

    def test_unsetup_preserves_a_users_own_ask_entries(self):
        import json
        from mokata import harness_setup as HS
        with tempfile.TemporaryDirectory() as d:
            p = self._settings(d)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"permissions": {"ask": ["Bash(rm*)"]}}), encoding="utf-8")
            HS.setup_harness("claude", root=d, scope="project", home=d,
                             assume_yes=True, out=lambda _: None)
            HS.unsetup_harness("claude", root=d, scope="project", home=d,
                               assume_yes=True, out=lambda _: None)
            data = self._read(d)
            self.assertIn("Bash(rm*)", data["permissions"]["ask"])
            self.assertNotIn(self.APPROVE_ASK, data["permissions"]["ask"])


# ======================================================================================
# registration — the tool exists and is served (opt-in, not absent)
# ======================================================================================
class TestRegistered(unittest.TestCase):
    def test_the_tool_is_registered(self):
        self.assertIn("approve", tool_names())

    def test_it_is_its_own_kind_not_a_write_tool(self):
        """It must be excluded from `_write_tools()` — it is the approval act, not a propose-only
        gated write, so the SI.3 write-tool sweeps must not touch it."""
        spec = next(t for t in TOOLS if t.name == "approve")
        self.assertNotEqual(spec.kind, "write")
        self.assertNotEqual(spec.kind, "read")


if __name__ == "__main__":
    unittest.main()
