#!/usr/bin/env bash
# Drives exit criterion 5's mutants through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python bash tests/_ec5_mutant_contract_mutants.sh
#
# ⚠⚠ WHAT THIS BATCH IS FOR, STATED BEFORE THE SCORE (§7i/§7j).
#
# The subject is a SWEEP, and the bar names its target: MUTATE THE DERIVATION. A sweep has two
# axes — the property it checks and the corpus it checks it against — and a sweep that derives the
# first and types the second means "the part I remembered" while reading as a general claim. So
# D01-D06 leave every check byte-for-byte correct and only shrink WHAT IT RANGES OVER. If those
# come back GREEN the domain is unpinned and the rest of this stage is decorative.
#
# D03 in particular reproduces a defect this sweep ACTUALLY HAD when first run: skipping
# directories named `build` at any depth silently dropped `docs/build/handoff/`, and the sweep
# reported 26 drivers while reading as "every driver in the tree".
#
# C05-C07 are the other half: three checks whose ABSENCE manufactures a false positive on a driver
# that conforms. A sweep that over-reports is not merely noisy — the declared-nonconforming table
# is where a real finding would then hide.
#
# ⚠ WHAT THIS BATCH DOES NOT GRADE, and it is not an omission a mutant can fix:
#
#   * `BASELINE-WINDOW-BETWEEN-CHECK-AND-GRADE` (doc 84, 0.0.19/if-observed). Nothing here touches
#     it, nothing here narrows it, and no mutant of this code can grade a gap the code does not
#     claim to close — stage 31 said the same of its own.
#   * The `if`-chain driver shape. The sweep reads conformance off the `case`-arm idiom all 29
#     drivers use; a driver written another way would red. That is a DECLARED limit in the
#     module's header, not a mutant, because no input reachable from this tree distinguishes it.
#
# SPDX-License-Identifier: Apache-2.0
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
export PYTHON="${PYTHON:-python3}"

S=tests/_mutant_driver_contract.py
T='test_mutant_driver_contract.py'

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
        printf "  See scripts/mutate.sh's EXIT CONTRACT for what %s means and what to do.\n" "$rc"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  output: %s\n' "${out:-<nothing>}"
                exit 70 ;;
    esac
}

# ==== ★ A. THE DERIVATION — every check stays intact and only the DOMAIN moves =================

mutant "D01 ★★ the domain shrinks to tests/ — the split directories vanish" "$S" \
  '            rel = os.path.relpath(full, root).replace(os.sep, "/")' \
  '            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not rel.startswith("tests/"):
                continue' "$T"

mutant "D02 ★ the domain shrinks to the _*_mutants.sh naming convention" "$S" \
  '            if not filename.endswith(".sh"):' \
  '            if not filename.endswith(".sh") or not filename.startswith("_"):' "$T"

mutant "D03 ★★ THE DEFECT THIS SWEEP ITSELF HAD — build skipped by NAME at any depth" "$S" \
  '            if d not in SKIP_DIRS and (prefix + d) not in SKIP_PATHS]' \
  '            if d not in SKIP_DIRS and d not in SKIP_PATHS]' "$T"

mutant "D04 the mutator counts as a consumer of its own contract" "$S" \
  '            if rel == MUTATOR_RELPATH:
                continue' \
  '            if False:
                continue' "$T"

mutant "D05 ★★ dispatch keyed on the NAME mutant — the _stage19c false positive, restored" "$S" \
  '        if _LITERAL_MUTATOR_CALL.search(body) or any(' \
  '        if name == "mutant" and any(' "$T"

mutant "D06 ★ call sites counted for ONE dispatch function instead of every one" "$S" \
  "body)) for n in names)" \
  "body)) for n in sorted(names)[1:])" "$T"

# ==== B. THE DECLARATIONS — the two literals, and both are graded ==============================

mutant "L01 the declaration is never consulted — everything outside tests/ is undeclared" "$S" \
  '    if rel in DECLARED_INTERNAL_DRIVERS:' \
  '    if False:' "$T"

mutant "L02 every location reads as shipped — the third state collapses" "$S" \
    '    if rel.split("/")[0] == DRIVER_HOME:
        return LOCATION_SHIPPED
    return LOCATION_UNDECLARED' \
    '    return LOCATION_SHIPPED' "$T"

mutant "L03 the known-broken table is one element off and nothing notices" "$S" \
  '    "docs/build/handoff/05-mutants.sh": frozenset({
        RUN_COUNTED, VERDICT_COUNTED, NO_VERDICT_ABORTS, TOTAL_DECLARED}),' \
  '    "docs/build/handoff/05-mutants.sh": frozenset({
        RUN_COUNTED, VERDICT_COUNTED, NO_VERDICT_ABORTS}),' "$T"

mutant "L04 the internal root is a directory the mirror does NOT exclude" "$S" \
  'INTERNAL_ROOT = "docs/build"' \
  'INTERNAL_ROOT = "docs"' "$T"

# ==== C. THE CHECKS THAT STOP FALSE POSITIVES — absence manufactures a finding =================

mutant "C05 ★ case-arm comment stripping dropped — _sync_marker_drift reads as broken" "$S" \
  '            stripped = re.sub(r"^(?:\s*#[^\n]*\n)*", "", chunk)' \
  '            stripped = chunk' "$T"

# ★ C06a/C06b are the §7f pair. Both conditions excluded the same real offender, so mutating
# either one alone SURVIVED the first pass — two defences of one property, neither gradable. Each
# now has an offender only it can see, and each of these mutants must red on its own.
mutant "C06a ★★ PIN-SUBSTRING-COMMENT-HOLE — a mention anywhere counts as a binding" "$S" \
  '        if value.endswith(MUTATOR_REFERENCE) or "MUTATE_SH" in value:' \
  '        if MUTATOR_REFERENCE in value or "MUTATE_SH" in value:' "$T"

mutant "C06b ★ the single-token condition dropped — a whole sentence counts as a path" "$S" \
  '        if " " in value or "\t" in value:
            continue' \
  '        if False:
            continue' "$T"

mutant "C07 ★ the call-position test line-anchored — _run_mutants.sh reads as never stopping" "$S" \
  '            if re.search(r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b" % re.escape(n),' \
  '            if re.search(r"(?:^)[ \t]*%s\b" % re.escape(n),' "$T"

# ==== D. THE ELEMENT CHECKS THEMSELVES — each synthetic offender must still bite ===============

mutant "E01 the sweep finds nothing, ever — the §7i failure in its purest form" "$S" \
  '    missing = set()
    funcs = shell_functions(text)' \
  '    return frozenset()
    missing = set()
    funcs = shell_functions(text)' "$T"

mutant "E02 a status-0 run with no verdict is accepted" "$S" \
  '        if not default or not any(re.search(r"\bexit\b", a) for a in default):' \
  '        if False:' "$T"

mutant "E03 an abort that exempts one status is accepted" "$S" \
  '            if re.search(r"\$\{?%s\}?\"?[ \t]*(?:-ne|!=|-eq|==?)[ \t]*\"?[1-9]"' \
  '            if re.search(r"(?!x)x"' "$T"

mutant "E04 an abort that loses the mutator status is accepted" "$S" \
  '                elif not re.search(r"\bexit[ \t]+\"?\$\{?%s\b" % re.escape(rc), reached):' \
  '                elif False:' "$T"

# ==== verdict ==================================================================================

if [ "$ran" -ne "$TOTAL" ]; then
    printf '\nBATCH ABORTED — %s mutants ran but this driver declares TOTAL=%s.\n' "$ran" "$TOTAL"
    printf '  No score is printed for a list that did not run: a partial tally reads as a tally.\n'
    exit 70
fi

printf '\n================================================================================\n'
printf 'EXIT CRITERION 5 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
