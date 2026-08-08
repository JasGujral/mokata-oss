"""REVIEW-FIX.R3 — ONE review-truth source, reachable from BOTH surfaces (0.0.16).

R1 made the review verdict run-keyed and fail-closed; R2 fixed the window it is read through.
This stage fixes WHO ELSE claims to answer the same question, and WHERE the answer can be reached
from.

Three defects, one theme — a gate is only as good as the number of places that can answer for it:

  1. an ORPHANED look-alike. `engine/ship.py:check_ship_readiness` was a second, divergent answer
     to "has review passed?" — a `review_passed` BOOLEAN the caller supplied, trusted on its say-so
     (pre-6r trust-the-caller shape), with ZERO production callers: only the `engine/__init__`
     re-export and its own tests. It could satisfy neither R1's run-keying nor R2's freshness
     bound, because a boolean carries neither. Its renderer even emitted the literal "criteriona"
     for any AC count != 1. Deleted, not repointed: the value was zero and the risk was an agent
     reaching for a gate that trusts whatever it is told.

  2. a CLI-ONLY loop. 56 registered MCP tools and NOT ONE for the 6r loop — so the harness had to
     shell out for BOTH halves of the one piece of evidence that decides whether a change may
     ship. `review_status` (read) + `review_record` (record) close it, through the SAME seam the
     CLI uses: same resolution, same event on disk, same gate verdicts. P22 — bare Claude Code has
     no review record at all; mokata has exactly ONE, and now it is reachable from both surfaces.

  3. a BLIND parity matrix. `record-review`/`review-status` are nested sub-actions of `progress`
     (deliberately — the top-level command set stays one `progress`), and Stage-54e checks that a
     COMMAND has a surface. `progress` already had two read tools and a slash, so the entire
     ship-gating review loop being CLI-only raised nothing. Both tools are now declared explicitly.

Plus: the OTHER TWO `review_passed` stores stay exactly where they are — the execmode ledger row
(display-only) and the playbook self-test check ('simulated') — with their non-authority made
EXPLICIT in the code, so the next reader cannot mistake either for the gate.

Business-level asserts: what the two SURFACES observe (the tool results, the CLI exit code + line,
the `ReviewGate` ship reads), never implementation poking.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import inspect
import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import parity
from mokata.run_resolver import bind_session_run
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.mcp import registry as REG
from mokata.mcp import tool_annotations as TA
from mokata.mcp import tools_read as TR
from mokata.progress_events import (
    ENVELOPE_KEYS,
    REVIEW_VERDICT,
    ProgressLog,
    record_review_verdict,
    ship_review_gate,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src", "mokata")

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")


# --------------------------------------------------------------------------- fixtures
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _persist_run(root, run_id, passed=PHASES):
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)
    return cp


def _cli(argv, cwd):
    """Run the CLI as ship/review do (a separate process's argv), returning (rc, stdout)."""
    from mokata.cli import main
    out = io.StringIO()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main(argv)
    finally:
        os.chdir(old)
    return rc, out.getvalue()


def _age_verdicts(surface, hours_ago):
    """Age the `review_verdict` events on disk — the only way to build "recorded a week ago"
    without waiting a week. Every other line is passed through byte-for-byte (borrowed from the
    R2 suite, which pins the same freshness bound)."""
    path = ProgressLog.from_surface(surface).path
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    out = []
    for ln in lines:
        e = json.loads(ln)
        if e.get("type") == REVIEW_VERDICT:
            e["ts"] = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        out.append(json.dumps(e))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def _events(surface):
    path = ProgressLog.from_surface(surface).path
    if not os.path.exists(path):        # nothing appended yet == no events
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh.read().splitlines() if ln.strip()]


def _verdict_events(surface):
    return [e for e in _events(surface) if e.get("type") == REVIEW_VERDICT]


class _NoPinnedSession(unittest.TestCase):
    """`MOKATA_SESSION_ID` pins run resolution (gate_hook.resolve_run) — a pin leaking in from the
    environment would silently change what these scenarios resolve to (the R1/R2 discipline)."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MOKATA_SESSION_ID", None)


# ======================================================= THE regression
class TestReviewFixR3Regression(_NoPinnedSession):
    def test_review_fix_r3_regression(self):
        """Both halves of "one truth, two surfaces", in one test.

        (a) THE ORPHAN IS GONE — the second answer cannot be imported, from the module or from
            the package's public surface, and its "criteriona" renderer went with it. Reconstructed
            verbatim first, so the test proves what was REMOVED and not merely that a name is
            absent.
        (b) ONE TRUTH, TWO SURFACES — an MCP-side record -> MCP-side status round-trip satisfies
            the SAME `ship_review_gate` the CLI path satisfies, on the same run, off the same
            event. Not "an equivalent gate": the identical function ship reads."""
        # ---- (a) the orphan is gone -------------------------------------------------------
        # The pre-R3 shape, reconstructed verbatim from engine/ship.py: a caller-supplied boolean,
        # trusted, plus the render bug. This is what a `from mokata.engine import ...` used to buy.
        pre_r3_render = (lambda acs: f"[READY] ship — spec with {acs} acceptance "
                                     f"criterion{'' if acs == 1 else 'a'} met, tests green, "
                                     f"review passed. Choose how to land it.")
        self.assertIn("criteriona", pre_r3_render(2))    # the bug WAS real (2 -> "criteriona")

        import mokata.engine as E
        import mokata.engine.ship as S
        for name in ("check_ship_readiness", "ShipReadiness"):
            with self.subTest(symbol=name):
                self.assertFalse(hasattr(S, name), f"{name} still lives in engine/ship.py")
                self.assertFalse(hasattr(E, name), f"{name} is still re-exported by engine")
                self.assertNotIn(name, E.__all__)
                with self.assertRaises(ImportError):
                    exec(f"from mokata.engine import {name}")     # noqa: S102 - a real caller's import

        # ---- (b) one truth, two surfaces --------------------------------------------------
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            # BLOCKED before any review — the honest starting state on both surfaces.
            self.assertTrue(TR.review_status(path=d)["blocks"])
            self.assertTrue(ship_review_gate(surface, run_id="solo").blocks)

            rec = TR.review_record(path=d, passed=True, independent=True)
            self.assertTrue(rec["recorded"])
            self.assertEqual(rec["run"], "solo")

            # the MCP read agrees…
            st = TR.review_status(path=d)
            self.assertFalse(st["blocks"])
            self.assertTrue(st["present"] and st["passed"] and st["independent"])
            # …and the SAME gate ship itself reads is satisfied, off the SAME record…
            gate = ship_review_gate(surface, run_id="solo")
            self.assertFalse(gate.blocks)
            self.assertEqual(gate.message, st["message"])
            # …and so is the CLI half, which was the only surface before this stage.
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0, out)
            self.assertIn("review passed (independent ✓)", out)
            # ONE record on disk — the MCP write did not open a second store.
            self.assertEqual(len(_verdict_events(surface)), 1)


# ======================================================= 1 · the orphan is DELETED
class TestOrphanDeleted(unittest.TestCase):
    def test_check_ship_readiness_is_not_importable(self):
        with self.assertRaises(ImportError):
            from mokata.engine.ship import check_ship_readiness  # noqa: F401

    def test_ship_readiness_carrier_is_not_importable(self):
        with self.assertRaises(ImportError):
            from mokata.engine.ship import ShipReadiness  # noqa: F401

    def test_the_engine_package_no_longer_exports_either(self):
        import mokata.engine as E
        self.assertNotIn("check_ship_readiness", E.__all__)
        self.assertNotIn("ShipReadiness", E.__all__)
        # …and `__all__` is not lying — a `import *` surface with no such names.
        ns: dict = {}
        exec("from mokata.engine import *", ns)      # noqa: S102 - the public surface, as callers see it
        self.assertNotIn("check_ship_readiness", ns)
        self.assertNotIn("ShipReadiness", ns)

    def test_criteriona_is_unrepresentable(self):
        """The render bug cannot be produced any more, because the renderer is GONE. Proven over
        the whole shipped source tree, not just the one file it used to live in."""
        hits = []
        for base, _dirs, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
                # the literal, and the f-string that MINTED it ("criterion" + a bare 'a' plural)
                if "criteriona" in text or "'' if self.spec_acs == 1 else 'a'" in text:
                    hits.append(os.path.relpath(p, REPO))
        self.assertEqual(hits, [], f"the 'criteriona' render survives in: {hits}")

    def test_no_production_reference_to_the_orphan_survives(self):
        """The grep the deletion turns on: zero CODE references anywhere in the shipped source.

        Prose still names both symbols — the deletion notes in `engine/ship.py` and
        `engine/__init__.py` exist so the next reader learns WHY they went. mokata's convention
        renders a code symbol in prose inside backticks, so stripping backticked spans leaves
        exactly the live references, which must be none."""
        hits = []
        for base, _dirs, files in os.walk(SRC):
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                with open(p, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        bare = re.sub(r"`[^`]*`", "", line)
                        if "check_ship_readiness" in bare or "ShipReadiness" in bare:
                            hits.append(f"{os.path.relpath(p, REPO)}:{i}")
        self.assertEqual(hits, [], f"live references to the deleted orphan: {hits}")

    def test_the_landing_decision_half_of_ship_py_still_works_when_called(self):
        """The deletion is surgical: ship.py's landing-decision recorder still WORKS.

        ⚠ READ THE SCOPE OF THAT CLAIM. This test constructs its own precondition — it imports
        `record_finish_decision` and calls it — so what it proves is that the CODE is correct. It
        proves nothing about whether anything reaches it, and until 0.0.17 this docstring said
        "still works exactly as before", which a reader takes as "still in service". It is not:
        stage 4 measured `engine/ship.py` a CERTIFIED ZERO — no module in the package imports it —
        because `ship` is an agent-facing SKILL, so "record the finish" is prose an agent follows
        rather than code a surface runs. That gap between "the code works" and "production reaches
        it" is `REACHABILITY-PINS-MISSING` (doc 84), and this test is a specimen of the shape:
        `tests/test_gate_reachability.py` is what asks the other question."""
        from mokata.engine import LANDING_OPTIONS, record_finish_decision
        self.assertEqual(set(LANDING_OPTIONS), {"merge", "pr", "keep", "discard"})
        dec = record_finish_decision(None, "keep", approve=True)
        self.assertTrue(dec.approved)
        with self.assertRaises(ValueError):
            record_finish_decision(None, "rm-rf", approve=True)

    def test_the_ship_readiness_gate_is_advisory_and_ship_py_still_exists(self):
        """R3 removed a FUNCTION, never the module — and 0.0.17 stage 5 then removed the GATE
        CLAIM, which is a different thing again.

        This test asserted `backed is True` until stage 5, on doc 85 §4's authority that
        `ship-readiness` was one of the 10 backed gates. Stage 4's reachability derivation showed
        why that was aspirational: nothing in the package so much as IMPORTS `engine/ship.py`, so
        the "landing-decision half" this suite's sibling test exercises has no production consumer.
        `backed=True` means "an executable gate a Contract clause may cite"; nothing executes this
        one, so it is advisory now and carries no enforcement point.

        What R3 established is UNCHANGED and still asserted here: `engine/ship.py` exists and the
        landing-decision recorder in it still works. The module was never the thing in doubt."""
        from mokata.skill_contracts import GATES
        ref = GATES["ship-readiness"]
        self.assertFalse(ref.backed, "nothing executes ship-readiness — it is a protocol boundary")
        self.assertEqual(ref.enforcement_point, "",
                         "an advisory boundary must not name an enforcement point")
        self.assertTrue(os.path.exists(os.path.join(REPO, "src/mokata/engine/ship.py")),
                        "the DEMOTION is not a deletion — R3's surgical removal still holds")


# ======================================================= 2 · MCP `review_status` (the read)
class TestMcpReviewStatus(_NoPinnedSession):
    def test_reports_the_resolved_run_and_the_verdict_fields(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            st = TR.review_status(path=d)
            self.assertEqual(st["run"], "solo")
            self.assertTrue(st["present"])
            self.assertTrue(st["passed"])
            self.assertTrue(st["independent"])
            self.assertFalse(st["blocks"])
            # recorded-at is the verdict's OWN envelope stamp (an ISO-8601 UTC string)
            self.assertIsInstance(st["recorded_at"], str)
            self.assertIsNotNone(datetime.fromisoformat(st["recorded_at"]))

    def test_absent_verdict_blocks_identically_to_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            st = TR.review_status(path=d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertTrue(st["blocks"])
            self.assertFalse(st["present"])
            self.assertEqual(rc, 2)
            self.assertIn(st["message"], out)
            self.assertEqual(st["message"], "review hasn't run — run /mokata:review first")

    def test_run_less_blocks_identically_to_the_cli(self):
        """Two runs with state and no live-session narrowing => AMBIGUOUS => the fail-closed R1
        refusal. Both surfaces must refuse the same way — an MCP surface that guessed here would
        re-open the exact leak R1 closed."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")
            st = TR.review_status(path=d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertIsNone(st["run"])
            self.assertTrue(st["blocks"])
            self.assertFalse(st["present"])
            self.assertIn("no run to attribute it to", st["message"])
            self.assertEqual(rc, 2)
            self.assertIn(st["message"], out)
            self.assertIn(st["unblock"], out)           # the same remedy, not a paraphrase

    def test_stale_verdict_blocks_identically_to_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            _age_verdicts(surface, hours_ago=48)        # past the 24h default bound (R2)
            st = TR.review_status(path=d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertTrue(st["blocks"])
            self.assertTrue(st["present"])              # the verdict IS there…
            self.assertFalse(st["passed"])              # …and it no longer counts as a pass
            self.assertIn("stale", st["message"])
            self.assertEqual(rc, 2)
            self.assertIn(st["message"], out)

    def test_a_failed_verdict_blocks_identically_to_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, failed=True, independent=True)
            st = TR.review_status(path=d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertTrue(st["blocks"])
            self.assertTrue(st["present"])
            self.assertFalse(st["passed"])
            self.assertEqual(rc, 2)
            self.assertIn(st["message"], out)

    def test_an_inline_pass_does_not_block_but_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True)       # independent omitted -> inline
            st = TR.review_status(path=d)
            self.assertFalse(st["blocks"])
            self.assertFalse(st["independent"])
            self.assertIn("inline", st["message"])

    def test_explicit_run_reads_that_runs_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")
            TR.review_record(path=d, passed=True, independent=True, run="runA")
            a = TR.review_status(path=d, run="runA")
            b = TR.review_status(path=d, run="runB")
            self.assertEqual(a["run"], "runA")
            self.assertFalse(a["blocks"])
            self.assertEqual(b["run"], "runB")
            self.assertTrue(b["blocks"])                # runB has no verdict of its own

    def test_never_leaks_findings_on_any_answer(self):
        """SECRET-SAFETY: findings text can quote project content. A STATUS read has no need of
        it, so it is absent from every answer — the pass, the block, and the degrade."""
        secret = "SECRET-FINDINGS-do-not-surface-me"
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, failed=True, findings=secret)
            for kw in ({}, {"response_format": "detailed"}, {"run": "solo"}):
                with self.subTest(call=kw):
                    st = TR.review_status(path=d, **kw)
                    self.assertNotIn("findings", st)
                    self.assertNotIn(secret, json.dumps(st))
            # …and on the STALE block, which quotes the verdict's timestamp
            _age_verdicts(surface, hours_ago=48)
            st = TR.review_status(path=d, response_format="detailed")
            self.assertNotIn(secret, json.dumps(st))
            # …and on the degrade answer, where a naive implementation might dump what it had
            with mock.patch("mokata.progress_events.ship_review_gate",
                            side_effect=OSError("boom")):
                st = TR.review_status(path=d)
            self.assertTrue(st["blocks"])
            self.assertNotIn(secret, json.dumps(st))
            # the findings ARE on disk — this is a non-disclosure, not a non-record
            self.assertIn(secret, json.dumps(_verdict_events(surface)))

    def test_degrades_clean_on_an_unreadable_gate(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            with mock.patch("mokata.progress_events.ship_review_gate",
                            side_effect=OSError("boom")):
                st = TR.review_status(path=d)
            self.assertTrue(st["blocks"])               # fail-CLOSED, never a traceback
            self.assertFalse(st["present"])

    def test_response_format_drops_the_render_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            self.assertNotIn("block", TR.review_status(path=d))
            detailed = TR.review_status(path=d, response_format="detailed")
            self.assertIn("block", detailed)
            # the render is the ONE line the CLI prints
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertEqual(detailed["block"], out.strip())

    def test_the_detailed_render_carries_the_unblock_remedy_like_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            detailed = TR.review_status(path=d, response_format="detailed")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertEqual(detailed["block"], out.strip())
            self.assertIn("→ to unblock:", detailed["block"])


# ======================================================= 3 · MCP `review_record` (the record)
class TestMcpReviewRecord(_NoPinnedSession):
    def test_writes_the_same_event_shape_as_the_cli(self):
        """Same envelope, same type, same `data` keys — byte-shape parity with the CLI's write,
        proven by writing one of each into the same log and diffing the shapes."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            _cli(["progress", "record-review", "--passed", "--independent",
                  "--findings", "2 nits", "--path", d], d)
            TR.review_record(path=d, passed=True, independent=True, findings="2 nits")
            events = _verdict_events(surface)
            self.assertEqual(len(events), 2)
            cli_ev, mcp_ev = events
            self.assertEqual(set(cli_ev), set(mcp_ev))
            self.assertEqual(set(mcp_ev), set(ENVELOPE_KEYS))
            self.assertEqual(mcp_ev["type"], REVIEW_VERDICT)
            self.assertEqual(cli_ev["stage"], mcp_ev["stage"])
            self.assertEqual(cli_ev["run_id"], mcp_ev["run_id"])
            self.assertEqual(cli_ev["data"], mcp_ev["data"])
            self.assertEqual(mcp_ev["data"],
                             {"passed": True, "independent": True, "findings": "2 nits"})

    def test_mcp_record_then_cli_review_status_sees_it(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0, out)
            self.assertIn("review passed (independent ✓)", out)

    def test_cli_record_then_mcp_review_status_sees_it(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, _ = _cli(["progress", "record-review", "--passed", "--independent",
                          "--path", d], d)
            self.assertEqual(rc, 0)
            st = TR.review_status(path=d)
            self.assertFalse(st["blocks"])
            self.assertTrue(st["passed"] and st["independent"])

    def test_two_runs_no_cross_attribution_through_the_mcp_surface(self):
        """R1's leak, re-asked on the NEW surface: runA's PASS must never satisfy runB's gate,
        and a run that cannot be resolved must satisfy nothing at all."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")
            TR.review_record(path=d, passed=True, independent=True, run="runA")

            self.assertFalse(TR.review_status(path=d, run="runA")["blocks"])
            self.assertTrue(TR.review_status(path=d, run="runB")["blocks"],
                            "runB shipped on runA's verdict — R1's leak, through MCP")
            self.assertTrue(ship_review_gate(surface, run_id="runB").blocks)
            # and with no run named, the ambiguity refuses rather than picking a winner
            unresolved = TR.review_status(path=d)
            self.assertIsNone(unresolved["run"])
            self.assertTrue(unresolved["blocks"])

    def test_the_record_key_is_the_read_key(self):
        """The record resolves the SAME way the read does — proven by never naming a run on
        either call and still having the gate satisfied."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rec = TR.review_record(path=d, passed=True, independent=True)
            st = TR.review_status(path=d)
            self.assertEqual(rec["run"], st["run"])
            self.assertFalse(st["blocks"])

    def test_a_session_bound_run_wins_resolution_on_both_halves(self):
        """The session-aware seam (R1) is the SAME one, so a bound session keys the record and the
        read to its own run even with a second run present."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")
            bind_session_run(d, "sessB", "runB")
            with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": "runB"}):
                rec = TR.review_record(path=d, passed=True, independent=True)
                st = TR.review_status(path=d)
            self.assertEqual(rec["run"], "runB")
            self.assertEqual(st["run"], "runB")
            self.assertFalse(st["blocks"])
            self.assertTrue(TR.review_status(path=d, run="runA")["blocks"])

    def test_a_run_less_verdict_is_recorded_but_satisfies_nothing_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")               # ambiguous => no run resolves
            rec = TR.review_record(path=d, passed=True, independent=True)
            self.assertTrue(rec["recorded"])      # honest observability: the event lands
            self.assertIsNone(rec["run"])
            self.assertFalse(rec["satisfies_gate"])
            self.assertIn("ship will NOT accept a run-less verdict", rec["message"])
            self.assertIn("--run <run id>", rec["message"])
            self.assertEqual(len(_verdict_events(surface)), 1)
            # and it satisfies no gate — neither run's, nor the unresolved read's
            self.assertTrue(TR.review_status(path=d, run="runA")["blocks"])
            self.assertTrue(TR.review_status(path=d, run="runB")["blocks"])
            self.assertTrue(TR.review_status(path=d)["blocks"])

    def test_the_recorded_message_is_the_cli_line_verbatim(self):
        """One truth source, one wording — the pre-R3 f-strings, reconstructed, must still be what
        BOTH surfaces say."""
        from mokata.cli_commands.runviews import review_recorded_line, review_runless_line
        self.assertEqual(review_recorded_line("passed", "independent", "r1"),
                         "mokata review: recorded verdict passed (independent) for run r1.")
        self.assertEqual(
            review_runless_line("failed", "inline"),
            "mokata review: recorded verdict failed (inline) WITHOUT a run — no run could "
            "be resolved here, and ship will NOT accept a run-less verdict. Re-record it "
            "naming the run: `mokata progress record-review --failed"
            " --run <run id>` (`mokata sessions` lists them).")
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rec = TR.review_record(path=d, passed=True, independent=True)
            rc, out = _cli(["progress", "record-review", "--passed", "--independent",
                            "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertEqual(rec["message"], out.strip())

    def test_requires_exactly_one_outcome(self):
        """The CLI's mutually-exclusive REQUIRED outcome group, as a refusal rather than an
        argparse exit — mokata records what the review concluded and will not guess it."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            _persist_run(d, "solo")
            for kw in ({}, {"passed": True, "failed": True}):
                with self.subTest(call=kw):
                    res = TR.review_record(path=d, **kw)
                    self.assertFalse(res["recorded"])
                    self.assertEqual(res["status"], "error")
                    self.assertIn("exactly one of passed / failed", res["reason"])
            self.assertEqual(_verdict_events(surface), [])   # and nothing was written

    def test_findings_are_recorded_with_the_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, failed=True, findings="3 findings: A, B, C")
            self.assertEqual(_verdict_events(surface)[0]["data"]["findings"],
                             "3 findings: A, B, C")

    def test_omitted_findings_are_absent_not_empty(self):
        """Same as the CLI's `--findings` default (None): the key is omitted, not written empty."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True)
            self.assertNotIn("findings", _verdict_events(surface)[0]["data"])

    def test_the_latest_verdict_wins_across_surfaces(self):
        """A re-review through EITHER surface supersedes the earlier verdict — one record, read
        latest-first, exactly as `ship_review_gate` has always behaved."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, failed=True)
            self.assertTrue(TR.review_status(path=d)["blocks"])
            _cli(["progress", "record-review", "--passed", "--independent", "--path", d], d)
            self.assertFalse(TR.review_status(path=d)["blocks"])
            self.assertFalse(ship_review_gate(surface, run_id="solo").blocks)

    def test_recording_is_ungated_no_proposal_no_approval(self):
        """The grounded trust posture, asserted: recording mirrors the CLI's UNGATED append. There
        is no `approve`/`confirm`/`proposal_id` parameter to type, nothing is 'proposed', and the
        event lands on the first call."""
        sig = set(inspect.signature(TR.review_record).parameters)
        self.assertEqual(sig, {"path", "passed", "failed", "independent", "findings", "run"})
        for banned in ("approve", "confirm", "proposal_id"):
            self.assertNotIn(banned, sig)
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            res = TR.review_record(path=d, passed=True, independent=True)
            self.assertNotEqual(res.get("status"), "proposed")
            self.assertNotIn("proposal_id", res)
            self.assertEqual(len(_verdict_events(surface)), 1)   # committed on call ONE
            # no approval artefact was minted anywhere
            approvals = os.path.join(d, ".mokata", "approvals")
            self.assertFalse(os.path.isdir(approvals) and os.listdir(approvals))

    def test_recording_degrades_clean_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            with mock.patch("mokata.progress_events.record_review_verdict",
                            side_effect=OSError("disk full")):
                res = TR.review_record(path=d, passed=True)
            self.assertFalse(res["recorded"])
            self.assertEqual(res["status"], "error")
            self.assertIn("disk full", res["reason"])


# ======================================================= 4 · the OTHER TWO stores stay, explicitly
class TestNonAuthorityIsExplicit(unittest.TestCase):
    """The two other `review_passed` stores are NOT unified — they answer different questions —
    but their non-authority is now stated in the code where a reader meets them."""

    def _text(self, rel):
        with open(os.path.join(SRC, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_the_execmode_ledger_field_names_the_one_truth_source(self):
        text = self._text(os.path.join("execmode", "orchestrator.py"))
        idx = text.index('ledger.record("subagent"')
        near = text[max(0, idx - 900):idx]
        self.assertIn("DISPLAY-ONLY", near)
        self.assertIn("review_verdict", near)
        self.assertIn("ship_review_gate", near)

    def test_the_playbook_check_names_the_one_truth_source(self):
        text = self._text("playbook.py")
        idx = text.index('checks["review_passed"] = "simulated"')
        near = text[max(0, idx - 1400):idx]
        self.assertIn("SELF-TEST", near)
        self.assertIn("review_verdict", near)
        self.assertIn("ship_review_gate", near)

    def test_the_execmode_ledger_row_still_behaves_exactly_as_before(self):
        """Explicitness is a COMMENT, not a behaviour change: the lane view still reads the row."""
        from mokata.progress import _subagent_state
        self.assertEqual(_subagent_state({"ok": True, "review_passed": False}),
                         _subagent_state({"ok": False}))
        self.assertNotEqual(_subagent_state({"ok": True, "review_passed": True}),
                            _subagent_state({"ok": True, "review_passed": False}))

    def test_the_playbook_simulated_check_still_behaves_exactly_as_before(self):
        from mokata.playbook import PlaybookResult
        base = {"brainstorm_approved": True, "gate_blocked_initially": True,
                "gate_passed_after_tests": True, "red_before_green": True,
                "knowledge_used": True, "within_budget": True}
        self.assertTrue(PlaybookResult("standard", "sequential",
                                       dict(base, review_passed="simulated")).ok)
        self.assertTrue(PlaybookResult("standard", "sequential",
                                       dict(base, review_passed=True)).ok)
        self.assertFalse(PlaybookResult("standard", "sequential",
                                        dict(base, review_passed=False)).ok)

    def test_neither_store_is_read_by_the_ship_gate(self):
        """The claim the comments make, asserted: `ship_review_gate`'s answer depends only on the
        progress-event record — an execmode ledger row cannot change it."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            from mokata.govern import AuditLedger
            led = AuditLedger.from_mokata_dir(surface.mokata_dir)
            led.record("subagent", task="t1", ok=True, isolated=True, review_passed=True)
            self.assertTrue(ship_review_gate(surface, run_id="solo").blocks,
                            "a ledger row satisfied the ship gate — it is not a truth source")
            record_review_verdict(surface, passed=True, independent=True, run_id="solo")
            self.assertFalse(ship_review_gate(surface, run_id="solo").blocks)


# ======================================================= 5 · parity + registry
class TestParityAndRegistry(unittest.TestCase):
    def test_the_parity_matrix_includes_both_tools(self):
        surface = parity.SURFACE_MATRIX["progress"]
        self.assertIn("review_status", surface.mcp_read)
        self.assertIn("review_record", surface.mcp_read)
        self.assertIn("review_status", surface.mcp)
        self.assertIn("review_record", surface.mcp)
        self.assertIn("review_status", parity.declared_mcp_tools())
        self.assertIn("review_record", parity.declared_mcp_tools())

    def test_the_matrix_still_verifies(self):
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())

    def test_a_separate_review_surface_would_be_STALE(self):
        """The grounded reason both tools ride `progress`: Stage-54e keys the matrix on LIVE
        argparse subcommands, and `record-review`/`review-status` are NESTED sub-actions — there is
        no top-level `review` command for a surface to attach to."""
        self.assertNotIn("review", parity.cli_command_names())
        self.assertIn("progress", parity.cli_command_names())
        saved = dict(parity.SURFACE_MATRIX)
        try:
            parity.SURFACE_MATRIX["review"] = parity.CommandSurface(
                "review", mcp_read=("review_status",))
            self.assertIn("review", parity.verify_parity().stale)
        finally:
            parity.SURFACE_MATRIX.clear()
            parity.SURFACE_MATRIX.update(saved)
        self.assertTrue(parity.verify_parity().ok)

    def test_both_are_registered_read_kind_and_neither_is_a_gated_write(self):
        self.assertIn("review_status", REG.read_tool_names())
        self.assertIn("review_record", REG.read_tool_names())
        self.assertNotIn("review_status", REG.write_tool_names())
        self.assertNotIn("review_record", REG.write_tool_names())
        self.assertIn("review_status", REG.tool_names())
        self.assertIn("review_record", REG.tool_names())

    def test_the_tool_count_moved_56_to_58(self):
        # R3's own delta was 56 -> 58 (`review_status` + `review_record`); WT-LIST (0.0.16) then
        # added `worktree_list` (59), and M-4/R5 added `consolidate_proposals` + `consolidate`
        # (61). R3's two tools are pinned by name above, which is the property this class owns.
        self.assertEqual(len(REG.TOOLS), 61)
        self.assertEqual(len(REG.tool_names()), len(set(REG.tool_names())))

    def test_annotations_are_projected_and_neither_is_open_world(self):
        for name in ("review_status", "review_record"):
            with self.subTest(tool=name):
                ann = TA.annotations_for("read", name)
                self.assertIs(ann["readOnlyHint"], True)
                self.assertIs(ann["openWorldHint"], False)
                self.assertNotIn(name, TA.OPEN_WORLD_TOOLS)

    def test_the_write_tool_sweep_does_not_apply_to_the_record_tool(self):
        """SI.3's sweep calls every WRITE tool with `approve=True, confirm=True` and demands a
        proposal. `review_record` takes neither parameter, by design — which is exactly why it is
        registered `read` and not `write`."""
        writes = {s.name for s in REG.TOOLS if s.kind == "write"}
        self.assertNotIn("review_record", writes)
        params = set(inspect.signature(TR.review_record).parameters)
        self.assertFalse(params & {"approve", "confirm", "proposal_id"})


# ======================================================= 6 · NEGATIVES — nothing else moved
class TestNothingElseMoved(_NoPinnedSession):
    def test_cli_record_review_output_is_byte_identical(self):
        """The pre-R3 CLI lines, reconstructed as f-strings verbatim from the code this stage
        refactored into shared builders."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = _cli(["progress", "record-review", "--passed", "--independent",
                            "--path", d], d)
            self.assertEqual(rc, 0)
            verdict, kind, rid = "passed", "independent", "solo"
            self.assertEqual(
                out, f"mokata review: recorded verdict {verdict} ({kind}) for run {rid}.\n")

        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA")
            _persist_run(d, "runB")                  # ambiguous -> the run-less line
            rc, out = _cli(["progress", "record-review", "--failed", "--path", d], d)
            self.assertEqual(rc, 0)
            verdict, kind, passed = "failed", "inline", False
            self.assertEqual(
                out,
                f"mokata review: recorded verdict {verdict} ({kind}) WITHOUT a run — no run could "
                f"be resolved here, and ship will NOT accept a run-less verdict. Re-record it "
                f"naming the run: `mokata progress record-review --"
                f"{'passed' if passed else 'failed'}"
                f" --run <run id>` (`mokata sessions` lists them).\n")

    def test_cli_review_status_output_and_exit_codes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            # a BLOCK prints the gate message + its unblock remedy, on one line (unchanged)
            self.assertEqual(
                (rc, out),
                (2, "review hasn't run — run /mokata:review first"
                    "  → to unblock: run /mokata:review first\n"))
            _cli(["progress", "record-review", "--passed", "--independent", "--path", d], d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual((rc, out), (0, "review passed (independent ✓)\n"))
            _cli(["progress", "record-review", "--passed", "--path", d], d)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual((rc, out), (0, "review passed (inline — not independent)\n"))

    def test_cli_mark_and_progress_are_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo")
            rc, out = _cli(["progress", "mark", "review", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertEqual(out, "mokata progress: entered 'review' (run solo).\n")

    def test_the_event_shape_on_disk_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            ev = _verdict_events(surface)[0]
            self.assertEqual(tuple(ev), ENVELOPE_KEYS)      # same keys, same ORDER
            self.assertEqual(ev["schema_version"], 1)
            self.assertEqual(ev["stage"], "review")

    def test_r1_and_r2_semantics_are_untouched_by_the_new_surface(self):
        """The R1/R2 contracts, re-asserted through the tools that now share their seam."""
        from mokata.progress_events import latest_review_verdict
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            # R1: a run-less READ still refuses outright
            self.assertIsNone(latest_review_verdict(surface, run_id=None))
            # R2: the freshness bound still governs, whoever recorded the verdict
            _age_verdicts(surface, hours_ago=48)
            gate = ship_review_gate(surface, run_id="solo")
            self.assertTrue(gate.blocks)
            self.assertIn("stale", gate.message)

    def test_the_ship_gate_signature_and_verdicts_are_unchanged(self):
        self.assertEqual(list(inspect.signature(ship_review_gate).parameters),
                         ["surface", "run_id"])
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            g = ship_review_gate(surface, run_id="solo")
            self.assertEqual((g.present, g.passed, g.blocks), (False, False, True))

    def test_a_status_read_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo")
            TR.review_record(path=d, passed=True, independent=True)
            path = ProgressLog.from_surface(surface).path
            before = os.stat(path).st_size
            for _ in range(3):
                TR.review_status(path=d, response_format="detailed")
            self.assertEqual(os.stat(path).st_size, before)


if __name__ == "__main__":
    unittest.main()
