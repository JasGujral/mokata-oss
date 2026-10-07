#!/usr/bin/env bash
# 0.0.21 stage 01 `retry-reads-the-head`, graded. Through scripts/mutate.sh.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage01_mutants.sh < /dev/null
#
#   V01-V03 ★★★ the VOCABULARY: a name leaves the closed set, the set stops being the ONE
#     declaration both sides read, and the `case` loses its catch-all so an unknown state falls
#     through instead of refusing.
#   S01-S05 ★★★ `pr_state`, the PURE function: the head comparison that tells OPEN-AT-THIS-COMMIT
#     from OPEN-ELSEWHERE, the empty-head guard, the two CLOSED facts collapsing into one, the
#     absent PR reading as something, and the function reaching for the network it must not touch.
#   R01-R02 ★★★ `pr_facts`: TWO reads instead of one, and the head field dropped.
#   C01-C03 ★★★ THE DEFECT THIS STAGE IS NAMED FOR — the CI gate and the merge agreeing on ONE
#     commit: the gate re-deriving the head locally, the re-read after the wait being deleted, and
#     the pre-wait head being captured AFTER that re-read (so the refusal names the wrong commit).
#
# ⚠ THREE MUTANTS IN THE FIRST RUN OF THIS BATCH CAME BACK GREEN, and all three were findings
# about the SUBJECT rather than about the batch: `S02` had no payload with an empty WANT_SHA, so
# deleting the `-n` guard changed nothing any row could see; `R02` dropped `headRefOid` from the
# `--jq` projection and the test was reading the `--json` REQUEST and never the answer; and `C03`
# deleted a guard clause that turned out to be UNREACHABLE. Two tests gained a case, one clause
# was deleted (§7f). *A surviving mutant is a question about the subject, not about the mutant.*
#   M01-M02 ★★★ the refusals: one stops writing to stderr (invisible in a piped release log), and
#     one names a remedy that does not exist.
#
# ⚠ THE SUBJECT IS A SHELL SCRIPT AND THE GRADER IS A PYTHON TEST THAT READS IT. That is the
# shape `test_b2_release_retry` already uses and the reason `_release_retry` exists: `release.sh`
# EXECUTES a release when sourced, so the pure functions are EXTRACTED and run in a sandbox rather
# than sourced. A mutant here changes the text; the test re-reads it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

SH=scripts/release.sh
T='test_a41_the_retry_reads_the_head.py'

TOTAL=15

# ⚠ WHY THIS BATCH RUNS IN WINDOWS, AND WHY A WINDOW IS NOT A VERDICT. Each mutant re-runs the
# whole of the subject module named in $T, and the harness this is driven from caps one call at
# ~180s. So FROM/TO run an index window, windows accumulate into a LEDGER keyed by a FINGERPRINT
# of every file this batch mutates, and ONLY the ledger reports a PASS: all $TOTAL indices present
# and RED. A window that comes back all-RED says nothing about the batch (§7e).
#
#   MUTANT_LEDGER=$HOME/s01.ledger FROM=1  TO=8  tests/_stage01_mutants.sh
#   MUTANT_LEDGER=$HOME/s01.ledger FROM=9  TO=15 tests/_stage01_mutants.sh

FROM="${FROM:-1}"
TO="${TO:-$TOTAL}"
LEDGER="${MUTANT_LEDGER:-}"

_sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
         else shasum -a 256 | cut -d' ' -f1; fi; }
FINGERPRINT="$(cat "$SH" "tests/$T" "$0" | _sha)"

if [ -n "$LEDGER" ]; then
    if [ ! -f "$LEDGER" ] || [ "$(head -1 "$LEDGER" 2>/dev/null)" != "fingerprint $FINGERPRINT" ]; then
        printf 'LEDGER RESET — the sources changed since the last window (or there was none).\n'
        printf 'fingerprint %s\n' "$FINGERPRINT" > "$LEDGER"
    fi
fi

# `seen` is the INDEX reached and `ran` is a COUNT of the mutants this window actually graded.
# They are separate because a window makes them different numbers, and the one the abort line
# needs ("how many never ran") is the count — see `_mutant_driver_contract.RUN_COUNTED`.
seen=0; ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out idx
    idx=$((seen + 1))
    seen="$idx"
    if [ "$idx" -lt "$FROM" ] || [ "$idx" -gt "$TO" ]; then return 0; fi
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$idx" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of this window graded; THIS BATCH DID NOT PASS — it stopped at %s.\n' \
            "$((ran - 1))" "$idx"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)); _record "$idx" RED "$label" ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n'
                _record "$idx" GREEN "$label" ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$idx" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

# One line per graded mutant; a re-run of the same index overwrites, so a GREEN that is fixed and
# re-run RED does not leave both claims in the file.
_record() {
    [ -n "$LEDGER" ] || return 0
    local tmp="$LEDGER.$$"
    grep -v "^$1	" "$LEDGER" > "$tmp" 2>/dev/null || true
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"
    { head -1 "$tmp"; tail -n +2 "$tmp" | sort -n; } > "$LEDGER"
    rm -f "$tmp"
}

# ==== V · the vocabulary is closed and lives in ONE place =======================================

mutant "V01 ★★★ a name leaves the closed set — the state it describes becomes unspellable" "$SH" \
  '                CLOSED-DECLINED CLOSED-BRANCH-GONE UNKNOWN' \
  '                CLOSED-DECLINED UNKNOWN' "$T"

mutant "V02 ★★★ the two OPEN names collapse — the whole point of the stage, undone" "$SH" \
  '    OPEN)     if [ -n "$2" ] && [ "$2" = "$3" ]; then echo "OPEN-AT-THIS-COMMIT"
              else echo "OPEN-ELSEWHERE"; fi ;;' \
  '    OPEN)     echo "OPEN-AT-THIS-COMMIT" ;;' "$T"

mutant "V03 ★★★ the catch-all goes — an unknown gh state falls THROUGH instead of refusing" "$SH" \
  '    *)        echo "UNKNOWN" ;;
  esac
}' \
  '  esac
}' "$T"

# ==== S · `pr_state`, the pure function ========================================================

mutant "S01 ★★★ the HEAD comparison inverts — a PR at another commit reads as at THIS one" "$SH" \
  '    OPEN)     if [ -n "$2" ] && [ "$2" = "$3" ]; then echo "OPEN-AT-THIS-COMMIT"' \
  '    OPEN)     if [ -n "$2" ] && [ "$2" != "$3" ]; then echo "OPEN-AT-THIS-COMMIT"' "$T"

mutant "S02 ★★★ the EMPTY-head guard goes — an unreadable head compares equal to an empty want" "$SH" \
  '    OPEN)     if [ -n "$2" ] && [ "$2" = "$3" ]; then echo "OPEN-AT-THIS-COMMIT"' \
  '    OPEN)     if [ "$2" = "$3" ]; then echo "OPEN-AT-THIS-COMMIT"' "$T"

mutant "S03 ★★★ the two CLOSED facts collapse — a deleted branch reads as a declined release" "$SH" \
  '    CLOSED)   if [ "$4" = "yes" ]; then echo "CLOSED-DECLINED"
              else echo "CLOSED-BRANCH-GONE"; fi ;;' \
  '    CLOSED)   echo "CLOSED-DECLINED" ;;' "$T"

mutant "S04 ★★★ an ABSENT PR stops being its own answer — NONE reads as UNKNOWN" "$SH" \
  '    "")       echo "NONE" ;;' \
  '    "none")   echo "NONE" ;;' "$T"

mutant "S05 ★★★ the pure function reaches the NETWORK — it can no longer be graded synthetically" "$SH" \
  '    MERGED)   echo "MERGED" ;;' \
  '    MERGED)   gh --version >/dev/null 2>&1; echo "MERGED" ;;' "$T"

# ==== R · `pr_facts` — ONE read for BOTH fields =================================================

mutant "R01 ★★★ the state and the head come from TWO reads — two reads, two PRs" "$SH" \
  '  ( cd "$1" && gh pr view "$2" --json state,headRefOid \' \
  '  ( cd "$1" && gh pr view "$2" --json headRefOid >/dev/null 2>&1; gh pr view "$2" --json state,headRefOid \' "$T"

mutant "R02 ★★★ the HEAD field is dropped — the state is read and the commit is guessed again" "$SH" \
  '      --jq '"'"'[.state, .headRefOid] | @tsv'"'"' 2>/dev/null ) || true' \
  '      --jq '"'"'[.state] | @tsv'"'"' 2>/dev/null ) || true' "$T"

# ==== C · THE DEFECT THIS STAGE IS NAMED FOR ===================================================
#
# ⛔ The CI gate and the merge must agree on ONE commit. The old code gated on the LOCAL branch
# and merged what the PR pointed at, so a PR carrying a different commit got a green from a commit
# it does not contain — and `--admin` published it.

mutant "C01 ★★★ the CI gate re-derives the head LOCALLY — two derivations of one fact, again" "$SH" \
  '  CI_SHA="${PR_HEAD:-$(cd "$PUB_CHECKOUT" && git rev-parse "$BRANCH")}"' \
  '  CI_SHA="$(cd "$PUB_CHECKOUT" && git rev-parse "$BRANCH")"' "$T"

mutant "C02 ★★★ the re-read AFTER the CI wait is deleted — the minutes-long window reopens" "$SH" \
  '    PRE_MERGE_HEAD="$PR_HEAD"
    read_pr' \
  '    PRE_MERGE_HEAD="$PR_HEAD"' "$T"

# ⛔ C03's FIRST VERSION SURVIVED, AND THE SURVIVOR WAS THE FINDING. It deleted
# `|| [ "$PR_HEAD" != "$PRE_MERGE_HEAD" ]` from the mid-flight guard and nothing reddened —
# because that clause was UNREACHABLE (§7f): every path into the block leaves PR_STATE as
# OPEN-AT-THIS-COMMIT, so PRE_MERGE_HEAD IS WANT_SHA, and `pr_state` answers OPEN-AT-THIS-COMMIT
# only when the head equals WANT_SHA. The clause is DELETED and C03 now grades the property that
# really is load-bearing: the CAPTURE ORDER, which decides whether the refusal names the commit
# the green was earned on or the one that just arrived.
mutant "C03 ★★★ PRE_MERGE_HEAD is captured AFTER the re-read — the refusal names the wrong commit" "$SH" \
  '    PRE_MERGE_HEAD="$PR_HEAD"
    read_pr' \
  '    read_pr
    PRE_MERGE_HEAD="$PR_HEAD"' "$T"

# ==== M · the refusals have to be READABLE and ACTIONABLE ======================================

mutant "M01 ★★★ a refusal goes to STDOUT — invisible in the piped log the release is read from" "$SH" \
  '      echo "REFUSING: the release PR for ${BRANCH} is OPEN but its head is ${PR_HEAD:-<unreadable>}," >&2' \
  '      echo "REFUSING: the release PR for ${BRANCH} is OPEN but its head is ${PR_HEAD:-<unreadable>},"' "$T"

mutant "M02 ★★★ a refusal names a remedy that does not EXIST — it sends a person looking" "$SH" \
  '      echo "      (cd ${PUB_CHECKOUT} && git push -u origin ${BRANCH})" >&2' \
  '      echo "      (cd ${PUB_CHECKOUT} && git unclose-pr ${BRANCH})" >&2' "$T"

printf '\n================================================================================\n'
printf 'STAGE 01 RETRY-READS-THE-HEAD MUTANTS — window %s..%s of %s: %s RED, %s GREEN\n' \
    "$FROM" "$TO" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS IN THIS WINDOW (each is a row whose fix does not grade):\n%s' "$survivors"
fi

if [ -z "$LEDGER" ]; then
    if [ "$FROM" -eq 1 ] && [ "$TO" -ge "$TOTAL" ] && [ -z "$survivors" ]; then
        printf 'ALL %s MUTANTS RED IN ONE RUN.\n' "$TOTAL"
        printf '================================================================================\n'
        exit 0
    fi
    printf 'NO LEDGER AND NOT A WHOLE-BATCH RUN — THIS IS NOT A BATCH VERDICT.\n'
    printf '================================================================================\n'
    exit 70
fi

reds="$(tail -n +2 "$LEDGER" | awk -F'\t' '$2 == "RED" {print $1}' | sort -n -u | wc -l | tr -d ' ')"
greens="$(tail -n +2 "$LEDGER" | awk -F'\t' '$2 == "GREEN" {print $1}' | wc -l | tr -d ' ')"
printf 'LEDGER %s: %s of %s indices RED, %s GREEN\n' "$LEDGER" "$reds" "$TOTAL" "$greens"
if [ "$reds" -eq "$TOTAL" ] && [ "$greens" -eq 0 ]; then
    printf 'BATCH PASSED — every one of the %s mutants is RED against this exact source.\n' "$TOTAL"
    printf '================================================================================\n'
    exit 0
fi
printf 'BATCH INCOMPLETE — run the remaining windows. Missing indices:\n'
i=1
while [ "$i" -le "$TOTAL" ]; do
    grep -q "^$i	RED	" "$LEDGER" || printf '  %s\n' "$i"
    i=$((i + 1))
done
if [ "$greens" -ne 0 ]; then
    printf 'AND THERE ARE SURVIVORS IN THE LEDGER:\n'
    tail -n +2 "$LEDGER" | awk -F'\t' '$2 == "GREEN" {print "  - " $3}'
fi
printf '================================================================================\n'
exit 70
