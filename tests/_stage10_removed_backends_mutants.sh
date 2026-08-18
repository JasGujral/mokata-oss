#!/usr/bin/env bash
# Drives the 0.0.18 stage-10 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage10_removed_backends_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Driver shape from _stage9_removal_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED,
# doc 84 — and it satisfies tests/_mutant_driver_contract.py, which is a live pin.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing",
# and every RED it prints is unattributable.
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. `sync-public.sh` excludes docs/build/, so a
# driver filed beside its report is ABSENT from the public mirror and a contributor grading this
# repo would run an incomplete corpus that READS as complete.
#
# WHAT THE STAGE MUST NOT BE ABLE TO LOSE, one group per mechanism. Note that A is the SMALLEST
# group and the deletion is what it grades: this stage is not mostly a deletion, it is mostly the
# refusal that stops the deletion from misrepresenting a user's data.
#   A. the removal itself          the backends are gone, and the probe can SEE they are gone
#   B. the refusal (data present)  the whole of doc 85 §7d's ONE EXCEPTION, in code
#   C. the notice  (no data)       the other side of the §7g split — loud, once, and NOT a raise
#   D. the disk surface            TOOL_CATALOG → .mokata/manifest.json, and detection
#   E. the parse floor             an OLD manifest must still LOAD, or the remedy is unrunnable
#   F. the src class guard         the axis `notice_pins` never ranged over
#   G. the widened sweeps          the two blind spots this stage found in stage 9's own guards
#   H. what SURVIVES               deletion is graded by what is left, not by what left

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

SEL=src/mokata/memory/selection.py
DEP=src/mokata/deprecation.py
DET=src/mokata/detect.py
SCH=src/mokata/schema.py
PRO=src/mokata/profiles.py
DR=tests/_deprecation_removal.py

T10='test_stage10_removed_backends.py'
T9='test_stage9_removal_release.py'
TSP='test_simp_s2_shim_parity.py'
TCF='test_close_fix_approve_list.py'

TOTAL=35
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T10" "$T9" "$TSP" "$TCF"; do
    if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
            -p "$pat" >/dev/null 2>&1; then
        printf '\nBATCH REFUSED — %s is not green before mutant 1.\n' "$pat"
        printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
        exit 75
    fi
    printf 'baseline GREEN: %s\n' "$pat"
done
printf 'Grading %s mutants.\n\n' "$TOTAL"

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

# ==== A. the removal itself ======================================================================

mutant "A01 ★★ the two channels are still ANNOUNCED as deprecated — a notice for a dead thing" "$DEP" \
  'REMOVED: Dict[str, RemovedNotice] = {' \
  'CHANNELS["obsidian"] = CHANNELS["neo4j"]
REMOVED: Dict[str, RemovedNotice] = {' "$T10"

mutant "A02 ★★ the REMOVED registry is empty — nothing is a removed channel any more" "$DEP" \
  'REMOVED_CHANNELS = tuple(REMOVED)' \
  'REMOVED.clear()
REMOVED_CHANNELS = tuple(REMOVED)' "$T10"

mutant "A03 ★★ a removed channel is REACHABLE again under its own name" "src/mokata/memory/backends.py" \
  '# -------------------------------------------------------------------------- postgres' \
  'ObsidianBackend = SQLiteBackend
# -------------------------------------------------------------------------- postgres' "$T10"

# ==== B. the refusal, when the data is THERE ====================================================

mutant "B01 ★★★ THE DEFECT ITSELF — the chain resolves past the removed backend in SILENCE" "$SEL" \
  '    _refuse_removed_memory_chain(router, root)' \
  '    pass' "$T10"

mutant "B02 ★★ data behind the channel is a NOTICE, not a refusal — scroll past it and lose it" "$SEL" \
  '        if detail:
            raise deprecation.RemovedChannelError(
                deprecation.removed_notice(tool).render(detail=detail))
        deprecation.warn_removed(tool, root)' \
  '        deprecation.warn_removed(tool, root)' "$T10"

mutant "B03 ★★ the refusal stops naming WHERE the notes are" "$SEL" \
  '            raise deprecation.RemovedChannelError(
                deprecation.removed_notice(tool).render(detail=detail))' \
  '            raise deprecation.RemovedChannelError(
                deprecation.removed_notice(tool).render())' "$T10"

mutant "B04 ★★ an EXPLICIT ask falls to the empty floor instead of refusing" "$SEL" \
  '    if tool in deprecation.REMOVED:
        # An EXPLICIT ask for a removed backend' \
  '    if False:
        # An EXPLICIT ask for a removed backend' "$T10"

mutant "B05 ★★ the evidence probe never finds a note — every repo reads as empty" "$SEL" \
  '    if not notes:
        return ""' \
  '    if True:
        return ""' "$T10"

mutant "B06 ★★ a directory that EXISTS counts as data, notes or not — the refusal cries wolf" "$SEL" \
  '        notes = [fn for fn in os.listdir(vault) if fn.endswith(".md")]' \
  '        notes = list(os.listdir(vault)) or ["x"]' "$T10"

mutant "B07 ★★ an unreadable vault reads as data — a permissions error becomes a hard refusal" "$SEL" \
  '    except OSError:
        return ""' \
  '    except OSError:
        return "(unreadable)"' "$T10"

mutant "B08 ★★ the CONFIGURED vault is ignored — the refusal names a directory the user never used" "$SEL" \
  '    vault = (config or {}).get("vault")
    return os.path.expanduser(vault) if vault else os.path.join(memory_dir_for(root), "vault")' \
  '    return os.path.join(memory_dir_for(root), "vault")' "$T10"

mutant "B09 ★★ native-memory INVENTS a location — evidence fabricated on a user surface" "$SEL" \
  '    if tool != "obsidian":
        return ""' \
  '    if False:
        return ""' "$T10"

mutant "B10 ★★ the refusal loses the remedy — a user is told what broke and not what to do" "$DEP" \
  '                f"will NOT hand you an empty store instead. {self.remedy}")' \
  '                f"will NOT hand you an empty store instead.")' "$T10"

mutant "B11 ★★ the remedy names THIS release — the one that cannot run the migration" "$DEP" \
  'LAST_SHIPPING_RELEASE = last_release_with(REMOVAL_RELEASE)' \
  'LAST_SHIPPING_RELEASE = REMOVAL_RELEASE' "$T10"

mutant "B12 ★ a .0 patch silently invents a predecessor instead of refusing" "$DEP" \
  '    if parts[-1] < 1:
        raise ValueError(' \
  '    if False:
        raise ValueError(' "$T10"

mutant "B13 ★★ a duck-typed router RAISES instead of refusing nothing (degrade-clean lost)" "$SEL" \
  '    try:
        chain = router.manifest.fallback_order("memory_store")
    except (ManifestError, AttributeError):
        return' \
  '    chain = router.manifest.fallback_order("memory_store")' "$T10"

# ==== C. the notice, when there is NO data =====================================================

mutant "C01 ★★ a stale chain entry with nothing behind it now HARD-FAILS every command" "$SEL" \
  '        deprecation.warn_removed(tool, root)' \
  '        raise deprecation.RemovedChannelError(deprecation.removed_notice(tool).render())' "$TSP"

mutant "C02 ★★★ THE MARKER COLLAPSE — the removal notice is keyed on the channel alone, so every
       repo that already saw the 0.0.17 DEPRECATION warn is silent about the removal" "$DEP" \
  '    marker = _marker_path(mokata_dir, "%s@removed-%s" % (channel, notice.removed))' \
  '    marker = _marker_path(mokata_dir, channel)' "$T10"

mutant "C03 ★★ the notice fires on EVERY read — a diagnostic becomes a nag" "$DEP" \
  '    except FileExistsError:
        return False
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`' \
  '    except FileExistsError:
        pass
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`' "$T10"

mutant "C04 ★ an unwritable marker dir CRASHES the read path instead of degrading" "$DEP" \
  '    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`' \
  '    except FileNotFoundError:
        return False                              # degrade-clean, exactly as `warn_deprecated`' "$T10"

mutant "C05 ★★ after the notice the resolution RAISES anyway — the split collapses" "$SEL" \
  '    if tool in deprecation.REMOVED:
        # We reach here ONLY past' \
  '    if False:
        # We reach here ONLY past' "$T10"

# ==== D. the disk surface, and detection =======================================================

mutant "D01 ★★★ THE CATALOG ENTRY RETURNS — init_repo writes a removed backend into every
       new repo's .mokata/manifest.json, which is stage 9's finding as a whole tool" "$PRO" \
  '    "postgres": {
        # Opt-in hosted/remote memory backend.' \
  '    "obsidian": {
        "provides": "memory_store", "kind": "external", "version": None,
        "detect": {"type": "obsidian"},
    },
    "postgres": {
        # Opt-in hosted/remote memory backend.' "$T10"

mutant "D02 the CAPABILITY registry lists it again — the declared fallback order for memory" "$PRO" \
  '        "fallback": ["sqlite"],' \
  '        "fallback": ["obsidian", "sqlite"],' "$TSP"

mutant "D02b the full PROFILE wires it again — and this is the one build_manifest_data reads,
       so it is what lands in a new repo on disk" "$PRO" \
  '            "code_graph": ["code-review-graph", "serena", "ast", "ripgrep", "grep"],
            "memory_store": ["sqlite"],
        },
    },
    "custom": {' \
  '            "code_graph": ["code-review-graph", "serena", "ast", "ripgrep", "grep"],
            "memory_store": ["obsidian", "sqlite"],
        },
    },
    "custom": {' "$T10"

mutant "D03 ★★ the detect strategy comes back to LIFE and starts finding vaults again" "$SCH" \
  'KNOWN_DETECT_TYPES = ("command", "python_module", "path", "always",' \
  'KNOWN_DETECT_TYPES = ("command", "python_module", "path", "obsidian", "always",' "$T10"

mutant "D04 ★ detection stops being TOTAL — an unknown strategy raises instead of reading absent" "$DET" \
  '        # Unknown strategy -> treat as absent (the manifest validator rejects these,
        # but detection must still be total and never throw).
        return False' \
  '        raise ValueError(dtype)' "$T10"

# ==== E. the parse floor — the remedy has to be RUNNABLE ========================================

mutant "E01 ★★★ an OLD manifest stops PARSING — every command in that repo dies with 'obsidian is
       not one of [...]', INCLUDING the config set the notice tells the user to run" "$SCH" \
  'PARSEABLE_DETECT_TYPES = KNOWN_DETECT_TYPES + REMOVED_DETECT_TYPES' \
  'PARSEABLE_DETECT_TYPES = KNOWN_DETECT_TYPES' "$T10"

mutant "E02 ★★ the hand-written validator diverges from the jsonschema one (§7g at the parser)" "$SCH" \
  '            if dt not in PARSEABLE_DETECT_TYPES:' \
  '            if dt not in KNOWN_DETECT_TYPES:' "$T10"

# ==== F. the src-side class guard ==============================================================

mutant "F01 ★★ the src sweep stops seeing a hand-typed release — --help can rot again" "$DR" \
  '            for literal in _LITERAL_VERSION.findall(value):
                found.append((path, line, literal))' \
  '            for literal in []:
                found.append((path, line, literal))' "$T10"

mutant "F02 ★★ an exemption becomes a whole FILE — parity.py stops being swept at all" "$DR" \
  '            if any(fragment in value for fragment in exempt_here):' \
  '            if exempt_here:' "$T10"

mutant "F03 ★★ a stale exemption never reports — it grants a pass to nothing, silently" "$DR" \
  '            if text is None or not any(fragment in value for value in present):
                stale.append((path, fragment))' \
  '            if False:
                stale.append((path, fragment))' "$T10"

mutant "F04 ★★ docstrings become values — prose about a historical release convicts the module" "$DR" \
  '            and id(node) not in docstrings]' \
  '            and True]' "$T10"

# ==== G. the two blind spots this stage found in stage 9's OWN guards ==========================

mutant "G01 ★★★ notice_pins re-anchors — the SEVENTH pin (a release inside a --help sentence)
       goes invisible again, which is how it survived the stage built to catch it" "$DR" \
  '_BARE_RELEASE = re.compile(r"\d+\.\d+(?:\.\d+)+")' \
  '_BARE_RELEASE = re.compile(r"^\d+\.\d+(?:\.\d+)+$")' "$T9"

mutant "G02 ★★★ the docs sweep reverts to TWO phrasings — eight published pages carrying a passed
       release read as a clean corpus, exactly as they did through 0.0.17" "$DR" \
  '_MENTION = re.compile(r"remov\w*(?:[^\d\n]|[\s>]*\n[\s>]*){0,40}?(\d+\.\d+(?:\.\d+)+)", re.I)' \
  '_MENTION = re.compile(r"REMOVED\s+in\s+mokata[\s>]*(\d+(?:\.\d+)+)"
                      r"|removal:\s*(\d+(?:\.\d+)+)", re.I)' "$T9"

# ==== H. what SURVIVES =========================================================================

mutant "H01 ★★ the SQLite floor stops being the answer for an unknown tool" "$SEL" \
  '    # "ripgrep"/unknown, or unavailable -> SQLite floor
    return floor()' \
  '    raise deprecation.RemovedChannelError("no")' "$T10"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-10 REMOVED-BACKEND MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
