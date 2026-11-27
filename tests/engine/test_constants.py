"""E1 constants: every number the SCORE names is asserted here, so a silent edit fails a test."""
import os
import unittest
from pathlib import Path

from ju import constants as C


class ConstantsTests(unittest.TestCase):
    def test_values_match_spec(self):
        expected = {
            "STALE_AFTER_S": 900,
            "RED_AT": 97.0,
            "WARMUP_E": 0.03,
            "SPAN_FLOOR_DIV": 7,
            "RUNS_OUT_LANDING": 105,
            "SESSION_S": 18000,
            "WEEK_S": 604800,
            "READ_EVERY_DEFAULT": 60,
            "READ_EVERY_MIN": 15,
            "READ_EVERY_MAX": 600,
            "BACKOFF_S": (300, 600, 1200, 1800),
            "RETRY_AFTER_CAP_S": 3600,
            "JITTER": 0.20,
            "MANUAL_FLOOR_S": 15,
            "SERVICE_REFRESH_S": 600,           # RULING 29
            "KEYCHAIN_TIMEOUT_S": 30,
            "HTTP_TIMEOUT_S": 15,
            "DB_BUSY_S": 10,
            "SHARE_MIN_INTERVAL_S": 300,
            "USAGE_URL": "https://api.anthropic.com/api/oauth/usage",
            "ANTHROPIC_BETA": "oauth-2025-04-20",
            "FINGERPRINT_TOL_S": 120,
            "DISAGREE_PCT": 5,
            "DISAGREE_RESET_S": 120,
            "COLS": ("input", "cache_5m", "cache_1h", "cache_read", "output"),
            "FEED_SCHEMA": 2,
            "LIVE_SCHEMA": 2,
            "DB_SCHEMA": 3,
            "REPORT_VERSION": 3,
        }
        for name, want in expected.items():
            with self.subTest(name):
                self.assertTrue(hasattr(C, name), f"constants.{name} missing")
                got = getattr(C, name)
                self.assertEqual(got, want)
                self.assertIs(type(got), type(want))

    def test_no_unlisted_public_constants(self):
        listed = {
            "HERE", "REPO_ROOT", "STATE_DEFAULT", "STALE_AFTER_S", "RED_AT", "WARMUP_E",
            "SPAN_FLOOR_DIV", "RUNS_OUT_LANDING", "SESSION_S", "WEEK_S", "READ_EVERY_DEFAULT",
            "READ_EVERY_MIN", "READ_EVERY_MAX", "BACKOFF_S", "RETRY_AFTER_CAP_S", "JITTER",
            "MANUAL_FLOOR_S", "SERVICE_REFRESH_S", "KEYCHAIN_TIMEOUT_S", "HTTP_TIMEOUT_S", "DB_BUSY_S",
            "SHARE_MIN_INTERVAL_S", "USAGE_URL", "ANTHROPIC_BETA", "FINGERPRINT_TOL_S",
            "DISAGREE_PCT", "DISAGREE_RESET_S", "COLS", "FEED_SCHEMA", "LIVE_SCHEMA",
            "DB_SCHEMA", "REPORT_VERSION", "state_dir",
        }
        public = {n for n in dir(C) if n.isupper() or n == "state_dir"}
        self.assertEqual(public - listed, set(), "constants outside the SCORE list")
        self.assertEqual(listed - public, set())

    def test_here_and_repo_root(self):
        self.assertEqual(C.HERE.name, "ju")
        self.assertEqual(C.HERE.parent.name, "engine")
        self.assertEqual(C.REPO_ROOT, C.HERE.parent.parent)
        self.assertTrue((C.REPO_ROOT / "VERSION").is_file(), "VERSION must sit at REPO_ROOT")

    def test_version_file_is_a_release_at_or_after_0_2_0(self):
        text = (C.REPO_ROOT / "VERSION").read_text().strip()
        self.assertRegex(text, r"^\d+\.\d+\.\d+$")
        self.assertGreaterEqual(tuple(int(x) for x in text.split(".")), (0, 2, 0))

    def test_state_default_is_home_claude_usage(self):
        self.assertEqual(C.STATE_DEFAULT, Path.home() / ".claude-usage")

    def test_state_dir_override_and_default(self):
        self.assertEqual(C.state_dir(None), C.STATE_DEFAULT)
        self.assertEqual(C.state_dir("/tmp/x-state"), Path("/tmp/x-state"))
        self.assertEqual(C.state_dir("~/x-state"), Path(os.path.expanduser("~/x-state")))
        self.assertTrue(C.state_dir("rel/x").is_absolute())

    def test_state_dir_empty_string_is_an_error_not_the_default(self):
        with self.assertRaises(ValueError):
            C.state_dir("")


if __name__ == "__main__":
    unittest.main()
