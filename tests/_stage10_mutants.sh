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

# ==== DERIVED PINS — NOTHING BELOW MAY HAND-TYPE A SHA =======================================
# A mutant's `old` string must be present in the target EXACTLY ONCE or mutate.sh refuses it
# (exit 3) and this batch aborts at that mutant, grading nothing after it. A hand-typed pin is
# therefore correct only until the next dependabot bump — and it does not announce that it has
# expired, it just takes the batch out on the next run, which may be weeks later and in the
# middle of a release cut. That is not hypothetical; it had happened THREE times before these
# helpers existed — and S06 below is a FOURTH, from the same cause without a pin in it
# (see MUTATION-FIXTURE-QUOTES-SOURCE-IT-DOES-NOT-OWN, doc 84):
#
#   T01  ci.yml's anchor named a `NEO4J_PASSWORD` job env deleted at 0.0.18 stage 14
#   T02  codeql-action pinned v4.37.2; dependabot had already moved the tree to v4.37.6
#   T06  gh-action-pypi-publish pinned v1.14.1; dependabot moved the tree to v1.14.2
#
# So the pins are READ OUT OF THE WORKFLOW at run time. A bump now changes what the mutant plants
# and nothing else; only the action being REMOVED or un-pinned can break these, and that aborts
# with a named reason rather than a bare "pattern occurs 0 times".
derive_pin() {   # <workflow> <action> -> "<action>@<40-hex> # v<ver>"
    local f="$1" action="$2" pin
    pin="$(grep -o -m1 "$action@[0-9a-f]\{40\} # v[0-9][0-9.]*" "$f")"
    if [ -z "$pin" ]; then
        printf '\nBATCH ABORTED — cannot derive the %s pin from %s.\n' "$action" "$f" >&2
        printf '  The action was removed, renamed, or is no longer SHA-pinned with a `# v<ver>`\n' >&2
        printf '  comment. Fix the mutant to name what the workflow actually uses. DO NOT paste a\n' >&2
        printf '  literal pin back in — that is the defect these helpers exist to prevent.\n' >&2
        exit 70
    fi
    printf '%s\n' "$pin"
}
tag_of() {       # "<action>@<40-hex> # v<ver>" -> "<action>@v<ver>"  (the tag pin to plant)
    printf '%s@v%s\n' "${1%%@*}" "${1##* # v}"
}

CHECKOUT="$(derive_pin "$W/ci.yml" 'actions/checkout')"            || exit 70
CHECKOUT_TAG="$(tag_of "$CHECKOUT")"
CODEQL="$(derive_pin "$W/codeql.yml" 'github/codeql-action/init')" || exit 70
PAGES="$(derive_pin "$W/docs.yml" 'actions/deploy-pages')"         || exit 70
SCORECARD="$(derive_pin "$W/scorecard.yml" 'ossf/scorecard-action')" || exit 70
PYPI="$(derive_pin "$W/release.yml" 'pypa/gh-action-pypi-publish')"  || exit 70

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

# ci.yml carries three identical checkout steps, so the anchor needs the two lines above one of
# them to be unique. DERIVED, for the same reason the pins are: the previous anchor hand-typed a
# `NEO4J_PASSWORD: mokatatest1` job env, stage 14 deleted the second graph DB along with that
# job, and T01 — the FIRST mutant — silently became unapplyable, so from 2026-08-15 every run of
# this batch aborted at mutant 1 of 16 and 15 mutants never ran again.
CI_OLD="$(awk -v pin="$CHECKOUT" '
    { b3 = b2; b2 = b1; b1 = $0 }
    !done && index($0, pin) { print b3; print b2; print b1; done = 1 }
' "$W/ci.yml")"
if [ -z "$CI_OLD" ]; then
    printf '\nBATCH ABORTED — no checkout step found in %s to anchor T01 on.\n' "$W/ci.yml" >&2
    exit 70
fi
# Same three lines, last one dropped to a tag pin. Built by substitution so the anchor and the
# mutation cannot drift apart the way two hand-typed heredocs could.
CI_NEW="${CI_OLD%"$CHECKOUT"}$CHECKOUT_TAG"
mutant "T01 ci.yml — a step drops to a tag pin" "$W/ci.yml" "$CI_OLD" "$CI_NEW" "$T"

mutant "T02 codeql.yml — a step drops to a tag pin" "$W/codeql.yml" \
  "$CODEQL" "$(tag_of "$CODEQL")" "$T"

mutant "T03 docs.yml — a step drops to a tag pin" "$W/docs.yml" \
  "$PAGES" "$(tag_of "$PAGES")" "$T"

mutant "T04 scorecard.yml — a step drops to a tag pin" "$W/scorecard.yml" \
  "$SCORECARD" "$(tag_of "$SCORECARD")" "$T"

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
  "$PYPI" "$(tag_of "$PYPI")" "$T"

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
#
# DERIVED, and not for the pins' reason — for a WIDER one. The block this mutant replaces used to
# be pasted here verbatim, message text and all, so 0.0.18 stage 2 broke it merely by REWORDING
# the exception: the raise still raised, the property still held, and the mutant that proves it
# stopped applying on 2026-08-12. Nothing about the sweep was wrong. A fixture that quotes source
# it does not own is coupled to that source's PROSE, which is the least stable thing in it — so
# the block is read out of the file by its structure (`except ImportError` through `) from exc`)
# and the wording is never named here at all.
S06_RC=0
S06_OLD="$(awk '
    /^[[:space:]]*except ImportError/ { inblk = 1 }
    inblk                             { print }
    inblk && /\) from exc[[:space:]]*$/ { found = 1; exit }
    END { if (!found) exit 1 }
' "$P")" || S06_RC=$?
# Both arms matter: awk exits 1 if it never saw the closing `) from exc` (so what it printed runs
# to EOF and is NOT the block), and an empty capture means it never saw the `except` at all.
if [ "$S06_RC" -ne 0 ] || [ -z "$S06_OLD" ]; then
    printf '\nBATCH ABORTED — could not read the `except ImportError ... ) from exc` block out of\n' >&2
    printf '  %s to build S06. The fail-loud path this mutant attacks has been\n' "$P" >&2
    printf '  restructured; re-derive it, do not paste the block back in.\n' >&2
    exit 70
fi
# Indent taken from the block itself, so S06 does not assume the function's nesting depth either.
S06_INDENT="${S06_OLD%%[! ]*}"
S06_NEW="${S06_INDENT}except ImportError:
${S06_INDENT}    return None"
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
