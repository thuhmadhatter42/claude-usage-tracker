"""E19 formats and names: one fixture table (tests/fixtures/formats.json) shared with Swift and JS."""
import unittest
from datetime import datetime

from ju import formats
from tests.engine import support


class FormatsCase(unittest.TestCase):
    fx = support.load_fixture("formats.json")

    def test_fixture_table(self):
        for r in self.fx["tokens"]:
            self.assertEqual(formats.tokens(r["n"]), r["text"], r)
        for r in self.fx["usd"]:
            self.assertEqual(formats.usd(r["usd"]), r["text"], r)
        for r in self.fx["countdown"]:
            self.assertEqual(formats.countdown(r["seconds"]), r["text"], r)
        for r in self.fx["clock"]:
            got = formats.clock(datetime.fromisoformat(r["at"]), datetime.fromisoformat(r["now"]),
                                not r["hour12"], tz_offset_min=r["tz_offset_min"])
            self.assertEqual(got, r["text"], r)

    def test_clock_default_zone_is_local(self):
        a = datetime.fromisoformat("2026-11-26T14:05:00-08:00")
        self.assertEqual(formats.clock(a, a, True), a.astimezone().strftime("%H:%M"))

    def test_model_name_any_date_suffix(self):
        self.assertEqual(formats.model_name("claude-haiku-4-5-20251001"), "haiku-4-5")
        self.assertEqual(formats.model_name("claude-sonnet-5-5-20271231"), "sonnet-5-5")
        self.assertEqual(formats.model_name("claude-opus-5"), "opus-5")
        self.assertEqual(formats.model_name("fable"), "fable")
        self.assertEqual(formats.model_name("claude-x-1234567"), "x-1234567")  # 7 digits is not a date

    def test_meter_names(self):
        self.assertEqual(formats.meter_name("session", None), "Session")
        self.assertEqual(formats.meter_name("weekly", None), "Weekly")
        self.assertEqual(formats.meter_name("weekly_scoped", "Fable"), "Weekly \u00b7 Fable")
        with self.assertRaises(ValueError):
            formats.meter_name("weekly_scoped", None)
        with self.assertRaises(ValueError):
            formats.meter_name("monthly", None)

    def test_constants(self):
        self.assertEqual(formats.RESET_DUE_TEXT, "Reset due, waiting for a fresh reading")
        self.assertEqual(formats.API_RATE_LABEL, "API-rate equivalent, not your bill")

    def test_refuses_nonsense(self):
        with self.assertRaises(ValueError):
            formats.tokens(-1)
        with self.assertRaises(ValueError):
            formats.usd(-0.5)
        with self.assertRaises(ValueError):
            formats.clock(datetime(2026, 1, 1, 1, 1), datetime.fromisoformat("2026-01-01T00:00:00+00:00"), True)

    def test_usd_rounding_up_into_hundred(self):
        self.assertEqual(formats.usd(99.996), "$100")


if __name__ == "__main__":
    unittest.main()
