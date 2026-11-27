"""E25 engine bring-up: every refusal is one line naming the file, exit-2 material."""
import json
import os
import shutil
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from ju import bringup, constants
from ju.contracts import BringUpRefused
from tests.engine import support


class BringUpTests(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.state = self.th.state
        # a private copy of the shipped layout: <root>/VERSION and <root>/engine/{pricing,themes}.json
        self.root = self.th.home / "bundle"
        (self.root / "engine").mkdir(parents=True)
        (self.root / "VERSION").write_text("0.2.0\n")
        for n in ("pricing.json", "themes.json"):
            shutil.copy(constants.HERE.parent / n, self.root / "engine" / n)
        self.pricing_calls = []
        self.probe_calls = []
        self.probe_result = 3

    def deps(self):
        def pricing_load(path):
            self.pricing_calls.append(path)
            json.loads(Path(path).read_text())          # a real parse, so a broken file fails

        def probe(path):
            self.probe_calls.append(path)
            return self.probe_result
        return dict(pricing_load=pricing_load, probe_version=probe)

    def check(self, **over):
        kw = dict(root=self.root, engine_dir=self.root / "engine")
        kw.update(self.deps())
        kw.update(over)
        return bringup.check(self.state, **kw)

    def refused(self, **over):
        with self.assertRaises(BringUpRefused) as cm:
            self.check(**over)
        line = cm.exception.line
        self.assertTrue(line.startswith("jusage: "), line)
        self.assertNotIn("\n", line)
        return line

    def test_clean_install_passes(self):
        self.assertIsNone(self.check())
        self.assertEqual(self.pricing_calls, [self.root / "engine" / "pricing.json"])

    def test_each_refusal_line(self):
        with self.subTest("VERSION missing"):
            (self.root / "VERSION").unlink()
            self.assertEqual(self.refused(), "jusage: VERSION: file is missing")
            (self.root / "VERSION").write_text("0.2.0")
        for bad in ("0.2", "v0.2.0", "0.2.0-beta", "", "1.2.3.4", "0.2.x"):
            with self.subTest("VERSION bad", bad=bad):
                (self.root / "VERSION").write_text(bad + "\n")
                self.assertEqual(self.refused(), f"jusage: VERSION: not major.minor.patch: {bad!r}")
        (self.root / "VERSION").write_text("10.20.30\n")
        self.check()                                               # multi-digit is fine

        with self.subTest("pricing.json invalid"):
            (self.root / "engine" / "pricing.json").write_text("{nope")
            line = self.refused()
            self.assertTrue(line.startswith("jusage: pricing.json: "), line)
            shutil.copy(constants.HERE.parent / "pricing.json", self.root / "engine" / "pricing.json")
        with self.subTest("pricing.json missing"):
            (self.root / "engine" / "pricing.json").unlink()
            self.assertEqual(self.refused(), "jusage: pricing.json: file is missing")
            shutil.copy(constants.HERE.parent / "pricing.json", self.root / "engine" / "pricing.json")

        with self.subTest("themes.json invalid"):
            d = json.loads((self.root / "engine" / "themes.json").read_text())
            del d["themes"]["mono"]["warn"]
            (self.root / "engine" / "themes.json").write_text(json.dumps(d))
            self.assertEqual(self.refused(), "jusage: themes.json: theme 'mono' lacks 'warn'")
            shutil.copy(constants.HERE.parent / "themes.json", self.root / "engine" / "themes.json")

        with self.subTest("state dir not 0700"):
            os.chmod(self.state, 0o755)
            self.assertEqual(self.refused(), f"jusage: {self.state}: permissions are 0755, must be 0700")
            os.chmod(self.state, 0o700)

        with self.subTest("state path is a file"):
            f = self.th.home / "afile"
            f.write_text("x")
            with self.assertRaises(BringUpRefused) as cm:
                bringup.check(f, root=self.root, engine_dir=self.root / "engine", **self.deps())
            self.assertEqual(cm.exception.line, f"jusage: {f}: not a folder")

        with self.subTest("usage.db newer"):
            (self.state / "usage.db").write_bytes(b"")
            self.probe_result = 4
            self.assertEqual(self.refused(),
                             "jusage: usage.db: schema 4 is newer than this jusage reads (3); update jusage")
            self.probe_result = 3

        with self.subTest("live.json schema unknown"):
            for schema in (9, 3, 1, 0, "2", None, 2.5):
                (self.state / "live.json").write_text(json.dumps({"schema": schema, "accounts": {}}))
                self.assertEqual(self.refused(),
                                 f"jusage: live.json: schema {schema!r} is not known to this jusage (reads 2 and the old unversioned shapes); update jusage")

    def test_absent_files_pass(self):
        # a fresh Mac: no usage.db, no live.json. The db probe must not even be called.
        self.check()
        self.assertEqual(self.probe_calls, [])

    def test_missing_state_dir_passes(self):
        shutil.rmtree(self.state)
        self.assertIsNone(self.check())

    def test_live_json_accepted_shapes(self):
        live = self.state / "live.json"
        for body in ({"schema": 2, "accounts": {}},
                     {"/a/.claude": {"data": {"x": 1}, "fetched": "2026-01-01T00:00:00+00:00"}},    # unversioned 0.1.19+
                     {"/a/.claude": {"data": {"error": "x"}}},                                     # pre-0.1.19
                     {}):
            with self.subTest(body=body):
                live.write_text(json.dumps(body))
                self.check()

    def test_corrupt_live_json_is_not_a_refusal(self):
        # R-33: E13 moves a corrupt live.json aside and starts empty; bring-up must not block that
        for body in (b"{nope", b"", b"[1]"):
            with self.subTest(body=body):
                (self.state / "live.json").write_bytes(body)
                self.check()

    def test_db_probe_error_refuses_naming_the_file(self):
        (self.state / "usage.db").write_bytes(b"x")
        def boom(path):
            raise sqlite3.DatabaseError("file is not a database")
        line = self.refused(probe_version=boom)
        self.assertEqual(line, "jusage: usage.db: not a readable database (file is not a database)")

    def test_db_probe_receives_the_state_db_path(self):
        (self.state / "usage.db").write_bytes(b"")
        self.check()
        self.assertEqual(self.probe_calls, [self.state / "usage.db"])

    def test_default_wiring_uses_pricing_and_ledger_db_modules(self):
        # no injected deps: bring-up imports E6/E7 itself. Stand-in modules prove the wiring.
        seen = {}
        pricing = types.ModuleType("ju.pricing")
        class PricingInvalid(Exception):
            pass
        pricing.PricingInvalid = PricingInvalid
        def load(path):
            seen["pricing"] = Path(path)
            raise PricingInvalid("rate for 'claude-x' has no output price")
        pricing.load = load
        db = types.ModuleType("ju.ledger_db")
        db.probe_version = lambda path: seen.setdefault("db", Path(path)) and 3
        (self.state / "usage.db").write_bytes(b"")
        with mock.patch.dict(sys.modules, {"ju.pricing": pricing, "ju.ledger_db": db}):
            with self.assertRaises(BringUpRefused) as cm:
                bringup.check(self.state, root=self.root, engine_dir=self.root / "engine")
        self.assertEqual(cm.exception.line, "jusage: pricing.json: rate for 'claude-x' has no output price")
        self.assertEqual(seen["pricing"], self.root / "engine" / "pricing.json")
        # and the db half: pricing passes, the stand-in probe reports schema 4
        pricing.load = lambda path: None
        db.probe_version = lambda path: seen.setdefault("db", Path(path)) and 4
        with mock.patch.dict(sys.modules, {"ju.pricing": pricing, "ju.ledger_db": db}):
            with self.assertRaises(BringUpRefused) as cm:
                bringup.check(self.state, root=self.root, engine_dir=self.root / "engine")
        self.assertIn("usage.db: schema 4", cm.exception.line)
        self.assertEqual(seen["db"], self.state / "usage.db")

    def test_defaults_point_at_the_shipped_files(self):
        # no root/engine_dir given: the real shipped VERSION, pricing.json, themes.json are checked
        bringup.check(self.state, **self.deps())
        self.assertEqual(self.pricing_calls, [constants.HERE.parent / "pricing.json"])


if __name__ == "__main__":
    unittest.main()
