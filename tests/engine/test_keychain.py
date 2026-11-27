"""E10 login finder (R-6...R-12, R-199). The Keychain is only ever the fake `security`."""
import hashlib
import json
import os
import re
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ju import constants, keychain, timeutil
from ju.contracts import Login, LoginError
from tests.engine import support

LEGACY = "Claude Code-credentials"
NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def svc(spelling: str) -> str:
    return "Claude Code-credentials-" + hashlib.sha256(spelling.encode()).hexdigest()[:8]


def ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def blob(token="tok-A", expires=None, key="claudeAiOauth"):
    inner = {"accessToken": token, "refreshToken": "REFRESH-DO-NOT-USE"}
    if expires is not None:
        inner["expiresAt"] = ms(expires)
    return json.dumps({key: inner})


MCP_ONLY = json.dumps({"mcpOAuth": {"server": {"accessToken": "mcp-tok"}}})
FUTURE = NOW + timedelta(hours=6)


class Base(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.home = str(self.th.home)
        self.env = {"HOME": self.home}
        self.default = str(self.th.home / ".claude")
        self.other = str(self.th.home / ".claude-newaccount")
        p = mock.patch.object(timeutil, "now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)

    def sec(self, items, hang=False):
        s = support.fake_security(items, hang=hang)
        s.activate()
        self.addCleanup(s.cleanup)
        return s


class NamingTests(Base):
    def test_naming_rule_pure(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("pure function ran a command")):
            got = keychain.candidate_services(self.other, {}, self.env)
        want = [svc(self.other), svc(self.other + "/"), svc(self.home + "/.claude-newaccount")]
        # as-given and resolved are the same string here, `~/<name>` is the same path too
        self.assertEqual(got, [svc(self.other), svc(self.other + "/")])
        self.assertEqual(want[0], got[0])

    def test_tilde_spelling_differs_when_folder_is_elsewhere(self):
        folder = "/Volumes/x/.claude-newaccount"
        got = keychain.candidate_services(folder, {}, self.env)
        self.assertEqual(got, [svc(folder), svc(folder + "/"), svc(self.home + "/.claude-newaccount")])

    def test_resolved_spelling_follows_a_symlink(self):
        real = Path(tempfile.mkdtemp(prefix="jusage-real-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(real, ignore_errors=True))
        link = self.th.home / "linked"
        link.symlink_to(real)
        got = keychain.candidate_services(str(link), {}, self.env)
        self.assertEqual(got[:3], [svc(str(link)), svc(str(real)), svc(str(link) + "/")])

    def test_hash_is_first_eight_hex_of_sha256(self):
        got = keychain.candidate_services(self.other, {}, self.env)[0]
        self.assertRegex(got, r"^Claude Code-credentials-[0-9a-f]{8}$")

    def test_securestorage_first(self):
        env = dict(self.env, CLAUDE_CONFIG_DIR=self.other + "/", CLAUDE_SECURESTORAGE_CONFIG_DIR="/secure/spot")
        got = keychain.candidate_services(self.other, {}, env)
        self.assertEqual(got[:3], [svc("/secure/spot"), svc(self.other + "/"), svc(self.other)])
        self.assertEqual(len(got), len(set(got)))

    def test_config_dir_spelling_verbatim_before_the_derived_ones(self):
        env = dict(self.env, CLAUDE_CONFIG_DIR="~/.claude-newaccount")
        got = keychain.candidate_services(self.other, {}, env)
        self.assertEqual(got[0], svc("~/.claude-newaccount"))
        self.assertIn(svc(self.other), got)

    def test_remembered_spellings_come_first(self):
        hints = {"config_dir": "/odd//spelling", "securestorage": "/remembered/secure"}
        env = dict(self.env, CLAUDE_CONFIG_DIR=self.other, CLAUDE_SECURESTORAGE_CONFIG_DIR="/env/secure")
        got = keychain.candidate_services(self.other, hints, env)
        self.assertEqual(got[:5], [svc("/remembered/secure"), svc("/odd//spelling"), svc("/env/secure"),
                                   svc(self.other), svc(self.other + "/")])

    def test_env_for_another_account_is_ignored(self):
        env = dict(self.env, CLAUDE_CONFIG_DIR=self.default + "/", CLAUDE_SECURESTORAGE_CONFIG_DIR="/secure/default")
        got = keychain.candidate_services(self.other, {}, env)
        self.assertNotIn(svc("/secure/default"), got)
        self.assertNotIn(svc(self.default + "/"), got)

    def test_none_hints_and_empty_values_are_fine(self):
        got = keychain.candidate_services(self.other, None, dict(self.env, CLAUDE_CONFIG_DIR=""))
        self.assertEqual(got[0], svc(self.other))
        got = keychain.candidate_services(self.other, {"config_dir": None, "securestorage": ""}, self.env)
        self.assertEqual(got[0], svc(self.other))

    def test_the_unsuffixed_item_is_never_a_candidate_service(self):
        for folder in (self.default, self.other):
            self.assertNotIn(LEGACY, keychain.candidate_services(folder, {}, self.env))


class FindLoginTests(Base):
    def test_finds_the_login_and_reads_the_expiry(self):
        self.sec({svc(self.other): blob("tok-A", FUTURE)})
        login = keychain.find_login(self.other, {}, self.env)
        self.assertIsInstance(login, Login)
        self.assertEqual(login.token, "tok-A")
        self.assertEqual(login.service, svc(self.other))
        self.assertEqual(login.expires_at, FUTURE)
        self.assertIsNotNone(login.expires_at.utcoffset())
        self.assertFalse(login.legacy)

    def test_no_expiry_in_the_item_is_not_expired(self):
        self.sec({svc(self.other): blob("tok-A")})
        login = keychain.find_login(self.other, {}, self.env)
        self.assertIsNone(login.expires_at)

    def test_first_valid_wins_over_later_expiry(self):
        s = self.sec({svc(self.other): blob("first", NOW + timedelta(minutes=5)),
                      svc(self.other + "/"): blob("later-expiry", NOW + timedelta(days=30))})
        self.assertEqual(keychain.find_login(self.other, {}, self.env).token, "first")
        self.assertEqual(s.services_asked(), [svc(self.other)])

    def test_securestorage_item_wins(self):
        self.sec({svc("/secure/spot"): blob("secure", FUTURE), svc(self.other): blob("plain", FUTURE)})
        env = dict(self.env, CLAUDE_CONFIG_DIR=self.other, CLAUDE_SECURESTORAGE_CONFIG_DIR="/secure/spot")
        self.assertEqual(keychain.find_login(self.other, {}, env).token, "secure")

    def test_mcp_only_is_no_login(self):
        self.sec({svc(self.other): MCP_ONLY})
        r = keychain.probe(svc(self.other))
        self.assertEqual((r.status, r.login), ("no_login", None))
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_missing")

    def test_mcp_only_item_does_not_hide_the_next_spelling(self):
        self.sec({svc(self.other): MCP_ONLY, svc(self.other + "/"): blob("second", FUTURE)})
        self.assertEqual(keychain.find_login(self.other, {}, self.env).token, "second")

    def test_probe_statuses(self):
        self.sec({"s-ok": blob("t", FUTURE), "s-garbage": "not json at all", "s-list": "[1, 2]",
                  "s-empty-token": json.dumps({"claudeAiOauth": {"accessToken": ""}})})
        self.assertEqual(keychain.probe("s-ok").status, "ok")
        self.assertEqual(keychain.probe("s-missing").status, "absent")
        self.assertEqual(keychain.probe("s-garbage").status, "unparsable")
        self.assertEqual(keychain.probe("s-list").status, "unparsable")
        self.assertEqual(keychain.probe("s-empty-token").status, "no_login")

    def test_denied_is_its_own_status_and_error(self):
        d = Path(tempfile.mkdtemp(prefix="jusage-denysec-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        exe = d / "security"
        exe.write_text("#!/bin/sh\necho 'security: User interaction is not allowed.' >&2\nexit 36\n")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        old = os.environ["PATH"]
        os.environ["PATH"] = str(d) + os.pathsep + old
        self.addCleanup(lambda: os.environ.__setitem__("PATH", old))
        r = keychain.probe(svc(self.other))
        self.assertEqual(r.status, "denied")
        self.assertIn("User interaction is not allowed", r.detail)
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "keychain_denied")
        self.assertIn("User interaction is not allowed", cm.exception.text)

    def test_absent_everywhere_is_login_missing(self):
        self.sec({})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_missing")
        self.assertIn("claude /login", cm.exception.text)

    def test_unparsable_everywhere_is_login_missing_with_its_own_text(self):
        self.sec({svc(self.other): "garbage"})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_missing")
        self.assertIn("not valid JSON", cm.exception.text)

    def test_expired_login(self):
        self.sec({svc(self.other): blob("old", NOW - timedelta(seconds=1))})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_expired")
        self.assertEqual(cm.exception.text, "Open Claude Code on this account, then refresh")

    def test_expiry_edge_is_expired(self):
        self.sec({svc(self.other): blob("old", NOW)})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_expired")

    def test_expired_first_login_is_not_replaced_by_a_later_valid_one(self):
        self.sec({svc(self.other): blob("old", NOW - timedelta(hours=1)),
                  svc(self.other + "/"): blob("fresh", FUTURE)})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_expired")

    def test_timeout_says_prompt_may_be_waiting(self):
        s = self.sec({svc(self.other): blob("t", FUTURE)}, hang=True)
        with mock.patch.object(constants, "KEYCHAIN_TIMEOUT_S", 0.5):
            with self.assertRaises(LoginError) as cm:
                keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "keychain_waiting")
        self.assertIn("Keychain prompt may be waiting", cm.exception.text)
        self.assertEqual(len(s.calls()), 1, "a timeout stops the search; no second prompt")

    def test_one_read_per_service(self):
        s = self.sec({})
        with self.assertRaises(LoginError):
            keychain.find_login(self.default, {}, self.env)
        asked = s.services_asked()
        self.assertEqual(len(asked), len(set(asked)))


class LegacyTests(Base):
    def test_legacy_only_default_folder_and_no_hashed_login(self):
        s = self.sec({LEGACY: blob("legacy-tok", FUTURE)})
        login = keychain.find_login(self.default, {}, self.env)
        self.assertTrue(login.legacy)
        self.assertEqual(login.service, LEGACY)
        self.assertEqual(login.token, "legacy-tok")
        self.assertEqual(s.services_asked()[-1], LEGACY)

    def test_legacy_never_for_another_folder(self):
        s = self.sec({LEGACY: blob("legacy-tok", FUTURE)})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_missing")
        self.assertNotIn(LEGACY, s.services_asked())

    def test_legacy_not_read_when_a_hashed_login_is_ok(self):
        s = self.sec({svc(self.default): blob("hashed", FUTURE), LEGACY: blob("legacy-tok", FUTURE)})
        login = keychain.find_login(self.default, {}, self.env)
        self.assertEqual((login.token, login.legacy), ("hashed", False))
        self.assertNotIn(LEGACY, s.services_asked())

    def test_hashed_mcp_only_still_allows_legacy(self):
        self.sec({svc(self.default): MCP_ONLY, LEGACY: blob("legacy-tok", FUTURE)})
        self.assertTrue(keychain.find_login(self.default, {}, self.env).legacy)

    def test_legacy_not_read_when_securestorage_value_applies(self):
        s = self.sec({LEGACY: blob("legacy-tok", FUTURE)})
        env = dict(self.env, CLAUDE_SECURESTORAGE_CONFIG_DIR="/secure/spot")
        with self.assertRaises(LoginError):
            keychain.find_login(self.default, {}, env)
        self.assertNotIn(LEGACY, s.services_asked())

    def test_legacy_not_read_when_config_dir_value_applies(self):
        s = self.sec({LEGACY: blob("legacy-tok", FUTURE)})
        env = dict(self.env, CLAUDE_CONFIG_DIR=self.default)
        with self.assertRaises(LoginError):
            keychain.find_login(self.default, {}, env)
        self.assertNotIn(LEGACY, s.services_asked())

    def test_legacy_not_read_when_a_remembered_spelling_exists(self):
        s = self.sec({LEGACY: blob("legacy-tok", FUTURE)})
        with self.assertRaises(LoginError):
            keychain.find_login(self.default, {"config_dir": self.default + "/", "securestorage": None}, self.env)
        self.assertNotIn(LEGACY, s.services_asked())

    def test_env_for_another_account_does_not_block_legacy(self):
        self.sec({LEGACY: blob("legacy-tok", FUTURE)})
        env = dict(self.env, CLAUDE_CONFIG_DIR=self.other, CLAUDE_SECURESTORAGE_CONFIG_DIR="/secure/other")
        self.assertTrue(keychain.find_login(self.default, {}, env).legacy)

    def test_expired_legacy_is_login_expired(self):
        self.sec({LEGACY: blob("legacy-tok", NOW - timedelta(hours=1))})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.default, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_expired")

    def test_legacy_item_denied_is_reported(self):
        self.sec({})
        with self.assertRaises(LoginError) as cm:
            keychain.find_login(self.default, {}, self.env)
        self.assertEqual(cm.exception.kind, "login_missing")


class ReadOnlyTests(Base):
    def test_only_find_generic_password_called(self):
        s = self.sec({svc(self.default): MCP_ONLY, LEGACY: blob("legacy-tok", FUTURE)})
        keychain.find_login(self.default, {}, self.env)
        self.assertEqual(s.refused(), [])
        self.assertTrue(s.calls())
        for argv, service in zip(s.calls(), s.services_asked()):
            self.assertEqual(argv, ["find-generic-password", "-s", service, "-w"])

    def test_source_never_names_a_write_verb(self):
        src = (constants.HERE / "keychain.py").read_text()
        for verb in ("add-generic-password", "delete-generic-password", "set-generic-password",
                     "add-internet-password", "delete-internet-password", "set-keychain",
                     "import-keychain", "-U", "stdin.write", "input="):
            code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
            self.assertNotRegex(code, r"(?<![\w-])" + re.escape(verb) + r"(?![\w-])", verb)

    def test_stdin_is_closed_so_nothing_can_be_written(self):
        src = (constants.HERE / "keychain.py").read_text()
        self.assertIn("DEVNULL", src)

    def test_token_is_never_in_repr_or_error_text(self):
        self.sec({svc(self.other): blob("SECRET-TOKEN-XYZ", FUTURE)})
        login = keychain.find_login(self.other, {}, self.env)
        r = keychain.probe(svc(self.other))
        self.assertNotIn("SECRET-TOKEN-XYZ", repr(login))
        self.assertNotIn("SECRET-TOKEN-XYZ", repr(r))
        self.assertNotIn("REFRESH-DO-NOT-USE", repr(login) + repr(r))

    def test_the_refresh_token_is_never_kept(self):
        self.sec({svc(self.other): blob("t", FUTURE)})
        login = keychain.find_login(self.other, {}, self.env)
        self.assertFalse(any("REFRESH" in str(v) for v in vars(login).values()))


class NonMacTests(Base):
    def write_creds(self, folder, text):
        Path(folder).mkdir(parents=True, exist_ok=True)
        f = Path(folder) / ".credentials.json"
        f.write_text(text)
        return f

    def test_reads_credentials_file_without_touching_security(self):
        s = self.sec({})
        self.write_creds(self.other, blob("file-tok", FUTURE))
        with mock.patch.object(keychain, "_is_macos", return_value=False):
            login = keychain.find_login(self.other, {}, self.env)
        self.assertEqual((login.token, login.legacy), ("file-tok", False))
        self.assertEqual(login.service, str(Path(self.other) / ".credentials.json"))
        self.assertEqual(s.calls(), [])

    def test_missing_file_expired_and_garbage(self):
        with mock.patch.object(keychain, "_is_macos", return_value=False):
            with self.assertRaises(LoginError) as cm:
                keychain.find_login(self.other, {}, self.env)
            self.assertEqual(cm.exception.kind, "login_missing")
            self.write_creds(self.other, blob("t", NOW - timedelta(hours=1)))
            with self.assertRaises(LoginError) as cm:
                keychain.find_login(self.other, {}, self.env)
            self.assertEqual(cm.exception.kind, "login_expired")
            self.write_creds(self.other, "garbage")
            with self.assertRaises(LoginError) as cm:
                keychain.find_login(self.other, {}, self.env)
            self.assertEqual(cm.exception.kind, "login_missing")
            self.write_creds(self.other, MCP_ONLY)
            with self.assertRaises(LoginError) as cm:
                keychain.find_login(self.other, {}, self.env)
            self.assertEqual(cm.exception.kind, "login_missing")

    def test_file_is_not_modified(self):
        f = self.write_creds(self.other, blob("t", FUTURE))
        before = (f.read_bytes(), f.stat().st_mtime_ns)
        with mock.patch.object(keychain, "_is_macos", return_value=False):
            keychain.find_login(self.other, {}, self.env)
        self.assertEqual((f.read_bytes(), f.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
