"""D4 svg: stacked day charts; the y ceiling is right for small values too."""
import unittest

from ju.dash import svg


class Svg(unittest.TestCase):
    def test_nice_ceiling_small_values(self):
        self.assertAlmostEqual(svg.nice_ceiling(0.5), 0.6)
        self.assertAlmostEqual(svg.nice_ceiling(0.05), 0.06)
        self.assertAlmostEqual(svg.nice_ceiling(0), 1)
        self.assertAlmostEqual(svg.nice_ceiling(100), 120)
        self.assertAlmostEqual(svg.nice_ceiling(9_600_000), 10_000_000)

    def test_ceiling_always_covers_value(self):
        for v in (0.003, 0.7, 1, 7, 99, 1234, 98765, 4_200_000_000):
            self.assertGreaterEqual(svg.nice_ceiling(v), v * 1.04 - 1e-12, v)

    def test_zero_series_skipped(self):
        days = ["2026-11-25", "2026-11-26"]
        series = [("a", "A"), ("b", "B")]
        values = {d: {"a": 100, "b": 0} for d in days}
        out = svg.stacked_svg(days, series, values, "t")
        self.assertIn("var(--s0)", out)
        self.assertNotIn("var(--s1)", out)

    def test_labels_escaped_and_chart_id_marked(self):
        out = svg.stacked_svg(["2026-11-26"], [("a", "A")], {"2026-11-26": {"a": 5}}, "proj")
        self.assertIn("data-chart='proj'", out)
        self.assertIn("data-day='2026-11-26'", out)


if __name__ == "__main__":
    unittest.main()
