#!/bin/bash
# Builds jusage.app (menu bar) next to this folder using swiftc from the Command Line Tools.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HERE/..}"
FINAL="$DEST/jusage.app"
APP="$DEST/.jusage.app.building"
rm -rf "$APP"
DEST_ABS="$(cd "$DEST" && pwd -P)"
VER="$(cat "$HERE/../VERSION")"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>jusage</string>
  <key>CFBundleDisplayName</key><string>jusage</string>
  <key>CFBundleIdentifier</key><string>com.goatdguild.jusage</string>
  <key>CFBundleExecutable</key><string>jusage</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VER</string>
  <key>CFBundleVersion</key><string>$VER</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>JusageScriptDir</key><string>$DEST_ABS</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF
swiftc -O -target "$(uname -m)-apple-macosx13.0" \
  -framework Cocoa -framework SwiftUI -framework ServiceManagement \
  "$HERE/main.swift" -o "$APP/Contents/MacOS/jusage"
codesign --force --sign - "$APP" >/dev/null 2>&1 || true
rm -rf "$FINAL" && mv "$APP" "$FINAL"
echo "built $FINAL ($VER)"
