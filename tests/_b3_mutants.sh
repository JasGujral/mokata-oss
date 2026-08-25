#!/usr/bin/env bash
# Drives the B3 `release-tail-ungraded` mutant list through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_b3_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness failure
# STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target mutated, and on
# exit 7 the baseline was not green so nothing after it is attributable. (Driver copied from
# _b5_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# WHAT THIS BATCH IS FOR. B3 exists because a step that never ran produced no evidence at all: the
# dev tag `v0.0.18` was not mis-created, it was skipped, and every check in that cut was green. The
# mutations below are the ways this check quietly becomes that same silence again, each in its own
# section — and the brief named the first three:
#
#   A  the divergence comparison inverted or emptied     — reds on nothing, or on everything
#   B  the third state collapsed into green              — "agree" reported having not looked
#   C  the check silently skipped                        — B1's row, in the release that fixed it
#   D  the absence made invisible                        — a divergence that no longer names a side
#   E  the credential rendered                           — the one output that must never appear
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
CHK=scripts/check_tag_sets.py
REL=scripts/release.sh

T='test_b3_release_tail_ungraded.py'

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

# ==== A. ★★ THE DIVERGENCE COMPARISON — the brief's first named mutant =========================

mutant "A01 ★★ the comparison inverted — a tag present on BOTH sides is reported missing, and a
          tag present on ONE is not" "$CHK" \
  '    missing_from_dev = tuple(t for t in mirror.tags if t not in dev.tags)' \
  '    missing_from_dev = tuple(t for t in mirror.tags if t in dev.tags)' "$T"

mutant "A02 ★★ the two sides swapped — the divergence is found but blamed on the wrong repository,
          so the remedy names a tag to create where it already exists" "$CHK" \
  '    missing_from_mirror = tuple(t for t in dev.tags if t not in mirror.tags)' \
  '    missing_from_mirror = tuple(t for t in mirror.tags if t not in dev.tags)' "$T"

mutant "A03 ★★ only ONE direction is compared — a tag on the dev side and absent from the mirror
          passes, which is exactly half of this row" "$CHK" \
  '    state = DIVERGE if (missing_from_dev or missing_from_mirror) else AGREE' \
  '    state = DIVERGE if missing_from_dev else AGREE' "$T"

mutant "A04 ★ the peeled ls-remote line counted as a second tag — every annotated tag becomes two
          and the sets can never agree" "$CHK" \
  '_TAG_REF = re.compile(r"^\S+\s+refs/tags/(.+?)(\^\{\})?$")' \
  '_TAG_REF = re.compile(r"^\S+\s+refs/tags/(.+)$")' "$T"

# ==== B. ★★ THE THIRD STATE COLLAPSED INTO GREEN — the brief's second named mutant ============
# Both directions, because both are failures: an unread side must never render as a pass, and it
# must never render as a divergence either (that one sends someone to create a tag by hand).

mutant "B01 ★★ NOT_CHECKABLE admitted to the passing set — the release gate passes having not
          looked, which is the defect this release has fixed five times" "$CHK" \
  'PASSING = (AGREE,)' \
  'PASSING = (AGREE, NOT_CHECKABLE)' "$T"

mutant "B02 ★★ the third state decided AFTER the comparison — an unread side beside an empty one
          answers AGREE, because nothing is missing from nobody" "$CHK" \
  '    unreadable = tuple(side.label for side in (dev, mirror) if not side.readable)
    if unreadable:
        return DivergenceReport(NOT_CHECKABLE, dev, mirror, unreadable=unreadable)

    missing_from_dev = tuple(t for t in mirror.tags if t not in dev.tags)' \
  '    unreadable = tuple(side.label for side in (dev, mirror) if not side.readable)
    if unreadable and (dev.tags or mirror.tags):
        return DivergenceReport(NOT_CHECKABLE, dev, mirror, unreadable=unreadable)

    missing_from_dev = tuple(t for t in (mirror.tags or ()) if t not in (dev.tags or ()))' "$T"

mutant "B03 ★★ an unread side becomes an EMPTY one — a failed ls-remote reads as a repository with
          no tags, §7g one level down" "$CHK" \
  '        return TagSetResult(label=label, spec=spec, tags=None,' \
  '        return TagSetResult(label=label, spec=spec, tags=(),' "$T"

mutant "B04 ★★ the third state maps onto exit 0 — three states, two exit codes" "$CHK" \
  '        return {AGREE: EXIT_AGREE, DIVERGE: EXIT_DIVERGE,
                NOT_CHECKABLE: EXIT_NOT_CHECKABLE}[self.state]' \
  '        return {AGREE: EXIT_AGREE, DIVERGE: EXIT_DIVERGE,
                NOT_CHECKABLE: EXIT_AGREE}[self.state]' "$T"

# ==== C. ★★ THE CHECK SILENTLY SKIPPED — the brief's third named mutant =======================
# ⭐ C01 IS THE ROW ITSELF. The preflight call is the ONLY thing that can observe a tag step that
# never ran; deleting it leaves a check that grades only the cuts that did not need grading.

mutant "C01 ★★ the PREFLIGHT call deleted — the tail of the previous cut is never looked at, which
          is the exact state the tree was in when v0.0.18 went missing" "$REL" \
  'verify_tag_sets "the tail of the last cut, before anything is pushed"' \
  ':' "$T"

mutant "C02 ★★ the POST-TAG call deleted — the tail is performed and not graded" "$REL" \
  'verify_tag_sets "this cut'"'"'s tail, immediately after tagging"' \
  ':' "$T"

mutant "C03 ★★ the divergence arm stops refusing — the check runs, prints, and the cut continues" \
  "$REL" \
  '      echo "  — or delete it deliberately from the repository that has it, then re-run." >&2
      exit 1' \
  '      echo "  — or delete it deliberately from the repository that has it, then re-run." >&2' "$T"

# ==== E. ★ THE CREDENTIAL RENDERED ============================================================
# This check prints the two remotes it consulted, at a cut, where a token lives.

mutant "E01 ★ the redaction removed — a tokened remote URL is printed verbatim" "$CHK" \
  '    return _USERINFO.sub(r"\1" + REDACTED + "@", spec or "")' \
  '    return spec or ""' "$T"

# ==== summary =================================================================================
printf '\n================================================================================\n'
printf 'B3 release-tail-ungraded mutants: %s ran of %s · RED %s · GREEN %s\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a path on which a skipped release tail could go unobserved:\n%s' \
        "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'No survivors.\n'
printf '================================================================================\n'
