"""E6: prices. USD per million tokens from the bundled pricing.json; the cache-write multipliers
live in that file (`_multipliers`), never in code. A model with no price is unpriced (None), and
every caller counts and flags it: unpriced calls are never silently $0 in a total."""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ju.constants import COLS

MULTIPLIER_KEYS = ("cache_5m", "cache_1h")
RATE_KEYS = ("input", "output", "cache_read")


class PricingInvalid(ValueError):
    """pricing.json is unreadable or wrong. `key` names the entry at fault."""

    def __init__(self, key: str, why: str):
        super().__init__(f"pricing.json: {key}: {why}")
        self.key = key


@dataclass(frozen=True)
class Rate:
    input: float
    output: float
    cache_read: float


@dataclass(frozen=True)
class Pricing:
    rates: Dict[str, Rate]
    multipliers: Dict[str, float]


_cache: Dict[tuple, Pricing] = {}


def _number(key: str, field: str, v, *, positive: bool) -> float:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if not ok or v < 0 or (positive and v <= 0):
        raise PricingInvalid(key, f"{field} must be a {'positive' if positive else 'non-negative'} number, got {v!r}")
    return float(v)


def load(path) -> Pricing:
    """Read and validate once per process: the parsed table is reused until the file changes."""
    path = Path(path)
    try:
        st = path.stat()
        stamp = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    except OSError as e:
        raise PricingInvalid(str(path), f"cannot read ({e.strerror})") from None
    if stamp in _cache:
        return _cache[stamp]
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PricingInvalid(str(path), f"cannot read ({e})") from None
    if not isinstance(table, dict):
        raise PricingInvalid(str(path), "top level must be an object")
    mult = table.get("_multipliers")
    if not isinstance(mult, dict):
        raise PricingInvalid("_multipliers", "missing")
    multipliers = {}
    for k in MULTIPLIER_KEYS:
        if k not in mult:
            raise PricingInvalid("_multipliers", f"{k} missing")
        multipliers[k] = _number("_multipliers", k, mult[k], positive=True)
    rates = {}
    for model, entry in table.items():
        if model.startswith("_"):
            continue
        if not isinstance(entry, dict):
            raise PricingInvalid(model, "must be an object with input, output, cache_read")
        for f in RATE_KEYS:
            if f not in entry:
                raise PricingInvalid(model, f"{f} missing")
        rates[model] = Rate(**{f: _number(model, f, entry[f], positive=False) for f in RATE_KEYS})
    p = Pricing(rates, multipliers)
    _cache[stamp] = p
    return p


def rate_for(p: Pricing, model: str) -> Optional[Rate]:
    """The rate of the longest model-id prefix that matches, or None (unpriced)."""
    best = None
    for key in p.rates:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return p.rates[best] if best is not None else None


def _get(row, k):
    return row[k] if isinstance(row, (Mapping, sqlite3.Row)) else getattr(row, k)


def cost_usd(p: Pricing, row) -> Optional[float]:
    """API-rate dollars for one ledger row (a Row, sqlite3.Row or mapping), or None when the
    model is unpriced."""
    r = rate_for(p, _get(row, "model"))
    if r is None:
        return None
    n = {c: _get(row, c) for c in COLS}
    return (n["input"] * r.input
            + n["cache_5m"] * r.input * p.multipliers["cache_5m"]
            + n["cache_1h"] * r.input * p.multipliers["cache_1h"]
            + n["cache_read"] * r.cache_read
            + n["output"] * r.output) / 1e6
