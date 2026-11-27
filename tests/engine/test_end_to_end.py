"""Wave 4 end to end: the real engine as a process, a temp HOME, a fake `security` first on PATH, the
usage endpoint a FakeUsageServer. Nothing here reaches the real Keychain, the real endpoint, the real
~/.claude* folders or the real install, and every run proves it: the engine runs under an audit hook
that records every path it opens or lists, and no path under the account's real home may start with
`.claude` (RULING 32).

    report written by share.py  -> dashboard renders it            (RULING 35)
    a Mac with no Claude account -> share writes an empty report   (RULING 50)
    menu-json                    -> the golden feed's shape        (tests/fixtures/feed-v2.json)
    status-line capture          -> the primary reading            (RULINGS 28/29)
    staleness                    -> flips at exactly 900 s         (RULING 33)
"""
import json
import os
import pwd
import subprocess
import sys
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.engine import support
from ju import accounts, formats, keychain, timeutil

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "engine"
ENTRY = ENGINE / "claude_usage.py"
REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir
TOKEN = "sk-ant-oat01-" + "E" * 60

# Runs claude_usage.main() with USAGE_URL pointed at the fake server and an audit hook that writes
# every path the process opens, lists or stats (and every program it starts) to a log file.
DRIVER = textwrap.dedent("""
    import importlib.util, json, os, sys
    entry, url, log, args = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
    seen = open(log, "a")
    def hook(event, a):
        if event in ("open", "os.listdir", "os.scandir", "os.chdir") and a and isinstance(a[0], (str, bytes, os.PathLike)):
            seen.write(os.fsdecode(a[0]) + "\\n")
        elif event == "subprocess.Popen":
            seen.write("exec:" + os.fsdecode(a[0]) + "\\n")
    sys.addaudithook(hook)
    sys.path.insert(0, os.path.dirname(entry))
    spec = importlib.util.spec_from_file_location("claude_usage", entry)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claude_usage"] = mod
    spec.loader.exec_module(mod)
    mod.USAGE_URL = url
    code = mod.main(args)
    seen.flush()
    sys.exit(code)
""")


def login_blob():
    exp = (datetime.now(timezone.utc) + timedelta(hours=3)).timestamp() * 1000
    return json.dumps({"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": exp}})


def reply(session_pct=37, weekly_pct=52):
    now = datetime.now(timezone.utc)
    return {"limits": [
        {"kind": "session", "percent": session_pct, "resets_at": (now + timedelta(hours=2)).isoformat()},
        {"kind": "weekly_all", "percent": weekly_pct, "resets_at": (now + timedelta(days=3)).isoformat()}]}


class EndToEnd(unittest.TestCase):
    def setUp(self):
        saved = {k: os.environ.pop(k) for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR") if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        self.h = support.temp_home()
        self.addCleanup(self.h.cleanup)
        self.items = {}
        self.seen = self.h.home / "opened.log"
        self.driver = self.h.home / "driver.py"
        self.driver.write_text(DRIVER)

    def tearDown(self):
        self.assert_no_real_claude_path()

    # ------------------------------------------------------------------ helpers

    def account(self, name=".claude", email="sam@example.com", uuid="u-1"):
        d = self.h.home / name
        (d / "projects").mkdir(parents=True, exist_ok=True)
        (d / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": email, "accountUuid": uuid}}))
        key = accounts.folder_key(d)
        self.items[keychain.candidate_services(key, None, {"HOME": str(self.h.home)})[0]] = login_blob()
        return key

    def transcript(self, folder=".claude", tokens=100, minutes_ago=5):
        ts = timeutil.iso(timeutil.now() - timedelta(minutes=minutes_ago))
        support.write_transcript(self.h.home / folder, [{"ts": ts, "model": "claude-opus-5",
                                                        "input": tokens, "output": 7}], cwd="/work/jusage")

    def engine(self, *args, url="http://127.0.0.1:9/never", stdin=None):
        with support.fake_security(self.items) as sec:
            env = {"HOME": str(self.h.home), "LANG": "en_US.UTF-8", "PATH": sec.path()}
            r = subprocess.run([sys.executable, "-B", "-E", "-s", str(self.driver), str(ENTRY), url, str(self.seen),
                                json.dumps(list(args))], env=env, capture_output=True, text=True, timeout=120,
                               input=stdin)
            self.assertEqual(sec.refused(), [], "the engine only ever reads the (fake) Keychain")
        return r

    def assert_no_real_claude_path(self):
        if not self.seen.exists():
            return
        lines = self.seen.read_text().splitlines()
        # the hook must be seeing the engine's opens, or this check could never fail
        self.assertTrue(any(l.startswith(str(self.h.home)) or l.startswith(os.path.realpath(str(self.h.home))) for l in lines),
                        "the audit hook recorded none of the engine's own files")
        bad = []
        for line in lines:
            p = line[5:] if line.startswith("exec:") else line
            if line.startswith("exec:") and os.path.basename(p) == "security" and not p.startswith("/"):
                continue            # resolved through PATH, where the fake is first
            full = os.path.realpath(p) if os.path.isabs(p) else p
            rel = os.path.relpath(full, REAL_HOME) if full.startswith(REAL_HOME + os.sep) else None
            if rel is not None and (rel.startswith(".claude") or rel.startswith("jusage") or rel.startswith("Library/Keychains")):
                bad.append(p)
            if full.startswith("/Applications/jusage.app"):
                bad.append(p)
        self.assertEqual(bad, [], "the engine reached a real Claude folder, Keychain or install")

    # ------------------------------------------------------------------ RULINGS 28/29

    def statusline_setup(self, service_age_s):
        """One account whose last service reading is `service_age_s` old (weekly reset learned, a per-model
        weekly in the reply), the backup switched on through the CLI, then Claude Code's status line run
        exactly as settings.json now says, with a fresh capture on stdin. Returns the capture's numbers."""
        key = self.account()
        folder = self.h.home / ".claude"
        (folder / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "echo mine"}}))
        now = datetime.now(timezone.utc)
        weekly = (now + timedelta(days=3)).replace(second=0, microsecond=0)
        session_reset = (now + timedelta(hours=2)).replace(microsecond=0)
        at = now - timedelta(seconds=service_age_s)
        data = {"limits": [{"kind": "session", "percent": 30, "resets_at": session_reset.isoformat()},
                           {"kind": "weekly_all", "percent": 50, "resets_at": weekly.isoformat()},
                           {"kind": "weekly_scoped", "percent": 18, "resets_at": weekly.isoformat(),
                            "scope": {"model": {"display_name": "Fable 5"}}}]}
        mow = timeutil.minute_of_week(weekly)
        (self.h.state / "live.json").write_text(json.dumps({"schema": 2, "accounts": {key: {
            "fetched": at.isoformat(), "data": data, "error": None, "warning": None, "source": "service",
            "blocked_until": None, "backoff_step": 0, "retry_after_explicit": False, "last_manual": None,
            "reset_anchor": {"minute_of_week": mow, "confirmations": 3, "candidate": None, "candidate_count": 0},
            "identity_uuid": "u-1"}}}))
        from ju import ledger_db
        from ju.contracts import Meter, Reading
        from ju.live_store import log_meters
        conn = ledger_db.open_db(self.h.state, [key])
        log_meters(conn, key, Reading(key, at, [Meter("weekly:fable", "Weekly · Fable", 18.0, weekly, 604800)],
                                      [], "u-1", "service"))
        conn.close()
        self.assertEqual(self.engine("config", "python", sys.executable).returncode, 0)
        r = self.engine("statusline", "on", "--folder", key)
        self.assertEqual(r.returncode, 0, r.stderr)
        command = json.loads((folder / "settings.json").read_text())["statusLine"]["command"]
        self.assertIn(" -B -E -s ", command)
        stdin = json.dumps({"transcript_path": str(folder / "projects" / "-work" / "s.jsonl"),
                            "model": {"display_name": "never stored"},
                            "rate_limits": {"five_hour": {"used_percentage": 41, "resets_at": session_reset.isoformat()},
                                            "seven_day": {"used_percentage": 53, "resets_at": weekly.isoformat()}}})
        cc = subprocess.run(["/bin/bash", "-c", command], input=stdin, capture_output=True, text=True, timeout=30,
                            env={"HOME": str(self.h.home), "PATH": "/usr/bin:/bin"})
        self.assertEqual((cc.returncode, cc.stdout.strip()), (0, "mine"), cc.stderr)     # the user's own line, untouched
        return key

    def test_fresh_capture_is_the_reading_and_the_endpoint_is_not_called(self):
        key = self.statusline_setup(service_age_s=120)
        with support.FakeUsageServer([]) as srv:
            r = self.engine("menu-json", url=srv.url)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(srv.requests, [], "an accepted capture means no endpoint call (RULING 28)")
        feed = json.loads(r.stdout)
        meters = {m["id"]: m for m in feed["accounts"][0]["meters"]}
        self.assertEqual((meters["session"]["pct"], meters["session"]["source"]), (41.0, "statusline"))
        self.assertEqual((meters["weekly"]["pct"], meters["weekly"]["source"]), (53.0, "statusline"))
        self.assertEqual(meters["weekly:fable"]["source"], "service")
        self.assertIsNotNone(meters["weekly:fable"]["pace"]["state"], "the per-model weekly is not grey")
        cap = (self.h.state / "statusline").glob("*.json")
        self.assertNotIn("never stored", "".join(p.read_text() for p in cap))

    def test_per_model_weekly_reads_the_service_at_10_minutes(self):
        self.statusline_setup(service_age_s=600)
        with support.FakeUsageServer([(200, {}, reply(30, 50))]) as srv:
            r = self.engine("menu-json", url=srv.url)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(srv.requests), 1, "RULING 29: the service is read once its reading is 10 min old")

    # ------------------------------------------------------------------ menu-json -> the golden feed

    def assert_same_shape(self, got, gold, path="feed"):
        """Same keys in the same order at every object; lists compared by their first element; an object
        keyed by data (statusline: folder keys) compared by its values; null on either side not compared."""
        if got is None or gold is None:
            return
        if isinstance(gold, dict):
            self.assertIsInstance(got, dict, path)
            if path.endswith(".statusline"):
                for k, v in got.items():
                    self.assert_same_shape(v, next(iter(gold.values())), f"{path}[{k}]")
                return
            self.assertEqual(list(got), list(gold), path)
            for k in gold:
                self.assert_same_shape(got[k], gold[k], f"{path}.{k}")
        elif isinstance(gold, list):
            self.assertIsInstance(got, list, path)
            if got and gold:
                self.assert_same_shape(got[0], gold[0], f"{path}[0]")
        elif isinstance(gold, bool):
            self.assertIsInstance(got, bool, path)
        elif isinstance(gold, (int, float)):
            self.assertIsInstance(got, (int, float), path)
            self.assertNotIsInstance(got, bool, path)
        else:
            self.assertIsInstance(got, type(gold), path)

    def test_menu_json_has_the_golden_feed_shape(self):
        from tests.engine.test_dash_fixtures import report_dict
        self.account()
        self.transcript(tokens=500)
        reports = self.h.home / "shared"
        reports.mkdir()
        peer = report_dict(user="Lee", host="lee-mac", install_id="c3d4", gen_ago_s=60)
        peer["generated"] = timeutil.iso(timeutil.now() - timedelta(minutes=1))
        (reports / "Lee@lee-mac-c3d4.json").write_text(json.dumps(peer))
        r1 = reply(37, 52)
        r1["limits"].append({"kind": "weekly_scoped", "percent": 18, "resets_at": r1["limits"][1]["resets_at"],
                             "scope": {"model": {"display_name": "Fable 5"}}})
        with support.FakeUsageServer([(200, {}, r1)]) as srv:
            share = self.engine("share", "--reports", str(reports), "--user", "Sam", url=srv.url)
            self.assertEqual(share.returncode, 0, share.stderr)
            r = self.engine("menu-json", url=srv.url)
            self.assertEqual(len(srv.requests), 1, "menu-json inside the cadence reads nothing new")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        feed = json.loads(r.stdout)
        gold = support.load_fixture("feed-v2.json")
        self.assertEqual(feed["people"][0]["user"], "Lee", "a peer report reaches the feed")
        self.assertTrue(feed["accounts"][0]["meters"], "the account has meters")
        self.assert_same_shape(feed, gold)

    # ------------------------------------------------------------------ RULING 50

    def test_share_on_a_mac_with_no_claude_account(self):
        reports = self.h.home / "shared"
        r = self.engine("share", "--reports", str(reports), "--user", "Sam")
        self.assertEqual(r.returncode, 0, r.stderr)
        files = sorted(reports.glob("Sam@*.json"))
        self.assertEqual(len(files), 1, list(reports.iterdir()))
        rep = json.loads(files[0].read_text())
        self.assertEqual((rep["accounts"], rep["live"], rep["blocks"], rep["session_share"]), ([], {}, {}, {}))
        self.assertEqual(rep["report_version"], 3)

    # ------------------------------------------------------------------ RULING 35

    def test_report_written_by_share_renders_in_the_dashboard(self):
        self.account()
        self.transcript(tokens=1234)
        reports = self.h.home / "shared"
        page = self.h.home / "page.html"
        with support.FakeUsageServer([(200, {}, reply(37, 52))]) as srv:
            r = self.engine("share", "--reports", str(reports), "--user", "Sam", url=srv.url)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = next(reports.glob("Sam@*.json")).read_text()
        self.assertNotIn(str(self.h.home), text)                         # review-security 2: no home paths
        rep = json.loads(text)
        self.assertEqual(list(rep["live"]), ["u-1"])
        self.assertEqual((rep["live"]["u-1"]["key"], rep["live"]["u-1"]["folders"]), ("acct:u-1", []))
        self.assertEqual(rep["live"]["u-1"]["email"], "sam@example.com")
        self.assertEqual([m["id"] for m in rep["live"]["u-1"]["meters"]][:2], ["session", "weekly"])
        r = self.engine("dashboard", "--reports", str(reports), "--out", str(page))
        self.assertEqual(r.returncode, 0, r.stderr)
        html = page.read_text()
        install = json.loads((self.h.state / "config.json").read_text())["install_id"]
        self.assertIn(f"data-k='person:{install}'", html)                 # this Mac's own report is on the page
        self.assertIn("<b>Sam</b>", html)
        self.assertIn("data-k='acct:u-1'", html)                          # plan meters grouped by uuid
        self.assertIn("<b>sam@example.com</b>", html)
        self.assertIn("37%", html)
        self.assertIn(formats.tokens(1234 + 7), html)                     # today's tokens from the report's days
        self.assertNotIn("No reports yet", html)


class Staleness(unittest.TestCase):
    """RULING 33 / R-197: 899 s keeps its colour, 900 s is stale, in every engine owner and in the
    dashboard (the app's StalenessTests and the page script's test_freshness.js pin the same edge,
    and EndToEndDecodeTests checks the app against the feed's rules.stale_after_s)."""

    def test_flips_at_exactly_900_seconds(self):
        from ju import constants, pace as pace_mod, share
        from ju.contracts import Meter
        from ju.dash import meters as dash_meters
        from ju.live_store import AccountLive
        self.assertEqual(constants.STALE_AFTER_S, 900)
        now = datetime(2026, 11, 26, 21, 40, tzinfo=timezone.utc)
        m = Meter("session", "Session", 40.0, now + timedelta(hours=2), 18000)
        rules = {"stale_after_s": 900, "red_at": 97, "warmup_e": 0.03, "read_every_s": 60}
        for age, stale in ((899, False), (900, True)):
            at = now - timedelta(seconds=age)
            self.assertEqual(pace_mod.pace(m, now, read_at=at).state is None, stale, ("E17 pace", age))
            self.assertEqual(AccountLive(fetched=at, data={}).is_stale(now), stale, ("E13 is_stale", age))
            person = share.Person("Sam", [share.Report("Sam", "mac", "a1b2", "0.2.0", at, [], {}, {}, {}, {}, False, "x")],
                                  False, frozenset(), {}, {}, [])
            self.assertEqual(person.is_stale(now), stale, ("E20 Person.is_stale", age))
            block = {"id": "session", "name": "Session", "pct": 40.0, "resets_at": timeutil.iso(now + timedelta(hours=2)),
                     "read_at": timeutil.iso(at), "source": "service", "period_s": 18000, "disagree": False,
                     "pace": {"e": 0.5, "landing": 80.0, "state": "green", "forecast": True, "runs_out": None,
                              "reset_due": False, "words": "On pace"}, "split": None}
            self.assertEqual("stale" in dash_meters.meter_html(block, now, rules).split(">", 1)[0], stale, ("D3", age))


if __name__ == "__main__":
    unittest.main()
