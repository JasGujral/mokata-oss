#!/usr/bin/env bash
# 0.0.22 stage A1 `release-sh-converges`, graded. Through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_a1_release_sh_converges_mutants.sh < /dev/null
#
# EVERY NEW REFUSAL THIS STAGE ADDED IS NEUTERED HERE IN TURN, plus the derivations they rest on:
#
#   C01-C09 ★★★ `CLOSED-BRANCH-GONE-REMEDY-IS-FALSE` — the close reason inferred again, the
#     same-instant deletion missed, a reopen not resetting it, an unreadable reason folded into a
#     decline, a re-pushed branch collapsed into GONE, a failed events read believed, the
#     unreadable arm not refusing, the NEW-PR offer losing its confirm, and the false sentence back.
#   E01-E11 ★★★ `RELEASE-SH-HAS-NO-RESUME` — the sha comparison dropped, an unreadable record
#     believed, a record that is never asked for trusted, the preflight skipped outright, a record
#     written by a run that never ran the preflight, master's confirm asked again, the tag skip
#     broken both ways, the tail skipped on re-entry, the branch push skipped regardless, and
#     "could not ask" read as "absent".
#   S01-S08 ★★★ `SYNC-DIVERGENCE-REFUSAL-OFFERS-NO-FAST-FORWARD` — the ancestry check disabled or
#     pointed at the stale fetched ref, the confirm bypassed, an unreadable live head waved
#     through, red and unknown CI read as green, a force on the fast-forward, the PR-closing delete
#     remedy back.
#   B01-B11 ★★★ `BRANCH-PROTECTION-DEGRADED-PASS` (b) — each of the four cache refusals neutered,
#     the age limit unenforced, the four exits collapsed into one, a cache write on a failed read,
#     none on a successful one, a 404 not clearing it, a future timestamp and a foreign record
#     trusted.
#
# ⚠ THE LIMIT, STATED BEFORE THE SCORE (§7i). This grades the decision logic against a fake `gh`
# and local bare remotes. It cannot grade what GitHub returns: the order of `head_ref_deleted` and
# `closed`, which PR `gh pr view <branch>` picks when two exist, and the statuses read-back are
# facts about the live API, owed to the first real cut.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

SH=scripts/release.sh
SY=scripts/sync-public.sh
BP=src/mokata/branch_protection.py
T='test_a1_release_sh_converges.py'
TC='test_a1_branch_protection_cache.py'

TOTAL=39

# ⚠ WHY THIS BATCH RUNS IN WINDOWS, AND WHY A WINDOW IS NOT A VERDICT. Each mutant re-runs a whole
# subject module, and one call of the harness this is driven from is capped. So FROM/TO run an
# index window, windows accumulate into a LEDGER keyed by a FINGERPRINT of every file this batch
# mutates, and ONLY the ledger reports a PASS: all $TOTAL indices present and RED (§7e).
#
#   MUTANT_LEDGER=$HOME/a1.ledger FROM=1  TO=20 tests/_a1_release_sh_converges_mutants.sh
#   MUTANT_LEDGER=$HOME/a1.ledger FROM=21 TO=39 tests/_a1_release_sh_converges_mutants.sh

FROM="${FROM:-1}"
TO="${TO:-$TOTAL}"
# MUTANT-DRIVER-WINDOW-FAILS-OPEN-ON-A-NON-INTEGER (0.0.22 A2). Driven from zsh, `set -- $w` did not
# word-split, so FROM='1 10' and TO='' — `[ -lt ]` errored, every index fell INSIDE the window, and the
# whole batch was graded under a window label. A window that cannot be read is refused, never widened.
case "$FROM" in ''|*[!0-9]*) _bad_window=1 ;; esac
case "$TO" in ''|*[!0-9]*) _bad_window=1 ;; esac
if [ -n "${_bad_window:-}" ] || [ "$FROM" -lt 1 ] || [ "$FROM" -gt "$TO" ]; then
    printf 'WINDOW REFUSED — FROM=%s TO=%s is not a window of 1..%s (two integers, 1 <= FROM <= TO).\n' \
        "'$FROM'" "'$TO'" "$TOTAL" >&2
    printf '  Nothing was graded. A malformed window would otherwise grade the WHOLE batch.\n' >&2
    exit 3
fi
LEDGER="${MUTANT_LEDGER:-}"

_sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
         else shasum -a 256 | cut -d' ' -f1; fi; }
FINGERPRINT="$(cat "$SH" "$SY" "$BP" "tests/$T" "tests/$TC" "$0" | _sha)"

if [ -n "$LEDGER" ]; then
    if [ ! -f "$LEDGER" ] || [ "$(head -1 "$LEDGER" 2>/dev/null)" != "fingerprint $FINGERPRINT" ]; then
        printf 'LEDGER RESET — the sources changed since the last window (or there was none).\n'
        printf 'fingerprint %s\n' "$FINGERPRINT" > "$LEDGER"
    fi
fi

# `seen` is the INDEX reached and `ran` is a COUNT of the mutants this window actually graded.
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

_record() {
    [ -n "$LEDGER" ] || return 0
    local tmp="$LEDGER.$$"
    grep -v "^$1	" "$LEDGER" > "$tmp" 2>/dev/null || true
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"
    { head -1 "$tmp"; tail -n +2 "$tmp" | sort -n; } > "$LEDGER"
    rm -f "$tmp"
}

# ==== C · the close reason is READ (CLOSED-BRANCH-GONE-REMEDY-IS-FALSE) ===================
mutant "C01 ★★★ the close reason is INFERRED again — a deletion close reads as a person's" "$SH" \
  '        if [ "$deleted" = "yes" ]; then reason="BRANCH-DELETED"; else reason="BY-ACTOR"; fi ;;' \
  '        reason="BY-ACTOR" ;;' "$T"
mutant "C02 ★★ a deletion stamped at the SAME instant but listed after the close is missed" "$SH" \
  '        if [ -n "$closed_at" ] && [ "$at" = "$closed_at" ]; then reason="BRANCH-DELETED"; fi ;;' \
  '        : ;;' "$T"
mutant "C03 ★★ a reopen no longer resets an earlier deletion" "$SH" \
  '      reopened|head_ref_restored)
        deleted="no" ;;' \
  '      reopened|head_ref_restored)
        : ;;' "$T"
mutant "C04 ★★★ an UNREADABLE reason is folded into DECLINED — the §7g collapse" "$SH" \
  '                *)              echo "CLOSED-REASON-UNREADABLE" ;;' \
  '                *)              echo "CLOSED-DECLINED" ;;' "$T"
mutant "C05 ★★★ a RE-PUSHED branch collapses into GONE — no new PR is ever offered" "$SH" \
  '                BRANCH-DELETED) if [ "$4" = "yes" ]; then echo "CLOSED-BRANCH-REPUSHED"' \
  '                BRANCH-DELETED) if false; then echo "CLOSED-BRANCH-REPUSHED"' "$T"
mutant "C06 ★★★ a FAILED events read is believed — rc is ignored" "$SH" \
  '  if [ "$1" != "0" ]; then echo "UNREADABLE"; return 0; fi' \
  '  :' "$T"
mutant "C07 ★★★ the unreadable-reason arm stops REFUSING" "$SH" \
  '      echo "      gh api repos/${PUB_REPO}/issues/${PR_NUMBER:-<number>}/events" >&2
      exit 1 ;;' \
  '      echo "      gh api repos/${PUB_REPO}/issues/${PR_NUMBER:-<number>}/events" >&2
      ;;' "$T"
mutant "C08 ★★★ the NEW-PR offer loses its confirm — a PR opened without a yes" "$SH" \
  '    confirm "$1" \
      || { echo "PR not opened (branch pushed, nothing merged/tagged)."; exit 0; }' \
  '    :' "$T"
mutant "C09 ★★ the FALSE remedy sentence comes back" "$SH" \
  '      echo "  step 3 and then offers a NEW pull request for it." >&2' \
  '      echo "  Re-push the branch, which re-opens the PR." >&2' "$T"

# ==== E · re-entry is DERIVED from the remotes (RELEASE-SH-HAS-NO-RESUME) =================
mutant "E01 ★★★ a success for ANOTHER sha proves HEAD — the explicit comparison dropped" "$SH" \
  '  elif [ -z "$4" ] || [ "$3" != "preflight passed at $4" ]; then echo "OTHER-SHA"' \
  '  elif false; then echo "OTHER-SHA"' "$T"
mutant "E02 ★★★ an UNREADABLE record is believed" "$SH" \
  '  if [ "$1" != "0" ]; then echo "UNREADABLE"
  elif [ -z "$2" ]; then echo "ABSENT"' \
  '  if false; then echo "UNREADABLE"
  elif [ -z "$2" ]; then echo "ABSENT"' "$T"
mutant "E03 ★★★ master at origin is taken AS the proof — the record is never asked for" "$SH" \
  '  PREFLIGHT_PROOF="$(read_preflight_record "$HEAD_SHA")"' \
  '  PREFLIGHT_PROOF="PROVEN"' "$T"
mutant "E04 ★★★ the preflight is skipped outright — a fresh run proves nothing and skips anyway" "$SH" \
  'PREFLIGHT_PROOF="NOT-ASKED"' \
  'PREFLIGHT_PROOF="PROVEN"' "$T"
mutant "E05 ★★★ a run that never ran the preflight records that it passed" "$SH" \
  'if [ "$PREFLIGHT_RAN" = "1" ]; then
  record_preflight "$HEAD_SHA"' \
  'if true; then
  record_preflight "$HEAD_SHA"' "$T"
mutant "E06 ★★★ the confirm the 0.0.21 cut died on is asked again on re-entry" "$SH" \
  'if [ "$MASTER_STATE" = "AT-HEAD" ]; then
  say "master is ALREADY at origin' \
  'if false; then
  say "master is ALREADY at origin' "$T"
mutant "E07 ★★ the tag skip never fires — a finished release still asks" "$SH" \
  'if [ "$(tag_state "$(tag_commit "." "$TAG")" "$DEV_TAG_AT")" = "AT-THIS-COMMIT" ] \' \
  'if false \' "$T"
mutant "E08 ★★★ the tag skip fires with the MIRROR'S remote tag missing" "$SH" \
  '   && [ "$(tag_state "$(remote_tag_commit "$PUB_CHECKOUT" origin "$TAG")" "$MIR_TAG_AT")" = "AT-THIS-COMMIT" ]; then' \
  '   && true; then' "$T"
mutant "E09 ★★★ the tail is skipped on re-entry — wait_for_release only on a fresh run" "$SH" \
  'wait_for_release "$PUB_REPO" "$TAG"' \
  'if [ "$PREFLIGHT_RAN" = "1" ]; then wait_for_release "$PUB_REPO" "$TAG"; fi' "$T"
mutant "E10 ★★ the mirror branch push is skipped whatever the remote says" "$SH" \
  '  if [ "$(read_remote_head "$PUB_CHECKOUT" origin "refs/heads/${BRANCH}" "$PUB_BRANCH_SHA")" = "AT-HEAD" ]; then' \
  '  if true; then' "$T"
mutant "E11 ★★ 'could not ask' reads as 'absent' — ls-remote 128 shares ABSENT's answer" "$SH" \
  '    *) echo "UNREADABLE" ;;' \
  '    *) echo "ABSENT" ;;' "$T"

# ==== S · the sync fast-forwards behind a yes (SYNC-DIVERGENCE-REFUSAL-OFFERS-NO-FAST-FORWARD) ====
mutant "S01 ★★★ the ANCESTRY check is gone — a moved branch is fast-forwarded over" "$SY" \
  '  if ! git merge-base --is-ancestor "$LIVE_HEAD" "$BUILT_ON" 2>/dev/null; then' \
  '  if false; then' "$T"
mutant "S02 ★★★ the ancestry is asked of the STALE fetched ref, not the live remote" "$SY" \
  '  LIVE_HEAD="$(printf '"'"'%s\n'"'"' "$_LIVE" | awk -v r="refs/heads/${BRANCH}" '"'"'$2 == r {print $1; exit}'"'"')"' \
  '  LIVE_HEAD="$BUILT_ON"' "$T"
mutant "S03 ★★★ the fast-forward needs no YES" "$SY" \
  '  if ! confirm "Commit '"'"'Release ${VER}'"'"' on ${BRANCH} and fast-forward origin (no force)?"; then' \
  '  if false; then' "$T"
mutant "S04 ★★ an UNREADABLE live head is waved through to the ancestry test" "$SY" \
  '  if [ "$_LIVE_RC" != "0" ] || [ -z "$LIVE_HEAD" ]; then' \
  '  if false; then' "$T"
mutant "S05 ★★★ a RED run reads as green — the false sentence, restored" "$SY" \
  '      failure|timed_out|startup_failure) echo "that CI graded RED ($3)" ;;' \
  '      failure|timed_out|startup_failure) echo "that CI graded GREEN" ;;' "$T"
mutant "S06 ★★★ UNKNOWN reads as green — gh unreachable is reported as a pass" "$SY" \
  '  if [ "$1" != "0" ]; then echo "whose CI conclusion is UNKNOWN (gh could not be asked)"' \
  '  if [ "$1" != "0" ]; then echo "that CI graded GREEN"' "$T"
mutant "S07 ★★★ the fast-forward push carries a FORCE" "$SY" \
  '  if ! git push origin "${BRANCH}"; then' \
  '  if ! git push --force origin "${BRANCH}"; then' "$T"
mutant "S08 ★★ the PR-closing delete remedy comes back" "$SY" \
  '    echo "    * the change is a new release   -> bump the version in pyproject.toml and re-run." >&2' \
  '    echo "    * the change is a new release   -> bump the version in pyproject.toml and re-run." >&2
    echo "    * or delete it: (cd ${DEST} && git push origin --delete ${BRANCH})" >&2' "$T"

# ==== B · the last-read protection cache (BRANCH-PROTECTION-DEGRADED-PASS) ================
mutant "B01 ★★★ a cache MISS passes — the boolean-only pass, restored" "$BP" \
  '    if hit.outcome == CACHE_MISS:' \
  '    if False:' "$TC"
mutant "B02 ★★★ an UNUSABLE cache passes" "$BP" \
  '    if hit.outcome == CACHE_UNUSABLE:' \
  '    if False:' "$TC"
mutant "B03 ★★★ a TOO-OLD document passes" "$BP" \
  '    if hit.outcome == CACHE_HIT_TOO_OLD:' \
  '    if False:' "$TC"
mutant "B04 ★★★ a document that DISAGREES passes" "$BP" \
  '    if hit.outcome == CACHE_HIT_DISAGREES:' \
  '    if False:' "$TC"
mutant "B05 ★★★ the age limit is never enforced" "$BP" \
  '        if hours > PROTECTION_CACHE_MAX_AGE_HOURS:' \
  '        if False:' "$TC"
mutant "B06 ★★ the four refusals share ONE exit — release.sh can no longer tell them apart" "$BP" \
  '        if not self.ok and self.cache_outcome in _CACHE_REFUSAL_EXIT:' \
  '        if False:' "$TC"
mutant "B07 ★★★ a FAILED read writes the cache" "$BP" \
  '    return _corroborate(run, repo, branch, error, cache=cache, now=clock)' \
  '    if cache is not None:
        cache.store(repo, branch, {}, clock())
    return _corroborate(run, repo, branch, error, cache=cache, now=clock)' "$TC"
mutant "B08 ★★★ a SUCCESSFUL read writes nothing — the cache can never fill" "$BP" \
  '        note = cache.store(repo, branch, payload, clock()) if cache is not None else ""' \
  '        note = ""' "$TC"
mutant "B09 ★★ a read saying NO protection leaves the old safe document behind" "$BP" \
  '        note = cache.forget(repo, branch) if cache is not None else ""' \
  '        note = ""' "$TC"
mutant "B10 ★★ a read time in the FUTURE is trusted" "$BP" \
  '        if age.total_seconds() < 0:' \
  '        if False:' "$TC"
mutant "B11 ★★ a record for ANOTHER repo is trusted" "$BP" \
  '        if record.get("repo") != repo or record.get("branch") != branch:' \
  '        if False:' "$TC"

printf '\n================================================================================\n'
printf 'A1 RELEASE-SH-CONVERGES MUTANTS — window %s..%s of %s: %s RED, %s GREEN\n' \
    "$FROM" "$TO" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS IN THIS WINDOW (each is a refusal whose test does not grade):\n%s' "$survivors"
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
