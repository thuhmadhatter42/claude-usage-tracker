"""E4: the engine's settings file, `<state>/config.json`.

One writer: `with ConfigStore.edit(state) as cfg:` takes the file lock, reloads, lets the caller
change `cfg`, and saves on a clean exit. `ConfigStore.load(state)` is a read-only snapshot (it
refuses every change). Keys this version does not know are kept on every write.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from ju import constants, fsutil, houselog
from ju.contracts import JusageError

_LOCK_WAIT_S = 30.0


class ConfigError(JusageError):
    """A setting the user can fix. The message is one plain line."""


class ConfigReadOnly(ConfigError):
    """A change was attempted on a read-only snapshot (use ConfigStore.edit)."""


# key -> (default factory, validator message or None). Validators return an error text or None.
def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_str_or_none(v):
    return None if v is None or isinstance(v, str) else "must be text or null"


def _check_bool(v):
    return None if isinstance(v, bool) else "must be true or false"


def _check_read_every(v):
    if not _is_int(v) or not (constants.READ_EVERY_MIN <= v <= constants.READ_EVERY_MAX):
        return f"must be a whole number from {constants.READ_EVERY_MIN} to {constants.READ_EVERY_MAX}"
    return None


def _check_dict(v):
    return None if isinstance(v, dict) else "must be an object"


def _check_str_list(v):
    return None if isinstance(v, list) and all(isinstance(x, str) for x in v) else "must be a list of text"


def _check_accounts(v):
    if not isinstance(v, dict) or not all(_is_int(n) and n >= 1 for n in v.values()):
        return "must be an object of folder -> number"
    return None


_KEYS = {
    "user": (lambda: None, _check_str_or_none),
    "reports_dir": (lambda: None, _check_str_or_none),
    "share_projects": (lambda: False, _check_bool),
    "install_id": (lambda: None, _check_str_or_none),
    "python": (lambda: None, _check_str_or_none),
    "read_every_s": (lambda: constants.READ_EVERY_DEFAULT, _check_read_every),
    "config_dirs": (list, _check_str_list),
    "spellings": (dict, _check_dict),
    "identities": (dict, _check_dict),
    "accounts": (dict, _check_accounts),
    "statusline": (dict, _check_dict),
}


class Config:
    """The settings, as attributes (and `cfg["key"]`). Defaults fill what the file lacks; the raw
    mapping (`cfg.raw`) keeps every key the file had, known or not."""

    def __init__(self, raw: dict, frozen: bool):
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "_frozen", frozen)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def _get(self, key):
        if key in self.raw:
            return self.raw[key]
        default = _KEYS[key][0]()
        if not self._frozen and isinstance(default, (list, dict)):
            self.raw[key] = default          # editable: nested mutation lands in the file
        return default

    def _set(self, key, value):
        if self._frozen:
            raise ConfigReadOnly(f"config.json is a read-only snapshot; cannot set {key} (use ConfigStore.edit)")
        problem = _KEYS[key][1](value) if key in _KEYS else None
        if problem:
            raise ConfigError(f"config.json: {key} {problem}")
        self.raw[key] = value

    def __getattr__(self, name):
        if name in _KEYS:
            return self._get(name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in _KEYS:
            self._set(name, value)
        else:
            raise AttributeError(f"Config has no setting {name!r}")

    def __getitem__(self, key):
        if key in _KEYS:
            return self._get(key)
        return self.raw[key]

    def __setitem__(self, key, value):
        self._set(key, value)

    def get(self, key, default=None):
        if key in _KEYS:
            return self._get(key)
        return self.raw.get(key, default)

    def __repr__(self):
        return f"Config({sorted(self.raw)})"


def _validate(raw: dict) -> None:
    for key, (_, check) in _KEYS.items():
        if key in raw:
            problem = check(raw[key])
            if problem:
                raise ConfigError(f"config.json: {key} {problem}")


def _default_notify(line: str) -> None:
    print(line, file=sys.stderr)


def _read(state: Path, notify: Optional[Callable[[str], None]]) -> dict:
    path = state / "config.json"
    try:
        raw = fsutil.read_json(path)
    except FileNotFoundError:
        return {}
    except fsutil.CorruptFile as e:
        moved = fsutil.move_aside_corrupt(path)
        line = f"jusage: config.json was unreadable; moved to {moved.name}, starting with empty settings"
        houselog.log("error", "config.json corrupt, moved aside", state=state, moved=moved.name, why=str(e))
        (notify or _default_notify)(line)
        return {}
    _validate(raw)
    return raw


class ConfigStore:
    @staticmethod
    def load(state, notify: Optional[Callable[[str], None]] = None) -> Config:
        """A read-only snapshot. A corrupt file is moved aside and an empty config returned."""
        return Config(_read(Path(state), notify), frozen=True)

    @staticmethod
    @contextlib.contextmanager
    def edit(state, notify: Optional[Callable[[str], None]] = None) -> Iterator[Config]:
        """Lock, reload, yield an editable Config, save on a clean exit if anything changed."""
        state = Path(state)
        fsutil.ensure_state_dir(state)
        lock_fd = os.open(state / "config.json.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(lock_fd, 0o600)
            deadline = time.monotonic() + _LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise ConfigError("config.json is locked by another jusage that is not finishing") from None
                    time.sleep(0.02)
            raw = _read(state, notify)
            before = copy.deepcopy(raw)
            cfg = Config(raw, frozen=False)
            yield cfg
            _validate(raw)
            if raw != before:
                body = json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
                fsutil.write_private(state / "config.json", body.encode("utf-8"))
        finally:
            os.close(lock_fd)


# ---------------------------------------------------------------- helpers

def install_id(cfg: Config) -> str:
    """The 4-hex id of this install (names this Mac's shared report). Created once, in an edit."""
    current = cfg.install_id
    if current:
        return current
    if cfg.frozen:
        raise ConfigReadOnly("config.json has no install_id yet; create it inside ConfigStore.edit")
    cfg.install_id = secrets.token_hex(2)
    return cfg.install_id


def user_name(cfg: Config) -> str:
    """Your name in shared reports and the feed's `me.user`: the one set (set-user / --user), else the
    installer's default, the first word of this Mac account's full name, else the account name."""
    if cfg.user:
        return cfg.user
    import pwd
    pw = pwd.getpwuid(os.getuid())
    return (pw.pw_gecos.split() or [pw.pw_name])[0]


def set_read_every(cfg: Config, seconds) -> None:
    """Refuses anything but a whole number of seconds from 15 to 600."""
    problem = _check_read_every(seconds)
    if problem:
        raise ConfigError(f"read-every {problem}, not {seconds!r}")
    cfg.read_every_s = seconds


def remember_spellings(cfg: Config, folder: str, env: Mapping[str, str]) -> bool:
    """R-9: keep, per account, the exact spelling of CLAUDE_CONFIG_DIR and
    CLAUDE_SECURESTORAGE_CONFIG_DIR a run saw (the app has no shell environment). Only what was
    set is stored; no secret is involved. True when the stored value changed."""
    seen = {
        "config_dir": env.get("CLAUDE_CONFIG_DIR") or None,
        "securestorage": env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR") or None,
    }
    if not any(seen.values()):
        return False
    spellings = dict(cfg.spellings)
    entry = dict(spellings.get(folder) or {"config_dir": None, "securestorage": None})
    changed = False
    for key, value in seen.items():
        if value is not None and entry.get(key) != value:
            entry[key] = value
            changed = True
    entry.setdefault("config_dir", None)
    entry.setdefault("securestorage", None)
    if changed:
        spellings[folder] = entry
        cfg.spellings = spellings
    return changed


def remember_folders(cfg: Config, keys) -> bool:
    """Add folder keys to `config_dirs` (every folder ever seen). True when any was new."""
    dirs = list(cfg.config_dirs)
    new = [k for k in keys if k not in dirs]
    if not new:
        return False
    for k in new:
        if k not in dirs:
            dirs.append(k)
    cfg.config_dirs = dirs
    return True


def reports_dir(cfg: Config, flag: Optional[str], create: bool) -> Path:
    """The shared reports folder: `flag` (a `--reports` value) else the stored one. A missing
    folder is an error unless `create` (an explicit --reports, R-119). On an editable config a
    flag that works is remembered."""
    chosen = flag or cfg.reports_dir
    if not chosen:
        raise ConfigError("No shared folder is set; pass --reports <folder>")
    path = Path(os.path.expanduser(chosen))
    if path.exists() and not path.is_dir():
        raise ConfigError(f"Shared folder {path} is not a folder")
    if not path.exists():
        if not create:
            raise ConfigError(f"Shared folder {path} is missing; pass --reports to create it")
        path.mkdir(parents=True, exist_ok=True)
    if flag and not cfg.frozen:
        cfg.reports_dir = str(path)
    return path
