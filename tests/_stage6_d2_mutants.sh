#!/usr/bin/env bash
# Drives 0.0.17 stage 6's mutants through scripts/mutate.sh — the ONLY sanctioned mutator (doc 85
# §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage6_d2_mutants.sh
#
# Stage 6 — BLAST-RADIUS-LEAF-DEGRADE (D2). The defect was a SHARED REPRESENTATION: `references ==
# []` meant both "the answer is zero" and "I have no evidence". So the mutants come in three
# families, and they are chosen to attack the fix from BOTH sides rather than only the side that
# would re-open the original bug:
#
#   A. RE-MERGE the two answers   (M01, M02, M06, M07, M11) — put the conflation back, either by
#      reverting to a fallthrough or by widening the certification until absence is certified too.
#      M01 is the DANGEROUS direction: a floor that certifies a zero it never verified is a worse
#      defect than the one this stage fixes, and it must be caught by a test, not by review.
#   B. BREAK THE DERIVATION       (M03, M04, M05, M08, M09) — make `degraded` and `basis` able to
#      disagree again, which is the shape §7g actually forbids.
#   C. SILENCE THE HONESTY        (M10, M12) — keep the verdict, drop the sentence that justifies
#      it, or let the human-facing surface go back to blaming grep for a leaf.
#
#   D. AIMED AT THE TESTS         (M13, M14) — §7f, and the last two stages each found a pin that
#      was green while grading nothing. M13 mutates THE FIXTURE this file's reasoning rests on: if
#      the "leaf" quietly acquires a caller and every leaf assertion still passes, the suite was
#      never reading the fixture. M14 does the same to the "absent" symbol from the other side.
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED.
#
# ⚠ MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) reaches EIGHT instances with this file.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

Q=src/mokata/knowledge/query.py
A=src/mokata/knowledge/ast_backend.py
L=src/mokata/knowledge/layer.py
C=src/mokata/cli_commands/knowledge.py
D=tests/test_d2_blast_radius_leaf.py
T='test_d2_blast_radius_leaf.py'
TQ='test_knowledge_query.py'
TF='test_gr_s4_freshness.py'

TOTAL=14
ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    shift
    out="$("$M" "$label" "$@")" || rc=$?
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
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n  said  : %s\n' "$label" "${out:-<nothing at all>}"
                exit 70 ;;
    esac
}

# ---- family A: re-merge the two answers -------------------------------------------------------

# THE DANGEROUS DIRECTION. Certifying a zero for a symbol the index never saw is a WORSE defect
# than the one this stage fixes: it would silence the gate on a repo the floor cannot read at all.
mutant "M01 every zero is certified — absence is dressed as a verified answer" "$A" \
  '        return bool(kind in SYMBOL_EDGE_KINDS and self._defs(target))' \
  '        return True' "$T"

mutant "M02 nothing is ever certified — the leaf falls through again (the original defect)" "$A" \
  '        return bool(kind in SYMBOL_EDGE_KINDS and self._defs(target))' \
  '        return False' "$T"

mutant "M06 the predicate reads CALLERS not DEFS — a leaf has none, so the fix silently reverts" "$A" \
  '        return bool(kind in SYMBOL_EDGE_KINDS and self._defs(target))' \
  '        return bool(kind in SYMBOL_EDGE_KINDS and self._callers(target))' "$T"

# ★ M07 WAS RE-AIMED. It first added `defs` to this tuple and SURVIVED (GREEN) — correctly, as it
# turned out: for `defs`, `refs` IS `_defs(target)`, so the certification branch is reached exactly
# when `_defs` is empty, which is exactly when the predicate is False anyway. That mutant graded
# UNREACHABLE code. `imports` is the reachable neighbour and the one that actually matters —
# `cart_summary` HAS a definition and ZERO imports-of, so admitting it certifies a zero the index
# never checked. The unreachability itself is now pinned by a test instead of by a mutant.
mutant "M07 imports joins the symbol-edge kinds — a module token is certified off a def site" "$A" \
  'SYMBOL_EDGE_KINDS = ("callers", "callees", "implementers", "blast_radius")' \
  'SYMBOL_EDGE_KINDS = ("callers", "callees", "implementers", "blast_radius", "imports")' "$T"

mutant "M11 blast_radius leaves the symbol-edge kinds — D2's own surface stops being fixed" "$A" \
  'SYMBOL_EDGE_KINDS = ("callers", "callees", "implementers", "blast_radius")' \
  'SYMBOL_EDGE_KINDS = ("callers", "callees", "implementers")' "$T"

# ---- family B: break the derivation -----------------------------------------------------------

mutant "M03 verified-empty rejoins the degraded camp — the verdict flips back" "$Q" \
  'STRUCTURAL_BASES = (BASIS_STRUCTURAL, BASIS_VERIFIED_EMPTY)' \
  'STRUCTURAL_BASES = (BASIS_STRUCTURAL,)' "$T"

mutant "M04 the lexical floor counts as structural — every absence is admitted" "$Q" \
  'STRUCTURAL_BASES = (BASIS_STRUCTURAL, BASIS_VERIFIED_EMPTY)' \
  'STRUCTURAL_BASES = (BASIS_STRUCTURAL, BASIS_VERIFIED_EMPTY, BASIS_LEXICAL)' "$TQ"

mutant "M05 degraded becomes a stored field again — the two halves can disagree once more" "$Q" \
  '    basis: str = BASIS_LEXICAL                  # which rung answered (the ONE stored signal)' \
  '    basis: str = BASIS_LEXICAL
    degraded: bool = False' "$TQ"

mutant "M08 the verified-empty coherence check is dropped — a certified zero may carry hits" "$Q" \
  '        if self.basis == BASIS_VERIFIED_EMPTY and self.references:' \
  '        if False:' "$T"

# ★ M09 WAS RE-AIMED. It first ran `test_gr_s4_freshness.py` and reported RED — but only in the
# developer's MAIN checkout. On a CLEAN tree (a linked worktree, and a plain copy of src+tests) the
# very same mutant SURVIVES: that suite never graded the demotion at all, and the RED was an
# artefact of the working checkout. Filed as `MUTANT-VERDICT-ENVIRONMENT-DEPENDENT`. The layer's two
# demotion sites now have BEHAVIOURAL pins of their own (a stale graph and a failed graph must each
# refuse to pass on the floor's certification), which is what this mutant grades.
mutant "M09 demote_to_floor stops moving the basis — a stale graph passes on a certification" "$Q" \
  '        self.basis = BASIS_LEXICAL
        if why:' \
  '        if why:' "$T"

# ---- family C: silence the honesty ------------------------------------------------------------

mutant "M10 the verified-empty answer stops saying what verified it" "$A" \
  'AST_EMPTY_NOTE = ("no references — the embedded AST floor holds this symbol'"'"'s definition and found "' \
  'AST_EMPTY_NOTE = ("" or "' "$T"

mutant "M12 the CLI calls a leaf a grep fallback again — the human-facing conflation returns" "$C" \
  '    if result.verified_empty:
        mode = "verified empty"
    elif result.degraded:' \
  '    if False:
        mode = "verified empty"
    elif result.degraded:' "$T"

# ---- family D: aimed at THE TESTS (§7f) -------------------------------------------------------

# If the "leaf" acquires a caller and every leaf assertion still passes, this file was never
# reading its own fixture — the exact false-green the last two stages each found.
mutant "M13 the fixture's LEAF gains a caller — do the leaf pins actually read the fixture?" "$D" \
  'def checkout_button(label):
    return f"<button>{label}</button>"' \
  'def checkout_button(label):
    return f"<button>{cart_summary(label)}</button>"' "$T"

# The same question from the other side: if the "absent" symbol is quietly DEFINED, the
# absence-still-refuses pins must go red, or they were grading nothing.
mutant "M14 the fixture's ABSENT symbol becomes defined — do the absence pins read the fixture?" "$D" \
  'def mini_cart(items):
    return use_cart(items)' \
  'def mini_cart(items):
    return use_cart(items)


def totally_unknown(x):
    return x' "$T"

# ---- report -----------------------------------------------------------------------------------

printf '\n================================================================================\n'
printf 'stage 6 (D2 · BLAST-RADIUS-LEAF-DEGRADE) mutation batch\n'
printf '  ran %s of %s · RED %s · GREEN %s\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf '\nSURVIVORS — these mutations were NOT caught:\n%s' "$survivors"
    printf 'A survivor is either a weak pin or unreachable code. Neither is acceptable unstated.\n'
    printf '================================================================================\n'
    exit 71
fi
printf '  ALL MUTANTS CAUGHT.\n'
printf '================================================================================\n'
