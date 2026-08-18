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
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
MUTATE="${MUTATE_SH:-$HERE/scripts/mutate.sh}"
T="tests/test_sync_public_nested_checkout.py"
PAT="test_sync_public_nested_checkout.py"

TOTAL=6
ran=0; red=0; green=0; survivors=""

# ⚠ FOUND AT STAGE 31, AND IT IS THE ONE DEFECT `test_mutant_batch_driver.py` WAS WRITTEN ABOUT.
# This was the only driver in the tree that inspected NO exit status: it invoked the mutator six
# times and carried on whatever came back. That is exactly the shape 0.0.17 stage 18b fixed in
# `_run_mutants.sh` — and on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED, so
# every later mutant here was graded against an uncontrolled edit. It also swallowed stage 31's
# exit 7, which would have made the baseline guard decorative for this batch alone.
#
# Kept deliberately compact rather than growing a fifth copy of the full dispatch table
# (MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84): the contract lives in `scripts/mutate.sh`'s header
# and this consumes the ONE RULE — exit 0 iff a verdict was produced.
#
# ⚠ COMPLETED AT EXIT CRITERION 5 (0.0.18), and the missing half was the half that produces the
# NUMBER. Stage 31 made this driver STOP on a harness failure; it still could not COUNT. stdout
# went straight to the terminal, so no verdict was ever read: a GREEN survivor scrolled past as
# one line among six and the batch exited 0 exactly as a clean run does. That is why `c658c73`'s
# "6 of 6 RED" was a reading rather than a derivation — the file that reported it had nothing in
# it that could produce a 6, or a 5.
mutant() {
    local rc=0 out
    ran=$((ran + 1))
    # stdout is captured so the verdict can be COUNTED and a status-0 run carrying no verdict is
    # noticed, then re-emitted immediately so the batch log reads as it always did. stderr is
    # deliberately NOT captured — REFUSED-RESTORE and LOCK-BUSY belong on the terminal.
    out="$("$MUTATE" "$1" "$2" "$3" "$4" "$5")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$1"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
            "$((TOTAL - ran))" "$TOTAL"
        printf '  See scripts/mutate.sh'"'"'s EXIT CONTRACT for what %s means and what to do.\n' "$rc"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $1"$'\n' ;;
        # Status 0 is a CLAIM that a verdict exists. Counting a line this driver did not
        # understand as a graded mutant is the laundering every hole in mutate.sh's header
        # turned out to be. 70 rather than a mutate.sh code: this is the driver's own finding.
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n' "$1"
                printf '  output: %s\n' "${out:-<nothing>}"
                exit 70 ;;
    esac
}

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

# ==== verdict ===================================================================================
# A batch that grades fewer mutants than it declares reads identically to one that graded them
# all — the silent-truncation shape. `ran` is checked against `TOTAL` before any score is printed.

if [ "$ran" -ne "$TOTAL" ]; then
    printf '\nBATCH ABORTED — %s mutants ran but this driver declares TOTAL=%s.\n' "$ran" "$TOTAL"
    printf '  No score is printed for a list that did not run: a partial tally is read as a tally.\n'
    exit 70
fi

printf '\n================================================================================\n'
printf 'SYNC-MARKER-DRIFT MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
