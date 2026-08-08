#!/usr/bin/env bash
# Drives the mutant list in `_stage5_mutants.txt` through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage5_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit.
#
# ⚠ THIS RE-IMPLEMENTS THAT CONTRACT rather than sharing `_run_mutants.sh`'s copy — the known
# duplication filed as MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). Not fixed here; this stage's
# scope is D1 and the registry, and quietly refactoring a reviewed driver is the drive-by doc 00
# forbids. The end state is one shared driver; this is not it.
#
# ⚠ TWO MUTANTS TARGET `src/`, NOT THE TEST HELPER — M09 plants a LIVE `graph_gate=` in a reached
# CLI handler (what 0.0.19's wiring will look like) and M10 re-promotes the certified zero to a
# backed registry entry. They are the §7i grades: this stage's subject is an ABSENCE, and a sweep
# over a healthy tree that finds nothing proves nothing.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
G=tests/_gatereach.py
S=src/mokata/skill_contracts.py
D1='test_d1_gate_unreachable.py'
GR='test_gate_reachability.py'

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

# ---- the new derivation: `keyword_origination` ------------------------------------------------

mutant "M01 keyword match inverted" "$G" \
  '            if not any(kw.arg == keyword for kw in node.keywords):' \
  '            if not any(kw.arg != keyword for kw in node.keywords):' "$D1"

mutant "M02 entry_reachable and -> or" "$G" \
  '                entry_reachable=owner is not None and (rel, owner) in reached))' \
  '                entry_reachable=owner is not None or (rel, owner) in reached))' "$D1"

mutant "M03 entry-reachability check dropped" "$G" \
  '                entry_reachable=owner is not None and (rel, owner) in reached))' \
  '                entry_reachable=True))' "$D1"

mutant "M04 owner lookup nulled" "$G" \
  '            owner = enclosure.owner_of(rel, node.lineno)' \
  '            owner = None' "$D1"

# M05 SURVIVED the first batch and was a real gap — `ast.walk` is breadth-first, so the sort is
# load-bearing and every corpus had at most one pass. `test_the_population_is_returned_in_source
# _order` was added for it; see `_stage5_mutants.txt`.
mutant "M05 source-order sort deleted" "$G" \
  '    return sorted(out, key=lambda p: (p.rel, p.lineno))' \
  '    return out' "$D1"

# ---- the registry moves -----------------------------------------------------------------------

mutant "M06 ship-readiness re-promoted to backed" "$S" \
  '        "recorded — an agent-facing protocol boundary, not a code gate",
        "", backed=False),' \
  '        "recorded — an agent-facing protocol boundary, not a code gate",
        "src/mokata/engine/ship.py"),' 'test_*.py'

mutant "M07 self-protect enforcement point repointed" "$S" \
  '        "src/mokata/selfprotect.py"),' \
  '        "src/mokata/govern/gate.py"),' "$GR"

# ---- the back-compat deletion -----------------------------------------------------------------
# ⚠ SCOPED TO `test_*.py` DELIBERATELY. Against `test_stage34_ship_and_baseline.py` alone this
# mutant SURVIVES — the not-approved path is pinned by `test_stage60_trust_visibility
# .test_ship_stays_human_gated_never_auto_merges` (the P2 human-gate invariant), not by the ship
# stage's own file. A narrower glob here would record a survivor that is a mis-scoped run.
mutant "M08 approved pinned True — the human gate ignored" src/mokata/engine/ship.py \
  '    approved = bool(approve)' '    approved = True' 'test_*.py'

# ---- §7i: the planted offenders, in the LIVE tree ---------------------------------------------

mutant "M09 SIMULATED 0.0.19 WIRING — a live graph_gate in a reached CLI handler" \
  src/mokata/cli_commands/approve.py \
  '    res = approval.approve(root, p.proposal_id, actor=args.actor, ledger=_ledger(root))' \
  '    res = approval.approve(root, p.proposal_id, actor=args.actor, ledger=_ledger(root), graph_gate=None)' \
  "$D1"

mutant "M10 PLANTED UNREACHABLE GATE — the certified zero re-promoted to backed" "$S" \
  '        "recorded — an agent-facing protocol boundary, not a code gate",
        "", backed=False),' \
  '        "recorded — an agent-facing protocol boundary, not a code gate",
        "src/mokata/engine/ship.py"),' "$GR"

printf '\n================================================================================\n'
printf 'stage 5 mutants: %s ran · %s RED · %s SURVIVED\n' "$ran" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS:\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf '================================================================================\n'
