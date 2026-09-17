#!/usr/bin/env bash
# Drives the 0.0.20 derived-evidence fix through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a14_derived_evidence_mutants.sh
#
# WHAT THIS BATCH GRADES: that the graph-required gate MEASURES its own evidence.
# `GRAPH-REQUIRED-GATE-TURNS-ON-A-BOOLEAN-THE-MODEL-WROTE` was invisible for two releases because
# the field defaulted to False and nothing computed it — so every mutant below hands the verdict
# back to the reporter, in one way or another, and requires the tests to red on it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

GR=src/mokata/govern/graph_required.py
TS=src/mokata/mcp/tools_spec.py
T='test_gr_s3_consumers.py'

TOTAL=5
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

# ==== the verdict goes back to the reporter ====================================================

mutant "P01 ★★★ THE ROW, RESTORED — the gate believes the persisted field again instead of the
       lens it just ran. This is the exact state 0.0.20 found the tree in" "$TS" \
  '        basis, degraded = GR.derive_graph_degraded(surface, targets)
        if not degraded:' \
  '        basis, degraded = GR.derive_graph_degraded(surface, targets)
        degraded = bool(getattr(imp, "graph_degraded", False)) if imp is not None else False
        if not degraded:' "$T"

mutant "P02 ★★★ the derivation always answers CLEAN — wired, called, and blind, which is the
       hardest of the three to notice because every surface still looks right" "$GR" \
  '    degraded = bool(getattr(impact, "graph_degraded", True))' \
  '    degraded = False' "$T"

mutant "P03 ★★ the empty-target shortcut swallows every case — an approach with targets is graded
       as though it named none, so nothing is ever queried and nothing is ever degraded" "$GR" \
  '    if not tgts:' \
  '    if True:' "$T"

# ==== and it stops failing closed ==============================================================

mutant "P04 ★★★ a lens that RAISES is read as a pass — a gate that cannot see becomes a gate that
       approves, which is the failure mode every gate in this repo is written against" "$GR" \
  '    except Exception:                                 # noqa: BLE001 — see the fail-closed note
        return UNDERIVABLE, True' \
  '    except Exception:                                 # noqa: BLE001 — see the fail-closed note
        return UNDERIVABLE, False' "$T"

mutant "P05 ★★★ NO LAYER AT ALL is read as a pass — the repo has no graph and the gate whose whole
       subject is 'was there a graph' says yes" "$GR" \
  '            return UNDERIVABLE, True
        impact = _lens_default(' \
  '            return UNDERIVABLE, False
        impact = _lens_default(' "$T"

printf '\n================================================================================\n'
printf 'DERIVED-EVIDENCE MUTANTS: %s ran of %s — %s RED / %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
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
