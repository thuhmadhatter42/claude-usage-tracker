"""E15: the status-line backup. Opt-in, off by default.

Claude Code runs a user's `statusLine` command after every turn and hands it JSON that includes
the account's `rate_limits`. This module (a) saves those two windows per account (`capture`),
(b) wraps the user's existing command so the wrapper (E16) can do that without changing a byte of
what the user sees (`turn_on` / `turn_off`), and (c) turns a saved capture into a Reading only
when the usage service is failing and only when it provably belongs to that account
(`backup_reading`; Claude Code issue #68772 can show the other account's numbers).

The capture file holds numbers and times only: never the model, the prompt or any text.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, Mapping, Optional, Tuple

from ju import timeutil

# The wrapper (E16) runs this module on every status-line refresh and has 50 ms on top of the
# user's own command, so what `capture` needs is imported at the top (json, hashlib, datetime,
# timeutil, contracts) and everything else (files, config, accounts, pathlib, shlex, constants)
# is imported inside the functions that use it: on/off/status and the backup decision, which
# run in the engine, not in the status line.

WRAPPER_NAME = "jusage-statusline"
PCT_MAX = 101                                        # the service can overshoot 100 slightly
_WINDOWS = (("five_hour", "session", "Session"), ("seven_day", "weekly", "Weekly"))


_refused_cls = None


def _refused_class():
    """StatuslineRefused(reason): the backup will not do what was asked; `reason` is one plain
    line. Built on first use (module `__getattr__` serves `ju.statusline.StatuslineRefused`) so
    the contracts module, which only error paths need, is not imported on every status-line
    refresh."""
    global _refused_cls
    if _refused_cls is None:
        from ju.contracts import JusageError

        class StatuslineRefused(JusageError):
            def __init__(self, reason: str):
                super().__init__(reason)
                self.reason = reason

        StatuslineRefused.__module__ = __name__
        _refused_cls = StatuslineRefused
    return _refused_cls


def __getattr__(name):
    if name == "StatuslineRefused":
        return _refused_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _refuse(reason: str):
    return _refused_class()(reason)


# ------------------------------------------------------------------ names and paths

def sha8(key: str) -> str:
    """First 8 hex digits of SHA-256. The C module is used directly: `hashlib` costs 4 ms to import."""
    try:
        from _sha2 import sha256
    except ImportError:                      # Python before 3.12
        from _sha256 import sha256
    return sha256(key.encode("utf-8")).hexdigest()[:8]


def capture_name(key: str) -> str:
    return sha8(key) + ".json"


def folder_key(p) -> str:
    """The same text as accounts.folder_key (resolved absolute path), spelled with os.path so the
    status line does not import the accounts module; tests pin the two together."""
    return os.path.realpath(os.path.expanduser(str(p)))


def _capture_path(state, key: str) -> str:
    return os.path.join(str(state), "statusline", capture_name(key))


def _default_state(env: Mapping[str, str]) -> str:
    return os.path.join(env.get("HOME") or os.path.expanduser("~"), ".claude-usage")


def read_capture(state, folder_key_: str) -> Optional[dict]:
    """The saved capture for the account, or None when there is none. A capture that does not
    parse is moved aside by E3's rules and treated as none."""
    from ju import fsutil
    p = _capture_path(state, folder_key_)
    try:
        return fsutil.read_json(p)
    except FileNotFoundError:
        return None
    except fsutil.CorruptFile:
        fsutil.move_aside_corrupt(p)
        return None


# ------------------------------------------------------------------ capture

def _folder_from_transcript(path: str) -> Optional[str]:
    """`<folder>/projects/<encoded cwd>/<session>.jsonl` -> `<folder>`."""
    projects = os.path.dirname(os.path.dirname(path))
    if os.path.basename(projects) == "projects":
        return folder_key(os.path.dirname(projects))
    return None


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _refuse(f"status line: {what} is not a number")
    return float(v)


def _reset_iso(v, what: str) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return timeutil.iso(datetime.fromtimestamp(float(v), tz=timezone.utc))     # epoch seconds
    if isinstance(v, str):
        try:
            return timeutil.iso(timeutil.parse_iso(v))
        except ValueError as e:
            raise _refuse(f"status line: {what} is not a time with an offset") from e
    raise _refuse(f"status line: {what} is not a time")


def _windows(doc: Mapping) -> Dict[str, dict]:
    limits = doc.get("rate_limits")
    if limits is None:
        return {}
    if not isinstance(limits, dict):
        raise _refuse("status line: rate_limits is not an object")
    out: Dict[str, dict] = {}
    for name, _, _ in _WINDOWS:
        w = limits.get(name)
        if w is None:
            continue
        if not isinstance(w, dict):
            raise _refuse(f"status line: rate_limits.{name} is not an object")
        out[name] = {"used_percentage": _num(w.get("used_percentage"), f"{name} used_percentage"),
                     "resets_at": _reset_iso(w.get("resets_at"), f"{name} resets_at")}
    return out


def capture(stdin: bytes, env: Mapping[str, str], state=None, now: Optional[datetime] = None) -> None:
    """Save Claude Code's `rate_limits` for the account that ran the status line.

    The folder comes from CLAUDE_CONFIG_DIR, else from `transcript_path`; when both are present
    and name different folders the capture is refused (that disagreement is the #68772
    symptom). Nothing is written when the JSON carries no rate limits (Claude Code omits them
    before its first reply), so an earlier capture is never replaced by an empty one."""
    state = str(state) if state is not None else _default_state(env)
    try:
        doc = json.loads(stdin.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _refuse("status line: input is not JSON") from e
    if not isinstance(doc, dict):
        raise _refuse("status line: input is not a JSON object")

    explicit = env.get("CLAUDE_CONFIG_DIR") or None
    from_env = folder_key(explicit) if explicit else None
    tp = doc.get("transcript_path")
    from_tp = _folder_from_transcript(tp) if isinstance(tp, str) and tp else None
    if from_env and from_tp and from_env != from_tp:
        raise _refuse("status line: CLAUDE_CONFIG_DIR and transcript_path name different accounts")
    key = from_env or from_tp
    if key is None:
        raise _refuse("status line: cannot tell which account this is (no CLAUDE_CONFIG_DIR, no transcript_path)")

    windows = _windows(doc)
    if not windows:
        return
    when = now or timeutil.now()
    spellings = {"config_dir": env.get("CLAUDE_CONFIG_DIR") or None,
                 "securestorage": env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR") or None}
    out = {"folder": key, "captured_at": timeutil.iso(when), "config_dir_present": explicit is not None,
           "rate_limits": windows, "spellings": spellings}
    _write_capture(state, key, (json.dumps(out, indent=1) + "\n").encode("utf-8"))
    _remember_spellings(state, key, env)


def _write_capture(state: str, key: str, body: bytes) -> None:
    """Folders 0700, file 0600, replaced by rename so a reader never sees half a file. No fsync:
    a capture is rewritten on the next turn and is worth less than the milliseconds a flush costs
    inside the status line (E3's write_private is for files that must survive a crash)."""
    folder = os.path.join(state, "statusline")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(state, 0o700)
    os.chmod(folder, 0o700)
    final = _capture_path(state, key)
    tmp = os.path.join(folder, f".{capture_name(key)}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _remember_spellings(state: str, key: str, env: Mapping[str, str]) -> None:
    """R-9. config.json is read plainly first and the config lock (E4) is taken only when a hint
    would actually change, so the common path never waits on another process."""
    seen = {"config_dir": env.get("CLAUDE_CONFIG_DIR") or None,
            "securestorage": env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR") or None}
    if not any(seen.values()):
        return
    try:
        with open(os.path.join(state, "config.json"), encoding="utf-8") as f:
            stored = (json.load(f).get("spellings") or {}).get(key) or {}
    except (OSError, ValueError, AttributeError):
        stored = {}
    if not any(v is not None and stored.get(k) != v for k, v in seen.items()):
        return
    from ju import config as cfgmod
    with cfgmod.ConfigStore.edit(state) as cfg:
        cfgmod.remember_spellings(cfg, key, env)


# ------------------------------------------------------------------ settings.json editing

class _Settings:
    """A parsed settings.json plus enough about its layout to write it back the way it was."""

    def __init__(self, path: Path, data: dict, indent: Optional[int], newline: bool, raw: Optional[bytes]):
        self.path, self.data, self.indent, self.newline, self.raw = path, data, indent, newline, raw

    def dumps(self, data: Optional[dict] = None) -> bytes:
        text = json.dumps(self.data if data is None else data, indent=self.indent, ensure_ascii=False)
        return (text + ("\n" if self.newline else "")).encode("utf-8")


def _indent_of(text: str) -> Optional[int]:
    lines = text.splitlines()
    for line in lines[1:]:
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return None if len(lines) <= 1 else 2


def _load_settings(key: str, verb: str) -> _Settings:
    from pathlib import Path
    link = Path(key) / "settings.json"
    path = Path(os.path.realpath(link))          # edit the file a symlink points at, never the link
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _Settings(path, {}, 2, True, None)
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise _refuse(f"statusline {verb}: {link} is not valid JSON; not touched") from None
    if not isinstance(data, dict):
        raise _refuse(f"statusline {verb}: {link} is not a JSON object; not touched")
    return _Settings(path, data, _indent_of(text), text.endswith("\n"), raw)


def _command_of(data: dict, verb: str, where) -> Tuple[Optional[str], str]:
    """(command, shape) of the `statusLine` key. shape: none | string | object."""
    if "statusLine" not in data:
        return None, "none"
    v = data["statusLine"]
    if isinstance(v, str):
        return v, "string"
    if isinstance(v, dict) and v.get("type") == "command" and isinstance(v.get("command"), str):
        return v["command"], "object"
    raise _refuse(f"statusline {verb}: the statusLine in {where} is not a command; not touched")


def _with_command(data: dict, command: Optional[str], shape: str) -> dict:
    import copy
    """A copy of `data` whose statusLine carries `command`; only that key changes."""
    out = copy.deepcopy(data)
    if command is None:
        out.pop("statusLine", None)
    elif shape == "string":
        out["statusLine"] = command
    elif shape == "object" and isinstance(out.get("statusLine"), dict):
        out["statusLine"]["command"] = command
    else:
        out["statusLine"] = {"type": "command", "command": command}
    return out


def _write_settings(s: _Settings, body: bytes) -> None:
    import stat
    from ju import fsutil
    mode = stat.S_IMODE(s.path.stat().st_mode) if s.path.exists() else 0o600
    fsutil.write_private(s.path, body)
    os.chmod(s.path, mode)


def _record(cfg, key: str) -> Optional[dict]:
    rec = cfg.statusline.get(key)
    return rec if isinstance(rec, dict) else None


def _set_record(cfg, key: str, rec: dict) -> None:
    statusline = dict(cfg.statusline)
    statusline[key] = rec
    cfg.statusline = statusline


def _wrapped(python: str, engine_dir, original: Optional[str]) -> str:
    import shlex
    from pathlib import Path
    head = f"{shlex.quote(python)} -B -E -s {shlex.quote(str(Path(engine_dir) / WRAPPER_NAME))} --"
    return head if original is None else f"{head} {shlex.quote(original)}"


# ------------------------------------------------------------------ on / off / status

def turn_on(state, folder: str, *, python: Optional[str] = None, engine_dir=None,
            now: Optional[datetime] = None) -> str:
    """Wrap the account's statusLine command; returns the wrapped command.

    Records the original verbatim in config.json, copies settings.json into `archive/`, proves
    on a copy that `off` would restore it exactly, then rewrites settings.json atomically,
    touching only the statusLine command. Already on and unchanged: nothing happens."""
    import copy, shlex, stat
    from pathlib import Path
    from ju import accounts, config as cfgmod, constants, fsutil
    key = folder_key(folder)
    with cfgmod.ConfigStore.edit(state) as cfg:
        s = _load_settings(key, "on")
        current, shape = _command_of(s.data, "on", s.path)
        rec = _record(cfg, key)
        if rec and rec.get("enabled"):
            if current == rec.get("wrapped"):
                return current
            raise _refuse(f"statusline on: the statusLine in {s.path} was changed after jusage "
                                    f"wrapped it; run `jusage statusline off` first")
        if current is not None and WRAPPER_NAME in current:
            raise _refuse(f"statusline on: the statusLine in {s.path} already runs {WRAPPER_NAME} "
                                    f"but jusage has no record of wrapping it; not touched")
        py = python or cfg.python
        if not py or not os.path.isabs(py) or not os.path.isfile(py):
            raise _refuse("statusline on: no usable Python is recorded; set it with `jusage config python <path>`")
        wrapped = _wrapped(py, engine_dir or constants.HERE.parent, current)

        after = _with_command(s.data, wrapped, shape)
        restored = _with_command(after, current, shape)
        if s.dumps(restored) != s.dumps():
            raise _refuse(f"statusline on: turning it off again would not restore {s.path} exactly; not touched")

        if s.raw is not None:
            when = now or timeutil.now()
            fsutil.ensure_state_dir(Path(state) / "archive")
            stamp = when.astimezone().strftime("%Y%m%dT%H%M%S%z")
            _archive(Path(state) / "archive", f"settings-{sha8(key)}-{stamp}", s.raw)
        _write_settings(s, s.dumps(after))
        _set_record(cfg, key, {"enabled": True, "original": current, "wrapped": wrapped, "shape": shape})
        return wrapped


def _archive(folder, stem: str, raw: bytes):
    from ju import fsutil
    n = 0
    while True:
        p = folder / (f"{stem}.json" if n == 0 else f"{stem}-{n}.json")
        if not p.exists():
            fsutil.write_private(p, raw)
            return p
        n += 1


def turn_off(state, folder: str) -> None:
    """Restore the recorded original exactly, but only while the current statusLine is still our
    wrapper. Anyone else's edit is left alone and reported."""
    import copy, shlex, stat
    from pathlib import Path
    from ju import accounts, config as cfgmod, constants, fsutil
    key = folder_key(folder)
    with cfgmod.ConfigStore.edit(state) as cfg:
        rec = _record(cfg, key)
        if not rec or not rec.get("enabled"):
            raise _refuse(f"statusline off: the status-line backup is not on for {key}")
        s = _load_settings(key, "off")
        current, _ = _command_of(s.data, "off", s.path)
        if current != rec.get("wrapped"):
            raise _refuse(f"statusline off: the statusLine in {s.path} was changed since jusage "
                                    f"turned it on; not touched, restore it by hand")
        restored = _with_command(s.data, rec.get("original"), rec.get("shape") or "object")
        _write_settings(s, s.dumps(restored))
        _set_record(cfg, key, {"enabled": False, "original": None, "wrapped": rec["wrapped"],
                               "shape": rec.get("shape") or "object"})


def status(state, folder: Optional[str] = None) -> Dict[str, dict]:
    """`{folder key: {"enabled": bool, "capture_at": ISO|None}}` for every folder jusage knows
    about, or just `folder_key`."""
    from ju import config as cfgmod
    cfg = cfgmod.ConfigStore.load(state)
    keys = [folder_key(folder)] if folder else sorted(set(cfg.statusline) | set(cfg.config_dirs))
    out = {}
    for k in keys:
        rec = cfg.statusline.get(k)
        cap = read_capture(state, k)
        out[k] = {"enabled": bool(rec and rec.get("enabled")),
                  "capture_at": cap.get("captured_at") if cap else None}
    return out


# ------------------------------------------------------------------ the backup reading

def capture_to_reading(cap: Mapping, uuid: Optional[str]) -> "Reading":
    """Session and weekly from a saved capture. Raises StatuslineRefused on a damaged capture."""
    from ju import constants
    from ju.contracts import Meter, Reading
    periods = {"session": constants.SESSION_S, "weekly": constants.WEEK_S}
    try:
        when = timeutil.parse_iso(cap["captured_at"])
        limits = cap["rate_limits"]
        meters = []
        for name, mid, label in _WINDOWS:
            w = limits.get(name)
            if w is None:
                continue
            pct = _num(w["used_percentage"], name)
            meters.append(Meter(mid, label, pct, timeutil.parse_iso(w["resets_at"]), periods[mid]))
        return Reading(cap["folder"], when, meters, [], uuid, "statusline")
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise _refuse(f"status-line capture is damaged ({type(e).__name__})") from e


def _near_minute(a: int, b: int) -> bool:
    from ju import constants
    week = 7 * 24 * 60
    d = abs(a - b) % week
    return min(d, week - d) <= constants.FINGERPRINT_TOL_S // 60


def backup_reading(acc, cap: Optional[Mapping], others: Mapping[str, int], now: datetime,
                   *, key: Optional[str] = None) -> Tuple[Optional["Reading"], str]:
    """The status-line capture as a Reading, or (None, reason).

    Only while the service read is failing or gated; capture younger than 15 minutes; every
    percentage 0..101; the capture's folder is this account's (`key`); and the weekly reset
    falls within 120 s of this account's LEARNED weekly reset. A reset matching another
    account's learned minute (`others`: folder key -> minute) is that account's data (#68772).
    Session and weekly only: the status line has no per-model window."""
    from ju import constants
    failing = acc.error is not None or (acc.blocked_until is not None and acc.blocked_until > now)
    if not failing:
        return None, "the usage service reading is fine; the status line is not needed"
    if cap is None:
        return None, "no status-line capture for this account"
    if key is not None and cap.get("folder") != key:
        return None, "the capture is from a different folder"
    try:
        reading = capture_to_reading(cap, acc.identity_uuid)
    except _refused_class() as e:
        return None, e.reason
    age = (now - reading.fetched).total_seconds()
    if age < 0:
        return None, "the capture is dated in the future"
    if age >= constants.STALE_AFTER_S:
        return None, "the capture is too old (15 minutes or more)"
    for m in reading.meters:
        if not (0 <= m.pct <= PCT_MAX):
            return None, f"the capture's {m.id} percentage {m.pct} is outside 0 to {PCT_MAX}"
    weekly = next((m for m in reading.meters if m.id == "weekly"), None)
    if weekly is None:
        return None, "the capture has no weekly window, so its account cannot be checked"
    own = (acc.reset_anchor or {}).get("minute_of_week")
    if own is None:
        return None, "no weekly reset has been learned for this account yet"
    minute = timeutil.minute_of_week(weekly.resets_at)
    if _near_minute(minute, own):
        return reading, "ok"
    if any(_near_minute(minute, m) for m in others.values()):
        return None, "the capture carries another account's weekly reset (Claude Code issue #68772)"
    return None, "the capture's weekly reset does not match this account's learned reset"


def disagree(service: "Reading", cap: "Reading") -> bool:
    """True when the two readings differ by more than 5 points or 120 s on any meter both carry."""
    from ju import constants
    theirs = {m.id: m for m in cap.meters}
    for m in service.meters:
        o = theirs.get(m.id)
        if o is None:
            continue
        if abs(m.pct - o.pct) > constants.DISAGREE_PCT:
            return True
        if m.resets_at is not None and o.resets_at is not None \
                and abs((m.resets_at - o.resets_at).total_seconds()) > constants.DISAGREE_RESET_S:
            return True
    return False
