#!/bin/bash
# release.sh: build, sign, notarize, staple and package jusage into <out>/jusage-<version>.dmg, then prove
# the DMG works as a quarantined download and stage the Sparkle appcast. From the house kit's release module
# (modules/release), with jusage's changes: the signing identity is read from the login keychain at run time
# (never written into a tracked file), a --dry-run that signs nothing, and the appcast step.
#
#   scripts/release.sh [options]
#
# Pipeline: read VERSION -> preflight -> build_app (release.conf: app/build-app.sh) -> house-kit gate (the
#   bundled files every app must carry) -> codesign helpers (hardened runtime, timestamp, per-helper
#   entitlements) -> codesign nested code and the app -> notarize + staple app -> DMG (Applications link) ->
#   sign DMG -> notarize + staple DMG -> verify (codesign, spctl, stapler, slices, minimum macOS) -> smoke
#   test the DMG as a quarantined download -> optional git tag <TAG_PREFIX>-<version> -> sha256 -> appcast
#   (scripts/appcast.sh stages the DMG and the GitHub Releases feed under <out>/downloads; publishing is the
#   separate `appcast.sh --publish`).
# Everything is logged to <out>/release-<version>.log. On notarization failure the notarytool log is
# fetched and printed. A failure ends with one line naming that log.
#
# Options (all optional):
#   --conf FILE         the app's settings (default: release.conf beside this script)
#   --version X         override the VERSION file
#   --app-path PATH     skip build_app, package this already-built .app instead
#   --out-dir DIR       output dir (default <repo>/dist; gitignored)
#   --no-notarize       sign + package only. Output is named *-UNNOTARIZED.dmg. Never ship it.
#   --dry-run           sign NOTHING, notarize nothing, tag nothing, upload nothing: build, gate, package an
#                       unsigned DMG named *-DRYRUN.dmg and check the slices and minimum macOS. Needs no keychain.
#   --show-identity     print the signing identity and team found in the keychain, then exit (signs nothing)
#   --plain-dmg         skip create-dmg (Finder layout); use plain hdiutil (no icon positions)
#   --no-smoke          skip the quarantined-download smoke test
#   --check-only        run ONLY the house-kit gate on --app-path and exit (no signing; for a retrofit)
#   --tag               after success, create git tag <TAG_PREFIX>-<version> (refused if it exists)
#   -h, --help
#
# Env overrides: SIGN_IDENTITY (full name, needed only when the keychain holds several Developer ID
# Application identities), NOTARY_PROFILE (the notarytool keychain profile).
set -eE -o pipefail
SCRIPT_LABEL="release"
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

# The signing identity is looked up in the login keychain when a run needs it (resolve_identity below), so no
# name or team id is tracked in the repo. The notary credentials are a keychain profile.
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
TEAM_ID=""
NOTARY_PROFILE="${NOTARY_PROFILE:-artemis-notary}"

CONF="$HERE/release.conf"; APP_PATH=""; OUT_DIR=""; VERSION=""
NOTARIZE=1; PLAIN_DMG=0; SMOKE=1; DO_TAG=0; CHECK_ONLY=0; DRY_RUN=0; SHOW_IDENTITY=0; SIGNING=1
while [ $# -gt 0 ]; do
  case "$1" in
    --conf) CONF="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --app-path) APP_PATH="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --no-notarize) NOTARIZE=0; shift ;;
    --dry-run) DRY_RUN=1; NOTARIZE=0; SIGNING=0; SMOKE=0; shift ;;
    --show-identity) SHOW_IDENTITY=1; shift ;;
    --plain-dmg) PLAIN_DMG=1; shift ;;
    --no-smoke) SMOKE=0; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --tag) DO_TAG=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# ------------------------------------------------------------------ the app's settings
# Defaults; release.conf overrides them. See release.conf for what each one means.
APP_NAME=""; BUNDLE_ID_PREFIX=""; TAG_PREFIX=""
MIN_MACOS="12.0"; APP_ARCHS="arm64 x86_64"
ENTITLEMENTS=""; DMG_BACKGROUND=""; DMG_EXTRAS=()
REQUIRED_RESOURCES=(uninstall.sh uninstall.manifest)
REQUIRED_PAGES=("Manual.pdf|Manual.html" "WhatsNew.html|WhatsNew.txt" "Licenses.html|Licenses.txt")
SINGLE_ARCH_OK=()            # Mach-O file names allowed to be single-CPU (name:arch)
build_app() { die "release.conf does not define build_app (or pass --app-path)"; }
helper_entitlements() { return 1; }
smoke_extra() { return 0; }
[ -f "$CONF" ] || { echo "no release.conf at $CONF (copy modules/release/release.conf and fill it in)" >&2; exit 2; }
# shellcheck disable=SC1090
. "$CONF"
[ -n "$APP_NAME" ] && [ "$APP_NAME" != "APP_NAME" ] || { echo "release.conf: APP_NAME is not filled in" >&2; exit 2; }
[ -n "$BUNDLE_ID_PREFIX" ] && [ "$BUNDLE_ID_PREFIX" != "BUNDLE_ID" ] || { echo "release.conf: BUNDLE_ID_PREFIX is not filled in" >&2; exit 2; }
[ -n "$TAG_PREFIX" ] || TAG_PREFIX="$APP_NAME"

[ -n "$VERSION" ] || VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null || true)"
[ -n "$VERSION" ] || { echo "no version: VERSION file missing/empty and --version not given" >&2; exit 2; }
grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' <<<"$VERSION" || { echo "bad version '$VERSION' (want N.N.N)" >&2; exit 2; }
[ -n "$OUT_DIR" ] || OUT_DIR="$REPO_ROOT/dist"

# ------------------------------------------------------------------ the house-kit gate
# house_gate <app>: the files and keys every app must carry (SOP.md). Prints one line per check; returns the
# number missing. This is what stops a release that forgot the uninstaller, the manual or the version.
house_gate() {
  local app="$1" res="$1/Contents/Resources" plist="$1/Contents/Info.plist" bad=0 f alt found ver
  for f in "${REQUIRED_RESOURCES[@]}"; do
    if [ -f "$res/$f" ]; then log "GATE ok: Contents/Resources/$f"; else _emit FAIL "GATE: Contents/Resources/$f is missing"; bad=$((bad + 1)); fi
  done
  for alt in "${REQUIRED_PAGES[@]}"; do
    found=""
    local IFS='|'; for f in $alt; do [ -f "$res/$f" ] && { found="$f"; break; }; done; unset IFS
    if [ -n "$found" ]; then log "GATE ok: Contents/Resources/$found"; else _emit FAIL "GATE: none of ${alt//|/ / } is in Contents/Resources (Help menu would fail)"; bad=$((bad + 1)); fi
  done
  if [ -f "$res/uninstall.manifest" ]; then
    if grep -Eq 'APP_NAME|BUNDLE_ID' "$res/uninstall.manifest"; then _emit FAIL "GATE: uninstall.manifest still has placeholders"; bad=$((bad + 1))
    elif ! grep -Eq "^[[:space:]]*app[[:space:]]+/Applications/$APP_NAME\.app[[:space:]]*$" "$res/uninstall.manifest"; then _emit FAIL "GATE: uninstall.manifest has no 'app /Applications/$APP_NAME.app' line"; bad=$((bad + 1))
    else log "GATE ok: uninstall.manifest names /Applications/$APP_NAME.app"; fi
  fi
  ver="$(plutil -extract CFBundleShortVersionString raw -o - "$plist" 2>/dev/null || true)"
  if [ "$ver" = "$VERSION" ]; then log "GATE ok: CFBundleShortVersionString $ver = VERSION"; else _emit FAIL "GATE: CFBundleShortVersionString is '$ver', VERSION is '$VERSION'"; bad=$((bad + 1)); fi
  if [ -d "$app/Contents/Frameworks/Sparkle.framework" ]; then
    for f in SUFeedURL SUPublicEDKey; do
      if [ -n "$(plutil -extract "$f" raw -o - "$plist" 2>/dev/null || true)" ]; then log "GATE ok: Info.plist $f"; else _emit FAIL "GATE: Sparkle is bundled but Info.plist has no $f"; bad=$((bad + 1)); fi
    done
    if [ "$(plutil -extract SUPublicEDKey raw -o - "$plist" 2>/dev/null || true)" = "__SU_PUBLIC_ED_KEY__" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then warn "GATE: SUPublicEDKey is still the placeholder __SU_PUBLIC_ED_KEY__ (a real release refuses; fine for a dry run)"
      else _emit FAIL "GATE: Info.plist SUPublicEDKey is still the placeholder __SU_PUBLIC_ED_KEY__: make the key with generate_keys first (RULINGS 37)"; bad=$((bad + 1)); fi
    fi
  else
    _emit FAIL "GATE: no Sparkle.framework in the app: Sparkle updates are REQUIRED in every native Mac app (J 2026-11-18); paste update-check (--with-sparkle)"; bad=$((bad + 1))
  fi
  return "$bad"
}

# resolve_identity: the Developer ID Application identity comes from the login keychain, at the moment a run
# needs it. SIGN_IDENTITY in the environment wins; otherwise exactly one such identity must exist. Sets
# SIGN_IDENTITY and TEAM_ID (the team is the parenthesised id at the end of the identity's name). Prints a plain
# line and returns 1 when it cannot decide. Nothing here names a person or a team.
resolve_identity() {
  local listing names n
  listing="$(security find-identity -v -p codesigning 2>/dev/null || true)"
  names="$(printf '%s\n' "$listing" | sed -n 's/^[^"]*"\(Developer ID Application: [^"]*\)".*$/\1/p' | sort -u)"
  if [ -n "$SIGN_IDENTITY" ]; then
    printf '%s\n' "$names" | grep -qxF "$SIGN_IDENTITY" || { echo "release.sh: SIGN_IDENTITY '$SIGN_IDENTITY' is not a valid Developer ID Application identity in the keychain" >&2; return 1; }
  else
    n="$(printf '%s\n' "$names" | grep -c . || true)"
    if [ "$n" -eq 0 ]; then echo "release.sh: no 'Developer ID Application' identity in the login keychain (security find-identity -v -p codesigning)" >&2; return 1; fi
    if [ "$n" -gt 1 ]; then echo "release.sh: $n Developer ID Application identities in the keychain; set SIGN_IDENTITY to the full name of the one to use" >&2; return 1; fi
    SIGN_IDENTITY="$names"
  fi
  TEAM_ID="$(printf '%s' "$SIGN_IDENTITY" | sed -n 's/^.*(\([A-Z0-9]\{10\}\))$/\1/p')"
  [ -n "$TEAM_ID" ] || { echo "release.sh: cannot read a team id from '$SIGN_IDENTITY'" >&2; return 1; }
}

if [ "$SHOW_IDENTITY" -eq 1 ]; then
  resolve_identity || exit 1
  echo "identity: $SIGN_IDENTITY"
  echo "team: $TEAM_ID"
  exit 0
fi
if [ "$DRY_RUN" -eq 1 ] && [ "$DO_TAG" -eq 1 ]; then echo "--dry-run never tags: drop --tag" >&2; exit 2; fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  [ -d "$APP_PATH" ] || { echo "--check-only needs --app-path <App>.app" >&2; exit 2; }
  if house_gate "$APP_PATH"; then log "HOUSE GATE OK: $APP_PATH"; exit 0; else rc=$?; _emit FAIL "HOUSE GATE: $rc thing(s) missing in $APP_PATH"; exit 1; fi
fi

[ -n "$ENTITLEMENTS" ] || ENTITLEMENTS="scripts/$APP_NAME.entitlements"
case "$ENTITLEMENTS" in /*) ;; *) ENTITLEMENTS="$REPO_ROOT/$ENTITLEMENTS" ;; esac
mkdir -p "$OUT_DIR"
init_log "$OUT_DIR/release-$VERSION.log"
SUFFIX=""
if [ "$DRY_RUN" -eq 1 ]; then SUFFIX="-DRYRUN"; elif [ "$NOTARIZE" -eq 0 ]; then SUFFIX="-UNNOTARIZED"; fi
DMG_NAME="$APP_NAME-$VERSION$SUFFIX.dmg"
DMG_OUT="$OUT_DIR/$DMG_NAME"
BUILD="$OUT_DIR/build-$VERSION"
APP="$BUILD/stage/$APP_NAME.app"
if [ "$DRY_RUN" -eq 1 ]; then warn "DRY RUN: nothing is signed, notarized, tagged or uploaded; the DMG is unsigned and never ships"
elif [ "$NOTARIZE" -eq 0 ]; then warn "--no-notarize: output is NOT notarized and will be blocked by Gatekeeper on other Macs"; fi
# A release never overwrites a file under an existing version's name (J's version rule).
[ ! -e "$OUT_DIR/$APP_NAME-$VERSION.dmg" ] || [ "$NOTARIZE" -eq 0 ] || die "$OUT_DIR/$APP_NAME-$VERSION.dmg already exists: bump VERSION, never overwrite a shipped version"

# single_arch <mach-o basename>: echo the one arch it may have; echo nothing = it must have every APP_ARCHS slice.
# (Always returns 0: under `set -E` a non-zero return inside $(...) would fire the ERR trap as a false failure.)
single_arch() {
  local e
  for e in "${SINGLE_ARCH_OK[@]}"; do
    if [ "${e%%:*}" = "$1" ]; then echo "${e#*:}"; return 0; fi
  done
  return 0
}
# minos_of <file> <arch>: the slice's minimum macOS (LC_BUILD_VERSION, or LC_VERSION_MIN_MACOSX in old binaries)
minos_of() {
  local m
  m="$(vtool -arch "$2" -show-build "$1" 2>/dev/null | awk '$1 == "minos" {print $2; exit}')"
  [ -n "$m" ] || m="$(otool -arch "$2" -l "$1" 2>/dev/null | awk '/LC_VERSION_MIN_MACOSX/ {f=1} f && $1 == "version" {print $2; exit}')"
  printf '%s' "$m"
}
# version_le A B: true when A <= B (dotted versions)
version_le() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$1" ]; }

# ------------------------------------------------------------------ 0. preflight
step "0. preflight"
for c in ditto hdiutil shasum plutil file vtool otool lipo; do require_cmd "$c"; done
if [ "$SIGNING" -eq 1 ]; then for c in codesign xcrun spctl security; do require_cmd "$c"; done; fi
[ -f "$ENTITLEMENTS" ] || die "app entitlements not found: $ENTITLEMENTS"
plutil -lint "$ENTITLEMENTS" >/dev/null || die "bad plist: $ENTITLEMENTS"
# plutil accepts XML comments but the kernel's AMFI parser rejects some ("--" inside) -> codesign fails late
if grep -q '<!--' "$ENTITLEMENTS"; then die "$ENTITLEMENTS contains an XML comment; AMFI may reject it. Put notes in a README instead."; fi
if [ "$SIGNING" -eq 1 ]; then
  resolve_identity || die "no usable signing identity (line above)"
  log "signing as '$SIGN_IDENTITY' (team $TEAM_ID)"
fi
if [ "$NOTARIZE" -eq 1 ]; then
  capture xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" || die "notary profile '$NOTARY_PROFILE' not usable (see output above). Re-store: xcrun notarytool store-credentials $NOTARY_PROFILE"
fi

# ------------------------------------------------------------------ 1. build
rm -rf "$BUILD"; mkdir -p "$BUILD/stage"
BUILD_NUM="$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || date +%y%m%d%H%M)"
if [ -z "$APP_PATH" ]; then
  step "1. build_app (release.conf)"
  mkdir -p "$BUILD/built"
  build_app "$BUILD/built" "$VERSION" "$BUILD_NUM" || die "build_app failed (full output in $LOG_FILE)"
  BUILT="$BUILD/built/$APP_NAME.app"
  [ -d "$BUILT" ] || die "build_app left no $APP_NAME.app in $BUILD/built"
else
  step "1. using prebuilt app $APP_PATH (skipping build_app)"
  [ -d "$APP_PATH" ] || die "--app-path not found: $APP_PATH"
  BUILT="$APP_PATH"
fi
run ditto "$BUILT" "$APP" || die "copy of built app failed"
xattr -cr "$APP" 2>/dev/null || true
PLIST="$APP/Contents/Info.plist"
[ -f "$PLIST" ] || die "no Info.plist in $APP"
EXE="$APP/Contents/MacOS/$(plutil -extract CFBundleExecutable raw -o - "$PLIST")"
log "built app: bundle id $(plutil -extract CFBundleIdentifier raw -o - "$PLIST"), version $(plutil -extract CFBundleShortVersionString raw -o - "$PLIST" 2>/dev/null || echo '?')"
capture lipo -archs "$EXE" || die "cannot read app executable"
for a in $APP_ARCHS; do
  case " $CAPTURED " in *" $a "*) log "app executable has $a slice" ;; *) die "app executable has no $a slice ($CAPTURED); it must have: $APP_ARCHS" ;; esac
done

step "1b. house-kit gate"
house_gate "$APP" || die "the app is missing house-kit pieces (lines above); see SOP.md"

# ------------------------------------------------------------------ 2. helpers
HELPERS="$APP/Contents/Helpers"
# sign_code <label> <codesign args...>: signs for real, or in a dry run only says what it would sign.
sign_code() {
  local label="$1"; shift
  if [ "$SIGNING" -eq 1 ]; then run codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$@" || die "codesign failed: $label"
  else log "DRY RUN would sign: $label"; fi
}

if [ -d "$HELPERS" ] && [ -n "$(/bin/ls -A "$HELPERS")" ]; then
  step "2. sign helpers"
  for f in "$HELPERS"/*; do
    [ -f "$f" ] || continue
    n="$(basename "$f")"
    ent="$(helper_entitlements "$n")" || die "no entitlements for helper '$n' (define helper_entitlements in release.conf)"
    case "$ent" in /*) ;; *) ent="$REPO_ROOT/$ent" ;; esac
    [ -f "$ent" ] || die "entitlements file for helper $n not found: $ent"
    sign_code "helper $n" --identifier "$BUNDLE_ID_PREFIX.helper.$n" --entitlements "$ent" "$f"
  done
else
  log "2. no Contents/Helpers: nothing to sign there"
fi

# ------------------------------------------------------------------ 3. nested code + app
step "3. sign nested code (frameworks/XPC/plugins/dylibs) and the app"
# Deepest paths first so inner code is signed before its container. Besides the bundles, every loose Mach-O
# file inside Contents/Frameworks is signed on its own (Sparkle's Autoupdate is one: a bundle signature does
# not cover it, and Apple's notary rejects it unsigned).
{
  find "$APP/Contents" \( -name '*.framework' -o -name '*.xpc' -o -name '*.appex' -o -name '*.app' -o -name '*.dylib' -o -name '*.bundle' \) \
       -not -path "$HELPERS/*" -print 2>/dev/null
  if [ -d "$APP/Contents/Frameworks" ]; then
    find "$APP/Contents/Frameworks" -type f -print 2>/dev/null | while IFS= read -r f; do
      if [ -n "$(lipo -archs "$f" 2>/dev/null || true)" ]; then printf '%s\n' "$f"; fi
    done
  fi
} | awk '{ n=gsub("/","/"); print n "\t" $0 }' | sort -rn | cut -f2- \
  | while IFS= read -r nested; do sign_code "${nested#"$APP/"}" "$nested"; done
sign_code "$APP_NAME.app" --entitlements "$ENTITLEMENTS" "$APP"
if [ "$SIGNING" -eq 1 ]; then run codesign --verify --deep --strict --verbose=2 "$APP" || die "app does not verify after signing"; fi

# ------------------------------------------------------------------ notarization helpers
# notarize <file> <label>: submit, wait, on failure print the notarytool log and die.
notarize() {
  local file="$1" label="$2" out id status
  [ "$NOTARIZE" -eq 1 ] || { warn "skipping notarization of $label (--no-notarize)"; return 0; }
  step "notarize $label ($(du -h "$file" | cut -f1))"
  set +e
  out="$(xcrun notarytool submit "$file" --keychain-profile "$NOTARY_PROFILE" --wait --output-format json 2>&1)"
  local rc=$?
  set -e
  printf '%s\n' "$out" >> "$LOG_FILE"
  id="$(printf '%s' "$out" | plutil -extract id raw -o - - 2>/dev/null || true)"
  status="$(printf '%s' "$out" | plutil -extract status raw -o - - 2>/dev/null || true)"
  log "$label: submission id=${id:-?} status=${status:-?} (notarytool exit $rc)"
  if [ "$rc" -ne 0 ] || [ "$status" != "Accepted" ]; then
    printf '%s\n' "$out" >&2
    if [ -n "$id" ]; then
      warn "fetching notarytool log for $id"
      xcrun notarytool log "$id" --keychain-profile "$NOTARY_PROFILE" 2>&1 | tee -a "$LOG_FILE" >&2 || true
    fi
    die "notarization of $label FAILED (status=${status:-unknown}); log above"
  fi
  log "$label: notarization Accepted"
}

staple() { # staple <path> <label>
  [ "$NOTARIZE" -eq 1 ] || return 0
  run xcrun stapler staple "$1" || die "stapling $2 failed"
  run xcrun stapler validate "$1" || die "stapler validate failed for $2"
}

# ------------------------------------------------------------------ 4. notarize + staple app
step "4. notarize + staple app"
ZIP="$BUILD/$APP_NAME-notarize.zip"
run ditto -c -k --keepParent "$APP" "$ZIP" || die "zip of app failed"
notarize "$ZIP" "$APP_NAME.app"
staple "$APP" "$APP_NAME.app"
rm -f "$ZIP"

# ------------------------------------------------------------------ 5. DMG
step "5. build DMG"
VOLNAME="$APP_NAME $VERSION"
BG=""
if [ -n "$DMG_BACKGROUND" ]; then
  case "$DMG_BACKGROUND" in /*) BG="$DMG_BACKGROUND" ;; *) BG="$REPO_ROOT/$DMG_BACKGROUND" ;; esac
  [ -f "$BG" ] || die "DMG background missing: $BG"
fi
rm -f "$DMG_OUT"
DMG_STAGE="$BUILD/dmgsrc"; rm -rf "$DMG_STAGE"; mkdir -p "$DMG_STAGE"
ditto "$APP" "$DMG_STAGE/$APP_NAME.app"
for extra in "${DMG_EXTRAS[@]}"; do          # e.g. the manual rides along on the DMG
  case "$extra" in /*) ;; *) extra="$REPO_ROOT/$extra" ;; esac
  [ -f "$extra" ] || die "DMG extra missing: $extra"
  cp "$extra" "$DMG_STAGE/"
done
# stale mounts of a previous run with the same volume name would make Finder layout hit the wrong disk
if [ -d "/Volumes/$VOLNAME" ]; then
  warn "/Volumes/$VOLNAME is already mounted; detaching"
  hdiutil detach "/Volumes/$VOLNAME" -force >>"$LOG_FILE" 2>&1 || die "could not detach stale /Volumes/$VOLNAME"
fi
MADE_FANCY=0
if [ "$PLAIN_DMG" -eq 0 ] && [ -n "$BG" ] && command -v create-dmg >/dev/null 2>&1; then
  # create-dmg drives Finder via AppleScript to place icons: needs a logged-in GUI session.
  if run create-dmg --volname "$VOLNAME" --background "$BG" --window-pos 200 120 --window-size 660 400 \
       --icon-size 128 --text-size 13 --icon "$APP_NAME.app" 170 190 --hide-extension "$APP_NAME.app" \
       --app-drop-link 490 190 --format UDZO --no-internet-enable "$DMG_OUT" "$DMG_STAGE"; then
    MADE_FANCY=1
  else
    warn "create-dmg failed (Finder automation denied / no GUI session?). Falling back to plain hdiutil DMG."
    hdiutil detach "/Volumes/$VOLNAME" -force >>"$LOG_FILE" 2>&1 || true
    rm -f "$DMG_OUT" "$OUT_DIR"/rw.*.dmg
  fi
fi
if [ "$MADE_FANCY" -eq 0 ]; then
  ln -s /Applications "$DMG_STAGE/Applications"
  if [ -n "$BG" ]; then mkdir -p "$DMG_STAGE/.background"; cp "$BG" "$DMG_STAGE/.background/background.png"; fi
  run hdiutil create -volname "$VOLNAME" -srcfolder "$DMG_STAGE" -fs HFS+ -format UDZO -ov "$DMG_OUT" || die "hdiutil create failed"
fi
[ -f "$DMG_OUT" ] || die "DMG was not produced"
log "DMG built ($([ "$MADE_FANCY" -eq 1 ] && echo create-dmg || echo plain-hdiutil)): $(du -h "$DMG_OUT" | cut -f1)"

# ------------------------------------------------------------------ 6. sign, notarize, staple DMG
step "6. sign + notarize + staple DMG"
if [ "$SIGNING" -eq 1 ]; then run codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG_OUT" || die "codesign of DMG failed"; else log "DRY RUN would sign: $DMG_NAME"; fi
notarize "$DMG_OUT" "$DMG_NAME"
staple "$DMG_OUT" "$DMG_NAME"

# ------------------------------------------------------------------ 7. verify
step "7. verify"
V=0; vfail() { _emit FAIL "VERIFY: $*"; V=$((V + 1)); }
vrun() { local what="$1"; shift; if capture "$@"; then log "VERIFY ok: $what"; return 0; else vfail "$what"; return 1; fi; }
if [ "$SIGNING" -eq 1 ]; then
  vrun "codesign --verify --deep --strict app" codesign --verify --deep --strict --verbose=2 "$APP" || true
  vrun "codesign verify dmg" codesign --verify --strict --verbose=2 "$DMG_OUT" || true
else
  vrun "hdiutil verify dmg" hdiutil verify "$DMG_OUT" || true
fi
# every Mach-O file in the bundle: signed by the team, hardened runtime, the slices it should have, and able to
# run on MIN_MACOS (a slice built for a newer macOS refuses to start on the older Macs the app is for)
while IFS= read -r mo; do
  marchs="$(lipo -archs "$mo" 2>/dev/null || true)"
  [ -n "$marchs" ] || continue
  rel="${mo#"$APP/"}"
  if [ "$SIGNING" -eq 1 ]; then
    # capture first: `codesign | grep -q` under pipefail fails at random when grep exits early (SIGPIPE)
    info="$(codesign -dvv "$mo" 2>&1 || true)"
    grep -q "TeamIdentifier=$TEAM_ID" <<<"$info" || vfail "$rel not signed by team $TEAM_ID"
    if file "$mo" | grep -q "executable"; then grep -q "flags=0x10000(runtime)" <<<"$info" || vfail "$rel lacks hardened runtime"; fi
    grep -q "^Timestamp=" <<<"$info" || vfail "$rel has no secure timestamp"
  fi
  only="$(single_arch "$(basename "$mo")")"
  if [ -n "$only" ]; then want_archs="$only"; else want_archs="$APP_ARCHS"; fi
  for a in $want_archs; do
    case " $marchs " in *" $a "*) ;; *) vfail "$rel has no $a slice ($marchs)" ;; esac
  done
  for a in $marchs; do
    mmin="$(minos_of "$mo" "$a")"
    if [ -n "$mmin" ] && version_le "$mmin" "$MIN_MACOS"; then
      log "VERIFY minimum macOS: $rel ($a) -> $mmin (limit $MIN_MACOS)"
    else
      vfail "$rel ($a) needs macOS '${mmin:-unknown}', above the limit $MIN_MACOS"
    fi
  done
done < <(find "$APP/Contents" -type f -perm -u+x)
lsmin="$(plutil -extract LSMinimumSystemVersion raw -o - "$PLIST" 2>/dev/null || true)"
if [ "$lsmin" = "$MIN_MACOS" ]; then log "VERIFY ok: LSMinimumSystemVersion $lsmin"; else vfail "LSMinimumSystemVersion is '$lsmin', want $MIN_MACOS"; fi
if [ "$NOTARIZE" -eq 1 ]; then
  vrun "spctl -a -vv app (Notarized Developer ID)" spctl -a -vv "$APP" || true
  grep -q "Notarized Developer ID" <<<"$CAPTURED" || vfail "app spctl source is not 'Notarized Developer ID': $CAPTURED"
  vrun "spctl -a -t open --context context:primary-signature -v dmg" spctl -a -t open --context context:primary-signature -v "$DMG_OUT" || true
  grep -q "Notarized Developer ID" <<<"$CAPTURED" || vfail "dmg spctl source is not 'Notarized Developer ID': $CAPTURED"
  vrun "stapler validate app" xcrun stapler validate "$APP" || true
  vrun "stapler validate dmg" xcrun stapler validate "$DMG_OUT" || true
else
  warn "verification of Gatekeeper/notarization skipped (--no-notarize / --dry-run)"
fi
[ "$V" -eq 0 ] || die "$V verification check(s) failed"

# ------------------------------------------------------------------ 8. smoke test as a quarantined download
if [ "$SMOKE" -eq 1 ] && [ "$NOTARIZE" -eq 1 ]; then
  step "8. smoke test: quarantined download"
  # SMOKE_LAUNCH=1 (default): also launch the quarantined copy through LaunchServices with --smoke-test <file>;
  # the app checks itself and writes OK (the sample's Smoke.swift is the pattern).
  export SMOKE_LAUNCH="${SMOKE_LAUNCH:-1}" SMOKE_ARCHS_WANTED="$APP_ARCHS"
  run "$HERE/lib/smoke-test-dmg.sh" "$DMG_OUT" "$APP_NAME" || die "smoke test failed (see above)"
  smoke_extra "$DMG_OUT" || die "smoke_extra (release.conf) failed"
else
  warn "smoke test skipped"
fi

if [ "$DO_TAG" -eq 1 ]; then
  step "tag $TAG_PREFIX-$VERSION"
  run git -C "$REPO_ROOT" tag "$TAG_PREFIX-$VERSION" || die "git tag failed (already exists? a shipped version is never re-tagged: bump VERSION)"
fi

SHA="$(shasum -a 256 "$DMG_OUT" | awk '{print $1}')"
log "RELEASE OK: $DMG_OUT"
log "sha256 $SHA"
printf '%s  %s\n' "$SHA" "$DMG_NAME" > "$DMG_OUT.sha256"
rm -rf "$BUILD"
if [ "$NOTARIZE" -eq 1 ]; then
  step "9. stage the Sparkle appcast (publishing is a separate step: scripts/appcast.sh --publish)"
  run "$HERE/appcast.sh" "$DMG_OUT" || die "appcast.sh failed"
else
  warn "no appcast: an unsigned or unnotarized DMG never goes in the feed"
fi
