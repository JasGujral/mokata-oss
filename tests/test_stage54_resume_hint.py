"""Stage 54 — proactive resume surfacing in the SessionStart briefing, on ONE derivation (0.0.28 F).

When you reopen a repo with a run mid-flight, the briefing says what that run owes next in ONE
line — the primary hint of `hints.derive` (what `mokata suggest` says), its command, for its run.
Nothing is said when there is nothing owed (no run, a finished run); an unreadable store is said as
unknown. The line fits the briefing budget and building it writes nothing.

Rewritten, not shimmed, by 0.0.28 F: the old two hand-written lines (`Resume: pipeline at …`,
`Resume: in-progress brainstorm …`) were a second derivation that could name a different command
than `mokata suggest` on the same state. They are gone.
"""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import hints as H
from mokata.bootstrap import BOOTSTRAP_TOKEN_BUDGET, build_bootstrap
from mokata.config import Surface
from mokata.govern import PipelineCheckpoint


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _state_snapshot(root):
    out = {}
    for dp, _dn, fns in os.walk(os.path.join(root, ".mokata")):
        for fn in fns:
            if fn.endswith("-shm"):         # SQLite's shared-memory index: every reader maps it
                continue
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                out[p] = fh.read()
    return out


class TestResumeHint(unittest.TestCase):

    def test_a_run_mid_flight_is_named_with_the_command_suggest_gives(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").ensure_registered()
            want = H.primary(H.derive(d))["command"]
            text = build_bootstrap(surface).text
            self.assertIn("Next step for run run-a", text)
            self.assertIn(f"`{want}`", text)
            self.assertNotIn("Resume:", text)

    def test_nothing_owed_says_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            self.assertNotIn("Next step", build_bootstrap(surface).text)

    def test_a_finished_run_says_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            cp = PipelineCheckpoint(surface.state, "run-c")
            cp.ensure_registered()
            surface.state.update("pipeline_run__run-c",
                                 lambda cur: dict(cur, completed_at="2026-11-01T00:00:00Z"),
                                 default={})
            self.assertEqual("none", H.derive(d)["status"])
            self.assertNotIn("Next step", build_bootstrap(surface).text)

    def test_building_the_line_writes_nothing(self):
        # the line's derivation is a pure read; the rest of the briefing (the session registry, the
        # memory store) is not this line's and is not measured here
        from mokata.bootstrap import resume_line
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            surface = _repo(d, profile="full")
            PipelineCheckpoint(surface.state, "run-z").ensure_registered()
            before = _state_snapshot(d)
            first = resume_line(H.derive(d, rules=H.PIPELINE_ONLY))
            second = resume_line(H.derive(d, rules=H.PIPELINE_ONLY))
            self.assertEqual(before, _state_snapshot(d))
            self.assertEqual(first, second)
            strip = lambda t: [ln for ln in t.splitlines() if "Next step" in ln]  # noqa: E731
            self.assertEqual([first], strip(build_bootstrap(surface).text))

    def test_an_unreadable_store_is_said_as_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-u").ensure_registered()
            path = surface.state.path("pipeline_run__run-u")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ not valid json")
            text = build_bootstrap(surface).text
            self.assertIn("mokata: next step unknown — ", text)
            self.assertTrue(build_bootstrap(surface).within_budget)

    def test_briefing_with_the_line_stays_within_budget(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            PipelineCheckpoint(surface.state, "run-a").ensure_registered()
            result = build_bootstrap(surface)
            self.assertTrue(result.within_budget)
            self.assertLessEqual(result.token_estimate, BOOTSTRAP_TOKEN_BUDGET)
            self.assertIn("Next step for run run-a", result.text)


if __name__ == "__main__":
    unittest.main()
