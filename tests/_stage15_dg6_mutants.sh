#!/usr/bin/env bash
# Drives the mutant list in `_stage15_dg6_mutants.txt` through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage15_dg6_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit.
#
# ⚠ THE FOURTH COPY OF THAT CONTRACT — MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) is now at four
# instances (`_run_mutants.sh`, `_stage16_mutants.sh`, `_stage17_mutants.sh`, this). Recording the
# fourth is what stops "we'll unify it later" being said a fourth time.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

# The guard under test, and the two user-facing pages it now walks.
T='test_hook_shell_agnostic.py'
G="$ROOT/tests/test_hook_shell_agnostic.py"
PS="$ROOT/docs/reference/platform-support.md"
UW="$ROOT/docs/how-to/use-without-plugin.md"

TOTAL=9
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
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

# ---- DG-3: the two user-facing pages re-assert the falsified premise ---------------------------
# These are the mutants that matter. Each restores, near enough, the text that shipped for a whole
# release after HOOK-SHELL-AGNOSTIC falsified it.

mutant "M01 platform-support re-asserts the premise (the page a Windows user checks)" "$PS" \
  'cmd.exe is **never** a hook shell, so `PATHEXT` completion of the extension-less path never happens on the hook path.' \
  'cmd.exe completes the extension-less path via `PATHEXT`.' "$T"

mutant "M02 use-without-plugin re-asserts the premise" "$UW" \
  'codes, but it is **not on the hook path**: cmd.exe is never a hook shell, so **no** `PATHEXT`' \
  'codes. `hooks.json` names the path without an extension, so cmd.exe completes it via `PATHEXT`' "$T"

# ---- DG-6: the guard's REACH ------------------------------------------------------------------

mutant "M03 the docs walk finds nothing — the guard passes vacuously" "$G" \
  '        docs = sorted(p for p in (ROOT / "docs").rglob("*.md")' \
  '        docs = sorted(p for p in (ROOT / "docs").rglob("*.NOTHING")' "$T"

mutant "M04 the scan is handed an EMPTY corpus — it checks nothing and says nothing is wrong" "$G" \
  '        walked, offenders = self._pathext_scan(docs)' \
  '        walked, offenders = self._pathext_scan([])' "$T"

mutant "M04b the same, on the SOURCE half of the guard" "$G" \
  '        walked, offenders = self._pathext_scan(
            (SHIM, SHIM_CMD, ROOT / "src" / "mokata" / "hook_wiring.py",
             ROOT / "tests" / "test_hook_resolve.py"))' \
  '        walked, offenders = self._pathext_scan([])' "$T"

mutant "M04c the docs walk stops excluding the internal planning tree" "$G" \
  '                      if "build" not in p.relative_to(ROOT / "docs").parts)' \
  '                      if True)' "$T"

# ---- DG-6: the guard's PREDICATE — the finding this batch produced ----------------------------

mutant "M05 clause-scoped negation reverted to LINE-scoped (the first draft's defect)" "$G" \
  '                for clause in cls._CLAUSE.split(line):' \
  '                for clause in [line]:' "$T"

mutant "M06 the negation allow-list loses 'never', which every corrected site uses" "$G" \
  '    _NEGATIONS = ("not ", "never", "no ", "falsified", "wrong")' \
  '    _NEGATIONS = ("not ", "no ", "falsified", "wrong")' "$T"

mutant "M07 the predicate stops matching at all — every page passes" "$G" \
  '                    if "pathext" in low and not any(w in low for w in cls._NEGATIONS):' \
  '                    if False and not any(w in low for w in cls._NEGATIONS):' "$T"

printf '\n================================================================================\n'
printf 'stage 15 (DG-3 + DG-6) mutants: %s of %s  |  RED %s  |  GREEN(survivors) %s\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a finding about a pin, reported, never quietly dropped:\n%s' \
        "$survivors"
fi
printf '================================================================================\n'
[ "$green" -eq 0 ]
