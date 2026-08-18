#!/usr/bin/env bash
# Drives the 0.0.18 stage-9 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage9_removal_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Driver shape from _stage8_supply_chain_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED,
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
# WHAT THE STAGE MUST NOT BE ABLE TO LOSE, one group per mechanism:
#   A. src/mokata/deprecation.py       the declaration and the constant derived from it
#   B. tests/_deprecation_removal.py   THE VERDICT — the arithmetic that reds a broken promise
#   C. tests/_deprecation_removal.py   the presence probe (what "the removal happened" means)
#   D. tests/_deprecation_removal.py   the registry's coherence, both directions
#   E. docs + the sweep                the other four surfaces that carried the same false release
#   F. tests/_deprecation_removal.py   "exactly one release string in the module"
#   G. src/mokata/deprecation.py       the declaration's provenance fields
#   H. the CLASS guard                 the row named one pin; the suite held six

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

DEP=src/mokata/deprecation.py
DR=tests/_deprecation_removal.py
GRAPH=docs/how-to/use-a-codebase-graph.md
STORE=docs/how-to/configure-storage-backends.md
SESS=docs/how-to/portable-sessions.md

T9='test_stage9_removal_release.py'
TS2='test_simp_s2_deprecation.py'

TOTAL=34
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T9" "$TS2"; do
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

# ==== A. the declaration and the constant ========================================================

mutant "A01 ★★ the declaration points back at 0.0.17 — THE DEFECT, restored verbatim" "$DEP" \
  'REMOVAL_DECLARATION = "REMOVAL(set=deprecated-channels, at=0.0.18, filed=2026-08-14, owner=Jas)"' \
  'REMOVAL_DECLARATION = "REMOVAL(set=deprecated-channels, at=0.0.17, filed=2026-08-14, owner=Jas)"' "$T9"

mutant "A02 ★★ an unreadable declaration falls back to a default instead of refusing" "$DEP" \
  '        raise ValueError(
            "REMOVAL declaration carries no readable `at=<version>`: %r" % (declaration,))' \
  '        return "0.0.18"' "$T9"

mutant "A03 ★★ any text is a version — 'soon' becomes a removal release" "$DEP" \
  '_VERSION = re.compile(r"^\d+(?:\.\d+)+$")' \
  '_VERSION = re.compile(r"^.*$")' "$T9"

mutant "A04 ★ prose with no declaration is parsed as one" "$DEP" \
  '    if match is None:
        return {}' \
  '    if False:
        return {}' "$T9"

mutant "A05 ★★ the constant stops being derived — a second literal returns to the module" "$DEP" \
  'REMOVAL_RELEASE = removal_target(REMOVAL_DECLARATION)' \
  'REMOVAL_RELEASE = "0.0.18"' "$T9"

mutant "A06 ★★ a notice carries its own release again — the reversed pin's own offender" "$DEP" \
  '    removal: str = REMOVAL_RELEASE' \
  '    removal: str = "0.0.17"' "$TS2"

# ==== B. the verdict =============================================================================

mutant "B01 ★★ the promised release ARRIVING reads as pending — C2 for exactly one release" "$DR" \
  '    return REMOVAL_OVERDUE if now >= due else REMOVAL_PENDING' \
  '    return REMOVAL_OVERDUE if now > due else REMOVAL_PENDING' "$T9"

mutant "B02 ★★ overdue and pending are swapped" "$DR" \
  '    return REMOVAL_OVERDUE if now >= due else REMOVAL_PENDING' \
  '    return REMOVAL_PENDING if now >= due else REMOVAL_OVERDUE' "$T9"

mutant "B03 ★★ everything reads as landed — the tripwire is disarmed and still green" "$DR" \
  '    if not present:
        return REMOVAL_LANDED' \
  '    if True:
        return REMOVAL_LANDED' "$T9"

mutant "B04 ★★ an unreadable version launders into pending (§7g) — a typo reads as 'fine for now'" "$DR" \
  '    if now is None or due is None:
        return REMOVAL_UNREADABLE' \
  '    if now is None or due is None:
        return REMOVAL_PENDING' "$T9"

mutant "B05 ★ only the CUT version is checked for readability, never the promise" "$DR" \
  '    if now is None or due is None:' \
  '    if now is None:' "$T9"

mutant "B06 ★★ an unreadable version sorts below every real one instead of refusing" "$DR" \
  '    if not _VERSION.match((text or "").strip()):
        return None' \
  '    if not _VERSION.match((text or "").strip()):
        return ()' "$T9"

mutant "B07 ★★ releases compare as TEXT — 0.0.9 outranks 0.0.10" "$DR" \
  '    return tuple(int(part) for part in text.strip().split("."))' \
  '    return tuple(text.strip().split("."))' "$T9"

mutant "B08 ★ a single number is a version — '18' grades as a release" "$DR" \
  '_VERSION = re.compile(r"^\d+(?:\.\d+)+$")' \
  '_VERSION = re.compile(r"^\d+(?:\.\d+)*$")' "$T9"

# ==== C. what 'the removal happened' means =======================================================

mutant "C01 ★★ a module that is GONE reads as present — the removal can never be seen to land" "$DR" \
  '    if spec is None:
        return False' \
  '    if spec is None:
        return True' "$T9"

mutant "C02 ★★ the symbol half is dropped — the two memory backends read present forever" "$DR" \
  '    if not symbol:
        return True' \
  '    if True:
        return True' "$T9"

mutant "C03 ★★ a FAILED lookup reads as a removal — one broken extra retires the guard" "$DR" \
  '    except (ImportError, AttributeError, ValueError):
        return True' \
  '    except (ImportError, AttributeError, ValueError):
        return False' "$T9"

mutant "C04 ★ the probe is never consulted — every channel is present by construction" "$DR" \
  '    return frozenset(channel for channel, target in targets.items() if probe(target))' \
  '    return frozenset(targets)' "$T9"

# ==== D. the registry's coherence ================================================================

mutant "D01 ★★ a notice that has outlived its implementation is never reported" "$DR" \
  '    return tuple(sorted(frozenset(announced) - frozenset(present)))' \
  '    return ()' "$T9"

mutant "D02 ★★ the two drift directions collapse into one (§7g)" "$DR" \
  '    return (tuple(sorted(announced - targets)), tuple(sorted(targets - announced)))' \
  '    return (tuple(sorted(announced - targets)), tuple(sorted(announced - targets)))' "$T9"

# ==== E. the other four surfaces =================================================================

mutant "E01 ★★ the WRAPPED quotation stops being seen — the sweep reads clean and is blind" "$DR" \
  'r"REMOVED\s+in\s+mokata[\s>]*(\d+(?:\.\d+)+)"' \
  'r"REMOVED\s+in\s+mokata (\d+(?:\.\d+)+)"' "$T9"

mutant "E02 ★★ the admonition-title form is dropped — three of the five pages go unread" "$DR" \
  'r"|removal:\s*(\d+(?:\.\d+)+)", re.I)' \
  'r"", re.I)' "$T9"

mutant "E03 ★★ drift compares nothing — every stated release is accepted" "$DR" \
  '    return tuple(m for m in removal_mentions(docs) if m[2] != removal)' \
  '    return ()' "$T9"

mutant "E04 ★★ a published page reverts to 0.0.17 — the REAL offender, on the real tree" "$GRAPH" \
  '!!! warning "The Neo4j backend is deprecated (removal: 0.0.18)"' \
  '!!! warning "The Neo4j backend is deprecated (removal: 0.0.17)"' "$T9"

mutant "E05 ★★ the WRAPPED quotation reverts on a real page — E01's offender, not synthetic" "$STORE" \
  '    > 0.0.18. The canonical memory store is local SQLite' \
  '    > 0.0.17. The canonical memory store is local SQLite' "$T9"

mutant "E06 ★★ a THIRD page reverts — the corpus axis (§7j), not the two files this stage edited" "$SESS" \
  '(removal: 0.0.18)' \
  '(removal: 0.0.17)' "$T9"

# ==== H. the class guard — the row said ONE pin and there were SIX =============================

mutant "H01 ★★ the domain widens to every test — 31 synthetic fixtures convict, the guard is noise" "$DR" \
  '        if _DEPRECATION_VOCABULARY not in text.lower():
            continue' \
  '        if False:
            continue' "$T9"

mutant "H02 ★★ only the FIRST argument is read — assertEqual(catalog, \"0.0.17\") walks free" "$DR" \
  '            for arg in node.args[:2]:' \
  '            for arg in node.args[:1]:' "$T9"

mutant "H03 ★★ the method list loses assertEqual — the two manifest pins go unseen" "$DR" \
  'NOTICE_PIN_METHODS = ("assertIn", "assertNotIn", "assertEqual", "assertNotEqual")' \
  'NOTICE_PIN_METHODS = ("assertIn", "assertNotIn")' "$T9"

mutant "H04 ★★ a partial version matches — every literal with two dots in it is an offender" "$DR" \
  '_BARE_RELEASE = re.compile(r"^\d+\.\d+(?:\.\d+)+$")' \
  '_BARE_RELEASE = re.compile(r"\d+\.\d+")' "$T9"

mutant "H05 ★★ the second SRC copy returns — the value init writes into every manifest.json" "src/mokata/profiles.py" \
  '        "deprecated": REMOVAL_RELEASE,
    },
    "grep": {' \
  '        "deprecated": "0.0.17",
    },
    "grep": {' "test_simp_s2_shim_parity.py"

# ==== F. exactly one release string ==============================================================

mutant "G01 ★ the declaration stops saying WHO promised it and WHEN" "$DEP" \
  'REMOVAL(set=deprecated-channels, at=0.0.18, filed=2026-08-14, owner=Jas)' \
  'REMOVAL(set=deprecated-channels, at=0.0.18)' "$T9"

mutant "F01 ★★ prose becomes a value — a docstring about the old release convicts the module" "$DR" \
  '        if id(node) in docstrings or not _LITERAL_VERSION.search(node.value):' \
  '        if not _LITERAL_VERSION.search(node.value):' "$T9"

# ==== I. the tripwire is wired to the REAL version ==============================================

mutant "I01 ★★ THE 0.0.18 CUT, SIMULATED — bump __version__ to the promised release with the channels still here" "src/mokata/__init__.py" \
  '__version__ = "0.0.17"' \
  '__version__ = "0.0.18"' "$T9"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-9 REMOVAL-PROMISE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
