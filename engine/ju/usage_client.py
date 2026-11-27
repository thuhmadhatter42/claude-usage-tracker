"""E11: the usage client (R-1 ... R-5, R-21). One GET to Anthropic's usage service and the parse
of its reply into meters. Never follows a redirect (urllib would resend the Authorization header
to the new host), never prints the token, never guesses a number: a reply that is not one of the
two known shapes, or holds a percent outside 0-101, is `ReplyChanged`.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, List, Optional

from ju import constants, timeutil
from ju.contracts import HttpError, LoginError, Meter, NetworkError, RateLimited, ReplyChanged

EXPIRED_TEXT = "Open Claude Code on this account, then refresh"
PCT_MAX = 101.0

FLAG_CREDITS = "using_credits"
FLAG_RESET = "reset_available"


@dataclass(frozen=True)
class ParsedReply:
    meters: List[Meter]
    flags: List[str]
    no_session_meter: bool


# ---------------------------------------------------------------- fetch

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HttpError(code)


def _version() -> str:
    return (constants.REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _retry_after(value: Optional[str]) -> Optional[int]:
    """Retry-After as whole seconds (a number, or an HTTP-date measured on the one clock).
    Absent, 0, in the past or unreadable -> None."""
    if value is None:
        return None
    text = value.strip()
    if re.fullmatch(r"\d+", text):
        n = int(text)
        return n or None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None or when.tzinfo is None:
        return None
    seconds = math.ceil((when - timeutil.now()).total_seconds())
    return seconds if seconds > 0 else None


def fetch(token: str, url: str = constants.USAGE_URL) -> dict:
    """GET the usage reply. 401 -> LoginError(login_expired); 429 -> RateLimited(retry_after_s);
    any redirect or other status -> HttpError(status); no connection -> NetworkError; a body that
    is not JSON -> ReplyChanged."""
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": constants.ANTHROPIC_BETA,
        "Accept": "application/json",
        "User-Agent": f"jusage/{_version()}",
    })
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=constants.HTTP_TIMEOUT_S) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 401:
            raise LoginError("login_expired", EXPIRED_TEXT) from None
        if e.code == 429:
            raise RateLimited(_retry_after(e.headers.get("Retry-After"))) from None
        raise HttpError(e.code) from None
    except (urllib.error.URLError, OSError):
        raise NetworkError() from None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ReplyChanged() from None


# ---------------------------------------------------------------- parse

def _pct(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReplyChanged()
    p = float(value)
    if not (0.0 <= p <= PCT_MAX):          # also false for NaN
        raise ReplyChanged()
    return p


def _reset(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return timeutil.parse_iso(value)
    except ValueError:
        raise ReplyChanged() from None


def _model_key(display_name: str) -> str:
    words = display_name.split()
    return words[0].lower() if words else ""


def _unique(base: str, taken: set) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _from_limits(limits: list) -> List[Meter]:
    meters: List[Meter] = []
    taken: set = set()
    for e in limits:
        if not isinstance(e, dict) or e.get("percent") is None:
            continue
        kind = e.get("kind")
        if kind == "session":
            mid, name, period = "session", "Session", constants.SESSION_S
        elif kind == "weekly_all":
            mid, name, period = "weekly", "Weekly", constants.WEEK_S
        elif kind == "weekly_scoped":
            scope = e.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            display = (model.get("display_name") or model.get("displayName")) if isinstance(model, dict) else None
            if not isinstance(display, str) or not _model_key(display):
                raise ReplyChanged()        # a scoped limit we cannot name is a reply we do not understand
            mid, name, period = f"weekly:{_model_key(display)}", f"Weekly · {display.strip()}", constants.WEEK_S
        else:
            continue                         # a kind this version has never heard of
        meters.append(Meter(_unique(mid, taken), name, _pct(e["percent"]), _reset(e.get("resets_at")), period))
    return meters


def _from_legacy(reply: dict) -> List[Meter]:
    meters: List[Meter] = []
    taken: set = set()
    for field in reply:
        if field == "five_hour":
            mid, name, period = "session", "Session", constants.SESSION_S
        elif field == "seven_day":
            mid, name, period = "weekly", "Weekly", constants.WEEK_S
        elif isinstance(field, str) and field.startswith("seven_day_") and len(field) > len("seven_day_"):
            key = field[len("seven_day_"):]
            mid, name, period = f"weekly:{key}", f"Weekly · {key.capitalize()}", constants.WEEK_S
        else:
            continue
        v = reply[field]
        if v is None or (isinstance(v, dict) and v.get("utilization") is None):
            continue                         # the account has no such limit
        if not isinstance(v, dict):
            raise ReplyChanged()
        meters.append(Meter(_unique(mid, taken), name, _pct(v["utilization"]), _reset(v.get("resets_at")), period))
    order = {"session": 0, "weekly": 1}
    return sorted(meters, key=lambda m: order.get(m.id, 2))


def _flags(reply: dict) -> List[str]:
    flags: List[str] = []
    extra = reply.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled") is True:
        flags.append(FLAG_CREDITS)
    limits = reply.get("limits")
    if isinstance(limits, list) and any(isinstance(e, dict) and e.get("severity") == "reset_available" for e in limits):
        flags.append(FLAG_RESET)
    return flags


def parse(reply: Any) -> ParsedReply:
    """The reply -> meters. `limits[]` first; the older five_hour / seven_day / seven_day_<model>
    fields are the fallback. Raises ReplyChanged for any other shape or an out-of-range percent."""
    if not isinstance(reply, dict):
        raise ReplyChanged()
    limits = reply.get("limits")
    meters = _from_limits(limits) if isinstance(limits, list) else []
    if not meters:
        meters = _from_legacy(reply)
    if not meters:
        raise ReplyChanged()
    return ParsedReply(meters=meters, flags=_flags(reply), no_session_meter=all(m.id != "session" for m in meters))
