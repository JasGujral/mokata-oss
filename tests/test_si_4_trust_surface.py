"""SI.4 — the K3 TRUST DIAL, actually wired to the write surfaces (0.0.13 seatbelt cluster, stage 4).

The bug this closes: K3 shipped a per-tool trust dial (`read-only` / `propose-only` / `gated-write`),
`mokata doctor` lints its levels, and the docs describe it — but **nothing ever constructed a
`TrustPolicy`**. Every `WriteGate` in `src/` was built as `WriteGate(ledger=...)`, and every
`WriteRequest` was built with no `tool=`, so the dial's two enforcement branches were unreachable
dead code. Setting `trust.remember = read-only` did exactly nothing.

Worse, the moment you DID wire it, `propose-only` was a functional dead end on MCP. The gate's rule
was `assume_yes = False` — i.e. "fall through to the interactive human prompt". But the MCP server is
stdio-bound to the harness and owns no TTY, and `prompt.read_yes_no` fails CLOSED off a TTY (returns
False without asking). So a `propose-only` tool on MCP would have had EVERY write declined — including
one carrying a valid, human-minted SI.3 approval — while printing "stdin is not a TTY" to the server's
stderr. The dial's middle rung was unusable on the surface it most needed to govern.

The semantic fix (in the gate, where it lives — not an MCP special case)
-----------------------------------------------------------------------
`propose-only` means **"no AUTO-approval; an explicit human decision is required"** — NOT "force a TTY
prompt". A prompt is merely one WAY to obtain a human decision. Post-SI.3 there is another, stronger
way: an approval a human MINTED out-of-band with `mokata approve <id>`, content-hash-bound to this
exact write, single-use and session-scoped. That IS explicit human consent, so it SATISFIES
propose-only. The gate therefore learns to be told `human_approved=True` — a carrier of an
already-verified decision, distinct from `assume_yes` (a stand-in auto-approval).

The honest ladder this produces on MCP (P22 — say what is NOT true)
------------------------------------------------------------------
Because SI.3 ALREADY forces every MCP write through propose → human-mints → redeem, `propose-only` and
`gated-write` land on the same behaviour there. So on the MCP surface the dial is really:

    read-only  ▸  write-allowed          (propose-only == gated-write == the SI.3 floor)

That is the truth, and these tests pin it as the truth rather than pretending to a three-rung ladder
MCP does not have. `propose-only` on MCP is worth setting precisely because it is *the floor SI.3
already built* — and giving it new teeth (a second approval, narrowed write kinds) would be new
product, not this stage.

What these tests pin:
  (a) The gate semantic, as a unit: propose-only + a verified human approval COMMITS with no TTY.
  (b) The keying: `trust.mcp` is a SURFACE default; `trust.<tool>` overrides it per tool.
  (c) The honest ladder on MCP: read-only blocks (ledgered) · propose-only proposes, then commits on a
      human approval · gated-write is status quo.
  (d) The floors are unloosenable at EVERY level: a secret is a hard block, and SI.3 holds — no trust
      level lets `approve=true` mint consent.
  (e) The threading: a tool that does NOT go through `_gated_write` (config_set, whose real gate lives
      in config_cmd) is governed too — the policy reaches the gate that actually writes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import approval as A                                   # noqa: E402
from mokata import session                                         # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.govern import (GATED_WRITE, PROPOSE_ONLY, READ_ONLY,   # noqa: E402
                           TrustPolicy, WriteGate, WriteRequest)
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.govern.trust import MCP_SURFACE, WritePolicy           # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.memory import MemoryStore                              # noqa: E402


def fake_secret() -> str:
    """The canonical AWS-docs example key, assembled at RUNTIME.

    Not a stylistic choice: mokata's own secret-guard PreToolUse hook blocks writing a file that
    carries a secret literal, so a test for the secret hard-block cannot spell one out. Dogfooding."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


class _Repo:
    """An initialized mokata repo on a pinned run id, whose trust dial the test can set."""

    def __init__(self, trust=None, run_id="run-si4"):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        if trust is not None:
            self.set_trust(trust)
        self.run_id = run_id
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run_id})
        self._env.start()
        session.reset_for_test()

    def close(self):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    def set_trust(self, levels):
        """Write `settings.trust` into the committed manifest — the EXISTING shape (a flat
        {key: level} dict), so this is additive: no schema change."""
        p = os.path.join(self.path, ".mokata", "manifest.json")
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["trust"] = levels
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def ledger(self):
        return AuditLedger.from_mokata_dir(os.path.join(self.path, ".mokata"))

    def entries(self, kind=None):
        led = self.ledger()
        rows = led.entries() if hasattr(led, "entries") else list(led.read())
        return [e for e in rows if kind is None or e.get("kind") == kind]

    def human_approves(self, pid, actor="human"):
        return A.approve(self.path, pid, actor=actor, ledger=self.ledger())

    def remembered(self, subject):
        store = MemoryStore.from_surface(Surface.load(self.path))
        return [i for i in store.scoped_active() if i.subject == subject]


def req(tool, surface=None, content="value=1"):
    return WriteRequest("config", "/repo/x.cfg", content, tool=tool, surface=surface)


def _never_ask(_text):
    raise AssertionError("the gate must NOT reach for a human prompt under a verified approval")


# ======================================================================================
# (a) the gate semantic — propose-only means "a human must decide", not "prompt at a TTY"
# ======================================================================================

class TestGateSemantic(unittest.TestCase):
    """The latent defect, fixed where it lives: in the gate, not with an MCP special case."""

    def test_propose_only_commits_under_a_verified_human_approval_with_no_tty(self):
        """THE fix. A human-minted, SI.3-verified approval IS explicit human consent, so it
        SATISFIES propose-only. No TTY is consulted (there is none on MCP)."""
        gate = WriteGate(trust=TrustPolicy({"agent-x": PROPOSE_ONLY}))
        committed = []
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1),
                          human_approved=True,
                          confirm=_never_ask)          # a TTY prompt would be a BUG here
        self.assertTrue(out.committed, "a verified human approval must satisfy propose-only")
        self.assertEqual(committed, [1])

    def test_propose_only_without_a_verified_approval_still_refuses_auto_approval(self):
        """The rung keeps its teeth: assume_yes (a STAND-IN approval) is not a human decision."""
        gate = WriteGate(trust=TrustPolicy({"agent-x": PROPOSE_ONLY}))
        committed = []
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1),
                          assume_yes=True, confirm=lambda _t: False)
        self.assertFalse(out.committed)
        self.assertEqual(committed, [])

    def test_read_only_blocks_even_a_verified_human_approval(self):
        """read-only is a CAPABILITY bound, not a consent question — approval cannot lift it."""
        gate = WriteGate(trust=TrustPolicy({"agent-x": READ_ONLY}))
        committed = []
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1),
                          human_approved=True, assume_yes=True)
        self.assertFalse(out.committed)
        self.assertEqual(committed, [])
        self.assertIn("read-only", out.reason)

    def test_a_secret_is_hard_blocked_at_every_trust_level_even_when_human_approved(self):
        """I1/P2 — a security block is not a methodology gate. No rung, and no approval, lifts it."""
        for level in (READ_ONLY, PROPOSE_ONLY, GATED_WRITE):
            with self.subTest(level=level):
                gate = WriteGate(trust=TrustPolicy({"agent-x": level}))
                committed = []
                out = gate.submit(
                    req("agent-x", content=f"aws_key = '{fake_secret()}'"),
                    commit=lambda: committed.append(1),
                    human_approved=True, assume_yes=True)
                self.assertFalse(out.committed)
                self.assertEqual(committed, [])

    def test_gated_write_is_unchanged(self):
        gate = WriteGate(trust=TrustPolicy({"agent-x": GATED_WRITE}))
        committed = []
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1), assume_yes=True)
        self.assertTrue(out.committed)

    def test_no_policy_at_all_is_byte_identical(self):
        gate = WriteGate()
        committed = []
        out = gate.submit(WriteRequest("config", "/x", "v"),
                          commit=lambda: committed.append(1), assume_yes=True)
        self.assertTrue(out.committed)


# ======================================================================================
# (b) the keying — a `trust.mcp` SURFACE default, overridable per tool
# ======================================================================================

class TestKeying(unittest.TestCase):

    def test_the_surface_default_governs_a_tool_with_no_level_of_its_own(self):
        policy = TrustPolicy({MCP_SURFACE: READ_ONLY})
        self.assertEqual(policy.level("remember", surface=MCP_SURFACE), READ_ONLY)

    def test_a_per_tool_level_overrides_the_surface_default(self):
        policy = TrustPolicy({MCP_SURFACE: READ_ONLY, "remember": GATED_WRITE})
        self.assertEqual(policy.level("remember", surface=MCP_SURFACE), GATED_WRITE)
        self.assertEqual(policy.level("session_push", surface=MCP_SURFACE), READ_ONLY)

    def test_the_default_with_nothing_set_is_gated_write(self):
        policy = TrustPolicy({})
        self.assertEqual(policy.level("remember", surface=MCP_SURFACE), GATED_WRITE)

    def test_a_surface_key_does_not_leak_onto_another_surface(self):
        policy = TrustPolicy({MCP_SURFACE: READ_ONLY})
        self.assertEqual(policy.level("skills", surface="cli"), GATED_WRITE)


# ======================================================================================
# (c) the honest ladder, on the real MCP surface
# ======================================================================================

class TestTheLadderOnMcp(unittest.TestCase):
    """read-only ▸ write-allowed. propose-only == gated-write == the floor SI.3 already built."""

    def test_read_only_blocks_a_write_tool_and_records_the_reason(self):
        repo = _Repo(trust={MCP_SURFACE: READ_ONLY})
        self.addCleanup(repo.close)
        res = TW.remember(path=repo.path, subject="db", value="postgres")
        self.assertEqual(res["status"], "refused")
        self.assertFalse(res.get("committed", False))
        self.assertEqual(res.get("reason_code"), "read-only")
        self.assertEqual(repo.remembered("db"), [], "a read-only tool must write nothing")
        self.assertNotIn("proposal_id", res,
                         "a read-only tool must not even propose — the human would be asked to "
                         "approve a write that can never commit")
        blocked = [e for e in repo.entries("write_gate") if e.get("decision") == "blocked"]
        self.assertTrue(blocked, "the refusal must be on the audit ledger")
        self.assertIn("read-only", blocked[-1].get("reason", ""))

    def test_propose_only_proposes_when_no_human_has_approved(self):
        repo = _Repo(trust={MCP_SURFACE: PROPOSE_ONLY})
        self.addCleanup(repo.close)
        res = TW.remember(path=repo.path, subject="db", value="postgres", approve=True)
        self.assertEqual(res["status"], "proposed")
        self.assertFalse(res.get("committed", False))
        self.assertEqual(repo.remembered("db"), [])

    def test_propose_only_commits_on_a_human_minted_approval(self):
        """The rung that was a dead end. A human approved it out-of-band; it must COMMIT."""
        repo = _Repo(trust={MCP_SURFACE: PROPOSE_ONLY})
        self.addCleanup(repo.close)
        proposed = TW.remember(path=repo.path, subject="db", value="postgres")
        pid = proposed["proposal_id"]
        repo.human_approves(pid)

        res = TW.remember(path=repo.path, subject="db", value="postgres", proposal_id=pid)
        self.assertEqual(res["status"], "committed",
                         "propose-only + a verified human approval must COMMIT (it was declining)")
        self.assertTrue(res["committed"])
        self.assertEqual(len(repo.remembered("db")), 1)

    def test_gated_write_is_the_status_quo(self):
        repo = _Repo(trust={MCP_SURFACE: GATED_WRITE})
        self.addCleanup(repo.close)
        proposed = TW.remember(path=repo.path, subject="db", value="postgres")
        repo.human_approves(proposed["proposal_id"])
        res = TW.remember(path=repo.path, subject="db", value="postgres",
                          proposal_id=proposed["proposal_id"])
        self.assertEqual(res["status"], "committed")
        self.assertEqual(len(repo.remembered("db")), 1)

    def test_a_per_tool_override_lifts_one_tool_off_a_read_only_surface(self):
        repo = _Repo(trust={MCP_SURFACE: READ_ONLY, "remember": GATED_WRITE})
        self.addCleanup(repo.close)
        proposed = TW.remember(path=repo.path, subject="db", value="postgres")
        repo.human_approves(proposed["proposal_id"])
        res = TW.remember(path=repo.path, subject="db", value="postgres",
                          proposal_id=proposed["proposal_id"])
        self.assertEqual(res["status"], "committed", "the per-tool override must win")

        blocked = TW.export_stack(path=repo.path)
        self.assertEqual(blocked["status"], "refused",
                         "a sibling tool stays governed by the read-only surface default")


# ======================================================================================
# (d) the floors — unloosenable at every rung
# ======================================================================================

class TestTheFloorsHold(unittest.TestCase):

    def test_si3_holds_at_every_trust_level_a_typed_approve_never_commits(self):
        for level in (PROPOSE_ONLY, GATED_WRITE):
            with self.subTest(level=level):
                repo = _Repo(trust={MCP_SURFACE: level})
                self.addCleanup(repo.close)
                res = TW.remember(path=repo.path, subject="db", value="pg",
                                  approve=True, confirm=True)
                self.assertEqual(res["status"], "proposed",
                                 "no trust level may let the MODEL mint consent (SI.3)")
                self.assertFalse(res.get("committed", False))
                self.assertEqual(repo.remembered("db"), [])

    def test_a_secret_is_hard_blocked_on_mcp_even_at_gated_write_with_a_real_approval(self):
        repo = _Repo(trust={MCP_SURFACE: GATED_WRITE})
        self.addCleanup(repo.close)
        secret = fake_secret()
        proposed = TW.remember(path=repo.path, subject="creds", value=secret)
        repo.human_approves(proposed["proposal_id"])
        res = TW.remember(path=repo.path, subject="creds", value=secret,
                          proposal_id=proposed["proposal_id"])
        self.assertFalse(res["committed"], "a secret is a security block approval cannot override")
        self.assertEqual(repo.remembered("creds"), [])


# ======================================================================================
# (e) the threading — the policy reaches the gate that ACTUALLY writes
# ======================================================================================

class TestPolicyReachesTheRealGate(unittest.TestCase):
    """`config_set` does not go through `_gated_write`; its real WriteGate lives in `config_cmd`.
    If the policy did not thread that far, a read-only surface would not govern it."""

    def test_read_only_governs_config_set_whose_gate_lives_in_config_cmd(self):
        repo = _Repo(trust={MCP_SURFACE: READ_ONLY})
        self.addCleanup(repo.close)
        res = TW.config_set(path=repo.path, key="settings.density", value='"terse"')
        self.assertEqual(res["status"], "refused")
        self.assertFalse(res.get("committed", False))

    def test_propose_only_commits_config_set_on_a_human_minted_approval(self):
        repo = _Repo(trust={MCP_SURFACE: PROPOSE_ONLY})
        self.addCleanup(repo.close)
        proposed = TW.config_set(path=repo.path, key="settings.density", value='"terse"')
        self.assertEqual(proposed["status"], "proposed")
        repo.human_approves(proposed["proposal_id"])
        res = TW.config_set(path=repo.path, key="settings.density", value='"terse"',
                            proposal_id=proposed["proposal_id"])
        self.assertEqual(res["status"], "committed",
                         "the threaded policy must let a verified approval through the REAL gate")
        self.assertTrue(res["committed"])


# ======================================================================================
# (f) the seam, at the gates that ACTUALLY write
# ======================================================================================

# The MCP-reachable gate constructions. Several MCP tools never touch `_gated_write` — their real
# WriteGate is built deep in config_cmd / session_bundle / memory / team. If the policy stopped at
# the MCP boundary, those gates would stay ungoverned, and the dial would be enforced only by the
# `_consent` pre-check — one layer, easily bypassed by any future caller that skips it.
SEAM_FUNCTIONS = [
    ("mokata.config_cmd", "config_set"),
    ("mokata.memory.migrate", "migrate_memory"),
    ("mokata.session_bundle", "commit_session_push_gated"),
    ("mokata.session_bundle", "hydrate_bundle"),
    ("mokata.session_bundle", "commit_session_rename_gated"),
    ("mokata.team", "_gated_write"),
    ("mokata.team", "_join_vault"),
    ("mokata.team_audit", "share_audit"),
]


class TestTheSeamReachesEveryGate(unittest.TestCase):

    def test_every_mcp_reachable_gate_constructor_accepts_the_policy_seam(self):
        import importlib
        import inspect
        for mod_name, fn_name in SEAM_FUNCTIONS:
            with self.subTest(fn=f"{mod_name}.{fn_name}"):
                fn = getattr(importlib.import_module(mod_name), fn_name)
                self.assertIn("policy", inspect.signature(fn).parameters,
                              f"{mod_name}.{fn_name} builds a WriteGate but cannot be told the "
                              f"trust policy — the dial cannot reach the gate that writes")

    def test_the_memory_stores_own_gate_accepts_the_policy_seam(self):
        import inspect
        self.assertIn("policy",
                      inspect.signature(MemoryStore._gated_commit).parameters)

    def test_config_sets_real_gate_refuses_a_read_only_policy_even_with_assume_yes(self):
        """The gate that actually writes the manifest must itself enforce the dial."""
        from mokata import config_cmd
        repo = _Repo()
        self.addCleanup(repo.close)
        ro = WritePolicy(trust=TrustPolicy({MCP_SURFACE: READ_ONLY}),
                         tool="config_set", surface=MCP_SURFACE)
        res = config_cmd.config_set(repo.path, "settings.density", '"terse"',
                                    assume_yes=True, policy=ro)
        self.assertFalse(res.committed, "a read-only policy must block at the REAL gate")

    def test_the_memory_gate_refuses_a_read_only_policy_even_with_assume_yes(self):
        from mokata.memory import MemoryItem
        repo = _Repo()
        self.addCleanup(repo.close)
        store = MemoryStore.from_surface(Surface.load(repo.path))
        ro = WritePolicy(trust=TrustPolicy({MCP_SURFACE: READ_ONLY}),
                         tool="remember", surface=MCP_SURFACE)
        out = store.remember(MemoryItem.create("db", "postgres"), assume_yes=True, policy=ro)
        self.assertFalse(out.committed)
        self.assertEqual(repo.remembered("db"), [])

    def test_the_memory_gate_commits_a_propose_only_write_under_a_verified_approval(self):
        from mokata.memory import MemoryItem
        repo = _Repo()
        self.addCleanup(repo.close)
        store = MemoryStore.from_surface(Surface.load(repo.path))
        po = WritePolicy(trust=TrustPolicy({MCP_SURFACE: PROPOSE_ONLY}),
                         tool="remember", surface=MCP_SURFACE, human_approved=True)
        out = store.remember(MemoryItem.create("db", "postgres"), policy=po)
        self.assertTrue(out.committed,
                        "a verified human approval must satisfy propose-only at the real gate")
        self.assertEqual(len(repo.remembered("db")), 1)


class TestEveryWriteRequestNamesItsWriter(unittest.TestCase):
    """THE enabling fix. The dial keys on WHO is writing, so a `WriteRequest` built with no `tool=`
    is ANONYMOUS: the gate resolves it to the default and the dial silently does nothing. That is
    precisely how K3 came to be dead code — every construction in `src/` omitted it. This guard makes
    a regression impossible to merge quietly."""

    def test_no_writerequest_in_src_is_constructed_without_a_tool_identity(self):
        import ast
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "mokata")
        anonymous = []
        for root, _dirs, files in os.walk(src):
            for name in sorted(f for f in files if f.endswith(".py")):
                p = os.path.join(root, name)
                with open(p, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=p)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    if getattr(fn, "id", None) != "WriteRequest" and \
                            getattr(fn, "attr", None) != "WriteRequest":
                        continue
                    if not any(kw.arg == "tool" for kw in node.keywords):
                        rel = os.path.relpath(p, src)
                        anonymous.append(f"{rel}:{node.lineno}")
        self.assertEqual(anonymous, [],
                         "these WriteRequest constructions are anonymous — the trust dial cannot "
                         "see who is writing, so it cannot govern them: " + ", ".join(anonymous))


if __name__ == "__main__":
    unittest.main()
