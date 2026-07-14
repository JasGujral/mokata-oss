"""SI-DEV — scope-change binding: an out-of-spec write is an exit-2, and `spec amend` is a FORCED
phase regression (0.0.13, Jas rider 2026-07-12, from a live incident).

The incident: the agent built batch-update/delete although the saved spec explicitly DEFERRED it,
treating the user's instruction as authorization. SK.S3's protocol said ask-and-amend first — prose,
which did not hold. SI-DEV.0 made the spec real (it had no writer at all); THIS stage binds its
scope.

What is honestly decidable from a PreToolUse hook (the grounding verdict this stage is built on):
the hook has the target path AND the incoming content (`tool_input.content` / `new_string`), plus
persisted state, on a millisecond budget. So it can decide **path-glob membership** and **literal
marker presence in the content being written**. It CANNOT decide "does this diff semantically
implement a deferred feature". The enforceable slice is therefore an explicit, human-approved scope
section on the spec — authorized globs + deferred items carrying their own paths/markers — enforced
strictly and failing OPEN everywhere it cannot decide (no scope section, no authorized list, a spec
that predates the section, an ambiguous run).

The headline is `test_si_dev_regression`: the incident, replayed end to end.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
from _support import mcp_commit

from mokata import gate_hook as G                                  # noqa: E402
from mokata import session, spec_scope as S, tdd_state as T        # noqa: E402
from mokata.brainstorm import Approach, BrainstormSession          # noqa: E402
from mokata.brainstorm_impact import FITS, DesignFitVerdict        # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.session_save import save_session                       # noqa: E402
from mokata.state import StateStore                                # noqa: E402

RUN = "run-sidev"

# --- the spec as it stood when the incident happened -------------------------------------------
# Single-item CRUD is authorized. Batch update/delete is EXPLICITLY DEFERRED — and the deferral now
# carries what a hook can actually check: the paths it would live in, and the tokens it would spell.
TITLE = "items api — single-item CRUD"
DEFERRED = {"id": "D1", "item": "batch update/delete",
            "paths": ["src/api/batch*.py"],
            "markers": ["batch_update", "bulk_delete"]}
SCOPE = {"authorized": ["src/api/items.py", "src/api/serializers.py"],
         "deferred": [DEFERRED]}
ACS = [("AC1", "GET /items/<id> returns one item"),
       ("AC2", "DELETE /items/<id> removes one item")]
TESTS = [("test_get_item", ["AC1"]), ("test_delete_item", ["AC2"])]

# --- what the agent then wrote, unasked ---------------------------------------------------------
# The realistic shape: NOT a new file — the batch endpoint goes into the SAME module the authorized
# single-item endpoints live in. Path-globs alone would wave this through; the marker catches it.
INCIDENT_PATH = "src/api/items.py"
INCIDENT_CODE = (
    "def get_item(item_id):\n"
    "    return store.get(item_id)\n\n"
    "def batch_update(ids, payload):\n"          # <-- the DEFERRED thing
    "    for i in ids:\n"
    "        store.put(i, payload)\n"
)


class _Repo:
    def __init__(self, run_id=RUN):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run_id})
        self._env.start()
        session.reset_for_test()
        self.surface = Surface.load(self.path)
        self.run_id = run_id

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


def _approve_an_approach(repo):
    """The production save path — a real approved brainstorm on disk."""
    s = BrainstormSession("items api")
    s.propose_approaches([
        Approach(name="rest", summary="plain REST", pros=["simple"], cons=["chatty"]),
        Approach(name="rpc", summary="rpc", pros=["compact"], cons=["opaque"]),
    ])
    s.assess_impacts(layer=None, memory_items=[])
    for a in s.approaches:
        s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
    s.approve("jas", "rest")
    save_session(repo.surface, brainstorm=s.to_dict())


def _emit_args(path, *, acs=ACS, tests=TESTS, scope=SCOPE):
    return dict(path=path, title=TITLE, approach="rest",
                criteria=[{"id": i, "text": t} for i, t in acs],
                tests=[{"name": n, "ac_ids": list(a)} for n, a in tests],
                scope=scope)


def _emitted(repo):
    """The spec exactly as it sits on disk for the gated run."""
    return repo.raw.read(G.SPEC_PREFIX + RUN)


# The pipeline phases a real run has already passed by the time it reaches `emit` — checkpointed
# through the production path (`save_session(passed=…)`, which the brainstorm skill drives).
PRE_EMIT_PHASES = ["brainstorm", "analysis", "strawman", "pre_mortem", "probes"]


def _setup_run(repo, **kw):
    """Approach approved + the pre-emit gates checkpointed + spec v1 emitted (through the real gated
    surface) + RED on record for the authorized work — i.e. a run in the middle of `develop`,
    exactly as the incident was."""
    _approve_an_approach(repo)
    save_session(repo.surface, passed=PRE_EMIT_PHASES)      # the real checkpoint path
    res = mcp_commit(TW.spec_emit, **_emit_args(repo.path, **kw))
    assert res["committed"], res
    T.record(repo.surface.state, RUN, red=["test_get_item", "test_delete_item"])
    return res


# ======================================================================================
# THE HEADLINE — the incident, replayed end to end
# ======================================================================================

class TestTheIncident(unittest.TestCase):

    def test_si_dev_regression(self):
        """spec vN defers batch update/delete → the agent writes it anyway → EXIT 2 naming the
        deferred item and `spec amend` → the amend is a FORCED PHASE REGRESSION (writes stay
        blocked mid-amend) → the real gates run → vN+1 persisted, vN superseded, diff ledgered →
        RED owed for the new AC (still blocked) → RED recorded → the same write PASSES."""
        with _Repo() as repo:
            _setup_run(repo)

            # --- 1. the authorized work is fine ------------------------------------------------
            ok = G.check_write(repo.path, "src/api/items.py", content="def get_item(i): pass")
            self.assertTrue(ok.allowed, ok.reason)

            # --- 2. THE INCIDENT: the deferred thing, written into an AUTHORIZED file ----------
            out = G.check_write(repo.path, INCIDENT_PATH, content=INCIDENT_CODE)
            self.assertFalse(out.allowed, "the deferred item was built — this MUST block")
            self.assertEqual(out.gate, G.GATE_SCOPE)
            self.assertEqual(out.exit_code, 2)
            self.assertIn("batch update/delete", out.reason, "the block must NAME the deferred item")
            self.assertIn("spec amend", out.reason, "and the ONE road back")

            # --- 3. the amend: act one — propose. The run REGRESSES to SPEC. -------------------
            new_acs = list(ACS) + [("AC3", "POST /items/batch updates many items")]
            new_tests = list(TESTS) + [("test_batch_update", ["AC3"])]
            new_scope = {"authorized": ["src/api/items.py", "src/api/serializers.py"],
                         "deferred": []}                       # D1 is no longer deferred
            args = dict(_emit_args(repo.path, acs=new_acs, tests=new_tests, scope=new_scope),
                        reason="the user asked for batch update; it was deferred in v1",
                        item="D1")

            proposed = TW.spec_amend(**args)
            self.assertEqual(proposed["status"], "proposed")
            self.assertFalse(proposed["committed"])

            # ...and while the amendment is open, DEVELOPMENT WRITES STAY BLOCKED. Not just the
            # out-of-scope one — the run has regressed out of develop entirely.
            mid = G.check_write(repo.path, "src/api/items.py", content="def get_item(i): pass")
            self.assertFalse(mid.allowed, "a mid-amend write must block — the phase regressed")
            self.assertEqual(mid.gate, G.GATE_SCOPE)
            self.assertIn("amend", mid.reason)

            # P17: the checkpoint rolled back past the gates that must re-run — not to scratch.
            from mokata.govern.resume import PipelineCheckpoint
            cp = PipelineCheckpoint(repo.surface.state, RUN)
            self.assertNotIn("emit", cp.passed)
            self.assertNotIn("completeness_gate", cp.passed)
            self.assertIn("probes", cp.passed, "the EARLIER passed gates must survive (P17)")
            self.assertEqual(cp.resume_phase(), "completeness_gate")

            # --- 4. act two: a HUMAN mints the approval out-of-band, the model redeems it ------
            from mokata import approval
            approval.approve(repo.path, proposed["proposal_id"], actor="jas",
                             ledger=repo.ledger())
            done = TW.spec_amend(**dict(args, proposal_id=proposed["proposal_id"]))
            self.assertTrue(done["committed"], done)
            self.assertEqual(done["version"], 2)

            # vN+1 persisted; vN SUPERSEDED, not deleted.
            self.assertEqual(_emitted(repo)["version"], 2)
            archived = repo.raw.read(S.ARCHIVE_PREFIX + RUN + "__v1")
            self.assertIsNotNone(archived, "v1 must be superseded, never deleted")
            self.assertEqual(archived["version"], 1)
            self.assertEqual(len(archived["criteria"]), 2)

            # --- 5. RED is OWED for the new AC — the write is STILL blocked -------------------
            still = G.check_write(repo.path, INCIDENT_PATH, content=INCIDENT_CODE)
            self.assertFalse(still.allowed, "the amended spec owes a failing test for AC3")
            self.assertEqual(still.gate, G.GATE_TDD)
            self.assertIn("test_batch_update", still.reason)

            # --- 6. RED recorded → develop resumes → THE SAME WRITE PASSES --------------------
            T.record(repo.surface.state, RUN, red=["test_batch_update"])
            cp = PipelineCheckpoint(repo.surface.state, RUN)
            self.assertTrue(cp.is_complete(), "develop resumes: the re-run gates passed again")

            final = G.check_write(repo.path, INCIDENT_PATH, content=INCIDENT_CODE)
            self.assertTrue(final.allowed,
                            f"after a properly gated amend the write must proceed: {final.reason}")


# ======================================================================================
# scope honesty — what it decides, and everywhere it refuses to
# ======================================================================================

class TestScopeHonesty(unittest.TestCase):

    def test_an_authorized_path_is_allowed(self):
        with _Repo() as repo:
            _setup_run(repo)
            out = G.check_write(repo.path, "src/api/serializers.py", content="def dump(x): pass")
            self.assertTrue(out.allowed)

    def test_a_path_outside_a_non_empty_authorized_list_blocks(self):
        with _Repo() as repo:
            _setup_run(repo)
            out = G.check_write(repo.path, "src/billing/charge.py", content="def charge(): pass")
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_SCOPE)
            self.assertIn("outside", out.reason)

    def test_a_deferred_PATH_blocks_and_names_the_item(self):
        with _Repo() as repo:
            _setup_run(repo)
            out = G.check_write(repo.path, "src/api/batch_ops.py", content="x = 1")
            self.assertFalse(out.allowed)
            self.assertIn("batch update/delete", out.reason)

    def test_an_empty_authorized_list_fails_OPEN(self):
        """mokata has no map to judge the path against — the honest answer is 'I cannot tell'."""
        with _Repo() as repo:
            _setup_run(repo, scope={"authorized": [], "deferred": [DEFERRED]})
            out = G.check_write(repo.path, "src/anywhere/at/all.py", content="def f(): pass")
            self.assertTrue(out.allowed, "an unmapped path must fail open")
            # ...but the explicit NEGATIVE still binds.
            blocked = G.check_write(repo.path, "src/anywhere/at/all.py",
                                    content="def bulk_delete(ids): pass")
            self.assertFalse(blocked.allowed)
            self.assertIn("batch update/delete", blocked.reason)

    def test_a_spec_with_NO_scope_section_fails_OPEN(self):
        """A pre-SI-DEV spec. It never carried scope; mokata will not invent one."""
        with _Repo() as repo:
            _setup_run(repo, scope=None)
            self.assertNotIn("scope", _emitted(repo))
            out = G.check_write(repo.path, "src/billing/charge.py",
                                content="def batch_update(x): pass")
            self.assertTrue(out.allowed, "a spec that predates the scope section must fail open")

    def test_a_corrupt_scope_section_fails_OPEN(self):
        with _Repo() as repo:
            _setup_run(repo)
            data = _emitted(repo)
            data["scope"] = "not a scope at all"
            repo.raw.write(G.SPEC_PREFIX + RUN, data)
            out = G.check_write(repo.path, "src/billing/charge.py", content="x=1")
            self.assertTrue(out.allowed, "unreadable scope must never block")

    def test_a_test_file_is_always_writable_even_out_of_scope(self):
        """RED must always be reachable — including the RED an amend owes."""
        with _Repo() as repo:
            _setup_run(repo)
            out = G.check_write(repo.path, "tests/test_batch.py",
                                content="def test_batch_update(): ...")
            self.assertTrue(out.allowed)

    def test_an_ambiguous_run_fails_OPEN(self):
        """Two runs and NO pin: the hook will not pick a window, so it enforces nothing — including
        the scope gate. Wrong-window blocking stays structurally impossible."""
        with _Repo() as repo:
            _setup_run(repo)
            repo.raw.write(G.APPROACH_PREFIX + "a-second-run", {"approach": "other"})

            blocked = G.check_write(repo.path, "src/api/batch_ops.py", content="x=1")
            self.assertFalse(blocked.allowed, "pinned: the gate still enforces")

            os.environ.pop(session.SESSION_ID_ENV, None)     # the pin goes away -> ambiguous
            session.reset_for_test()
            out = G.check_write(repo.path, "src/api/batch_ops.py", content="x=1")
            self.assertTrue(out.allowed, "two runs, none pinned — never guess")
            self.assertIsNotNone(out.notice, "and say so, once")

    def test_a_non_mokata_repo_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(G.find_mokata_root(d))

    def test_no_scope_check_without_content(self):
        """A caller that hands no content (an older harness) still gets the path checks, and the
        marker check simply has nothing to look at — it must not throw or guess."""
        with _Repo() as repo:
            _setup_run(repo)
            self.assertTrue(G.check_write(repo.path, "src/api/items.py").allowed)
            self.assertFalse(G.check_write(repo.path, "src/api/batch_ops.py").allowed)


# ======================================================================================
# the hook's I/O — the content the envelope carries (hook_cli used to discard it)
# ======================================================================================

class TestTheHookReadsTheContent(unittest.TestCase):

    def test_content_is_extracted_from_every_native_tool_shape(self):
        from mokata.gate_hook import target_content
        self.assertIn("batch_update", target_content({"content": "def batch_update(): ..."}))
        self.assertIn("batch_update", target_content({"new_string": "def batch_update(): ..."}))
        self.assertIn("batch_update", target_content(
            {"edits": [{"new_string": "x = 1"}, {"new_string": "def batch_update(): ..."}]}))
        self.assertEqual(target_content({}), "")
        self.assertEqual(target_content(None), "")

    def test_the_hook_blocks_the_incident_end_to_end(self):
        """Through `gate_guard_main` — the real stdin envelope, the real exit code."""
        import io
        import json
        from mokata.hook_cli import gate_guard_main

        with _Repo() as repo:
            _setup_run(repo)
            envelope = json.dumps({
                "session_id": "cc-1", "cwd": repo.path, "tool_name": "Write",
                "tool_input": {"file_path": INCIDENT_PATH, "content": INCIDENT_CODE},
            })
            err = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO(envelope)), \
                 mock.patch("sys.stderr", err):
                code = gate_guard_main([])
            self.assertEqual(code, 2)
            self.assertIn("batch update/delete", err.getvalue())
            self.assertIn("spec amend", err.getvalue())


# ======================================================================================
# the P14 override — SI-DEV's block lifts with the EXISTING ceremony
# ======================================================================================

class TestTheOverride(unittest.TestCase):

    def test_the_scope_gate_is_overridable_with_the_existing_discipline(self):
        with _Repo() as repo:
            _setup_run(repo)
            self.assertFalse(G.check_write(repo.path, INCIDENT_PATH,
                                           content=INCIDENT_CODE).allowed)

            self.assertIn(G.GATE_SCOPE, G.GATES, "the gate must be nameable to `gate override`")
            repo.raw.write(G.OVERRIDE_PREFIX + RUN,
                           {"run_id": RUN, "scopes": [G.GATE_SCOPE], "actor": "jas",
                            "reason": "shipping a hotfix", "at": "2026-07-14T00:00:00Z"})

            out = G.check_write(repo.path, INCIDENT_PATH, content=INCIDENT_CODE)
            self.assertTrue(out.allowed)
            self.assertTrue(out.overridden)

    def test_the_override_expires_with_the_run(self):
        with _Repo() as repo:
            _setup_run(repo)
            repo.raw.write(G.OVERRIDE_PREFIX + "another-run",
                           {"run_id": "another-run", "scopes": [G.GATE_SCOPE]})
            out = G.check_write(repo.path, INCIDENT_PATH, content=INCIDENT_CODE)
            self.assertFalse(out.allowed, "another run's override must not lift THIS run's gate")


# ======================================================================================
# the amend: the REAL gates, re-invoked
# ======================================================================================

class TestTheAmendGates(unittest.TestCase):

    def _amend_args(self, repo, **kw):
        new_acs = list(ACS) + [("AC3", "POST /items/batch updates many items")]
        new_tests = list(TESTS) + [("test_batch_update", ["AC3"])]
        base = dict(_emit_args(repo.path, acs=new_acs, tests=new_tests,
                               scope={"authorized": ["src/api/items.py"], "deferred": []}),
                    reason="batch update was asked for", item="D1")
        base.update(kw)
        return base

    def test_completeness_refuses_an_unmapped_new_criterion(self):
        with _Repo() as repo:
            _setup_run(repo)
            args = self._amend_args(repo)
            args["tests"] = [{"name": "test_get_item", "ac_ids": ["AC1"]},
                             {"name": "test_delete_item", "ac_ids": ["AC2"]}]   # AC3 unmapped
            res = mcp_commit(TW.spec_amend, **args)
            self.assertFalse(res["committed"])
            self.assertEqual(res["gate"], "completeness")
            self.assertIn("AC3", res["unmapped"])
            self.assertEqual(_emitted(repo)["version"], 1, "a refused amend writes nothing")

    def test_the_model_cannot_mint_its_own_amend_approval(self):
        with _Repo() as repo:
            _setup_run(repo)
            res = TW.spec_amend(**self._amend_args(repo, approve=True))
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(res["committed"])
            self.assertEqual(_emitted(repo)["version"], 1,
                             "approve=true is not consent — it never was")

    def test_widening_the_scope_re_runs_the_impact_lens(self):
        """Lens-1 (blast radius) re-runs when the amendment WIDENS what the spec may touch."""
        with _Repo() as repo:
            _setup_run(repo)
            args = self._amend_args(repo)
            args["scope"] = {"authorized": ["src/api/items.py", "src/billing/charge.py"],
                             "deferred": []}                       # a NEW authorized surface
            res = mcp_commit(TW.spec_amend, **args)
            self.assertTrue(res["committed"], res)
            self.assertTrue(res.get("scope_widened"))
            self.assertIsNotNone(res.get("impact"),
                                 "a widened blast radius must be re-computed, not assumed")

    def test_a_narrowing_amend_does_not_re_run_the_lens(self):
        """Narrowing cannot widen the blast radius, so the lens is not re-run — a cost with no
        answer in it. NOTE what counts as widening: releasing a DEFERRED item does, even though the
        authorized list shrank. That is the whole point of the deferred list."""
        with _Repo() as repo:
            _setup_run(repo)
            args = self._amend_args(repo)
            args["scope"] = {"authorized": ["src/api/items.py"],   # dropped serializers.py
                             "deferred": [DEFERRED]}               # D1 stays deferred
            res = mcp_commit(TW.spec_amend, **args)
            self.assertTrue(res["committed"], res)
            self.assertFalse(res.get("scope_widened"))
            self.assertIsNone(res.get("impact"))

    def test_releasing_a_deferred_item_counts_as_widening(self):
        with _Repo() as repo:
            _setup_run(repo)
            args = self._amend_args(repo)   # authorized shrinks, but D1 is RELEASED
            res = mcp_commit(TW.spec_amend, **args)
            self.assertTrue(res["committed"], res)
            self.assertTrue(res["scope_widened"],
                            "un-deferring is widening — the build may now touch what it could not")

    def test_the_amend_leaves_the_ledger_chain_intact(self):
        with _Repo() as repo:
            _setup_run(repo)
            mcp_commit(TW.spec_amend, **self._amend_args(repo))

            entries = repo.ledger().entries()
            self.assertTrue(repo.ledger().verify().intact,
                            "the hash chain must survive the amend")

            kinds = [e.get("kind") for e in entries]
            self.assertIn("deviation", kinds, "a scope change is a plan deviation — surfaced")
            self.assertIn("spec_amend", kinds, "the diff must be on the chain")
            self.assertIn("write_approval", kinds, "linked to the human approval that licensed it")

            amend = [e for e in entries if e.get("kind") == "spec_amend"][-1]
            self.assertEqual(amend["from_version"], 1)
            self.assertEqual(amend["to_version"], 2)
            self.assertIn("AC3", amend["added_criteria"])
            self.assertIn("D1", amend["undeferred"])

    def test_an_amend_with_no_open_spec_is_refused(self):
        with _Repo() as repo:
            _approve_an_approach(repo)          # no spec emitted at all
            res = TW.spec_amend(**self._amend_args(repo))
            self.assertFalse(res["committed"])
            self.assertIn("no spec", res["reason"].lower())


# ======================================================================================
# no behaviour change where there is no scope to enforce
# ======================================================================================

class TestNoBehaviourChange(unittest.TestCase):

    def test_the_si1_gates_keep_their_exact_semantics(self):
        with _Repo() as repo:
            _approve_an_approach(repo)
            out = G.check_write(repo.path, "src/api/items.py", content="x=1")
            self.assertEqual(out.gate, G.GATE_SPEC, "approach, no spec -> spec-persisted, as before")

            mcp_commit(TW.spec_emit, **_emit_args(repo.path))
            out = G.check_write(repo.path, "src/api/items.py", content="x=1")
            self.assertEqual(out.gate, G.GATE_TDD, "spec, no RED -> the TDD gate, as before")

    def test_a_run_with_no_spec_at_all_is_not_policed(self):
        with _Repo() as repo:
            out = G.check_write(repo.path, "src/api/batch_ops.py", content="def bulk_delete(): ...")
            self.assertTrue(out.allowed, "no run state — not our business")

    def test_spec_emit_output_is_unchanged_but_for_the_additive_section(self):
        with _Repo() as repo:
            _approve_an_approach(repo)
            mcp_commit(TW.spec_emit, **_emit_args(repo.path, scope=None))
            data = _emitted(repo)
            self.assertEqual(set(data) - {"version"},
                             {"title", "criteria", "approach", "domains"},
                             "a scope-less emit must persist exactly what it always did")


if __name__ == "__main__":
    unittest.main()
