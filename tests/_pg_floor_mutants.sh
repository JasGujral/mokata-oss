#!/usr/bin/env bash
# Stage 20 (PG-FLOOR-RATIFIED-NOWHERE-BUT-THE-ADR) mutant batch — driven through scripts/mutate.sh,
# the ONLY sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_pg_floor_mutants.sh
#
# THE CONTRACT BELOW IS NOT RE-DERIVED HERE. Every line from the pipefail setting down to the
# summary is `tests/_run_mutants.sh`'s, transplanted verbatim, because that driver is the one
# `tests/_mutant_driver_contract.py` grades and a hand-written copy is exactly the
# `MUTANT-DRIVER-CONTRACT-DUPLICATED` shape that row already names. What is this stage's is the
# TARGET, the TOTAL, and the mutant list.
#
# WHAT THE BATCH IS DESIGNED AROUND. The tracked tree is CLEAN of stale floor claims the moment
# this stage lands, so `test_every_tracked_floor_claim_states_the_declared_floor` — the assertion
# the whole guard exists for — passes by saying nothing. That is the same position
# `tests/test_stage16_doc_gate.py` was in, and the same answer applies: the mutants below are
# killed by the SYNTHETIC cases in `TestTheDetectorIsNotVacuous` and by the planted-exemption
# cases, not by the clean-corpus sweep. A batch killable only by the clean sweep would be grading
# the tree, not the instrument.
#
# ⚠ M06 and M07 ARE THE DELIBERATE EXCEPTIONS — the deny-list going slack IS caught by the clean
# corpus, because the history it stops excluding is real and stale on purpose. They are included
# so the split between the two halves is MEASURED rather than asserted.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# The mutator is overridable for the SAME reason `PYTHON` is: so this driver's own self-tests can
# drive all call sites against a stub returning a chosen exit code in milliseconds, instead of
# running that many real mutation passes. `mutate.sh`'s tests already use this idiom
# (`MUTATE_LOCKFILE`, `MUTATE_TESTS_DIR`, `MUTATE_LOCK_IMPL`). Real runs get the default.
M="${MUTATE_SH:-scripts/mutate.sh}"
# The one subject this stage added, and the pattern that grades it.
C=tests/_pg_floor.py
T='test_pg_floor_drift.py'

# The number of `mutant` call sites below. Pinned against the real count by
# `test_mutant_batch_driver.TestTheAccountingCannotDrift`, because "3 of 8 never ran" is only true
# if 8 is true — a batch that overstates its own list is the silent-truncation shape doc 85 warns
# about, wearing the costume of an honest abort.
TOTAL=14

ran=0
red=0
green=0
survivors=""

abort() {
    local rc="$1" label="$2" meaning remedy
    # Every code below is one scripts/mutate.sh's EXIT CONTRACT documents; that header is the
    # single source and this is its consumer. `test_mutant_batch_driver` fails if this dispatch
    # ever explains a code the contract does not define.
    case "$rc" in
        1) meaning="USAGE OR ENVIRONMENT — a bad argument, or a target that could not be copied."
           remedy="  Nothing was touched. Fix the invocation for this mutant and re-run." ;;
        2) meaning="LOCK MISCONFIGURED — MUTATE_LOCK_IMPL is not auto|flock|pidfile, or flock is absent."
           remedy="  Nothing was touched. Fix MUTATE_LOCK_IMPL and re-run." ;;
        3) meaning="THE MUTATION COULD NOT BE APPLIED — the pattern matched no site, or several."
           remedy="  The tree is RESTORED, so nothing is contaminated. This mutant's old/new strings
  have drifted from the source they describe; re-derive them and re-run." ;;
        4) meaning="REFUSED RESTORE — something wrote to the target while the run held it snapshotted."
           remedy="  ★ THE WORKING TREE IS STILL MUTATED, on purpose. mutate.sh refused to restore so
  that the other edit survived, and left BOTH copies: the target as they wrote it, and the
  pristine source beside it as <file>.REFUSED-RESTORE*.bak. Its message above names the file
  and both hashes.
  REMEDY: diff the target against its .REFUSED-RESTORE*.bak, keep whichever content you want,
  delete the .bak, check 'git diff' says what you expect, and only then re-run the batch." ;;
        5) meaning="LOCK BUSY — another mutate.sh run holds the mutation lock."
           remedy="  Nothing was touched. Two runs interleaving is exactly how one run's snapshot gets
  restored over the other's work, so this one refused to start rather than queue.
  REMEDY: wait for the other run to report, or kill it and remove its lockfile, then re-run." ;;
        6) meaning="NO VERDICT — the mutation applied but the test run produced nothing to grade."
           remedy="  The tree is RESTORED. Either the test pattern matched no files, or discovery never
  reported at all. Either way nothing was exercised, so this mutant is UNGRADED — it is not a
  survivor. Fix the pattern (or the tests dir) and re-run." ;;
        7) meaning="THE BASELINE WAS NOT GREEN — the selected tests were ALREADY FAILING, unmutated."
           remedy="  Nothing was mutated and nothing was graded. This is the one status that condemns the
  WHOLE BATCH rather than this mutant: every later mutant would find the same failure, be scored
  RED, and credit the kill to a mutation that had nothing to do with it — and a batch of those
  reports a PERFECT SCORE. That is what 0.0.17 stage 28's clean sweep turned out to be.
  REMEDY: make the selected tests pass on an unmutated tree, then re-run the batch." ;;
        *) meaning="UNKNOWN STATUS $rc — not in scripts/mutate.sh's EXIT CONTRACT."
           remedy="  Treat the tree as SUSPECT: check 'git status' and 'git diff' before doing anything
  else, and reconcile this status with the contract in scripts/mutate.sh's header." ;;
    esac
    # stdout, not stderr — see the header. A batch log that captures only stdout must not be able
    # to end mid-list looking like a completed run.
    printf '\n'
    printf '================================================================================\n'
    printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
    printf '  mutant: %s\n' "$label"
    printf '  status: %s\n' "$meaning"
    printf '%s\n' "$remedy"
    printf '\n'
    printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
        "$((TOTAL - ran))" "$TOTAL"
    printf '  A harness failure means NO VERDICT was produced and the tree may not hold what this\n'
    printf '  run believes it holds, so every grade after this point would have been read off a\n'
    printf '  tree nothing can vouch for.\n'
    printf '================================================================================\n'
    exit "$rc"
}

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    # stdout is captured so the verdict can be COUNTED and so a status-0 run carrying no verdict
    # is noticed — then re-emitted immediately, so the batch log reads exactly as it always did.
    # stderr is deliberately NOT captured: mutate.sh's REFUSED-RESTORE and LOCK-BUSY blocks go
    # straight to the terminal, where they are the first thing the reader needs.
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi

    if [ "$rc" -ne 0 ]; then abort "$rc" "$label"; fi

    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      # Status 0 is a CLAIM that a verdict exists. If the mutator ever drifts — a new
                # branch that prints something else and falls off the end at 0 — counting that
                # line as a graded mutant is the very laundering every hole in mutate.sh's header
                # turned out to be. 70 rather than a mutate.sh code: this is the driver's finding.
                printf '\n'
                printf 'BATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n' "$label"
                printf '  said  : %s\n' "${out:-<nothing at all>}"
                printf '  Exit 0 means "a RED or GREEN verdict was produced" (scripts/mutate.sh, EXIT\n'
                printf '  CONTRACT). This run claimed one and did not produce it, so it is UNGRADED and\n'
                printf '  the contract the rest of this batch relies on no longer holds.\n'
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

# ---------------------------------------------------------------------------------------------
# WHAT COUNTS AS A CLAIM — the comparator set is the single most load-bearing decision in the
# detector. Widen it and the suite's own fixtures become violations; narrow it and a real stale
# sentence walks past. Both directions are mutated, because one `_MIN` mutant could be killed by
# either half and would not say which.
# ---------------------------------------------------------------------------------------------

mutant "M01 a bare > counts as a floor comparator — a length check becomes a violation" "$C" \
  '_MIN = r"(?:>=|≥|&ge;)"' \
  '_MIN = r"(?:>=|≥|&ge;|>)"' "$T"

mutant "M02 the unicode spelling stops counting — every ≥15 site goes unread" "$C" \
  '_MIN = r"(?:>=|≥|&ge;)"' \
  '_MIN = r"(?:>=|&ge;)"' "$T"

mutant "M03 the postgres token widens to anything — the window matches unrelated prose" "$C" \
  '_PG = r"(?:postgres(?:ql)?|pg)"' \
  '_PG = r"(?:postgres(?:ql)?|pg|p)"' "$T"

# ---------------------------------------------------------------------------------------------
# THE DECLARED FLOOR — read, never typed. A guard that defaults when it cannot read the
# declaration grades the corpus against a number nobody declared and reports a clean sweep for it.
# ---------------------------------------------------------------------------------------------

mutant "M04 an unreadable declaration answers a default instead of None — §7g collapsed" "$C" \
  '    except OSError:
        return None' \
  '    except OSError:
        return (15, 17)' "$T"

mutant "M05 a MISSING declaration is treated as found — the single source stops being required" "$C" \
  '    if lo is None or hi is None:
        return None' \
  '    if lo is None and hi is None:
        return None' "$T"

# ---------------------------------------------------------------------------------------------
# SCOPE — the deny-list is the guard'"'"'s only hole, so both of its failure directions are mutated:
# a list that stops biting (history reds) and a list that swallows the corpus (nothing is read).
# ---------------------------------------------------------------------------------------------

mutant "M06 the deny-list stops biting — dated history is graded as a live claim" "$C" \
  '    if rel in HISTORY_FILES:
        return False' \
  '    if rel in HISTORY_FILES:
        return True' "$T"

mutant "M07 the prefix half of the deny-list is dropped — docs/build/ reds on its own provenance" "$C" \
  '    return not any(rel.startswith(prefix) for prefix in HISTORY_PREFIXES)' \
  '    return True' "$T"

mutant "M08 scan ignores scope entirely — the deny-list is decorative" "$C" \
  '        if not in_scope(rel):
            continue' \
  '        if False:
            continue' "$T"

# ---------------------------------------------------------------------------------------------
# THE EXPIRY CHECK — an exemption that outlives its subject is a permanent hole wearing a
# justification, and an expiry check that always answers "nothing stale" is the same hole with a
# green tick on it. This is the mutant a clean tree cannot kill.
# ---------------------------------------------------------------------------------------------

mutant "M09 the exact-path half is never walked — those exemptions become permanent" "$C" \
  '    for rel in sorted(HISTORY_FILES):' \
  '    for rel in sorted({}):' "$T"

mutant "M10 an INERT exemption reads as live — only the absent half is reported" "$C" \
  '        if not (text and claims(text)):
            stale.append((rel, INERT, "tracked, but carries no floor claim to exempt"))' \
  '        if not text:
            stale.append((rel, INERT, "tracked, but carries no floor claim to exempt"))' "$T"

# ---------------------------------------------------------------------------------------------
# CLAIM vs INSTANTIATION — the distinction the whole design rests on. Collapsing it in either
# direction produces a guard that is confidently wrong about a correct pin.
# ---------------------------------------------------------------------------------------------

mutant "M11 an image tag is read as a floor claim — a correct pin becomes a violation" "$C" \
  '_IMAGE_TAG = re.compile(r"\b(?:postgres|pgvector)[:/](?:pgvector:)?(?:pg)?(\d{2})\b", re.I)' \
  '_IMAGE_TAG = re.compile(r"\b(?:postgres|pgvector)[:/](?:pgvector:)?(?:pg)?(\d{2})\b", re.I)
_PATTERNS = _PATTERNS + (_IMAGE_TAG,)' "$T"

mutant "M12 the tag detector stops finding tags — a PG14 image ships unnoticed" "$C" \
  '        match = _IMAGE_TAG.search(line)' \
  '        match = None' "$T"

# ---------------------------------------------------------------------------------------------
# THE THREE-STATE BASIS — the half of this guard that a dev-tree-only run cannot grade at all,
# because in the dev tree NOTHING is absent. It was added after running the suite as SHIPPED, in
# a mirror simulation, where `docs/build/` is absent by design and a two-state check went red.
# Both directions of the collapse are mutated: the bases merging, and the internal partition
# widening to forgive every absence.
# ---------------------------------------------------------------------------------------------

mutant "M13 ABSENT and INERT collapse into one basis — §7g inside the expiry check" "$C" \
  '            stale.append((rel, ABSENT, "not a tracked path in this checkout"))' \
  '            stale.append((rel, INERT, "not a tracked path in this checkout"))' "$T"

mutant "M14 every exemption is declared internal — a vanished SHIPPING path is forgiven" "$C" \
  '            if not ships}' \
  '            if ships or not ships}' "$T"

printf '\n'
printf '================================================================================\n'
printf 'BATCH COMPLETE — %s/%s mutants ran, every one produced a verdict · %s RED · %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf '\n'
    printf '  %s SURVIVOR(S) — the mutation was NOT caught. Each is a real finding about a real\n' "$green"
    printf '  pin, and needs a look: either the pin is weak, or the mutated line is unreachable on\n'
    printf '  the path these tests drive (which wants an input that reaches it, not a deleted guard).\n'
    printf '%s' "$survivors"
fi
printf '================================================================================\n'
