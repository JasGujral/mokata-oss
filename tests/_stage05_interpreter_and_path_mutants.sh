#!/usr/bin/env bash
# 0.0.21 stage 05 `the-interpreter-and-the-path`, graded. Through scripts/mutate.sh (§7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_stage05_interpreter_and_path_mutants.sh
#
# Three rows, and the mutants are grouped by which one they attack:
#
#   P01-P05 ★★★ THE REFUSAL (`NO-COMMAND-PROVISIONS-...`). P01/P02 drop a check. P03 is the one
#     that matters most: `os._exit` back to `SystemExit`, which is what my FIRST version did and
#     which unittest's bare `except:` swallows into `Ran 1 test … FAILED (errors=1)` — a number,
#     per module, i.e. the defect reproduced by its own fix. P04 removes the flush, so the refusal
#     is silent as well as abrupt. P05 un-wires it from `_support`, where §7i lives.
#   T01-T02 ★★★ THE TIDY PATH (`THE-PREFLIGHT-VENV-IS-NOT-ON-ITS-OWN-PATH`), both legs.
#   T03     ★★★ the `local -x`, i.e. the PATH edit escaping into the rest of the cut.
#   H01-H05 ★★★ THE HOSTILE LEG (`NOTHING-PROVES-...`): the call site, the shim's wrongness, the
#     shim's position, the fail-open check, and the derived module list going stale.
#   C01     ★★★ the polarity control — the refusal fires on a PROVISIONED interpreter, which
#     would make every assertion above true of a tree nobody can test.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

V=tests/_provisioning.py
S=tests/_support.py
R=scripts/release.sh
L=tests/_hostile_path_modules.txt
T='test_a31_the_interpreter_and_the_path.py'

TOTAL=15
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

# ==== P · the refusal ==========================================================================

mutant "P01 ★★★ PyYAML is no longer checked — 116 errors come back as a broken tree" "$V" \
  '    if not has_yaml:
        missing.append(NEED_YAML)' \
  '    if False:
        missing.append(NEED_YAML)' "$T"

mutant "P02 ★★★ the console scripts are no longer checked — the gates report OFF, rightly, about the wrong thing" "$V" \
  '    if not all(s in stems for s in _SCRIPTS):' \
  '    if False:' "$T"

mutant "P03 ★★★ os._exit back to SystemExit — unittest swallows it and prints a COUNT (my first fix)" "$V" \
  '    (halt or os._exit)(2)' \
  '    raise SystemExit(2)' "$T"

mutant "P04 ★★★ the flush goes — os._exit skips buffers, so the refusal dies MUTE as well as abrupt" "$V" \
  '    try:
        out.flush()
    except Exception:                                  # noqa: BLE001
        pass' \
  '    pass' "$T"

mutant "P05 ★★★ the refusal is UN-WIRED from _support — §7i, a perfect guard nothing calls" "$S" \
  '_provisioning.refuse_unless_provisioned()' \
  'pass  # _provisioning.refuse_unless_provisioned()' "$T"

mutant "C01 ★★★ an UNREADABLE script dir reads as PRESENT — the instrument fails open (§7e)" "$V" \
  '    except OSError:
        present = set()' \
  '    except OSError:
        present = set(_SCRIPTS)' "$T"

mutant "C02 ★★★ no script dir at all collapses into MISSING — two facts, one representation (§7g)" "$V" \
  '        return ProvisioningResolution(UNDECIDABLE_NO_SCRIPT_DIR, missing, scripts_dir)' \
  '        return ProvisioningResolution(MISSING, missing, scripts_dir)' "$T"

# ==== T · the tidy PATH ========================================================================

mutant "T01 ★★★ run_test_preflight stops exporting its venv's bin — the gate grades a HYBRID again" "$R" \
  '    export PATH="${venv}/bin:$PATH"' \
  '    : # export PATH' "$T"

mutant "T02 ★★★ the public-subset leg stops exporting its venv's bin — the same defect one function over" "$R" \
  '  export PATH="${venv}/bin:$PATH"
  # Run the shipped suite FROM the subset' \
  '  : # export PATH
  # Run the shipped suite FROM the subset' "$T"

mutant "T03 ★★★ the PATH edit is no longer function-local — it outlives a venv already deleted" "$R" \
  '  local -x PATH="$PATH"      # function-local AND exported — see run_test_preflight
  tmp="$(mktemp -d)"; venv="${tmp}/venv"; shim="${tmp}/shim"' \
  '  tmp="$(mktemp -d)"; venv="${tmp}/venv"; shim="${tmp}/shim"' "$T"

# ==== H · the hostile leg ======================================================================

mutant "H01 ★★★ the cut never CALLS the hostile leg — §7i, a leg nothing invokes" "$R" \
  'run_hostile_path_preflight
run_public_subset_preflight' \
  'run_public_subset_preflight' "$T"

mutant "H02 ★★★ the shim answers with the REAL version — the arrangement is no longer hostile" "$R" \
  'if [ "\${1:-}" = "--version" ]; then echo "${m} ${HOSTILE_SHIM_VERSION}"; exit 0; fi' \
  'if [ "\${1:-}" = "--version" ]; then echo "${m} $VER"; exit 0; fi' "$T"

mutant "H03 ★★★ the shim goes BEHIND the venv — present on PATH, never reached, green for nothing" "$R" \
  '  export PATH="${shim}:${venv}/bin:$PATH"' \
  '  export PATH="${venv}/bin:${shim}:$PATH"' "$T"

mutant "H04 ★★★ the did-the-shim-take check goes — a fixture that silently missed reports success" "$R" \
  '    echo "REFUSING TO RELEASE: the hostile-path shim did not take, so this leg grades nothing." >&2' \
  '    echo "warning: shim not verified" >&2' "$T"

mutant "H05 ★★★ a resolution module is DROPPED from the leg list — the leg covers less and says nothing" "$L" \
  'test_hook_resolve
test_hook_shell_agnostic' \
  'test_hook_shell_agnostic' "$T"

printf '\n================================================================================\n'
printf 'STAGE 05 INTERPRETER-AND-PATH MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a row whose fix does not grade):\n%s' "$survivors"
else
    printf 'Every mutant was caught.\n'
fi
printf '================================================================================\n'
[ "$ran" -eq "$TOTAL" ] || exit 70
