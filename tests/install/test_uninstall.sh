#!/bin/bash
# The installer's footprint is exactly what the uninstall manifest names, so "take it off" leaves nothing behind.
# (~/Library/Caches/com.apple.python is Apple's own Python bytecode cache; it is not ours and is ignored.)
# Runs install.sh in a temp HOME against PATH stubs and a fake release DMG; never touches /Applications, the real
# Trash, a real Keychain or a Claude folder. Prints "ok"/"FAIL" per check; exit 0 only when every check passes.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/jusage-uninstall-test.XXXXXX")"
trap 'chmod -R u+w "$T" 2>/dev/null; rm -rf "$T"' EXIT
FAIL=0; CHECKS=0
check() { CHECKS=$((CHECKS + 1)); if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; FAIL=$((FAIL + 1)); fi; }

H="$T/home"; APPS="$T/Apps"; STUBS="$T/stubs"; HOST="$T/host"; TRASHED="$T/trashed"
mkdir -p "$H/tmp" "$APPS" "$STUBS" "$HOST" "$TRASHED"
stub() { printf '#!/bin/bash\n%s\n' "$2" > "$STUBS/$1"; chmod +x "$STUBS/$1"; }
stub open 'exit 0'; stub osascript 'exit 0'; stub pgrep 'exit 1'
stub spctl 'echo "accepted source=Notarized Developer ID"; exit 0'
stub xcode-select '[ "$1" = "-p" ] && { echo /Library/Developer/CommandLineTools; exit 0; }; exit 0'
stub sw_vers '[ "$1" = "-productVersion" ] && { echo 14.5; exit 0; }; exec /usr/bin/sw_vers "$@"'
stub trash "for p in \"\$@\"; do mv \"\$p\" '$TRASHED/'\"\$(basename \"\$p\").\$\$.\$RANDOM\"; done"

# a fake release: jusage.app (carrying the real engine) inside a DMG
SRC="$T/src/jusage.app/Contents"; mkdir -p "$SRC/Resources" "$SRC/MacOS"
cat > "$SRC/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.goatdguild.jusage</string><key>CFBundleShortVersionString</key><string>$(tr -d '[:space:]' < "$ROOT/VERSION")</string><key>LSMinimumSystemVersion</key><string>13.0</string></dict></plist>
PL
cp -R "$ROOT/engine" "$SRC/Resources/engine"; find "$SRC/Resources/engine" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/VERSION" "$SRC/Resources/VERSION"
hdiutil create -quiet -volname jusage -srcfolder "$T/src" -format UDZO "$HOST/jusage-latest.dmg" || { echo "FAIL could not build the fake DMG"; exit 1; }

inst() {
  env -i HOME="$H" TMPDIR="$H/tmp" PATH="$STUBS:/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$(command -v python3)")" \
    JUSAGE_APPS_DIR="$APPS" JUSAGE_TRASH="$STUBS/trash" JUSAGE_DOWNLOAD_BASE="file://${BASE:-$HOST}" \
    bash "$ROOT/install.sh" --no-launch "$@" 2>&1
}
footprint() { ( cd "$H" && /usr/bin/find . -maxdepth 1 -mindepth 1 ! -name tmp ! -name Library | sort; cd "$APPS" && /usr/bin/find . -maxdepth 1 -mindepth 1 | sed 's#^#apps/#' | sort ); }

OUT="$(inst)"; RC=$?
check "install exits 0" '[ $RC -eq 0 ]'
FP1="$(footprint)"
check "the footprint is the app, the shell file and the state folder, nothing else" \
  '[ "$FP1" = "$(printf "%s\n" ./.claude-usage ./.zshrc apps/./jusage.app)" ]'
check "the state folder is private (0700)" '[ "$(stat -f %Lp "$H/.claude-usage")" = "700" ]'
check "the shell file has exactly one jusage alias" '[ "$(grep -c "^alias jusage=" "$H/.zshrc")" = "1" ]'

OUT="$(inst)"; RC=$?
check "a second install exits 0" '[ $RC -eq 0 ]'
check "a second install leaves the same footprint" '[ "$(footprint)" = "$FP1" ]'
check "the replaced app went to the Trash" '[ "$(ls -A "$TRASHED" | wc -l | tr -d " ")" = "1" ]'

echo marker > "$APPS/jusage.app/marker"; cp "$H/.zshrc" "$T/zshrc.before"
BASE="$T/nowhere" OUT="$(BASE="$T/nowhere" inst)"; RC=$?
check "a failed install exits 1 and names the log" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "install.log"'
check "a failed install leaves the installed app and the shell file as they were" \
  '[ -f "$APPS/jusage.app/marker" ] && cmp -s "$H/.zshrc" "$T/zshrc.before" && [ "$(footprint)" = "$FP1" ]'

M="$ROOT/app/Resources/uninstall.manifest"
check "the app ships the uninstall manifest and script (H2)" '[ -f "$M" ] && [ -f "$ROOT/app/Resources/uninstall.sh" ]'
check "the manifest removes the app where the installer put it (/Applications and ~/Applications)" \
    'grep -Eq "^[[:space:]]*app[[:space:]]+/Applications/jusage\.app" "$M" && grep -Eq "Applications/jusage\.app" "$M"'
check "the manifest covers the state folder the installer made" 'grep -Eq "(data|user)[[:space:]]+\.claude-usage" "$M"'

echo "uninstall_test: $CHECKS checks, $FAIL failed"
[ $FAIL = 0 ] || exit 1
