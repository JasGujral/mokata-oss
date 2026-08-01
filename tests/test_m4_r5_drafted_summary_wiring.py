"""M-4/R5 CONTINUATION — the drafter reaches the PRODUCT PATH, in two honest phases.

The seam (`test_m4_r5_drafted_summary.py`) proved a drafted summary can replace the placeholder.
This file proves the product actually drafts: `mokata memory consolidate` and the MCP tools, not
just a unit seam nothing calls.

WHY TWO PHASES — grounded, not chosen. `mokata memory consolidate` runs in a SEPARATE PROCESS from
the agent's session; `cli_commands/spec.py` states the same of `spec emit` ("a human at a terminal,
exactly like `mokata approve`"). There is no synchronous in-process drafter to block on, and the
execmode "handback" is sub-agent output-capping, not a channel for authored content. So the shape
is the one `spec_emit` already uses: mokata ASKS (phase 1, with the turns), the agent ANSWERS
(phase 2, submitting its draft), and the human still approves before anything is written.

This stage also adds the FIRST product caller of `apply_consolidation`. Before it, the gated apply
was reachable only from library code, so no consolidation of any kind could be applied at all.

CONTRACTS
  1. BARE CLI IS BYTE-IDENTICAL. `mokata memory consolidate` with no flags prints exactly what it
     printed before this stage — a human at a terminal cannot draft, and must not be made worse off.
  2. Phase 1 (`--drafting-request`) emits the summarize proposals WITH the turns to draft from.
  3. Phase 1 carries the drafting INSTRUCTION, from the single shared constant.
  4. Phase 1 is read-only — asking what to draft writes nothing.
  5. Phase 2 (`--draft S --value T`) puts the agent's text through the gate and stores it on approve.
  6. Phase 2 REFUSES at the gate: declining writes nothing.
  7. Phase 2 without `--value` is a usage error — mokata will not invent the summary.
  8. Phase 2 for an unknown session errors rather than guessing which cluster was meant.
  9. A drafted summary submitted through the product path is SECRET-SCANNED and hard-blocked.
 10. The stored summary carries the real `kind` (REFERENCE) — the seam's typing survives the CLI.
 11. MCP phase 1 (`consolidate_proposals`) returns the same request + the SAME instruction string.
 12. MCP phase 2 (`consolidate`) is PROPOSE-ONLY: the model gets a proposal_id and NOTHING is
     written. The model cannot approve its own draft (SI.3 — it may reference consent, never mint).
 13. MCP phase 2 validates before proposing (missing session/value, unknown session).
 14. The degrade contract survives the wiring: a cluster nobody drafted keeps the placeholder.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata.config import Surface
from mokata.memory import MemoryItem, MemoryStore
from mokata.memory.item import ACTIVE, EPISODIC, REFERENCE
from mokata.memory.consolidation import DRAFTING_INSTRUCTION
from mokata.init import init_repo


def _silent(*_a, **_k) -> None:
    pass


def _store(d) -> MemoryStore:
    return MemoryStore.from_surface(Surface.load(d))


def fake_secret() -> str:
    """Assembled at runtime — mokata's own secret-guard hook blocks writing a literal into a file
    (the SI.4 convention `test_si_6_writegate_side_doors.py` established)."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _repo_with_turns(d, session="sess-alpha", n=3):
    """A real initialized repo whose memory holds one episodic cluster (>=3 turns => a proposal)."""
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    store = _store(d)
    for i in range(n):
        store.backend.put(MemoryItem.create(
            session, f"turn {i}: we compared postgres and sqlite",
            mtype=EPISODIC, created_at=f"2026-01-0{i + 1}T00:00:00+00:00"))
    store.close()


def _run(argv):
    """Run the real CLI, capturing stdout+stderr. Returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestBareCliIsUnchanged(unittest.TestCase):
    """Contract 1 — THE regression bar. The wiring must not make the bare command worse."""

    def test_bare_consolidate_still_prints_the_placeholder_listing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            code, out, _ = _run(["memory", "consolidate", "--path", d])
            self.assertEqual(code, 0)
            self.assertIn("PROPOSAL-ONLY", out)
            self.assertIn("(summarize) [episodic] sess-alpha", out)
            # the placeholder VALUE, unchanged — no drafter was injected, so none was drafted
            self.assertIn("summary of 3 episodic turns in 'sess-alpha'", out)
            # and the bare path does NOT dump raw turns at a human who cannot draft them
            self.assertNotIn("turn 0:", out)
            self.assertNotIn(DRAFTING_INSTRUCTION, out)

    def test_bare_consolidate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            before = len(_store(d).backend.all(statuses=(ACTIVE,)))
            _run(["memory", "consolidate", "--path", d])
            self.assertEqual(len(_store(d).backend.all(statuses=(ACTIVE,))), before)


class TestPhase1DraftingRequest(unittest.TestCase):
    """Contracts 2, 3, 4 — mokata ASKS for a draft, and hands over what is needed to write one."""

    def _request(self, d):
        code, out, _ = _run(["memory", "consolidate", "--drafting-request", "--path", d])
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_it_carries_the_turns_to_draft_from(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            payload = self._request(d)
            reqs = payload["drafting_requests"]
            self.assertEqual(len(reqs), 1)
            self.assertEqual(reqs[0]["session"], "sess-alpha")
            values = [t["value"] for t in reqs[0]["turns"]]
            self.assertEqual(len(values), 3)
            self.assertTrue(all("postgres and sqlite" in v for v in values),
                            "the drafter needs the turns' CONTENT, not a count")

    def test_it_carries_the_shared_instruction(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            self.assertEqual(self._request(d)["instruction"], DRAFTING_INSTRUCTION)

    def test_nothing_to_draft_is_still_valid_json(self):
        """The consumer is a machine. "Nothing to draft" must arrive in the same SHAPE as
        everything else — English prose here would be a parse error, not an answer."""
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=_silent)
            payload = self._request(d)          # asserts exit 0 + parses as JSON
            self.assertEqual(payload["drafting_requests"], [])
            self.assertEqual(payload["instruction"], DRAFTING_INSTRUCTION)

    def test_asking_what_to_draft_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            before = len(_store(d).backend.all(statuses=(ACTIVE,)))
            self._request(d)
            self.assertEqual(len(_store(d).backend.all(statuses=(ACTIVE,))), before)


class TestPhase2SubmitTheDraft(unittest.TestCase):
    """Contracts 5-10 — the agent ANSWERS, and the human still decides."""

    DRAFT = "Compared Postgres and SQLite for the team store; Postgres chosen."

    def _submit(self, d, value=DRAFT, session="sess-alpha", answer="a"):
        """Drive the real CLI phase 2 with a scripted answer at the human gate.

        The gate is `read_approve_edit_reject`, whose keys are a/e/r and whose SAFE DEFAULT is
        reject — so "a" approves and anything else declines. Patching `builtins.input` drives the
        real gate rather than stubbing it out, which is the point: the test exercises the gate that
        actually stands between a drafted summary and the store."""
        from unittest import mock
        with mock.patch("builtins.input", return_value=answer):
            return _run(["memory", "consolidate", "--draft", session,
                         "--value", value, "--path", d])

    def test_an_approved_draft_is_stored(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            code, out, err = self._submit(d)
            self.assertEqual(code, 0, f"out={out!r} err={err!r}")
            stored = [i for i in _store(d).backend.all(statuses=(ACTIVE,))
                      if i.value == self.DRAFT]
            self.assertEqual(len(stored), 1, "the agent's drafted summary must reach the store")

    def test_the_stored_draft_carries_the_real_kind(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            self._submit(d)
            stored = [i for i in _store(d).backend.all(statuses=(ACTIVE,))
                      if i.value == self.DRAFT]
            self.assertTrue(stored, "precondition: the draft was stored")
            self.assertEqual(stored[0].kind, REFERENCE,
                             "the seam's typing must survive the trip through the CLI")

    def test_declining_at_the_gate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            self._submit(d, answer="r")
            values = [i.value for i in _store(d).backend.all()]
            self.assertNotIn(self.DRAFT, values, "a declined draft must not be stored")

    def test_a_drafted_secret_is_hard_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            poisoned = f"the team agreed the prod key is {fake_secret()}"
            self._submit(d, value=poisoned)
            values = [i.value for i in _store(d).backend.all()]
            self.assertFalse(any(fake_secret() in v for v in values),
                             "a drafted secret must not reach the store through the product path")

    def test_the_humans_edit_wins_over_the_agents_draft(self):
        """The gate's `edit` must store what the HUMAN typed. Storing the agent's draft while the
        human believes they replaced it is the one outcome an edit may never produce."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            answers = iter(["e", "The human's own wording."])
            with mock.patch("builtins.input", side_effect=lambda _p: next(answers)):
                _run(["memory", "consolidate", "--draft", "sess-alpha",
                      "--value", self.DRAFT, "--path", d])
            values = [i.value for i in _store(d).backend.all(statuses=(ACTIVE,))]
            self.assertIn("The human's own wording.", values)
            self.assertNotIn(self.DRAFT, values, "the agent's draft must not survive an edit")

    def test_an_empty_edit_changes_nothing(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            answers = iter(["e", "   "])
            with mock.patch("builtins.input", side_effect=lambda _p: next(answers)):
                code, out, _ = _run(["memory", "consolidate", "--draft", "sess-alpha",
                                     "--value", self.DRAFT, "--path", d])
            self.assertEqual(code, 0)
            values = [i.value for i in _store(d).backend.all()]
            self.assertNotIn(self.DRAFT, values,
                             "an empty edit must not silently fall back to the agent's draft")

    def test_yes_does_not_mint_consent_on_a_draft(self):
        """`--yes` must NOT be a consent-minting shortcut on the draft path.

        The danger is concrete and eight lines away in the same file: `_memory_edit` DOES honour
        `--yes` (memory.py:277 `if args.yes: ... apply_proposal("approve")`), and `--yes` is on the
        shared `memory` parser. Copying that shape here would let the AGENT approve the text the
        AGENT just wrote — the model minting consent for its own content, which is exactly what
        `approval.py` forbids.

        This pins the human act itself, not an outcome: the gate FUNCTION must be consulted, and
        its verdict must decide. A `if args.yes:` short-circuit above the gate leaves `consulted`
        empty; an auto-approve stores the draft despite a rejecting gate. Either fails here."""
        from unittest import mock

        from mokata.prompt import GateResponse

        for flags in ([], ["--yes"]):
            with self.subTest(flags=flags or ["(none)"]):
                consulted = []

                def _rejecting_gate(_prompt, proposed, **_kw):
                    consulted.append(proposed)
                    return GateResponse("reject")

                with tempfile.TemporaryDirectory() as d:
                    _repo_with_turns(d)
                    with mock.patch("mokata.prompt.read_approve_edit_reject",
                                    side_effect=_rejecting_gate):
                        _run(["memory", "consolidate", "--draft", "sess-alpha",
                              "--value", self.DRAFT, "--path", d] + flags)
                    self.assertEqual(len(consulted), 1,
                                     "the human gate must be consulted — `--yes` may not skip it")
                    values = [i.value for i in _store(d).backend.all()]
                    self.assertNotIn(self.DRAFT, values,
                                     "the GATE's verdict decides, not `--yes`")

    def test_the_refusal_to_stage_an_edit_is_loud(self):
        """Defect-#1's REAL branch: the edit was accepted at the gate but could not be staged.

        Reached by making the re-derivation return no proposal — not the empty-edit guard above it,
        which is a different (and already-covered) path. Falling through here would store the
        AGENT's draft while the human believes they replaced it, so the refusal must be loud AND
        empty-handed: a non-zero exit, a named reason on stderr, and nothing written."""
        from unittest import mock

        from mokata.memory import consolidation as C

        real_find = C.find_summarize
        calls = {"n": 0}

        def _stages_then_vanishes(proposals, session):
            calls["n"] += 1
            # 1st call resolves the proposal being submitted; the 2nd (the re-derivation of the
            # human's edited text) finds nothing — the refusal branch's precondition.
            return real_find(proposals, session) if calls["n"] == 1 else None

        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            answers = iter(["e", "The human's own wording."])
            with mock.patch.object(C, "find_summarize", side_effect=_stages_then_vanishes):
                with mock.patch("builtins.input", side_effect=lambda _p: next(answers)):
                    code, _out, err = _run(["memory", "consolidate", "--draft", "sess-alpha",
                                            "--value", self.DRAFT, "--path", d])
            self.assertEqual(code, 1, "a refusal must not report success")
            self.assertIn("could not stage your edited summary", err,
                          "the human must be TOLD their edit did not land")
            values = [i.value for i in _store(d).backend.all()]
            self.assertNotIn(self.DRAFT, values,
                             "the agent's draft must not slip in behind a refused edit")
            self.assertNotIn("The human's own wording.", values)

    def test_no_value_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            code, _out, err = _run(["memory", "consolidate", "--draft", "sess-alpha",
                                    "--path", d])
            self.assertEqual(code, 2)
            self.assertIn("mokata does not write it", err)

    def test_an_unknown_session_errors_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            code, _out, err = self._submit(d, session="no-such-session")
            self.assertEqual(code, 1)
            self.assertIn("no summarize proposal", err)


class TestTheMcpSurface(unittest.TestCase):
    """Contracts 11, 12, 13 — the same two phases where the agent actually lives."""

    def test_phase1_tool_returns_the_request_and_the_same_instruction(self):
        from mokata.mcp.tools_read import consolidate_proposals
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            resp = consolidate_proposals(path=d)
            self.assertTrue(resp["enabled"])
            self.assertEqual(resp["instruction"], DRAFTING_INSTRUCTION,
                             "CLI and MCP must not drift into two differently-worded asks")
            self.assertEqual(len(resp["drafting_requests"]), 1)
            self.assertEqual(len(resp["drafting_requests"][0]["turns"]), 3)

    def test_both_surfaces_ledger_the_drafting_request_identically(self):
        """LEDGER PARITY — asking what to draft leaves the SAME provenance whether the human asked
        (CLI) or the agent did (MCP). Pinned on both sides so they cannot silently diverge: a
        drafting request that is recorded on one surface and invisible on the other would make the
        audit trail depend on which door the request came through."""
        from mokata.govern import AuditLedger
        from mokata.mcp.tools_read import consolidate_proposals

        def _records(d):
            led = AuditLedger.from_mokata_dir(Surface.load(d).mokata_dir)
            return len([e for e in led.entries() if e["kind"] == "consolidation_proposal"])

        with tempfile.TemporaryDirectory() as d:          # the CLI surface
            _repo_with_turns(d)
            self.assertEqual(_records(d), 0)
            _run(["memory", "consolidate", "--drafting-request", "--path", d])
            cli_records = _records(d)

        with tempfile.TemporaryDirectory() as d:          # the MCP surface
            _repo_with_turns(d)
            self.assertEqual(_records(d), 0)
            consolidate_proposals(path=d)
            mcp_records = _records(d)

        self.assertEqual(cli_records, 1, "one cluster => one recorded drafting request (CLI)")
        self.assertEqual(mcp_records, 1, "one cluster => one recorded drafting request (MCP)")

    def test_the_drafting_request_writes_no_memory_state(self):
        """The ledger record above is PROVENANCE, not a memory write. Asking what to draft must
        leave the store itself untouched — no item, no status change, on either surface."""
        from mokata.mcp.tools_read import consolidate_proposals

        def _snapshot(d):
            return sorted((i.id, i.value, i.status) for i in _store(d).backend.all())

        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            before = _snapshot(d)
            _run(["memory", "consolidate", "--drafting-request", "--path", d])
            consolidate_proposals(path=d)
            self.assertEqual(_snapshot(d), before,
                             "a drafting request must not touch memory state on either surface")

    def test_phase2_tool_is_propose_only_and_writes_nothing(self):
        from mokata.mcp.tools_memory import consolidate
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            resp = consolidate(path=d, session="sess-alpha", value="A drafted summary.")
            self.assertEqual(resp.get("status"), "proposed",
                             "the model must get a proposal, never a write")
            self.assertIn("proposal_id", resp)
            values = [i.value for i in _store(d).backend.all()]
            self.assertNotIn("A drafted summary.", values)

    def test_the_model_cannot_approve_its_own_draft(self):
        """SI.3 — `approve=True` is accepted for schema stability but commits nothing."""
        from mokata.mcp.tools_memory import consolidate
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            resp = consolidate(path=d, session="sess-alpha", value="Self-approved.",
                               approve=True, confirm=True)
            self.assertEqual(resp.get("status"), "proposed",
                             "`approve=True` is accepted for schema stability but DEMOTED — it "
                             "must still come back as a proposal, never a commit")
            values = [i.value for i in _store(d).backend.all()]
            self.assertNotIn("Self-approved.", values,
                             "there must be no argument a model can pass that reaches a write")

    def test_phase2_tool_validates_its_inputs(self):
        from mokata.mcp.tools_memory import consolidate
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d)
            self.assertEqual(consolidate(path=d, session="sess-alpha")["status"], "error")
            self.assertEqual(consolidate(path=d, value="x")["status"], "error")
            unknown = consolidate(path=d, session="nope", value="x")
            self.assertEqual(unknown["status"], "error")
            self.assertIn("no summarize proposal", unknown["message"])


class TestTheDegradeContractSurvives(unittest.TestCase):
    """Contract 14 — wiring a drafter in must not remove the fallback the seam guarantees."""

    def test_an_undrafted_cluster_keeps_its_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_turns(d, session="sess-alpha")
            store = _store(d)
            for i in range(3):     # a SECOND cluster nobody drafts
                store.backend.put(MemoryItem.create(
                    "sess-beta", f"beta turn {i}", mtype=EPISODIC,
                    created_at=f"2026-02-0{i + 1}T00:00:00+00:00"))
            store.close()
            from unittest import mock
            with mock.patch("builtins.input", return_value="a"):
                _run(["memory", "consolidate", "--draft", "sess-alpha",
                      "--value", "Alpha was drafted.", "--path", d])
            code, out, _ = _run(["memory", "consolidate", "--path", d])
            self.assertEqual(code, 0)
            self.assertIn("summary of 3 episodic turns in 'sess-beta'", out,
                          "the cluster nobody drafted must still show the placeholder")


if __name__ == "__main__":
    unittest.main()
