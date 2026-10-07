#!/usr/bin/env bash
# The 0.0.21 stage-14 INDEPENDENT REVIEW's mutant list, through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage14_review_mutants.sh
#
# 🔴 WHY THIS FILE IS SEPARATE FROM `_stage14_freshness_mutants.sh`. That batch is the one the builder
# designed, and it scored 12/12. Two independent reviewers then wrote fifteen mutants of their own and
# **NINE SURVIVED** — four against the freshness module, five against the gate. Keeping them in their
# own file keeps the record honest: the builder's batch graded what the builder thought about, and
# this one grades what the builder did not. A single merged file would hide that difference, and the
# difference is the most useful thing either batch has to say.
#
# Every mutant here corresponds to a numbered review finding, and every one of them was GREEN before
# the fixes in this batch's commit. If any goes GREEN again, the fix for that finding has come undone.
#
#   A1a/A1b ★★★ the writer/reader namespace, and the note that was gated on the wrong thing. A1 is
#     the finding that falsified the stage's headline claim: fix A rewired the READER only.
#   A3      ★★★ the fourth filter case — a path that exists and cannot be hashed. A forever loop.
#   A6a/A6b ★★ the two properties nothing asserted: the extension filter (after its unreachable
#     twin was deleted) and the post-answer baseline advance.
#   A6c     ★★★ signal 1 peeked instead of drained. NO test anywhere drove signal 1 end to end.
#   A7      ★★ an explicit id silently replaced by an ambient pin.
#   B1      ★★★ the machine `hint` disagreeing with the refusal it ships beside.
#   B2      ★★★ a repo with NO graph told to repair the one it has — the symptom inverted.
#   B3a/B3b ★★★ the §7h verdict guard, which graded 6 of 32 reason sets: weaken the gate for a
#     reachable PAIR, and remove the ledgered escape for that same pair.
#   B4      ★★ one attribute, two opposite defaults.
#   B6      ★★ the one-time notice prescribing a remedy it cannot know applies.
#   B7      ★ a refusal carrying no reason token, indistinguishable from a clean answer.
#
# 🔴 REVIEW FINDING 3-9 — THIS HEADER ADVERTISED A MUTANT THE FILE DID NOT CONTAIN. It documented 14
# findings including B6 (*"the one-time notice prescribing a remedy it cannot know applies"*) and
# declared *"every mutant here corresponds to a numbered review finding"*, while `TOTAL=13` and no
# B6 call existed. §7b: the list IS the score, so a list that over-states its own coverage reports a
# confident number for work it did not do — and the third reviewer confirmed the B6 property was in
# fact UNGRADED, not merely unlisted. B6 is now present and TOTAL is 14.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

F=src/mokata/knowledge/freshness.py
G=src/mokata/govern/graph_required.py
I=src/mokata/brainstorm_impact.py
S=src/mokata/mcp/tools_spec.py
TF='test_a28_the_graph_notices_you_edited_it.py'
TD='test_a29_the_degrade_reason_is_not_one_word.py'

TOTAL=14
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

# ==== A · the freshness module ==================================================================

mutant "A1a ★★★ the WRITER goes back to _sid — the namespaces separate and signal 1 dies again" "$F" \
  '    key, _basis = resolve_harness_session(root, session_id)' \
  '    key, _basis = (_sid(session_id), SESSION_GIVEN)' "$TF"

# ⚠ A1b's PATTERN was refreshed when review finding 3-6 rewrote this note (it now counts RECORDS and
# fires only on a pass that would otherwise report fresh). The MUTATION is unchanged: restore the
# basis gate, and a resolved session draining a key nobody wrote goes silent again.
mutant "A1b ★★★ the blind-signal note is gated on the BASIS again — a silent fresh returns" "$F" \
  '            foreign, foreign_records = foreign_dirty_records(self.root, self.session_id)' \
  '            foreign, foreign_records = (foreign_dirty_records(self.root, self.session_id) if self.session_basis == SESSION_UNBOUND else ([], 0))' "$TF"

mutant "A3 ★★★ an unhashable path is skipped instead of sentinelled — the forever loop returns" "$F" \
  '                base.entries[rel] = IndexEntry(rel, UNFINGERPRINTABLE, 0.0, -1)
                continue' \
  '                continue' "$TF"

mutant "A6a ★★ the extension filter is removed — now the ONLY defence, so it must grade" "$F" \
  '    return [rel for rel in paths if rel.lower().endswith(languages.SOURCE_EXTENSIONS)]' \
  '    return list(paths)' "$TF"

mutant "A6b ★★ the post-answer recheck never advances the baseline — a rebuild per answer" "$F" \
  '            self._index.reindex(self.root, only=stale)
            self._save_index(self._index)' \
  '            pass' "$TF"

mutant "A6c ★★★ signal 1 is PEEKED, not drained — one edit rebuilds for the whole session" "$F" \
  '        dirty = drain_dirty(self.root, session_id=self.session_id)' \
  '        dirty = read_dirty(self.root, session_id=self.session_id)' "$TF"

mutant "A7 ★★ an explicit id is passed as the BOUND input only — an ambient pin silently wins" "$F" \
  '    return _resolve_session(root, session_id=session_id, run_id=session_id)' \
  '    return _resolve_session(root, session_id=session_id, run_id=None)' "$TF"

# ==== B · the graph.required gate ===============================================================

mutant "B1 ★★★ the machine hint goes back to a hardcoded 'adopt' beside a 'do not adopt' refusal" "$S" \
  '                "hint": GR.hint_for(reasons)}' \
  '                "hint": "this blast radius is a degraded lexical estimate — adopt a code graph (`mokata graph adopt`) or accept it with `--allow-degraded`."}' "$TD"

# ⚠ B2's PATTERN was refreshed when review finding 3-7 moved `floor-empty` to the front of the
# precedence and 3-1 added `graph-unavailable` to the repair family. The MUTATION is unchanged:
# demote `chain-lexical` below the repair family, and a repo with no graph is told to repair one.
mutant "B2 ★★★ chain-lexical stops outranking the repair family — no graph, told to repair one" "$G" \
  '    if DEGRADE_CHAIN_IS_LEXICAL in rs:
        return REMEDY_ADOPT' \
  '    if False:
        return REMEDY_ADOPT' "$TD"

mutant "B3a ★★★ the gate stops refusing for ONE reachable reason PAIR (the reviewer's R01)" "$G" \
  '    refused = bool(degraded and required and not overridden)' \
  '    refused = bool(degraded and required and not overridden) and not {DEGRADE_QUERY_FAULT, DEGRADE_CHAIN_IS_LEXICAL} <= set(reasons or ())' "$TD"

mutant "B3b ★★★ the ledgered escape is dropped for that same pair (the reviewer's R02)" "$G" \
  '    accept = ("  2. Explicitly accept the degraded evidence for THIS session — `--allow-degraded` "' \
  '    accept = ("" if {DEGRADE_QUERY_FAULT, DEGRADE_CHAIN_IS_LEXICAL} <= set(reasons or ()) else "  2. Explicitly accept the degraded evidence for THIS session — `--allow-degraded` "' "$TD"

mutant "B4 ★★ uses_graph defaults to TRUE again — an attribute-less layer reads as a real graph" "$I" \
  '        return bool(getattr(layer, "uses_graph", False))' \
  '        return bool(getattr(layer, "uses_graph", True))' "$TD"

mutant "B6 ★★ the one-time notice prescribes a remedy it cannot know applies" "$G" \
  '"do about THIS refusal is below; it depends on why the evidence is degraded. Turn' \
  '"do about THIS refusal is below. Adopt a real code graph (`mokata graph adopt`). Turn' "$TD"

mutant "B7 ★ a refusal carries NO reason token — indistinguishable from a clean answer (§7g)" "$G" \
  '    if refused and not reasons:
        reasons = (DEGRADE_NOT_DERIVED,)' \
  '    if False:
        reasons = (DEGRADE_NOT_DERIVED,)' "$TD"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE 14 REVIEW MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a REVIEW FINDING whose fix does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
