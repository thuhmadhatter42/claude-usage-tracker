"""E17 pace: forecast, colour, runs-out. The fixture is the truth table."""
import unittest
from datetime import datetime, timedelta, timezone

from ju import formats, pace
from ju.contracts import Meter
from tests.engine import support

NOW = datetime(2026, 11, 26, 9, 0, 0, tzinfo=timezone.utc)
FX = support.load_fixture("pace_rows.json")
PERIOD = FX["rules"]["periods_s"]


def meter(used, t_s, L, now=NOW):
    return Meter("session" if L == 18000 else "weekly", "x", used, now + timedelta(seconds=L - t_s), L)


class PaceTable(unittest.TestCase):
    def test_21_sim_rows(self):
        self.assertEqual(len(FX["rows"]), 35)
        for r in FX["rows"]:
            L = PERIOD[r["window"]]
            p = pace.pace(meter(r["used"], r["t_s"], L), NOW)
            x = r["expect"]
            with self.subTest(r["id"]):
                self.assertEqual(p.state, x["state"])
                self.assertEqual(p.forecast, x["forecast"])
                self.assertEqual(p.reset_due, x["reset_due"])
                if x["landing"] is None:
                    self.assertIsNone(p.landing)
                else:
                    self.assertAlmostEqual(p.landing, x["landing"], delta=FX["landing_tol"])
                if x["runs_out_in_s"] is None:
                    self.assertIsNone(p.runs_out)
                else:
                    self.assertEqual(int((p.runs_out - NOW).total_seconds()), x["runs_out_in_s"])


def run(used, t_s, L=18000):
    return pace.pace(meter(used, t_s, L), NOW)


class PaceRules(unittest.TestCase):
    def test_exactly_on_pace_never_amber_or_red(self):
        for t in (540, 900, 1800, 3600, 9000, 14400):
            used = 100 * t / 18000
            self.assertEqual(run(used, t).state, "green", t)
        self.assertEqual(run(50, 9000).state, "green")
        self.assertEqual(run(25, 151200, 604800).state, "green")

    def test_no_forecast_first_3pct(self):
        p = run(5, 539)
        self.assertFalse(p.forecast)
        self.assertIsNone(p.landing)
        self.assertEqual(p.words, "Too early to forecast")

    def test_burst_8pct_never_runs_out_before_30min(self):
        for t in range(0, 1800, 60):
            self.assertIsNone(run(8, t).runs_out, t)

    def test_1_5x_amber_by_min_30(self):
        self.assertEqual(run(15, 1800).state, "amber")   # 15% used, 10% of the period gone

    def test_reset_passed_no_forecast(self):
        m = Meter("weekly", "w", 40.0, NOW - timedelta(seconds=1), 604800)
        p = pace.pace(m, NOW)
        self.assertTrue(p.reset_due)
        self.assertFalse(p.forecast)
        self.assertIsNone(p.state)
        self.assertIsNone(p.landing)
        self.assertIsNone(p.runs_out)
        self.assertEqual(p.words, formats.RESET_DUE_TEXT)

    def test_red_96_9_vs_97_0(self):
        self.assertEqual(run(96.9, 17000).state, "amber")
        self.assertEqual(run(97.0, 17000).state, "red")
        self.assertEqual(run(97.0, 100).state, "red")

    def test_amber_boundary_white_line(self):
        self.assertEqual(run(50.0, 9000).state, "green")
        self.assertEqual(run(50.1, 9000).state, "amber")

    def test_runs_out_landing_104_vs_105(self):
        # t = L/2, so landing = 2 * used
        self.assertIsNone(run(52.4, 9000).runs_out)
        p = run(52.5, 9000)
        self.assertEqual(p.landing, 105.0)
        self.assertEqual(p.runs_out, NOW + timedelta(seconds=int(9000 * 47.5 / 52.5)))
        # exact rational edge: 105% from floats that are not exact in binary
        self.assertIsNotNone(run(21.0, 3600).runs_out)   # 21 * 5 = 105
        self.assertIsNone(run(20.9, 3600).runs_out)      # 104.5

    def test_runs_out_never_changes_colour(self):
        a, b = run(52.5, 9000), run(52.4, 9000)
        self.assertEqual((a.state, b.state), ("amber", "amber"))
        self.assertEqual(run(20, 3600).state, "green")   # landing 100, no runs-out
        self.assertEqual(run(30, 3600).state, "amber")   # landing 150, runs-out inside period

    def test_stale_meter_has_no_colour(self):
        m = meter(40, 9000, 18000)
        old = NOW - timedelta(seconds=901)
        self.assertIsNone(pace.pace(m, NOW, read_at=old).state)
        self.assertIsNone(pace.pace(m, NOW, read_at=NOW - timedelta(seconds=900)).state)    # RULING 33: >= 900 s
        self.assertEqual(pace.pace(m, NOW, read_at=NOW - timedelta(seconds=899)).state, "green")

    def test_words(self):
        self.assertEqual(run(30, 9000).words, "On pace: about 60% by the reset.")
        self.assertEqual(run(52.5, 9000).words,
                         "At this pace you hit the limit {runs_out}, 14m before the reset.")
        self.assertNotRegex(run(52.5, 9000).words.replace("{runs_out}", ""), r"\d\d:\d\d")

    def test_huge_landing_always_has_a_runs_out_line(self):
        p = pace.pace(meter(90, 1800, 18000), NOW)    # landing 630
        self.assertTrue(p.words.startswith("At this pace you hit the limit {runs_out}"))
        self.assertIsNotNone(p.runs_out)

    def test_no_reset_time(self):
        p = pace.pace(Meter("weekly", "w", 98.0, None, 604800), NOW)
        self.assertEqual((p.state, p.forecast, p.reset_due), ("red", False, False))

    def test_naive_now_refused(self):
        from ju.timeutil import NaiveTime
        with self.assertRaises(NaiveTime):
            pace.pace(meter(10, 5000, 18000), datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
