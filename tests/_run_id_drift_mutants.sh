#!/usr/bin/env bash
# Drives the mutant list in `_run_id_drift_mutants.txt` through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_run_id_drift_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit.
#
# ⚠ THE FIFTH COPY OF THAT CONTRACT — MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). `_run_mutants.sh`,
# `_stage16_mutants.sh`, `_stage17_mutants.sh` and `_stage21_mutants.sh` each carry their own.
# Recording the fifth instance is what stops "we'll unify it later" from being said a fifth time.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

R=src/mokata/run_resolver.py              # THE resolver — the ladder, the refusal, the badge filter
V=src/mokata/cli_commands/runviews.py     # the writing surface #44 was reported on
P=src/mokata/progress.py                  # the display surface + B-LIFE retirement
S=src/mokata/mcp/server.py                # eager registration
G=src/mokata/session_registry.py          # sticky pid
T=src/mokata/tdd_state.py                 # the declared inverse of state_dir

D='test_run_id_drift.py'                  # this stage's own pins
B='test_b_badge_session_scope.py'         # the badge's display filter
L='test_b_life.py'                        # ship retirement (display, not ladder)
R1='test_review_fix_r1.py'                # the verdict key's session-awareness
RE='test_re_entry.py'                     # the evidence rung / the pin
MC='test_r_mcp_self_registration.py'      # the hook's narrowing + its no-extra-read budget

TOTAL=22
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

# ---- the LADDER'S ORDERING — the design itself. Each of these pairs is argued in the module ----
# ---- docstring as load-bearing, so each must be gradable rather than merely asserted. ---------

mutant "M01 EVIDENCE goes inert — the re-entry shape falls to OWN / live-narrowing" "$R" \
  '        evidence = _evidence_runs(root)' '        evidence = set()' "$RE"

mutant "M02 OWN fires even with two genuine pipelines — R1's refusal becomes a window pick" "$R" \
  '        if not evidence:' '        if True:' "$D"

mutant "M03 BOUND goes inert — the session binding stops meaning anything" "$R" \
  '        bound = _bound_run(root, session_id)' '        bound = None' "$R1"

mutant "M04 the PIN stops short-circuiting — an explicit human instruction is ignored" "$R" \
  '        pinned = os.environ.get(PIN_ENV, "").strip()' '        pinned = ""' "$RE"

mutant "M05 SINGLE drops below LIVE — the gate hook's no-extra-registry-read budget is spent" "$R" \
  '        if len(candidates) == 1:' '        if False:' "$MC"

# ---- THE REFUSAL. The whole defect was picking where narrowing could not narrow. ---------------

mutant "M06 ambiguity degrades back into a PICK — OSS #44, restored" "$R" \
  '        return RunResolution(None, BASIS_AMBIGUOUS, tuple(sorted(candidates)))' \
  '        return RunResolution(sorted(candidates)[0], BASIS_AMBIGUOUS, tuple(sorted(candidates)))' "$D"

mutant "M07 live-narrowing takes the first of MANY survivors instead of demanding exactly one" "$R" \
  '        if len(survivors) == 1:' '        if len(survivors) >= 1:' "$D"

mutant "M08 the unshipped narrowing picks instead of narrowing" "$R" \
  '        unshipped = _unshipped(root, candidates)' \
  '        unshipped = _unshipped(root, candidates)[:1]' "$D"

mutant "M09 the evidence rung picks between two pipelines" "$R" \
  '        if len(evidence) == 1:' '        if len(evidence) >= 1:' "$D"

# ---- CLASS 1: the two absences must not collapse into one another ------------------------------

mutant "M10 'I cannot tell which' becomes indistinguishable from 'there is no run'" "$R" \
  '        return self.basis == BASIS_AMBIGUOUS' '        return False' "$D"

mutant "M11 the refusal stops naming the candidates — a remedy nobody can act on" "$R" \
  '        shown = ", ".join(r[:8] for r in res.candidates[:5])' '        shown = ""' "$D"

# ---- the BADGE's display filter: it may SUBTRACT, it may never SUBSTITUTE ----------------------

mutant "M12 the badge wears a dead, unattached run — B-BADGE's live report, restored" "$R" \
  '        if not res.attached and not _is_live(root, res.run_id):' '        if False:' "$B"

mutant "M13 the badge stops retiring a shipped run — B-LIFE's live report, restored" "$R" \
  '        if _run_is_shipped(root, res.run_id):' '        if False:' "$L"

mutant "M14 every basis reads as ATTACHED — the filter admits an inference as if it were a fact" "$R" \
  '        return self.basis in ATTACHED_BASES' '        return True' "$B"

# ---- the OWN rung claims only a run it really owns ---------------------------------------------

mutant "M15 OWN claims this process's minted id even with NO state on disk" "$R" \
  '    return rid if rid in candidates else None' '    return rid' "$D"

mutant "M16 the OWN rung goes inert — five bare live runs become unresolvable again" "$R" \
  '            own = _own_run(candidates)' '            own = None' "$D"

# ---- the WRITING surface refuses rather than guessing ------------------------------------------

mutant "M17 the stage mark stamps into a guessed run — the writing half of #44" "$V" \
  '        if res.ambiguous:' '        if False:' "$D"

# ---- B-LIFE retirement stayed OUT of the ladder and IN the display -----------------------------

mutant "M18 progress reports a finished run as the current one" "$P" \
  '        if rid is not None and rid in _shipped_run_ids(store, rid):' '        if False:' "$L"

# ---- eager MCP registration --------------------------------------------------------------------

mutant "M19 registration goes back to lazy — the un-used window is invisible to narrowing" "$S" \
  '    _register_this_window(getattr(args, "path", ".") or ".")' '    pass' "$D"

# ---- sticky pid ---------------------------------------------------------------------------------

mutant "M20 a sibling stomps a LIVE owner's pid — the window is pruned as a corpse" "$G" \
  '    return prev_pid if isinstance(prev_pid, int) and pid_alive(prev_pid) else own_pid' \
  '    return own_pid' "$D"

mutant "M21 stickiness never yields — a restarted pinned session can never re-register" "$G" \
  '    return prev_pid if isinstance(prev_pid, int) and pid_alive(prev_pid) else own_pid' \
  '    return prev_pid if isinstance(prev_pid, int) else own_pid' "$D"

# ---- the declared inverse of state_dir ----------------------------------------------------------

mutant "M22 root recovery guesses a root for any directory at all" "$T" \
  '    if len(parts) <= len(tail) or tuple(parts[-len(tail):]) != tail:' '    if False:' "$D"

printf '\n================================================================================\n'
printf 'RUN-ID-DRIFT mutation batch — %s of %s graded: %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf '\nSURVIVORS (a finding about a pin, not a harness failure):\n%s' "$survivors"
    exit 0
fi
printf 'No survivors.\n'
printf '================================================================================\n'
