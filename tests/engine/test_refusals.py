"""Wave 4 T2: rigid bring-up refusals (SCORE §3, phase 6), engine side.

Each layer is broken on purpose in a temp copy of the shipped engine, and the real process must
REFUSE loudly: exit 2, exactly one stderr line `jusage: <file>: <problem>`, the refusal logged to
engine.log through houselog, and nothing else done. The checks go through `bringup.check`'s default
path with the REAL `ju.pricing.load` / `PricingInvalid` and `ju.ledger_db.probe_version` (RULING 13),
and the shipped pricing.json passes it (RULING 18).

Layers here: engine bring-up (VERSION), pricing.json, themes.json, usage.db schema, live.json schema.
The app's layers (feed schema, bundle VERSION, engine present, themes) are BringUpRefusalTests.swift.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.engine import support

ROOT = Path(__file__).resolve().parents[2]
LINE = re.compile(r"^jusage: [^:]+: .+$")


class Refusals(unittest.TestCase):
    def setUp(self):
        saved = {k: os.environ.pop(k) for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR") if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        self.h = support.temp_home()
        self.addCleanup(self.h.cleanup)
        self.repo = Path(tempfile.mkdtemp(prefix="jusage-refusal-")).resolve()
        self.addCleanup(shutil.rmtree, self.repo, True)
        shutil.copytree(ROOT / "engine", self.repo / "engine", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(ROOT / "VERSION", self.repo / "VERSION")

    def run_engine(self, *args):
        with support.fake_security({}) as sec:
            env = {"HOME": str(self.h.home), "LANG": "en_US.UTF-8", "PATH": sec.path()}
            r = subprocess.run([sys.executable, "-B", "-E", "-s", str(self.repo / "engine" / "claude_usage.py"), *args],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(sec.calls(), [], "a refused bring-up reads no Keychain")
        return r

    def assert_refused(self, file, problem):
        r = self.run_engine("menu-json")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "", "a refusal prints no feed")
        lines = r.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertRegex(lines[0], LINE)
        self.assertTrue(lines[0].startswith(f"jusage: {file}: "), lines[0])
        self.assertIn(problem, lines[0])
        log = (self.h.state / "logs" / "engine.log").read_text()
        self.assertIn("bring-up refused", log)
        self.assertIn(lines[0], log.replace('\\"', '"'))
        self.assertFalse((self.h.state / "usage.db").exists() and file != "usage.db", "nothing was created")
        return lines[0]

    # ---------------------------------------------------------------- the shipped files pass

    def test_shipped_files_pass_through_the_default_path(self):
        from ju import bringup
        bringup.check(self.h.state, root=self.repo, engine_dir=self.repo / "engine")    # real pricing.load, probe
        r = self.run_engine("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_engine("config", "get", "read_every_s")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ---------------------------------------------------------------- one test per layer

    def test_missing_version(self):
        (self.repo / "VERSION").unlink()
        self.assert_refused("VERSION", "file is missing")

    def test_bad_version(self):
        (self.repo / "VERSION").write_text("0.2\n")
        self.assert_refused("VERSION", "not major.minor.patch")

    def test_bad_pricing_real_loader(self):
        p = self.repo / "engine" / "pricing.json"
        data = json.loads(p.read_text())
        del data["_multipliers"]
        p.write_text(json.dumps(data))
        self.assert_refused("pricing.json", "_multipliers")

    def test_negative_price_real_loader(self):
        p = self.repo / "engine" / "pricing.json"
        data = json.loads(p.read_text())
        model = next(k for k in data if not k.startswith("_"))
        data[model]["output"] = -1
        p.write_text(json.dumps(data))
        self.assert_refused("pricing.json", model)

    def test_bad_themes(self):
        p = self.repo / "engine" / "themes.json"
        p.write_text('{"themes": "not a list"')
        line = self.assert_refused("themes.json", "")
        self.assertIn("themes.json", line)

    def test_db_schema_4_real_probe(self):
        c = sqlite3.connect(self.h.state / "usage.db")
        c.execute("PRAGMA user_version=4")
        c.execute("CREATE TABLE t (x)")
        c.commit()
        c.close()
        before = (self.h.state / "usage.db").read_bytes()
        self.assert_refused("usage.db", "schema 4 is newer")
        self.assertEqual((self.h.state / "usage.db").read_bytes(), before, "the newer database is untouched")

    def test_db_not_a_database_real_probe(self):
        (self.h.state / "usage.db").write_bytes(b"this is not sqlite" * 100)
        self.assert_refused("usage.db", "not a readable database")

    def test_live_schema_9(self):
        (self.h.state / "live.json").write_text(json.dumps({"schema": 9, "accounts": {}}))
        self.assert_refused("live.json", "schema 9")
        self.assertEqual(json.loads((self.h.state / "live.json").read_text())["schema"], 9, "never rewritten")


if __name__ == "__main__":
    unittest.main()
