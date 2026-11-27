"""Builders for the dashboard tests (D1-D5): report v3 dicts as `share.load_reports` hands them to
the page builder, and feed-shaped meter blocks. One place, so every dash test speaks one shape."""
import unittest
from datetime import datetime, timedelta, timezone

from ju.timeutil import iso

PST = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 26, 21, 40, tzinfo=PST)
RULES = {"stale_after_s": 900, "red_at": 97, "warmup_e": 0.03, "read_every_s": 60}
U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"


def at(**delta) -> str:
    return iso(NOW + timedelta(**delta))


def meter(mid="session", pct=37.0, *, read_ago_s=60, resets_in_s=9000, period_s=None, state="green",
          landing=74.0, e=0.5, forecast=True, runs_out=None, reset_due=False, words=None, split=None):
    period_s = period_s or (18000 if mid == "session" else 604800)
    name = {"session": "Session", "weekly": "Weekly"}.get(mid, "Weekly · Fable")
    return {"id": mid, "name": name, "pct": pct, "resets_at": at(seconds=resets_in_s),
            "read_at": at(seconds=-read_ago_s), "source": "service", "period_s": period_s,
            "disagree": False,
            "pace": {"e": e, "landing": landing, "state": state, "forecast": forecast,
                     "runs_out": runs_out, "reset_due": reset_due,
                     "words": words or "On pace: about 75% by the reset."},
            "split": split}


def account(uuid=U1, email="sam@example.com", number=1, meters=None, error=None, session_share=None):
    return {"key": f"/abs/{number}", "number": number, "email": email, "uuid": uuid,
            "folders": [f"/abs/{number}"], "weekly_reset": None, "flags": [], "warning": None,
            "error": error, "no_session_meter": False,
            "meters": meters if meters is not None else [meter()],
            "session_share": session_share, "missing_people": []}


def day(tokens=1000, cost=1.0, calls=3, models=None, projects=None, accounts=None, unpriced=0):
    q = tokens // 4
    return {"input": q, "cache_5m": q // 2, "cache_1h": q - q // 2, "cache_read": q,
            "output": tokens - 3 * q, "calls": calls, "cost": cost, "unpriced": unpriced,
            "models": models if models is not None else
            {"claude-opus-5": {"input": q, "cache_5m": q // 2, "cache_1h": q - q // 2,
                               "cache_read": q, "output": tokens - 3 * q, "tokens": tokens,
                               "calls": calls, "cost": cost}},
            "accounts": accounts if accounts is not None else {U1: {"tokens": tokens, "cost": cost}},
            "projects": projects if projects is not None else {}}


def report(user="Sam", host="mac", install_id="a1b2", gen_ago_s=60, live=None, days=None,
           accounts=None, blocks=None, legacy=False):
    live = {U1: account()} if live is None else live
    if accounts is None:
        accounts = [{"key_hash": "h" + u[:4], "number": b["number"], "email": b["email"], "uuid": u}
                    for u, b in live.items()]
    r = {"report_version": 3, "user": user, "host": host, "install_id": install_id,
         "tool_version": "0.2.0", "generated": at(seconds=-gen_ago_s), "accounts": accounts,
         "live": live,
         "days": days if days is not None else {NOW.date().isoformat(): day()},
         "blocks": blocks if blocks is not None else {}, "session_share": None}
    if legacy:
        r["legacy"] = True
    return r


class FixtureShape(unittest.TestCase):
    def test_report_carries_v3_keys(self):
        r = report()
        for k in ("report_version", "user", "host", "install_id", "generated", "accounts", "live", "days", "blocks"):
            self.assertIn(k, r)
        self.assertEqual(r["accounts"][0]["uuid"], U1)
