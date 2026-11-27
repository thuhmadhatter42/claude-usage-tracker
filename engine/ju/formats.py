"""E19: how the engine writes numbers, durations and names. The renderers (Swift, JS) follow the
same table, tests/fixtures/formats.json. The engine never writes a clock time into feed text
(RULING 4); `clock` exists for the Python report and CLI, which print on this Mac."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

RESET_DUE_TEXT = "Reset due, waiting for a fresh reading"
API_RATE_LABEL = "API-rate equivalent, not your bill"

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def tokens(n) -> str:
    """1.23B / 4.5M / 450k / 12. Halves round up; a value that rounds up into the next unit
    is written in that unit ('1.0M', never '1000k')."""
    if n < 0:
        raise ValueError(f"negative token count: {n!r}")
    n = int(n)
    if n < 1000:
        return str(n)
    k = (n + 500) // 1000
    if k < 1000:
        return f"{k}k"
    tenths = (n + 50_000) // 100_000
    if tenths < 10_000:
        return f"{tenths // 10}.{tenths % 10}M"
    hundredths = (n + 5_000_000) // 10_000_000
    return f"{hundredths // 100}.{hundredths % 100:02d}B"


def usd(x) -> str:
    """>= $100 no cents with a thousands separator, else two decimals."""
    d = Decimal(repr(x)) if isinstance(x, float) else Decimal(x)
    if d < 0:
        raise ValueError(f"negative dollars: {x!r}")
    cents = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cents < 100:
        return f"${cents}"
    return f"${int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP)):,}"


def countdown(seconds) -> str:
    """Whole minutes, floored: '42m', '2h 05m', '5d 21h'. Zero or less is 'due'."""
    if seconds <= 0:
        return "due"
    minutes = int(seconds) // 60
    hours, mins = divmod(minutes, 60)
    if hours == 0:
        return f"{mins}m"
    if hours < 24:
        return f"{hours}h {mins:02d}m"
    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs}h"


def clock(dt: datetime, now: datetime, h24: bool, tz_offset_min: Optional[int] = None) -> str:
    """'14:05' / '2:05 PM' when `dt` is on the same calendar day as `now`, else the English
    weekday first ('Fri 14:05'). Days are counted in `tz_offset_min` (minutes east of UTC), or the
    Mac's local zone when None."""
    for d in (dt, now):
        if d.tzinfo is None or d.utcoffset() is None:
            raise ValueError(f"time has no offset: {d!r}")
    if tz_offset_min is None:
        a, b = dt.astimezone(), now.astimezone()
    else:
        zone = timezone(timedelta(minutes=tz_offset_min))
        a, b = dt.astimezone(zone), now.astimezone(zone)
    if h24:
        body = f"{a.hour:02d}:{a.minute:02d}"
    else:
        body = f"{a.hour % 12 or 12}:{a.minute:02d} {'AM' if a.hour < 12 else 'PM'}"
    if a.date() == b.date():
        return body
    return f"{_WEEKDAYS[a.weekday()]} {body}"


def model_name(model_id: str) -> str:
    """Strip `claude-` and any trailing `-YYYYMMDD`."""
    name = model_id[len("claude-"):] if model_id.startswith("claude-") else model_id
    return _DATE_SUFFIX.sub("", name)


def meter_name(kind: str, model_display: Optional[str]) -> str:
    """Anthropic's words: Session, Weekly, 'Weekly · <model>'."""
    if kind == "session":
        return "Session"
    if kind == "weekly":
        return "Weekly"
    if kind == "weekly_scoped":
        if not model_display:
            raise ValueError("a scoped weekly meter needs its model name")
        return f"Weekly · {model_display}"
    raise ValueError(f"unknown meter kind {kind!r}")
