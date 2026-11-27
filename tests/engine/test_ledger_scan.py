"""E8 scan: transcripts -> usage.db rows. Counts only, deterministic, one owner per key."""
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from ju import ledger_db, ledger_scan

from tests.engine import support

FIX = support.FIXTURES / "transcripts"
EXPECTED = json.loads((FIX / "expected.json").read_text())
KEYS = ("key", "ts", "model", "project", "session", "input", "cache_5m", "cache_1h", "cache_read",
        "output", "stop_reason", "complete")


def ts(h, m=0, s=0):
    return f"2026-11-26T{h:02d}:{m:02d}:{s:02d}-08:00"


class TzCase(unittest.TestCase):
    def setUp(self):
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)
        self.home = support.temp_home()
        self.addCleanup(self.home.cleanup)
        self.logged = []
        self.log = lambda level, msg, **kv: self.logged.append((level, msg))

    def db(self):
        c = ledger_db.open_db(self.home.state, log=self.log)
        self.addCleanup(c.close)
        return c

    def folder(self, name):
        p = self.home.home / name
        p.mkdir(parents=True, exist_ok=True)
        return str(p)


class ParseFileTests(TzCase):
    def test_matches_the_fixture_oracle(self):
        by_file = {}
        for e in EXPECTED["rows"]:
            by_file.setdefault(e["file"], []).append(e)
        for rel, expected in by_file.items():
            with self.subTest(file=rel):
                acct = "/acct/" + rel.split("/")[0]
                got = list(ledger_scan.parse_file(FIX / rel, acct, log=self.log))
                self.assertEqual(len(got), len(expected))
                for g, e in zip(got, expected):
                    for k in KEYS:
                        self.assertEqual(getattr(g, k), e[k], f"{e['case']}.{k}")
                    self.assertEqual(g.account, acct)
                    self.assertEqual(g.day, e["ts"][:10])

    def test_uuid_fallback_is_logged(self):
        rel = "account-a/projects/-work-demo/sess-a1.jsonl"
        list(ledger_scan.parse_file(FIX / rel, "/a", log=self.log))
        self.assertTrue(any("uuid" in m for _, m in self.logged), self.logged)

    def test_cache_5m_derived_never_negative(self):
        f = Path(tempfile.mkdtemp()) / "a.jsonl"
        base = {"type": "assistant", "timestamp": ts(9), "sessionId": "s", "cwd": "/w/p", "requestId": "r"}
        lines = []
        for i, (total, one, five) in enumerate(((100, 30, None), (10, 50, None), (0, 0, None), (100, 30, 70))):
            cc = {"ephemeral_1h_input_tokens": one}
            if five is not None:
                cc["ephemeral_5m_input_tokens"] = five
            lines.append(dict(base, requestId=f"r{i}", message={"id": f"m{i}", "model": "claude-opus-5",
                          "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_creation_input_tokens": total, "cache_creation": cc}}))
        f.write_text("\n".join(json.dumps(l) for l in lines))
        got = [r.cache_5m for r in ledger_scan.parse_file(f, "/a", log=self.log)]
        self.assertEqual(got, [70, 0, 0, 70])

    def test_synthetic_skipped_unknown_model(self):
        f = Path(tempfile.mkdtemp()) / "a.jsonl"
        row = lambda model, i: {"type": "assistant", "timestamp": ts(9, i), "sessionId": "s", "cwd": "/w/p",
                                "requestId": f"r{i}", "message": {"id": f"m{i}", "model": model,
                                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}}
        noconf = row("x", 3)
        del noconf["message"]["model"]
        f.write_text("\n".join(json.dumps(r) for r in (row("<synthetic>", 1), row("claude-opus-5", 2), noconf)))
        got = list(ledger_scan.parse_file(f, "/a", log=self.log))
        self.assertEqual([r.model for r in got], ["claude-opus-5", "unknown"])

    def test_worktree_credited_to_repo(self):
        cases = {"/work/demo/.claude/worktrees/agent-7": "demo", "/work/demo": "demo",
                 "/work/demo/.claude/worktrees/agent-7/sub": "demo", "": "?", None: "?",
                 "/work/demo/.claude/worktrees/featurex": "featurex"}
        for cwd, want in cases.items():
            with self.subTest(cwd=cwd):
                self.assertEqual(ledger_scan.project_of(cwd), want)

    def test_naive_timestamp_row_is_skipped_and_logged(self):
        f = Path(tempfile.mkdtemp()) / "a.jsonl"
        f.write_text(json.dumps({"type": "assistant", "timestamp": "2026-11-26T10:00:00", "sessionId": "s",
                                 "cwd": "/w/p", "requestId": "r", "message": {"id": "m", "model": "claude-opus-5",
                                 "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}}))
        self.assertEqual(list(ledger_scan.parse_file(f, "/a", log=self.log)), [])
        self.assertTrue(self.logged)


class BetterTests(TzCase):
    def mk(self, **kw):
        base = dict(key="k", ts=ts(10), day="2026-11-26", account="/a", model="m", project="p", session="s",
                    input=1, cache_5m=0, cache_1h=0, cache_read=0, output=10, stop_reason="end_turn", complete=1)
        base.update(kw)
        return ledger_db.Row(**base)

    def test_higher_output_wins(self):
        a, b = self.mk(output=40, stop_reason=None, complete=0), self.mk(output=300)
        self.assertIs(ledger_scan.better(a, b), b)
        self.assertIs(ledger_scan.better(b, a), b)

    def test_stop_reason_breaks_output_tie(self):
        a, b = self.mk(stop_reason=None, complete=0), self.mk()
        self.assertIs(ledger_scan.better(a, b), b)
        self.assertIs(ledger_scan.better(b, a), b)

    def test_later_ts_breaks_the_next_tie(self):
        a, b = self.mk(ts=ts(10)), self.mk(ts=ts(11))
        self.assertIs(ledger_scan.better(a, b), b)
        self.assertIs(ledger_scan.better(b, a), b)

    def test_ts_compared_as_instants_across_offsets(self):
        a = self.mk(ts="2026-11-26T10:00:00-08:00")      # 18:00Z
        b = self.mk(ts="2026-11-26T18:30:00+00:00")      # later instant, earlier-looking text
        self.assertIs(ledger_scan.better(a, b), b)

    def test_total_order_never_depends_on_argument_order(self):
        rows = [self.mk(input=i, session=s) for i in (1, 2) for s in ("a", "b")]
        for a in rows:
            for b in rows:
                self.assertEqual(ledger_scan.better(a, b), ledger_scan.better(b, a))


class ScanTests(TzCase):
    def two_accounts(self):
        a, b = self.folder("acct-a"), self.folder("acct-b")
        for name, key in (("account-a", a), ("account-b", b)):
            shutil.copytree(FIX / name / "projects", Path(key) / "projects")
        return a, b

    def dump(self, conn):
        return [tuple(r) for r in conn.execute(
            "SELECT key, ts, day, account, model, project, session, input, cache_5m, cache_1h, cache_read,"
            " output, stop_reason, complete FROM records ORDER BY key")]

    def test_fixture_ledger_after_full_scan(self):
        a, b = self.two_accounts()
        conn = self.db()
        stats = ledger_scan.scan(conn, [a, b], full=True, log=self.log)
        rows = {r["key"]: r for r in conn.execute("SELECT * FROM records")}
        self.assertEqual(len(rows), 6)
        self.assertEqual(stats.total, 6)
        self.assertEqual(stats.files, 3)
        self.assertEqual(stats.new, 6)
        r = rows["msg_1:req_1"]
        self.assertEqual((r["output"], r["stop_reason"], r["complete"], r["account"]), (300, "end_turn", 1, a))
        self.assertEqual(rows["msg_2:req_2"]["complete"], 0)
        self.assertEqual(rows["msg_w1:req_w1"]["project"], "demo")

    def test_duplicates_across_files_and_accounts(self):
        a, b = self.two_accounts()
        # three files all carrying the same key with different outputs, in two accounts
        for acct, session, out, stop in ((a, "s1", 40, None), (a, "s2", 300, "end_turn"), (b, "s3", 120, "end_turn")):
            support.write_transcript(acct, [{"ts": ts(12), "model": "claude-opus-5", "output": out,
                                             "stop_reason": stop, "message_id": "msg_dup", "request_id": "req_dup",
                                             "session": session}], session=session)
        results = []
        for _ in range(3):
            d = Path(tempfile.mkdtemp(prefix="jusage-shuf-"))
            (d / "st").mkdir(mode=0o700)
            conn = ledger_db.open_db(d / "st", log=self.log)
            ledger_scan.scan(conn, [a, b], full=True, log=self.log)
            results.append(self.dump(conn))
            conn.close()
        self.assertTrue(all(r == results[0] for r in results))
        dup = [r for r in results[0] if r[0] == "msg_dup:req_dup"][0]
        self.assertEqual((dup[3], dup[11], dup[12]), (a, 300, "end_turn"))

    def test_shuffled_order_identical_rows(self):
        a = self.folder("acct-a")
        for i, (out, stop) in enumerate(((40, None), (300, "end_turn"), (300, None), (120, "end_turn"))):
            support.write_transcript(a, [{"ts": ts(12, i), "model": "claude-opus-5", "output": out,
                                          "stop_reason": stop, "message_id": "m", "request_id": "r",
                                          "session": f"s{i}"}], session=f"s{i}")
        files = sorted((Path(a) / "projects").rglob("*.jsonl"))
        outcomes = set()
        for seed in range(6):
            d = Path(tempfile.mkdtemp(prefix="jusage-ord-"))
            (d / "st").mkdir(mode=0o700)
            conn = ledger_db.open_db(d / "st", log=self.log)
            order = files[:]
            random.Random(seed).shuffle(order)
            for f in order:
                for row in ledger_scan.parse_file(f, a, log=self.log):
                    ledger_scan.upsert(conn, [row], {a: 0})
            outcomes.add(tuple(self.dump(conn)))
            conn.close()
        self.assertEqual(len(outcomes), 1)
        (rows,) = outcomes
        self.assertEqual((rows[0][11], rows[0][12]), (300, "end_turn"))

    def test_key_in_two_accounts_lands_on_1(self):
        a, b = self.two_accounts()
        conn = self.db()
        # the folders list is the locked order: b first would make b #1
        ledger_scan.scan(conn, [a, b], full=True, log=self.log)
        self.assertEqual(conn.execute("SELECT account FROM records WHERE key='msg_1:req_1'").fetchone()[0], a)
        # an incremental scan that sees the lower account's copy later still moves the row to it
        d = Path(tempfile.mkdtemp(prefix="jusage-inc-"))
        (d / "st").mkdir(mode=0o700)
        c2 = ledger_db.open_db(d / "st", log=self.log)
        self.addCleanup(c2.close)
        ledger_scan.scan(c2, [b], full=False, log=self.log)           # only #2's folder known yet
        self.assertEqual(c2.execute("SELECT account FROM records WHERE key='msg_1:req_1'").fetchone()[0], b)
        ledger_scan.scan(c2, [a, b], full=False, log=self.log)
        self.assertEqual(c2.execute("SELECT account FROM records WHERE key='msg_1:req_1'").fetchone()[0], a)

    def test_incomplete_kept_and_flagged(self):
        a = self.folder("acct-a")
        support.write_transcript(a, [{"ts": ts(9), "model": "claude-opus-5", "input": 7, "output": 25,
                                      "stop_reason": None, "cache_read": 5}])
        conn = self.db()
        ledger_scan.scan(conn, [a], log=self.log)
        r = conn.execute("SELECT input, cache_read, stop_reason, complete FROM records").fetchone()
        self.assertEqual((r[0], r[1], r[2], r[3]), (7, 5, None, 0))

    def test_later_scan_never_lowers_output(self):
        a = self.folder("acct-a")
        f = support.write_transcript(a, [{"ts": ts(9), "model": "claude-opus-5", "output": 300,
                                          "message_id": "m", "request_id": "r"}])
        conn = self.db()
        ledger_scan.scan(conn, [a], log=self.log)
        f.write_text("")
        support.write_transcript(a, [{"ts": ts(9), "model": "claude-opus-5", "output": 40, "stop_reason": None,
                                      "message_id": "m", "request_id": "r"}])
        st = ledger_scan.scan(conn, [a], log=self.log)
        self.assertEqual(st.files, 1)
        r = conn.execute("SELECT output, complete FROM records").fetchone()
        self.assertEqual((r[0], r[1]), (300, 1))

    def test_incremental_skips_unchanged_and_full_rereads(self):
        a, b = self.two_accounts()
        conn = self.db()
        self.assertEqual(ledger_scan.scan(conn, [a, b], log=self.log).files, 3)
        again = ledger_scan.scan(conn, [a, b], log=self.log)
        self.assertEqual((again.files, again.new, again.total), (0, 0, 6))
        self.assertEqual(ledger_scan.scan(conn, [a, b], full=True, log=self.log).files, 3)
        support.write_transcript(a, [{"ts": ts(13), "model": "claude-opus-5", "output": 5, "session": "sess-a1",
                                      "cwd": "/work/demo", "message_id": "mnew", "request_id": "rnew"}])
        grew = ledger_scan.scan(conn, [a, b], log=self.log)
        self.assertEqual((grew.files, grew.new, grew.total), (1, 1, 7))

    def test_dangling_symlink_gone(self):
        a = self.folder("acct-a")
        support.write_transcript(a, [{"ts": ts(9), "model": "claude-opus-5", "output": 1}])
        proj = next((Path(a) / "projects").iterdir())
        os.symlink(self.home.home / "no-such-target.jsonl", proj / "dangling.jsonl")
        conn = self.db()
        st = ledger_scan.scan(conn, [a], log=self.log)
        self.assertEqual((st.gone, st.files, st.total), (1, 1, 1))
        self.assertFalse([m for lvl, m in self.logged if lvl == "error"])

    def test_unreadable_file_logged_skipped_counted_and_retried(self):
        a = self.folder("acct-a")
        f = support.write_transcript(a, [{"ts": ts(9), "model": "claude-opus-5", "output": 1}])
        os.chmod(f, 0)
        self.addCleanup(lambda: os.chmod(f, 0o600))
        conn = self.db()
        st = ledger_scan.scan(conn, [a], log=self.log)
        self.assertEqual((st.unreadable, st.files, st.total), (1, 0, 0))
        self.assertTrue([m for lvl, m in self.logged if str(f) in m])
        os.chmod(f, 0o600)
        self.assertEqual(ledger_scan.scan(conn, [a], log=self.log).total, 1)

    def test_subagent_folders_are_scanned(self):
        a = self.folder("acct-a")
        f = support.write_transcript(a, [{"ts": ts(9), "model": "claude-opus-5", "output": 1, "message_id": "m1",
                                          "request_id": "r1"}])
        sub = f.parent / "sess-1" / "subagents" / "workflows"
        sub.mkdir(parents=True)
        other = support.write_transcript(a, [{"ts": ts(9, 5), "model": "claude-opus-5", "output": 2,
                                              "message_id": "m2", "request_id": "r2", "session": "agent-x"}],
                                         session="agent-x")
        shutil.move(str(other), sub / "agent-x.jsonl")
        conn = self.db()
        st = ledger_scan.scan(conn, [a], log=self.log)
        self.assertEqual(st.total, 2)

    def test_no_message_text_stored(self):
        a = self.folder("acct-a")
        marker = "SECRET-PROMPT-TEXT-91823"
        proj = Path(a) / "projects" / "-w-p"
        proj.mkdir(parents=True)
        line = {"type": "assistant", "timestamp": ts(9), "sessionId": "s", "cwd": "/w/p", "requestId": "r",
                "uuid": "u", "toolUseResult": marker,
                "message": {"id": "m", "model": "claude-opus-5", "stop_reason": "end_turn",
                            "content": [{"type": "text", "text": marker}],
                            "usage": {"input_tokens": 1, "output_tokens": 2}}}
        (proj / "s.jsonl").write_text(json.dumps(line) + "\n")
        conn = self.db()
        ledger_scan.scan(conn, [a], log=self.log)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for name in os.listdir(self.home.state):
            self.assertNotIn(marker.encode(), (self.home.state / name).read_bytes(), name)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(records)")}
        self.assertFalse(cols & {"content", "text", "message", "prompt"})


if __name__ == "__main__":
    unittest.main()
