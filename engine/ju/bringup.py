"""E25: engine bring-up. Run before any command; the first problem raises BringUpRefused with one
line `jusage: <file>: <problem>` (claude_usage.py prints it and exits 2). No migration happens here.

What it checks: VERSION is major.minor.patch, pricing.json loads (E6), themes.json validates (E27),
the state folder is a 0700 folder, usage.db is no newer than this jusage reads (E7 probe), and
live.json carries a schema this jusage knows (2, or one of the old unversioned shapes E13 migrates).
A corrupt live.json is not refused here: E13 moves it aside and starts empty (R-33).
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Callable, Optional

from ju import constants, themes
from ju.contracts import BringUpRefused

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _refuse(file: str, problem: str) -> BringUpRefused:
    return BringUpRefused(f"jusage: {file}: {problem}")


def _check_version(root: Path) -> None:
    try:
        text = (root / "VERSION").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise _refuse("VERSION", "file is missing") from None
    except (OSError, UnicodeDecodeError) as e:
        raise _refuse("VERSION", f"cannot be read ({e})") from None
    if not _VERSION.match(text):
        raise _refuse("VERSION", f"not major.minor.patch: {text!r}")


def _check_pricing(path: Path, load: Optional[Callable]) -> None:
    if load is None:
        mod = importlib.import_module("ju.pricing")
        load, catch = mod.load, (mod.PricingInvalid, OSError, ValueError)
    else:
        catch = (OSError, ValueError)
    if not path.exists():
        raise _refuse("pricing.json", "file is missing")
    try:
        load(path)
    except catch as e:                  # PricingInvalid (E6); unreadable; not JSON
        raise _refuse("pricing.json", str(e)) from None


def _check_state_dir(state: Path) -> None:
    try:
        st = os.stat(state)
    except FileNotFoundError:
        return                                  # a fresh Mac: the first command creates it, 0700
    if not stat.S_ISDIR(st.st_mode):
        raise _refuse(str(state), "not a folder")
    perm = stat.S_IMODE(st.st_mode)
    if perm != 0o700:
        raise _refuse(str(state), f"permissions are {perm:04o}, must be 0700")


def _check_db(state: Path, probe: Optional[Callable]) -> None:
    db = state / "usage.db"
    if not db.exists():
        return
    if probe is None:
        probe = importlib.import_module("ju.ledger_db").probe_version
    try:
        version = probe(db)
    except sqlite3.Error as e:
        raise _refuse("usage.db", f"not a readable database ({e})") from None
    if version > constants.DB_SCHEMA:
        raise _refuse("usage.db", f"schema {version} is newer than this jusage reads ({constants.DB_SCHEMA}); update jusage")


def _check_live(state: Path) -> None:
    path = state / "live.json"
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return
    except (OSError, UnicodeDecodeError, ValueError):
        return                                  # corrupt: E13 moves it aside (R-33)
    if not isinstance(data, dict) or "schema" not in data:
        return                                  # unversioned legacy shapes: E13 migrates once
    schema = data["schema"]
    if type(schema) is not int or schema != constants.LIVE_SCHEMA:
        raise _refuse(
            "live.json",
            f"schema {schema!r} is not known to this jusage (reads {constants.LIVE_SCHEMA} and the old unversioned shapes); update jusage",
        )


def check(state, *, root=None, engine_dir=None, pricing_load: Optional[Callable] = None,
          probe_version: Optional[Callable] = None) -> None:
    """Raise BringUpRefused for the first problem, else return None.

    `root` (holds VERSION) and `engine_dir` (holds pricing.json, themes.json) default to the
    shipped layout; `pricing_load` and `probe_version` default to E6's `pricing.load` and E7's
    `ledger_db.probe_version` and exist so tests can stand in for them."""
    state = Path(state)
    root = Path(root) if root is not None else constants.REPO_ROOT
    engine_dir = Path(engine_dir) if engine_dir is not None else constants.HERE.parent
    _check_version(root)
    _check_pricing(engine_dir / "pricing.json", pricing_load)
    try:
        themes.load_themes(engine_dir / "themes.json")
    except themes.ThemesInvalid as e:
        raise BringUpRefused(f"jusage: {e}") from None
    _check_state_dir(state)
    _check_db(state, probe_version)
    _check_live(state)
