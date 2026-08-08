#!/usr/bin/env bash
# Drives the mutant list in `_stage16_mutants.txt` through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage16_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT, which is 0.0.17 stage 18b's rule and the reason that stage
# existed: a VERDICT (RED / GREEN) is a completed run — count it and carry on; a HARNESS FAILURE
# (any non-zero, or a status 0 carrying no verdict) STOPS THE BATCH DEAD, because on an exit 4 the
# mutator deliberately LEAVES THE TARGET MUTATED and everything graded afterwards would be graded
# against a tree holding an uncontrolled edit. The abort prints to STDOUT so a stdout-only batch
# log cannot end mid-list looking like a completed run.
#
# ⚠ IT RE-IMPLEMENTS THAT CONTRACT RATHER THAN SHARING `_run_mutants.sh`'s COPY, and that is a
# known duplication, filed as MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) rather than fixed here:
# `_run_mutants.sh` is stage 1a-FU's list, it had just been reviewed and committed at 18b, and
# refactoring it mid-stage to host a second list is a change nobody asked for on a file whose whole
# value is that it was verified. The end state is one shared driver; this is not it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
F=src/mokata/memory/migrate.py
T='test_stage16*.py'

TOTAL=15
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
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

# ---- the refusals (behaviour unchanged by this stage; they MOVED, so they are re-pinned here) ----

mutant "M01 self-migration refusal inverted" "$F" \
  '    if same_store:' '    if not same_store:' "$T"

mutant "M02 pending/conflict refusal: or -> and" "$F" \
  '    if pending or conflicts:' '    if pending and conflicts:' "$T"

mutant "M03 pending/conflict refusal removed" "$F" \
  '    if pending or conflicts:' '    if False:' "$T"

# ---- the consent (NOT redesigned by this stage — pinned so it cannot be redesigned by accident) --

mutant "M04 consent bypassed — approved unconditionally" "$F" \
  '    approved = assume_yes' '    approved = True' "$T"

mutant "M05 the fail-closed default replaced by an always-yes gate" "$F" \
  '        dgate = drop_confirm or _default_drop_confirm' \
  '        dgate = drop_confirm or (lambda _t: True)' "$T"

mutant "M06 the caller's drop dispatch inverted" "$F" \
  '    if drop_source:' '    if not drop_source:' "$T"

# ---- the record — THE STAGE ------------------------------------------------------------------

mutant "M07 the ledger record deleted" "$F" \
  '        if ledger is not None and dropped:' '        if False:' "$T"

mutant "M08 the record fires when nothing was destroyed" "$F" \
  '        if ledger is not None and dropped:' '        if ledger is not None:' "$T"

mutant "M09 items= reports the count ASKED FOR, not the count done" "$F" \
  '                          items=dropped, attempted=len(migrated_items),' \
  '                          items=len(migrated_items), attempted=len(migrated_items),' "$T"

mutant "M10 attempted= collapses onto items=" "$F" \
  '                          items=dropped, attempted=len(migrated_items),' \
  '                          items=dropped, attempted=dropped,' "$T"

mutant "M11 complete= inverted" "$F" \
  '                          complete=(dropped == len(migrated_items)))' \
  '                          complete=(dropped != len(migrated_items)))' "$T"

# M12 records ONLY on the happy path. The first attempt at this mutant was `finally:` -> `else:`,
# which is a SyntaxError (a `try` needs an `except` before an `else`) — it went RED on an IMPORT
# failure, `Ran 1 test ... errors=1`, which is a mutant grading the parser rather than the pins.
# Replaced with the semantically valid form, and recorded here because a RED nobody looked at is
# how a mutation score inflates.
mutant "M12 the record runs only on the happy path — a partial drop goes unrecorded" "$F" \
  '    finally:' \
  '    except BaseException:
        raise
    else:' "$T"

mutant "M13 the record echoes the migrated items (SECRET LEAK)" "$F" \
  '            ledger.record("migrate_drop_source", op="drop_source", subject=src_tool,' \
  '            ledger.record("migrate_drop_source", op="drop_source", subject=str([i.subject for i in migrated_items]),' "$T"

# ---- the count, and the value the caller reports ----------------------------------------------

mutant "M14 the counter increments BEFORE the delete" "$F" \
  '            source.delete(it.id)
            dropped += 1' \
  '            dropped += 1
            source.delete(it.id)' "$T"

mutant "M15 _drop_source reports nothing was dropped" "$F" \
  '    return dropped' '    return 0' "$T"

printf '\n================================================================================\n'
printf 'stage 16 mutants: %s of %s  |  RED %s  |  GREEN(survivors) %s\n' "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a real finding about a real pin:\n%s' "$survivors"
fi
printf '================================================================================\n'
[ "$green" -eq 0 ]
