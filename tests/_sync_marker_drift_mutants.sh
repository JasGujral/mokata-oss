#!/usr/bin/env bash
# Mutation batch for the 0.0.17 §7i audit's ONE conversion: the sync-public marker-drift rule in
# `tests/test_sync_public_nested_checkout.py` (`_BOUNDARY_FIND` · `boundary_find_sites` ·
# `marker_drift` · `BOUNDARY_WALKS`).
#
# The rule being mutated is TEST-side, so the mutation target is the test module — and that is the
# point of the conversion. Before it the rule was `assertIn(f"-name {CHECKOUT_MARKER}", code)` over
# the whole script, and NO mutant could red it: the tree it judged had no offender in it, which is
# doc 85 §7i exactly. Every mutant below reproduces one of the two defects the audit convicted, or
# removes one half of the fix.
#
# Run: PYTHON=/Users/jas/jsvenv_mk312/bin/python bash tests/_sync_marker_drift_mutants.sh
#
# SPDX-License-Identifier: Apache-2.0
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
MUTATE="${MUTATE_SH:-$HERE/scripts/mutate.sh}"
T="tests/test_sync_public_nested_checkout.py"
PAT="test_sync_public_nested_checkout.py"

mutant() { "$MUTATE" "$1" "$2" "$3" "$4" "$5"; }

# S1 — THE AUDITED DEFECT, half one: containment instead of an exact match, so `.gitdir` is
#      accepted as `.git`.
mutant "S1 exact match -> containment" "$T" \
    "if tok != marker]" \
    "if marker not in tok]" \
    "$PAT"

# S2 — THE AUDITED DEFECT, half two: one arm spelling the marker exonerates the whole file. This
#      is precisely what the old whole-file `assertIn` did, expressed inside the new function.
mutant "S2 per-arm -> any-arm-will-do" "$T" \
    "    return offenders" \
    "    return [] if len(offenders) < len(sites) else offenders" \
    "$PAT"

# S3 — the §7i shape in its purest form: the walk finds nothing, and an empty corpus reads clean.
mutant "S3 boundary walks never found" "$T" \
    "for m in [_BOUNDARY_FIND.search(ln)] if m]" \
    "for m in [None] if m]" \
    "$PAT"

# S4 — comment stripping dropped, so the script's own prose about the marker counts as a walk.
mutant "S4 comment stripping dropped" "$T" \
    'if not ln.lstrip().startswith("#")]' \
    "if True]" \
    "$PAT"

# S5 — the deleted-arm half. `marker_drift` reports offenders and cannot see an ABSENCE; only the
#      arm count converts a removed boundary walk into a red.
mutant "S5 arm-count guard defanged" "$T" \
    "BOUNDARY_WALKS = 2 " \
    "BOUNDARY_WALKS = 1 " \
    "$PAT"

# S6 — the regex loses its loop anchor, so every `-name` in the script reads as a boundary walk
#      (the churn direction of the same rule, and the reason the anchor is there).
mutant "S6 regex loses the loop anchor" "$T" \
    'r"done\s*<\s*<\(\s*find\b[^)]*?-name\s+(\S+)"' \
    'r"-name\s+(\S+)"' \
    "$PAT"
