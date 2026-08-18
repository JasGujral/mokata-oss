#!/usr/bin/env bash
# Stage 17 (CI-MATRIX-FLOOR-GAP breadth) mutant batch — driven through scripts/mutate.sh, the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage17_floor_breadth_mutants.sh
#
# THE CONTRACT BELOW IS NOT RE-DERIVED HERE. Every line from `set -uo pipefail` down to the
# summary is `tests/_run_mutants.sh`'s, transplanted verbatim, because that driver is the one
# `tests/_mutant_driver_contract.py` grades and a hand-written copy is exactly the
# `MUTANT-DRIVER-CONTRACT-DUPLICATED` shape that row already names. What is this stage's is the
# TARGETS, the TOTAL, and the mutant list.
#
# ⚠ THREE SUBJECTS, TWO OF THEM CONFIGURATION. `.github/workflows/ci.yml` and `release.yml` are
# not code, and that is the point: the whole finding this row records is that the gate's SHAPE was
# wrong while every test passed. A pin over a workflow is only worth what its mutants prove, and
# the two workflows have to be mutated together — half of what stage 17 asserts about ci.yml's
# never-run leg is falsifiable only against release.yml, which is the other place a leg could
# have run.
#
# ⚠ THE ANCHORS DERIVE THE FLOOR RATHER THAN NAMING IT. `$FLOOR` below is read out of the
# provisioner this stage adds, so raising the project's floor moves the anchors with it instead of
# turning this whole batch into twenty `MUTATION-BROKEN: pattern occurs 0 times` reports.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# The mutator is overridable for the SAME reason `PYTHON` is: so this driver's own self-tests can
# drive all call sites against a stub returning a chosen exit code in milliseconds, instead of
# running that many real mutation passes. `mutate.sh`'s tests already use this idiom
# (`MUTATE_LOCKFILE`, `MUTATE_TESTS_DIR`, `MUTATE_LOCK_IMPL`). Real runs get the default.
M="${MUTATE_SH:-scripts/mutate.sh}"
# The three subjects, and the pattern that grades each.
CI=.github/workflows/ci.yml
REL=.github/workflows/release.yml
P=scripts/floor-python.sh
T='test_ci_matrix_floor.py'
TP='test_floor_provisioner.py'

# THE DECLARED FLOOR, read rather than typed — see the header. If this cannot be read the anchors
# below are meaningless, so it fails here instead of reporting twenty broken patterns.
FLOOR="$(bash scripts/floor-python.sh --print-floor)" || exit 1
case "$FLOOR" in [0-9]*.[0-9]*) : ;; *) echo "could not derive the floor" >&2; exit 1 ;; esac

# The number of `mutant` call sites below. Pinned against the real count by
# `test_mutant_batch_driver.TestTheAccountingCannotDrift`, because "3 of 8 never ran" is only true
# if 8 is true — a batch that overstates its own list is the silent-truncation shape doc 85 warns
# about, wearing the costume of an honest abort.
TOTAL=21

ran=0
red=0
green=0
survivors=""

abort() {
    local rc="$1" label="$2" meaning remedy
    # Every code below is one scripts/mutate.sh's EXIT CONTRACT documents; that header is the
    # single source and this is its consumer. `test_mutant_batch_driver` fails if this dispatch
    # ever explains a code the contract does not define.
    case "$rc" in
        1) meaning="USAGE OR ENVIRONMENT — a bad argument, or a target that could not be copied."
           remedy="  Nothing was touched. Fix the invocation for this mutant and re-run." ;;
        2) meaning="LOCK MISCONFIGURED — MUTATE_LOCK_IMPL is not auto|flock|pidfile, or flock is absent."
           remedy="  Nothing was touched. Fix MUTATE_LOCK_IMPL and re-run." ;;
        3) meaning="THE MUTATION COULD NOT BE APPLIED — the pattern matched no site, or several."
           remedy="  The tree is RESTORED, so nothing is contaminated. This mutant's old/new strings
  have drifted from the source they describe; re-derive them and re-run." ;;
        4) meaning="REFUSED RESTORE — something wrote to the target while the run held it snapshotted."
           remedy="  ★ THE WORKING TREE IS STILL MUTATED, on purpose. mutate.sh refused to restore so
  that the other edit survived, and left BOTH copies: the target as they wrote it, and the
  pristine source beside it as <file>.REFUSED-RESTORE*.bak. Its message above names the file
  and both hashes.
  REMEDY: diff the target against its .REFUSED-RESTORE*.bak, keep whichever content you want,
  delete the .bak, check 'git diff' says what you expect, and only then re-run the batch." ;;
        5) meaning="LOCK BUSY — another mutate.sh run holds the mutation lock."
           remedy="  Nothing was touched. Two runs interleaving is exactly how one run's snapshot gets
  restored over the other's work, so this one refused to start rather than queue.
  REMEDY: wait for the other run to report, or kill it and remove its lockfile, then re-run." ;;
        6) meaning="NO VERDICT — the mutation applied but the test run produced nothing to grade."
           remedy="  The tree is RESTORED. Either the test pattern matched no files, or discovery never
  reported at all. Either way nothing was exercised, so this mutant is UNGRADED — it is not a
  survivor. Fix the pattern (or the tests dir) and re-run." ;;
        7) meaning="THE BASELINE WAS NOT GREEN — the selected tests were ALREADY FAILING, unmutated."
           remedy="  Nothing was mutated and nothing was graded. This is the one status that condemns the
  WHOLE BATCH rather than this mutant: every later mutant would find the same failure, be scored
  RED, and credit the kill to a mutation that had nothing to do with it — and a batch of those
  reports a PERFECT SCORE. That is what 0.0.17 stage 28's clean sweep turned out to be.
  REMEDY: make the selected tests pass on an unmutated tree, then re-run the batch." ;;
        *) meaning="UNKNOWN STATUS $rc — not in scripts/mutate.sh's EXIT CONTRACT."
           remedy="  Treat the tree as SUSPECT: check 'git status' and 'git diff' before doing anything
  else, and reconcile this status with the contract in scripts/mutate.sh's header." ;;
    esac
    # stdout, not stderr — see the header. A batch log that captures only stdout must not be able
    # to end mid-list looking like a completed run.
    printf '\n'
    printf '================================================================================\n'
    printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
    printf '  mutant: %s\n' "$label"
    printf '  status: %s\n' "$meaning"
    printf '%s\n' "$remedy"
    printf '\n'
    printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
        "$((TOTAL - ran))" "$TOTAL"
    printf '  A harness failure means NO VERDICT was produced and the tree may not hold what this\n'
    printf '  run believes it holds, so every grade after this point would have been read off a\n'
    printf '  tree nothing can vouch for.\n'
    printf '================================================================================\n'
    exit "$rc"
}

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    # stdout is captured so the verdict can be COUNTED and so a status-0 run carrying no verdict
    # is noticed — then re-emitted immediately, so the batch log reads exactly as it always did.
    # stderr is deliberately NOT captured: mutate.sh's REFUSED-RESTORE and LOCK-BUSY blocks go
    # straight to the terminal, where they are the first thing the reader needs.
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi

    if [ "$rc" -ne 0 ]; then abort "$rc" "$label"; fi

    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      # Status 0 is a CLAIM that a verdict exists. If the mutator ever drifts — a new
                # branch that prints something else and falls off the end at 0 — counting that
                # line as a graded mutant is the very laundering every hole in mutate.sh's header
                # turned out to be. 70 rather than a mutate.sh code: this is the driver's finding.
                printf '\n'
                printf 'BATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n' "$label"
                printf '  said  : %s\n' "${out:-<nothing at all>}"
                printf '  Exit 0 means "a RED or GREEN verdict was produced" (scripts/mutate.sh, EXIT\n'
                printf '  CONTRACT). This run claimed one and did not produce it, so it is UNGRADED and\n'
                printf '  the contract the rest of this batch relies on no longer holds.\n'
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}


# =================================================================================================
# PART 1 — THE BREADTH. Three floor legs, and each of the two new ones can be lost in a different
# way. A pin that only counted legs would survive every one of these.
# =================================================================================================

mutant "M01 the Windows floor leg is quietly moved back to ubuntu — the OS axis reopens" "$CI" \
  "          - os: \"windows-latest\"
            python: \"$FLOOR\"" \
  "          - os: \"ubuntu-latest\"
            python: \"$FLOOR\"" "$T"

mutant "M02 the jsonschema=absent floor leg becomes a second present leg — the dep axis reopens" "$CI" \
  "            python: \"$FLOOR\"
            jsonschema: \"absent\"" \
  "            python: \"$FLOOR\"
            jsonschema: \"present\"" "$T"

mutant "M03 the floor is 'fixed' onto the python axis — the cross-product this row refused" "$CI" \
  '        python: ["3.12"]' \
  "        python: [\"3.12\", \"$FLOOR\"]" "$T"

mutant "M04 the floor REPLACES 3.12 rather than joining it — coverage swapped, not added" "$CI" \
  '        python: ["3.12"]' \
  "        python: [\"$FLOOR\"]" "$T"

mutant "M05 a floor leg goes HOLLOW — no jsonschema key, so every conditional step skips" "$CI" \
  "            python: \"$FLOOR\"
            jsonschema: \"absent\"
" \
  "            python: \"$FLOOR\"
" "$T"

mutant "M06 Windows loses its 3.12 legs — 'we have the Windows floor now' as a coverage trade" "$CI" \
  '        os: ["ubuntu-latest", "windows-latest"]' \
  '        os: ["ubuntu-latest"]' "$T"

# =================================================================================================
# PART 2 — THE FAILURE POLICY. The leg that has never run is BLOCKING by decision, so both ways of
# making it un-blocking must red: at the leg, and at the step every leg shares.
# =================================================================================================

mutant "M07 the never-run Windows leg is made continue-on-error — a red becomes a green tick" "$CI" \
  "          - os: \"windows-latest\"
            python: \"$FLOOR\"
            jsonschema: \"present\"" \
  "          - os: \"windows-latest\"
            python: \"$FLOOR\"
            jsonschema: \"present\"
            continue-on-error: true" "$T"

mutant "M08 the unit-suite STEP swallows its failure — every leg green whatever the tests say" "$CI" \
  '        run: python -m unittest discover -s tests -t tests
      - name: Run integration suite (the release gate)' \
  '        run: python -m unittest discover -s tests -t tests
        continue-on-error: true
      - name: Run integration suite (the release gate)' "$T"

mutant "M09 the whole test job is made non-blocking" "$CI" \
  '  test:
    name: ${{ matrix.os }}' \
  '  test:
    continue-on-error: true
    name: ${{ matrix.os }}' "$T"

# =================================================================================================
# PART 3 — THE PROVENANCE RECORD, and this is the half nothing else in the suite could grade.
# "This leg has never run anywhere" is a claim about history, and release.yml is the only place
# that claim is FALSIFIABLE. Both directions of the expiry are mutated, plus the derivation the
# policy itself rests on.
# =================================================================================================

mutant "M10 release.yml gains a Windows axis — the never-run leg HAS now run, and the record lies" "$REL" \
  "        python: [\"$FLOOR\", \"3.11\", \"3.12\", \"3.13\"]" \
  "        os: [\"windows-latest\"]
        python: [\"$FLOOR\", \"3.11\", \"3.12\", \"3.13\"]" "$T"

mutant "M11 release.yml drops the floor — a leg declared run-at-tag is really never-run" "$REL" \
  "        python: [\"$FLOOR\", \"3.11\", \"3.12\", \"3.13\"]" \
  '        python: ["3.11", "3.12", "3.13"]' "$T"

mutant "M12 the publish chain reaches a job release.yml does not declare — ci.yml gates the tag" "$REL" \
  '    needs: [test, validate]' \
  '    needs: [test, validate, ci]' "$T"

# =================================================================================================
# PART 4 — THE PROVISIONER. The floor must be DERIVED, and the grade must keep four answers apart.
# M15/M16/M20 are the ones this stage exists for: each collapses two facts into one status, which
# is precisely how "I ran it on the floor" became unfalsifiable in the first place.
# =================================================================================================

mutant "M13 the floor is hard-coded — the provisioner stops following pyproject.toml" "$P" \
  'FLOOR="$(declared_floor)"' \
  "FLOOR=\"$FLOOR\"" "$TP"

mutant "M14 the derivation loses the minor version — the floor becomes a major" "$P" \
  's/.*>=[[:space:]]*([0-9]+\.[0-9]+).*/\1/' \
  's/.*>=[[:space:]]*([0-9]+)\..*/\1/' "$TP"

mutant "M15 ABOVE the floor is accepted — the grade degenerates to the >= check it refuses" "$P" \
  '    return 4
}' \
  '    return 0
}' "$TP"

mutant "M16 BELOW and ABOVE share a status — two opposite remedies, one answer" "$P" \
  '        return 3' \
  '        return 4' "$TP"

mutant "M17 an un-provisioned venv is reported as below the floor — un-run wearing failed" "$P" \
  "            printf '  \`scripts/floor-python.sh\` to build it.\n' >&2
            exit 5" \
  "            printf '  \`scripts/floor-python.sh\` to build it.\n' >&2
            exit 3" "$TP"

mutant "M18 --check ignores its own grade — the one caller of the whole thing launders it" "$P" \
  '        grade_version "$("$PY" -c '"'"'import sys; print("%d.%d.%d" % sys.version_info[:3])'"'"')" "$PY"
        exit $? ;;' \
  '        grade_version "$("$PY" -c '"'"'import sys; print("%d.%d.%d" % sys.version_info[:3])'"'"')" "$PY"
        exit 0 ;;' "$TP"

mutant "M19 --exec runs the interpreter on PATH — the floor venv is built and then not used" "$P" \
  '        exec "$PY" "${EXEC_ARGS[@]}" ;;' \
  '        exec python3 "${EXEC_ARGS[@]}" ;;' "$TP"

mutant "M20 the provisioning command stops REQUESTING the floor — whatever uv picks becomes it" "$P" \
  "        printf 'uv venv --seed --python %s %s' \"\$FLOOR\" \"\$VENV\"" \
  "        printf 'uv venv --seed %s' \"\$VENV\"" "$TP"

mutant "M21 the default venv escapes the mirror-excluded directory — an interpreter ships" "$P" \
  'VENV="build/floor-venv"' \
  'VENV=".venv-floor"' "$TP"

printf '\n'
printf '================================================================================\n'
printf 'BATCH COMPLETE — %s/%s mutants ran, every one produced a verdict · %s RED · %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf '\n'
    printf '  %s SURVIVOR(S) — the mutation was NOT caught. Each is a real finding about a real\n' "$green"
    printf '  pin, and needs a look: either the pin is weak, or the mutated line is unreachable on\n'
    printf '  the path these tests drive (which wants an input that reaches it, not a deleted guard).\n'
    printf '%s' "$survivors"
fi
printf '================================================================================\n'
