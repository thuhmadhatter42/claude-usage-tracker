"""T1 harness self-tests: the helpers every later chair builds on must behave as documented."""
import json
import os
import stat
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.engine import support


class TempHomeTests(unittest.TestCase):
    def test_sets_home_and_state_and_restores(self):
        before = os.environ.get("HOME")
        with support.temp_home() as th:
            self.assertEqual(os.environ["HOME"], str(th.home))
            self.assertEqual(Path.home(), th.home)
            self.assertEqual(th.state, th.home / ".claude-usage")
            self.assertTrue(th.state.is_dir())
            self.assertEqual(stat.S_IMODE(th.state.stat().st_mode), 0o700)
            home = th.home
        self.assertEqual(os.environ.get("HOME"), before)
        self.assertFalse(home.exists())

    def test_cleanup_without_with(self):
        th = support.temp_home()
        self.assertEqual(os.environ["HOME"], str(th.home))
        th.cleanup()
        self.assertFalse(th.home.exists())
        th.cleanup()                      # twice is fine

    def test_home_is_resolved_real_path(self):
        with support.temp_home() as th:
            self.assertEqual(th.home, th.home.resolve())

    def test_no_real_home_leak(self):
        with support.temp_home() as th:
            self.assertNotIn(str(Path(__file__).resolve().parents[2]), str(th.home))


class FakeSecurityTests(unittest.TestCase):
    def run_sec(self, fs, *args, timeout=10):
        env = dict(os.environ, PATH=fs.path())
        return subprocess.run(["security", *args], capture_output=True, text=True, env=env, timeout=timeout)

    def test_found_item_prints_value_exit_0(self):
        with support.fake_security({"Claude Code-credentials": '{"a":1}'}) as fs:
            r = self.run_sec(fs, "find-generic-password", "-s", "Claude Code-credentials", "-w")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, '{"a":1}\n')

    def test_missing_item_exit_44_stderr(self):
        with support.fake_security({}) as fs:
            r = self.run_sec(fs, "find-generic-password", "-s", "nope", "-w")
            self.assertEqual(r.returncode, 44)
            self.assertEqual(r.stdout, "")
            self.assertIn("could not be found", r.stderr)

    def test_records_every_call(self):
        with support.fake_security({"x": "v"}) as fs:
            self.run_sec(fs, "find-generic-password", "-s", "x", "-w")
            self.run_sec(fs, "find-generic-password", "-s", "y", "-w")
            self.assertEqual(fs.calls(), [["find-generic-password", "-s", "x", "-w"],
                                          ["find-generic-password", "-s", "y", "-w"]])
            self.assertEqual(fs.services_asked(), ["x", "y"])

    def test_any_write_subcommand_is_refused_and_recorded(self):
        with support.fake_security({"x": "v"}) as fs:
            for sub in ("add-generic-password", "delete-generic-password", "unlock-keychain"):
                r = self.run_sec(fs, sub, "-s", "x")
                self.assertEqual(r.returncode, 64, sub)
            self.assertEqual(len(fs.refused()), 3)
            self.assertEqual(fs.refused()[0][0], "add-generic-password")

    def test_find_without_w_is_refused(self):
        with support.fake_security({"x": "v"}) as fs:
            r = self.run_sec(fs, "find-generic-password", "-s", "x")
            self.assertEqual(r.returncode, 64)
            self.assertEqual(len(fs.refused()), 1)

    def test_hang_blocks_until_killed(self):
        with support.fake_security({"x": "v"}, hang=True) as fs:
            t0 = time.time()
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_sec(fs, "find-generic-password", "-s", "x", "-w", timeout=1)
            self.assertLess(time.time() - t0, 5)

    def test_applied_to_environ_inside_with(self):
        with support.fake_security({"x": "v"}) as fs:
            self.assertTrue(os.environ["PATH"].startswith(str(fs.bin_dir)))
            r = subprocess.run(["security", "find-generic-password", "-s", "x", "-w"],
                               capture_output=True, text=True)
            self.assertEqual(r.stdout, "v\n")
        self.assertNotIn("jusage-fakesec", os.environ["PATH"])

    def test_cleanup_removes_dir(self):
        with support.fake_security({}) as fs:
            d = fs.bin_dir
        self.assertFalse(d.exists())


class FakeUsageServerTests(unittest.TestCase):
    def get(self, url, headers=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, dict(e.headers), e.read()
            finally:
                e.close()

    def test_serves_in_order_and_records(self):
        with support.FakeUsageServer([(200, {}, b'{"a":1}'), (429, {"Retry-After": "90"}, b"{}")]) as s:
            self.assertTrue(s.url.startswith("http://127.0.0.1:"))
            st, _, body = self.get(s.url, {"Authorization": "Bearer t1", "anthropic-beta": "oauth-2025-04-20"})
            self.assertEqual((st, body), (200, b'{"a":1}'))
            st, h, _ = self.get(s.url)
            self.assertEqual(st, 429)
            self.assertEqual(h.get("Retry-After"), "90")
            self.assertEqual(len(s.requests), 2)
            self.assertEqual(s.requests[0].method, "GET")
            self.assertEqual(s.requests[0].headers["Authorization"], "Bearer t1")
            self.assertEqual(s.requests[0].headers["anthropic-beta"], "oauth-2025-04-20")

    def test_exhausted_queue_answers_599_and_records(self):
        with support.FakeUsageServer([(200, {}, b"{}")]) as s:
            self.get(s.url)
            st, _, body = self.get(s.url)
            self.assertEqual(st, 599)
            self.assertIn(b"no response queued", body)
            self.assertEqual(len(s.requests), 2)

    def test_redirect_response_is_served_as_is(self):
        with support.FakeUsageServer([(302, {"Location": "http://127.0.0.1:1/x"}, b"")]) as s:
            st, h, _ = self.get(s.url)
            self.assertEqual(st, 302)
            self.assertEqual(h["Location"], "http://127.0.0.1:1/x")

    def test_dict_body_is_json_encoded(self):
        with support.FakeUsageServer([(200, {}, {"k": [1, 2]})]) as s:
            st, h, body = self.get(s.url)
            self.assertEqual(json.loads(body), {"k": [1, 2]})
            self.assertEqual(h["Content-Type"], "application/json")

    def test_close_frees_port(self):
        s = support.FakeUsageServer([])
        url = s.url
        s.close()
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(url, timeout=2)


class WriteTranscriptTests(unittest.TestCase):
    def test_writes_claude_code_shaped_assistant_rows(self):
        with support.temp_home() as th:
            folder = th.home / ".claude"
            p = support.write_transcript(folder, [
                {"ts": "2026-11-26T10:00:00-08:00", "model": "claude-opus-5", "session": "s1",
                 "cwd": "/work/demo", "input": 10, "output": 20, "cache_5m": 5, "cache_1h": 7,
                 "cache_read": 100, "message_id": "m1", "request_id": "r1"},
            ])
            self.assertEqual(p.parent.parent, folder / "projects")
            self.assertEqual(p.name, "s1.jsonl")
            rows = [json.loads(l) for l in p.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            o = rows[0]
            self.assertEqual(o["type"], "assistant")
            self.assertEqual(o["timestamp"], "2026-11-26T10:00:00-08:00")
            self.assertEqual(o["sessionId"], "s1")
            self.assertEqual(o["cwd"], "/work/demo")
            self.assertEqual(o["requestId"], "r1")
            m = o["message"]
            self.assertEqual((m["id"], m["model"], m["stop_reason"]), ("m1", "claude-opus-5", "end_turn"))
            u = m["usage"]
            self.assertEqual(u["input_tokens"], 10)
            self.assertEqual(u["output_tokens"], 20)
            self.assertEqual(u["cache_read_input_tokens"], 100)
            self.assertEqual(u["cache_creation_input_tokens"], 12)
            self.assertEqual(u["cache_creation"], {"ephemeral_5m_input_tokens": 5, "ephemeral_1h_input_tokens": 7})

    def test_no_message_text_is_ever_written(self):
        with support.temp_home() as th:
            p = support.write_transcript(th.home / ".claude", [
                {"ts": "2026-11-26T10:00:00-08:00", "model": "m", "session": "s", "cwd": "/w", "output": 1}])
            self.assertNotIn("content", json.loads(p.read_text())["message"])

    def test_incomplete_row_has_null_stop_reason(self):
        with support.temp_home() as th:
            p = support.write_transcript(th.home / ".claude", [
                {"ts": "2026-11-26T10:00:00-08:00", "model": "m", "session": "s", "cwd": "/w", "output": 1,
                 "stop_reason": None}])
            self.assertIsNone(json.loads(p.read_text())["message"]["stop_reason"])

    def test_appends_and_accepts_raw_lines(self):
        with support.temp_home() as th:
            f = th.home / ".claude"
            row = {"ts": "2026-11-26T10:00:00-08:00", "model": "m", "session": "s", "cwd": "/w", "output": 1}
            support.write_transcript(f, [row])
            p = support.write_transcript(f, ["{not json", dict(row, message_id="m2", request_id="r2")])
            lines = p.read_text().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[1], "{not json")

    def test_two_sessions_two_files_and_project_dir_from_cwd(self):
        with support.temp_home() as th:
            f = th.home / ".claude"
            a = support.write_transcript(f, [{"ts": "2026-11-26T10:00:00-08:00", "model": "m", "session": "a", "cwd": "/w/x", "output": 1}])
            b = support.write_transcript(f, [{"ts": "2026-11-26T10:00:00-08:00", "model": "m", "session": "b", "cwd": "/w/y", "output": 1}])
            self.assertNotEqual(a, b)
            self.assertNotEqual(a.parent, b.parent)


if __name__ == "__main__":
    unittest.main()
