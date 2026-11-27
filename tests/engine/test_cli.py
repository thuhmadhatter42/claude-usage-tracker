"""E24 CLI entry (engine/claude_usage.py): stdout is only JSON for menu-json, a failure is exit 1
with one stderr line, a bring-up refusal is exit 2, `update --out` is honoured (N1/R-139) and
`update --projects` accepted, and the real wave-1 modules are wired (gate, ledger, Keychain read).

Every test runs with a temp HOME and a fake `security` first on PATH; the usage endpoint is a
FakeUsageServer. The real Keychain and the real endpoint are never reached. E15 (statusline),
E20 (share) and D1 (dashboard page) are stand-in modules here; wave 4 tests the real ones."""
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.engine import support
from ju import accounts, keychain, timeutil
from ju.contracts import Meter, Reading

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "engine" / "claude_usage.py"
TOKEN = "sk-ant-oat01-" + "Z" * 60


def load_entry():
    spec = importlib.util.spec_from_file_location("claude_usage", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claude_usage"] = mod          # dataclasses look their module up while the class is built
    spec.loader.exec_module(mod)
    return mod


CU = load_entry()


def login_blob():
    exp = (datetime.now(timezone.utc) + timedelta(hours=3)).timestamp() * 1000
    return json.dumps({"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": exp}})


def reply(session_pct=37, weekly_pct=52):
    now = datetime.now(timezone.utc)
    return {"limits": [
        {"kind": "session", "percent": session_pct, "resets_at": (now + timedelta(hours=2)).isoformat()},
        {"kind": "weekly_all", "percent": weekly_pct, "resets_at": (now + timedelta(days=3)).isoformat()}]}


SESSION_VARS = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR")


class Base(unittest.TestCase):
    def setUp(self):
        self.h = support.temp_home()
        self.addCleanup(self.h.cleanup)
        # the test runner may itself run inside a Claude Code session: its folder must never leak in
        saved = {k: os.environ.pop(k) for k in SESSION_VARS if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        self.k1 = self.make_account(".claude", "sam@example.com", "u-1")
        self.items = {}

    def make_account(self, name, email, uuid):
        d = self.h.home / name
        (d / "projects").mkdir(parents=True)
        (d / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": email, "accountUuid": uuid}}))
        return accounts.folder_key(d)

    def transcript(self, folder=".claude", tokens=100):
        ts = timeutil.iso(timeutil.now() - timedelta(minutes=5))
        support.write_transcript(self.h.home / folder, [{"ts": ts, "model": "claude-opus-5",
                                                        "input": tokens, "output": 7}], cwd="/work/jusage")

    def service_for(self, key):
        return keychain.candidate_services(key, None, {"HOME": str(self.h.home)})[0]

    # -- subprocess: the real process boundary
    def run_cli(self, *args):
        with support.fake_security(self.items) as sec:
            env = {"HOME": str(self.h.home), "LANG": "en_US.UTF-8", "PATH": sec.path()}
            return subprocess.run([sys.executable, "-E", "-s", str(ENTRY), *args], env=env,
                                  capture_output=True, text=True, timeout=120)

    # -- in process: for stand-in modules and the fake endpoint
    def main(self, *args, url=None):
        out, err = io.StringIO(), io.StringIO()
        with support.fake_security(self.items), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(CU, "USAGE_URL", url or "http://127.0.0.1:9/never"):
            rc = CU.main(["--state-dir", str(self.h.state), *args])
        return rc, out.getvalue(), err.getvalue()

    def stand_in(self, name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        old = sys.modules.get(name)
        sys.modules[name] = mod
        self.addCleanup(lambda: sys.modules.__setitem__(name, old) if old else sys.modules.pop(name, None))
        return mod

    def cfg(self):
        return json.loads((self.h.state / "config.json").read_text())


class MenuJson(Base):
    def test_menu_json_stdout_is_only_json(self):
        self.transcript()
        r = self.run_cli("menu-json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        feed = json.loads(r.stdout)                  # the whole of stdout is one JSON document
        self.assertEqual(feed["schema"], 2)
        self.assertEqual(feed["accounts"][0]["key"], self.k1)
        self.assertEqual(feed["accounts"][0]["number"], 1)
        self.assertEqual(feed["accounts"][0]["error"]["kind"], "login_missing")
        self.assertEqual(feed["totals"]["today"]["calls"], 1)
        self.assertIsNone(feed["dashboard"])
        self.assertEqual(len(self.cfg()["install_id"]), 4)        # RULING 12: made once, inside an edit

    def test_menu_json_reads_through_the_gate(self):
        self.items = {self.service_for(self.k1): login_blob()}
        with support.FakeUsageServer([(200, {}, reply())]) as srv:
            rc, out, err = self.main("menu-json", url=srv.url)
            self.assertEqual(rc, 0, err)
            feed = json.loads(out)
            m = feed["accounts"][0]["meters"]
            self.assertEqual([(x["id"], x["pct"], x["source"]) for x in m],
                             [("session", 37.0, "service"), ("weekly", 52.0, "service")])
            self.assertEqual(len(srv.requests), 1)
            self.assertEqual(srv.requests[0].headers["Authorization"], "Bearer " + TOKEN)
            rc, out, err = self.main("menu-json", url=srv.url)          # inside the 60 s cadence: no read
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(srv.requests), 1)
        live = json.loads((self.h.state / "live.json").read_text())
        self.assertEqual(live["schema"], 2)
        self.assertNotIn(TOKEN, (self.h.state / "live.json").read_text())
        c = sqlite3.connect(self.h.state / "usage.db")
        self.assertEqual(c.execute("SELECT count(*) FROM meter_log").fetchone()[0], 2)
        c.close()
        log = self.h.state / "logs" / "engine.log"
        if log.exists():
            self.assertNotIn("ZZZZZZZZ", log.read_text())

    def test_force_is_a_manual_refresh(self):
        self.items = {self.service_for(self.k1): login_blob()}
        with support.FakeUsageServer([(200, {}, reply()), (200, {}, reply(40))]) as srv:
            self.main("menu-json", url=srv.url)
            rc, out, err = self.main("menu-json", "--force", url=srv.url)
            self.assertEqual(len(srv.requests), 1)        # the manual floor (15 s) still holds
            self.assertEqual(json.loads(out)["accounts"][0]["meters"][0]["pct"], 37.0)

    def test_capture_backs_up_a_failing_read_through_e15(self):
        cap = {"captured_at": timeutil.iso(timeutil.now() - timedelta(minutes=1)), "folder": self.k1,
               "rate_limits": {"five_hour": {"used_percentage": 44, "resets_at": 1}}}
        from ju import feed
        p = feed.capture_path(self.h.state, self.k1)
        p.parent.mkdir(mode=0o700)
        p.write_text(json.dumps(cap))
        calls = []
        resets = timeutil.now() + timedelta(hours=2)

        def backup_reading(acc, capture, fingerprints, now):
            calls.append((acc.error.kind, capture, fingerprints))
            return Reading(self.k1, timeutil.parse_iso(capture["captured_at"]),
                           [Meter("session", "Session", 44.0, resets, 18000)], [], "u-1", "statusline"), None

        self.stand_in("ju.statusline", backup_reading=backup_reading, disagree=lambda s, c: False)
        rc, out, err = self.main("menu-json")
        self.assertEqual(rc, 0, err)
        a = json.loads(out)["accounts"][0]
        self.assertEqual(calls[0][0], "login_missing")
        self.assertEqual(calls[0][1], cap)
        self.assertEqual([(m["id"], m["source"], m["pct"]) for m in a["meters"]], [("session", "statusline", 44.0)])
        self.assertEqual(json.loads(out)["statusline"][self.k1]["capture_at"], cap["captured_at"])


class StatuslinePrimary(Base):
    """RULING 28: with the backup on, an accepted capture newer than the stored reading is the
    account's reading, saved before the gate decides; the endpoint is read only without one."""

    def setUp(self):
        super().setUp()
        self.items = {self.service_for(self.k1): login_blob()}
        self.judged = []

    def capture(self, age_s, pct=44):
        from ju import feed
        at = timeutil.now() - timedelta(seconds=age_s)
        cap = {"captured_at": timeutil.iso(at), "folder": self.k1, "pct": pct}
        p = feed.capture_path(self.h.state, self.k1)
        p.parent.mkdir(mode=0o700, exist_ok=True)
        p.write_text(json.dumps(cap))
        return cap

    def statusline(self, accept=True):
        resets = timeutil.now() + timedelta(hours=2)
        weekly = timeutil.now() + timedelta(days=3)

        def backup_reading(acc, cap, others, now, key=None):
            self.judged.append(key)
            if not accept:
                return None, "weekly reset matches another account"
            at = timeutil.parse_iso(cap["captured_at"])
            return Reading(key, at, [Meter("session", "Session", float(cap["pct"]), resets, 18000),
                                     Meter("weekly", "Weekly", 60.0, weekly, 604800)], [], "u-1", "statusline"), None

        self.stand_in("ju.statusline", backup_reading=backup_reading, disagree=lambda s, c: False)

    def backup_on(self, on=True):
        from ju.config import ConfigStore
        with ConfigStore.edit(self.h.state) as cfg:
            cfg.statusline = {self.k1: {"enabled": on, "original": None, "wrapped": "w"}}

    def test_fresh_accepted_capture_means_no_endpoint_call(self):
        self.backup_on()
        self.statusline()
        cap = self.capture(10)
        with support.FakeUsageServer([(200, {}, reply())]) as srv:
            rc, out, err = self.main("menu-json", url=srv.url)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(srv.requests), 0)
        a = json.loads(out)["accounts"][0]
        self.assertEqual([(m["id"], m["source"], m["pct"]) for m in a["meters"]],
                         [("session", "statusline", 44.0), ("weekly", "statusline", 60.0)])
        self.assertEqual(a["meters"][0]["read_at"], cap["captured_at"])
        self.assertIsNone(a["error"])
        self.assertEqual(self.judged, [self.k1])
        live = json.loads((self.h.state / "live.json").read_text())["accounts"][self.k1]
        self.assertEqual((live["source"], live["fetched"]), ("statusline", cap["captured_at"]))

    def test_capture_older_than_cadence_reads_the_endpoint(self):
        self.backup_on()
        self.statusline()
        self.capture(300)                                  # cadence is 60 s
        with support.FakeUsageServer([(200, {}, reply())]) as srv:
            rc, out, err = self.main("menu-json", url=srv.url)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(srv.requests), 1)
        self.assertEqual(json.loads(out)["accounts"][0]["meters"][0]["source"], "service")

    def test_rejected_capture_never_counts(self):
        self.backup_on()
        self.statusline(accept=False)
        self.capture(10)
        with support.FakeUsageServer([(200, {}, reply())]) as srv:
            rc, out, err = self.main("menu-json", url=srv.url)
            self.assertEqual(len(srv.requests), 1)
        self.assertEqual([m["source"] for m in json.loads(out)["accounts"][0]["meters"]], ["service", "service"])
        self.assertEqual(json.loads(out)["accounts"][0]["meters"][0]["pct"], 37.0)

    def test_backup_off_unchanged(self):
        self.backup_on(False)
        self.statusline()
        self.capture(10)
        with support.FakeUsageServer([(200, {}, reply())]) as srv:
            rc, out, err = self.main("menu-json", url=srv.url)
            self.assertEqual(len(srv.requests), 1)
        self.assertEqual(self.judged, [])
        self.assertEqual(json.loads(out)["accounts"][0]["meters"][0]["source"], "service")

    def test_manual_refresh_reads_the_endpoint(self):
        self.backup_on()
        self.statusline()
        self.capture(10)
        with support.FakeUsageServer([(200, {}, reply())]) as srv:
            rc, out, err = self.main("menu-json", "--force", url=srv.url)
            self.assertEqual(len(srv.requests), 1)

    def test_per_model_weekly_keeps_its_service_age(self):
        self.backup_on()
        self.statusline()
        r = reply()
        r["limits"].append({"kind": "weekly_scoped", "percent": 18, "resets_at": r["limits"][1]["resets_at"],
                            "scope": {"model": {"display_name": "Fable 5"}}})
        with support.FakeUsageServer([(200, {}, r)]) as srv:
            self.main("menu-json", "--force", url=srv.url)       # a service reading first
        service_at = json.loads((self.h.state / "live.json").read_text())["accounts"][self.k1]["fetched"]
        import time
        time.sleep(1.1)                                   # times are whole seconds: the capture must be newer
        cap = self.capture(0)
        rc, out, err = self.main("menu-json")
        self.assertEqual(rc, 0, err)
        m = {x["id"]: x for x in json.loads(out)["accounts"][0]["meters"]}
        self.assertEqual((m["session"]["source"], m["session"]["read_at"]), ("statusline", cap["captured_at"]))
        self.assertEqual((m["weekly:fable"]["source"], m["weekly:fable"]["read_at"]), ("service", service_at))
        self.assertEqual(m["weekly:fable"]["pct"], 18.0)


class Exits(Base):
    def test_failure_exit_1_one_line(self):
        r = self.run_cli("report", "--since", "garbage")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)
        self.assertIn("bad date 'garbage'", r.stderr)
        self.assertIn("garbage", (self.h.state / "logs" / "engine.log").read_text())   # the trace is logged

    def test_usage_error_exit_1_one_line(self):
        r = self.run_cli("report", "--by", "nonsense")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)
        r = self.run_cli()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)

    def test_bringup_exit_2(self):
        (self.h.state / "live.json").write_text(json.dumps({"schema": 9, "accounts": {}}))
        r = self.run_cli("menu-json")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)
        self.assertTrue(r.stderr.startswith("jusage: live.json:"), r.stderr)

    def test_db_from_newer_jusage_exit_2(self):
        c = sqlite3.connect(self.h.state / "usage.db")
        c.execute("PRAGMA user_version=4")
        c.close()
        r = self.run_cli("scan")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.startswith("jusage: usage.db:"), r.stderr)

    def test_state_dir_tightened_before_bringup(self):
        os.chmod(self.h.state, 0o755)                    # RULING 11: an old 0755 folder never refuses forever
        r = self.run_cli("scan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.stat(self.h.state).st_mode & 0o777, 0o700)

    def test_version_prints_VERSION(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), (ROOT / "VERSION").read_text().strip())


class ShareAndDashboard(Base):
    def fake_share(self, rewrite=True):
        calls = {"build": [], "write": [], "load": []}

        def build_report(conn, cfg, live, pricing, projects, *, session_share=None, now=None):
            self.assertIsInstance(session_share, dict)
            self.assertIsNotNone(now)
            calls["build"].append(projects)
            return {"report_version": 3, "user": cfg.user}

        def write_report(d, report, cfg):
            calls["write"].append(Path(d))
            (Path(d) / f"{cfg.user}@mac-{cfg.install_id}.json").write_text(json.dumps(report))

        def load_reports(d, own):
            calls["load"].append(own)
            return [{"user": "Sam"}], ["skipped Kim@mac-e5f6.json: report_version 1"]

        self.stand_in("ju.share", report_id=lambda cfg: (cfg.user, "mac", f"{cfg.user}@mac-{cfg.install_id}.json"),
                      build_report=build_report, write_report=write_report, load_reports=load_reports,
                      should_rewrite=lambda last, changed, now: rewrite, people=lambda reports, now: [],
                      meter_split=lambda meter, uuid, windows, reports, now, *, me: None)
        return calls

    def fake_page(self):
        calls = {"render": [], "write": []}

        def render(reports, notes, version, now, rules):
            calls["render"].append((reports, notes, rules))
            return "<html></html>"

        def write(path, html):
            calls["write"].append(Path(path))
            Path(path).write_text(html)

        self.stand_in("ju.dash", page=None)
        self.stand_in("ju.dash.page", render=render, write=write)
        return calls

    def test_update_out_honoured(self):
        reports = self.h.home / "shared"
        out = self.h.home / "elsewhere" / "page.html"
        out.parent.mkdir()
        s, p = self.fake_share(), self.fake_page()
        rc, stdout, err = self.main("update", "--reports", str(reports), "--user", "Sam", "--out", str(out))
        self.assertEqual(rc, 0, err)
        self.assertEqual(p["write"], [out])
        self.assertTrue(out.exists())
        self.assertFalse((self.h.state / "dashboard.html").exists())
        self.assertEqual(s["write"], [reports])
        cfg = self.cfg()
        self.assertEqual((cfg["reports_dir"], cfg["user"]), (str(reports), "Sam"))
        self.assertEqual(p["render"][0][2]["stale_after_s"], 900)

    def test_update_accepts_projects(self):
        s, _ = self.fake_share(), self.fake_page()
        rc, _, err = self.main("update", "--reports", str(self.h.home / "shared"), "--projects")
        self.assertEqual(rc, 0, err)
        self.assertEqual(s["build"], [True])
        self.assertTrue(self.cfg()["share_projects"])
        rc, _, err = self.main("share", "--no-projects")
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.cfg()["share_projects"])

    def test_missing_shared_folder_needs_reports_flag(self):
        with mock.patch.dict(os.environ, {}):
            self.fake_share()
            self.main("set-user", "Sam")
            rc, _, err = self.main("share")
            self.assertEqual(rc, 1)
            self.assertIn("--reports", err)

    def test_menu_json_rewrites_only_when_due(self):
        reports = self.h.home / "shared"
        reports.mkdir()
        s, p = self.fake_share(rewrite=False), self.fake_page()
        self.main("set-user", "Sam")
        rc, _, _ = self.main("share", "--reports", str(reports))          # explicit share always writes
        self.assertEqual(len(s["write"]), 1)
        rc, out, err = self.main("menu-json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(s["write"]), 1)          # should_rewrite said no
        self.assertEqual(p["render"], [])             # the dashboard is rebuilt only with the report
        feed = json.loads(out)
        self.assertIsNotNone(feed["share"]["written_at"])
        self.assertIsNone(feed["share"]["error"])
        self.assertIn("skipped Kim@mac-e5f6.json: report_version 1", feed["notes"])

    def test_menu_json_rewrite_rebuilds_dashboard(self):
        reports = self.h.home / "shared"
        reports.mkdir()
        s, p = self.fake_share(rewrite=True), self.fake_page()
        self.main("set-user", "Sam")
        self.main("share", "--reports", str(reports))
        rc, out, err = self.main("menu-json")
        self.assertEqual(len(s["write"]), 2)
        self.assertEqual(p["write"], [self.h.state / "dashboard.html"])
        self.assertEqual(json.loads(out)["dashboard"], str(self.h.state / "dashboard.html"))

    def test_menu_json_share_error_keeps_feed(self):
        self.fake_share()
        reports = self.h.home / "shared"
        reports.mkdir()
        self.main("share", "--reports", str(reports))
        reports.rename(self.h.home / "gone")
        rc, out, err = self.main("menu-json")
        self.assertEqual(rc, 0, err)
        feed = json.loads(out)
        self.assertIn("missing", feed["share"]["error"])
        self.assertEqual(feed["accounts"][0]["key"], self.k1)


class Commands(Base):
    def test_config_read_every_and_python(self):
        r = self.run_cli("config", "read-every", "120")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_cli("config", "get", "read_every_s").stdout.strip(), "120")
        r = self.run_cli("config", "read-every", "5")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertEqual(self.run_cli("config", "python", "/bin/sh").returncode, 0)
        self.assertEqual(self.run_cli("config", "get", "python").stdout, "/bin/sh\n")
        self.assertEqual(self.run_cli("config", "python", "relative/python").returncode, 1)
        self.assertEqual(self.run_cli("config", "get", "reports_dir").stdout, "\n")
        whole = json.loads(self.run_cli("config", "get").stdout)
        self.assertEqual(whole["read_every_s"], 120)

    def test_statusline_on_off_go_through_e15(self):
        from ju.contracts import JusageError

        class StatuslineRefused(JusageError):
            pass

        calls = []

        def turn_off(state, key):
            raise StatuslineRefused("settings.json statusLine was changed by someone else; not touched")

        self.stand_in("ju.statusline", StatuslineRefused=StatuslineRefused,
                      turn_on=lambda state, key: calls.append(("on", Path(state), key)), turn_off=turn_off)
        rc, out, err = self.main("statusline", "on", "--folder", str(self.h.home / ".claude"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls, [("on", self.h.state, self.k1)])
        rc, out, err = self.main("statusline", "off", "--all")
        self.assertEqual(rc, 0, err)                 # --all off: only folders recorded as on (none here)
        with mock.patch.object(CU, "_statusline_enabled", lambda cfg: [self.k1]):
            rc, out, err = self.main("statusline", "off", "--all")
        self.assertEqual(rc, 1)
        self.assertIn("changed by someone else", err)
        rc, out, err = self.main("statusline", "on")
        self.assertEqual(rc, 1)                      # needs --folder or --all

    def test_statusline_status_lists_folders(self):
        rc, out, err = self.main("statusline", "status")
        self.assertEqual(rc, 0, err)
        self.assertIn(self.k1, out)
        self.assertIn("off", out)

    def test_doctor_prints_and_never_the_token(self):
        self.items = {self.service_for(self.k1): login_blob()}
        r = self.run_cli("doctor")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.service_for(self.k1), r.stdout)
        self.assertTrue(r.stdout.strip().splitlines()[-1].startswith("OK "), r.stdout)
        self.assertNotIn("ZZZZZZZZ", r.stdout)

    def test_scan_and_report(self):
        self.transcript()
        r = self.run_cli("scan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 new", r.stdout)
        r = self.run_cli("report", "--by", "model")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("$ API-rate", r.stdout)
        self.assertIn("claude-opus-5", r.stdout)
        r = self.run_cli("report", "--by", "blocks", "--last", "6")
        self.assertIn("open", r.stdout)
        r = self.run_cli("report", "--account", ".claude", "--since", "all")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("report", "--account", ".nope")
        self.assertEqual(r.returncode, 1)

    def test_live_prints_one_block_per_account(self):
        self.make_account(".claude-newaccount", "pat@example.com", "u-2")
        rc, out, err = self.main("live")
        self.assertEqual(rc, 0, err)
        heads = [l for l in out.splitlines() if l.startswith("#")]
        self.assertEqual([h.split()[0] for h in heads], ["#1", "#2"])

    def test_set_user(self):
        rc, out, err = self.main("set-user", "Lee")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.cfg()["user"], "Lee")


if __name__ == "__main__":
    unittest.main()
