#!/usr/bin/env bash
# check-reproducible.sh — Stage 68: prove the sdist + wheel build is REPRODUCIBLE.
#
# Builds the package twice into separate dirs (honoring SOURCE_DATE_EPOCH so setuptools
# stamps deterministic timestamps and strips build-host paths), then compares the two builds
# byte-for-byte by sha256. Identical hashes => a reproducible build a security reviewer (or a
# rebuilder verifying provenance) can independently reproduce.
#
#   scripts/check-reproducible.sh            # builds from the repo root
#
# Fail-closed (set -euo pipefail): any build error or a hash mismatch exits non-zero. The
# release workflow runs this before signing/attesting; you can run it locally too.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Pin the build timestamp to the current commit's author date so two builds (here, or here vs
# a rebuilder) agree. Overridable; defaults to the HEAD commit time, else a fixed epoch.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --pretty=%ct 2>/dev/null || echo 1700000000)}"
echo "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 -m build --outdir "$WORK/build1" . >/dev/null
python3 -m build --outdir "$WORK/build2" . >/dev/null

# Normalize each sdist deterministically (setuptools leaves a few build-generated members —
# PKG-INFO, .egg-info, dir entries — stamped with the wall-clock build time). The wheel is
# already reproducible; this closes the sdist's gap. Metadata-only — contents are untouched.
python3 "$ROOT/scripts/normalize_sdist.py" "$WORK"/build1/*.tar.gz
python3 "$ROOT/scripts/normalize_sdist.py" "$WORK"/build2/*.tar.gz

hash_dir() {
  # sha256 of each artifact, keyed by basename, sorted — host-path independent.
  ( cd "$1" && for f in *; do
      if command -v sha256sum >/dev/null 2>&1; then
        printf '%s  %s\n' "$(sha256sum "$f" | cut -d" " -f1)" "$f"
      else
        printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d" " -f1)" "$f"
      fi
    done | sort -k2 )
}

H1="$(hash_dir "$WORK/build1")"
H2="$(hash_dir "$WORK/build2")"

echo "--- build #1 ---"; echo "$H1"
echo "--- build #2 ---"; echo "$H2"

if [ "$H1" = "$H2" ]; then
  echo "REPRODUCIBLE: both builds are byte-identical (sha256 match)."
else
  echo "NOT REPRODUCIBLE: the two builds differ (see hashes above)." >&2
  diff <(printf '%s\n' "$H1") <(printf '%s\n' "$H2") >&2 || true
  exit 1
fi
