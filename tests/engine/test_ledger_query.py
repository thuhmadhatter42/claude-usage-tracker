"""E9 queries: dates, per-day totals, 5-hour blocks, windows, months, rankings."""
import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from ju import ledger_db, ledger_query, pricing, timeutil

from tests.engine import support

PRICES = {
    "_multipliers": {"cache_5m": 1.25, "cache_1h": 2.0},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2},
}


class QueryCase(unittest.TestCase):
    def setUp(self):
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)
        self.home = support.temp_home()
        self.addCleanup(self.home.cleanup)
        self.conn = ledger_db.open_db(self.home.state, log=lambda *a, **k: None)
        self.addCleanup(self.conn.close)
        pf = self.home.home / "pricing.json"
        pf.write_text(json.dumps(PRICES))
        self.pricing = pricing.load(pf)
        self.n = 0

    def add(self, ts, account="/a/.claude", model="claude-opus-5", project="demo", output=1000, input=0,
            cache_5m=0, cache_1h=0, cache_read=0):
        self.n += 1
        day = ts[:10]
        ledger_db.insert_record(self.conn, ledger_db.Row(
            key=f"k{self.n}", ts=ts, day=day, account=account, model=model, project=project, session="s",
            input=input, cache_5m=cache_5m, cache_1h=cache_1h, cache_read=cache_read, output=output,
            stop_reason="end_turn", complete=1))

    def now(self, s):
        return timeutil.parse_iso(s)


class SinceTests(QueryCase):
    def test_since_rejects_garbage(self):
        for bad in ("yesterday", "7", "d", "0d", "-3d", "2026-13-01", "2026-1-1", "7days", "", " 7d", "1.5d",
                    "20261126", "2026-11-26T00:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    ledger_query.since_date(bad)
                self.assertEqual(str(cm.exception), f"bad date '{bad}'")

    def test_since_accepts(self):
        with mock.patch.object(timeutil, "now", return_value=self.now("2026-11-26T09:00:00-08:00")):
            self.assertEqual(ledger_query.since_date("7d"), "2026-11-20")
            self.assertEqual(ledger_query.since_date("1d"), "2026-11-26")
            self.assertEqual(ledger_query.since_date("30d"), "2026-10-28")
        self.assertEqual(ledger_query.since_date("2026-11-01"), "2026-11-01")
        self.assertIsNone(ledger_query.since_date("all"))
        self.assertIsNone(ledger_query.since_date(None))


class RowsTests(QueryCase):
    def test_rows_filter_and_order_by_instant(self):
        self.add("2026-11-01T01:30:00-07:00")                        # 08:30Z
        self.add("2026-11-01T01:10:00-08:00", account="/b/.claude")  # 09:10Z: later although text sorts earlier... 
        self.add("2026-11-02T10:00:00-08:00")
        rows = ledger_query.rows(self.conn, None, None)
        self.assertEqual([r["key"] for r in rows], ["k1", "k2", "k3"])
        self.assertEqual([r["key"] for r in ledger_query.rows(self.conn, "2026-11-02", None)], ["k3"])
        self.assertEqual([r["key"] for r in ledger_query.rows(self.conn, None, "/b/.claude")], ["k2"])


class TotalsTests(QueryCase):
    def test_totals_days_inclusive_cost_and_unpriced(self):
        self.add("2026-11-25T10:00:00-08:00", input=1_000_000, output=0)
        self.add("2026-11-26T10:00:00-08:00", input=1_000_000, cache_5m=1_000_000, cache_1h=1_000_000,
                 cache_read=1_000_000, output=1_000_000)
        self.add("2026-11-26T11:00:00-08:00", model="claude-mystery", input=7, output=3)
        self.add("2026-11-27T10:00:00-08:00", input=1_000_000, output=0)
        t = ledger_query.totals(self.conn, self.pricing, "2026-11-25", "2026-11-26")
        self.assertEqual(t.calls, 3)
        self.assertEqual(t.unpriced, 1)
        self.assertEqual(t.input, 2_000_007)
        self.assertEqual(t.output, 1_000_003)
        self.assertAlmostEqual(t.cost, 5.0 + (5.0 + 6.25 + 10.0 + 0.5 + 25.0))
        self.assertEqual(t.tokens, 2_000_007 + 1_000_000 + 1_000_000 + 1_000_000 + 1_000_003)

    def test_empty_range_is_zero(self):
        t = ledger_query.totals(self.conn, self.pricing, "2026-11-01", "2026-11-02")
        self.assertEqual((t.tokens, t.cost, t.calls, t.unpriced), (0, 0.0, 0, 0))


class BlocksTests(QueryCase):
    def rows(self, *stamps):
        for s in stamps:
            self.add(s)
        return ledger_query.rows(self.conn, None, None)

    def test_blocks_top_of_hour(self):
        bl = ledger_query.blocks(self.rows("2026-11-26T10:47:00-08:00", "2026-11-26T12:00:00-08:00",
                                           "2026-11-26T15:46:59-08:00"))
        self.assertEqual([timeutil.iso(b.start) for b in bl],
                         ["2026-11-26T10:00:00-08:00", "2026-11-26T15:00:00-08:00"])
        self.assertEqual([timeutil.iso(b.end) for b in bl],
                         ["2026-11-26T15:00:00-08:00", "2026-11-26T20:00:00-08:00"])
        self.assertEqual([len(b.rows) for b in bl], [2, 1])

    def test_block_opens_at_first_call_after_the_last_closed(self):
        self.conn.execute("DELETE FROM records")
        bl = ledger_query.blocks(self.rows("2026-11-26T10:47:00-08:00", "2026-11-26T15:00:00-08:00",
                                           "2026-11-26T15:30:00-08:00", "2026-11-26T20:30:00-08:00"))
        starts = [timeutil.iso(b.start)[11:16] for b in bl]
        self.assertEqual(starts, ["10:00", "15:00", "20:00"])
        self.assertEqual([len(b.rows) for b in bl], [1, 2, 1])

    def test_blocks_by_account_never_interleave(self):
        self.add("2026-11-26T10:10:00-08:00", account="/a")
        self.add("2026-11-26T13:00:00-08:00", account="/b")
        self.add("2026-11-26T16:30:00-08:00", account="/a")      # 6h20 after a's block opened: new block for a
        per = ledger_query.blocks_by_account(ledger_query.rows(self.conn, None, None))
        self.assertEqual(list(per), ["/a", "/b"])
        self.assertEqual([len(b.rows) for b in per["/a"]], [1, 1])
        self.assertEqual([len(b.rows) for b in per["/b"]], [1])

    def test_no_rows_no_blocks(self):
        self.assertEqual(ledger_query.blocks([]), [])

    def test_block_totals(self):
        bl = ledger_query.blocks(self.rows("2026-11-26T10:00:00-08:00", "2026-11-26T11:00:00-08:00"))
        t = ledger_query.sum_rows(bl[0].rows, self.pricing)
        self.assertEqual((t.calls, t.output), (2, 2000))


class WindowsTests(QueryCase):
    def test_latest_block_per_account_with_open_flag(self):
        self.add("2026-11-20T10:00:00-08:00", account="/a")
        self.add("2026-11-26T08:15:00-08:00", account="/a", output=2_000_000)
        self.add("2026-11-26T01:00:00-08:00", account="/b")
        w = ledger_query.windows(self.conn, self.pricing, self.now("2026-11-26T09:00:00-08:00"))
        self.assertEqual([x.account for x in w], ["/a", "/b"])
        a, b = w
        self.assertEqual(timeutil.iso(a.start), "2026-11-26T08:00:00-08:00")
        self.assertEqual(timeutil.iso(a.end), "2026-11-26T13:00:00-08:00")
        self.assertTrue(a.open)
        self.assertFalse(b.open)
        self.assertEqual(a.totals.output, 2_000_000)
        self.assertAlmostEqual(a.totals.cost, 50.0)

    def test_window_older_than_7_days_not_listed(self):
        self.add("2026-11-10T08:15:00-08:00", account="/old")
        w = ledger_query.windows(self.conn, self.pricing, self.now("2026-11-26T09:00:00-08:00"))
        self.assertEqual(w, [])


class MonthsTests(QueryCase):
    def test_months_three_newest_first(self):
        self.add("2026-11-03T10:00:00-08:00", project="a", output=300)
        self.add("2026-10-31T23:59:00-07:00", project="b", output=200, model="claude-sonnet-5")
        self.add("2026-09-01T00:00:00-07:00", project="c", output=100)
        self.add("2026-08-31T10:00:00-07:00", project="d", output=50)
        with mock.patch.object(timeutil, "now", return_value=self.now("2026-11-26T09:00:00-08:00")):
            m = ledger_query.months(self.conn, self.pricing)
        self.assertEqual([x.key for x in m], ["2026-11", "2026-10", "2026-09"])
        self.assertEqual([x.label for x in m], ["November", "October", "September"])
        self.assertEqual([x.totals.output for x in m], [300, 200, 100])
        self.assertEqual(m[1].models[0].name, "claude-sonnet-5")
        self.assertEqual(m[0].projects[0].name, "a")

    def test_months_cross_year(self):
        with mock.patch.object(timeutil, "now", return_value=self.now("2027-01-05T09:00:00-08:00")):
            m = ledger_query.months(self.conn, self.pricing, n=3)
        self.assertEqual([x.key for x in m], ["2027-01", "2026-12", "2026-11"])
        self.assertEqual(m[0].label, "January")

    def test_month_with_no_rows_is_present_and_zero(self):
        with mock.patch.object(timeutil, "now", return_value=self.now("2026-11-26T09:00:00-08:00")):
            m = ledger_query.months(self.conn, self.pricing, n=1)
        self.assertEqual((m[0].totals.tokens, m[0].models, m[0].projects), (0, [], []))


class RankTests(QueryCase):
    def test_subagents_group(self):
        self.assertEqual(ledger_query.project_group("agent-a1b2"), "subagents")
        self.assertEqual(ledger_query.project_group("agent-"), "subagents")
        self.assertEqual(ledger_query.project_group("demo"), "demo")
        self.assertEqual(ledger_query.project_group("my-agent-x"), "my-agent-x")
        self.add("2026-11-26T10:00:00-08:00", project="agent-aaa", output=100)
        self.add("2026-11-26T10:01:00-08:00", project="agent-bbb", output=200)
        self.add("2026-11-26T10:02:00-08:00", project="demo", output=250)
        top = ledger_query.top_projects(self.conn, self.pricing, "2026-11-26", "2026-11-26", 5)
        self.assertEqual([(r.name, r.totals.output) for r in top], [("subagents", 300), ("demo", 250)])

    def test_top_models_ordered_by_tokens_then_name_and_capped(self):
        self.add("2026-11-26T10:00:00-08:00", model="claude-sonnet-5", output=100)
        self.add("2026-11-26T10:01:00-08:00", model="claude-opus-5", output=100)
        self.add("2026-11-26T10:02:00-08:00", model="claude-opus-5", output=50)
        self.add("2026-11-26T10:03:00-08:00", model="claude-zzz", output=100)
        top = ledger_query.top_models(self.conn, self.pricing, "2026-11-26", "2026-11-26", 2)
        self.assertEqual([r.name for r in top], ["claude-opus-5", "claude-sonnet-5"])

    def test_rankings_respect_the_account_filter(self):
        self.add("2026-11-26T10:00:00-08:00", account="/a", project="x", output=10)
        self.add("2026-11-26T10:00:01-08:00", account="/b", project="y", output=99)
        top = ledger_query.top_projects(self.conn, self.pricing, "2026-11-26", "2026-11-26", 5, account="/a")
        self.assertEqual([r.name for r in top], ["x"])


if __name__ == "__main__":
    unittest.main()
