#!/usr/bin/env bash
# Drives the 0.0.21 stage-14 mutant list (graph freshness, fixes A-D) through scripts/mutate.sh —
# the ONLY sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage14_freshness_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
#
# 🔴 WHAT THIS BATCH IS AGAINST. Stage 14's four fixes each turned a signal back ON, and the whole
# failure mode of this module is a signal that LOOKS alive and reports nothing — the graph answering
# from data it has been told is current when it is not. So every mutant here is a change that leaves
# the code reading correctly and the signal dead, which is exactly the shape of the four defects the
# stage found. A GREEN here is a fix that does not hold.
#
#   A01-A02 ★ THE SESSION LADDER (fix A). A per-process id makes signals 1 and 3 blind; `unbound`
#     sharing a representation with a resolved session makes the blindness invisible (§7g).
#
#   B01-B04 ★ SIGNAL 2 (fix B). B01 restores the gate that was the original defect. B02-B04 attack
#     the three-case filter and the baseline advance — the loop guards, whose failure is a rebuild
#     on EVERY query rather than a missed one, so a test that edits once cannot see them.
#
#   C01-C03 ★ THE PERSISTED BASELINE + UNTRACKED (fix C). C01 restores the in-memory-only read that
#     made `recheck_after_answer` die at the process boundary. C02-C03 attack `git_untracked`.
#
#   D01-D03 ★ THE DEGRADE REASON (fix D). Each collapses two reasons into one, which is how the
#     refusal comes to tell a user to adopt a graph they already have.
#
# ⚠ A01, B02 AND B03'S PATTERNS WERE REFRESHED after the independent review's fixes moved the lines
# they name: `resolve_freshness_session` split into two entry points over one `_resolve_session` body
# (finding A1/A7), and the filter's staleness test became `stale_against` (finding A3). None of the
# three MUTATIONS changed. A zero-occurrence pattern exits 3 and ABORTS this batch, silently
# ungrading every mutant after it, so a stale pattern here is not cosmetic.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

F=src/mokata/knowledge/freshness.py
I=src/mokata/brainstorm_impact.py
TF='test_a28_the_graph_notices_you_edited_it.py'
TD='test_a29_the_degrade_reason_is_not_one_word.py'

TOTAL=12
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

# ==== A. the session ladder (fix A) =============================================================

mutant "A01 ★ the ladder is skipped — every process invents its own id again (the original defect)" "$F" \
  '        res = resolve_run(root, session_id=session_id, run_id=run_id)
        if res.resolved:' \
  '        res = resolve_run(root, session_id=session_id, run_id=run_id)
        if False:' "$TF"

mutant "A02 ★ an UNBOUND session reports the same basis as a resolved one (§7g)" "$F" \
  '    return _sid(None), SESSION_UNBOUND' \
  '    return _sid(None), SESSION_GIVEN' "$TF"

# ==== B. signal 2 (fix B) =======================================================================

# ⚠ B01's PATTERN was refreshed when review finding 3-5 moved the baseline load above signal 2 (the
# additive pass needs it too). The MUTATION is unchanged: restore the HEAD gate, and signal 2 fires
# only when you commit.
mutant "B01 ★ the HEAD gate is restored — signal 2 fires only when you COMMIT" "$F" \
  '        base = self._load_index()
        if cand:' \
  '        base = self._load_index()
        if cand and head != st.head_sha:' "$TF"

# ⚠ B02's SUBJECT was re-scoped by review finding 3-5, and the old one is recorded because the change
# is the interesting part: this mutation used to reproduce the reconciled-deletion loop, and the
# absence TOMBSTONE now closes that route independently — a reconciled deletion IS in the baseline, so
# the absent-from-baseline arm never sees it. The mutant went GREEN, i.e. it had stopped grading
# anything (§7i). §7f says separate or delete, so the arm keeps the ONE case it still owns alone — a
# path git names that was never indexed and is not on disk — and this mutant now grades that.
mutant "B02 ★ the os.path.exists arm is neutered — a never-indexed deleted path forces a rebuild" "$F" \
  '                    or (rel not in base.entries
                        and os.path.exists(os.path.join(self.root, rel)))' \
  '                    or (rel not in base.entries and True)' "$TF"

mutant "B03 a NEW file absent from the baseline stops counting — is_stale answers False for it" "$F" \
  '                    if stale_against(base, self.root, rel)
                    or (rel not in base.entries' \
  '                    if stale_against(base, self.root, rel)
                    and (rel not in base.entries' "$TF"

# ⚠ B04's PATTERN was refreshed at the fourth review pass: finding 4-8(b) made
# `_advance_baseline` RETURN the tombstones it had to drop, so the call site now binds its result.
# The MUTATION is unchanged: drop the advance.
mutant "B04 ★ the baseline is never advanced — the same edit rebuilds on EVERY query" "$F" \
  '            dropped_tombs += self._advance_baseline(changed)' \
  '            pass' "$TF"

# ==== C. the persisted baseline + untracked (fix C) ============================================

mutant "C01 ★ the recheck reads the in-memory index only — dead from process 2 (fix C's defect)" "$F" \
  '        if self._index is None:
            self._index = self._load_index()
        if self._index is None:' \
  '        if self._index is None:' "$TF"

mutant "C02 ★ untracked files stop being asked for — a new file is invisible until git add" "$F" \
  '            + git_untracked(self.root, run=self._git_run))' \
  '            + list([]))' "$TF"

# ⚠ C03's PATTERN was refreshed TWICE: review finding 3-2 wrapped both git readers in
# `_no_quote_path`, and finding 4-5 added `-z` (because `core.quotePath=false` closed only one of
# four quoting classes). The MUTATION is unchanged both times: drop `--exclude-standard`.
mutant "C03 the ignore rules are dropped — a venv arrives as thousands of changed source files" "$F" \
  '        rc, out = runner(root, ["ls-files", "--others", "--exclude-standard", "-z"])' \
  '        rc, out = runner(root, ["ls-files", "--others", "-z"])' "$TF"

# ==== D. the degrade reason (fix D) ============================================================

# ⚠ D01's PATTERN was refreshed when review finding 3-1 split ADOPTION from AVAILABILITY, turning
# this two-way choice into three. The MUTATION is unchanged: collapse everything to `chain-lexical`,
# and an adopted graph that failed is told to adopt one.
mutant "D01 ★ an ADOPTED graph that failed is reported as an unwired chain — adopt-again advice" "$I" \
  '                if chain_has_a_graph:
                    _reason(DEGRADE_GRAPH_FAILED)' \
  '                if False:
                    _reason(DEGRADE_GRAPH_FAILED)' "$TD"

mutant "D02 ★ an EMPTY floor answer stops being distinguishable from one with evidence" "$I" \
  '                if not (getattr(qr, "references", None) or []):' \
  '                if False:' "$TD"

mutant "D03 a query FAULT is reported as a floor answer — the backend raised, not answered" "$I" \
  '                _reason(DEGRADE_QUERY_FAULT)' \
  '                _reason(DEGRADE_CHAIN_IS_LEXICAL)' "$TD"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE 14 FRESHNESS MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
