#!/usr/bin/env bash
# check-release-assets.sh — the Release's ASSET SET, declared ONCE and asserted on EXACT PATHS.
#
#   scripts/check-release-assets.sh <dir>            assert the full published set; print it
#   scripts/check-release-assets.sh <dir> --inputs   assert the signing inputs; print them
#
# WHY THIS FILE EXISTS, and it is not tidiness.
#
# `v0.0.17` shipped a Release with no `.sig` and no `.pem` on it, and TWO checks in the release
# path said nothing. Both were checks that CANNOT FAIL:
#
#   1. the build job ran  `ls -l dist/*.sig dist/*.pem dist/*.sigstore.json`  nine lines under a
#      `shopt -s nullglob`. An unmatched glob expands to NOTHING there rather than to itself, so
#      `ls` received only the three bundles and exited 0. doc 84 then read that exit 0 back as
#      PROOF THE FILES EXISTED, and the row was wrong for a further two diagnoses on the strength
#      of it.
#   2. the release job listed `dist/*.sig` and `dist/*.pem` among `softprops/action-gh-release`'s
#      `files:` globs. That action's `fail_on_unmatched_files` DEFAULTS TO FALSE, so it printed
#      `🤔 Pattern 'dist/*.sig' does not match any files.` (run 31271824251, lines 24593-24594)
#      and published the short set anyway.
#
# THE RULE THIS FILE IMPLEMENTS: every existence check below is on an EXACT PATH — `[ -f "$want" ]`
# where `$want` is a concrete filename — never on a glob. A glob is how a check gets disarmed; an
# exact path cannot expand to nothing. `nullglob` is still set, because the loop that EXPANDS the
# patterns genuinely needs it (without it an unmatched pattern iterates over its own literal
# string), and that expansion reports a no-match rather than absorbing it.
#
# THREE STATES, NEVER TWO (doc 85 §7g). "the pattern matched nothing" and "the input is unsigned"
# are different facts about a different part of the pipeline and they get different exit codes:
#
#   0  the set is complete; stdout carries it, one path per line
#   1  usage, or the directory does not exist — nothing was inspected
#   3  A SIGNED PATTERN MATCHED NOTHING. The build produced no wheel / no sdist / no SBOM, so the
#      signing loop would sign nothing and every later check would pass over an empty set.
#   4  AN INPUT HAS NO SIGNATURE BUNDLE. The artifact exists and is UNSIGNED — the v0.0.17 defect's
#      shape, and the one a short `files:` list ships silently.
#
# WHAT IS SIGNED, AND WHAT COSIGN ACTUALLY WRITES. `SIGNED_PATTERNS` is the one declaration of the
# set; `release.yml` reads it from here rather than keeping a second copy, and
# `tests/test_release_asset_set.py` grades the `files:` block against it so the two cannot part.
# `BUNDLE_SUFFIX` is `.sigstore.json` because that is the ONLY thing cosign v3 writes:
# `--output-signature` / `--output-certificate` are accepted, warned about and IGNORED whenever the
# new bundle format is in use (which is the v3 default) — measured on cosign v3.0.6, the version
# `sigstore/cosign-installer` v4.1.2 installs by default. See `tests/_release_assets.py`.

set -uo pipefail

# The patterns whose matches are signed and published. Relative to the directory passed in.
#
# `provenance.intoto.jsonl` is the SLSA build-provenance bundle `actions/attest-build-provenance`
# already produced on every release and never published (0.0.18 stage 8, `SIGNED-RELEASES-PROVENANCE
# (ii)`). It is a LITERAL, like `sbom.cdx.json`, because the workflow chooses the name when it
# copies the bundle out of `$RUNNER_TEMP` — nothing globs for it, so a build that failed to attach
# it fails HERE on an exact path rather than expanding to nothing.
#
# ⚠ IT IS IN THE *SIGNED* LIST DELIBERATELY, AND THAT IS A CHOICE, NOT AN OVERSIGHT. The bundle is
# already Sigstore-signed in its own right, so its `.sigstore.json` adds no new trust; what it adds
# is that the provenance asset is covered by the SAME exact-path assertion as everything else,
# under ONE invariant ("every published artifact ships beside its signature bundle") instead of two.
# The alternative considered and rejected was a second `PUBLISHED_BUT_UNSIGNED` list — a second
# declaration, which is precisely what stage 6 removed.
SIGNED_PATTERNS='*.whl
*.tar.gz
sbom.cdx.json
provenance.intoto.jsonl'

# The one artefact cosign v3 emits per signed blob.
BUNDLE_SUFFIX='.sigstore.json'

dir="${1:-}"
mode="${2:-full}"

if [ -z "$dir" ] || { [ "$mode" != "full" ] && [ "$mode" != "--inputs" ]; }; then
    printf 'usage: %s <dir> [--inputs]\n' "$0" >&2
    exit 1
fi
if [ ! -d "$dir" ]; then
    printf 'ABORT: no such directory: %s\n' "$dir" >&2
    exit 1
fi

# Load-bearing, and the ONLY thing it is here for: without it an unmatched pattern would iterate
# over the literal string `dist/*.whl` and the loop would try to sign a file of that name. With it
# the expansion is empty and the `-eq 0` test below turns that emptiness into exit 3 instead of
# letting it pass as "nothing to do".
shopt -s nullglob

unmatched=''
inputs=''
while IFS= read -r pattern; do
    [ -n "$pattern" ] || continue
    matches=( "$dir"/$pattern )
    if [ "${#matches[@]}" -eq 0 ]; then
        unmatched="$unmatched  $dir/$pattern"$'\n'
        continue
    fi
    # ⚠⚠ NULLGLOB DOES NOT APPLY TO A LITERAL, AND THAT COLLAPSED TWO STATES INTO ONE (0.0.18
    # stage 8). bash removes an unmatched word only when it CONTAINS a glob metacharacter; a
    # pattern like `sbom.cdx.json` survives whether or not the file exists, so the array is
    # non-empty and the loop falls through to the exact-path check below — which then reports a
    # build that produced NOTHING as exit 4, "an input has no signature bundle", i.e. "the
    # artifact exists and is UNSIGNED". It does not exist. That is §7g inside the script written
    # to keep those two facts apart, and it was invisible while `sbom.cdx.json` was the only
    # literal; stage 8's `provenance.intoto.jsonl` makes it two. Both spellings of "matched
    # nothing" now arrive as exit 3.
    if [ "${#matches[@]}" -eq 1 ] && [ ! -e "${matches[0]}" ]; then
        unmatched="$unmatched  $dir/$pattern"$'\n'
        continue
    fi
    for match in "${matches[@]}"; do
        inputs="$inputs$match"$'\n'
    done
done <<EOF
$SIGNED_PATTERNS
EOF

if [ -n "$unmatched" ]; then
    printf 'ABORT: a signed pattern matched NOTHING in %s:\n%s' "$dir" "$unmatched" >&2
    printf 'The release signs whatever these patterns match. A pattern that matches nothing means\n' >&2
    printf 'the artifact was never built — and under nullglob that is SILENT unless it is checked.\n' >&2
    exit 3
fi

if [ "$mode" = "--inputs" ]; then
    printf '%s' "$inputs"
    exit 0
fi

expected=''
missing=''
while IFS= read -r input; do
    [ -n "$input" ] || continue
    for want in "$input" "$input$BUNDLE_SUFFIX"; do
        expected="$expected$want"$'\n'
        # EXACT PATH. Not a glob — see the header. This is the check v0.0.17 did not have.
        [ -f "$want" ] || missing="$missing  $want"$'\n'
    done
done <<EOF
$inputs
EOF

if [ -n "$missing" ]; then
    printf 'ABORT: the Release asset set is SHORT. Missing:\n%s' "$missing" >&2
    printf 'Every built artifact must ship beside its %s signature bundle. Publishing an\n' \
        "$BUNDLE_SUFFIX" >&2
    printf 'unsigned artifact is what v0.0.17 did, and no check said so.\n' >&2
    exit 4
fi

printf '%s' "$expected"
