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
# EXIT CONTRACT — THE SINGLE SOURCE. `tests/_run_mutants.sh` dispatches on exactly these codes.
#
# THE ONE RULE, and everything below is a consequence of it:
#
#     exit 0  IF AND ONLY IF  A VERDICT WAS PRODUCED.
#
# A VERDICT (RED or GREEN) is a run that COMPLETED: the mutation was applied, the selected tests
# ran, and they either caught it or they did not. A HARNESS FAILURE (everything non-zero) means
# NO VERDICT EXISTS and the tree may not be trustworthy. If those two ever share a status, no
# caller can tell a graded mutant from a broken run, and a batch will happily grade 20 more
# mutants against a tree it has no business trusting. That is not hypothetical: this contract was
# written at 0.0.17 stage 18b, after the batch driver was found running all 26 mutants and
# exiting 0 no matter what this script returned.
#
#   0  A VERDICT WAS PRODUCED. stdout carries exactly one RED or GREEN line. The target is
#      restored byte-for-byte, the `.bak` is gone, and no mutant bytecode survives the run.
#      THIS IS THE ONLY STATUS THAT MEANS ANYTHING WAS GRADED.
#
#   1  USAGE OR ENVIRONMENT, before anything was touched. A missing argument (bash's own status
#      for the `${n:?...}` expansions above) or a target that cannot be copied. No lock was
#      taken, no snapshot exists, the tree is untouched.
#
#   2  LOCK MISCONFIGURED. MUTATE_LOCK_IMPL is not auto|flock|pidfile, or flock was demanded and
#      is not on PATH. Nothing was touched.
#
#   3  THE MUTATION COULD NOT BE APPLIED — the pattern occurred zero times, or more than one
#      (see hole 4, and the `found != 1` note in the patcher for why two is not "good enough").
#      The tree is RESTORED. A retry after fixing the pattern is safe.
#
#   4  ★ REFUSED RESTORE, AND THE TARGET IS LEFT MUTATED ON PURPOSE. Something wrote to the file
#      while this run held it snapshotted, so restoring would have destroyed their work and left
#      `git status` clean (hole 6). The pristine copy is at `<file>.REFUSED-RESTORE*.bak` and
#      BOTH survive. THIS IS THE ONE STATUS A CALLER MUST NOT CARRY ON PAST: the working tree is
#      still carrying the mutation, so every later grade would be read off a contaminated tree.
#
#   5  LOCK BUSY. Another mutate.sh run holds the mutation lock. This run took no snapshot and
#      did not touch the target — it fails fast rather than queueing, because queueing would
#      only move the collision later.
#
#   130 / 143  KILLED BY A SIGNAL (128+signum: INT=130, TERM=143). The tree is RESTORED and the
#      lock released before the status is returned — a signalled run terminates rather than
#      returning into its wait (0.0.17 stage 18c). Like every non-zero status it means NO VERDICT
#      EXISTS, so a batch driver must stop; it is the ordinary status for a Ctrl-C.
#
#   7  ★ THE BASELINE WAS NOT GREEN. The selected tests were ALREADY FAILING with the target
#      PRISTINE, so no result from this tree can be attributed to a mutation. The mutation was
#      never applied and the tree is RESTORED untouched. A caller MUST abort the whole batch:
#      the next mutant would be graded against the same broken tree and would report the same
#      unattributable failure, which a batch driver counts as a kill. See THE GREEN BASELINE.
#
#   6  NO VERDICT FROM THE TEST RUN. The mutation applied and the tree is RESTORED, but the run
#      produced nothing to grade, in one of two shapes:
#        (a) the test pattern matched no files — unittest prints "Ran 0 tests ... OK" (hole 5);
#        (b) the test run never reported at all — no `Ran` line whatsoever, e.g. discovery could
#            not import MUTATE_TESTS_DIR. Until stage 18b this fell out of the else-branch and
#            was printed as `GREEN ... <-- SURVIVOR` at exit 0: a false GREEN, from a run in
#            which not one test executed. Both are the absence of evidence; neither is evidence
#            of survival.
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
#
# ---------------------------------------------------------------------------------------------
# HOLE 6 — THE SNAPSHOT INTERLOCK. The five holes above are all about BYTECODE. This one is about
# the SNAPSHOT, and it produces the same silent corruption by a different door.
#
# This script copies the target to `<file>.bak` and its EXIT trap puts that copy back
# UNCONDITIONALLY. So if anything else writes the target while a run holds that snapshot, the
# restore reverts it. And because the restore puts back the file the run itself started from,
# `git status` comes back CLEAN and `git diff` empty. The work is simply gone, with no artefact
# anywhere saying so — the same undetectable signature as the mirror hazard, reached another way.
#
# NOT THEORETICAL, AGAIN. 0.0.17 stage 1a-FU: a source file was edited while a mutation run had it
# snapshotted. It did not fire only because the verdict was read before anyone touched the file
# again. That was the SECOND near-miss on this guardrail in two consecutive stages (1a was a
# size-preserving hand edit outside this script), and neither was caught by a test — both were
# self-reported. A guardrail that depends on somebody remembering is a discipline, not a mechanism.
# Doc 85 §7b had the rule the whole time. The rule was the problem.
#
# WHY THE CHECK IS A CONTENT HASH, AND WHY IT MUST STAY ONE. The cheap implementation is to stat
# the file at snapshot time and compare mtime + size at restore time. That is EXACTLY the pair
# CPython uses to validate a `.pyc`, and holes 1 and 2 exist because a size-preserving edit inside
# one integer second defeats it. Reproducing that comparison inside the guard AGAINST it would be
# a joke told with a straight face. So: the sha256 of the bytes this run itself last wrote is
# recorded, and the file on disk is re-hashed at restore time. Only content identity can see an
# edit that kept the byte count and put the mtime back.
#
# ON A MISMATCH THE RESTORE IS REFUSED, LOUDLY AND NON-ZERO — a silent skip would be the same
# defect wearing a different mask. Both copies survive: their edit is left exactly where it is,
# and the pristine copy is renamed to `<file>.REFUSED-RESTORE.bak` so the next run cannot clobber
# it under the ordinary `.bak` name. The message names the file, both hashes, and the remedy.
#
# THE KILL PATH IS UNAFFECTED BY CONSTRUCTION, and that matters — a run killed at a timeout
# restoring correctly is the trap doing its job. Killed mid-run, the file on disk IS what this
# script last wrote, so the hash matches and the restore proceeds exactly as it always did. A
# guard that broke that would leave every interrupted run MUTATED, which is worse than the hazard.
#
# AND AN EXCLUSIVE LOCK, so the collision cannot be set up in the first place: one mutation run at
# a time per checkout, `flock` where it exists and a pid-bearing lockfile everywhere else (stock
# macOS ships no flock). A second run FAILS FAST naming the pid and the file the other run holds —
# it does not queue and it does not proceed, because a queue would just make the collision later.
# A lockfile whose pid is dead is a corpse, not a holder, and is reclaimed: a dev tool that bricks
# a checkout after one Ctrl-C would be turned off, and a guard that is turned off guards nothing.
#
# ---------------------------------------------------------------------------------------------
# HOLE 7 — THE GREEN BASELINE (0.0.18 stage 31, MUTANT-BATCH-NEEDS-A-GREEN-BASELINE).
#
# Holes 1-5 are about BYTECODE and hole 6 is about the SNAPSHOT. This one is about the TREE, and
# it produces the most convincing false result the harness can produce, because it wears the
# costume of a perfect run.
#
# THE DEFECT. Every mutant ran against a tree nobody had verified. If anything unrelated was
# ALREADY FAILING in the selected test set, the mutant run found `FAILED` and was graded RED — a
# kill credited to a mutation that had nothing to do with it. Do that to a whole batch and every
# mutant reports RED, the driver scores a clean sweep, and the log is indistinguishable from a
# healthy batch. A GREEN at least sends somebody to look; this produces nothing to look at.
#
# NOT HYPOTHETICAL. 0.0.17 stage 28 shipped a 27/27 this way. The 29b rider re-measured it and
# found 4 of the 27 false and a 5th machine-dependent. The contaminant was `.mutate.lock`.
#
# ★ WHY IT RUNS HERE — THE PLACEMENT IS THE WHOLE DESIGN, AND THE OBVIOUS PLACE IS WRONG.
#
# The natural instinct is to run one clean suite in the BATCH DRIVER before mutant 1. That guard
# would have been DECORATIVE, and stage 28 is the proof. Its contaminant was `.mutate.lock`, which
# this script creates for the DURATION OF EACH MUTANT RUN and which is a literal
# `sync-public.sh --exclude` entry — so under the then-current disk-probe derivation it joined the
# deriving set while, and only while, the mutator was up. A driver-level pre-check executes with
# NO LOCK ON DISK. In stage 28's scenario it would have come back green, the batch would have
# proceeded, and all 27 mutants would still have been scored off a contaminated corpus. A check
# that cannot fail, read as a check that passed.
#
# So the baseline runs INSIDE this script, AFTER the lock is taken and AFTER the snapshot exists,
# with only the mutation itself skipped. Every artefact a mutant run puts in the tree — the lock,
# the `.bak` — is on disk while the baseline executes, so the baseline sees what the graded run
# sees. `TestTheBaselineSeesWhatAMutantRunSees` proves that by construction: the fixture's own
# test process writes down the lock, the snapshot and the target's bytes from inside each phase,
# and the pin reads the BASELINE's record back. DO NOT HOIST THIS INTO THE DRIVER TO SAVE TIME.
#
# ONE PLACE, NOT TWENTY-THREE. Every mutant driver in `tests/` routes through this script, so the
# mechanism reaches all of them without a 23-file edit — and without a 23rd copy of a contract
# that `MUTANT-DRIVER-CONTRACT-DUPLICATED` already files as over-duplicated.
#
# WHAT IT COVERS, STATED RATHER THAN IMPLIED. The baseline runs THIS mutant's own test pattern,
# immediately before THIS mutant, inside THIS mutant's lock. A batch that uses a different pattern
# per mutant is therefore covered pattern-by-pattern with no union to reason about, and
# contamination that appears MID-BATCH is caught at the mutant it first affects rather than only
# at mutant 1. WHAT IT DOES NOT CATCH: a failure that appears BETWEEN the baseline and the graded
# run of the same mutant — including one caused by the mutant's own test run — is still scored as
# a kill. That window is one test run wide and nothing here closes it.
#
# THE COST IS A SECOND TEST RUN PER MUTANT, and it was accepted deliberately. The cheaper design
# is to memoise the baseline per batch, which needs a cross-process cache keyed on something that
# identifies "this batch" (the parent pid), plus its invalidation and its staleness rules. That is
# a second code path through the harness that nothing routinely exercises, and every hole above is
# a second code path that nothing routinely exercised. A doubled dev-tool runtime is the cheaper
# of the two prices.
#
# §7g — THREE OUTCOMES, THREE REPRESENTATIONS. "the tests were already failing" gets exit 7 of its
# own. It is NOT folded into exit 6: an empty or unreportable test run is the ABSENCE of evidence
# and wants "fix the pattern", while a red baseline is evidence of a broken TREE and wants "fix the
# tree". So the abort fires only on POSITIVE evidence — a `Ran` line AND a `FAILED` — and every
# other shape falls through to the machinery that already classifies it.
#
# ---------------------------------------------------------------------------------------------
# ★ STANDING RULE: NEVER MUTATE A SOURCE FILE BY HAND. THIS SCRIPT OR NOTHING.
#
# Holes 1 and 2 are properties of CPython's bytecode cache, NOT of this script. They apply to ANY
# mutate -> restore done by hand, and by hand NEITHER protection is in force: nothing deletes the
# stale pyc going in, and nothing stops the mutant pyc being written on the way out.
#
# THIS IS NOT THEORETICAL — IT RECURRED, LIVE, DURING THE 2026-08-02 PRE-AUDIT SWEEP, in the very
# session that was auditing this hazard. A by-hand edit of `src/mokata/teamdb.py`
# (`_pg.open_unmanaged` -> `_pg.get_connection`, 14 characters -> 14 characters, exactly
# size-preserving) was applied and restored outside this script. The restore landed in the same
# integer second, so the mutant `.pyc` stayed valid. THE WORKING TREE THEN EXECUTED CODE THAT WAS
# NOT IN THE WORKING COPY FOR ROUGHLY FORTY MINUTES, with `git status` clean and `git diff` empty.
# It reddened three PROBE-ORPHAN pins and sent the investigation after a phantom cross-test
# interaction that did not exist.
#
# It was caught only by a `_MANAGER.__setitem__` trace whose stack read `teamdb.py:459
# open_unmanaged` calling `_pg.py:151 get_connection`. THAT IS THE SIGNATURE, and it is worth
# memorising because nothing else gives the game away: THE SOURCE LINE AND THE EXECUTING FRAME
# DISAGREE. Neither the diff, the status, nor the test output can show you this — by construction,
# since the whole failure is that the file on disk is not the code being run.
#
# So: if you are about to hand-edit a source file to see whether a test catches it, STOP and run
# this script instead. If you have already done it, `find . -name '__pycache__' -exec rm -rf {} +`
# before you believe ANY result from that shell. A one-line edit is exactly the size-preserving
# case, and "I'll put it back in a second" is exactly the mtime collision.
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

LOCKFILE="${MUTATE_LOCKFILE:-$ROOT/.mutate.lock}"
LOCK_IMPL="${MUTATE_LOCK_IMPL:-auto}"          # auto | flock | pidfile

# HOLE 6 (a): content identity. The hasher is chosen ONCE, here, so the teardown path never
# depends on a tool lookup at the moment it is least able to cope with one failing.
if command -v shasum >/dev/null 2>&1; then
    sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
elif command -v sha256sum >/dev/null 2>&1; then
    sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v openssl >/dev/null 2>&1; then
    sha256_of() { openssl dgst -sha256 "$1" | awk '{print $NF}'; }
else
    sha256_of() { "$PYTHON" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }
fi

# HOLE 6 (b): one mutation run at a time per checkout.
lock_held=0
lock_impl_used=""
holder_pid=""
want_flock=0

publish_lock() { printf 'pid=%s\ntarget=%s\n' "$$" "$target" > "$LOCKFILE"; }
lock_holder_line() {
    if [ -s "$LOCKFILE" ]; then tr '\n' ' ' < "$LOCKFILE"; else printf 'pid=unknown target=unknown'; fi
}

acquire_lock() {
    case "$LOCK_IMPL" in
        flock)
            if ! command -v flock >/dev/null 2>&1; then
                echo "mutate.sh: MUTATE_LOCK_IMPL=flock but no flock(1) is on PATH" >&2
                exit 2
            fi
            want_flock=1 ;;
        pidfile) want_flock=0 ;;
        auto)    if command -v flock >/dev/null 2>&1; then want_flock=1; else want_flock=0; fi ;;
        *) echo "mutate.sh: MUTATE_LOCK_IMPL must be auto|flock|pidfile (got '$LOCK_IMPL')" >&2
           exit 2 ;;
    esac

    if [ "$want_flock" = 1 ]; then
        lock_impl_used="flock"
        # `9<>` and NOT `9>`: `>` truncates at OPEN, so a waiter using it would destroy the
        # holder's identity before it even asked whether it could have the lock — and the
        # fail-fast message would then have no pid to name.
        exec 9<>"$LOCKFILE"
        if flock -n 9; then
            publish_lock
            lock_held=1
            return 0
        fi
        return 1
    fi

    lock_impl_used="pidfile"
    # `set -C` makes the redirect O_EXCL, so creation IS the acquisition — atomic, no window.
    # It runs in a subshell so noclobber does not leak into the rest of the script.
    if (set -C; publish_lock) 2>/dev/null; then lock_held=1; return 0; fi

    # The file exists, but a lockfile is only a HOLDER while its pid is alive. Otherwise it is
    # the corpse of a run that was SIGKILLed, and refusing forever on a corpse would brick the
    # checkout after one Ctrl-C.
    holder_pid="$(sed -n 's/^pid=//p' "$LOCKFILE" 2>/dev/null | head -1)"
    if [ -n "$holder_pid" ] && kill -0 "$holder_pid" 2>/dev/null; then return 1; fi
    rm -f "$LOCKFILE"
    if (set -C; publish_lock) 2>/dev/null; then lock_held=1; return 0; fi
    return 1
}

release_lock() {
    if [ "$lock_held" != 1 ]; then return 0; fi
    lock_held=0
    if [ "$lock_impl_used" = "flock" ]; then
        # Truncate rather than unlink: another run may already hold this path open, and removing
        # the inode under it would hand two runs two different locks under the same name.
        : > "$LOCKFILE"
        flock -u 9 2>/dev/null || true
        exec 9>&-
    else
        rm -f "$LOCKFILE"
    fi
}

# THE AUTHORED SET — the sha256 of every state THIS RUN is responsible for the target being in.
# Those are the only contents the restore is allowed to overwrite. Both are set before the trap
# can need them, so teardown never reads one unset.
#
#   pristine_sha  the snapshot, i.e. what the target held when this run took its .bak
#   expected_sha  the bytes this run last wrote (== pristine_sha until the mutation is applied)
#
# ⚠ WHY A SET AND NOT ONE VALUE (0.0.17 stage 18c, from a live defect this script shipped with).
# `expected_sha` used to be updated AFTER the mutator's command substitution returned — but the
# mutant bytes reach disk INSIDE that substitution, and bash defers a pending TERM trap to the end
# of the enclosing compound command. A run killed in that gap therefore ran `restore()` while
# `expected_sha` still held the PRISTINE hash, compared the mutant on disk against it, concluded a
# third party had written, refused, and exited 4 — leaving the tree MUTATED (hole 3, the very
# hazard the trap exists to close), a displaced .REFUSED-RESTORE.bak beside it, and a message
# accusing a collision that never happened. It read as a flake for three stages, and reddened CI.
#
# One value cannot fix it in either direction: the shell learns the mutant hash only after the
# write, and pre-assigning it would break the mirror-image window where the target is still
# pristine. So the mutation is PLANNED (hashed in memory, nothing written), BOTH hashes are
# recorded, and only then is it APPLIED. Every instant the target holds one of two known states.
#
# HOLE 6 IS UNWEAKENED. A third party's bytes match neither hash, so the refusal fires exactly as
# before — pinned by `test_a_real_third_party_write_in_that_same_window_is_still_refused`, which
# writes in this exact window and demands the refusal STAND.
pristine_sha=""
expected_sha=""
restore_done=0
refused=0

# True when the bytes on disk are a state this run put there (or left there).
we_authored() {
    [ -n "$1" ] && { [ "$1" = "$expected_sha" ] || [ "$1" = "$pristine_sha" ]; }
}

# HOLE 3: the source goes back however we leave — success, failure, or Ctrl-C. The pyc goes with
# it, so no bytecode compiled from a mutated source ever outlives this script.
# HOLE 6: ...unless the file changed underneath us, in which case putting our copy back would
# destroy somebody else's work and leave no trace that it happened. Then we REFUSE, keep both,
# and fail non-zero.
restore() {
    if [ "$restore_done" = 1 ]; then return 0; fi   # the TERM trap and the EXIT trap both call it
    restore_done=1

    if [ -f "$target.bak" ]; then
        found=""
        if [ -f "$target" ]; then found="$(sha256_of "$target")"; fi
        if we_authored "$found"; then
            mv -f "$target.bak" "$target"
            rm -f "$pycdir/$base".*.pyc
        else
            keep="$target.REFUSED-RESTORE.bak"
            # A second refusal must not clobber the first one's preserved copy — that would be
            # this stage's own failure mode, committed by the code written to prevent it.
            if [ -e "$keep" ]; then keep="$target.REFUSED-RESTORE.$$.bak"; fi
            mv -f "$target.bak" "$keep"
            rm -f "$pycdir/$base".*.pyc          # never leave mutant bytecode behind either
            refused=1
            {
                echo ""
                echo "REFUSED-RESTORE!!  $label"
                echo "  Something wrote to $target while this run held it snapshotted."
                echo "  expected sha256 (the bytes this run last wrote): $expected_sha"
                echo "  or              (its snapshot, before mutating): $pristine_sha"
                echo "  found    sha256 (the bytes on disk right now)  : ${found:-<file is gone>}"
                echo "  Restoring would silently revert that edit and leave 'git status' clean, so"
                echo "  this run has NOT restored. Their edit is untouched; the pristine copy is at:"
                echo "    $keep"
                echo "  REMEDY: diff those two, keep what you want, then delete the .REFUSED-RESTORE.bak."
            } >&2
        fi
    fi

    release_lock
    if [ "$refused" = 1 ]; then exit 4; fi
}

# A SIGNAL MUST END THE RUN (0.0.17 stage 18c, second defect). `restore` was trapped on INT and
# TERM directly, so it restored and then RETURNED — back into the `wait` for the test-run command
# substitution, which does not end just because the shell was signalled.
#
# `kill(2)` on a process GROUP reaches the processes that are in it AT THAT MOMENT. Under load the
# gap between the mutation landing and the test pipeline being forked is wide enough that the
# signal arrives FIRST, so the `unittest` and `grep` children are born after it and never receive
# it. A `ps` on the group 20s after a SIGTERM showed all four processes alive and sleeping: the
# outer shell, the substitution subshell, and both of its children — the outer shell had honoured
# the signal, restored correctly, and then settled down to wait out a 120s inner test run.
#
# The tree was right and the run was a zombie, which is why this presented as a TIMEOUT rather
# than as corruption, and why widening the timeout would have hidden it rather than fixed it. It
# also released the lock on the way through, so a second run could snapshot while this one's tests
# were still executing — the interleave the interlock exists to prevent.
# No `restore` call here ON PURPOSE: `exit` runs the EXIT trap, which IS `restore`. A belt-and-
# braces call was written first and a mutant proved it could not matter — deleting it left every
# test green, because the restore had already happened by the other route. Kept deleted rather
# than pinned: a second call that cannot change an outcome is a second path to reason about, and
# a refusal's `exit 4` overrides this status from inside the EXIT trap either way.
on_signal() {
    exit $((128 + $1))     # the conventional shell status for "died on signal N"
}
trap restore EXIT
trap 'on_signal 2'  INT
trap 'on_signal 15' TERM

if ! acquire_lock; then
    {
        echo ""
        echo "LOCK-BUSY!!  $label"
        echo "  Another mutate.sh run is live and holds the mutation lock:"
        echo "    $(lock_holder_line)"
        echo "  Two runs interleaving is exactly how one run's snapshot gets restored over the"
        echo "  other's work. This run has NOT touched $target and has taken no snapshot."
        echo "  REMEDY: wait for that run to report. If it is a corpse, kill it and rm $LOCKFILE."
    } >&2
    exit 5
fi

cp "$target" "$target.bak"
# Hash the SNAPSHOT, not the target: whatever else happens, the .bak is by definition the bytes
# this run holds, so a write that lands between the cp and here is caught rather than adopted.
pristine_sha="$(sha256_of "$target.bak")"
expected_sha="$pristine_sha"

# The mutator, run twice: once to PLAN (hash in memory, write nothing) and once to APPLY. Both
# validate the pattern, so a broken mutation is refused at the plan step, before the target has
# been touched at all.
#
# HOLE 6: the hash is taken FROM MEMORY, never by re-reading the file. Re-reading would open a
# window in which a third party's write could be recorded as this run's own — and then faithfully
# restored over.
mutation_step() {   # mode target old new [source_sha_the_plan_saw]
    "$PYTHON" - "$@" <<'PYEOF'
import hashlib
import sys

mode, path, old, new = sys.argv[1:5]
source = open(path, encoding="utf-8").read()
found = source.count(old)
if found != 1:
    # Not "> 0": a pattern matching twice mutates BOTH sites, and the verdict would then belong
    # to a mutant with two edits in it. Exactly one, or it is not the mutation that was described.
    print(f"MUTATION-BROKEN: pattern occurs {found} times, expected exactly 1", file=sys.stderr)
    sys.exit(3)
mutated = source.replace(old, new)
if mode == "plan":
    print(hashlib.sha256(mutated.encode("utf-8")).hexdigest())
    sys.exit(0)
# The plan and the apply are two reads of the same file, so the apply must prove it is mutating
# what was planned. If the source moved in between, the planned hash describes bytes that no
# longer exist and writing would put the target into a state this run never recorded.
if hashlib.sha256(source.encode("utf-8")).hexdigest() != sys.argv[5]:
    print("MUTATION-BROKEN: the source changed between the plan and the apply", file=sys.stderr)
    sys.exit(3)
open(path, "w", encoding="utf-8").write(mutated)
PYEOF
}

# HOLE 4: `if ! ...` suppresses errexit for these, so a mutation that cannot be applied is
# REPORTED rather than silently fatal to the whole batch.
if ! mutant_sha="$(mutation_step plan "$target" "$old" "$new")"; then
    echo "BROKEN!!  $label   <-- MUTATION COULD NOT BE APPLIED (not a verdict)"
    exit 3
fi

# HOLE 7: THE GREEN BASELINE. The selected tests, once, with the target PRISTINE — see the header
# for why this runs here and not in the batch driver. It is placed AFTER the plan so a mutation
# that cannot be applied still costs nothing and still exits 3, and BEFORE the apply so it grades
# a tree carrying the lock and the snapshot but no mutation.
rm -f "$pycdir/$base".*.pyc          # HOLE 1 applies to the baseline too: nothing stale may be READ

set +e
baseline_out=$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover \
        -s "$TESTS_DIR" -t "$TESTS_DIR" -p "$pattern" 2>&1 \
      | grep -E "^(Ran|OK|FAILED)")   # HOLE 2: nothing may be written either
set -e

baseline_summary="$(echo "$baseline_out" | tr '\n' ' ')"

# POSITIVE EVIDENCE ONLY, and the conjunction is load-bearing. `Ran` alone would let a discovery
# failure through as "already failing"; `FAILED` alone would fire on any line that happened to
# carry the word. Both together is the one shape that means "tests executed, and they failed" —
# every other shape is the absence of evidence and belongs to exit 6, which classifies it after
# the mutation has been applied exactly as it always did.
if echo "$baseline_out" | grep -qE "^Ran " && echo "$baseline_out" | grep -q FAILED; then
    echo "BASELINE!!  $label   ($baseline_summary)  <-- THE TESTS WERE ALREADY FAILING (not a verdict)"
    {
        echo ""
        echo "BASELINE-NOT-GREEN!!  $label"
        echo "  The selected tests were run with $target PRISTINE and they FAILED:"
        echo "    pattern: $pattern   (in $TESTS_DIR)"
        echo "    result : $baseline_summary"
        echo "  NOTHING WAS MUTATED and nothing was graded. A mutant run against this tree would"
        echo "  have found the same failure, been scored RED, and credited the kill to a mutation"
        echo "  that had nothing to do with it — and a whole batch of those is a perfect score."
        echo "  REMEDY: make the selected tests pass on an unmutated tree, then re-run the batch."
    } >&2
    exit 7
fi

# ⚠ THIS LINE MUST PRECEDE THE APPLY, and stage 18c exists because it did not. Recording the
# mutant hash while the target is still PRISTINE is what leaves no instant in which the bytes on
# disk are a state the restore does not recognise. Both hashes are now known; the target holds
# one of them from here until the trap runs.
expected_sha="$mutant_sha"

if ! mutation_step apply "$target" "$old" "$new" "$pristine_sha"; then
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

# HOLE 6: a verdict produced while somebody else was writing the target IS NOT A VERDICT. The
# tests just ran against a tree that was moving underneath them, so whatever they said belongs to
# a mutant nobody designed — a GREEN here is the FALSE GREEN of hole 1 with a different cause.
# The trap refuses the restore a moment later either way; this stops the bad verdict being printed
# to stdout first, where a batch log or a `_run()` that only captures stdout would read it as one.
now_sha=""
if [ -f "$target" ]; then now_sha="$(sha256_of "$target")"; fi

if [ "$now_sha" != "$expected_sha" ]; then
    echo "BROKEN!!  $label   ($summary)  <-- TARGET CHANGED UNDER THE RUN (not a verdict)"
    # 6 rather than falling off the end at 0. The EXIT trap is about to refuse the restore and
    # raise this to 4, which is the status a caller wants — but only if the trap's re-hash still
    # sees the foreign bytes. If the target raced back to what this run wrote, the trap restores
    # and the status stands, and it must still not be 0: no verdict was produced either way.
    exit 6
elif ! echo "$out" | grep -qE "^Ran "; then
    # HOLE 5 (b) — the test run never reported AT ALL. Hole 5 greps for "Ran 0 tests", which is
    # what unittest prints when it ran and found nothing; it cannot see what unittest prints when
    # it never ran (an unimportable start directory, say). That fell through to the else-branch
    # and was announced as `GREEN ... <-- SURVIVOR` — a false GREEN off zero executed tests, and
    # GREEN is the verdict that sends somebody to go and weaken a real guard.
    echo "BROKEN!!  $label   ($summary)  <-- THE TEST RUN NEVER REPORTED (not a verdict)"
    exit 6
elif echo "$out" | grep -qE "^Ran 0 tests"; then
    # HOLE 5 (a)
    echo "BROKEN!!  $label   ($summary)  <-- PATTERN MATCHED NO TESTS (not a verdict)"
    exit 6
elif echo "$out" | grep -q FAILED; then
    echo "RED   ✓  $label   ($summary)"
else
    echo "GREEN ✗  $label   ($summary)  <-- SURVIVOR"
fi
