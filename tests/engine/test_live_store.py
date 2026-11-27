"""E13 live store: live.json schema 2, legacy migration, refusal, corrupt handling, meter_log."""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.engine import support
from ju import live_store, timeutil
from ju.contracts import BringUpRefused, Meter, Reading, ReplyChanged
from ju.live_store import AccountLive, ErrorInfo, Live, LiveStore

T0 = datetime(2026, 11, 26, 14, 0, 0, tzinfo=timezone.utc)

# E7 owns the real schema (SCORE §1.6.1); this is its meter_log table verbatim, so log_meters is
# tested against the exact columns and STRICT types it will meet.
METER_LOG_DDL = """CREATE TABLE meter_log (ts TEXT NOT NULL, account TEXT NOT NULL, meter TEXT NOT NULL,
  pct REAL NOT NULL, resets_at TEXT, source TEXT NOT NULL DEFAULT 'service',
  PRIMARY KEY (ts, account, meter)) STRICT;"""


class FakeIO:
    """Stands in for E3 (fsutil) and E2 (houselog) behind StoreIO, recording every call. The real
    E3/E2 are wired in wave 2; their own tests prove atomic 0600 writes and log rotation."""

    class CorruptFile(Exception):
        def __init__(self, path):
            super().__init__(str(path))
            self.path = path

    def __init__(self):
        self.writes, self.asides, self.logged = [], [], []

    def write_private(self, path, data: bytes):
        assert isinstance(data, bytes)
        tmp = Path(str(path) + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self.writes.append((Path(path), data))

    def move_aside_corrupt(self, path):
        dest = Path(str(path) + ".corrupt")
        os.replace(path, dest)
        self.asides.append(Path(path))
        return dest

    def read_json(self, path):
        try:
            obj = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:
            raise self.CorruptFile(path) from None
        if not isinstance(obj, dict):
            raise self.CorruptFile(path)
        return obj

    def log(self, level, msg, **kv):
        self.logged.append((level, msg, kv))

    def store_io(self):
        return live_store.StoreIO(write_private=self.write_private, move_aside_corrupt=self.move_aside_corrupt,
                                  read_json=self.read_json, corrupt_error=self.CorruptFile, log=self.log,
                                  check_reply=self.check_reply)

    @staticmethod
    def check_reply(data):
        """Stands in for E11 parse: {"bad": true} is a reply the parser refuses."""
        if data.get("bad"):
            raise ReplyChanged()


class LiveStoreCase(unittest.TestCase):
    def setUp(self):
        self.home = support.temp_home()
        self.addCleanup(self.home.cleanup)
        self.state = self.home.state
        self.io = FakeIO()
        self.store = LiveStore(self.io.store_io())
        self.a = str(self.home.home / ".claude")
        self.b = str(self.home.home / ".claude-newaccount")
        for p in (self.a, self.b):
            os.makedirs(p)

    def path(self) -> Path:
        return self.state / "live.json"

    def write_raw(self, obj):
        self.path().write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


class TestLiveStore(LiveStoreCase):
    def test_schema2_roundtrip(self):
        acc = AccountLive(
            fetched=T0, data={"limits": [{"kind": "session", "percent": 12}]},
            error=ErrorInfo("rate_limited", "Anthropic is rate-limiting usage reads", T0 + timedelta(minutes=1)),
            warning="legacy login", source="service", blocked_until=T0 + timedelta(minutes=6),
            backoff_step=1, retry_after_explicit=False, last_manual=T0 - timedelta(seconds=30),
            reset_anchor={"minute_of_week": 2280, "confirmations": 2, "candidate": None, "candidate_count": 0},
            identity_uuid="u-1")
        live = Live({self.a: acc})
        self.store.save(self.state, live)
        self.assertEqual(self.io.writes[-1][0], self.path())
        on_disk = json.loads(self.path().read_text())
        self.assertEqual(on_disk["schema"], 2)
        self.assertEqual(set(on_disk["accounts"][self.a]),
                         {"fetched", "data", "error", "warning", "source", "blocked_until", "backoff_step",
                          "retry_after_explicit", "last_manual", "reset_anchor", "identity_uuid"})
        back = self.store.load(self.state, [self.a, self.b])
        self.assertEqual(back.account(self.a), acc)
        self.assertEqual(back.account(self.b), AccountLive())   # unknown folder: empty, not stored
        self.assertNotIn(self.b, back.accounts)

    def test_every_saved_time_has_offset(self):
        self.store.save(self.state, Live({self.a: AccountLive(fetched=T0, data={})}))
        fetched = json.loads(self.path().read_text())["accounts"][self.a]["fetched"]
        timeutil.parse_iso(fetched)   # raises NaiveTime without an offset

    def test_legacy_shapes_migrate_once(self):
        fetched = "2026-11-25T10:00:00-08:00"
        self.write_raw({
            ".claude": {"fetched": fetched, "data": {"five_hour": {"utilization": 40}}, "error": None,
                        "warning": "w"},
            # pre-0.1.19: the error lived inside data
            ".claude-newaccount": {"fetched": fetched, "data": {"five_hour": {"utilization": 9},
                                                                "stale": True, "error_now": "429"}},
            "gone-folder": {"fetched": fetched, "data": {}, "error": None},
        })
        live = self.store.load(self.state, [self.a, self.b])
        a, b = live.account(self.a), live.account(self.b)
        self.assertEqual(a.data, {"five_hour": {"utilization": 40}})
        self.assertEqual(a.warning, "w")
        self.assertEqual(a.fetched, timeutil.parse_iso(fetched))
        self.assertEqual(b.data, {"five_hour": {"utilization": 9}})   # stale/error_now stripped
        self.assertIsNone(b.error)                                   # a free-text legacy error has no kind
        self.assertEqual(set(live.accounts), {self.a, self.b})       # unmatched short key dropped
        on_disk = json.loads(self.path().read_text())
        self.assertEqual(on_disk["schema"], 2)
        self.assertEqual(set(on_disk["accounts"]), {self.a, self.b})
        migrated = [m for m in self.io.logged if "migrated" in m[1]]
        self.assertEqual(len(migrated), 1)
        dropped = [m for m in self.io.logged if "gone-folder" in str(m)]
        self.assertEqual(len(dropped), 1)
        self.store.load(self.state, [self.a, self.b])
        self.assertEqual(len([m for m in self.io.logged if "migrated" in m[1]]), 1)

    def test_legacy_reply_that_no_longer_parses_keeps_the_entry_without_data(self):
        """Review-engine 6: 0.1.39 stored any reply it got. One the 0.2.0 parser refuses is kept with
        data=None (logged), so no later run crashes parsing it."""
        self.write_raw({".claude": {"fetched": "2026-11-25T10:00:00-08:00", "data": {"bad": True}, "error": None}})
        live = self.store.load(self.state, [self.a])
        self.assertIn(self.a, live.accounts)
        self.assertIsNone(live.account(self.a).data)
        self.assertEqual(live.account(self.a).fetched, timeutil.parse_iso("2026-11-25T10:00:00-08:00"))
        self.assertTrue(any("no longer parses" in m[1] for m in self.io.logged))
        on_disk = json.loads(self.path().read_text())
        self.assertIsNone(on_disk["accounts"][self.a]["data"])

    def test_legacy_pre_0119_error_reply_has_no_data(self):
        self.write_raw({".claude": {"fetched": "2026-11-25T10:00:00-08:00",
                                    "data": {"error": "HTTP 500"}}})
        live = self.store.load(self.state, [self.a])
        self.assertIsNone(live.account(self.a).data)

    def test_legacy_twin_basename_key_maps_by_parent(self):
        twin = str(self.home.home / "Volumes" / "X" / ".claude")
        self.write_raw({"X/.claude": {"fetched": "2026-11-25T10:00:00-08:00", "data": {"k": 1}, "error": None},
                        ".claude": {"fetched": "2026-11-25T10:00:00-08:00", "data": {"k": 2}, "error": None}})
        live = self.store.load(self.state, [self.a, twin])
        self.assertEqual(live.account(twin).data, {"k": 1})
        self.assertNotIn(self.a, live.accounts)   # a bare ".claude" is ambiguous beside a twin: dropped
        self.assertTrue(any(m[2].get("entry") == ".claude" for m in self.io.logged))

    def test_unknown_schema_refused(self):
        for bad in (9, 1, "2", None):
            with self.subTest(schema=bad):
                self.write_raw({"schema": bad, "accounts": {}})
                with self.assertRaises(BringUpRefused) as cm:
                    self.store.load(self.state, [self.a])
                self.assertTrue(cm.exception.line.startswith("jusage: live.json: "), cm.exception.line)
                with self.assertRaises(BringUpRefused):
                    self.store.probe(self.state)
        self.assertEqual(self.io.asides, [])   # refused, never moved aside

    def test_probe(self):
        self.assertEqual(self.store.probe(self.state), "absent")
        self.write_raw({".claude": {"fetched": None, "data": None, "error": None}})
        self.assertEqual(self.store.probe(self.state), "legacy")
        self.write_raw({"schema": 2, "accounts": {}})
        self.assertEqual(self.store.probe(self.state), 2)
        self.write_raw("{not json")
        self.assertEqual(self.store.probe(self.state), "corrupt")
        self.assertTrue(self.path().exists())   # probe never moves anything

    def test_corrupt_moved_aside(self):
        bad_files = {
            "not json": "{nope",
            "bad error kind": {"schema": 2, "accounts": {"/x": dict(_empty_json(), error={"kind": "oops", "text": "t", "at": "2026-11-26T10:00:00+00:00"})}},
            "naive time": {"schema": 2, "accounts": {"/x": dict(_empty_json(), fetched="2026-11-26T10:00:00")}},
            "missing field": {"schema": 2, "accounts": {"/x": {"fetched": None}}},
            "extra field": {"schema": 2, "accounts": {"/x": dict(_empty_json(), surprise=1)}},
            "relative key": {"schema": 2, "accounts": {".claude": _empty_json()}},
            "accounts not object": {"schema": 2, "accounts": []},
        }
        for name, content in bad_files.items():
            with self.subTest(name):
                self.io.asides.clear()
                self.write_raw(content)
                live = self.store.load(self.state, [self.a])
                self.assertEqual(live.accounts, {})
                self.assertEqual(self.io.asides, [self.path()])
                self.assertTrue(any("corrupt" in m[1] for m in self.io.logged))

    def test_stale_is_data_plus_error(self):
        now = T0
        fresh = AccountLive(fetched=now - timedelta(seconds=60), data={"x": 1})
        self.assertFalse(fresh.is_stale(now))
        failed = fresh.__class__(**{**fresh.__dict__, "error": ErrorInfo("no_network", "No network", now)})
        self.assertTrue(failed.is_stale(now))
        old = AccountLive(fetched=now - timedelta(seconds=901), data={"x": 1})
        self.assertTrue(old.is_stale(now))
        edge = AccountLive(fetched=now - timedelta(seconds=900), data={"x": 1})
        self.assertTrue(edge.is_stale(now))                   # RULING 33: >= 900 s
        self.assertFalse(AccountLive(fetched=now - timedelta(seconds=899), data={"x": 1}).is_stale(now))
        nothing = AccountLive(error=ErrorInfo("login_missing", "No login", now))
        self.assertFalse(nothing.is_stale(now))   # no data: an error, not a stale number

    def test_meter_log_same_second_service_wins(self):
        """A status-line capture and a service read in the same second (ts is whole seconds) never fail the
        run: the service row is kept either way, since it is what the per-model age is measured from."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(METER_LOG_DDL)
        m = lambda pct: [Meter("session", "Session", pct, T0 + timedelta(hours=2), 18000)]
        cap = Reading(folder=self.a, fetched=T0, flags=[], uuid="u", source="statusline", meters=m(41.0))
        svc = Reading(folder=self.a, fetched=T0.replace(microsecond=500000), flags=[], uuid="u", source="service", meters=m(40.0))
        live_store.log_meters(conn, self.a, cap)
        live_store.log_meters(conn, self.a, svc)
        live_store.log_meters(conn, self.a, cap)
        self.assertEqual(conn.execute("SELECT pct, source FROM meter_log").fetchall(), [(40.0, "service")])

    def test_meter_log_keeps_35_days(self):
        """Review-engine 8: meter_log is pruned as it is written; only the weekly period (session
        share) and the newest service row (per-model age) are ever read, so 35 days is plenty."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(METER_LOG_DDL)
        pst = timezone(timedelta(hours=-8))
        old = (T0 - timedelta(days=37)).astimezone(pst)
        kept = (T0 - timedelta(days=30)).astimezone(pst)
        conn.executemany("INSERT INTO meter_log (ts, account, meter, pct, resets_at, source) VALUES (?,?,?,?,?,'service')",
                         [(timeutil.iso(old), self.a, "weekly", 1.0, None), (timeutil.iso(kept), self.a, "weekly", 2.0, None)])
        live_store.log_meters(conn, self.a, Reading(self.a, T0, [Meter("weekly", "Weekly", 3.0, None, 604800)],
                                                    [], None, "service"))
        self.assertEqual([r[0] for r in conn.execute("SELECT pct FROM meter_log ORDER BY pct")], [2.0, 3.0])

    def test_meter_log_period_by_minute(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(METER_LOG_DDL)
        r1 = Reading(folder=self.a, fetched=T0, flags=[], uuid="u", source="service", meters=[
            Meter("session", "Session", 10.0, T0.replace(hour=16, minute=0, second=0, microsecond=400000), 18000),
            Meter("weekly", "Weekly", 30.0, T0 + timedelta(days=2), 604800)])
        r2 = Reading(folder=self.a, fetched=T0 + timedelta(minutes=5), flags=[], uuid="u", source="statusline",
                     meters=[Meter("session", "Session", 12.0,
                                   T0.replace(hour=15, minute=59, second=59, microsecond=800000), 18000)])
        live_store.log_meters(conn, self.a, r1)
        live_store.log_meters(conn, self.a, r2)
        rows = conn.execute("SELECT ts, account, meter, pct, resets_at, source FROM meter_log "
                            "ORDER BY ts, meter").fetchall()
        self.assertEqual(len(rows), 3)
        sessions = [r for r in rows if r[2] == "session"]
        self.assertEqual(sessions[0][4], sessions[1][4])   # sub-second drift: same period
        self.assertEqual([r[5] for r in sessions], ["service", "statusline"])
        for r in rows:
            timeutil.parse_iso(r[0]); timeutil.parse_iso(r[4])
            self.assertEqual(r[1], self.a)

    def test_meter_without_reset_logged_null(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(METER_LOG_DDL)
        live_store.log_meters(conn, self.a, Reading(self.a, T0, [Meter("weekly", "Weekly", 1.0, None, 604800)],
                                                    [], None, "service"))
        self.assertIsNone(conn.execute("SELECT resets_at FROM meter_log").fetchone()[0])


def _empty_json():
    return {"fetched": None, "data": None, "error": None, "warning": None, "source": "service",
            "blocked_until": None, "backoff_step": 0, "retry_after_explicit": False, "last_manual": None,
            "reset_anchor": {"minute_of_week": None, "confirmations": 0, "candidate": None, "candidate_count": 0},
            "identity_uuid": None}


class ForeignResetOther(LiveStoreCase):
    def test_error_other_roundtrips_and_is_checked(self):
        """RULING 31: an identity_foreign_reset error keeps the matched account's folder key."""
        err = ErrorInfo("identity_foreign_reset", "This reading matches another account's reset", T0, other=self.b)
        self.store.save(self.state, Live({self.a: AccountLive(error=err)}))
        on_disk = json.loads(self.path().read_text())["accounts"][self.a]["error"]
        self.assertEqual(on_disk["other"], self.b)
        self.assertEqual(self.store.load(self.state, [self.a]).account(self.a).error, err)
        plain = ErrorInfo("no_network", "No network", T0)
        self.store.save(self.state, Live({self.a: AccountLive(error=plain)}))
        self.assertNotIn("other", json.loads(self.path().read_text())["accounts"][self.a]["error"])
        with self.assertRaises(ValueError):
            ErrorInfo("no_network", "No network", T0, other=self.b)       # only a foreign reset names one


if __name__ == "__main__":
    unittest.main()
