#!/bin/sh
# Uninstall an app: every path in its manifest goes to the user's Trash (never deleted outright), its
# installer receipts are forgotten. Generalised from Artemis (app/src/uninstall/uninstall-artemis.sh).
# The SAME script ships in every app; what it removes is the app's own `uninstall.manifest`.
#
# Shipped inside the app (Contents/Resources/uninstall.sh + uninstall.manifest). The app menu item
# "Uninstall <App>…" runs it (as administrator only when --needs-admin says so); the manual shows the
# Terminal form:   sh "/Applications/<App>.app/Contents/Resources/uninstall.sh"      (sudo if it says so)
#
# THE MANIFEST: one item per line, "<kind> <path or id>". Blank lines and # comments are ignored.
#   name     <text>    the app's name, for messages                                    (one, required)
#   system   <path>    an absolute path outside the home folder   (/Library/..., a driver, a system plug-in)
#   user     <path>    a path inside the home folder, written WITHOUT the leading ~/   (always removed)
#   data     <path>    like user, but KEPT when the user ticks "Keep my settings"      (--keep-data)
#   receipt  <id>      an installer receipt id, forgotten with pkgutil --forget
#   app      <path>    the app bundle itself: an absolute path, moved LAST (this script lives inside it)
#   note     <text>    a closing line printed after a successful run ("restart the Mac to ...")
# Paths may contain spaces; everything after the kind is the path. No quotes, no ~, no variables, no "..".
#
# It NEVER restarts the Mac, never stops a system service, never quits other apps, never runs rm.
#
# usage: uninstall.sh [--manifest FILE] [--dry-run] [--keep-data] [--needs-admin]
#                     [--home DIR] [--user NAME] [--root DIR]
#   --manifest FILE  the manifest (default: uninstall.manifest beside this script)
#   --dry-run        print what would be moved, move nothing
#   --keep-data      leave the manifest's `data` items in place (settings, sessions, licence)
#   --needs-admin    move nothing: print "yes" if a real run needs administrator rights (something to move
#                    that this user cannot move, or a receipt to forget), else "no"
#   --home DIR       the user's home (default $HOME; the app passes it because root's $HOME differs)
#   --user NAME      owner given to trashed items when run as root, so the user can empty the Trash
#                    (default: the owner of the home folder)
#   --root DIR       TEST FIXTURE: treat DIR as "/" for `system` and `app` items; receipts are only named
# Exit 0 = done (or dry-run / --needs-admin answered), 2 = bad arguments or a bad manifest,
#      1 = a move failed or the Trash was refused (a symbolic link where ~/.Trash should be).
# Every line it prints is one fact (move / absent / keep / forget / FAILED / REFUSED); the app logs them all.
set -u

main() {
  DRY=0 KEEP=0 ASK=0 HOME_DIR=${HOME:-} OWNER= ROOT= MANIFEST=
  while [ $# -gt 0 ]; do
    case $1 in
      --dry-run) DRY=1 ;;
      --keep-data) KEEP=1 ;;
      --needs-admin) ASK=1 ;;
      --manifest) shift; MANIFEST=${1:-} ;;
      --home) shift; HOME_DIR=${1:-} ;;
      --user) shift; OWNER=${1:-} ;;
      --root) shift; ROOT=${1:-} ;;
      *) echo "unknown argument: $1" >&2; return 2 ;;
    esac
    shift
  done
  [ -n "$MANIFEST" ] || MANIFEST="$(dirname "$0")/uninstall.manifest"
  [ -f "$MANIFEST" ] || { echo "no manifest: '$MANIFEST'" >&2; return 2; }
  [ -n "$HOME_DIR" ] && [ -d "$HOME_DIR" ] || { echo "no home directory: '$HOME_DIR'" >&2; return 2; }
  [ -n "$OWNER" ] || OWNER=$(stat -f %Su "$HOME_DIR")
  TRASH="$HOME_DIR/.Trash"
  FAILED=0
  NEEDS=no

  # ---- read + check the manifest BEFORE anything moves: a bad line stops the whole run with exit 2.
  NAME= ; NOTES= ; APPS= ; NITEMS=0
  LINENO_M=0
  while IFS= read -r line || [ -n "$line" ]; do
    LINENO_M=$((LINENO_M + 1))
    line=$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    case $line in ''|'#'*) continue ;; esac
    kind=$(printf '%s' "$line" | sed -e 's/[[:space:]].*$//')
    rest=$(printf '%s' "$line" | sed -e 's/^[^[:space:]]*[[:space:]]*//')
    [ -n "$rest" ] || { echo "manifest line $LINENO_M: '$kind' has no value" >&2; return 2; }
    case $kind in
      name) NAME=$rest ;;
      note) ;;
      receipt) NITEMS=$((NITEMS + 1)) ;;
      system|app)
        case $rest in /*) ;; *) echo "manifest line $LINENO_M: $kind path must be absolute: $rest" >&2; return 2 ;; esac
        NITEMS=$((NITEMS + 1)) ;;
      user|data)
        case $rest in /*|'~'*) echo "manifest line $LINENO_M: $kind path is relative to the home folder (no leading / or ~): $rest" >&2; return 2 ;; esac
        NITEMS=$((NITEMS + 1)) ;;
      *) echo "manifest line $LINENO_M: unknown kind '$kind'" >&2; return 2 ;;
    esac
    case $kind in system|app|user|data)
      case "/$rest/" in */../*) echo "manifest line $LINENO_M: '..' is not allowed: $rest" >&2; return 2 ;; esac
      # A path that IS a whole shared folder would take other apps' files with it: refuse the obvious ones.
      case $rest in /|/Applications|/Library|/System|/Users|Library|Documents|Desktop|Downloads|'Library/Application Support'|Library/Preferences|Library/Logs|Library/Caches)
        echo "manifest line $LINENO_M: refusing to remove a shared folder: $rest" >&2; return 2 ;; esac ;;
    esac
  done < "$MANIFEST"
  [ -n "$NAME" ] || { echo "manifest has no 'name' line" >&2; return 2; }
  [ "$NITEMS" -gt 0 ] || { echo "manifest lists nothing to remove" >&2; return 2; }
  case $NAME in *APP_NAME*) echo "manifest still has the APP_NAME placeholder: fill it in" >&2; return 2 ;; esac

  say() { if [ "$DRY" = 1 ]; then echo "would $*"; else echo "$*"; fi; }

  # ensure_trash: a user who has never trashed anything has no ~/.Trash.  Made here, it is the
  # user's own and private, as the Finder makes it — never left owned by root, which the user
  # could not empty.  An existing Trash is never touched.  This may run as ROOT inside a folder the
  # user controls, so a Trash that is a symbolic link — there before, or swapped in after the
  # mkdir — is refused, never followed: moving into it, or chown'ing through it, would hand a
  # system directory to the user.
  ensure_trash() {
    if [ -L "$TRASH" ]; then
      echo "REFUSED: $TRASH is a symbolic link, not a folder — nothing is moved into it" >&2
      exit 1                                   # the whole uninstall stops: one line, non-zero
    fi
    [ -d "$TRASH" ] && return 0
    mkdir -m 700 "$TRASH" || return 1
    if [ "$(id -u)" = 0 ]; then
      if [ -L "$TRASH" ] || [ ! -d "$TRASH" ]; then
        echo "REFUSED: $TRASH changed under us (a link, or not a folder) — not giving it to $OWNER" >&2
        exit 1
      fi
      chown -h "$OWNER" "$TRASH" || { echo "FAILED to give $TRASH to $OWNER" >&2; return 1; }
    fi
    return 0
  }

  # movable <path>: can THIS user move it without administrator rights?  Moving needs write access to the
  # folder that holds it; a sticky folder or a root-owned item inside it needs ownership too.
  movable() {
    parent=$(dirname "$1")
    [ -w "$parent" ] || return 1
    [ "$(id -u)" = 0 ] && return 0
    # moving a FOLDER to another folder rewrites its ".." entry: the folder itself must be writable too
    if [ -d "$1" ] && [ ! -L "$1" ] && [ ! -w "$1" ]; then return 1; fi
    if [ -k "$parent" ] && [ "$(stat -f %Su "$1" 2>/dev/null)" != "$(id -un)" ]; then return 1; fi
    return 0
  }

  # trash_item <path>: move to the Trash under a name that cannot collide; owner = the user.
  trash_item() {
    p=$1
    if [ ! -e "$p" ] && [ ! -L "$p" ]; then [ "$ASK" = 1 ] || echo "absent $p"; return 0; fi
    if [ "$ASK" = 1 ]; then movable "$p" || NEEDS=yes; return 0; fi
    base=$(basename "$p"); dest="$TRASH/$base"
    n=0; while [ -e "$dest" ] || [ -L "$dest" ]; do n=$((n + 1)); dest="$TRASH/$base $n"; done
    say "move $p -> $dest"
    [ "$DRY" = 1 ] && return 0
    ensure_trash && mv "$p" "$dest" || { echo "FAILED to move $p" >&2; FAILED=1; return 1; }
    if [ "$(id -u)" = 0 ]; then chown -R "$OWNER" "$dest" 2>/dev/null || true; fi
    return 0
  }

  forget_receipt() {
    id=$1
    if [ -n "$ROOT" ]; then [ "$ASK" = 1 ] || say "forget receipt $id (fixture: not run)"; return 0; fi
    if pkgutil --pkg-info "$id" >/dev/null 2>&1; then
      if [ "$ASK" = 1 ]; then NEEDS=yes; return 0; fi     # pkgutil --forget is root-only
      say "forget receipt $id"
      [ "$DRY" = 1 ] || pkgutil --forget "$id" >/dev/null 2>&1 || { echo "FAILED to forget $id" >&2; FAILED=1; }
    else
      [ "$ASK" = 1 ] || echo "absent receipt $id"
    fi
  }

  # ---- the run, in manifest order; `app` items are held back and moved last.
  while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    case $line in ''|'#'*) continue ;; esac
    kind=$(printf '%s' "$line" | sed -e 's/[[:space:]].*$//')
    rest=$(printf '%s' "$line" | sed -e 's/^[^[:space:]]*[[:space:]]*//')
    case $kind in
      system) trash_item "$ROOT$rest" ;;
      user) trash_item "$HOME_DIR/$rest" ;;
      data)
        if [ "$KEEP" = 1 ]; then [ "$ASK" = 1 ] || echo "keep $HOME_DIR/$rest"
        else trash_item "$HOME_DIR/$rest"; fi ;;
      receipt) forget_receipt "$rest" ;;
      app) APPS="$APPS$rest
" ;;
      note) NOTES="$NOTES$rest
" ;;
    esac
  done < "$MANIFEST"
  # (here-document, not a pipe: a pipe would run the loop in a subshell and lose FAILED / NEEDS)
  while IFS= read -r a; do
    [ -n "$a" ] && trash_item "$ROOT$a"
  done <<EOF
$APPS
EOF

  if [ "$ASK" = 1 ]; then echo "$NEEDS"; return 0; fi
  [ "$FAILED" = 0 ] || return 1
  [ -z "$NOTES" ] || printf '%s' "$NOTES"
  echo "DONE"
  return 0
}

main "$@"
exit $?
