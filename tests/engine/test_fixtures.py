"""T1 fixture self-tests. Each fixture is checked against an independent recomputation from the
spec, so a typo in a golden file fails here and not in some later chair's test.

The pace oracle below is the SPEC (sections 9 and 10, as amended by J) written out with exact
fractions. It is a test oracle, not engine code: the engine's own pace function (chair E17) is
tested against the same fixture."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

from ju import constants as C
from ju import timeutil

from tests.engine import support

L = {"session": C.SESSION_S, "weekly": C.WEEK_S}
ISO_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def ref_pace(period_s, t_s, used):
    """-> dict(forecast, landing, state, runs_out_in_s, reset_due) for `used` percent, `t_s` seconds
    into a window of `period_s` seconds. Exact arithmetic; states per the amended colour rule."""
    used = Fraction(str(used))
    period = Fraction(period_s)
    t = Fraction(t_s)
    if t >= period:
        return {"forecast": False, "landing": None, "state": None, "runs_out_in_s": None, "reset_due": True}
    e = t / period
    forecast = e >= Fraction(3, 100)
    red = used >= Fraction(str(C.RED_AT))
    amber = (not red) and forecast and used > e * 100
    state = "red" if red else "amber" if amber else "green"
    landing = runs_out = None
    if forecast:
        span = max(t, period / C.SPAN_FLOOR_DIV)
        landing = used * period / span
        if used > 0 and landing >= C.RUNS_OUT_LANDING:
            r = span * (100 - used) / used
            if r < period - t:
                runs_out = r
    return {"forecast": forecast,
            "landing": None if landing is None else float(landing),
            "state": state,
            "runs_out_in_s": None if runs_out is None else int(runs_out),
            "reset_due": False}


class PaceRowsTests(unittest.TestCase):
    def setUp(self):
        self.fx = support.load_fixture("pace_rows.json")

    def test_rules_block_matches_constants(self):
        r = self.fx["rules"]
        self.assertEqual(r["red_at"], C.RED_AT)
        self.assertEqual(r["warmup_e"], C.WARMUP_E)
        self.assertEqual(r["span_floor_div"], C.SPAN_FLOOR_DIV)
        self.assertEqual(r["runs_out_landing"], C.RUNS_OUT_LANDING)
        self.assertEqual(r["periods_s"], {"5h": 18000, "7d": 604800})

    def test_every_row_matches_the_oracle(self):
        for row in self.fx["rows"]:
            with self.subTest(row["id"]):
                want = ref_pace({"5h": 18000, "7d": 604800}[row["window"]], row["t_s"], row["used"])
                got = row["expect"]
                self.assertEqual(got["forecast"], want["forecast"])
                self.assertEqual(got["state"], want["state"])
                self.assertEqual(got["reset_due"], want["reset_due"])
                self.assertEqual(got["runs_out_in_s"], want["runs_out_in_s"])
                if want["landing"] is None:
                    self.assertIsNone(got["landing"])
                else:
                    self.assertAlmostEqual(got["landing"], want["landing"], delta=self.fx["landing_tol"])

    def test_ids_unique_and_sim_rows_all_present(self):
        ids = [r["id"] for r in self.fx["rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        sim = [r for r in self.fx["rows"] if r["id"].startswith("sim-")]
        self.assertEqual(len(sim), 24)
        for r in sim:
            self.assertIn("old_verdict", r)

    def test_named_spec_cases(self):
        by = {r["id"]: r["expect"] for r in self.fx["rows"]}
        # 5 h burst: no forecast at 5 min
        self.assertFalse(by["sim-5h-burst-5m"]["forecast"])
        self.assertAlmostEqual(by["sim-5h-burst-15m"]["landing"], 56.0, delta=0.1)
        # exactly on pace is never amber or red (rows with exact on-pace numbers)
        for rid in ("sim-5h-onpace-15m", "sim-5h-onpace-30m", "sim-5h-onpace-60m"):
            self.assertEqual(by[rid]["state"], "green")
        # 1.5x on 5 h: amber with a runs-out time by 30 min
        self.assertEqual(by["sim-5h-fast-30m"]["state"], "amber")
        self.assertIsNotNone(by["sim-5h-fast-30m"]["runs_out_in_s"])
        # boundaries
        self.assertFalse(by["x-warmup-just-before"]["forecast"])
        self.assertTrue(by["x-warmup-exact"]["forecast"])
        self.assertEqual(by["x-white-line-equal"]["state"], "green")
        self.assertEqual(by["x-white-line-over"]["state"], "amber")
        self.assertEqual(by["x-red-96-9"]["state"], "amber")
        self.assertEqual(by["x-red-97"]["state"], "red")
        self.assertEqual(by["x-red-in-warmup"]["state"], "red")
        self.assertTrue(by["x-reset-due"]["reset_due"])
        self.assertFalse(by["x-reset-due"]["forecast"])
        self.assertIsNone(by["x-reset-due"]["landing"])

    def test_colour_follows_the_white_line_not_the_forecast(self):
        by = {r["id"]: r["expect"] for r in self.fx["rows"]}
        # amber although the landing is far under 105: past the white line is all that matters
        self.assertEqual(by["sim-5h-burst-15m"]["state"], "amber")
        self.assertLess(by["sim-5h-burst-15m"]["landing"], 80)
        # the same burst later: under the white line again, green
        self.assertEqual(by["sim-5h-burst-30m"]["state"], "green")

    def test_rows_landing_exactly_on_105_are_flagged(self):
        flagged = {r["id"] for r in self.fx["rows"] if "edge" in r}
        self.assertEqual(flagged, {"sim-5h-fast-30m", "sim-7d-burst-12h", "sim-7d-burst-24h"})
        for r in self.fx["rows"]:
            if r["id"] in flagged:
                self.assertEqual(r["expect"]["landing"], 105.0)
                self.assertIsNotNone(r["expect"]["runs_out_in_s"])


class FormatsTests(unittest.TestCase):
    def setUp(self):
        self.fx = support.load_fixture("formats.json")

    @staticmethod
    def tokens(n):
        return (f"{n/1e9:.2f}B" if n >= 1e9 else f"{n/1e6:.1f}M" if n >= 1e6
                else f"{n/1e3:.0f}k" if n >= 1e3 else str(int(n)))

    @staticmethod
    def usd(v):
        return f"${v:,.0f}" if v >= 100 else f"${v:.2f}"

    @staticmethod
    def countdown(s):
        if s <= 0:
            return "due"
        m = int(s) // 60
        if m < 60:
            return f"{m}m"
        if m < 1440:
            return f"{m // 60}h {m % 60:02d}m"
        return f"{m // 1440}d {(m % 1440) // 60}h"

    @staticmethod
    def clock(at, now, tz_min, hour12):
        tz = timezone(timedelta(minutes=tz_min))
        a = timeutil.parse_iso(at).astimezone(tz)
        n = timeutil.parse_iso(now).astimezone(tz)
        if hour12:
            h = a.hour % 12 or 12
            hm = f"{h}:{a.minute:02d} {'AM' if a.hour < 12 else 'PM'}"
        else:
            hm = f"{a.hour:02d}:{a.minute:02d}"
        return hm if a.date() == n.date() else f"{a.strftime('%a')} {hm}"

    def test_tokens(self):
        self.assertGreaterEqual(len(self.fx["tokens"]), 12)
        for c in self.fx["tokens"]:
            with self.subTest(c["n"]):
                self.assertEqual(c["text"], self.tokens(c["n"]))
        texts = {c["text"] for c in self.fx["tokens"]}
        for must in ("1.23B", "4.5M", "450k", "12"):
            self.assertIn(must, texts)

    def test_usd(self):
        self.assertGreaterEqual(len(self.fx["usd"]), 8)
        for c in self.fx["usd"]:
            with self.subTest(c["usd"]):
                self.assertEqual(c["text"], self.usd(c["usd"]))

    def test_countdown(self):
        self.assertGreaterEqual(len(self.fx["countdown"]), 8)
        for c in self.fx["countdown"]:
            with self.subTest(c["seconds"]):
                self.assertEqual(c["text"], self.countdown(c["seconds"]))
        texts = {c["text"] for c in self.fx["countdown"]}
        for must in ("42m", "2h 14m", "5d 21h"):
            self.assertIn(must, texts)

    def test_clock(self):
        self.assertGreaterEqual(len(self.fx["clock"]), 6)
        for c in self.fx["clock"]:
            with self.subTest(c):
                self.assertEqual(c["text"], self.clock(c["at"], c["now"], c["tz_offset_min"], c["hour12"]))
        self.assertTrue(any(c["hour12"] for c in self.fx["clock"]))
        self.assertTrue(any(not c["hour12"] for c in self.fx["clock"]))
        self.assertTrue(any(c["text"].startswith("Fri ") for c in self.fx["clock"]))

    def test_no_value_sits_on_a_half_rounding_edge(self):
        # Python, Swift and JS round halves differently; the fixture must not depend on it.
        for c in self.fx["tokens"]:
            n = c["n"]
            if n >= 1e9:
                self.assertNotEqual(round(n / 1e7 % 1, 6), 0.5)
            elif n >= 1e6:
                self.assertNotEqual(round(n / 1e5 % 1, 6), 0.5)
            elif n >= 1e3:
                self.assertNotEqual(round(n / 1e3 % 1, 6), 0.5)


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)
    else:
        yield None, o


class FeedFixtureTests(unittest.TestCase):
    TOP = ["schema", "version", "generated", "next_due", "rules", "me", "accounts", "totals", "windows",
           "models", "projects", "months", "people", "statusline", "dashboard", "share", "notes"]
    ACCOUNT = ["key", "number", "email", "uuid", "folders", "weekly_reset", "flags", "warning", "error",
               "no_session_meter", "meters", "session_share", "missing_people"]
    METER = ["id", "name", "pct", "resets_at", "read_at", "source", "period_s", "disagree", "pace", "split"]
    PACE = ["e", "landing", "state", "forecast", "runs_out", "reset_due", "words"]

    def setUp(self):
        self.feed = support.load_fixture("feed-v2.json")

    def test_top_level_keys_exactly_the_schema(self):
        self.assertEqual(list(self.feed), self.TOP)
        self.assertEqual(self.feed["schema"], C.FEED_SCHEMA)
        self.assertRegex(self.feed["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(self.feed["rules"], {"stale_after_s": C.STALE_AFTER_S, "red_at": 97, "warmup_e": 0.03,
                                              "read_every_s": C.READ_EVERY_DEFAULT})

    def test_every_time_has_an_offset(self):
        n = 0
        for _, v in walk(self.feed):
            if isinstance(v, str) and ISO_LIKE.match(v):
                timeutil.parse_iso(v)       # raises NaiveTime without an offset
                n += 1
        self.assertGreater(n, 20)

    def test_accounts_sorted_by_number_and_keys_exact(self):
        accts = self.feed["accounts"]
        self.assertGreaterEqual(len(accts), 2)
        self.assertEqual([a["number"] for a in accts], sorted(a["number"] for a in accts))
        for a in accts:
            self.assertEqual(list(a), self.ACCOUNT)
            for m in a["meters"]:
                self.assertEqual(list(m), self.METER)
                self.assertEqual(list(m["pace"]), self.PACE)
                self.assertIn(m["source"], ("service", "statusline", "last_good"))

    def test_person_blocks_have_no_runs_out(self):
        self.assertGreaterEqual(len(self.feed["people"]), 1)
        for p in self.feed["people"]:
            self.assertEqual(list(p), ["install_id", "user", "host", "generated", "today", "week", "accounts"])
            for a in p["accounts"]:
                for m in a["meters"]:
                    self.assertNotIn("runs_out", m["pace"])

    def test_error_kinds_are_in_the_closed_set(self):
        from ju.contracts import ERROR_KINDS
        seen = [a["error"]["kind"] for a in self.feed["accounts"] if a["error"]]
        self.assertTrue(seen)
        for k in seen:
            self.assertIn(k, ERROR_KINDS)

    def test_pace_blocks_match_the_oracle(self):
        gen = timeutil.parse_iso(self.feed["generated"])
        n = 0
        for a in self.feed["accounts"]:
            for m in a["meters"]:
                with self.subTest(f"{a['number']}/{m['id']}"):
                    reset = timeutil.parse_iso(m["resets_at"])
                    t_s = m["period_s"] - (reset - gen).total_seconds()
                    want = ref_pace(m["period_s"], t_s, m["pct"])
                    p = m["pace"]
                    self.assertEqual(p["state"], want["state"])
                    self.assertEqual(p["forecast"], want["forecast"])
                    self.assertAlmostEqual(p["e"], t_s / m["period_s"], places=4)
                    if want["landing"] is None:
                        self.assertIsNone(p["landing"])
                    else:
                        self.assertAlmostEqual(p["landing"], want["landing"], delta=0.1)
                    if want["runs_out_in_s"] is None:
                        self.assertIsNone(p["runs_out"])
                    else:
                        ro = timeutil.parse_iso(p["runs_out"])
                        self.assertAlmostEqual((ro - gen).total_seconds(), want["runs_out_in_s"], delta=1)
                    n += 1
        self.assertGreaterEqual(n, 5)

    def test_words_are_what_the_engine_writes(self):
        """RULING 34: the golden words are E17's own text at `generated`; a clock is never in them
        (RULING 4: `{runs_out}` is the placeholder the app fills in)."""
        from ju import pace as pace_mod
        from ju.contracts import Meter
        gen = timeutil.parse_iso(self.feed["generated"])
        for a in self.feed["accounts"]:
            for m in a["meters"]:
                with self.subTest(f"{a['number']}/{m['id']}"):
                    meter = Meter(m["id"], m["name"], m["pct"], timeutil.parse_iso(m["resets_at"]), m["period_s"])
                    self.assertEqual(m["pace"]["words"], pace_mod.pace(meter, gen).words)
                    self.assertNotRegex(m["pace"]["words"].replace("{runs_out}", ""), r"\d:\d\d")

    def test_covers_each_state_and_a_stale_reading(self):
        states = {m["pace"]["state"] for a in self.feed["accounts"] for m in a["meters"]}
        self.assertEqual(states, {"green", "amber", "red"})
        gen = timeutil.parse_iso(self.feed["generated"])
        ages = [(gen - timeutil.parse_iso(m["read_at"])).total_seconds()
                for a in self.feed["accounts"] for m in a["meters"]]
        self.assertTrue(any(x >= C.STALE_AFTER_S for x in ages))
        self.assertTrue(any(x < C.STALE_AFTER_S for x in ages))

    def test_weekly_meter_carries_a_split(self):
        splits = [m["split"] for a in self.feed["accounts"] for m in a["meters"] if m["split"]]
        self.assertTrue(splits)
        for s in splits:
            self.assertAlmostEqual(sum(p["share"] for p in s["parts"]), 1.0, places=2)
            timeutil.parse_iso(s["period_start"])

    def test_totals_and_months_shape(self):
        t = self.feed["totals"]
        self.assertEqual(list(t), ["as_of", "today", "week"])
        self.assertEqual(list(t["today"]), ["tokens", "usd", "calls", "unpriced"])
        self.assertEqual(list(t["week"]), ["tokens", "usd", "unpriced"])
        m = self.feed["months"][0]
        self.assertEqual(list(m), ["key", "label", "tokens", "usd", "calls", "types", "models", "projects"])
        self.assertEqual(list(m["types"]), ["input", "cache_w", "cache_read", "output"])

    def test_docs_copy_of_schema_exists_and_names_every_top_key(self):
        doc = (Path(__file__).resolve().parents[2] / "docs" / "feed-schema.md").read_text()
        for k in self.TOP:
            self.assertIn(f'"{k}"', doc)


class ReplyFixtureTests(unittest.TestCase):
    def test_limits_reply_has_session_weekly_all_and_scoped(self):
        d = support.load_fixture("reply-limits.json")
        kinds = [e["kind"] for e in d["limits"]]
        self.assertEqual(kinds, ["session", "weekly_all", "weekly_scoped"])
        scoped = d["limits"][2]
        self.assertEqual(scoped["scope"]["model"]["display_name"], "Fable 5")
        for e in d["limits"]:
            self.assertIsInstance(e["percent"], (int, float))
            timeutil.parse_iso(e["resets_at"])

    def test_legacy_reply_has_old_objects_and_no_limits(self):
        d = support.load_fixture("reply-legacy.json")
        self.assertNotIn("limits", d)
        for k in ("five_hour", "seven_day", "seven_day_opus"):
            self.assertIn("utilization", d[k])
            timeutil.parse_iso(d[k]["resets_at"])

    def test_changed_reply_has_neither_shape(self):
        d = support.load_fixture("reply-changed.json")
        self.assertNotIn("limits", d)
        for k in ("five_hour", "seven_day"):
            self.assertNotIn(k, d)
        self.assertTrue(d)


class TranscriptFixtureTests(unittest.TestCase):
    ROOT = support.FIXTURES / "transcripts"

    def setUp(self):
        self.man = json.loads((self.ROOT / "expected.json").read_text())

    def test_every_listed_file_exists_with_the_listed_line_count(self):
        for f in self.man["files"]:
            with self.subTest(f["path"]):
                p = self.ROOT / f["path"]
                self.assertTrue(p.is_file())
                self.assertEqual(len(p.read_text().splitlines()), f["lines"])

    def test_no_file_holds_message_text_or_home_paths(self):
        for p in self.ROOT.rglob("*.jsonl"):
            text = p.read_text()
            self.assertNotIn('"content"', text)
            self.assertNotIn("/" + "Users" + "/", text)   # spelled apart so this file passes the public-hygiene sweep

    def test_expected_rows_name_real_files_and_cover_the_cases(self):
        paths = {f["path"] for f in self.man["files"]}
        for r in self.man["rows"]:
            self.assertIn(r["file"], paths)
        cases = {r["case"] for r in self.man["rows"]}
        for must in ("plain", "streaming-duplicate-final", "unfinished", "uuid-fallback", "old-cache-format",
                     "worktree", "same-key-other-account"):
            self.assertIn(must, cases)
        self.assertEqual(set(self.man["skipped"]), {"synthetic-model", "malformed-line", "non-assistant"})
        self.assertEqual(len({r["key"] for r in self.man["rows"]}), 6)

    def test_expected_rows_agree_with_the_file_contents(self):
        for r in self.man["rows"]:
            with self.subTest(r["case"]):
                lines = (self.ROOT / r["file"]).read_text().splitlines()
                o = json.loads(lines[r["line"] - 1])
                self.assertEqual(o["type"], "assistant")
                self.assertEqual(o["timestamp"], r["ts"])
                self.assertEqual(o["message"]["model"], r["model"])
                self.assertEqual(o["message"]["usage"]["output_tokens"], r["output"])


if __name__ == "__main__":
    unittest.main()
