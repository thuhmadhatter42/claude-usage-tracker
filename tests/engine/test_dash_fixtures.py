"""Builders for the dashboard tests (D1-D5): `share.Report` objects exactly as `share.load_reports`
hands them to the page builder (a v3 dict run through E20's own parser, RULING 35), and feed-shaped
meter blocks. One place, so every dash test speaks one shape."""
import unittest
from datetime import datetime, timedelta, timezone

from ju import share
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
    return {"key": f"acct:{uuid}", "number": number, "email": email, "uuid": uuid,
            "folders": [], "weekly_reset": None, "flags": [], "warning": None,
            "error": error, "no_session_meter": False,
            "meters": meters if meters is not None else [meter()],
            "session_share": session_share, "missing_people": []}


def day(tokens=1000, usd=1.0, calls=3, models=None, projects=None, accounts=None, unpriced=0):
    """One v3 day entry, as share.build_report writes it."""
    q = tokens // 4
    return {"input": q, "cache_5m": q // 2, "cache_1h": q - q // 2, "cache_read": q,
            "output": tokens - 3 * q, "calls": calls, "usd": usd, "unpriced": unpriced,
            "models": models if models is not None else
            {"claude-opus-5": {"input": q, "cache_5m": q // 2, "cache_1h": q - q // 2,
                               "cache_read": q, "output": tokens - 3 * q, "tokens": tokens,
                               "calls": calls, "usd": usd, "unpriced": 0}},
            "accounts": accounts if accounts is not None else {U1: {"tokens": tokens, "usd": usd}},
            "projects": projects if projects is not None else {}}


def report_dict(user="Sam", host="mac", install_id="a1b2", gen_ago_s=60, live=None, days=None,
                accounts=None, blocks=None):
    """A report v3 file body."""
    live = {U1: account()} if live is None else live
    if accounts is None:
        accounts = [{"number": b["number"], "email": b["email"], "uuid": u}
                    for u, b in live.items()]
    return {"report_version": 3, "user": user, "host": host, "install_id": install_id,
            "tool_version": "0.2.0", "generated": at(seconds=-gen_ago_s), "accounts": accounts,
            "live": live,
            "days": days if days is not None else {NOW.date().isoformat(): day()},
            "blocks": blocks if blocks is not None else {}, "session_share": {}}


def legacy_dict(user="Old", host="old-mac", install_id="e5f6", gen_ago_s=60, days=None):
    """A report from before report_version (0.1.21): days with `cost`, no account ids."""
    q = 250
    d = {"input": q, "cache_5m": 0, "cache_1h": 0, "cache_read": q, "output": q, "calls": 2, "cost": 0.5}
    return {"user": user, "host": host, "install_id": install_id, "tool_version": "0.1.21",
            "generated": at(seconds=-gen_ago_s), "days": days if days is not None else {NOW.date().isoformat(): d},
            "live": {"old@example.com": {"meters": []}}}


def report(legacy=False, **kw) -> "share.Report":
    """What the page builder receives: the file body parsed by E20 (share.load_reports' own parser)."""
    raw = legacy_dict(**kw) if legacy else report_dict(**kw)
    return share._parse_report(f"{raw['user']}@{raw['host']}-{raw['install_id']}.json", raw)


class FixtureShape(unittest.TestCase):
    def test_report_is_a_parsed_v3_report(self):
        r = report()
        self.assertIsInstance(r, share.Report)
        self.assertEqual((r.user, r.host, r.install_id, r.legacy), ("Sam", "mac", "a1b2", False))
        self.assertEqual(r.accounts[0]["uuid"], U1)
        self.assertTrue(report(legacy=True).legacy)
