#!/usr/bin/env bash
# The 0.0.21 stage-14 FOURTH-PASS review's mutant list, through scripts/mutate.sh (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage14_review3_mutants.sh
#
# 🔴 THE FOURTH BATCH, AND THE SCORE HISTORY IS THE ARGUMENT FOR KEEPING THEM SEPARATE:
#
#     builder's batch      12/12 RED  ->  9 survivors found by pass 2
#     passes 1+2's batch   14/14 RED  -> 11 survivors found by pass 3
#     pass 3's batch       13/13 RED  -> 10 survivors found by pass 4
#     this batch             ?
#
# ⭐ Every pass has found defects the PREVIOUS pass's fixes introduced or left ungraded, and the
# severity has not fallen off. Pass 4 found a REAL content edit reading `fresh=True` with an empty
# note, a shipped refusal stating four false things, and all of fix D deletable with one token.
#
#   S01-S04 ★★★ `baseline_drift`'s three stat fields. Each was an individual survivor (§7f) — only
#     the pair was graded, and only indirectly through the tombstone arms. S04 is the one that
#     matters most: `ctime` is the only field userspace cannot forge.
#   S05-S07 ★★★ the three arms pass 4 found ungraded: the re-stamp that keeps 2b ∝ churn, the
#     tombstoned-and-gone arm, and the unhashable re-ask.
#   S08-S10 ★★★ THE OTHER THREE WRITERS OF A RECORD, added when S04 survived: `reindex`, the
#     freshness cold walk, and `_advance_baseline`. The ctime CLAUSE and the ctime FIELD are
#     separate things to get wrong (§7f), and four sites write the field. Dropping the write at any
#     one of them disarms the clause silently, because a recorded 0.0 is "no opinion" by design.
#   S11     ★★★ the stat comparison fires on EVERYTHING — the polarity control for S01-S03, without
#     which "the clause caught it" is also true of a pass that reports every indexed path.
#   C01-C06 ★★★ finding 4-8's two caps, and THE DIRECTION of each. C01/C02 are the fail-open shapes
#     (drop the unaffordable candidates; re-stamp them as settled) — the ones that turn a cap into a
#     false green. C03/C05 delete the disclosure. C04 drops the whole prune. C06 widens the
#     tombstone prune to REAL records, which is data loss wearing a cap's clothes (§7j).
#   F01     ★★★ ALL OF FIX D, deleted by dropping one keyword at the one live call site.
#   F02     ★★★ `floor-empty` back to per-target scope — a 40-reference radius called ABSENT.
#   F03     ★★★ `pinned_graph_tool` derived from `is_graph` — adoption collapsed into availability
#     at its SOURCE, one layer under where N01 grades it.
#   Q01-Q02 ★★★ the quoting classes `-c core.quotePath=false` alone does not close, and the merge
#     gate that failed OPEN on a filename.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

F=src/mokata/knowledge/freshness.py
X=src/mokata/knowledge/index.py
L=src/mokata/knowledge/layer.py
I=src/mokata/brainstorm_impact.py
S=src/mokata/mcp/tools_spec.py
K=src/mokata/cli_commands/knowledge.py
TF='test_a28_the_graph_notices_you_edited_it.py'
TD='test_a29_the_degrade_reason_is_not_one_word.py'

TOTAL=22
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

# ==== S · the additive baseline's own comparison ================================================

mutant "S01 ★★★ the MTIME clause is dropped — only size is compared" "$F" \
  '            if st.st_mtime != float(getattr(entry, "mtime", 0)) \' \
  '            if False \' "$TF"

mutant "S02 ★★★ the SIZE clause is dropped — only mtime is compared" "$F" \
  '                    or st.st_size != int(getattr(entry, "size", -1)) \' \
  '                    or False \' "$TF"

mutant "S03 ★★★ the CTIME clause is dropped — a mtime-preserving write reads as a clean tree" "$F" \
  '                    or (recorded_ctime and st.st_ctime != recorded_ctime):' \
  '                    or False:' "$TF"

mutant "S04 ★★★ ctime is never RECORDED, so the clause above can never fire" "$X" \
  '            self.entries[rel] = IndexEntry(rel, h, m, s, file_ctime(ab))
        return list(self.entries)' \
  '            self.entries[rel] = IndexEntry(rel, h, m, s)
        return list(self.entries)' "$TF"

mutant "S08 ★★★ REINDEX drops the ctime — a record build got right is downgraded on re-walk" "$X" \
  '                self.entries[rel] = IndexEntry(rel, h, m, s, file_ctime(ab))
        return targets' \
  '                self.entries[rel] = IndexEntry(rel, h, m, s)
        return targets' "$TF"

mutant "S09 ★★★ the FRESHNESS cold walk drops the ctime — the baseline is born with no opinion" "$F" \
  '                idx.entries[rel] = IndexEntry(rel, h, m, s, file_ctime(ab))' \
  '                idx.entries[rel] = IndexEntry(rel, h, m, s)' "$TF"

mutant "S10 ★★★ the BASELINE ADVANCE drops the ctime — the signal works once, then is disarmed" "$F" \
  '            base.entries[rel] = IndexEntry(rel, h, m, sz, file_ctime(ab))' \
  '            base.entries[rel] = IndexEntry(rel, h, m, sz)' "$TF"

mutant "S11 ★★★ the stat comparison fires on EVERYTHING — S01-S03's polarity control" "$F" \
  '            if st.st_mtime != float(getattr(entry, "mtime", 0)) \' \
  '            if True or st.st_mtime != float(getattr(entry, "mtime", 0)) \' "$TF"

mutant "S05 ★★★ the drift re-stamp is dropped — 2b becomes ∝ touched-files, not ∝ churn" "$F" \
  '                drift_settled = [rel for rel in confirmable if rel not in hits]' \
  '                drift_settled = []' "$TF"

mutant "S06 ★★★ the tombstoned-and-still-gone arm goes — a reconciled deletion drifts forever" "$F" \
  '            if recorded != ABSENT:
                out.append(rel)                     # indexed, and gone' \
  '            if True:
                out.append(rel)                     # indexed, and gone' "$TF"

mutant "S07 ★★★ an unhashable path is never re-asked — the sentinel becomes an amnesty" "$F" \
  '        if recorded == UNFINGERPRINTABLE:
            out.append(rel)                         # parked as unhashable — ask again now it stats
            continue' \
  '        if recorded == UNFINGERPRINTABLE:
            continue' "$TF"

# ==== C · finding 4-8's caps, and which way each one fails =====================================

mutant "C01 ★★★ the over-cap candidates are DROPPED — an unaffordable hash reads as CLEAN (§7e)" "$F" \
  '                    changed |= set(unaffordable)' \
  '                    pass' "$TF"

# ⛔ C02 WAS WITHDRAWN AS AN EQUIVALENT MUTANT, and it is recorded here rather than deleted.
#
#   C02 was "the unaffordable candidates are RE-STAMPED as settled", i.e. widening `drift_settled`
#   from `confirmable` back to `cands`. It ran GREEN, and the GREEN was CORRECT: `_advance_baseline`
#   computes a fresh `file_fingerprint` itself, and `unaffordable ⊆ changed` always holds, so the
#   rebuild arm advances exactly the same records with exactly the same values. There is no
#   observable difference to grade, so no test can kill it and none should be written to try.
#
#   The code comment it falsified ("the cap's own fail-open, one line later") and the test written
#   to the same wrong theory were both corrected — see the note at `drift_settled` and
#   `test_a_capped_pass_SETTLES_rather_than_re_drifting_every_time`. An equivalent mutant left in a
#   batch is a permanent GREEN that trains the reader to discount GREENs, which is the one thing a
#   mutant list cannot afford (§7b).
#
#   C02b is what replaced it: the arm the restriction protects is real and IS gradeable — a capped
#   pass must still settle, and dropping the advance is what breaks that.

mutant "C02b ★★★ a capped pass does not advance at all — the unconfirmed paths re-drift forever" "$F" \
  '            dropped_tombs += self._advance_baseline(changed)' \
  '            pass' "$TF"

mutant "C03 ★★★ the drift cap stops disclosing — an expensive read looks like a cheap one" "$F" \
  '                    notes.append(
                        "freshness: baseline-drift cap' \
  '                    _unused_note = (
                        "freshness: baseline-drift cap' "$TF"

mutant "C04 ★★★ the tombstone prune never runs — the baseline grows with the repo HISTORY" "$F" \
  '        dropped = self._prune_tombstones(base)' \
  '        dropped = []' "$TF"

mutant "C05 ★★★ the dropped tombstones are not DISCLOSED — a reopened A5 gap, silently" "$F" \
  '        if dropped_tombs:' \
  '        if False:' "$TF"

mutant "C06 ★★★ the tombstone cap counts REAL records too — data loss wearing a cap's clothes (§7j)" "$F" \
  '        tombs = [rel for rel, e in entries.items()
                 if getattr(e, "content_hash", None) == ABSENT]' \
  '        tombs = list(entries)' "$TF"

# ==== F · fix D's delivery, scope and source ===================================================

mutant "F01 ★★★ the live call site stops passing the reasons — ALL of fix D reverts" "$S" \
  '            targets=list(targets), notice=notice, reasons=reasons)' \
  '            targets=list(targets), notice=notice)' "$TD"

mutant "F02 ★★★ floor-empty goes back to PER-TARGET — a 40-reference radius is called ABSENT" "$I" \
  '    if empty_targets and not ref_keys:' \
  '    if empty_targets:' "$TD"

mutant "F03 ★★★ pinned_graph_tool is derived from is_graph — adoption collapses into availability" "$L" \
  '            return graph_pinned_tool(_load_manifest_data(root))' \
  '            return "code-review-graph" if self.primary.is_graph else None' "$TD"

# ==== Q · the quoting classes ==================================================================

mutant "Q01 ★★★ the diff reader drops -z — a name containing a quote goes unseen again" "$F" \
  '        rc, out = runner(root, ["diff", "--name-only", "-z", sha])' \
  '        rc, out = runner(root, ["diff", "--name-only", sha])' "$TF"

mutant "Q02 ★★★ the untracked reader drops -z" "$F" \
  '        rc, out = runner(root, ["ls-files", "--others", "--exclude-standard", "-z"])' \
  '        rc, out = runner(root, ["ls-files", "--others", "--exclude-standard"])' "$TF"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE 14 REVIEW-3 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a REVIEW FINDING whose fix does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
