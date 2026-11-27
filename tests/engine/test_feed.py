"""E21 feed: build_feed gives the exact §1.7 shape (tests/fixtures/feed-v2.json), accounts by
number, every time with an offset, a meter's source and disagreement, and nothing filtered."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from tests.engine import support
from ju import accounts, feed, ledger_db, pricing as pricing_mod, timeutil
from ju.config import ConfigStore
from ju.constants import REPO_ROOT, STALE_AFTER_S
from ju.contracts import Meter, Reading
from ju.gate import AccountBlock, RefreshResult
from ju.live_store import AccountLive, ErrorInfo, Live

PST = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 26, 21, 40, tzinfo=PST)
REPLY = support.load_fixture("reply-limits.json")
GOLDEN = support.load_fixture("feed-v2.json")


def write_identity(folder, email, uuid):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": email, "accountUuid": uuid}}))
    (folder / "projects").mkdir(exist_ok=True)


class Scenario:
    """A temp HOME with ~/.claude (#1), ~/.claude-newaccount (#2) and ~/.claude-aaa (#3)."""

    def __init__(self, test):
        self.h = support.temp_home()
        test.addCleanup(self.h.cleanup)
        home = self.h.home
        self.k1 = accounts.folder_key(home / ".claude")
        self.k2 = accounts.folder_key(home / ".claude-newaccount")
        self.k3 = accounts.folder_key(home / ".claude-aaa")
        write_identity(home / ".claude", "sam@example.com", "u-1")
        write_identity(home / ".claude-newaccount", "pat@example.com", "u-2")
        write_identity(home / ".claude-aaa", "kim@example.com", "u-3")
        with ConfigStore.edit(self.h.state) as cfg:
            cfg.user = "Sam"
            cfg.install_id = "a1b2"
            for k in (self.k1, self.k2, self.k3):
                accounts.number_for(cfg, k)
            cfg.statusline = {self.k2: {"enabled": True, "original": None, "wrapped": "x"}}
        self.cfg = ConfigStore.load(self.h.state)
        self.conn = ledger_db.open_db(self.h.state, [self.k1, self.k2, self.k3])
        test.addCleanup(self.conn.close)
        self.pricing = pricing_mod.load(REPO_ROOT / "engine" / "pricing.json")

    def block(self, key, state, uuid):
        return AccountBlock(key, [key], uuid, state)

    def refresh(self, blocks):
        live = Live({b.key: b.state for b in blocks})
        return RefreshResult(live=live, accounts=blocks, fetched=[], next_due=NOW + timedelta(seconds=60))

    def build(self, blocks, **kw):
        args = dict(cfg=self.cfg, refresh=self.refresh(blocks), conn=self.conn, pricing=self.pricing,
                    now=NOW, version="0.2.0", statusline_view=lambda b, others, now: feed.NO_CAPTURE,
                    split_view=lambda m, uuid, now: feed.NO_SPLIT, people=[], notes=[],
                    share={"written_at": None, "error": None}, dashboard=None)
        args.update(kw)
        return feed.build_feed(**args)


def fresh(reply=REPLY, at=NOW - timedelta(minutes=1)):
    return AccountLive(fetched=at, data=reply, identity_uuid="u")


def _shape(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    return "object"


def same_shape(test, ours, golden, path="feed"):
    """Same keys at every object level, same JSON type wherever both sides hold a value
    (None stands for the schema's |null). Lists compare their first elements."""
    if isinstance(golden, dict) and isinstance(ours, dict) and path not in ("feed.statusline",):
        test.assertEqual(sorted(ours), sorted(golden), f"keys differ at {path}")
        for k in golden:
            same_shape(test, ours[k], golden[k], f"{path}.{k}")
        return
    a, b = _shape(ours), _shape(golden)
    if a is not None and b is not None:
        test.assertEqual(a, b, f"type differs at {path}: {ours!r} vs {golden!r}")
    if isinstance(golden, list) and isinstance(ours, list) and golden and ours:
        same_shape(test, ours[0], golden[0], f"{path}[0]")


class GoldenShape(unittest.TestCase):
    def test_matches_golden_shape(self):
        s = Scenario(self)
        f = s.build([s.block(s.k1, fresh(), "u-1")],
                    split_view=lambda m, uuid, now: feed.SplitView(
                        {"period_start": timeutil.iso(NOW - timedelta(days=3)),
                         "parts": [{"user": "Sam", "install_id": "a1b2", "usd": 4.0, "share": 1.0}]}, []),
                    people=[GOLDEN["people"][0]], notes=["skipped x"], dashboard="/abs/dashboard.html",
                    share={"written_at": timeutil.iso(NOW), "error": None})
        json.dumps(f)                                   # JSON only, nothing exotic
        golden = dict(GOLDEN)
        same_shape(self, f, golden)
        self.assertEqual(f["schema"], 2)
        self.assertEqual(f["rules"], {"stale_after_s": 900, "red_at": 97.0, "warmup_e": 0.03, "read_every_s": 60})
        self.assertEqual(f["me"], {"user": "Sam", "install_id": "a1b2"})
        acct = f["accounts"][0]
        self.assertEqual([m["id"] for m in acct["meters"]], ["session", "weekly", "weekly:fable"])
        self.assertEqual(acct["meters"][0]["pct"], 37.0)
        self.assertEqual(acct["email"], "sam@example.com")
        self.assertEqual(acct["weekly_reset"], timeutil.iso(timeutil.parse_iso("2026-12-01T01:00:00+00:00")))

    def test_pace_comes_from_e17(self):
        s = Scenario(self)
        f = s.build([s.block(s.k1, fresh(), "u-1")])
        session = f["accounts"][0]["meters"][0]
        # session resets 00:10, 5 h period, now 21:40 -> e = 0.5, landing 74
        self.assertEqual(session["pace"]["e"], 0.5)
        self.assertEqual(session["pace"]["landing"], 74.0)
        self.assertEqual(session["pace"]["state"], "green")
        self.assertIsNone(session["pace"]["runs_out"])


class Order(unittest.TestCase):
    def test_accounts_ordered_by_number(self):
        s = Scenario(self)
        blocks = [s.block(s.k3, fresh(), "u-3"), s.block(s.k2, fresh(), "u-2"), s.block(s.k1, fresh(), "u-1")]
        f = s.build(blocks)
        self.assertEqual([a["number"] for a in f["accounts"]], [1, 2, 3])
        self.assertEqual([a["key"] for a in f["accounts"]], [s.k1, s.k2, s.k3])

    def test_group_keyed_by_lowest_number_and_lists_folders(self):
        s = Scenario(self)
        b = AccountBlock(s.k3, [s.k1, s.k3], "u-1", fresh())
        f = s.build([b])
        self.assertEqual(f["accounts"][0]["key"], s.k1)
        self.assertEqual(f["accounts"][0]["folders"], [s.k1, s.k3])
        self.assertEqual(set(f["statusline"]), {s.k1, s.k3})


ISO_KEYS = re.compile(r"(^|_)(at|generated|start|end|until|due|of|reset|out|period_start)$")


def walk(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, value


class Times(unittest.TestCase):
    def test_every_time_has_offset(self):
        s = Scenario(self)
        err = ErrorInfo("rate_limited", "Anthropic is rate-limiting usage reads", NOW)
        b2 = s.block(s.k2, AccountLive(fetched=NOW - timedelta(minutes=5), data=REPLY, error=err,
                                       blocked_until=NOW + timedelta(minutes=10)), "u-2")
        support.write_transcript(s.h.home / ".claude", [{"ts": "2026-11-26T20:00:00-08:00", "model": "claude-opus-5",
                                                         "input": 10, "output": 5}])
        from ju import ledger_scan
        ledger_scan.scan(s.conn, [s.k1])
        f = s.build([s.block(s.k1, fresh(), "u-1"), b2])
        times = 0
        for path, v in walk(f):
            leaf = path.rsplit(".", 1)[-1].split("[")[0]
            if isinstance(v, str) and ISO_KEYS.search(leaf) and leaf not in ("words",):
                timeutil.parse_iso(v)               # raises NaiveTime on a time with no offset
                self.assertRegex(v, r"[+-]\d\d:\d\d$", path)
                times += 1
        self.assertGreater(times, 10)

    def test_naive_time_in_live_is_refused(self):
        s = Scenario(self)
        bad = AccountLive(fetched=datetime(2026, 11, 26, 21, 0), data=REPLY)
        with self.assertRaises(timeutil.NaiveTime):
            s.build([s.block(s.k1, bad, "u-1")])


class Sources(unittest.TestCase):
    def test_source_and_disagree(self):
        s = Scenario(self)
        err = ErrorInfo("no_network", "No network", NOW - timedelta(minutes=2))
        failing = AccountLive(fetched=NOW - timedelta(minutes=30), data=REPLY, error=err,
                              blocked_until=NOW + timedelta(minutes=3))
        capture_at = NOW - timedelta(minutes=1)
        backup = Reading(folder=s.k2, fetched=capture_at, flags=[], uuid="u-2", source="statusline", meters=[
            Meter("session", "Session", 41.0, timeutil.parse_iso("2026-11-27T08:10:00+00:00"), 18000),
            Meter("weekly", "Weekly", 53.0, timeutil.parse_iso("2026-12-01T01:00:00+00:00"), 604800)])

        def view(block, others, now):
            if block.folders[0] == s.k2:
                return feed.StatuslineView(backup, None, frozenset(), capture_at)
            return feed.StatuslineView(None, None, frozenset({"weekly"}), capture_at)

        f = s.build([s.block(s.k1, fresh(), "u-1"), s.block(s.k2, failing, "u-2")], statusline_view=view)
        a1, a2 = f["accounts"]
        self.assertEqual([(m["id"], m["source"], m["disagree"]) for m in a1["meters"]],
                         [("session", "service", False), ("weekly", "service", True), ("weekly:fable", "service", False)])
        self.assertEqual([(m["id"], m["source"]) for m in a2["meters"]],
                         [("session", "statusline"), ("weekly", "statusline"), ("weekly:fable", "last_good")])
        self.assertEqual(a2["meters"][0]["pct"], 41.0)
        self.assertEqual(a2["meters"][0]["read_at"], timeutil.iso(capture_at))
        # the per-model weekly is 30 min old: past the staleness rule, no colour (RULING 9)
        self.assertIsNone(a2["meters"][2]["pace"]["state"])
        self.assertEqual(a2["error"], {"kind": "no_network", "text": "No network",
                                       "until": timeutil.iso(NOW + timedelta(minutes=3))})
        self.assertEqual(f["statusline"][s.k2], {"enabled": True, "capture_at": timeutil.iso(capture_at)})
        self.assertEqual(f["statusline"][s.k1]["enabled"], False)

    def test_last_good_under_15min_keeps_colour(self):
        s = Scenario(self)
        err = ErrorInfo("http", "Anthropic answered 500", NOW)
        acc = AccountLive(fetched=NOW - timedelta(seconds=STALE_AFTER_S - 60), data=REPLY, error=err)
        m = s.build([s.block(s.k1, acc, "u-1")])["accounts"][0]["meters"][0]
        self.assertEqual(m["source"], "last_good")
        self.assertEqual(m["pace"]["state"], "green")

    def test_split_and_missing_people_from_share_view(self):
        s = Scenario(self)
        split = {"period_start": timeutil.iso(NOW - timedelta(days=2)),
                 "parts": [{"user": "Sam", "install_id": "a1b2", "usd": 3.0, "share": 1.0}]}

        def sv(m, uuid, now):
            self.assertEqual(uuid, "u-1")
            return feed.SplitView(split if m.id == "weekly" else None, ["Lee"] if m.id == "weekly" else [])

        a = s.build([s.block(s.k1, fresh(), "u-1")], split_view=sv)["accounts"][0]
        self.assertEqual(a["meters"][1]["split"], split)
        self.assertIsNone(a["meters"][0]["split"])
        self.assertEqual(a["missing_people"], ["Lee"])


class WithCapture(unittest.TestCase):
    def test_capture_without_five_hour_keeps_the_session_meter(self):
        """Review-engine 5: a capture carrying only seven_day replaces weekly and keeps every other
        stored meter, the session one included."""
        acc = AccountLive(fetched=NOW - timedelta(minutes=5), data=REPLY, identity_uuid="u-1")
        cap = Reading(folder="/x", fetched=NOW, flags=[], uuid="u-1", source="statusline", meters=[
            Meter("weekly", "Weekly", 60.0, timeutil.parse_iso("2026-12-01T01:00:00+00:00"), 604800)])
        new = feed.with_capture(acc, cap)
        from ju import usage_client
        got = {m.id: m.pct for m in usage_client.parse(new.data).meters}
        self.assertEqual(got, {"session": 37.0, "weekly": 60.0, "weekly:fable": 18.0})
        self.assertFalse(usage_client.parse(new.data).no_session_meter)


class NothingDropped(unittest.TestCase):
    def test_hidden_nothing_dropped(self):
        s = Scenario(self)
        with ConfigStore.edit(s.h.state) as cfg:
            cfg["hidden"] = [s.k2]                       # an app-side idea; the engine never filters
        s.cfg = ConfigStore.load(s.h.state)
        no_login = AccountLive(error=ErrorInfo("login_missing", "No claude.ai login found", NOW))
        f = s.build([s.block(s.k1, fresh(), "u-1"), s.block(s.k2, no_login, "u-2"),
                     s.block(s.k3, AccountLive(), None)])
        self.assertEqual([a["number"] for a in f["accounts"]], [1, 2, 3])
        a2 = f["accounts"][1]
        self.assertEqual(a2["meters"], [])
        self.assertEqual(a2["error"]["kind"], "login_missing")
        self.assertIsNone(a2["error"]["until"])
        self.assertIsNone(f["accounts"][2]["error"])
        self.assertIsNone(f["accounts"][2]["weekly_reset"])


class Ledger(unittest.TestCase):
    def test_totals_models_projects_windows_months(self):
        s = Scenario(self)
        from ju import ledger_scan
        support.write_transcript(s.h.home / ".claude", [
            {"ts": "2026-11-26T20:00:00-08:00", "model": "claude-opus-5", "input": 1000, "output": 500},
            {"ts": "2026-11-26T20:30:00-08:00", "model": "claude-sonnet-5", "input": 100, "output": 50},
            {"ts": "2026-11-26T20:40:00-08:00", "model": "claude-unknown-9", "input": 1, "output": 1},
        ], cwd="/work/jusage")
        ledger_scan.scan(s.conn, [s.k1])
        f = s.build([s.block(s.k1, fresh(), "u-1")])
        self.assertEqual(f["totals"]["today"]["calls"], 3)
        self.assertEqual(f["totals"]["today"]["tokens"], 1652)
        self.assertEqual(f["totals"]["today"]["unpriced"], 1)
        self.assertEqual(f["totals"]["as_of"], timeutil.iso(NOW))
        self.assertEqual([m["name"] for m in f["models"]], ["opus-5", "sonnet-5", "unknown-9"])
        self.assertEqual(f["projects"][0]["name"], "jusage")
        self.assertEqual(f["projects"][0]["share"], 1.0)
        self.assertEqual(f["projects_today"], f["projects"])          # all of it today

    def test_projects_today_is_today_only(self):
        """RULINGS 48h: "Projects today" reads its own list; `projects` stays the week's."""
        s = Scenario(self)
        from ju import ledger_scan
        support.write_transcript(s.h.home / ".claude", [
            {"ts": "2026-11-24T10:00:00-08:00", "model": "claude-opus-5", "input": 9000, "output": 1}], cwd="/work/old")
        support.write_transcript(s.h.home / ".claude", [
            {"ts": "2026-11-26T20:00:00-08:00", "model": "claude-opus-5", "input": 100, "output": 1}],
            session="sess-2", cwd="/work/jusage")
        ledger_scan.scan(s.conn, [s.k1])
        f = s.build([s.block(s.k1, fresh(), "u-1")])
        self.assertEqual([p["name"] for p in f["projects"]], ["old", "jusage"])
        self.assertEqual([(p["name"], p["share"]) for p in f["projects_today"]], [("jusage", 1.0)])
        self.assertEqual(f["windows"][0]["account"], s.k1)
        self.assertTrue(f["windows"][0]["open"])
        self.assertEqual(len(f["months"]), 3)


class SessionShare(unittest.TestCase):
    def test_session_share_from_meter_log(self):
        s = Scenario(self)
        resets = "2026-11-27T00:10:00-08:00"           # current session 19:10 -> 00:10
        weekly = "2026-11-30T17:00:00-08:00"
        rows = [("2026-11-26T19:20:00-08:00", "weekly", 50.0, weekly), ("2026-11-26T21:30:00-08:00", "weekly", 52.0, weekly),
                ("2026-11-26T19:20:00-08:00", "session", 5.0, resets), ("2026-11-26T21:30:00-08:00", "session", 37.0, resets)]
        s.conn.executemany("INSERT INTO meter_log (ts, account, meter, pct, resets_at, source) VALUES (?,?,?,?,?,'service')",
                           [(t, s.k1, m, p, r) for t, m, p, r in rows])
        ss = s.build([s.block(s.k1, fresh(), "u-1")])["accounts"][0]["session_share"]
        self.assertEqual(ss["current"], 2.0)
        self.assertEqual(ss["list"][-1]["start"], timeutil.iso(timeutil.parse_iso("2026-11-26T19:10:00-08:00")))

    def test_no_readings_no_share(self):
        s = Scenario(self)
        self.assertIsNone(s.build([s.block(s.k1, fresh(), "u-1")])["accounts"][0]["session_share"])


class ForeignResetLabel(unittest.TestCase):
    def test_error_names_the_other_account(self):
        """RULING 31 / R-14: the feed fills in the other account's label."""
        s = Scenario(self)
        err = ErrorInfo("identity_foreign_reset", "This reading matches another account's reset", NOW, other=s.k2)
        f = s.build([s.block(s.k1, AccountLive(error=err), "u-1"), s.block(s.k2, fresh(), "u-2")])
        text = f["accounts"][0]["error"]["text"]
        self.assertEqual(text, "This reading matches pat@example.com's reset")


if __name__ == "__main__":
    unittest.main()
