#!/usr/bin/env bash
# Drives the stage-22 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage22_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. This driver is a copy of tests/_stage11_mutants.sh's — NOT of
# tests/_sync_marker_drift_mutants.sh, which is a bare passthrough implementing none of it
# (MUTANT-DRIVER-CONTRACT-UNIMPLEMENTED, doc 84).
#
# WHY §7i FORCES TWO LAYERS. The shipped prose is CORRECT today, so every real-tree assertion in
# the pin is green from the moment it was written and grades nothing on its own.
#
#   C01-C03 DEGRADE THE REAL SHIPPED PROSE, on BOTH surfaces. These prove the pin catches the
#     actual defect and not a synthetic string. C01/C03 matter most: the instruction still NAMES
#     `register` afterwards, so a naive substring pin stays green while the requirement that it
#     be SET has gone.
#
#   B01-B11 attack the checker itself. B04 is the load-bearing one — it makes an UNGRADABLE
#     ordering read as a pass, which is this stage's one prohibition: "no anchor found" must
#     never silently mean "the order is fine".
#
# NOT MUTATED: src/mokata/gate_hook.py. Making registration structural was triaged and ruled out
# (it collides with the gate's positive-trigger contract — a run registered for every window puts
# ordinary hand-editing under house arrest). The gate is out of bounds in this batch exactly as it
# is in the diff.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
P=tests/_skill_prose.py
K=src/mokata/skills/brainstorm/SKILL.md
C=src/mokata/templates/commands/brainstorm.md
T='test_run_reg_prose_pin.py'

TOTAL=14
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

# ==== the REAL shipped prose is degraded ======================================================

mutant 'C01 SKILL.md: `register` = true downgraded to a bare mention of the parameter' "$K" \
  '`register` = true. This is the protocol' \
  'the `register` parameter. This is the protocol' "$T"

mutant 'C02 SKILL.md: the heading stops announcing that the step REGISTERS anything' "$K" \
  '## First: register the run (so this brainstorm is TRACKED, not invisible)' \
  '## First: get set up (so this brainstorm is TRACKED, not invisible)' "$T"

mutant 'C03 command template: `register` = true downgraded to a bare mention' "$C" \
  '`register` = true. This is the protocol' \
  'the `register` parameter. This is the protocol' "$T"

# ==== the checker itself ======================================================================

mutant 'B01 requiring register be SET collapses to merely NAMING it' "$P" \
  '_REGISTER_TRUE = re.compile(r"\bregister\b\s*(?:=|:|is|set\s+to|to)\s*true\b", re.IGNORECASE)' \
  '_REGISTER_TRUE = re.compile(r"\bregister\b", re.IGNORECASE)' "$T"

mutant 'B02 the tool-name requirement stops being checked' "$P" \
  '    if TOOL_NAME not in body:' \
  '    if False:' "$T"

mutant 'B03 the ordering comparison stops firing (a step below the first question passes)' "$P" \
  '    elif section.start > anchor.start:' \
  '    elif False:' "$T"

# THE STAGE'S ONE PROHIBITION: an UNGRADABLE requirement must never read as a pass.
mutant 'B04 an UNGRADABLE ordering reads as GREEN' "$P" \
  '        return not self.missing and not self.ungradable' \
  '        return not self.missing' "$T"

mutant 'B05 the heading basis is reported for what was a body-fallback find' "$P" \
  '            return section, "heading"' \
  '            return section, "body"' "$T"

mutant 'B06 an ABSENT step resolves to the first section instead of None' "$P" \
  '    return None, "absent"' \
  '    return sections[0], "absent"' "$T"

mutant 'B07 the question anchor widens past a single clause (a red-flags row poses as one)' "$P" \
  '_ASK_QUESTION = re.compile(r"\bask\b[^.\n]{0,40}?\bquestions?\b", re.IGNORECASE)' \
  '_ASK_QUESTION = re.compile(r"\bask\b[\s\S]{0,400}?\bquestions?\b", re.IGNORECASE)' "$T"

mutant 'B08 fenced code stops being excluded (a ## inside a code block becomes a section)' "$P" \
  '        if _FENCE.match(line):' \
  '        if False:' "$T"

mutant 'B09 the registration section is allowed to be its own ordering anchor' "$P" \
  '        if exclude is not None and section.start == exclude.start:' \
  '        if False:' "$T"

mutant 'B10 section bounds stop partitioning the document (offender slices go silently corrupt)' "$P" \
  '                body_start = offset' \
  '                body_start = offset + 1' "$T"

mutant 'B11 the regist- STEM tightens to the exact word, so "registering" stops matching' "$P" \
  '_REGIST = re.compile(r"\bregist\w*", re.IGNORECASE)' \
  '_REGIST = re.compile(r"\bregister\b", re.IGNORECASE)' "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 22 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
