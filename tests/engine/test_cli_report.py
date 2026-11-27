"""E23 CLI output: the report table (dollars headed "$ API-rate", unpriced rows starred and
counted), the 5-hour block list, one live block per account, and the split line."""
import io
import unittest
from datetime import datetime, timedelta, timezone

from ju import cli_report, formats, pricing as pricing_mod, timeutil
from ju.constants import REPO_ROOT

PST = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 26, 21, 40, tzinfo=PST)
PRICING = pricing_mod.load(REPO_ROOT / "engine" / "pricing.json")


def row(day, model, account="/h/.claude", project="demo", ts=None, **tok):
    r = {"day": day, "model": model, "account": account, "project": project,
         "ts": ts or f"{day}T10:00:00-08:00", "input": 0, "cache_5m": 0, "cache_1h": 0, "cache_read": 0, "output": 0}
    r.update(tok)
    return r


class Table(unittest.TestCase):
    def test_table_totals_and_unpriced_star(self):
        rows = [row("2026-11-25", "claude-opus-5", input=1_000_000, output=100_000),
                row("2026-11-26", "claude-opus-5", input=2_000_000),
                row("2026-11-26", "claude-nothing-1", input=5)]
        out = io.StringIO()
        cli_report.print_table(rows, "day", PRICING, title="usage", out=out)
        text = out.getvalue()
        lines = text.splitlines()
        header = next(l for l in lines if l.startswith("day"))
        self.assertIn("$ API-rate", header)
        self.assertNotIn("$ api ", header)
        day26 = next(l for l in lines if l.startswith("2026-11-26"))
        self.assertTrue(day26.rstrip().endswith("*"), day26)
        day25 = next(l for l in lines if l.startswith("2026-11-25"))
        self.assertFalse(day25.rstrip().endswith("*"))
        total = next(l for l in lines if l.startswith("TOTAL"))
        self.assertIn(" 3 ", total)                         # calls
        self.assertIn("* 1 call", text)
        self.assertIn(formats.API_RATE_LABEL, text)
        self.assertLess(lines.index(day25), lines.index(day26))     # days in order

    def test_by_model_sorted_by_tokens_and_named(self):
        rows = [row("2026-11-26", "claude-sonnet-5-20261101", input=10),
                row("2026-11-26", "claude-opus-5", input=1000)]
        out = io.StringIO()
        cli_report.print_table(rows, "model", PRICING, title="usage", out=out)
        body = [l for l in out.getvalue().splitlines() if l.startswith("claude-")]
        self.assertEqual([l.split()[0] for l in body], ["claude-opus-5", "claude-sonnet-5-20261101"])

    def test_by_account_uses_short_names(self):
        rows = [row("2026-11-26", "claude-opus-5", account="/h/.claude", input=10),
                row("2026-11-26", "claude-opus-5", account="/h/.claude-newaccount", input=20)]
        out = io.StringIO()
        cli_report.print_table(rows, "account", PRICING, title="usage", out=out)
        self.assertIn(".claude-newaccount", out.getvalue())
        self.assertNotIn("/h/.claude-newaccount", out.getvalue())

    def test_by_project_groups_subagents(self):
        rows = [row("2026-11-26", "claude-opus-5", project="agent-1", input=10),
                row("2026-11-26", "claude-opus-5", project="agent-2", input=10)]
        out = io.StringIO()
        cli_report.print_table(rows, "project", PRICING, title="usage", out=out)
        self.assertEqual(sum(1 for l in out.getvalue().splitlines() if l.startswith("subagents")), 1)

    def test_blocks_open_window_marked(self):
        rows = [row("2026-11-26", "claude-opus-5", ts="2026-11-26T14:10:00-08:00", input=10),
                row("2026-11-26", "claude-opus-5", ts="2026-11-26T20:10:00-08:00", input=10)]
        out = io.StringIO()
        cli_report.print_blocks(rows, PRICING, last=6, now=NOW, out=out)
        lines = [l for l in out.getvalue().splitlines() if "tokens" in l]
        self.assertEqual(len(lines), 2)
        self.assertNotIn("open", lines[0])
        self.assertIn("open", lines[1])


def meter(mid, pct, source="service", runs_out=None, words="On pace: about 75% by the reset."):
    return {"id": mid, "name": mid.capitalize(), "pct": pct, "resets_at": timeutil.iso(NOW + timedelta(hours=2)),
            "read_at": timeutil.iso(NOW - timedelta(minutes=1)), "source": source, "period_s": 18000,
            "disagree": False, "split": None,
            "pace": {"e": 0.5, "landing": 74.0, "state": "green", "forecast": True, "runs_out": runs_out,
                     "reset_due": False, "words": words}}


class Live(unittest.TestCase):
    def feed(self):
        ro = timeutil.iso(NOW + timedelta(minutes=30))
        return {"accounts": [
            {"key": "/h/.claude", "number": 1, "email": "sam@example.com", "warning": None, "error": None,
             "meters": [meter("session", 37.0), meter("weekly", 80.0, runs_out=ro,
                                                      words="At this pace you hit the limit {runs_out}, 1h 30m before the reset.")]},
            {"key": "/h/.claude-newaccount", "number": 2, "email": None, "warning": "legacy item",
             "error": {"kind": "rate_limited", "text": "Anthropic is rate-limiting usage reads",
                       "until": timeutil.iso(NOW + timedelta(minutes=17))},
             "meters": [meter("session", 98.0, source="last_good")]}]}

    def test_live_lines_one_per_account(self):
        out = io.StringIO()
        cli_report.print_live(self.feed(), NOW, out=out)
        text = out.getvalue()
        heads = [l for l in text.splitlines() if l.startswith("#")]
        self.assertEqual(len(heads), 2)
        self.assertTrue(heads[0].startswith("#1 sam@example.com"))
        self.assertTrue(heads[1].startswith("#2 .claude-newaccount"))
        self.assertIn("rate-limiting", heads[1])
        self.assertIn("37.0%", text)
        self.assertNotIn("{runs_out}", text)
        self.assertIn(formats.clock(NOW + timedelta(minutes=30), NOW, True), text)
        self.assertIn("last good", text)
        self.assertIn("legacy item", text)

    def test_split_line(self):
        out = io.StringIO()
        cli_report.print_split({"period_start": timeutil.iso(NOW), "parts": [
            {"user": "Sam", "install_id": "a1b2", "usd": 412.0, "share": 0.71},
            {"user": "Lee", "install_id": "c3d4", "usd": 168.2, "share": 0.29}]}, ["Kim"], out=out)
        self.assertEqual(out.getvalue().strip(),
                         "this period: Sam 71% ($412) · Lee 29% ($168) · Kim: not reported yet (update jusage)")

    def test_no_split_prints_nothing(self):
        out = io.StringIO()
        cli_report.print_split(None, [], out=out)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
