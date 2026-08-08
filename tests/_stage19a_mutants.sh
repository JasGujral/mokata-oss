#!/usr/bin/env bash
# Drives the mutant list in `_stage19a_mutants.txt` through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage19a_mutants.sh
#
# Stage 19a — APPROVED-STILL-READS-AS-AWAITING. The defect was a MISSING DISTINCTION, so every
# mutant here collapses it again, one surface at a time: count the approved into the wait, render
# them under the wait's wording, hand back the approve command, or flip the degrade direction so
# an unreadable record UNDER-asks. A pin that survives any of these is not pinning the fix.
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED.
#
# ⚠ MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) reaches SIX instances with this file. The end state
# is one shared driver taking a list file; this is still not it, and the row is now overdue.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

A=src/mokata/awaiting.py
L=src/mokata/cli_commands/approve.py
T='test_approved_still_reads_as_awaiting.py'

TOTAL=8
ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    shift
    out="$("$M" "$label" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
            "$((TOTAL - ran))" "$TOTAL"
        printf '  See scripts/mutate.sh'"'"'s EXIT CONTRACT for what %s means and what to do.\n' "$rc"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n  said  : %s\n' "$label" "${out:-<nothing at all>}"
                exit 70 ;;
    esac
}

# ---- the split itself -------------------------------------------------------------------------

mutant "M01 the split collapses — an approved proposal goes back into the wait bucket" "$A" \
  '        (approved if getattr(p, "approved", False) else waiting).append(p)' \
  '        waiting.append(p)' "$T"

mutant "M02 the degrade direction flips — an unreadable record UNDER-asks instead of over-asking" "$A" \
  '        (approved if getattr(p, "approved", False) else waiting).append(p)' \
  '        (approved if getattr(p, "approved", True) else waiting).append(p)' "$T"

# ---- doctor / pending_lines -------------------------------------------------------------------

mutant "M03 the wait COUNT goes back to the whole live set" "$A" \
  '            lines.append(f"mokata pending: {len(waiting)} write(s) awaiting YOUR approval "' \
  '            lines.append(f"mokata pending: {len(proposals)} write(s) awaiting YOUR approval "' "$T"

mutant "M04 the approved block stops rendering — a live unwritten proposal vanishes" "$A" \
  '        if approved:
            # Says what is TRUE of every approved write' \
  '        if False:
            # Says what is TRUE of every approved write' "$T"

mutant "M05 the approved row hands back the approve command again" "$A" \
  '    lines = [f"  {tool} APPROVED {pid} — approved, not yet written"]' \
  '    lines = [f"  {tool} APPROVED {pid} — Fix: `" + APPROVE_CMD.format(proposal_id=pid) + "`"]' "$T"

# ---- the statusline ---------------------------------------------------------------------------

mutant "M06 the statusline calls an approved proposal a wait again" "$A" \
  '            parts.append("✅ approved, not yet written " + _seg(approved))' \
  '            parts.append("⏳ awaiting approval " + _seg(approved))' "$T"

mutant "M07 the statusline +N count mixes the two states again" "$A" \
  '            parts.append("⏳ awaiting approval " + _seg(waiting))' \
  '            parts.append("⏳ awaiting approval " + _seg(waiting + approved))' "$T"

# ---- approve --list ---------------------------------------------------------------------------

mutant "M08 approve --list heading counts the approved as waiting again" "$L" \
  '        print(f"mokata · {len(waiting)} durable write(s) waiting for your approval:\n")' \
  '        print(f"mokata · {len(items)} durable write(s) waiting for your approval:\n")' "$T"

printf '\n================================================================================\n'
printf 'stage 19a mutants: %s of %s graded — RED %s / GREEN %s\n' "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (the pin did NOT catch these):\n%s' "$survivors"
    exit 1
fi
printf 'no survivors.\n'
printf '================================================================================\n'
