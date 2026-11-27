"""E18 session share: weekly percent gained inside each 5-hour session."""
import unittest
from datetime import datetime, timedelta, timezone

from ju import session_share as ss
from ju.session_share import Window

T0 = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc)
WEEK_END = T0 + timedelta(days=5)
WEEK_END_NEXT = WEEK_END + timedelta(days=7)


def at(h, m=0):
    return T0 + timedelta(hours=h, minutes=m)


def row(t, pct, resets=WEEK_END, meter="weekly"):
    return {"ts": t.isoformat(), "account": "a", "meter": meter, "pct": pct,
            "resets_at": resets.isoformat(), "source": "service"}


def win(h0, h1, open=False):
    return Window(at(h0), at(h1), open)


class ShareCase(unittest.TestCase):
    def test_delta_within_session(self):
        rows = [row(at(0, 5), 10.0), row(at(2), 12.5), row(at(4, 50), 14.0)]
        r = ss.session_share(rows, [win(0, 5)], None)
        self.assertEqual([i.pct for i in r.list], [4.0])
        self.assertEqual(r.list[0].start, at(0))

    def test_needs_two_readings(self):
        r = ss.session_share([row(at(1), 10.0)], [win(0, 5)], None)
        self.assertIsNone(r.list[0].pct)
        self.assertIsNone(r.last)
        self.assertIsNone(r.median7)

    def test_readings_outside_session_ignored(self):
        rows = [row(at(-1), 1.0), row(at(1), 10.0), row(at(2), 11.0), row(at(6), 40.0)]
        self.assertEqual(ss.session_share(rows, [win(0, 5)], None).list[0].pct, 1.0)

    def test_other_meters_ignored(self):
        rows = [row(at(1), 10.0), row(at(2), 12.0), row(at(1), 90.0, meter="session"),
                row(at(3), 95.0, meter="session")]
        self.assertEqual(ss.session_share(rows, [win(0, 5)], None).list[0].pct, 2.0)

    def test_crossing_weekly_reset_excluded(self):
        rows = [row(at(1), 95.0), row(at(2), 98.0), row(at(3), 1.0, WEEK_END_NEXT),
                row(at(4), 3.0, WEEK_END_NEXT)]
        self.assertIsNone(ss.session_share(rows, [win(0, 5)], None).list[0].pct)

    def test_reset_time_jitter_is_same_period(self):
        rows = [row(at(1), 10.0, WEEK_END), row(at(2), 12.0, WEEK_END + timedelta(seconds=90))]
        self.assertEqual(ss.session_share(rows, [win(0, 5)], None).list[0].pct, 2.0)

    def test_weekly_period_filter(self):
        rows = [row(at(1), 10.0), row(at(2), 12.0), row(at(6), 1.0, WEEK_END_NEXT),
                row(at(7), 2.0, WEEK_END_NEXT)]
        r = ss.session_share(rows, [win(0, 5), win(5, 10)], WEEK_END_NEXT)
        self.assertEqual([i.pct for i in r.list], [None, 1.0])

    def test_negative_delta_refused(self):
        rows = [row(at(1), 12.0), row(at(2), 10.0)]
        self.assertIsNone(ss.session_share(rows, [win(0, 5)], None).list[0].pct)

    def test_ultra_uses_transcript_blocks(self):
        blocks = [win(0, 5), win(5, 10)]
        rows = [row(at(1), 10.0), row(at(4), 13.0), row(at(6), 14.0), row(at(9), 20.0)]
        r = ss.session_share(rows, blocks, None)
        self.assertEqual([i.pct for i in r.list], [3.0, 6.0])

    def test_current_and_last(self):
        rows = [row(at(1), 10.0), row(at(4), 15.0), row(at(6), 16.0), row(at(7), 20.0)]
        r = ss.session_share(rows, [win(0, 5), win(5, 10, open=True)], None)
        self.assertEqual((r.current, r.last), (4.0, 5.0))

    def test_closed_latest_session_has_no_current(self):
        rows = [row(at(1), 10.0), row(at(4), 15.0)]
        r = ss.session_share(rows, [win(0, 5)], None)
        self.assertEqual((r.current, r.last), (None, 5.0))

    def test_median_of_last_7(self):
        pcts = [1, 2, 3, 100, 5, 6, 7, 8, 9]    # the oldest two fall off; median of 3,100,5,6,7,8,9 = 7
        rows, wins, base = [], [], 0.0
        for i, p in enumerate(pcts):
            rows += [row(at(i * 5 + 1), base), row(at(i * 5 + 2), base + p)]
            base += p
            wins.append(win(i * 5, i * 5 + 5))
        r = ss.session_share(rows, wins, None)
        self.assertEqual(r.median7, 7.0)
        self.assertEqual(len(r.list), 9)

    def test_median_even_count(self):
        rows = [row(at(1), 0.0), row(at(2), 2.0), row(at(6), 2.0), row(at(7), 6.0)]
        r = ss.session_share(rows, [win(0, 5), win(5, 10)], None)
        self.assertEqual(r.median7, 3.0)

    def test_none_without_sessions_or_weekly_readings(self):
        self.assertIsNone(ss.session_share([row(at(1), 1.0)], [], None))
        self.assertIsNone(ss.session_share([], [win(0, 5)], None))

    def test_sessions_sorted_by_start(self):
        rows = [row(at(1), 10.0), row(at(2), 11.0), row(at(6), 11.0), row(at(7), 14.0)]
        r = ss.session_share(rows, [win(5, 10), win(0, 5)], None)
        self.assertEqual([i.pct for i in r.list], [1.0, 3.0])

    def test_feed_shape(self):
        rows = [row(at(1), 10.0), row(at(4), 15.0)]
        d = ss.session_share(rows, [win(0, 5)], None).to_feed()
        self.assertEqual(set(d), {"current", "last", "median7", "list", "as_of"})
        self.assertEqual(d["list"], [{"start": at(0).astimezone().isoformat(timespec="seconds"), "pct": 5.0}])
        self.assertEqual(d["as_of"], at(4).astimezone().isoformat(timespec="seconds"))

    def test_naive_row_time_refused(self):
        from ju.timeutil import NaiveTime
        bad = {"ts": "2026-11-20T01:00:00", "meter": "weekly", "pct": 1.0, "resets_at": WEEK_END.isoformat()}
        with self.assertRaises(NaiveTime):
            ss.session_share([bad], [win(0, 5)], None)


if __name__ == "__main__":
    unittest.main()
