"""E13: the live store. `live.json` schema 2 (SCORE §1.6.2) is the one shape for every account's
last reading, last error and gate state. One writer: the fetch gate (E14), plus the one-time
legacy migration inside `load`. Readers get a `Live` from the gate's `RefreshResult`.

File access goes through `StoreIO` (E3's write_private / move_aside_corrupt / read_json and
CorruptFile, E2's log), handed in by the caller, so this module owns the shape and nothing else.

Rigid on read: a schema-2 file whose shape is wrong in any way (missing or extra field, unknown
error kind, a time without an offset, a key that is not an absolute folder path) is corrupt:
moved aside, logged, and the store starts empty (R-33). An unknown `schema` is refused (R-34).
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Union

from .constants import LIVE_SCHEMA, STALE_AFTER_S
from .contracts import ERROR_KINDS, BringUpRefused, Reading, ReplyChanged
from .timeutil import iso, parse_iso

FILE_NAME = "live.json"
SOURCES = ("service", "statusline")
ANCHOR_KEYS = ("minute_of_week", "confirmations", "candidate", "candidate_count")


def empty_anchor() -> dict:
    """The weekly-reset fingerprint state before anything is learned. E12 owns what it means."""
    return {"minute_of_week": None, "confirmations": 0, "candidate": None, "candidate_count": 0}


@dataclass(frozen=True)
class ErrorInfo:
    kind: str                     # one of contracts.ERROR_KINDS
    text: str
    at: datetime
    other: Optional[str] = None   # identity_foreign_reset only: the folder key whose reset matched (R-14)

    def __post_init__(self):
        if self.kind not in ERROR_KINDS:
            raise ValueError(f"unknown error kind {self.kind!r}")
        if self.other is not None and (self.kind != "identity_foreign_reset" or not isinstance(self.other, str)):
            raise ValueError("only an identity_foreign_reset error names another account")


@dataclass(frozen=True)
class AccountLive:
    """One config folder's entry. `data` is the last good reply; `error` the most recent failure.
    Both set = a stale reading, never a guessed number."""
    fetched: Optional[datetime] = None
    data: Optional[dict] = None
    error: Optional[ErrorInfo] = None
    warning: Optional[str] = None
    source: str = "service"
    blocked_until: Optional[datetime] = None
    backoff_step: int = 0
    retry_after_explicit: bool = False
    last_manual: Optional[datetime] = None
    reset_anchor: dict = field(default_factory=empty_anchor)
    identity_uuid: Optional[str] = None

    def is_stale(self, now: datetime) -> bool:
        """A number exists but cannot be trusted as current: the last attempt failed, or the
        reading is STALE_AFTER_S old or more. No data at all is an error, not a stale number."""
        if self.data is None:
            return False
        if self.error is not None:
            return True
        return self.fetched is None or (now - self.fetched).total_seconds() >= STALE_AFTER_S


@dataclass
class Live:
    accounts: Dict[str, AccountLive] = field(default_factory=dict)

    def account(self, key: str) -> AccountLive:
        """The folder's entry, or an empty one (not stored) when it has never been read."""
        return self.accounts.get(key, AccountLive())

    def put(self, key: str, acc: AccountLive) -> None:
        _check_key(key)
        self.accounts[key] = acc


@dataclass(frozen=True)
class StoreIO:
    write_private: Callable[[Path, bytes], None]      # E3: temp + fsync + rename, 0600
    move_aside_corrupt: Callable[[Path], Path]        # E3: .corrupt, .corrupt.1, … never overwrite
    read_json: Callable[[Path], dict]                 # E3: raises corrupt_error on bad JSON / non-object
    corrupt_error: type                               # E3: CorruptFile
    log: Callable[..., None]                          # E2: log(level, msg, **kv), never raises
    check_reply: Callable[[dict], object]             # E11 parse: raises ReplyChanged on a reply it refuses


class _BadShape(ValueError):
    pass


class LiveStore:
    def __init__(self, io: StoreIO):
        self.io = io

    @staticmethod
    def path(state: Path) -> Path:
        return Path(state) / FILE_NAME

    def probe(self, state: Path) -> Union[int, str]:
        """For bring-up (E25), touching nothing: "absent", "legacy", "corrupt" or LIVE_SCHEMA.
        Any other schema raises BringUpRefused."""
        p = self.path(state)
        if not p.exists():
            return "absent"
        try:
            raw = self.io.read_json(p)
        except self.io.corrupt_error:
            return "corrupt"
        if "schema" not in raw:
            return "legacy"
        _check_schema(raw["schema"])
        return LIVE_SCHEMA

    def load(self, state: Path, folders: Sequence[str]) -> Live:
        """Read live.json. `folders` = this Mac's config folder keys (E5), used only to map a
        legacy file's short names to full keys; the migrated file is written once, here."""
        p = self.path(state)
        kind = self.probe(state)
        if kind == "absent":
            return Live()
        if kind == "corrupt":
            return self._aside(p, "not valid JSON")
        raw = self.io.read_json(p)
        if kind == "legacy":
            live = self._migrate(raw, folders)
            self.save(state, live)
            self.io.log("info", "live.json migrated from the legacy shape to schema 2",
                        accounts=len(live.accounts))
            return live
        try:
            return _from_json(raw)
        except (_BadShape, ValueError) as e:
            return self._aside(p, str(e))

    def save(self, state: Path, live: Live) -> None:
        doc = {"schema": LIVE_SCHEMA, "accounts": {k: _acc_to_json(a) for k, a in sorted(live.accounts.items())}}
        self.io.write_private(self.path(state), (json.dumps(doc, indent=1) + "\n").encode("utf-8"))

    def _aside(self, p: Path, why: str) -> Live:
        dest = self.io.move_aside_corrupt(p)
        self.io.log("error", "live.json corrupt; moved aside, starting empty", reason=why, moved_to=str(dest))
        return Live()

    def _migrate(self, raw: dict, folders: Sequence[str]) -> Live:
        """0.1.19+ shape {short name: {fetched, data, error, warning?}} and the older one (error
        inside data) -> schema 2. A legacy error is free text with no kind, so it is dropped (the
        next read sets a real one); an entry whose name matches no folder is dropped and logged."""
        by_name = _short_names(folders)
        live = Live()
        for name, v in raw.items():
            key = by_name.get(name)
            if key is None:
                self.io.log("warn", "live.json migration: no config folder matches a legacy entry; dropped",
                            entry=name)
                continue
            try:
                acc = _legacy_entry(v)
            except (_BadShape, ValueError) as e:
                self.io.log("warn", "live.json migration: unreadable legacy entry; dropped", entry=name,
                            reason=str(e))
                continue
            if acc.data is not None:
                try:
                    self.io.check_reply(acc.data)
                except ReplyChanged:
                    # 0.1.x stored any reply it got; one 0.2.0 refuses would crash every later parse
                    self.io.log("warn", "live.json migration: stored reply no longer parses; kept without data",
                                entry=name)
                    acc = replace(acc, data=None)
            live.put(key, acc)
        return live


METER_LOG_KEEP_DAYS = 35      # read back: the weekly period (session share) and the newest service row


def ts_floor(at: datetime) -> str:
    """A text lower bound for meter_log `ts`, safe to compare in SQL. `ts` keeps the offset it was
    written with, so text order is local wall-clock order; a bound one day earlier than `at` (in
    UTC, no offset) is below every row at or after `at`, whatever its offset. Callers filter the
    exact instant in Python; this only keeps SQL from handing back the whole history."""
    return (at.astimezone(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")


def log_meters(conn: sqlite3.Connection, key: str, reading: Reading) -> None:
    """One meter_log row per meter of a successful reading (R-35). resets_at is rounded to the
    minute: the service's reset time drifts sub-second, and readings of one period must match.
    `ts` is whole seconds, so a status-line capture and a service read can share one: the service
    row is kept either way (the per-model age, RULING 29, is measured from it). Rows older than
    METER_LOG_KEEP_DAYS are pruned here, so the table never grows with history."""
    ts = iso(reading.fetched)
    conn.executemany(
        "INSERT INTO meter_log (ts, account, meter, pct, resets_at, source) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (ts, account, meter) DO UPDATE SET pct = excluded.pct, resets_at = excluded.resets_at, "
        "source = excluded.source WHERE excluded.source = 'service'",
        [(ts, key, m.id, float(m.pct), iso(_round_minute(m.resets_at)) if m.resets_at else None, reading.source)
         for m in reading.meters])
    conn.execute("DELETE FROM meter_log WHERE ts < ?",
                 (ts_floor(reading.fetched - timedelta(days=METER_LOG_KEEP_DAYS)),))
    conn.commit()


# ------------------------------------------------------------------ shape

def _round_minute(d: datetime) -> datetime:
    return (d + timedelta(seconds=30)).replace(second=0, microsecond=0)


def _check_schema(n) -> None:
    if type(n) is not int or n != LIVE_SCHEMA:
        raise BringUpRefused(f"jusage: {FILE_NAME}: schema {n!r} is not {LIVE_SCHEMA}; "
                             f"this file is from another jusage version")


def _check_key(key) -> None:
    if not isinstance(key, str) or not os.path.isabs(key):
        raise _BadShape(f"account key {key!r} is not an absolute config folder path")


def _time(v) -> Optional[datetime]:
    return None if v is None else parse_iso(v)


def _t(v) -> Optional[str]:
    return None if v is None else iso(v)


def _acc_to_json(a: AccountLive) -> dict:
    d = asdict(a)
    d["fetched"], d["blocked_until"], d["last_manual"] = _t(a.fetched), _t(a.blocked_until), _t(a.last_manual)
    d["error"] = None if a.error is None else {"kind": a.error.kind, "text": a.error.text, "at": iso(a.error.at)}
    if a.error is not None and a.error.other is not None:
        d["error"]["other"] = a.error.other
    return d


def _from_json(raw: dict) -> Live:
    if set(raw) != {"schema", "accounts"}:
        raise _BadShape(f"top-level keys {sorted(raw)}")
    if not isinstance(raw["accounts"], dict):
        raise _BadShape("accounts is not an object")
    live = Live()
    for key, v in raw["accounts"].items():
        live.put(key, _acc_from_json(v))
    return live


_FIELDS = tuple(f.name for f in fields(AccountLive))


def _acc_from_json(v) -> AccountLive:
    if not isinstance(v, dict) or set(v) != set(_FIELDS):
        raise _BadShape(f"account fields {sorted(v) if isinstance(v, dict) else type(v).__name__}")
    _expect(v["data"], (dict, type(None)), "data")
    _expect(v["warning"], (str, type(None)), "warning")
    _expect(v["identity_uuid"], (str, type(None)), "identity_uuid")
    if v["source"] not in SOURCES:
        raise _BadShape(f"source {v['source']!r}")
    if type(v["backoff_step"]) is not int or v["backoff_step"] < 0:
        raise _BadShape(f"backoff_step {v['backoff_step']!r}")
    if type(v["retry_after_explicit"]) is not bool:
        raise _BadShape("retry_after_explicit is not a bool")
    anchor = v["reset_anchor"]
    if not isinstance(anchor, dict) or set(anchor) != set(ANCHOR_KEYS):
        raise _BadShape("reset_anchor fields")
    for k in ANCHOR_KEYS:
        if not (anchor[k] is None and k in ("minute_of_week", "candidate")) and type(anchor[k]) is not int:
            raise _BadShape(f"reset_anchor.{k} {anchor[k]!r}")
    err = v["error"]
    if err is not None:
        if not isinstance(err, dict) or set(err) - {"other"} != {"kind", "text", "at"} or not isinstance(err["text"], str):
            raise _BadShape("error fields")
        try:
            err = ErrorInfo(err["kind"], err["text"], parse_iso(err["at"]), err.get("other"))
        except ValueError as e:
            raise _BadShape(f"error: {e}") from None
    return AccountLive(fetched=_time(v["fetched"]), data=v["data"], error=err, warning=v["warning"],
                       source=v["source"], blocked_until=_time(v["blocked_until"]),
                       backoff_step=v["backoff_step"], retry_after_explicit=v["retry_after_explicit"],
                       last_manual=_time(v["last_manual"]), reset_anchor=dict(anchor),
                       identity_uuid=v["identity_uuid"])


def _expect(v, types, name):
    if not isinstance(v, types):
        raise _BadShape(f"{name} has type {type(v).__name__}")


def _short_names(folders: Sequence[str]) -> Dict[str, str]:
    """The names 0.1.x stored entries under: the basename, or `parent/basename` when two folders
    share a basename. Both spellings map to the folder whenever they are unambiguous."""
    out: Dict[str, str] = {}
    base_count: Dict[str, int] = {}
    for f in folders:
        base_count[Path(f).name] = base_count.get(Path(f).name, 0) + 1
    for f in folders:
        p = Path(f)
        out[f"{p.parent.name}/{p.name}"] = f
        if base_count[p.name] == 1:
            out[p.name] = f
    return out


def _legacy_entry(v) -> AccountLive:
    if not isinstance(v, dict):
        raise _BadShape("legacy entry is not an object")
    data = v.get("data")
    if isinstance(data, dict) and ("error" in data or "stale" in data):
        data = None if "error" in data else {k: x for k, x in data.items() if k not in ("stale", "error_now")}
    _expect(data, (dict, type(None)), "data")
    warning = v.get("warning")
    _expect(warning, (str, type(None)), "warning")
    return AccountLive(fetched=_time(v.get("fetched")), data=data, warning=warning)
