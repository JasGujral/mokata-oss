#!/usr/bin/env bash
# Drives the 0.0.20 stage-05 wiring + G14-clause-3 mutants through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a23_graph_ts_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THIS BATCH GRADES TWO THINGS A WALKER ALONE IS NOT: that the GRAPH uses it, and that a user
# who cannot use it is TOLD. doc 105 §9 makes the second one load-bearing in terms — "if clause 3
# is not built, re-open G14" — so a silent announcement is not a missing nicety here, it is a
# ruling becoming wrong.
#
#   W01-W03 ★ THE WIRING. Each takes TypeScript back out of the graph, or lets it in with no
#     parser to read it.
#
#   A01-A04 ★ THE ANNOUNCEMENT (clause 3). A01 silences it outright. A02/A03 make it fire when it
#     must not — a warning a user cannot act on trains them to ignore doctor, which costs every
#     OTHER finding in the report.
#
#   S01-S02 THE THREE STATES (§7g). Each collapses "could not ask" into "nothing to say".
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

B=src/mokata/knowledge/ast_backend.py
D=src/mokata/govern/doctor.py
T='test_a23_the_graph_reads_typescript.py'

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

# ==== W. the wiring =============================================================================

mutant "W01 ★ TypeScript never enters the corpus — the walker exists and the graph ignores it" "$B" \
  '                elif ts_on and ts_edges.is_ts_source(fn):' \
  '                elif False:' "$T"

mutant "W02 ★ TS files enter the corpus with NO PARSER to read them" "$B" \
  '        ts_on = ts_edges.available()' \
  '        ts_on = True' "$T"

mutant "W03 the parse dispatch ignores the extension, so TS goes to the Python parser" "$B" \
  '        if ts_edges.is_ts_source(abspath):' \
  '        if False:' "$T"

# ==== A. the announcement — G14 clause 3 ========================================================

mutant "A01 ★ CLAUSE 3 IS SILENT — the ruling that made [graph-ts] an extra becomes wrong" "$D" \
  '    if verdict != TS_UNPARSED:
        return []' \
  '    if True:
        return []' "$T"

mutant "A02 ★ it fires even when the parser IS installed — a warning nobody can act on" "$D" \
  '    return (TS_PARSED if installed else TS_UNPARSED), count' \
  '    return TS_UNPARSED, count' "$T"

mutant "A03 ★ node_modules counts, so a Python repo with one dependency is a TypeScript repo" "$D" \
  '            prune_source_dirs(dirpath, dirnames)
            for name in filenames:
                if is_source(name):' \
  '            for name in filenames:
                if is_source(name):' "$T"

mutant "A04 the remedy stops naming the command that fixes it" "$D" \
  '        "the majority who never open a .ts file. Fix: run `pip install '"'"'mokata[graph-ts]'"'"'`, then "' \
  '        "the majority who never open a .ts file. "' "$T"

# ==== S. the three states (§7g) =================================================================

mutant "S01 ★ an unwalkable tree reports NO TYPESCRIPT instead of UNDECIDABLE" "$D" \
  '        return TS_UNDECIDABLE, 0' \
  '        return TS_NOT_APPLICABLE, 0' "$T"

mutant "S02 UNDECIDABLE stops producing an info finding and passes silently" "$D" \
  '    if verdict == TS_UNDECIDABLE:' \
  '    if False:' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A23 GRAPH-TS MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
