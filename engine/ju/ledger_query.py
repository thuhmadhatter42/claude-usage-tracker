"""E9: reads of the ledger. Dates, totals, 5-hour blocks, the open window per account, calendar
months, and model / project rankings. Every dollar here is API-rate equivalent (E6) and an
unpriced model is counted in `unpriced`, never folded in as $0 without a flag."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from ju import pricing as pricing_mod
from ju import timeutil
from ju.constants import COLS

BLOCK_HOURS = 5
WINDOW_LOOKBACK_DAYS = 7
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")
_ND = re.compile(r"^([0-9]+)d$")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


@dataclass
class Totals:
    input: int = 0
    cache_5m: int = 0
    cache_1h: int = 0
    cache_read: int = 0
    output: int = 0
    cost: float = 0.0          # API-rate dollars of the priced calls
    calls: int = 0
    unpriced: int = 0          # calls whose model has no price (their dollars are not in `cost`)

    @property
    def tokens(self) -> int:
        return self.input + self.cache_5m + self.cache_1h + self.cache_read + self.output

    def add(self, o: "Totals") -> None:
        for c in COLS:
            setattr(self, c, getattr(self, c) + getattr(o, c))
        self.cost += o.cost
        self.calls += o.calls
        self.unpriced += o.unpriced


@dataclass
class Block:
    start: datetime
    end: datetime
    rows: list = field(default_factory=list)


@dataclass
class Window:
    account: str
    start: datetime
    end: datetime
    totals: Totals
    open: bool


@dataclass
class Ranked:
    name: str
    totals: Totals


@dataclass
class Month:
    key: str                       # YYYY-MM
    label: str                     # "November"
    totals: Totals
    models: List[Ranked]
    projects: List[Ranked]


def since_date(s: Optional[str]) -> Optional[str]:
    """`Nd` (N >= 1: today and the N-1 days before), `YYYY-MM-DD`, `all` (no limit) or None (no
    limit) -> a day string or None. Anything else is a ValueError, never a silent SQL parameter."""
    if s is None or s == "all":
        return None
    m = _ND.match(s)
    if m and int(m.group(1)) >= 1:
        return (timeutil.now().date() - timedelta(days=int(m.group(1)) - 1)).isoformat()
    if _DAY.match(s):
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            pass
    raise ValueError(f"bad date '{s}'")


def project_group(name: str) -> str:
    return "subagents" if name.startswith("agent-") else name


def rows(conn: sqlite3.Connection, since: Optional[str], account: Optional[str] = None) -> list:
    """Ledger rows from day `since` on (None = all), one account or all, oldest first by instant."""
    conds, params = [], []
    if since:
        conds.append("day >= ?")
        params.append(since)
    if account:
        conds.append("account = ?")
        params.append(account)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return conn.execute(f"SELECT * FROM records{where} ORDER BY julianday(ts), key", params).fetchall()


def _totals_of(p: pricing_mod.Pricing, model: str, sums: Dict[str, int], calls: int) -> Totals:
    cost = pricing_mod.cost_usd(p, {"model": model, **sums})
    return Totals(**sums, cost=0.0 if cost is None else cost, calls=calls, unpriced=calls if cost is None else 0)


def sum_rows(rows_: Sequence, p: pricing_mod.Pricing) -> Totals:
    out = Totals()
    for r in rows_:
        out.add(_totals_of(p, r["model"], {c: r[c] for c in COLS}, 1))
    return out


def _grouped(conn, p, day_from, day_to, account, dim: Optional[str]) -> Dict[str, Totals]:
    """Totals per `dim` (model, project or None = one group), cost summed per model so every
    price applies to exactly its own tokens."""
    sel = f"{dim}, " if dim else ""
    cols = ", ".join(f"sum({c})" for c in COLS)
    where, params = "day >= ? AND day <= ?", [day_from, day_to]
    if account:
        where += " AND account = ?"
        params.append(account)
    q = (f"SELECT {sel}model, {cols}, count(*) FROM records WHERE {where} "
         f"GROUP BY {sel}model ORDER BY {sel}model")
    out: Dict[str, Totals] = {}
    for rec in conn.execute(q, params):
        i = 1 if dim else 0
        name = rec[0] if dim else ""
        if dim == "project":
            name = project_group(name)
        model = rec[i]
        sums = {c: rec[i + 1 + k] for k, c in enumerate(COLS)}
        out.setdefault(name, Totals()).add(_totals_of(p, model, sums, rec[i + 1 + len(COLS)]))
    return out


def totals(conn, p, day_from: str, day_to: str, account: Optional[str] = None) -> Totals:
    return _grouped(conn, p, day_from, day_to, account, None).get("", Totals())


def _ranked(groups: Dict[str, Totals], n: Optional[int]) -> List[Ranked]:
    items = sorted(groups.items(), key=lambda kv: (-kv[1].tokens, kv[0]))
    return [Ranked(k, v) for k, v in items[:n]]


def top_models(conn, p, day_from: str, day_to: str, n: Optional[int], account: Optional[str] = None) -> List[Ranked]:
    return _ranked(_grouped(conn, p, day_from, day_to, account, "model"), n)


def top_projects(conn, p, day_from: str, day_to: str, n: Optional[int], account: Optional[str] = None) -> List[Ranked]:
    """Projects ranked by tokens; every `agent-*` folder is one row called "subagents"."""
    return _ranked(_grouped(conn, p, day_from, day_to, account, "project"), n)


def blocks(rows_: Sequence) -> List[Block]:
    """5-hour windows the way the rate limit counts them: a window opens at the top of the hour of
    the first call after the previous window closed. `rows_` is one account, oldest first."""
    out: List[Block] = []
    end = None
    for r in rows_:
        t = timeutil.parse_iso(r["ts"])
        if end is None or t >= end:
            start = t.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=BLOCK_HOURS)
            out.append(Block(start, end))
        out[-1].rows.append(r)
    return out


def blocks_by_account(rows_: Sequence) -> Dict[str, List[Block]]:
    """Each account has its own 5-hour clock; interleaving two would invent windows nobody has."""
    per: Dict[str, list] = {}
    for r in rows_:
        per.setdefault(r["account"], []).append(r)
    return {a: blocks(rs) for a, rs in sorted(per.items())}


def windows(conn, p, now: datetime) -> List[Window]:
    """Each account's latest 5-hour block of the last 7 days: its totals, and whether `now` is inside."""
    timeutil.iso(now)     # refuses a time without an offset
    since = (now.astimezone().date() - timedelta(days=WINDOW_LOOKBACK_DAYS - 1)).isoformat()
    out = []
    for acct, bl in blocks_by_account(rows(conn, since)).items():
        b = bl[-1]
        out.append(Window(acct, b.start, b.end, sum_rows(b.rows, p), now < b.end))
    return out


def months(conn, p, n: int = 3) -> List[Month]:
    """The last `n` calendar months (this one first), each with its models and projects."""
    today = timeutil.now().astimezone().date()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        f, l = first.isoformat(), last.isoformat()
        out.append(Month(f"{y:04d}-{m:02d}", MONTH_NAMES[m - 1], totals(conn, p, f, l),
                         top_models(conn, p, f, l, None), top_projects(conn, p, f, l, None)))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out
