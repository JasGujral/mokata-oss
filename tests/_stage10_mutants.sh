#!/usr/bin/env bash
# Drives the stage-10 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage10_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run — count it and
# carry on; any harness failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately
# leaves the target mutated and everything graded after it would be graded against a dirty tree.
# (Re-implements _run_mutants.sh's copy — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84, not fixed
# here.)
#
# ⚠ §7i IS THE ENTIRE RISK OF THIS STAGE. Every action reference in all nine workflows was
# ALREADY correctly SHA-pinned before stage 10 and still is, so the sweep's real-tree assertion
# passes over a corpus with zero offenders — which grades nothing. This batch is what makes it
# mean something, in two layers:
#
#   LAYER 1 (T01-T09) plants a tag pin in EACH OF THE NINE WORKFLOWS, one at a time. Eight of
#     them had NO generic SHA-pin coverage of any kind before this stage; T06 targets the one job
#     that did (release.yml's `pypi`) and is therefore the SUBSUMPTION PROOF for deleting
#     test_stage68_supply_chain.py::test_pypi_actions_are_sha_pinned.
#
#   LAYER 2 (S01-S07) attacks the sweep's own logic, because a sweep that catches a planted tag
#     for the wrong reason is still broken. These prove the ref shape, the local/docker
#     exemptions, the job-level `uses:` walk, multi-file coverage, and — S06 — that the
#     missing-PyYAML path RAISES rather than reporting a clean tree.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
W=.github/workflows
P=tests/_workflow_pins.py
T='test_s10_workflow_pins.py'

CHECKOUT='actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1'
CHECKOUT_TAG='actions/checkout@v7.0.1'

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
                exit 70 ;;
    esac
}

# ==== LAYER 1 — a tag pin planted in each of the nine real workflows ===========================
# The eight that had NO generic coverage before this stage:

# ci.yml carries three identical checkout steps, so the anchor takes the unique job env above it.
CI_OLD=$(cat <<'EOF'
      NEO4J_PASSWORD: mokatatest1
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
EOF
)
CI_NEW=$(cat <<'EOF'
      NEO4J_PASSWORD: mokatatest1
    steps:
      - uses: actions/checkout@v7.0.1
EOF
)
mutant "T01 ci.yml — a step drops to a tag pin" "$W/ci.yml" "$CI_OLD" "$CI_NEW" "$T"

mutant "T02 codeql.yml — a step drops to a tag pin" "$W/codeql.yml" \
  'github/codeql-action/init@e0647621c2984b5ed2f768cb892365bf2a616ad1 # v4.37.2' \
  'github/codeql-action/init@v4.37.2' "$T"

mutant "T03 docs.yml — a step drops to a tag pin" "$W/docs.yml" \
  'actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128 # v5.0.0' \
  'actions/deploy-pages@v5.0.0' "$T"

mutant "T04 scorecard.yml — a step drops to a tag pin" "$W/scorecard.yml" \
  'ossf/scorecard-action@4eaacf0543bb3f2c246792bd56e8cdeffafb205a # v2.4.3' \
  'ossf/scorecard-action@v2.4.3' "$T"

mutant "T05 quality-at-scale.yml — a step drops to a tag pin" "$W/quality-at-scale.yml" \
  "$CHECKOUT" "$CHECKOUT_TAG" "$T"

mutant "T07 embeddings-leg.yml — a step drops to a tag pin" "$W/embeddings-leg.yml" \
  "$CHECKOUT" "$CHECKOUT_TAG" "$T"

mutant "T08 live-db-legs.yml — a step drops to a tag pin" "$W/live-db-legs.yml" \
  "$CHECKOUT" "$CHECKOUT_TAG" "$T"

mutant "T09 real-crg.yml — a step drops to a tag pin" "$W/real-crg.yml" \
  "$CHECKOUT" "$CHECKOUT_TAG" "$T"

# ★ THE SUBSUMPTION PROOF. This is the one job the deleted bespoke pin
# (test_stage68_supply_chain.py::test_pypi_actions_are_sha_pinned) covered. If the new sweep
# does not go RED here, that pin must NOT be deleted.
mutant "T06 release.yml pypi job — SUBSUMPTION PROOF for the deleted bespoke pin" "$W/release.yml" \
  'pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1' \
  'pypa/gh-action-pypi-publish@v1.14.1' "$T"

# ==== LAYER 2 — the sweep's own logic =========================================================

mutant "S01 SHA shape accepts UPPERCASE hex (a ref git will not resolve)" "$P" \
  'SHA40 = re.compile(r"^[0-9a-f]{40}$")' \
  'SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")' "$T"

mutant "S02 SHA shape accepts an ABBREVIATED sha (collidable, and resolved at run time)" "$P" \
  'SHA40 = re.compile(r"^[0-9a-f]{40}$")' \
  'SHA40 = re.compile(r"^[0-9a-f]{7,40}$")' "$T"

S03_OLD=$(cat <<'EOF'
            if isinstance(job.get("uses"), str):
                found.append(ActionRef(name, "jobs.%s" % job_id, job["uses"]))
EOF
)
S03_NEW=$(cat <<'EOF'
            if False:
                found.append(ActionRef(name, "jobs.%s" % job_id, job["uses"]))
EOF
)
mutant "S03 job-level \`uses:\` (reusable workflows) no longer walked" "$P" \
  "$S03_OLD" "$S03_NEW" "$T"

S04_OLD=$(cat <<'EOF'
        if self.kind == KIND_DOCKER:
            return self.ref.startswith("sha256:")
EOF
)
S04_NEW=$(cat <<'EOF'
        if self.kind == KIND_DOCKER:
            return True
EOF
)
mutant "S04 a mutable docker tag counts as pinned" "$P" "$S04_OLD" "$S04_NEW" "$T"

mutant "S05 the corpus walk stops after the first file" "$P" \
  '        n for n in os.listdir(corpus_dir) if n.endswith((".yml", ".yaml"))))' \
  '        n for n in os.listdir(corpus_dir) if n.endswith((".yml", ".yaml"))))[:1]' "$T"

# ★ §7g. The whole point of the fail-loud requirement: without PyYAML the sweep must not be able
# to report a clean tree. This mutant makes it degrade the way the suite's other 17 PyYAML call
# sites do — quietly — and the pins must catch that.
S06_OLD=$(cat <<'EOF'
    except ImportError as exc:                                  # pragma: no cover - proven by test
        raise MissingParser(
            "PyYAML is required to sweep workflow action pins and is not installed. This is a "
            "HARD FAILURE, not a skip: skipping would report OK while every action pin in "
            ".github/workflows/ went unchecked. Install it (requirements/ci.txt)."
        ) from exc
EOF
)
S06_NEW=$(cat <<'EOF'
    except ImportError:
        return None
EOF
)
mutant "S06 missing PyYAML degrades QUIETLY instead of raising (§7g)" "$P" \
  "$S06_OLD" "$S06_NEW" "$T"

mutant "S07 everything is treated as a local action (nothing needs a pin)" "$P" \
  '        if uses.startswith("./") or uses.startswith("../"):' \
  '        if True:' "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 10 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
