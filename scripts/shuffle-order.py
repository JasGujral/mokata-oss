#!/usr/bin/env python3
"""SHUFFLE-ORDER — run the suite in a SEEDED random module order, and print the seed.

WHY THIS EXISTS
---------------
`A-TEST-PASSES-OR-FAILS-BY-WHAT-RAN-BEFORE-IT` (doc 84) was filed after `scripts/precut.py` found
one test that passed alone and reddened inside a 57-module set. 0.0.21 stage 04 fixed that instance
— and the instance was never the finding. **The finding is what the GREEN is worth.**

The full suite runs in alphabetical discovery order. Nothing chose that order and nothing pins it, so
a suite that is green is green *in one arrangement*, and the arrangement changes when somebody renames
a module. Every cross-module residue in this tree — process-global state a test leaves behind and
another reads — is invisible until the order moves. This tool moves it, on purpose, reproducibly.

⛔ **THIS IS A FINDING TOOL, NOT A GATE, AND THE DISTINCTION IS DELIBERATE.** A leg that FAILED the
build on any order would be red on day one: this tree has 7961 tests written under one arrangement,
and turning that into a blocking check would either be switched off in a week or would block a release
on a defect nobody scheduled. What it does instead is make a flake into a BUG REPORT WITH A COMMAND
ATTACHED — the seed reproduces the exact order that failed. `scripts/release.sh` does not call it.

⭐ **MODULE-LEVEL, NOT TEST-LEVEL, AND THAT IS A SCOPING DECISION RATHER THAN A LIMITATION.** The
documented failure class is CROSS-MODULE residue. Shuffling individual test methods would also
interleave classes, which makes `unittest` tear down and set up `setUpClass` fixtures repeatedly — so
a red would be ambiguous between "a residue exists" and "this class's fixture is not re-entrant", and
an ambiguous red in a finding tool is worse than no red. Each module keeps its internal order; only
the modules move. A test-level mode is a separate tool with a separate question.

USAGE
-----
    python3 scripts/shuffle-order.py                       # a random seed, printed
    python3 scripts/shuffle-order.py --seed 1234           # reproduce that order exactly
    python3 scripts/shuffle-order.py --modules a b c       # a subset, still shuffled
    python3 scripts/shuffle-order.py --derived             # precut's tree-wide + touched set
    python3 scripts/shuffle-order.py --list-only --seed 7  # print the order, run nothing

Exit: whatever the test run returned (0 green). ⚠ A non-zero exit means THIS ORDER failed — read it
as a lead, not as a verdict on the tree, and re-run with the printed seed to confirm it reproduces.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: ⚠ A DECLARED TEST SEAM, stated rather than left to be guessed. This tool's whole claim is that it
#: can tell an order-dependent failure from a plain one, and the only honest way to grade that claim
#: is to hand it a tree containing a PLANTED residue — two fixture modules where one leaves
#: process-global state and the other reads it. Pointing it at the real `tests/` could never prove it
#: FINDS anything, only that it currently finds nothing, which is the §7i shape: an instrument nobody
#: has watched succeed is not an instrument. Unset in every real run.
TESTS = os.environ.get("MOKATA_SHUFFLE_TESTS_DIR") or os.path.join(REPO, "tests")
sys.path.insert(0, TESTS)

#: A shuffle over too few modules cannot express a cross-module residue at all, and would report a
#: confident green having asked nothing. The floor is a tripwire on a broken selection, not a target.
MIN_SHUFFLE_MODULES = 8


def all_modules():
    """Every test module, derived from the directory — never a list."""
    return sorted(e[:-3] for e in os.listdir(TESTS)
                  if e.startswith("test_") and e.endswith(".py"))


def derived_modules():
    """`precut.py`'s set (tree-wide + touched), reused rather than re-implemented.

    ⭐ Imported from the script that owns the derivation. Two copies of "which modules matter" would
    drift, and the one that drifted would be the one nobody ran."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_precut", os.path.join(REPO, "scripts", "precut.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted({m[:-3] for m in set(mod.tree_wide_modules()) | set(mod.touched_modules())})


def shuffled(modules, seed):
    """The module order for `seed`. Deterministic for a given (modules, seed) pair.

    ⚠ `modules` is SORTED FIRST, and that is load-bearing: `random.Random(seed).shuffle` permutes
    whatever order it is handed, so an unsorted input would make the seed reproduce a different
    order on a filesystem that lists differently. A seed you cannot replay is not a seed."""
    out = sorted(modules)
    random.Random(seed).shuffle(out)
    return out


def main(argv):
    seed = None
    modules = None
    list_only = False
    passthrough = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--seed":
            i += 1
            seed = int(argv[i])
        elif a == "--list-only":
            list_only = True
        elif a == "--derived":
            modules = derived_modules()
        elif a == "--modules":
            modules = [m[:-3] if m.endswith(".py") else m for m in argv[i + 1:]]
            i = len(argv)
        else:
            passthrough.append(a)
        i += 1

    if modules is None:
        modules = all_modules()
    if len(modules) < MIN_SHUFFLE_MODULES:
        print("REFUSING: %d module(s) selected, under the floor of %d. A shuffle over a handful of "
              "modules cannot express a cross-module residue, so it would report a green having "
              "asked nothing." % (len(modules), MIN_SHUFFLE_MODULES), file=sys.stderr)
        return 2
    if seed is None:
        seed = random.randrange(1, 2 ** 31)

    order = shuffled(modules, seed)
    print("=" * 78)
    print("SHUFFLE-ORDER — %d module(s), SEED %d" % (len(order), seed))
    print("Reproduce this exact order:")
    print("    python3 scripts/shuffle-order.py --seed %d%s"
          % (seed, " --derived" if modules != all_modules() else ""))
    print("⛔ NOT THE RELEASE GATE. A red here is a LEAD — a cross-module residue this order")
    print("   exposed and alphabetical order hides. Confirm it reproduces with the seed above.")
    print("=" * 78)
    sys.stdout.flush()
    if list_only:
        for n, m in enumerate(order, start=1):
            print("%4d  %s" % (n, m))
        return 0

    started = time.time()
    # ⛔ THE ALPHABETICAL BASELINE RUNS FIRST, AND THE REPORT IS THE DIFFERENCE. The first version of
    # this tool printed "⚠ THIS ORDER FAILED" for any red at all — and on the dev VM that meant four
    # environment failures (`test_a2_phase_gate_is_real`, named in `precut.py`'s own docstring) on
    # EVERY run. A finding tool whose signal is drowned by pre-existing noise trains the reader to
    # ignore it, which costs every other finding it would ever make (§7i, and the same argument
    # `_a18_graph_floor_mutants.sh`'s header makes about a doctor warning that nags).
    #
    # ⭐ A test that fails in BOTH orders is not an ordering finding. It is a red test, and some other
    # instrument owns it. What this tool owns is the DIFFERENCE, both ways: a test that fails ONLY
    # shuffled is a residue exposed, and one that fails ONLY alphabetically is a residue the
    # alphabet CREATES — equally a finding, and one a shuffle-only report would never see.
    #
    # The cost is one extra full run, stated rather than hidden: a finding tool that reports a
    # difference has to measure both sides.
    base_ids, base_rc, base_ok = _run("BASELINE (alphabetical)", sorted(modules), passthrough)
    shuf_ids, shuf_rc, shuf_ok = _run("SHUFFLED (seed %d)" % seed, order, passthrough)
    elapsed = time.time() - started

    print("\n" + "=" * 78)
    print("SHUFFLE-ORDER — %.1fs, %d module(s), SEED %d" % (elapsed, len(order), seed))
    if not (base_ok and shuf_ok):
        # §7e — a parse that lost track of the failures must not report a confident difference.
        print("⛔ NO VERDICT. The failing-test parse did not reconcile with unittest's own counts "
              "for %s. A difference computed from an incomplete list would UNDER-report, which is "
              "the silent direction, so nothing is claimed here."
              % (", ".join(n for n, ok in (("baseline", base_ok), ("shuffled", shuf_ok)) if not ok)))
        print("=" * 78)
        return 2

    only_shuffled = sorted(shuf_ids - base_ids)
    only_alpha = sorted(base_ids - shuf_ids)
    both = sorted(shuf_ids & base_ids)

    if both:
        print("· %d test(s) fail in BOTH orders — NOT an ordering finding, and not this tool's "
              "business:" % len(both))
        for t in both[:8]:
            print("    %s" % t)
        if len(both) > 8:
            print("    … and %d more" % (len(both) - 8))
    if not only_shuffled and not only_alpha:
        print("✓ NO ORDER-DEPENDENT TEST AT THIS SEED. The same set fails in both orders.")
        print("⚠ One green seed is one sample. Run more seeds; this is a search, not a proof.")
        print("=" * 78)
        return 0

    if only_shuffled:
        print("🔴 %d test(s) fail ONLY in the shuffled order — a cross-module residue the alphabet "
              "HIDES:" % len(only_shuffled))
        for t in only_shuffled:
            print("    %s" % t)
    if only_alpha:
        print("🔴 %d test(s) fail ONLY in alphabetical order — a residue the alphabet CREATES, which "
              "a shuffle-only report would never see:" % len(only_alpha))
        for t in only_alpha:
            print("    %s" % t)
    print("Reproduce: python3 scripts/shuffle-order.py --seed %d" % seed)
    print("Then bisect: the residue is left by a module that runs before the victim in ONE of the "
          "two orders and not the other. `--list-only --seed %d` prints the order." % seed)
    print("=" * 78)
    return 1


_FAIL_RE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)")
_COUNT_RE = re.compile(r"^FAILED \((.*)\)")


def _run(label, order, passthrough):
    """Run one order. Returns `(failing_test_ids, returncode, parse_reconciled)`.

    ⚠ THE THIRD VALUE IS NOT DECORATION. The ids come from parsing unittest's `FAIL:`/`ERROR:`
    lines, and a parse that silently loses one would make this tool UNDER-report a difference —
    the direction a finding tool must never be wrong in. So the parsed count is reconciled against
    unittest's own `FAILED (failures=N, errors=M)` summary, and a mismatch yields NO VERDICT rather
    than a confident one (§7e: an instrument that fails open is a false green)."""
    print("\n--- %s" % label)
    sys.stdout.flush()
    proc = subprocess.run([sys.executable, "-m", "unittest"] + list(order) + list(passthrough),
                          cwd=TESTS, check=False, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    ids = set()
    for line in text.splitlines():
        m = _FAIL_RE.match(line.strip())
        if m:
            ids.add("%s.%s" % (m.group(2), m.group(1)))
    declared = 0
    for line in text.splitlines():
        m = _COUNT_RE.match(line.strip())
        if m:
            for part in m.group(1).split(","):
                part = part.strip()
                if part.startswith(("failures=", "errors=")):
                    declared += int(part.split("=", 1)[1])
    ran = re.search(r"^Ran (\d+) test", text, re.M)
    print("    %s · %d failing id(s) parsed, unittest declared %d"
          % (("Ran %s" % ran.group(1)) if ran else "no Ran line", len(ids), declared))
    reconciled = (len(ids) == declared) and (ran is not None)
    if not reconciled:
        print("    ⛔ parse did NOT reconcile — no difference will be claimed from this run")
    return ids, proc.returncode, reconciled


def _utf8_stdio():
    """🔴 WINDOWS CI, 0.0.21 cut (run 37310393789). A piped stdout on Windows is cp1252, and the
    banner's first `⛔` raised `UnicodeEncodeError` — exit 1 after three lines, on every seed, so the
    tool reported NOTHING on the one OS it had never run on. Write UTF-8 everywhere; a console that
    cannot render a glyph shows a replacement character, never a crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):            # pragma: no cover - a replaced stream
            pass


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main(sys.argv[1:]))
