#!/usr/bin/env bash
# Drives the mutant list in `_stage17_mutants.txt` through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage17_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit. The
# abort prints to STDOUT so a stdout-only batch log cannot end mid-list looking like a completed run.
#
# ⚠ THE THIRD COPY OF THAT CONTRACT, and it is still filed rather than fixed:
# MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). `_run_mutants.sh` (stage 1a-FU) and
# `_stage16_mutants.sh` each carry their own. Folding three lists into one driver is a change
# nobody asked for mid-stage on files whose whole value is that they were verified; the end state
# is one shared driver taking a list file, and this is not it. Recording the third instance is what
# stops "we'll unify it later" from being said three more times.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
F=src/mokata/migrate_channels.py
T='test_stage17*.py'
S='test_si_6*.py'

TOTAL=22
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

# ---- the record is UNCONDITIONAL — deliverable 2 ----------------------------------------------

mutant "M01 the no-ledger refusal deleted" "$F" \
  '    if ledger is None:' '    if False:' "$T"

mutant "M02 the no-ledger refusal inverted" "$F" \
  '    if ledger is None:' '    if ledger is not None:' "$T"

mutant "M03 the refusal reports success, so the once-migrated marker is written" "$F" \
  '            channel="vault", aborted=True, migrated=0,
            message="migrate vault: no audit ledger' \
  '            channel="vault", aborted=False, migrated=0,
            message="migrate vault: no audit ledger' "$T"

mutant "M11 the per-bundle migrate_channel record deleted" "$F" \
  '        ledger.record("migrate_channel", channel="vault", tag=tag, to=dst.name)' \
  '        pass' "$T"

mutant "M12 the record names the wrong bundle" "$F" \
  '        ledger.record("migrate_channel", channel="vault", tag=tag, to=dst.name)' \
  '        ledger.record("migrate_channel", channel="vault", tag="?", to=dst.name)' "$T"

# ---- the scan — deliverable 1, THE STAGE ------------------------------------------------------

mutant "M04 the gate's verdict ignored — a refused bundle is re-homed anyway" "$F" \
  '        if not outcome.committed:' '        if False:' "$T"

mutant "M05 the gate's verdict inverted" "$F" \
  '        if not outcome.committed:' '        if outcome.committed:' "$T"

mutant "M08 nothing is handed to the scanner" "$F" \
  '            WriteRequest("config", dst.location(tag), content=blob, actor="migrate",' \
  '            WriteRequest("config", dst.location(tag), content="", actor="migrate",' "$T"

mutant "M09 the gate loses its ledger — the write_gate decisions vanish" "$F" \
  '    write_gate = WriteGate(ledger=ledger)' '    write_gate = WriteGate()' "$T"

mutant "M18 the write ESCAPES the commit callable (behaviour)" "$F" \
  '            commit=(lambda tag=tag, blob=blob: dst.write_bundle(tag, blob)),
            human_approved=not assume_yes, assume_yes=assume_yes)' \
  '            human_approved=not assume_yes, assume_yes=assume_yes)
        dst.write_bundle(tag, blob)' "$T"

# ---- the refusal is announced, and secret-safe ------------------------------------------------

mutant "M06 the refusal goes silent" "$F" \
  '            emit(f"migrate vault: bundle '"'"'{tag}'"'"' was REFUSED at the write gate — not re-homed. "
                 f"{outcome.reason}")
            continue' \
  '            continue' "$T"

mutant "M07 the announcement echoes the bundle bytes (PLANTED SECRET LEAK)" "$F" \
  '                 f"{outcome.reason}")' '                 f"{blob}")' "$T"

# ---- one human decision, not N (the 1a-FU ruling) ---------------------------------------------

mutant "M10 the batch decision becomes one prompt per bundle" "$F" \
  '            human_approved=not assume_yes, assume_yes=assume_yes)' \
  '            confirm=confirm)' "$T"

# ---- the two flags carry the batch decision TRUTHFULLY -----------------------------------------
# GATE-HUMAN-APPROVED-CONFLATES-ASSUME-YES. Until this fix the batch decision rode in as a
# hardcoded `human_approved=True`, and the list below USED to declare that swapping the two flags
# was an equivalent mutant nobody could catch. That declaration is now FALSE and these three are
# why: the pins read the flags off a spy wrapped round the REAL `WriteGate.submit`, so a lie told
# to the gate is caught at the call site rather than only once a trust policy exists to punish it.

mutant "M20 the pre-fix conflation restored — under --yes the gate is told a human approved" "$F" \
  '            human_approved=not assume_yes, assume_yes=assume_yes)' \
  '            human_approved=True, assume_yes=assume_yes)' "$T"

mutant "M21 the stand-in approval is not carried at all" "$F" \
  '            human_approved=not assume_yes, assume_yes=assume_yes)' \
  '            human_approved=not assume_yes)' "$T"

mutant "M22 the two flags inverted — a real human decision submitted as a stand-in" "$F" \
  '            human_approved=not assume_yes, assume_yes=assume_yes)' \
  '            human_approved=assume_yes, assume_yes=not assume_yes)' "$T"

# ---- the do-not-change set ---------------------------------------------------------------------

mutant "M13 fail-closed consent flipped open" "$F" \
  '        gate = confirm or (lambda _t: False)' \
  '        gate = confirm or (lambda _t: True)' "$T"

mutant "M14 the idempotence skip removed" "$F" \
  '        if dst.read_bundle(tag) is not None:' '        if False:' "$T"

mutant "M15 the integrity verification removed" "$F" \
  '            SB.verify_bundle(json.loads(blob))' '            json.loads(blob)' "$T"

mutant "M16 the migration becomes DESTRUCTIVE" "$F" \
  '        migrated += 1
        ledger.record("migrate_channel"' \
  '        src.delete_bundle(tag)
        migrated += 1
        ledger.record("migrate_channel"' "$T"

mutant "M17 the typed degrade stops saying why" "$F" \
  '                                    message=f"migrate vault: cannot resolve the canonical "
                                            f"transport ({exc}) — nothing re-homed.")' \
  '                                    message="migrate vault: cannot resolve the canonical "
                                            "transport — nothing re-homed.")' "$T"

# ---- the register's own claim — deliverable 3's closing loop -----------------------------------
# SAME mutation as M18, graded by the SWEEP. If this one goes GREEN, direction B has stopped being
# able to convict the regression the deleted exception used to describe, and the register is free
# to carry a false GATED entry again.

mutant "M19 the write ESCAPES the commit callable (graded by the SI.6 sweep)" "$F" \
  '            commit=(lambda tag=tag, blob=blob: dst.write_bundle(tag, blob)),
            human_approved=not assume_yes, assume_yes=assume_yes)' \
  '            human_approved=not assume_yes, assume_yes=assume_yes)
        dst.write_bundle(tag, blob)' "$S"

printf '\n================================================================================\n'
printf 'stage 17 mutants: %s of %s  |  RED %s  |  GREEN(survivors) %s\n' "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a real finding about a real pin:\n%s' "$survivors"
fi
printf '================================================================================\n'
[ "$green" -eq 0 ]
