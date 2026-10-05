#!/usr/bin/env bash
# 0.0.21 stage 09 `h2-token-calibration`, graded. Through scripts/mutate.sh.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage09_h2_token_calibration_mutants.sh < /dev/null
#
#   T01-T08 ★★★ the transcript adapter: the all-text refusal, the `actual > 0` floor, the bool
#     guard, the two halves of "the id validates the offset", complete-lines-only, the
#     false-positive keying `schema_blind` had in my own first draft, and the oversized record
#     that stops being counted.
#   T12-T14 ★★★ the INDEPENDENT REVIEW's three findings, none of which had a mutant: a group
#     judged before anything proved it finished (F1 — 7.48 by way of INTERLEAVED records), the
#     wedge a reason-with-progress caused (F2), and the partial `usage` that read as a moved
#     schema (F3).
#   C01-C05 ★★★ the loop: the publish floor, the margin's inclusive edge, the ledger record's
#     REAL actual (whose absence is the exact defect this stage exists to fix), the cursor, and
#     the three reasons collapsing into one.
#   M01-M02 ★★★ the §7j fix in BOTH directions — each writer of `memory_stats.json` restored to
#     a mutator that types its own schema, so the other one's section is erased.
#   H01-H02 ★★★ the hook: un-wired (§7i, a mechanism nothing runs), and the refusal that stops
#     it creating `.mokata/` in a tree the user never initialized.
#   D01-D03 ★★★ clause (c): the trail cleared again, the trail kept but not MARKED, and the
#     partial flag that stops distinguishing an honest zero from an erased total.
#   R01-R02 ★★★ the reader: un-wired from `diagnose` (the written-and-never-read defect this
#     backlog already carries a row about), and the under-floor branch printing a noisy ratio.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

A=src/mokata/adapters/transcript.py
L=src/mokata/govern/calibration.py
K=src/mokata/govern/tokens.py
O=src/mokata/execmode/orchestrator.py
S=src/mokata/memory/store.py
D=src/mokata/govern/doctor.py
HC=src/mokata/hook_cli.py
T='test_a38_the_token_estimate_finally_meets_a_real_number.py'

TOTAL=28

# ⚠ WHY THIS BATCH CAN BE RUN IN WINDOWS, AND WHY A WINDOW IS NOT A VERDICT.
# Each mutant re-runs the whole of test_a38, and the harness this batch is driven from caps a
# single call at ~180s — not enough for 25 of them. So the batch takes FROM/TO and runs an index
# window. A window that comes back all-RED says nothing about the batch (§7e: an instrument that
# fails open is a false green), so windows accumulate into a LEDGER and only the ledger can
# report a PASS: every one of the 25 indices must be present and RED.
#
#   MUTANT_LEDGER=/tmp/s09.ledger FROM=1  TO=8  tests/_stage09_h2_token_calibration_mutants.sh
#   MUTANT_LEDGER=/tmp/s09.ledger FROM=9  TO=17 tests/_stage09_h2_token_calibration_mutants.sh
#   MUTANT_LEDGER=/tmp/s09.ledger FROM=18 TO=25 tests/_stage09_h2_token_calibration_mutants.sh
#
# The ledger is keyed by a FINGERPRINT of every file this batch mutates plus the test file plus
# this script. Change any of them and the ledger is discarded rather than believed — a verdict
# recorded against source that no longer exists is the stale-green failure, not a saving.
FROM="${FROM:-1}"
TO="${TO:-$TOTAL}"
LEDGER="${MUTANT_LEDGER:-}"

_sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
         else shasum -a 256 | cut -d' ' -f1; fi; }
FINGERPRINT="$(cat "$A" "$L" "$K" "$O" "$S" "$D" "$HC" "tests/$T" "$0" | _sha)"

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

# One line per graded mutant. A re-run of the same index overwrites, so a GREEN that is then
# fixed and re-run RED does not leave both claims in the file.
_record() {
    [ -n "$LEDGER" ] || return 0
    local tmp="$LEDGER.$$"
    grep -v "^$1	" "$LEDGER" > "$tmp" 2>/dev/null || true
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"
    { head -1 "$tmp"; tail -n +2 "$tmp" | sort -n; } > "$LEDGER"
    rm -f "$tmp"
}

# ==== T · the transcript adapter ================================================================

# ⚠ T01's FIRST DRAFT POINTED AT DEAD CODE AND SURVIVED. It mutated the all-text refusal inside
# `_text_of`, which the per-response rework had left reachable from nothing — so the mutation was
# applied, every test passed, and the GREEN was reporting on a function no caller could enter.
# `_text_of` is DELETED (§7d), and T01 now points at the refusal the shipped path runs.
mutant "T01 ★★★ the all-text refusal becomes a skip — a tool_use response counted on its text half" "$A" \
  '    if not blocks or any(t != _TEXT_BLOCK for t, _x in blocks):' \
  '    if not blocks:' "$T"

# T02 is aimed at the ONE positivity guard in `_priced`, and that is the point: the first draft
# of this mutant relaxed a SECOND, redundant `out <= 0` in the same function and SURVIVED (§7f).
mutant "T02 ★★★ actual=0 becomes a sample — a zero count, and a response whose thinking ate it all" "$A" \
  '    return out if out > 0 else None' \
  '    return out' "$T"

mutant "T03 ★★★ the bool guard goes — a flag where a count belongs contributes actual=1" "$A" \
  '    if not isinstance(out, int) or isinstance(out, bool):' \
  '    if not isinstance(out, int):' "$T"

mutant "T04 ★★★ the SESSION ID stops validating the offset — a stale bookmark into another session" "$A" \
  '    if cursor is not None and cursor.session_id == session_id and cursor.offset <= size:' \
  '    if cursor is not None and cursor.offset <= size:' "$T"

mutant "T05 ★★★ an offset PAST the end of a shrunk file is trusted" "$A" \
  '    if cursor is not None and cursor.session_id == session_id and cursor.offset <= size:' \
  '    if cursor is not None and cursor.session_id == session_id:' "$T"

mutant "T06 ★★★ complete-lines-only goes — a partial last record is parsed and then lost" "$A" \
  '    head, newline, _tail = blob.rpartition(b"\n")' \
  '    head, newline, _tail = blob, b"\n", b""' "$T"

mutant "T07 ★★★ schema_blind keyed on \`usable\` again — the false-positive class of my own first draft" "$A" \
  '        return self.reason is None and self.responses > 0 and self.priced == 0' \
  '        return self.reason is None and self.records > 0 and self.usable == 0' "$T"

mutant "T08 ★★★ an oversized record stops being counted, so it hides" "$A" \
  '        if len(raw) > TRANSCRIPT_LINE_CAP_BYTES:
            reading.records += 1           # counted, so an oversized record cannot hide
            continue' \
  '        if len(raw) > TRANSCRIPT_LINE_CAP_BYTES:
            continue' "$T"

# ==== C · the calibration loop ==================================================================

mutant "C01 ★★★ the publish floor goes — a ratio over 10 estimated tokens is printed as a number" "$L" \
  '        if self.estimate_total < CALIBRATION_MIN_ESTIMATE_TOKENS or self.estimate_total <= 0:' \
  '        if self.estimate_total <= 0:' "$T"

mutant "C02 ★★★ the margin fires AT its edge — drift reported on a perfectly healthy 1.00" "$L" \
  '        return r is not None and r > CALIBRATION_MARGIN_RATIO' \
  '        return r is not None and r >= CALIBRATION_MARGIN_RATIO' "$T"

mutant "C03 ★★★ the ledger record loses its REAL actual — the stage's own defect restored" "$L" \
  '                log_calibration(led, f"{CALIBRATION_CONTEXT_PREFIX}{model}",
                                estimate=rec.estimate_total, actual=rec.actual_total)' \
  '                log_calibration(led, f"{CALIBRATION_CONTEXT_PREFIX}{model}",
                                estimate=rec.estimate_total)' "$T"

mutant "C04 ★★★ the cursor is never saved — every hook call re-folds the same window" "$L" \
  '        if reading.consumed:
            save_cursor(root, transcript_path, reading.cursor)' \
  '        if False:
            save_cursor(root, transcript_path, reading.cursor)' "$T"

mutant "C05 ★★★ the three different nothings collapse into one sentence (§7g)" "$L" \
  '                                          reason="no new transcript records since the last read")' \
  '                                          reason="nothing to calibrate")' "$T"

# ==== M · the §7j fix, both writers of one file =================================================

mutant "M01 ★★★ the memory counters type their own schema again — a counted read erases the ratio" "$S" \
  '    base["reads"] = prev_reads + int(reads)
    base["writes"] = prev_writes + int(writes)
    return base' \
  '    base["reads"] = prev_reads + int(reads)
    base["writes"] = prev_writes + int(writes)
    return {"reads": base["reads"], "writes": base["writes"]}' "$T"

mutant "M02 ★★★ and the OTHER writer does it back — the calibration write erases the counters" "$L" \
  '    base: Dict[str, Any] = dict(current) if isinstance(current, dict) else {}' \
  '    base: Dict[str, Any] = {}' "$T"

# ==== H · the hook is the production caller =====================================================

mutant "H01 ★★★ the hook call goes — §7i, a perfect mechanism nothing runs" "$HC" \
  '        _calibrate_from_transcript(root, payload, session_id)' \
  '        pass' "$T"

mutant "H02 ★★★ the refusal goes — .mokata/ created in a tree the user never initialized" "$HC" \
  '        from .config import Surface
        if not Surface.is_initialized(root):
            return' \
  '        from .config import Surface
        if False:
            return' "$T"

# ==== D · clause (c), the degraded trail ========================================================

mutant "D01 ★★★ the trail is CLEARED again — \$0.0000 for a batch that spent 1000 tokens" "$O" \
  '            kept = tracker.mark_degraded()' \
  '            tracker.entries.clear(); kept = 0' "$T"

mutant "D02 ★★★ the trail is kept but NOT MARKED — right totals, and a report that denies the degrade" "$K" \
  '        for entry in self.entries:
            entry.degraded = True' \
  '        for entry in self.entries:
            pass' "$T"

mutant "D03 ★★★ the partial flag stops telling an honest zero from an erased total" "$O" \
  '        return self.degraded and self.actual_total > 0' \
  '        return self.degraded' "$T"

# ==== R · the reader ============================================================================

mutant "R01 ★★★ the aggregate is un-wired from diagnose — written by every hook call, read by nothing" "$D" \
  '    findings.extend(token_calibration_findings(surface))' \
  '    pass' "$T"

mutant "R02 ★★★ the under-floor branch prints the noisy ratio anyway (§7g)" "$D" \
  '        if ratio is None:' \
  '        if False:' "$T"

# ⚠ T09-T11 ARE THE MUTANTS THE FIRST REAL TRANSCRIPT PAID FOR. The adapter classified one
# JSONL record at a time and reported a chars/4 ratio of 7.48 on a live session, because `usage`
# describes an API RESPONSE and Claude Code writes one record per content BLOCK. These grade the
# three halves of that repair.

mutant "T09 ★★★ the response grouping collapses — a whole compared to a part again (ratio 7.48)" "$A" \
  '        group = groups.get(gid)' \
  '        group = None' "$T"

mutant "T10 ★★★ thinking tokens stop being subtracted — the model's reasoning inflates the ratio" "$A" \
  '    if isinstance(think, int) and not isinstance(think, bool) and think > 0:
        out -= think' \
  '    if False:
        out -= think' "$T"

mutant "T11 ★★★ the cursor advances past the OPEN response — the window boundary loses it" "$A" \
  '    if open_groups and not (truncated and consumed_to == offset):' \
  '    if False and not (truncated and consumed_to == offset):' "$T"

# ==== T12-T14 · what the INDEPENDENT REVIEW found, and what had no mutant ======================
#
# ⚠ T09 GRADED THE GROUPING AND STILL MISSED F1. It mutated the id TRANSITION, which is the
# thing the review proved was the wrong model: records of two responses INTERLEAVE, so closing on
# "the next id differs" judged a response from its first blocks and invented a third. A mutant
# over a mechanism cannot find that the mechanism is the defect — only a reader, or a live
# transcript, can. These three grade the repair.

mutant "T12 ★★★ an OPEN group is judged anyway — a partial response priced as a whole one" "$A" \
  '    for gid, g in groups.items():
        if gid in closed:' \
  '    for gid, g in groups.items():
        if True:' "$T"

mutant "T13 ★★★ the cursor save goes back BEHIND the readability branch — one long line wedges the session" "$L" \
  '        if reading.consumed:
            save_cursor(root, transcript_path, reading.cursor)' \
  '        if reading.consumed and reading.readable:
            save_cursor(root, transcript_path, reading.cursor)' "$T"

mutant "T14 ★★★ a PARTIAL usage locks the group — a fine transcript reads as a moved schema" "$A" \
  '        if _priced(group.get("usage")) is None:' \
  '        if group.get("usage") is None:' "$T"

printf '\n================================================================================\n'
printf 'STAGE 09 H-2 TOKEN-CALIBRATION MUTANTS — window %s..%s of %s: %s RED, %s GREEN\n' \
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
