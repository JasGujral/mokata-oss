#!/usr/bin/env bash
# release-0.0.1.sh — guarded 0.0.1 re-baseline cut.
# REVIEW THIS, then run it yourself from the dev checkout root.
# It backs up old release metadata, PAUSES before the irreversible delete, and
# verifies between steps. Nothing destructive happens without you typing "yes".
#
# Prereqs: gh authenticated (`gh auth status`), on master, working tree clean,
# version already swept to 0.0.1 (Stage 24B PREP, commit aea7f4d).
set -euo pipefail

DEV_REPO="JasGujral/mokata"
PUB_REPO="JasGujral/mokata-oss"
OLD=(v1.0.0 v1.1.0 v1.2.0 v1.2.1 v1.2.2 v1.2.3)
PUB_CHECKOUT="${1:-}"   # pass your local mokata-oss checkout path as arg 1

confirm() { read -r -p "$1 [type 'yes' to proceed] " a; [ "$a" = "yes" ]; }
say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# --- preflight ---------------------------------------------------------------
say "Preflight"
[ "$(git rev-parse --abbrev-ref HEAD)" = "master" ] || { echo "not on master"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean"; exit 1; }
grep -q '^version = "0.0.1"' pyproject.toml || { echo "version is not 0.0.1"; exit 1; }
gh auth status >/dev/null || { echo "gh not authenticated"; exit 1; }
# Validate the public checkout NOW — BEFORE any irreversible step — so a missing or
# placeholder path can never let the deletes/pushes (steps 1-3) fire and then fail at sync.
if [ -n "$PUB_CHECKOUT" ]; then
  [ -d "$PUB_CHECKOUT/.git" ] || {
    echo "public checkout '$PUB_CHECKOUT' is not a git repo (pass your real mokata-oss path, "
    echo "or pass no arg to handle the mirror manually). Refusing to start."; exit 1; }
  echo "public checkout OK: $PUB_CHECKOUT"
else
  echo "no public checkout arg — the mirror step (4) will print manual instructions."
fi
echo "on master, clean, version 0.0.1, gh authed. OK."

# --- 0) BACKUP old release notes before anything is deleted ------------------
say "Backup old release metadata -> ./release-backup-$(date +%Y%m%d)"
BK="release-backup-$(date +%Y%m%d)"; mkdir -p "$BK"
for V in "${OLD[@]}"; do
  for R in "$DEV_REPO" "$PUB_REPO"; do
    gh release view "$V" --repo "$R" --json tagName,name,body,createdAt \
      > "$BK/${R//\//_}_$V.json" 2>/dev/null || true
  done
done
echo "backed up what existed into $BK/ (review before continuing)."

# --- 1) IRREVERSIBLE: delete old releases + tags ----------------------------
say "IRREVERSIBLE — delete releases + tags ${OLD[*]} on BOTH repos"
confirm "Delete the old releases and tags now?" || { echo "aborted (nothing deleted)."; exit 0; }
for V in "${OLD[@]}"; do
  gh release delete "$V" --repo "$DEV_REPO" --yes --cleanup-tag || true
  gh release delete "$V" --repo "$PUB_REPO" --yes --cleanup-tag || true
done
git tag -d v1.0.0 v1.1.0 2>/dev/null || true
echo "old releases/tags deleted."

# --- 2) push master ----------------------------------------------------------
say "Push master -> origin ($DEV_REPO)"
confirm "Push master?" || { echo "stopped after delete; push manually when ready."; exit 0; }
git push origin master

# --- 3) tag + push v0.0.1 (dev) ---------------------------------------------
say "Tag v0.0.1 (dev) and push the tag"
git tag -a v0.0.1 -m "mokata 0.0.1 — inaugural public release"
git push origin v0.0.1

# --- 4) sync the public mirror ----------------------------------------------
say "Sync public mirror"
if [ -z "$PUB_CHECKOUT" ]; then
  echo "No mokata-oss checkout path given (arg 1). Run sync manually:"
  echo "  scripts/sync-public.sh /path/to/mokata-oss-checkout"
  echo "  (cd /path/to/mokata-oss-checkout && git push origin main && \\"
  echo "     git tag -a v0.0.1 -m 'mokata 0.0.1' && git push origin v0.0.1)"
else
  scripts/sync-public.sh "$PUB_CHECKOUT"
  ( cd "$PUB_CHECKOUT" && git --no-pager diff --stat HEAD || true )
  confirm "Public mirror diff looks right — push main + tag v0.0.1?" || { echo "mirror not pushed."; exit 0; }
  ( cd "$PUB_CHECKOUT" && git push origin main && \
      git tag -a v0.0.1 -m "mokata 0.0.1" && git push origin v0.0.1 )
fi

# --- 5) publish the 0.0.1 release on the public repo ------------------------
say "Publish the 0.0.1 GitHub release ($PUB_REPO)"
confirm "Create the public 0.0.1 release from RELEASE_NOTES.md?" || { echo "release not published."; exit 0; }
gh release create v0.0.1 --repo "$PUB_REPO" --title "mokata 0.0.1" --notes-file RELEASE_NOTES.md

say "Done"
echo "Now watch the release.yml CD go green before announcing:"
echo "  gh run watch --repo $PUB_REPO  (or check the Actions tab)"
