#!/usr/bin/env bash
# Drives the 0.0.20 `INDEX-NEXT-FREE-IS-MANUAL` mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_a15_docs_index_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 WHY THIS BATCH EXISTS AND NOT JUST THE TESTS. The subject is a field that went stale FIVE
# times while a note above it prescribed the fix. The one thing that must not happen is a
# derivation that is itself decorative — a guard printed rather than grading, which is the same
# defect one layer up. ⭐ §7i is the live risk here specifically: `INDEX.md` was CORRECTED in the
# same change that added the guard, so the live assertion passes trivially and every claim about
# catching staleness rests on the planted controls. These mutants are what grades them.
#
#   D01-D03 THE DERIVATION. Each shrinks or shifts the number space so a taken number would be
#     handed out. D02 is the one that matters most — dropping the archive is the same defect one
#     directory deeper, and it is invisible from the live tree.
#
#   F01-F03 THE THREE FIELD STATES (§7g). Each collapses one of DECLARED / FIELD_ABSENT /
#     UNPARSEABLE into another, which is precisely how "nobody checked" came to look like
#     "checked and fine" for five releases.
#
#   G01-G02 THE VERDICT. Each makes the grader answer CURRENT where it cannot know.
#
#   P01-P03 THE PAIRING EXCUSE. `audit-brief-` is the ONE sanctioned reason two docs share a
#     number. Each of these widens the excuse, and a widened excuse hides real collisions —
#     the harm this whole guard is here to measure.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

D=tests/_docs_index.py
T='test_a15_docs_index_next_free_is_derived.py'

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

# ==== D. the derivation ========================================================================

mutant "D01 ★ next-free is the highest CLAIMED number — the next author collides immediately" "$D" \
  '    return max(claimed) + 1' \
  '    return max(claimed)' "$T"

mutant "D02 ★ the ARCHIVE is dropped — a retired doc's number is handed out again" "$D" \
  '    for sub in ("", "archive"):' \
  '    for sub in ("",):' "$T"

mutant "D03 the filename is SEARCHED, not anchored — a number mid-name claims a slot" "$D" \
  '            m = _NUMBERED.match(name)' \
  '            m = _NUMBERED.search(name)' "$T"

# ==== F. the three field states (§7g) ==========================================================

mutant "F01 a MISSING field reads as an unreadable one — two facts, one representation" "$D" \
  '        return Field(FIELD_ABSENT)' \
  '        return Field(UNPARSEABLE)' "$T"

mutant "F02 an UNREADABLE value reads as absent, losing the raw text a reader needs" "$D" \
  '        return Field(UNPARSEABLE, None, raw)' \
  '        return Field(FIELD_ABSENT)' "$T"

mutant "F03 the LAST match wins, so the index's own drift COMMENTARY is graded" "$D" \
  '    m = _NEXT_FREE.search(text or "")' \
  '    m = ([None] + list(_NEXT_FREE.finditer(text or "")))[-1]' "$T"

# ==== G. the verdict ===========================================================================

mutant "G01 ★ a stale field is called CURRENT — the whole defect, restored" "$D" \
  '    if field.value < derived:' \
  '    if False:' "$T"

mutant "G02 an UNREADABLE field is graded as a pass — §7f, a clean result meaning UNGRADABLE" "$D" \
  '    if field.state != DECLARED or derived is None:
        return Verdict(UNGRADABLE, None, derived, field)' \
  '    if False:
        return Verdict(UNGRADABLE, None, derived, field)' "$T"

# ==== P. the pairing excuse ====================================================================

mutant "P01 THREE documents on one number are called a pair" "$D" \
  '    if len(paths) != 2:' \
  '    if len(paths) < 2:' "$T"

mutant "P02 TWO briefs and no audit are called a pair" "$D" \
  '    return len(briefs) == 1' \
  '    return len(briefs) >= 1' "$T"

mutant "P03 the excuse keys on the WORD anywhere in the name, not on the prefix" "$D" \
  '    briefs = [s for s in stems if s.startswith(_PAIRED_PREFIX)]' \
  '    briefs = [s for s in stems if _PAIRED_PREFIX in s]' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A15 DOCS-INDEX MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
