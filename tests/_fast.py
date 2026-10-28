"""The builder's inner-loop run: every test module EXCEPT the release-audit tier.

Why this exists. The full suite is ~83 min on one process, and ~50 min of it is sixteen modules that
grade the TREE (whole-corpus sweeps, mutant/stage bookkeeping, reachability verdicts, perf budgets,
the retrieval-quality benchmark) rather than a behaviour a change under development can break in an
afternoon. A builder iterating on one lane paid that cost on every run.

Nothing is skipped for the release: CI, `scripts/shard_tests.py` and every checkpoint run the whole
suite (`python -m unittest discover -s . -t .`). This runner only narrows the INNER loop.

    cd tests && PYTHONPATH=../src:. python _fast.py            # fast tier
    cd tests && PYTHONPATH=../src:. python _fast.py --audit    # the audit tier alone

A name in RELEASE_AUDIT that no longer exists is an error, so the list cannot rot silently.
"""

from __future__ import annotations

import os
import sys
import unittest

RELEASE_AUDIT = frozenset({
    "test_db_s8d_quality",                      # retrieval-quality benchmark, ~640 s
    "test_gate_reachability",                   # per-gate production reachability, ~630 s
    "test_d1_gate_unreachable",                 # ~370 s
    "test_windows_shell_and_paths",             # whole-tree Windows-class sweep, ~320 s
    "test_hb_hook_budget",                      # hook latency budget (perf, load-sensitive)
    "test_b1_internal_tests_meet_their_subject",
    "test_b5_disclosure_must_resolve",
    "test_a36_no_test_reaches_a_prompt_on_the_ambient_stdin",
    "test_a9_mutant_batches_are_swept",
    "test_stage3_corpus_walk",
    "test_ms_s7_stress",
    "test_se_s1_skill_evals_gate",
    "test_mutation_harness",
    "test_d5_sweep_register",
    "test_repo_paths_invariant",
    "test_b_ver_version_parity",
})

HERE = os.path.dirname(os.path.abspath(__file__))


def modules(audit: bool) -> list:
    # CORPUS: THE WORKING TREE — the test modules on disk here, whatever git tracks.
    present = {f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py")}
    missing = sorted(RELEASE_AUDIT - present)
    if missing:
        raise SystemExit("tests/_fast.py: RELEASE_AUDIT names modules that do not exist: "
                         + ", ".join(missing))
    return sorted(RELEASE_AUDIT if audit else present - RELEASE_AUDIT)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    names = modules("--audit" in argv)
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    print("running %d modules (%s tier)" % (len(names), "audit" if "--audit" in argv else "fast"),
          flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
