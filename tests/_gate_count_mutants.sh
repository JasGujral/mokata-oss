#!/usr/bin/env bash
# Drives the GATE-COUNT-TRUTH mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_gate_count_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. (Driver shape from _stage29_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL.
#
# Stage 31 (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84) exists because no driver in this tree
# verifies that the graded tests PASS before mutant 1. Without it a batch cannot tell "the mutant
# was caught" from "these tests were already failing", and every RED it prints is unattributable
# — the mutation-testing equivalent of §7i's guard with no offender. This driver runs the graded
# suite clean first and REFUSES to grade anything if it is not green.
#
# ⚠ GROUP A MUTATES `src/mokata/skill_contracts.py`, which GATE-COUNT-TRUTH is otherwise forbidden
# to touch. That is the point: a guard that reads the docs and compares against a hardcoded 9
# passes every doc mutant and is still worthless. Flipping a `backed` flag moves the DERIVED
# expectation under the docs, and only a guard genuinely wired to the registry can red on it.
# mutate.sh restores byte-for-byte and purges the stale `.pyc`; nothing here is committed.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

SC=src/mokata/skill_contracts.py
PC=tests/_public_counts.py
T='test_public_counts_guard.py'

TOTAL=18
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
        -k test_public_counts_guard 2>&1 | tail -5; then
    printf '\nBATCH REFUSED — the graded suite is not green before mutant 1.\n'
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    exit 75
fi
if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
        -k test_public_counts_guard >/dev/null 2>&1; then
    printf '\nBATCH REFUSED — the graded suite is not green before mutant 1.\n'
    exit 75
fi
printf 'Baseline GREEN. Grading %s mutants.\n\n' "$TOTAL"

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

# ==== A. the derivation is pinned to the REGISTRY, not to a hardcoded 9 =========================

mutant "A01 ★★ ship-readiness is backed again — the exact defect, injected at the source" "$SC" \
  '        "recorded — an agent-facing protocol boundary, not a code gate",
        "", backed=False),' \
  '        "recorded — an agent-facing protocol boundary, not a code gate",
        "src/mokata/engine/ship.py"),' "$T"

mutant "A02 ★★ self-protect is demoted — the derived set drops to eight" "$SC" \
  '        "writes to installed package trees, mokata'"'"'s own install, or outside the workspace root "
        "are refused — security-class, never overridable",
        "src/mokata/selfprotect.py"),' \
  '        "writes to installed package trees, mokata'"'"'s own install, or outside the workspace root "
        "are refused — security-class, never overridable",
        "", backed=False),' "$T"

mutant "A03 ★ a gate neither half of the original defect touched is demoted" "$SC" \
  '        "a plan change is surfaced, human-gated, and audited — never silent",
        "src/mokata/govern/deviation.py"),' \
  '        "a plan change is surfaced, human-gated, and audited — never silent",
        "", backed=False),' "$T"

# ==== B. the guard'"'"'s own mechanism, each graded by a PLANTED offender (doc 85 §7i) ==============

mutant "B01 ★ the backed filter admits every gate (re-spelled — see the .txt)" "$PC" \
  '    backed = {name for name, gate in skill_contracts.GATES.items() if gate.backed}' \
  '    backed = {name for name, gate in skill_contracts.GATES.items() if gate or True}' "$T"

mutant "B02 ★ membership becomes SUBSET — a roster that drops a gate goes quiet" "$PC" \
  '        if roster.ids == fact.members:' \
  '        if roster.ids <= fact.members:' "$T"

mutant "B03 ★ the count gate stops reporting a mismatch at all" "$PC" \
  '        if claim.stated != fact.value:' \
  '        if False:' "$T"

mutant "B04 ★ the backed-gates anchor is de-anchored and matches nothing" "$PC" \
  '    ("backed_gates", re.compile(_NUM + r"\s+backed gates", re.I)),' \
  '    ("backed_gates", re.compile(_NUM + r"\s+backed gaits", re.I)),' "$T"

mutant "B05 ★ the unmarked-enumeration floor is raised out of reach (§7j corpus axis)" "$PC" \
  '_ENUMERATION_FLOOR = 2' \
  '_ENUMERATION_FLOOR = 99' "$T"

mutant "B06 ★ the corpus drops overrides/ — the file no sweep had ever read" "$PC" \
  '    for base in ("README.md", "docs", "overrides"):' \
  '    for base in ("README.md", "docs"):' "$T"

mutant "B07 ★ the corpus stops honouring sync-public.sh and floods with internal notes" "$PC" \
  '        prefixes.add(entry)' \
  '        pass' "$T"

mutant "B08 ★ normalise stops stripping HTML — every home.html claim goes invisible" "$PC" \
  '    return _EMPHASIS.sub("", _TAG.sub(" ", line))' \
  '    return _EMPHASIS.sub("", line)' "$T"

mutant "B09 normalise stops stripping Markdown emphasis" "$PC" \
  '    return _EMPHASIS.sub("", _TAG.sub(" ", line))' \
  '    return _TAG.sub(" ", line)' "$T"

mutant "B10 the word-number table loses 'five' — a claim written in words stops grading" "$PC" \
  '    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,' \
  '    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "five": None,' "$T"

mutant "B11 the MCP split legs are transposed — 40/20/1 stops being read in order" "$PC" \
  '    (("mcp_read", "mcp_write", "mcp_approve"), re.compile(' \
  '    (("mcp_write", "mcp_read", "mcp_approve"), re.compile(' "$T"

mutant "B13 ★ paragraphs stop joining — a claim that WRAPS mid-sentence goes unread" "$PC" \
  '        yield " ".join(parts), offsets' \
  '        yield parts[-1], offsets[-1:]' "$T"

# ⚠ B14's FIRST SPELLING HUNG THE BATCH — recorded here, not silently re-spelled.
# Neutering the OUTER blank-line skip (`if not lines[index].strip():` -> `if False:`) leaves
# `index` un-advanced on a blank line while the inner loop refuses to enter, so `paragraphs()`
# spins forever. `mutate.sh` has no timeout, so the run sat for 12 minutes WITH THE TARGET
# MUTATED and produced no verdict. A mutant that hangs is not a GREEN and not a RED — it is the
# BROKEN third state, and the harness cannot currently reach it on its own.
# Filed as `MUTATE-SH-NO-TIMEOUT-A-HANGING-MUTANT-IS-NO-VERDICT` (doc 84 §2).
# Re-spelled to weld paragraphs while still advancing: the inner loop swallows blank lines.
mutant "B14 ★ the blank-line boundary is dropped — numbers WELD across paragraphs" "$PC" \
  '        while index < len(lines) and lines[index].strip():' \
  '        while index < len(lines):' "$T"

mutant "B15 the MCP split legs stop tolerating an intervening parenthetical" "$PC" \
  '        r"(\d+)\s+read\b[^0-9]*?(\d+)\s+write\b[^0-9]*?(\d+)\s+(?:opt-in\s+)?approve", re.I)),' \
  '        r"(\d+)\s+read\s*[·,]\s*(\d+)\s+write\s*[·,]\s*(\d+)\s+(?:opt-in\s+)?approve", re.I)),' "$T"

mutant "B12 the pyproject parse loses its scope to the dependencies array" "$PC" \
  '    return [item for item in re.findall(r"['"'"'\"]([^'"'"'\"]+)['"'"'\"]", match.group(1))]' \
  '    return [item for item in re.findall(r"['"'"'\"]([^'"'"'\"]+)['"'"'\"]", text)]' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'GATE-COUNT-TRUTH MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
