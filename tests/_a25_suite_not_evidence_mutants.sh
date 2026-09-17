#!/usr/bin/env bash
# Drives the 0.0.20 suite-is-not-evidence mutants through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a25_suite_not_evidence_mutants.sh
#
# 🔴 THE SUBJECT IS AN INSTRUCTION, AND AN INSTRUCTION HAS TWO WAYS TO STOP APPLYING: it can be
# WEAKENED, or it can simply not REACH the surface the agent reads. Both shipped OSS #65.
#
#   W01-W04 ★ THE WEAKENING. Each puts back one clause of the pre-#65 instruction: the seam, the
#           boundary values, the reviewer's trust in the suite, the rationalization that called all
#           of it scope creep.
#   D01-D02 ★ THE DRIFT. `skills.py` is the source and the agent NEVER READS IT — the running
#           instruction is the command template and the SKILL.md. A mutant that edits only a mirror
#           is the shape where the registry says one thing and the agent is told another.
#   C01-C02 ★ THE CONTROLS. Each removes something the fix must NOT have cost: the scope rule that
#           keeps specs provable, and the reviewer's independence. A fix that bought the new
#           behaviour with either of those is a worse trade than the defect.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

S=src/mokata/skills.py
A=src/mokata/skill_anatomy.py
C=src/mokata/templates/commands/test.md
K=src/mokata/skills/review/SKILL.md
T='test_a25_the_suite_is_not_the_evidence.py'

TOTAL=9
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

# ==== W. the weakening — the pre-#65 instruction, restored one clause at a time =================

mutant "W01 ★★ the SEAM clause goes — the AC is proven at the producer again" "$S" \
  '            "AS THE CONSUMER READS IT (its shape, its length, its truthiness). A unit test that "' \
  '            "as the producer writes it. A unit test that "' "$T"

mutant "W02 ★ the producer-only test stops being called worthless" "$S" \
  '            "it, passes and proves nothing. (b) THE ADVERSARIAL VALUES: for each AC, name its "' \
  '            "it, is usually fine. (b) THE ADVERSARIAL VALUES: for each AC, name its "' "$T"

mutant "W03 ★★ the FALSY case goes — a 0 value is never fed, which is #65 own example" "$S" \
  '            "empty, zero and other FALSY-BUT-PRESENT values, absent versus present-and-empty, "' \
  '            "a reasonable example, "' "$T"

mutant "W04 ★★ the reviewer trusts the suite again" "$S" \
  '    "test run (never the builder'"'"'s claim). ⛔ THE SUITE IS THE BUILDER'"'"'S ARTEFACT TOO: a green "' \
  '    "test run (never the builder'"'"'s claim). A green "' "$T"

mutant "W05 ★★ the rationalization calls the boundary scope creep again — the #65 instruction" "$A" \
  '               "New BEHAVIOUR is scope creep — amend the spec. But the approved AC at its "' \
  '               "Unapproved coverage is scope creep. Not at its "' "$T"

# ==== D. the drift — the registry moves and the agent is not told ==============================

mutant "D01 ★★ the COMMAND TEMPLATE keeps the old text while the registry has the new" "$C" \
  'AS THE CONSUMER READS IT' \
  'as the producer writes it' "$T"

mutant "D02 ★★ the AGENT SKILL keeps the old text — the auto-engaged surface, silently stale" "$K" \
  'THE SUITE IS THE BUILDER'"'"'S ARTEFACT TOO' \
  'The suite is helpful' "$T"

# ==== C. the controls — what the fix must NOT have cost =========================================

mutant "C01 ★ scope discipline is deleted instead of narrowed — specs stop being provable" "$S" \
  '            "shape you expect. Test ONLY the approved acceptance criteria — do not invent "' \
  '            "shape you expect. Cover what you judge useful — do not invent "' "$T"

mutant "C02 ★ the reviewer stops being independent — the half #65 did NOT fault" "$S" \
  '    "\n\nRun this review INDEPENDENTLY by default (this is the closing gate, not a self-check). "' \
  '    "\n\nRun this review inline, reusing your own context. "' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A25 SUITE-NOT-EVIDENCE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
