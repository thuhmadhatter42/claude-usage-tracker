#!/bin/bash
# install.command — double-click to install jusage from a downloaded or cloned copy of this folder.
# Same installer as install.sh, no questions asked. If macOS refuses to open it, right-click → Open once,
# or paste the one-line install from README.md into Terminal instead.
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HERE/install.sh" ] && [ ! -f "$HOME/jusage/claude_usage.py" ] && [ "$HERE" != "$HOME/jusage" ]; then
  # A fresh download: seed ~/jusage from this copy so the installer needs no network for the code.
  mkdir -p "$HOME/jusage" && rsync -a --exclude .remember "$HERE/" "$HOME/jusage/"
fi
if [ -f "$HOME/jusage/install.sh" ]; then bash "$HOME/jusage/install.sh" "$@"; else bash "$HERE/install.sh" "$@"; fi
