#!/bin/bash
# install.sh — installs jusage (claude-usage-tracker) on a Mac with no questions asked.
#
#   curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh | bash
#
# Puts the code in ~/jusage, builds the menu bar app into /Applications, runs the first scan,
# adds the `jusage` command to your shell, and opens the app. Re-running it upgrades in place.
#
# Options (mostly for an agent doing the install — see AGENT-SETUP.md):
#   --reports <folder>   share a daily summary into this synced folder (remembered)
#   --user <name>        your name as the others see it (default: this Mac's account full name)
#   --projects           also share project folder names
#   --dir <folder>       install somewhere other than ~/jusage (must be inside your home)
#   --no-app             skip the menu bar app
#   --no-launch          build the app but do not open it

set -u
REPO_URL="https://github.com/thuhmadhatter42/claude-usage-tracker"
DEST="$HOME/jusage"
REPORTS=""; USER_NAME=""; PROJECTS=""; NO_APP=""; NO_LAUNCH=""

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✘ %s\033[0m\n\n' "$*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --reports) REPORTS="${2:-}"; shift 2 ;;
    --user) USER_NAME="${2:-}"; shift 2 ;;
    --projects) PROJECTS=1; shift ;;
    --dir) DEST="${2:-}"; shift 2 ;;
    --no-app) NO_APP=1; shift ;;
    --no-launch) NO_LAUNCH=1; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

[ "$(uname)" = "Darwin" ] || die "This installer is for macOS. On Linux: git clone $REPO_URL and run claude_usage.py directly."
DEST="${DEST/#\~/$HOME}"
case "$DEST" in "$HOME"/*) ;; *) die "The install folder must be inside your home ($HOME), e.g. ~/jusage — not $DEST." ;; esac
if [ -d "$DEST" ] && [ ! -f "$DEST/claude_usage.py" ] && [ -n "$(command ls -A "$DEST" 2>/dev/null)" ]; then
  die "$DEST exists and is not a jusage install. Pick another folder with --dir."
fi

bold "jusage — Claude Code usage, per account, per person"

# ---------- 1. tools ----------
if xcode-select -p >/dev/null 2>&1; then
  ok "Command Line Tools (git, python3, swiftc)"
else
  warn "Apple's Command Line Tools are needed (git, python3, swiftc). Its install window is opening —"
  warn "click Install, and this script continues by itself when it finishes (a few minutes)."
  xcode-select --install >/dev/null 2>&1
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
  ok "Command Line Tools installed"
fi
python3 -c 'import sys, sqlite3, json; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || die "python3 3.9+ not found. Install Python from python.org (or 'brew install python') and re-run."
ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
command -v git >/dev/null 2>&1 || die "git not found even after Command Line Tools — re-run once Terminal has been reopened."

# ---------- 2. code ----------
if [ -d "$DEST/.git" ]; then
  if git -C "$DEST" pull --ff-only -q; then ok "updated $DEST"; else warn "git pull failed; keeping the copy already in $DEST"; fi
elif [ -f "$DEST/claude_usage.py" ]; then
  ok "using the copy already in $DEST (no git history, so 'jusage upgrade' will not work)"
else
  git clone -q "$REPO_URL" "$DEST" || die "Could not clone $REPO_URL into $DEST."
  ok "downloaded to $DEST"
fi
chmod +x "$DEST/jusage" "$DEST/install.sh" "$DEST/install.command" "$DEST/claude_usage.py" 2>/dev/null
ok "jusage $(cat "$DEST/VERSION")"

if [ -d "$HOME/.claude/projects" ] || command ls -d "$HOME"/.claude*/projects >/dev/null 2>&1; then
  ok "Claude Code transcripts found"
else
  warn "no ~/.claude*/projects yet — everything shows zero until Claude Code has run on this Mac"
fi

# ---------- 3. first scan ----------
python3 "$DEST/claude_usage.py" scan >/dev/null || die "First scan failed — open an issue at $REPO_URL with this window's output."
ok "first scan done (reads your transcripts locally; nothing leaves this Mac)"

# ---------- 4. name + sharing ----------
CFG="$HOME/.claude-usage/config.json"
if [ -z "$USER_NAME" ]; then
  if [ -f "$CFG" ] && python3 -c "import json,sys; sys.exit(0 if json.load(open('$CFG')).get('user') else 1)" 2>/dev/null; then
    USER_NAME=""   # already set; leave it
  else
    USER_NAME="$(id -F 2>/dev/null | awk '{print $1}')"; USER_NAME="${USER_NAME:-$(whoami)}"
  fi
fi
if [ -n "$REPORTS" ]; then
  REPORTS="${REPORTS/#\~/$HOME}"
  ARGS=(share --reports "$REPORTS"); [ -n "$USER_NAME" ] && ARGS+=(--user "$USER_NAME"); [ -n "$PROJECTS" ] && ARGS+=(--projects)
  python3 "$DEST/claude_usage.py" "${ARGS[@]}" >/dev/null || die "Could not share into $REPORTS."
  python3 "$DEST/claude_usage.py" dashboard >/dev/null
  ok "sharing into $REPORTS as $(python3 -c "import json;print(json.load(open('$CFG'))['user'])")"
elif [ -n "$USER_NAME" ]; then
  python3 "$DEST/claude_usage.py" set-user "$USER_NAME" >/dev/null
  ok "your name for the others: $USER_NAME  (change it: jusage set-user <name>)"
fi

# ---------- 5. menu bar app ----------
APP=""
if [ -z "$NO_APP" ]; then
  if command -v swiftc >/dev/null 2>&1; then
    LOG="$(mktemp -t jusage-build)"
    if bash "$DEST/menubar/build.sh" "$DEST" >"$LOG" 2>&1; then
      APP="$DEST/jusage.app"
      osascript -e 'tell application "jusage" to quit' >/dev/null 2>&1; sleep 1   # always: `open` would just re-activate the old binary
      if [ -w /Applications ]; then
        if ditto "$DEST/jusage.app" /Applications/jusage.app 2>/dev/null; then APP=/Applications/jusage.app; fi
      fi
      ok "menu bar app: $APP"
      if [ -z "$NO_LAUNCH" ]; then open -a "$APP" && ok "opened — look for it at the right of the menu bar; 'Open at login' is inside"; fi
    else
      warn "menu bar app did not build (log: $LOG); the command-line tool still works"
    fi
  else
    warn "swiftc not found; skipping the menu bar app"
  fi
fi

# ---------- 6. shell command ----------
LINE="alias jusage='\"$DEST/jusage\"'"
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ "$rc" = "$HOME/.bashrc" ] && [ ! -f "$rc" ] && continue
  touch "$rc"
  if grep -q '^alias jusage=' "$rc"; then sed -i '' "s|^alias jusage=.*|$LINE|" "$rc"
  else printf '\n# jusage — Claude Code usage tracker\n%s\n' "$LINE" >> "$rc"; fi
done
ok "'jusage' command added to your shell (new Terminal windows)"

bold "Done."
echo "  jusage            numbers in the terminal        jusage live    Claude's own meters"
echo "  jusage upgrade    latest version                 jusage help    everything else"
if [ -z "$REPORTS" ] && ! { [ -f "$CFG" ] && grep -q reports_dir "$CFG"; }; then
  echo
  echo "  To see who used what across the people you split a plan with, pick a folder you all sync"
  echo "  (iCloud Drive, Dropbox, Syncthing …) and run:"
  echo "      jusage share --reports \"<that folder>/claude-usage/reports\""
  echo "  or hand AGENT-SETUP.md ($REPO_URL/blob/main/AGENT-SETUP.md) to your Claude Code and it does it."
fi
echo
