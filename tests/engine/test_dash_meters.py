"""D3 meters: the app's three meanings (lit = used, dim = forecast at reset, white line = time
gone), coloured by pace state for every lit segment, never by position."""
import re
import unittest

from ju import formats
from ju.dash import meters
from tests.engine.test_dash_fixtures import NOW, RULES, at, meter


def segs(html, cls):
    return len(re.findall(r'<i class="%s"></i>' % cls, html))


class Meters(unittest.TestCase):
    def test_colour_by_state_not_position(self):
        h = meters.meter_html(meter(pct=95.0, state="green", landing=96.0), NOW, RULES)
        self.assertIn('data-state="green"', h)
        self.assertEqual(segs(h, "u"), 23)            # round(95% of 24)
        self.assertNotRegex(h, r'class="[^"]*\b(w|h|hot|warn)\b')
        h = meters.meter_html(meter(pct=10.0, state="amber", landing=20.0, e=0.05), NOW, RULES)
        self.assertIn('data-state="amber"', h)
        self.assertEqual(segs(h, "u"), 2)

    def test_twenty_four_segments(self):
        h = meters.meter_html(meter(pct=25.0, landing=50.0), NOW, RULES)
        self.assertEqual(segs(h, "u") + segs(h, "f") + segs(h, ""), 24)

    def test_forecast_dim_to_landing_and_white_line_at_e(self):
        h = meters.meter_html(meter(pct=25.0, landing=50.0, e=0.5), NOW, RULES)
        self.assertEqual(segs(h, "u"), 6)
        self.assertEqual(segs(h, "f"), 6)
        self.assertIn('class="tick" style="left:50.0%"', h)

    def test_no_forecast_no_dim(self):
        h = meters.meter_html(meter(pct=2.0, landing=None, forecast=False, e=0.01,
                                    words="Too early to forecast"), NOW, RULES)
        self.assertEqual(segs(h, "f"), 0)
        self.assertIn("Too early to forecast", h)

    def test_reset_due_text(self):
        m = meter(pct=88.0, resets_in_s=-60, state=None, landing=None, forecast=False,
                  reset_due=True, words=formats.RESET_DUE_TEXT)
        h = meters.meter_html(m, NOW, RULES)
        self.assertIn(formats.RESET_DUE_TEXT, h)
        self.assertIn('data-state="none"', h)
        self.assertNotIn('class="tick"', h)           # the reading belongs to the finished period
        self.assertNotIn("0h 00m", h)

    def test_data_attributes_present(self):
        m = meter()
        h = meters.meter_html(m, NOW, RULES)
        self.assertIn('data-read-at="%s"' % m["read_at"], h)
        self.assertIn('data-reset="%s"' % m["resets_at"], h)
        self.assertIn('data-state="green"', h)

    def test_first_paint_countdown_uses_given_now(self):
        h = meters.meter_html(meter(resets_in_s=7500), NOW, RULES)
        self.assertIn("in 2h 05m", h)

    def test_stale_at_fifteen_minutes(self):
        self.assertNotIn(" stale", meters.meter_html(meter(read_ago_s=899), NOW, RULES))
        self.assertIn('class="meter stale"', meters.meter_html(meter(read_ago_s=900), NOW, RULES))

    def test_stale_window_comes_from_rules(self):
        h = meters.meter_html(meter(read_ago_s=120), NOW, dict(RULES, stale_after_s=60))
        self.assertIn('class="meter stale"', h)

    def test_rules_without_stale_after_refused(self):
        with self.assertRaises(KeyError):
            meters.meter_html(meter(), NOW, {"red_at": 97})

    def test_runs_out_placeholder_filled_with_duration_first_paint(self):
        m = meter(pct=60.0, state="amber", landing=130.0, runs_out=at(hours=1),
                  words="At this pace you hit the limit {runs_out}, 1h 30m before the reset.")
        h = meters.meter_html(m, NOW, RULES)
        self.assertIn("hit the limit in 1h 00m,", h)
        self.assertIn('data-runs-out="%s"' % m["pace"]["runs_out"], h)
        self.assertIn("data-words=\"At this pace you hit the limit {runs_out}", h)

    def test_split_shares_and_api_rate_hover(self):
        sp = {"period_start": at(days=-3), "parts": [
            {"user": "Sam", "install_id": "a1b2", "usd": 412.0, "share": 0.71},
            {"user": "Lee", "install_id": "c3d4", "usd": 168.2, "share": 0.29}]}
        h = meters.meter_html(meter("weekly", split=sp), NOW, RULES)
        self.assertIn("Sam 71%", h)
        self.assertIn("Lee 29%", h)
        self.assertIn(formats.API_RATE_LABEL, h)

    def test_text_is_escaped(self):
        h = meters.meter_html(meter(words="<b>x</b>"), NOW, RULES)
        self.assertNotIn("<b>x</b>", h)


if __name__ == "__main__":
    unittest.main()
