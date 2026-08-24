#!/usr/bin/env bash
# Drives the B5 `disclosure-must-resolve` mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_b5_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated, and on exit 7 the baseline was not green so nothing after it is attributable.
# (Driver copied from _branch_protection_states_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED,
# doc 84.)
#
# WHAT THIS BATCH IS FOR. B5 exists because a PRESENCE test passed: `release-notes-check` graded
# whether a promise was still printed and never whether it was still true, so five published
# commitments went to PyPI against a release whose scope had been replaced, green. The four
# mutations the brief named are therefore the four ways this check quietly becomes the thing it
# replaced, and each has its own section:
#
#   A  *resolves* weakened back to *is present*        — the original defect, restored
#   B  the third state collapsed into a pass           — the mirror silently passing everything
#   C  the accounting widened to exempt everything     — an exemption list wearing a check's clothes
#   D  the check unwired                               — B1's row, in the release that fixed it
#
# ⚠ THE LIMIT, STATED BEFORE THE SCORE (§7i). This grades the DECISION LOGIC and the WIRING TEXT.
# It cannot grade what a real cut does: `release.sh` is not executed here, and the CI leg's exit-2
# arm is graded as the YAML text that carries it, not as a run of GitHub Actions. What that costs is
# narrow and worth naming — a step renamed or moved to another job would keep the text and lose the
# wiring, and only a cut would notice. Sections D01/D02 pin the call site and the arm rather than
# the mere presence of the string, which is the strongest thing available offline.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
DISC=src/mokata/disclosure.py
PKG=src/mokata/packaging.py
CLI=src/mokata/cli_commands/core.py

T='test_b5_disclosure_must_resolve.py'

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

# ==== A. ★★ *RESOLVES* WEAKENED BACK TO *IS PRESENT* — the defect itself, replayed ============
# A01 is the bug expressed as a mutation. If it survives, the whole row is decorative and this file
# is the only thing that would have said so.

mutant "A01 ★★ resolution becomes PRESENCE — any assignment of the key passes, whatever release" \
  "$DISC" \
  '    if claim.target in releases:' \
  '    if assignments:' "$T"

mutant "A02 ★★ a row is identified by its TEXT, not its first cell — the complaining row resolves
          the promise it was filed to complain about" "$DISC" \
  '            key_match = _ITEM_KEY.match(cells[0].strip().strip("*`_ "))' \
  '            key_match = _ITEM_KEY.search(stripped)' "$T"

mutant "A03 ★ an item the corpus has never heard of passes" "$DISC" \
  '    if not assignments:
        return Verdict(claim, UNRESOLVED,
                       "the planning corpus was consulted and knows no item %r at all, so this "
                       "release promise is owned by nothing." % claim.key)' \
  '    if not assignments:
        return Verdict(claim, RESOLVES, "no assignment found")' "$T"

# ==== B. ★★ THE THIRD STATE COLLAPSED — in BOTH directions, because both are failures =========
# "Absent because this is the mirror" must never render as a pass AND must never render as a
# failure. A batch that only mutated one direction would leave the other unguarded.

mutant "B01 ★★ NOT_CHECKABLE admitted to the passing set — the mirror silently passes everything" \
  "$DISC" \
  'PASSING = (RESOLVES, DISCLOSED, HELD, SPENT)' \
  'PASSING = (RESOLVES, DISCLOSED, HELD, SPENT, NOT_CHECKABLE)' "$T"

mutant "B02 ★★ an absent corpus is treated as a CONSULTED empty one — the mirror reds on every
          keyed claim instead" "$DISC" \
  '    index = None if plan_sources is None else plan_assignments(plan_sources)' \
  '    index = plan_assignments(plan_sources or {})' "$T"

mutant "B03 ★★ the report calls an undecided run a PASS" "$DISC" \
  '    @property
    def undecided(self) -> bool:
        """Something was left for the resolving gate. NOT a failure, and NOT a pass."""
        return bool(self.not_checkable)' \
  '    @property
    def undecided(self) -> bool:
        """Something was left for the resolving gate. NOT a failure, and NOT a pass."""
        return False' "$T"

mutant "B04 ★★ the CLI maps the third state onto 0 — three states, two exit codes" "$CLI" \
  '    return 2 if res.claims_undecided else 0' \
  '    return 0' "$T"

mutant "B05 ★ the SHIPPED leg starts supplying a corpus — the boundary crossed from the other
          side, and the third state becomes unreachable" "$PKG" \
  '        claims=check_disclosure_resolves(shipped_claim_sources(root), None, cutting=norm or None),' \
  '        claims=check_disclosure_resolves(shipped_claim_sources(root), {}, cutting=norm or None),' "$T"

# ==== C. ★★ THE ACCOUNTING WIDENED — an exemption list wearing a check's clothes ===============
# The table is the one declared thing in this design, so it is the one thing that can grow until
# nothing is graded. Both narrowings are mutated, plus the blank-cheque form.

mutant "C01 ★★ the accounting covers KEYED claims too — narrowing #1 gone, and offender 1 becomes
          excusable by declaration instead of correctable by fixing it" "$DISC" \
  '    if claim.key is not None:
        return None' \
  '    if False:
        return None' "$T"

mutant "C02 ★★ the accounting answers BEFORE the third state — narrowing #2 gone, and the mirror
          passes through the accounting column" "$DISC" \
  '    entry = account_for(claim, accounted)
    if entry is not None:
        return Verdict(claim, DISCLOSED if entry.status == ACCOUNT_DISCLOSED else HELD,
                       entry.render())

    if claim.key is None:' \
  '    if claim.key is None:' "$T"

mutant "C03 ★★ an EMPTY fragment matches every claim — the blank cheque" "$DISC" \
  '        if entry.fragment and entry.fragment in claim.text:' \
  '        if entry.fragment in claim.text:' "$T"

mutant "C04 ★ staleness stops being reported — an entry that describes nothing keeps its pass
          while the reworded claim goes unguarded" "$DISC" \
  '        stale=stale_accounts(claims, accounted),' \
  '        stale=(),' "$T"

# ==== D. ★★ THE CHECK UNWIRED — a check that runs nowhere is B1's row, in B1's release =========

mutant "D01 ★★ release.sh stops running the resolving gate" scripts/release.sh \
  'verify_release_notes "." "the dev checkout (HEAD)"
verify_disclosure_resolves' \
  'verify_release_notes "." "the dev checkout (HEAD)"' "$T"

# ==== summary =================================================================================
printf '\n================================================================================\n'
printf 'B5 disclosure-must-resolve mutants: %s ran of %s · RED %s · GREEN %s\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a path on which a broken promise could ship:\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'No survivors.\n'
printf '================================================================================\n'
