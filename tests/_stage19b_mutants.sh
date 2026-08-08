#!/usr/bin/env bash
# Drives stage 19b's mutants through scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
# Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage19b_mutants.sh
#
# Stage 19b — AMEND-STEP-2-IS-UNADVERTISED. The defect was an ABSENT INSTRUCTION, so every mutant
# here removes it again from one surface: drop the finish key, drop the finish note, silence the
# doctor line, or put the literal back in `gate_hook` so the two can drift apart once more.
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED.
#
# ⚠ MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) reaches SEVEN instances with this file.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

A=src/mokata/awaiting.py
G=src/mokata/gate_hook.py
T='test_amend_step_2_is_unadvertised.py'

TOTAL=7
ran=0; red=0; green=0; survivors=""

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
                exit 70 ;;
    esac
}

# ---- the block: the abort ships alone again ---------------------------------------------------

mutant "M01 the finish key is dropped — abort is advertised without a completion" "$A" \
  '        block["awaiting_finish_command"] = AMEND_FINISH_CMD' \
  '        block["awaiting_abort_command"] = AMEND_ABORT_CMD' "$T"

# D2's rider (0.0.17 stage 6) re-rendered this literal WITH its required argument — the bare form
# exits 1 at a shell with "--file is required", and a refusal may not name a remedy that fails.
# The mutant is unchanged in spirit: point the finish at the give-up path.
mutant "M02 the finish command IS the abort — approving lands you on the give-up path" "$A" \
  'AMEND_FINISH_CMD = "mokata spec amend --file <your-spec.json>"' \
  'AMEND_FINISH_CMD = "mokata spec amend --abort"' "$T"

# ---- the approved head ------------------------------------------------------------------------

mutant "M03 the approved head stops saying approval alone does not land it" "$A" \
  '        if tool == AMEND_TOOL_NAME:
            # The human-facing spelling of that same step. Without it the approved state told the
            # MODEL what to do and told the HUMAN nothing, which is precisely the moment they
            # concluded the server was wedged.
            head += f"  {AMEND_FINISH_NOTE}."' \
  '        if False:
            head += f"  {AMEND_FINISH_NOTE}."' "$T"

mutant "M04 the finish note stops naming the command" "$A" \
  'AMEND_FINISH_NOTE = (f"approving does NOT land the amendment — the run stays REGRESSED until the "
                     f"amend is finished: re-run `{AMEND_FINISH_CMD}`")' \
  'AMEND_FINISH_NOTE = "approving does NOT land the amendment"' "$T"

# ---- doctor ------------------------------------------------------------------------------------

mutant "M05 doctor stops telling an approved amendment how to finish" "$A" \
  '    if tool == AMEND_TOOL_NAME:
        lines.append(f"    {AMEND_FINISH_NOTE}")' \
  '    if False:
        lines.append(f"    {AMEND_FINISH_NOTE}")' "$T"

mutant "M06 the PENDING amendment loses the finish half of its menu" "$A" \
  '                                 f"then  `{AMEND_FINISH_CMD}`  "' \
  '                                 f"  "' "$T"

# ---- the one-place rule -------------------------------------------------------------------------

mutant "M07 gate_hook goes back to its own literal — the two surfaces can drift again" "$G" \
  '            f"(mokata approve <id>, then re-run `{AMEND_FINISH_CMD}`), abandon it "' \
  '            f"(mokata approve <id>, then re-run `mokata spec amend`), abandon it "' "$T"

printf '\n================================================================================\n'
printf 'stage 19b mutants: %s of %s graded — RED %s / GREEN %s\n' "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (the pin did NOT catch these):\n%s' "$survivors"
    exit 1
fi
printf 'no survivors.\n'
printf '================================================================================\n'
