#!/bin/bash
# install.sh: installs jusage on a Mac, no questions asked.
#
#   curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh | bash
#
# Downloads the latest notarized jusage.app, checks it with Gatekeeper, puts it in /Applications (or ~/Applications
# when /Applications is not writable), runs the first scan, adds the `jusage` command to your shell and opens the
# app. The engine rides inside the app, so there is nothing else to build or keep in sync. Re-running it upgrades.
#
# Options:
#   --reports <folder>     share a daily summary into this synced folder (remembered)
#   --user <name>          your name as the others see it (default: the first word of this Mac account's full name)
#   --projects             also share project folder names (remembered, with or without --reports)
#   --from-source <dir>    no app: keep a git checkout in <dir> (inside your home) and run the engine from it.
#                          For macOS 12 and for command-line-only use. Needs git.
#   --no-launch            do not open the app
#   -h, --help             this text
#
# Everything is logged to ~/.claude-usage/logs/install.log (what `jusage doctor` reads). A failure prints one line
# naming that file, exits non-zero and leaves nothing half-installed.
#
# Environment (mirrors and tests): JUSAGE_DOWNLOAD_BASE, JUSAGE_REPO_URL, JUSAGE_APPS_DIR, JUSAGE_TRASH,
# JUSAGE_CLT_TIMEOUT (seconds, default 900), JUSAGE_CLT_POLL (seconds, default 10).

set -u
REPO_URL="${JUSAGE_REPO_URL:-https://github.com/thuhmadhatter42/claude-usage-tracker}"
DOWNLOAD_BASE="${JUSAGE_DOWNLOAD_BASE:-https://github.com/thuhmadhatter42/claude-usage-tracker/releases/latest/download}"
BUNDLE_ID="com.goatdguild.jusage"
TEAM_ID="9WTFGV63S2"            # the only Developer ID team jusage installs from (J Shriver)
TEAM_REQ="anchor apple generic and certificate leaf[subject.OU] = \"$TEAM_ID\""
STATE="$HOME/.claude-usage"
LOG="$STATE/logs/install.log"
REPORTS=""; USER_NAME=""; PROJECTS=""; SOURCE_DIR=""; NO_LAUNCH=""

usage() {
  /bin/cat <<'USAGE'
install.sh: installs jusage on a Mac, no questions asked.

  curl -fsSL https://raw.githubusercontent.com/thuhmadhatter42/claude-usage-tracker/main/install.sh | bash

Downloads the latest notarized jusage.app, checks it with Gatekeeper, puts it in /Applications (or ~/Applications
when /Applications is not writable), runs the first scan, adds the `jusage` command to your shell and opens the
app. Re-running it upgrades.

Options:
  --reports <folder>     share a daily summary into this synced folder (remembered)
  --user <name>          your name as the others see it (default: the first word of this Mac account's full name)
  --projects             also share project folder names (remembered, with or without --reports)
  --from-source <dir>    no app: keep a git checkout in <dir> (inside your home) and run the engine from it.
                         For macOS 12 and for command-line-only use. Needs git.
  --no-launch            do not open the app
  -h, --help             this text

Everything is logged to ~/.claude-usage/logs/install.log. A failure prints one line naming that file, exits
non-zero and leaves nothing half-installed.
USAGE
}
bad_usage() { printf 'install.sh: %s (try --help)\n' "$*" >&2; exit 1; }
needs_value() {   # needs_value <option> <what> <value>: the value must exist and not look like another option
  case "${3:-}" in ""|-*) bad_usage "$1 needs a $2" ;; esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --reports)     needs_value --reports folder "${2:-}"; REPORTS="$2"; shift 2 ;;
    --user)        needs_value --user name "${2:-}"; USER_NAME="$2"; shift 2 ;;
    --from-source) needs_value --from-source folder "${2:-}"; SOURCE_DIR="$2"; shift 2 ;;
    --projects)    PROJECTS=1; shift ;;
    --no-launch)   NO_LAUNCH=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             bad_usage "Unknown option: $1" ;;
  esac
done

# ---------------------------------------------------------------- logging
TMP=""; MNT=""
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$1" "$2" >> "$LOG"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; note ok "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; note warn "$*"; }
die()  { note FAIL "$*"; printf '\n\033[31m✘ %s\033[0m\n  Details: %s\n\n' "$*" "$LOG" >&2; exit 1; }
cleanup() {
  [ -z "$MNT" ] || hdiutil detach "$MNT" -force >/dev/null 2>&1
  [ -z "$TMP" ] || rm -rf "$TMP"
}
trap cleanup EXIT

[ "$(uname)" = "Darwin" ] || bad_usage "this installer is for macOS"
mkdir -p "$STATE/logs" && chmod 700 "$STATE" || { echo "install.sh: cannot create $STATE" >&2; exit 1; }
note start "args: $*"

MACOS="$(sw_vers -productVersion)"
if [ -z "$SOURCE_DIR" ] && [ "${MACOS%%.*}" -lt 13 ] 2>/dev/null; then
  die "jusage's app needs macOS 13 or newer (this Mac has $MACOS). On this Mac run: install.sh --from-source ~/jusage"
fi

# ---------------------------------------------------------------- 1. tools (Python comes with the Command Line Tools)
bold "jusage: Claude Code usage, per account, per person"
if xcode-select -p >/dev/null 2>&1; then
  ok "Command Line Tools"
else
  LIMIT="${JUSAGE_CLT_TIMEOUT:-900}"; POLL="${JUSAGE_CLT_POLL:-10}"
  warn "Apple's Command Line Tools are needed (python3). Its install window is opening: click Install."
  xcode-select --install >/dev/null 2>&1
  waited=0
  until xcode-select -p >/dev/null 2>&1; do
    if [ "$waited" -ge "$LIMIT" ]; then
      if [ "$LIMIT" -ge 60 ]; then span="$((LIMIT / 60)) minutes"; else span="$LIMIT seconds"; fi
      die "The Command Line Tools did not finish installing within $span. Install them (xcode-select --install), then run this installer again."
    fi
    sleep "$POLL"; waited=$((waited + POLL))
  done
  ok "Command Line Tools installed"
fi
PY="$(python3 -c 'import sys, sqlite3, json
if sys.version_info < (3, 9): sys.exit(1)
print(sys.executable)' 2>/dev/null)" || PY=""
[ -n "$PY" ] && [ -x "$PY" ] || die "Python 3.9 or newer with sqlite3 was not found. Install the Command Line Tools or Python from python.org, then run this installer again."
ok "python3 $("$PY" -c 'import platform; print(platform.python_version())')"

# ---------------------------------------------------------------- helpers
resolve_loose() {   # real path of a folder that may not exist yet (the nearest existing parent is resolved)
  local p="$1" tail=""
  case "$p" in "~"|"~/"*) p="$HOME${p#"~"}" ;; esac
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ ! -d "$p" ]; do tail="/$(basename "$p")$tail"; p="$(dirname "$p")"; done
  printf '%s%s' "$(cd -P "$p" && pwd)" "$tail"
}
trash_path() {
  if [ -n "${JUSAGE_TRASH:-}" ]; then "$JUSAGE_TRASH" "$1"
  elif [ -x /usr/bin/trash ]; then /usr/bin/trash "$1"
  else osascript -e "tell application \"Finder\" to delete POSIX file \"$1\"" >/dev/null; fi
}
# signed_by_team <path>: notarized by somebody is not enough (a hijacked release could be anyone's
# paid account). The signature must satisfy a requirement naming TEAM_ID, and codesign's own report
# of the signer must say that team too.
signed_by_team() {
  codesign --verify --strict -R="$TEAM_REQ" "$1" >>"$LOG" 2>&1 || return 1
  [ "$(codesign -dv "$1" 2>&1 | sed -n 's/^TeamIdentifier=//p')" = "$TEAM_ID" ]
}
# The engine runs with -B so no bytecode is written into a signed app bundle.
engine()     { "$PY" -B -E -s "$ENGINE_DIR/claude_usage.py" "$@" >>"$LOG" 2>&1; }
engine_out() { "$PY" -B -E -s "$ENGINE_DIR/claude_usage.py" "$@" 2>>"$LOG"; }

# ---------------------------------------------------------------- 2. the code: the notarized app, or a source checkout
APP=""
if [ -n "$SOURCE_DIR" ]; then
  command -v git >/dev/null 2>&1 || die "git was not found. Install the Command Line Tools, then run this installer again."
  HOME_REAL="$(cd -P "$HOME" && pwd)"
  case "/${SOURCE_DIR}/" in */../*) die "The folder must not contain '..': $SOURCE_DIR (it has to be inside your home, $HOME_REAL)." ;; esac
  case "$SOURCE_DIR" in *'$'*|*'`'*|*'"'*|*'\'*) die "The folder must not contain \$, \`, \" or \\ (the jusage alias would run them): $SOURCE_DIR" ;; esac
  DIR="$(resolve_loose "$SOURCE_DIR")"
  case "$DIR" in "$HOME_REAL"/?*) ;; *) die "The install folder must be inside your home ($HOME_REAL), e.g. ~/jusage; not $DIR." ;; esac
  if [ -d "$DIR" ] && [ -n "$(/bin/ls -A "$DIR" 2>/dev/null)" ] && [ ! -f "$DIR/engine/claude_usage.py" ] && [ ! -f "$DIR/claude_usage.py" ]; then
    die "$DIR is not a jusage install and is not empty; pick another folder (or an empty one) for --from-source."
  fi
  if [ -e "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only -q >>"$LOG" 2>&1 || die "Could not update $DIR (git pull failed)."
    ok "updated $DIR"
  elif [ -f "$DIR/engine/claude_usage.py" ]; then
    ok "using the copy already in $DIR (no git history, so 'jusage upgrade' will not work)"
  else
    git clone -q "$REPO_URL" "$DIR" >>"$LOG" 2>&1 || die "Could not clone $REPO_URL into $DIR."
    ok "downloaded to $DIR"
  fi
  ENGINE_DIR="$DIR/engine"
  [ -f "$ENGINE_DIR/claude_usage.py" ] || die "$DIR has no engine/claude_usage.py (an old 0.1 layout?). Remove it or pick another folder."
  CLI="$ENGINE_DIR/bin/jusage"
  ok "jusage $(tr -d '[:space:]' < "$DIR/VERSION") (source)"
else
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/jusage-install.XXXXXX")" || die "Could not make a temporary folder."
  DMG="$TMP/jusage.dmg"
  curl -fsSL --retry 2 -o "$DMG" "$DOWNLOAD_BASE/jusage-latest.dmg" >>"$LOG" 2>&1 \
    || die "Could not download $DOWNLOAD_BASE/jusage-latest.dmg. Check the connection."
  ok "downloaded jusage-latest.dmg"
  spctl -a -t open --context context:primary-signature -v "$DMG" >>"$LOG" 2>&1 \
    || die "macOS does not accept the downloaded disk image as a notarized app. Nothing was installed."
  signed_by_team "$DMG" || die "The disk image is not signed by jusage's developer (team $TEAM_ID). Nothing was installed."
  MNT="$TMP/mnt"; mkdir "$MNT"
  hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" >>"$LOG" 2>&1 || die "Could not open the downloaded disk image."
  [ -d "$MNT/jusage.app" ] || die "The disk image holds no jusage.app."
  spctl -a -vv "$MNT/jusage.app" >>"$LOG" 2>&1 || die "macOS does not accept jusage.app (Gatekeeper). Nothing was installed."
  signed_by_team "$MNT/jusage.app" || die "jusage.app is not signed by jusage's developer (team $TEAM_ID). Nothing was installed."
  got_id="$(plutil -extract CFBundleIdentifier raw -o - "$MNT/jusage.app/Contents/Info.plist" 2>/dev/null || true)"
  [ "$got_id" = "$BUNDLE_ID" ] || die "The app in the disk image has bundle id '${got_id:-none}', expected $BUNDLE_ID. Nothing was installed."
  NEW_VER="$(plutil -extract CFBundleShortVersionString raw -o - "$MNT/jusage.app/Contents/Info.plist" 2>/dev/null || true)"
  [ -f "$MNT/jusage.app/Contents/Resources/engine/claude_usage.py" ] || die "The app in the disk image has no engine. Nothing was installed."
  ok "jusage $NEW_VER passes Gatekeeper"

  APPS="${JUSAGE_APPS_DIR:-/Applications}"
  if [ ! -d "$APPS" ] || [ ! -w "$APPS" ]; then
    APPS="$HOME/Applications"
    mkdir -p "$APPS" || die "Could not create $APPS."
    warn "/Applications is not writable here, so jusage goes to ~/Applications ($APPS)"
  fi
  # quit the running app first: `open` would just re-activate the old binary
  if pgrep -x Jusage >/dev/null 2>&1; then
    osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1
    n=0
    while pgrep -x Jusage >/dev/null 2>&1; do
      n=$((n + 1)); [ "$n" -le 20 ] || die "jusage is still running. Quit it from the menu bar, then run this installer again."
      sleep 1
    done
    ok "quit the running jusage"
  fi
  FINAL="$APPS/jusage.app"; STAGING="$APPS/.jusage.app.installing"; OLD="$APPS/.jusage.app.old"
  for d in "$STAGING" "$OLD"; do
    [ ! -e "$d" ] || trash_path "$d" >>"$LOG" 2>&1 || die "Could not clear the leftover $d."
  done
  ditto "$MNT/jusage.app" "$STAGING" >>"$LOG" 2>&1 || { trash_path "$STAGING" >/dev/null 2>&1; die "Could not copy the app into $APPS."; }
  if [ -e "$FINAL" ]; then mv "$FINAL" "$OLD" || { trash_path "$STAGING" >/dev/null 2>&1; die "Could not move the old jusage.app aside."; }; fi
  if ! mv "$STAGING" "$FINAL"; then
    [ ! -e "$OLD" ] || mv "$OLD" "$FINAL"
    die "Could not put the new jusage.app in place."
  fi
  if [ -e "$OLD" ]; then trash_path "$OLD" >>"$LOG" 2>&1 || warn "Could not move the old app ($OLD) to the Trash; remove it by hand."; fi
  APP="$FINAL"
  ENGINE_DIR="$APP/Contents/Resources/engine"
  CLI="$ENGINE_DIR/bin/jusage"
  note installed "app $APP version $NEW_VER"
  ok "installed $APP"
fi

if [ -d "$HOME/.claude/projects" ] || /bin/ls -d "$HOME"/.claude*/projects >/dev/null 2>&1; then
  ok "Claude Code transcripts found"
else
  warn "no ~/.claude*/projects yet: everything shows zero until Claude Code has run on this Mac"
fi

# ---------------------------------------------------------------- 3. Python for the command and the status line
# (an app that is about to open records it itself from --set-python)
if [ -z "$APP" ] || [ -n "$NO_LAUNCH" ]; then
  engine config python "$PY" || die "Could not record Python ($PY) for jusage."
  ok "recorded Python: $PY"
fi

# ---------------------------------------------------------------- 4. first scan
engine scan || die "The first scan failed."
ok "first scan done (reads your transcripts locally; nothing leaves this Mac)"

# ---------------------------------------------------------------- 5. name + sharing
if [ -z "$USER_NAME" ] && [ -z "$(engine_out config get user)" ]; then
  USER_NAME="$(id -F 2>/dev/null | awk '{print $1}')"; USER_NAME="${USER_NAME:-$(whoami)}"
fi
if [ -n "$REPORTS" ]; then
  REPORTS="${REPORTS/#\~/$HOME}"
  ARGS=(share --reports "$REPORTS"); [ -n "$USER_NAME" ] && ARGS+=(--user "$USER_NAME"); [ -n "$PROJECTS" ] && ARGS+=(--projects)
  engine "${ARGS[@]}" || die "Could not share into $REPORTS."
  engine dashboard || die "Could not build the dashboard from $REPORTS."
  ok "sharing into $REPORTS as $(engine_out config get user)"
else
  if [ -n "$USER_NAME" ]; then
    engine set-user "$USER_NAME" || die "Could not set your name."
    ok "your name for the others: $USER_NAME  (change it: jusage set-user <name>)"
  fi
  if [ -n "$PROJECTS" ]; then
    # `share --projects` remembers the choice and then stops because no shared folder is set yet: the one
    # expected refusal. The remembered value is what is checked.
    engine share --projects
    [ "$(engine_out config get share_projects)" = "true" ] || die "Could not remember --projects."
    ok "project names will be shared once you pick a shared folder (jusage share --reports <folder>)"
  fi
fi

# ---------------------------------------------------------------- 6. open the app
if [ -n "$APP" ] && [ -z "$NO_LAUNCH" ]; then
  open -a "$APP" --args --set-python "$PY" >>"$LOG" 2>&1 || die "Could not open $APP."
  ok "opened: look for it at the right of the menu bar ('Open at login' is inside)"
fi

# ---------------------------------------------------------------- 7. the `jusage` command
# The alias carries its own quotes; a ' in the path is escaped; the replacement goes through the environment
# so no character of the path is ever interpreted by sed or awk.
case "$CLI" in *'$'*|*'`'*|*'"'*|*'\'*) die "The jusage folder must not contain \$, \`, \" or \\ (the alias would run them): $CLI" ;; esac
QUOTE="'\\''"
ALIAS_LINE="alias jusage='\"${CLI//\'/$QUOTE}\"'"
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ "$rc" = "$HOME/.bashrc" ] && [ ! -f "$rc" ] && continue
  touch "$rc"
  if /usr/bin/grep -q '^alias jusage=' "$rc"; then
    RCTMP="$(mktemp "${TMPDIR:-/tmp}/jusage-rc.XXXXXX")"
    NEWLINE="$ALIAS_LINE" awk '/^alias jusage=/ { print ENVIRON["NEWLINE"]; next } { print }' "$rc" > "$RCTMP" \
      && cat "$RCTMP" > "$rc" || die "Could not update the jusage alias in $rc."
    rm -f "$RCTMP"
  else
    printf '\n# jusage: Claude Code usage tracker\n%s\n' "$ALIAS_LINE" >> "$rc" || die "Could not write the jusage alias to $rc."
  fi
done
ok "'jusage' command added to your shell (new Terminal windows)"

bold "Done."
echo "  jusage            numbers in the terminal        jusage live    Claude's own meters"
echo "  jusage upgrade    latest version                 jusage help    everything else"
if [ -z "$REPORTS" ] && [ -z "$(engine_out config get reports_dir)" ]; then
  echo
  echo "  To see who used what across the people you split a plan with, pick a folder you all sync"
  echo "  (iCloud Drive, Dropbox, Syncthing ...) and run:"
  echo "      jusage share --reports \"<that folder>/claude-usage/reports\""
  echo "  or hand AGENT-SETUP.md to your Claude Code and it does it."
fi
echo
note done "install finished"
