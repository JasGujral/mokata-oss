#!/usr/bin/env python3
"""PRE-CUT — the two-minute half of the release gate, run BEFORE the ten-minute half.

WHY THIS EXISTS
---------------
`release.sh`'s first real gate is the whole unit suite: **562 seconds, measured at the 0.0.20
cut**, and it is also the first thing that tells you a new test file broke a rule. That ordering
is what turned this release into a loop. The defects that cost the most were never deep — three of
them in a row were a NEW test file tripping a guard that sweeps the whole tree:

  * `test_shim_declaration` reads every module under `tests/`;
  * `test_secret_value_scan` reads every literal in the tree;
  * `test_stage3_corpus_walk.TestTheRealTree` reads every corpus site in `tests/`.

Every file anyone adds is downstream of all of them, and each miss costs a ten-minute round trip
(or, once pushed, a thirty-five-minute one through Windows CI).

MEASURED, on the dev VM at the 0.0.20 cut: **44 modules, 1138 tests, 104s** — against the full
suite's **7961 tests, 562s**. Not seconds, and the docstring said "seconds" until it was run: 5.4x
is the honest number and it is the one worth having, because it is the WHOLE tree-wide class plus
whatever you just touched. Add `-f` to stop at the first failure when you are iterating.

⚠ IT RUNS IN THIS SHELL'S ENVIRONMENT, so it inherits that environment's own reds. The dev VM,
for one, has no `claude` console script and no WAL-capable mount, and reports four failures in
`test_a2_phase_gate_is_real` that CI does not. Read a pre-cut red as "look at this", never as
"the tree is broken" — and never the other way round either.

⛔ **THIS IS NOT THE GATE, AND IT MUST NEVER BE MISTAKEN FOR ONE.** It runs a derived SUBSET. A
green pre-cut says only "the tree-wide rules still hold and the modules you touched still pass";
`release.sh` remains the thing that decides whether a release may be cut. Printing that sentence at
the end is part of the tool (§7f: a check that reaches less than it appears to is worse than none).

HOW THE GUARD SET IS DERIVED — AND WHY IT IS NOT A LIST
-------------------------------------------------------
⭐ DERIVE COUNTS, DO NOT TYPE THEM. A hand-maintained list of "the tree-wide tests" would be stale
the first time somebody adds one, and stale in the silent direction: the tool would keep passing
while covering less. So the set is derived from `_corpus_sweep`, which already classifies every
corpus read in `tests/` by what it is ANCHORED to. A module holding at least one `REPO_ANCHORED`
site is, by that classification's own definition, a module whose question is about THIS tree — so
it is downstream of every file in it. That is the definition of the set this tool needs, and it is
already computed and already tested (`test_stage3_corpus_walk`).

The derivation refuses below a floor, because a derivation that silently collapses to two modules
would report a fast green while grading almost nothing — the exact shape the corpus sweep itself
exists to close.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO, "tests")
sys.path.insert(0, TESTS)

#: The derivation must not collapse. MEASURED at the 0.0.20 cut: 30 modules carry a repo-anchored
#: corpus site. A floor well under that catches a broken derivation without failing on ordinary
#: churn; it is a tripwire, not a target.
MIN_TREE_WIDE_MODULES = 12


def tree_wide_modules():
    """Every test module that asks a question about THIS tree, derived, never listed."""
    import _corpus_sweep as cs

    corpus = cs.read_corpus(TESTS, recursive=True)
    return sorted({site.module for site in cs.sweep(corpus)
                   if site.anchor == cs.REPO_ANCHORED})


def _git(*args):
    out = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True,
                         text=True, check=False)
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def touched_modules():
    """Test modules implicated by the working tree's own changes.

    Three sources, because a change that has not been `git add`ed is exactly the change most
    likely to be the one that breaks something: unstaged, staged, and untracked."""
    paths = set(_git("diff", "--name-only")) | set(_git("diff", "--cached", "--name-only"))
    paths |= set(_git("ls-files", "--others", "--exclude-standard"))
    modules, stems = set(), set()
    for path in paths:
        name = os.path.basename(path)
        if path.startswith("tests/") and name.startswith("test_") and name.endswith(".py"):
            modules.add(name)
        elif path.endswith(".py"):
            stems.add(os.path.splitext(name)[0])
    # A changed `src/` module or test HELPER (`_mutant_sweep.py`) has no test of its own name.
    # Find its readers by the one thing that cannot lie about a Python import: the importers.
    if stems:
        for entry in sorted(os.listdir(TESTS)):
            if not (entry.startswith("test_") and entry.endswith(".py")):
                continue
            with open(os.path.join(TESTS, entry), encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            if any(stem in body for stem in stems):
                modules.add(entry)
    return sorted(modules)


def main(argv):
    started = time.time()
    wide = tree_wide_modules()
    if len(wide) < MIN_TREE_WIDE_MODULES:
        print("REFUSING: the tree-wide derivation returned %d module(s), under the floor of %d. "
              "A pre-cut that grades almost nothing reports a fast green for the same reason a "
              "working one does — fix the derivation before trusting this tool."
              % (len(wide), MIN_TREE_WIDE_MODULES), file=sys.stderr)
        return 2
    touched = touched_modules()
    modules = sorted(set(wide) | set(touched))
    names = [m[:-3] for m in modules]

    print("PRE-CUT — %d module(s): %d tree-wide (derived from repo-anchored corpus sites) + "
          "%d touched by the working tree." % (len(modules), len(wide), len(touched)))
    if touched:
        print("  touched: " + ", ".join(m[:-3] for m in touched))
    sys.stdout.flush()

    proc = subprocess.run([sys.executable, "-m", "unittest"] + names + argv,
                          cwd=TESTS, check=False)
    elapsed = time.time() - started
    print("\nPRE-CUT finished in %.1fs over %d module(s)." % (elapsed, len(modules)))
    print("⛔ THIS IS NOT THE RELEASE GATE. It ran a derived SUBSET: the tree-wide rules and the "
          "modules you touched. `scripts/release.sh` still decides whether a release may be cut, "
          "and only the full suite — both jsonschema legs — can say the tree is green.")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
