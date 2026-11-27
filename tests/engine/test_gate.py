"""E14 fetch gate: due/backoff rules (pure), and refresh (lock, single flight, pipeline, merge)."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.engine import support
from tests.engine.test_live_store import FakeIO, METER_LOG_DDL
from ju import gate, identity, timeutil
from ju.contracts import (HttpError, IdentityRejected, Login, LoginError, Meter, NetworkError, RateLimited,
                          ReplyChanged)
from ju.live_store import AccountLive, ErrorInfo, LiveStore

T0 = datetime(2026, 11, 26, 14, 0, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[2]


def fresh(at=T0, **kw):
    return AccountLive(fetched=at, data={"meters": []}, identity_uuid="u", **kw)


class TestDue(unittest.TestCase):
    def test_cadence(self):
        acc = fresh()
        self.assertFalse(gate.due(acc, 60, T0 + timedelta(seconds=59), False, []).due)
        d = gate.due(acc, 60, T0 + timedelta(seconds=60), False, [])
        self.assertTrue(d.due)
        self.assertEqual(gate.due(acc, 60, T0 + timedelta(seconds=10), False, []).next_at,
                         T0 + timedelta(seconds=60))

    def test_never_fetched_is_due(self):
        self.assertTrue(gate.due(AccountLive(), 60, T0, False, []).due)

    def test_read_every_bounds(self):
        for bad in (14, 601, 0):
            with self.subTest(bad), self.assertRaises(ValueError):
                gate.due(fresh(), bad, T0, False, [])
        gate.due(fresh(), 15, T0, False, [])
        gate.due(fresh(), 600, T0, False, [])

    def test_reset_passed_due_now(self):
        acc = fresh()
        reset = T0 + timedelta(seconds=20)
        meters = [Meter("session", "Session", 50.0, reset, 18000)]
        self.assertFalse(gate.due(acc, 600, T0 + timedelta(seconds=19), False, meters).due)
        self.assertEqual(gate.due(acc, 600, T0 + timedelta(seconds=19), False, meters).next_at, reset)
        d = gate.due(acc, 600, T0 + timedelta(seconds=21), False, meters)
        self.assertTrue(d.due)
        self.assertEqual(d.reason, "reset_passed")
        # a reset already behind the reading when it was taken is not a new period
        old = [Meter("session", "Session", 50.0, T0 - timedelta(seconds=5), 18000)]
        self.assertFalse(gate.due(acc, 600, T0 + timedelta(seconds=30), False, old).due)

    def test_statusline_reading_still_reads_the_service_every_10_minutes(self):
        """RULING 29: while captures feed session and weekly, the per-model weeklies come only from the
        service, so a status-line reading is due once the last service reading is 10 minutes old."""
        reset = T0 + timedelta(days=3)
        per_model = [Meter("session", "Session", 10.0, reset, 18000), Meter("weekly", "Weekly", 20.0, reset, 604800),
                     Meter("weekly:fable", "Weekly · Fable", 5.0, reset, 604800)]
        acc = fresh(source="statusline")
        service_at = T0 - timedelta(seconds=540)                    # 9 min before the capture
        d = gate.due(acc, 600, T0 + timedelta(seconds=59), False, per_model, service_at=service_at)
        self.assertFalse(d.due)
        self.assertEqual(d.next_at, service_at + timedelta(seconds=600))
        d = gate.due(acc, 600, service_at + timedelta(seconds=600), False, per_model, service_at=service_at)
        self.assertEqual((d.due, d.reason), (True, "per_model"))
        self.assertTrue(gate.due(acc, 600, T0, False, per_model, service_at=None).due, "never read from the service")
        # no per-model meter: the capture covers everything, cadence alone decides
        self.assertFalse(gate.due(acc, 600, T0 + timedelta(seconds=59), False, per_model[:2], service_at=None).due)
        # a service reading is never held to this rule
        self.assertFalse(gate.due(fresh(), 600, T0 + timedelta(seconds=59), False, per_model, service_at=None).due)

    def test_reset_passed_never_beats_block(self):
        acc = gate.on_429(fresh(), 120, T0, lambda: 0.0, 60)
        meters = [Meter("session", "Session", 50.0, T0 + timedelta(seconds=5), 18000)]
        d = gate.due(acc, 60, T0 + timedelta(seconds=60), False, meters)
        self.assertFalse(d.due)
        self.assertEqual(d.next_at, T0 + timedelta(seconds=300))     # the ladder outlasts a short Retry-After


class TestBackoff(unittest.TestCase):
    def test_429_retry_after_120_0_absent_7200_capped(self):
        cases = {900: (900, True), 120: (300, True), 0: (300, False), None: (300, False), 7200: (3600, True)}
        for ra, (wait, explicit) in cases.items():
            with self.subTest(retry_after=ra):
                acc = gate.on_429(fresh(), ra, T0, lambda: 0.0, 60)
                self.assertEqual(acc.blocked_until, T0 + timedelta(seconds=wait))
                self.assertEqual(acc.retry_after_explicit, explicit)
                self.assertEqual(acc.error.kind, "rate_limited")
                self.assertEqual(acc.data, {"meters": []})            # last good kept
                self.assertEqual(acc.fetched, T0)
                self.assertFalse(gate.due(acc, 15, T0 + timedelta(seconds=wait - 1), False, []).due)
                self.assertTrue(gate.due(acc, 15, T0 + timedelta(seconds=wait), False, []).due)

    def test_backoff_ladder_jitter_bounds(self):
        for r, factor in ((0.0, 1.0), (0.999999, 1.2)):
            acc = fresh()
            waits = []
            for _ in range(6):
                acc = gate.on_429(acc, None, T0, lambda: r, 60)
                waits.append((acc.blocked_until - T0).total_seconds())
            expect = [s * factor for s in (300, 600, 1200, 1800, 1800, 1800)]
            for w, e in zip(waits, expect):
                self.assertAlmostEqual(w, e, delta=0.01)
        import random
        rng = random.Random(7)
        acc = fresh()
        for base in (300, 600, 1200, 1800, 1800):
            acc = gate.on_429(acc, None, T0, rng.random, 60)
            w = (acc.blocked_until - T0).total_seconds()
            self.assertGreaterEqual(w, base)
            self.assertLessEqual(w, base * 1.2)

    def test_success_clears_backoff(self):
        acc = gate.on_429(gate.on_429(fresh(), None, T0, lambda: 0.0, 60), None, T0, lambda: 0.0, 60)
        self.assertEqual(acc.backoff_step, 2)
        ok = gate.on_success(acc, data={"n": 2}, uuid="u", warning=None, anchor=acc.reset_anchor,
                             now=T0 + timedelta(hours=1))
        self.assertIsNone(ok.blocked_until)
        self.assertEqual(ok.backoff_step, 0)
        self.assertIsNone(ok.error)
        self.assertFalse(ok.retry_after_explicit)
        self.assertEqual(ok.source, "service")
        self.assertEqual(ok.fetched, T0 + timedelta(hours=1))
        self.assertEqual(gate.on_429(ok, None, T0, lambda: 0.0, 60).blocked_until, T0 + timedelta(seconds=300))

    def test_explicit_retry_after_is_exact_no_jitter(self):
        acc = gate.on_429(fresh(), 900, T0, lambda: 0.99, 60)
        self.assertEqual(acc.blocked_until, T0 + timedelta(seconds=900))

    def test_short_retry_after_never_shortens_the_cadence(self):
        """Review-engine 2: Retry-After only ever lengthens a wait. The block is the longest of the
        Retry-After, the ladder step and read_every_s, and every 429 climbs the ladder."""
        acc = fresh(at=T0 - timedelta(hours=1))
        waits = []
        now = T0
        for _ in range(5):
            acc = gate.on_429(acc, 5, now, lambda: 0.0, 600)
            waits.append((acc.blocked_until - now).total_seconds())
            self.assertTrue(acc.retry_after_explicit)
            now = acc.blocked_until
        self.assertEqual(waits, [600, 600, 1200, 1800, 1800])
        self.assertEqual(acc.backoff_step, 5)
        # the 60 s default: still never sooner than the ladder
        self.assertEqual(gate.on_429(fresh(), 5, T0, lambda: 0.0, 60).blocked_until, T0 + timedelta(seconds=300))

    def test_failure_never_sooner(self):
        for kind in ("no_network", "http", "reply_changed", "login_expired", "identity_changed"):
            with self.subTest(kind):
                acc = gate.on_failure(fresh(at=T0 - timedelta(hours=1)), kind, "t", T0, lambda: 0.0)
                self.assertEqual(acc.error, ErrorInfo(kind, "t", T0))
                self.assertEqual(acc.data, {"meters": []})
                self.assertEqual(acc.fetched, T0 - timedelta(hours=1))
                # the last success is an hour old, cadence 15 s: still not before the backoff time
                self.assertFalse(gate.due(acc, 15, T0 + timedelta(seconds=299), False, []).due)
                self.assertTrue(gate.due(acc, 15, T0 + timedelta(seconds=300), False, []).due)
                again = gate.on_failure(acc, kind, "t", T0 + timedelta(seconds=300), lambda: 0.0)
                self.assertEqual(again.blocked_until, T0 + timedelta(seconds=900))
        with self.assertRaises(ValueError):
            gate.on_failure(fresh(), "rate_limited", "t", T0, lambda: 0.0)   # 429s go through on_429

    def test_explicit_block_never_shortened(self):
        acc = gate.on_429(fresh(), 3600, T0, lambda: 0.0, 60)
        after = gate.on_failure(acc, "no_network", "No network", T0 + timedelta(seconds=10), lambda: 0.0)
        self.assertEqual(after.blocked_until, T0 + timedelta(seconds=3600))
        self.assertTrue(after.retry_after_explicit)

    def test_cadence_never_beats_backoff(self):
        acc = gate.on_429(fresh(), None, T0, lambda: 0.0, 60)
        for s in (15, 16, 60, 299):
            self.assertFalse(gate.due(acc, 15, T0 + timedelta(seconds=s), False, []).due, s)
        self.assertTrue(gate.due(acc, 15, T0 + timedelta(seconds=300), False, []).due)


class TestManual(unittest.TestCase):
    def test_manual_vs_explicit_retry_after(self):
        guessed = gate.on_429(fresh(at=T0 - timedelta(minutes=5)), None, T0, lambda: 0.0, 60)
        explicit = gate.on_429(fresh(at=T0 - timedelta(minutes=5)), 900, T0, lambda: 0.0, 60)
        later = T0 + timedelta(seconds=20)
        self.assertTrue(gate.due(guessed, 60, later, True, []).due)
        d = gate.due(explicit, 60, later, True, [])
        self.assertFalse(d.due)
        self.assertEqual(d.next_at, T0 + timedelta(seconds=900))
        self.assertFalse(gate.due(guessed, 60, later, False, []).due)

    def test_manual_skips_cadence(self):
        self.assertTrue(gate.due(fresh(), 600, T0 + timedelta(seconds=20), True, []).due)

    def test_manual_floor_15s(self):
        acc = fresh()
        d = gate.due(acc, 600, T0 + timedelta(seconds=14), True, [])
        self.assertFalse(d.due)
        self.assertEqual(d.next_at, T0 + timedelta(seconds=15))
        self.assertTrue(gate.due(acc, 600, T0 + timedelta(seconds=15), True, []).due)
        tapped = replace(fresh(at=T0 - timedelta(hours=1)), last_manual=T0)
        self.assertFalse(gate.due(tapped, 600, T0 + timedelta(seconds=14), True, []).due)
        self.assertTrue(gate.due(tapped, 600, T0 + timedelta(seconds=15), True, []).due)


# ------------------------------------------------------------------ refresh

class Script:
    """GateDeps built from plain tables: per-folder login, uuid sequence, and a queue of replies or
    exceptions per token. Records every fetch."""

    def __init__(self, io, state, *, logins, uuids, replies, read_every=60, now=T0, observe=None):
        self.io, self.state = io, state
        self.logins, self.uuids, self.replies = logins, {k: list(v) for k, v in uuids.items()}, replies
        self.fetched_tokens = []
        self.clock = [now]
        self.observe_fn = observe or (lambda anchor, weekly_reset, others: (anchor, "accept"))
        self.db = Path(state) / "meters.db"
        c = sqlite3.connect(str(self.db))
        c.executescript(METER_LOG_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
        c.close()
        self.read_every = read_every

    def rows(self, sql):
        c = sqlite3.connect(str(self.db))
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def find_login(self, folder):
        v = self.logins[folder]
        if isinstance(v, Exception):
            raise v
        return Login(service="svc-" + folder, token=v, expires_at=None, legacy=folder.endswith("legacy"))

    def read_uuid(self, folder):
        seq = self.uuids[folder]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def fetch(self, token):
        self.fetched_tokens.append(token)
        v = self.replies[token].pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    @staticmethod
    def parse(reply):
        if reply.get("bad"):
            raise ReplyChanged()
        ms = [Meter(m["id"], m["id"].title(), float(m["pct"]),
                    timeutil.parse_iso(m["resets"]) if m.get("resets") else None, 604800)
              for m in reply["meters"]]
        return gate.Parsed(meters=ms, flags=[])

    @staticmethod
    def check_stable(before, after):
        if before != after:
            raise IdentityRejected("identity_changed")

    def deps(self):
        return gate.GateDeps(
            store=LiveStore(self.io.store_io()), find_login=self.find_login, read_uuid=self.read_uuid,
            fetch=self.fetch, parse=self.parse, check_stable=self.check_stable, observe=self.observe_fn,
            login_warning=lambda login: "old login" if login.legacy else None,
            open_db=lambda state: sqlite3.connect(str(self.db)), read_every_s=self.read_every, now=lambda: self.clock[0],
            rand=lambda: 0.0, log=self.io.log)


def reply(pct, weekly_reset="2026-11-28T18:00:00+00:00"):
    return {"meters": [{"id": "session", "pct": pct, "resets": "2026-11-26T18:00:00+00:00"},
                       {"id": "weekly", "pct": pct / 2, "resets": weekly_reset}]}


class RefreshCase(unittest.TestCase):
    def setUp(self):
        self.home = support.temp_home()
        self.addCleanup(self.home.cleanup)
        self.state = self.home.state
        self.io = FakeIO()
        self.a = str(self.home.home / ".claude")
        self.b = str(self.home.home / ".claude-newaccount")


class TestRefresh(RefreshCase):
    def test_success_stores_and_logs_meters(self):
        s = Script(self.io, self.state, logins={self.a: "ta"}, uuids={self.a: ["u1"]}, replies={"ta": [reply(40)]})
        res = gate.refresh(self.state, [self.a], manual=False, deps=s.deps())
        self.assertEqual(res.fetched, [self.a])
        acc = res.live.account(self.a)
        self.assertEqual(acc.fetched, T0)
        self.assertEqual(acc.data, reply(40))
        self.assertEqual(acc.identity_uuid, "u1")
        self.assertIsNone(acc.error)
        on_disk = LiveStore(self.io.store_io()).load(self.state, [self.a])
        self.assertEqual(on_disk.account(self.a), acc)
        rows = s.rows("SELECT account, meter, source FROM meter_log ORDER BY meter")
        self.assertEqual(rows, [(self.a, "session", "service"), (self.a, "weekly", "service")])
        self.assertEqual(res.next_due, T0 + timedelta(seconds=60))
        self.assertEqual([(blk.key, blk.folders) for blk in res.accounts], [(self.a, [self.a])])

    def test_legacy_login_sets_warning(self):
        leg = str(self.home.home / ".claude-legacy")
        s = Script(self.io, self.state, logins={leg: "t"}, uuids={leg: ["u"]}, replies={"t": [reply(1)]})
        res = gate.refresh(self.state, [leg], manual=False, deps=s.deps())
        self.assertEqual(res.live.account(leg).warning, "old login")

    def test_one_account_failure_does_not_raise(self):
        failures = {
            "login_missing": LoginError("login_missing", "No login"),
            "rate_limited": RateLimited(None),
            "http": HttpError(500),
            "no_network": NetworkError(),
            "reply_changed": {"bad": True},
            "login_expired": LoginError("login_expired", "Open Claude Code on this account, then refresh"),
        }
        for kind, failure in failures.items():
            with self.subTest(kind):
                (self.state / "live.json").unlink(missing_ok=True)
                (self.state / "meters.db").unlink(missing_ok=True)
                login = failure if kind.startswith("login_missing") else "ta"
                s = Script(self.io, self.state, logins={self.a: login, self.b: "tb"},
                           uuids={self.a: ["u1"], self.b: ["u2"]},
                           replies={"ta": [failure], "tb": [reply(10)]})
                res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
                self.assertEqual(res.live.account(self.a).error.kind, kind)
                self.assertIsNone(res.live.account(self.b).error)
                self.assertIsNotNone(res.live.account(self.a).blocked_until)

    def test_a_crash_after_a_fetch_still_saves_every_account(self):
        """Review-engine 1: #1 is told to wait an hour, then logging #2's reading fails (a locked
        database). The error is loud, but #1's block and #2's reading are on disk, so the next run
        does not call the service 30 s after it was asked to wait an hour."""
        s = Script(self.io, self.state, logins={self.a: "ta", self.b: "tb"},
                   uuids={self.a: ["u1"], self.b: ["u2"]},
                   replies={"ta": [RateLimited(3600)], "tb": [reply(10)]})
        deps = s.deps()

        def locked(state):
            raise sqlite3.OperationalError("database is locked")
        deps = replace(deps, open_db=locked)
        with self.assertRaises(sqlite3.OperationalError):
            gate.refresh(self.state, [self.a, self.b], manual=False, deps=deps)
        on_disk = LiveStore(self.io.store_io()).load(self.state, [self.a, self.b])
        self.assertEqual(on_disk.account(self.a).blocked_until, T0 + timedelta(seconds=3600))
        self.assertTrue(on_disk.account(self.a).retry_after_explicit)
        self.assertEqual(on_disk.account(self.b).data, reply(10))
        s.clock[0] = T0 + timedelta(seconds=30)
        gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, ["ta", "tb"])     # nothing read again

    def test_identity_changed_discarded_last_good_kept(self):
        s = Script(self.io, self.state, logins={self.a: "ta"}, uuids={self.a: ["u1"]}, replies={"ta": [reply(40)]})
        gate.refresh(self.state, [self.a], manual=False, deps=s.deps())
        s2 = Script(self.io, self.state, logins={self.a: "ta"}, uuids={self.a: ["u1", "u1", "u9"]},   # grouping, before, after
                    replies={"ta": [reply(80)]}, now=T0 + timedelta(minutes=2))
        res = gate.refresh(self.state, [self.a], manual=False, deps=s2.deps())
        acc = res.live.account(self.a)
        self.assertEqual(acc.error.kind, "identity_changed")
        self.assertEqual(acc.data, reply(40))
        self.assertEqual(acc.fetched, T0)
        self.assertEqual(s2.rows("SELECT COUNT(*) FROM meter_log")[0][0], 2)   # only the first reading

    def test_foreign_reset_rejected_for_the_legacy_item(self):
        """R-14 on a reading from the unsuffixed item (it names no account): a weekly reset that is
        another account's learned minute is that account's reading."""
        leg = str(self.home.home / ".claude-legacy")
        seen = {}

        def observe(anchor, weekly_reset, others):
            seen["others"], seen["weekly_reset"] = others, weekly_reset
            return identity.observe(anchor, weekly_reset, others)

        anchor_b = {"minute_of_week": 6300, "confirmations": 2, "candidate": None, "candidate_count": 0}  # 09:00 Fri
        LiveStore(self.io.store_io()).save(self.state, gate_live({self.b: fresh(at=T0, reset_anchor=anchor_b)}))
        s = Script(self.io, self.state, logins={leg: "ta", self.b: "tb"}, uuids={leg: ["u1"], self.b: ["u2"]},
                   replies={"ta": [reply(40, weekly_reset="2026-11-27T09:00:00+00:00")]}, observe=observe)
        res = gate.refresh(self.state, [leg, self.b], manual=False, deps=s.deps())
        acc = res.live.account(leg)
        self.assertEqual(acc.error.kind, "identity_foreign_reset")
        self.assertIsNone(acc.data)
        self.assertEqual(acc.reset_anchor, identity.new_anchor())   # a rejected legacy reading teaches nothing
        self.assertEqual(acc.error.other, self.b)             # RULING 31: the matched account is named
        self.assertEqual(s.fetched_tokens, ["ta"])            # b was fresh: not fetched

    def _legacy_run(self, *, own_anchor=None, other_anchor=None, weekly="2026-11-28T18:00:00+00:00"):
        leg = str(self.home.home / ".claude-legacy")
        stored = {}
        if own_anchor is not None:
            stored[leg] = fresh(at=T0 - timedelta(hours=1), reset_anchor=own_anchor)
        stored[self.b] = fresh(at=T0, reset_anchor=other_anchor or identity.new_anchor())
        LiveStore(self.io.store_io()).save(self.state, gate_live(stored))
        s = Script(self.io, self.state, logins={leg: "ta", self.b: "tb"}, uuids={leg: ["u1"], self.b: ["u2"]},
                   replies={"ta": [reply(40, weekly_reset=weekly)]}, observe=identity.observe)
        return leg, gate.refresh(self.state, [leg, self.b], manual=False, deps=s.deps()).live.account(leg)

    def test_legacy_reading_never_teaches_a_new_minute(self):
        """Review-engine 4 (R-10): the unsuffixed item names no account. With a learned minute, its
        reading must match it; a different minute is discarded and counts toward nothing."""
        sat = timeutil.minute_of_week(timeutil.parse_iso("2026-11-28T18:00:00+00:00"))
        own = {"minute_of_week": sat, "confirmations": 3, "candidate": None, "candidate_count": 0}
        leg, acc = self._legacy_run(own_anchor=own, weekly="2026-11-29T03:00:00+00:00")
        self.assertEqual(acc.error.kind, "identity_foreign_reset")
        self.assertIsNone(acc.error.other)
        self.assertEqual(acc.reset_anchor, own)                    # no candidate counted
        self.assertEqual(acc.data, {"meters": []})                 # last good kept
        self.assertIsNotNone(acc.blocked_until)

    def test_legacy_reading_matching_its_own_minute_is_accepted(self):
        sat = timeutil.minute_of_week(timeutil.parse_iso("2026-11-28T18:00:00+00:00"))
        own = {"minute_of_week": sat, "confirmations": 3, "candidate": None, "candidate_count": 0}
        leg, acc = self._legacy_run(own_anchor=own)
        self.assertIsNone(acc.error)
        self.assertEqual(acc.data, reply(40))
        self.assertEqual(acc.reset_anchor["confirmations"], 4)

    def test_legacy_reading_unchecked_while_another_account_is_unlearned(self):
        """Nothing learned for this account and another account's minute unknown: the reading could
        be that account's, so it is discarded and teaches nothing."""
        leg, acc = self._legacy_run()
        self.assertEqual(acc.error.kind, "identity_foreign_reset")
        self.assertIsNone(acc.data)
        self.assertEqual(acc.reset_anchor, identity.new_anchor())

    def test_legacy_reading_learns_when_every_other_account_is_known(self):
        """Every other account's minute is learned and this reading matches none: it cannot be
        theirs, so it is accepted and starts learning (a Mac whose only login is the old item)."""
        other = {"minute_of_week": 6300, "confirmations": 2, "candidate": None, "candidate_count": 0}
        leg, acc = self._legacy_run(other_anchor=other)
        self.assertIsNone(acc.error)
        self.assertEqual(acc.data, reply(40))
        self.assertEqual(acc.reset_anchor["candidate_count"], 1)

    def test_legacy_only_account_on_its_own_learns(self):
        leg = str(self.home.home / ".claude-legacy")
        s = Script(self.io, self.state, logins={leg: "ta"}, uuids={leg: ["u1"]},
                   replies={"ta": [reply(40)]}, observe=identity.observe)
        acc = gate.refresh(self.state, [leg], manual=False, deps=s.deps()).live.account(leg)
        self.assertIsNone(acc.error)
        self.assertEqual(acc.reset_anchor["candidate_count"], 1)

    def test_same_reset_as_another_account_never_locks_a_hashed_item_out(self):
        """Review-engine 3: two accounts whose weekly resets coincide (a global reset moves both).
        A hashed item's reading passed the uuid check, so it is accepted and relearns the minute."""
        anchor_a = {"minute_of_week": identity.timeutil.minute_of_week(timeutil.parse_iso("2026-11-28T18:00:00+00:00")),
                    "confirmations": 5, "candidate": None, "candidate_count": 0}
        anchor_b = {"minute_of_week": 6300, "confirmations": 2, "candidate": None, "candidate_count": 0}
        LiveStore(self.io.store_io()).save(self.state, gate_live({
            self.a: fresh(at=T0 - timedelta(hours=1), reset_anchor=anchor_a),
            self.b: fresh(at=T0, reset_anchor=anchor_b)}))
        same = "2026-11-27T09:00:00+00:00"
        s = Script(self.io, self.state, logins={self.a: "ta", self.b: "tb"}, uuids={self.a: ["u1"], self.b: ["u2"]},
                   replies={"ta": [reply(40, weekly_reset=same), reply(41, weekly_reset=same)],
                            "tb": [reply(10, weekly_reset=same)]},
                   observe=identity.observe)
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertIsNone(res.live.account(self.a).error)
        self.assertEqual(res.live.account(self.a).data, reply(40, weekly_reset=same))
        s.clock[0] = T0 + timedelta(seconds=60)
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertIsNone(res.live.account(self.a).error)
        self.assertEqual(res.live.account(self.a).reset_anchor["minute_of_week"], 6300)   # relearned

    def test_two_folders_one_uuid_single_block(self):
        old = fresh(at=T0 - timedelta(minutes=30))
        LiveStore(self.io.store_io()).save(self.state, gate_live({self.b: old}))
        s = Script(self.io, self.state, logins={self.a: "ta", self.b: "tb"},
                   uuids={self.a: ["same"], self.b: ["same"]}, replies={"ta": [reply(40)], "tb": [reply(41)]})
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, ["ta"])            # one account, one read
        self.assertEqual(len(res.accounts), 1)
        blk = res.accounts[0]
        self.assertEqual(blk.key, self.a)
        self.assertEqual(blk.folders, [self.a, self.b])
        self.assertEqual(blk.uuid, "same")
        self.assertEqual(blk.state.data, reply(40))          # freshest wins

    def test_group_reads_through_first_member_with_a_login(self):
        missing = LoginError("login_missing", "No login")
        s = Script(self.io, self.state, logins={self.a: missing, self.b: "tb"},
                   uuids={self.a: ["same"], self.b: ["same"]}, replies={"tb": [reply(40)]})
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, ["tb"])            # exactly one fetch, through the second
        self.assertEqual(res.fetched, [self.b])
        self.assertEqual(len(res.accounts), 1)
        blk = res.accounts[0]
        self.assertEqual(blk.key, self.b)
        self.assertEqual(blk.folders, [self.a, self.b])
        self.assertEqual(blk.state.data, reply(40))
        self.assertIsNone(blk.state.error)
        s.clock[0] = T0 + timedelta(seconds=30)               # inside the cadence: the group is not due
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, ["tb"])

    def test_group_with_no_login_gets_login_missing(self):
        missing = LoginError("login_missing", "No login")
        s = Script(self.io, self.state, logins={self.a: missing, self.b: LoginError("login_missing", "No login b")},
                   uuids={self.a: ["same"], self.b: ["same"]}, replies={})
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, [])
        blk = res.accounts[0]
        self.assertEqual(blk.state.error, ErrorInfo("login_missing", "No login", T0))
        self.assertIsNotNone(blk.state.blocked_until)

    def test_login_failure_is_saved_so_its_backoff_grows(self):
        """Review-engine 7: a run that reads nothing (no login anywhere) still saves the account's
        block, so the next run waits it out and a repeat climbs the ladder (5, then 10 min)."""
        missing = LoginError("login_expired", "Open Claude Code on this account, then refresh")
        s = Script(self.io, self.state, logins={self.a: missing}, uuids={self.a: ["u1"]}, replies={})
        gate.refresh(self.state, [self.a], manual=False, deps=s.deps())
        on_disk = LiveStore(self.io.store_io()).load(self.state, [self.a]).account(self.a)
        self.assertEqual(on_disk.error.kind, "login_expired")
        self.assertEqual(on_disk.blocked_until, T0 + timedelta(seconds=300))
        self.assertEqual(on_disk.backoff_step, 1)
        s.clock[0] = T0 + timedelta(seconds=60)               # blocked: nothing tried, nothing changes
        res = gate.refresh(self.state, [self.a], manual=False, deps=s.deps())
        self.assertEqual(res.live.account(self.a).backoff_step, 1)
        s.clock[0] = T0 + timedelta(seconds=300)
        gate.refresh(self.state, [self.a], manual=False, deps=s.deps())
        on_disk = LiveStore(self.io.store_io()).load(self.state, [self.a]).account(self.a)
        self.assertEqual(on_disk.backoff_step, 2)
        self.assertEqual(on_disk.blocked_until, T0 + timedelta(seconds=900))

    def test_merge_freshest_wins_when_lead_older(self):
        LiveStore(self.io.store_io()).save(self.state, gate_live({
            self.a: fresh(at=T0 - timedelta(seconds=10)),
            self.b: replace(fresh(at=T0 - timedelta(seconds=5)), data={"meters": [], "newer": 1})}))
        s = Script(self.io, self.state, logins={}, uuids={self.a: ["same"], self.b: ["same"]}, replies={})
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, [])
        self.assertEqual(res.accounts[0].state.data, {"meters": [], "newer": 1})

    def test_next_due_is_earliest(self):
        LiveStore(self.io.store_io()).save(self.state, gate_live({
            self.a: fresh(at=T0 - timedelta(seconds=20)),
            self.b: gate.on_429(fresh(at=T0 - timedelta(seconds=50)), 25, T0 - timedelta(seconds=295), lambda: 0, 60)}))
        s = Script(self.io, self.state, logins={}, uuids={self.a: ["u1"], self.b: ["u2"]}, replies={})
        res = gate.refresh(self.state, [self.a, self.b], manual=False, deps=s.deps())
        self.assertEqual(s.fetched_tokens, [])
        self.assertEqual(res.next_due, T0 + timedelta(seconds=5))    # b's block ends before a's cadence

    def test_manual_records_last_manual(self):
        s = Script(self.io, self.state, logins={self.a: "ta"}, uuids={self.a: ["u"]},
                   replies={"ta": [reply(1), reply(2)]})
        gate.refresh(self.state, [self.a], manual=True, deps=s.deps())
        s.clock[0] = T0 + timedelta(seconds=5)
        res = gate.refresh(self.state, [self.a], manual=True, deps=s.deps())
        self.assertEqual(res.fetched, [])
        self.assertEqual(res.live.account(self.a).last_manual, T0)

    def test_stuck_lock_message(self):
        (self.state / "fetch.lock").touch()
        fd = os.open(self.state / "fetch.lock", os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        old = gate.LOCK_TIMEOUT_S
        gate.LOCK_TIMEOUT_S = 0.3
        self.addCleanup(setattr, gate, "LOCK_TIMEOUT_S", old)
        s = Script(self.io, self.state, logins={self.a: "ta"}, uuids={self.a: ["u"]}, replies={"ta": [reply(1)]})
        with self.assertRaises(RuntimeError) as cm:
            gate.refresh(self.state, [self.a], manual=False, deps=s.deps())
        self.assertEqual(str(cm.exception), "another jusage refresh is stuck")
        self.assertEqual(s.fetched_tokens, [])

    def test_two_callers_single_flight(self):
        a, b = self.a, self.b
        responses = [(200, {}, reply(10)), (200, {}, reply(20)), (200, {}, reply(30)), (200, {}, reply(40))]
        with support.FakeUsageServer(responses) as srv:
            marker = self.home.home / "first-is-fetching"
            procs = []
            for i in range(2):
                if i == 1:
                    deadline = time.time() + 20
                    while not marker.exists() and time.time() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), "first caller never started fetching")
                procs.append(subprocess.Popen(
                    [sys.executable, "-c", CALLER, str(self.state), srv.url, str(marker), a, b],
                    cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=dict({k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")},   # RULING 32
                             PYTHONPATH=str(REPO / "engine") + os.pathsep + str(REPO))))
            outs = []
            for p in procs:
                out, err = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0, err.decode())
                outs.append(json.loads(out))
            self.assertEqual(len(srv.requests), 2)               # one request per account, total
            self.assertEqual(sorted(outs[0]["fetched"]), sorted([a, b]))
            self.assertEqual(outs[1]["fetched"], [])
            self.assertEqual(outs[1]["pcts"], outs[0]["pcts"])   # the second caller reads the first's result


def gate_live(accounts):
    from ju.live_store import Live
    return Live(dict(accounts))


# A second process: real flock, real HTTP to the fake server, the slow first fetch holding the lock.
CALLER = textwrap.dedent('''
    import json, sqlite3, sys, time, urllib.request
    from pathlib import Path
    from tests.engine.test_live_store import FakeIO, METER_LOG_DDL
    from tests.engine.test_gate import Script
    from ju import gate, timeutil
    from ju.contracts import Login
    from ju.live_store import LiveStore
    state, url, marker, a, b = sys.argv[1:6]
    state, marker = Path(state), Path(marker)

    def fetch(token):
        marker.touch()
        time.sleep(0.8)
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())

    def open_db(st):
        c = sqlite3.connect(str(st / "meters.db"), timeout=10)
        c.execute(METER_LOG_DDL.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
        return c

    io = FakeIO()
    deps = gate.GateDeps(
        store=LiveStore(io.store_io()),
        find_login=lambda f: Login(service="s", token=f, expires_at=None, legacy=False),
        read_uuid=lambda f: "uuid-" + f, fetch=fetch, parse=Script.parse, check_stable=Script.check_stable,
        observe=lambda anchor, w, o: (anchor, "accept"), login_warning=lambda l: None, open_db=open_db,
        read_every_s=60, now=timeutil.now, rand=lambda: 0.0, log=io.log)
    res = gate.refresh(state, [a, b], manual=False, deps=deps)
    print(json.dumps({"fetched": res.fetched,
                      "pcts": {k: res.live.account(k).data["meters"][0]["pct"] for k in (a, b)}}))
''')


class PublicNames(unittest.TestCase):
    def test_fetch_lock_is_public(self):
        """RULING 30: the CLI takes the fetch lock through a public name."""
        from ju import gate
        self.assertTrue(callable(gate.fetch_lock))
        self.assertFalse(hasattr(gate, "_lock"))


if __name__ == "__main__":
    unittest.main()
