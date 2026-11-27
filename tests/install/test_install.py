"""I4 installer + I5 double-click wrapper (W3-D).

The installer runs in a temp HOME against PATH stubs for xcode-select, spctl, osascript, open, pgrep and
sw_vers. The "release host" is a temp folder holding a fake jusage-latest.dmg (a DMG made with the real
hdiutil around a fake jusage.app that carries the real engine). /Applications is never touched
(JUSAGE_APPS_DIR), no real app is opened, no real Keychain or Claude folder is read.
"""
import json
import os
import plistlib
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from tests.install import harness as h

INSTALL = h.ROOT / "install.sh"
COMMAND = h.ROOT / "install.command"
_DMG = {}


def tearDownModule():
    h.cleanup_all()


RECORDER = """import json, os, sys
with open(os.environ["JUSAGE_RECORD"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["config", "get"]:
    print("")
"""


def fake_dmg(version=h.VERSION, recorder=False) -> Path:
    key = (version, recorder)
    if key in _DMG and _DMG[key].exists():
        return _DMG[key]
    d = h.scratch()
    src = d / "src"
    app = src / "jusage.app" / "Contents"
    (app / "Resources").mkdir(parents=True)
    (app / "MacOS").mkdir()
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": h.APP_ID, "CFBundleExecutable": "Jusage",
                       "CFBundleShortVersionString": version, "LSMinimumSystemVersion": "13.0"}, f)
    (app / "MacOS" / "Jusage").write_text("#!/bin/sh\n")
    shutil.copytree(h.ROOT / "engine", app / "Resources" / "engine", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if recorder:   # an engine that only records its arguments: tests the installer's calls, not the engine
        (app / "Resources" / "engine" / "claude_usage.py").write_text(RECORDER)
    shutil.copy2(h.ROOT / "VERSION", app / "Resources" / "VERSION")
    host = d / "host"
    host.mkdir()
    r = subprocess.run(["hdiutil", "create", "-quiet", "-volname", "jusage", "-srcfolder", str(src), "-format", "UDZO",
                        "-ov", str(host / "jusage-latest.dmg")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    _DMG[key] = host
    return host


class Installer(unittest.TestCase):
    def setUp(self):
        self.tmp = h.scratch()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.apps = self.tmp / "Apps"
        self.apps.mkdir()
        self.calls = self.tmp / "calls.log"
        self.trashed = self.tmp / "trashed"
        h.stub_trash(self.stubs, self.trashed)
        for name in ("open", "osascript"):
            h.write_exe(self.stubs / name, f"#!/bin/bash\necho \"{name} $*\" >> '{self.calls}'\nexit 0\n")
        h.write_exe(self.stubs / "spctl", f"#!/bin/bash\necho \"spctl $*\" >> '{self.calls}'\necho 'accepted source=Notarized Developer ID'\nexit 0\n")
        h.write_exe(self.stubs / "pgrep", "#!/bin/bash\nexit 1\n")
        h.write_exe(self.stubs / "xcode-select", "#!/bin/bash\n[ \"$1\" = \"-p\" ] && { echo /Library/Developer/CommandLineTools; exit 0; }\nexit 0\n")
        h.write_exe(self.stubs / "sw_vers", "#!/bin/bash\n[ \"$1\" = \"-productVersion\" ] && { echo 14.5; exit 0; }\nexec /usr/bin/sw_vers \"$@\"\n")

    def env(self, **extra):
        base = {"JUSAGE_APPS_DIR": str(self.apps), "JUSAGE_TRASH": str(self.stubs / "trash"),
                "JUSAGE_DOWNLOAD_BASE": "file://" + str(fake_dmg())}
        base.update(extra)
        return h.clean_env(self.home, self.stubs, extra=base)

    def install(self, *args, timeout=120, **extra):
        return h.run(["bash", INSTALL, *args], env=self.env(**extra), cwd=self.tmp, timeout=timeout)

    def engine(self, *args):
        app = self.apps / "jusage.app" / "Contents" / "Resources" / "engine" / "claude_usage.py"
        rc, out = h.run(["python3", "-E", "-s", app, *args], env=h.clean_env(self.home))
        return out.strip()

    @property
    def log(self) -> Path:
        return self.home / ".claude-usage" / "logs" / "install.log"

    def nothing_installed(self):
        self.assertEqual(list(self.apps.iterdir()), [])
        self.assertFalse((self.home / "Applications").exists())
        for rc in (".zshrc", ".bashrc"):
            p = self.home / rc
            self.assertFalse(p.exists() and "alias jusage=" in p.read_text(), rc)


class TestOptions(Installer):
    def test_option_without_value_exits(self):
        for opt in ("--reports", "--user", "--from-source"):
            rc, out = self.install(opt, timeout=20)
            self.assertEqual(rc, 1, (opt, out))
            self.assertIn(opt, out)
            self.assertRegex(out, r"needs a")
        rc, out = self.install("--reports", "--projects", timeout=20)
        self.assertEqual(rc, 1, out)
        self.assertFalse((self.home / ".claude-usage").exists(), "a usage error creates nothing")

    def test_help_is_a_real_usage(self):
        rc, out = self.install("-h")
        self.assertEqual(rc, 0)
        for opt in ("--reports", "--user", "--projects", "--from-source", "--no-launch", "--help"):
            self.assertIn(opt, out)

    def test_unknown_option(self):
        rc, out = self.install("--dir", "x")
        self.assertEqual(rc, 1)
        self.assertIn("--dir", out)

    def test_home_dotdot_refused(self):
        rc, out = self.install("--from-source", str(self.home) + "/../x", "--no-launch")
        self.assertEqual(rc, 1, out)
        self.assertIn("inside your home", out)
        self.assertFalse((self.tmp / "x").exists())
        self.nothing_installed()

    def test_symlink_out_of_home_refused(self):
        (self.home / "escape").symlink_to(self.tmp)
        rc, out = self.install("--from-source", str(self.home / "escape" / "y"))
        self.assertEqual(rc, 1, out)
        self.assertIn("inside your home", out)
        self.assertFalse((self.tmp / "y").exists())

    def test_non_empty_non_jusage_folder_refused(self):
        d = self.home / "stuff"
        d.mkdir()
        (d / "precious.txt").write_text("keep")
        rc, out = self.install("--from-source", str(d), timeout=30)
        self.assertEqual(rc, 1, out)
        self.assertIn("not a jusage install", out)
        self.assertEqual((d / "precious.txt").read_text(), "keep")


class TestPrerequisites(Installer):
    def test_clt_wait_times_out(self):
        h.write_exe(self.stubs / "xcode-select", f"#!/bin/bash\necho \"xcode-select $*\" >> '{self.calls}'\n[ \"$1\" = \"-p\" ] && exit 2\nexit 0\n")
        rc, out = self.install(JUSAGE_CLT_TIMEOUT="2", JUSAGE_CLT_POLL="1", timeout=60)
        self.assertEqual(rc, 1, out)
        self.assertIn("Command Line Tools", out)
        self.assertIn("install.log", out)
        self.assertIn("xcode-select --install", self.calls.read_text())
        self.nothing_installed()

    def test_old_macos_refused_before_any_download(self):
        h.write_exe(self.stubs / "sw_vers", "#!/bin/bash\necho 12.7.6\n")
        rc, out = self.install(JUSAGE_DOWNLOAD_BASE="file:///nonexistent")
        self.assertEqual(rc, 1)
        self.assertIn("macOS 13", out)
        self.assertIn("--from-source", out)
        self.nothing_installed()

    def test_python_too_old_refused(self):
        h.write_exe(self.stubs / "python3", "#!/bin/bash\nexit 1\n")
        rc, out = self.install()
        self.assertEqual(rc, 1)
        self.assertIn("Python 3.9", out)
        self.nothing_installed()


class TestDownloadInstall(Installer):
    def test_installs_app_alias_scan_and_opens(self):
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        app = self.apps / "jusage.app"
        self.assertTrue((app / "Contents" / "Resources" / "engine" / "claude_usage.py").is_file())
        calls = self.calls.read_text()
        self.assertIn("spctl", calls)
        self.assertRegex(calls, r"open -a .*jusage\.app --args --set-python /")
        zshrc = (self.home / ".zshrc").read_text()
        self.assertEqual(zshrc.count("alias jusage="), 1)
        self.assertIn("Contents/Resources/engine/bin/jusage", zshrc)
        self.assertTrue((self.home / ".claude-usage" / "usage.db").exists(), "first scan ran")
        first = subprocess.run(["id", "-F"], capture_output=True, text=True).stdout.split()[0]
        self.assertEqual(self.engine("config", "get", "user"), first)
        self.assertTrue(self.log.is_file())
        self.assertIn("installed", self.log.read_text())
        self.assertEqual(oct(self.log.parent.parent.stat().st_mode & 0o777), "0o700")

    def test_no_launch_still_records_python(self):
        rc, out = self.install("--no-launch")
        self.assertEqual(rc, 0, out)
        self.assertNotRegex(self.calls.read_text(), r"(?m)^open ")
        self.assertTrue(os.path.isabs(self.engine("config", "get", "python")))

    def test_projects_alone_remembered(self):
        rc, out = self.install("--projects", "--no-launch")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.engine("config", "get", "share_projects"), "true")

    def test_reports_and_user_shared(self):
        # The recording engine keeps this about the installer's calls (the engine has its own tests).
        rec = self.tmp / "record.jsonl"
        reports = self.home / "sync" / "reports"
        rc, out = self.install("--reports", str(reports), "--user", "Mary Jane", "--projects", "--no-launch",
                               JUSAGE_DOWNLOAD_BASE="file://" + str(fake_dmg(recorder=True)), JUSAGE_RECORD=str(rec))
        self.assertEqual(rc, 0, out)
        calls = [json.loads(l) for l in rec.read_text().splitlines()]
        self.assertIn(["share", "--reports", str(reports), "--user", "Mary Jane", "--projects"], calls)
        self.assertLess(calls.index(["scan"]), calls.index(["share", "--reports", str(reports), "--user", "Mary Jane", "--projects"]))
        self.assertIn(["dashboard"], calls)

    def test_default_name_is_first_word_of_full_name(self):
        rec = self.tmp / "record.jsonl"
        rc, out = self.install("--no-launch", JUSAGE_DOWNLOAD_BASE="file://" + str(fake_dmg(recorder=True)), JUSAGE_RECORD=str(rec))
        self.assertEqual(rc, 0, out)
        first = subprocess.run(["id", "-F"], capture_output=True, text=True).stdout.split()[0]
        self.assertIn(["set-user", first], [json.loads(l) for l in rec.read_text().splitlines()])

    def test_upgrade_quits_app_and_trashes_old(self):
        self.assertEqual(self.install("--no-launch")[0], 0)
        (self.apps / "jusage.app" / "marker").write_text("old")
        counter = self.tmp / "pgrep.n"
        h.write_exe(self.stubs / "pgrep", f"#!/bin/bash\nn=$(cat '{counter}' 2>/dev/null || echo 0)\necho $((n+1)) > '{counter}'\n[ \"$n\" -eq 0 ] && exit 0\nexit 1\n")
        rc, out = self.install("--no-launch")
        self.assertEqual(rc, 0, out)
        self.assertIn("quit", self.calls.read_text())
        self.assertFalse((self.apps / "jusage.app" / "marker").exists())
        self.assertTrue(any((p / "marker").exists() for p in self.trashed.iterdir()))
        self.assertEqual([p.name for p in self.apps.iterdir()], ["jusage.app"], "no hidden leftovers")
        self.assertEqual((self.home / ".zshrc").read_text().count("alias jusage="), 1)

    def test_apps_dir_not_writable_goes_to_home_applications(self):
        self.apps.chmod(0o555)
        try:
            rc, out = self.install("--no-launch")
        finally:
            self.apps.chmod(0o755)
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.home / "Applications" / "jusage.app").is_dir())
        self.assertIn("~/Applications", out)

    def test_download_failure_leaves_nothing(self):
        rc, out = self.install(JUSAGE_DOWNLOAD_BASE="file://" + str(self.tmp / "nowhere"))
        self.assertEqual(rc, 1)
        self.assertIn("install.log", out)
        self.assertEqual(len([l for l in out.splitlines() if l.strip()][-1:]), 1)
        self.nothing_installed()
        self.assertTrue(self.log.is_file())

    def test_gatekeeper_rejection_leaves_nothing(self):
        h.write_exe(self.stubs / "spctl", "#!/bin/bash\necho rejected >&2\nexit 3\n")
        rc, out = self.install()
        self.assertEqual(rc, 1)
        self.assertIn("install.log", out)
        self.nothing_installed()

    def test_wrong_bundle_id_refused(self):
        host = fake_dmg()
        d = h.scratch()
        # a DMG whose app has another bundle id
        src = d / "src"
        shutil.copytree(self._mount_copy(host), src)
        with open(src / "jusage.app" / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleIdentifier": "com.example.other", "CFBundleShortVersionString": "1.0.0", "LSMinimumSystemVersion": "13.0"}, f)
        (d / "host").mkdir()
        subprocess.run(["hdiutil", "create", "-quiet", "-volname", "jusage", "-srcfolder", str(src), "-format", "UDZO", str(d / "host" / "jusage-latest.dmg")], check=True)
        rc, out = self.install(JUSAGE_DOWNLOAD_BASE="file://" + str(d / "host"))
        self.assertEqual(rc, 1)
        self.assertIn("bundle id", out)
        self.nothing_installed()

    def _mount_copy(self, host):
        mnt = h.scratch() / "mnt"
        mnt.mkdir()
        subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-readonly", "-mountpoint", str(mnt), str(host / "jusage-latest.dmg")], check=True)
        dst = h.scratch() / "copy"
        shutil.copytree(mnt, dst, symlinks=True, ignore=shutil.ignore_patterns(".fseventsd", ".Trashes", ".DS_Store", ".Spotlight-V100"))
        subprocess.run(["hdiutil", "detach", "-quiet", str(mnt)], check=True)
        return dst


class TestFromSource(Installer):
    def setUp(self):
        super().setUp()
        self.url = "file://" + str(h.ROOT)

    def test_alias_with_space_and_pipe_in_path(self):
        (self.home / ".bashrc").write_text("# mine\n")
        target = self.home / "my jusage|x"
        for _ in range(2):   # the second run must replace the alias line, not add one
            rc, out = self.install("--from-source", str(target), "--no-launch", JUSAGE_REPO_URL=self.url)
            self.assertEqual(rc, 0, out)
        for rc_file in (".zshrc", ".bashrc"):
            text = (self.home / rc_file).read_text()
            self.assertEqual(text.count("alias jusage="), 1, rc_file)
        self.assertIn("# mine", (self.home / ".bashrc").read_text())
        r = subprocess.run(["bash", "-c", f"shopt -s expand_aliases\n. '{self.home}/.zshrc'\njusage --version"],
                           env=h.clean_env(self.home), capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), h.VERSION, r.stderr)
        self.assertFalse((self.apps / "jusage.app").exists(), "a source install builds no app")

    def test_apostrophe_in_path(self):
        target = self.home / "it's here"
        rc, out = self.install("--from-source", str(target), "--no-launch", JUSAGE_REPO_URL=self.url)
        self.assertEqual(rc, 0, out)
        r = subprocess.run(["bash", "-c", f"shopt -s expand_aliases\n. '{self.home}/.zshrc'\njusage --version"],
                           env=h.clean_env(self.home), capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), h.VERSION, r.stderr)

    def test_existing_install_folder_is_updated_not_refused(self):
        target = self.home / "src"
        self.assertEqual(self.install("--from-source", str(target), "--no-launch", JUSAGE_REPO_URL=self.url)[0], 0)
        rc, out = self.install("--from-source", str(target), "--no-launch", JUSAGE_REPO_URL=self.url)
        self.assertEqual(rc, 0, out)
        self.assertIn("updated", out)


class TestDoubleClick(unittest.TestCase):
    def test_command_passes_args(self):
        tmp = h.scratch()
        folder = tmp / "a folder"
        folder.mkdir()
        shutil.copy2(COMMAND, folder / "install.command")
        h.write_exe(folder / "install.sh", f"#!/bin/bash\nprintf '%s\\n' \"$PWD\" \"$@\" > '{tmp}/seen'\n")
        other = tmp / "elsewhere"
        other.mkdir()
        rc, out = h.run(["bash", folder / "install.command", "--user", "Sam Q", "--projects"], cwd=other)
        self.assertEqual(rc, 0, out)
        seen = (tmp / "seen").read_text().splitlines()
        self.assertEqual(seen[1:], ["--user", "Sam Q", "--projects"])

    def test_command_seeds_nothing_and_needs_its_sibling(self):
        tmp = h.scratch()
        shutil.copy2(COMMAND, tmp / "install.command")
        home = tmp / "home"
        home.mkdir()
        rc, out = h.run(["bash", tmp / "install.command"], env=h.clean_env(home), cwd=tmp)
        self.assertEqual(rc, 1)
        self.assertIn("install.sh", out)
        self.assertEqual(list(home.iterdir()), [] if not (home / "tmp").exists() else [home / "tmp"])


if __name__ == "__main__":
    unittest.main()
