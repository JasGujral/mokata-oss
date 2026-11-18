#!/usr/bin/env bash
# Drives the `tests/windows_e2e_check.py` `_run` UTF-8 mutant through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_windows_e2e_check_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Satisfies tests/_mutant_driver_contract.py.
#
# 0.0.29 D3 (`CI-TIMING-LOWS-0028`, the 0.0.28 `_run` row). ONE MUTANT, and WHERE it can be killed
# is part of its statement, not a footnote:
#
#   M1  tests/windows_e2e_check.py   `_run` drops `encoding="utf-8"` — a child's UTF-8 output is
#                                    then decoded with the ANSI code page.
#
# ⚠ EXPECTED VERDICT DEPENDS ON THE HOST, declared here rather than discovered:
#   * WINDOWS (cp1252 locale): RED — `test_windows_e2e_check_reads_utf8` reads a child's em dash
#     through `_run` and the ANSI decode mangles it.
#   * macOS / Linux (UTF-8 locale): a KNOWN SURVIVOR (exit 72). `text=True` already decodes UTF-8
#     there, so the mutant changes nothing observable and no test on that host can kill it. A
#     GREEN here off Windows is the mutant's nature, not a weakened pin.
# It is graded on the mirror Windows matrix (COMMON §Method 5); its CRLF-safe application on that
# checkout is exactly what D3's `scripts/mutate.sh` line-ending fix made possible.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

CHECK=tests/windows_e2e_check.py
T='test_windows_e2e_check_reads_utf8.py'

TOTAL=1
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
BASE_LOG="$(mktemp "${TMPDIR:-/tmp}/wincheck-baseline.XXXXXX" 2>/dev/null)" || BASE_LOG=""
if [ -z "$BASE_LOG" ] || [ ! -f "$BASE_LOG" ]; then
    printf '\nHARNESS FAILURE — this driver could not create its own baseline log.\n'
    printf '  NOTHING IS WRONG WITH THE TREE and nothing was graded. The fault is in this script.\n'
    printf '  Exit 76 means the DRIVER broke; exit 75 means the SUBJECT was red. They are not the same.\n'
    exit 76
fi
PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
    -k test_windows_e2e_check_reads_utf8 > "$BASE_LOG" 2>&1
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

mutant "M1 ★★ _run reads a child's output in the ANSI code page (RED on Windows only)" "$CHECK" \
  '    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",' \
  '    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          errors="replace",' "$T"

printf '\n================================================================================\n'
printf 'WINDOWS_E2E_CHECK MUTANT BATCH — %s of %s ran · %s RED · %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'INCOMPLETE — %s mutants never ran.\n' "$((TOTAL - ran))"
    printf '================================================================================\n'
    exit 71
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (the pin did NOT catch these):\n%s' "$survivors"
    printf '  (off Windows M1 is a KNOWN survivor — see the header; on Windows it is a real one)\n'
    printf '================================================================================\n'
    exit 72
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
