#!/usr/bin/env bash
# Drives the branch-protection THREE-STATE mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_branch_protection_states_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated, and on exit 7 the baseline was not green so nothing after it is attributable.
# (Driver copied from _stage29_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# WHAT THIS BATCH IS FOR. The brief that commissioned the change said it in one line: "the
# degraded path is the whole risk, so pin it hardest." A third state that can pass is a new way
# for a release to proceed, and the only honest question to ask of the tests guarding it is
# whether they GRADE. So every REFUSAL in `branch_protection.py` is neutered here in turn, one at
# a time, and every one must red something. A survivor in section B or C is not a style note — it
# is a path on which a release could proceed with no evidence at all.
#
# ⚠ THE LIMIT, STATED BEFORE THE SCORE (§7i). This grades the DECISION LOGIC against an injected
# `gh`. It does not and cannot grade what GitHub actually returns during an outage: the 503 that
# started this is reproduced from its message text, not from the API. What that costs is narrow
# and worth naming — if GitHub ever answers an unprotected branch with something OTHER than
# `Branch not protected`, the state-2/state-3 split moves, and nothing here would notice. The
# split is deliberately fail-safe in that direction (an unrecognised error becomes state 3, which
# must then EARN its pass from corroboration) and section D grades that direction explicitly.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
BP=src/mokata/branch_protection.py

T='test_tm_s12a_branch_protection.py'

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

# ==== A. ★★ THE DEFECT ITSELF, REPLAYED — collapse three states back into two =================
# A01 is the bug that was fixed, expressed as a mutation. If it survives, the whole change is
# decorative and this file is the only thing that would have said so.

mutant "A01 ★★ UNREADABLE collapses back into NOT PROTECTED — the §7g defect, restored" "$BP" \
  '    return _corroborate(run, repo, branch, error)' \
  '    return ProtectionVerdict(repo=repo, branch=branch, ok=False,
                             state=STATE_NOT_PROTECTED, failures=[error])' "$T"

mutant "A02 ★ every non-zero read is treated as a STATEMENT that protection is absent" "$BP" \
  '    return "branch not protected" in (error or "").lower()' \
  '    return True' "$T"

mutant "A03 ★ nothing is ever a statement — a real 404 stops carrying the apply remedy" "$BP" \
  '    return "branch not protected" in (error or "").lower()' \
  '    return False' "$T"

# ==== B. ★★ THE DEGRADED PATH MUST EARN ITS PASS — neuter each refusal in turn =================
# Each mutant below is "a failed read means fine", planted at a different level. This is the exact
# bug being fixed, re-admitted one layer down, which is the way it would actually come back.

mutant "B01 ★★ a FAILED corroborating branch read passes anyway" "$BP" \
  '    if not b_ok:
        return refuse([f"and the corroborating read of {branch_path} ALSO failed: {b_err} — a "
                       "failed read is not a pass"])' \
  '    if False:
        return refuse([f"and the corroborating read of {branch_path} ALSO failed: {b_err} — a "
                       "failed read is not a pass"])
    b_payload = b_payload if isinstance(b_payload, dict) else {"protected": True}' "$T"

mutant "B02 ★★ a FAILED corroborating rulesets read passes anyway" "$BP" \
  '    if not r_ok:
        return refuse([f"and the corroborating read of {rulesets_path} ALSO failed: {r_err} — a "
                       "failed read is not a pass"])' \
  '    if False:
        return refuse([f"and the corroborating read of {rulesets_path} ALSO failed: {r_err} — a "
                       "failed read is not a pass"])
    r_payload = r_payload if isinstance(r_payload, list) else []' "$T"

mutant "B03 ★★ protected == false is accepted as corroboration" "$BP" \
  '    if protected is not True:' \
  '    if False:' "$T"

mutant "B04 ★ a truthy-but-not-True protected value is accepted (the is-not-True narrowing dropped)" "$BP" \
  '    if protected is not True:' \
  '    if not protected:' "$T"

mutant "B05 ★ ruleset consistency stops being required" "$BP" \
  '    if not consistent:' \
  '    if False:' "$T"

mutant "B06 ★ an unenforced ruleset counts as consistent" "$BP" \
  '        if enforcement != "active":' \
  '        if False:' "$T"

mutant "B07 ★ a non-array rulesets response counts as consistent" "$BP" \
  '    if not isinstance(payload, list):
        return False, ["the rulesets response was not a JSON array"]' \
  '    if not isinstance(payload, list):
        return True, []' "$T"

mutant "B08 ★ the corroborating branch payload need not be an object" "$BP" \
  '    if not isinstance(b_payload, dict):
        return refuse([f"the corroborating read of {branch_path} was not a JSON object"])' \
  '    if not isinstance(b_payload, dict):
        b_payload = {"protected": True}' "$T"

# ==== C. ★ A READ FAILURE MUST NEVER SUGGEST A WRITE ===========================================
# The second defect. Put the destructive remedy back on the read-failure path and on the degraded
# path; both must red.

mutant "C01 ★★ the unreadable refusal reuses the NOT-PROTECTED render — the PUT comes back" "$BP" \
  '        return self._render_degraded() if self.ok else self._render_unreadable_refusal()' \
  '        return self._render_degraded() if self.ok else self._render_not_protected()' "$T"

mutant "C02 ★ the degraded notice grows a PUT remedy" "$BP" \
  '        lines.append(f"  This exemption EXPIRES with: {RESTORE_ROW}")' \
  '        lines.append("  fix: gh api -X PUT repos/o/r/branches/main/protection --input -")
        lines.append(f"  This exemption EXPIRES with: {RESTORE_ROW}")' "$T"

# ==== D. ★ THE DEGRADED PASS IS LOUD, DATED, AND DISTINGUISHABLE FROM A GREEN ==================

mutant "D01 ★★ the degraded verdict renders as the ordinary PASS line — a SILENT exemption" "$BP" \
  '        return self._render_degraded() if self.ok else self._render_unreadable_refusal()' \
  '        return self._render_pass() if self.ok else self._render_unreadable_refusal()' "$T"

mutant "D02 ★ the degraded notice stops naming which assurances were not obtained" "$BP" \
  '        for a in UNOBTAINED_ASSURANCES:
            lines.append(f"    ✗ {a}")' \
  '        lines.append("    (details omitted)")' "$T"

mutant "D03 ★ the degraded pass exits 0 — indistinguishable from a real green to release.sh" "$BP" \
  '        return EXIT_DEGRADED if self.ok else EXIT_UNREADABLE' \
  '        return EXIT_PROTECTED if self.ok else EXIT_UNREADABLE' "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'BRANCH-PROTECTION STATE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH ABORTED — %s ran but this driver declares TOTAL=%s.\n' "$ran" "$TOTAL"
    exit 70
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
