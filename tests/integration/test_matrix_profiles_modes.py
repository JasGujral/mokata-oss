"""Stage 20 — the release-gate matrix.

The whole pipeline must hold on EVERY profile (minimal/standard/full) and BOTH execution
modes (sequential + parallel), including parallel's degrade-to-sequential when no subagent
runner is available. Each cell runs the real playbook on a fresh repo.

Run `python tests/integration/test_matrix_profiles_modes.py` to print the PASS/FAIL grid.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata.bootstrap import estimate_tokens
from mokata.config import Surface
from mokata.execmode import PARALLEL, SEQUENTIAL, ExecutionChoice, TaskResult
from mokata.init import init_repo
from mokata.playbook import run_playbook

PROFILES = ("minimal", "standard", "full")


def _silent(_):
    pass


class InlineRunner:
    """Stand-in subagent runner (the harness fulfils this in real use)."""

    def run(self, task):
        out = f"impl:{task.id}"
        return TaskResult(task.id, True, f"done {task.id}", output=out,
                          input_tokens=estimate_tokens(task.context + task.description),
                          output_tokens=estimate_tokens(out), seen_context=task.context)


def _init(d, profile):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


# (label, exec-choice factory, runner factory, expected `degraded`)
MODES = (
    ("sequential",
     lambda: ExecutionChoice(SEQUENTIAL), lambda: None, False),
    ("parallel",
     lambda: ExecutionChoice(PARALLEL, isolation=True, fanout=True),
     lambda: InlineRunner(), False),
    ("parallel(degraded)",
     lambda: ExecutionChoice(PARALLEL, isolation=True), lambda: None, True),
)


def _run_cell(profile, choice_factory, runner_factory):
    with tempfile.TemporaryDirectory() as d:
        return run_playbook(_init(d, profile), choice_factory(),
                            runner=runner_factory())


class TestMatrixProfilesAndModes(unittest.TestCase):
    def test_passes_on_every_profile_and_mode(self):
        for profile in PROFILES:
            for label, choice_f, runner_f, expect_degraded in MODES:
                with self.subTest(profile=profile, mode=label):
                    r = _run_cell(profile, choice_f, runner_f)
                    self.assertTrue(r.ok, f"{profile}/{label}: {r.checks}")
                    self.assertEqual(r.degraded, expect_degraded,
                                     f"{profile}/{label} degraded={r.degraded}")
                    if profile == "minimal":
                        # memory layer off on minimal — not required, still passes
                        self.assertFalse(r.checks["memory_enabled"])
                    else:
                        self.assertTrue(r.checks["memory_enabled"])
                        self.assertTrue(r.checks["memory_written"])


def _print_matrix():
    cols = [m[0] for m in MODES]
    width = max(len(c) for c in cols) + 2
    header = "profile".ljust(12) + "".join(c.ljust(width) for c in cols)
    print(header)
    print("-" * len(header))
    all_ok = True
    for profile in PROFILES:
        row = profile.ljust(12)
        for label, choice_f, runner_f, expect_degraded in MODES:
            r = _run_cell(profile, choice_f, runner_f)
            ok = r.ok and r.degraded == expect_degraded
            all_ok = all_ok and ok
            row += ("PASS" if ok else "FAIL").ljust(width)
        print(row)
    print("-" * len(header))
    print("MATRIX:", "PASS" if all_ok else "FAIL")
    return all_ok


if __name__ == "__main__":
    raise SystemExit(0 if _print_matrix() else 1)
