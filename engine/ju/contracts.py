"""E0: the shared types and the closed set of error kinds. Types only: no behaviour lives here.
Every error kind a person can see is in ERROR_KINDS; live.json, the feed and the app banners
share that one set."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

ERROR_KINDS = frozenset({
    "login_expired", "login_missing", "keychain_waiting", "keychain_denied",
    "rate_limited", "no_network", "http", "reply_changed",
    "identity_changed", "identity_foreign_reset",
})
IDENTITY_KINDS = frozenset({"identity_changed", "identity_foreign_reset"})


@dataclass(frozen=True)
class Meter:
    id: str                       # session | weekly | weekly:<model>
    name: str
    pct: float
    resets_at: Optional[datetime]
    period_s: int


@dataclass(frozen=True)
class Reading:
    folder: str
    fetched: datetime
    meters: List[Meter]
    flags: List[str]
    uuid: Optional[str]
    source: str                   # service | statusline


@dataclass(frozen=True)
class Pace:
    e: float
    landing: Optional[float]
    state: str                    # green | amber | red
    forecast: bool
    runs_out: Optional[datetime]
    reset_due: bool
    words: str


@dataclass(frozen=True)
class Login:
    service: str
    token: str = field(repr=False)        # a token is never printed, logged or stored
    expires_at: Optional[datetime]
    legacy: bool


@dataclass(frozen=True)
class GateDecision:
    due: bool
    reason: str
    next_at: Optional[datetime]


class JusageError(Exception):
    """Base of every engine error. `kind` is always a member of ERROR_KINDS where one applies."""
    kind = ""


class LoginError(JusageError):
    def __init__(self, kind: str, text: str):
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown error kind {kind!r}")
        super().__init__(text)
        self.kind = kind
        self.text = text


class RateLimited(JusageError):
    kind = "rate_limited"

    def __init__(self, retry_after_s: Optional[int]):
        super().__init__("rate limited by Anthropic (429)")
        self.retry_after_s = retry_after_s


class ReplyChanged(JusageError):
    kind = "reply_changed"

    def __init__(self, text: str = "Anthropic changed the usage reply"):
        super().__init__(text)


class IdentityRejected(JusageError):
    def __init__(self, kind: str):
        if kind not in IDENTITY_KINDS:
            raise ValueError(f"unknown identity kind {kind!r}")
        super().__init__(kind)
        self.kind = kind


class BringUpRefused(JusageError):
    def __init__(self, line: str):
        super().__init__(line)
        self.line = line


class NetworkError(JusageError):
    kind = "no_network"

    def __init__(self, text: str = "No network"):
        super().__init__(text)


class HttpError(JusageError):
    kind = "http"

    def __init__(self, status: int):
        super().__init__(f"Anthropic answered {status}")
        self.status = status
