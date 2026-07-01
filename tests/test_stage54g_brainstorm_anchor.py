"""Stage 54g — Brainstorm anti-drift: the long-session context anchor.

Proves the immutable anchor + the bounded running synthesis + the `build_anchor_brief`
re-surfacing helper, that both round-trip through to_dict/from_dict and save/restore, that
the HARD-GATE still holds after restore, and that the regenerated protocol/template carry the
anchor + drift-check language. Degrade-clean throughout.
"""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import (
    MAX_SYNTHESIS_ITEMS,
    MAX_SYNTHESIS_LINE,
    Approach,
    BRAINSTORM_PROTOCOL,
    BrainstormGateError,
    BrainstormSession,
    Synthesis,
    build_anchor_brief,
    restore_brainstorm_progress,
    save_brainstorm_progress,
)
from mokata.state import StateStore


def _store():
    d = tempfile.mkdtemp()
    return StateStore(os.path.join(d, "state"))


# --------------------------------------------------------------- the immutable anchor
class TestImmutableAnchor(unittest.TestCase):
    def test_anchor_set_once_at_start(self):
        s = BrainstormSession("caching", anchor="make reads fast under heavy traffic")
        self.assertEqual(s.anchor, "make reads fast under heavy traffic")

    def test_anchor_settable_via_set_anchor_when_absent(self):
        s = BrainstormSession("caching")
        self.assertIsNone(s.anchor)
        s.set_anchor("make reads fast")
        self.assertEqual(s.anchor, "make reads fast")

    def test_later_turns_cannot_overwrite_the_anchor(self):
        s = BrainstormSession("caching", anchor="make reads fast")
        s.set_anchor("something totally different")  # a later turn tries to rewrite it
        self.assertEqual(s.anchor, "make reads fast")  # original wins — immutable

    def test_anchor_has_no_setter(self):
        s = BrainstormSession("caching", anchor="original ask")
        with self.assertRaises(AttributeError):
            s.anchor = "mutated"  # type: ignore[misc]

    def test_anchor_immutable_across_save_restore(self):
        store = _store()
        s = BrainstormSession("caching", anchor="make reads fast")
        save_brainstorm_progress(s, store)
        r = restore_brainstorm_progress(store)
        self.assertEqual(r.anchor, "make reads fast")
        r.set_anchor("rewrite after restore")  # restore must not reopen the anchor
        self.assertEqual(r.anchor, "make reads fast")


# ---------------------------------------------------------- the bounded synthesis
class TestBoundedSynthesis(unittest.TestCase):
    def test_synthesis_updates_each_turn(self):
        s = BrainstormSession("caching", anchor="fast reads")
        s.update_synthesis(goal="cut p99 read latency")
        self.assertEqual(s.synthesis.goal, "cut p99 read latency")
        s.update_synthesis(open_question="which keys are hot?")
        # earlier fields are preserved when not passed
        self.assertEqual(s.synthesis.goal, "cut p99 read latency")
        self.assertEqual(s.synthesis.open_question, "which keys are hot?")

    def test_synthesis_caps_list_length(self):
        s = BrainstormSession("caching", anchor="fast reads")
        s.update_synthesis(constraints=[f"c{i}" for i in range(50)],
                           approaches=[f"a{i}" for i in range(50)])
        self.assertLessEqual(len(s.synthesis.constraints), MAX_SYNTHESIS_ITEMS)
        self.assertLessEqual(len(s.synthesis.approaches), MAX_SYNTHESIS_ITEMS)

    def test_synthesis_clips_line_length(self):
        s = BrainstormSession("caching", anchor="fast reads")
        s.update_synthesis(goal="x" * 5000)
        self.assertLessEqual(len(s.synthesis.goal), MAX_SYNTHESIS_LINE)

    def test_synthesis_from_dict_reclamps_oversized_payload(self):
        # a hand-crafted/oversized dict must still come back bounded — no transcript dump
        syn = Synthesis.from_dict({
            "goal": "y" * 5000,
            "constraints": [f"c{i}" for i in range(99)],
            "approaches": [f"a{i}" for i in range(99)],
            "open_question": "z" * 5000,
        })
        self.assertLessEqual(len(syn.goal), MAX_SYNTHESIS_LINE)
        self.assertLessEqual(len(syn.open_question), MAX_SYNTHESIS_LINE)
        self.assertLessEqual(len(syn.constraints), MAX_SYNTHESIS_ITEMS)
        self.assertLessEqual(len(syn.approaches), MAX_SYNTHESIS_ITEMS)

    def test_empty_synthesis_is_empty(self):
        self.assertTrue(Synthesis().is_empty)
        self.assertFalse(Synthesis(goal="x").is_empty)


# ----------------------------------------------------------- build_anchor_brief
class TestAnchorBrief(unittest.TestCase):
    def test_anchor_only_when_no_synthesis(self):
        s = BrainstormSession("caching", anchor="make reads fast")
        brief = build_anchor_brief(s)
        self.assertIn("make reads fast", brief)
        self.assertIn("Drift-check", brief)
        # degrade-clean: no synthesis lines rendered
        self.assertNotIn("Goal:", brief)
        self.assertNotIn("Decided so far:", brief)

    def test_brief_falls_back_to_topic_without_anchor(self):
        s = BrainstormSession("the caching topic")  # no explicit anchor
        brief = build_anchor_brief(s)
        self.assertIn("the caching topic", brief)
        self.assertIn("Drift-check", brief)

    def test_brief_renders_synthesis_when_present(self):
        s = BrainstormSession("caching", anchor="make reads fast")
        s.update_synthesis(goal="cut p99", constraints=["read-heavy"],
                           approaches=["cache-aside", "write-through"],
                           open_question="hot keys?")
        brief = build_anchor_brief(s)
        for needle in ("cut p99", "read-heavy", "cache-aside", "write-through", "hot keys?"):
            self.assertIn(needle, brief)

    def test_brief_is_deterministic(self):
        s = BrainstormSession("caching", anchor="make reads fast")
        s.update_synthesis(goal="cut p99")
        self.assertEqual(build_anchor_brief(s), build_anchor_brief(s))

    def test_brief_is_bounded(self):
        # even with maximal, oversized inputs the brief stays compact (no transcript dump)
        s = BrainstormSession("caching", anchor="A" * 5000)
        s.update_synthesis(goal="g" * 5000,
                           constraints=[f"c{i}" * 500 for i in range(99)],
                           approaches=[f"a{i}" * 500 for i in range(99)],
                           open_question="q" * 5000)
        brief = build_anchor_brief(s)
        # a handful of bounded lines — bounded well under a transcript's size
        self.assertLess(len(brief), 4000)
        self.assertLessEqual(len(brief.splitlines()), 8)


# ----------------------------------------------------------- round-trip + HARD-GATE
class TestRoundTripAndGate(unittest.TestCase):
    def _wip(self):
        s = BrainstormSession("caching", anchor="make reads fast")
        s.ask("read/write ratio?")
        s.answer("read-heavy")
        s.update_synthesis(goal="cut p99", constraints=["read-heavy"],
                           approaches=["cache-aside"], open_question="hot keys?")
        s.propose_approaches([
            Approach("cache-aside", "read-through", pros=["simple"], cons=["stale"]),
            Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow"]),
        ])
        return s

    def test_anchor_and_synthesis_round_trip_to_dict(self):
        s = self._wip()
        r = BrainstormSession.from_dict(s.to_dict())
        self.assertEqual(r.anchor, "make reads fast")
        self.assertEqual(r.synthesis.goal, "cut p99")
        self.assertEqual(r.synthesis.approaches, ["cache-aside"])
        self.assertEqual(r.synthesis.open_question, "hot keys?")

    def test_anchor_and_synthesis_round_trip_save_restore(self):
        store = _store()
        s = self._wip()
        save_brainstorm_progress(s, store)
        r = restore_brainstorm_progress(store)
        self.assertEqual(r.anchor, "make reads fast")
        self.assertEqual(r.synthesis.goal, "cut p99")

    def test_hard_gate_holds_after_restore(self):
        store = _store()
        save_brainstorm_progress(self._wip(), store)
        r = restore_brainstorm_progress(store)
        self.assertFalse(r.can_emit_spec)
        with self.assertRaises(BrainstormGateError):
            r.handoff()

    def test_degrade_clean_old_dict_without_anchor_or_synthesis(self):
        # a Stage-50-era saved session (no anchor/synthesis keys) must restore cleanly
        old = {"topic": "legacy", "approaches": [], "approved": False}
        r = BrainstormSession.from_dict(old)
        self.assertIsNone(r.anchor)
        self.assertIsNone(r.synthesis)
        # the brief still works (falls back to topic)
        self.assertIn("legacy", build_anchor_brief(r))


# --------------------------------------------- the protocol + template carry the language
class TestProtocolAndTemplate(unittest.TestCase):
    def test_protocol_carries_anchor_and_drift_language(self):
        p = BRAINSTORM_PROTOCOL.lower()
        self.assertIn("anchor", p)
        self.assertIn("immutable", p)
        self.assertIn("drift-check", p)
        self.assertIn("synthesis", p)

    def test_template_carries_anchor_and_drift_language(self):
        here = os.path.dirname(__file__)
        path = os.path.join(here, "..", "templates", "commands", "brainstorm.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read().lower()
        self.assertIn("anchor", text)
        self.assertIn("immutable", text)
        self.assertIn("drift-check", text)
        self.assertIn("synthesis", text)


if __name__ == "__main__":
    unittest.main()
