#!/usr/bin/env bash
# The 0.0.21 stage-14 THIRD-PASS review's mutant list, through scripts/mutate.sh (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage14_review2_mutants.sh
#
# 🔴 WHY A THIRD FILE. `_stage14_freshness_mutants.sh` is the builder's (12/12).
# `_stage14_review_mutants.sh` is the first two reviewers' survivors (13/13). This is the THIRD
# reviewer's, and the reason to keep them apart is the score history itself:
#
#     builder's batch           12/12 RED   <- and nine reviewer mutants then survived
#     first reviewers' batch    13/13 RED   <- and ELEVEN more then survived
#     this batch                  ?
#
# ⭐ Eleven of the thirteen below are the reviewer's own, verbatim where the line still exists. Ten of
# those eleven were GREEN against a28+a29 on the day they were written, and five survived the wider
# `test_gr_s3_*` suite too. The pattern they exposed is §7j: the 32-subset verdict guard derived ONE
# axis of a SIX-argument function and typed the other five, so every weakening keyed on a consumer
# string, a mention count, a target count or the notice was invisible — including `graph.required`
# DEFAULTING OFF, which opens the whole gate.
#
#   A01-A04 ★★★ the four axes the old guard typed to constants.
#   A05     ★★★ the DEFAULT. doc 85 calls required-on the D1 differentiator; nothing pinned it,
#     because every test passes `required=` explicitly.
#   A06     ★★★ the notice riding a non-refusal — the one-time banner spent on a pass.
#   A07-A08 ★★★ two FAIL-OPEN mutations that survived both suites: the gate's own input forced
#     clean, and `UNDERIVABLE` silently skipped on the only path that fires.
#   A09-A10 ★★★ the remedy precedence, and the hint branch that let B-F1 back in.
#   N01-N03 ★★★ the three NEW fixes: adoption-vs-availability, git's quoted paths, and the additive
#     baseline that closes A4/A5 for 7 ms rather than the 843 ms this module said it would cost.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

G=src/mokata/govern/graph_required.py
I=src/mokata/brainstorm_impact.py
S=src/mokata/mcp/tools_spec.py
F=src/mokata/knowledge/freshness.py
TD='test_a29_the_degrade_reason_is_not_one_word.py'
TF='test_a28_the_graph_notices_you_edited_it.py'

TOTAL=12
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

# ==== A · the axes the verdict guard had typed (§7j) ===========================================

mutant "A01 ★★★ the gate stops refusing for the LIVE consumer string (reviewer's R1)" "$G" \
  '    refused = bool(degraded and required and not overridden)' \
  '    refused = bool(degraded and required and not overridden) and consumer != "blast radius (Lens 1)"' "$TD"

mutant "A02 ★★★ any degraded radius with a grep hit is ALLOWED (reviewer's R2)" "$G" \
  '    refused = bool(degraded and required and not overridden)' \
  '    refused = bool(degraded and required and not overridden) and not mentions' "$TD"

mutant "A03 ★★★ the gate opens once more than one file is touched (reviewer's R3)" "$G" \
  '    refused = bool(degraded and required and not overridden)' \
  '    refused = bool(degraded and required and not overridden) and files < 1' "$TD"

mutant "A04 ★★★ the gate opens for any approach naming 2+ targets (reviewer's R4)" "$G" \
  '    refused = bool(degraded and required and not overridden)' \
  '    refused = bool(degraded and required and not overridden) and len(list(targets or [])) < 2' "$TD"

mutant "A05 ★★★ graph.required DEFAULTS OFF — the whole D1 gate opens (reviewer's R10)" "$G" \
  '            REQUIRED_LEAF, True))' \
  '            REQUIRED_LEAF, False))' "$TD"

mutant "A06 ★★★ the one-time notice rides a NON-refusal and is spent on a pass (R9)" "$G" \
  '        overridden=bool(overridden), refused=refused, reason=reason,
        reasons=tuple(reasons or ()), notice=notice if refused else None)' \
  '        overridden=bool(overridden), refused=refused, reason=reason,
        reasons=tuple(reasons or ()), notice=notice)' "$TD"

# ==== A · the two fail-open mutations that survived both suites ================================

mutant "A07 ★★★ the derivation FAILS OPEN — a gate that cannot see becomes one that approves (R6)" "$G" \
  '    degraded = bool(getattr(impact, "graph_degraded", True))' \
  '    degraded = bool(getattr(impact, "graph_degraded", False))' "$TD"

mutant "A08 ★★★ 'mokata could not look' is silently skipped on the only live path (R5)" "$S" \
  '        if not degraded:
            return None' \
  '        if not degraded or basis == GR.UNDERIVABLE:
            return None' "$TD"

# ==== A · the remedy, and the hint branch that let B-F1 back in ================================

mutant "A09 ★★★ floor-empty stops outranking repair — the ruling table contradicted again (R8)" "$G" \
  '    if DEGRADE_FLOOR_FOUND_NOTHING in rs:
        return REMEDY_CHECK_TARGET
    if DEGRADE_CHAIN_IS_LEXICAL in rs:' \
  '    if DEGRADE_CHAIN_IS_LEXICAL in rs:' "$TD"

mutant "A10 ★★★ the hint's CHECK-TARGET branch is deleted — B-F1 verbatim (reviewer's R11)" "$G" \
  '    if remedy == REMEDY_CHECK_TARGET:
        # 🔴 REVIEW FINDING 3-4' \
  '    if False:
        # 🔴 REVIEW FINDING 3-4' "$TD"

# ==== N · the three new fixes =================================================================

mutant "N01 ★★★ ADOPTION is ignored again — an adopted-but-dead graph says 'adopt a graph' (3-1)" "$I" \
  '                elif pinned_tool:' \
  '                elif False:' "$TD"

# ⛔ N02 WAS WITHDRAWN AT THE FOURTH REVIEW PASS, and it is recorded here rather than deleted.
#
#   N02 was "git quotes non-ASCII paths again", i.e. deleting `-c core.quotePath=false` from
#   `_no_quote_path`. It graded finding 3-2 honestly AT THE TIME. Finding 4-5 then showed that flag
#   closed only one of four quoting classes and added `-z` to both readers — and with `-z` git does
#   not quote AT ALL, so from that moment no input distinguished the flag's presence from its
#   absence and N02 became an EQUIVALENT mutant. It duly ran GREEN against a 70-test module.
#
#   §7f: two redundant defences are untestable — separate them or delete one. `_no_quote_path` is
#   DELETED (§7d, pre-1.0), `-z` is the single graded defence, and the class is graded by Q01/Q02 in
#   `_stage14_review3_mutants.sh`. The lesson is that a mutant can be killed by a LATER fix as well
#   as by a regression, and a batch nobody re-runs will not tell you which.

mutant "N03 ★★★ the absence TOMBSTONE goes back to a pop — A5 reopens (3-5)" "$F" \
  '                base.entries[rel] = IndexEntry(rel, ABSENT, 0.0, -1)' \
  '                base.entries.pop(rel, None)' "$TF"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE 14 REVIEW-2 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a REVIEW FINDING whose fix does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
