#!/usr/bin/env bash
# Drives the mutant list in `_stage18c_mutants.txt` through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage18c_mutants.sh
#
# ⚠ THIS BATCH GRADES THE MUTATOR WITH THE MUTATOR, so it does one thing no other batch does:
# it mutates a COPY. bash reads a script lazily from a remembered byte offset, so rewriting
# `scripts/mutate.sh` while an instance of it is executing corrupts THAT run — the one doing the
# grading. The copy is byte-identical apart from the mutation, and the tests are pointed at it
# with MUTATE_SH_UNDER_TEST. Everything else is the ordinary contract.
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED.
#
# ⚠ THE FIFTH COPY OF THAT CONTRACT — MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84) is now at five
# instances. The end state is one shared driver taking a list file; this is still not it.
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
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

# ---- DEFECT 1: the authored SET — no instant the restore cannot recognise ---------------------

mutant "M01 the pre-stage shape restored — the mutant hash is recorded AFTER the apply" "$COPY" \
  'expected_sha="$mutant_sha"

if ! mutation_step apply "$target" "$old" "$new" "$pristine_sha"; then' \
  'if ! mutation_step apply "$target" "$old" "$new" "$pristine_sha"; then
    expected_sha="$mutant_sha"' "$T"

mutant "M02 the restore accepts only the LAST written state, not the snapshot" "$COPY" \
  '    [ -n "$1" ] && { [ "$1" = "$expected_sha" ] || [ "$1" = "$pristine_sha" ]; }' \
  '    [ -n "$1" ] && [ "$1" = "$expected_sha" ]' "$T"

mutant "M03 the restore accepts only the SNAPSHOT, not the mutation it applied" "$COPY" \
  '    [ -n "$1" ] && { [ "$1" = "$expected_sha" ] || [ "$1" = "$pristine_sha" ]; }' \
  '    [ -n "$1" ] && [ "$1" = "$pristine_sha" ]' "$T"

mutant "M04 HOLE 6 WEAKENED — the restore accepts ANYTHING (a third party is overwritten)" "$COPY" \
  '    [ -n "$1" ] && { [ "$1" = "$expected_sha" ] || [ "$1" = "$pristine_sha" ]; }' \
  '    return 0' "$T"

mutant "M05 the apply stops checking the source is what the plan hashed" "$COPY" \
  'if hashlib.sha256(source.encode("utf-8")).hexdigest() != sys.argv[5]:' \
  'if False:' "$T"

mutant "M06 the plan WRITES — the two-step split collapses back to one" "$COPY" \
  'if mode == "plan":
    print(hashlib.sha256(mutated.encode("utf-8")).hexdigest())
    sys.exit(0)' \
  'if mode == "plan":
    open(path, "w", encoding="utf-8").write(mutated)
    print(hashlib.sha256(mutated.encode("utf-8")).hexdigest())
    sys.exit(0)' "$T"

# ---- DEFECT 2: a signal ENDS the run ----------------------------------------------------------

mutant "M07 the pre-stage shape restored — restore is trapped on INT/TERM directly" "$COPY" \
  'trap restore EXIT
trap '"'"'on_signal 2'"'"'  INT
trap '"'"'on_signal 15'"'"' TERM' \
  'trap restore EXIT INT TERM' "$T"

mutant "M08 the signal handler restores but does not terminate" "$COPY" \
  '    exit $((128 + $1))     # the conventional shell status for "died on signal N"' \
  '    return 0' "$T"

mutant "M09 a signalled run reports a status a batch driver reads as a VERDICT" "$COPY" \
  '    exit $((128 + $1))     # the conventional shell status for "died on signal N"' \
  '    exit 0' "$T"

mutant "M10 the EXIT trap is dropped — a signalled run exits 143 having restored nothing" "$COPY" \
  'trap restore EXIT
trap' \
  'trap' "$T"

# ---- the guards the fix must NOT buy its green with ------------------------------------------

mutant "M11 the plan/apply staleness check inverted" "$COPY" \
  'if hashlib.sha256(source.encode("utf-8")).hexdigest() != sys.argv[5]:' \
  'if hashlib.sha256(source.encode("utf-8")).hexdigest() == sys.argv[5]:' "$T"

mutant "M12 a refusal stops preserving the pristine copy" "$COPY" \
  '            mv -f "$target.bak" "$keep"' \
  '            rm -f "$target.bak"' "$T"

printf '\n================================================================================\n'
printf 'stage 18c mutants: %s of %s  |  RED %s  |  GREEN(survivors) %s\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a finding about a pin, reported, never quietly dropped:\n%s' \
        "$survivors"
fi
printf '================================================================================\n'
[ "$green" -eq 0 ]
