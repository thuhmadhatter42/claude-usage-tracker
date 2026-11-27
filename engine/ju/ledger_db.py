"""E7: usage.db. Schema 3, WAL, STRICT tables, the migration ladder, and the one row type every
ledger chair shares. Nothing else opens the file or creates a table.

A database written by a newer jusage is refused (DbTooNew), never opened read-write and never
"fixed". The ladder runs once, inside one transaction, under a write lock taken before the version
is read, so two processes opening an old database at once migrate it exactly once."""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Sequence

from ju.constants import DB_BUSY_S, DB_SCHEMA
from ju.contracts import JusageError

DB_NAME = "usage.db"
RECORD_COLS = ("key", "ts", "day", "account", "model", "project", "session",
               "input", "cache_5m", "cache_1h", "cache_read", "output", "stop_reason", "complete")
BACKUP_MARKS = (".bak", "backup", ".old")

_SCHEMA = """
CREATE TABLE records (key TEXT PRIMARY KEY, ts TEXT NOT NULL, day TEXT NOT NULL,
  account TEXT NOT NULL,
  model TEXT NOT NULL, project TEXT NOT NULL, session TEXT NOT NULL,
  input INTEGER NOT NULL, cache_5m INTEGER NOT NULL, cache_1h INTEGER NOT NULL,
  cache_read INTEGER NOT NULL, output INTEGER NOT NULL,
  stop_reason TEXT, complete INTEGER NOT NULL DEFAULT 1) STRICT;
CREATE INDEX records_ts ON records(ts);
CREATE INDEX records_acct_day ON records(account, day);
CREATE TABLE files (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL) STRICT;
CREATE TABLE meter_log (ts TEXT NOT NULL, account TEXT NOT NULL, meter TEXT NOT NULL,
  pct REAL NOT NULL, resets_at TEXT, source TEXT NOT NULL DEFAULT 'service',
  PRIMARY KEY (ts, account, meter)) STRICT;
"""

_INSERT = (f"INSERT OR REPLACE INTO records ({', '.join(RECORD_COLS)}) "
           f"VALUES ({', '.join('?' * len(RECORD_COLS))})")

Log = Callable[..., None]
_logger = logging.getLogger("ju.ledger")


def default_log(level: str, msg: str, **kv) -> None:
    _logger.log(getattr(logging, level.upper(), logging.WARNING), msg)


class Row(NamedTuple):
    """One response in the ledger. `account` is the config folder key (resolved absolute path).
    `complete` is 1 when the response carries a stop reason, 0 when it is an unfinished reply."""
    key: str
    ts: str
    day: str
    account: str
    model: str
    project: str
    session: str
    input: int
    cache_5m: int
    cache_1h: int
    cache_read: int
    output: int
    stop_reason: Optional[str]
    complete: int


class DbTooNew(JusageError):
    def __init__(self, n: int):
        super().__init__(f"usage.db is from a newer jusage (schema {n}); update jusage")
        self.n = n


def probe_version(path) -> int:
    """`PRAGMA user_version` of the file, read-only; 0 when the file does not exist. Never creates
    or migrates anything (E25 uses it at bring-up)."""
    path = Path(path)
    if not path.exists():
        return 0
    c = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=DB_BUSY_S)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def insert_record(conn: sqlite3.Connection, row: Row) -> None:
    """Write one row (replacing the same key). Every column is named, in RECORD_COLS order."""
    conn.execute(_INSERT, tuple(row))


# ---------------------------------------------------------------- ladder

def _short_keys(folders: Sequence[str]) -> dict:
    """short display key -> full path, the way 0.1.x stored accounts: the folder name, qualified by
    its parent only when two known folders share a name."""
    names = [Path(f).name for f in folders]
    out = {}
    for f in folders:
        p = Path(f)
        out[f"{p.parent.name}/{p.name}" if names.count(p.name) > 1 else p.name] = str(f)
    return out


def _is_backup(account: str) -> bool:
    last = account.rsplit("/", 1)[-1].lower()
    return any(m in last for m in BACKUP_MARKS)


def _meter_v3(meter: str) -> str:
    if meter == "five_hour":
        return "session"
    if meter == "seven_day":
        return "weekly"
    if meter.startswith("seven_day_"):
        return "weekly:" + meter[len("seven_day_"):]
    return meter


def _step_to_2(c: sqlite3.Connection) -> None:
    # forces a rescan under the worktree -> repo rule
    c.execute("DELETE FROM files")


def _step_to_3(c: sqlite3.Connection, folders: Sequence[str], log: Log) -> None:
    c.execute("ALTER TABLE records ADD stop_reason TEXT")
    c.execute("ALTER TABLE records ADD complete INTEGER NOT NULL DEFAULT 1")
    c.execute("ALTER TABLE meter_log ADD source TEXT NOT NULL DEFAULT 'service'")
    # backup-folder rows go once, here, never again on a later connection
    for table in ("records", "meter_log"):
        for (acct,) in c.execute(f"SELECT DISTINCT account FROM {table}").fetchall():
            if _is_backup(acct):
                c.execute(f"DELETE FROM {table} WHERE account = ?", (acct,))
    # short keys -> full paths
    short = _short_keys(folders)
    seen = set()
    for table in ("records", "meter_log"):
        for (acct,) in c.execute(f"SELECT DISTINCT account FROM {table}").fetchall():
            if acct.startswith("/"):
                continue
            if acct in short:
                c.execute(f"UPDATE {table} SET account = ? WHERE account = ?", (short[acct], acct))
            elif acct not in seen:
                seen.add(acct)
                log("warning", f"usage.db migration: account key {acct!r} matches no known config folder; kept as is")
    for (m,) in c.execute("SELECT DISTINCT meter FROM meter_log").fetchall():
        new = _meter_v3(m)
        if new != m:
            c.execute("UPDATE meter_log SET meter = ? WHERE meter = ?", (new, m))
    c.execute("DELETE FROM files")      # a full rescan fills stop_reason


def _migrate(c: sqlite3.Connection, version: int, folders: Sequence[str], log: Log) -> None:
    if version < 2:
        _step_to_2(c)
    if version < 3:
        _step_to_3(c, folders, log)
    c.execute(f"PRAGMA user_version={DB_SCHEMA}")


def open_db(state, folders: Sequence = (), log: Optional[Log] = None) -> sqlite3.Connection:
    """The connection for `<state>/usage.db`: created at schema 3 when absent, climbed from an
    older schema when present. `folders` = the known config folder keys (E5), used only to turn
    0.1.x short account keys into full paths during the 2 -> 3 step."""
    log = log or default_log
    path = Path(state) / DB_NAME
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)     # 0600 from the first byte
        os.close(fd)
    os.chmod(path, 0o600)
    c = sqlite3.connect(path, timeout=DB_BUSY_S, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        c.execute(f"PRAGMA busy_timeout={DB_BUSY_S * 1000}")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("BEGIN IMMEDIATE")        # the lock comes before the version is read
        try:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            has_records = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone()
            if version > DB_SCHEMA:
                raise DbTooNew(version)
            if not has_records:
                for stmt in _SCHEMA.split(";"):
                    if stmt.strip():
                        c.execute(stmt)
                c.execute(f"PRAGMA user_version={DB_SCHEMA}")
            elif version < DB_SCHEMA:
                _migrate(c, version, [str(f) for f in folders], log)
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        return c
    except BaseException:
        c.close()
        raise
