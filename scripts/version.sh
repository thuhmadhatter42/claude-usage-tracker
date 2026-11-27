#!/bin/sh
# version.sh: the ONE version of a project. J's rule (global CLAUDE.md §5):
#   - one VERSION file per project; every build reads it; nothing else carries a version string
#   - 0.x.y until the first real ship = 1.0.0
#   - every SHIP bumps the last number by one (not every edit); the middle or first number only when J says
#   - tag = <PROJECT>-<version>; a shipped version is never re-used or overwritten
#
#   version.sh show                      print the version (fails if VERSION is missing or not N.N.N)
#   version.sh bump                      last number + 1, written back to VERSION
#   version.sh tag <PROJECT>             git tag <PROJECT>-<version> (refused if the tag exists)
#   version.sh plist <Info.plist>        write it into CFBundleShortVersionString (CFBundleVersion = commit count)
#   version.sh web <house-config.js>     write it into the web config's  version: '...'  line
#   --file FILE                          the VERSION file (default: VERSION in the current folder)
# Exit 0 = done, 2 = bad arguments or a bad VERSION, 1 = the step failed.
set -eu
FILE=VERSION
[ "${1:-}" = "--file" ] && { FILE=${2:?--file needs a path}; shift 2; }
CMD=${1:-show}
[ $# -gt 0 ] && shift

read_version() {
  [ -f "$FILE" ] || { echo "no VERSION file at $FILE" >&2; exit 2; }
  V=$(tr -d '[:space:]' < "$FILE")
  echo "$V" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "bad version '$V' in $FILE (want N.N.N)" >&2; exit 2; }
}

case $CMD in
  show)
    read_version; echo "$V" ;;
  bump)
    read_version
    LAST=${V##*.}; HEAD=${V%.*}
    NEW="$HEAD.$((LAST + 1))"
    printf '%s\n' "$NEW" > "$FILE"
    echo "$V -> $NEW" ;;
  tag)
    PROJECT=${1:?usage: version.sh tag <PROJECT>}
    read_version
    DIR=$(cd "$(dirname "$FILE")" && pwd)
    TAG="$PROJECT-$V"
    if git -C "$DIR" rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
      echo "tag $TAG already exists: a shipped version is never re-tagged. Run: version.sh bump" >&2; exit 1
    fi
    git -C "$DIR" tag "$TAG" || { echo "git tag $TAG failed" >&2; exit 1; }
    echo "tagged $TAG" ;;
  plist)
    PLIST=${1:?usage: version.sh plist <Info.plist>}
    read_version
    [ -f "$PLIST" ] || { echo "no Info.plist at $PLIST" >&2; exit 2; }
    DIR=$(cd "$(dirname "$FILE")" && pwd)
    BUILD=$(git -C "$DIR" rev-list --count HEAD 2>/dev/null || echo 0)
    plutil -replace CFBundleShortVersionString -string "$V" "$PLIST" || { echo "could not write the version into $PLIST" >&2; exit 1; }
    plutil -replace CFBundleVersion -string "$BUILD" "$PLIST" || { echo "could not write the build number into $PLIST" >&2; exit 1; }
    echo "$PLIST: $V ($BUILD)" ;;
  web)
    JS=${1:?usage: version.sh web <house-config.js>}
    read_version
    [ -f "$JS" ] || { echo "no file at $JS" >&2; exit 2; }
    grep -Eq "version: '[^']*'" "$JS" || { echo "$JS has no  version: '...'  line to stamp" >&2; exit 1; }
    sed -i '' "s/version: '[^']*'/version: '$V'/" "$JS" || { echo "could not write the version into $JS" >&2; exit 1; }
    echo "$JS: $V" ;;
  *)
    echo "unknown command: $CMD (show | bump | tag <PROJECT> | plist <file> | web <file>)" >&2; exit 2 ;;
esac
