#!/usr/bin/env bash
# Drives the stage-23 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage23_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Driver copied from `tests/_stage11_mutants.sh`, which is the one that implements the
# contract — NOT from `_sync_marker_drift_mutants.sh`, which implements none of it
# (MUTANT-DRIVER-CONTRACT-UNIMPLEMENTED, doc 84, open).
#
# ⚠ §7i does NOT bite here the way it bit stages 2 and 11, and it is worth saying why rather
# than claiming immunity. This stage's subject is a PURE FUNCTION OVER A SUPPLIED PAIR of
# readings — `diff(previous, current)` discovers nothing and walks no tree — so the corpus is
# an argument, not the repo, and every mutant below is graded against readings the test hands
# it. Two of those readings are REAL (`_scorecard_reading_2026-06-30.json` and
# `_scorecard_reading_2026-08-04.json`), and their diff is the actual historical regression the
# row was filed for: the aggregate ROSE 5.5 -> 6.6 while SAST DROPPED 10 -> 8. So the offender
# is not planted — it happened.
#
# The three loudest mutants are D01, G01 and G03, one per §7g face:
#   D01  a drop stops being a drop            — the feature itself
#   G01  "not compared" collapses into "pass" — a first run reads as a clean one
#   G03  Scorecard's -1 re-enters arithmetic  — an absent answer becomes a number again
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
S=scripts/scorecard_delta.py
T='test_s23_scorecard_delta.py'

TOTAL=14
ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
            "$((TOTAL - ran))" "$TOTAL"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$ran" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

# ==== D — THE FEATURE: a per-check drop is red, whatever the aggregate did =====================

mutant "D01 a falling score is classified as a rise (the drop stops existing)" "$S" \
  '        kind = DROP if a < b else RISE' \
  '        kind = RISE' "$T"

mutant "D02 a DROP stops counting as a regression (reported, but green)" "$S" \
  '    DROP: True,' \
  '    DROP: False,' "$T"

mutant "D03 regressions are no longer reported first (the drop is buried mid-table)" "$S" \
  '    changes.sort(key=lambda c: (not c.is_regression, _KIND_RANK[c.kind], c.name))' \
  '    changes.sort(key=lambda c: c.name)' "$T"

# ==== G — §7g: an absent answer must never wear a real answer's representation =================

# ★ THE ONE THIS STAGE EXISTS FOR alongside D01: a first run that grades nothing must not
#   report the exit status of a run that graded everything and found it clean.
mutant "G01 'not compared' returns the PASS exit code (a first run reads as clean)" "$S" \
  '        if not self.compared:
            return EXIT_NOT_COMPARED' \
  '        if False:
            return EXIT_NOT_COMPARED' "$T"

mutant "G02 every delta claims to have been compared (NO_PREVIOUS collapses into NO_CHANGE)" "$S" \
  '        return self.status != NO_PREVIOUS' \
  '        return True' "$T"

mutant "G03 Scorecard's -1 stays a number, so an absent score re-enters arithmetic" "$S" \
  '        if score == -1:' \
  '        if False:' "$T"

mutant "G04 losing the ability to measure a check stops being a regression" "$S" \
  '    BECAME_UNSCORED: True,   # a lost measurement is not a neutral event' \
  '    BECAME_UNSCORED: False,  # a lost measurement is not a neutral event' "$T"

mutant "G05 a check vanishing from the report stops being a regression" "$S" \
  '    REMOVED: True,           # ditto, one level up: the check itself stopped being reported' \
  '    REMOVED: False,          # ditto, one level up: the check itself stopped being reported' "$T"

mutant "G06 a NO_PREVIOUS delta may carry changes (a delta claiming not to be one)" "$S" \
  '            if changes:
                raise ValueError("NO_PREVIOUS cannot carry changes -- nothing was compared")' \
  '            if False:
                raise ValueError("NO_PREVIOUS cannot carry changes -- nothing was compared")' "$T"

mutant "G07 an omitted --previous is INFERRED as a first run instead of refused" "$S" \
  '    elif not args.first_run:' \
  '    elif False:' "$T"

# ==== R — the reason string: what caught this row, and what must not be invented ===============

mutant "R01 a reason we never recorded is compared as though it were one" "$S" \
  '    if REASON_UNRECORDED in (before.reason_state, after.reason_state):' \
  '    if False:' "$T"

mutant "R02 a reason-only change starts FAILING the post-cut step" "$S" \
  '    REASON_CHANGED: False,   # worth surfacing, explicitly not worth failing' \
  '    REASON_CHANGED: True,    # worth surfacing, explicitly not worth failing' "$T"

# ==== P — the parser refuses rather than defaults ==============================================

mutant "P01 a reading with zero checks parses as empty (diffs as 'everything REMOVED')" "$S" \
  '    if not isinstance(raw, list) or not raw:' \
  '    if not isinstance(raw, list):' "$T"

mutant "P02 a duplicate check name silently keeps the last one" "$S" \
  '        if name in checks:' \
  '        if False:' "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 23 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
