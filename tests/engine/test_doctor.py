"""E22 doctor: per account, every Keychain spelling tried and its status, the chosen item, the
legacy warning, identity + fingerprint, gate and status-line state; one OK/WARN/FAIL verdict per
account; never a secret, on screen or in the log."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from tests.engine import support
from ju import accounts, doctor, feed, fsutil, houselog, keychain, timeutil
from ju.live_store import LiveStore, StoreIO
from ju.config import ConfigStore

TOKEN = "sk-ant-oat01-" + "Q" * 60
PST = timezone(timedelta(hours=-8))


def login_blob(expires_in_s=3600 * 3):
    exp = (datetime.now(timezone.utc) + timedelta(seconds=expires_in_s)).timestamp() * 1000
    return json.dumps({"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": exp}})


class DoctorCase(unittest.TestCase):
    def setUp(self):
        self.h = support.temp_home()
        self.addCleanup(self.h.cleanup)
        houselog.configure(self.h.state)
        self.addCleanup(houselog.reset)
        self.k1 = accounts.folder_key(self.h.home / ".claude")
        (self.h.home / ".claude" / "projects").mkdir(parents=True)
        (self.h.home / ".claude" / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "sam@example.com", "accountUuid": "u-1"}}))
        self.env = {"HOME": str(self.h.home)}
        self.services = keychain.candidate_services(self.k1, None, self.env)

    def run_doctor(self, items, **kw):
        with support.fake_security(items) as sec:
            store = LiveStore(StoreIO(fsutil.write_private, fsutil.move_aside_corrupt, fsutil.read_json,
                                      fsutil.CorruptFile, houselog.log))
            lines = doctor.run(self.h.state, self.env, store=store, **kw)
            self.asked, self.refused = sec.services_asked(), sec.refused()
        return lines

    def test_lists_every_spelling(self):
        lines = self.run_doctor({self.services[1]: login_blob()})
        text = "\n".join(lines)
        for s in self.services:
            self.assertIn(s, text)
        chosen = [l for l in lines if l.lstrip().startswith("→")]
        self.assertEqual(len(chosen), 1)
        self.assertIn(self.services[1], chosen[0])
        self.assertIn("absent", next(l for l in lines if self.services[0] in l))
        self.assertIn(f"chosen: {self.services[1]}", text)
        self.assertIn("u-1", text)
        self.assertTrue(lines[-1].startswith("OK "), lines[-1])
        # stops at the first item that holds a login, like find_login
        self.assertEqual(self.asked, self.services[:2])
        self.assertEqual(self.refused, [])

    def test_never_prints_token(self):
        lines = self.run_doctor({self.services[0]: login_blob()})
        self.assertNotIn(TOKEN, "\n".join(lines))
        self.assertNotIn("QQQQQQQQ", "\n".join(lines))
        log = houselog.log_path(self.h.state)
        if log.exists():
            self.assertNotIn("QQQQQQQQ", log.read_text())

    def test_no_login_fails_and_names_the_fix(self):
        lines = self.run_doctor({})
        self.assertIn("legacy", "\n".join(lines))          # the unsuffixed item was tried too (R-10)
        self.assertTrue(lines[-1].startswith("FAIL "), lines[-1])
        self.assertIn("claude /login", lines[-1])

    def test_legacy_item_warns(self):
        lines = self.run_doctor({keychain.LEGACY_SERVICE: login_blob()})
        text = "\n".join(lines)
        self.assertIn("legacy", text)
        self.assertTrue(lines[-1].startswith("WARN "), lines[-1])

    def test_expired_login_fails(self):
        lines = self.run_doctor({self.services[0]: login_blob(-60)})
        self.assertTrue(lines[-1].startswith("FAIL "), lines[-1])
        self.assertIn("expired", "\n".join(lines))

    def test_gate_fingerprint_and_statusline_state(self):
        now = timeutil.now()
        live = {"schema": 2, "accounts": {self.k1: {
            "fetched": timeutil.iso(now - timedelta(minutes=3)), "data": support.load_fixture("reply-limits.json"),
            "error": {"kind": "rate_limited", "text": "Anthropic is rate-limiting usage reads", "at": timeutil.iso(now)},
            "warning": None, "source": "service", "blocked_until": timeutil.iso(now + timedelta(minutes=9)),
            "backoff_step": 1, "retry_after_explicit": True, "last_manual": None,
            "reset_anchor": {"minute_of_week": 1500, "confirmations": 3, "candidate": None, "candidate_count": 0},
            "identity_uuid": "u-1"}}}
        (self.h.state / "live.json").write_text(json.dumps(live))
        with ConfigStore.edit(self.h.state) as cfg:
            cfg.statusline = {self.k1: {"enabled": True, "original": "x", "wrapped": "y"}}
        cap = feed.capture_path(self.h.state, self.k1)
        cap.parent.mkdir(mode=0o700)
        cap.write_text(json.dumps({"captured_at": timeutil.iso(now - timedelta(minutes=4)), "folder": self.k1,
                                   "rate_limits": {}}))
        text = "\n".join(self.run_doctor({self.services[0]: login_blob()}))
        self.assertIn("learned", text)
        self.assertIn("Tue 01:00 UTC", text)                 # minute 1500 = Tuesday 01:00 UTC
        self.assertIn("rate_limited", text)
        self.assertIn("blocked until", text)
        self.assertIn("status line: on", text)
        self.assertIn("4m", text)

    def test_no_accounts_says_so(self):
        import shutil
        shutil.rmtree(self.h.home / ".claude")
        lines = self.run_doctor({})
        self.assertTrue(lines[-1].startswith("FAIL "))
        self.assertIn("no Claude Code config folder", lines[-1])


if __name__ == "__main__":
    unittest.main()
