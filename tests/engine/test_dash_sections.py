"""D2 sections: one function per section, each renders alone from one DashModel."""
import re
import unittest
from datetime import timedelta

from ju import formats
from ju.dash import sections
from ju.dash.sections import DashModel
from tests.engine.test_dash_fixtures import (NOW, RULES, U1, U2, account, at, day, meter,
                                             report)

SECTION_FUNCS = ("today_panels", "everyone", "plan_meters", "charts", "models_table",
                 "month_tables", "footnote", "drawer")


def build(reps, notes=()):
    return DashModel.build(reps, NOW, RULES, list(notes))


def days_ago(n):
    return (NOW.date() - timedelta(days=n)).isoformat()


class Sections(unittest.TestCase):
    def test_each_section_renders_alone(self):
        for reps in ([], [report()], [report(), report(user="Lee", install_id="c3d4")]):
            m = build(reps)
            for name in SECTION_FUNCS:
                out = getattr(sections, name)(m)
                self.assertIsInstance(out, str, name)

    def test_group_by_uuid_freshest_usable(self):
        fresh = report(user="Sam", install_id="a1b2",
                       live={U1: account(meters=[meter(pct=40.0, read_ago_s=300)])})
        stale_newer_gen = report(user="Lee", install_id="c3d4", gen_ago_s=10,
                                 live={U1: account(meters=[meter(pct=11.0, read_ago_s=1800)])})
        h = sections.plan_meters(build([stale_newer_gen, fresh]))
        self.assertEqual(h.count("data-k='acct:%s'" % U1), 1)       # one account, one block
        self.assertIn("40%", h)
        self.assertNotIn("11%", h)
        self.assertIn("read on Sam", h)
        self.assertIn("shared by Lee, Sam", h)

    def test_newest_of_two_fresh_wins(self):
        a = report(user="Sam", live={U1: account(meters=[meter(pct=40.0, read_ago_s=300)])})
        b = report(user="Lee", install_id="c3d4", live={U1: account(meters=[meter(pct=42.0, read_ago_s=60)])})
        h = sections.plan_meters(build([a, b]))
        self.assertIn("42%", h)
        self.assertNotIn("40%", h)

    def test_legacy_group_wording(self):
        sp = {"period_start": at(days=-3), "parts": [{"user": "Old", "install_id": "e5f6", "usd": 1.0, "share": 1.0}]}
        old = report(user="Old", install_id="e5f6", legacy=True,
                     live={"old@example.com": {"meters": [meter(split=sp)]}},
                     accounts=["old@example.com"])
        h = sections.plan_meters(build([old]))
        self.assertIn("not reported yet (update jusage)", h)
        self.assertNotIn("Old 100%", h)                    # no split for a legacy group

    def test_top7_plus_other(self):
        projects = {f"p{i}": {"tokens": 1000 - i, "cost": 0.1} for i in range(9)}
        m = build([report(days={NOW.date().isoformat(): day(projects=projects)})])
        proj = next(c for c in m.charts if c["cid"] == "proj")
        labels = [lab for _, lab in proj["series"]]
        self.assertEqual(labels, [f"p{i}" for i in range(7)] + ["other"])

    def test_model_legend_7d(self):
        old_model = {"claude-sonnet-4-5-20250929": {"input": 1, "cache_5m": 0, "cache_1h": 0, "cache_read": 0,
                                                     "output": 9, "tokens": 999999, "calls": 1, "cost": 9.0}}
        reps = [report(days={NOW.date().isoformat(): day(), days_ago(20): day(models=old_model)})]
        m = build(reps)
        model = next(c for c in m.charts if c["cid"] == "model")
        self.assertEqual([lab for _, lab in model["series"]], ["opus-5"])
        self.assertIn("sonnet-4-5", sections.models_table(m))       # the 30-day table still has it

    def test_drawer_keys_by_id(self):
        h = sections.drawer(build([report()]))
        self.assertIn("value='person:a1b2'", h)
        self.assertIn("value='acct:%s'" % U1, h)
        self.assertNotRegex(h, r"value='[^']*@")                   # never an email as a key

    def test_one_person_per_user_and_uuid_set(self):
        two_macs = [report(user="Sam", host="mac", install_id="a1b2"),
                    report(user="Sam", host="mini", install_id="9f9f")]
        m = build(two_macs)
        self.assertEqual(len(m.people), 1)
        self.assertEqual(len(re.findall(r"class='panel today person", sections.today_panels(m))), 1)
        other = build([report(user="Sam"), report(user="Lee", install_id="c3d4")])
        self.assertEqual(len(other.people), 2)
        diff_accts = build([report(user="Sam"),
                            report(user="Sam", install_id="9f9f", live={U2: account(uuid=U2, email="b@x")})])
        self.assertEqual(len(diff_accts.people), 2)

    def test_two_macs_of_one_person_add_up(self):
        m = build([report(user="Sam", install_id="a1b2"), report(user="Sam", host="mini", install_id="9f9f")])
        self.assertIn(formats.tokens(2000), sections.today_panels(m))

    def test_peer_stale_15_min_from_generated(self):
        h = sections.today_panels(build([report(gen_ago_s=899)]))
        self.assertNotIn("person stale", h)
        h = sections.today_panels(build([report(gen_ago_s=900)]))
        self.assertIn("person stale", h)
        self.assertIn('data-read-at="%s"' % at(seconds=-900), h)

    def test_dollars_labelled_api_rate(self):
        m = build([report()])
        self.assertIn(formats.API_RATE_LABEL, sections.today_panels(m))
        self.assertIn("$ API-rate", sections.models_table(m))
        self.assertIn("$ API-rate", sections.month_tables(m))
        self.assertIn(formats.API_RATE_LABEL, sections.footnote(m))

    def test_session_share_line_greys_with_its_own_time(self):
        ss = {"current": 4.0, "last": 11.0, "median7": 9.0,
              "list": [{"start": at(hours=-6), "pct": 11.0}], "as_of": at(seconds=-60)}
        h = sections.plan_meters(build([report(live={U1: account(session_share=ss)})]))
        self.assertIn("This session: 4% of the week · last: 11%", h)
        self.assertIn('data-read-at="%s"' % ss["as_of"], h)
        self.assertIn("median of the last 7: 9%", h)

    def test_footnote_unpriced_local_days_and_notes(self):
        m = build([report(days={NOW.date().isoformat(): day(unpriced=3)})],
                  notes=["skipped Lee@mac-c3d4.json: report_version 1"])
        h = sections.footnote(m)
        self.assertIn("3 calls this month", h)
        self.assertIn("local calendar days", h)
        self.assertIn("skipped Lee@mac-c3d4.json: report_version 1", h)

    def test_account_error_shown_above_last_good(self):
        err = {"kind": "rate_limited", "text": "Anthropic asked us to wait", "until": None}
        h = sections.plan_meters(build([report(live={U1: account(error=err)})]))
        self.assertIn("Anthropic asked us to wait", h)

    def test_open_window_only(self):
        blocks = {U1: [{"start": at(hours=-2), "end": at(hours=3), "tokens": 4500, "usd": 1.0}]}
        h = sections.today_panels(build([report(blocks=blocks)]))
        self.assertIn("5-hour window", h)
        closed = {U1: [{"start": at(hours=-7), "end": at(hours=-2), "tokens": 4500, "usd": 1.0}]}
        self.assertNotIn("5-hour window", sections.today_panels(build([report(blocks=closed)])))

    def test_everyone_only_with_two_people(self):
        self.assertEqual(sections.everyone(build([report()])), "")
        self.assertIn("everyone", sections.everyone(build([report(), report(user="Lee", install_id="c3d4")])))

    def test_no_reports_says_so(self):
        self.assertIn("No reports yet", sections.today_panels(build([])))


if __name__ == "__main__":
    unittest.main()
