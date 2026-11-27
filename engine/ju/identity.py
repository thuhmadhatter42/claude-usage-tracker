"""E12: identity checks (R-13, R-14, R-26). Pure functions; the caller owns the stored anchor.

`check_stable` is the uuid half: the account uuid read before and after a fetch must be the same
or the reading is discarded. The fingerprint half learns each account's weekly-reset minute and
refuses a reading that carries another account's.

An anchor is the `reset_anchor` object of live.json:
    {"minute_of_week": int|None, "confirmations": int, "candidate": int|None, "candidate_count": int}
Minutes are minutes since Monday 00:00 UTC (timeutil.minute_of_week), compared around the week's
wrap. Tolerance is FINGERPRINT_TOL_S (120 s = 2 minutes), the resolution of an anchor.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Mapping, Optional, Tuple

from ju import constants, timeutil
from ju.contracts import IdentityRejected

ACCEPT = "accept"
REJECT_FOREIGN = "reject_foreign"
_WEEK_MIN = 7 * 24 * 60
_TOL_MIN = constants.FINGERPRINT_TOL_S // 60
LEARN_AFTER = 2                      # agreeing readings before a minute is learned or relearned


def check_stable(before: Optional[str], after: Optional[str]) -> None:
    """Raise IdentityRejected(identity_changed) unless the uuid is exactly the same. A uuid that
    appears or vanishes between the two reads is a change: identity fails closed."""
    if before != after:
        raise IdentityRejected("identity_changed")


def new_anchor() -> Dict[str, Optional[int]]:
    return {"minute_of_week": None, "confirmations": 0, "candidate": None, "candidate_count": 0}


def _near(a: int, b: int) -> bool:
    d = abs(a - b) % _WEEK_MIN
    return min(d, _WEEK_MIN - d) <= _TOL_MIN


def foreign_match(weekly_reset: Optional[datetime], others: Mapping[str, int]) -> Optional[str]:
    """The folder key (first in sorted order) whose learned minute this weekly reset matches, or
    None. The gate records it on an identity_foreign_reset so the feed can name that account (R-14)."""
    if weekly_reset is None:
        return None
    w = timeutil.minute_of_week(weekly_reset)
    return next((k for k in sorted(others) if _near(w, others[k])), None)


def matches_own(anchor: Mapping, weekly_reset: Optional[datetime]) -> bool:
    """True when this weekly reset falls on the account's learned minute (none learned -> False)."""
    own = anchor.get("minute_of_week")
    return weekly_reset is not None and own is not None and _near(timeutil.minute_of_week(weekly_reset), own)


def observe(anchor: Mapping, weekly_reset: Optional[datetime], others: Mapping[str, int]) -> Tuple[dict, str]:
    """Judge one reading's weekly reset against this account's learned minute and the other
    accounts' (`others`: folder key -> learned minute). Returns (new anchor, verdict); the input
    is never changed.

    - matches own learned minute -> accept (own wins even if another account shares the minute);
    - matches another account's and not its own -> reject_foreign, anchor unchanged (R-14);
    - otherwise accept and count it: two agreeing readings learn (or relearn) the minute."""
    a = dict(anchor)
    if weekly_reset is None:
        return a, ACCEPT
    w = timeutil.minute_of_week(weekly_reset)
    own = a.get("minute_of_week")
    if own is not None and _near(w, own):
        a["confirmations"] = a.get("confirmations", 0) + 1
        a["candidate"], a["candidate_count"] = None, 0
        return a, ACCEPT
    if any(_near(w, m) for m in others.values()):
        return a, REJECT_FOREIGN
    cand = a.get("candidate")
    if cand is not None and _near(w, cand):
        a["candidate_count"] = a.get("candidate_count", 0) + 1
    else:
        a["candidate"], a["candidate_count"] = w, 1
    if a["candidate_count"] >= LEARN_AFTER:
        a["minute_of_week"], a["confirmations"] = a["candidate"], a["candidate_count"]
        a["candidate"], a["candidate_count"] = None, 0
    return a, ACCEPT
