"""E7 usage.db: schema 3, STRICT, WAL, the migration ladder, refusal of a newer database."""
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from ju import ledger_db

from tests.engine import support

V2_SCHEMA = """
CREATE TABLE records (key TEXT PRIMARY KEY, ts TEXT NOT NULL, day TEXT NOT NULL,
  account TEXT NOT NULL, model TEXT NOT NULL, project TEXT NOT NULL, session TEXT NOT NULL,
  input INTEGER NOT NULL, cache_5m INTEGER NOT NULL, cache_1h INTEGER NOT NULL,
  cache_read INTEGER NOT NULL, output INTEGER NOT NULL) STRICT;
CREATE INDEX records_ts ON records(ts);
CREATE TABLE files (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL) STRICT;
CREATE TABLE meter_log (ts TEXT NOT NULL, account TEXT NOT NULL, meter TEXT NOT NULL,
  pct REAL NOT NULL, resets_at TEXT, PRIMARY KEY (ts, account, meter)) STRICT;
"""


def make_v2(state: Path, version=2):
    c = sqlite3.connect(state / "usage.db")
    c.executescript(V2_SCHEMA)
    ins = ("INSERT INTO records (key, ts, day, account, model, project, session, input, cache_5m,"
           " cache_1h, cache_read, output) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    for key, acct in (("k1", ".claude"), ("k2", ".claude-newaccount"), ("k3", ".claude.bak"),
                      ("k4", "weird/.claude-gone"), ("k5", "Volumes/.claude")):
        c.execute(ins, (key, "2026-11-01T10:00:00-07:00", "2026-11-01", acct, "claude-opus-5", "demo",
                        "s", 1, 2, 3, 4, 5))
    ml = "INSERT INTO meter_log (ts, account, meter, pct, resets_at) VALUES (?,?,?,?,?)"
    for i, (acct, meter) in enumerate((
            (".claude", "five_hour"), (".claude", "seven_day"), (".claude", "seven_day_opus"),
            (".claude-newaccount", "seven_day_sonnet"), (".claude.bak", "five_hour"))):
        c.execute(ml, (f"2026-11-01T10:0{i}:00-07:00", acct, meter, 10.0 + i, None))
    c.execute("INSERT INTO files (path, size, mtime) VALUES ('/x/a.jsonl', 10, 1.0)")
    c.execute(f"PRAGMA user_version={version}")
    c.commit()
    c.close()


class LedgerDbTests(unittest.TestCase):
    def setUp(self):
        self.home = support.temp_home()
        self.addCleanup(self.home.cleanup)
        self.state = self.home.state
        self.logged = []
        self.log = lambda level, msg, **kv: self.logged.append((level, msg))
        self.folders = [str(self.home.home / ".claude"), str(self.home.home / ".claude-newaccount")]

    def test_fresh_is_schema3_strict(self):
        c = ledger_db.open_db(self.state)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(c.execute("PRAGMA busy_timeout").fetchone()[0], 10000)
        strict = {r["name"]: r["strict"] for r in c.execute("PRAGMA table_list") if r["schema"] == "main"}
        for t in ("records", "files", "meter_log"):
            self.assertEqual(strict[t], 1, t)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(records)")]
        self.assertEqual(cols, ["key", "ts", "day", "account", "model", "project", "session", "input",
                                "cache_5m", "cache_1h", "cache_read", "output", "stop_reason", "complete"])
        mcols = [r["name"] for r in c.execute("PRAGMA table_info(meter_log)")]
        self.assertEqual(mcols, ["ts", "account", "meter", "pct", "resets_at", "source"])
        idx = {r["name"] for r in c.execute("PRAGMA index_list(records)")}
        self.assertTrue({"records_ts", "records_acct_day"} <= idx)
        mode = stat.S_IMODE(os.stat(self.state / "usage.db").st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(ledger_db.probe_version(self.state / "usage.db"), 3)

    def test_probe_of_absent_file_is_zero_and_creates_nothing(self):
        p = self.state / "usage.db"
        self.assertEqual(ledger_db.probe_version(p), 0)
        self.assertFalse(p.exists())

    def test_reopen_is_stable(self):
        ledger_db.open_db(self.state).close()
        c = ledger_db.open_db(self.state)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_v2_to_v3_maps_keys_and_meters(self):
        make_v2(self.state)
        c = ledger_db.open_db(self.state, folders=self.folders, log=self.log)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
        accts = {r["key"]: r["account"] for r in c.execute("SELECT key, account FROM records")}
        self.assertEqual(accts["k1"], self.folders[0])
        self.assertEqual(accts["k2"], self.folders[1])
        self.assertEqual(accts["k4"], "weird/.claude-gone")      # unmatched: kept as it was
        self.assertEqual(accts["k5"], "Volumes/.claude")
        self.assertNotIn("k3", accts)                            # backup folder purged
        meters = {(r["account"], r["meter"]) for r in c.execute("SELECT account, meter FROM meter_log")}
        self.assertEqual(meters, {(self.folders[0], "session"), (self.folders[0], "weekly"),
                                  (self.folders[0], "weekly:opus"), (self.folders[1], "weekly:sonnet")})
        r = c.execute("SELECT stop_reason, complete FROM records WHERE key='k1'").fetchone()
        self.assertIsNone(r["stop_reason"])
        self.assertEqual(r["complete"], 1)
        self.assertEqual({r["source"] for r in c.execute("SELECT source FROM meter_log")}, {"service"})
        self.assertEqual(c.execute("SELECT count(*) FROM files").fetchone()[0], 0)
        unmatched = [m for _, m in self.logged if "weird/.claude-gone" in m or "Volumes/.claude" in m]
        self.assertEqual(len(unmatched), 2)

    def test_twin_qualified_key_is_matched(self):
        c0 = sqlite3.connect(self.state / "usage.db")
        c0.executescript(V2_SCHEMA)
        c0.execute("INSERT INTO records (key, ts, day, account, model, project, session, input, cache_5m,"
                   " cache_1h, cache_read, output) VALUES ('t1','2026-11-01T10:00:00-07:00','2026-11-01',"
                   "'X/.claude','m','p','s',1,1,1,1,1)")
        c0.execute("PRAGMA user_version=2")
        c0.commit()
        c0.close()
        folders = [str(self.home.home / ".claude"), "/Volumes/X/.claude"]
        c = ledger_db.open_db(self.state, folders=folders, log=self.log)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("SELECT account FROM records WHERE key='t1'").fetchone()[0],
                         "/Volumes/X/.claude")

    def test_ladder_from_version_0_and_1_climbs(self):
        for v in (0, 1):
            with self.subTest(version=v):
                d = Path(tempfile.mkdtemp(prefix="jusage-st-"))
                make_v2(d, version=v)
                c = ledger_db.open_db(d, log=self.log)
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(c.execute("SELECT count(*) FROM files").fetchone()[0], 0)
                c.close()

    def test_backup_purge_runs_once(self):
        make_v2(self.state)
        c = ledger_db.open_db(self.state, folders=self.folders, log=self.log)
        # a backup-named account appearing after the migration is NOT purged by a later connection
        row = ledger_db.Row(key="late", ts="2026-11-02T10:00:00-08:00", day="2026-11-02",
                            account="/x/.claude.bak", model="m", project="p", session="s",
                            input=1, cache_5m=0, cache_1h=0, cache_read=0, output=1,
                            stop_reason="end_turn", complete=1)
        ledger_db.insert_record(c, row)
        c.commit()
        c.close()
        c = ledger_db.open_db(self.state, folders=self.folders, log=self.log)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("SELECT count(*) FROM records WHERE key='late'").fetchone()[0], 1)

    def test_purge_tests_the_folder_name_not_its_parents(self):
        c0 = sqlite3.connect(self.state / "usage.db")
        c0.executescript(V2_SCHEMA)
        c0.execute("INSERT INTO records (key, ts, day, account, model, project, session, input, cache_5m,"
                   " cache_1h, cache_read, output) VALUES ('keep','2026-11-01T10:00:00-07:00','2026-11-01',"
                   "'/Volumes/backup/.claude','m','p','s',1,1,1,1,1)")
        c0.execute("PRAGMA user_version=2")
        c0.commit()
        c0.close()
        c = ledger_db.open_db(self.state, folders=["/Volumes/backup/.claude"], log=self.log)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("SELECT count(*) FROM records WHERE key='keep'").fetchone()[0], 1)

    def test_newer_refused(self):
        make_v2(self.state, version=4)
        with self.assertRaises(ledger_db.DbTooNew) as cm:
            ledger_db.open_db(self.state)
        self.assertEqual(cm.exception.n, 4)
        self.assertEqual(str(cm.exception), "usage.db is from a newer jusage (schema 4); update jusage")
        self.assertEqual(ledger_db.probe_version(self.state / "usage.db"), 4)
        # refusal changed nothing
        c = sqlite3.connect(self.state / "usage.db")
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 4)
        c.close()

    def test_failed_migration_rolls_back_whole(self):
        make_v2(self.state)
        c0 = sqlite3.connect(self.state / "usage.db")
        c0.execute("ALTER TABLE records ADD stop_reason TEXT")   # a half-applied ladder: ADD will now fail
        c0.commit()
        c0.close()
        with self.assertRaises(sqlite3.OperationalError):
            ledger_db.open_db(self.state, folders=self.folders, log=self.log)
        c = sqlite3.connect(self.state / "usage.db")
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(c.execute("SELECT count(*) FROM records WHERE account='.claude.bak'").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT count(*) FROM files").fetchone()[0], 1)
        c.close()

    def test_inserts_name_columns(self):
        c = ledger_db.open_db(self.state)
        self.addCleanup(c.close)
        seen = []
        c.set_trace_callback(seen.append)
        ledger_db.insert_record(c, ledger_db.Row(
            key="a:b", ts="2026-11-26T10:00:00-08:00", day="2026-11-26", account="/x/.claude",
            model="claude-opus-5", project="demo", session="s", input=1, cache_5m=2, cache_1h=3,
            cache_read=4, output=5, stop_reason=None, complete=0))
        c.commit()
        sql = [s for s in seen if s.lstrip().upper().startswith("INSERT")]
        self.assertEqual(len(sql), 1)
        for col in ledger_db.RECORD_COLS:
            self.assertIn(col, sql[0])
        self.assertIn("(key, ts, day", sql[0])
        r = c.execute("SELECT * FROM records").fetchone()
        self.assertEqual((r["cache_1h"], r["stop_reason"], r["complete"]), (3, None, 0))

    def test_insert_replaces_same_key(self):
        c = ledger_db.open_db(self.state)
        self.addCleanup(c.close)
        base = dict(key="a:b", ts="2026-11-26T10:00:00-08:00", day="2026-11-26", account="/x/.claude",
                    model="m", project="p", session="s", input=1, cache_5m=0, cache_1h=0, cache_read=0,
                    stop_reason="end_turn", complete=1)
        ledger_db.insert_record(c, ledger_db.Row(output=5, **base))
        ledger_db.insert_record(c, ledger_db.Row(output=9, **base))
        self.assertEqual([r["output"] for r in c.execute("SELECT output FROM records")], [9])

    def test_two_openers_one_migration(self):
        make_v2(self.state)
        import subprocess, sys, textwrap
        code = textwrap.dedent(f"""
            import sys; sys.path.insert(0, {str(Path(ledger_db.__file__).parents[1])!r})
            from ju import ledger_db
            from pathlib import Path
            c = ledger_db.open_db(Path({str(self.state)!r}), folders={self.folders!r}, log=lambda *a, **k: None)
            print(c.execute("PRAGMA user_version").fetchone()[0])
        """)
        procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(3)]
        outs = [p.communicate(timeout=60) for p in procs]
        for p, (o, e) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, e.decode())
            self.assertEqual(o.decode().strip(), "3")

    def test_many_openers_race_fresh_and_old(self):
        """RULING 49: eight openers released at the same instant, on a v2 database and on no
        database, ten rounds. The journal-mode switch and the migration run under one file lock, so
        none sees 'database is locked', every one reads schema 3 in WAL, and the rows migrate once."""
        import subprocess, sys, textwrap, time
        engine = str(Path(ledger_db.__file__).parents[1])
        for round_ in range(10):
            state = self.state / f"r{round_}"
            state.mkdir(mode=0o700)
            old = round_ % 2 == 0
            if old:
                make_v2(state)
            go = time.time() + 1.0
            code = textwrap.dedent(f"""
                import sys, time; sys.path.insert(0, {engine!r})
                from ju import ledger_db
                from pathlib import Path
                while time.time() < {go!r}: pass
                c = ledger_db.open_db(Path({str(state)!r}), folders={self.folders!r}, log=lambda *a, **k: None)
                print(c.execute("PRAGMA user_version").fetchone()[0], c.execute("PRAGMA journal_mode").fetchone()[0])
            """)
            procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(8)]
            outs = [p.communicate(timeout=60) for p in procs]
            for p, (o, e) in zip(procs, outs):
                self.assertEqual(p.returncode, 0, e.decode())
                self.assertEqual(o.decode().split(), ["3", "wal"])
            c = sqlite3.connect(state / "usage.db")
            # the v2 file's five rows lose the one from a backup folder (.claude.bak), once
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 4 if old else 0)
            c.close()


if __name__ == "__main__":
    unittest.main()
