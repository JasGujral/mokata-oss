#!/usr/bin/env bash
# Drives 0.0.20 G13's mutants through scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a13_typed_imports_mutants.sh
#
# WHAT THIS BATCH GRADES: that an import edge's KIND is a fact and not decoration. The ruling it
# comes from (doc 105 §9 G13) turns on §7g — a contract dependency and a runtime one may not share
# a representation — so every mutant below collapses them back together, in one direction or the
# other, and requires the tests to red on it.
#
# ⚠ THE SEVENTH COPY OF THE DRIVER CONTRACT: MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

AST=src/mokata/knowledge/ast_backend.py
T='test_a13_import_edges_are_typed.py'

TOTAL=6
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

# ==== the two facts stop being two ============================================================

mutant "N01 ★★★ THE COLLAPSE, DIRECTION A — the guard is never recognised, so every TYPE_CHECKING
       import is reported as a RUNTIME dependency that does not exist" "$AST" \
  '    if not isinstance(node, ast.If):
        return False' \
  '    if True:
        return False' "$T"

mutant "N02 ★★★ THE COLLAPSE, DIRECTION B — EVERY `if` is a type guard, so a conditional runtime
       import (a version check, a platform branch) is reported as creating no runtime edge" "$AST" \
  '    if not isinstance(node, ast.If):
        return False' \
  '    if not isinstance(node, ast.If):
        return True' "$T"

mutant "N03 ★★★ THE ELSE BRANCH INHERITS THE TYPE KIND — the RUNTIME half of the idiom is marked
       as type-only, inverting the fact for exactly the pattern the idiom exists to express" "$AST" \
  '                for inner in child.orelse:
                    walk(ast.Module(body=[inner], type_ignores=[]), scope, in_class, import_kind)' \
  '                for inner in child.orelse:
                    walk(ast.Module(body=[inner], type_ignores=[]), scope, in_class, IMPORT_TYPE)' "$T"

mutant "N04 ★★ the kind stops travelling into nested scopes — an import inside a function under a
       type guard silently becomes a runtime edge again" "$AST" \
  '                edges.imports.extend(_import_edges(child, import_kind))' \
  '                edges.imports.extend(_import_edges(child))' "$T"

# ==== and the record stops being able to carry it =============================================

mutant "N05 ★★★ THE CACHE VERSION DOES NOT MOVE — a cache written before the kind existed hydrates
       into the new reader, which is the silent shape mismatch the bump exists to turn into a
       re-parse" "$AST" \
  'CACHE_SCHEMA_VERSION = 2' \
  'CACHE_SCHEMA_VERSION = 1' "$T"

mutant "N06 ★★★ the old two-element row is GUESSED into a runtime edge instead of dropped — and
       the guess is wrong for every TYPE_CHECKING import the old walker recorded" "$AST" \
  '            imports=[tuple(x) for x in d.get("imports", []) if len(x) == 3],' \
  '            imports=[tuple(x) if len(x) == 3 else (x[0], x[1], IMPORT_VALUE)
                     for x in d.get("imports", [])],' "$T"

printf '\n================================================================================\n'
printf 'G13 TYPED-IMPORT MUTANTS: %s ran of %s — %s RED / %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
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
