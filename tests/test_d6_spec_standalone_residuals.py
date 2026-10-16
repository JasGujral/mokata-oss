"""0.0.23 stage D6 — `spec-standalone-residuals`: the three SPEC-STANDALONE residuals + SPEC-HISTORY.

(1) the spec skill's `## Standalone` section carries an AGENT-layer STOP-and-ASK (one question, before
    a proposal is minted) — and only the spec skill does.
(2) `completeness.GateResult.render()` is WIRED: `mokata spec emit`'s completeness block prints the
    gate's own rendering, carried on `EmitOutcome.completeness`.
(3) `engine/phases._completeness` passes the STORE, so the engine pipeline sees an approved
    refinement set instead of reading a refine-front-ended run as standalone.
SPEC-HISTORY — `mokata spec show --version N / --versions` and MCP `spec_show(version=,
list_versions=)` read superseded specs back from `spec_archive__<run>__v<N>`; every answer names the
spec's version.
"""
import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.engine import phases as PH
from mokata.engine import spec_history as SH
from mokata.engine.emit import emit_spec, spec_commit
from mokata.engine.spec import AcceptanceCriterion, Spec, TestRef
from mokata.engine.spec_gate import SPEC_STATE_KEY
from mokata.spec_scope import archive_key
from mokata.state import StateStore

ROOT = os.path.join(os.path.dirname(__file__), "..", "src", "mokata")


def _read(*rel):
    with open(os.path.join(ROOT, *rel), "rb") as fh:
        return fh.read().decode("utf-8")


class R1StopAndAsk(unittest.TestCase):

    def test_the_spec_skill_asks_before_drafting_standalone(self):
        from mokata.skills import SPEC_STANDALONE_ASK
        for rel in (("templates", "commands", "spec.md"), ("skills", "spec", "SKILL.md")):
            body = _read(*rel)
            section = body.split("## Standalone\n", 1)[1].split("\n## ", 1)[0]
            self.assertIn(SPEC_STANDALONE_ASK, section)
            self.assertIn("STOP and ASK the user ONE question", section)

    def test_no_other_skill_gains_the_question(self):
        for name in ("develop", "test", "review"):
            self.assertNotIn("STOP and ASK the user ONE question",
                             _read("templates", "commands", f"{name}.md"))


class R2RenderIsWired(unittest.TestCase):

    def test_a_completeness_block_carries_the_gate_result(self):
        with tempfile.TemporaryDirectory() as d:
            surface = mock.Mock(root=d, state=StateStore(d))
            spec = Spec("t", [AcceptanceCriterion("AC-1", "x"), AcceptanceCriterion("AC-2", "y")])
            out = emit_spec(surface, spec, [TestRef("t", ["AC-1"])], run_id="r1")
            self.assertTrue(out.blocked_by_completeness)
            self.assertEqual(["AC-2"], out.completeness.unmapped_ids)

    def test_the_cli_prints_the_gates_own_render(self):
        from mokata.cli_commands import spec as CS
        gr = mock.Mock()
        gr.render.return_value = "[BLOCK] completeness gate — RENDERED BY THE GATE"
        outcome = mock.Mock(blocked_by_completeness=True, completeness=gr)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"title": "t", "criteria": [{"id": "AC-1", "text": "x"}]}, fh)
            args = types.SimpleNamespace(path=d, file=path, yes=True)
            with mock.patch.object(CS, "_load_surface", return_value=mock.Mock(root=d,
                                                                                mokata_dir=d)), \
                    mock.patch.object(CS, "_run_scoped_store", return_value=(mock.Mock(), None,
                                                                             None)), \
                    mock.patch("mokata.engine.emit.emit_spec", return_value=outcome), \
                    mock.patch("mokata.brainstorm.load_approved_approach", return_value=None), \
                    mock.patch.object(CS, "AuditLedger"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = CS.cmd_spec_emit(args)
        self.assertEqual(1, rc)
        self.assertIn("RENDERED BY THE GATE", buf.getvalue())
        gr.render.assert_called_once_with()


class R3ThePipelineSeesTheStore(unittest.TestCase):

    def test_a_refine_front_ended_run_is_not_read_as_standalone(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "x")])
        ctx = PH.PhaseContext(handoff=None, spec=spec, tests=[TestRef("t", ["AC-1"])])
        plan = mock.Mock(label="refinements v1")
        with mock.patch("mokata.engine.completeness.load_approved_refinements",
                        return_value=plan), \
                mock.patch("mokata.engine.completeness.load_approved_approach",
                           return_value=None):
            PH._completeness(ctx, store=object())
            self.assertFalse(ctx.gate_result.standalone)
            self.assertEqual("refinements v1", ctx.gate_result.refinements)
            PH._completeness(ctx)                       # no store: the old (wrong) reading
            self.assertTrue(ctx.gate_result.standalone)

    def test_with_a_handoff_the_verdict_is_unchanged(self):
        from mokata.brainstorm import Handoff
        h = Handoff.from_dict({"topic": "t", "approach": {"name": "A", "summary": "s"},
                               "answered_questions": [], "grounding": {}})
        spec = Spec("t", [AcceptanceCriterion("AC-1", "x")])
        ctx = PH.PhaseContext(handoff=h, spec=spec, tests=[TestRef("t", ["AC-1"])])
        PH._completeness(ctx, store=None)
        a = ctx.gate_result
        PH._completeness(ctx, store=StateStore(tempfile.mkdtemp()))
        self.assertEqual((a.passed, a.approach, a.standalone),
                         (ctx.gate_result.passed, ctx.gate_result.approach,
                          ctx.gate_result.standalone))


class SpecHistory(unittest.TestCase):

    def _two_versions(self, d):
        store = StateStore(d)
        spec_commit(store, Spec("first", [AcceptanceCriterion("AC-1", "one")]), run_id="r1")
        spec_commit(store, Spec("second", [AcceptanceCriterion("AC-1", "one"),
                                           AcceptanceCriterion("AC-2", "two")]), run_id="r1")
        return store

    def test_versions_list_the_archive_and_the_current_spec(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._two_versions(d)
            rows = SH.spec_versions(store, "r1")
            self.assertEqual([(1, archive_key("r1", 1), False, "first", 1),
                              (2, SPEC_STATE_KEY, True, "second", 2)],
                             [(r["version"], r["key"], r["current"], r["title"], r["ac_count"])
                              for r in rows])

    def test_a_superseded_version_reads_back(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._two_versions(d)
            self.assertEqual("first", SH.spec_at_version(store, "r1", 1)["spec"].title)
            self.assertIsNone(SH.spec_at_version(store, "r1", 7)["spec"])

    def test_a_malformed_archive_is_malformed_not_absent(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._two_versions(d)
            store.write(archive_key("r1", 1), {"title": "x", "criteria": [{"text": "no id"}]})
            row = SH.spec_at_version(store, "r1", 1)
            self.assertTrue(row["malformed"])
            self.assertIn("MALFORMED", "\n".join(SH.render_versions(SH.spec_versions(store,
                                                                                       "r1"))))

    def _mcp(self, store, **kw):
        from mokata.mcp import tools_read as T
        with mock.patch("mokata.cli_commands.spec._run_scoped_store",
                        return_value=(store, "r1", "")), mock.patch.object(T, "_surface"):
            return T.spec_show(**kw)

    def test_mcp_spec_show_names_the_version_and_reads_history(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._two_versions(d)
            cur = self._mcp(store)
            self.assertEqual((2, 2, False, "second"),
                             (cur["version"], cur["current_version"], cur["superseded"],
                              cur["title"]))
            old = self._mcp(store, version=1, response_format="detailed")
            self.assertEqual(("first", 1, True), (old["title"], old["version"],
                                                  old["superseded"]))
            self.assertIn("version: v1 (superseded — current is v2)", old["block"])
            listed = self._mcp(store, list_versions=True)
            self.assertEqual([1, 2], [r["version"] for r in listed["versions"]])
            missing = self._mcp(store, version=9)
            self.assertEqual("no-such-version", missing["reason"])

    def _cli(self, store, **kw):
        from mokata.cli_commands import spec as CS
        args = types.SimpleNamespace(**{"path": ".", "version": None, "versions": False, **kw})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(CS, "_load_surface"), \
                mock.patch.object(CS, "_run_scoped_store", return_value=(store, "r1", None)):
            with redirect_stdout(out), redirect_stderr(err):
                rc = CS.cmd_spec_show(args)
        return rc, out.getvalue(), err.getvalue()

    def test_cli_spec_show_version_and_versions(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._two_versions(d)
            rc, out, _ = self._cli(store)
            self.assertEqual(0, rc)
            self.assertIn("spec: second\nversion: v2 (current)\n", out)
            rc, out, _ = self._cli(store, **{"version": 1})
            self.assertIn("spec: first\nversion: v1 (superseded — current is v2)\n", out)
            rc, out, _ = self._cli(store, **{"versions": True})
            self.assertIn("v1: 'first' — 1 AC(s)", out)
            self.assertIn("v2 (current): 'second' — 2 AC(s)", out)
            rc, _, err = self._cli(store, **{"version": 5})
            self.assertEqual(1, rc)
            self.assertIn("no spec v5", err)

    def test_the_cli_and_mcp_blocks_still_agree_line_for_line(self):
        from mokata.cli_commands import spec as CS
        with tempfile.TemporaryDirectory() as d:
            store = self._two_versions(d)
            _, cli, _ = self._cli(store, **{"version": 1})
            mcp = self._mcp(store, version=1, response_format="detailed")["block"]
            self.assertEqual(cli.rstrip("\n"), mcp)
            self.assertTrue(CS)


if __name__ == "__main__":
    unittest.main()
