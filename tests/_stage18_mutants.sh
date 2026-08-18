#!/usr/bin/env bash
# Stage 18 (lane F, "attention") mutant batch — driven through scripts/mutate.sh, the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage18_mutants.sh
#
# THE CONTRACT BELOW IS NOT RE-DERIVED HERE. Every line from `set -uo pipefail` down to the
# summary is `tests/_run_mutants.sh`'s, transplanted verbatim, because that driver is the one
# `tests/_mutant_driver_contract.py` grades and a hand-written copy is exactly the
# `MUTANT-DRIVER-CONTRACT-DUPLICATED` shape that row already names. What is this stage's is
# the TARGETS, the TOTAL, and the mutant list.
#
# ---------------------------------------------------------------------------------------------
# WHY THIS FILE INSPECTS AN EXIT STATUS, AND WHY A HARNESS FAILURE STOPS THE BATCH DEAD
# (0.0.17 stage 18b — RUN-MUTANTS-IGNORES-EXIT-CODES)
#
# Until stage 18b this file ran `set -uo pipefail` — no `-e` — and invoked the mutator 26 times
# without ever looking at a status. Measured against a stub: for EVERY failure mode mutate.sh
# has (3, 4, 5, 6) the batch ran all 26 mutants and exited 0. Every guard in mutate.sh's header
# was, from this file's point of view, a comment.
#
# ON AN EXIT 4 THAT IS ACTIVELY DANGEROUS, not merely quiet. `mutate.sh` refuses to restore its
# snapshot precisely so that a third party's edit survives — which means it LEAVES THE TARGET
# MUTATED, on purpose. Carrying on past that grades every remaining mutant against a tree holding
# an uncontrolled edit: a compound mutant nobody designed, reported with a straight face. That is
# hole 3 in mutate.sh's header, arriving one layer up where hole 3's trap cannot see it.
#
# SO THE RULE HERE IS THE MIRROR OF THE MUTATOR'S CONTRACT:
#
#     a VERDICT (RED / GREEN) is a completed run  ->  count it, carry on.
#     a HARNESS FAILURE (non-zero)                ->  STOP. Nothing after this is trustworthy.
#
# A SURVIVOR IS NOT A FAILURE, and confusing the two would be a worse defect than the one this
# stage fixed. `GREEN ... <-- SURVIVOR` means the mutation was not caught — a real finding about
# a real pin, and the remaining mutants are still worth grading. It is reported, named again in
# the summary so it cannot be lost in 26 lines of log, and the batch continues.
#
# AND A BATCH THAT STOPS EARLY MUST NEVER READ AS A BATCH THAT PASSED. The abort names the mutant
# it died on and how many of the list never ran, and it prints to STDOUT rather than stderr on
# purpose: a batch log that captures only stdout would otherwise simply end, mid-list, looking
# indistinguishable from a run that covered everything.
#
# `set -e` is deliberately NOT used. Every status is checked explicitly and at exactly one place
# (`mutant`), which is what lets the failure be EXPLAINED rather than merely fatal — the same
# reasoning as hole 4 in mutate.sh's header.
# ---------------------------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# The mutator is overridable for the SAME reason `PYTHON` is: so this driver's own self-tests can
# drive all 26 call sites against a stub returning a chosen exit code in milliseconds, instead of
# running 26 real mutation passes. `mutate.sh`'s tests already use this idiom (`MUTATE_LOCKFILE`,
# `MUTATE_TESTS_DIR`, `MUTATE_LOCK_IMPL`). Real runs get the default.
M="${MUTATE_SH:-scripts/mutate.sh}"
# The three subjects lane F touched, and the pattern that grades them.
N=src/mokata/notify.py
R=src/mokata/progress.py
T='test_stage18*.py'

# The number of `mutant` call sites below. Pinned against the real count by
# `test_mutant_batch_driver.TestTheAccountingCannotDrift`, because "19 of 26 never ran" is only
# true if 26 is true — a batch that overstates its own list is the silent-truncation shape doc 85
# warns about, wearing the costume of an honest abort.
TOTAL=16

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
# THE LEVEL PREDICATE — the whole of "does this kind fire", so the whole of what can silently
# collapse the three configured levels into one.
# ---------------------------------------------------------------------------------------------

mutant "M01 harness-silent ignores the harness and double-notifies" "$N" \
  '        return not harness_notified' \
  '        return True' "$T"

mutant "M02 every level fires on CLI prompts" "$N" \
  '        return level == ALL_PROMPTS' \
  '        return True' "$T"

mutant "M03 the default level is narrowed from the widest" "$N" \
  'DEFAULT_LEVEL = ALL_PROMPTS' \
  'DEFAULT_LEVEL = HARNESS_SILENT' "$T"

mutant "M04 an unrecognised level is taken at face value" "$N" \
  '    return value if value in LEVELS else DEFAULT_LEVEL' \
  '    return value' "$T"

# ---------------------------------------------------------------------------------------------
# PRESENCE — the three silences, each mutated separately, because "it was quiet" is satisfied by
# any one of them and a batch that mutated them together could not tell which was load-bearing.
# ---------------------------------------------------------------------------------------------

mutant "M05 CI no longer silences the notifier" "$N" \
  '    if _in_ci(env):' \
  '    if False:' "$T"

mutant "M06 a CLI prompt announces with no terminal to answer on" "$N" \
  '        return is_tty                       # the terminal IS the answer channel' \
  '        return True                         # the terminal IS the answer channel' "$T"

mutant "M07 the desktop notification is raised over SSH" "$N" \
  '    if argv is not None and not _over_ssh(env):' \
  '    if argv is not None:' "$T"

mutant "M08 a test process is allowed to touch the desktop" "$N" \
  '    if not under_test:' \
  '    if True:' "$T"

# ---------------------------------------------------------------------------------------------
# THE TWO SETTINGS KEYS THAT MUST STAY TWO, and the rate limit that keeps the feature usable.
# ---------------------------------------------------------------------------------------------

mutant "M09 the off switch is ignored" "$N" \
  '    if not settings.enabled:' \
  '    if False:' "$T"

mutant "M10 silencing the audio is ignored — the two keys collapse into one" "$N" \
  '    if settings.audio:' \
  '    if True:' "$T"

mutant "M11 the rate limit is removed — a batch of waits becomes a batch of banners" "$N" \
  '    if debounce and _debounced(root, now=time.time() if now is None else now):' \
  '    if False and _debounced(root, now=time.time() if now is None else now):' "$T"

mutant "M12 the rate limit never lets go — a mute button wearing a limiter" "$N" \
  '        if now - last < NOTIFY_MIN_INTERVAL_SECONDS:' \
  '        if True:' "$T"

# ---------------------------------------------------------------------------------------------
# F10 — the security class. The fixed-body check is the control that makes "nothing variable
# reaches an argv" a check rather than a comment, so it is mutated on its own.
# ---------------------------------------------------------------------------------------------

mutant "M13 any body may be shelled out with" "$N" \
  '    return body in BODIES' \
  '    return True' "$T"

mutant "M14 the unverifiable Windows arm ships" "$N" \
  '    if platform.startswith("win"):' \
  '    if False:' "$T"

# ---------------------------------------------------------------------------------------------
# PART 2 — the statusline. Both halves of the defect: the segment reaching the badge at all, and
# reaching it in the position that survives truncation.
# ---------------------------------------------------------------------------------------------

mutant "M15 the wait is appended again instead of leading" "$R" \
  '    return f"{wait}  {line}" if wait else line' \
  '    return f"{line}  {wait}" if wait else line' "$T"

mutant "M16 the badge re-merges the absent answer with the real one (7g)" "$R" \
  '        return BADGE_UNRESOLVED_ASCII if ascii_only else BADGE_UNRESOLVED' \
  '        return BADGE_NO_RUN' "$T"

if [ "$ran" -ne "$TOTAL" ]; then
    printf '\n'
    printf 'BATCH ABORTED — %s mutants ran but this driver declares TOTAL=%s.\n' "$ran" "$TOTAL"
    printf '  Every "N of %s never ran" line this file can print is wrong by the difference, and\n' "$TOTAL"
    printf '  a batch reporting more coverage than it has is exactly what stage 18b set out to fix.\n'
    exit 70
fi

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
