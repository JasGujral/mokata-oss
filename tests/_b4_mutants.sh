#!/usr/bin/env bash
# Drives the CI-JOBS-WITHOUT-A-TIMEOUT (row B4) mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_b4_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Satisfies tests/_mutant_driver_contract.py, which is a live pin since 0.0.18 exit
# criterion 5.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing",
# and every RED it prints is unattributable.
#
# FOUR MUTANTS, and the count is the stage's size rather than an accident — doc 104 calls B4 the
# lowest-value row of the four and the brief says three or four is right here. One per surface the
# row actually stands on:
#
#   M1  .github/workflows/ci.yml    a ceiling REMOVED — does the coverage test notice?
#   M2  tests/_timeout_sweep.py     the corpus PARAMETER ignored, so the planted offender becomes
#                                   unreachable and the sweep grades the (healthy) real tree
#                                   instead. §7i's own mutant, aimed at this guard.
#   M3  tests/_timeout_sweep.py     an unreadable corpus SKIPS SILENTLY instead of refusing —
#                                   "no jobs" wearing the pass that belongs to "no offenders".
#   M4  tests/_timeout_sweep.py     INEFFECTIVE collapsed into COVERED, i.e. `timeout-minutes: 360`
#                                   accepted as a ceiling. The one-line way to silence this guard.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

SWEEP=tests/_timeout_sweep.py
CI=.github/workflows/ci.yml
T='test_b4_ci_jobs_without_a_timeout.py'

TOTAL=4
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
BASE_LOG="$(mktemp -t b4-baseline)"
PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
    -k test_b4_ci_jobs_without_a_timeout > "$BASE_LOG" 2>&1
BASE_RC=$?
tail -5 "$BASE_LOG"
if [ "$BASE_RC" -ne 0 ]; then
    printf '\nBATCH REFUSED — the graded suite is not green before mutant 1 (exit %s).\n' "$BASE_RC"
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    rm -f "$BASE_LOG"
    exit 75
fi
rm -f "$BASE_LOG"
printf 'Baseline GREEN (exit %s). Grading %s mutants.\n\n' "$BASE_RC" "$TOTAL"

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

mutant "M1 ★★ a ceiling is removed — hooks-execute goes back to GitHub's 360" "$CI" \
  '    timeout-minutes: 3' \
  '    # timeout-minutes: 3' "$T"

mutant "M2 ★★ the corpus parameter is ignored — the planted offender becomes unreachable" "$SWEEP" \
  '    names = [n for n in os.listdir(corpus_dir) if n.endswith(WORKFLOW_SUFFIXES)]' \
  '    names = [n for n in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows")) if n.endswith(WORKFLOW_SUFFIXES)]' "$T"

mutant "M3 ★★ an unreadable corpus skips silently instead of refusing (§7g)" "$SWEEP" \
  '    if not names:
        raise EmptyCorpus(' \
  '    if False:
        raise EmptyCorpus(' "$T"

mutant "M4 ★★ INEFFECTIVE collapses into COVERED — timeout-minutes: 360 buys silence" "$SWEEP" \
  '    if minutes >= PLATFORM_DEFAULT_MINUTES or minutes <= 0:' \
  '    if False:' "$T"

printf '\n================================================================================\n'
printf 'B4 MUTANT BATCH — %s of %s ran · %s RED · %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'INCOMPLETE — %s mutants never ran.\n' "$((TOTAL - ran))"
    printf '================================================================================\n'
    exit 71
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (the pin did NOT catch these):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 72
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
