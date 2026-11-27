"""E11 usage client: fetch (R-1, R-2, R-21) and parse (R-3, R-4, R-5). Never the real endpoint."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

from ju import constants, timeutil, usage_client
from ju.contracts import HttpError, LoginError, NetworkError, RateLimited, ReplyChanged
from tests.engine import support

LIMITS = support.load_fixture("reply-limits.json")
LEGACY = support.load_fixture("reply-legacy.json")
CHANGED = support.load_fixture("reply-changed.json")


def ids(parsed):
    return [m.id for m in parsed.meters]


class ParseTests(unittest.TestCase):
    def test_limits_first(self):
        p = usage_client.parse(LIMITS)
        self.assertEqual(ids(p), ["session", "weekly", "weekly:fable"])
        self.assertEqual([m.name for m in p.meters], ["Session", "Weekly", "Weekly · Fable 5"])
        self.assertEqual([m.pct for m in p.meters], [37.0, 52.0, 18.0])
        self.assertEqual([m.period_s for m in p.meters],
                         [constants.SESSION_S, constants.WEEK_S, constants.WEEK_S])
        s = p.meters[0]
        self.assertEqual(s.resets_at, datetime(2026, 11, 27, 8, 10, 0, 123456, tzinfo=timezone.utc))
        self.assertIsNotNone(s.resets_at.utcoffset())
        self.assertFalse(p.no_session_meter)
        self.assertEqual(p.flags, [])

    def test_limits_win_over_legacy_fields(self):
        reply = copy.deepcopy(LIMITS)
        reply["five_hour"] = {"utilization": 99.0, "resets_at": "2026-11-27T08:10:00+00:00"}
        self.assertEqual(usage_client.parse(reply).meters[0].pct, 37.0)

    def test_legacy_fallback(self):
        p = usage_client.parse(LEGACY)
        self.assertEqual(ids(p), ["session", "weekly", "weekly:opus"])
        self.assertEqual([m.name for m in p.meters], ["Session", "Weekly", "Weekly · Opus"])
        self.assertEqual([m.pct for m in p.meters], [37.0, 52.0, 18.0])
        self.assertEqual([m.period_s for m in p.meters],
                         [constants.SESSION_S, constants.WEEK_S, constants.WEEK_S])

    def test_legacy_null_scoped_field_is_skipped(self):
        reply = copy.deepcopy(LEGACY)
        reply["seven_day_sonnet"] = None
        self.assertEqual(ids(usage_client.parse(reply)), ["session", "weekly", "weekly:opus"])

    def test_no_session_meter_flag(self):
        reply = {"limits": [LIMITS["limits"][1]]}
        p = usage_client.parse(reply)
        self.assertEqual(ids(p), ["weekly"])
        self.assertTrue(p.no_session_meter)
        legacy = {"seven_day": LEGACY["seven_day"], "five_hour": None}
        self.assertTrue(usage_client.parse(legacy).no_session_meter)

    def test_duplicate_scoped_ids_numbered(self):
        reply = copy.deepcopy(LIMITS)
        extra = copy.deepcopy(LIMITS["limits"][2])
        extra["scope"]["model"]["display_name"] = "Fable 4"
        extra["percent"] = 9
        reply["limits"].append(extra)
        p = usage_client.parse(reply)
        self.assertEqual(ids(p), ["session", "weekly", "weekly:fable", "weekly:fable_2"])
        self.assertEqual(p.meters[3].name, "Weekly · Fable 4")

    def test_pct_out_of_range_refused(self):
        for bad in (-1, 101.5, 250, float("nan"), "40", True, None):
            with self.subTest(bad=bad):
                reply = copy.deepcopy(LIMITS)
                reply["limits"][0]["percent"] = bad
                if bad is None:     # a missing percent drops that entry; the rest still parse
                    self.assertEqual(ids(usage_client.parse(reply)), ["weekly", "weekly:fable"])
                else:
                    with self.assertRaises(ReplyChanged):
                        usage_client.parse(reply)
        for bad in (-0.5, 101.01):
            reply = copy.deepcopy(LEGACY)
            reply["five_hour"]["utilization"] = bad
            with self.assertRaises(ReplyChanged):
                usage_client.parse(reply)

    def test_range_edges_accepted(self):
        for ok in (0, 101, 100.5):
            reply = {"limits": [{"kind": "weekly_all", "percent": ok, "resets_at": "2026-12-01T01:00:00+00:00"}]}
            self.assertEqual(usage_client.parse(reply).meters[0].pct, float(ok))

    def test_neither_shape_refused(self):
        for reply in (CHANGED, {}, {"limits": []}, {"limits": [{"kind": "mystery", "percent": 3}]},
                      [], "x", None, {"five_hour": None}):
            with self.subTest(reply=reply):
                with self.assertRaises(ReplyChanged):
                    usage_client.parse(reply)

    def test_naive_or_garbage_reset_refused(self):
        for bad in ("2026-11-27T08:10:00", "soon", 5):
            reply = copy.deepcopy(LIMITS)
            reply["limits"][0]["resets_at"] = bad
            with self.subTest(bad=bad):
                with self.assertRaises(ReplyChanged):
                    usage_client.parse(reply)

    def test_missing_reset_is_none(self):
        reply = copy.deepcopy(LIMITS)
        del reply["limits"][0]["resets_at"]
        self.assertIsNone(usage_client.parse(reply).meters[0].resets_at)

    def test_scoped_limit_without_a_model_name_refused(self):
        reply = copy.deepcopy(LIMITS)
        del reply["limits"][2]["scope"]
        with self.assertRaises(ReplyChanged):
            usage_client.parse(reply)

    def test_flags(self):
        reply = copy.deepcopy(LIMITS)
        reply["extra_usage"] = {"is_enabled": True, "used_credits": 12.5}
        self.assertEqual(usage_client.parse(reply).flags, ["using_credits"])
        reply = copy.deepcopy(LIMITS)
        reply["limits"][1]["severity"] = "reset_available"
        self.assertEqual(usage_client.parse(reply).flags, ["reset_available"])
        reply["extra_usage"] = {"is_enabled": True}
        self.assertEqual(usage_client.parse(reply).flags, ["using_credits", "reset_available"])

    def test_flags_off(self):
        reply = copy.deepcopy(LIMITS)
        reply["extra_usage"] = {"is_enabled": False}
        reply["limits"][1]["severity"] = "ok"
        reply["limits"][2]["severity"] = None
        self.assertEqual(usage_client.parse(reply).flags, [])
        reply["extra_usage"] = None
        self.assertEqual(usage_client.parse(reply).flags, [])


class FetchTests(unittest.TestCase):
    def test_sends_the_documented_headers_and_returns_the_reply(self):
        with support.FakeUsageServer([(200, {}, LIMITS)]) as srv:
            out = usage_client.fetch("tok-123", url=srv.url)
            self.assertEqual(out, LIMITS)
            self.assertEqual(len(srv.requests), 1)
            req = srv.requests[0]
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.headers["Authorization"], "Bearer tok-123")
            self.assertEqual(req.headers["anthropic-beta"], constants.ANTHROPIC_BETA)

    def test_user_agent_version(self):
        version = (constants.REPO_ROOT / "VERSION").read_text().strip()
        with support.FakeUsageServer([(200, {}, LIMITS)]) as srv:
            usage_client.fetch("t", url=srv.url)
            self.assertEqual(srv.requests[0].headers["User-Agent"], f"jusage/{version}")

    def test_default_url_is_the_constant(self):
        import inspect
        self.assertEqual(inspect.signature(usage_client.fetch).parameters["url"].default, constants.USAGE_URL)

    def test_redirect_not_followed(self):
        with support.FakeUsageServer([(200, {}, LIMITS)]) as second:
            with support.FakeUsageServer([(302, {"Location": second.url}, b"")]) as first:
                with self.assertRaises(HttpError) as cm:
                    usage_client.fetch("secret-token", url=first.url)
                self.assertEqual(cm.exception.status, 302)
                self.assertEqual(len(first.requests), 1)
                self.assertEqual(second.requests, [])

    def test_401_is_login_expired(self):
        with support.FakeUsageServer([(401, {}, b"{}")]) as srv:
            with self.assertRaises(LoginError) as cm:
                usage_client.fetch("t", url=srv.url)
            self.assertEqual(cm.exception.kind, "login_expired")
            self.assertEqual(cm.exception.text, "Open Claude Code on this account, then refresh")

    def test_429_retry_after_seconds_and_date(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=300)
        cases = [({"Retry-After": "120"}, 120), ({}, None), ({"Retry-After": "0"}, None),
                 ({"Retry-After": "soon"}, None), ({"Retry-After": "-5"}, None)]
        for headers, want in cases:
            with self.subTest(headers=headers):
                with support.FakeUsageServer([(429, headers, b"{}")]) as srv:
                    with self.assertRaises(RateLimited) as cm:
                        usage_client.fetch("t", url=srv.url)
                    self.assertEqual(cm.exception.retry_after_s, want)
        with support.FakeUsageServer([(429, {"Retry-After": format_datetime(future, usegmt=True)}, b"{}")]) as srv:
            with self.assertRaises(RateLimited) as cm:
                usage_client.fetch("t", url=srv.url)
        self.assertTrue(295 <= cm.exception.retry_after_s <= 300, cm.exception.retry_after_s)

    def test_429_retry_after_date_in_the_past_is_none(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with support.FakeUsageServer([(429, {"Retry-After": format_datetime(past, usegmt=True)}, b"{}")]) as srv:
            with self.assertRaises(RateLimited) as cm:
                usage_client.fetch("t", url=srv.url)
        self.assertIsNone(cm.exception.retry_after_s)

    def test_retry_after_date_uses_the_one_clock(self):
        fixed = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
        hdr = format_datetime(fixed + timedelta(seconds=90), usegmt=True)
        with mock.patch.object(timeutil, "now", return_value=fixed):
            with support.FakeUsageServer([(429, {"Retry-After": hdr}, b"{}")]) as srv:
                with self.assertRaises(RateLimited) as cm:
                    usage_client.fetch("t", url=srv.url)
        self.assertEqual(cm.exception.retry_after_s, 90)

    def test_other_status_is_http_error(self):
        for status in (403, 500, 503):
            with self.subTest(status=status):
                with support.FakeUsageServer([(status, {}, b"x")]) as srv:
                    with self.assertRaises(HttpError) as cm:
                        usage_client.fetch("t", url=srv.url)
                    self.assertEqual(cm.exception.status, status)

    def test_refused_connection_is_network_error(self):
        srv = support.FakeUsageServer([])
        url = srv.url
        srv.close()
        with self.assertRaises(NetworkError):
            usage_client.fetch("t", url=url)

    def test_non_json_body_is_reply_changed(self):
        with support.FakeUsageServer([(200, {}, b"<html>hello</html>")]) as srv:
            with self.assertRaises(ReplyChanged):
                usage_client.fetch("t", url=srv.url)

    def test_timeout_is_network_error(self):
        import socket
        import threading
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        url = f"http://127.0.0.1:{lsock.getsockname()[1]}/api/oauth/usage"
        with mock.patch.object(constants, "HTTP_TIMEOUT_S", 0.3):
            try:
                with self.assertRaises(NetworkError):
                    usage_client.fetch("t", url=url)
            finally:
                lsock.close()

    def test_token_not_in_any_error_text(self):
        for status in (401, 429, 500):
            with support.FakeUsageServer([(status, {}, b"{}")]) as srv:
                try:
                    usage_client.fetch("SECRET-TOKEN-XYZ", url=srv.url)
                except Exception as e:  # noqa: BLE001
                    self.assertNotIn("SECRET-TOKEN-XYZ", str(e))
                    self.assertNotIn("SECRET-TOKEN-XYZ", repr(e))


if __name__ == "__main__":
    unittest.main()
