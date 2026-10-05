#!/usr/bin/env bash
# 0.0.21 stage 06 `a-suite-that-cannot-hang-or-silently-widen`, graded. Through scripts/mutate.sh.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage06_no_hang_no_widen_mutants.sh
#
# ⚠ THREE MODULES, TWO MUTANT TARGETS, SPLIT BY COST — and the split was FORCED, not chosen. With
# the sweep tests in one module, the glob mutant (W04) made the planted sweeps discover the whole
# tests tree under a forced TTY, including the modules that RUN the sweep: the mutant HUNG instead
# of producing a verdict, and so did W01 by a different route. ⭐ A mutant whose effect is a hang
# grades nothing and costs the most. `test_a36` holds the ~48s whole-tree reading and NO mutant
# points at it: there is nothing in a reading to mutate.
#
#   W01-W04 ★★★ the EMPTY-LIST refusal: gone, downgraded to a return, the name dropped from the
#     message, and the live consumer bypassed. W04 is the §7i one — a refusal nobody calls.
#   B01-B03 ★★★ the bare-invocation detector: the rule, the command-position narrowing (which my
#     first version lacked, convicting six prose sites), and the self-exclusion.
#   T01-T04 ★★★ the tty sweep: the instrument disarmed, the anti-vacuity step, the correct-pattern
#     distinction, and the SELF-EXCLUSION whose absence was a HANG rather than a failure.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

G=tests/_graded_invocation.py
A=tests/test_a34_a_suite_that_cannot_hang_or_silently_widen.py
B=tests/test_a35_the_tty_sweep_instrument_works.py
S=tests/_tty_prompt_sweep.py
# ⚠ TWO TEST TARGETS, BY COST. `$T34` is pure (milliseconds); `$T35` drives the sweep on planted
# modules (~2s). Pointing the W/B mutants at `$T35` would have made the glob mutant discover the
# whole tree under a forced TTY and HANG — measured, twice. See `test_a34`'s header.
T34='test_a34_a_suite_that_cannot_hang_or_silently_widen.py'
T35='test_a35_the_tty_sweep_instrument_works.py'

TOTAL=10
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

# ==== W · the empty-list refusal ===============================================================

mutant "W01 ★★★ an empty derivation is ALLOWED through — bare unittest grades the whole suite" "$G" \
  '    if not out:
        raise EmptyGradingSet(' \
  '    if False:
        raise EmptyGradingSet(' "$T34"

mutant "W02 ★★★ the refusal names no derivation — the reader goes to the runner, not to the rule" "$G" \
  '            "the %s resolved to NOTHING, so there is nothing to grade.\n"' \
  '            "the list resolved to NOTHING, so there is nothing to grade.\n"' "$T34"

mutant "W03 ★★★ the refusal stops being an AssertionError — a forgetful test lands in errors" "$G" \
  'class EmptyGradingSet(AssertionError):' \
  'class EmptyGradingSet(Exception):' "$T34"

# ⚠ W04's MUTATION IS A GLOB THAT MATCHES NOTHING, not `test_*.py`, AND THE REASON IS A HANG.
# `test_*.py` under the forced TTY discovers the whole tests tree — including the modules that RUN
# the sweep — so the mutant recursed and the batch stalled rather than producing a verdict. A
# non-matching glob exercises the same property (`Ran 0 tests ... OK` is the other false green) in
# under a second. ⭐ A mutant whose effect is a hang grades nothing and costs the most.
mutant "W04 ★★★ the argv builder emits a GLOB — Ran 0 tests OK, the other false green" "$G" \
  '    return [python, "-m", "unittest", *names_or_refuse(names, what)]' \
  '    return [python, "-m", "unittest", "discover", "-p", "zzz_matches_nothing.py"]' "$T34"

# ==== B · the bare-invocation detector ==========================================================

mutant "B01 ★★★ the detector convicts NOTHING — the hazard is unguarded again" "$A" \
  '            if not in_command_position(body, match.start()):
                continue            # a sentence ABOUT a command is not a command' \
  '            if True:
                continue            # a sentence ABOUT a command is not a command' "$T34"

mutant "B02 ★★★ the command-position narrowing goes — PROSE about the hazard is convicted" "$A" \
  '    return before[-1] in _COMMAND_POSITION' \
  '    return True' "$T34"

# ⛔ B03 WAS WITHDRAWN AND ITS SUBJECT DELETED, which is the honest outcome rather than a mutant
# nobody can kill. It attacked a SECOND defence: this module excluding itself from its own corpus,
# the way `disclosure.DECLARATION_MODULE` does. It ran GREEN — because the command-position rule
# already declines every one of the module's fixture strings (each is preceded by a quote), so no
# input distinguishes the exclusion's presence from its absence. §7f: two defences that cannot be
# told apart are untestable, so the weaker one is DELETED (§7d) rather than documented, and B02 is
# the single graded defence. Second instance of this shape in the release, after `_no_quote_path`.

# ==== T · the tty sweep =========================================================================

mutant "T01 ★★★ the instrument never arms — a clean journal and a clean tree become one green" "$S" \
  'if os.environ.get("TTYSWEEP") == "1":' \
  'if False:' "$T35"

mutant "T02 ★★★ the journal is never written — every reading is empty, i.e. every tree is clean" "$S" \
  '    with open(_OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
        fh.flush()' \
  '    pass' "$T35"

mutant "T03 ★★★ a CORRECT interactive test is flagged too — the reading becomes useless" "$S" \
  '    if not isinstance(sys.stdin, _ForcedTtyStdin):
        return _real_input(prompt)' \
  '    if False:
        return _real_input(prompt)' "$T35"

mutant "T04 ★★★ the sweep runners stop excluding themselves — the sweep sweeps itself and HANGS" "$B" \
  '            if _RUNS_THE_SWEEP in probe.read():
                continue        # it runs the sweep; sweeping it makes the sweep sweep itself' \
  '            if False:
                continue        # it runs the sweep; sweeping it makes the sweep sweep itself' "$T35"

printf '\n================================================================================\n'
printf 'STAGE 06 NO-HANG-NO-WIDEN MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a row whose fix does not grade):\n%s' "$survivors"
else
    printf 'Every mutant was caught.\n'
fi
printf '================================================================================\n'
[ "$ran" -eq "$TOTAL" ] || exit 70
