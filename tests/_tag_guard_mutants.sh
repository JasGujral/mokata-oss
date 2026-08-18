#!/usr/bin/env bash
# Drives the TAG-ON-THE-DEV-REPO-IS-A-SILENT-NO-OP mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_tag_guard_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Satisfies tests/_mutant_driver_contract.py, which is a live pin since exit criterion 5.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing",
# and every RED it prints is unattributable.
#
# ⚠ THE BASELINE'S EXIT STATUS IS CAPTURED FROM THE RUN ITSELF, NEVER THROUGH A PIPE. Stage 6's
# reviewer piped a full suite through `| tail -5` and read `tail`'s status — zero regardless of
# outcome. That is THIS RELEASE'S OWN SUBJECT (an absent answer wearing a pass, §7g) appearing in
# the command used to verify it, so the run below writes to a file and `EXIT=$?` reads the run.
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. Location is part of the contract:
# scripts/sync-public.sh excludes docs/build/, so a driver filed beside its report is ABSENT from
# the public mirror and a public contributor grading this repo would run an incomplete corpus that
# READS as complete (MUTANT-DRIVERS-IN-DOCS-BUILD-DO-NOT-SHIP).
#
# FOUR GROUPS, one per surface the row named plus the sweep that reads them:
#   A. tests/_release_repo_guards.py   the sweep — every derivation the pins stand on
#   B. .github/workflows/release.yml   the inverse guard itself
#   C. scripts/release.sh              the deleted waiver, the surviving mirror gates, the recipe
#   D. src/mokata/packaging.py + cli   which package answered, and the three exit codes
#
# ⚠ C mutates an INTERNAL file. On the public mirror `scripts/release.sh` is absent and the C
# group cannot run — the batch says so rather than skipping quietly.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

RG=tests/_release_repo_guards.py
YML=.github/workflows/release.yml
SH=scripts/release.sh
PKG=src/mokata/packaging.py
CLI=src/mokata/cli_commands/core.py
T='test_tag_is_not_a_publish.py'

TOTAL=45
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
BASE_LOG="$(mktemp -t tagguard-baseline)"
PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
    -k test_tag_is_not_a_publish > "$BASE_LOG" 2>&1
BASE_RC=$?
tail -5 "$BASE_LOG"
if [ "$BASE_RC" -ne 0 ]; then
    printf '\nBATCH REFUSED — the graded suite is not green before mutant 1 (exit %s).\n' "$BASE_RC"
    printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
    rm -f "$BASE_LOG"
    exit 75
fi
rm -f "$BASE_LOG"
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

# ==== A. the sweep: every derivation the pins stand on ==========================================

mutant "A01 ★★ != is read as == — the inverse guard classifies as a publish guard" "$RG" \
  '    return (repository == literal) if operator == "==" else (repository != literal)' \
  '    return repository == literal' "$T"

mutant "A02 ★★ an unreadable guard returns False instead of raising (§7g)" "$RG" \
  '    if match is None:
        raise UnreadableGuard(expression)' \
  '    if match is None:
        return False' "$T"

mutant "A03 ★★ a job with NO if: reads as guarded — the sixth unguarded job goes quiet" "$RG" \
  '    if condition is None or str(condition).strip() == "":
        return True' \
  '    if condition is None or str(condition).strip() == "":
        return False' "$T"

mutant "A04 ★★ the refusal is filed as a publish guard — the two classes collapse" "$RG" \
  '    if on_dev and not on_mirror:
        return GUARD_REFUSAL' \
  '    if on_dev and not on_mirror:
        return GUARD_PUBLISH' "$T"

mutant "A05 ★ a job guarded to NOBODY reads as unguarded — a dead job hides in a live class" "$RG" \
  '    return GUARD_UNGUARDED if on_mirror else GUARD_NEVER_RUNS' \
  '    return GUARD_UNGUARDED' "$T"

mutant "A06 ★★ guard_classes reads no jobs — the whole workflow sweep is vacuous on empty" "$RG" \
  '    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {job_id: classify_condition(' \
  '    jobs = None
    if not isinstance(jobs, dict):
        return {}
    return {job_id: classify_condition(' "$T"

mutant "A07 ★★ every job reports no needs — a refusal that inherits a skip goes unseen" "$RG" \
  '    needs = job.get("needs") if isinstance(job, dict) else None' \
  '    needs = None' "$T"

mutant "A08 ★ a single-string needs: is not read as a dependency" "$RG" \
  '    if isinstance(needs, str):
        return (needs,)' \
  '    if False:
        return (needs,)' "$T"

mutant "A09 ★★ job_run_steps returns nothing — the refusal stops being executed at all" "$RG" \
  '    return tuple((step.get("name") or "", step["run"]) for step in steps
                 if isinstance(step, dict) and isinstance(step.get("run"), str))' \
  '    return ()' "$T"

mutant "A10 ★★ a waiver can never expire — the sunset mechanism becomes decorative" "$RG" \
  '        elif now > through:
            out.append((number, WAIVER_EXPIRED, fields))' \
  '        elif False:
            out.append((number, WAIVER_EXPIRED, fields))' "$T"

mutant "A11 ★★ an UNDATED waiver reads as live — a permanent waiver in a hat" "$RG" \
  '            out.append((number, WAIVER_UNDATED, fields))' \
  '            out.append((number, WAIVER_LIVE, fields))' "$T"

mutant "A12 ★★ no declaration is ever found — the mechanism grades an empty set (§7j)" "$RG" \
  '_WAIVER = re.compile(r"WAIVER\(([^)]*)\)")' \
  '_WAIVER = re.compile(r"WAIVERZ\(([^)]*)\)")' "$T"

mutant "A13 ★★ versions are compared as TEXT — 0.0.9 sorts after 0.0.10" "$RG" \
  '    return tuple(int(part) for part in text.strip().split("."))' \
  '    return text.strip()' "$T"

mutant "A14 ★★ any word starts a disabled call — prose about the script is convicted" "$RG" \
  '    pattern = re.compile(r"^[ \t]*(%s)[ \t]+[\"'"'"'$]" % "|".join(sorted(map(re.escape, names))))' \
  '    pattern = re.compile(r"^[ \t]*(\w+)[ \t]+[\"'"'"'$]")' "$T"

mutant "A15 ★★ disabled_calls never fires — the shape the exemption had goes unseen" "$RG" \
  '        if comment is not None and pattern.match(comment):' \
  '        if False:' "$T"

mutant "A16 ★★ code and comment stop being split — a commented call counts as a live one" "$RG" \
  '        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index], line[index + 1:]' \
  '        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line, None' "$T"

mutant "A17 ★★ the DEFINITION counts as a call site — the row's own reading mistake, in code" "$RG" \
  '    pattern = re.compile(r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b(?![ \t]*\()[ \t]*(.*)$"' \
  '    pattern = re.compile(r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b[ \t]*(.*)$"' "$T"

mutant "A18 ★ a call with no arguments is missed — the fail-closed preflight reads as uninvoked" "$RG" \
  '(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b(?![ \t]*\()[ \t]*(.*)$' \
  '(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b(?![ \t]*\()[ \t]+(\S.*)$' "$T"

mutant "A19 ★★ the command-position anchor is dropped — a name inside a string is a call" "$RG" \
  '    pattern = re.compile(r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b(?![ \t]*\()[ \t]*(.*)$"
                         % re.escape(name))' \
  '    pattern = re.compile(r"%s\b(?![ \t]*\()[ \t]*(.*)$" % re.escape(name))' "$T"

mutant "A20 ★ the first argument is not unquoted — \"\$PUB_REPO\" never equals \$PUB_REPO" "$RG" \
  '    return words[0].strip("\"'"'"'") if words else ""' \
  '    return words[0] if words else ""' "$T"

mutant "A21 ★★ the \${{ }} form stops being a guard — the template spelling reads unreadable" "$RG" \
  '    r"""^\s*(?:\$\{\{)?\s*github\.repository\s*(==|!=)\s*'"'"'([^'"'"']*)'"'"'\s*(?:\}\})?\s*$""")' \
  '    r"""^\s*github\.repository\s*(==|!=)\s*'"'"'([^'"'"']*)'"'"'\s*$""")' "$T"

mutant "A22 ★★ no function names are derived — disabled_calls grades nothing (§7j)" "$RG" \
  '    return frozenset(_FUNC_HEAD.findall(text or ""))' \
  '    return frozenset()' "$T"

# ==== B. the inverse guard itself ===============================================================

mutant "B01 ★★★ the inverse guard is flipped — the refusal never fires where it is needed" "$YML" \
  "    if: github.repository != 'JasGujral/mokata-oss'" \
  "    if: github.repository == 'JasGujral/mokata-oss'" "$T"

mutant "B02 ★★★ the refusal exits 0 — a silent skip with extra words" "$YML" \
  '          echo "Delete the tag pushed here (git push --delete origin <tag>) and start at step 1."
          exit 1' \
  '          echo "Delete the tag pushed here (git push --delete origin <tag>) and start at step 1."
          exit 0' "$T"

mutant "B03 ★★★ the refusal gains a needs: — on the dev repo it is skipped with its dependency" "$YML" \
  "  refuse-to-publish-from-a-non-publishing-repository:
    if: github.repository != 'JasGujral/mokata-oss'" \
  "  refuse-to-publish-from-a-non-publishing-repository:
    needs: [test]
    if: github.repository != 'JasGujral/mokata-oss'" "$T"

mutant "B04 ★★ the refusal dies before it explains — red, with no reason in the log" "$YML" \
  '      - name: This repository does not publish — refuse the tag, loudly
        run: |
          echo "::error title=Wrong repository for a release tag::${GITHUB_REPOSITORY} does not publish."' \
  '      - name: This repository does not publish — refuse the tag, loudly
        run: |
          exit 1
          echo "::error title=Wrong repository for a release tag::${GITHUB_REPOSITORY} does not publish."' "$T"

mutant "B05 ★★★ a SIXTH job appears with no guard — the exact regression the row names" "$YML" \
  'jobs:
  # ============================================================================================' \
  'jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - run: echo "release started"
  # ============================================================================================' "$T"

mutant "B06 ★★★ the pypi publish guard is removed — the publish job runs from the private repo" "$YML" \
  "  pypi:
    needs: [build]        # publish ONLY after the full matrix + validate + reproducible build pass
    if: github.repository == 'JasGujral/mokata-oss'" \
  "  pypi:
    needs: [build]        # publish ONLY after the full matrix + validate + reproducible build pass" "$T"

# ==== C. the script (INTERNAL — absent on the public mirror) =====================================

if [ ! -f "$SH" ]; then
    printf '\n================================================================================\n'
    printf 'BATCH ABORTED — %s is absent. This is the PUBLIC subset, where the C group cannot\n' "$SH"
    printf 'run. %s of %s mutants NEVER RAN; this is not a pass.\n' "$((TOTAL - ran))" "$TOTAL"
    printf '================================================================================\n'
    exit 66
fi

mutant "C01 ★★★ the disabled call comes back — the third thing no reader can predict" "$SH" \
  'echo "NOTE: ${DEV_REPO}'"'"'s own CI is not a release gate and has not been one since 0.0.10."' \
  '# wait_for_ci_green "$DEV_REPO" "$(git rev-parse HEAD)" "the dev repo"
echo "NOTE: ${DEV_REPO}'"'"'s own CI is not a release gate and has not been one since 0.0.10."' "$T"

mutant "C02 ★★★ a LIVE dev-repo CI wait is re-armed — the direction is reversed silently" "$SH" \
  'echo "NOTE: ${DEV_REPO}'"'"'s own CI is not a release gate and has not been one since 0.0.10."' \
  'wait_for_ci_green "$DEV_REPO" "$(git rev-parse HEAD)" "the dev repo"' "$T"

mutant "C03 ★★★ a MIRROR CI wait is deleted — the ENFORCED gate goes (the negative)" "$SH" \
  'wait_for_ci_green "$PUB_REPO" "$(cd "$PUB_CHECKOUT" && git rev-parse "$BRANCH")" "the release PR (${BRANCH})"' \
  ': # the release PR CI wait, removed' "$T"

mutant "C04 ★★ the operator is no longer told at the moment of use" "$SH" \
  'echo "NOTE: ${DEV_REPO}'"'"'s own CI is not a release gate and has not been one since 0.0.10."' \
  'echo "NOTE: skipping the dev CI wait."' "$T"

mutant "C05 ★★★ an EXPIRED waiver is planted — the sunset mechanism is what must catch it" "$SH" \
  'echo "NOTE: ${DEV_REPO}'"'"'s own CI is not a release gate and has not been one since 0.0.10."' \
  '# WAIVER(id=dev-ci-billing, through=0.0.10, owner=Jas, filed=2026-07-06): billing is off.
echo "NOTE: ${DEV_REPO}'"'"'s own CI is not a release gate and has not been one since 0.0.10."' "$T"

mutant "C06 ★★ the DOCUMENTED recipe loses PYTHONPATH — the human gets the stale answer" "$SH" \
  '  echo "  7. PYTHONPATH=/path/to/mokata-oss/src ${PYTHON} -m mokata release-check ${VER} \\"' \
  '  echo "  7. ${PYTHON} -m mokata release-check ${VER} \\"' "$T"

mutant "C07 ★★ the TAG-TIME invocation loses PYTHONPATH — the remedy stops existing" "$SH" \
  '  if PYTHONPATH="${root}/src" "$PYTHON" -m mokata release-check "$VER" --root "$root"; then' \
  '  if "$PYTHON" -m mokata release-check "$VER" --root "$root"; then' "$T"

# ==== D. which package answered =================================================================

mutant "D01 ★★★ every package reads as in-root — release-check never refuses again" "$PKG" \
  '    state = ANSWERED_IN_ROOT if _within(package_dir, resolved_root) else ANSWERED_OUT_OF_ROOT' \
  '    state = ANSWERED_IN_ROOT' "$T"

# ⚠ D02 WAS ONE MUTANT AND IT CAME BACK GREEN — the FIRST batch's only survivor. `_within` resolved
# both paths and `answering_package` had already resolved them, so each realpath covered for the
# other and neither was gradable (doc 85 §7f). The duplicate was DELETED, and the one remaining
# resolution happens at TWO sites which need TWO offenders — the root's, and the package's.
mutant "D02a ★★ the ROOT stops being resolved — a symlinked checkout is falsely refused" "$PKG" \
  '    resolved_root = os.path.realpath(root or ".")' \
  '    resolved_root = os.path.abspath(root or ".")' "$T"

mutant "D02b ★★ the PACKAGE path stops being resolved — a symlinked src/ is falsely refused" "$PKG" \
  '    package_dir = os.path.realpath(os.path.dirname(package_file))' \
  '    package_dir = os.path.abspath(os.path.dirname(package_file))' "$T"

mutant "D03 ★★ the boundary is a bare prefix — mokata-oss counts as inside mokata" "$PKG" \
  '    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)' \
  '    return child == parent or child.startswith(parent)' "$T"

mutant "D04 ★★ a package with no __file__ reads as the checkout's own (§7g)" "$PKG" \
  '        return PackageProvenance(ANSWERED_UNRESOLVABLE, resolved_root, None, version, field_count)' \
  '        return PackageProvenance(ANSWERED_IN_ROOT, resolved_root, None, version, field_count)' "$T"

mutant "D05 ★ the field count stops being derived — the 5-vs-7 tell goes quiet (§7j)" "$PKG" \
  '        field_count = len(_VERSION_FIELDS)' \
  '        field_count = 5' "$T"

mutant "D06 ★★★ every provenance is trustworthy — the refusal is unreachable" "$PKG" \
  '        return self.state == ANSWERED_IN_ROOT' \
  '        return True' "$T"

mutant "D07 ★★ the remedy stops naming PYTHONPATH — a refusal with no way out" "$PKG" \
  "        return 'PYTHONPATH=\"%s/src\" python3 -m mokata release-check <version> --root \"%s\"' % (" \
  "        return 'PYTHON_PATH=\"%s/src\" python3 -m mokata release-check <version> --root \"%s\"' % (" "$T"

mutant "D08 ★★★ two exit codes collapse into one — the master defect class, here (§7g)" "$CLI" \
  'RELEASE_CHECK_ANSWERED_ELSEWHERE = 3' \
  'RELEASE_CHECK_ANSWERED_ELSEWHERE = 1' "$T"

mutant "D09 ★★ the provenance line is never printed — the answer stops naming its author" "$CLI" \
  '    prov = answering_package(args.root, getattr(packaging, "__file__", None), __version__)
    print(prov.render())' \
  '    prov = answering_package(args.root, getattr(packaging, "__file__", None), __version__)' "$T"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'TAG-GUARD MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
