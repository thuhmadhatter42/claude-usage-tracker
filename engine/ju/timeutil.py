"""E28: time. Every time that crosses a process boundary is ISO 8601 with an offset; a time
without one is a bug and is refused here, once, for every caller."""
from __future__ import annotations

import re
from datetime import datetime, timezone


class NaiveTime(ValueError):
    """A time with no offset. Never guessed, never assumed local."""


_FRACTION = re.compile(r"(\.\d+)")


def parse_iso(s) -> datetime:
    """ISO 8601 -> aware datetime. 'Z' and any offset accepted, any fraction length.
    Raises NaiveTime when there is no offset, ValueError when it is not a time at all."""
    if not isinstance(s, str) or not s:
        raise ValueError(f"not an ISO time: {s!r}")
    text = s.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: (m.group(1) + "000000")[:7], text, count=1)
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"not an ISO time: {s!r}") from None
    if d.tzinfo is None or d.utcoffset() is None:
        raise NaiveTime(f"time has no offset: {s!r}")
    return d


def _aware(d: datetime) -> datetime:
    if d.tzinfo is None or d.utcoffset() is None:
        raise NaiveTime(f"time has no offset: {d!r}")
    return d


def iso(d: datetime) -> str:
    """aware datetime -> local time, whole seconds, with offset."""
    return _aware(d).astimezone().isoformat(timespec="seconds")


def now() -> datetime:
    """The current aware local time. The one place tests patch."""
    return datetime.now(timezone.utc).astimezone()


def minute_of_week(d: datetime) -> int:
    """Minutes since Monday 00:00 UTC, 0..10079. UTC, because a weekly reset is one instant
    per week: wall-clock fields would move an hour across a daylight-saving change."""
    u = _aware(d).astimezone(timezone.utc)
    return u.weekday() * 1440 + u.hour * 60 + u.minute
