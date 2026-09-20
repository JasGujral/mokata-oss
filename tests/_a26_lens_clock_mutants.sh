#!/usr/bin/env bash
# Drives the 0.0.20 amend-lens-clock mutants through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a26_lens_clock_mutants.sh
#
# 🔴 A BOUND FAILS IN FOUR DIRECTIONS AND THREE OF THEM LOOK LIKE WORKING CODE.
#
#   B01-B03 ★ THE BOUND ITSELF. B01 removes it (the hang, restored — OSS #66/#69/#70/#73).
#           B02 sets it to the harness's OWN kill, which can never fire in time to help and is the
#           subtlest of the four. B03 makes it so small a legitimate one-target widening dies.
#   L01-L02 ★ THE LATE ANSWER. Each accepts work that finished PAST the budget — a bound that
#           measures nothing, because the answer it returns is the one it was built to refuse.
#   V01-V02 ★ THE VERDICT COLLAPSES. Each makes a timeout indistinguishable from a fault, sending
#           the reader to repair an engine that is working (§7g).
#   O01-O02 ★ FAIL-OPEN. Each lets a scope-WIDENING amendment through on expiry — trading a hang
#           for the silent approval gate 2 exists to prevent. This is the expensive one.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

A=src/mokata/engine/amend.py
T='test_a26_the_amend_lens_has_a_clock.py'

TOTAL=9
ran=0; red=0; green=0; survivors=""; equiv=0; unexpected=""

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

# Same contract as `mutant`, with the verdict INVERTED. A declared-equivalent mutant is one no test
# CAN kill, so a GREEN is the correct result and a RED means the reasoning has stopped being true.
# ⭐ That inversion is the whole value: it turns "this survives and we think that is fine" from a
# sentence in a report into a graded claim.
equivalent() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    shift
    out="$("$M" "$label" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\nBATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        exit "$rc"
    fi
    case "$out" in
        GREEN*) equiv=$((equiv + 1)); printf '        ^ EQUIVALENT — surviving is the expected, correct result.\n' ;;
        RED*)   unexpected="$unexpected  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s\n' "$ran" "$TOTAL"
                exit 70 ;;
    esac
}

# ==== B. the bound itself =======================================================================

mutant "B01 ★★★ THE HANG, RESTORED — the lens runs unbounded again (the four issues verbatim)" "$A" \
  '    thread.join(timeout)
    if thread.is_alive():
        return None, True' \
  '    thread.join()
    if False:
        return None, True' "$T"

mutant "B02 ★★ the budget becomes the harness OWN KILL — it can never fire in time to help" "$A" \
  '_LENS_BUDGET_SECS = 20.0' \
  '_LENS_BUDGET_SECS = 60.0' "$T"

mutant "B03 ★ the budget is too small for a legitimate one-target widening on the floor" "$A" \
  '_LENS_BUDGET_SECS = 20.0' \
  '_LENS_BUDGET_SECS = 2.0' "$T"

# ==== L. the late answer ========================================================================

# ⚠ DECLARED EQUIVALENT, WITH AN EXPIRY. `_run` assigns `holder["value"] = work()` and the thread
# then EXITS, so `"value" in holder` and `is_alive()` overlap only in the instant between the
# assignment and the thread ending — a window of nanoseconds that no test can produce on demand.
# The added clause is therefore unreachable in practice, and the mutant survives.
#
# ⛔ IT IS DECLARED RATHER THAN DELETED, and the distinction is the point. The clause it removes is
# semantically RIGHT — `hook_cli._bounded`'s docstring names this exact race as the reason
# `is_alive()` is the deciding line — and the equivalence is a property of `_run`'s CURRENT shape,
# not of the design. The day `_run` publishes a partial or streamed result before finishing, this
# mutation becomes killable and the batch will say so under DECLARED-EQUIVALENT ... CAUGHT.
#
# ⚠ And it was NOT declared on first sight: it was written as a killable mutant, the test that
# should have caught it was found not to produce the racing state at all, the fixture was rebuilt
# to publish before lingering — and it STILL survived, because `_run` cannot reach that state. The
# declaration is the result of failing to kill it honestly, not of assuming it could not be killed.
equivalent "L01 ★★ [EQUIVALENT while _run assigns-then-exits] a COMPLETE answer that arrived PAST the budget is accepted" "$A" \
  '    if thread.is_alive():
        return None, True' \
  '    if thread.is_alive() and "value" not in holder:
        return None, True' "$T"

mutant "L02 ★ the runner reports no timeout at all — every late answer is on time" "$A" \
  '    if thread.is_alive():
        return None, True' \
  '    if False:
        return None, True' "$T"

# ==== V. the verdict collapses ==================================================================

mutant "V01 ★★ a timeout is reported as the FAULT — repair an engine that is working" "$A" \
  '                plan.gate = "blast-radius-timeout"' \
  '                plan.gate = "blast-radius"' "$T"

mutant "V02 ★★ a RAISE is swallowed into the timeout arm — the fault arm becomes unreachable" "$A" \
  '        except BaseException as exc:                  # noqa: BLE001 — re-raised in the caller
            holder["error"] = exc' \
  '        except BaseException:                         # noqa: BLE001
            pass' "$T"

# ==== O. fail-open — the expensive one ==========================================================

mutant "O01 ★★★ the timeout APPROVES the widening amendment — gate 2 undone by its own fix" "$A" \
  '                plan.ok = False
                plan.gate = "blast-radius-timeout"' \
  '                plan.ok = True
                plan.gate = "blast-radius-timeout"' "$T"

mutant "O02 ★★ the lens moves OUTSIDE the widens-scope guard — every amendment pays for it" "$A" \
  '    if diff.widens_scope:
        try:
            from ..brainstorm_impact import compute_impact' \
  '    if True:
        try:
            from ..brainstorm_impact import compute_impact' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A26 AMEND-LENS-CLOCK MUTANTS: %s ran of %s — %s RED, %s GREEN, %s EQUIVALENT\n' \
    "$ran" "$TOTAL" "$red" "$green" "$equiv"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
if [ -n "$unexpected" ]; then
    printf 'A DECLARED-EQUIVALENT MUTANT WAS CAUGHT — the reasoning behind it has stopped being\n'
    printf 'true, and it must be re-derived rather than re-declared:\n%s' "$unexpected"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
