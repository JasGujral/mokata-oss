#!/usr/bin/env bash
# Drives the stage-2 (PYYAML-SKIP-CLUSTER) mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python bash tests/_stage2_pyyaml_mutants.sh
#
# Baseline: stage 31's hole 7 is in force. `mutate.sh` runs the selected tests against the
# PRISTINE target before applying anything and exits 7 if they were already failing, so every
# verdict below is attributable to its mutation. A 7 aborts this batch like any other non-zero.
#
# ⚠⚠ WHAT THIS BATCH IS FOR, STATED BEFORE THE SCORE (§7i/§7f).
#
# This stage's deliverable is a GUARD against checks that quietly do not run, and the real tree
# now contains zero offenders for it to find. That is exactly the shape §7i convicts: the sweep
# would pass whether or not it worked. So the mutants aim at the classifier itself, driven by the
# PLANTED offenders in `test_pyyaml_skip_cluster.py` — if a mutant blinds a detection rule and the
# suite stays green, that rule is graded by nothing and the inventory it produced is a guess.
#
# THE THREE STARRED MUTANTS ARE THE STAGE:
#   * M01 makes the sweep answer "no offenders" unconditionally — a check that cannot fail, read
#     as a check that passed (`NULLGLOB-DISARMS-THE-EXISTENCE-CHECK`, in this stage's own guard).
#   * M09 makes `safe_load` RETURN instead of RAISE when PyYAML is missing — the defect itself,
#     restored at the single site all seventeen converted call sites now funnel through.
#   * M12 blinds the CI-side sweep, which is the half stage 10 found and the half no test in the
#     suite covered before this stage.
#
# ⚠ WHAT THIS BATCH DOES NOT GRADE, named here rather than padded into arms that cannot fail:
#
#   * `read_corpus` — the one impure function. Mutating it breaks every test in the module at
#     once, so a RED says "the file reader broke", not "the guard grades". A kill with no
#     information is worse than an honest gap.
#   * `_catches_import_error`'s TUPLE and BARE-`except` branches. No fixture in the suite spells
#     `except (ImportError, ModuleNotFoundError):` or a bare `except:` around `import yaml`, and
#     no real module does either, so nothing can distinguish those branches from their absence.
#     Belt-and-braces against an idiom that has not appeared, HONESTLY UNGRADED rather than
#     quietly dropped. A fixture could be planted for them; it would grade the fixture.
#   * `Site.__eq__` / `__hash__` / `render` — presentation. `render`'s output is asserted only as
#     part of a failure message nobody reads on a green run, and a mutant of it would be scored
#     by whether the message is pretty.
#   * The ORDER of the four shapes' severity. That is a judgement recorded in prose (doc 84), not
#     a property of the code, and no mutation can grade a ranking.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M="$ROOT/scripts/mutate.sh"
export PYTHON="${PYTHON:-python3}"

S=tests/_pyyaml_sweep.py            # the sweep — the §7i-graded gate
W=tests/_workflow_pins.py           # the single representation of "the parser is absent"
T='test_pyyaml_skip_cluster.py'

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

# ==== A. the sweep can still ANSWER =============================================================

mutant "M01 ★★ the sweep reports no offenders, always — a check that cannot fail" "$S" \
  'return [s for s in sweep(corpus) if s.disposition == TOLERATE]' \
  'return []' "$T"

mutant "M02 ★ every offender is classified REFUSE — the tree reads clean because nothing is bad" "$S" \
  '    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise):
                return True' \
  '    return True
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise):
                return True' "$T"

mutant "M03 nothing counts as a refusal — the two ALREADY-CORRECT sites get convicted" "$S" \
  '            if isinstance(sub, ast.Raise):
                return True' \
  '            if isinstance(sub, ast.Raise):
                return False' "$T"

# ==== B. each detection rule, one shape at a time ===============================================

mutant "M04 the skipUnless DECORATOR rule is blinded — shape (i), 4 real sites" "$S" \
  '                if not name.startswith("skip"):
                    continue' \
  '                if True:
                    continue' "$T"

mutant "M05 the skipTest MESSAGE rule is blinded — shape (i) through a tolerant factory" "$S" \
  '                and node.func.attr == "skipTest" and _mentions_yaml(node) \
                and node.lineno not in claimed:' \
  '                and node.func.attr == "skipTest" and False \
                and node.lineno not in claimed:' "$T"

mutant "M06 ★ shape (iii) is renamed shape (ii) — the WORST shape loses its own name (§7g)" "$S" \
  '                    sites.append(Site(module, node.lineno, TOLERATE, SILENT_RETURN))' \
  '                    sites.append(Site(module, node.lineno, TOLERATE, WEAKENED))' "$T"

mutant "M07 no name is ever a yaml flag — every _HAVE_YAML conditional becomes invisible" "$S" \
  '    flags = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _guards_yaml_import(node):' \
  '    flags = set()
    return flags
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _guards_yaml_import(node):' "$T"

mutant "M08 the tolerant-HANDLER rule is blinded — the inline try/except idiom, 3 real sites" "$S" \
  '                if _returns(handler.body):' \
  '                if False:' "$T"

mutant "M09b the fallthrough shape is dropped — 'if _HAVE_YAML:' with no else reads clean" "$S" \
  '                elif not branch:
                    sites.append(Site(module, node.lineno, TOLERATE, FALLTHROUGH))' \
  '                elif not branch:
                    pass' "$T"

# ==== C. ★ the refusal itself — the single representation all 17 sites now funnel through ========

mutant "M09 ★★ safe_load RETURNS instead of RAISING — the defect restored at the shared site" "$W" \
  '        raise MissingParser(
            "PyYAML is required to " + what + " and is not installed. This is a "' \
  '        return None
        raise MissingParser(
            "PyYAML is required to " + what + " and is not installed. This is a "' "$T"

mutant "M10 ★ the refusal loses its REMEDY — a refusal pointing nowhere (§7g corollary)" "$W" \
  '            "Install it: pip install -r requirements/ci.txt"' \
  '            "Install PyYAML."' "$T"

mutant "M11 the refusal loses the CALLER PROVENANCE — every red says the same thing" "$W" \
  '            "PyYAML is required to " + what + " and is not installed. This is a "' \
  '            "PyYAML is required and is not installed. This is a "' "$T"

# ==== D. the CI side — the half stage 10 found ==================================================

mutant "M12 ★★ the CI sweep reports no parser-less job, always" "$S" \
  '            if "requirements/ci.txt" not in blob:
                offenders.append((name, job_name))' \
  '            if False:
                offenders.append((name, job_name))' "$T"

mutant "M13 ★ -p and -k stop mattering — the three CLEARED workflows get convicted" "$S" \
  '    return "-p" not in tokens and "-k" not in tokens' \
  '    return True' "$T"

mutant "M14 no invocation ever counts as a full-suite run — the CI guard grades nothing" "$S" \
  '    if not tokens or tokens[0] != "discover":
        return False' \
  '    if True:
        return False' "$T"

# ==== verdict ===================================================================================

printf '\n================================================================================\n'
printf 'STAGE 2 (PYYAML-SKIP-CLUSTER) MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
