# shellcheck shell=bash
# Shared helpers for build and release scripts. Source, do not execute. Lifted from Jlipper (scripts/lib/common.sh).
# Provides: REPO_ROOT, init_log <file>, log/warn/die, step, run, capture, require_cmd.
# Every message goes to stderr AND the script's log file; a failing command prints
# file:line + the command via the ERR trap so nothing fails silently.
# Expected layout in an app: <repo>/scripts/release.sh and <repo>/scripts/lib/common.sh (REPO_ROOT = two up).

if [ -z "${REPO_ROOT:-}" ]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
export REPO_ROOT

LOG_FILE="${LOG_FILE:-}"
SCRIPT_LABEL="${SCRIPT_LABEL:-script}"

init_log() {
  LOG_FILE="$1"
  mkdir -p "$(dirname "$LOG_FILE")"
  : >> "$LOG_FILE"
  log "=== $SCRIPT_LABEL start $(date '+%Y-%m-%d %H:%M:%S %Z') | host $(hostname -s) | macOS $(sw_vers -productVersion 2>/dev/null) $(uname -m) ==="
}

_emit() { # level msg
  local line
  line="$(date '+%H:%M:%S') [$1] $2"
  printf '%s\n' "$line" >&2
  if [ -n "$LOG_FILE" ]; then printf '%s\n' "$line" >> "$LOG_FILE"; fi
  return 0
}
log()  { _emit INFO "$*"; }
warn() { _emit WARN "$*"; }
die()  { _emit FAIL "$*"; _emit FAIL "see log: ${LOG_FILE:-<none>}"; exit 1; }
step() { _emit STEP ">>> $*"; }

# run <cmd...>: run a command, mirror its combined output to the log, return its status.
run() {
  _emit RUN "$*"
  local rc
  set +e
  if [ -n "$LOG_FILE" ]; then
    "$@" 2>&1 | tee -a "$LOG_FILE" >&2
    rc=${PIPESTATUS[0]}
  else
    "$@"; rc=$?
  fi
  set -e
  if [ "$rc" -ne 0 ]; then _emit FAIL "exit $rc: $*"; fi
  return "$rc"
}

# capture <cmd...>: run quietly, leave combined output in $CAPTURED, mirror to log.
CAPTURED=""
capture() {
  _emit RUN "$*"
  local rc
  set +e
  CAPTURED="$("$@" 2>&1)"; rc=$?
  set -e
  if [ -n "$LOG_FILE" ]; then printf '%s\n' "$CAPTURED" >> "$LOG_FILE"; fi
  if [ "$rc" -ne 0 ]; then _emit FAIL "exit $rc: $*"; printf '%s\n' "$CAPTURED" >&2; fi
  return "$rc"
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1${2:+ ($2)}"; }

# (file:line of the command that failed: BASH_SOURCE[0] + LINENO are right at the top level of a script AND
# inside a function; Jlipper's original used BASH_SOURCE[1], which is empty at the top level.)
trap '_emit FAIL "unexpected error at ${BASH_SOURCE[0]:-?}:${LINENO:-?}: ${BASH_COMMAND}"' ERR
