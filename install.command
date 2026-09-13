#!/bin/bash
# install.command — double-click to install jusage (claude-usage-tracker) on a Mac.
# Checks and installs what it needs, copies the app, runs the first scan, offers the alias.
# Safe to re-run: it updates an existing install in place.

set -u
SRC="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="https://github.com/thuhmadhatter42/claude-usage-tracker"
DEFAULT_DIR="$HOME/jusage"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✘ %s\033[0m\n\n' "$*"; read -r -p "Press Return to close. " _; exit 1; }

clear
bold "jusage installer — Claude Code token usage, per account, per person"
echo   "Source: $SRC"
echo

# ---------- 1. where ----------
read -r -p "Where do you want to install this? [$DEFAULT_DIR] " ans
DEST="${ans:-$DEFAULT_DIR}"
DEST="${DEST/#\~/$HOME}"
case "$DEST" in
  "$HOME"|"$HOME/"|/|/Users|/Users/*/|/tmp|/tmp/*|/Users/Shared|/Users/Shared/*) die "Pick a folder of its own inside your home, e.g. $DEFAULT_DIR — not $DEST." ;;
esac
case "$DEST" in "$HOME"/*) ;; *) die "The install folder must be inside your home ($HOME), so nobody else on this Mac can change it." ;; esac
if [ -d "$DEST" ] && [ -n "$(find "$DEST" -mindepth 1 -maxdepth 1 2>/dev/null | head -1)" ] && [ ! -f "$DEST/claude_usage.py" ]; then
  die "$DEST already exists and is not a jusage install. Pick an empty or new folder."
fi
echo

# ---------- 2. dependencies ----------
bold "Checking dependencies"
[ "$(uname)" = "Darwin" ] || die "This installer is for macOS (Keychain login, menu bar app). On Linux run claude_usage.py directly: scan / report work, live reads ~/.claude/.credentials.json."
if xcode-select -p >/dev/null 2>&1; then
  ok "Xcode Command Line Tools (gives you git + python3 + swiftc)"
else
  warn "Xcode Command Line Tools missing — Apple's install window is opening. Click Install and wait."
  xcode-select --install >/dev/null 2>&1
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
  ok "Command Line Tools installed"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
  ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
else
  if command -v brew >/dev/null 2>&1; then
    warn "python3 too old or missing — installing with Homebrew"
    brew install python >/dev/null || die "Homebrew could not install python."
    ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
  else
    die "python3 3.9+ not found. Install Homebrew (https://brew.sh) then re-run, or install Python from python.org."
  fi
fi
python3 -c 'import sqlite3, json, argparse' 2>/dev/null && ok "python standard library (sqlite3, json)" || die "python3 is missing its standard library — reinstall Python."

if command -v git >/dev/null 2>&1; then ok "git $(git --version | awk '{print $3}')"; else GIT_MISSING=1; warn "git not found — will copy files instead of cloning (no 'jusage upgrade' until git exists)"; fi

if [ -d "$HOME/.claude/projects" ] || ls -d "$HOME"/.claude*/projects >/dev/null 2>&1; then
  ok "Claude Code transcripts found in ~/.claude*"
else
  warn "No ~/.claude*/projects yet — jusage will show zero until Claude Code has run on this Mac."
fi
echo

# ---------- 3. install ----------
bold "Installing to $DEST"
if [ "$SRC" = "$DEST" ]; then
  ok "Already running from the install folder — files stay where they are"
elif [ -d "$DEST/.git" ] && [ -z "${GIT_MISSING:-}" ]; then
  git -C "$DEST" pull --ff-only >/dev/null 2>&1 && ok "Existing install updated with git pull" || warn "git pull failed; keeping the existing copy"
else
  mkdir -p "$DEST" || die "Cannot create $DEST"
  if [ -d "$SRC/.git" ]; then
    rsync -a --exclude .remember "$SRC/" "$DEST/" || die "Copy failed"
    ok "Copied app (with git history, so 'jusage upgrade' works)"
  else
    rsync -a --exclude .remember "$SRC/" "$DEST/" || die "Copy failed"
    ok "Copied app"
  fi
fi
chmod +x "$DEST/jusage" "$DEST/install.command" "$DEST/claude_usage.py"
ok "jusage $(cat "$DEST/VERSION")"
echo

# ---------- 3b. menu bar app ----------
bold "Menu bar app"
if command -v swiftc >/dev/null 2>&1; then
  BUILD_LOG="$(mktemp -t jusage-build)"
  if bash "$DEST/menubar/build.sh" "$DEST" >"$BUILD_LOG" 2>&1; then
    ok "built $DEST/jusage.app (lives in your menu bar; 'Open at login' is a checkbox inside it)"
  else
    warn "menu bar app did not build (see $BUILD_LOG); the command-line tool still works"
  fi
else
  warn "swiftc not found; skipping the menu bar app"
fi
echo

# ---------- 4. first run ----------
bold "First scan (reads your Claude Code transcripts, nothing leaves this Mac)"
python3 "$DEST/claude_usage.py" scan || die "Scan failed — open an issue on GitHub with this window's output."
echo

bold "Sharing with the people you split a plan with (optional)"
echo "  jusage can drop a per-day totals file into a folder you sync with them, so one dashboard"
echo "  shows everyone: your name, this Mac's name, day totals by model and account, 5-hour window"
echo "  times, Claude's meters. Project folder names only if you later run: jusage share --projects"
CFG="$HOME/.claude-usage/config.json"
if [ -f "$CFG" ] && grep -q reports_dir "$CFG"; then
  ok "Already configured: $(python3 -c "import json;d=json.load(open('$CFG'));print(d.get('user','?'),'→',d['reports_dir'])")"
else
  read -r -p "  Your name as the others see it [$(whoami)]: " uname_
  USER_NAME="${uname_:-$(whoami)}"
  read -r -p "  Shared reports folder (Return to skip): " rep
  rep="${rep/#\~/$HOME}"
  if [ -n "$rep" ]; then
    python3 "$DEST/claude_usage.py" share --reports "$rep" --user "$USER_NAME" && python3 "$DEST/claude_usage.py" dashboard
  else
    python3 "$DEST/claude_usage.py" set-user "$USER_NAME"
    warn "Skipped sharing. Later:  jusage share --reports <folder> --user $USER_NAME"
  fi
fi
echo

# ---------- 5. alias ----------
bold "Do you want to set up an alias?"
echo "  This will add the \"jusage\" command to your shell."
read -r -p "  Add it? [Y/n] " a
if [[ ! "${a:-Y}" =~ ^[Nn] ]]; then
  LINE="alias jusage=\"$DEST/jusage\""
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [ "$rc" = "$HOME/.bashrc" ] && [ ! -f "$rc" ] && continue
    touch "$rc"
    if grep -q '^alias jusage=' "$rc"; then
      sed -i '' "s|^alias jusage=.*|$LINE|" "$rc"
    else
      printf '\n# jusage — Claude Code usage tracker\n%s\n' "$LINE" >> "$rc"
    fi
    ok "alias written to $rc"
  done
  echo
  bold "Done. Open a new Terminal window and type:  jusage [enter]  to launch the jusage app."
  [ -d "$DEST/jusage.app" ] && echo "  Menu bar:  jusage app   (or double-click $DEST/jusage.app)"
else
  echo
  bold "Done. Run it with:  $DEST/jusage"
fi
echo
read -r -p "Press Return to close. " _
