#!/usr/bin/env bash
# Drives stage 19c's mutants through scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage19c_mutants.sh
#
# Stage 19c — OSS issue #43, the UserPromptSubmit hook's wall-clock budget. The defect was an
# ABSENT BOUND, so the mutants remove the bound again, set it where it cannot help (at or past the
# harness's own 30s kill), or break the two properties that make the degrade safe: that a timeout
# emits NOTHING rather than a partial pack, and that an abandoned worker cannot mark items
# "already injected" that the model never saw.
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE stops the batch dead.
#
# ⚠ MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) reaches EIGHT instances with this file.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

H=src/mokata/hook_cli.py
T='test_hook_work_budget.py'

# TWO of these are EQUIVALENT MUTANTS — behaviourally indistinguishable from the original, so no
# test can kill them and a GREEN is the CORRECT result. They are declared with `equivalent` rather
# than `mutant`, which INVERTS the grading: GREEN is expected, and a RED means the reasoning below
# has stopped being true and needs re-deriving. An undeclared survivor is still a batch failure.
#
#   M05 — dropping the `pack is None` guard lets None reach `pack.text`, which raises
#         AttributeError, which the documented broad fail-open floor at the end of
#         `user_prompt_submit_main` catches -> return 0, no emit. Identical observable behaviour;
#         the guard is there so the silence is INTENDED rather than an absorbed crash.
#   M06 — `is_alive()` guards a race in which the worker populates its holder in the instant
#         between the join expiring and the check. The holder is written as the worker's last act,
#         so that window is not externally constructible: measured 0 hits in 20,000 attempts at a
#         0s budget. The guard is correct and stays; the race is simply not reachable by a test.
TOTAL=7
ran=0; red=0; green=0; survivors=""; equiv=0; unexpected=""

# Same contract as `mutant`, with the verdict inverted — see the note above.
equivalent() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    shift
    out="$("$M" "$label" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\nBATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        exit "$rc"
    fi
    case "$out" in
        GREEN*) equiv=$((equiv + 1)); printf '        ^ EQUIVALENT — surviving is the expected, correct result.\n' ;;
        RED*)   unexpected="$unexpected  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — no verdict for mutant %s of %s.\n' "$ran" "$TOTAL"; exit 70 ;;
    esac
}

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    shift
    out="$("$M" "$label" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
            "$((TOTAL - ran))" "$TOTAL"
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                exit 70 ;;
    esac
}

# ---- the bound itself --------------------------------------------------------------------------

mutant "M01 the budget is removed — the body is unbounded again (the shipped bug)" "$H" \
  '        pack = _bounded(lambda: _build_injection_for(surface, prompt, root, session_id),
                        _HOOK_WORK_BUDGET_SECS)' \
  '        pack = _build_injection_for(surface, prompt, root, session_id)' "$T"

mutant "M02 the budget is set AT the harness kill — it can never fire in time" "$H" \
  '_HOOK_WORK_BUDGET_SECS = 5.0' \
  '_HOOK_WORK_BUDGET_SECS = 30.0' "$T"

mutant "M03 the budget is set PAST the harness kill" "$H" \
  '_HOOK_WORK_BUDGET_SECS = 5.0' \
  '_HOOK_WORK_BUDGET_SECS = 45.0' "$T"

mutant "M04 the budget is so tight it trips a healthy turn" "$H" \
  '_HOOK_WORK_BUDGET_SECS = 5.0' \
  '_HOOK_WORK_BUDGET_SECS = 0.0001' "$T"

# ---- the timeout never returns a partial ---------------------------------------------------------

equivalent "M05 [EQUIVALENT] the no-result check is dropped — an empty/absent pack flows on to the emit" "$H" \
  '        if pack is None:
            return 0                       # over budget / unreadable — silence, never a partial' \
  '        if False:
            return 0                       # over budget / unreadable — silence, never a partial' "$T"

equivalent "M06 [EQUIVALENT] _bounded reads the holder unconditionally — a LATE pack is handed back and emitted" "$H" \
  '    if thread.is_alive():
        return None                  # over budget — refuse it for RUNNING, not for being empty
    return holder.get("value")' \
  '    return holder.get("value")' "$T"

# ---- the abandoned worker must not record ---------------------------------------------------------

mutant "M07 the ledger write moves INSIDE the bounded worker — a dropped pack is recorded" "$H" \
  '    return build_injection(surface, prompt,
                           exclude_ids=already_injected(root, session_id=session_id))' \
  '    from .injection_ledger import record_injected
    pack = build_injection(surface, prompt,
                           exclude_ids=already_injected(root, session_id=session_id))
    record_injected(root, pack.item_ids, session_id=session_id)
    return pack' "$T"

printf '\n================================================================================\n'
printf 'stage 19c mutants: %s of %s graded — RED %s / GREEN %s / EQUIVALENT %s\n' \
    "$ran" "$TOTAL" "$red" "$green" "$equiv"
if [ -n "$unexpected" ]; then
    printf 'DECLARED-EQUIVALENT mutants that were CAUGHT — the equivalence reasoning is stale:\n%s' \
        "$unexpected"
    exit 1
fi
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (the pin did NOT catch these):\n%s' "$survivors"
    exit 1
fi
printf 'no undeclared survivors.\n'
printf '================================================================================\n'
