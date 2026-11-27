"""E16: the status-line wrapper. Runs the real script as a child process, the way Claude Code does."""
import json
import os
import statistics
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.engine import support
from ju import accounts, statusline

WRAPPER = Path(__file__).resolve().parents[2] / "engine" / "jusage-statusline"


class WrapperBase(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.folder = self.th.home / ".claude"
        self.folder.mkdir()
        self.key = accounts.folder_key(self.folder)
        self.env = {"HOME": str(self.th.home), "PATH": os.environ.get("PATH", ""),
                    "CLAUDE_CONFIG_DIR": str(self.folder)}

    def payload(self):
        reset = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        return json.dumps({"transcript_path": str(self.folder / "projects" / "-x" / "s.jsonl"),
                           "rate_limits": {"five_hour": {"used_percentage": 10, "resets_at": reset},
                                           "seven_day": {"used_percentage": 3, "resets_at": reset + 86400}}}).encode()

    def run_wrapper(self, *args, stdin=None, env=None):
        return subprocess.run([sys.executable, "-E", "-s", str(WRAPPER), *args],
                              input=self.payload() if stdin is None else stdin,
                              capture_output=True, env=env or self.env, cwd=str(self.th.home))


class WrapperTests(WrapperBase):
    def test_bytes_and_exit_code_pass_through(self):
        script = r"printf 'a\033[31mred\033[0m\200\377 tail'; printf 'oops' >&2; exit 3"
        r = self.run_wrapper("--", script)
        self.assertEqual(r.stdout, b"a\x1b[31mred\x1b[0m\x80\xff tail")
        self.assertEqual(r.stderr, b"oops")
        self.assertEqual(r.returncode, 3)

    def test_original_receives_the_same_stdin(self):
        payload = self.payload()
        r = self.run_wrapper("--", "cat", stdin=payload)
        self.assertEqual(r.stdout, payload)

    def test_capture_happens(self):
        self.run_wrapper("--", "true")
        doc = statusline.read_capture(self.th.state, self.key)
        self.assertEqual(doc["rate_limits"]["five_hour"]["used_percentage"], 10.0)

    def test_original_with_quotes_runs_as_one_command(self):
        r = self.run_wrapper("--", "echo 'two  words' \"and more\"")
        self.assertEqual(r.stdout, b"two  words and more\n")

    def test_capture_failure_still_runs_original(self):
        r = self.run_wrapper("--", "printf ok", stdin=b"this is not json")
        self.assertEqual(r.stdout, b"ok")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, b"")
        log = (self.th.state / "logs" / "engine.log").read_text()
        self.assertIn("statusline", log)

    def test_capture_failure_with_unwritable_state_still_runs_original(self):
        (self.th.home / ".claude-usage").rename(self.th.home / "moved")
        (self.th.home / ".claude-usage").write_text("a file, not a folder")
        r = self.run_wrapper("--", "printf ok")
        self.assertEqual(r.stdout, b"ok")
        self.assertEqual(r.returncode, 0)

    def test_no_original_prints_nothing(self):
        r = self.run_wrapper("--")
        self.assertEqual((r.stdout, r.stderr, r.returncode), (b"", b"", 0))
        self.assertIsNotNone(statusline.read_capture(self.th.state, self.key))

    def test_no_original_and_no_dashes_prints_nothing(self):
        r = self.run_wrapper()
        self.assertEqual((r.stdout, r.stderr, r.returncode), (b"", b"", 0))

    def test_no_stdin_data(self):
        r = self.run_wrapper("--", "printf ok", stdin=b"")
        self.assertEqual((r.stdout, r.returncode), (b"ok", 0))

    def test_killed_original_gives_128_plus_signal(self):
        r = self.run_wrapper("--", "kill -TERM $$")
        self.assertEqual(r.returncode, 128 + 15)

    def test_wrapped_command_from_turn_on_runs_end_to_end(self):
        (self.folder / "settings.json").write_text(json.dumps(
            {"statusLine": {"type": "command", "command": "printf 'hello world'"}}))
        wrapped = statusline.turn_on(self.th.state, self.key, python=sys.executable, engine_dir=WRAPPER.parent,
                                     now=datetime.now(timezone.utc).astimezone())
        r = subprocess.run(["/bin/sh", "-c", wrapped], input=self.payload(), capture_output=True, env=self.env)
        self.assertEqual(r.stdout, b"hello world")
        self.assertEqual(r.returncode, 0)

    def test_overhead_under_50ms(self):
        """Median over 41 back-to-back (bare, wrapped) pairs of wrapped minus bare. Each pair runs
        under the same instant of machine load, so a load spike moves both halves, and the median
        ignores the pairs a spike still splits. The budget stays 50 ms."""
        bare_cmd = ["/bin/sh", "-c", "true"]
        wrapped_cmd = [sys.executable, "-E", "-s", str(WRAPPER), "--", "true"]
        payload = self.payload()

        def timed(cmd):
            t = time.perf_counter()
            subprocess.run(cmd, input=payload, capture_output=True, env=self.env)
            return time.perf_counter() - t
        diffs = []
        for _ in range(41):
            b = timed(bare_cmd)
            diffs.append(timed(wrapped_cmd) - b)
        added = statistics.median(diffs)
        self.assertLess(added, 0.050, f"wrapper added {added * 1000:.1f} ms")

if __name__ == "__main__":
    unittest.main()
