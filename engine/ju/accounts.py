"""E5: which config folders are accounts, their stable keys, numbers and identities.

A folder's key is its full resolved path (R-106); the short display name is derived and never
stored. Numbers: `~/.claude` is #1, `~/.claude-newaccount` is #2 (reserved even if seen later),
everything else takes the next number on first sight, and nothing is ever renumbered.
"""
from __future__ import annotations

import json
import os
from collections import namedtuple
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from ju import houselog
from ju.config import Config, ConfigReadOnly

Identity = namedtuple("Identity", "email uuid mtime")

_BACKUP_MARKS = (".bak", "backup", ".old")
_STATE_NAMES = (".claude-usage",)       # jusage's own state folders are never accounts


def _home(env: Optional[Mapping[str, str]] = None) -> Path:
    h = (env or {}).get("HOME") or os.path.expanduser("~")
    return Path(h)


def folder_key(p) -> str:
    """Resolved absolute path as text. `~` is expanded; a trailing slash or a symlink makes no difference."""
    return str(Path(os.path.expanduser(str(p))).resolve())


def _is_backup(name: str) -> bool:
    low = name.lower()
    return any(m in low for m in _BACKUP_MARKS)


def _is_state(name: str) -> bool:
    return any(name == s or name.startswith(s + "-") for s in _STATE_NAMES)


def _is_account_folder(d: Path) -> bool:
    return d.is_dir() and ((d / "projects").is_dir() or (d / ".claude.json").is_file())


def discover_folders(env: Mapping[str, str], cfg: Config) -> List[Path]:
    """Account folders on this Mac, resolved, de-duplicated, sorted by path:
    `~/.claude*` holding `projects/` or `.claude.json` (minus backups), the folder named by
    CLAUDE_CONFIG_DIR, and folders remembered in config.json that still exist."""
    found: Dict[str, Path] = {}
    home = _home(env)
    try:
        entries = sorted(home.iterdir())
    except OSError:
        entries = []
    for d in entries:
        if d.name.startswith(".claude") and not _is_backup(d.name) and not _is_state(d.name) \
                and _is_account_folder(d):
            found.setdefault(folder_key(d), Path(folder_key(d)))
    explicit = (env or {}).get("CLAUDE_CONFIG_DIR")
    if explicit:
        d = Path(os.path.expanduser(explicit))
        if d.is_dir():
            found.setdefault(folder_key(d), Path(folder_key(d)))
    for key in cfg.config_dirs:
        d = Path(key)
        if d.is_dir() and not _is_backup(d.name) and not _is_state(d.name):
            found.setdefault(folder_key(d), Path(folder_key(d)))
    return [found[k] for k in sorted(found)]


def short_name(key: str, all_keys) -> str:
    """Display name: the folder's own name; `parent/name` when another account shares it;
    the full key if even that clashes. Never stored, never a key."""
    p = Path(key)
    keys = list(all_keys)
    if sum(1 for k in keys if Path(k).name == p.name) <= 1:
        return p.name
    qualified = f"{p.parent.name}/{p.name}"
    if sum(1 for k in keys if f"{Path(k).parent.name}/{Path(k).name}" == qualified) <= 1:
        return qualified
    return key


def _reserved(key: str) -> Optional[int]:
    home = Path(os.path.expanduser("~"))
    if key == folder_key(home / ".claude"):
        return 1
    if key == folder_key(home / ".claude-newaccount"):
        return 2
    return None


def number_for(cfg: Config, key: str) -> int:
    """The account's number: already assigned, else 1/2 for the two reserved folders, else the
    next free number (never below 3). Assigning needs an editable config."""
    assigned = cfg.accounts.get(key)
    if assigned is not None:
        return assigned
    number = _reserved(key)
    if number is None:
        if cfg.frozen:
            raise ConfigReadOnly(f"{key} has no account number yet; assign it inside ConfigStore.edit")
        number = max([2] + list(cfg.accounts.values())) + 1
    if not cfg.frozen:
        accounts = dict(cfg.accounts)
        accounts[key] = number
        cfg.accounts = accounts
    return number


def ordered(keys, cfg: Config) -> List[str]:
    """Keys sorted by account number (never by email or name)."""
    return sorted(keys, key=lambda k: number_for(cfg, k))


def _read_oauth(path: Path) -> Optional[Identity]:
    try:
        o = json.loads(path.read_text(encoding="utf-8")).get("oauthAccount") or {}
        if o.get("emailAddress") and o.get("accountUuid"):
            return Identity(o["emailAddress"], o["accountUuid"], path.stat().st_mtime)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def read_identity(key: str, cfg: Optional[Config] = None) -> Optional[Identity]:
    """Who the folder is logged in as, from the `oauthAccount` block in the newest of
    `<folder>/.claude.json` and (for `~/.claude` only) `~/.claude.json`. Reads no token.
    When the file is unreadable mid-write the cached identity in `cfg` is returned (and logged);
    on an editable config a fresh identity is cached."""
    home = Path(os.path.expanduser("~"))
    paths = [Path(key) / ".claude.json"]
    if key == folder_key(home / ".claude"):
        paths.append(home / ".claude.json")
    found = [i for i in (_read_oauth(p) for p in paths) if i is not None]
    cache = cfg.identities if cfg is not None else {}
    if found:
        ident = max(found, key=lambda i: i.mtime)
        if cfg is not None and not cfg.frozen and cache.get(key) != {"email": ident.email, "uuid": ident.uuid}:
            identities = dict(cache)
            identities[key] = {"email": ident.email, "uuid": ident.uuid}
            cfg.identities = identities
        return ident
    cached = cache.get(key)
    if cached and cached.get("email") and cached.get("uuid"):
        houselog.log("warning", "identity: .claude.json unreadable, using cached identity", folder=key)
        return Identity(cached["email"], cached["uuid"], 0.0)
    return None


def group_by_uuid(keys, identities: Mapping[str, Optional[Identity]]) -> List[List[str]]:
    """Folders sharing one account uuid form one group; a folder with no identity stands alone.
    Groups come in order of their first folder in `keys`."""
    groups: List[List[str]] = []
    index: Dict[str, int] = {}
    for k in keys:
        ident = identities.get(k)
        if ident is None:
            groups.append([k])
        elif ident.uuid in index:
            groups[index[ident.uuid]].append(k)
        else:
            index[ident.uuid] = len(groups)
            groups.append([k])
    return groups
