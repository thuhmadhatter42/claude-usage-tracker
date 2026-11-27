"""E20 sharing: the per-person report in a synced folder, who is one person, who spent what."""
import hashlib
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ju import config, ledger_db, pricing, share, timeutil
from ju.config import ConfigError, ConfigStore
from ju.contracts import Meter
from ju.timeutil import parse_iso

from tests.engine import support

UTC = timezone.utc
NOW = datetime(2026, 11, 26, 18, 0, 0, tzinfo=UTC)
UUID_A = "11111111-1111-1111-1111-111111111111"
UUID_B = "22222222-2222-2222-2222-222222222222"
PRICES = {
    "_multipliers": {"cache_5m": 1.25, "cache_1h": 2.0},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
}


def quiet(*a, **k):
    pass


def iso(d):
    return d.isoformat()


def day_entry(tokens=1000, usd=1.0, calls=2):
    return {"input": 0, "cache_5m": 0, "cache_1h": 0, "cache_read": 0, "output": tokens,
            "calls": calls, "usd": usd, "unpriced": 0, "models": {}, "accounts": {}, "projects": {}}


def block(start, hours=5, usd=10.0):
    return {"start": iso(start), "end": iso(start + timedelta(hours=hours)), "tokens": 1000, "calls": 3, "usd": usd}


def peer(user="Lee", host="lee-mac", install="c3d4", uuids=(UUID_A,), generated=NOW, blocks=None, days=None,
         live=None, **over):
    r = {"report_version": 3, "user": user, "host": host, "install_id": install, "tool_version": "0.2.0",
         "generated": iso(generated),
         "accounts": [{"number": i + 1, "email": f"{user}@x.test", "uuid": u}
                      for i, u in enumerate(uuids)],
         "live": live if live is not None else {},
         "days": days if days is not None else {"2026-11-26": day_entry()},
         "blocks": blocks if blocks is not None else {u: [] for u in uuids},
         "session_share": {}}
    r.update(over)
    return r


def acct_block(**kw):
    """A full feed §1.7 account block (the dashboard fixtures' builder), for account A."""
    from tests.engine.test_dash_fixtures import account
    warning = kw.pop("warning", None)
    return dict(account(uuid=UUID_A, **kw), warning=warning)


def meter_block(mid, **kw):
    from tests.engine.test_dash_fixtures import meter
    return meter(mid, **kw)


def legacy(user="Old", host="old-mac", version="0.1.21", days=None):
    return {"user": user, "host": host, "tool_version": version,
            "generated": "2026-11-26T09:00:00+00:00",
            "days": days if days is not None else {"2026-11-26": {"input": 0, "cache_5m": 0, "cache_1h": 0,
                                                               "cache_read": 0, "output": 500, "calls": 1,
                                                               "cost": 2.5}},
            "blocks": {"Old #1": [{"start": "2026-11-26T08:00", "end": "2026-11-26T13:00", "tokens": 1, "cost": 9}]}}


class FolderCase(unittest.TestCase):
    def setUp(self):
        self.home = support.temp_home()
        self.addCleanup(self.home.cleanup)
        self.dir = Path(tempfile.mkdtemp(prefix="jusage-reports-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def put(self, name, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj)
        (self.dir / name).write_text(text)

    def cfg(self, **kw):
        with ConfigStore.edit(self.home.state) as c:
            c.user = kw.get("user", "Sam")
            c.install_id = kw.get("install_id", "a1b2")
            for k, v in kw.items():
                if k not in ("user", "install_id"):
                    setattr(c, k, v)
        return ConfigStore.load(self.home.state)


class ReportIdTests(FolderCase):
    def test_report_id_name(self):
        cfg = self.cfg()
        user, host, name = share.report_id(cfg)
        self.assertEqual(user, "Sam")
        self.assertEqual(host, socket.gethostname().split(".")[0])
        self.assertEqual(name, f"Sam@{host}-a1b2.json")

    def test_no_user_set_uses_the_installers_default(self):
        """No name set and no $USER (the app runs the engine with HOME and LANG only): the report still has
        a name, the installer's default (config.user_name), never a refusal that stops sharing."""
        import pwd
        with ConfigStore.edit(self.home.state) as c:
            c.install_id = "a1b2"
        old = os.environ.pop("USER", None)
        try:
            user, _, name = share.report_id(ConfigStore.load(self.home.state))
        finally:
            if old is not None:
                os.environ["USER"] = old
        pw = pwd.getpwuid(os.getuid())
        self.assertEqual(user, (pw.pw_gecos.split() or [pw.pw_name])[0])
        self.assertTrue(name.startswith(user + "@") and name.endswith("-a1b2.json"))

    def test_user_with_slash_refused(self):
        with self.assertRaises(ConfigError):
            share.report_id(self.cfg(user="a/b"))


class BuildTests(FolderCase):
    def setUp(self):
        super().setUp()
        self.conn = ledger_db.open_db(self.home.state, log=quiet)
        self.addCleanup(self.conn.close)
        pf = self.home.home / "pricing.json"
        pf.write_text(json.dumps(PRICES))
        self.pricing = pricing.load(pf)
        self.folder_a = "/a/.claude"
        self.folder_b = "/b/.claude-two"
        self.n = 0

    def add(self, ts, account=None, project="secret-project", output=1_000_000, model="claude-opus-5"):
        self.n += 1
        ledger_db.insert_record(self.conn, ledger_db.Row(
            key=f"k{self.n}", ts=ts, day=ts[:10], account=account or self.folder_a, model=model, project=project,
            session="s", input=0, cache_5m=0, cache_1h=0, cache_read=0, output=output,
            stop_reason="end_turn", complete=1))

    def cfg_ids(self):
        return self.cfg(identities={self.folder_a: {"email": "a@x.test", "uuid": UUID_A},
                                    self.folder_b: {"email": "a@x.test", "uuid": UUID_A}},
                        accounts={self.folder_a: 1, self.folder_b: 3})

    def build(self, cfg=None, projects=False, live=None, **kw):
        return share.build_report(self.conn, cfg or self.cfg_ids(), live if live is not None else {},
                                  self.pricing, projects, now=NOW, **kw)

    def test_report_v3_shape(self):
        self.add("2026-11-26T10:00:00+00:00")
        r = self.build()
        self.assertEqual(r["report_version"], 3)
        self.assertEqual((r["user"], r["install_id"]), ("Sam", "a1b2"))
        self.assertEqual(r["tool_version"], (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip())
        self.assertEqual(parse_iso(r["generated"]), NOW)
        self.assertEqual([a["number"] for a in r["accounts"]], [1, 3])
        self.assertNotIn("key_hash", r["accounts"][0])            # a path hash is guessable: never shared
        self.assertEqual(r["accounts"][0]["uuid"], UUID_A)
        day = r["days"]["2026-11-26"]
        self.assertEqual((day["output"], day["calls"], day["usd"]), (1_000_000, 1, 25.0))
        self.assertEqual(day["models"]["claude-opus-5"]["usd"], 25.0)
        self.assertEqual(day["accounts"][UUID_A]["usd"], 25.0)
        # a built report passes the loader's own validation
        self.put("Sam@mac-a1b2.json", r)
        reports, notes = share.load_reports(self.dir, "zzzz")
        self.assertEqual((len(reports), notes), (1, []))

    def test_ninety_days_only(self):
        self.add("2026-08-29T10:00:00+00:00")       # day 90 back: kept (today + 89 before)
        self.add("2026-08-28T10:00:00+00:00")       # 91st day: dropped
        r = self.build()
        self.assertEqual(sorted(r["days"]), ["2026-08-29"])

    def test_blocks_keyed_by_uuid_two_folders_one_account(self):
        self.add("2026-11-26T10:20:00+00:00", account=self.folder_a)
        self.add("2026-11-26T11:20:00+00:00", account=self.folder_b)
        r = self.build()
        self.assertEqual(list(r["blocks"]), [UUID_A])
        self.assertEqual(len(r["blocks"][UUID_A]), 2)
        b = r["blocks"][UUID_A][0]
        self.assertEqual(parse_iso(b["end"]) - parse_iso(b["start"]), timedelta(hours=5))
        self.assertEqual(b["usd"], 25.0)

    def test_last_fifty_blocks(self):
        for i in range(60):
            self.add((datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=6 * i)).isoformat())
        r = self.build()
        self.assertEqual(len(r["blocks"][UUID_A]), 50)

    def test_projects_only_with_flag(self):
        self.add("2026-11-26T10:00:00+00:00", project="secret-project")
        self.add("2026-11-26T10:01:00+00:00", project="agent-xyz")
        off = self.build(projects=False)
        self.assertNotIn("secret-project", json.dumps(off))
        self.assertEqual(off["days"]["2026-11-26"]["projects"], {})
        on = self.build(projects=True)
        self.assertEqual(sorted(on["days"]["2026-11-26"]["projects"]), ["secret-project", "subagents"])
        self.assertEqual(on["days"]["2026-11-26"]["projects"]["secret-project"]["usd"], 25.0)

    def test_live_and_session_share_pass_through(self):
        live = {UUID_A: acct_block(meters=[])}
        ss = {"current": 4.0, "last": None, "median7": None, "list": [], "as_of": NOW.isoformat()}
        r = self.build(live=live, session_share={UUID_A: ss})
        self.assertEqual(r["live"][UUID_A]["meters"], [])
        self.assertEqual(r["session_share"], {UUID_A: ss})

    def test_no_home_paths_leave_this_mac(self):
        """Review-security 2 (J approved): what this Mac writes to the shared folder names accounts by
        uuid only. Each live block's key becomes acct:<uuid>, its folders [], and no path or path
        hash is left anywhere in the report."""
        home = str(Path(self.folder_a).parent)
        live = {UUID_A: acct_block(meters=[])}
        live[UUID_A]["key"], live[UUID_A]["folders"] = self.folder_a, [self.folder_a, self.folder_b]
        r = self.build(live=live)
        self.assertEqual(r["live"][UUID_A]["key"], "acct:" + UUID_A)
        self.assertEqual(r["live"][UUID_A]["folders"], [])
        self.assertEqual(live[UUID_A]["key"], self.folder_a)       # the caller's feed block is untouched
        text = json.dumps(r)
        self.assertNotIn(home, text)
        for f in (self.folder_a, self.folder_b):
            self.assertNotIn(f, text)
            self.assertNotIn(hashlib.sha256(f.encode()).hexdigest()[:8], text)
        self.put("Sam@mac-a1b2.json", r)
        reports, notes = share.load_reports(self.dir, "zzzz")
        self.assertEqual((len(reports), notes), (1, []))
        self.assertEqual(share.people(reports, NOW)[0].accounts[0]["key"], "acct:" + UUID_A)

    def test_live_must_be_object_of_objects(self):
        with self.assertRaises(ValueError):
            self.build(live={UUID_A: []})

    def test_naive_now_refused(self):
        with self.assertRaises(timeutil.NaiveTime):
            share.build_report(self.conn, self.cfg_ids(), {}, self.pricing, False, now=datetime(2026, 11, 26))

    def test_writes_own_file_only_and_removes_old_names(self):
        self.add("2026-11-26T10:00:00+00:00")
        cfg = self.cfg_ids()
        user, host, name = share.report_id(cfg)
        self.put("Lee@lee-mac-c3d4.json", peer())                         # someone else's: untouched
        self.put(f"{user}@{host}.json", {"user": user, "host": host, "days": {}})   # pre-0.1.21 name, no id
        self.put(f"{user}@old-host-a1b2.json", peer(user=user, install="a1b2"))     # this install, old host name
        self.put("Sam@other-mac-ffff.json", peer(user="Sam", install="ffff"))       # my other Mac: untouched
        self.put("garbage.json", "{not json")                                       # unreadable: untouched
        before = (self.dir / "Lee@lee-mac-c3d4.json").read_text()
        path, removed = share.write_report(self.dir, self.build(cfg), cfg)
        self.assertEqual(path, self.dir / name)
        self.assertEqual(sorted(removed), sorted([f"{user}@{host}.json", f"{user}@old-host-a1b2.json"]))
        left = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(left, sorted(["Lee@lee-mac-c3d4.json", "Sam@other-mac-ffff.json", "garbage.json", name]))
        self.assertEqual((self.dir / "Lee@lee-mac-c3d4.json").read_text(), before)
        self.assertEqual(json.loads(path.read_text())["install_id"], "a1b2")
        self.assertFalse([p for p in self.dir.iterdir() if p.name.endswith(".tmp")])

    def test_write_into_missing_folder_is_an_error(self):
        cfg = self.cfg_ids()
        gone = self.dir / "nope"
        with self.assertRaises(ConfigError) as cm:
            share.write_report(gone, self.build(cfg), cfg)
        self.assertIn("missing; pass --reports to create it", str(cm.exception))
        self.assertFalse(gone.exists())

    def test_write_refuses_report_from_another_install(self):
        cfg = self.cfg_ids()
        r = self.build(cfg)
        r["install_id"] = "ffff"
        with self.assertRaises(ValueError):
            share.write_report(self.dir, r, cfg)
        self.assertEqual(list(self.dir.iterdir()), [])


class RewriteTests(unittest.TestCase):
    def test_rewrite_on_change_at_most_5min(self):
        t = NOW
        self.assertTrue(share.should_rewrite(None, True, t))
        self.assertFalse(share.should_rewrite(None, False, t))
        self.assertFalse(share.should_rewrite(t - timedelta(seconds=299), True, t))
        self.assertTrue(share.should_rewrite(t - timedelta(seconds=300), True, t))
        self.assertFalse(share.should_rewrite(t - timedelta(hours=3), False, t))

    def test_naive_clock_refused(self):
        with self.assertRaises(timeutil.NaiveTime):
            share.should_rewrite(None, True, datetime(2026, 11, 26))


class LoadTests(FolderCase):
    def load(self, own="zzzz"):
        return share.load_reports(self.dir, own)

    def test_one_hostile_file_skips_only_itself(self):
        """Review-security 3: a symlink (Syncthing syncs them as links), an oversized file, or JSON
        nested deep enough to raise RecursionError is skipped and named; the good report loads."""
        from unittest import mock
        self.put("Lee@lee-mac-c3d4.json", peer())
        big = self.dir.parent / (self.dir.name + "-big.bin")
        big.write_bytes(b"x" * 10)
        self.addCleanup(big.unlink)
        (self.dir / "link.json").symlink_to(big)
        (self.dir / "huge.json").write_bytes(b" " * (share.REPORT_MAX_BYTES + 1))
        self.put("deep.json", "[[[]]]")
        real = json.loads

        def loads(text, *a, **k):
            if text == "[[[]]]":
                raise RecursionError("maximum recursion depth exceeded")
            return real(text, *a, **k)
        with mock.patch("ju.share.json.loads", loads):
            reports, notes = self.load()
        self.assertEqual([r.user for r in reports], ["Lee"])
        self.assertIn("skipped link.json: not a regular file", notes)
        self.assertIn(f"skipped huge.json: larger than {share.REPORT_MAX_BYTES} bytes", notes)
        self.assertIn("skipped deep.json: unreadable (RecursionError)", notes)

    def test_load_reports_skips_tmp_conflict_and_bad_shape(self):
        self.put("Lee@lee-mac-c3d4.json", peer())
        self.put(".Lee@lee-mac-c3d4.json.ab12.tmp", '{"half": ')
        self.put("Lee@lee-mac-c3d4.sync-conflict-20261126-101010-ABCDEFG.json", peer())
        self.put("Future@mac-9999.json", peer(user="Future", install="9999", report_version=4))
        self.put("Old3@mac-1111.json", peer(user="Old3", install="1111", report_version=1))
        self.put("NoDays@mac-2222.json", {"report_version": 3, "user": "x", "host": "y"})
        self.put("ListBlocks@mac-3333.json", peer(user="ListBlocks", install="3333", blocks=[]))
        self.put("Half@mac-4444.json", '{"report_version": 3, "user": "Ha')
        self.put("NotObject@mac-5555.json", "[1, 2]")
        self.put("readme.txt", "hello")
        reports, notes = self.load()
        self.assertEqual([r.user for r in reports], ["Lee"])
        text = "\n".join(notes)
        for frag in ("Lee@lee-mac-c3d4.sync-conflict", ".tmp", "Future@mac-9999.json", "report_version 4",
                     "Old3@mac-1111.json", "report_version 1", "NoDays@mac-2222.json", "days",
                     "ListBlocks@mac-3333.json", "blocks", "Half@mac-4444.json", "NotObject@mac-5555.json"):
            self.assertIn(frag, text)
        self.assertNotIn("readme.txt", text)
        self.assertEqual(len(notes), 8)
        for n in notes:
            self.assertTrue(n.startswith("skipped "), n)

    def test_future_version_file_names_itself(self):
        self.put("Future@mac-9999.json", peer(user="Future", install="9999", report_version=4))
        reports, notes = self.load()
        self.assertEqual(reports, [])
        self.assertEqual(notes, ["skipped Future@mac-9999.json: report_version 4 is newer than this jusage reads (3)"])

    def test_own_install_is_dropped_whatever_its_name(self):
        self.put("Sam@renamed-a1b2.json", peer(user="Sam", install="a1b2"))
        self.put("Lee@lee-mac-c3d4.json", peer())
        reports, notes = self.load(own="a1b2")
        self.assertEqual([r.install_id for r in reports], ["c3d4"])
        self.assertEqual(notes, [])

    def test_no_own_install_keeps_every_report(self):
        """The dashboard shows everyone, this Mac included (RULING 35): own_install=None drops nothing."""
        self.put("Sam@renamed-a1b2.json", peer(user="Sam", install="a1b2"))
        self.put("Lee@lee-mac-c3d4.json", peer())
        reports, _ = self.load(own=None)
        self.assertEqual(sorted(r.install_id for r in reports), ["a1b2", "c3d4"])

    def test_instants_not_text(self):
        # the same install under two names: text order says X is later, the instant says Y is
        x = peer(install="c3d4", host="old", generated=parse_iso("2026-11-26T10:00:00+02:00"))
        y = peer(install="c3d4", host="new", generated=parse_iso("2026-11-26T09:30:00+00:00"))
        self.assertGreater(x["generated"], y["generated"])
        self.put("Lee@old-c3d4.json", x)
        self.put("Lee@new-c3d4.json", y)
        reports, notes = self.load()
        self.assertEqual([r.host for r in reports], ["new"])
        self.assertEqual(len(notes), 1)
        self.assertIn("ignoring Lee@old-c3d4.json", notes[0])
        self.assertIn("Lee@new-c3d4.json is newer", notes[0])
        self.assertTrue((self.dir / "Lee@old-c3d4.json").exists())      # never deleted: it is theirs
        self.assertEqual(reports[0].generated, parse_iso("2026-11-26T09:30:00+00:00"))

    def test_time_without_offset_skipped(self):
        r = peer()
        r["generated"] = "2026-11-26T09:30:00"
        self.put("Lee@lee-mac-c3d4.json", r)
        reports, notes = self.load()
        self.assertEqual(reports, [])
        self.assertIn("generated", notes[0])

    def test_bad_block_time_skipped(self):
        b = block(NOW - timedelta(hours=1))
        b["start"] = "2026-11-26T10:00:00"
        self.put("Lee@lee-mac-c3d4.json", peer(blocks={UUID_A: [b]}))
        reports, notes = self.load()
        self.assertEqual(reports, [])
        self.assertIn("blocks", notes[0])

    def test_legacy_report_kept_and_flagged(self):
        self.put("Old@old-mac.json", legacy())
        reports, notes = self.load()
        self.assertEqual(notes, [])
        r = reports[0]
        self.assertTrue(r.legacy)
        self.assertEqual((r.user, r.host, r.accounts, r.blocks), ("Old", "old-mac", [], {}))
        self.assertEqual(r.days["2026-11-26"]["usd"], 2.5)
        self.assertEqual(r.days["2026-11-26"]["output"], 500)

    def test_pre_v3_without_version_or_days_is_not_a_report(self):
        self.put("A@m.json", {"user": "A", "host": "m", "days": {}})                       # no tool_version
        self.put("B@m.json", {"user": "B", "host": "m", "tool_version": "banana", "days": {}})
        self.put("C@m.json", {"user": "C", "host": "m", "tool_version": "0.1.30", "days": []})
        reports, notes = self.load()
        self.assertEqual(reports, [])
        self.assertEqual(len(notes), 3)

    def test_missing_folder_is_an_error(self):
        with self.assertRaises(ConfigError):
            share.load_reports(self.dir / "gone", "zzzz")

    def test_v3_day_with_bad_number_skipped(self):
        d = day_entry()
        d["usd"] = "lots"
        self.put("Lee@lee-mac-c3d4.json", peer(days={"2026-11-26": d}))
        reports, notes = self.load()
        self.assertEqual(reports, [])
        self.assertIn("days", notes[0])


class VersionTests(unittest.TestCase):
    def test_version_compare_0_1_100(self):
        self.assertGreater(share.version_tuple("0.1.100"), share.version_tuple("0.1.22"))
        self.assertLess("0.1.100", "0.1.22")                   # the old string-compare bug, for the record
        self.assertEqual(share.version_tuple("0.2.0"), (0, 2, 0))
        self.assertLess(share.version_tuple("0.1.21"), share.version_tuple("0.1.22"))

    def test_version_refuses_garbage(self):
        for bad in ("", "1", "0.1.x", "v0.1.2", "0.1.2-beta", "0..1", None, 3, "0.1.-2", " 0.1.2"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    share.version_tuple(bad)


class PeopleTests(FolderCase):
    def load(self, **files):
        for name, obj in files.items():
            self.put(name, obj)
        return share.load_reports(self.dir, "zzzz")[0]

    def test_two_macs_one_person(self):
        reports = self.load(**{
            "Lee@mac1-c3d4.json": peer(install="c3d4", host="mac1", generated=NOW - timedelta(minutes=2),
                                       days={"2026-11-26": day_entry(1000, 4.0, 2), "2026-11-25": day_entry(500, 1.0, 1)}),
            "Lee@mac2-e5f6.json": peer(install="e5f6", host="mac2", generated=NOW,
                                       days={"2026-11-26": day_entry(2000, 6.0, 3)}),
            "Kim@mac3-7777.json": peer(user="Kim", install="7777", host="mac3", uuids=(UUID_B,)),
            "Lee2@mac4-8888.json": peer(user="Lee", install="8888", host="mac4", uuids=(UUID_B,)),   # same name, other account
        })
        ppl = share.people(reports, NOW)
        lee = [p for p in ppl if p.user == "Lee" and UUID_A in p.uuids]
        self.assertEqual(len(lee), 1)
        lee = lee[0]
        self.assertEqual(sorted(lee.install_ids), ["c3d4", "e5f6"])
        self.assertEqual(lee.install_id, "e5f6")                       # the newest install speaks for the person
        self.assertEqual(lee.generated, NOW)
        self.assertEqual(lee.today["tokens"], 3000)
        self.assertEqual(lee.today["usd"], 10.0)
        self.assertEqual(lee.today["calls"], 5)
        self.assertEqual(lee.week["tokens"], 3500)
        self.assertEqual(lee.week["usd"], 11.0)
        self.assertEqual(len(ppl), 3)      # Lee(A, two Macs), Kim(B), Lee(B): same name alone never merges

    def test_week_is_seven_days_back(self):
        days = {"2026-11-26": day_entry(1, 1.0), "2026-11-20": day_entry(10, 1.0), "2026-11-19": day_entry(100, 1.0)}
        p = share.people(self.load(**{"Lee@m-c3d4.json": peer(days=days)}), NOW)[0]
        self.assertEqual(p.week["tokens"], 11)

    def test_today_uses_readers_local_date(self):
        # NOW is 18:00Z; the report's day keys are the peer's own local dates, compared as dates
        p = share.people(self.load(**{"Lee@m-c3d4.json": peer()}), NOW)[0]
        self.assertEqual(p.today["tokens"], 1000 if NOW.astimezone().date().isoformat() == "2026-11-26" else 0)

    def test_stale_after_15_minutes(self):
        p = share.people(self.load(**{"Lee@m-c3d4.json": peer(generated=NOW - timedelta(seconds=900))}), NOW)[0]
        self.assertFalse(p.is_stale(NOW - timedelta(seconds=1)))   # 899 s
        self.assertTrue(p.is_stale(NOW))                          # 900 s: RULING 33
        self.assertTrue(p.is_stale(NOW + timedelta(minutes=16)))
        fresh = share.people(self.load(**{"Lee@m-c3d4.json": peer(generated=NOW - timedelta(minutes=14))}), NOW)[0]
        self.assertFalse(fresh.is_stale(NOW))

    def test_hostile_or_broken_live_blocks_are_skipped(self):
        """Review-security 1b: a report's live blocks are checked field by field; one that would
        inject into the page or crash the builder is skipped and named, never rendered."""
        from tests.engine.test_dash_fixtures import account, meter
        good = account(uuid=UUID_A)
        bad = {
            "state": account(uuid=UUID_A, meters=[meter(state='x" onmouseover="y')]),
            "pct_text": account(uuid=UUID_A, meters=[dict(meter(), pct="40")]),
            "pct_high": account(uuid=UUID_A, meters=[dict(meter(), pct=500.0)]),
            "no_pace": account(uuid=UUID_A, meters=[{k: v for k, v in meter().items() if k != "pace"}]),
            "words_obj": account(uuid=UUID_A, meters=[meter(words={"a": 1})]),
            "read_at": account(uuid=UUID_A, meters=[dict(meter(), read_at="yesterday")]),
            "email_obj": dict(good, email={"x": 1}),
            "uuid_other": account(uuid=UUID_B),
            "error_kind": dict(good, error={"kind": "boom", "text": "t", "until": None}),
            "share_text": dict(good, session_share={"current": "4", "last": None, "median7": None,
                                                   "list": [], "as_of": NOW.isoformat()}),
        }
        for why, blk in bad.items():
            with self.subTest(why):
                self.put("Lee@m-c3d4.json", peer(live={UUID_A: blk}))
                reports, notes = share.load_reports(self.dir, "zzzz")
                self.assertEqual(reports, [])
                self.assertEqual(len(notes), 1)
                self.assertTrue(notes[0].startswith("skipped Lee@m-c3d4.json: live"), notes[0])
        self.put("Lee@m-c3d4.json", peer(live={UUID_A: good}))
        self.assertEqual(len(share.load_reports(self.dir, "zzzz")[0]), 1)

    def test_bad_day_parts_are_skipped(self):
        for why, d in {"model_text": dict(day_entry(), models={"m": "x"}),
                       "acct_tokens": dict(day_entry(), accounts={UUID_A: {"tokens": "9", "usd": 1.0}}),
                       "proj_list": dict(day_entry(), projects={"p": []}),
                       "not_a_date": None}.items():
            with self.subTest(why):
                days = {"2026-99-99": day_entry()} if d is None else {"2026-11-26": d}
                self.put("Lee@m-c3d4.json", peer(days=days))
                reports, notes = share.load_reports(self.dir, "zzzz")
                self.assertEqual(reports, [], notes)

    def test_peer_accounts_have_no_runs_out(self):
        live = {UUID_A: acct_block(meters=[meter_block("weekly", pct=40.0, state="green",
                                                       runs_out="2026-11-27T10:00:00+00:00", e=0.4)])}
        p = share.people(self.load(**{"Lee@m-c3d4.json": peer(live=live)}), NOW)[0]
        meter = p.accounts[0]["meters"][0]
        self.assertNotIn("runs_out", meter["pace"])
        self.assertEqual(meter["pace"]["state"], "green")

    def test_peer_words_lose_the_runs_out_placeholder(self):
        """Review-engine 9: with runs_out removed, no `{runs_out}` may be left for the app to show."""
        live = {UUID_A: acct_block(meters=[meter_block(
            "weekly", pct=80.0, state="amber", runs_out="2026-11-27T10:00:00+00:00", e=0.4,
            words="At this pace you hit the limit {runs_out}, 2h 05m before the reset.")])}
        p = share.people(self.load(**{"Lee@m-c3d4.json": peer(live=live)}), NOW)[0]
        words = p.accounts[0]["meters"][0]["pace"]["words"]
        self.assertNotIn("{runs_out}", words)
        self.assertEqual(words, "At this pace you hit the limit before the reset.")
        self.assertEqual(live[UUID_A]["meters"][0]["pace"]["runs_out"], "2026-11-27T10:00:00+00:00")  # input untouched

    def test_newest_report_speaks_for_an_account(self):
        old = {UUID_A: acct_block(warning="old")}
        new = {UUID_A: acct_block(warning="new")}
        reports = self.load(**{
            "Lee@m1-c3d4.json": peer(install="c3d4", generated=NOW - timedelta(minutes=1), live=old),
            "Lee@m2-e5f6.json": peer(install="e5f6", generated=NOW, live=new)})
        p = share.people(reports, NOW)[0]
        self.assertEqual([a["warning"] for a in p.accounts], ["new"])

    def test_legacy_person_is_flagged_and_alone(self):
        reports = self.load(**{"Old@old-mac.json": legacy(), "Lee@m-c3d4.json": peer()})
        ppl = share.people(reports, NOW)
        old = [p for p in ppl if p.user == "Old"][0]
        self.assertTrue(old.legacy)
        self.assertEqual(old.accounts, [])
        self.assertFalse([p for p in ppl if p.user == "Lee"][0].legacy)

    def test_to_feed_shape(self):
        p = share.people(self.load(**{"Lee@m-c3d4.json": peer(host="lee-mac")}), NOW)[0]
        f = p.to_feed()
        self.assertEqual(sorted(f), ["accounts", "generated", "host", "install_id", "today", "user", "week"])
        self.assertEqual((f["user"], f["host"], f["install_id"]), ("Lee", "lee-mac", "c3d4"))
        self.assertEqual(parse_iso(f["generated"]), NOW)
        json.dumps(f)


def v039(user="Ari", host="ari-mac", install="9f9f", version="0.1.39", labels=None, ids=None, blocks=None):
    """A report the way 0.1.39 writes it: no report_version, blocks keyed by account label (the
    account's email, else '<user> #n'), account_ids label -> claude.ai uuid, spend under `cost`."""
    labels = labels if labels is not None else ["ari@x.test"]
    ids = ids if ids is not None else {"ari@x.test": UUID_A}
    return {"user": user, "host": host, "install_id": install, "tool_version": version,
            "generated": "2026-11-26T17:55:00+00:00", "accounts": labels, "account_ids": ids,
            "live": {labels[0]: {"meters": []}},
            "days": {"2026-11-26": {"input": 0, "cache_5m": 0, "cache_1h": 0, "cache_read": 0, "output": 700,
                                    "calls": 4, "cost": 3.0, "unpriced": 0, "models": {}, "accounts": {},
                                    "projects": {}}},
            "blocks": blocks if blocks is not None else {labels[0]: [
                {"start": "2026-11-26T08:00-08:00", "end": "2026-11-26T13:00-08:00", "tokens": 5, "calls": 2,
                 "cost": 20.0}]}}


class SplitTests(FolderCase):
    RESET = datetime(2026, 11, 26, 20, 0, 0, tzinfo=UTC)

    def meter(self, kind="session", resets=None):
        period = 18000 if kind == "session" else 604800
        return Meter(kind, kind.title(), 50.0, resets or self.RESET, period)

    def reports(self, **files):
        for name, obj in files.items():
            self.put(name, obj)
        return share.load_reports(self.dir, "zzzz")[0]

    def split(self, meter, own, reports, now=NOW):
        return share.meter_split(meter, UUID_A, own, reports, now, me=("Sam", "a1b2"))

    def test_split_prorated_open_window(self):
        # session period = 15:00..20:00; now = 18:00.
        # Sam's open window began 16:00 and has spent $30 so far: inside the period for its whole run.
        own = [block(datetime(2026, 11, 26, 16, 0, tzinfo=UTC), usd=30.0)]
        # Lee's window 13:00..18:00 spent $50; the period cuts it at 15:00: 3h of 5h inside = $30.
        reports = self.reports(**{"Lee@m-c3d4.json": peer(
            blocks={UUID_A: [block(datetime(2026, 11, 26, 13, 0, tzinfo=UTC), usd=50.0)]})})
        s = self.split(self.meter(), own, reports)
        self.assertEqual(s.period_start, datetime(2026, 11, 26, 15, 0, tzinfo=UTC))
        parts = {p.user: p for p in s.parts}
        self.assertEqual(parts["Sam"].usd, 30.0)       # not discounted a second time by dividing by five hours
        self.assertEqual(parts["Lee"].usd, 30.0)
        self.assertEqual((parts["Sam"].share, parts["Lee"].share), (0.5, 0.5))
        self.assertEqual(parts["Sam"].install_id, "a1b2")
        self.assertEqual(parts["Lee"].install_id, "c3d4")
        self.assertEqual(s.missing, [])

    def test_open_window_cut_by_period_start_is_prorated_by_time_run(self):
        # open window 14:00..19:00 (now 18:00): has run 4h and spent $40; period starts 15:00 so 3 of 4 run-hours count
        own = [block(datetime(2026, 11, 26, 14, 0, tzinfo=UTC), usd=40.0)]
        s = self.split(self.meter(), own, [])
        self.assertEqual(s.parts[0].usd, 30.0)

    def test_window_after_now_or_before_period_counts_nothing(self):
        own = [block(datetime(2026, 11, 26, 8, 0, tzinfo=UTC), usd=99.0),     # ended 13:00, before the period
               block(datetime(2026, 11, 26, 19, 0, tzinfo=UTC), usd=99.0)]    # starts after now
        self.assertIsNone(self.split(self.meter(), own, []))

    def test_weekly_period_seven_days(self):
        resets = datetime(2026, 11, 27, 0, 0, tzinfo=UTC)
        own = [block(datetime(2026, 11, 20, 1, 0, tzinfo=UTC), usd=7.0),      # inside (period began 11-20 00:00)
               block(datetime(2026, 11, 19, 1, 0, tzinfo=UTC), usd=7.0)]      # before it
        s = self.split(self.meter("weekly", resets), own, [])
        self.assertEqual(s.parts[0].usd, 7.0)
        self.assertEqual(s.period_start, datetime(2026, 11, 20, 0, 0, tzinfo=UTC))

    def test_reset_in_the_past_period_ends_at_the_reset(self):
        own = [block(datetime(2026, 11, 26, 16, 0, tzinfo=UTC), usd=10.0)]
        s = self.split(self.meter(resets=datetime(2026, 11, 26, 17, 0, tzinfo=UTC)), own, [])
        # period 12:00..17:00, window ran 16:00..17:00 of its 5h: the window ended at 21:00, span to period end = 1h
        self.assertEqual(s.parts[0].usd, 10.0)

    def test_no_reset_time_no_split(self):
        m = Meter("session", "Session", 10.0, None, 18000)
        self.assertIsNone(self.split(m, [block(NOW, usd=1.0)], []))

    def test_dollars_not_percent_and_sorted_desc(self):
        own = [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=10.0)]
        reports = self.reports(**{"Lee@m-c3d4.json": peer(
            blocks={UUID_A: [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=30.0)]})})
        s = self.split(self.meter(), own, reports)
        self.assertEqual([p.user for p in s.parts], ["Lee", "Sam"])
        self.assertEqual([p.share for p in s.parts], [0.75, 0.25])

    def test_other_accounts_blocks_ignored(self):
        reports = self.reports(**{"Lee@m-c3d4.json": peer(
            uuids=(UUID_A, UUID_B),
            blocks={UUID_A: [], UUID_B: [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=30.0)]})})
        self.assertIsNone(self.split(self.meter(), [], reports))

    def test_legacy_report_missing_not_split(self):
        own = [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=10.0)]
        reports = self.reports(**{"Old@old-mac.json": legacy()})
        s = self.split(self.meter(), own, reports)
        self.assertEqual([p.user for p in s.parts], ["Sam"])
        self.assertEqual(s.parts[0].share, 1.0)
        self.assertEqual(s.missing, ["Old"])
        # alone: the legacy peer is named, no number invented for it
        s2 = self.split(self.meter(), [], reports)
        self.assertEqual((s2.parts, s2.missing), ([], ["Old"]))

    def test_0_1_39_report_splits_through_its_account_ids(self):
        # 08:00-08:00 is 16:00Z: window 16:00..21:00Z, now 18:00Z, period 15:00..20:00Z: 2h of 2h run = full
        own = [block(datetime(2026, 11, 26, 16, 0, tzinfo=UTC), usd=20.0)]
        reports = self.reports(**{"Ari@ari-mac-9f9f.json": v039()})
        self.assertFalse(reports[0].legacy)
        s = self.split(self.meter(), own, reports)
        parts = {p.user: p for p in s.parts}
        self.assertEqual(parts["Ari"].usd, 20.0)
        self.assertEqual(parts["Ari"].install_id, "9f9f")
        self.assertEqual(parts["Sam"].share, 0.5)
        self.assertEqual(s.missing, [])

    def test_0_1_39_other_account_not_counted_and_not_missing(self):
        reports = self.reports(**{"Ari@ari-mac-9f9f.json": v039(ids={"ari@x.test": UUID_B})})
        self.assertIsNone(self.split(self.meter(), [], reports))

    def test_0_1_39_label_with_no_uuid_is_missing(self):
        r = v039(labels=["Ari #1"], ids={}, blocks={"Ari #1": [
            {"start": "2026-11-26T08:00-08:00", "end": "2026-11-26T13:00-08:00", "tokens": 5, "calls": 2, "cost": 20.0}]})
        reports = self.reports(**{"Ari@ari-mac-9f9f.json": r})
        self.assertTrue(reports[0].legacy)
        s = self.split(self.meter(), [], reports)
        self.assertEqual((s.parts, s.missing), ([], ["Ari"]))

    def test_0_1_22_is_the_first_splittable_version(self):
        reports = self.reports(**{"Ari@ari-mac-9f9f.json": v039(version="0.1.22"),
                                  "Old@old-mac-1111.json": v039(user="Old", install="1111", version="0.1.21")})
        by = {r.user: r.legacy for r in reports}
        self.assertEqual(by, {"Ari": False, "Old": True})

    def test_0_1_39_bad_block_skipped(self):
        reports, notes = share.load_reports(self.dir, "zzzz") if self.put("A@m-1.json", v039(blocks=[])) is None else (0, 0)
        self.assertEqual(reports, [])
        self.assertIn("blocks", notes[0])

    def test_two_macs_of_one_person_are_one_part(self):
        own = [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=10.0)]
        reports = self.reports(**{
            "Sam@other-ffff.json": peer(user="Sam", install="ffff",
                                        blocks={UUID_A: [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=5.0)]}),
            "Lee@m-c3d4.json": peer(blocks={UUID_A: [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=15.0)]})})
        s = self.split(self.meter(), own, reports)
        parts = {p.user: p for p in s.parts}
        self.assertEqual(parts["Sam"].usd, 15.0)
        self.assertEqual(parts["Sam"].install_id, "a1b2")
        self.assertEqual(len(s.parts), 2)

    def test_blocks_compared_as_instants(self):
        # 15:00Z written with a +02:00 offset is 17:00 local text: inside the period only as an instant
        b = {"start": "2026-11-26T17:00:00+02:00", "end": "2026-11-26T22:00:00+02:00", "tokens": 1, "calls": 1,
             "usd": 8.0}
        reports = self.reports(**{"Lee@m-c3d4.json": peer(blocks={UUID_A: [b]})})
        s = self.split(self.meter(), [], reports)
        self.assertEqual(s.parts[0].usd, 8.0)

    def test_naive_own_block_time_refused(self):
        bad = {"start": "2026-11-26T15:00:00", "end": "2026-11-26T20:00:00", "usd": 1.0}
        with self.assertRaises(timeutil.NaiveTime):
            self.split(self.meter(), [bad], [])

    def test_to_feed(self):
        own = [block(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), usd=10.0)]
        f = self.split(self.meter(), own, []).to_feed()
        self.assertEqual(sorted(f), ["parts", "period_start"])
        self.assertEqual(f["parts"], [{"user": "Sam", "install_id": "a1b2", "usd": 10.0, "share": 1.0}])
        self.assertEqual(parse_iso(f["period_start"]), datetime(2026, 11, 26, 15, 0, tzinfo=UTC))
        json.dumps(f)


if __name__ == "__main__":
    unittest.main()
