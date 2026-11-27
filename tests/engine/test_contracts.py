"""E0 contracts: the shared types and the closed set of error kinds."""
import dataclasses
import unittest
from datetime import datetime, timezone

from ju import contracts as K


def fields(cls):
    return [f.name for f in dataclasses.fields(cls)]


class ContractsTests(unittest.TestCase):
    def test_error_kinds_closed_set(self):
        self.assertEqual(K.ERROR_KINDS, frozenset({
            "login_expired", "login_missing", "keychain_waiting", "keychain_denied",
            "rate_limited", "no_network", "http", "reply_changed",
            "identity_changed", "identity_foreign_reset"}))
        self.assertIsInstance(K.ERROR_KINDS, frozenset)

    def test_dataclass_fields_in_contract_order(self):
        self.assertEqual(fields(K.Meter), ["id", "name", "pct", "resets_at", "period_s"])
        self.assertEqual(fields(K.Reading), ["folder", "fetched", "meters", "flags", "uuid", "source"])
        self.assertEqual(fields(K.Pace), ["e", "landing", "state", "forecast", "runs_out", "reset_due", "words"])
        self.assertEqual(fields(K.Login), ["service", "token", "expires_at", "legacy"])
        self.assertEqual(fields(K.GateDecision), ["due", "reason", "next_at"])

    def test_dataclasses_are_frozen_and_have_no_defaults(self):
        for cls in (K.Meter, K.Reading, K.Pace, K.Login, K.GateDecision):
            with self.subTest(cls.__name__):
                for f in dataclasses.fields(cls):
                    self.assertIs(f.default, dataclasses.MISSING)
                    self.assertIs(f.default_factory, dataclasses.MISSING)
                self.assertTrue(cls.__dataclass_params__.frozen)

    def test_construct_and_equality(self):
        t = datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc)
        m = K.Meter("session", "Session", 37.0, t, 18000)
        self.assertEqual(m, K.Meter("session", "Session", 37.0, t, 18000))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            m.pct = 1.0
        r = K.Reading("/abs/.claude", t, [m], ["using_credits"], "u-1", "service")
        self.assertEqual(r.meters[0].id, "session")
        K.Pace(0.41, 90.0, "green", True, None, False, "On pace")
        K.Login("Claude Code-credentials-ab12cd34", "tok", t, False)
        K.GateDecision(True, "due", t)

    def test_login_repr_never_shows_token(self):
        l = K.Login("svc", "sk-ant-oat01-SECRETSECRET", None, False)
        self.assertNotIn("SECRET", repr(l))
        self.assertNotIn("SECRET", str(l))

    def test_login_error_carries_kind_and_text(self):
        e = K.LoginError("login_expired", "Open Claude Code on this account")
        self.assertEqual(e.kind, "login_expired")
        self.assertEqual(e.text, "Open Claude Code on this account")
        self.assertEqual(str(e), "Open Claude Code on this account")

    def test_login_error_refuses_unknown_kind(self):
        with self.assertRaises(ValueError):
            K.LoginError("made_up", "x")

    def test_rate_limited_retry_after(self):
        self.assertIsNone(K.RateLimited(None).retry_after_s)
        self.assertEqual(K.RateLimited(90).retry_after_s, 90)

    def test_http_error_status(self):
        e = K.HttpError(503)
        self.assertEqual(e.status, 503)
        self.assertIn("503", str(e))

    def test_identity_rejected_kinds(self):
        for k in ("identity_changed", "identity_foreign_reset"):
            self.assertEqual(K.IdentityRejected(k).kind, k)
        for bad in ("login_expired", "nope"):
            with self.assertRaises(ValueError):
                K.IdentityRejected(bad)

    def test_bring_up_refused_carries_line(self):
        e = K.BringUpRefused("themes.json: theme 'mono' lacks 'warn'")
        self.assertEqual(e.line, "themes.json: theme 'mono' lacks 'warn'")
        self.assertEqual(str(e), e.line)

    def test_exception_hierarchy(self):
        for cls in (K.ReplyChanged, K.NetworkError):
            self.assertTrue(issubclass(cls, Exception))
        self.assertTrue(issubclass(K.ReplyChanged, K.JusageError))
        for cls in (K.LoginError, K.RateLimited, K.HttpError, K.NetworkError, K.IdentityRejected, K.BringUpRefused):
            self.assertTrue(issubclass(cls, K.JusageError))

    def test_every_error_class_maps_to_a_kind_in_the_closed_set(self):
        self.assertEqual(K.RateLimited(None).kind, "rate_limited")
        self.assertEqual(K.NetworkError().kind, "no_network")
        self.assertEqual(K.HttpError(500).kind, "http")
        self.assertEqual(K.ReplyChanged().kind, "reply_changed")
        for e in (K.RateLimited(None), K.NetworkError(), K.HttpError(500), K.ReplyChanged(),
                  K.LoginError("login_missing", "x"), K.IdentityRejected("identity_changed")):
            self.assertIn(e.kind, K.ERROR_KINDS)


if __name__ == "__main__":
    unittest.main()
