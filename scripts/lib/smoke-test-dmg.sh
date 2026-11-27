#!/bin/bash
# smoke-test-dmg.sh <dmg> <AppName>
# Simulates a user downloading the DMG in a browser and checks the app would really work.
# Generalised from Jlipper (scripts/lib/smoke-test-dmg.sh).
#   1. copy the DMG to a temp dir and stamp com.apple.quarantine on it (as Safari/Chrome do)
#   2. Gatekeeper-assess the quarantined DMG, mount it, check the app and the Applications link are on it
#   3. copy the app out (as a drag to /Applications), stamp quarantine on the copy
#   4. spctl + codesign --deep --strict + stapler on the copy
#   5. SMOKE_LAUNCH=1: launch the quarantined copy through LaunchServices with `--smoke-test <file>`, once per
#      CPU this Mac can run, and require the app to write OK into that file. The app's side of this is a
#      windowless self-check (sample/Sources/HouseSample/Smoke.swift is the pattern).
# Always unmounts and deletes its temp dir. Exit non-zero on any failure. Log: appended to $LOG_FILE if the
# caller set it, else <repo>/scripts/logs/smoke-test.log.
set -eE -o pipefail
SCRIPT_LABEL="smoke-test"
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

DMG="${1:?usage: smoke-test-dmg.sh <dmg> <AppName>}"
APP_NAME="${2:?usage: smoke-test-dmg.sh <dmg> <AppName>}"
[ -f "$DMG" ] || { echo "dmg not found: $DMG" >&2; exit 2; }
if [ -z "${LOG_FILE:-}" ]; then init_log "$REPO_ROOT/scripts/logs/smoke-test.log"; fi

T="$(mktemp -d "${TMPDIR:-/tmp}/smoke.XXXXXX")"
MNT="$T/mnt"
cleanup() {
  hdiutil detach "$MNT" -force >/dev/null 2>&1 || true
  rm -rf "$T"
}
trap cleanup EXIT
FAILS=0
bad() { _emit FAIL "SMOKE: $*"; FAILS=$((FAILS + 1)); }
quarantine() { xattr -w com.apple.quarantine "0081;$(printf '%x' "$(date +%s)");Safari;" "$1"; }

step "smoke 1: quarantined copy of $(basename "$DMG")"
cp "$DMG" "$T/dl.dmg"
quarantine "$T/dl.dmg"
log "xattr on dmg: $(xattr -p com.apple.quarantine "$T/dl.dmg")"
if capture spctl -a -t open --context context:primary-signature -v "$T/dl.dmg"; then
  case "$CAPTURED" in *"Notarized Developer ID"*) log "SMOKE ok: Gatekeeper accepts quarantined DMG (Notarized Developer ID)" ;; *) bad "dmg accepted but not as Notarized Developer ID: $CAPTURED" ;; esac
else bad "Gatekeeper REJECTS quarantined DMG"; fi

step "smoke 2: mount"
mkdir -p "$MNT"
capture hdiutil attach "$T/dl.dmg" -nobrowse -readonly -mountpoint "$MNT" || die "could not mount quarantined DMG"
[ -d "$MNT/$APP_NAME.app" ] || die "no $APP_NAME.app on the mounted DMG: $(ls -A "$MNT")"
if [ -L "$MNT/Applications" ]; then log "SMOKE ok: Applications symlink present -> $(readlink "$MNT/Applications")"; else bad "no Applications symlink on DMG"; fi

step "smoke 3: copy app out (simulated drag to Applications), re-quarantine"
mkdir -p "$T/Applications"
ditto "$MNT/$APP_NAME.app" "$T/Applications/$APP_NAME.app"
xattr -w -r com.apple.quarantine "0081;$(printf '%x' "$(date +%s)");Safari;" "$T/Applications/$APP_NAME.app"
COPY="$T/Applications/$APP_NAME.app"

step "smoke 4: Gatekeeper, signature and ticket on the quarantined copy"
if capture spctl -a -vv "$COPY"; then
  case "$CAPTURED" in *"Notarized Developer ID"*) log "SMOKE ok: Gatekeeper accepts quarantined app copy (Notarized Developer ID)" ;; *) bad "app accepted but not Notarized Developer ID: $CAPTURED" ;; esac
else bad "Gatekeeper REJECTS quarantined app copy"; fi
if capture codesign --verify --deep --strict --verbose=2 "$COPY"; then log "SMOKE ok: codesign --deep --strict"; else bad "codesign verify failed on copy"; fi
if capture xcrun stapler validate "$COPY"; then log "SMOKE ok: stapler validate (offline ticket present)"; else bad "stapler validate failed on copy"; fi
for f in uninstall.sh uninstall.manifest; do
  if [ -f "$COPY/Contents/Resources/$f" ]; then log "SMOKE ok: $f is in the shipped app"; else bad "$f is not in the shipped app"; fi
done

if [ "${SMOKE_LAUNCH:-0}" = "1" ]; then
  step "smoke 5: launch the app via LaunchServices (--smoke-test)"
  # Every CPU the app claims that this Mac can run (x86_64 through Rosetta on Apple silicon).
  HOST="$(uname -m)"; ARCHS=""
  for A in ${SMOKE_ARCHS_WANTED:-$HOST}; do
    if [ "$A" = "$HOST" ]; then ARCHS="$ARCHS $A"
    elif [ "$HOST" = "arm64" ] && [ "$A" = "x86_64" ] && arch -x86_64 /usr/bin/true 2>/dev/null; then ARCHS="$ARCHS $A"
    else warn "cannot run the $A slice on this $HOST Mac: not launched (test it on a $A Mac)"; fi
  done
  for A in $ARCHS; do
    OUTF="$T/launch-result-$A.txt"
    open --arch "$A" -W -n -a "$COPY" --args --smoke-test "$OUTF" || bad "[$A] open failed (Gatekeeper dialog / launch error)"
    if [ -f "$OUTF" ] && head -1 "$OUTF" | grep -q '^OK'; then
      log "SMOKE ok: [$A] app launched and checked itself: $(tr '\n' ' ' < "$OUTF" | cut -c1-300)"
    else
      bad "[$A] app launch result missing or not OK: $(cat "$OUTF" 2>/dev/null)"
    fi
  done
fi

if [ "$FAILS" -ne 0 ]; then die "$FAILS smoke check(s) failed"; fi
log "SMOKE PASSED for $(basename "$DMG")"
