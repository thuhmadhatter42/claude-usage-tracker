"""E17: the one pace function. Everything forecast-related comes from Claude's own percentage and
the clock, nothing else (R-36). Comparisons that decide a colour or the runs-out edge are exact
(Fractions), so 20.1 at e=0.2 and landing 105 never wobble on binary floats (RULING 2)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Optional

from ju import formats
from ju.constants import RED_AT, RUNS_OUT_LANDING, SPAN_FLOOR_DIV, STALE_AFTER_S, WARMUP_E
from ju.contracts import Meter, Pace
from ju.timeutil import NaiveTime

_RED = Fraction(repr(RED_AT))
_WARMUP = Fraction(repr(WARMUP_E))
_EDGE = Fraction(RUNS_OUT_LANDING)
_ROUND_TO = 5


def _aware(d: datetime) -> datetime:
    if d.tzinfo is None or d.utcoffset() is None:
        raise NaiveTime(f"time has no offset: {d!r}")
    return d


def _secs(td: timedelta) -> Fraction:
    return Fraction(td.days * 86400 + td.seconds) + Fraction(td.microseconds, 10 ** 6)


def state(used, e) -> Optional[str]:
    """red at used >= 97; amber when used is past the share of the period gone and at least 3%
    of it has gone; green otherwise. The forecast never colours."""
    u, el = Fraction(repr(used)) if isinstance(used, float) else Fraction(used), \
        Fraction(repr(e)) if isinstance(e, float) else Fraction(e)
    if u >= _RED:
        return "red"
    if el >= _WARMUP and u > el * 100:
        return "amber"
    return "green"


def _landing_words(landing: Fraction) -> str:
    """Landing below the runs-out edge (105) rounded to the nearest 5. The R-40 "over 200%" cap
    cannot arise: any landing of 105 or more has a runs-out time inside the period, and that
    sentence replaces this one."""
    return f"about {math.floor(landing / _ROUND_TO + Fraction(1, 2)) * _ROUND_TO}%"


def pace(m: Meter, now: datetime, read_at: Optional[datetime] = None) -> Pace:
    """Forecast, colour and runs-out for one meter. `state` is None (no colour: the app draws it
    grey) when the reset is due or, if `read_at` is given, the reading is at least
    STALE_AFTER_S. `runs_out` is a timestamp; the words carry the `{runs_out}` placeholder for the
    app to fill in with the Mac's clock (RULING 4)."""
    now = _aware(now)
    stale = read_at is not None and _secs(now - _aware(read_at)) >= STALE_AFTER_S
    used = Fraction(repr(m.pct)) if isinstance(m.pct, float) else Fraction(m.pct)
    L = Fraction(m.period_s)

    if m.resets_at is None:
        return Pace(e=0.0, landing=None, state=None if stale else state(used, 0),
                    forecast=False, runs_out=None, reset_due=False,
                    words="No reset time to forecast from")

    left = _secs(_aware(m.resets_at) - now)
    if left <= 0:
        return Pace(e=1.0, landing=None, state=None, forecast=False, runs_out=None,
                    reset_due=True, words=formats.RESET_DUE_TEXT)

    t = max(L - left, Fraction(0))
    e = t / L
    st = None if stale else state(used, e)
    if e < _WARMUP:
        return Pace(e=float(e), landing=None, state=st, forecast=False, runs_out=None,
                    reset_due=False, words="Too early to forecast")

    span = max(t, L / SPAN_FLOOR_DIV)
    landing = used * L / span
    runs_out = None
    if used > 0 and landing >= _EDGE:
        secs_to_full = span * (100 - used) / used
        if secs_to_full < left:
            runs_out = now + timedelta(seconds=math.floor(secs_to_full))
    if runs_out is not None:
        words = (f"At this pace you hit the limit {{runs_out}}, "
                 f"{formats.countdown(int(_secs(m.resets_at - runs_out)))} before the reset.")
    else:
        words = f"On pace: {_landing_words(landing)} by the reset."
    return Pace(e=float(e), landing=float(landing), state=st, forecast=True, runs_out=runs_out,
                reset_due=False, words=words)
