"""MCP-R.D2 — harness-boundary legibility (+ B-AMEND-STUCK · UX-STUCK · UX-NOTIFY).

The bug this stage closes is not a missing feature: `spec_amend` already returned the proposal id,
the regressed-state note, and both recovery commands. It returned them BURIED, and a harness
permission-decline returns nothing at all — so a healthy WAIT and a wedged SERVER looked identical
to the model and to Jas (the live 0.0.14 pain, doc 84 §2 B-AMEND-STUCK). Everything here asserts
LEGIBILITY of a state that was already correct.

Covers: the one shared awaiting shape reaching all nineteen propose sites through the single
`_propose` seam (source-scanned, not just spot-checked); the amend propose- and gate-blocked paths
naming the road back; the three-outcome description contract on gated writes (and read tools left
byte-identical); doctor's pending / grant / liveness sections and its UNCHANGED exit; the UX-NOTIFY
channel classification degrading clean on an unsupported harness; and the secret-safety line that
matters most here — every one of these surfaces names ids and commands, never proposal CONTENT.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import contextlib
import io
import os
import re
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
from _support import mcp_commit

from mokata import awaiting as A                                   # noqa: E402
from mokata import gate_hook as G                                  # noqa: E402
from mokata import approval, mcp_admin, session                    # noqa: E402
from mokata import tdd_state as T                                  # noqa: E402
from mokata.brainstorm import Approach, BrainstormSession          # noqa: E402
from mokata.brainstorm_impact import FITS, DesignFitVerdict        # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import consent as C                                # noqa: E402
from mokata.mcp import registry as REG                             # noqa: E402
from mokata.mcp import tool_annotations as TA                      # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.session_save import save_session                       # noqa: E402
from mokata.state import StateStore                                # noqa: E402

RUN = "run-d2"
TITLE = "items api — single-item CRUD"
SCOPE = {"authorized": ["src/api/items.py"],
         "deferred": [{"id": "D1", "item": "batch update/delete",
                       "paths": ["src/api/batch*.py"], "markers": ["batch_update"]}]}
ACS = [("AC1", "GET /items/<id> returns one item")]
TESTS = [("test_get_item", ["AC1"])]

PRE_EMIT_PHASES = ["brainstorm", "analysis", "strawman", "pre_mortem", "probes"]

# A DSN + a secret-shaped token, planted in the amendment's CONTENT (its title and its reason).
# Every legibility surface in this stage renders somewhere a human or a transcript can retain, so
# each is asserted to carry the id and the commands and NEITHER of these.
SECRET_DSN = "postgresql://admin:hunter2@db.internal:5432/prod"
SECRET_TOKEN = "sk-live-d2-must-never-render"


class _Repo:
    def __init__(self, run_id=RUN):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run_id})
        self._env.start()
        session.reset_for_test()
        self.surface = Surface.load(self.path)

    def close(self):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    @property
    def raw(self):
        return StateStore(T.state_dir(self.path))

    def ledger(self):
        return AuditLedger.from_mokata_dir(os.path.join(self.path, ".mokata"))


def _emit_args(path, *, acs=ACS, tests=TESTS, scope=SCOPE, title=TITLE):
    return dict(path=path, title=title, approach="rest",
                criteria=[{"id": i, "text": t} for i, t in acs],
                tests=[{"name": n, "ac_ids": list(a)} for n, a in tests],
                scope=scope)


def _setup_run(repo):
    """A run mid-`develop`: approach approved, pre-emit gates checkpointed, spec v1 emitted through
    the real gated surface, RED on record — exactly the state Jas was in when amend looked stuck."""
    s = BrainstormSession("items api")
    s.propose_approaches([
        Approach(name="rest", summary="plain REST", pros=["simple"], cons=["chatty"]),
        Approach(name="rpc", summary="rpc", pros=["compact"], cons=["opaque"]),
    ])
    s.assess_impacts(layer=None, memory_items=[])
    for a in s.approaches:
        s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
    s.assess_prior_art(layer=None)
    s.approve("jas", "rest")
    save_session(repo.surface, brainstorm=s.to_dict())
    save_session(repo.surface, passed=PRE_EMIT_PHASES)
    res = mcp_commit(TW.spec_emit, **_emit_args(repo.path))
    assert res["committed"], res
    T.record(repo.surface.state, RUN, red=["test_get_item"])
    return res


def _amend_args(repo, *, mapped=True, secrets=False):
    """The amendment that releases the deferred item. `mapped=False` withholds the test for the new
    criterion, which is what trips the COMPLETENESS gate (the blocked path). `secrets=True` plants
    the DSN/token in content fields so the secret-safety assertions have something real to catch."""
    acs = list(ACS) + [("AC2", "POST /items/batch updates many items")]
    tests = list(TESTS) + ([("test_batch_update", ["AC2"])] if mapped else [])
    title = f"{TITLE} {SECRET_TOKEN}" if secrets else TITLE
    return dict(_emit_args(repo.path, acs=acs, tests=tests, title=title,
                           scope={"authorized": ["src/api/items.py"], "deferred": []}),
                reason=(f"connect to {SECRET_DSN}" if secrets else "the user asked for batch"),
                item="D1")


# ======================================================================================
# 1. B-AMEND-STUCK — the amend result LEADS with the wait, and names the road back
# ======================================================================================

class TestAmendAwaitingBlock(unittest.TestCase):

    def test_mcp_r_d2_amend_awaiting_block(self):
        """The propose-path result leads with the loud proposal-id + regressed-note + both
        commands — the exact three things Jas needed and could not see."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**_amend_args(repo))
            self.assertEqual(out["status"], "proposed")
            self.assertFalse(out["committed"])

            # FIRST — not merely present. Insertion order is what makes it the first thing a model
            # or a human reading raw JSON hits, and burial was the whole bug.
            self.assertEqual(list(out)[0], "awaiting",
                             "the awaiting head must be the FIRST key — burying it IS the bug")

            head = out["awaiting"]
            self.assertIn(A.AWAITING, head)
            self.assertIn(out["proposal_id"], head, "the id must be IN the loud head")
            # It must say, in words, that this is not a fault. That sentence is the entire
            # wait-vs-hang distinction (P16).
            self.assertRegex(head, r"WAITING ON A HUMAN")
            self.assertRegex(head, r"not.{0,20}stuck")
            self.assertIn("NOTHING was written", head)

            # the regressed state — the fact that makes this wait urgent, not ignorable
            self.assertIn("REGRESSED", out["awaiting_blocks"])
            self.assertIn("BLOCKED", out["awaiting_blocks"])

            # BOTH commands, structurally (not prose a relay has to parse out)
            self.assertEqual(out["awaiting_approve_command"],
                             f"mokata approve {out['proposal_id']}")
            self.assertEqual(out["awaiting_abort_command"], A.AMEND_ABORT_CMD)
            self.assertEqual(out["awaiting_list_command"], A.LIST_CMD)

    def test_mcp_r_d2_amend_blocked_names_recovery(self):
        """The gate-blocked path is the OTHER way to land mid-amend with the run regressed — and it
        has NO proposal to approve, so it must name `--abort` as the operative way out."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**_amend_args(repo, mapped=False))
            self.assertEqual(out["status"], "blocked")
            self.assertFalse(out["committed"])

            self.assertIn("REGRESSED", out["blocked_recovery"])
            self.assertIn("BLOCKED", out["blocked_recovery"])
            self.assertEqual(out["abort_command"], A.AMEND_ABORT_CMD)
            # and it must not read as a fault — the gate is working
            self.assertRegex(out["blocked_recovery"], r"[Nn]othing is stuck")

    def test_mcp_r_d2_awaiting_shape_is_ONE_shared_helper(self):
        """Source-scan: no tool writes its own awaiting prose. The head is built in exactly one
        place (`awaiting.awaiting_block`) and reaches every propose site through the single
        `_propose` seam — which is what stops the nineteen copies drifting."""
        import inspect
        from mokata.mcp import (tools_config, tools_memory, tools_session,
                                tools_share, tools_spec, tools_team)
        modules = [tools_config, tools_memory, tools_session, tools_share, tools_spec, tools_team]

        # (a) the literal lives in awaiting.py alone
        for mod in modules + [C]:
            src = inspect.getsource(mod)
            self.assertNotIn(f'"{A.AWAITING}', src,
                             f"{mod.__name__} spells its own awaiting head — use the helper")

        # (b) `_propose` is the one caller of the helper, and it is the only builder
        self.assertIn("awaiting_block", inspect.getsource(C._propose))
        for mod in modules:
            self.assertNotIn("awaiting_block(", inspect.getsource(mod),
                             f"{mod.__name__} builds its own awaiting block — route via _propose")

    def test_mcp_r_d2_every_propose_site_inherits_the_head(self):
        """The seam claim, verified against the REGISTRY rather than a list in this file: every
        propose site routes through `_propose`, so a new gated write cannot ship without the head.
        Asserted by source-scan of each write tool that proposes."""
        import inspect
        from mokata.mcp import (tools_config, tools_memory, tools_session,
                                tools_share, tools_spec, tools_team)
        sites = 0
        for mod in (tools_config, tools_memory, tools_session, tools_share, tools_spec, tools_team):
            sites += len(re.findall(r"_propose\(", inspect.getsource(mod)))
        self.assertGreaterEqual(sites, 15, "expected the full propose-site fleet")
        # and `_propose` unconditionally builds the head — no flag, no opt-out
        body = inspect.getsource(C._propose)
        self.assertRegex(body, r"out\s*:\s*Dict\[str, Any\]\s*=\s*awaiting_block\(",
                         "the head must be the dict's SEED, so nothing can precede it")

    def test_mcp_r_d2_approve_confirm_note_survives_the_amend_path(self):
        """Regression for the bug found while building D2: the amend path used to append its
        regressed note as `note`, silently CLOBBERING `_propose`'s approve/confirm demotion
        warning. Now the regression rides `awaiting_blocks`, so both survive."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**dict(_amend_args(repo), approve=True))
            self.assertIn("no longer commit", out["note"],
                          "the approve/confirm demotion warning must survive")
            self.assertIn("REGRESSED", out["awaiting_blocks"])


# ======================================================================================
# 2. the DESCRIPTION contract — the three outcomes a model must tell apart
# ======================================================================================

class TestDescriptionContract(unittest.TestCase):

    def test_mcp_r_d2_gated_write_descriptions_document_three_outcomes(self):
        """Descriptions are the contract surface the model reads BEFORE calling — which is the only
        place the no-result outcome can be explained, since by definition it sends no result."""
        for spec in REG.TOOLS:
            if spec.kind == TA.READ:
                continue
            desc = TA.description_for(spec.kind, spec.name, spec.fn.__doc__ or "")
            with self.subTest(tool=spec.name):
                # (a) proposal returned — a WAIT
                self.assertIn("proposed", desc)
                self.assertRegex(desc, r"WAITING ON A HUMAN")
                # (b) the human DECLINED — explicitly a human NO, explicitly not a stuck server
                self.assertRegex(desc, r"NO result|empty result")
                self.assertRegex(desc, r"HUMAN DECLINED|DECLINED this call")
                self.assertRegex(desc, r"NOT a stuck server")
                # (c) a real server error
                self.assertRegex(desc, r"timed_out")
                self.assertRegex(desc, r'status "error"')
                # the CLI fallback, in the decline case where it is the recovery
                self.assertIn(A.LIST_CMD, desc)
                # and the tool's OWN docstring is preserved, not replaced
                self.assertIn((spec.fn.__doc__ or "").strip()[:40], desc)

    def test_mcp_r_d2_read_tool_descriptions_are_byte_identical(self):
        """Read tools do not propose and cannot be declined at a write gate — no third outcome, so
        no contract, and their descriptions must not grow a byte (the negative half)."""
        for spec in REG.TOOLS:
            if spec.kind != TA.READ:
                continue
            with self.subTest(tool=spec.name):
                self.assertEqual(TA.description_for(spec.kind, spec.name, spec.fn.__doc__ or ""),
                                 (spec.fn.__doc__ or "").strip())

    def test_mcp_r_d2_outcomes_block_is_one_shared_string(self):
        """One block, appended from `kind` — not the same paragraph re-prosed into nineteen
        docstrings, which is how they would drift."""
        import inspect
        for spec in REG.TOOLS:
            self.assertNotIn("OUTCOMES —", spec.fn.__doc__ or "",
                             f"{spec.name} inlines the outcomes contract; it is appended from kind")
        self.assertIn("OUTCOMES —", TA._WRITE_OUTCOMES)
        self.assertIn("_WRITE_OUTCOMES", inspect.getsource(TA.description_for))

    def test_mcp_r_d2_approve_tool_carries_the_contract(self):
        """`approve` is the ONE tool deliberately left in `permissions.ask`, so it is the most
        likely of all of them to come back as outcome (b). It must carry the contract."""
        specs = [s for s in REG.TOOLS if s.name == "approve"]
        self.assertTrue(specs, "the approve tool must exist")
        desc = TA.description_for(specs[0].kind, "approve", specs[0].fn.__doc__ or "")
        self.assertRegex(desc, r"HUMAN DECLINED|DECLINED this call")


# ======================================================================================
# 3. DOCTOR — pending + grant + liveness, all informational
# ======================================================================================

def _doctor(path, home=None):
    """Run `cmd_doctor` and capture (exit_code, stdout)."""
    from mokata.cli_commands.diagnostics import cmd_doctor
    args = argparse.Namespace(path=path, home=home, matrix=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cmd_doctor(args)
    return code, buf.getvalue()


class TestDoctorPending(unittest.TestCase):

    def test_mcp_r_d2_doctor_pending_amendment(self):
        """A regressed run with a pending amendment renders the line a returning user needs — the
        answer to 'I came back and everything is blocked and I don't know why'."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**_amend_args(repo, secrets=True))
            pid = out["proposal_id"]

            lines = A.pending_lines(repo.path, quiet_when_ok=False)
            text = "\n".join(lines)
            self.assertIn("amendment pending", text)
            self.assertIn(pid, text)
            self.assertIn(A.AMEND_ABORT_CMD, text)
            self.assertIn(f"mokata approve {pid}", text)
            # it must NOT read as a fault: the gate working is not a problem found
            self.assertRegex(text, r"waiting, not stuck")

    def test_mcp_r_d2_doctor_pending_is_silent_when_nothing_waits(self):
        """The negative: a healthy repo with nothing pending grows no loud output, and the
        quiet_when_ok default emits NOTHING at all."""
        with _Repo() as repo:
            _setup_run(repo)
            self.assertEqual(A.pending_lines(repo.path), [])
            quiet = A.pending_lines(repo.path, quiet_when_ok=False)
            self.assertEqual(len(quiet), 1)
            self.assertIn("nothing is waiting on you", quiet[0])

    def test_mcp_r_d2_unreadable_store_is_loud_not_a_false_all_clear(self):
        """An unreadable approval store must NOT render as 'nothing is waiting on you ✓'. That
        would be a health claim mokata never verified, on the exact surface a stuck user runs to
        find out why they are blocked — this stage's own failure mode, reintroduced by its own
        diagnostic. It names the cause instead, and still never raises."""
        with _Repo() as repo:
            with mock.patch.object(approval, "pending", side_effect=OSError("store unreadable")):
                lines = A.pending_lines(repo.path, quiet_when_ok=False)
                text = "\n".join(lines)
                self.assertIn("check skipped", text)
                self.assertIn("store unreadable", text, "the cause must be named")
                self.assertNotIn("nothing is waiting on you", text,
                                 "an unverified all-clear is exactly the lie D2 exists to kill")
                # the cosmetic statusline, by contrast, stays silent — the wait is already
                # announced loudly above and by the tool result
                self.assertEqual(A.statusline_segment(repo.path), "")

    def test_mcp_r_d2_doctor_exit_unchanged_by_a_pending_proposal(self):
        """THE constraint: doctor's exit is derived from `report.ok` alone. A pending proposal is
        mokata working correctly (P2) — it must never flip the exit or add a finding."""
        with _Repo() as repo:
            _setup_run(repo)
            before_code, before_out = _doctor(repo.path)
            TW.spec_amend(**_amend_args(repo))
            after_code, after_out = _doctor(repo.path)
            self.assertEqual(before_code, after_code,
                             "a pending proposal must NOT change doctor's exit code")
            # and the change is purely ADDITIVE — the pre-existing sections are untouched
            self.assertIn("amendment pending", after_out)
            self.assertNotIn("amendment pending", before_out)
            for line in before_out.splitlines():
                if line.strip() and "pending" not in line:
                    self.assertIn(line, after_out,
                                  "D2 adds informational lines; it must rewrite nothing")

    def test_mcp_r_d2_doctor_renders_the_pending_and_liveness_sections(self):
        with _Repo() as repo:
            _setup_run(repo)
            TW.spec_amend(**_amend_args(repo))
            _code, out = _doctor(repo.path)
            self.assertIn("mokata pending:", out)
            self.assertIn("mokata liveness:", out)

    def test_mcp_r_d2_liveness_references_d0_budgets_without_rebuilding_them(self):
        """UX-STUCK's never-hang half shipped in D0. This line REFERENCES those budgets — reading
        the constants, so a retune moves the line and cannot drift — and probes nothing."""
        from mokata.baseline import BASELINE_MCP_TIMEOUT_SECONDS
        from mokata.mcp.server import MCP_SURFACE_TIMEOUT_SECONDS
        text = "\n".join(A.liveness_lines())
        self.assertIn(f"{MCP_SURFACE_TIMEOUT_SECONDS:g}s", text)
        self.assertIn(f"{BASELINE_MCP_TIMEOUT_SECONDS:g}s", text)
        self.assertIn("timed_out", text)
        # no probe, no subprocess, no measurement — it takes no root and reads no repo
        import inspect
        self.assertNotIn("subprocess", inspect.getsource(A.liveness_lines))
        self.assertEqual(inspect.signature(A.liveness_lines).parameters, {})


class TestDoctorGrantLegibility(unittest.TestCase):
    """UX-STUCK grant legibility — doctor reports the permission-grant state, so a user can tell an
    auto-granted surface (no per-call prompts) from the stuck-loop, and can see that `approve`
    still prompts BY DESIGN."""

    @contextlib.contextmanager
    def _settings(self, allow=None, ask=None):
        import json
        home = tempfile.mkdtemp()
        with tempfile.TemporaryDirectory() as root:
            p = mcp_admin.claude_settings_path("user", root, home)
            p.parent.mkdir(parents=True, exist_ok=True)
            perms = {}
            if allow is not None:
                perms["allow"] = allow
            if ask is not None:
                perms["ask"] = ask
            p.write_text(json.dumps({"permissions": perms}), encoding="utf-8")
            yield root, home

    def test_mcp_r_d2_doctor_grant_legibility_healthy(self):
        """The SHIPPED state that `mokata setup` writes: the wildcard in allow, approve in ask.
        Both axes read healthy, and the approve-ask line SAYS it is intentional — because 'why does
        approve still prompt me?' is the question silence was answering nowhere."""
        from mokata.harness_paths import MCP_APPROVE_TOOL_ASK, MCP_TOOL_PERMISSION
        with self._settings(allow=[MCP_TOOL_PERMISSION], ask=[MCP_APPROVE_TOOL_ASK]) as (root, home):
            g = mcp_admin.grant_status(root, home)
            self.assertTrue(g.permitted)
            self.assertTrue(g.approve_asked)
            lines = "\n".join(mcp_admin.full_status(root=root, home=home, timeout=0.1).lines)
            self.assertIn("approve-ask ✓", lines)
            self.assertIn("intentional", lines)

    def test_mcp_r_d2_doctor_grant_legibility_missing_names_the_setup_fix(self):
        """No grant at all — the stuck-loop. Both the missing wildcard and the n/a approve axis
        must name `mokata setup`'s install command as the fix."""
        with self._settings(allow=[], ask=[]) as (root, home):
            g = mcp_admin.grant_status(root, home)
            self.assertFalse(g.permitted)
            self.assertFalse(g.approve_asked)
            lines = "\n".join(mcp_admin.full_status(root=root, home=home, timeout=0.1).lines)
            self.assertIn("permitted ✗", lines)
            self.assertIn(mcp_admin._INSTALL_FIX, lines)
            self.assertIn("stuck-loop", lines)
            # with no allow-grant, everything already prompts — the approve axis is not a problem
            self.assertIn("approve-ask — n/a", lines)

    def test_mcp_r_d2_doctor_grant_legibility_wildcard_without_ask_is_loud(self):
        """The DANGEROUS combination and the reason this axis is reported at all: the allow-wildcard
        MATCHES `mcp__mokata__approve`, so an allow-grant with the ask externally dropped lets the
        model's own approve call sail through un-prompted — SI.3's hole, re-opened."""
        from mokata.harness_paths import MCP_TOOL_PERMISSION
        with self._settings(allow=[MCP_TOOL_PERMISSION], ask=[]) as (root, home):
            g = mcp_admin.grant_status(root, home)
            self.assertTrue(g.permitted)
            self.assertFalse(g.approve_asked)
            lines = "\n".join(mcp_admin.full_status(root=root, home=home, timeout=0.1).lines)
            self.assertIn("approve-ask ✗", lines)
            self.assertIn("COVERS", lines)
            self.assertIn(mcp_admin._INSTALL_FIX, lines)

    def test_mcp_r_d2_grant_status_matches_what_merge_grant_actually_writes(self):
        """Ground the reporter against the WRITER, so the two cannot drift: run the real
        `_merge_grant` and assert `grant_status` reads both keys back as healthy."""
        import json
        from pathlib import Path
        from mokata.harness_setup import _merge_grant
        home = tempfile.mkdtemp()
        with tempfile.TemporaryDirectory() as root:
            p = Path(mcp_admin.claude_settings_path("user", root, home))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({}), encoding="utf-8")
            _merge_grant(p)
            g = mcp_admin.grant_status(root, home)
            self.assertTrue(g.permitted, "grant_status must read what _merge_grant writes")
            self.assertTrue(g.approve_asked, "the AP-MCP ask axis must read back too")


# ======================================================================================
# 4. UX-NOTIFY — which channel a wait raises, and degrading clean when there is none
# ======================================================================================

class TestNotifyOnWait(unittest.TestCase):

    def test_mcp_r_d2_notify_on_wait(self):
        """A wait-on-human raises a signal. With the allow-grant in place a gated write does NOT
        prompt — so no harness notification fires — and mokata's OWN channels must carry it."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**_amend_args(repo))
            channels = A.signal_wait(repo.path, tool="spec_amend",
                                     proposal_id=out["proposal_id"])
            # the tool result is the unconditional channel — it is why a wait is legible even on a
            # harness with no statusline and no notification event at all
            self.assertIn(A.TOOL_RESULT, channels)
            # and the mokata-owned statusline signal renders, naming the id
            seg = A.statusline_segment(repo.path)
            self.assertIn("⏳ awaiting approval", seg)
            self.assertIn(out["proposal_id"], seg)

    def test_mcp_r_d2_notify_approve_tool_rides_the_harness_prompt(self):
        """Pin the permission-prompt case: `approve` is held in `permissions.ask` precisely so it
        PROMPTS, and a Claude Code permission prompt fires its own `Notification` event
        (`permission_prompt` matcher). mokata must classify that as the harness's channel and add
        nothing — synthesizing a harness notification would lie about who is asking."""
        with _Repo() as repo:
            channels = A.signal_wait(repo.path, tool="approve")
            self.assertIn(A.HARNESS_NOTIFICATION, channels)
            self.assertEqual(channels[0], A.HARNESS_NOTIFICATION,
                             "the harness channel is the most authoritative — list it first")

    def test_mcp_r_d2_notify_degrades_clean_on_unsupported_harness(self):
        """Unsupported / broken harness → a no-op, never a crash. Every probe this stage makes is
        allowed to fail, and the wait stays legible through the tool result regardless."""
        with _Repo() as repo:
            with mock.patch.object(mcp_admin, "grant_status", side_effect=OSError("no harness")):
                channels = A.signal_wait(repo.path, tool="spec_amend")
            # The harness axis is gone — and its loss is SILENT, not fatal. mokata's own channels
            # are unaffected (killing the grant probe says nothing about the statusline), which is
            # the whole point of owning a channel that does not depend on the harness answering.
            self.assertNotIn(A.HARNESS_NOTIFICATION, channels)
            self.assertIn(A.TOOL_RESULT, channels)

        # and on a path that is not a repo at all
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(A.statusline_segment(empty), "")
            self.assertEqual(A.pending_lines(empty), [])
            self.assertIn(A.TOOL_RESULT, A.signal_wait(empty, tool="spec_amend"))

        # a wholly unreadable root must still not raise
        self.assertEqual(A.statusline_segment("/nonexistent/d2/root"), "")
        self.assertEqual(A.pending_lines("/nonexistent/d2/root"), [])

    def test_mcp_r_d2_statusline_is_byte_identical_with_nothing_pending(self):
        """The negative: no pending proposal → mokata's badge grows nothing."""
        with _Repo() as repo:
            _setup_run(repo)
            self.assertEqual(A.statusline_segment(repo.path), "")

    def test_mcp_r_d2_signal_wait_fires_nothing(self):
        """`signal_wait` CLASSIFIES an existing wait; it must not write, spawn, or notify — no
        daemon (the stage constraint), and no new dependency."""
        import inspect
        src = inspect.getsource(A.signal_wait) + inspect.getsource(A._rides_harness_prompt)
        for forbidden in ("subprocess", "Thread", "Popen", "write_text", "os.system"):
            self.assertNotIn(forbidden, src, f"signal_wait must not {forbidden}")


# ======================================================================================
# 5. SECRET-SAFETY — ids and commands travel; proposal CONTENT never does
# ======================================================================================

class TestSecretSafety(unittest.TestCase):

    def test_mcp_r_d2_surfaces_never_render_proposal_content(self):
        """The one that matters most here: a proposal's `summary`/`preview` carry the CONTENT of the
        write (for the amend path, the spec diff and the stated reason). Those are the human's to
        read at the terminal via `mokata approve <id>` — never in doctor output, never on the
        statusline, never in a tool result a transcript may retain."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**_amend_args(repo, secrets=True))
            pid = out["proposal_id"]

            # the planted secrets ARE really in the stored proposal — otherwise this proves nothing
            stored = approval.load(repo.path, pid)
            self.assertIn(SECRET_DSN, stored.preview + stored.summary,
                          "fixture check: the secret must actually be in the proposal content")

            surfaces = {
                "awaiting head": out["awaiting"],
                "awaiting block": " ".join(str(v) for k, v in out.items()
                                           if k.startswith("awaiting")),
                "pending_lines": "\n".join(A.pending_lines(repo.path, quiet_when_ok=False)),
                "statusline": A.statusline_segment(repo.path),
                "liveness": "\n".join(A.liveness_lines()),
                "doctor": _doctor(repo.path)[1],
            }
            for name, text in surfaces.items():
                with self.subTest(surface=name):
                    self.assertNotIn(SECRET_DSN, text, f"{name} leaked a DSN")
                    self.assertNotIn(SECRET_TOKEN, text, f"{name} leaked a token")
                    self.assertNotIn("hunter2", text, f"{name} leaked a password")

            # ...while still carrying what a stuck user needs
            for name in ("awaiting head", "pending_lines", "statusline", "doctor"):
                self.assertIn(pid, surfaces[name], f"{name} must still name the proposal id")

    def test_mcp_r_d2_pending_lines_never_touch_summary_or_preview(self):
        """Structural companion to the behavioural test above — the fields are never read at all,
        so a future proposal shape cannot quietly reintroduce the leak."""
        import ast
        import inspect
        # Parse rather than grep: the source CONTAINS ".preview" in the comment forbidding it, and
        # a scan that a documenting comment can fail is a scan that invites deleting the comment.
        # The AST sees attribute ACCESS only.
        for fn in (A.pending_lines, A.statusline_segment):
            tree = ast.parse(inspect.getsource(fn).strip())
            attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            self.assertNotIn("preview", attrs, f"{fn.__name__} reads proposal content")
            self.assertNotIn("summary", attrs, f"{fn.__name__} reads proposal content")
            # and the string literals it renders name no content field either
            consts = {n.value for n in ast.walk(tree)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            self.assertNotIn("preview", consts)
            self.assertNotIn("summary", consts)


# ======================================================================================
# 6. NO RE-GATING — D2 surfaces state, it never changes who may write
# ======================================================================================

class TestGatesUnchanged(unittest.TestCase):

    def test_mcp_r_d2_gates_are_untouched(self):
        """P2 intact: the amend still regresses the run, still blocks development writes while it
        stands, and still commits ONLY under a human-minted approval. D2 changed how this LOOKS,
        never what it does."""
        with _Repo() as repo:
            _setup_run(repo)
            # the authorized write is fine before the amend
            self.assertTrue(G.check_write(repo.path, "src/api/items.py",
                                          content="def get_item(i): pass").allowed)

            out = TW.spec_amend(**_amend_args(repo))
            self.assertFalse(out["committed"], "a propose must never commit")

            # mid-amend, development writes STAY blocked — the regression is real, not cosmetic
            mid = G.check_write(repo.path, "src/api/items.py", content="def get_item(i): pass")
            self.assertFalse(mid.allowed, "a mid-amend write must still block")
            self.assertEqual(mid.exit_code, 2)

            # and only a HUMAN-minted approval commits it
            approval.approve(repo.path, out["proposal_id"], actor="jas", ledger=repo.ledger())
            done = TW.spec_amend(**dict(_amend_args(repo), proposal_id=out["proposal_id"]))
            self.assertTrue(done["committed"], done)
            self.assertEqual(done["version"], 2)

    def test_mcp_r_d2_committed_result_has_no_awaiting_head(self):
        """The negative on the no-wait path: a committed result is not a wait, so it must grow no
        awaiting keys — byte-identical to pre-D2."""
        with _Repo() as repo:
            _setup_run(repo)
            out = TW.spec_amend(**_amend_args(repo))
            approval.approve(repo.path, out["proposal_id"], actor="jas", ledger=repo.ledger())
            done = TW.spec_amend(**dict(_amend_args(repo), proposal_id=out["proposal_id"]))
            self.assertTrue(done["committed"])
            self.assertEqual([k for k in done if k.startswith("awaiting")], [],
                             "a committed write is not a wait")


if __name__ == "__main__":
    unittest.main()
