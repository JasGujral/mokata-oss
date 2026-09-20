#!/usr/bin/env bash
# Drives 0.0.20 stage 11's mutants through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_a11_deprecation_arm_mutants.sh
#
# WHAT THIS BATCH GRADES: that the deprecation announcer is REACHED, and reached SOFTLY.
# `WARN-DEPRECATED-HAS-NO-CALLERS` was invisible for two releases because an unwired announcer and
# an empty registry produce byte-identical output (§7g), so every mutant here removes or corrupts
# the reach and requires the new tests to red on it. ⛔ Without this batch, deleting the wiring
# again would be silent all over again.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

SEL=src/mokata/memory/selection.py
LAY=src/mokata/knowledge/layer.py
DEP=src/mokata/deprecation.py
T='test_a11_deprecation_arm_is_reachable.py'

TOTAL=8
ran=0; red=0; green=0; survivors=""

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

# ==== the REACH ================================================================================

mutant "K01 ★★★ THE ROW ITSELF — the memory surface stops reaching the announcer, which is the
       state this release found the tree in" "$SEL" \
  '    for tool in deprecation.deprecated_channels_in(chain):' \
  '    for tool in ():' "$T"

mutant "K02 ★★★ the graph surface stops calling its arm — the same defect on the capability that
       lost its call site LAST" "$LAY" \
  '    _announce_deprecated_graph_chain(router, root)' \
  '    pass' "$T"

mutant "K03 ★★★ the reach answers EMPTY for every chain — wired, called, and blind, which is the
       hardest of the three to see" "$DEP" \
  '    return tuple(tool for tool in (chain or ()) if isinstance(CHANNELS.get(tool),
                                                              DeprecationNotice))' \
  '    return ()' "$T"

# ==== and reached SOFTLY =======================================================================

mutant "K04 ★★★ the deprecation arm RAISES like the removal arm — deprecating a channel becomes
       the same act as deleting it, and a working provider goes down" "$SEL" \
  '        deprecation.warn_deprecated(tool, os.path.join(root, MOKATA_DIR))' \
  '        raise deprecation.RemovedChannelError(deprecation.CHANNELS[tool].render())' "$T"

mutant "K05 ★★ the filter is dropped and every chain is announced — a false deprecation warning on
       every repo's first read, which is how a notice gets ignored" "$DEP" \
  '    return tuple(tool for tool in (chain or ()) if isinstance(CHANNELS.get(tool),' \
  '    return tuple(tool for tool in (chain or ()) if not isinstance(CHANNELS.get(tool),' "$T"

# ==== the choices/metavar rule, which two surfaces had already hit by hand ======================

mutant "K06 ★★★ THE TYPO ANSWER, RESTORED — choices derives from the LIVE set alone, so a flag a
       year of published --help told users to pass answers 'invalid choice' (exit 2)" "$DEP" \
  '    return tuple(choices), "{%s}" % ",".join(live)' \
  '    return tuple(live), "{%s}" % ",".join(live)' "$T"

mutant "K07 ★★ the other direction — metavar advertises the removed channels too, so --help sells
       something that is gone while the answer path exists to say it is gone" "$DEP" \
  '    return tuple(choices), "{%s}" % ",".join(live)' \
  '    return tuple(choices), "{%s}" % ",".join(choices)' "$T"

mutant "K08 ★★ the guard stops seeing a hand-typed registry copy — the scan that makes the helper
       stick becomes decoration" "tests/test_a11_deprecation_arm_is_reachable.py" \
  '                        if len(overlap) >= _COPY_THRESHOLD:' \
  '                        if False:' "$T"

printf '\n================================================================================\n'
printf 'STAGE-11 DEPRECATION-ARM MUTANTS: %s ran of %s — %s RED / %s GREEN\n' \
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
