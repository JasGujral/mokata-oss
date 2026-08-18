#!/usr/bin/env bash
# Drives the NULLGLOB-DISARMS-THE-EXISTENCE-CHECK mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_release_asset_set_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Driver shape from _gate_count_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84 —
# and it satisfies tests/_mutant_driver_contract.py, which is now a live pin.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing",
# and every RED it prints is unattributable.
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. Location is part of the contract:
# scripts/sync-public.sh excludes docs/build/, so a driver filed beside its report is ABSENT from
# the public mirror and a public contributor grading this repo would run an incomplete corpus that
# READS as complete. Three drivers are already declared-internal rather than repaired
# (MUTANT-DRIVERS-IN-DOCS-BUILD-DO-NOT-SHIP); this is not a fourth.
#
# TWO GROUPS, and the split is the stage's own finding — the defect had a STATIC half and a
# RUNTIME half, and fixing either alone leaves a Release that can still ship short:
#   A. tests/_release_assets.py       the sweep that reads the release path's CONFIGURATION
#   B. scripts/check-release-assets.sh the assertion that reads the BUILT ARTIFACTS

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

RA=tests/_release_assets.py
CS=scripts/check-release-assets.sh
T='test_release_asset_set.py'

TOTAL=24
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
        -k test_release_asset_set 2>&1 | tail -5; then
    printf '\nBATCH REFUSED — the graded suite is not green before mutant 1.\n'
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    exit 75
fi
if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
        -k test_release_asset_set >/dev/null 2>&1; then
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

# ==== A. the sweep that reads the CONFIGURATION ==================================================

mutant "A01 ★★ the nullglob gate is dropped — every honest glob check in the tree is convicted" "$RA" \
  '        if not armed:
            continue' \
  '        if False:
            continue' "$T"

mutant "A02 ★★ the offender test never fires — the sweep grades nothing and reads as clean" "$RA" \
  '            if _has_unquoted_glob(" ".join(words[1:])):' \
  '            if False:' "$T"

mutant "A03 ★ quoting stops mattering — a LITERAL filename is convicted as a disarmed glob" "$RA" \
  '    return any(char in _QUOTED.sub("", text) for char in _GLOB_CHARS)' \
  '    return any(char in text for char in _GLOB_CHARS)' "$T"

mutant "A04 ★★ the semicolon splitter is lost — the EXACT line this stage is named for is missed" "$RA" \
  '_SPLIT = re.compile(r"(?:\|\||&&|[;|&])")' \
  '_SPLIT = re.compile(r"(?:\|\||&&|[|&])")' "$T"

mutant "A05 ★ comments stop being stripped — release.yml's own warning comment is convicted" "$RA" \
  '        for fragment in _SPLIT.split(_strip_comment(line)):' \
  '        for fragment in _SPLIT.split(line):' "$T"

mutant "A06 ★★ the class collapses to the instance — only ls is graded (§7j)" "$RA" \
  'EXISTENCE_COMMANDS = ("ls", "stat", "test", "[", "readlink", "file")' \
  'EXISTENCE_COMMANDS = ("ls",)' "$T"

mutant "A07 ★ nullglob can never be turned back OFF — a fixed block stays convicted" "$RA" \
  '_NULLGLOB_OFF = re.compile(r"^[ \t]*shopt[ \t]+-u[ \t]+(?:[-\w]+[ \t]+)*nullglob\b", re.M)' \
  '_NULLGLOB_OFF = re.compile(r"^[ \t]*shopt[ \t]+-u[ \t]+(?:[-\w]+[ \t]+)*nullglub\b", re.M)' "$T"

mutant "A08 ★★ fail_on_unmatched_files is read as a bool only — the STRING true reads as unarmed" "$RA" \
  '    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"' \
  '    if isinstance(value, bool):
        return value
    return False' "$T"

mutant "A09 ★★ every upload reads as armed — the release surface stops being graded at all" "$RA" \
  '    value = with_mapping.get(FAIL_ON_UNMATCHED)' \
  '    return True
    value = with_mapping.get(FAIL_ON_UNMATCHED)' "$T"

mutant "A10 ★ an upload attaching NOTHING is convicted — a false red switches the sweep off" "$RA" \
  '        if declared_upload_patterns(inputs) and not _armed(inputs))' \
  '        if not _armed(inputs))' "$T"

mutant "A11 ★★ a glob crosses a directory — dist/nested/a.whl satisfies dist/*.whl" "$RA" \
  '            if len(segments) == len(parts) and all(' \
  '            if True and all(' "$T"

mutant "A12 ★★ unmatched_patterns reports nothing — the v0.0.17 known-bad case goes quiet" "$RA" \
  '        else:
            found.append(pattern)' \
  '        else:
            pass' "$T"

mutant "A13 ★★ the legacy-bundle escape is gone — a spelling where the flags WORK is convicted" "$RA" \
  '        if _OLD_BUNDLE_FORMAT.search(invocation):
            continue' \
  '        if False:
            continue' "$T"

mutant "A14 ★★ line continuations stop folding — the five-line invocation that shipped is misread" "$RA" \
  '    joined = shell_text.replace("\\\n", " ")' \
  '    joined = shell_text' "$T"

mutant "A15 ★ the ignored-flag list is emptied — why the pair was never written stops grading" "$RA" \
  'COSIGN_IGNORED_WITH_NEW_BUNDLE = ("--output-signature", "--output-certificate")' \
  'COSIGN_IGNORED_WITH_NEW_BUNDLE = ("--output-signature",)' "$T"

mutant "A16 ★★ the pattern block cannot be read — the derivation silently becomes vacuous (§7j)" "$RA" \
  '_PATTERN_BLOCK = re.compile(r"^SIGNED_PATTERNS='"'"'([^'"'"']*)'"'"'", re.M)' \
  '_PATTERN_BLOCK = re.compile(r"^SIGNED_PATTERNZ='"'"'([^'"'"']*)'"'"'", re.M)' "$T"

mutant "A17 ★ the upload step is matched WITH its ref — no step is ever found" "$RA" \
  '            if isinstance(uses, str) and uses.split("@", 1)[0] == action:' \
  '            if isinstance(uses, str) and uses == action:' "$T"

mutant "A18 ★★ run_blocks returns nothing — the whole build-surface sweep reads clean on empty" "$RA" \
  '            if isinstance(step, dict) and isinstance(step.get("run"), str):
                found.append(("jobs.%s.steps[%d]" % (job_id, index), step["run"]))' \
  '            if False:
                found.append(("jobs.%s.steps[%d]" % (job_id, index), step["run"]))' "$T"

# ==== B. the assertion that reads the BUILT ARTIFACTS ============================================

mutant "B01 ★★ nullglob is turned OFF — an unmatched pattern iterates over its own literal" "$CS" \
  'shopt -s nullglob

unmatched=' \
  'shopt -u nullglob

unmatched=' "$T"

mutant "B02 ★★ the missing-bundle check is neutered — an UNSIGNED artifact ships" "$CS" \
  '        [ -f "$want" ] || missing="$missing  $want"$'"'"'\n'"'"'' \
  '        [ -f "$want" ] || true' "$T"

mutant "B03 ★★ the no-match test can never fire — a build that produced nothing reads as fine" "$CS" \
  '    if [ "${#matches[@]}" -eq 0 ]; then' \
  '    if [ "${#matches[@]}" -lt 0 ]; then' "$T"

mutant "B04 ★★ the two failure modes collapse into one exit code (§7g)" "$CS" \
  '    exit 3
fi' \
  '    exit 4
fi' "$T"

mutant "B05 ★★ an unsigned artifact exits 0 — the check becomes decorative" "$CS" \
  '    exit 4
fi

printf '"'"'%s'"'"' "$expected"' \
  '    exit 0
fi

printf '"'"'%s'"'"' "$expected"' "$T"

mutant "B06 ★ the bundle suffix stops matching what cosign v3 writes" "$CS" \
  "BUNDLE_SUFFIX='.sigstore.json'" \
  "BUNDLE_SUFFIX='.sig'" "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'RELEASE-ASSET-SET MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
