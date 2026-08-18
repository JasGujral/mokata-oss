#!/usr/bin/env bash
# Drives the stage-3 (CORPUS-IS-A-FILESYSTEM-WALK-NOT-THE-INDEX) mutant list through
# scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b). Never hand-edit a file to see
# whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python bash tests/_stage3_corpus_mutants.sh
#
# Baseline: stage 31's hole 7 is in force. `mutate.sh` runs the selected tests against the
# PRISTINE target before applying anything and exits 7 if they were already failing, so every
# verdict below is attributable to its mutation. A 7 aborts this batch like any other non-zero.
#
# ⚠⚠ WHAT THIS BATCH IS FOR, STATED BEFORE THE SCORE (§7i/§7f).
#
# This stage's deliverable is a CLASSIFIER, and once the tree is declared it has no undeclared
# site left to find — the §7i shape exactly, and the one stage 2 fell into INSIDE its own §7i
# guard. So the mutants aim at the classification rules, driven by the PLANTED sites in
# `test_stage3_corpus_walk.py`. If a mutant blinds a rule and the suite stays green, that rule is
# graded by nothing and the 2/37/56/59 split it produced is a guess with a number on it.
#
# THE FOUR STARRED MUTANTS ARE THE STAGE:
#   * M01 makes the sweep report no offenders unconditionally — a check that cannot fail read as
#     a check that passed (`NULLGLOB-DISARMS-THE-EXISTENCE-CHECK`, inside this stage's own guard).
#   * M02 folds "I could not resolve the root" into "a fixture, not in the class" — the §7g
#     shape: two different facts sharing one representation. THIS ONE SURVIVED THE FIRST BATCH
#     and was a real hole in this stage's own classifier; see the note above the mutant.
#   * M05 makes the declaration LEAK across scopes, which silently converts the whole mechanism
#     from a per-site predicate into a module-wide allow-list — the exact thing the row says the
#     per-caller `skip_dirs` already was.
#   * M11 makes `iter_tracked_files` fall back to a walk instead of raising. That is the false
#     green this entire stage exists to remove: the caller keeps getting an answer, the corpus
#     silently changes underneath it, and nothing ever goes red.
#
# ⚠ WHAT THIS BATCH DOES NOT GRADE, named here rather than padded into arms that cannot fail:
#
#   * `read_corpus` — the one impure function in the sweep. Mutating it breaks every test in the
#     module at once, so a RED says "the file reader broke", not "the classifier grades".
#   * `render` / `Site.__repr__` / `__eq__` / `__hash__` — presentation. `render` appears only
#     inside failure messages nobody reads on a green run; a mutant would be scored on prettiness.
#   * The CHOICE of question at each of the 37 declared sites. Whether
#     `test_footer2_src_boundary` is really a working-tree question is a judgement about what
#     `sync-public.sh` ships, recorded in prose at the site. No mutation can grade a judgement —
#     it can only grade whether the declaration is ENFORCED, which M05 and M06 do.
#   * `NotACheckout`'s message text, and the OSError arm of `iter_tracked_files` (no git binary).
#     Nothing in the suite runs without git, so an arm for it would grade the fixture.
#   * The 3.10 floor. These mutants run on one interpreter; the floor is a separate gate.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

S=tests/_corpus_sweep.py            # the classifier — the §7i-graded gate
R=tests/_support.py                 # the index reader the classifier points callers at
T='test_stage3_corpus_walk.py'

TOTAL=13
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
        printf '  See scripts/mutate.sh'"'"'s EXIT CONTRACT for what %s means and what to do.\n' "$rc"
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

# ==== A. the sweep can still ANSWER =============================================================

mutant "M01 ★★ the sweep reports no offenders, always — a check that cannot fail" "$S" \
  '    return [s for s in sweep(corpus) if s.question == UNDECLARED]' \
  '    return []' "$T"

# ⚠ M02 SURVIVED THE FIRST BATCH, AND IT WAS A REAL §7g HOLE IN THIS STAGE'S OWN CLASSIFIER.
# The sweep folded "I could not resolve the root" into "a fixture, not in the class" — 60 sites,
# including `_shipped_reads.shipped_test_sources`, the one site doc 84 names by hand as
# must-not-convert. No fixture distinguished the two, so nothing could catch the fold. Closed by
# giving the unresolved population its own answer (ASKS_UNKNOWN) and pinning it; the mutant below
# is the original mutation, now RED against the corrected code.
mutant "M02 ★★ an unresolved root is called a fixture — the offender population vanishes (§7g)" "$S" \
  '            question = declared_question(source, node.lineno, tree) or ASKS_UNKNOWN' \
  '            question = declared_question(source, node.lineno, tree) or ASKS_NEITHER' "$T"

mutant "M03 ★ a declaration is never checked against the code it sits above" "$S" \
  '        if s.question == ASKS_INDEX and s.idiom not in (INDEX_READER, RELATIVE_EXISTS):
            bad.append(s)' \
  '        if False:
            bad.append(s)' "$T"

# ==== B. the anchor derivation, one rule at a time ==============================================

mutant "M04 __file__ stops anchoring anything — every repo walk reads as a fixture" "$S" \
  '        if expr.id == "__file__":
            return REPO_ANCHORED' \
  '        if expr.id == "__file__":
            return UNRESOLVED' "$T"

mutant "M05 ★★ the declaration LEAKS across scopes — a predicate becomes an allow-list" "$S" \
  '            if not stripped.startswith("#"):
                break                         # real code — the block has ended' \
  '            if not stripped.startswith("#"):
                continue                      # real code — the block has ended' "$T"

mutant "M06 a docstring counts as a declaration — prose about the rule absolves the site" "$S" \
  '    if not m or not line.lstrip().startswith("#"):
        return None' \
  '    if not m:
        return None' "$T"

mutant "M07 two origins resolve to the first one found instead of UNRESOLVED — a guess" "$S" \
  '        found.discard(UNRESOLVED)
        if len(found) == 1:
            return found.pop()
        return UNRESOLVED                          # unknown, or two origins — never a coin flip' \
  '        found.discard(UNRESOLVED)
        if found:
            return sorted(found)[0]
        return UNRESOLVED                          # unknown, or two origins — never a coin flip' "$T"

# ⚠ M08 ALSO SURVIVED THE FIRST BATCH, and the honest reading was NOT "write a harder test".
# It aimed at a visited-set cycle guard that sat beside the depth limit. Re-measured across all
# 429 test modules, deleting that guard changed not one verdict — it was a second code path
# nothing could grade, so it was DELETED rather than propped up with a fixture written to reach
# it. M08 now aims at the guard that actually does the work.
mutant "M08 the depth limit is removed — a self-referential path assignment never terminates" "$S" \
  '    if _depth > 12:
        return UNRESOLVED' \
  '    if _depth > 100000:
        return UNRESOLVED' "$T"

# ==== C. the call inventory — the §7j half, where the typed scope was the bug ====================

mutant "M09 ★ the relative-existence shape leaves the inventory — the site §7j found is invisible" "$S" \
  '            if _is_relative_literal_chain(node.args[0], assigns):' \
  '            if False and _is_relative_literal_chain(node.args[0], assigns):' "$T"

mutant "M10 an ABSOLUTE path counts as a corpus question — the false-positive direction" "$S" \
  '        return not os.path.isabs(expr.value) and ("/" in expr.value' \
  '        return True and ("/" in expr.value' "$T"

# ==== D. the index reader — the fix the row calls principled ====================================

mutant "M11 ★★ a non-checkout DEGRADES to a walk instead of refusing — the false green itself" "$R" \
  '    if proc.returncode != 0:
        raise NotACheckout(' \
  '    if proc.returncode != 0 and False:
        raise NotACheckout(' "$T"

mutant "M12 ★ the index reader stops excluding ignored files — it becomes the walk it replaced" "$R" \
  '            ["git", "-C", root, "ls-files", "-z", "--cached", "--exclude-standard"],' \
  '            ["git", "-C", root, "ls-files", "-z", "--cached", "--others"],' "$T"

mutant "M13 a tracked-but-deleted path is yielded — every caller is about to open it" "$R" \
  '        if not os.path.isfile(ab):
            continue                              # tracked but deleted from the worktree' \
  '        if False:
            continue                              # tracked but deleted from the worktree' "$T"

# ==== verdict ===================================================================================

printf '\n================================================================================\n'
printf 'stage 3 mutants: %s ran of %s declared — %s RED, %s GREEN (survivors)\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH INCOMPLETE — %s of %s never ran.\n' "$((TOTAL - ran))" "$TOTAL"
    printf '================================================================================\n'
    exit 71
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS — these rules are graded by NOTHING:\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'All %s RED. ⚠ §7f: a clean batch is a prompt to write harder mutants, not a finish\n' "$TOTAL"
printf 'line. What it did NOT grade is listed in this file'"'"'s header — read that too.\n'
printf '================================================================================\n'
