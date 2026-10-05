#!/usr/bin/env bash
# 0.0.21 stage 03 `every-gate-reports-before-it-stops`, graded. Through scripts/mutate.sh (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage03_gates_report_mutants.sh
#
#   V01-V06 ★★★ B5's versionless shape: the disposition collapsing into UNRESOLVED (same colour,
#     DIFFERENT remedy), the pattern going away, UNRESOLVABLE not being red, the subject-position
#     guard (which is what keeps "The next release adds X" out), and the two narrowings my own
#     first drafts needed.
#   S01-S03 ★★★ the SECTION dating, which is the half my first version got wrong twice: no dating
#     at all (history reds forever and the only green is to edit history), `<=` instead of `<`
#     (the live section reads as SPENT — the fail-open direction), and the unrecognised-heading
#     degrade direction.
#   R01-R02 ★★★ the reachable ceiling: hard-coded either way.
#   G01-G04 ★★★ the collected cheap gates: the report, a gate going back to aborting alone, the
#     mirror check moving back behind the test preflights, and the value-passing file.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

D=src/mokata/disclosure.py
P=src/mokata/packaging.py
R=scripts/release.sh
T='test_a33_every_gate_reports_before_it_stops.py'

TOTAL=15
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

# ==== V · the versionless claim ================================================================

mutant "V01 ★★★ UNRESOLVABLE collapses into UNRESOLVED — same colour, DIFFERENT remedy (§7g)" "$D" \
  '        return Verdict(claim, UNRESOLVABLE,
                       "this promise is phrased relative to the release' \
  '        return Verdict(claim, UNRESOLVED,
                       "this promise is phrased relative to the release' "$T"

mutant "V02 ★★★ the relative patterns are never consulted — B5 is blind again" "$D" \
  '    if not found:
        for name, pattern in RELATIVE_CLAIM_PATTERNS:' \
  '    if False:
        for name, pattern in RELATIVE_CLAIM_PATTERNS:' "$T"

mutant "V03 ★★★ UNRESOLVABLE is no longer RED — an uncheckable promise passes the cut" "$D" \
  '        return (bool(self.unresolved) or bool(self.unresolvable)' \
  '        return (bool(self.unresolved) or bool(False)' "$T"

mutant "V04 ★★★ the subject-position guard goes — 'The next release adds X' is convicted as a promise" "$D" \
  '                if name == "bare-relative-qualifier" and _subject_position(text, match.start()):' \
  '                if False:' "$T"

mutant "V05 ★★★ the bare qualifier widens back to this-release — the live false positive returns" "$D" \
  '    ("bare-relative-qualifier", re.compile(r"\**" + _FORWARD_RELATIVE, re.I)),' \
  '    ("bare-relative-qualifier", re.compile(r"\**" + _RELEASE_RELATIVE, re.I)),' "$T"

mutant "V06 ★★★ a dated claim no longer suppresses the relative one — one sentence, two verdicts" "$D" \
  '    if not found:
        for name, pattern in RELATIVE_CLAIM_PATTERNS:' \
  '    if True:
        for name, pattern in RELATIVE_CLAIM_PATTERNS:' "$T"

# ==== S · dating a relative claim by its section ================================================

mutant "S01 ★★★ the section never dates a relative claim — history reds forever" "$D" \
  '        if cutting and claim.section and version_tuple(claim.section) < version_tuple(cutting):' \
  '        if False:' "$T"

mutant "S02 ★★★ the section comparison is <= — the LIVE section reads as SPENT (fail-open)" "$D" \
  '        if cutting and claim.section and version_tuple(claim.section) < version_tuple(cutting):' \
  '        if cutting and claim.section and version_tuple(claim.section) <= version_tuple(cutting):' "$T"

mutant "S03 ★★★ an unrecognised heading RESETS the dating — historical prose becomes live promises" "$D" \
  '        if match:
            current = match.group(1)' \
  '        if raw.startswith("## "):
            current = match.group(1) if match else None' "$T"

mutant "S04 ★★★ the section is never attached to the claim at all" "$D" \
  '                [replace(c, section=(sections[c.line] if 0 < c.line < len(sections) else None))
                 for c in prose])' \
  '                prose)' "$T"

# ==== R · the reachable ceiling =================================================================

mutant "R01 ★★★ the ceiling is hard-coded to 2 — the resolving gate's bar becomes unclearable" "$P" \
  '        return 2 if self.claims_undecided else 0' \
  '        return 2' "$T"

mutant "R02 ★★★ the ceiling is hard-coded to 0 — the shipped leg is held to an impossible bar again" "$P" \
  '        return 2 if self.claims_undecided else 0' \
  '        return 0' "$T"

# ==== G · the collected cheap gates =============================================================

mutant "G01 ★★★ the gate report stops listing anything — the whole point of collecting" "$R" \
  '  local i=0
  while [ "$i" -lt "${#GATE_FAIL_LABELS[@]}" ]; do' \
  '  local i=0
  while false; do' "$T"

mutant "G02 ★★★ DG-7 goes back to aborting alone — a second cheap failure hides until the retry" "$R" \
  'gate "release notes announce this tag and drop no limitation (DG-7)" \
  verify_release_notes "." "the dev checkout (HEAD)"' \
  'verify_release_notes "." "the dev checkout (HEAD)"' "$T"

mutant "G03 ★★★ the mirror branch is no longer carried across the subshell boundary" "$R" \
  'PUB_DEFAULT_BRANCH="$(cat "$MIRROR_BRANCH_FILE" 2>/dev/null || true)"' \
  'PUB_DEFAULT_BRANCH=""' "$T"

printf '\n================================================================================\n'
printf 'STAGE 03 GATES-REPORT MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a row whose fix does not grade):\n%s' "$survivors"
else
    printf 'Every mutant was caught.\n'
fi
printf '================================================================================\n'
[ "$ran" -eq "$TOTAL" ] || exit 70
