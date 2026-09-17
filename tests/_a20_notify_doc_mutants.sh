#!/usr/bin/env bash
# Drives the 0.0.20 `MANIFEST-UX-NOTIFY-ROW-OVERPROMISES-ON-WINDOWS` mutant list through
# scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a20_notify_doc_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THIS BATCH IS WEIGHTED TOWARD ONE FAILURE DIRECTION: **VACUOUS GREEN**. Three drafts of the
# instrument reported GREEN with an EMPTY derived set, each for a different reason, and every one of
# them would have shipped a guard that graded nothing while looking like it graded everything.
# V01-V04 are those three drafts plus the shape they share.
#
#   V01-V04 ★ THE VACUITY. Each empties or blinds the derivation. Every one produced a real GREEN
#     during development; they are not hypothetical.
#
#   C01-C03 THE CLAUSE READER. The doc states coverage POSITIVELY ("macOS and Linux only") and the
#     exclusion sentence sits right after it. Each mutant makes the reader swallow the wrong half.
#
#   D01-D02 THE TWO DIRECTIONS. Over- and under-promising come out of one comparison; each mutant
#     drops one of them.
#
#   A01 THE AUDIO QUALIFIER. The one thing no runner can ever verify must stay said.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

N=tests/_notify_doc_truth.py
T='test_a20_the_notify_doc_matches_the_arms.py'

TOTAL=10
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

# ==== V. the vacuity — every one of these WAS a real draft ======================================

mutant "V01 ★ DRAFT 3: a missing dispatcher reports an EMPTY world instead of raising" "$N" \
  '    if dispatch is None:
        raise AttributeError(' \
  '    if dispatch is None:
        return () or AttributeError(' "$T"

mutant "V02 ★ DRAFT 1: the probe body is one the notifier REFUSES, so no platform has an arm" "$N" \
  '    return sorted(notify_module.BODIES)[0]' \
  '    return "a body"' "$T"

mutant "V03 ★ the dispatcher name is wrong — an absent function reads as an absent arm" "$N" \
  'VISUAL_DISPATCHER = "_notification_argv"' \
  'VISUAL_DISPATCHER = "_visual_argv"' "$T"

mutant "V04 the platform probe set is emptied, so every comparison is trivially satisfied" "$N" \
  'PROBED_PLATFORMS = (
    ("darwin", "macOS"),
    ("linux", "Linux"),
    ("win32", "Windows"),
)' \
  'PROBED_PLATFORMS = ()' "$T"

# ==== C. the clause reader ======================================================================

mutant "C01 ★ DRAFT 2: the clause runs past the colon and swallows the EXCLUSION sentence" "$N" \
  '_ONLY_CLAUSE = re.compile(r"[^.|;:]*\bonly\b[^.|;:]*")' \
  '_ONLY_CLAUSE = re.compile(r"[^.|]*\bonly\b[^.|]*")' "$T"

mutant "C02 a row with NO coverage claim is treated as a claim covering nothing" "$N" \
  '        return (), False' \
  '        return (), True' "$T"

mutant "C03 the no-claim case stops being the filed defect and passes" "$N" \
  '        return DocVerdict(no_coverage_claim=True,' \
  '        return DocVerdict(no_coverage_claim=False,' "$T"

# ==== D. the two directions =====================================================================

mutant "D01 an OVERPROMISE (claimed, no arm) stops being reported" "$N" \
  '    unnamed = tuple(t for t, _n in PROBED_PLATFORMS if t in claimed and t not in with_arm)' \
  '    unnamed = ()' "$T"

mutant "D02 an UNDERSTATED platform (has an arm, not claimed) stops being reported" "$N" \
  '    false_ex = tuple(t for t, _n in PROBED_PLATFORMS if t in with_arm and t not in claimed)' \
  '    false_ex = ()' "$T"

# ==== A. the audio qualifier ====================================================================

mutant "A01 the audio row may promise a sound without saying it was only QUEUED" "$N" \
  '    overpromises = not any(q.lower() in audio_row for q in AUDIBILITY_QUALIFIERS)' \
  '    overpromises = False' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A20 NOTIFY-DOC MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
