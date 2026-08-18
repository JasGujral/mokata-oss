#!/usr/bin/env bash
# Stage 16 (CI-SPEED) mutant batch — driven through scripts/mutate.sh, the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage16_ci_speed_mutants.sh
#
# THE CONTRACT BELOW IS NOT RE-DERIVED HERE. Every line from `set -uo pipefail` down to the
# summary is `tests/_run_mutants.sh`'s, transplanted verbatim, because that driver is the one
# `tests/_mutant_driver_contract.py` grades and a hand-written copy is exactly the
# `MUTANT-DRIVER-CONTRACT-DUPLICATED` shape that row already names. What is this stage's is the
# TARGET, the TOTAL, and the mutant list.
#
# ⚠ THE SUBJECT IS A SCRIPT, NOT A MODULE, AND THAT IS THE POINT. `scripts/check-tracker-tables.py`
# is called "the ONLY mechanical doc gate" by `tests/test_s11_bookkeeping_derived.py`, doc 99 cites
# its `exit 0` as a release-check line, and until this stage NOTHING EXECUTED IT — it was red at
# HEAD and no runner, suite or hook would have said so. A checker nothing runs and nothing mutates
# is two claims deep in the same hole: the first is that it passes, the second is that passing
# means anything. These mutants grade the second.
#
# WHAT THE BATCH IS DESIGNED AROUND: `tests/test_stage16_doc_gate.py`'s clean-corpus assertion is
# the WEAK half — it would pass against a checker that had stopped checking — so all but one
# mutant below must be killed by the PLANTED cases, not by it. M07 is the deliberate exception:
# it is the one mutation the clean assertion alone catches, included so the split between the two
# halves is measured rather than asserted.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# The mutator is overridable for the SAME reason `PYTHON` is: so this driver's own self-tests can
# drive all call sites against a stub returning a chosen exit code in milliseconds, instead of
# running that many real mutation passes. `mutate.sh`'s tests already use this idiom
# (`MUTATE_LOCKFILE`, `MUTATE_TESTS_DIR`, `MUTATE_LOCK_IMPL`). Real runs get the default.
M="${MUTATE_SH:-scripts/mutate.sh}"
# The one subject this stage wired into the suite, and the pattern that grades it.
C=scripts/check-tracker-tables.py
T='test_stage16_doc_gate.py'

# The number of `mutant` call sites below. Pinned against the real count by
# `test_mutant_batch_driver.TestTheAccountingCannotDrift`, because "3 of 8 never ran" is only true
# if 8 is true — a batch that overstates its own list is the silent-truncation shape doc 85 warns
# about, wearing the costume of an honest abort.
TOTAL=8

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

# ---------------------------------------------------------------------------------------------
# THE RENDER RULE — the escape is the whole reason this checker exists rather than a `split('|')`,
# so it is mutated in both directions: the rule dropped, and the rule applied where it should not
# be. GFM splits cells BEFORE inline parsing; Python-Markdown does not, and trusting the lenient
# renderer is how the live defect survived to HEAD.
# ---------------------------------------------------------------------------------------------

mutant "M01 the escape is ignored — an escaped pipe splits a cell again" "$C" \
  '_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")' \
  '_UNESCAPED_PIPE = re.compile(r"\|")' "$T"

mutant "M02 the render rule is inverted — only ESCAPED pipes split" "$C" \
  '_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")' \
  '_UNESCAPED_PIPE = re.compile(r"(?<=\\)\|")' "$T"

# ---------------------------------------------------------------------------------------------
# THE COMPARISON — two defects share one symptom and the row exists to catch BOTH, so a checker
# narrowed to either half is a gate that passes on half the defects it advertises. Each direction
# is mutated on its own, because a single `!=` mutant could be killed by either planted case and
# would not tell which.
# ---------------------------------------------------------------------------------------------

mutant "M03 short rows stop being defects — 'no verdict recorded' renders unchecked" "$C" \
  '        if got != want:' \
  '        if got > want:' "$T"

mutant "M04 extra cells stop being defects — the DROPPED cell renders unchecked" "$C" \
  '        if got != want:' \
  '        if got < want:' "$T"

# ---------------------------------------------------------------------------------------------
# THE VERDICT REACHING THE CALLER — doc 99 cites this script's `exit 0` as a release-check line,
# so a checker that finds everything and exits 0 anyway is worse than no checker: it launders.
# ---------------------------------------------------------------------------------------------

mutant "M05 findings are printed but the exit code stays 0 — doc 99's release line launders" "$C" \
  '    if failures:' \
  '    if False:' "$T"

mutant "M06 the refusal stops naming the row — a count with nothing to act on" "$C" \
  '            key = _UNESCAPED_PIPE.split(stripped.strip("|"))[0].strip()[:44]' \
  '            key = ""' "$T"

# ---------------------------------------------------------------------------------------------
# WHAT DEFINES A TABLE — the separator row is what makes the line above it a header, and the
# header is what every count is measured against. Get this wrong and every later comparison is
# against the wrong number, which is the failure mode a cell-counting checker cannot self-detect.
# ---------------------------------------------------------------------------------------------

mutant "M07 the header is read off the wrong line — every row measured against a phantom" "$C" \
  '            want = cell_count(lines[n - 2])' \
  '            want = cell_count(lines[n - 3])' "$T"

mutant "M08 any pipe line defines a table — the separator stops being required" "$C" \
  '_SEP = re.compile(r"^\|(?:\s*:?-{1,}:?\s*\|)+$")' \
  '_SEP = re.compile(r"^\|")' "$T"

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
