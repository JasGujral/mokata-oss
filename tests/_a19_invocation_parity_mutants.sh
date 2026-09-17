#!/usr/bin/env bash
# Drives the 0.0.20 stage-10b `PREFLIGHT-VS-CI-INVOCATION-PARITY` mutant list through
# scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_a19_invocation_parity_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 THE ROW THIS CLOSES IS ITSELF ABOUT A CHECK THAT WAS SILENT ON AN AXIS. So the batch's first
# duty is to prove each axis is actually READ — a parser that silently returns `None` for `-k`
# would make every `-k` divergence invisible and the whole file would still be green.
#
#   A01-A04 ★ THE AXES. Each blinds one, which is the row's own defect reproduced inside its fix.
#
#   D01-D03 THE DEFAULTS. `-t` defaulting to `-s` and `-p` defaulting to unittest's own are what
#     keep "not passed" from reading as a divergence. Each mutant turns a legitimate match into a
#     false RED — a check that cries wolf gets deleted, which is worse than one that is silent.
#
#   V01-V03 THE VERDICT. Each collapses the three states, and V02 is the row's warning made
#     executable: a SELECTION divergence must not be excusable by a declaration.
#
#   S01-S02 THE STALE-DECLARATION ARM. A register that outlives its entry is a lie that makes the
#     next reader trust the whole list less.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

I=tests/_invocation_parity.py
T='test_a19_the_invocation_is_compared_too.py'

TOTAL=12
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

# ==== A. the axes — the row's own defect, reproduced inside its fix =============================

mutant "A01 ★ -k stops being read, so every -k divergence is invisible" "$I" \
  '    "k_filter": re.compile(r"(?:^|\s)-k\s+(?:\"([^\"]*)\"|'"'"'([^'"'"']*)'"'"'|(\S+))"),' \
  '    "k_filter": re.compile(r"(?!x)x"),' "$T"

mutant "A02 ★ -p stops being read" "$I" \
  '    "pattern": re.compile(r"(?:^|\s)-p\s+(?:\"([^\"]*)\"|'"'"'([^'"'"']*)'"'"'|(\S+))"),' \
  '    "pattern": re.compile(r"(?!x)x"),' "$T"

mutant "A03 ★ the stdin redirect is never seen — the declared divergence vanishes" "$I" \
  '_STDIN_REDIRECT = re.compile(r"<\s*/dev/null")' \
  '_STDIN_REDIRECT = re.compile(r"(?!x)x")' "$T"

mutant "A04 comments are kept, so a redirect QUOTED in prose counts as one" "$I" \
  '    code = "\n".join(ln for ln in (text or "").splitlines() if not ln.lstrip().startswith("#"))' \
  '    code = text or ""' "$T"

# ==== D. the defaults — a check that cries wolf gets deleted ====================================

mutant "D01 -t no longer defaults to -s, so 'not passed' reads as a divergence" "$I" \
  '        top_level=grab("top_level") or source,' \
  '        top_level=grab("top_level"),' "$T"

mutant "D02 -p no longer defaults to unittest's own pattern" "$I" \
  '        pattern=grab("pattern") or DEFAULT_PATTERN,' \
  '        pattern=grab("pattern"),' "$T"

mutant "D03 the default pattern is wrong, so an explicit -p test*.py stops matching" "$I" \
  'DEFAULT_PATTERN = "test*.py"' \
  'DEFAULT_PATTERN = "tests*.py"' "$T"

# ==== V. the verdict ===========================================================================

mutant "V01 an UNDECLARED divergence passes — the whole point, removed" "$I" \
  '    verdict = GREEN if not unmatched and all(d.declared for d in divergences) else RED' \
  '    verdict = GREEN if not unmatched else RED' "$T"

mutant "V02 ★ a SELECTION divergence becomes excusable by a declaration" "$I" \
  '        counterparts = [c for c in ci if c.selection == inv.selection]' \
  '        counterparts = [c for c in ci if c.source == inv.source]' "$T"

mutant "V03 an empty preflight is GREEN instead of UNDECIDABLE — vacuously true reads as a pass" "$I" \
  '    if not pre:' \
  '    if False:' "$T"

# ==== S. the stale-declaration arm =============================================================

mutant "S01 a declaration with nothing behind it is never reported" "$I" \
  '    return tuple(sorted(set(declared) - live))' \
  '    return ()' "$T"

mutant "S02 the stale check reports LIVE declarations too — it would red on a healthy tree" "$I" \
  '    return tuple(sorted(set(declared) - live))' \
  '    return tuple(sorted(set(declared)))' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'A19 INVOCATION-PARITY MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
