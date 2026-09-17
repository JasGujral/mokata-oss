#!/usr/bin/env bash
# Drives the 0.0.20 vendored-source corpus mutants through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a21_vendor_dirs_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THIS SUBJECT HAS TWO OPPOSITE FAILURE MODES AND THE BATCH MUST GRADE BOTH. Pruning too little
# puts somebody else's source in the user's graph — the defect this closed. Pruning too much
# DELETES THE USER'S OWN CODE FROM THEIR OWN GRAPH, silently, and that is the harder one to notice
# because the answer just gets quieter. P01-P03 restore the leak; O01-O03 cause the deletion.
#
#   P01-P03 ★ THE LEAK. Each puts node_modules back in the corpus.
#   O01-O03 ★ THE OVER-PRUNE. Each eats a directory the user hand-maintains.
#   D01-D02 THE DERIVATION. Each breaks "one declaration beside the extensions it belongs with".
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

W=src/mokata/repo_walk.py
L=src/mokata/languages.py
T='test_a21_vendored_source_is_not_the_users_graph.py'

TOTAL=8
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

# ==== P. the leak — the defect, restored ========================================================

mutant "P01 ★ the prune is removed — node_modules is back in the user's graph" "$W" \
  '        if name in VENDOR_DIRS:
            continue' \
  '        if False:
            continue' "$T"

mutant "P02 ★ javascript stops declaring node_modules, so the set is empty" "$L" \
  '    vendor_dirs=("node_modules",),' \
  '    vendor_dirs=(),' "$T"

mutant "P03 the union reads a field that does not exist, so the set is silently empty" "$W" \
  '    from .languages import VENDOR_DIRS' \
  '    VENDOR_DIRS = frozenset()' "$T"

# ==== O. the over-prune — the user's own code, deleted from their own graph =====================

mutant "O01 ★ the match becomes a SUBSTRING — my_node_modules_helper is eaten too" "$W" \
  '        if name in VENDOR_DIRS:' \
  '        if any(v in name for v in VENDOR_DIRS):' "$T"

mutant "O02 ★ go's vendor/ is pruned — a hand-maintained source directory, silently gone" "$L" \
  '    name="go",
    extensions=(".go",),' \
  '    name="go",
    vendor_dirs=("vendor",),
    extensions=(".go",),' "$T"

mutant "O03 every directory is pruned, so the corpus is empty and every answer is quiet" "$W" \
  '        if name in VENDOR_DIRS:
            continue' \
  '        if True:
            continue' "$T"

# ==== D. the derivation ========================================================================

mutant "D01 ★ the union reads ONE language instead of the supplied table" "$L" \
  '    return frozenset(name for lang in table.values() for name in lang.vendor_dirs)' \
  '    return frozenset(JAVASCRIPT.vendor_dirs)' "$T"

mutant "D02 an install dir is reported as a skipped CHECKOUT — noise on every JS repo" "$W" \
  '        if name in VENDOR_DIRS:
            continue' \
  '        if name in VENDOR_DIRS:
            if skipped is not None:
                skipped.append(os.path.join(dirpath, name))
            continue' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A21 VENDOR-DIR MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
