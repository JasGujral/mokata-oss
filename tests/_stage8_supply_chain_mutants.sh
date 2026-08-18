#!/usr/bin/env bash
# Drives the 0.0.18 stage-8 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage8_supply_chain_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Driver shape from _release_asset_set_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED,
# doc 84 — and it satisfies tests/_mutant_driver_contract.py, which is a live pin.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing",
# and every RED it prints is unattributable.
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. Location is part of the contract:
# scripts/sync-public.sh excludes docs/build/, so a driver filed beside its report is ABSENT from
# the public mirror and a public contributor grading this repo would run an incomplete corpus that
# READS as complete. Three drivers are already declared-internal rather than repaired
# (MUTANT-DRIVERS-IN-DOCS-BUILD-DO-NOT-SHIP); this is not a fourth.
#
# SIX GROUPS, one per surface the stage touches, because the three rows fail in different places:
#   A. tests/_supply_chain_sweep.py        the shell reader (pip classes + executed scripts)
#   B. tests/_mirror_bookkeeping.py        the ships-list derivation, both directions
#   C. scripts/check-release-assets.sh     the asset-set assertion, incl. the literal/§7g repair
#   D. tests/_release_assets.py            the provenance helpers
#   E. .github/workflows/release.yml       the release path itself
#   F. CLAUDE.md                           the prose the whole of row ③ is about

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

SC=tests/_supply_chain_sweep.py
MB=tests/_mirror_bookkeeping.py
CS=scripts/check-release-assets.sh
RA=tests/_release_assets.py
RY=.github/workflows/release.yml
CI=.github/workflows/ci.yml
CM=CLAUDE.md

T_PIN='test_pinned_dependency_property.py'
T_LIST='test_claude_md_ships_list.py'
T_SET='test_release_asset_set.py'
T_PROV='test_release_provenance.py'

TOTAL=44
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T_PIN" "$T_LIST" "$T_SET" "$T_PROV"; do
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

# ==== A. the shell reader ========================================================================

mutant "A01 ★★ pip is never recognised — the whole corpus vanishes and the property is vacuous" "$SC" \
  '_PIP = re.compile(r"^(?:.*/)?pip[23]?(?:\.\d+)?$")' \
  '_PIP = re.compile(r"^(?:.*/)?pipzz$")' "$T_PIN"

mutant "A02 ★★ --require-hashes stops mattering — an un-hashed requirements install reads pinned" "$SC" \
  '        return HASH_PINNED if "--require-hashes" in flags else UNPINNED_FETCH' \
  '        return HASH_PINNED' "$T_PIN"

mutant "A03 ★★ the -r rule is gone — a local path holding remote NAMES reads as local source" "$SC" \
  '    if requirement:
        return' \
  '    if False:
        return' "$T_PIN"

mutant "A04 ★★ every specifier is local — pip install requests satisfies the property" "$SC" \
  '    return bare.startswith((".", "/", "~")) or "/" in bare' \
  '    return True' "$T_PIN"

mutant "A05 ★ block keywords stop being stripped — the one-line if/then install is never seen" "$SC" \
  '    "then", "else", "elif", "do", "done", "fi", "!", "time",' \
  '    "else", "elif", "do", "done", "fi", "!", "time",' "$T_PIN"

mutant "A06 ★ a leading VAR=value hides the command from the reader" "$SC" \
  '            while words and (words[0] in _PREFIX_WORDS or _ASSIGNMENT.match(words[0])):' \
  '            while words and (words[0] in _PREFIX_WORDS or False):' "$T_PIN"

mutant "A07 ★★ a GLUED redirection is read as a requirement specifier" "$SC" \
  '                if not _REDIRECT_GLUED.match(word):
                    kept.append(word)' \
  '                if True:
                    kept.append(word)' "$T_PIN"

mutant "A08 ★ a DETACHED redirection leaves its target behind as a specifier" "$SC" \
  '                if _REDIRECT_BARE.match(word):
                    index += 2' \
  '                if _REDIRECT_BARE.match(word):
                    index += 1' "$T_PIN"

mutant "A09 ★★ an EMPTY specifier list classifies local — the empty-glob defect, one class over" "$SC" \
  '    if specs and all(_is_local(s) for s in specs):' \
  '    if all(_is_local(s) for s in specs):' "$T_PIN"

mutant "A10 ★★ \$( is no longer a command boundary — a helper run inside \$(...) reads as never run" "$SC" \
  '_SPLIT = re.compile(r"(?:\|\||&&|[;|&\n]|\$\()")' \
  '_SPLIT = re.compile(r"(?:\|\||&&|[;|&\n])")' "$T_LIST"

mutant "A11 ★★ any word may be a script — an ECHOed dev-only path reads as a helper CI runs" "$SC" \
  '        if not _INTERPRETERS.match(words[0]):
            continue' \
  '        if False:
            continue' "$T_LIST"

mutant "A12 ★ the script's own ARGUMENTS are read as scripts too" "$SC" \
  '            break            # the first non-flag word is the script; the rest are ITS arguments' \
  '            continue         # the first non-flag word is the script; the rest are ITS arguments' "$T_LIST"

mutant "A13 ★ comments stop being stripped — a commented-out install is convicted" "$SC" \
  '        for fragment in _SPLIT.split(_strip_comment(line)):' \
  '        for fragment in _SPLIT.split(line):' "$T_PIN"

mutant "A14 ★★ the python -m pip spelling is lost — most of the release path stops being read" "$SC" \
  '    elif _PYTHON.match(words[0]) and words[1:3] and words[1] == "-m" and _PIP.match(words[2]):' \
  '    elif False:' "$T_PIN"

# ==== B. the ships-list derivation ===============================================================

mutant "B01 ★★ the enumeration runs into its own footnotes — the guard grades its commentary" "$MB" \
  '    enumeration = bullet.split(ENUMERATION_ENDS_AT, 1)[0]' \
  '    enumeration = bullet' "$T_LIST"

mutant "B02 ★★ a bullet no longer ends at the next one — the two lists vouch for each other" "$MB" \
  '_TOP_BULLET = re.compile(r"^- .*?(?=^- |\Z)", re.M | re.S)' \
  '_TOP_BULLET = re.compile(r"^- .*\Z", re.M | re.S)' "$T_LIST"

mutant "B03 ★★ an absent heading returns an EMPTY list — a broken derivation reads as a clean one" "$MB" \
  '    bullet = _bullet(prose, heading)
    if bullet is None:
        return None' \
  '    bullet = _bullet(prose, heading)
    if bullet is None:
        return frozenset()' "$T_LIST"

mutant "B04 ★★ an empty deriving set is GREEN — vacuously true, byte-identical to complete" "$MB" \
  '    if not tracked_excludes:
        return ShipsListResolution(' \
  '    if False:
        return ShipsListResolution(' "$T_LIST"

mutant "B05 ★★ drifting BOTH ways collapses into one direction (§7g)" "$MB" \
  '    if omitted and overclaimed:' \
  '    if False:' "$T_LIST"

mutant "B06 ★★ UNDECIDABLE launders to fine — a prose entry resting on a GLOB is waved through" "$MB" \
  '        p for p in listed if resolve(p, script, script_path).verdict != GREEN)' \
  '        p for p in listed if resolve(p, script, script_path).verdict == RED)' "$T_LIST"

mutant "B07 ★ a trailing slash makes a directory a different path in the prose than in the guard" "$MB" \
  'def _norm(path):
    return path.rstrip("/")' \
  'def _norm(path):
    return path' "$T_LIST"

mutant "B08 ★ every backticked word is a path — prose becomes list members" "$MB" \
  '        if token and " " not in token and ("/" in token or "." in token))' \
  '        if token)' "$T_LIST"

mutant "B09 ★★ the stays-public check reports nothing — stage 6's drift stops being visible" "$MB" \
  '    return frozenset(_norm(path) for path in executed) - listed' \
  '    return frozenset()' "$T_LIST"

mutant "B10 ★★ the LEAK direction is reported as the FALSE-REASSURANCE one" "$MB" \
  '        return ShipsListResolution(heading, BASIS_LIST_OMITS_AN_EXCLUDED, omitted=omitted)' \
  '        return ShipsListResolution(heading, BASIS_LIST_CLAIMS_AN_UNEXCLUDED, omitted=omitted)' "$T_LIST"

# ==== C. the asset-set assertion =================================================================

mutant "C01 ★★ the literal/§7g repair is undone — 'nothing was built' reports as 'unsigned'" "$CS" \
  '    if [ "${#matches[@]}" -eq 1 ] && [ ! -e "${matches[0]}" ]; then' \
  '    if false; then' "$T_SET"

mutant "C02 ★★ the literal test is INVERTED — an artifact that exists reads as never built" "$CS" \
  '    if [ "${#matches[@]}" -eq 1 ] && [ ! -e "${matches[0]}" ]; then' \
  '    if [ "${#matches[@]}" -eq 1 ] && [ -e "${matches[0]}" ]; then' "$T_SET"

mutant "C03 ★★ the provenance class leaves the declaration — a Release without it passes" "$CS" \
  "sbom.cdx.json
provenance.intoto.jsonl'" \
  "sbom.cdx.json'" "$T_PROV"

# ==== D. the provenance helpers ==================================================================

mutant "D01 ★★ an output reference is never recognised — 'nobody reads bundle-path' goes quiet" "$RA" \
  '_OUTPUT_REF = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")' \
  '_OUTPUT_REF = re.compile(r"stepz\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")' "$T_PROV"

mutant "D02 ★ an action is matched WITH its ref — the attestation step is never found" "$RA" \
  '        if isinstance(step.get("uses"), str) and step["uses"].split("@", 1)[0] == action)' \
  '        if isinstance(step.get("uses"), str) and step["uses"] == action)' "$T_PROV"

mutant "D03 ★★ job_steps yields nothing — every ordering claim becomes vacuously true" "$RA" \
  '            if isinstance(step, dict):
                found.append((job_id, index, step))' \
  '            if False:
                found.append((job_id, index, step))' "$T_PROV"

mutant "D04 ★★ the suffix Scorecard reads is wrong — the asset scores as if it were absent" "$RA" \
  'SCORECARD_PROVENANCE_SUFFIX = ".intoto.jsonl"' \
  'SCORECARD_PROVENANCE_SUFFIX = ".json"' "$T_PROV"

mutant "D05 ★ the distribution suffixes are emptied — the pypi strip check convicts the wheel" "$RA" \
  'DISTRIBUTION_SUFFIXES = (".whl", ".tar.gz")' \
  'DISTRIBUTION_SUFFIXES = ()' "$T_PROV"

# ==== E. the release path ========================================================================

mutant "E01 ★★ the bundle path stops coming from the attestation — the output is unread again" "$RY" \
  '          BUNDLE_PATH: ${{ steps.provenance.outputs.bundle-path }}' \
  '          BUNDLE_PATH: /tmp/attestation.json' "$T_PROV"

mutant "E02 ★★ the empty-bundle guard is disarmed — a cut publishes with provenance silently lost" "$RY" \
  '          if [ -z "$BUNDLE_PATH" ] || [ ! -f "$BUNDLE_PATH" ]; then' \
  '          if false; then' "$T_PROV"

mutant "E03 ★★ the unpinned pip upgrade comes back — the property this stage chose is reversed" "$RY" \
  '          python -m venv /tmp/sbomenv
          /tmp/sbomenv/bin/python -m pip install dist/*.whl >/dev/null' \
  '          python -m venv /tmp/sbomenv
          /tmp/sbomenv/bin/python -m pip install --upgrade pip >/dev/null
          /tmp/sbomenv/bin/python -m pip install dist/*.whl >/dev/null' "$T_PIN"

mutant "E04 ★★ the provenance asset is declared but never uploaded — a short Release, again" "$RY" \
  '            dist/sbom.cdx.json
            dist/provenance.intoto.jsonl' \
  '            dist/sbom.cdx.json' "$T_SET"

mutant "E05 ★★ the third copy of the asset set parts from the declaration — twine gets the bundle" "$RY" \
  '        run: rm -f dist/sbom.cdx.json dist/provenance.intoto.jsonl dist/*.sigstore.json' \
  '        run: rm -f dist/sbom.cdx.json dist/*.sigstore.json' "$T_PROV"

mutant "E06 ★★ the attestation takes its own bundle as a subject" "$RY" \
  '          subject-path: dist/*' \
  '          subject-path: dist/provenance.intoto.jsonl' "$T_PROV"

mutant "E07 ★ the copy is no longer runnable offline — the executed pin loses its subject" "$RY" \
  '          cp "$BUNDLE_PATH" dist/provenance.intoto.jsonl' \
  '          cp "${{ steps.provenance.outputs.bundle-path }}" dist/provenance.intoto.jsonl' "$T_PROV"

mutant "E08 ★★ a SECOND job resolves its shell from an expression — the blind-spot claim goes stale" "$CI" \
  '    defaults:
      run:
        shell: bash' \
  '    defaults:
      run:
        shell: ${{ matrix.jsonschema }}' "$T_PIN"

# ==== F. the prose the row is about ==============================================================

mutant "F01 ★★ docs/talks/ leaves the never-ships list — today's real defect, restored" "$CM" \
  '`docs/marketing/`, `docs/talks/`,' \
  '`docs/marketing/`,' "$T_LIST"

mutant "F02 ★★ scripts/check-tracker-tables.py leaves it — the entry the row actually names" "$CM" \
  '`scripts/release.sh`, `scripts/check-tracker-tables.py`.' \
  '`scripts/release.sh`.' "$T_LIST"

mutant "F03 ★★ the stays-public list loses the helper release.yml runs — stage 6's drift, restored" "$CM" \
  '`scripts/normalize_sdist.py` +
  `scripts/check-release-assets.sh` (all three run by `release.yml`)' \
  '`scripts/normalize_sdist.py` (both run by `release.yml`)' "$T_LIST"

mutant "F04 ★★ a path nothing excludes joins the list — the FALSE-REASSURANCE direction, on the REAL tree" "$CM" \
  '- **NEVER ships to public:** `docs/build/`, `docs/launch/`,' \
  '- **NEVER ships to public:** `docs/nowhere/`, `docs/build/`, `docs/launch/`,' "$T_LIST"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-8 SUPPLY-CHAIN MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
