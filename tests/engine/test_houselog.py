"""E2 logging: one engine.log per state dir, rotating, never leaks a token, never raises."""
import os
import re
import stat
import unittest

from ju import houselog
from tests.engine import support

TOKEN = "sk-ant-oat01-" + "A1b2C3d4" * 6


class HouselogTests(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.addCleanup(houselog.reset)
        houselog.configure(self.th.state)

    def text(self):
        return houselog.log_path().read_text(encoding="utf-8")

    def test_log_path_is_state_logs_engine_log(self):
        self.assertEqual(houselog.log_path(), self.th.state / "logs" / "engine.log")

    def test_default_path_follows_home_when_unconfigured(self):
        houselog.reset()
        self.assertEqual(houselog.log_path(), self.th.home / ".claude-usage" / "logs" / "engine.log")

    def test_line_has_local_iso_offset_level_message_and_pairs(self):
        self.assertTrue(houselog.log("error", "scan: bad file", path="/x/y.jsonl", n=3))
        line = self.text().splitlines()[0]
        self.assertRegex(line, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d ERROR scan: bad file ")
        self.assertIn("path=/x/y.jsonl", line)
        self.assertIn("n=3", line)

    def test_one_entry_is_one_line(self):
        houselog.log("info", "a\nb\r\nc", note="x\ny")
        self.assertEqual(len(self.text().splitlines()), 1)

    def test_state_argument_overrides_configured_dir(self):
        other = self.th.home / "other"
        houselog.log("info", "hello", state=other)
        self.assertIn("hello", (other / "logs" / "engine.log").read_text())

    def test_log_dir_is_private(self):
        houselog.log("info", "x")
        self.assertEqual(stat.S_IMODE(os.stat(houselog.log_path()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(houselog.log_path().parent).st_mode), 0o700)

    def test_redacts_tokens(self):
        long_b64 = "Zm9vYmFyYmF6" * 5  # 60 chars base64url-shaped
        houselog.log(
            "error", f"fetch failed with {TOKEN} and Bearer abc.def-123",
            accessToken=TOKEN, raw='{"accessToken": "secretvalue123", "refreshToken":"r3fr3sh"}',
            blob=long_b64, hdr="Authorization: Bearer zzzz1111",
        )
        body = self.text()
        for leaked in (TOKEN, "A1b2C3d4A1b2C3d4", "abc.def-123", "secretvalue123", "r3fr3sh", long_b64, "zzzz1111"):
            self.assertNotIn(leaked, body)
        self.assertIn("***", body)

    def test_redact_function_keeps_ordinary_text(self):
        s = "usage.db: migration 2->3 failed for /a/.claude uuid 123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(houselog.redact(s), s)

    def test_rotates_at_1mb(self):
        chunk = "word " * 200      # spaces: a 1000-char unbroken run would (rightly) be redacted
        for i in range(2600):
            houselog.log("info", f"line {i} {chunk}")
        d = houselog.log_path().parent
        names = sorted(p.name for p in d.iterdir() if p.name.startswith("engine.log"))
        self.assertIn("engine.log.1", names)
        self.assertNotIn("engine.log.4", names)
        for n in names:
            self.assertLessEqual((d / n).stat().st_size, 1024 * 1024 + 2048, n)
        # newest line is in the live file, nothing was dropped mid-line
        self.assertIn("line 2599", self.text())

    def test_never_raises_on_unwritable_dir(self):
        blocker = self.th.home / "blocker"
        blocker.write_text("a file where the state dir should be")
        houselog.configure(blocker)
        self.assertFalse(houselog.log("error", "cannot be written"))

    def test_never_raises_on_odd_values(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("no str")
        self.assertTrue(houselog.log("info", "ok", weird=Bad(), none=None))
        self.assertTrue(houselog.log("nonsense-level", "still logs"))

    def test_unknown_level_becomes_info(self):
        houselog.log("loud", "x")
        self.assertIn(" INFO x", self.text())


if __name__ == "__main__":
    unittest.main()
