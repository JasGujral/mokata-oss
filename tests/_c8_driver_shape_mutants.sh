#!/usr/bin/env bash
# Drives 0.0.20 stage 08's mutants through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_c8_driver_shape_mutants.sh
#
# WHAT THIS BATCH GRADES: that the guard on the PostgreSQL floor's ONE INPUT can actually fire.
# The row it closes — SERVER-VERSION-SHAPE-CHANGE-LAPSES-THE-FLOOR — is a row about a check that
# reports NEUTRAL when it breaks, so a guard for it that also reports neutral when IT breaks would
# be the same defect one layer up. Every mutant below moves the read, widens the degrade, or blinds
# the derivation, and requires the new tests to red on it.
#
# ⚠ THE REAL-DRIVER CLASS SKIPS WITHOUT `.[postgres]`, so mutants aimed at it are graded only where
# psycopg is installed. Mutants A01-A04 are aimed at the AST half, which runs everywhere — that
# split is deliberate, and it is why the file has two halves.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

DB=src/mokata/teamdb.py
T='test_c8_pg_driver_shape.py'
TD='test_pg_floor_drift.py'
# The floor COMPARISON is stage 04's subject, not this file's. A06 is graded there.
TC1='test_c1_pg_floor_enforcement.py'

TOTAL=8
ran=0; red=0; green=0; survivors=""

printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests -p "$T" >/dev/null 2>&1
then
    printf '\nBATCH REFUSED — %s is not green before mutant 1.\n' "$T"
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    exit 75
fi
printf 'Baseline GREEN. Grading %s mutants.\n\n' "$TOTAL"

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\nBATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' "$((TOTAL - ran))" "$TOTAL"
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$ran" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

mutant "A01 ★★★ THE FLOOR'S INPUT MOVES — the read is aimed somewhere else, which is the exact
       shape of the lapse this row is about, arriving from mokata's side instead" "$DB" \
  '        raw = int(conn.info.server_version)' \
  '        raw = int(conn.info.protocol_version)' "$T"

mutant "A02 ★★★ the third-party read loses its guard — a driver change becomes a CRASH on the
       connect path instead of a neutral verdict. A different defect, not a better one" "$DB" \
  '    try:
        raw = int(conn.info.server_version)
    except Exception:' \
  '    if True:
        raw = int(conn.info.server_version)
    except_never = lambda: 0
    if False:' "$T"

mutant "A03 ★★★ ZERO STOPS MEANING UNKNOWN — libpq answers 0 when it has no version, and a 0 that
       divides to 0 is 'below the floor', so an unreadable server starts being REFUSED" "$DB" \
  '    return raw // 10000 if raw > 0 else None' \
  '    return raw // 10000' "$T"

mutant "A04 ★★ the 9.x encoding stops answering 9 — a genuinely ancient server reads as modern,
       which is the one direction a floor may never be wrong in" "$DB" \
  '    return raw // 10000 if raw > 0 else None' \
  '    return raw // 100 if raw > 0 else None' "$T"

mutant "A05 ★★★ UNKNOWN STOPS BEING NEUTRAL — None is judged as below the floor, so every server
       whose version could not be read is refused. The row's defect, inverted" "$DB" \
  '    if server_major is None:
        return FLOOR_UNKNOWN' \
  '    if server_major is None:
        return FLOOR_REFUSE' "$T"

# ⚠ RE-POINTED before this batch was ever committed: A06 was written against `$T` and SURVIVED,
# because the floor COMPARISON is not what `test_c8` grades — it grades the INPUT to it. A mutant
# aimed at the wrong grader is a survivor that says nothing about a pin, and the right repair is to
# name the grader that owns the property rather than to widen this file until it happens to catch
# it. `test_c1_pg_floor_enforcement` owns the comparison and kills it.
mutant "A06 ★★ the floor comparison is loosened by one, so the release below the floor passes —
       the control that the INPUT guard is not the only thing standing here" "$DB" \
  '    if server_major >= MIN_PG_MAJOR:' \
  '    if server_major >= MIN_PG_MAJOR - 1:' "$TC1"

# ==== G12 — the UNKNOWN verdict may not go back to sharing OK's silence ========================

mutant "A07 ★★★ THE SILENCE RETURNS — UNKNOWN renders nothing again, so a met floor and an UNREAD
       one are the same output on every surface. This is the lapse arriving at a human as
       nothing at all" "$DB" \
  '    if verdict == FLOOR_UNKNOWN:' \
  '    if False:' "$TD"

mutant "A08 ★★★ the UNKNOWN sentence starts CARRYING THE MAJOR — a claim about the user's server
       on the strength of a version nobody managed to read, which is the exact thing the old
       pin was right to forbid" "$DB" \
  '        return ("mokata could not read this server'"'"'s PostgreSQL version, so the supported-version "' \
  '        return (f"mokata could not read this server (major {server_major}) so the version "' "$TD"

printf '\n================================================================================\n'
printf 'STAGE-08 DRIVER-SHAPE MUTANTS: %s ran of %s — %s RED / %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH INCOMPLETE — %s of %s never ran. Not a score.\n' "$((TOTAL - ran))" "$TOTAL"
    printf '================================================================================\n'
    exit 71
fi
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
