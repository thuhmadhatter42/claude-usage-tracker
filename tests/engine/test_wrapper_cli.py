"""E26 `engine/bin/jusage`: help is a real usage() function (R-140), bare `jusage` shares only
when a shared folder is set, the recorded Python is read from config.json as JSON (R-141), and
the script calls plain system commands, never aliases (R-142).

The recorded Python is a stub here that logs its argv, so no engine command really runs."""
import json
import os
import re
import stat
import subprocess
import unittest
from pathlib import Path

from tests.engine import support

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "engine" / "bin" / "jusage"

STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "{log}"
if [ "$5 $6" = "config get" ]; then printf '%s\\n' "${{STUB_REPORTS:-}}"; fi
exit 0
"""


class Wrapper(unittest.TestCase):
    def setUp(self):
        self.h = support.temp_home()
        self.addCleanup(self.h.cleanup)
        self.log = self.h.home / "calls.log"
        self.py = self.h.home / "bin dir" / "python3"          # a space in the path on purpose
        self.py.parent.mkdir()
        self.py.write_text(STUB.format(log=self.log))
        self.py.chmod(self.py.stat().st_mode | stat.S_IXUSR)

    def record_python(self, extra=None):
        cfg = {"note": "\"python\": \"/nowhere\" is not the key", "reports_dir_hint": "x", "python": str(self.py)}
        cfg.update(extra or {})
        (self.h.state / "config.json").write_text(json.dumps(cfg, indent=3))

    def run_w(self, *args, reports=""):
        env = {"HOME": str(self.h.home), "PATH": "/usr/bin:/bin", "STUB_REPORTS": reports}
        return subprocess.run(["/bin/bash", str(WRAPPER), *args], env=env, capture_output=True, text=True, timeout=30)

    def calls(self):
        if not self.log.exists():
            return []
        cu = str(ROOT / "engine" / "claude_usage.py")
        out = []
        for line in self.log.read_text().splitlines():
            self.assertTrue(line.startswith(f"-B -E -s {cu}"), line)          # RULINGS 51: -B, no .pyc
            out.append(line[len(f"-B -E -s {cu}"):].strip())
        return out

    def test_help_is_usage_function(self):
        text = WRAPPER.read_text()
        self.assertRegex(text, r"(?m)^usage\(\) \{")
        self.assertNotRegex(text, r"sed -n|\$0\"? *\| *head|head -n")      # no printing of its own lines
        for flag in ("help", "-h", "--help"):
            r = self.run_w(flag)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("jusage app", r.stdout)
            self.assertIn("jusage upgrade", r.stdout)
            self.assertIn("jusage uninstall", r.stdout)
        self.assertEqual(self.calls(), [])                                 # no Python recorded: usage alone
        self.record_python()
        r = self.run_w("help")
        self.assertEqual(self.calls(), ["--help"])

    def test_bare_shares_only_when_reports_dir(self):
        self.record_python()
        r = self.run_w()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["scan", "live", "report --since 7d", "report --by blocks --last 6",
                                        "config get reports_dir"])
        self.assertIn("--reports", r.stdout)
        self.log.unlink()
        r = self.run_w(reports="/shared/folder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[-2:], ["share", "dashboard --open"])

    def test_reads_config_as_json(self):
        self.record_python()
        r = self.run_w("report", "--by", "model")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["report --by model"])

    def test_no_recorded_python_says_open_jusage(self):
        r = self.run_w("scan")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "Open jusage once so it records Python.")
        (self.h.state / "config.json").write_text(json.dumps({"python": None}))
        r = self.run_w("scan")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "Open jusage once so it records Python.")

    def test_recorded_python_gone_refuses(self):
        self.record_python({"python": str(self.h.home / "gone" / "python3")})
        r = self.run_w("scan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("gone", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_engine_failure_exit_code_passes_through(self):
        self.py.write_text("#!/bin/bash\necho 'jusage: boom' >&2\nexit 2\n")
        self.record_python()
        r = self.run_w("menu-json")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.strip(), "jusage: boom")

    def test_symlinked_wrapper_finds_its_engine(self):
        self.record_python()
        link = self.h.home / "jusage"
        os.symlink(WRAPPER, link)
        env = {"HOME": str(self.h.home), "PATH": "/usr/bin:/bin"}
        r = subprocess.run(["/bin/bash", str(link), "scan"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["scan"])

    def test_no_aliases_used(self):
        bare = re.compile(r"(^|[\s;|&(`])(ls|grep|sed|awk|cat|find|head|tail|open|git|readlink|dirname|plutil)\s")
        in_text = False
        for n, line in enumerate(WRAPPER.read_text().splitlines(), 1):
            if in_text:                                   # the usage() heredoc is words, not commands
                in_text = line.strip() != "EOF"
                continue
            in_text = "<<'EOF'" in line
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#!") else ""
            self.assertIsNone(bare.search(code), f"line {n} calls a bare command: {line}")

    def test_app_upgrade_uninstall_targets(self):
        text = WRAPPER.read_text()
        self.assertIn('BUNDLE_ID="com.goatdguild.jusage"', text)
        self.assertIn('/usr/bin/open -b "$BUNDLE_ID"', text)
        self.assertIn("--check-for-updates", text)
        self.assertIn("pull --ff-only", text)
        self.assertIn("statusline off --all", text)            # originals restored before anything moves
        self.assertIn("uninstall.sh", text)
        self.assertEqual(subprocess.run(["/bin/bash", "-n", str(WRAPPER)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
