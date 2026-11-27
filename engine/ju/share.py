"""E20: sharing. Each install writes one report (`report_version: 3`) into a synced folder and reads
everyone else's. From those reports this module decides who is one person, how old their numbers
are, and who spent what inside a meter's period.

The split is a share of API-rate spend, never a share of the percentage (R-117, R-110): Anthropic
meters an account, not a person, and nobody outside Anthropic knows how its percentage is weighted.
An open 5-hour window is prorated by the time it has run (its cost is what it has spent so far, so
dividing by a full five hours would discount it a second time). Every time from another Mac is
compared as an instant, never as text (R-116). Version numbers compare as numbers (R-118).

A report that cannot be trusted is skipped and named in `notes`; nothing here deletes a file that
belongs to anyone else, and a missing shared folder is an error (R-119)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ju import accounts as accounts_mod
from ju import fsutil, ledger_query, timeutil
from ju.config import Config, ConfigError, install_id as config_install_id
from ju.constants import COLS, REPO_ROOT, REPORT_VERSION, SHARE_MIN_INTERVAL_S, STALE_AFTER_S
from ju.timeutil import parse_iso

DAYS_KEPT = 90
BLOCKS_KEPT = 50
SPLITTABLE_FROM = (0, 1, 22)         # first version that wrote account ids (R-122)
PROJECTS_PER_DAY = 10
WEEK_DAYS = 7
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_REQUIRED = ("user", "host", "install_id", "tool_version", "generated", "accounts", "live", "days",
             "blocks", "session_share")


class _Bad(ValueError):
    """A report that fails validation; the text names the field."""


# ---------------------------------------------------------------- versions

def version_tuple(s) -> Tuple[int, ...]:
    """'0.1.100' -> (0, 1, 100). Numbers, never text: "0.1.100" < "0.1.22" as a string and is
    newer as a version. Anything that is not dot-separated whole numbers is a ValueError."""
    if not isinstance(s, str) or not re.fullmatch(r"[0-9]+(\.[0-9]+)+", s):
        raise ValueError(f"not a version number: {s!r}")
    return tuple(int(p) for p in s.split("."))


# ---------------------------------------------------------------- identity of this install's report

def report_id(cfg: Config) -> Tuple[str, str, str]:
    """(your name, this Mac's short host name, the file name). One function, so the writer and the
    feed's `share` block can never disagree about the name."""
    user = cfg.user or os.environ.get("USER")
    if not user:
        raise ConfigError("No user name is set; run `config user <name>`")
    host = socket.gethostname().split(".")[0]
    if not host:
        raise ConfigError("This Mac has no host name")
    for label, v in (("user name", user), ("host name", host)):
        if "/" in v or "\x00" in v or v.startswith("."):
            raise ConfigError(f"The {label} {v!r} cannot be part of a file name")
    return user, host, f"{user}@{host}-{config_install_id(cfg)}.json"


def _tool_version() -> str:
    text = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    version_tuple(text)
    return text


def _hash8(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- building my report

def _entry(t: ledger_query.Totals) -> dict:
    return {**{c: getattr(t, c) for c in COLS}, "calls": t.calls, "usd": round(t.cost, 2), "unpriced": t.unpriced}


def _group(rows_: Iterable, key) -> Dict[str, list]:
    out: Dict[str, list] = {}
    for r in rows_:
        out.setdefault(key(r), []).append(r)
    return out


def build_report(conn, cfg: Config, live: dict, pricing, projects: bool, *,
                 session_share: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """This install's report (v3). `live` is `{account uuid: account block}` exactly as the feed
    carries it; `session_share` is `{uuid: SessionShare.to_feed()}`. Project names are included only
    when `projects` is true (R-120). Per-account parts are keyed by account uuid; a folder whose
    identity is unknown has no uuid, so it counts in the day totals and nowhere else."""
    now = timeutil.now() if now is None else now
    generated = timeutil.iso(now)                                   # refuses a time with no offset
    if not isinstance(live, dict) or not all(isinstance(v, dict) for v in live.values()):
        raise ValueError("live must be an object of account blocks keyed by account uuid")
    shares = {} if session_share is None else session_share
    if not isinstance(shares, dict):
        raise ValueError("session_share must be an object keyed by account uuid")
    user, host, _ = report_id(cfg)
    install = config_install_id(cfg)

    idents = {k: v for k, v in cfg.identities.items()
              if isinstance(v, dict) and isinstance(v.get("uuid"), str) and isinstance(v.get("email"), str)}
    uuid_of = {k: v["uuid"] for k, v in idents.items()}
    accts = sorted(({"key_hash": _hash8(k), "number": accounts_mod.number_for(cfg, k),
                     "email": v["email"], "uuid": v["uuid"]} for k, v in idents.items()),
                   key=lambda a: (a["number"], a["key_hash"]))

    since = (now.astimezone().date() - timedelta(days=DAYS_KEPT - 1)).isoformat()
    rows_ = ledger_query.rows(conn, since)

    days = {}
    for day, drows in sorted(_group(rows_, lambda r: r["day"]).items()):
        d = _entry(ledger_query.sum_rows(drows, pricing))
        models = {}
        for m, mrows in sorted(_group(drows, lambda r: r["model"]).items()):
            t = ledger_query.sum_rows(mrows, pricing)
            models[m] = {**_entry(t), "tokens": t.tokens}
        per_acct = {}
        for acct, arows in sorted(_group(drows, lambda r: r["account"]).items()):
            u = uuid_of.get(acct)
            if u is None:
                continue
            t = ledger_query.sum_rows(arows, pricing)
            prev = per_acct.setdefault(u, {"tokens": 0, "usd": 0.0})
            prev["tokens"] += t.tokens
            prev["usd"] = round(prev["usd"] + t.cost, 2)
        proj = {}
        if projects:
            ranked = sorted(((name, ledger_query.sum_rows(prows, pricing))
                             for name, prows in _group(drows, lambda r: ledger_query.project_group(r["project"])).items()),
                            key=lambda kv: (-kv[1].tokens, kv[0]))[:PROJECTS_PER_DAY]
            proj = {n: {"tokens": t.tokens, "usd": round(t.cost, 2)} for n, t in ranked}
        days[day] = {**d, "models": models, "accounts": per_acct, "projects": proj}

    blocks: Dict[str, list] = {}
    for acct, bl in ledger_query.blocks_by_account(rows_).items():
        u = uuid_of.get(acct)
        if u is None:
            continue
        for b in bl:
            t = ledger_query.sum_rows(b.rows, pricing)
            blocks.setdefault(u, []).append({"start": timeutil.iso(b.start), "end": timeutil.iso(b.end),
                                             "tokens": t.tokens, "calls": t.calls, "usd": round(t.cost, 4),
                                             "unpriced": t.unpriced})
    for u in blocks:
        blocks[u] = sorted(blocks[u], key=lambda b: parse_iso(b["start"]))[-BLOCKS_KEPT:]

    report = {"report_version": REPORT_VERSION, "user": user, "host": host, "install_id": install,
              "tool_version": _tool_version(), "generated": generated, "accounts": accts,
              "live": json.loads(json.dumps(live)), "days": days, "blocks": blocks,
              "session_share": json.loads(json.dumps(shares))}
    _validate_v3(report)                                            # what I write is what I would accept
    return report


def write_report(directory, report: dict, cfg: Config) -> Tuple[Path, List[str]]:
    """Write this install's report into the shared folder (temp file + rename, so a sync client
    never sees half of it) and remove only this install's older names. Another person's file, and a
    file that will not parse, is never touched. Returns (path, names removed)."""
    directory = Path(directory)
    user, host, name = report_id(cfg)
    mine = config_install_id(cfg)
    try:
        _validate_v3(report)
    except _Bad as e:
        raise ValueError(f"refusing to write a bad report: {e}") from None
    if (report["user"], report["host"], report["install_id"]) != (user, host, mine):
        raise ValueError("refusing to write a report that is not this install's own")
    if not directory.is_dir():
        raise ConfigError(f"Shared folder {directory} is missing; pass --reports to create it")
    out = directory / name
    body = json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    fsutil.write_private(out, body.encode("utf-8"))
    removed = []
    pre_0121 = f"{user}@{host}.json"                                # the pre-0.1.21 name of this same Mac
    for p in sorted(directory.glob("*.json")):
        if p == out or ".sync-conflict" in p.name:
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        theirs = raw.get("install_id")
        if theirs == mine or (p.name == pre_0121 and theirs is None):
            p.unlink()
            removed.append(p.name)
    return out, removed


def should_rewrite(last_written: Optional[datetime], changed: bool, now: datetime) -> bool:
    """Rewrite my report when its data changed and at least 300 s have passed since the last write,
    so a running peer's numbers stay under 15 minutes old on other Macs (R-114)."""
    timeutil.iso(now)
    if not changed:
        return False
    if last_written is None:
        return True
    return (now - last_written).total_seconds() >= SHARE_MIN_INTERVAL_S


# ---------------------------------------------------------------- reading everyone's reports

@dataclass
class Report:
    user: str
    host: str
    install_id: Optional[str]            # None only for a pre-0.1.21 file
    tool_version: str
    generated: datetime
    accounts: List[dict]
    live: dict
    days: Dict[str, dict]
    blocks: Dict[str, List[dict]]
    session_share: dict
    legacy: bool                          # written before report_version 3: no account ids, never split
    name: str

    @property
    def key(self) -> str:
        return self.install_id or f"{self.user}@{self.host}"

    @property
    def uuids(self) -> frozenset:
        return frozenset(a["uuid"] for a in self.accounts)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _need_str(raw: dict, k: str) -> str:
    v = raw.get(k)
    if not isinstance(v, str) or not v.strip():
        raise _Bad(f"{k} must be text")
    return v


def _check_time(label: str, v) -> datetime:
    try:
        return parse_iso(v)
    except ValueError as e:
        raise _Bad(f"{label}: {e}") from None


def _check_day(day: str, d) -> None:
    if not _DAY.match(day) or not isinstance(d, dict):
        raise _Bad(f"days: {day!r} is not a day entry")
    for c in COLS + ("calls", "unpriced"):
        if not _is_int(d.get(c)):
            raise _Bad(f"days: {day} {c} must be a whole number")
    if not _is_num(d.get("usd")):
        raise _Bad(f"days: {day} usd must be a number")
    for k in ("models", "accounts", "projects"):
        if not isinstance(d.get(k), dict):
            raise _Bad(f"days: {day} {k} must be an object")


def _check_block(u: str, b) -> None:
    if not isinstance(b, dict):
        raise _Bad(f"blocks: {u} holds something that is not a window")
    start = _check_time("blocks start", b.get("start"))
    end = _check_time("blocks end", b.get("end"))
    if end <= start:
        raise _Bad("blocks: a window ends before it starts")
    if not _is_num(b.get("usd")):
        raise _Bad("blocks: usd must be a number")


def _validate_v3(raw) -> None:
    if not isinstance(raw, dict):
        raise _Bad("not a report object")
    v = raw.get("report_version")
    if not _is_int(v):
        raise _Bad("report_version must be a whole number")
    if v != REPORT_VERSION:
        raise _Bad(f"report_version {v} is {'newer' if v > REPORT_VERSION else 'older'} than this jusage reads ({REPORT_VERSION})")
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise _Bad(f"missing {', '.join(missing)}")
    for k in ("user", "host", "install_id"):
        _need_str(raw, k)
    try:
        version_tuple(raw["tool_version"])
    except ValueError as e:
        raise _Bad(f"tool_version: {e}") from None
    _check_time("generated", raw["generated"])
    if not isinstance(raw["accounts"], list):
        raise _Bad("accounts must be a list")
    for a in raw["accounts"]:
        if not (isinstance(a, dict) and all(isinstance(a.get(k), str) and a[k] for k in ("key_hash", "email", "uuid"))
                and _is_int(a.get("number"))):
            raise _Bad("accounts: each needs key_hash, number, email and uuid")
    if not isinstance(raw["live"], dict) or not all(isinstance(x, dict) for x in raw["live"].values()):
        raise _Bad("live must be an object of account blocks")
    if not isinstance(raw["days"], dict):
        raise _Bad("days must be an object keyed by day")
    for day, d in raw["days"].items():
        _check_day(day, d)
    if not isinstance(raw["blocks"], dict):
        raise _Bad("blocks must be an object keyed by account id")
    for u, bl in raw["blocks"].items():
        if not isinstance(bl, list):
            raise _Bad(f"blocks: {u} must be a list")
        for b in bl:
            _check_block(u, b)
    if not isinstance(raw["session_share"], dict):
        raise _Bad("session_share must be an object")


def _legacy_days(days) -> Dict[str, dict]:
    if not isinstance(days, dict):
        raise _Bad("days must be an object keyed by day")
    out = {}
    for day, a in days.items():
        if not _DAY.match(day) or not isinstance(a, dict):
            raise _Bad(f"days: {day!r} is not a day entry")
        for c in COLS + ("calls",):
            if not _is_int(a.get(c, 0)):
                raise _Bad(f"days: {day} {c} must be a whole number")
        if not _is_num(a.get("cost", 0)):
            raise _Bad(f"days: {day} cost must be a number")
        out[day] = {**{c: a.get(c, 0) for c in COLS}, "calls": a.get("calls", 0), "usd": a.get("cost", 0),
                    "unpriced": a["unpriced"] if _is_int(a.get("unpriced")) else 0,
                    "models": {}, "accounts": {}, "projects": {}}
    return out


def _parse_report(name: str, raw) -> Report:
    if not isinstance(raw, dict):
        raise _Bad("not a report object")
    if "report_version" in raw:
        _validate_v3(raw)
        return Report(raw["user"], raw["host"], raw["install_id"], raw["tool_version"],
                      parse_iso(raw["generated"]), raw["accounts"], raw["live"], raw["days"], raw["blocks"],
                      raw["session_share"], False, name)
    # a report from before report_version existed: no account ids, so it can be counted but never split
    missing = [k for k in ("user", "host", "days") if k not in raw]
    if missing:
        raise _Bad(f"not a jusage report (missing {', '.join(missing)})")
    _need_str(raw, "user")
    _need_str(raw, "host")
    try:
        version_tuple(raw.get("tool_version"))
    except ValueError:
        raise _Bad("not a jusage report (no readable tool_version)") from None
    gen = _check_time("generated", raw.get("generated"))
    inst = raw.get("install_id")
    inst = inst if isinstance(inst, str) and inst else None
    days = _legacy_days(raw["days"])
    accounts, blocks = _label_accounts(raw) if version_tuple(raw["tool_version"]) >= SPLITTABLE_FROM else ([], {})
    return Report(raw["user"], raw["host"], inst, raw["tool_version"], gen, accounts, {}, days, blocks, {},
                  not accounts, name)


def _label_accounts(raw: dict) -> Tuple[List[dict], Dict[str, List[dict]]]:
    """A 0.1.22+ report before report_version: its blocks are keyed by account label (the email,
    else '<user> #n') and `account_ids` maps label -> claude.ai uuid. A label with a uuid is mapped
    onto the v3 shape (uuid-keyed blocks, `cost` -> `usd`); a label without one cannot be matched to
    anyone's account and is dropped. No label mapped at all -> ([], {}): the report stays legacy."""
    ids = raw.get("account_ids")
    ids = {k: v for k, v in ids.items() if isinstance(k, str) and isinstance(v, str) and v} if isinstance(ids, dict) else {}
    raw_blocks = raw.get("blocks", {})
    if not isinstance(raw_blocks, dict):
        raise _Bad("blocks must be an object keyed by account label")
    blocks: Dict[str, List[dict]] = {}
    for label, bl in raw_blocks.items():
        if not isinstance(bl, list):
            raise _Bad(f"blocks: {label} must be a list")
        mapped = []
        for b in bl:
            if not isinstance(b, dict):
                raise _Bad(f"blocks: {label} holds something that is not a window")
            start, end = _check_time("blocks start", b.get("start")), _check_time("blocks end", b.get("end"))
            if end <= start or not _is_num(b.get("cost", 0)):
                raise _Bad("blocks: a window is out of order or its cost is not a number")
            mapped.append({"start": b["start"], "end": b["end"], "tokens": b.get("tokens", 0) if _is_int(b.get("tokens", 0)) else 0,
                           "calls": b.get("calls", 0) if _is_int(b.get("calls", 0)) else 0, "usd": b.get("cost", 0)})
        if label in ids:
            blocks.setdefault(ids[label], []).extend(mapped)
    accounts = [{"key_hash": "", "number": i + 1, "email": label if "@" in label else "", "uuid": u}
                for i, (label, u) in enumerate(sorted(ids.items()))]
    return accounts, blocks


def load_reports(directory, own_install: Optional[str]) -> Tuple[List[Report], List[str]]:
    """Every usable report in the shared folder except this install's own (its data comes from the
    ledger; own_install=None keeps it, for the dashboard, which shows everyone), and a note for each
    file skipped. Half-synced `.tmp` files, `.sync-conflict` copies,
    unreadable files, other versions and bad shapes are skipped with the file named (R-115, R-129).
    Two files for one install (a rename, a reinstall) keep the newer `generated` instant, compared
    as instants (R-116); the older is ignored, never deleted: it is theirs."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ConfigError(f"Shared folder {directory} is missing; pass --reports to create it")
    notes: List[str] = []
    best: Dict[str, Report] = {}
    for p in sorted(directory.iterdir(), key=lambda q: q.name):
        name = p.name
        if name.endswith(".tmp"):
            notes.append(f"skipped {name}: half-synced temp file")
            continue
        if ".sync-conflict" in name:
            notes.append(f"skipped {name}: sync-conflict copy")
            continue
        if not name.endswith(".json") or not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            notes.append(f"skipped {name}: unreadable ({type(e).__name__})")
            continue
        try:
            rep = _parse_report(name, raw)
        except _Bad as e:
            notes.append(f"skipped {name}: {e}")
            continue
        if rep.install_id == own_install:
            continue
        prev = best.get(rep.key)
        if prev is None:
            best[rep.key] = rep
        elif rep.generated > prev.generated:
            notes.append(f"ignoring {prev.name}: {rep.name} is newer for the same install")
            best[rep.key] = rep
        else:
            notes.append(f"ignoring {rep.name}: {prev.name} is newer for the same install")
    return sorted(best.values(), key=lambda r: r.name), notes


# ---------------------------------------------------------------- people

@dataclass
class Person:
    user: str
    reports: List[Report]                 # newest first; the newest speaks for the person
    legacy: bool
    uuids: frozenset
    today: dict
    week: dict
    accounts: List[dict]                  # account blocks, `runs_out` removed: a forecast is mine to make

    @property
    def install_ids(self) -> List[str]:
        return sorted(r.key for r in self.reports)

    @property
    def install_id(self) -> str:
        return self.reports[0].key

    @property
    def host(self) -> str:
        return self.reports[0].host

    @property
    def generated(self) -> datetime:
        return self.reports[0].generated

    def is_stale(self, now: datetime) -> bool:
        """Greys 15 minutes after the report's `generated` instant (R-121): the one staleness rule."""
        return (now - self.generated).total_seconds() >= STALE_AFTER_S

    def to_feed(self) -> dict:
        return {"install_id": self.install_id, "user": self.user, "host": self.host,
                "generated": timeutil.iso(self.generated), "today": self.today, "week": self.week,
                "accounts": self.accounts}


def _strip_runs_out(block: dict) -> dict:
    b = json.loads(json.dumps(block))
    for m in b.get("meters") or []:
        if isinstance(m, dict) and isinstance(m.get("pace"), dict):
            m["pace"].pop("runs_out", None)
    return b


def _span(reports: Sequence[Report], lo: str, hi: str) -> dict:
    tokens = calls = unpriced = 0
    usd = 0.0
    for r in reports:
        for day, a in r.days.items():
            if lo <= day <= hi:
                tokens += sum(a[c] for c in COLS)
                calls += a["calls"]
                unpriced += a["unpriced"]
                usd += a["usd"]
    return {"tokens": tokens, "usd": round(usd, 2), "calls": calls, "unpriced": unpriced}


def people(reports: Sequence[Report], now: datetime) -> List[Person]:
    """Group reports into people. The same user name AND a shared account uuid is one person across
    Macs (R-123); a name alone never merges, and a legacy report (no uuid) stands alone. Sorted by
    name, then newest report."""
    timeutil.iso(now)
    parent = list(range(len(reports)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(reports):
        for j in range(i):
            b = reports[j]
            if not a.legacy and not b.legacy and a.user == b.user and a.uuids & b.uuids:
                parent[find(i)] = find(j)
    groups: Dict[int, List[Report]] = {}
    for i, r in enumerate(reports):
        groups.setdefault(find(i), []).append(r)

    today = now.astimezone().date()
    hi = today.isoformat()
    lo = (today - timedelta(days=WEEK_DAYS - 1)).isoformat()
    out = []
    for members in groups.values():
        members = sorted(members, key=lambda r: (-r.generated.timestamp(), r.name))
        merged: Dict[str, dict] = {}
        for r in members:                                   # newest report first: it wins an account
            for u, block in r.live.items():
                merged.setdefault(u, _strip_runs_out(block))
        accts = [merged[u] for u in sorted(merged, key=lambda u: (
            merged[u]["number"] if _is_int(merged[u].get("number")) else 10 ** 9, u))]
        out.append(Person(members[0].user, members, all(r.legacy for r in members),
                          frozenset().union(*(r.uuids for r in members)), _span(members, hi, hi),
                          _span(members, lo, hi), accts))
    return sorted(out, key=lambda p: (p.user.casefold(), -p.generated.timestamp(), p.install_id))


# ---------------------------------------------------------------- the split

@dataclass(frozen=True)
class Part:
    user: str
    install_id: str
    usd: float
    share: float


@dataclass(frozen=True)
class Split:
    period_start: datetime
    parts: List[Part]                     # biggest spend first
    missing: List[str]                    # people whose report is too old to match (R-122)

    def to_feed(self) -> dict:
        return {"period_start": timeutil.iso(self.period_start),
                "parts": [{"user": p.user, "install_id": p.install_id, "usd": p.usd, "share": p.share}
                          for p in self.parts]}


def _spend_in(blocks: Iterable[dict], start: datetime, end: datetime) -> float:
    cost = 0.0
    for b in blocks:
        b0, b1 = parse_iso(b["start"]), parse_iso(b["end"])
        # a window the period cuts counts by the fraction inside it; the divisor is the window as far
        # as it has run, so an open window (its cost is what it spent so far) is not discounted twice
        overlap = (min(end, b1) - max(start, b0)).total_seconds()
        span = (min(end, b1) - b0).total_seconds()
        if overlap > 0 and span > 0:
            cost += b["usd"] * overlap / span
    return cost


def meter_split(meter, account_uuid: str, own_windows: Sequence[dict], reports: Sequence[Report],
                now: datetime, *, me: Tuple[str, str]) -> Optional[Split]:
    """Who spent what inside this meter's current period (reset back `period_s`, up to now or the
    reset, whichever is first). `own_windows` are this install's windows for the account in the
    report's block shape; `reports` are everyone else's (E20 `load_reports`); `me` is
    (user, install_id). A person on two Macs is one part. None when the meter has no reset time or
    nobody spent anything and nobody is missing."""
    timeutil.iso(now)
    if meter.resets_at is None:
        return None
    start = meter.resets_at - timedelta(seconds=meter.period_s)
    end = min(now, meter.resets_at)
    spend: Dict[str, float] = {}
    install: Dict[str, str] = {}
    own_user, own_install = me
    spend[own_user] = _spend_in(own_windows, start, end)
    install[own_user] = own_install
    missing = set()
    for r in sorted(reports, key=lambda r: (-r.generated.timestamp(), r.name)):
        if r.legacy:
            missing.add(r.user)
            continue
        if account_uuid not in r.blocks:
            continue
        spend[r.user] = spend.get(r.user, 0.0) + _spend_in(r.blocks[account_uuid], start, end)
        install.setdefault(r.user, r.key)
    spend = {u: c for u, c in spend.items() if c > 0}
    total = sum(spend.values())
    parts = [Part(u, install[u], round(c, 2), round(c / total, 4)) for u, c in spend.items()]
    parts.sort(key=lambda p: (-p.usd, p.user))
    if not parts and not missing:
        return None
    return Split(start, parts, sorted(missing))
