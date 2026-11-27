"""E14: the fetch gate. The only door to the usage service (R-16). Every caller (app feed, `jusage
live`, `update`, `share`, bare `jusage`) calls `refresh`, which holds an exclusive flock on
`<state>/fetch.lock`; a second caller waits, then re-reads live.json under the lock and finds the
first caller's readings fresh, so nothing is fetched twice.

Rules (R-17…R-20 with J's cadence amendment), all in the pure functions below:
  due        after `read_every_s` (15–600) since the last success; at once when a reset has passed
             since the reading was taken; never before `blocked_until`. Manual skips the cadence
             and a guessed block, never an explicit Retry-After, and is floored at 15 s.
  on_429     Retry-After > 0 → exactly that, capped at 3600 s (explicit); absent/0 → the ladder.
  on_failure every other failure → the ladder (guessed). A failure never brings the next try sooner.
  ladder     300, 600, 1200, 1800 s (cap), each +0–20 % jitter; success clears it.

Collaborators arrive in `GateDeps` (E10 login, E5 identity, E11 fetch/parse, E12 checks, E7 db,
E4 cadence, E2 log); the real ones are wired by the CLI entry (E24).
"""
from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .constants import (BACKOFF_S, JITTER, MANUAL_FLOOR_S, READ_EVERY_MAX, READ_EVERY_MIN, RETRY_AFTER_CAP_S,
                        SERVICE_REFRESH_S)
from .contracts import (ERROR_KINDS, GateDecision, JusageError, Login, LoginError, Meter, RateLimited, Reading)
from .live_store import AccountLive, ErrorInfo, Live, LiveStore, log_meters
from . import identity
from .timeutil import minute_of_week, parse_iso

LOCK_NAME = "fetch.lock"
LOCK_TIMEOUT_S = 120
LOCK_POLL_S = 0.05
STUCK = "another jusage refresh is stuck"
RATE_LIMITED_TEXT = "Anthropic is rate-limiting usage reads"
IDENTITY_CHANGED_TEXT = "Account changed mid-read; reading discarded"


@dataclass(frozen=True)
class Parsed:
    """What the gate needs from E11's ParsedReply: the meters and the flags."""
    meters: List[Meter]
    flags: List[str]


@dataclass(frozen=True)
class GateDeps:
    store: LiveStore
    find_login: Callable[[str], Login]                       # E10; raises LoginError
    read_uuid: Callable[[str], Optional[str]]                # E5 read_identity(...).uuid
    fetch: Callable[[str], dict]                             # E11; raises RateLimited/LoginError/HttpError/NetworkError
    parse: Callable[[dict], Parsed]                          # E11; raises ReplyChanged
    check_stable: Callable[[Optional[str], Optional[str]], None]          # E12; raises IdentityRejected
    observe: Callable[[dict, datetime, Dict[str, int]], Tuple[dict, str]]  # E12 Fingerprint.observe
    login_warning: Callable[[Login], Optional[str]]          # E10's words for a legacy login
    open_db: Callable[[Path], object]                        # E7 open_db -> sqlite3.Connection
    read_every_s: int                                        # E4 config read_every_s
    now: Callable[[], datetime]                              # timeutil.now
    rand: Callable[[], float]                                # random.random, in [0, 1)
    log: Callable[..., None]                                 # E2 houselog.log


@dataclass(frozen=True)
class AccountBlock:
    """One claude.ai account: folders sharing a uuid merged, the freshest reading kept (R-15)."""
    key: str                      # the member holding the group's state (see _current_key)
    folders: List[str]
    uuid: Optional[str]
    state: AccountLive


@dataclass(frozen=True)
class RefreshResult:
    live: Live
    accounts: List[AccountBlock]
    fetched: List[str]            # folders read from the service by this call
    next_due: datetime


# ------------------------------------------------------------------ pure rules

_CAPTURED = ("session", "weekly")      # the meters a status-line capture carries


def due(acc: AccountLive, read_every_s: int, now: datetime, manual: bool,
        meters: Sequence[Meter], service_at: Optional[datetime] = None) -> GateDecision:
    """May this folder be read now? `meters` = the parsed meters of `acc.data` (empty if none).
    next_at = the earliest moment it becomes due (None when due now). `service_at` = the newest
    usage-service reading in meter_log: a stored status-line reading (RULING 28) carrying per-model
    meters is due once that is SERVICE_REFRESH_S old (RULING 29), so those never grey."""
    if not READ_EVERY_MIN <= read_every_s <= READ_EVERY_MAX:
        raise ValueError(f"read_every_s {read_every_s} outside {READ_EVERY_MIN}-{READ_EVERY_MAX}")
    blocked = acc.blocked_until is not None and now < acc.blocked_until
    if blocked and (acc.retry_after_explicit or not manual):
        return GateDecision(False, "rate_limited" if acc.retry_after_explicit else "backoff", acc.blocked_until)
    if manual:
        last = max((t for t in (acc.fetched, acc.last_manual) if t is not None), default=None)
        if last is not None and (now - last).total_seconds() < MANUAL_FLOOR_S:
            return GateDecision(False, "manual_floor", last + timedelta(seconds=MANUAL_FLOOR_S))
        return GateDecision(True, "manual", None)
    if acc.fetched is None:
        return GateDecision(True, "never_read", None)
    resets = sorted(m.resets_at for m in meters if m.resets_at is not None and m.resets_at > acc.fetched)
    if resets and resets[0] <= now:
        return GateDecision(True, "reset_passed", None)
    cadence_at = acc.fetched + timedelta(seconds=read_every_s)
    if now >= cadence_at:
        return GateDecision(True, "cadence", None)
    service_due = []
    if acc.source == "statusline" and any(m.id not in _CAPTURED for m in meters):
        if service_at is None:
            return GateDecision(True, "per_model", None)
        service_due = [service_at + timedelta(seconds=SERVICE_REFRESH_S)]
        if now >= service_due[0]:
            return GateDecision(True, "per_model", None)
    nxt = min([cadence_at] + resets[:1] + service_due)
    if acc.blocked_until is not None:
        nxt = max(nxt, acc.blocked_until)
    return GateDecision(False, "cadence", nxt)


def on_success(acc: AccountLive, *, data: dict, uuid: Optional[str], warning: Optional[str], anchor: dict,
               now: datetime) -> AccountLive:
    return replace(acc, fetched=now, data=data, error=None, warning=warning, source="service",
                   blocked_until=None, backoff_step=0, retry_after_explicit=False, reset_anchor=anchor,
                   identity_uuid=uuid)


def on_429(acc: AccountLive, retry_after: Optional[int], now: datetime, rand: Callable[[], float]) -> AccountLive:
    err = ErrorInfo("rate_limited", RATE_LIMITED_TEXT, now)
    if retry_after:
        until = now + timedelta(seconds=min(int(retry_after), RETRY_AFTER_CAP_S))
        return _block(acc, err, until, explicit=True, step=acc.backoff_step)
    return _ladder(acc, err, now, rand)


def on_failure(acc: AccountLive, kind: str, text: str, now: datetime, rand: Callable[[], float],
               other: Optional[str] = None) -> AccountLive:
    if kind not in ERROR_KINDS or kind == "rate_limited":
        raise ValueError(f"on_failure takes a non-429 error kind, got {kind!r}")
    return _ladder(acc, ErrorInfo(kind, text, now, other), now, rand)


def _ladder(acc: AccountLive, err: ErrorInfo, now: datetime, rand: Callable[[], float]) -> AccountLive:
    base = BACKOFF_S[min(acc.backoff_step, len(BACKOFF_S) - 1)]
    until = now + timedelta(seconds=base * (1 + JITTER * rand()))
    return _block(acc, err, until, explicit=False, step=acc.backoff_step + 1)


def _block(acc: AccountLive, err: ErrorInfo, until: datetime, *, explicit: bool, step: int) -> AccountLive:
    """A new block never ends before a current one, and an explicit Retry-After still running stays
    explicit (a failure never makes the next attempt sooner, R-18)."""
    if acc.blocked_until is not None and acc.blocked_until > until:
        until, explicit = acc.blocked_until, acc.retry_after_explicit or explicit
    return replace(acc, error=err, blocked_until=until, retry_after_explicit=explicit, backoff_step=step)


# ------------------------------------------------------------------ refresh

def refresh(state: Path, accounts: Sequence[str], *, manual: bool, deps: GateDeps) -> RefreshResult:
    """Read every due account through the lock and store the outcome. `accounts` = config folder
    keys in display order (E5). One account's failure is stored as its error, never raised."""
    fd = fetch_lock(Path(state))
    try:
        return _refresh_locked(Path(state), list(accounts), manual, deps)
    finally:
        os.close(fd)              # closing the descriptor releases the flock


def fetch_lock(state: Path) -> int:
    fd = os.open(state / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise RuntimeError(STUCK) from None
            time.sleep(LOCK_POLL_S)


def _refresh_locked(state: Path, accounts: List[str], manual: bool, deps: GateDeps) -> RefreshResult:
    live = deps.store.load(state, accounts)
    groups = _group(accounts, {k: deps.read_uuid(k) for k in accounts})
    fetched: List[str] = []
    changed = False
    conn = None
    try:
        for _, members, _ in groups:
            cur = _current_key(live, members)
            acc = live.account(cur)
            now = deps.now()
            if conn is None and acc.source == "statusline":
                conn = deps.open_db(state)
            if not due(acc, deps.read_every_s, now, manual, _meters(acc, deps), _service_at(conn, members)).due:
                continue
            others = {}
            for _, other, _ in groups:
                if other is not members:
                    ok = _current_key(live, other)
                    if live.account(ok).reset_anchor["minute_of_week"] is not None:
                        others[ok] = live.account(ok).reset_anchor["minute_of_week"]
            if manual:
                acc = replace(acc, last_manual=now)
            reader, acc, reading = _read_group(members, acc, others, now, deps)
            key = reader if reader is not None else cur
            live.put(key, acc)
            changed = True                      # a failure with no reader is saved too: its ladder grows
            if reader is not None:
                fetched.append(reader)
            if reading is not None:
                if conn is None:
                    conn = deps.open_db(state)
                log_meters(conn, reader, reading)
        if changed:
            deps.store.save(state, live)
    finally:
        if conn is not None:
            conn.close()
    blocks = []
    for _, members, uuid in groups:
        cur = _current_key(live, members)
        blocks.append(AccountBlock(cur, members, uuid, live.account(cur)))
    return RefreshResult(live=live, accounts=blocks, fetched=fetched,
                         next_due=_next_due(state, live, groups, deps))


def _read_group(members: List[str], acc: AccountLive, others: Dict[str, int], now: datetime,
                deps: GateDeps) -> Tuple[Optional[str], AccountLive, Optional[Reading]]:
    """The group's reader is the first member, in order, whose find_login succeeds; then uuid
    before → fetch → parse → uuid after → fingerprint. Returns (reader or None when no member has
    a login, the updated entry, the accepted reading or None). No login anywhere → the first
    member's LoginError is the group's error."""
    first_err: Optional[LoginError] = None
    folder = login = None
    for m in members:
        try:
            login = deps.find_login(m)
        except LoginError as e:
            first_err = first_err or e
            continue
        folder = m
        break
    if folder is None:
        deps.log("warn", "no member of the account has a usable login", folders=members, kind=first_err.kind)
        return None, on_failure(acc, first_err.kind, first_err.text, now, deps.rand), None
    reader = folder
    try:
        before = deps.read_uuid(folder)
        reply = deps.fetch(login.token)
        parsed = deps.parse(reply)
        after = deps.read_uuid(folder)
        deps.check_stable(before, after)
        anchor = acc.reset_anchor
        weekly = next((m.resets_at for m in parsed.meters if m.id == "weekly" and m.resets_at), None)
        if weekly is not None:
            anchor, verdict = deps.observe(acc.reset_anchor, weekly, others)
            if verdict == "reject_foreign":
                deps.log("warn", "reading rejected: weekly reset matches another account", folder=folder,
                         minute_of_week=minute_of_week(weekly))
                return reader, on_failure(replace(acc, reset_anchor=anchor), "identity_foreign_reset",
                                          "This reading matches another account's reset", now, deps.rand,
                                          other=identity.foreign_match(weekly, others)), None
            if verdict != "accept":
                raise ValueError(f"fingerprint verdict {verdict!r}")
    except RateLimited as e:
        deps.log("warn", "usage read rate-limited", folder=folder, retry_after=e.retry_after_s)
        return reader, on_429(acc, e.retry_after_s, now, deps.rand), None
    except JusageError as e:
        deps.log("warn", "usage read failed", folder=folder, kind=e.kind)
        text = IDENTITY_CHANGED_TEXT if e.kind == "identity_changed" else getattr(e, "text", None) or str(e)
        return reader, on_failure(acc, e.kind, text, now, deps.rand), None
    new = on_success(acc, data=reply, uuid=after, warning=deps.login_warning(login), anchor=anchor, now=now)
    return reader, new, Reading(folder=folder, fetched=now, meters=list(parsed.meters), flags=list(parsed.flags),
                        uuid=after, source="service")


def _group(accounts: List[str], uuids: Dict[str, Optional[str]]) -> List[Tuple[str, List[str], Optional[str]]]:
    """Folders sharing a uuid → one group, members in the caller's order; no uuid → its own group."""
    groups: List[Tuple[str, List[str], Optional[str]]] = []
    by_uuid: Dict[str, List[str]] = {}
    for k in accounts:
        u = uuids[k]
        if u is None:
            groups.append((k, [k], None))
        elif u in by_uuid:
            by_uuid[u].append(k)
        else:
            by_uuid[u] = [k]
            groups.append((k, by_uuid[u], u))
    return groups


def _current_key(live: Live, members: List[str]) -> str:
    """The member holding the group's gate state: the one the gate touched last (latest success or
    failure). A success is stored on the reader and a failure on the current member, so this is
    also the member with the freshest reading. Never touched → the first member."""
    def touched(k):
        a = live.account(k)
        return max((t for t in (a.fetched, a.error.at if a.error else None) if t is not None), default=None)
    seen = [(touched(k), i, k) for i, k in enumerate(members) if touched(k) is not None]
    return max(seen, key=lambda x: (x[0], -x[1]))[2] if seen else members[0]


def _meters(acc: AccountLive, deps: GateDeps) -> List[Meter]:
    """The stored reply was accepted by parse when it was fetched; it parses the same way now."""
    return list(deps.parse(acc.data).meters) if acc.data is not None else []


def _service_at(conn, members: List[str]) -> Optional[datetime]:
    """The newest usage-service reading of any member in meter_log (None without a connection: only a
    status-line reading needs it, and only then is the database opened)."""
    if conn is None:
        return None
    marks = ",".join("?" * len(members))
    # the newest row by insertion (ts strings carry different offsets, so MAX(ts) would compare text)
    row = conn.execute(f"SELECT ts FROM meter_log WHERE source = 'service' AND account IN ({marks}) "
                       "ORDER BY rowid DESC LIMIT 1", list(members)).fetchone()
    return parse_iso(row[0]) if row else None


def _next_due(state: Path, live: Live, groups, deps: GateDeps) -> datetime:
    now = deps.now()
    if not groups:
        return now + timedelta(seconds=deps.read_every_s)
    times = []
    conn = None
    try:
        for _, members, _ in groups:
            acc = live.account(_current_key(live, members))
            if conn is None and acc.source == "statusline":
                conn = deps.open_db(state)
            d = due(acc, deps.read_every_s, now, False, _meters(acc, deps), _service_at(conn, members))
            times.append(now if d.due else d.next_at)
    finally:
        if conn is not None:
            conn.close()
    return min(times)
