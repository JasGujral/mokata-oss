#!/usr/bin/env bash
# release.sh — guarded, version-agnostic release cut for mokata (0.0.2 onward).
# Reads the version from pyproject.toml; NO destructive deletes (unlike the one-time
# release-0.0.1.sh re-baseline). REVIEW, then run it yourself from the dev checkout root.
# It pauses for an explicit "yes" before each push/publish step.
#
#   scripts/release.sh /path/to/mokata-oss-checkout
#
# Prereqs: on master, clean tree, version bumped + committed, gh authenticated.
set -euo pipefail

DEV_REPO="JasGujral/mokata"
PUB_REPO="JasGujral/mokata-oss"
PUB_CHECKOUT="${1:-}"

VER="$(grep -m1 '^version' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
TAG="v${VER}"

confirm() { read -r -p "$1 [type 'yes' to proceed] " a; [ "$a" = "yes" ]; }
say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# --- preflight ---------------------------------------------------------------
say "Preflight — releasing mokata ${VER} (tag ${TAG})"
[ "$(git rev-parse --abbrev-ref HEAD)" = "master" ] || { echo "not on master"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean"; exit 1; }
[ -n "$VER" ] || { echo "could not read version from pyproject.toml"; exit 1; }
gh auth status >/dev/null || { echo "gh not authenticated"; exit 1; }
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "tag $TAG already exists locally — bump the version or delete the stale tag first."; exit 1
fi
if [ -n "$PUB_CHECKOUT" ]; then
  [ -d "$PUB_CHECKOUT/.git" ] || { echo "public checkout '$PUB_CHECKOUT' is not a git repo. Refusing to start."; exit 1; }
  echo "public checkout OK: $PUB_CHECKOUT"
else
  echo "no public checkout arg — the mirror step will print manual instructions."
fi
echo "on master, clean, version ${VER}, gh authed, ${TAG} free. OK."

# --- 1) push master ----------------------------------------------------------
say "Push master -> origin ($DEV_REPO)"
confirm "Push master?" || { echo "stopped."; exit 0; }
git push origin master

# --- 2) tag + push the dev tag ----------------------------------------------
say "Tag ${TAG} (dev) and push it"
confirm "Create + push ${TAG} on the dev repo?" || { echo "stopped (master pushed, not tagged)."; exit 0; }
git tag -a "$TAG" -m "mokata ${VER}"
git push origin "$TAG"

# --- 3) sync the public mirror ----------------------------------------------
say "Sync public mirror"
if [ -z "$PUB_CHECKOUT" ]; then
  echo "Run the mirror sync manually:"
  echo "  scripts/sync-public.sh /path/to/mokata-oss-checkout"
  echo "  (cd /path/to/mokata-oss && git push origin main && git tag -a ${TAG} -m 'mokata ${VER}' && git push origin ${TAG})"
else
  scripts/sync-public.sh "$PUB_CHECKOUT"
  ( cd "$PUB_CHECKOUT" && git --no-pager diff --stat HEAD~1 2>/dev/null || true )
  confirm "Public mirror diff looks right — push main + tag ${TAG}?" || { echo "mirror not pushed."; exit 0; }
  ( cd "$PUB_CHECKOUT" && git push origin main && \
      git tag -a "$TAG" -m "mokata ${VER}" && git push origin "$TAG" )
fi

# --- 4) publish the GitHub release ------------------------------------------
say "Publish the ${TAG} release ($PUB_REPO)"
confirm "Create the public ${TAG} release from RELEASE_NOTES.md?" || { echo "release not published."; exit 0; }
gh release create "$TAG" --repo "$PUB_REPO" --title "mokata ${VER}" --notes-file RELEASE_NOTES.md

say "Done — mokata ${VER} released"
echo "Watch CD go green before announcing:  gh run watch --repo $PUB_REPO"
