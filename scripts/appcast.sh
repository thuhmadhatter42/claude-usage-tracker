#!/usr/bin/env bash
# appcast.sh: stage a notarized jusage DMG for Sparkle and (re)build the appcast, with GitHub Releases URLs.
# From the house kit's update-check module, plus the GitHub Releases layout and a --publish step.
#
#   scripts/appcast.sh <dist/jusage-N.N.N.dmg> [--publish [--dry-run]]
#
# Staging copies the DMG into DOWNLOADS (default <repo>/dist/downloads), refreshes jusage-latest.dmg (the
# installer's stable link) and runs Sparkle's generate_appcast over the versioned DMGs. Every enclosure URL is
# then pointed at <DOWNLOAD_PREFIX><RELEASE_TAG_PREFIX>-<version>/jusage-<version>.dmg. It REFUSES a DMG that is
# not stapled, or not named jusage-N.N.N.dmg (a -DRYRUN or -UNNOTARIZED file never goes in the feed).
# --publish uploads with `gh release create <tag> <dmg> jusage-latest.dmg appcast.xml`; with --dry-run it only
# prints that command. Settings: appcast.conf beside this script; environment values win:
#   APP_NAME DOWNLOADS DOWNLOAD_PREFIX RELEASE_TAG_PREFIX REPO_SLUG SPARKLE_ACCOUNT SPARKLE_BIN
# One-time key set-up: <SPARKLE_BIN>/generate_keys --account <SPARKLE_ACCOUNT> (writes to the login keychain).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
die() { echo "FAIL $*" >&2; exit 1; }
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }
_env_APP_NAME="${APP_NAME:-}"; _env_DOWNLOADS="${DOWNLOADS:-}"; _env_PREFIX="${DOWNLOAD_PREFIX:-}"
_env_TAGP="${RELEASE_TAG_PREFIX:-}"; _env_SLUG="${REPO_SLUG:-}"
# shellcheck disable=SC1091
[ -f "$HERE/appcast.conf" ] && . "$HERE/appcast.conf"
[ -z "$_env_APP_NAME" ] || APP_NAME="$_env_APP_NAME"
[ -z "$_env_PREFIX" ] || DOWNLOAD_PREFIX="$_env_PREFIX"
[ -z "$_env_TAGP" ] || RELEASE_TAG_PREFIX="$_env_TAGP"
[ -z "$_env_SLUG" ] || REPO_SLUG="$_env_SLUG"
DOWNLOADS="${_env_DOWNLOADS:-$REPO_ROOT/dist/downloads}"

DMG=""; PUBLISH=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --publish) PUBLISH=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) DMG="$1"; shift ;;
  esac
done
[ -n "$DMG" ] || { usage >&2; exit 2; }
[ "$DRY" -eq 0 ] || [ "$PUBLISH" -eq 1 ] || { echo "--dry-run goes with --publish" >&2; exit 2; }
case "${DOWNLOAD_PREFIX:-}" in https://*/) ;; *) echo "DOWNLOAD_PREFIX must be an https:// URL ending in / (got '${DOWNLOAD_PREFIX:-}')" >&2; exit 2 ;; esac
SPARKLE_ACCOUNT="${SPARKLE_ACCOUNT:-$APP_NAME}"
TOOLS="${SPARKLE_BIN:-$HOME/dev/thekitchen-deps/sparkle-2.10.1/bin}"

[ -f "$DMG" ] || die "no DMG at $DMG"
BASE="$(basename "$DMG")"
[[ "$BASE" =~ ^"$APP_NAME"-([0-9]+\.[0-9]+\.[0-9]+)\.dmg$ ]] || die "$BASE is not named $APP_NAME-<N.N.N>.dmg (a -DRYRUN or -UNNOTARIZED file never goes in the feed)"
VER="${BASH_REMATCH[1]}"
TAG="${RELEASE_TAG_PREFIX:-$APP_NAME}-$VER"
[ -x "$TOOLS/generate_appcast" ] || die "no generate_appcast in $TOOLS (set SPARKLE_BIN)"
xcrun stapler validate "$DMG" >/dev/null 2>&1 || die "$DMG is not stapled (run release.sh first)"
mkdir -p "$DOWNLOADS"
[ ! -e "$DOWNLOADS/$BASE" ] || cmp -s "$DMG" "$DOWNLOADS/$BASE" || die "$DOWNLOADS/$BASE already exists and differs: a shipped version is never overwritten (bump VERSION)"
# Already staged and signed (release.sh ran this step)? Then publishing reuses that appcast: re-running
# generate_appcast needs the Keychain key, and with the screen locked it waits forever on a prompt (2026-11-27).
SIGNED=0
if [ -e "$DOWNLOADS/$BASE" ] && [ -f "$DOWNLOADS/appcast.xml" ] \
   && grep -q "shortVersionString>$VER<" "$DOWNLOADS/appcast.xml" && grep -q "$BASE\".*sparkle:edSignature\|sparkle:edSignature.*$BASE\|$TAG/$BASE" "$DOWNLOADS/appcast.xml"; then
  SIGNED=1
fi
cp -p "$DMG" "$DOWNLOADS/"
cp -p "$DMG" "$DOWNLOADS/$APP_NAME-latest.dmg.tmp" && mv "$DOWNLOADS/$APP_NAME-latest.dmg.tmp" "$DOWNLOADS/$APP_NAME-latest.dmg"
if [ "$SIGNED" -eq 1 ]; then
  echo "appcast already has $BASE signed; reusing it"
else
  STAGE="$(mktemp -d "${TMPDIR:-/tmp}/appcast.XXXXXX")"; trap 'rm -rf "$STAGE"' EXIT
  find "$DOWNLOADS" -maxdepth 1 -name "$APP_NAME-[0-9]*.dmg" -exec cp -p {} "$STAGE/" \;
  [ -f "$DOWNLOADS/appcast.xml" ] && cp -p "$DOWNLOADS/appcast.xml" "$STAGE/"
  "$TOOLS/generate_appcast" --account "$SPARKLE_ACCOUNT" --download-url-prefix "$DOWNLOAD_PREFIX" "$STAGE" || die "generate_appcast failed"
  [ -s "$STAGE/appcast.xml" ] || die "generate_appcast wrote no appcast.xml"
  if [ -n "${RELEASE_TAG_PREFIX:-}" ]; then
    esc="$(printf '%s' "$DOWNLOAD_PREFIX" | sed 's/[][\.*^$#|&]/\\&/g')"
    sed -E "s#url=\"${esc}($APP_NAME-([0-9]+\\.[0-9]+\\.[0-9]+)\\.dmg)\"#url=\"${DOWNLOAD_PREFIX}${RELEASE_TAG_PREFIX}-\\2/\\1\"#g" "$STAGE/appcast.xml" > "$STAGE/appcast.rewritten"
    mv "$STAGE/appcast.rewritten" "$STAGE/appcast.xml"
  fi
  cp "$STAGE/appcast.xml" "$DOWNLOADS/appcast.xml"
fi
echo "OK $DOWNLOADS/appcast.xml ($(grep -c '<item>' "$DOWNLOADS/appcast.xml") item(s)); latest = $BASE"

if [ "$PUBLISH" -eq 1 ]; then
  CMD=(gh release create "$TAG" --repo "${REPO_SLUG:?REPO_SLUG not set}" --title "$APP_NAME $VER" --notes "$APP_NAME $VER"
       "$DOWNLOADS/$BASE" "$DOWNLOADS/$APP_NAME-latest.dmg" "$DOWNLOADS/appcast.xml")
  if [ "$DRY" -eq 1 ]; then echo "DRY RUN, not run: ${CMD[*]}"; else "${CMD[@]}" || die "gh release create failed"; fi
fi
