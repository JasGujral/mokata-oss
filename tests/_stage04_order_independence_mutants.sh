#!/usr/bin/env bash
# 0.0.21 stage 04's mutant list, through scripts/mutate.sh — the ONLY sanctioned mutator (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage04_order_independence_mutants.sh
#
# 🔴 WHAT THIS BATCH IS AGAINST. Stage 04 ships a FINDING TOOL, and the failure mode of a finding tool
# is not "it stops working" — it is **"it keeps reporting and nobody reads it"**, or "it reports a
# confident green having asked nothing". Both are silent. So every mutant here leaves the tool running
# and printing, and breaks what its output is WORTH:
#
#   S01-S02 ★★★ THE SEED. A seed that does not replay the order is not a seed, and a shuffle that
#     ignores it reports a confident green about the alphabet it was meant to escape.
#   S03     ★★ the floor — a shuffle over a handful of modules cannot express a cross-module residue.
#   S04-S05 ★★★ THE BASELINE DIFFERENCE, which is the whole reason this tool is readable. S04 restores
#     the first version's behaviour (every red is an ordering ⚠, so four environment failures nag on
#     every run and train the reader to ignore it — §7i). S05 takes the difference one way only, which
#     makes a residue the ALPHABET creates invisible.
#   S06     ★★★ the parse reconciliation. A parse that loses a failing id makes the tool UNDER-report,
#     which is the one direction a finding tool must never be wrong in (§7e).
#   V01-V02 ★★★ THE VICTIM. V01 restores the inherited precondition — the actual defect, which for
#     eight days read as an ordering bug. V02 removes the precondition's self-assertion, so the next
#     time the fixture breaks it says `AssertionError` about the wrong subject again (§7g).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

S=scripts/shuffle-order.py
V=tests/test_hook_shell_agnostic.py
T='test_a30_the_suite_is_green_in_one_arrangement.py'

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

# ==== S · the tool =============================================================================

mutant "S01 ★★★ the sort-before-shuffle is dropped — the seed no longer replays the order" "$S" \
  '    out = sorted(modules)' \
  '    out = list(modules)' "$T"

mutant "S02 ★★★ the seed is ignored — the tool reports on the alphabet it exists to escape" "$S" \
  '    random.Random(seed).shuffle(out)' \
  '    pass' "$T"

mutant "S03 ★★ the floor is removed — a shuffle over three modules reports a confident green" "$S" \
  '    if len(modules) < MIN_SHUFFLE_MODULES:' \
  '    if False:' "$T"

mutant "S04 ★★★ the BASELINE is dropped — every red becomes an ordering ⚠ and the tool gets ignored" "$S" \
  '    base_ids, base_rc, base_ok = _run("BASELINE (alphabetical)", sorted(modules), passthrough)' \
  '    base_ids, base_rc, base_ok = (set(), 0, True)' "$T"

mutant "S05 ★★★ the difference is taken ONE way — a residue the ALPHABET creates goes unseen" "$S" \
  '    only_alpha = sorted(base_ids - shuf_ids)' \
  '    only_alpha = []' "$T"

mutant "S06 ★★★ the parse reconciliation is dropped — the tool UNDER-reports in silence (§7e)" "$S" \
  '    reconciled = (len(ids) == declared) and (ran is not None)' \
  '    reconciled = True' "$T"

# ==== V · the victim ===========================================================================

mutant "V01 ★★★ the victim asks the ENVIRONMENT for its precondition again — the original defect" "$V" \
  '% _a_launchable_program(d)}]}]}}),' \
  "% harness_setup.resolved_console_script('mokata-hook')}]}]}}),"  "$T"

mutant "V02 ★★★ the precondition stops asserting itself — a broken fixture blames the subject" "$V" \
  '            self.assertNotIn("hooks-not-firing", codes,' \
  '            self.assertNotIn("hooks-not-firing-DISABLED", codes,' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE 04 ORDER-INDEPENDENCE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
