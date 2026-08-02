#!/usr/bin/env bash
# mutate.sh — apply ONE source mutation, run the tests that should catch it, restore, report.
#
#   scripts/mutate.sh "<label>" <file> "<old>" "<new>" "<test pattern>"
#
#   scripts/mutate.sh "guard dropped" src/mokata/memory/store.py \
#       "if o.id != keep.id:" "if True:" "test_derives_from_producer.py"
#
# RED   = the tests caught the mutation. The pin is real.
# GREEN = the mutation SURVIVED. Either the pin is weak, or the code is unreachable.
# BROKEN = no verdict was produced. Never read a BROKEN line as either of the above.
#
# Env: PYTHON (default python3) · MUTATE_TESTS_DIR (default tests)
#
# ---------------------------------------------------------------------------------------------
# WHY THIS FILE IS SO CAREFUL: A HARNESS WHOSE GREENS NEED SUSPICION PROVES NOTHING
#
# Mutation testing is only worth running if a GREEN is believable, because GREEN is the verdict
# that makes you go change something. This harness previously produced a FALSE GREEN, and it was
# the fourth integrity failure in a row. Five holes are now closed, and each is named below with
# the reason, because the next person to "simplify" one of them needs to know what it cost.
#
# THE ROOT CAUSE (hole 1). CPython decides a cached `.pyc` is still valid by comparing exactly
# two things against the source file: the **mtime truncated to whole seconds**, and the **size in
# bytes**. Nothing else — not a hash of the content. This harness's mutate -> run -> restore cycle
# completes in tens of milliseconds, so inside a batch the mtime is NOT a discriminator between
# consecutive states of the same file: several mutants land in the same integer second. That
# leaves SIZE as the only thing standing between a mutant and its own bytecode.
#
# So a mutation that PRESERVES the byte count is invisible to the import system. A pure REORDER
# of two statements is the obvious case; so is any equal-length token swap, and `!=` -> `==` is
# both the commonest mutation operator there is and exactly size-preserving. The tests then run
# the PRISTINE bytecode, pass, and the mutant is reported GREEN. This is not flakiness — it is
# deterministic for that class of mutation whenever the previous compile of that file landed in
# the same second.
#
# THE CONTRAPOSITIVE, worth keeping in your head when triaging an old result: a mutation that
# CHANGES the byte count can never be affected, because size alone invalidates the pyc no matter
# what the clock did. Only size-preserving mutants were ever at risk. That bounds the blast
# radius of any pre-fix run precisely, and it is how a historical pass can be audited cheaply
# instead of re-run wholesale.
#
# THE MIRROR HAZARD (hole 2) is the worse half and is why deleting the pyc alone is not enough.
# If a size-preserving mutant's bytecode DOES get written, the restore puts the pristine source
# back under an mtime and size that the MUTANT pyc still matches. Nothing recompiles. `git status`
# is clean, the diff is empty, and the installed package executes mutant code until something
# happens to touch the file — including for every test run that follows, in this shell or any
# other. A harness that can silently leave the working copy executing code that is not in the
# working copy is a hazard well beyond a wrong verdict.
#
# THE FIVE HOLES, ALL CLOSED HERE
#   1. STALE BYTECODE IN. A size-preserving mutant reused pristine bytecode and reported GREEN.
#      Closed by deleting the target's pyc before the run, so nothing stale can be READ.
#   2. MUTANT BYTECODE OUT (the mirror hazard above). Closed by PYTHONDONTWRITEBYTECODE=1 for the
#      run, so the mutant's bytecode is never persisted and the restore cannot be poisoned.
#      Together, 1 and 2 make batch and isolation equivalent BY CONSTRUCTION rather than by luck:
#      correctness no longer depends on how much wall-clock passed between two mutants.
#   3. NO RESTORE ON FAILURE. The source was put back only on the happy path. Any mid-batch abort
#      therefore left the file MUTATED, and every later mutant in that batch either failed to
#      apply or — far worse — applied ON TOP of the previous one and reported a verdict for a
#      COMPOUND mutant nobody designed. One failure could corrupt an entire batch silently.
#      Closed by `trap restore EXIT INT TERM`, so the source goes back however we leave.
#   4. A BROKEN MUTATION DIED SILENTLY. The old code ran the patcher, then tested `rc=$?` to
#      report failure — but under `set -e` a non-zero patcher already killed the script, so that
#      branch was unreachable dead code and the batch just stopped, mid-list, with no message.
#      Closed with `if ! ...; then`, which suppresses errexit for the test so the failure is
#      REPORTED instead of being fatal.
#   5. "Ran 0 tests ... OK" READ AS A SURVIVOR. A typo in the test pattern matches no files;
#      unittest prints OK; the old grep saw no FAILED and called it GREEN. A pattern that matched
#      nothing is the absence of evidence, not evidence of survival. Closed with an explicit
#      BROKEN verdict.
#
# What is deliberately NOT claimed: this does not make a GREEN mean "the pin is weak". A GREEN can
# still be an honest survivor because the mutated line is UNREACHABLE on the path the tests drive.
# That is a real finding and wants a hand-built input that reaches it, not a deleted guard.
# ---------------------------------------------------------------------------------------------
set -euo pipefail

label="${1:?usage: scripts/mutate.sh <label> <file> <old> <new> <test-pattern>}"
target="${2:?missing <file>}"
old="${3:?missing <old>}"
new="${4-}"          # may legitimately be empty: deleting a line IS a mutation
pattern="${5:?missing <test-pattern>}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
TESTS_DIR="${MUTATE_TESTS_DIR:-tests}"
cd "$ROOT"

pycdir="$(dirname "$target")/__pycache__"
base="$(basename "$target" .py)"

cp "$target" "$target.bak"

# HOLE 3: the source goes back however we leave — success, failure, or Ctrl-C. The pyc goes with
# it, so no bytecode compiled from a mutated source ever outlives this script.
restore() {
    if [ -f "$target.bak" ]; then mv -f "$target.bak" "$target"; fi
    rm -f "$pycdir/$base".*.pyc
}
trap restore EXIT INT TERM

# HOLE 4: `if ! ...` suppresses errexit for this command, so a mutation that cannot be applied is
# REPORTED rather than silently fatal to the whole batch.
if ! "$PYTHON" - "$target" "$old" "$new" <<'PYEOF'
import sys

path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
source = open(path, encoding="utf-8").read()
found = source.count(old)
if found != 1:
    # Not "> 0": a pattern matching twice mutates BOTH sites, and the verdict would then belong
    # to a mutant with two edits in it. Exactly one, or it is not the mutation that was described.
    print(f"MUTATION-BROKEN: pattern occurs {found} times, expected exactly 1", file=sys.stderr)
    sys.exit(3)
open(path, "w", encoding="utf-8").write(source.replace(old, new))
PYEOF
then
    echo "BROKEN!!  $label   <-- MUTATION COULD NOT BE APPLIED (not a verdict)"
    exit 3
fi

rm -f "$pycdir/$base".*.pyc          # HOLE 1: nothing stale can be read

set +e                               # the grep finds nothing on a clean run; that is not an error
out=$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover \
        -s "$TESTS_DIR" -t "$TESTS_DIR" -p "$pattern" 2>&1 \
      | grep -E "^(Ran|OK|FAILED)")   # HOLE 2: nothing mutant can be written
set -e

summary="$(echo "$out" | tr '\n' ' ')"

if echo "$out" | grep -qE "^Ran 0 tests"; then
    # HOLE 5
    echo "BROKEN!!  $label   ($summary)  <-- PATTERN MATCHED NO TESTS (not a verdict)"
elif echo "$out" | grep -q FAILED; then
    echo "RED   ✓  $label   ($summary)"
else
    echo "GREEN ✗  $label   ($summary)  <-- SURVIVOR"
fi
