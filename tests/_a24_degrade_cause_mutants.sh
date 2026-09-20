#!/usr/bin/env bash
# Drives the 0.0.20 degrade-names-its-cause mutants through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a24_degrade_cause_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THE SUBJECT IS A MESSAGE, AND A MESSAGE FAILS IN FOUR DIRECTIONS. Each group restores one of
# them, and each one shipped at least once:
#
#   C01-C04 ★ THE CLASSIFIER COLLAPSES. Every mutant here makes mokata answer ONE story for both
#           causes — which is the defect OSS #67 was, exactly: a `TypeError` reported as a disk
#           problem. C03/C04 collapse it the OTHER way, so "it always says internal" is graded too.
#   S01-S03 ★ THE SUMMARY. S01 empties it (the type never reaches the reader — #67 again). S02 and
#           S03 fill it from the exception's own text, which is the CM.S1 PATH LEAK: the two
#           failures of a diagnostic string are saying nothing and saying too much.
#   R01-R02 ★ THE REPEAT COUNT (#68). R01 counts only announced failures — "warned once" reading as
#           "failed once". R02 makes doctor silent about repeats, so the number exists and nobody
#           can ask for it.
#   F01-F02 ★ THE FORWARDING. F01 drops the exception at the real seam; F02 drops it at the engine's
#           handler. Either one puts one call site back to guessing while the others are fixed.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

F=src/mokata/session_flow.py
D=src/mokata/govern/doctor.py
P=src/mokata/engine/phases.py
T='test_a24_a_degrade_names_its_cause.py'

TOTAL=11
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

# ==== C. the classifier collapses — OSS #67, restored verbatim ==================================

mutant "C01 ★★ THE DEFECT — every failure is a disk problem, whatever raised" "$F" \
  '    return FAILURE_LOCAL_IO if isinstance(error, OSError) else FAILURE_INTERNAL' \
  '    return FAILURE_LOCAL_IO' "$T"

mutant "C02 ★ the check is on the EXACT type — PermissionError becomes a mokata bug" "$F" \
  '    return FAILURE_LOCAL_IO if isinstance(error, OSError) else FAILURE_INTERNAL' \
  '    return FAILURE_LOCAL_IO if type(error) is OSError else FAILURE_INTERNAL' "$T"

mutant "C03 ★ collapsed the OTHER way — a real disk failure sends the reader to the tracker" "$F" \
  '    return FAILURE_LOCAL_IO if isinstance(error, OSError) else FAILURE_INTERNAL' \
  '    return FAILURE_INTERNAL' "$T"

mutant "C04 the two classes become the same constant, so 'different' is vacuous" "$F" \
  '    return FAILURE_LOCAL_IO if isinstance(error, OSError) else FAILURE_INTERNAL' \
  '    return FAILURE_LOCAL_IO if isinstance(error, OSError) else FAILURE_LOCAL_IO' "$T"

# ==== S. the summary — silent, or leaking =======================================================

mutant "S01 ★★ the type never reaches the reader — #67's own blindness" "$F" \
  '    name = type(error).__name__' \
  '    name = ""' "$T"

mutant "S02 ★★ THE PATH LEAK — the summary is taken from the exception's own text" "$F" \
  '    name = type(error).__name__' \
  '    name = str(error)' "$T"

mutant "S03 ★ the OSError arm appends the message alongside the errno" "$F" \
  '        if symbols:
            return "%s (%s)" % (name, ", ".join(symbols))' \
  '        if symbols:
            return "%s (%s): %s" % (name, ", ".join(symbols), error)' "$T"

# ==== R. the repeat count — OSS #68 =============================================================

mutant "R01 ★★ only ANNOUNCED failures are counted — 'warned once' means 'failed once' again" "$F" \
  '    _FAILURES[moment] = _FAILURES.get(moment, 0) + 1
    _LAST_ERROR[moment] = summary
    if moment in store:
        return False' \
  '    if moment in store:
        return False
    _FAILURES[moment] = _FAILURES.get(moment, 0) + 1
    _LAST_ERROR[moment] = summary' "$T"

mutant "R02 ★ doctor stops reporting repeats — the count exists and nobody can ask" "$D" \
  '    lines.extend(_repeat_lines())' \
  '    lines.extend([])' "$T"

# ==== F. the forwarding — one site left guessing while the others are fixed =====================

mutant "F01 ★★ the real checkpoint seam drops the exception again" "$F" \
  '            note_persist_failure(moment, self._warn, error=exc)' \
  '            note_persist_failure(moment, self._warn)' "$T"

mutant "F02 ★ the engine's gate handler drops it — §7j, a fix that reached a subset" "$P" \
  '        note_persist_failure("gate:" + phase, error=exc)' \
  '        note_persist_failure("gate:" + phase)' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A24 DEGRADE-CAUSE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
