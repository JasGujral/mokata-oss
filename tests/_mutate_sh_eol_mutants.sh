#!/usr/bin/env bash
# Drives the 0.0.29 D3 `MUTATE-SH-WINDOWS-NEWLINE` mutants through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_mutate_sh_eol_mutants.sh
#
# ⚠ IT GRADES THE MUTATOR WITH THE MUTATOR, so — exactly as `_stage18c_mutants.sh` — it mutates a
# COPY: bash reads a running script lazily from a byte offset, so rewriting `scripts/mutate.sh`
# under the instance doing the grading corrupts that run. The graders
# (`test_mutation_harness.TestTheMutantKeepsTheTargetsLineEndings`) are pointed at the copy with
# MUTATE_SH_UNDER_TEST. `$M` is the mutator this driver RUNS; `$SUBJECT_SRC` is what it copies.
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD. Satisfies tests/_mutant_driver_contract.py.
#
#   M1  the apply writes TEXT mode            RED on Windows only (LF->CRLF translation there); a
#                                             KNOWN SURVIVOR on macOS/Linux (no translation) — the
#                                             Windows cell is graded on the mirror probe matrix.
#   M2  the read is TEXT mode (universal NL)  RED everywhere: a CRLF target is rewritten LF.
#   M3  a non-UTF-8 target decoded leniently  RED everywhere: the refusal test.
#   M4  the CRLF explanation is dropped       RED everywhere: a multi-line pattern on a CRLF target
#                                             must say CRLF, not just "occurs 0 times".
#   M5  the `shasum` arm hashes BY NAME again RED where `shasum` is the hasher (macOS, Linux): a
#                                             backslash in the path escapes the hash (`\<hex>`). A
#                                             KNOWN SURVIVOR on the Windows runner, whose Git Bash
#                                             has no `shasum` (measured: probe run 43416005900).
#   M6  the `sha256sum` arm hashes BY NAME    RED on Windows only — the one host that takes that arm
#                                             (every path there holds a backslash; probe 43413469846
#                                             was this exact failure). A KNOWN SURVIVOR elsewhere.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-$ROOT/scripts/mutate.sh}"
SUBJECT_SRC="$ROOT/scripts/mutate.sh"
PY="${PYTHON:-python3}"
export PYTHON="$PY"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/eol-mutants.XXXXXX" 2>/dev/null)" || WORK=""
if [ -z "$WORK" ] || [ ! -d "$WORK" ]; then
    printf '\nHARNESS FAILURE — this driver could not create its own work directory.\n'
    printf '  NOTHING IS WRONG WITH THE TREE and nothing was graded. The fault is in this script.\n'
    printf '  Exit 76 means the DRIVER broke; exit 75 means the SUBJECT was red. They are not the same.\n'
    exit 76
fi
COPY="$WORK/mutate.sh"
cp "$SUBJECT_SRC" "$COPY"
chmod +x "$COPY"
trap 'rm -rf "$WORK"' EXIT INT TERM
export MUTATE_SH_UNDER_TEST="$COPY"

T='test_mutation_harness.py'

TOTAL=6
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
BASE_LOG="$WORK/baseline.log"
PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
    -p "$T" -k TestTheMutantKeepsTheTargetsLineEndings > "$BASE_LOG" 2>&1
BASE_RC=$?
tail -5 "$BASE_LOG"
if [ "$BASE_RC" -ne 0 ]; then
    printf '\nBATCH REFUSED — the graded suite is not green before mutant 1 (exit %s).\n' "$BASE_RC"
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    exit 75
fi
printf 'Baseline GREEN (exit %s). Grading %s mutants.\n\n' "$BASE_RC" "$TOTAL"

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

mutant "M1 ★★ the apply writes text mode (RED on Windows only)" "$COPY" \
  'with open(path, "wb") as fh:
    fh.write(mutated)' \
  'with open(path, "w", encoding="utf-8") as fh:
    fh.write(mutated.decode("utf-8"))' "$T"

mutant "M2 ★★ the read is text mode — universal newlines turn a CRLF target LF" "$COPY" \
  'raw = open(path, "rb").read()' \
  'raw = open(path, encoding="utf-8", errors="surrogateescape").read().encode("utf-8", "surrogateescape")' "$T"

mutant "M3 ★★ a non-UTF-8 target is decoded leniently and rewritten" "$COPY" \
  '    source = raw.decode("utf-8")' \
  '    source = raw.decode("utf-8", "replace")' "$T"

mutant "M4 ★ the CRLF explanation is dropped from a multi-line refusal" "$COPY" \
  'if found == 0 and "\n" in old and b"\r\n" in raw:' \
  'if False:' "$T"

mutant "M5 ★★ the snapshot is hashed by NAME (a backslash path escapes the hash)" "$COPY" \
  '    sha256_of() { shasum -b -a 256 < "$1" | cut -d'"'"' '"'"' -f1; }' \
  '    sha256_of() { shasum -a 256 "$1" | cut -d'"'"' '"'"' -f1; }' "$T"

mutant "M6 ★★ the sha256sum arm hashes by NAME (RED on Windows only)" "$COPY" \
  '    sha256_of() { sha256sum -b < "$1" | cut -d'"'"' '"'"' -f1; }' \
  '    sha256_of() { sha256sum "$1" | cut -d'"'"' '"'"' -f1; }' "$T"

printf '\n================================================================================\n'
printf 'MUTATE.SH EOL MUTANT BATCH — %s of %s ran · %s RED · %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'INCOMPLETE — %s mutants never ran.\n' "$((TOTAL - ran))"
    printf '================================================================================\n'
    exit 71
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (the pin did NOT catch these):\n%s' "$survivors"
    printf '  (KNOWN by host — see the header: off Windows M1 and M6 survive; on Windows M5 does)\n'
    printf '================================================================================\n'
    exit 72
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
