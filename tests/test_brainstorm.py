"""D6 — brainstorm phase: Socratic flow, 2–3 approaches, design write-up, and the
HARD-GATE that blocks the spec until an approach is explicitly approved."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import (
    PIPELINE_PHASES,
    Approach,
    BrainstormError,
    BrainstormGateError,
    BrainstormSession,
)


def two_approaches():
    return [
        Approach("cache-aside", "App reads cache, falls back to DB.",
                 pros=["simple", "no write path change"],
                 cons=["stale reads possible"]),
        Approach("write-through", "Writes go through the cache.",
                 pros=["fresh reads"],
                 cons=["slower writes", "more moving parts"]),
    ]


class TestPipelinePosition(unittest.TestCase):
    def test_brainstorm_is_the_front_phase(self):
        self.assertEqual(PIPELINE_PHASES[0], "brainstorm")
        # the next phase the handoff feeds is the strawman/analysis side
        self.assertIn("strawman", PIPELINE_PHASES)


class TestOneQuestionAtATime(unittest.TestCase):
    def setUp(self):
        self.s = BrainstormSession("add a caching layer")

    def test_cannot_ask_two_questions_without_an_answer(self):
        self.s.ask("What's the read/write ratio?")
        with self.assertRaises(BrainstormError):
            self.s.ask("What's the consistency requirement?")

    def test_answer_then_ask_again_is_allowed(self):
        self.s.ask("What's the read/write ratio?")
        self.s.answer("read-heavy")
        self.s.ask("What's the consistency requirement?")  # no raise
        self.assertEqual(len(self.s.questions), 2)

    def test_answer_with_no_pending_question_raises(self):
        with self.assertRaises(BrainstormError):
            self.s.answer("nothing was asked")

    def test_answered_questions_are_tracked(self):
        self.s.ask("Q1")
        self.s.answer("A1")
        self.assertEqual(
            [(q.text, q.answer) for q in self.s.answered_questions],
            [("Q1", "A1")],
        )


class TestApproaches(unittest.TestCase):
    def setUp(self):
        self.s = BrainstormSession("add a caching layer")

    def test_requires_at_least_two_approaches(self):
        with self.assertRaises(BrainstormError):
            self.s.propose_approaches([two_approaches()[0]])

    def test_rejects_more_than_three_approaches(self):
        four = two_approaches() + [
            Approach("c", "third", pros=["p"], cons=["c"]),
            Approach("d", "fourth", pros=["p"], cons=["c"]),
        ]
        with self.assertRaises(BrainstormError):
            self.s.propose_approaches(four)

    def test_each_approach_needs_a_real_tradeoff(self):
        bad = [
            Approach("only-pros", "no downside stated", pros=["great"], cons=[]),
            two_approaches()[1],
        ]
        with self.assertRaises(BrainstormError):
            self.s.propose_approaches(bad)

    def test_accepts_two_or_three_with_tradeoffs(self):
        self.s.propose_approaches(two_approaches())
        self.assertEqual(len(self.s.approaches), 2)


class TestDesignWriteup(unittest.TestCase):
    def test_writeup_contains_topic_approaches_and_tradeoffs(self):
        s = BrainstormSession("add a caching layer")
        s.ask("read/write ratio?")
        s.answer("read-heavy")
        s.propose_approaches(two_approaches())
        text = s.design_writeup()
        self.assertIn("add a caching layer", text)
        self.assertIn("cache-aside", text)
        self.assertIn("write-through", text)
        self.assertIn("stale reads possible", text)   # a con surfaces
        self.assertIn("read-heavy", text)             # the answered Q informs it


class TestHardGate(unittest.TestCase):
    def setUp(self):
        self.s = BrainstormSession("add a caching layer")
        self.s.propose_approaches(two_approaches())

    def test_handoff_blocked_before_approval(self):
        self.assertFalse(self.s.can_emit_spec)
        with self.assertRaises(BrainstormGateError):
            self.s.handoff()

    def test_cannot_approve_without_choosing_a_real_approach(self):
        with self.assertRaises(BrainstormError):
            self.s.approve("jas", "no-such-approach")

    def test_cannot_approve_before_any_approaches_exist(self):
        fresh = BrainstormSession("greenfield")
        with self.assertRaises(BrainstormGateError):
            fresh.approve("jas", "anything")

    def test_approval_opens_the_gate_and_handoff_carries_the_choice(self):
        self.s.approve("jas", "write-through")
        self.assertTrue(self.s.can_emit_spec)
        h = self.s.handoff()
        self.assertEqual(h.approach.name, "write-through")
        self.assertEqual(h.topic, "add a caching layer")
        self.assertEqual(h.approver, "jas")
        self.assertTrue(h.approved_at)  # recorded for audit

    def test_handoff_feeds_next_phase_with_answered_questions(self):
        self.s.ask("consistency need?")
        self.s.answer("eventual is fine")
        self.s.approve("jas", "cache-aside")
        h = self.s.handoff()
        self.assertIn(
            ("consistency need?", "eventual is fine"),
            [(q.text, q.answer) for q in h.answered_questions],
        )


if __name__ == "__main__":
    unittest.main()
