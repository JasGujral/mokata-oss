#!/usr/bin/env bash
# Drives the stage-27 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage27_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. (Copied from _stage11_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84. Not
# extracted here on purpose: MUTANT-DRIVER-CONTRACT-UNIMPLEMENTED is the OTHER driver,
# _sync_marker_drift_mutants.sh, which implements NONE of this contract and would have to be
# fixed in the same change that extracted it.)
#
# ⚠ §7i. Once the fix lands, release.sh installs everything ci.yml installs, so the real-tree
# derivation is GREEN and grades nothing on its own. Three layers fix that:
#
#   C01-C03 BREAK THE REAL CONTROLS — remove the ci.txt install from release.sh, move it INSIDE
#     the `present` branch (which leaves the `absent` leg exactly as broken as it was), and remove
#     the install from ci.yml so the DERIVED set silently shrinks. C01 is the live 0.0.17 defect
#     restored byte-for-byte; if it does not red, this stage shipped a comment.
#
#   B01-B08 attack the derivation. B03/B05/B06 are the load-bearing ones: each makes an
#     UNDECIDABLE case render as a decided GREEN, which is §7g's one prohibition — and B03 is the
#     specific collapse this pin is most exposed to, because an empty derived set makes the subset
#     check VACUOUSLY true and a vacuous true is byte-identical to a covered preflight.
#
#   B09-B13 attack the SCOPE. A derivation that judges the wrong set of jobs, or counts a
#     requirements path that is not a `-r` argument on a pip INSTALL, answers a different question
#     than its name. B12/B13 SURVIVED the first pass and are why there was a second one — see the
#     note above them.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
P=tests/_preflight_parity.py
R=scripts/release.sh
C=.github/workflows/ci.yml
T='test_s27_preflight_parity.py'

TOTAL=16
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

# ==== the REAL controls break ==================================================================

# ★ THE LIVE DEFECT, restored. This is the mutant that decides whether the stage is real.
mutant "C01 release.sh stops installing requirements/ci.txt (the 0.0.17 defect, restored)" "$R" \
  '    "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null' \
  '    : # the install this stage added, removed again' "$T"

C02_OLD=$(cat <<'EOF'
    "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null
    if [ "$leg" = "present" ]; then
EOF
)
C02_NEW=$(cat <<'EOF'
    if [ "$leg" = "present" ]; then
      "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null
EOF
)
mutant "C02 the ci.txt install moves INSIDE the present branch (absent leg still broken)" \
  "$R" "$C02_OLD" "$C02_NEW" "$T"

mutant "C03 ci.yml stops installing requirements/ci.txt — the DERIVED set silently shrinks" "$C" \
  '        run: pip install --require-hashes -r requirements/ci.txt' \
  '        run: pip install -e .' "$T"

# ==== the derivation itself ====================================================================

mutant "B01 comments are no longer stripped (a commented-out install counts as an install)" "$P" \
  '    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))' \
  '    return "\n".join(ln for ln in text.splitlines())' "$T"

B02_OLD=$(cat <<'EOF'
    end = body.find("\n}", start)
    if end == -1:
        return None
EOF
)
B02_NEW=$(cat <<'EOF'
    end = len(body)
    if end == -1:
        return None
EOF
)
mutant "B02 the preflight body becomes 'everything after the function opens' — a DIFFERENT function's install counts" \
  "$P" "$B02_OLD" "$B02_NEW" "$T"

# ★ §7g's one prohibition, in the shape this pin is most exposed to.
mutant "B03 a ci.yml that installs NO requirements derives GREEN — a vacuous true wearing a pass" "$P" \
  '    BASIS_NO_CI_REQUIREMENTS: UNDECIDABLE,' \
  '    BASIS_NO_CI_REQUIREMENTS: GREEN,' "$T"

mutant "B04 a requirements file CI installs and the preflight does not stops being a defect" "$P" \
  '    BASIS_MISSING_INSTALLS: RED,' \
  '    BASIS_MISSING_INSTALLS: GREEN,' "$T"

mutant "B05 a release.sh with no locatable preflight function derives GREEN" "$P" \
  '    BASIS_NO_PREFLIGHT_FUNCTION: UNDECIDABLE,' \
  '    BASIS_NO_PREFLIGHT_FUNCTION: GREEN,' "$T"

mutant "B06 an unreadable release.sh derives GREEN instead of UNDECIDABLE" "$P" \
  '    BASIS_RELEASE_UNREADABLE: UNDECIDABLE,' \
  '    BASIS_RELEASE_UNREADABLE: GREEN,' "$T"

mutant "B07 a ci.yml that runs the suite nowhere derives GREEN" "$P" \
  '    BASIS_NO_UNIT_SUITE_JOBS: UNDECIDABLE,' \
  '    BASIS_NO_UNIT_SUITE_JOBS: GREEN,' "$T"

B08_OLD=$(cat <<'EOF'
        if basis in UNDECIDABLE_BASES and not detail:
EOF
)
B08_NEW=$(cat <<'EOF'
        if False:
EOF
)
mutant "B08 an UNDECIDABLE may be constructed with no reason (a shrug reads as green)" \
  "$P" "$B08_OLD" "$B08_NEW" "$T"

# ==== the SCOPE — what "for the unit suite" means ==============================================

mutant "B09 a live-service job is no longer excluded (the preflight is asked to be CI)" "$P" \
  '    return isinstance(job.get("services"), dict) and bool(job["services"])' \
  '    return False' "$T"

mutant "B10 a job that never runs the suite is treated as a unit-suite job" "$P" \
  '        if not roots:' \
  '        if False:' "$T"

mutant "B11 any requirements path in a pip line counts, not just a -r argument" "$P" \
  '_DASH_R = re.compile(r"(?:^|\s)(?:-r|--requirement)(?:=|\s+)(\S+)")' \
  '_DASH_R = re.compile(r"(\S+)")' "$T"

# ⚠ B12/B13 SURVIVED THE FIRST PASS (14/14 RED) and are the reason there was a second one. §7f:
# a perfect score is a prompt to write harder mutants, not a finish line. Nothing graded the
# `pip install` guard on EITHER side — a `pip download -r requirements/x.txt` fetches a wheel into
# a directory and installs nothing, and both sides counted it as an install. Two tests added, then
# these two mutants; they are the reason those tests exist and must never be deleted separately.

B12_OLD=$(cat <<'EOF'
            for line in run.splitlines():
                if "pip install" not in line:
                    continue
EOF
)
B12_NEW=$(cat <<'EOF'
            for line in run.splitlines():
                if False:
                    continue
EOF
)
mutant "B12 CI side: a -r on a line that is not a pip INSTALL counts as an install" \
  "$P" "$B12_OLD" "$B12_NEW" "$T"

B13_OLD=$(cat <<'EOF'
    for line in body.splitlines():
        if "pip install" not in line:
            continue
EOF
)
B13_NEW=$(cat <<'EOF'
    for line in body.splitlines():
        if False:
            continue
EOF
)
mutant "B13 release side: a -r on a line that is not a pip INSTALL counts as an install" \
  "$P" "$B13_OLD" "$B13_NEW" "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 27 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
