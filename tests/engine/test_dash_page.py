"""D1 page: one self-contained file; data block JSON with `<` escaped; named placeholders only;
the saved theme is applied in <head> so the first paint has the right colours."""
import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

from ju import formats, timeutil
from ju.dash import page
from tests.engine.test_dash_fixtures import NOW, RULES, at, day, report

EVIL = "</script><script>alert(1)</script>"


def data_block(html):
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


class Page(unittest.TestCase):
    def render(self, reps=None, rules=RULES, notes=()):
        return page.render([report()] if reps is None else reps, list(notes), "0.2.0", NOW, rules)

    def test_script_breakout_escaped(self):
        projects = {EVIL: {"tokens": 500, "usd": 0.1}}
        h = self.render([report(days={NOW.date().isoformat(): day(projects=projects)})])
        self.assertNotIn(EVIL, h)
        self.assertNotIn("<script>alert", h)
        tips = data_block(h)["tips"]["proj"]
        self.assertIn([EVIL, EVIL], tips["series"])

    def test_strict_content_security_policy(self):
        """Review-security 1c: the page needs no network, so nothing may load or send; only the
        page's own inline scripts run (by hash), so an injected handler or script does nothing."""
        import base64, hashlib
        h = self.render()
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', h)
        self.assertIsNotNone(m, "no CSP meta")
        self.assertLess(m.start(), h.index("<script"), "the CSP must come before the first script")
        csp = dict((d.strip().split(" ", 1) + [""])[:2] for d in m.group(1).split(";") if d.strip())
        self.assertEqual(csp["default-src"], "'none'")
        self.assertNotIn("unsafe-inline", csp["script-src"])
        self.assertEqual(csp["base-uri"], "'none'")
        self.assertEqual(csp["form-action"], "'none'")
        scripts = re.findall(r"<script>(.*?)</script>", h, re.S)
        self.assertEqual(len(scripts), 3)
        want = {"'sha256-%s'" % base64.b64encode(hashlib.sha256(x.encode("utf-8")).digest()).decode() for x in scripts}
        self.assertEqual(set(csp["script-src"].split()), want)

    def test_no_percent_formatting(self):
        name = "100% {{css}} %s %%"
        h = self.render([report(days={NOW.date().isoformat(): day(projects={name: {"tokens": 5, "usd": 0}})})])
        self.assertIn("100% {{css}} %s %%", h)
        with self.assertRaises(KeyError):
            page.fill("<p>{{a}}{{b}}</p>", {"a": "x"})
        with self.assertRaises(KeyError):
            page.fill("<p>{{a}}</p>", {"a": "x", "c": "y"})
        self.assertEqual(page.fill("{{a}}|{{b}}", {"a": "{{b}}", "b": "%s"}), "{{b}}|%s")

    def test_template_placeholders_are_the_closed_set(self):
        found = set(re.findall(r"\{\{(\w+)\}\}", page.template_text()))
        self.assertEqual(found, set(page.PLACEHOLDERS))

    def test_theme_applied_in_head(self):
        h = self.render()
        head = h[:h.index("</head>")]
        first_style = head.index("<style>")
        script = head.index("<script>")
        self.assertLess(script, first_style)                      # theme set before any colour is drawn
        self.assertIn("localStorage.getItem('jusage.theme')", head[script:first_style])
        self.assertIn("try{", head[script:first_style])
        self.assertIn('"graphite"', head[script:first_style])     # only known theme ids are applied

    def test_header_uses_data_time(self):
        reps = [report(gen_ago_s=3600), report(user="Lee", install_id="c3d4", gen_ago_s=7200)]
        later = NOW.replace(hour=23, minute=59)
        h = page.render(reps, [], "0.2.0", later, RULES)
        self.assertIn("Claude usage · jusage 0.2.0 · data as of", h)
        self.assertIn('datetime="%s"' % at(seconds=-3600), h)
        data_time = timeutil.parse_iso(at(seconds=-3600))
        self.assertIn(">%s<" % formats.clock(data_time, later, True), h)   # never the build time
        self.assertEqual(data_block(h)["generated"], at(seconds=-3600))

    def test_no_reports_header(self):
        h = self.render([])
        self.assertIn("no reports yet", h)

    def test_data_block_carries_rules_and_version(self):
        d = data_block(self.render())
        self.assertEqual(d["rules"]["stale_after_s"], 900)
        self.assertEqual(d["version"], "0.2.0")

    def test_rules_without_stale_after_refused_at_build(self):
        with self.assertRaises(KeyError):
            self.render(rules={"red_at": 97})

    def test_house_modules_inlined(self):
        h = self.render()
        for name in ("window.HOUSE", "clientErr", "HouseAccent", "HouseQuickKeys", "HouseReport",
                     "HouseTips", "HouseVersion", "hk-btn", "JDash"):
            self.assertIn(name, h)
        self.assertIn("contact@fourletterrecords.com", h)
        self.assertIn("appId: 'jusage'", h)
        self.assertNotRegex(h, r"<script[^>]+src=|<link[^>]+href=")   # self-contained: file:// has nothing else

    def test_house_config_before_errlog_before_rest(self):
        h = self.render()
        i_cfg, i_err = h.index("window.HOUSE ="), h.index("window.clientErr =")
        i_acc, i_dash = h.index("window.HouseAccent ="), h.index("var JDash")
        i_data = h.index('id="data"')
        self.assertLess(i_data, i_cfg)
        self.assertLess(i_cfg, i_err)
        self.assertLess(i_err, i_acc)
        self.assertLess(i_acc, i_dash)

    def test_api_rate_label_on_page(self):
        self.assertIn(formats.API_RATE_LABEL, self.render())

    def test_notes_on_page(self):
        self.assertIn("skipped x.json", self.render(notes=["skipped x.json"]))

    def test_inlined_code_cannot_close_its_element(self):
        h = self.render()
        for chunk in re.findall(r"<script>(.*?)</script>", h, re.S):
            self.assertNotIn("</script", chunk.lower())
        self.assertEqual(h.lower().count("</style>"), 3)

    def test_script_and_engine_share_reset_due_text(self):
        self.assertIn("'%s'" % formats.RESET_DUE_TEXT, (page.STATIC / "dash.js").read_text())

    def test_tips_follow_board_talk(self):
        tips_js = (page.STATIC / "house" / "tips.js").read_text()
        banned = re.findall(r"'([^']+)'", re.search(r"var BANNED = \[(.*?)\];", tips_js).group(1))
        self.assertIn("please", banned)
        sp = {"period_start": at(days=-3), "parts": [{"user": "Sam", "install_id": "a1b2", "usd": 9.0, "share": 1.0}]}
        from tests.engine.test_dash_fixtures import account, meter, U1
        h = self.render([report(live={U1: account(meters=[meter("weekly", split=sp)])})])
        tips = re.findall(r"""title=(?:"([^"]*)"|'([^']*)')""", h)
        self.assertTrue(tips)
        for a, b in tips:
            tip = a or b
            self.assertLessEqual(len(tip), 60, tip)
            words = " " + re.sub(r"[^a-z0-9]+", " ", tip.lower()) + " "
            for w in banned:
                self.assertNotIn(" %s " % w, words, tip)

    def test_write_atomic_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dashboard.html"
            page.write(p, "<p>one</p>")
            page.write(p, "<p>two</p>")
            self.assertEqual(p.read_text(), "<p>two</p>")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(sorted(os.listdir(d)), ["dashboard.html"])


if __name__ == "__main__":
    unittest.main()
