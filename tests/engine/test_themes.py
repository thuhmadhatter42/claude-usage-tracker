"""E27 themes.json validation: a bad file is named, down to the theme and key."""
import copy
import json
import unittest
from pathlib import Path

from ju import constants, themes
from tests.engine import support


class ThemesTests(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.good = json.loads((constants.HERE.parent / "themes.json").read_text())

    def write(self, data):
        p = self.th.home / "themes.json"
        p.write_text(data if isinstance(data, str) else json.dumps(data))
        return p

    def refused(self, data):
        with self.assertRaises(themes.ThemesInvalid) as cm:
            themes.load_themes(self.write(data))
        return str(cm.exception)

    def test_shipped_file_valid(self):
        d = themes.load_themes(constants.HERE.parent / "themes.json")
        self.assertEqual(d["order"][0], "system")
        self.assertEqual(set(d["order"]), set(d["themes"]))

    def test_missing_key_named(self):
        d = copy.deepcopy(self.good)
        del d["themes"]["mono"]["warn"]
        self.assertEqual(self.refused(d), "themes.json: theme 'mono' lacks 'warn'")

    def test_every_required_key_is_checked(self):
        for key in themes.REQUIRED_KEYS:
            with self.subTest(key):
                d = copy.deepcopy(self.good)
                del d["themes"]["paper"][key]
                self.assertEqual(self.refused(d), f"themes.json: theme 'paper' lacks '{key}'")

    def test_order_id_missing_named(self):
        d = copy.deepcopy(self.good)
        d["order"].append("neon")
        self.assertEqual(self.refused(d), "themes.json: order lists 'neon' but there is no such theme")

    def test_bad_hex_named(self):
        for bad in ("#12345", "123456", "#GGGGGG", "red", 7, None, "#1234567"):
            with self.subTest(bad=bad):
                d = copy.deepcopy(self.good)
                d["themes"]["graphite"]["ink"] = bad
                self.assertEqual(self.refused(d), f"themes.json: theme 'graphite' key 'ink' is not #RRGGBB: {bad!r}")

    def test_optional_colours_are_checked_when_present(self):
        d = copy.deepcopy(self.good)
        d["themes"]["paper"]["grid"] = "nope"
        self.assertIn("theme 'paper' key 'grid' is not #RRGGBB", self.refused(d))

    def test_bad_scheme_named(self):
        d = copy.deepcopy(self.good)
        d["themes"]["paper"]["scheme"] = "sepia"
        self.assertEqual(self.refused(d), "themes.json: theme 'paper' scheme must be 'light' or 'dark', not 'sepia'")

    def test_system_must_exist(self):
        d = copy.deepcopy(self.good)
        del d["themes"]["system"]
        d["order"].remove("system")
        self.assertEqual(self.refused(d), "themes.json: theme 'system' is missing")

    def test_structure_problems(self):
        self.assertTrue(self.refused("{nope").startswith("themes.json: is not valid JSON ("))
        self.assertEqual(self.refused([1]), "themes.json: top level must be an object")
        self.assertEqual(self.refused({"themes": {}}), "themes.json: 'order' must be a non-empty list of theme ids")
        self.assertEqual(self.refused({"order": ["system"]}), "themes.json: 'themes' must be an object")
        self.assertEqual(self.refused({"order": ["system"], "themes": {"system": 5}}), "themes.json: theme 'system' must be an object")

    def test_missing_file_named(self):
        with self.assertRaises(themes.ThemesInvalid) as cm:
            themes.load_themes(self.th.home / "absent" / "themes.json")
        self.assertEqual(str(cm.exception), "themes.json: file is missing")


if __name__ == "__main__":
    unittest.main()
