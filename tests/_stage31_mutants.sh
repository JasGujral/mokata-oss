#!/usr/bin/env bash
# Drives the stage-31 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python bash tests/_stage31_mutants.sh
#
# ⚠ THIS BATCH GRADES THE MUTATOR WITH THE MUTATOR, so — like `_stage18c_mutants.sh` — it mutates
# a COPY. bash reads a script lazily from a remembered byte offset, so rewriting
# `scripts/mutate.sh` while an instance of it is executing corrupts THAT run, the one doing the
# grading. The copy is byte-identical apart from the mutation and the tests are pointed at it with
# MUTATE_SH_UNDER_TEST. Everything else is the ordinary contract.
#
# ⚠⚠ WHAT THIS BATCH IS FOR, STATED BEFORE THE SCORE (§7i/§7f).
#
# Stage 31 added hole 7 — the GREEN BASELINE. A baseline check that always passes is WORSE than no
# baseline at all: it converts "unverified" into "verified" in the reader's head, which is the
# `NULLGLOB-DISARMS-THE-EXISTENCE-CHECK` shape (a check that cannot fail, read as a check that
# passed) reproduced inside the very harness written to stop it. So the guard's own arms are graded
# here, not just the code around them.
#
# THE THREE STARRED MUTANTS ARE THE STAGE. B08, B09 and B10 are the PLACEMENT — they leave the
# guard fully present and working and only change WHAT IT SEES. B08 in particular reproduces the
# driver-level baseline the brief warned about: the check runs, passes, and is blind to
# `.mutate.lock`, which is the artefact stage 28's contamination actually rode in on. If B08 comes
# back GREEN, the placement is unpinned and this whole stage is decorative.
#
# ⚠ WHAT THIS BATCH DOES NOT GRADE, and it is not an omission that can be fixed by adding a mutant:
#
#   * the `^Ran ` conjunct in the abort condition. Dropping it leaves the guard firing on `FAILED`
#     alone, and `unittest` never prints a `FAILED` line without a `Ran` line above it, so no input
#     reachable from these fixtures can tell the two spellings apart. It is belt-and-braces against
#     a future runner with different output, and it is HONESTLY UNGRADED rather than quietly
#     dropped. Measured, not assumed — see the stage report.
#   * the pyc purge before the baseline. The baseline reads a PRISTINE target, so bytecode compiled
#     from that same pristine source is not stale; the purge is hygiene inherited from hole 1 and no
#     fixture here can distinguish its presence from its absence.
#   * the WINDOW between the baseline and the graded run. A failure appearing inside it is still
#     scored as a kill. That is a property of the design, stated in `mutate.sh`'s header, and no
#     mutant of this code can grade a gap the code does not claim to close.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

WORK="$(mktemp -d)"
COPY="$WORK/mutate.sh"
cp "$M" "$COPY"
chmod +x "$COPY"
trap 'rm -rf "$WORK"' EXIT INT TERM
export MUTATE_SH_UNDER_TEST="$COPY"

T='test_mutation_harness.py'

TOTAL=10
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

# The baseline's test run, quoted once. Three mutants below wrap it rather than change it, which
# is the point: the guard is untouched and only its VANTAGE moves.
BASELINE_RUN='set +e
baseline_out=$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover \
        -s "$TESTS_DIR" -t "$TESTS_DIR" -p "$pattern" 2>&1 \
      | grep -E "^(Ran|OK|FAILED)")   # HOLE 2: nothing may be written either
set -e'

# ==== A. the baseline EXISTS and actually runs =================================================

mutant "B01 ★ the baseline never runs — its result is fabricated as empty" "$COPY" \
  "$BASELINE_RUN" \
  'baseline_out=""' "$T"

mutant "B02 ★★ the guard can never fire — a check that cannot fail, read as one that passed" "$COPY" \
  'if echo "$baseline_out" | grep -qE "^Ran " && echo "$baseline_out" | grep -q FAILED; then' \
  'if false; then' "$T"

mutant "B03 the guard fires on ANY completed run — it aborts every batch, graded or not" "$COPY" \
  'if echo "$baseline_out" | grep -qE "^Ran " && echo "$baseline_out" | grep -q FAILED; then' \
  'if echo "$baseline_out" | grep -qE "^Ran "; then' "$T"

# ==== B. §7g — the third outcome keeps its own representation ==================================

mutant "B04 ★ the abort collapses into exit 6 — 'already failing' and 'nothing ran' share a code" \
  "$COPY" \
  '    exit 7
fi

# ⚠ THIS LINE MUST PRECEDE THE APPLY' \
  '    exit 6
fi

# ⚠ THIS LINE MUST PRECEDE THE APPLY' "$T"

mutant "B05 ★ the abort reports SUCCESS — a batch driver carries straight on" "$COPY" \
  '    exit 7
fi

# ⚠ THIS LINE MUST PRECEDE THE APPLY' \
  '    exit 0
fi

# ⚠ THIS LINE MUST PRECEDE THE APPLY' "$T"

mutant "B06 ★★ a VERDICT is printed for a tree that was already red — the defect itself, restored" \
  "$COPY" \
  '    echo "BASELINE!!  $label   ($baseline_summary)  <-- THE TESTS WERE ALREADY FAILING (not a verdict)"' \
  '    echo "RED   ✓  $label   ($baseline_summary)"' "$T"

mutant "B07 the abort complains but does not STOP — the mutation is applied anyway" "$COPY" \
  '    } >&2
    exit 7' \
  '    } >&2' "$T"

# ==== C. ★ THE PLACEMENT — the guard is intact and only its VANTAGE changes =====================
# These are the stage. Each leaves the check byte-for-byte correct and moves what it can see, which
# is exactly the difference between this baseline and the decorative driver-level one.

mutant "B08 ★★ THE DRIVER-LEVEL BASELINE — it runs blind to .mutate.lock, as a pre-check would" \
  "$COPY" \
  "$BASELINE_RUN" \
  'mv -f "$LOCKFILE" "$LOCKFILE.stage31-hidden"
set +e
baseline_out=$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover \
        -s "$TESTS_DIR" -t "$TESTS_DIR" -p "$pattern" 2>&1 \
      | grep -E "^(Ran|OK|FAILED)")   # HOLE 2: nothing may be written either
set -e
mv -f "$LOCKFILE.stage31-hidden" "$LOCKFILE"' "$T"

mutant "B09 ★ the baseline runs before the snapshot exists — blind to <target>.bak" "$COPY" \
  "$BASELINE_RUN" \
  'mv -f "$target.bak" "$target.stage31-hidden"
set +e
baseline_out=$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest discover \
        -s "$TESTS_DIR" -t "$TESTS_DIR" -p "$pattern" 2>&1 \
      | grep -E "^(Ran|OK|FAILED)")   # HOLE 2: nothing may be written either
set -e
mv -f "$target.stage31-hidden" "$target.bak"' "$T"

mutant "B10 ★★ the baseline grades the MUTATED tree — a 'baseline' that is just a second mutant run" \
  "$COPY" \
  'rm -f "$pycdir/$base".*.pyc          # HOLE 1 applies to the baseline too: nothing stale may be READ' \
  'mutation_step apply "$target" "$old" "$new" "$pristine_sha"
rm -f "$pycdir/$base".*.pyc          # HOLE 1 applies to the baseline too: nothing stale may be READ' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE 31 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
