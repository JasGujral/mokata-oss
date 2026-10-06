#!/usr/bin/env bash
# 0.0.21 stage 02 `one-mirror-one-truth`, graded. Through scripts/mutate.sh (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage02_one_mirror_one_truth_mutants.sh
#
# Four rows, grouped by which one each mutant attacks:
#
#   M01-M05 ★★★ `verify-mirror.sh`'s refusals: the missing path (the `|| cd` idiom that caused the
#     whole row), the wrong repo, AHEAD, DIVERGED, and the dirty tree — which is
#     SYNC-CHECKOUT-FALLBACK's actual root cause, refused at the front.
#   M06     ★★★ the NOT-FETCHED state collapsing back into DIVERGED, which is what my own first
#     draft did: the alarming word for the routine problem, and a real divergence saying the same.
#   B01-B02 ★★★ the DEFAULT BRANCH derivation: guessing `main`, and the fail-closed refusal when
#     neither the remote nor origin/HEAD answers.
#   F01-F03 ★★★ the FAST-FORWARD half, which is where the hazard actually lived: both refusals
#     back to best-effort, and the soft note that carried on from a stale base.
#   N01     ★★★ the checkout fallback's mask coming back.
#   C01     ★★★ the polarity control — verify-mirror refuses everything, which would make every
#     refusal above true of a release that can never run.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

V=scripts/verify-mirror.sh
S=scripts/sync-public.sh
R=scripts/release.sh
T='test_a32_one_mirror_one_truth.py'

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

# ==== M · verify-mirror.sh's refusals ==========================================================

mutant "M01 ★★★ a missing checkout path is accepted — the || cd idiom that caused the row" "$V" \
  '[ -d "$DEST" ] || die "the public checkout path '"'"'$DEST'"'"' is not a directory." \' \
  '[ -d "$DEST" ] || true && false || die "unreachable" \' "$T"

mutant "M02 ★★★ the wrong REPOSITORY is accepted" "$V" \
        '*) die "'"'"'$DEST'"'"' does not point at ${EXPECTED_REPO}." \' \
        '*) : ; die "unreachable" \' "$T"

mutant "M03 ★★★ an AHEAD checkout is accepted — unpublished local commits ship in the release PR" "$V" \
  '        die "'"'"'$DEST'"'"' is AHEAD of origin — its ${DEFAULT_BRANCH} carries commits the remote does not." \' \
  '        echo "note: ahead" >&2 || die "x" \' "$T"

mutant "M04 ★★★ a DIVERGED checkout is accepted — the human state is rewritten, not reported" "$V" \
  '        die "'"'"'$DEST'"'"' has DIVERGED from origin — neither ${DEFAULT_BRANCH} is an ancestor of the other." \' \
  '        echo "note: diverged" >&2 || die "x" \' "$T"

mutant "M05 ★★★ a DIRTY mirror is accepted — SYNC-CHECKOUT-FALLBACK's root cause, unrefused" "$V" \
  'if [ -n "$DIRTY" ]; then' \
  'if false; then' "$T"

mutant "M06 ★★★ NOT-FETCHED collapses into DIVERGED — the alarming word for the routine problem" "$V" \
  '    if ! git cat-file -e "${REMOTE_SHA}^{commit}" 2>/dev/null; then' \
  '    if false; then' "$T"

# ==== B · the default branch, derived once ======================================================

mutant "B01 ★★★ the derivation falls back to guessing main — RELEASE-SH-HARDCODES-main, restored" "$V" \
  'if [ -z "$DEFAULT_BRANCH" ]; then
    die "could not derive' \
  'if [ -z "$DEFAULT_BRANCH" ]; then
    DEFAULT_BRANCH="main"
fi
if false; then
    die "could not derive' "$T"

mutant "B02 ★★★ sync-public.sh guesses main again instead of consuming the one derivation" "$S" \
  'DEFAULT_BRANCH="${MOKATA_MIRROR_DEFAULT_BRANCH:-}"' \
  'DEFAULT_BRANCH="${MOKATA_MIRROR_DEFAULT_BRANCH:-}"
DEFAULT_BRANCH="main"' "$T"

# ==== F · the fast-forward, which is where the hazard lived =====================================

mutant "F01 ★★★ the FETCH failure is best-effort again" "$S" \
  'if ! _FF_ERR="$(git fetch -q origin "$DEFAULT_BRANCH" 2>&1)"; then' \
  'if false; then' "$T"

mutant "F02 ★★★ the FAST-FORWARD failure is best-effort again" "$S" \
  'if ! _FF_ERR="$(git merge --ff-only -q "origin/${DEFAULT_BRANCH}" 2>&1)"; then' \
  'if false; then' "$T"

mutant "N01 ★★★ the checkout mask comes back — git's own diagnosis is discarded again" "$S" \
  'if ! _CO_ERR="$(git checkout -q "$DEFAULT_BRANCH" 2>&1)"; then' \
  'if ! _CO_ERR="$(git checkout -q "$DEFAULT_BRANCH" 2>/dev/null)"; then' "$T"

# ==== C · the polarity control =================================================================

mutant "C01 ★★★ verify-mirror refuses EVERYTHING — M01-M06's polarity control" "$V" \
  'cd "$DEST" || die "could not enter '"'"'$DEST'"'"'."' \
  'cd "$DEST" || true; die "refusing unconditionally"' "$T"

printf '\n================================================================================\n'
printf 'STAGE 02 ONE-MIRROR-ONE-TRUTH MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a row whose fix does not grade):\n%s' "$survivors"
else
    printf 'Every mutant was caught.\n'
fi
printf '================================================================================\n'
[ "$ran" -eq "$TOTAL" ] || exit 70
