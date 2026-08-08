#!/usr/bin/env bash
# Drives the mutant list in `_stage2_shim_mutants.txt` through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage2_shim_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit.
#
# ⚠ THE FIFTH COPY OF THAT CONTRACT — MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). `_run_mutants.sh`,
# `_stage16_mutants.sh`, `_stage17_mutants.sh` and `_stage21_mutants.sh` each carry their own.
# Recording the fifth instance is what stops "we'll unify it later" being said a fifth time.
#
# A THIRD OF THIS BATCH IS AIMED AT THE TESTS THEMSELVES (M13–M18). Three stages running have
# found a pin that was green while grading nothing, and this stage SHIPS pins whose whole job is
# to grade other pins — so the guards are graded here too, or the stage is asserting its own
# honesty on its own authority.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

X=tests/_translating.py                   # the mechanism: declaration + enforcement
G=tests/test_shim_declaration.py          # the sweep + the runtime-refusal pins
F=tests/test_db_s3_fts.py                 # the suite whose declaration was measurably false
S=tests/test_ms_s5_single_flusher.py      # the suite no name-based sweep had ever found

D='test_shim_declaration.py'              # the stage's own pins
F_T='test_db_s3_fts.py'
S_T='test_ms_s5_single_flusher.py'
V='test_db_s4_pgvector.py'

TOTAL=18
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

# ---- 1 · THE REFUSAL IS REAL (the enforcement half of "declared AND enforced") ----------------

mutant "M01 the refusal goes inert — every undeclared translation is silently executed" "$X" \
  '        if cached:' \
  '        if False:' "$D"

mutant "M02 the analysis finds nothing — the divergence table is never consulted" "$X" \
  '        problems = []
        declared' \
  '        return []
        problems = []
        declared' "$D"

mutant "M03 a surviving %s is no longer a divergence — the literal-match false green returns" "$X" \
  '    ("%s", "unbound Postgres placeholder — SQLite reads `%s` as a LITERAL, so the query still "' \
  '    ("\x00never-matches", "unbound Postgres placeholder — SQLite reads `%s` as a LITERAL, so the query still "' \
  "$D"

mutant "M04 WITH RECURSIVE is allowed — the MEASURED DB.S7b hazard walks back in" "$X" \
  '        if _RECURSIVE.search(sql) and DIVERGENCE_RECURSIVE_CTE not in accepted:' \
  '        if False:' "$D"

mutant "M05 text-collation ordering stops being a divergence" "$X" \
  '        if DIVERGENCE_TEXT_COLLATION not in accepted:' \
  '        if False:' "$D"

mutant "M06 a declaration no longer has to say what it does NOT prove" "$X" \
  '        if not self.not_proven:' \
  '        if False:' "$D"

# ---- 2 · THE DECLARED RULES ARE WHAT RUNS (not a list beside the code) ------------------------

mutant "M07 declared rewrites are never applied — the declaration becomes decoration" "$X" \
  '        for rule in self.declaration.rewrites:' \
  '        for rule in []:' "$D"

mutant "M08 a rewrite fires but is never counted — the translation log stops attributing" "$X" \
  '            if after != run:
                self._count(rule.name)' \
  '            if after != run:
                pass' "$D"

mutant "M09 the basis collapses — a translated pass reports itself as a live-Postgres pass" "$X" \
  '    SQLITE_TRANSLATED = "sqlite-translated"' \
  '    SQLITE_TRANSLATED = "postgres-live"' "$D"

mutant "M10 an interception no longer short-circuits — it falls through to the engine" "$X" \
  '        for icept in self.declaration.interceptions:' \
  '        for icept in []:' "$D"

# ---- 3 · THE SUITE DECLARATIONS ARE LOAD-BEARING ----------------------------------------------

mutant "M11 DB.S3 loses the jsonb-value-accessor rewrite — the rewrite this stage FOUND" "$F" \
  '        Rewrite.literal(
            "jsonb-value-accessor", "(doc::jsonb->>'"'"'value'"'"')", "pg_doc_value(doc)",' \
  '        Rewrite.literal(
            "jsonb-value-accessor", "(never::matches->>'"'"'value'"'"')", "pg_doc_value(doc)",' \
  "$F_T"

mutant "M12 MS.S5 loses the now() rewrite — the semantic substitution nobody had declared" "$S" \
  '        Rewrite.literal(
            "transaction-clock", "now()", "CURRENT_TIMESTAMP",' \
  '        Rewrite.literal(
            "transaction-clock", "never()", "CURRENT_TIMESTAMP",' \
  "$S_T"

# ---- 4 · AIMED AT THE TESTS THEMSELVES — do the guards actually grade anything? ---------------

mutant "M13 the sweep's rewrite detector goes blind — it can no longer see any translation" "$G" \
  '    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in _REWRITE_METHODS:' \
  '    if False:
        if node.func.attr in _REWRITE_METHODS:' "$D"

mutant "M14 the sweep allow-lists everything — every offender is skipped before it is reported" "$G" \
  '        if (site["path"], site["scope"]) in allow_list:' \
  '        if True:' "$D"

mutant "M15 the stale-entry check is dropped — a permission outlives its reason forever" "$G" \
  '    return sorted(k for k in allow_list if k not in live)' \
  '    return []' "$D"

mutant "M16 the module-set drift check reports nothing — one-way, so a new shim is unnoticed" "$G" \
  '    return sorted(found - expected), sorted(expected - found)' \
  '    return [], []' "$D"

mutant "M17 the bypass check always says the subclass delegated" "$G" \
  '                delegates = True
                if override is not None:' \
  '                delegates = True
                if False:' "$D"

mutant "M18 the can-fire test plants something that does NOT translate — the guard grades nothing" "$G" \
  "                    \"        return self._c.execute(sql.replace('%s', '?'), tuple(params))\\n\")" \
  "                    \"        return self._c.execute(sql, tuple(params))\\n\")" "$D"

# ---- the record is UNCONDITIONAL -------------------------------------------------------------

printf '\n================================================================================\n'
printf 'STAGE 2 (SHIM-FALSE-GREEN) MUTANTS: %s RED / %s GREEN out of %s\n' "$red" "$green" "$TOTAL"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (a finding about a PIN, reported, never quietly dropped):\n%s' "$survivors"
    exit 1
fi
printf 'No survivors.\n'
printf '================================================================================\n'
