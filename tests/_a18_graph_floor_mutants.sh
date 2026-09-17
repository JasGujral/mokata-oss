#!/usr/bin/env bash
# Drives the 0.0.20 stage-11c `WIRED-GRAPH-CHAIN-PREDATES-THE-AST-PROVIDER` mutant list through
# scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a18_graph_floor_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THE RISK THIS BATCH IS AGAINST IS NOT "THE CHECK STOPS FIRING" — it is "the check fires when
# it should not". A doctor warning that nags a repo it cannot help is worse than no warning: it
# trains the user to ignore doctor, which costs every OTHER finding in the report. So the batch is
# weighted toward FALSE POSITIVES, and every one of those mutants is a change a well-meaning reader
# might make while "simplifying".
#
#   F01-F03 ★ THE FLOOR SET. Each widens what counts as a floor, and a widened floor set nags a
#     user about a provider they deliberately unwired or one that needs an install.
#
#   S01-S03 ★ THE FOUR STATES (§7g). Each collapses one into another. S02 is the one that matters
#     most: an unaskable question reading as an answered one is how a clean doctor run comes to
#     mean "your chain is fine" when nothing was checked.
#
#   A01-A02 THE APPLICABILITY PROBE. Each reports a floor that would answer NOTHING on this repo —
#     advice that a user follows and gets the same results from.
#
#   P01-P02 THE PROPOSAL. Each breaks the half that makes this a doctor check rather than a fix:
#     the command named, and the promise that a human is asked.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

D=src/mokata/govern/doctor.py
T='test_a18_the_graph_floor_is_re_offered.py'

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

# ==== F. the floor set — every mutant here is a NAG =============================================

mutant "F01 ★ the kind filter is dropped — a user who unwired ripgrep is nagged forever" "$D" \
  '                 if (entry or {}).get("kind") == "builtin"' \
  '                 if True' "$T"

mutant "F02 the capability match is dropped — a memory provider is offered to code_graph" "$D" \
  '                 and (entry or {}).get("provides") == need)' \
  '                 and True)' "$T"

mutant "F03 the kind test widens to 'not external' — cli and mcp providers become floors" "$D" \
  '                 if (entry or {}).get("kind") == "builtin"' \
  '                 if (entry or {}).get("kind") != "external"' "$T"

# ==== S. the four states (§7g) =================================================================

mutant "S01 a chain that already names the floor still reports MISSING" "$D" \
  '    if not candidates:
        return FLOOR_WIRED, ()' \
  '    if False:
        return FLOOR_WIRED, ()' "$T"

mutant "S02 ★ an UNASKABLE question reads as an ANSWERED one — a clean run means nothing" "$D" \
  '            return FLOOR_UNDECIDABLE, candidates
    if not answering:' \
  '            continue
    if not answering:' "$T"

mutant "S03 UNDECIDABLE is reported as a silent pass rather than an info finding" "$D" \
  '    if verdict == FLOOR_UNDECIDABLE:' \
  '    if False:' "$T"

# ==== A. the applicability probe ===============================================================

mutant "A01 ★ a repo with no Python is told to wire a floor that answers NOTHING" "$D" \
  '    if not answering:
        return FLOOR_NOT_APPLICABLE, candidates' \
  '    if False:
        return FLOOR_NOT_APPLICABLE, candidates' "$T"

mutant "A02 the probe result is ignored and every candidate counts as answering" "$D" \
  '            if det.is_present(tid, TOOL_CATALOG[tid]):
                answering.append(tid)' \
  '            answering.append(tid)' "$T"

# ==== P. the proposal ==========================================================================

mutant "P01 the finding stops naming the command that fixes it" "$D" \
  '        "Fix: run `mokata reconfigure --add %s` (previews the change and asks before writing) — "' \
  '        "Fix: re-run setup. %s"' "$T"

mutant "P02 the check is unreachable from diagnose() — a guard nothing calls" "$D" \
  '    findings.extend(graph_floor_findings(surface))' \
  '    pass' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A18 GRAPH-FLOOR MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
