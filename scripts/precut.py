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

⚠ IT RUNS A DIFFERENT SET THAN THE SUITE DOES, so it can surface an ORDER-DEPENDENT test that
the full run happens to order safely. MEASURED on its first outing:
`test_hook_shell_agnostic.TestDoctorPerShellFinding` passes alone, passes beside its 15 touched
siblings, passes in the full suite, and REDS inside this tool's 57-module set. That is a real
defect in the tree — a test whose verdict depends on what ran before it is not grading what it
claims — and it is filed as `A-TEST-PASSES-OR-FAILS-BY-WHAT-RAN-BEFORE-IT`, not worked around
here. Until it is fixed, a pre-cut red in a module you did not touch is worth confirming alone.

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
import re
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

#: `_support` is the path shim, imported by 324 of the test modules, and its own corpus sites are
#: utilities (`posix_rel`) exercised by whichever module calls them. Attributing them to its
#: importers would make every pre-cut the full suite — the ten-minute door this tool exists to
#: avoid. DECLARED here with the reason (doc 85 §7j) rather than filtered by a size cutoff, and
#: `_HELPER_IMPORTER_NOTE` below makes the NEXT universal helper announce itself instead of
#: silently doubling the set.
UNIVERSAL_HELPERS = ("_support.py",)

#: Above this, a helper is behaving like `_support` and the run gets loud about it.
LOUD_HELPER_IMPORTERS = 30

#: ⛔ Modules that RUN A NESTED SUITE in a subprocess. Putting one inside this tool means this
#: tool contains a suite run, which IS the ten-minute door it exists to avoid: MEASURED,
#: `test_b1` alone is **90 s** — most of a pre-cut, for one module. They stay in the full suite,
#: which `release.sh` gates on, and are NAMED in the output rather than silently dropped.
#:
#: ⚠ DECLARED, not derived, and the derivation was TRIED and REJECTED with evidence: an AST rule
#: for *"a `subprocess.run` whose argv mentions unittest"* convicted `test_public_counts_guard`,
#: whose only mention is the re-derive command printed in a FAILURE MESSAGE. *A sentence about a
#: command is not a command* — stage 06 paid for that lesson once, in the detector that reddened on
#: the documentation of its own defect. A two-name list with its reason beats a rule that excludes
#: the guard this tool was widened to include (doc 85 §7j).
#:
#: The TRIPWIRE for this list going stale is not another list: it is `SLOW_RUN_BUDGET_S` below,
#: which measures the run it actually did.
NESTED_SUITE_MODULES = (
    "test_b1_internal_tests_meet_their_subject.py",     # runs the suite twice (dev vs mirror)
    "test_a30_the_suite_is_green_in_one_arrangement.py",  # runs it under a shuffled module order
)

#: Over this, say so. ⭐ A MEASUREMENT, not a limit — nothing is skipped to stay under it. The
#: tool's entire claim is "the fast half", and a tool whose claim has quietly stopped being true
#: is worse than no tool (§7e). MEASURED at the 0.0.20 cut: 44 modules / 104 s against the full
#: suite's 562 s.
SLOW_RUN_BUDGET_S = 240

_IMPORT_RE = r"(?m)^[ \t]*(?:import|from)[ \t]+%s(?![A-Za-z0-9_])"


def _test_module_names():
    return sorted(e for e in os.listdir(TESTS)
                  if e.startswith("test_") and e.endswith(".py"))


def _importers_of(helper_module):
    """The test modules that IMPORT a helper. A corpus site inside `_public_counts.py` belongs,
    for running purposes, to `test_public_counts_guard.py` — the helper is not a runnable module
    and `python -m unittest _public_counts` is an error, not a test."""
    stem = helper_module[:-3]
    pattern = re.compile(_IMPORT_RE % re.escape(stem))
    found = []
    for entry in _test_module_names():
        with open(os.path.join(TESTS, entry), encoding="utf-8", errors="replace") as fh:
            if pattern.search(fh.read()):
                found.append(entry)
    return found


def tree_wide_modules():
    """Every test module that asks a question about THIS tree, derived, never listed.

    TWO anchor buckets count, and the second one is why a RED TREE reached `master` at 0.0.21
    stage 11.

      * `REPO_ANCHORED` — the walk root traced back to `__file__` / `os.getcwd()`. Tree-wide by
        the classification's own definition.
      * `UNRESOLVED` — the trace DID NOT bottom out. `_corpus_sweep` surfaces that rather than
        guessing, which is right for a classifier; precut then read "not repo-anchored" as "not
        tree-wide", which is an instrument FAILING OPEN (§7e). `test_public_counts_guard` walks
        every shipped `.md` and `.html` in the tree and grades the public counts against the
        registries — through a walk root that arrives as a FUNCTION PARAMETER two module hops
        from its origin (`TAINT-STOPS-ONE-HOP-SHORT`, doc 84). So it landed in neither bucket,
        no pre-cut ever ran it, and CM.S5's 62nd MCP tool was committed with six public pages
        still saying 61. An UNKNOWN anchor is now counted IN: a pre-cut that runs a module it
        need not have is slower, and the other way round is what happened.

    A site in a HELPER is attributed by the sweep to the helper, which is not runnable, so helper
    sites resolve to the modules that import it (`_importers_of`)."""
    import _corpus_sweep as cs

    corpus = cs.read_corpus(TESTS, recursive=True)
    wide, helpers = set(), set()
    for site in cs.sweep(corpus):
        if site.anchor not in (cs.REPO_ANCHORED, cs.UNRESOLVED):
            continue
        if site.module.startswith("test_"):
            wide.add(site.module)
        else:
            helpers.add(site.module)
    for helper in sorted(helpers - set(UNIVERSAL_HELPERS)):
        importers = _importers_of(helper)
        if len(importers) > LOUD_HELPER_IMPORTERS:
            print("PRE-CUT — %s is imported by %d module(s): it is behaving like `_support`, and "
                  "this run is correspondingly large. If that is permanent, add it to "
                  "UNIVERSAL_HELPERS with the reason." % (helper, len(importers)),
                  file=sys.stderr)
        wide.update(importers)
    return sorted(wide - set(NESTED_SUITE_MODULES))


def _git(*args):
    out = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True,
                         text=True, check=False)
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def _report_skipped(paths):
    """Name every touched test module this tool is NOT going to run, and how to run it.

    It prints rather than returns because the alternative is the thing precut exists to prevent:
    a green that covers less than the author believes. See `touched_modules` for the defect that
    made this necessary."""
    if not paths:
        return
    print("PRE-CUT — %d touched test module(s) are NOT in this run (separate runner):"
          % len(paths), file=sys.stderr)
    for path in sorted(paths):
        print("  %s" % path, file=sys.stderr)
    # ⛔ THE REMEDY IS THE ONE THAT ACTUALLY RUNS. The first version printed
    # `-s tests/integration -t tests/integration`, which errors with
    # `ModuleNotFoundError: No module named '_provisioning'` for EVERY integration module, because
    # `-t` there puts `tests/` outside the import path and the integration suite imports helpers
    # from it. Found by the CM.S5 review (F10). A guard's remedy is part of the guard: a refusal
    # whose instruction does not work is a refusal nobody can act on, which is why
    # `test_a41`'s `EveryRefusalNamesARemedyThatEXISTS` grades this class of line.
    print("  run them with: (cd tests && PYTHONPATH=\"$PWD\" python3 -m unittest discover "
          "-s integration -t integration)   (live-DB legs also need MOKATA_LIVE_DB=1 + a DSN)",
          file=sys.stderr)


def touched_modules():
    """Test modules implicated by the working tree's own changes.

    Three sources, because a change that has not been `git add`ed is exactly the change most
    likely to be the one that breaks something: unstaged, staged, and untracked."""
    paths = set(_git("diff", "--name-only")) | set(_git("diff", "--cached", "--name-only"))
    paths |= set(_git("ls-files", "--others", "--exclude-standard"))
    modules, stems, elsewhere = set(), set(), set()
    for path in paths:
        name = os.path.basename(path)
        if path.startswith("tests/") and name.startswith("test_") and name.endswith(".py"):
            # ⛔ ONLY the modules directly under `tests/`, and the slash is the whole fix.
            # `os.path.basename` happily turned `tests/integration/test_cm_s5_live_db.py` into the
            # bare name `test_cm_s5_live_db`, which is NOT importable from `tests/` — so touching
            # an integration module made this tool report ERRORs for files it had collected and
            # could never run. Latent until 0.0.21 stage 11 touched two of them.
            #
            # ⭐ The integration suite has its OWN runner (`unittest discover -s tests/integration
            # -t tests/integration`), so the right answer is to leave it to that runner and SAY SO.
            # A silent skip would be the §7e failure this tool is otherwise built to avoid.
            if path.count("/") == 1:
                modules.add(name)
            else:
                elsewhere.add(path)
            continue
        # ⛔ NOT ONLY `.py`. The first version of this function looked at Python alone and reported
        # "0 touched" for a change to `.github/workflows/ci.yml` — a file several tests parse. The
        # tree's guards read YAML, Markdown and shell just as readily as source, so the token is
        # the FILE, by both its stem and its full name: `ci` finds a test that builds the path,
        # `ci.yml` finds one that names it outright.
        stems.add(name)                              # `ci.yml` — exact, and never ambiguous
        stem = os.path.splitext(name)[0]
        # ⛔ A SHORT STEM IS NOT A TOKEN. `ci.yml` -> `ci` matched a substring of `decision`,
        # `explicit`, `specific` … and pulled in 338 modules — the whole suite, which is precisely
        # the ten-minute door this tool exists to avoid. MEASURED the first time it ran. Long
        # stems only, matched on a word boundary.
        if len(stem) >= 4:
            stems.add(stem)
    stems.discard("")
    _report_skipped(elsewhere)
    # A changed `src/` module, a test HELPER (`_mutant_sweep.py`), a workflow or a tracker has no
    # test of its own name. Find its readers by the thing that cannot hide: they name it.
    if stems:
        for entry in sorted(os.listdir(TESTS)):
            if not (entry.startswith("test_") and entry.endswith(".py")):
                continue
            with open(os.path.join(TESTS, entry), encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            if any(re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(stem), body)
                   for stem in stems):
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

    print("PRE-CUT — %d module(s): %d tree-wide (repo-anchored AND unresolved corpus sites) + "
          "%d touched by the working tree." % (len(modules), len(wide), len(touched)))
    nested = [m for m in NESTED_SUITE_MODULES
              if os.path.exists(os.path.join(TESTS, m))]
    if nested:
        print("  NOT here (each runs a nested suite — %ss for test_b1 alone; the full suite runs "
              "them and `release.sh` gates on it): %s"
              % (90, ", ".join(m[:-3] for m in nested)))
    if touched:
        print("  touched: " + ", ".join(m[:-3] for m in touched))
    sys.stdout.flush()

    proc = subprocess.run([sys.executable, "-m", "unittest"] + names + argv,
                          cwd=TESTS, check=False)
    elapsed = time.time() - started
    print("\nPRE-CUT finished in %.1fs over %d module(s)." % (elapsed, len(modules)))
    if elapsed > SLOW_RUN_BUDGET_S:
        print("⚠ THAT IS OVER THIS TOOL'S %ds BUDGET, so it is no longer clearly the fast half. "
              "Read the module list above: either the touched set pulled in a nested-suite module "
              "through a short stem, or a tree-wide module has become expensive and belongs in "
              "NESTED_SUITE_MODULES with its measurement." % SLOW_RUN_BUDGET_S, file=sys.stderr)
    print("⛔ THIS IS NOT THE RELEASE GATE. It ran a derived SUBSET: the tree-wide rules and the "
          "modules you touched. `scripts/release.sh` still decides whether a release may be cut, "
          "and only the full suite — both jsonschema legs — can say the tree is green.")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
