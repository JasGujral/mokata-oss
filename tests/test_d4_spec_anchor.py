"""0.0.23 stage D4 — `spec-anchor`: SPEC-ANCHOR S1→S2→S3.

S1 — each AC carries `anchors[]` ({file, symbol?, kind, source, confidence}) captured AT EMIT in the
one committer from artifacts brainstorm already computed (approach decisions' about_code, the Lens-1
impact, the scope fallback). Additive: an anchorless AC's bytes are unchanged. Strict: a malformed
anchor makes the persisted spec read as MALFORMED (refused loudly, never silently dropped).
S2 — one per-AC line `AC-1 (text) → implement `file:sym` · test `path`` on spec_show / `mokata spec
show` and in develop's per-turn pack (the ONE channel).
S3 — the completeness gate WARNS (never blocks) on an anchorless AC when a structural backend is
available and on an anchor that does not resolve; develop re-reports a stale anchor as live evidence.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import APPROACH_STATE_KEY, Handoff
from mokata.engine import spec_anchor as SA
from mokata.engine.completeness import run_completeness_gate
from mokata.engine.emit import preview_content, spec_commit, spec_from_payload
from mokata.engine.spec import AcceptanceCriterion, CodeAnchor, Spec, TestRef
from mokata.engine.spec_gate import SPEC_STATE_KEY, read_emitted_spec
from mokata.spec_scope import SpecScope
from mokata.state import StateStore


def _handoff_dict(decisions=(), targets=(), files=()):
    return {
        "topic": "limits", "phase": "brainstorm", "approver": "jas", "approved_at": "now",
        "approach": {"name": "A", "summary": "s", "decisions": [dict(d) for d in decisions]},
        "answered_questions": [], "grounding": {},
        "impact": {"approach": "A", "targets": list(targets), "impacted_files": list(files)},
    }


DECISION = {"id": "D1", "statement": "single cusip limits read amountMax",
            "about_code": ["src/limits.py:amount_max"]}


class S1TheShape(unittest.TestCase):

    def test_an_anchorless_ac_serializes_exactly_as_before(self):
        self.assertEqual({"id": "AC-1", "text": "x"}, AcceptanceCriterion("AC-1", "x").to_dict())

    def test_anchors_round_trip(self):
        ac = AcceptanceCriterion("AC-1", "x", anchors=[CodeAnchor("src/a.py", "f")])
        again = AcceptanceCriterion.from_dict(json.loads(json.dumps(ac.to_dict())))
        self.assertEqual(ac, again)

    def test_a_malformed_anchor_is_refused_loudly(self):
        for bad in ({"file": ""}, {"file": "a.py", "kind": "nope"}, {"file": "a.py", "source": "x"},
                    {"file": "a.py", "confidence": "certain"}, "a.py"):
            with self.assertRaises(ValueError):
                CodeAnchor.from_dict(bad)
        with self.assertRaises(ValueError):
            AcceptanceCriterion.from_dict({"id": "AC-1", "anchors": "src/a.py"})

    def test_a_persisted_spec_with_a_bad_anchor_reads_as_malformed_not_absent(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            store.write(SPEC_STATE_KEY, {"title": "t", "criteria": [
                {"id": "AC-1", "text": "x", "anchors": [{"file": "a.py", "kind": "bogus"}]}]})
            spec, malformed = read_emitted_spec(store)
            self.assertIsNone(spec)
            self.assertTrue(malformed, "a user's spec is never silently read as anchorless")

    def test_the_payload_carries_author_anchors_and_refuses_bad_ones(self):
        spec, _ = spec_from_payload({"title": "t", "criteria": [
            {"id": "AC-1", "text": "x", "anchors": [{"file": "src/a.py", "symbol": "f"}]}]})
        self.assertEqual("src/a.py", spec.criteria[0].anchors[0].file)
        self.assertEqual("spec", spec.criteria[0].anchors[0].source)
        with self.assertRaises(ValueError):
            spec_from_payload({"title": "t", "criteria": [{"id": "AC-1", "anchors": [{}]}]})


class S1Capture(unittest.TestCase):

    def test_a_decision_named_by_the_ac_anchors_it_with_high_confidence(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "per D1, the table reads the max")])
        SA.derive_anchors(spec, Handoff.from_dict(_handoff_dict([DECISION])))
        a = spec.criteria[0].anchors[0]
        self.assertEqual(("src/limits.py", "amount_max", "impl", "approach", "high"),
                         (a.file, a.symbol, a.kind, a.source, a.confidence))

    def test_the_impact_set_supplies_impl_and_test_targets(self):
        h = Handoff.from_dict(_handoff_dict(files=["src/limits.py", "tests/test_limits.py",
                                                   "src/unrelated.py"]))
        spec = Spec("t", [AcceptanceCriterion("AC-1", "limits reject a value over the max")])
        SA.derive_anchors(spec, h)
        got = {(a.file, a.kind, a.source) for a in spec.criteria[0].anchors}
        self.assertIn(("src/limits.py", "impl", "blast-radius"), got)
        self.assertIn(("tests/test_limits.py", "test-target", "blast-radius"), got)
        self.assertNotIn("src/unrelated.py", {f for f, _, _ in got})

    def test_scope_is_the_coarse_fallback_only_for_an_unplaced_ac(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "limits apply"),
                          AcceptanceCriterion("AC-2", "something else entirely")],
                    scope=SpecScope(authorized=("src/limits*",)))
        SA.derive_anchors(spec, Handoff.from_dict(_handoff_dict(files=["src/limits.py"])))
        self.assertEqual({"blast-radius"}, {a.source for a in spec.criteria[0].anchors})
        self.assertEqual([("src/limits*", "reference", "scope", "low")],
                         [(a.file, a.kind, a.source, a.confidence)
                          for a in spec.criteria[1].anchors])

    def test_author_anchors_lead_and_derivation_is_idempotent(self):
        mine = CodeAnchor("src/mine.py", "f", source="spec")
        spec = Spec("t", [AcceptanceCriterion("AC-1", "per D1 the max", anchors=[mine])])
        h = Handoff.from_dict(_handoff_dict([DECISION]))
        SA.derive_anchors(spec, h)
        once = spec.to_dict()
        SA.derive_anchors(spec, h)
        self.assertEqual(once, spec.to_dict())
        self.assertEqual(mine, spec.criteria[0].anchors[0])

    def test_nothing_to_derive_leaves_the_spec_byte_identical(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "x")])
        before = json.dumps(spec.to_dict())
        SA.derive_anchors(spec, None)
        self.assertEqual(before, json.dumps(spec.to_dict()))

    def test_the_committer_captures_anchors_and_the_preview_shows_exactly_what_lands(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            store.write(APPROACH_STATE_KEY, _handoff_dict([DECISION]))
            spec = Spec("t", [AcceptanceCriterion("AC-1", "per D1 the max")])
            shown = json.loads(preview_content(store, spec))
            self.assertEqual([], spec.criteria[0].anchors, "the preview must not mutate the spec")
            spec_commit(store, spec, run_id="r1")
            landed = store.read(SPEC_STATE_KEY)
            self.assertEqual(shown["criteria"], landed["criteria"])
            self.assertEqual("approach", landed["criteria"][0]["anchors"][0]["source"])


class S3WarnNeverBlock(unittest.TestCase):

    def test_an_anchorless_ac_warns_only_when_a_structural_backend_is_available(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "x")])
        tests = [TestRef("test_x", ["AC-1"])]
        gr = run_completeness_gate(spec, tests, graph_available=True)
        self.assertTrue(gr.passed, "a warning never flips the verdict")
        self.assertIn(SA.WARN_ANCHORLESS.format(ac="AC-1"), gr.warnings)
        self.assertEqual([], run_completeness_gate(spec, tests, graph_available=False).warnings)

    def test_an_unresolved_anchor_warns(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            with open(os.path.join(d, "src", "a.py"), "wb") as fh:
                fh.write(b"def present():\n    pass\n")
            spec = Spec("t", [AcceptanceCriterion("AC-1", "x", anchors=[
                CodeAnchor("src/a.py", "present"), CodeAnchor("src/a.py", "gone"),
                CodeAnchor("src/missing.py")])])
            gr = run_completeness_gate(spec, [TestRef("t", ["AC-1"])], root=d)
            self.assertTrue(gr.passed)
            text = "\n".join(gr.warnings)
            self.assertNotIn("src/a.py:present", text)
            self.assertIn("`src/a.py:gone` does not resolve (symbol `gone` not found", text)
            self.assertIn("`src/missing.py` does not resolve (file not found)", text)

    def test_without_the_new_arguments_the_gate_is_unchanged(self):
        spec = Spec("t", [AcceptanceCriterion("AC-1", "x", anchors=[CodeAnchor("nope.py")])])
        self.assertEqual([], run_completeness_gate(spec, [TestRef("t", ["AC-1"])]).warnings)


class S2Surface(unittest.TestCase):

    def _spec(self):
        return Spec("t", [AcceptanceCriterion("AC-1", "limits reject over max", anchors=[
            CodeAnchor("src/limits.py", "check"),
            CodeAnchor("tests/test_limits.py", kind="test-target")]),
            AcceptanceCriterion("AC-2", "unanchored")])

    def test_one_line_per_anchored_ac(self):
        self.assertEqual(["AC-1 (limits reject over max) → implement `src/limits.py:check` · "
                          "test `tests/test_limits.py`"], SA.ac_anchor_lines(self._spec()))

    def test_spec_show_carries_anchors_and_the_line(self):
        from mokata.mcp import tools_read as T
        from unittest import mock
        spec = self._spec()
        with mock.patch("mokata.cli_commands.spec._run_scoped_store",
                        return_value=(object(), "r1", "")), \
                mock.patch("mokata.engine.spec_gate.read_emitted_spec",
                           return_value=(spec, False)), mock.patch.object(T, "_surface"):
            out = T.spec_show(response_format="detailed")
        self.assertEqual("src/limits.py", out["criteria"][0]["anchors"][0]["file"])
        self.assertNotIn("anchors", out["criteria"][1], "an anchorless AC is unchanged")
        self.assertIn("→ AC-1 (limits reject over max) → implement", out["block"])

    def test_develop_lines_report_stale_anchors_first_then_placement(self):
        with tempfile.TemporaryDirectory() as d:
            lines = SA.develop_anchor_lines(None, self._spec(), None, root=d)
            self.assertTrue(lines[0].startswith("- live-evidence: AC-1 anchor `src/limits.py:check`"
                                                " is stale (file not found)"))
            self.assertIn("- anchor: AC-1 (limits reject over max)", lines[-1])

    def test_the_cli_consent_identity_is_unchanged_for_an_anchorless_spec(self):
        from mokata.cli_commands.spec import _consent_criteria
        payload = {"title": "t", "criteria": [{"id": "AC-1", "text": "x"}]}
        spec, _ = spec_from_payload(payload)
        self.assertEqual([{"id": "AC-1", "text": "x"}], _consent_criteria(spec, payload))
        raw = [{"file": "a.py"}]
        payload = {"title": "t", "criteria": [{"id": "AC-1", "text": "x", "anchors": raw}]}
        spec, _ = spec_from_payload(payload)
        self.assertEqual([{"id": "AC-1", "text": "x", "anchors": raw}],
                         _consent_criteria(spec, payload))

    def test_cmd_spec_show_prints_the_line(self):
        from unittest import mock
        from mokata.cli_commands import spec as CS
        import types
        args = types.SimpleNamespace(path=".")
        with mock.patch.object(CS, "_load_surface"), \
                mock.patch.object(CS, "_run_scoped_store", return_value=(object(), "r1", "")), \
                mock.patch("mokata.engine.spec_gate.load_emitted_spec",
                           return_value=self._spec()):
            buf = io.StringIO()
            with redirect_stdout(buf):
                CS.cmd_spec_show(args)
        self.assertIn("  → AC-1 (limits reject over max) → implement `src/limits.py:check`",
                      buf.getvalue())


if __name__ == "__main__":
    unittest.main()
