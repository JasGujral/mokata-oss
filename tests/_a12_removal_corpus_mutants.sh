#!/usr/bin/env bash
# Drives 0.0.20 stage 12's mutants through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_a12_removal_corpus_mutants.sh
#
# WHAT THIS BATCH GRADES: the two axes of the removal-drift sweep, each of which was a filed row.
# A guard whose offenders you have just fixed grades nothing (§7i), so every mutant below puts an
# offender BACK — a shrunk corpus, an ignored exclusion, a surface scan that cannot tell a printed
# string from a docstring — and requires the new tests to red on it.
#
# ⚠ THE SIXTH COPY OF THE DRIVER CONTRACT: MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). The end
# state is one shared driver taking a list file; this is still not it, and recording the sixth
# instance is what stops "we'll unify it later" from being said a seventh time.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

DR=tests/_deprecation_removal.py
T='test_a12_removal_drift_corpus_and_surfaces.py'

TOTAL=5
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests -p "$T" >/dev/null 2>&1
then
    printf '\nBATCH REFUSED — %s is not green before mutant 1.\n' "$T"
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    exit 75
fi
printf 'Baseline GREEN. Grading %s mutants.\n\n' "$TOTAL"

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\nBATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' "$((TOTAL - ran))" "$TOTAL"
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$ran" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

# ==== A. what the sweep READS ==================================================================

mutant "J01 ★★★ EVERY EXCLUSION IS IGNORED — the corpus swallows the build log, and 65 correct
       historical mentions become reds. The direction that gets a guard switched off" "$DR" \
  '    return any(rel == d or rel.startswith(d + "/") for d in DENIED_FROM_RELEASE_CLAIMS)' \
  '    return False' "$T"

mutant "J02 ★★★ THE ROW ITSELF, RESTORED — the corpus shrinks back to the published doc tree, so
       everything outside docs/ is unswept again" "$DR" \
  '    for dirpath, dirnames, filenames in os.walk(root):' \
  '    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "docs")):' "$T"

mutant "J03 ★★★ docs/talks LEAVES THE CORPUS — the exact directory whose deck carried a false
       removal release to an audience for three releases" "$DR" \
  '    "docs/build": (' \
  '    "docs/talks": "excluded",
    "docs/build": (' "$T"

# ==== B. what the sweep MATCHES ================================================================

mutant "J04 ★★★ THE SURFACE SCAN LOSES ITS SURFACE — a docstring is graded as if a user read it,
       so the scan reds on correct history and nobody keeps it" "$DR" \
  '                if id(node) in skip:' \
  '                if False:' "$T"

mutant "J05 ★★ the surface deny-list is ignored — the one path declared not-a-surface is scanned
       anyway, which is the same false-red pressure from the other side" "$DR" \
  '            if rel in DENIED_FROM_USER_SURFACES:' \
  '            if False:' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-12 REMOVAL-CORPUS MUTANTS: %s ran of %s — %s RED / %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH INCOMPLETE — %s of %s never ran. Not a score.\n' "$((TOTAL - ran))" "$TOTAL"
    printf '================================================================================\n'
    exit 71
fi
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
