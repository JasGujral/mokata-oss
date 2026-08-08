#!/usr/bin/env bash
# Drives the stage-11 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage11_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. (Re-implements _run_mutants.sh's copy — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# ⚠ §7i. The real controls in sync-public.sh cover every required path, so every real-tree
# derivation is GREEN and grades nothing on its own. Two layers fix that:
#
#   C01/C02 BREAK THE REAL CONTROLS — drop `_to_delete` from `--exclude`, drop
#     `editors/vscode/out` from `INTERNAL_PATHS`. These are what grade the DOC-AGREEMENT pin:
#     under them the derived status flips to OPEN while doc 02 still says "✅ NO — closed", which
#     is precisely the stale-green this stage exists to make impossible.
#
#   B01-B08 attack the derivation itself, and B03/B04/B05 are the load-bearing ones: each makes
#     an UNDECIDABLE case render as a decided GREEN, which is the row's one prohibition
#     ("undecidable rows render UNKNOWN, never default-green").
#
# ⚠ NOT MUTATED: docs/build/02-mokata-build-status.md. It carries another lane's uncommitted
# edits in this worktree, and mutate.sh's restore, though byte-exact, is not worth pointing at a
# file whose contents this stage does not own. The doc side is graded from the CONTROL side
# instead (C01/C02), which flips the same comparison.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
B=tests/_mirror_bookkeeping.py
S=scripts/sync-public.sh
T='test_s11_bookkeeping_derived.py'

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

# ==== the REAL controls break, and the doc keeps claiming they are closed ======================

mutant "C01 sync-public.sh drops _to_delete from --exclude (doc still says closed)" "$S" \
  "  --exclude='_to_delete/' \\" \
  "  --exclude='_to_delete_NOT/' \\" "$T"

mutant "C02 sync-public.sh drops editors/vscode/out from INTERNAL_PATHS (doc still says closed)" "$S" \
  '  editors/vscode/node_modules editors/vscode/out' \
  '  editors/vscode/node_modules' "$T"

# ==== the derivation itself ====================================================================

mutant "B01 comments are no longer stripped (a commented-out entry counts as a control)" "$B" \
  '        ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))' \
  '        ln for ln in text.splitlines())' "$T"

B02_OLD=$(cat <<'EOF'
    end = body.find("\n)", start)
    if end == -1:
        return None
EOF
)
B02_NEW=$(cat <<'EOF'
    end = len(body)
    if end == -1:
        return None
EOF
)
mutant "B02 the guard becomes 'everything after INTERNAL_PATHS=(' — the loop counts as the array" \
  "$B" "$B02_OLD" "$B02_NEW" "$T"

# ★ THE ROW'S ONE PROHIBITION: an undecidable case must never render as a decided green.
mutant "B03 an unreadable script derives GREEN instead of UNDECIDABLE" "$B" \
  '    BASIS_SCRIPT_UNREADABLE: UNDECIDABLE,' \
  '    BASIS_SCRIPT_UNREADABLE: GREEN,' "$T"

mutant "B04 a script with no --exclude clauses derives GREEN instead of UNDECIDABLE" "$B" \
  '    BASIS_NO_EXCLUDE_CLAUSES: UNDECIDABLE,' \
  '    BASIS_NO_EXCLUDE_CLAUSES: GREEN,' "$T"

mutant "B05 glob-only coverage is GUESSED as GREEN instead of asking" "$B" \
  '    BASIS_PATTERN_COVERAGE: UNDECIDABLE,' \
  '    BASIS_PATTERN_COVERAGE: GREEN,' "$T"

mutant "B06 losing the hard-guard backstop stops being a defect" "$B" \
  '    BASIS_EXCLUDE_ONLY: RED,' \
  '    BASIS_EXCLUDE_ONLY: GREEN,' "$T"

mutant "B07 a path covered by NEITHER control derives GREEN" "$B" \
  '    BASIS_VERIFIED_NEITHER: RED,' \
  '    BASIS_VERIFIED_NEITHER: GREEN,' "$T"

B08_OLD=$(cat <<'EOF'
        if basis in UNDECIDABLE_BASES and not detail:
EOF
)
B08_NEW=$(cat <<'EOF'
        if False:
EOF
)
mutant "B08 an UNDECIDABLE may be constructed with no reason (a shrug reads as green)" "$B" \
  "$B08_OLD" "$B08_NEW" "$T"

mutant "B09 the guard-only defect (rsync still copies) stops being a defect" "$B" \
  '    BASIS_GUARD_ONLY: RED,' \
  '    BASIS_GUARD_ONLY: GREEN,' "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 11 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
