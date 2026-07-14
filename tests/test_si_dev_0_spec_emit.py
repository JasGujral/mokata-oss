"""SI-DEV.0 — the REAL spec-emit surface (0.0.13 seatbelt cluster).

The grounding that produced this stage
--------------------------------------
`emitted_spec` had NO writer reachable from any user-facing surface. Its sole writer was
`engine/phases.py:_emit`, reachable only through `run_pipeline()`, which had ZERO callers in
`src/` (tests only). There was no `mokata spec` CLI command (only the read-only `spec-check`) and
no spec-emit MCP tool. Separately, `spec_corpus` — the corpus the SK.S3 regression guard reads —
was read by THREE surfaces (`ci_check`, the `spec-check` CLI, the `spec_check` MCP tool) and
written by NONE, so `load_spec_corpus` always returned [] and `spec-check` always answered "no
saved specs yet, skipped". That is the mechanical explanation of the scope-creep incident: the
protocol routed the agent to a check that could not fire.

THE BRICK (a live P0, fixed here)
---------------------------------
`approved_approach__<rid>` IS written in production — the MCP `session_save` tool routes through
`SessionFlow` -> `save_session` -> `persist_approach` whenever the brainstorm session carries an
approval — and the `gate-guard` PreToolUse hook IS wired (`hooks/hooks.json`). So after a real
approach approval, the run-state hook saw approach-present + spec-absent and blocked EVERY
implementation write, permanently, telling the user to "Emit the spec first (/mokata:spec)" — a
message that NO surface could satisfy. The gate was not a seatbelt; it was a brick.

Blocking without a spec is CORRECT (that is the `spec-persisted` gate doing its job). The bug was
that the block was UNSATISFIABLE. Both halves are pinned below: no spec means blocked, and — the
cure — emit through the real surface means the same write proceeds.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit

from mokata import gate_hook as G                                  # noqa: E402
from mokata import session, tdd_state as T                         # noqa: E402
from mokata.brainstorm import Approach, BrainstormSession          # noqa: E402
from mokata.brainstorm_impact import FITS, DesignFitVerdict        # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.engine import AcceptanceCriterion, Spec, TestRef       # noqa: E402
from mokata.engine.spec_awareness import (SPEC_CORPUS_KEY,         # noqa: E402
                                          load_spec_corpus)
from mokata.engine.spec_gate import check_spec_persisted           # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.mcp.registry import TOOLS                              # noqa: E402
from mokata.session_save import save_session                       # noqa: E402

RUN = "run-sidev0"

TITLE = "slugify keeps unicode intact"
ACS = [("AC1", "slugify of a unicode string returns a url-safe slug"),
       ("AC2", "slugify of an empty string raises ValueError")]
TESTS = [("test_slugify_unicode", ["AC1"]), ("test_slugify_empty", ["AC2"])]


# ======================================================================================
# harness — a repo on a pinned run, and a REAL approved brainstorm session
# ======================================================================================

class _Repo:
    """An initialized mokata repo on a pinned run id (the field's `MOKATA_SESSION_ID`)."""

    def __init__(self, run_id=RUN):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self.run_id = run_id
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


def _approved_session():
    """A brainstorm session that has genuinely passed the P21 hard-gate: both decision lenses on
    the table, then an explicit approval. Built the way `playbook.py` builds it — no shortcut, so
    what lands on disk is what a real run lands."""
    s = BrainstormSession("slugify")
    s.propose_approaches([
        Approach(name="normalize-then-slug", summary="NFKD then strip",
                 pros=["keeps unicode intent"], cons=["slower on long input"]),
        Approach(name="transliterate", summary="map to ascii",
                 pros=["ascii-only output"], cons=["lossy for CJK"]),
    ])
    s.assess_impacts(layer=None, memory_items=[])
    for a in s.approaches:
        s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
    s.approve("jas", "normalize-then-slug")
    return s


def _persist_approach_for_real(repo):
    """Persist the approved approach through the PRODUCTION seam — `save_session`, which is what
    the MCP `session_save` tool calls via `SessionFlow`. Not a hand-seeded state file: the point of
    the brick test is that this is what a real session actually leaves on disk."""
    return save_session(repo.surface, brainstorm=_approved_session().to_dict())


def _spec():
    return Spec(TITLE, [AcceptanceCriterion(i, t) for i, t in ACS],
                approach="normalize-then-slug")


def _tests():
    return [TestRef(name, list(acs)) for name, acs in TESTS]


def _args(path):
    return dict(
        path=path,
        title=TITLE,
        criteria=[{"id": i, "text": t} for i, t in ACS],
        tests=[{"name": n, "ac_ids": list(a)} for n, a in TESTS],
        approach="normalize-then-slug",
    )


# ======================================================================================
# 1 — THE BRICK, and its cure (the headline)
# ======================================================================================

class TestTheRunStateBrick(unittest.TestCase):
    """The live P0 this stage fixes, shaped like a real session."""

    def test_a_real_session_with_no_spec_blocks_implementation(self):
        """Half one — CORRECT, and pinned so the cure cannot regress it. An approved approach with
        no emitted spec must block implementation: that is `spec-persisted` doing its job."""
        with _Repo() as repo:
            saved = _persist_approach_for_real(repo)
            self.assertIn("approved_approach", saved.wrote,
                          "the production save path must persist the approved approach — if it "
                          "does not, this test is no longer shaped like a real session")

            run = G.resolve_run(repo.path)
            self.assertEqual(run.run_id, RUN, "the run must resolve (pinned); ambiguity fails open")

            out = G.check_write(repo.path, "src/slugify.py")
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_SPEC)
            self.assertIn("/mokata:spec", out.reason)

    def test_the_block_is_now_satisfiable(self):
        """THE CURE. Before this stage the message above named `/mokata:spec`, and no surface on
        earth could satisfy it — `emitted_spec` had no reachable writer, so the gate blocked
        forever. Now the emit surface exists: the same write, after emitting, is no longer
        spec-blocked, and once a failing test is on record it proceeds outright."""
        with _Repo() as repo:
            _persist_approach_for_real(repo)
            self.assertFalse(G.check_write(repo.path, "src/slugify.py").allowed)

            # /mokata:spec — through the REAL surface (the MCP tool, full human round-trip).
            res = mcp_commit(TW.spec_emit, **_args(repo.path))
            self.assertTrue(res["committed"], f"emit did not commit: {res}")

            # The spec gate is satisfied — the hook now hands off to the TDD gate, which is the
            # correct next obligation (RED before implementation), not the spec brick.
            out = G.check_write(repo.path, "src/slugify.py")
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_TDD,
                             "with a spec on disk the block must move to the TDD gate — if it is "
                             "still the spec gate, the emit did not land where the hook reads it")

            # RED on record (the real persisted-TDD path) -> the write proceeds.
            T.record(repo.surface.state, RUN, red=["test_slugify_unicode"])
            out = G.check_write(repo.path, "src/slugify.py")
            self.assertTrue(out.allowed, f"still blocked after spec + RED: {out.reason}")

    def test_the_spec_lands_where_the_hook_reads_it(self):
        """`has_spec` flips: the physical key the hook scans for is the one emit writes."""
        with _Repo() as repo:
            from mokata.state import StateStore
            raw = StateStore(T.state_dir(repo.path))
            self.assertFalse(raw.exists(G.SPEC_PREFIX + RUN))

            mcp_commit(TW.spec_emit, **_args(repo.path))

            self.assertTrue(raw.exists(G.SPEC_PREFIX + RUN),
                            "emit must write the session-scoped `emitted_spec__<run>` the hook "
                            "resolves — a repo-global singleton would not be seen")


# ======================================================================================
# 2 — emit runs through the REAL gates (re-invoked, not reimplemented)
# ======================================================================================

class TestEmitThroughTheRealGates(unittest.TestCase):

    def test_completeness_still_refuses_an_unmapped_criterion(self):
        """The completeness gate is the SAME `run_completeness_gate` the engine already ran. An AC
        with no test must refuse the emit — and write NOTHING."""
        with _Repo() as repo:
            args = _args(repo.path)
            args["tests"] = [{"name": "test_slugify_unicode", "ac_ids": ["AC1"]}]   # AC2 unmapped

            res = mcp_commit(TW.spec_emit, **args)

            self.assertFalse(res["committed"])
            self.assertEqual(res.get("gate"), "completeness")
            self.assertIn("AC2", res.get("unmapped", []))
            self.assertFalse(check_spec_persisted(repo.surface.state).passed,
                             "a refused emit must persist no spec at all")

    def test_a_refused_emit_never_even_proposes(self):
        """A completeness failure is a correctness refusal, not a missing approval: there must be
        no proposal for a human to approve (approving it could never make it correct)."""
        with _Repo() as repo:
            args = _args(repo.path)
            args["tests"] = []
            res = TW.spec_emit(**args)
            self.assertNotIn("proposal_id", res)
            self.assertFalse(res["committed"])

    def test_the_model_cannot_mint_its_own_consent(self):
        """The persisted-approval rule: `approve=True` is a parameter the MODEL types. It must not
        commit."""
        with _Repo() as repo:
            res = TW.spec_emit(**dict(_args(repo.path), approve=True))
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(res["committed"])
            self.assertFalse(check_spec_persisted(repo.surface.state).passed,
                             "a model-typed approve=true must never land a spec")

    def test_the_human_approval_uses_the_real_consent_path(self):
        """And the honest round-trip — propose, a human mints the approval out-of-band, the model
        redeems it by id — DOES commit, exactly once."""
        with _Repo() as repo:
            proposed = TW.spec_emit(**_args(repo.path))
            self.assertEqual(proposed["status"], "proposed")
            pid = proposed["proposal_id"]

            from mokata import approval
            from mokata.govern.ledger import AuditLedger
            ledger = AuditLedger.from_mokata_dir(os.path.join(repo.path, ".mokata"))
            approval.approve(repo.path, pid, actor="jas", ledger=ledger)   # the human's own process

            res = TW.spec_emit(**dict(_args(repo.path), proposal_id=pid))
            self.assertTrue(res["committed"])

            again = TW.spec_emit(**dict(_args(repo.path), proposal_id=pid))
            self.assertFalse(again["committed"], "an approval licenses exactly one write")

    def test_the_emit_is_ledgered(self):
        with _Repo() as repo:
            mcp_commit(TW.spec_emit, **_args(repo.path))
            from mokata.govern.ledger import AuditLedger
            entries = AuditLedger.from_mokata_dir(os.path.join(repo.path, ".mokata")).entries()
            gates = [e for e in entries if e.get("kind") == "write_gate"]
            self.assertTrue(any(e.get("decision") == "approved" for e in gates),
                            "the emit must leave a write_gate decision on the hash-chained ledger")
            self.assertTrue(any(e.get("kind") == "write_approval" for e in entries),
                            "and the redemption record linking the human's approval to the write")

    def test_both_surfaces_share_one_committer(self):
        """Re-invoke, don't reimplement: both surfaces route through `engine.emit.emit_spec`."""
        from mokata.engine import emit as E
        with _Repo() as repo:
            out = E.emit_spec(repo.surface, _spec(), _tests(), assume_yes=True)
            self.assertTrue(out.committed)
            self.assertTrue(check_spec_persisted(repo.surface.state).passed)

    def test_the_engine_pipeline_still_emits_through_the_same_path(self):
        """`run_pipeline`'s emit phase keeps working — it now shares the one committer, so a spec
        emitted by the engine ALSO lands in the corpus."""
        from mokata.engine import run_pipeline
        with _Repo() as repo:
            handoff = _approved_session().handoff()
            run = run_pipeline(handoff, _spec(), _tests(), store=repo.surface.state)
            self.assertTrue(run.ok, run.render())
            self.assertTrue(check_spec_persisted(repo.surface.state).passed)
            self.assertEqual(len(repo.surface.state.read(SPEC_CORPUS_KEY) or []), 1)


# ======================================================================================
# 2b — the CLI emits onto the run being GATED, not onto its own shell session
# ======================================================================================

class TestTheCliEmitsOntoTheGatedRun(unittest.TestCase):
    """Caught by driving the real CLI, not by a test: `mokata spec emit` runs in the HUMAN's shell,
    a separate process from the agent's session. Its own `current_run_id()` is a fresh uuid4, so a
    naive emit writes `emitted_spec__<this shell>` — a run the gate is not looking at — AND leaves a
    second run's state in the repo, which makes the hook's resolution ambiguous and silently turns
    every gate OFF. Emitting would unblock nothing and disable everything. The CLI therefore
    resolves the run with the hook's OWN resolver, exactly as `mokata gate override` does."""

    def test_the_cli_writes_onto_the_run_the_hook_enforces(self):
        from mokata.cli_commands.spec import _run_scoped_store
        from mokata.state import StateStore

        with _Repo() as repo:
            _persist_approach_for_real(repo)

            # The human's shell: NO pin, so this process mints a run id of its own — which is
            # exactly the id a naive `surface.state` would have written the spec under.
            os.environ.pop(session.SESSION_ID_ENV, None)
            session.reset_for_test()
            shell_run = session.current_run_id()
            self.assertNotEqual(shell_run, RUN, "the shell is a different run than the session")

            store, run_id, err = _run_scoped_store(repo.surface)
            self.assertIsNone(err)
            self.assertEqual(run_id, RUN,
                             "the CLI must resolve the run that HAS pipeline state, not its own")

            from mokata.engine.emit import emit_spec
            out = emit_spec(repo.surface, _spec(), _tests(), store=store, assume_yes=True)
            self.assertTrue(out.committed)

            raw = StateStore(T.state_dir(repo.path))
            self.assertTrue(raw.exists(G.SPEC_PREFIX + RUN),
                            "the spec must land on the gated run")
            self.assertFalse(raw.exists(G.SPEC_PREFIX + shell_run),
                             "and NOT on the shell's own session — that would leave the real run "
                             "blocked and make the hook ambiguous")

    def test_the_cli_refuses_to_guess_between_two_runs(self):
        """Ambiguity is refused, never guessed — emitting onto the wrong run would leave the right
        one blocked forever."""
        from mokata.cli_commands.spec import _run_scoped_store
        from mokata.state import StateStore

        with _Repo() as repo:
            _persist_approach_for_real(repo)
            StateStore(T.state_dir(repo.path)).write(
                G.APPROACH_PREFIX + "a-second-run", {"approach": "other"})

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(session.SESSION_ID_ENV, None)
                session.reset_for_test()
                _store, _run, err = _run_scoped_store(repo.surface)
            session.reset_for_test()

            self.assertIsNotNone(err, "two runs and no pin must REFUSE, not pick one")
            self.assertIn("will not guess", err)


# ======================================================================================
# 3 — the corpus: the reader existed, this stage wires the writer (the SS.S1 pattern)
# ======================================================================================

class TestTheCorpusIsWired(unittest.TestCase):

    def test_the_corpus_was_empty_before_any_emit(self):
        with _Repo() as repo:
            self.assertEqual(load_spec_corpus(repo.surface.state), [],
                             "baseline: nothing has ever written the corpus")

    def test_emit_populates_the_corpus_in_the_same_gated_commit(self):
        with _Repo() as repo:
            mcp_commit(TW.spec_emit, **_args(repo.path))

            raw = repo.surface.state.read(SPEC_CORPUS_KEY)
            self.assertIsInstance(raw, list)
            self.assertEqual(len(raw), 1, "the emit must append the spec to the shared corpus")
            self.assertIn(TITLE, [s.title for s in load_spec_corpus(repo.surface.state)])

    def test_spec_check_now_returns_a_real_verdict_on_an_overlap(self):
        """The incident, in miniature. `spec-check` used to answer "no saved specs yet, skipped"
        for every change, because the corpus was structurally empty. With a spec on record, a
        change that touches the spec's surface is now REPORTED."""
        from mokata.engine.spec_awareness import ChangeSet, check_change

        with _Repo() as repo:
            change = ChangeSet(symbols=["slugify"], files=["src/slugify.py"])

            before = check_change(change, load_spec_corpus(repo.surface.state), [])
            self.assertFalse(before.checked, "baseline: the guard could not run at all")

            mcp_commit(TW.spec_emit, **_args(repo.path))

            after = check_change(change, load_spec_corpus(repo.surface.state), [])
            self.assertTrue(after.checked, "the guard must actually run now")
            self.assertTrue(after.has_conflicts,
                            "a change to slugify overlaps the saved spec — spec-check must say so "
                            "instead of shrugging")

    def test_a_re_emit_supersedes_rather_than_duplicating(self):
        with _Repo() as repo:
            mcp_commit(TW.spec_emit, **_args(repo.path))
            args = _args(repo.path)
            args["criteria"] = [{"id": "AC1", "text": "slugify keeps unicode and also trims"},
                                {"id": "AC2", "text": "slugify of an empty string raises"}]
            mcp_commit(TW.spec_emit, **args)

            raw = repo.surface.state.read(SPEC_CORPUS_KEY)
            self.assertEqual(len(raw), 1, "the same spec re-emitted must not duplicate the corpus")
            self.assertIn("trims", json.dumps(raw), "the corpus must carry the LATEST text")


# ======================================================================================
# 4 — the surface is real: registry + parity
# ======================================================================================

class TestTheSurfaceIsRegistered(unittest.TestCase):

    def test_spec_emit_is_a_registered_write_tool(self):
        spec = next((t for t in TOOLS if t.name == "spec_emit"), None)
        self.assertIsNotNone(spec, "spec_emit must be in the one tool registry")
        self.assertEqual(spec.kind, "write", "it is a durable write — it must be gated as one")

    def test_the_cli_exposes_the_spec_command(self):
        from mokata.parity import cli_command_names
        self.assertIn("spec", cli_command_names(),
                      "`mokata spec` must exist — the CLI half of the surface")

    def test_the_command_has_a_declared_in_harness_surface(self):
        from mokata.parity import SURFACE_MATRIX, verify_parity
        s = SURFACE_MATRIX.get("spec")
        self.assertIsNotNone(s, "a new command may never be a silent parity gap")
        self.assertTrue(s.covered)
        self.assertIn("spec_emit", s.mcp_write)
        self.assertTrue(verify_parity().ok, "the parity matrix must stay clean")


if __name__ == "__main__":
    unittest.main()
