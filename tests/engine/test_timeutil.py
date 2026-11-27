"""E28 time: ISO 8601 with an offset is the only time that crosses a boundary."""
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ju import timeutil


class TzCase(unittest.TestCase):
    def set_tz(self, name):
        old = os.environ.get("TZ")
        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = name
        time.tzset()


class TimeutilTests(TzCase):
    def test_naive_refused(self):
        for s in ("2026-11-26T10:00:00", "2026-11-26 10:00:00", "2026-11-26T10:00:00.123456"):
            with self.subTest(s):
                with self.assertRaises(timeutil.NaiveTime):
                    timeutil.parse_iso(s)
        with self.assertRaises(timeutil.NaiveTime):
            timeutil.iso(datetime(2026, 11, 26, 10, 0, 0))
        with self.assertRaises(timeutil.NaiveTime):
            timeutil.minute_of_week(datetime(2026, 11, 26, 10, 0, 0))

    def test_naive_time_is_a_value_error(self):
        self.assertTrue(issubclass(timeutil.NaiveTime, ValueError))

    def test_garbage_is_a_value_error_not_naive(self):
        for s in ("", "yesterday", "2026-13-40T00:00:00+00:00", None, 5):
            with self.subTest(s):
                with self.assertRaises(ValueError) as cm:
                    timeutil.parse_iso(s)
                self.assertNotIsInstance(cm.exception, timeutil.NaiveTime)

    def test_z_and_offset_equal_instants(self):
        a = timeutil.parse_iso("2026-11-26T20:00:00Z")
        b = timeutil.parse_iso("2026-11-26T12:00:00-08:00")
        c = timeutil.parse_iso("2026-11-27T01:30:00+05:30")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertIsNotNone(a.tzinfo)
        self.assertEqual(a.utcoffset(), timedelta(0))
        self.assertEqual(b.utcoffset(), timedelta(hours=-8))

    def test_fractional_seconds_any_length(self):
        base = timeutil.parse_iso("2026-11-26T20:00:00+00:00")
        for frac, micro in ((".5", 500000), (".12", 120000), (".1234", 123400),
                            (".123456", 123456), (".1234567", 123456)):
            with self.subTest(frac):
                t = timeutil.parse_iso(f"2026-11-26T20:00:00{frac}+00:00")
                self.assertEqual(t - base, timedelta(microseconds=micro))

    def test_iso_is_local_seconds_with_offset(self):
        self.set_tz("America/Los_Angeles")
        t = datetime(2026, 11, 26, 20, 0, 0, 999999, tzinfo=timezone.utc)
        self.assertEqual(timeutil.iso(t), "2026-11-26T12:00:00-08:00")
        t = datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(timeutil.iso(t), "2026-07-01T13:00:00-07:00")

    def test_iso_round_trips_through_parse(self):
        self.set_tz("Asia/Kolkata")
        t = timeutil.parse_iso("2026-11-26T20:00:00Z")
        s = timeutil.iso(t)
        self.assertEqual(s, "2026-11-27T01:30:00+05:30")
        self.assertEqual(timeutil.parse_iso(s), t)

    def test_now_is_aware_and_patchable(self):
        n = timeutil.now()
        self.assertIsNotNone(n.tzinfo)
        self.assertLess(abs((n - datetime.now(timezone.utc)).total_seconds()), 5)
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with mock.patch.object(timeutil, "now", return_value=fixed):
            self.assertEqual(timeutil.now(), fixed)

    def test_minute_of_week_known_values(self):
        # Monday 00:00 UTC = 0; Tuesday 14:00 UTC = 1*1440 + 14*60 = 2280; last minute = 10079.
        self.assertEqual(timeutil.minute_of_week(timeutil.parse_iso("2026-11-23T00:00:00Z")), 0)
        self.assertEqual(timeutil.minute_of_week(timeutil.parse_iso("2026-11-24T14:00:00Z")), 2280)
        self.assertEqual(timeutil.minute_of_week(timeutil.parse_iso("2026-11-29T23:59:00Z")), 10079)

    def test_minute_of_week_same_instant_any_offset(self):
        a = timeutil.minute_of_week(timeutil.parse_iso("2026-11-24T14:00:00Z"))
        b = timeutil.minute_of_week(timeutil.parse_iso("2026-11-24T06:00:00-08:00"))
        c = timeutil.minute_of_week(timeutil.parse_iso("2026-11-24T19:30:00+05:30"))
        self.assertEqual((a, b, c), (2280, 2280, 2280))

    def test_minute_of_week_dst_safe(self):
        # Weekly resets one week apart straddle the US fall-back (2026-11-01, Los Angeles).
        # The reset is one instant per week, so the anchor must not move by an hour.
        self.set_tz("America/Los_Angeles")
        before = timeutil.parse_iso("2026-10-27T14:00:00Z")
        after = timeutil.parse_iso("2026-11-03T14:00:00Z")
        self.assertEqual(after - before, timedelta(days=7))
        self.assertNotEqual(before.astimezone().utcoffset(), after.astimezone().utcoffset())
        self.assertEqual(timeutil.minute_of_week(before), timeutil.minute_of_week(after))

    def test_minute_of_week_ignores_sub_minute(self):
        a = timeutil.parse_iso("2026-11-24T14:00:59Z")
        self.assertEqual(timeutil.minute_of_week(a), 2280)


if __name__ == "__main__":
    unittest.main()
