#!/usr/bin/env bash
# Drives the 0.0.20 stage-05 TS-walker mutants through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a22_ts_walker_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 A GRAPH WALKER FAILS QUIETLY. It does not raise — it returns FEWER EDGES, and a smaller graph
# answers every query with a shorter list that looks exactly like a correct answer about a smaller
# codebase. Every mutant below drops or mis-types one edge class, which is the only failure mode
# this subject has.
#
#   H01-H03 ★ THE HERITAGE. `implementers` is the edge the row is most about — "who implements
#     this interface" is what the lexical floor cannot answer at all.
#
#   D01-D03 ★ THE DEFINITIONS. D01 is the row's own example: a walker that only knows
#     `function_declaration` reports ZERO defs for a React component file.
#
#   I01-I03 THE IMPORTS, including the type/value distinction G13 already built.
#
#   M01-M04 ★ THE MODULE-TOKEN RULE — the one thing the Python model does not transfer. Each
#     mutant restores the spelling nobody can type, or splits one module across two tokens.
#
#   C01-C02 THE CORPUS. C01 lets `.d.ts` in, which is the node_modules harm arriving through a
#     file that is not under node_modules.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

W=src/mokata/knowledge/ts_edges.py
T='test_a22_the_ts_walker_answers_the_graph.py'

TOTAL=15
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

# ==== H. the heritage — the edge the row is most about ==========================================

mutant "H01 ★ implements is dropped — 'who implements this interface' goes unanswered" "$W" \
  '        if clause.type not in ("extends_clause", "implements_clause"):' \
  '        if clause.type not in ("extends_clause",):' "$T"

mutant "H02 ★ extends is dropped — the class hierarchy loses its spine" "$W" \
  '        if clause.type not in ("extends_clause", "implements_clause"):' \
  '        if clause.type not in ("implements_clause",):' "$T"

mutant "H03 an interface stops being a class-like declaration and cannot be named" "$W" \
  '_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration", "interface_declaration"})' \
  '_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration"})' "$T"

# ==== D. the definitions ========================================================================

mutant "D01 ★ THE ROW'S OWN EXAMPLE: an arrow-function component is no longer a definition" "$W" \
  '_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function"})' \
  '_FUNCTION_VALUES = frozenset({"function_expression", "function"})' "$T"

mutant "D02 methods are typed as functions — the kind stops distinguishing them" "$W" \
  '                    edges.defs.append((name, _line(child), KIND_METHOD))' \
  '                    edges.defs.append((name, _line(child), KIND_FUNCTION))' "$T"

mutant "D03 an interface method signature is not a definition" "$W" \
  '_METHOD_NODES = frozenset({"method_definition", "method_signature"})' \
  '_METHOD_NODES = frozenset({"method_definition"})' "$T"

# ==== I. the imports ============================================================================

mutant "I01 ★ a type-only import is recorded as a runtime edge" "$W" \
  '        if child.type == "type":
            return True
    return False' \
  '        if child.type == "type":
            return False
    return False' "$T"

mutant "I02 a re-export stops being an import edge — the dependency disappears" "$W" \
  '            if kind == "export_statement" and _named_child(child, "string") is not None:' \
  '            if False:' "$T"

mutant "I03 local names are dropped, so an alias and a namespace bind nothing" "$W" \
  '                toks |= _local_names(_named_child(child, "import_clause"), source)' \
  '                pass' "$T"

# ==== M. the module-token rule — the thing that does not transfer ===============================

mutant "M01 ★ the raw relative specifier becomes a token — the spelling nobody can type" "$W" \
  '        parts = [p for p in body.split("/") if p not in ("", ".", "..")]' \
  '        parts = [p for p in body.split("/") if p != ""]' "$T"

mutant "M02 ★ the extension is kept, so ./util.js and ./util are two different modules" "$W" \
  '        if ext.lower() in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts") and stem:' \
  '        if False and stem:' "$T"

mutant "M03 the path TAIL is lost, so api/client cannot be named" "$W" \
  '    elif len(parts) > 1:
        # A relative path'"'"'s TAIL is nameable (`api/client`); its leading `./` is not.
        out.add("/".join(parts))' \
  '    elif False:
        out.add("/".join(parts))' "$T"

mutant "M04 a scoped package loses its bare form, so imports(scope) misses" "$W" \
  '        if part.startswith("@") and len(part) > 1:
            out.add(part[1:])' \
  '        if False:
            out.add(part[1:])' "$T"

# ==== C. the corpus =============================================================================

mutant "C01 ★ .d.ts is walked — a dependency's whole public API becomes the user's defs" "$W" \
  '    if low.endswith(DECLARATION_SUFFIX):
        return False' \
  '    if False:
        return False' "$T"

mutant "C02 plain JavaScript is claimed and handed to the TypeScript grammar" "$W" \
  'TS_EXTENSIONS: Tuple[str, ...] = (".ts", ".tsx", ".mts", ".cts")' \
  'TS_EXTENSIONS: Tuple[str, ...] = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx")' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A22 TS-WALKER MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
