#!/usr/bin/env bash
# Drives the stage-12 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage12_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit.
#
# ⚠ RE-IMPLEMENTS that contract rather than sharing `_run_mutants.sh`'s copy — the known
# duplication filed as MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). Not fixed here; refactoring a
# reviewed driver mid-stage is the drive-by doc 00 forbids.
#
# ⚠ §7i — THE SUBJECT OF THIS STAGE IS AN ABSENCE. After the fix, `gh release create` occurs
# nowhere in release.sh, so every pin here is asserting that nothing is there. A pin over a tree
# with zero offenders grades NOTHING until an offender exists, so M01/M02/M04 PUT ONE BACK:
# M01 restores the executed call, M02 restores the printed instruction, M04 plants a second
# publishing workflow. Those three are the whole reason this batch is not a comment.
#
# Old/new strings come from quoted heredocs (`<<'EOF'`) so nothing in them — `$TAG`, backslashes,
# nested quotes — is expanded or re-escaped on the way to the patcher.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
SH=scripts/release.sh
RY=.github/workflows/release.yml
CI=.github/workflows/ci.yml
T='test_s12_release_publisher.py'

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

# ---- §7i: the synthetic offenders — the race, put back ----------------------------------------

CALL='wait_for_release "$PUB_REPO" "$TAG"'

mutant "M01 the second publisher returns (the exact pre-stage-12 defect)" "$SH" \
  "$CALL" \
  'gh release create "$TAG" --repo "$PUB_REPO" --title "mokata ${VER}"' "$T"

M02_OLD=$(cat <<'EOF'
  echo "       gh release view ${TAG} --repo ${PUB_REPO} --json assets --jq '.assets[].name'"
EOF
)
M02_NEW=$(cat <<'EOF'
  echo "       gh release create ${TAG} --repo ${PUB_REPO} --title 'mokata ${VER}'"
EOF
)
mutant "M02 the printed runbook re-advertises the manual create (AMEND-STEP-2 inverted)" "$SH" \
  "$M02_OLD" "$M02_NEW" "$T"

# A second workflow gains a publishing step. Planted at ci.yml's top level rather than as a real
# job step: the pin is a whole-tree count of non-comment mentions, and what must be graded is that
# a SECOND one anywhere is caught — not that this particular plant is valid GitHub Actions.
M04_OLD=$(cat <<'EOF'
name: CI
EOF
)
M04_NEW=$(cat <<'EOF'
name: CI
x-planted-second-publisher: softprops/action-gh-release@0000000000000000000000000000000000000000
EOF
)
mutant "M04 a SECOND workflow publishes (count 1 -> 2)" "$CI" "$M04_OLD" "$M04_NEW" "$T"

# ---- the publisher is gone entirely ----------------------------------------------------------

mutant "M03 the workflow's publishing step deleted (count 1 -> 0)" "$RY" \
  '        uses: softprops/action-gh-release@3d0d9888cb7fd7b750713d6e236d1fcb99157228 # v3.0.2' \
  '        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1' "$T"

mutant "M09 wait_for_release DEFINED but never CALLED (dead code reading as a gate)" "$SH" \
  "$CALL" 'true' "$T"

# ---- the wait stops being a wait --------------------------------------------------------------

# The 6-space `exit 1` is not unique on its own (the asset branches use 4), so the anchor spans
# the refusal line above it.
M05_OLD=$(cat <<'EOF'
      echo "hand-made release carries neither the signed artifacts nor the SBOM." >&2
      exit 1
EOF
)
M05_NEW=$(cat <<'EOF'
      echo "hand-made release carries neither the signed artifacts nor the SBOM." >&2
      break
EOF
)
mutant "M05 fail-OPEN on the publish timeout (refuses, then proceeds anyway)" "$SH" \
  "$M05_OLD" "$M05_NEW" "$T"

# Retarget rather than delete: the refusal MESSAGES still say 'Sigstore' and 'SBOM', so a pin that
# grepped the function body for those words would stay green here. Only a pin on the grep
# EXPRESSION catches it. That is the PIN-SUBSTRING-COMMENT-HOLE shape, reproduced on purpose.
mutant "M06 attestation check retargeted (messages intact, check dead)" "$SH" \
  '\.sigstore\.json' '\.no-such-asset\.json' "$T"

mutant "M07 SBOM check retargeted (messages intact, check dead)" "$SH" \
  'sbom\.cdx\.json' 'nosuch\.cdx\.json' "$T"

# ---- the runbook stops naming the publisher ---------------------------------------------------

M08_OLD=$(cat <<'EOF'
  echo " 11. DO NOT PUBLISH THE RELEASE BY HAND. Step 9's tag push triggers .github/workflows/release.yml,"
  echo "     whose 'github-release' job publishes ${TAG} WITH the signed wheel/sdist + SBOM. VERIFY it landed:"
  echo "       gh release view ${TAG} --repo ${PUB_REPO} --json assets --jq '.assets[].name'"
  echo "     Expect *.sigstore.json and sbom.cdx.json. If the release is missing, fix release.yml and"
  echo "     re-run its workflow — publishing it manually strips the attestations and re-creates the race."
EOF
)
M08_NEW=$(cat <<'EOF'
  echo " 11. The release is published for you. Nothing to do."
EOF
)
mutant "M08 runbook stops naming the publisher (operator left to guess)" "$SH" \
  "$M08_OLD" "$M08_NEW" "$T"

# ---- verdict ----------------------------------------------------------------------------------

printf '\n================================================================================\n'
printf 'STAGE 12 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
