"""E6 pricing: bundled rates, longest-prefix match, multipliers from the file, unpriced = None."""
import json
import tempfile
import unittest
from pathlib import Path

from ju import pricing


def write(table) -> Path:
    d = Path(tempfile.mkdtemp(prefix="jusage-pricing-"))
    p = d / "pricing.json"
    p.write_text(json.dumps(table))
    return p


BASE = {
    "_comment": "x",
    "_multipliers": {"cache_5m": 1.25, "cache_1h": 2.0},
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_read": 1.5},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
}


def row(model="claude-opus-4-5", **kw):
    r = {"model": model, "input": 0, "cache_5m": 0, "cache_1h": 0, "cache_read": 0, "output": 0}
    r.update(kw)
    return r


class PricingTests(unittest.TestCase):
    def test_longest_prefix(self):
        p = pricing.load(write(BASE))
        self.assertEqual(pricing.rate_for(p, "claude-opus-4-5-20251101").input, 5.0)
        self.assertEqual(pricing.rate_for(p, "claude-opus-4-1-20250805").input, 15.0)
        self.assertEqual(pricing.rate_for(p, "claude-opus-4").input, 15.0)

    def test_multipliers_from_file(self):
        table = dict(BASE, _multipliers={"cache_5m": 1.5, "cache_1h": 3.0})
        p = pricing.load(write(table))
        c = pricing.cost_usd(p, row(cache_5m=1_000_000, cache_1h=1_000_000))
        self.assertAlmostEqual(c, 5.0 * 1.5 + 5.0 * 3.0)

    def test_cost_all_columns(self):
        p = pricing.load(write(BASE))
        c = pricing.cost_usd(p, row(input=1_000_000, cache_5m=1_000_000, cache_1h=1_000_000,
                                    cache_read=1_000_000, output=1_000_000))
        self.assertAlmostEqual(c, 5.0 + 5.0 * 1.25 + 5.0 * 2.0 + 0.5 + 25.0)

    def test_unpriced_none(self):
        p = pricing.load(write(BASE))
        self.assertIsNone(pricing.rate_for(p, "claude-mystery-9"))
        self.assertIsNone(pricing.cost_usd(p, row(model="claude-mystery-9", input=5)))
        self.assertIsNone(pricing.cost_usd(p, row(model="unknown", input=5)))

    def test_missing_multiplier_refused(self):
        no_block = {k: v for k, v in BASE.items() if k != "_multipliers"}
        with self.assertRaises(pricing.PricingInvalid) as cm:
            pricing.load(write(no_block))
        self.assertIn("_multipliers", str(cm.exception))
        for gone in ("cache_5m", "cache_1h"):
            table = dict(BASE, _multipliers={k: v for k, v in BASE["_multipliers"].items() if k != gone})
            with self.assertRaises(pricing.PricingInvalid) as cm:
                pricing.load(write(table))
            self.assertIn(gone, str(cm.exception))

    def test_bad_numbers_name_the_key(self):
        for bad in ({"input": -1, "output": 1, "cache_read": 1},
                    {"input": "5", "output": 1, "cache_read": 1},
                    {"input": True, "output": 1, "cache_read": 1},
                    {"output": 1, "cache_read": 1},
                    {"input": float("inf"), "output": 1, "cache_read": 1},
                    "free"):
            with self.subTest(bad=bad):
                with self.assertRaises(pricing.PricingInvalid) as cm:
                    pricing.load(write(dict(BASE, **{"claude-bad": bad})))
                self.assertIn("claude-bad", str(cm.exception))

    def test_bad_multiplier_values(self):
        for bad in (0, -1, "2", True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(pricing.PricingInvalid):
                    pricing.load(write(dict(BASE, _multipliers={"cache_5m": bad, "cache_1h": 2.0})))

    def test_unreadable_or_not_json_refused(self):
        with self.assertRaises(pricing.PricingInvalid):
            pricing.load(Path(tempfile.mkdtemp()) / "nope.json")
        p = write(BASE)
        p.write_text("{not json")
        with self.assertRaises(pricing.PricingInvalid):
            pricing.load(p)

    def test_sqlite_row_and_mapping_both_work(self):
        import sqlite3
        p = pricing.load(write(BASE))
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT 'claude-opus-4-5' AS model, 1000000 AS input, 0 AS cache_5m, 0 AS cache_1h,"
                      " 0 AS cache_read, 0 AS output").fetchone()
        self.assertAlmostEqual(pricing.cost_usd(p, r), 5.0)

    def test_reload_sees_a_changed_file(self):
        path = write(BASE)
        a = pricing.load(path)
        path.write_text(json.dumps(dict(BASE, **{"claude-opus-4-5": {"input": 1.0, "output": 1.0, "cache_read": 1.0}})))
        b = pricing.load(path)
        self.assertEqual(pricing.rate_for(a, "claude-opus-4-5").input, 5.0)
        self.assertEqual(pricing.rate_for(b, "claude-opus-4-5").input, 1.0)


if __name__ == "__main__":
    unittest.main()
