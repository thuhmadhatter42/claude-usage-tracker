"""E21: the feed. One function turns one run's state into feed schema 2 (SCORE §1.7,
docs/feed-schema.md): the JSON the app decodes and the dashboard's per-account block shape.

Owners it reads from, never re-implements: meters and flags from E11 `usage_client.parse`, the
colour/forecast/runs-out from E17 `pace`, session share from E18, totals/windows/months/rankings
from E9, names from E19, account numbers and identities from E5, the gate's state from E14's
`RefreshResult`. The two wave-2 neighbours arrive as views handed in by the caller (E24):

    statusline_view(block, others, now) -> StatuslineView   E15: the accepted backup reading (only
        while the service read fails or is gated), the meter ids where a fresh capture disagrees
        with a fresh service reading, and the capture time.
    split_view(meter, account_uuid, now) -> SplitView       E20: who spent what in the meter's period.

The engine never filters for display: every account is in the feed, hidden or not.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence

from ju import accounts, config, formats, fsutil, ledger_query, pace as pace_mod, session_share as share_mod, timeutil, usage_client
from ju.constants import FEED_SCHEMA, RED_AT, SESSION_S, STALE_AFTER_S, WARMUP_E, WEEK_S
from ju.contracts import Meter, Reading
from ju.gate import AccountBlock, RefreshResult
from ju.live_store import AccountLive, ts_floor

STATUSLINE_METERS = ("session", "weekly")      # R-27: the status line has no per-model window
TOP_PROJECTS = 8
E_DIGITS = 4
LANDING_DIGITS = 1


@dataclass(frozen=True)
class StatuslineView:
    reading: Optional[Reading]          # an accepted backup reading (session + weekly), or None
    reason: Optional[str]               # why a capture was not used (logged), None when used or absent
    disagree: FrozenSet[str]            # meter ids where both sources are fresh and differ (R-29)
    capture_at: Optional[datetime]      # the newest capture of the account's folders


@dataclass(frozen=True)
class SplitView:
    split: Optional[dict]               # §1.7 `split` ({"period_start", "parts"}) or None
    missing: List[str]                  # people sharing the account whose report cannot be split (R-122)


NO_CAPTURE = StatuslineView(None, None, frozenset(), None)
NO_SPLIT = SplitView(None, [])

StatuslineFn = Callable[[AccountBlock, Dict[str, int], datetime], StatuslineView]
SplitFn = Callable[[Meter, Optional[str], datetime], SplitView]


# ---------------------------------------------------------------- E15's capture files (read only)

def capture_path(state, folder_key: str) -> Path:
    """`<state>/statusline/<sha8(folder key)>.json` (SCORE §1.6), written only by E15's capture."""
    return Path(state) / "statusline" / (hashlib.sha256(folder_key.encode("utf-8")).hexdigest()[:8] + ".json")


def read_capture(state, folder_key: str) -> Optional[dict]:
    """The folder's last capture, or None when the status line never captured for it. A file that
    will not parse raises fsutil.CorruptFile (the caller logs it; the capture is not used)."""
    try:
        return fsutil.read_json(capture_path(state, folder_key))
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------- small pieces

def _iso(d: Optional[datetime]) -> Optional[str]:
    return None if d is None else timeutil.iso(d)


def _usd(x: float) -> float:
    return round(x, 2)


def pace_block(p) -> dict:
    return {"e": round(p.e, E_DIGITS),
            "landing": None if p.landing is None else round(p.landing, LANDING_DIGITS),
            "state": p.state, "forecast": p.forecast, "runs_out": _iso(p.runs_out),
            "reset_due": p.reset_due, "words": p.words}


FOREIGN_RESET_TEXT = "This reading matches {other}'s reset"


def _error_block(acc: AccountLive, label: Callable[[str], str]) -> Optional[dict]:
    """The account's error; a foreign reset names the other account by its label (R-14)."""
    if acc.error is None:
        return None
    text = acc.error.text
    if acc.error.kind == "identity_foreign_reset" and acc.error.other is not None:
        text = FOREIGN_RESET_TEXT.format(other=label(acc.error.other))
    return {"kind": acc.error.kind, "text": text, "until": _iso(acc.blocked_until)}


def _label(cfg, all_keys: Sequence[str]) -> Callable[[str], str]:
    """An account's on-screen label: its email when its identity is known, else its folder name."""
    def label(key: str) -> str:
        ident = accounts.read_identity(key, cfg)
        return ident.email if ident is not None and ident.email else accounts.short_name(key, all_keys)
    return label


def _others(refresh: RefreshResult, block: AccountBlock) -> Dict[str, int]:
    """Every other account's learned weekly-reset minute (E12 anchors), for the identity check."""
    out = {}
    for b in refresh.accounts:
        if b is block:
            continue
        minute = b.state.reset_anchor.get("minute_of_week")
        if minute is not None:
            out[b.key] = minute
    return out


def _identity(cfg, members: Sequence[str]):
    for k in members:
        ident = accounts.read_identity(k, cfg)
        if ident is not None:
            return ident
    return None


# ---------------------------------------------------------------- meters

_KINDS = {"session": "session", "weekly": "weekly_all"}


def with_capture(acc: AccountLive, reading: Reading) -> AccountLive:
    """RULING 28: an accepted status-line reading as the account's stored reading. `data` keeps
    the usage-service reply shape (so E11 parses it and E14's due() sees it): session and weekly
    from the capture (a window it lacks keeps the stored meter), the per-model weeklies and the credits flag carried from the last service
    reply (their age stays in meter_log, which the feed reads)."""
    old = usage_client.parse(acc.data).meters if acc.data is not None else []
    taken = {m.id: m for m in reading.meters if m.id in STATUSLINE_METERS}
    kept = {m.id: m for m in old}
    # a capture replaces only the windows it carries; one missing from it keeps the stored meter
    limits = [{"kind": _KINDS[mid], "percent": m.pct, "resets_at": _iso(m.resets_at)}
              for mid in STATUSLINE_METERS for m in [taken.get(mid) or kept.get(mid)] if m is not None]
    for m in old:
        if m.id not in STATUSLINE_METERS:
            limits.append({"kind": "weekly_scoped", "percent": m.pct, "resets_at": _iso(m.resets_at),
                           "scope": {"model": {"display_name": m.name.split(" · ", 1)[1]}}})
    data = {"limits": limits}
    if acc.data is not None and "extra_usage" in acc.data:
        data["extra_usage"] = acc.data["extra_usage"]
    return replace(acc, fetched=reading.fetched, data=data, error=None, source="statusline")


def _service_read_at(conn, members: Sequence[str]) -> Callable[[str], Optional[datetime]]:
    """The time of a meter's newest usage-service reading in meter_log (R-27: a per-model weekly
    keeps its own age while the status line feeds session and weekly)."""
    marks = ",".join("?" * len(members))

    def read_at(meter_id: str) -> Optional[datetime]:
        # the newest by insertion, read backward: ts text carries different offsets (gate._service_at)
        row = conn.execute(
            f"SELECT ts FROM meter_log WHERE account IN ({marks}) AND meter = ? AND source = 'service' "
            "ORDER BY rowid DESC LIMIT 1", [*members, meter_id]).fetchone()
        return timeutil.parse_iso(row["ts"]) if row else None
    return read_at


def _meters(acc: AccountLive, view: StatuslineView, now: datetime, uuid: Optional[str],
            split_view: SplitFn, service_read_at: Callable[[str], Optional[datetime]]):
    """(meter blocks, flags, no_session_meter, missing people). Service meters in E11's order; while
    the service read is failing, an accepted status-line reading replaces session and weekly. A
    reading stored from the status line (RULING 28) marks session and weekly `statusline`; its
    per-model weeklies keep their service age."""
    service = usage_client.parse(acc.data) if acc.data is not None else None
    failing = acc.error is not None
    backup = {}
    if failing and view.reading is not None:
        backup = {m.id: m for m in view.reading.meters if m.id in STATUSLINE_METERS}
    if service is None:
        order = {mid: i for i, mid in enumerate(STATUSLINE_METERS)}
        chosen = [(m, "statusline", view.reading.fetched) for m in sorted(backup.values(), key=lambda m: order[m.id])]
    else:
        chosen = []
        for m in service.meters:
            if m.id in backup:
                chosen.append((backup[m.id], "statusline", view.reading.fetched))
            elif acc.source == "statusline" and m.id in STATUSLINE_METERS:
                chosen.append((m, "last_good" if failing else "statusline", acc.fetched))
            elif acc.source == "statusline":
                chosen.append((m, "last_good" if failing else "service", service_read_at(m.id) or acc.fetched))
            else:
                chosen.append((m, "last_good" if failing else "service", acc.fetched))
    blocks, missing = [], []
    for m, source, read_at in chosen:
        sv = split_view(m, uuid, now)
        for person in sv.missing:
            if person not in missing:
                missing.append(person)
        p = pace_mod.pace(m, now, read_at=read_at)
        blocks.append({
            "id": m.id, "name": m.name, "pct": float(m.pct), "resets_at": _iso(m.resets_at),
            "read_at": _iso(read_at), "source": source, "period_s": m.period_s,
            "disagree": source == "service" and m.id in view.disagree,
            "pace": pace_block(p), "split": sv.split})
    if service is not None:
        flags, no_session = list(service.flags), service.no_session_meter
    else:
        flags, no_session = [], all(b["id"] != "session" for b in blocks)
    return blocks, flags, no_session, missing


# ---------------------------------------------------------------- session share

def _session_share(conn, members: Sequence[str], meters: List[dict], no_session: bool, now: datetime):
    """R-87: per 5-hour session, the weekly % gained inside it. Sessions are the service's session
    windows (from logged session resets) or, without a session meter, E9's 5-hour blocks."""
    weekly = next((m for m in meters if m["id"] == "weekly"), None)
    if weekly is None or weekly["resets_at"] is None:
        return None
    period_end = timeutil.parse_iso(weekly["resets_at"])
    period_start = period_end - timedelta(seconds=WEEK_S)
    marks = ",".join("?" * len(members))
    rows = [r for r in conn.execute(
        f"SELECT ts, meter, pct, resets_at FROM meter_log WHERE ts >= ? AND account IN ({marks}) "
        f"AND meter IN ('session', 'weekly')", [ts_floor(period_start), *members]).fetchall()
            if period_start <= timeutil.parse_iso(r["ts"]) <= now]
    if no_session:
        since = period_start.astimezone().date().isoformat()
        ledger = sorted((r for k in members for r in ledger_query.rows(conn, since, k)),
                        key=lambda r: timeutil.parse_iso(r["ts"]))
        windows = [share_mod.Window(b.start, b.end, now < b.end) for b in ledger_query.blocks(ledger)
                   if b.end > period_start]
    else:
        resets = sorted({timeutil.parse_iso(r["resets_at"]) for r in rows
                         if r["meter"] == "session" and r["resets_at"] is not None})
        windows = [share_mod.Window(r - timedelta(seconds=SESSION_S), r, now < r) for r in resets]
    result = share_mod.session_share(rows, windows, period_end)
    return None if result is None else result.to_feed()


# ---------------------------------------------------------------- accounts

def views(refresh: RefreshResult, statusline_view: StatuslineFn, now: datetime) -> List[StatuslineView]:
    """E15's view of each account block, asked once per run, in `refresh.accounts` order."""
    return [statusline_view(b, _others(refresh, b), now) for b in refresh.accounts]


def account_blocks(*, cfg, refresh: RefreshResult, conn, now: datetime,
                   views: List[StatuslineView], split_view: SplitFn) -> List[dict]:
    """The `accounts` list: one block per claude.ai account (folders sharing a uuid are one),
    keyed by its lowest-numbered folder, sorted by number. `views` = views(...) of this refresh."""
    out = []
    label = _label(cfg, [k for b in refresh.accounts for k in b.folders])
    for block, view in zip(refresh.accounts, views):
        members = accounts.ordered(block.folders, cfg)
        key = members[0]
        acc = block.state
        ident = _identity(cfg, members)
        uuid = block.uuid if block.uuid is not None else (ident.uuid if ident else None)
        meters, flags, no_session, missing = _meters(acc, view, now, uuid, split_view,
                                                     _service_read_at(conn, members))
        weekly = next((m for m in meters if m["id"] == "weekly"), None)
        out.append({
            "key": key, "number": accounts.number_for(cfg, key),
            "email": ident.email if ident else None, "uuid": uuid, "folders": list(members),
            "weekly_reset": weekly["resets_at"] if weekly else None, "flags": flags,
            "warning": acc.warning, "error": _error_block(acc, label), "no_session_meter": no_session,
            "meters": meters,
            "session_share": _session_share(conn, members, meters, no_session, now),
            "missing_people": missing})
    out.sort(key=lambda a: a["number"])
    return out


def statusline_block(cfg, refresh: RefreshResult, views: List[StatuslineView]) -> dict:
    """{folder key: {enabled, capture_at}} for every folder: enabled is E15's record in config.json."""
    out = {}
    for block, view in zip(refresh.accounts, views):
        for k in block.folders:
            entry = cfg.statusline.get(k) or {}
            out[k] = {"enabled": bool(entry.get("enabled", False)), "capture_at": _iso(view.capture_at)}
    return out


# ---------------------------------------------------------------- ledger sections

def _totals(t: ledger_query.Totals, calls: bool) -> dict:
    d = {"tokens": t.tokens, "usd": _usd(t.cost)}
    if calls:
        d["calls"] = t.calls
    d["unpriced"] = t.unpriced
    return d


def _models(ranked) -> List[dict]:
    return [{"id": r.name, "name": formats.model_name(r.name), "tokens": r.totals.tokens, "usd": _usd(r.totals.cost)}
            for r in ranked]


def _projects(ranked) -> List[dict]:
    total = sum(r.totals.tokens for r in ranked)
    return [{"name": r.name, "share": round(r.totals.tokens / total, 4) if total else 0.0, "tokens": r.totals.tokens}
            for r in ranked[:TOP_PROJECTS]]


def _months(conn, pricing) -> List[dict]:
    out = []
    for m in ledger_query.months(conn, pricing, 3):
        t = m.totals
        out.append({"key": m.key, "label": m.label, "tokens": t.tokens, "usd": _usd(t.cost), "calls": t.calls,
                    "types": {"input": t.input, "cache_w": t.cache_5m + t.cache_1h,
                              "cache_read": t.cache_read, "output": t.output},
                    "models": _models(m.models), "projects": _projects(m.projects)})
    return out


def _windows(conn, pricing, now) -> List[dict]:
    return [{"account": w.account, "start": _iso(w.start), "end": _iso(w.end), "tokens": w.totals.tokens,
             "usd": _usd(w.totals.cost), "open": w.open} for w in ledger_query.windows(conn, pricing, now)]


# ---------------------------------------------------------------- the feed

def rules(cfg) -> dict:
    """The one copy of the display rules the app and the dashboard read (R-51)."""
    return {"stale_after_s": STALE_AFTER_S, "red_at": RED_AT, "warmup_e": WARMUP_E, "read_every_s": cfg.read_every_s}


def build_feed(*, cfg, refresh: RefreshResult, conn, pricing, now: datetime, version: str,
               statusline_view: StatuslineFn, split_view: SplitFn, people: List[dict], notes: List[str],
               share: dict, dashboard: Optional[str]) -> dict:
    """Feed schema 2, exactly. `people` are E20's peer blocks already in §1.7 shape; `share` is
    {"written_at", "error"}; `dashboard` the page path or None. Raises NaiveTime on any time
    without an offset (a bug, never papered over)."""
    timeutil.iso(now)
    seen = views(refresh, statusline_view, now)
    today = now.astimezone().date()
    day, week_from = today.isoformat(), (today - timedelta(days=6)).isoformat()
    return {
        "schema": FEED_SCHEMA, "version": version, "generated": _iso(now), "next_due": _iso(refresh.next_due),
        "rules": rules(cfg),
        "me": {"user": config.user_name(cfg), "install_id": cfg.install_id},
        "accounts": account_blocks(cfg=cfg, refresh=refresh, conn=conn, now=now,
                                   views=seen, split_view=split_view),
        "totals": {"as_of": _iso(now),
                   "today": _totals(ledger_query.totals(conn, pricing, day, day), calls=True),
                   "week": _totals(ledger_query.totals(conn, pricing, week_from, day), calls=False)},
        "windows": _windows(conn, pricing, now),
        "models": _models(ledger_query.top_models(conn, pricing, week_from, day, None)),
        "projects": _projects(ledger_query.top_projects(conn, pricing, week_from, day, None)),
        "projects_today": _projects(ledger_query.top_projects(conn, pricing, day, day, None)),
        "months": _months(conn, pricing),
        "people": list(people),
        "statusline": statusline_block(cfg, refresh, seen),
        "dashboard": dashboard,
        "share": {"written_at": share.get("written_at"), "error": share.get("error")},
        "notes": list(notes),
    }
