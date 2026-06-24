"""E7 — plan/dry-run preview: list the pipeline's planned actions, gates, and file
touches WITHOUT executing anything (zero side effects)."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import PIPELINE_PHASES
from mokata.engine import preview_pipeline


class TestPreview(unittest.TestCase):
    def test_preview_lists_every_phase_with_action_and_gate(self):
        pv = preview_pipeline()
        self.assertEqual([i.phase for i in pv.items], list(PIPELINE_PHASES))
        for item in pv.items:
            self.assertTrue(item.action.strip())
        # gated phases carry their gate id
        gate_ids = {i.gate_id for i in pv.items if i.gate_id}
        self.assertIn("completeness", gate_ids)
        self.assertIn("approach-approval", gate_ids)

    def test_preview_lists_emit_file_touches(self):
        pv = preview_pipeline(mokata_dir="/repo/.mokata")
        emit = next(i for i in pv.items if i.phase == "emit")
        self.assertTrue(emit.file_touches)
        self.assertTrue(any("emitted_spec" in t for t in emit.file_touches))

    def test_preview_has_zero_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            before = set(os.listdir(d))
            preview_pipeline(mokata_dir=os.path.join(d, ".mokata"))
            after = set(os.listdir(d))
            self.assertEqual(before, after)        # nothing created/written

    def test_preview_can_be_sliced(self):
        pv = preview_pipeline(start="pre_mortem", stop="completeness_gate")
        self.assertEqual([i.phase for i in pv.items],
                         ["pre_mortem", "probes", "completeness_gate"])

    def test_render_is_readable(self):
        text = preview_pipeline().render()
        self.assertIn("emit", text)
        self.assertIn("completeness", text)


if __name__ == "__main__":
    unittest.main()
