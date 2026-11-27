"""E18: how much of the week each 5-hour session used (R-87..R-89). Per session: the Weekly
percent at its last reading minus the percent at its first, both inside the session and inside
one weekly period; fewer than two readings, a weekly reset in the middle, or a percent that went
down gives no number (None), never a guess."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from ju.constants import FINGERPRINT_TOL_S
from ju.timeutil import iso, parse_iso

MEDIAN_OF = 7
WEEKLY = "weekly"


@dataclass(frozen=True)
class Window:
    """A session: the usage service's 5-hour window, or an E9 block (anything with these three
    fields works the same way, which is how an Ultra account without a session meter is served)."""
    start: datetime
    end: datetime
    open: bool = False


@dataclass(frozen=True)
class ShareItem:
    start: datetime
    pct: Optional[float]


@dataclass(frozen=True)
class SessionShare:
    current: Optional[float]      # the open session's share so far, when the latest session is open
    last: Optional[float]         # newest finished session with a number
    median7: Optional[float]      # median of the newest 7 sessions that have a number
    list: List[ShareItem]         # oldest first
    as_of: datetime               # newest weekly reading considered

    def to_feed(self) -> dict:
        return {"current": self.current, "last": self.last, "median7": self.median7,
                "list": [{"start": iso(i.start), "pct": i.pct} for i in self.list],
                "as_of": iso(self.as_of)}


def _same_period(a: datetime, b: datetime) -> bool:
    return abs((a - b).total_seconds()) <= FINGERPRINT_TOL_S


def _readings(rows) -> list:
    out = []
    for r in rows:
        if r["meter"] != WEEKLY:
            continue
        resets = r["resets_at"]
        out.append((parse_iso(r["ts"]), float(r["pct"]), parse_iso(resets) if resets else None))
    out.sort(key=lambda x: x[0])
    return out


def _share(readings, w, weekly_period) -> Optional[float]:
    inside = [x for x in readings if w.start <= x[0] <= w.end
              and (weekly_period is None or (x[2] is not None and _same_period(x[2], weekly_period)))]
    if len(inside) < 2:
        return None
    first = inside[0][2]
    if first is None or any(x[2] is None or not _same_period(x[2], first) for x in inside):
        return None
    delta = inside[-1][1] - inside[0][1]
    return round(delta, 6) if delta >= 0 else None


def session_share(meter_log_rows, sessions: Sequence, weekly_period: Optional[datetime]) -> Optional[SessionShare]:
    """`meter_log_rows`: mappings with ts, meter, pct, resets_at (dicts or sqlite3.Row), already
    one account's. `weekly_period`: the weekly reset time to restrict to, or None for every
    period (each session is still judged inside its own). None when there are no sessions or no
    weekly readings."""
    readings = _readings(meter_log_rows)
    if not readings or not sessions:
        return None
    ordered = sorted(sessions, key=lambda w: w.start)
    items = [ShareItem(w.start, _share(readings, w, weekly_period)) for w in ordered]
    latest = ordered[-1]
    current = items[-1].pct if latest.open else None
    finished = [i.pct for i, w in zip(items, ordered) if not w.open and i.pct is not None]
    last = finished[-1] if finished else None
    numbers = [i.pct for i in items if i.pct is not None][-MEDIAN_OF:]
    median7 = float(statistics.median(numbers)) if numbers else None
    return SessionShare(current, last, median7, items, readings[-1][0])
