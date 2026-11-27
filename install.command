#!/bin/bash
# install.command: double-click to run the jusage installer that sits beside this file.
# It only wraps install.sh: same options, same behaviour, and it seeds nothing. If macOS refuses to open it,
# right-click, then Open, once; or paste the one-line install from README.md into Terminal instead.
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$HERE/install.sh" ]; then
  echo "install.command: install.sh is not next to this file ($HERE). Download the whole folder again." >&2
  exit 1
fi
exec bash "$HERE/install.sh" "$@"
