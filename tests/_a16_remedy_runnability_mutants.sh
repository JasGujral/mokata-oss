#!/usr/bin/env bash
# Drives the 0.0.20 stage-11b `RECONFIGURE-REMOVE-CANNOT-UNWIRE-A-REMOVED-PROVIDER` mutant list
# through scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a16_remedy_runnability_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THE SUBJECT IS `_removable_tools`, AND IT IS MUTATED IN BOTH DIRECTIONS. The row's whole
# history is a rule that was widened once and was still wrong; the risk on this repair is the
# mirror image — a rule widened so far it eats the floors. R01-R03 restore the old defect,
# R04-R06 overshoot it, and each has a named question.
#
#   R01-R03 ★ THE SUBJECT. Each puts back a version of the bug: the floor set typed from the
#     current profile instead of the default, the floor filter dropped so nothing is removable,
#     and the pre-repair `OPTIONAL_INTEGRATIONS` test restored — the exact state that made
#     `--remove pgvector` a no-op.
#
#   R04-R05 THE OVERSHOOT. Each makes a floor removable, which is the failure mode a widening
#     repair invites and which no test would have caught before this stage.
#
#   V01-V03 THE VERDICTS (§7g). Each collapses RUNNABLE / NO_OP / UNRUNNABLE into another, which
#     is how the original defect presented in the first place: exit 0 and a reassuring sentence.
#
#   C01-C02 THE CLAIM READER. Each widens what counts as a runnable remedy, and a widened reader
#     grades placeholders it can never execute.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

O=src/mokata/onboarding.py
R=tests/_remedy_runnability.py
T='test_a16_remedies_are_runnable.py'

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

# ==== R. the subject — the old defect, restored three ways ======================================

mutant "R01 ★ SUBJECT: the pre-repair rule is back — --remove pgvector is a no-op again" "$O" \
  '        out.update(t for t in chain if t not in floors)' \
  '        out.update(t for t in chain if t in OPTIONAL_INTEGRATIONS or t not in TOOL_CATALOG)' "$T"

mutant "R02 ★ SUBJECT: the floors come from the CURRENT profile, so full makes serena a floor" "$O" \
  '    caps = (PROFILES.get(DEFAULT_PROFILE) or {}).get("capabilities") or {}' \
  '    caps = (PROFILES.get("full") or {}).get("capabilities") or {}' "$T"

mutant "R03 nothing is removable at all — the widening inverted" "$O" \
  '        out.update(t for t in chain if t not in floors)' \
  '        out.update(t for t in chain if t in floors)' "$T"

# ==== R04-R05. the overshoot — the failure mode a widening repair invites =======================

mutant "R04 ★ the floor set derives EMPTY, so --remove grep stops being a no-op" "$O" \
  '    return frozenset(t for chain in caps.values() for t in (chain or []))' \
  '    return frozenset()' "$T"

mutant "R05 the floor set loses the memory_store half — sqlite becomes removable" "$O" \
  '    return frozenset(t for chain in caps.values() for t in (chain or []))' \
  '    return frozenset(caps.get("code_graph") or [])' "$T"

# ==== V. the three verdicts (§7g) ==============================================================

mutant "V01 a NO-OP is reported RUNNABLE — exit 0 and a reassuring sentence, restored" "$R" \
  '    return Result(NO_OP, channel, removed)' \
  '    return Result(RUNNABLE, channel, removed)' "$T"

mutant "V02 an UNRUNNABLE remedy is reported as a NO-OP — a crash wearing a no-op's colour" "$R" \
  '        return Result(UNRUNNABLE, channel, (), exc)' \
  '        return Result(NO_OP, channel, (), exc)' "$T"

mutant "V03 the channel is never checked against what the plan removed" "$R" \
  '    if channel in removed:' \
  '    if True:' "$T"

# ==== C. the claim reader ======================================================================

mutant "C01 a remedy with a <placeholder> is treated as an executable command" "$R" \
  '_RECONFIGURE_REMOVE = re.compile(r"^mokata reconfigure --remove (?P<channel>[a-z0-9][a-z0-9._-]*)$")' \
  '_RECONFIGURE_REMOVE = re.compile(r"^mokata reconfigure --remove (?P<channel>.*)$")' "$T"

mutant "C02 prose naming a command counts as naming it — backticks stop being the grammar" "$R" \
  '_COMMAND = re.compile(r"`(?P<cmd>mokata [^`]+)`")' \
  '_COMMAND = re.compile(r"(?P<cmd>mokata [^`\n.]+)")' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A16 REMEDY-RUNNABILITY MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
