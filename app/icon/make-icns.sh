#!/bin/bash
# make-icns.sh: draws the app icon from make_icon.swift and turns it into an .icns (iconutil, scripted).
#   app/icon/make-icns.sh            writes app/Resources/AppIcon.icns and store/icon.png (the committed copies)
#   app/icon/make-icns.sh --out DIR  writes DIR/AppIcon.icns and DIR/icon.png (the build uses this)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT_ICNS="$REPO/app/Resources/AppIcon.icns"; OUT_PNG="$REPO/store/icon.png"
case "${1:-}" in
  "") ;;
  --out) [ -n "${2:-}" ] || { echo "make-icns.sh: --out needs a folder" >&2; exit 2; }
         mkdir -p "$2"; OUT_ICNS="$2/AppIcon.icns"; OUT_PNG="$2/icon.png" ;;
  *) echo "usage: make-icns.sh [--out DIR]" >&2; exit 2 ;;
esac
T="$(mktemp -d "${TMPDIR:-/tmp}/jusage-icon.XXXXXX")"
trap 'rm -rf "$T"' EXIT
swiftc -O "$HERE/make_icon.swift" -o "$T/make_icon" || { echo "make-icns.sh: make_icon.swift did not compile" >&2; exit 1; }
"$T/make_icon" "$T/drawn.png" >/dev/null || { echo "make-icns.sh: the icon drawing failed" >&2; exit 1; }
# the drawing is at the screen's scale (2048 on a Retina Mac); the master is always exactly 1024
sips -z 1024 1024 "$T/drawn.png" --out "$T/icon_1024.png" >/dev/null
SET="$T/AppIcon.iconset"; mkdir "$SET"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$T/icon_1024.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z "$((s * 2))" "$((s * 2))" "$T/icon_1024.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns -o "$T/AppIcon.icns" "$SET"
cp "$T/AppIcon.icns" "$OUT_ICNS"; cp "$T/icon_1024.png" "$OUT_PNG"
echo "icon: $OUT_ICNS"
