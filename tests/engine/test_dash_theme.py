"""D5 theme CSS: every colour token comes from themes.json; Mono's warn/hot read on its page; the
chart palette's slot 5 has its own dark shade."""
import copy
import json
import re
import unittest
from pathlib import Path

from ju import themes
from ju.dash import theme_css

ROOT = Path(__file__).resolve().parents[2]
THEMES_PATH = ROOT / "engine" / "themes.json"
DASH_CSS = ROOT / "engine" / "ju" / "dash" / "static" / "dash.css"


def lum(hexc):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


class Theme(unittest.TestCase):
    data = themes.load_themes(THEMES_PATH)

    def test_shipped_file_still_valid(self):
        self.assertEqual(self.data["order"][0], "system")
        self.assertEqual(len(self.data["order"]), 7)

    def test_tokens_from_json_only(self):
        css = theme_css.css(self.data)
        for tid in self.data["order"][1:]:
            t = self.data["themes"][tid]
            self.assertIn(':root[data-theme="%s"]' % tid, css)
            self.assertIn("--bg:%s;" % t["bg"], css)
        changed = copy.deepcopy(self.data)
        changed["themes"]["sand"]["bg"] = "#123456"
        self.assertIn("--bg:#123456;", theme_css.css(changed))
        # the hand-written stylesheet holds no colour of its own: every colour is a token
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", DASH_CSS.read_text()), [])

    def test_system_is_paper_light_and_graphite_dark(self):
        css = theme_css.css(self.data)
        paper, graphite = self.data["themes"]["paper"], self.data["themes"]["graphite"]
        root = css[css.index(":root{"):css.index("}", css.index(":root{"))]
        self.assertIn("--bg:%s;" % paper["bg"], root)
        dark = css[css.index("@media (prefers-color-scheme: dark)"):]
        dark = dark[:dark.index("}}") + 2]
        self.assertIn(":root:not([data-theme])", dark)
        self.assertIn("--bg:%s;" % graphite["bg"], dark)

    def test_system_mapping_must_name_real_themes(self):
        bad = copy.deepcopy(self.data)
        bad["themes"]["system"]["dark"] = "nope"
        with self.assertRaises(themes.ThemesInvalid) as cm:
            theme_css.css(bad)
        self.assertIn("nope", str(cm.exception))

    def test_mono_contrast(self):
        mono = self.data["themes"]["mono"]
        self.assertGreaterEqual(contrast(mono["warn"], mono["bg"]), 3.0)
        self.assertGreaterEqual(contrast(mono["hot"], mono["bg"]), 3.0)

    def test_pal_slot5_differs(self):
        self.assertEqual(len(theme_css.PAL), 8)
        light, dark = theme_css.PAL[5]
        self.assertNotEqual(light, dark)
        for pair in theme_css.PAL:
            for c in pair:
                self.assertRegex(c, r"^#[0-9a-f]{6}$")

    def test_dark_themes_get_dark_palette(self):
        css = theme_css.css(self.data)
        g = css[css.index(':root[data-theme="graphite"]'):]
        g = g[:g.index("}")]
        self.assertIn("--s5:%s;" % theme_css.PAL[5][1], g)
        p = css[css.index(':root[data-theme="paper"]'):]
        p = p[:p.index("}")]
        self.assertIn("--s5:%s;" % theme_css.PAL[5][0], p)

    def test_hidden_and_narrow_grid_rules(self):
        text = DASH_CSS.read_text()
        self.assertIn("[hidden]{display:none!important}", text)
        self.assertIn("minmax(min(100%,400px),1fr)", text)


if __name__ == "__main__":
    unittest.main()
