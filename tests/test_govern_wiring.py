"""Stage 41 — token-governance WIRING (F4 output-density + F6 cache-prefix).

These modules are unit-tested in `test_govern_governance.py`; this file proves they are
actually CALLED at runtime (the "dark code" gap 0.1.0 closes):

  - F4: OutputDensity.compress runs on the sub-agent handback path (orchestrator), gated
        OFF by default, reducing the handback the parent absorbs without altering content.
  - F6: stable_prefix_for(surface) is emitted AHEAD of the variable content in the
        SessionStart briefing, byte-stable across assemblies so the prompt cache hits.

Both degrade clean (no toggle / nothing to compress / no constitution -> pass-through).
"""

import unittest

from _support import sample_manifest_data

from mokata.bootstrap import build_bootstrap, estimate_tokens
from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.execmode import PARALLEL, ExecutionChoice, Task, TaskResult, run_tasks
from mokata.govern import OutputDensity, density_enabled, stable_prefix_for
from mokata.manifest import Manifest
from mokata.playbook import run_playbook


# A handback with compressible noise: blank-line runs, duplicate lines, trailing space.
# compress_output collapses these without dropping any content line.
VERBOSE_HANDBACK = "alpha\n\n\n\nbeta\nbeta\nbeta\n   \n\ngamma   \ngamma   \n"
_CONTENT_LINES = ["alpha", "beta", "gamma"]


def make_surface(constitution_text="# c\n## Article 1 — x\n## Article 2 — y\n",
                 overrides=None):
    manifest = Manifest.from_dict(sample_manifest_data())
    constitution = Constitution(text=constitution_text, path="<mem>")
    return Surface(manifest, constitution, root=".",
                   detector=Detector(overrides=overrides or {}))


class _Runner:
    """A subagent runner whose handback is the given (verbose) text."""

    def __init__(self, text):
        self.text = text

    def run(self, task):
        return TaskResult(task.id, True, self.text, output=self.text,
                          input_tokens=1, output_tokens=estimate_tokens(self.text),
                          seen_context=task.context)


def _run_handback(density):
    return run_tasks([Task("a", "implement a", context="ctx")],
                     ExecutionChoice(PARALLEL, isolation=True),
                     runner=_Runner(VERBOSE_HANDBACK), density=density)


# --- F4: output-density is WIRED into the handback path -------------------------
class TestHandbackDensityWiring(unittest.TestCase):
    def test_density_off_by_default_is_passthrough(self):
        # No density argument -> the parent absorbs the handback unchanged.
        summary = _run_handback(None).results[0].summary
        self.assertEqual(summary, VERBOSE_HANDBACK)

    def test_disabled_density_is_passthrough(self):
        summary = _run_handback(OutputDensity(False)).results[0].summary
        self.assertEqual(summary, VERBOSE_HANDBACK)

    def test_density_on_reduces_handback_tokens(self):
        off = _run_handback(None).results[0].summary
        on = _run_handback(OutputDensity(True)).results[0].summary
        self.assertLess(estimate_tokens(on), estimate_tokens(off))

    def test_density_preserves_content_semantics(self):
        # Only density changes — every content line survives, just denser.
        on = _run_handback(OutputDensity(True)).results[0].summary
        self.assertEqual([ln.strip() for ln in on.splitlines() if ln.strip()],
                         _CONTENT_LINES)


# --- F4: the --dense surface (run_playbook) ------------------------------------
class TestPlaybookDenseSurface(unittest.TestCase):
    def test_default_resolves_to_manifest_toggle_off(self):
        # Off by default: the sample manifest does not enable output_density.
        self.assertFalse(density_enabled(make_surface().manifest))

    def test_dense_flag_runs_clean(self):
        # The --dense surface threads through and degrades clean (no harness -> sequential).
        res = run_playbook(make_surface(), dense=True)
        self.assertTrue(res.ok)

    def test_default_runs_clean(self):
        res = run_playbook(make_surface())
        self.assertTrue(res.ok)


# --- F6: stable cache prefix is WIRED into the briefing ahead of variable -------
class TestBriefingCachePrefix(unittest.TestCase):
    def test_prefix_is_emitted_ahead_of_variable_content(self):
        surface = make_surface()
        text = build_bootstrap(surface).text
        prefix = stable_prefix_for(surface).text()
        self.assertTrue(text.startswith(prefix))
        # the live/variable section ("resolved now") sits AFTER the stable prefix
        self.assertIn("resolved now", text[len(prefix):])

    def test_prefix_is_byte_identical_across_assemblies(self):
        surface = make_surface()
        a = build_bootstrap(surface).text
        b = build_bootstrap(surface).text
        prefix = stable_prefix_for(surface).text()
        self.assertEqual(a[:len(prefix)], prefix)
        self.assertEqual(a[:len(prefix)], b[:len(prefix)])

    def test_degrades_clean_without_constitution(self):
        surface = make_surface(constitution_text="")
        text = build_bootstrap(surface).text       # must not raise
        self.assertTrue(text.startswith(stable_prefix_for(surface).text()))

    def test_briefing_stays_within_budget(self):
        self.assertTrue(build_bootstrap(make_surface()).within_budget)


if __name__ == "__main__":
    unittest.main()
