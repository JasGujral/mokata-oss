#!/usr/bin/env bash
# Drives the 0.0.19 stage-05 (row B1) mutant list through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_b1_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated.
# (Driver shape copied from _stage28_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# 🔴 WHY THIS FILE IS THE STAGE'S EXIT BAR, NOT A FORMALITY. B1 exists because a block of tests
# reported success by not running. A green harness proves the harness EXECUTES; only a red one
# proves it GRADES. 0.0.18's close booked two failures of exactly that shape on one release
# (`fail_on_unmatched_files` green with a complete list so the failure path never ran; the refusal
# job present-and-skipped). Shipping a third would put this stage inside the problem it exists to
# fix. Every arm below therefore has a named question it alone can answer.
#
#   S01-S03 ★ THE SUBJECTS. Mutate `release.sh`, `sync-public.sh` and `CLAUDE.md`'s ships-list —
#     the three files the row is about — and require the harness to go RED. These are the only
#     mutants that answer "does the grader meet its subject on a runner?"; everything else grades
#     the machinery that makes them possible.
#
#   T01-T04 THE THREE-STATE TREE. Each collapses one of `tree_state`'s answers into another, which
#     is the §7g defect the stage closes, committed inside the fix.
#
#   C01-C03 THE CENSUS PAIRING. Each lets a tree's state and its run outcome disagree silently.
#
#   R01-R03 THE RUN-TIME ARM. The first cut of this harness predicted 109 skips against a run that
#     reported 113 — the four §7g companions decide at run time and no decorator reader can see
#     them. These make sure the arm that closed that gap is graded and not decorative.
#
#   D01-D03 THE DERIVATION'S SCOPE. Each shrinks the sweep, which is the failure mode the stage
#     itself started with: the row scoped B1 to four files and the class is fourteen.
#
# ⚠ NOT MUTATED, AND WHY (§7i, stated rather than papered over): `TestTheBoundaryIsUnchangedByThis
#   Stage`'s two transcribed sets. A mutant that edits `sync-public.sh`'s `--exclude` list to make
#   that pin red would be a mutant of the PUBLIC/OSS BOUNDARY ITSELF, run against a working tree, in
#   a stage whose first constraint is that the boundary does not move. S02 mutates the script's
#   nested-checkout rule instead — the same file, no boundary entry touched. The pin is graded by
#   `test_no_control_names_anything_this_stage_added` and by its own literal, not by this batch.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

I=tests/_internal_subject.py
B=tests/_shipped_reads.py
R=scripts/release.sh
S=scripts/sync-public.sh
C=CLAUDE.md
T='test_b1_internal_tests_meet_their_subject.py'

TOTAL=16
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

# ==== S. ★ THE SUBJECTS — the stage's exit bar =================================================

mutant "S01 ★ SUBJECT: release.sh stops failing closed" "$R" \
  'set -euo pipefail' 'set -uo pipefail' "$T"

mutant "S02 ★ SUBJECT: sync-public.sh's boundary walk stops finding nested checkouts" "$S" \
  'done < <(find "$SRC" -mindepth 2 -name .git -print 2>/dev/null || true)' \
  'done < <(find "$SRC" -mindepth 2 -name .gitmodules -print 2>/dev/null || true)' "$T"

mutant "S03 ★ SUBJECT: the CLAUDE.md ships-list drops an entry the controls exclude" "$C" \
  '`scripts/sync-public.sh`, `scripts/release.sh`, `scripts/check-tracker-tables.py`.' \
  '`scripts/sync-public.sh`, `scripts/release.sh`.' "$T"

# ==== T. the three-state tree (§7g) ============================================================

mutant "T01 a broken dev tree is called the MIRROR — the collapse the stage exists to end" "$I" \
  '    if not internal_present:
        return TreeReport(TREE_MIRROR, internal_present)
    return TreeReport(TREE_INCOHERENT, internal_present)' \
  '    return TreeReport(TREE_MIRROR, internal_present)' "$T"

mutant "T02 a directory that is not mokata at all is accepted as a checkout" "$I" \
  '    if shipped_missing:
        return TreeReport(TREE_INCOHERENT, internal_present, shipped_missing)' \
  '    if False:
        return TreeReport(TREE_INCOHERENT, internal_present, shipped_missing)' "$T"

mutant "T03 ONE internal witness present is enough to call the tree DEV" "$I" \
  '    if len(internal_present) == len(INTERNAL_WITNESSES):' \
  '    if internal_present:' "$T"

mutant "T04 an unresolved guard is GRADED — vacuity reads as a real grade" "$I" \
  '    if not subjects:
        # THE VACUITY THIS MUST REFUSE' \
  '    if False:
        # THE VACUITY THIS MUST REFUSE' "$T"

# ==== C. the census pairing ====================================================================

mutant "C01 UNDECIDABLE is folded into ABSENT_BY_DESIGN — three states become two" "$I" \
    '    if tree.kind == TREE_MIRROR:
        return ABSENT_BY_DESIGN
    return UNDECIDABLE' \
    '    return ABSENT_BY_DESIGN' "$T"

mutant "C02 the census reports every guarded class as graded, whatever the tree says" "$I" \
  '        state = subject_state(root, guard.subjects, tree)' \
  '        state = GRADED' "$T"

mutant "C03 UNDECIDABLE rows stop rendering, so the census cannot say it is unsure" "$I" \
  '        if self.undecidable:' '        if False:' "$T"

# ==== R. the run-time arm — the gap that made the first cut wrong ==============================

mutant "R01 ★ skips_expected drops the run-time companions (predicts 109, the run reports 113)" "$I" \
  '        return (sum(len(g.tests) for g in self.ungraded if keep(g.filename))
                + sum(1 for r in self.runtime_ungraded if keep(r.filename)))' \
  '        return sum(len(g.tests) for g in self.ungraded if keep(g.filename))' "$T"

mutant "R02 a skip that fires when the subject is PRESENT is counted as a mirror skip" "$I" \
  '        if not isinstance(child, ast.UnaryOp) or not isinstance(child.op, ast.Not):
            continue' \
  '        if not isinstance(child, ast.UnaryOp):
            continue' "$T"

mutant "R03 the run-time arm counts companions in the tree where their subject EXISTS" "$I" \
  '    runtime = tuple(skip for skip in runtime_boundary_skips(corpus)
                    if subject_state(root, skip.subjects, tree) != GRADED)' \
  '    runtime = runtime_boundary_skips(corpus)' "$T"

# ==== D. the derivation's scope ================================================================

mutant "D01 the sweep stops seeing decorator guards — every assertion goes vacuously true" "$B" \
  '        if _is_existence_condition(dec.args[0]):
            return GUARD_DECORATOR' \
  '        if False:
            return GUARD_DECORATOR' "$T"

mutant "D02 guard_subjects resolves nothing, so no skip can name what it skipped for" "$I" \
  '                                      sr.guard_subjects(node, tree), tests))' \
  '                                      frozenset(), tests))' "$T"

mutant "D03 the corpus sweep silently drops the file it cannot parse" "$I" \
  '        try:
            ast.parse(source)
        except SyntaxError as exc:
            bad.append((filename, "unparseable: %s" % (exc,)))' \
  '        try:
            ast.parse(source)
        except SyntaxError:
            pass' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'B1 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
