#!/usr/bin/env bash
# Jas's 2026-10-02 `rm -rf` ruling, graded. Through scripts/mutate.sh (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_sync_public_no_force_delete_mutants.sh
#
# ⛔ THE RULING WAS "don't accept 'documented' as closure", so the closure has to be gradeable.
# These six attack the two halves of the remedy and the control that keeps it from over-refusing:
#
#   R01-R03 ★★★ the CONTAINMENT proof. Drop the strictly-inside case, drop the `.git` refusal,
#     drop the traversal refusal. Each is a different abort with a different message (§7g), which
#     is why there are three and not one.
#   R04-R05 ★★★ the DELETER. R04 puts `rm -rf` back, which is the ruling itself. R05 swaps
#     `rmdir` for `rm -rf`, which is the same hazard one layer in — `rmdir` is what makes the
#     catastrophic shape UNAVAILABLE rather than merely unwanted.
#   R06 ★★★ the resolver refuses EVERYTHING — the polarity control. Without it, every refusal
#     above is also true of a guard that has broken the sync.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

S=scripts/sync-public.sh
T='test_sync_public_nested_checkout.py'

TOTAL=6
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

mutant "R01 ★★★ the strictly-inside case accepts anything — containment is no longer proved" "$S" \
  '    "$PUBLIC_ROOT"/?*) ;;   # strictly inside, and at least one character deep' \
  '    *) ;;                   # strictly inside, and at least one character deep' "$T"

mutant "R02 ★★★ the mirror's own .git is no longer refused BY NAME" "$S" \
  '    "$PUBLIC_ROOT"/.git|"$PUBLIC_ROOT"/.git/*)' \
  '    __never_matches_anything__)' "$T"

mutant "R03 ★★★ a directory traversal is accepted as a path" "$S" \
  '  if [ -z "$base" ] || [ "$base" = "." ] || [ "$base" = ".." ] || [ "$base" = "/" ]; then' \
  '  if false; then' "$T"

mutant "R04 ★★★ the recursive force-delete is PUT BACK — Jas's ruling, reverted in one line" "$S" \
  '  abs="$(_resolve_under_root "$p" "$kind")" || exit 1' \
  '  abs="$p"; rm -rf -- "$p"; return' "$T"

mutant "R05 ★★★ rmdir becomes rm -rf — the hazard one layer in, where the FLOOR actually is" "$S" \
  '    rmdir -- "$d" || {' \
  '    rm -rf -- "$d" || {' "$T"

mutant "R06 ★★★ the resolver refuses EVERYTHING — R01-R03's polarity control" "$S" \
  '  parent="$(dirname -- "$p")"' \
  '  parent="$(dirname -- "$p")"; exit 1' "$T"

printf '\n================================================================================\n'
printf 'SYNC-PUBLIC NO-FORCE-DELETE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a hole in Jas 2026-10-02'"'"'s ruling):\n%s' "$survivors"
else
    printf 'Every mutant was caught.\n'
fi
printf '================================================================================\n'
[ "$ran" -eq "$TOTAL" ] || exit 70
