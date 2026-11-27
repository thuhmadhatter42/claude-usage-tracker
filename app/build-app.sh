#!/bin/bash
# build-app.sh: builds the universal jusage.app into DEST_DIR.
#   app/build-app.sh [--dev] DEST_DIR
#
#   swift build -c release --arch arm64 --arch x86_64, then the bundle is assembled in DEST_DIR/.jusage.app.building:
#   the binary, Sparkle.framework, engine/ (no tests, no __pycache__), VERSION, themes.json, the icon (regenerated
#   from make_icon.swift) and the other app/Resources files, with Info.plist stamped from app/Resources/Info.plist.in
#   and VERSION. Only a finished bundle is swapped in as DEST_DIR/jusage.app: the old one is renamed to
#   .jusage.app.old and then moved to the Trash, so a running app is never removed out from under itself and a
#   failed build leaves the old app untouched. The bundle is ad-hoc signed so it runs; release.sh signs for real.
#
#   --dev   bundle id com.goatdguild.jusage.dev, JusageStateDir ~/.claude-usage-dev, no automatic update checks
#
# DEST_DIR must not be under /Applications: build into dist/ and `ditto dist/jusage.app /Applications/jusage.app`.
# Log: DEST_DIR/build-app.log (a failure ends with one line naming it).
# Env: JUSAGE_TRASH = the command that moves a path to the Trash (default /usr/bin/trash).
set -eE -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SCRIPT_LABEL="build-app"
# shellcheck source=../scripts/lib/common.sh
. "$REPO_ROOT/scripts/lib/common.sh"

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }
DEV=0; DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "build-app.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
    *) [ -z "$DEST" ] || { echo "build-app.sh: one DEST_DIR only" >&2; exit 2; }; DEST="$1"; shift ;;
  esac
done
[ -n "$DEST" ] || { echo "usage: build-app.sh [--dev] DEST_DIR" >&2; exit 2; }

# ---- refuse /Applications before anything is created: resolve the real path (symlinks, case) of DEST
case "/$DEST/" in */../*) echo "build-app.sh: DEST_DIR must not contain '..' ($DEST); use a plain path such as dist" >&2; exit 2 ;; esac
resolve_loose() {   # real path of a folder that may not exist yet (the nearest existing parent is resolved)
  local p="$1" tail=""
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ ! -d "$p" ]; do tail="/$(basename "$p")$tail"; p="$(dirname "$p")"; done
  printf '%s%s' "$(cd -P "$p" && pwd)" "$tail"
}
DEST_REAL="$(resolve_loose "$DEST")"
lower="$(printf '%s' "$DEST_REAL" | tr '[:upper:]' '[:lower:]')"
case "$lower/" in
  /applications/*|/system/volumes/data/applications/*)
    echo "build-app.sh: DEST_DIR must not be under /Applications ($DEST_REAL). Build into dist/ and then: ditto dist/jusage.app /Applications/jusage.app" >&2
    exit 2 ;;
esac
DEST="$DEST_REAL"
mkdir -p "$DEST"
init_log "$DEST/build-app.log"

VERSION="$(sh "$REPO_ROOT/scripts/version.sh" --file "$REPO_ROOT/VERSION" show)" || die "bad VERSION file"
TRASH="${JUSAGE_TRASH:-/usr/bin/trash}"
[ -x "$TRASH" ] || die "no trash command at $TRASH (set JUSAGE_TRASH); nothing is ever deleted outright"
APP="$DEST/.jusage.app.building"
FINAL="$DEST/jusage.app"
OLD="$DEST/.jusage.app.old"
SCRATCH="$DEST/.swiftpm"
RES_SRC="$HERE/Resources"
[ -f "$RES_SRC/Info.plist.in" ] || die "no $RES_SRC/Info.plist.in"

for d in "$APP" "$OLD"; do
  if [ -e "$d" ]; then run "$TRASH" "$d" || die "could not move the leftover $d to the Trash"; fi
done

step "1. swift build (release, arm64 + x86_64)"
SWIFT_ARGS=(build -c release --arch arm64 --arch x86_64 --package-path "$HERE" --scratch-path "$SCRATCH")
run swift "${SWIFT_ARGS[@]}" || die "swift build failed"
BIN_DIR="$(swift "${SWIFT_ARGS[@]}" --show-bin-path 2>>"$LOG_FILE")"
BIN="$BIN_DIR/Jusage"
[ -f "$BIN" ] || die "swift build left no binary at $BIN"
capture lipo -archs "$BIN" || die "cannot read the built binary"
for a in arm64 x86_64; do
  case " $CAPTURED " in *" $a "*) ;; *) die "the built binary has no $a slice ($CAPTURED): jusage ships universal" ;; esac
done

step "2. assemble the bundle"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"
cp "$RES_SRC/Info.plist.in" "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null || die "Info.plist.in is not a valid plist"
EXE_NAME="$(plutil -extract CFBundleExecutable raw -o - "$APP/Contents/Info.plist")" || die "Info.plist.in has no CFBundleExecutable"
[ "$EXE_NAME" = "Jusage" ] || die "CFBundleExecutable is '$EXE_NAME' in Info.plist.in; the binary is named Jusage"
cp "$BIN" "$APP/Contents/MacOS/$EXE_NAME"
[ -d "$BIN_DIR/Sparkle.framework" ] || die "no Sparkle.framework beside the binary in $BIN_DIR"
ditto "$BIN_DIR/Sparkle.framework" "$APP/Contents/Frameworks/Sparkle.framework"
if ! otool -l "$APP/Contents/MacOS/$EXE_NAME" | grep -q "@executable_path/../Frameworks"; then
  run install_name_tool -add_rpath "@executable_path/../Frameworks" "$APP/Contents/MacOS/$EXE_NAME" || die "could not add the Frameworks rpath"
fi
# engine/ without tests and bytecode; the executable bits come along with -a
rsync -a --exclude '__pycache__/' --exclude '*.pyc' --exclude 'tests/' --exclude '.DS_Store' "$REPO_ROOT/engine/" "$APP/Contents/Resources/engine/" || die "copy of engine/ failed"
cp "$REPO_ROOT/VERSION" "$APP/Contents/Resources/VERSION"
cp "$REPO_ROOT/engine/themes.json" "$APP/Contents/Resources/themes.json"
for f in "$RES_SRC"/*; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in Info.plist.in|AppIcon.icns|.DS_Store) continue ;; esac
  cp -p "$f" "$APP/Contents/Resources/"
done
run bash "$HERE/icon/make-icns.sh" --out "$DEST/.icon" || die "icon generation failed"
cp "$DEST/.icon/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$DEST/.icon"
log "icon regenerated from make_icon.swift"

step "3. stamp Info.plist"
run sh "$REPO_ROOT/scripts/version.sh" --file "$REPO_ROOT/VERSION" plist "$APP/Contents/Info.plist" || die "version stamp failed"
if [ "$DEV" -eq 1 ]; then
  plutil -replace CFBundleIdentifier -string "com.goatdguild.jusage.dev" "$APP/Contents/Info.plist"
  plutil -replace JusageStateDir -string "~/.claude-usage-dev" "$APP/Contents/Info.plist"
  plutil -replace SUEnableAutomaticChecks -bool NO "$APP/Contents/Info.plist"
  log "dev build: bundle id com.goatdguild.jusage.dev, state folder ~/.claude-usage-dev"
fi
plutil -lint "$APP/Contents/Info.plist" >/dev/null || die "the stamped Info.plist is not valid"
run codesign --force --deep --sign - "$APP" || die "ad-hoc signing failed"

step "4. swap in the finished bundle"
if [ -e "$FINAL" ]; then
  mv "$FINAL" "$OLD" || die "could not move the old jusage.app aside"
fi
if ! mv "$APP" "$FINAL"; then
  [ ! -e "$OLD" ] || mv "$OLD" "$FINAL"
  die "could not move the new bundle into place"
fi
if [ -e "$OLD" ]; then run "$TRASH" "$OLD" || warn "could not move $OLD to the Trash; remove it by hand"; fi
rm -rf "$SCRATCH"
log "built $FINAL ($VERSION)"
echo "built $FINAL ($VERSION)"
