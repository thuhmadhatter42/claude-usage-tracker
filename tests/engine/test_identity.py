"""E12 identity: uuid stability and the weekly-reset fingerprint (R-13, R-14, R-26)."""
import unittest
from datetime import datetime, timedelta, timezone

from ju import identity
from ju.contracts import IdentityRejected

BASE = datetime(2026, 11, 30, 17, 30, 0, tzinfo=timezone.utc)   # a Monday


def moment(minutes=0, seconds=0):
    return BASE + timedelta(minutes=minutes, seconds=seconds)


def mow(d):
    return d.weekday() * 1440 + d.hour * 60 + d.minute


class StableTests(unittest.TestCase):
    def test_uuid_change_mid_read_discarded(self):
        with self.assertRaises(IdentityRejected) as cm:
            identity.check_stable("aaaa", "bbbb")
        self.assertEqual(cm.exception.kind, "identity_changed")

    def test_same_uuid_passes(self):
        identity.check_stable("aaaa", "aaaa")
        identity.check_stable(None, None)

    def test_appearing_or_vanishing_uuid_fails_closed(self):
        for before, after in (("aaaa", None), (None, "aaaa")):
            with self.subTest(before=before, after=after):
                with self.assertRaises(IdentityRejected):
                    identity.check_stable(before, after)


class FingerprintTests(unittest.TestCase):
    def test_new_anchor_is_empty(self):
        self.assertEqual(identity.new_anchor(),
                         {"minute_of_week": None, "confirmations": 0, "candidate": None, "candidate_count": 0})

    def test_learned_after_two_matches(self):
        a = identity.new_anchor()
        a, v = identity.observe(a, moment(), {})
        self.assertEqual(v, "accept")
        self.assertIsNone(a["minute_of_week"])
        a, v = identity.observe(a, moment(0, 30), {})
        self.assertEqual(v, "accept")
        self.assertEqual(a["minute_of_week"], mow(moment()))
        self.assertIsNone(a["candidate"])

    def test_observe_does_not_mutate_its_input(self):
        a = identity.new_anchor()
        before = dict(a)
        identity.observe(a, moment(), {})
        self.assertEqual(a, before)

    def test_foreign_reset_rejected(self):
        own = mow(moment(600))
        other = mow(moment())
        a = {"minute_of_week": own, "confirmations": 5, "candidate": None, "candidate_count": 0}
        a2, v = identity.observe(a, moment(), {"~/other": other})
        self.assertEqual(v, "reject_foreign")
        self.assertEqual(a2, a)

    def test_foreign_reset_rejected_before_own_is_learned(self):
        a, v = identity.observe(identity.new_anchor(), moment(), {"~/other": mow(moment())})
        self.assertEqual(v, "reject_foreign")
        self.assertIsNone(a["candidate"])

    def test_own_match_wins_when_another_account_shares_the_minute(self):
        m = mow(moment())
        a = {"minute_of_week": m, "confirmations": 1, "candidate": None, "candidate_count": 0}
        a2, v = identity.observe(a, moment(), {"~/other": m})
        self.assertEqual(v, "accept")
        self.assertEqual(a2["confirmations"], 2)

    def test_relearn_after_two_matching(self):
        old = mow(moment(600))
        a = {"minute_of_week": old, "confirmations": 9, "candidate": None, "candidate_count": 0}
        a, v = identity.observe(a, moment(), {})
        self.assertEqual((v, a["minute_of_week"]), ("accept", old))
        self.assertEqual((a["candidate"], a["candidate_count"]), (mow(moment()), 1))
        a, v = identity.observe(a, moment(1), {})
        self.assertEqual(v, "accept")
        self.assertEqual(a["minute_of_week"], mow(moment()))
        self.assertIsNone(a["candidate"])
        self.assertEqual(a["candidate_count"], 0)

    def test_disagreeing_candidates_restart_the_count(self):
        old = mow(moment(600))
        a = {"minute_of_week": old, "confirmations": 9, "candidate": None, "candidate_count": 0}
        a, _ = identity.observe(a, moment(), {})
        a, _ = identity.observe(a, moment(300), {})
        self.assertEqual(a["minute_of_week"], old)
        self.assertEqual((a["candidate"], a["candidate_count"]), (mow(moment(300)), 1))

    def test_returning_to_the_learned_minute_clears_the_candidate(self):
        old = mow(moment(600))
        a = {"minute_of_week": old, "confirmations": 9, "candidate": mow(moment()), "candidate_count": 1}
        a, v = identity.observe(a, moment(600), {})
        self.assertEqual(v, "accept")
        self.assertIsNone(a["candidate"])
        self.assertEqual(a["candidate_count"], 0)

    def test_candidate_equal_to_another_account_is_never_learned(self):
        old = mow(moment(600))
        a = {"minute_of_week": old, "confirmations": 9, "candidate": None, "candidate_count": 0}
        for _ in range(4):
            a, v = identity.observe(a, moment(), {"~/other": mow(moment())})
            self.assertEqual(v, "reject_foreign")
        self.assertEqual(a["minute_of_week"], old)

    def test_tolerance_120s(self):
        m = mow(moment())
        a = {"minute_of_week": m, "confirmations": 3, "candidate": None, "candidate_count": 0}
        other = {"~/other": mow(moment(600))}
        # two minutes either side still matches the account's own anchor
        for d in (-2, -1, 0, 1, 2):
            a2, v = identity.observe(a, moment(d), other)
            self.assertEqual((v, a2["candidate"]), ("accept", None), d)
        # three minutes away is a different time: a candidate, not a match
        a3, v = identity.observe(a, moment(3), other)
        self.assertEqual(a3["candidate"], mow(moment(3)))
        # and a reading two minutes from another account's learned minute is that account's
        _, v = identity.observe(a, moment(602), {"~/other": mow(moment(600))})
        self.assertEqual(v, "reject_foreign")
        _, v = identity.observe(a, moment(603), {"~/other": mow(moment(600))})
        self.assertEqual(v, "accept")

    def test_tolerance_wraps_across_the_week_boundary(self):
        monday_start = datetime(2026, 11, 30, 0, 0, tzinfo=timezone.utc)
        sunday_end = monday_start - timedelta(minutes=1)
        a = {"minute_of_week": mow(sunday_end), "confirmations": 3, "candidate": None, "candidate_count": 0}
        a2, v = identity.observe(a, monday_start + timedelta(minutes=1), {})
        self.assertEqual((v, a2["candidate"]), ("accept", None))

    def test_no_weekly_reset_changes_nothing(self):
        a = identity.new_anchor()
        self.assertEqual(identity.observe(a, None, {"~/other": 5}), (a, "accept"))

    def test_naive_reset_refused(self):
        from ju import timeutil
        with self.assertRaises(timeutil.NaiveTime):
            identity.observe(identity.new_anchor(), datetime(2026, 11, 30, 1, 0), {})


class ForeignMatch(unittest.TestCase):
    def test_names_the_account_whose_minute_matches(self):
        """RULING 31: the gate records which account a foreign reset belongs to."""
        others = {"~/b": mow(moment(600)), "~/a": mow(moment())}
        self.assertEqual(identity.foreign_match(moment(), others), "~/a")
        self.assertIsNone(identity.foreign_match(moment(3000), others))
        self.assertIsNone(identity.foreign_match(None, others))


if __name__ == "__main__":
    unittest.main()
