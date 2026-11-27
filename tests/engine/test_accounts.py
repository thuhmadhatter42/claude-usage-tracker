"""E5 accounts: folders, keys, numbers, identity. Keys are full resolved paths."""
import json
import os
import unittest
from pathlib import Path

from ju import accounts, config, houselog
from tests.engine import support


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.addCleanup(houselog.reset)
        houselog.configure(self.th.state)
        self.home = self.th.home

    def folder(self, name, projects=True, claude_json=None):
        d = self.home / name
        d.mkdir(parents=True, exist_ok=True)
        if projects:
            (d / "projects").mkdir(exist_ok=True)
        if claude_json is not None:
            (d / ".claude.json").write_text(json.dumps(claude_json))
        return d

    def env(self, **kw):
        return dict({"HOME": str(self.home)}, **kw)

    def cfg(self):
        return config.ConfigStore.load(self.th.state)

    # ---- discovery
    def test_discovers_dot_claude_folders_with_projects_or_claude_json(self):
        a = self.folder(".claude")
        b = self.folder(".claude-newaccount", projects=False, claude_json={})
        self.folder(".claude-empty", projects=False)          # no projects, no .claude.json
        (self.home / ".claude.json").write_text("{}")         # a file, not a folder
        got = accounts.discover_folders(self.env(), self.cfg())
        self.assertEqual(got, [a, b])

    def test_state_dir_is_not_an_account(self):
        (self.home / ".claude-usage" / "projects").mkdir()    # even with a projects dir
        self.folder(".claude")
        got = accounts.discover_folders(self.env(), self.cfg())
        self.assertEqual([p.name for p in got], [".claude"])

    def test_backup_folders_excluded(self):
        self.folder(".claude")
        for n in (".claude.bak", ".claude-backup", ".claude.old", ".claude-Backup-2", ".claude.bak-1"):
            self.folder(n)
        got = accounts.discover_folders(self.env(), self.cfg())
        self.assertEqual([p.name for p in got], [".claude"])

    def test_claude_config_dir_env_added(self):
        elsewhere = self.home / "work" / "acct"
        elsewhere.mkdir(parents=True)
        got = accounts.discover_folders(self.env(CLAUDE_CONFIG_DIR=str(elsewhere)), self.cfg())
        self.assertEqual(got, [elsewhere])

    def test_claude_config_dir_env_missing_folder_skipped(self):
        got = accounts.discover_folders(self.env(CLAUDE_CONFIG_DIR=str(self.home / "nope")), self.cfg())
        self.assertEqual(got, [])

    def test_remembered_folders_added_when_they_still_exist(self):
        keep = self.home / "w" / "k"
        keep.mkdir(parents=True)
        with config.ConfigStore.edit(self.th.state) as c:
            config.remember_folders(c, [str(keep), str(self.home / "gone"), str(self.home / ".claude.bak")])
        (self.home / ".claude.bak").mkdir()
        got = accounts.discover_folders(self.env(), self.cfg())
        self.assertEqual(got, [keep])

    def test_dedup_through_symlink_and_env(self):
        real = self.folder(".claude")
        link = self.home / "link"
        link.symlink_to(real)
        got = accounts.discover_folders(self.env(CLAUDE_CONFIG_DIR=str(link)), self.cfg())
        self.assertEqual(got, [real])

    # ---- keys
    def test_folder_key_is_resolved_absolute(self):
        real = self.folder(".claude")
        link = self.home / "link"
        link.symlink_to(real)
        self.assertEqual(accounts.folder_key(link), str(real))
        self.assertEqual(accounts.folder_key("~/.claude"), str(real))
        self.assertEqual(accounts.folder_key(str(real) + "/"), str(real))

    def test_twin_basename_keeps_full_key(self):
        a = accounts.folder_key(self.folder("one/.claude"))
        b = accounts.folder_key(self.folder("two/.claude"))
        c = accounts.folder_key(self.folder(".claude-solo"))
        keys = [a, b, c]
        self.assertEqual(accounts.short_name(c, keys), ".claude-solo")
        self.assertEqual(accounts.short_name(a, keys), "one/.claude")
        self.assertEqual(accounts.short_name(b, keys), "two/.claude")
        self.assertTrue(a.startswith("/") and b.startswith("/") and a != b)

    def test_short_name_alone(self):
        k = accounts.folder_key(self.folder(".claude"))
        self.assertEqual(accounts.short_name(k, [k]), ".claude")

    # ---- numbers
    def test_reserved_numbers_and_first_seen(self):
        d1 = accounts.folder_key(self.folder(".claude"))
        d2 = accounts.folder_key(self.folder(".claude-newaccount"))
        x = accounts.folder_key(self.folder(".claude-work"))
        y = accounts.folder_key(self.folder(".claude-play"))
        with config.ConfigStore.edit(self.th.state) as c:
            # others are seen BEFORE #1 and #2: still 3 and 4, #1/#2 stay reserved
            self.assertEqual(accounts.number_for(c, x), 3)
            self.assertEqual(accounts.number_for(c, y), 4)
            self.assertEqual(accounts.number_for(c, d2), 2)
            self.assertEqual(accounts.number_for(c, d1), 1)

    def test_numbers_never_renumber(self):
        x = accounts.folder_key(self.folder(".claude-work"))
        y = accounts.folder_key(self.folder(".claude-play"))
        with config.ConfigStore.edit(self.th.state) as c:
            accounts.number_for(c, x)
            accounts.number_for(c, y)
        # x folder disappears from the Mac; a new one arrives; nothing shifts
        z = accounts.folder_key(self.folder(".claude-new"))
        with config.ConfigStore.edit(self.th.state) as c:
            self.assertEqual(accounts.number_for(c, z), 5)
            self.assertEqual(accounts.number_for(c, y), 4)
            self.assertEqual(accounts.number_for(c, x), 3)
        snap = self.cfg()
        self.assertEqual(accounts.number_for(snap, y), 4)

    def test_snapshot_cannot_assign_a_new_number(self):
        x = accounts.folder_key(self.folder(".claude-work"))
        with self.assertRaises(config.ConfigReadOnly):
            accounts.number_for(self.cfg(), x)

    def test_snapshot_answers_reserved_without_writing(self):
        d1 = accounts.folder_key(self.folder(".claude"))
        self.assertEqual(accounts.number_for(self.cfg(), d1), 1)
        self.assertFalse((self.th.state / "config.json").exists())

    def test_order_is_number_not_email(self):
        d1 = accounts.folder_key(self.folder(".claude", claude_json={"oauthAccount": {"emailAddress": "zed@x.com", "accountUuid": "u1"}}))
        d2 = accounts.folder_key(self.folder(".claude-newaccount", claude_json={"oauthAccount": {"emailAddress": "amy@x.com", "accountUuid": "u2"}}))
        x = accounts.folder_key(self.folder(".claude-work"))
        with config.ConfigStore.edit(self.th.state) as c:
            self.assertEqual(accounts.ordered([x, d2, d1], c), [d1, d2, x])

    # ---- identity
    def test_identity_read_from_oauth_account(self):
        k = accounts.folder_key(self.folder(".claude-newaccount", claude_json={
            "oauthAccount": {"emailAddress": "a@b.co", "accountUuid": "uu-1"}}))
        ident = accounts.read_identity(k)
        self.assertEqual((ident.email, ident.uuid), ("a@b.co", "uu-1"))
        self.assertGreater(ident.mtime, 0)

    def test_identity_newest_json_wins(self):
        d = self.folder(".claude", claude_json={"oauthAccount": {"emailAddress": "old@x.co", "accountUuid": "old"}})
        top = self.home / ".claude.json"
        top.write_text(json.dumps({"oauthAccount": {"emailAddress": "new@x.co", "accountUuid": "new"}}))
        now = 2_000_000_000
        os.utime(d / ".claude.json", (now - 100, now - 100))
        os.utime(top, (now, now))
        self.assertEqual(accounts.read_identity(accounts.folder_key(d)).uuid, "new")
        os.utime(d / ".claude.json", (now + 50, now + 50))
        self.assertEqual(accounts.read_identity(accounts.folder_key(d)).uuid, "old")

    def test_home_claude_json_only_counts_for_default_folder(self):
        (self.home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "h@x.co", "accountUuid": "h"}}))
        other = self.folder(".claude-other")
        self.assertIsNone(accounts.read_identity(accounts.folder_key(other)))

    def test_identity_needs_both_email_and_uuid(self):
        k = accounts.folder_key(self.folder(".claude-a", claude_json={"oauthAccount": {"emailAddress": "a@b.co"}}))
        self.assertIsNone(accounts.read_identity(k))

    def test_unreadable_json_uses_cached_identity_and_logs(self):
        k = accounts.folder_key(self.folder(".claude-a", claude_json={"oauthAccount": {"emailAddress": "a@b.co", "accountUuid": "u9"}}))
        with config.ConfigStore.edit(self.th.state) as c:
            first = accounts.read_identity(k, c)
        self.assertEqual(first.uuid, "u9")
        (Path(k) / ".claude.json").write_text('{"oauthAccount": {"emai')   # mid-write
        got = accounts.read_identity(k, self.cfg())
        self.assertEqual((got.email, got.uuid), ("a@b.co", "u9"))
        self.assertIn("cached identity", houselog.log_path().read_text())

    def test_no_file_and_no_cache_is_none(self):
        self.assertIsNone(accounts.read_identity(accounts.folder_key(self.folder(".claude-z")), self.cfg()))

    def test_two_folders_one_uuid_grouped(self):
        I = accounts.Identity
        ids = {"/a": I("x@y", "U1", 1.0), "/b": I("x@y", "U1", 2.0), "/c": I("z@y", "U2", 1.0), "/d": None, "/e": None}
        got = accounts.group_by_uuid(["/b", "/a", "/c", "/d", "/e"], ids)
        self.assertEqual(got, [["/b", "/a"], ["/c"], ["/d"], ["/e"]])


if __name__ == "__main__":
    unittest.main()
