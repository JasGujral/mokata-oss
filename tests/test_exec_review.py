"""E3 — two-stage review: spec-compliance, then code-quality."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.execmode import Task, TaskResult, two_stage_review


def ok_result():
    return TaskResult(task_id="t1", ok=True, summary="done", output="some code")


class TestTwoStageReview(unittest.TestCase):
    def test_two_stages_in_order(self):
        review = two_stage_review(Task("t1", "do x"), ok_result())
        self.assertEqual([s.name for s in review.stages],
                         ["spec-compliance", "code-quality"])

    def test_passes_when_both_stages_pass(self):
        review = two_stage_review(Task("t1", "do x"), ok_result())
        self.assertTrue(review.passed)

    def test_fails_when_spec_stage_fails(self):
        def reviewer(stage, task, result):
            return (False, "off-spec") if stage == "spec-compliance" else (True, "ok")
        review = two_stage_review(Task("t1", "do x"), ok_result(), reviewer=reviewer)
        self.assertFalse(review.passed)
        spec_stage = next(s for s in review.stages if s.name == "spec-compliance")
        self.assertFalse(spec_stage.passed)

    def test_empty_output_fails_quality(self):
        empty = TaskResult(task_id="t1", ok=True, summary="done", output="")
        review = two_stage_review(Task("t1", "do x"), empty)
        self.assertFalse(review.passed)


if __name__ == "__main__":
    unittest.main()
