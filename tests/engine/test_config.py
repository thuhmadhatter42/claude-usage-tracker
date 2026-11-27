"""E4 engine config: one locked writer, corrupt moved aside, settings bounded."""
import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from ju import config, constants, houselog
from tests.engine import support

ENGINE = str(constants.HERE.parent)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.addCleanup(houselog.reset)
        self.state = self.th.state

    def raw(self):
        return json.loads((self.state / "config.json").read_text())

    # ---- load / defaults
    def test_fresh_load_has_defaults_and_writes_nothing(self):
        cfg = config.ConfigStore.load(self.state)
        self.assertEqual(cfg.read_every_s, constants.READ_EVERY_DEFAULT)
        self.assertEqual(cfg.config_dirs, [])
        self.assertEqual(cfg.accounts, {})
        self.assertIsNone(cfg.reports_dir)
        self.assertFalse(cfg.share_projects)
        self.assertFalse((self.state / "config.json").exists())

    def test_snapshot_is_read_only(self):
        cfg = config.ConfigStore.load(self.state)
        with self.assertRaises(config.ConfigReadOnly):
            cfg.user = "Sam"
        with self.assertRaises(config.ConfigReadOnly):
            cfg["user"] = "Sam"

    def test_edit_writes_0600_and_load_sees_it(self):
        with config.ConfigStore.edit(self.state) as cfg:
            cfg.user = "Sam"
            cfg.share_projects = True
        self.assertEqual(stat.S_IMODE(os.stat(self.state / "config.json").st_mode), 0o600)
        cfg = config.ConfigStore.load(self.state)
        self.assertEqual(cfg.user, "Sam")
        self.assertTrue(cfg.share_projects)

    def test_exception_in_edit_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            with config.ConfigStore.edit(self.state) as cfg:
                cfg.user = "Sam"
                raise RuntimeError("boom")
        self.assertFalse((self.state / "config.json").exists())

    def test_unchanged_edit_does_not_rewrite(self):
        with config.ConfigStore.edit(self.state) as cfg:
            cfg.user = "Sam"
        before = (self.state / "config.json").stat().st_mtime_ns
        with config.ConfigStore.edit(self.state):
            pass
        self.assertEqual((self.state / "config.json").stat().st_mtime_ns, before)

    # ---- the lock
    def test_two_writers_do_not_lose_keys(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from ju import config\n"
            "from pathlib import Path\n"
            "tag = sys.argv[3]\n"
            "for i in range(40):\n"
            "    with config.ConfigStore.edit(Path(sys.argv[2])) as cfg:\n"
            "        cfg.accounts = dict(cfg.accounts, **{f'{tag}{i}': i + 1})\n"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", code, ENGINE, str(self.state), tag],
                             stderr=subprocess.PIPE, text=True)
            for tag in ("a", "b")
        ]
        for p in procs:
            _, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        accounts = self.raw()["accounts"]
        self.assertEqual(len(accounts), 80)

    # ---- corrupt
    def test_corrupt_moved_aside_with_message(self):
        (self.state / "config.json").write_text("{not json")
        lines = []
        cfg = config.ConfigStore.load(self.state, notify=lines.append)
        self.assertEqual(cfg.read_every_s, constants.READ_EVERY_DEFAULT)
        self.assertFalse((self.state / "config.json").exists())
        self.assertEqual((self.state / "config.json.corrupt").read_text(), "{not json")
        self.assertEqual(len(lines), 1)
        self.assertIn("config.json", lines[0])
        self.assertIn("config.json.corrupt", lines[0])
        self.assertIn("config.json.corrupt", houselog.log_path(self.state).read_text())

    def test_earlier_corrupt_not_overwritten(self):
        (self.state / "config.json.corrupt").write_text("first")
        (self.state / "config.json").write_text("[1]")
        config.ConfigStore.load(self.state, notify=lambda s: None)
        self.assertEqual((self.state / "config.json.corrupt").read_text(), "first")
        self.assertEqual((self.state / "config.json.corrupt.1").read_text(), "[1]")

    def test_corrupt_inside_edit_starts_empty_and_saves_fresh(self):
        (self.state / "config.json").write_text("garbage")
        with config.ConfigStore.edit(self.state, notify=lambda s: None) as cfg:
            cfg.user = "Sam"
        self.assertEqual(self.raw()["user"], "Sam")
        self.assertTrue((self.state / "config.json.corrupt").exists())

    def test_wrong_typed_known_key_refused_loudly(self):
        for key, value in (("read_every_s", "60"), ("read_every_s", 5), ("read_every_s", 601),
                           ("config_dirs", "x"), ("accounts", []), ("share_projects", "yes")):
            with self.subTest(key=key, value=value):
                (self.state / "config.json").write_text(json.dumps({key: value}))
                with self.assertRaises(config.ConfigError) as cm:
                    config.ConfigStore.load(self.state)
                self.assertIn(key, str(cm.exception))
                self.assertTrue((self.state / "config.json").exists())  # not moved: a human edit to fix

    def test_unknown_keys_preserved(self):
        (self.state / "config.json").write_text(json.dumps({"future": {"a": [1]}, "user": "Sam"}))
        with config.ConfigStore.edit(self.state) as cfg:
            cfg.user = "Lee"
        d = self.raw()
        self.assertEqual(d["future"], {"a": [1]})
        self.assertEqual(d["user"], "Lee")

    # ---- read_every
    def test_read_every_bounds(self):
        with config.ConfigStore.edit(self.state) as cfg:
            for ok in (15, 60, 600):
                config.set_read_every(cfg, ok)
                self.assertEqual(cfg.read_every_s, ok)
            for bad in (14, 601, 0, -1, 60.5, "60", None, True):
                with self.subTest(bad=bad):
                    with self.assertRaises(config.ConfigError):
                        config.set_read_every(cfg, bad)
            self.assertEqual(cfg.read_every_s, 600)

    # ---- install id
    def test_install_id_is_four_hex_and_stable(self):
        with config.ConfigStore.edit(self.state) as cfg:
            a = config.install_id(cfg)
        self.assertRegex(a, r"^[0-9a-f]{4}$")
        with config.ConfigStore.edit(self.state) as cfg:
            self.assertEqual(config.install_id(cfg), a)
        self.assertEqual(config.install_id(config.ConfigStore.load(self.state)), a)

    def test_install_id_missing_in_snapshot_refused_not_invented(self):
        with self.assertRaises(config.ConfigReadOnly):
            config.install_id(config.ConfigStore.load(self.state))

    # ---- spellings (R-9)
    def test_remember_spellings(self):
        folder = str(self.th.home / ".claude-x")
        env = {"CLAUDE_CONFIG_DIR": "~/.claude-x/", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/some/where"}
        with config.ConfigStore.edit(self.state) as cfg:
            self.assertTrue(config.remember_spellings(cfg, folder, env))
            self.assertFalse(config.remember_spellings(cfg, folder, env))   # nothing new
        sp = config.ConfigStore.load(self.state).spellings[folder]
        self.assertEqual(sp, {"config_dir": "~/.claude-x/", "securestorage": "/some/where"})

    def test_remember_spellings_only_touches_what_was_seen(self):
        folder = "/a/.claude"
        with config.ConfigStore.edit(self.state) as cfg:
            config.remember_spellings(cfg, folder, {"CLAUDE_CONFIG_DIR": "/a/.claude"})
            self.assertFalse(config.remember_spellings(cfg, folder, {}))
            self.assertFalse(config.remember_spellings(cfg, folder, {"CLAUDE_CONFIG_DIR": ""}))
            config.remember_spellings(cfg, folder, {"CLAUDE_SECURESTORAGE_CONFIG_DIR": "/s"})
            self.assertEqual(cfg.spellings[folder], {"config_dir": "/a/.claude", "securestorage": "/s"})

    def test_remember_folders(self):
        with config.ConfigStore.edit(self.state) as cfg:
            self.assertTrue(config.remember_folders(cfg, ["/a/.claude", "/b/.claude"]))
            self.assertFalse(config.remember_folders(cfg, ["/a/.claude"]))
            self.assertEqual(cfg.config_dirs, ["/a/.claude", "/b/.claude"])

    # ---- reports dir (R-119)
    def test_reports_dir_missing_without_create_refused(self):
        p = self.th.home / "shared"
        cfg = config.ConfigStore.load(self.state)
        with self.assertRaises(config.ConfigError) as cm:
            config.reports_dir(cfg, str(p), create=False)
        self.assertEqual(str(cm.exception), f"Shared folder {p} is missing; pass --reports to create it")
        self.assertFalse(p.exists())

    def test_reports_dir_create_makes_and_remembers(self):
        p = self.th.home / "shared" / "deep"
        with config.ConfigStore.edit(self.state) as cfg:
            got = config.reports_dir(cfg, str(p), create=True)
        self.assertEqual(got, p)
        self.assertTrue(p.is_dir())
        self.assertEqual(config.ConfigStore.load(self.state).reports_dir, str(p))

    def test_reports_dir_from_config_and_tilde(self):
        (self.th.home / "sh").mkdir()
        with config.ConfigStore.edit(self.state) as cfg:
            cfg.reports_dir = "~/sh"
        cfg = config.ConfigStore.load(self.state)
        self.assertEqual(config.reports_dir(cfg, None, create=False), self.th.home / "sh")

    def test_reports_dir_none_set_refused(self):
        cfg = config.ConfigStore.load(self.state)
        with self.assertRaises(config.ConfigError) as cm:
            config.reports_dir(cfg, None, create=False)
        self.assertIn("--reports", str(cm.exception))

    def test_reports_dir_is_a_file_refused(self):
        f = self.th.home / "afile"
        f.write_text("x")
        cfg = config.ConfigStore.load(self.state)
        with self.assertRaises(config.ConfigError):
            config.reports_dir(cfg, str(f), create=True)


class UserName(unittest.TestCase):
    def test_default_is_the_first_word_of_the_full_name(self):
        """The feed's `me.user` and the report's name are never empty: with no name set, the same default the
        installer uses (first word of this Mac account's full name, else the account name). The app refuses a
        feed whose me.user is not a string, so an unset name must never reach it as null."""
        import pwd
        from ju import config
        h = support.temp_home()
        self.addCleanup(h.cleanup)
        with config.ConfigStore.edit(h.state):
            pass
        cfg = config.ConfigStore.load(h.state)
        pw = pwd.getpwuid(os.getuid())
        want = (pw.pw_gecos.split() or [pw.pw_name])[0]
        self.assertEqual(config.user_name(cfg), want)
        with config.ConfigStore.edit(h.state) as c:
            c.user = "Sam"
        self.assertEqual(config.user_name(config.ConfigStore.load(h.state)), "Sam")


if __name__ == "__main__":
    unittest.main()
