"""E15: status-line capture, on/off wrap, and the identity-checked backup reading.
Every test uses temp settings.json files under support.temp_home; no real config is touched."""
import json
import os
import stat
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.engine import support
from ju import accounts, constants, identity, statusline, timeutil
from ju.config import ConfigStore
from ju.contracts import Meter, Reading
from ju.live_store import AccountLive, ErrorInfo
from ju.statusline import StatuslineRefused

NOW = datetime(2026, 11, 26, 14, 30, tzinfo=timezone.utc)
SESSION_RESET = NOW + timedelta(hours=2)
WEEK_RESET = datetime(2026, 12, 1, 14, 0, tzinfo=timezone.utc)      # a Tuesday
ORIGINAL = '~/.claude/hooks/statusline-account.sh --flag "two words"'


class Base(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.state = self.th.state
        self.folder = self.th.home / ".claude"
        self.folder.mkdir()
        self.key = accounts.folder_key(self.folder)
        self.python = "/usr/bin/python3"
        self.engine = Path(constants.HERE).parent

    def settings(self, folder=None):
        return (folder or self.folder) / "settings.json"

    def write_settings(self, data, folder=None, raw=None):
        p = self.settings(folder)
        p.write_text(raw if raw is not None else json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return p

    def on(self, key=None):
        return statusline.turn_on(self.state, key or self.key, python=self.python, engine_dir=self.engine, now=NOW)

    def payload(self, **extra):
        doc = {
            "session_id": "abc", "model": {"display_name": "Opus", "id": "claude-opus"},
            "prompt": "SECRET PROMPT", "workspace": {"current_dir": "/work/secret"},
            "transcript_path": str(self.folder / "projects" / "-work-x" / "abc.jsonl"),
            "rate_limits": {
                "five_hour": {"used_percentage": 37.5, "resets_at": int(SESSION_RESET.timestamp())},
                "seven_day": {"used_percentage": 12, "resets_at": int(WEEK_RESET.timestamp())},
            },
        }
        doc.update(extra)
        return json.dumps(doc).encode()

    def env(self, **kv):
        e = {"HOME": str(self.th.home), "CLAUDE_CONFIG_DIR": str(self.folder)}
        e.update(kv)
        return e


class CaptureTests(Base):
    def test_capture_fields_only(self):
        statusline.capture(self.payload(), self.env(), state=self.state, now=NOW)
        files = list((self.state / "statusline").glob("*.json"))
        self.assertEqual([f.name for f in files], [statusline.capture_name(self.key)])
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        doc = json.loads(files[0].read_text())
        self.assertEqual(set(doc), {"folder", "captured_at", "config_dir_present", "rate_limits", "spellings"})
        self.assertEqual(doc["folder"], self.key)
        self.assertTrue(doc["config_dir_present"])
        self.assertEqual(set(doc["rate_limits"]), {"five_hour", "seven_day"})
        self.assertEqual(set(doc["rate_limits"]["five_hour"]), {"used_percentage", "resets_at"})
        self.assertEqual(doc["rate_limits"]["five_hour"]["used_percentage"], 37.5)
        self.assertEqual(timeutil.parse_iso(doc["rate_limits"]["five_hour"]["resets_at"]), SESSION_RESET)
        self.assertEqual(timeutil.parse_iso(doc["captured_at"]), NOW)
        text = files[0].read_text()
        for leaked in ("SECRET PROMPT", "Opus", "/work/secret", "abc"):
            self.assertNotIn(leaked, text)

    def test_name_is_sha8_of_folder_key(self):
        import hashlib
        self.assertEqual(statusline.capture_name(self.key),
                         hashlib.sha256(self.key.encode()).hexdigest()[:8] + ".json")

    def test_folder_key_is_the_accounts_folder_key(self):
        link = self.th.home / "link"
        link.symlink_to(self.folder)
        for p in (self.folder, str(self.folder) + "/", link, "~/.claude", "~/nope/../.claude"):
            self.assertEqual(statusline.folder_key(p), accounts.folder_key(p))

    def test_folder_from_transcript_when_no_config_dir(self):
        env = {"HOME": str(self.th.home)}
        statusline.capture(self.payload(), env, state=self.state, now=NOW)
        doc = statusline.read_capture(self.state, self.key)
        self.assertEqual(doc["folder"], self.key)
        self.assertFalse(doc["config_dir_present"])

    def test_config_dir_and_transcript_disagree_refused(self):
        other = self.th.home / ".claude-newaccount"
        other.mkdir()
        with self.assertRaises(StatuslineRefused):
            statusline.capture(self.payload(), self.env(CLAUDE_CONFIG_DIR=str(other)), state=self.state, now=NOW)
        self.assertFalse((self.state / "statusline").exists() and list((self.state / "statusline").glob("*.json")))

    def test_no_folder_derivable_refused(self):
        with self.assertRaises(StatuslineRefused):
            statusline.capture(self.payload(transcript_path="/elsewhere/x.jsonl"), {"HOME": str(self.th.home)},
                               state=self.state, now=NOW)

    def test_not_json_refused(self):
        with self.assertRaises(StatuslineRefused):
            statusline.capture(b"not json", self.env(), state=self.state, now=NOW)

    def test_no_rate_limits_writes_nothing_and_keeps_previous(self):
        statusline.capture(self.payload(), self.env(), state=self.state, now=NOW)
        before = statusline.read_capture(self.state, self.key)
        statusline.capture(json.dumps({"session_id": "x"}).encode(), self.env(), state=self.state,
                           now=NOW + timedelta(minutes=1))
        self.assertEqual(statusline.read_capture(self.state, self.key), before)

    def test_one_window_only_is_stored_as_is(self):
        doc = json.loads(self.payload())
        del doc["rate_limits"]["seven_day"]
        statusline.capture(json.dumps(doc).encode(), self.env(), state=self.state, now=NOW)
        self.assertEqual(set(statusline.read_capture(self.state, self.key)["rate_limits"]), {"five_hour"})

    def test_malformed_percentage_refused(self):
        doc = json.loads(self.payload())
        doc["rate_limits"]["five_hour"]["used_percentage"] = "lots"
        with self.assertRaises(StatuslineRefused):
            statusline.capture(json.dumps(doc).encode(), self.env(), state=self.state, now=NOW)

    def test_iso_reset_accepted(self):
        doc = json.loads(self.payload())
        doc["rate_limits"]["five_hour"]["resets_at"] = "2026-11-26T16:30:00Z"
        statusline.capture(json.dumps(doc).encode(), self.env(), state=self.state, now=NOW)
        got = statusline.read_capture(self.state, self.key)["rate_limits"]["five_hour"]["resets_at"]
        self.assertEqual(timeutil.parse_iso(got), SESSION_RESET)

    def test_spelling_hints_go_to_config(self):
        env = self.env(CLAUDE_SECURESTORAGE_CONFIG_DIR="/some/spelling/")
        statusline.capture(self.payload(), env, state=self.state, now=NOW)
        cfg = ConfigStore.load(self.state)
        self.assertEqual(cfg.spellings[self.key]["config_dir"], str(self.folder))
        self.assertEqual(cfg.spellings[self.key]["securestorage"], "/some/spelling/")

    def test_unchanged_spelling_does_not_take_the_config_lock(self):
        statusline.capture(self.payload(), self.env(), state=self.state, now=NOW)
        before = (self.state / "config.json").stat().st_mtime_ns
        statusline.capture(self.payload(), self.env(), state=self.state, now=NOW)
        self.assertEqual((self.state / "config.json").stat().st_mtime_ns, before)


class OnOffTests(Base):
    def test_on_off_restores_exact_string(self):
        self.write_settings({"theme": "dark", "statusLine": {"type": "command", "command": ORIGINAL, "padding": 2},
                             "other": [1, 2]})
        before = json.loads(self.settings().read_text())
        wrapped = self.on()
        cur = json.loads(self.settings().read_text())
        self.assertEqual(cur["theme"], "dark")
        self.assertEqual(cur["other"], [1, 2])
        self.assertEqual(cur["statusLine"]["padding"], 2)
        self.assertEqual(cur["statusLine"]["command"], wrapped)
        self.assertIn("jusage-statusline", wrapped)
        self.assertTrue(wrapped.startswith(f"{self.python} -B -E -s "))     # RULINGS 51: -B, no .pyc
        rec = ConfigStore.load(self.state).statusline[self.key]
        self.assertEqual(rec["original"], ORIGINAL)
        self.assertTrue(rec["enabled"])
        self.assertEqual(rec["wrapped"], wrapped)
        statusline.turn_off(self.state, self.key)
        self.assertEqual(json.loads(self.settings().read_text()), before)
        self.assertEqual(json.loads(self.settings().read_text())["statusLine"]["command"], ORIGINAL)
        self.assertFalse(ConfigStore.load(self.state).statusline[self.key]["enabled"])

    def test_string_shaped_statusline_restored_as_string(self):
        self.write_settings({"statusLine": ORIGINAL})
        self.on()
        self.assertIsInstance(json.loads(self.settings().read_text())["statusLine"], str)
        statusline.turn_off(self.state, self.key)
        self.assertEqual(json.loads(self.settings().read_text())["statusLine"], ORIGINAL)

    def test_wrapped_command_passes_original_as_one_shell_word(self):
        import shlex
        self.write_settings({"statusLine": {"type": "command", "command": ORIGINAL}})
        wrapped = self.on()
        words = shlex.split(wrapped)
        self.assertEqual(words[-2:], ["--", ORIGINAL])

    def test_no_original_wrapper_has_nothing_after_dashes_and_off_removes_key(self):
        self.write_settings({"theme": "dark"})
        wrapped = self.on()
        self.assertTrue(wrapped.endswith(" --"))
        self.assertEqual(json.loads(self.settings().read_text())["statusLine"]["command"], wrapped)
        statusline.turn_off(self.state, self.key)
        self.assertEqual(json.loads(self.settings().read_text()), {"theme": "dark"})

    def test_missing_settings_file_created_then_off_leaves_empty_object(self):
        self.assertFalse(self.settings().exists())
        self.on()
        self.assertTrue(self.settings().exists())
        statusline.turn_off(self.state, self.key)
        self.assertEqual(json.loads(self.settings().read_text()), {})

    def test_archive_copy_before_edit(self):
        p = self.write_settings({"statusLine": {"type": "command", "command": ORIGINAL}})
        original_bytes = p.read_bytes()
        self.on()
        copies = list((self.state / "archive").glob("settings-*.json"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), original_bytes)
        self.assertTrue(copies[0].name.startswith("settings-" + statusline.sha8(self.key) + "-"))

    def test_file_mode_and_symlink_preserved(self):
        real = self.th.home / "real-settings.json"
        real.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo hi"}}))
        os.chmod(real, 0o644)
        link = self.settings()
        link.symlink_to(real)
        self.on()
        self.assertTrue(link.is_symlink())
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o644)
        self.assertIn("jusage-statusline", json.loads(real.read_text())["statusLine"]["command"])

    def test_indent_and_trailing_newline_kept(self):
        raw = '{\n    "a": 1,\n    "statusLine": {"type": "command", "command": "echo hi"}\n}\n'
        self.write_settings(None, raw=raw)
        self.on()
        text = self.settings().read_text()
        self.assertTrue(text.startswith('{\n    "a": 1,\n'))
        self.assertTrue(text.endswith("}\n"))

    def test_unparsable_settings_refused_untouched(self):
        bad = '{"statusLine": '
        p = self.write_settings(None, raw=bad)
        with self.assertRaises(StatuslineRefused) as cm:
            self.on()
        self.assertIn("is not valid JSON; not touched", str(cm.exception))
        self.assertIn(str(p), str(cm.exception))
        self.assertEqual(p.read_text(), bad)
        self.assertFalse((self.state / "archive").exists() and list((self.state / "archive").iterdir()))
        self.assertNotIn(self.key, ConfigStore.load(self.state).statusline)

    def test_top_level_not_object_refused(self):
        p = self.write_settings(None, raw="[1,2]")
        with self.assertRaises(StatuslineRefused):
            self.on()
        self.assertEqual(p.read_text(), "[1,2]")

    def test_statusline_of_unknown_shape_refused(self):
        p = self.write_settings({"statusLine": {"type": "other", "x": 1}})
        before = p.read_bytes()
        with self.assertRaises(StatuslineRefused):
            self.on()
        self.assertEqual(p.read_bytes(), before)

    def test_off_refuses_when_command_changed_by_someone_else(self):
        self.write_settings({"statusLine": {"type": "command", "command": ORIGINAL}})
        self.on()
        doc = json.loads(self.settings().read_text())
        doc["statusLine"]["command"] = "echo someone else"
        self.write_settings(doc)
        before = self.settings().read_bytes()
        with self.assertRaises(StatuslineRefused) as cm:
            statusline.turn_off(self.state, self.key)
        self.assertIn("changed", str(cm.exception))
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertTrue(ConfigStore.load(self.state).statusline[self.key]["enabled"])

    def test_off_when_never_on_refused(self):
        self.write_settings({})
        with self.assertRaises(StatuslineRefused):
            statusline.turn_off(self.state, self.key)

    def test_on_twice_is_a_no_op(self):
        self.write_settings({"statusLine": {"type": "command", "command": ORIGINAL}})
        first = self.on()
        before = self.settings().read_bytes()
        self.assertEqual(self.on(), first)
        self.assertEqual(self.settings().read_bytes(), before)
        statusline.turn_off(self.state, self.key)
        self.assertEqual(json.loads(self.settings().read_text())["statusLine"]["command"], ORIGINAL)

    def test_foreign_wrapper_not_nested(self):
        self.write_settings({"statusLine": {"type": "command", "command": "py /x/jusage-statusline -- echo"}})
        with self.assertRaises(StatuslineRefused):
            self.on()

    def test_no_python_refused(self):
        self.write_settings({})
        with self.assertRaises(StatuslineRefused):
            statusline.turn_on(self.state, self.key, engine_dir=self.engine, now=NOW)

    def test_python_from_config(self):
        self.write_settings({})
        with ConfigStore.edit(self.state) as cfg:
            cfg.python = self.python
        wrapped = statusline.turn_on(self.state, self.key, engine_dir=self.engine, now=NOW)
        self.assertTrue(wrapped.startswith(self.python))

    def test_two_accounts_independent(self):
        other = self.th.home / ".claude-newaccount"
        other.mkdir()
        okey = accounts.folder_key(other)
        self.write_settings({"statusLine": {"type": "command", "command": "A"}})
        self.write_settings({"statusLine": {"type": "command", "command": "B"}}, folder=other)
        self.on()
        self.on(okey)
        statusline.turn_off(self.state, self.key)
        self.assertEqual(json.loads(self.settings().read_text())["statusLine"]["command"], "A")
        self.assertIn("jusage-statusline", json.loads(self.settings(other).read_text())["statusLine"]["command"])
        rec = ConfigStore.load(self.state).statusline
        self.assertFalse(rec[self.key]["enabled"])
        self.assertTrue(rec[okey]["enabled"])

    def test_status_reports_enabled_and_capture_at(self):
        self.write_settings({})
        self.assertEqual(statusline.status(self.state)[self.key] if self.key in statusline.status(self.state) else
                         {"enabled": False, "capture_at": None}, {"enabled": False, "capture_at": None})
        self.on()
        s = statusline.status(self.state)
        self.assertEqual(s[self.key], {"enabled": True, "capture_at": None})
        statusline.capture(self.payload(), self.env(), state=self.state, now=NOW)
        s = statusline.status(self.state, self.key)
        self.assertEqual(timeutil.parse_iso(s[self.key]["capture_at"]), NOW)


class BackupTests(Base):
    def capture_doc(self, age_s=60, five=37.5, seven=12.0, week_reset=WEEK_RESET, folder=None, extra=None):
        doc = {
            "folder": folder or self.key, "captured_at": timeutil.iso(NOW - timedelta(seconds=age_s)),
            "config_dir_present": True, "spellings": {"config_dir": None, "securestorage": None},
            "rate_limits": {
                "five_hour": {"used_percentage": five, "resets_at": timeutil.iso(SESSION_RESET)},
                "seven_day": {"used_percentage": seven, "resets_at": timeutil.iso(week_reset)},
            },
        }
        if extra:
            doc["rate_limits"].update(extra)
        return doc

    def failing(self, minute=None, uuid="u-1"):
        anchor = identity.new_anchor()
        anchor["minute_of_week"] = timeutil.minute_of_week(WEEK_RESET) if minute is None else minute
        return AccountLive(error=ErrorInfo("rate_limited", "429", NOW - timedelta(minutes=1)),
                           reset_anchor=anchor, identity_uuid=uuid)

    def test_accepted_reading_shape(self):
        r, why = statusline.backup_reading(self.failing(), self.capture_doc(), {}, NOW, key=self.key)
        self.assertIsNotNone(r, why)
        self.assertEqual(r.source, "statusline")
        self.assertEqual(r.folder, self.key)
        self.assertEqual(r.uuid, "u-1")
        self.assertEqual(r.fetched, NOW - timedelta(seconds=60))
        self.assertEqual([m.id for m in r.meters], ["session", "weekly"])
        self.assertEqual([m.pct for m in r.meters], [37.5, 12.0])
        self.assertEqual([m.period_s for m in r.meters], [constants.SESSION_S, constants.WEEK_S])
        self.assertEqual(r.meters[1].resets_at, WEEK_RESET)

    def test_gated_by_blocked_until_counts_as_failing(self):
        acc = AccountLive(blocked_until=NOW + timedelta(minutes=5), reset_anchor=self.failing().reset_anchor)
        r, _ = statusline.backup_reading(acc, self.capture_doc(), {}, NOW, key=self.key)
        self.assertIsNotNone(r)

    def test_healthy_service_means_no_backup(self):
        acc = AccountLive(fetched=NOW, data={"x": 1}, reset_anchor=self.failing().reset_anchor)
        r, why = statusline.backup_reading(acc, self.capture_doc(), {}, NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("service", why)

    def test_primary_capture_accepted_while_the_service_is_healthy(self):
        """RULING 28: with the backup on, an accepted capture is the reading even when the service is fine;
        every identity check still applies."""
        acc = AccountLive(fetched=NOW - timedelta(minutes=5), data={"x": 1}, reset_anchor=self.failing().reset_anchor,
                          identity_uuid="u-1")
        r, why = statusline.backup_reading(acc, self.capture_doc(), {}, NOW, key=self.key, primary=True)
        self.assertIsNotNone(r, why)
        self.assertEqual(r.source, "statusline")
        mine = timeutil.minute_of_week(WEEK_RESET)
        r, why = statusline.backup_reading(acc, self.capture_doc(), {"/other": mine}, NOW, key=self.key, primary=True)
        self.assertIsNotNone(r, "own learned minute wins")
        odd = WEEK_RESET + timedelta(hours=7)
        r, why = statusline.backup_reading(acc, self.capture_doc(week_reset=odd), {}, NOW, key=self.key, primary=True)
        self.assertIsNone(r)
        r, why = statusline.backup_reading(acc, self.capture_doc(folder="/elsewhere"), {}, NOW, key=self.key, primary=True)
        self.assertIsNone(r)

    def test_foreign_reading_68772_ignored(self):
        mine = timeutil.minute_of_week(WEEK_RESET)
        other_reset = WEEK_RESET + timedelta(days=1, hours=3)
        other_min = timeutil.minute_of_week(other_reset)
        r, why = statusline.backup_reading(self.failing(), self.capture_doc(week_reset=other_reset),
                                           {"/abs/.claude-newaccount": other_min}, NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("another account", why)
        self.assertNotEqual(mine, other_min)

    def test_reset_matching_nobody_also_ignored(self):
        odd = WEEK_RESET + timedelta(hours=5)
        r, why = statusline.backup_reading(self.failing(), self.capture_doc(week_reset=odd), {}, NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("does not match", why)

    def test_capture_for_other_folder_ignored(self):
        r, why = statusline.backup_reading(self.failing(), self.capture_doc(folder="/abs/.claude-newaccount"), {},
                                           NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("folder", why)

    def test_unlearned_anchor_never_trusted(self):
        acc = AccountLive(error=ErrorInfo("rate_limited", "429", NOW), reset_anchor=identity.new_anchor())
        r, why = statusline.backup_reading(acc, self.capture_doc(), {}, NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("learned", why)

    def test_anchor_tolerance_120s(self):
        for delta, ok in ((120, True), (121 + 60, False)):
            r, _ = statusline.backup_reading(self.failing(), self.capture_doc(
                week_reset=WEEK_RESET + timedelta(seconds=delta)), {}, NOW, key=self.key)
            self.assertEqual(r is not None, ok, delta)

    def test_capture_older_than_15min_ignored(self):
        r, _ = statusline.backup_reading(self.failing(), self.capture_doc(age_s=899), {}, NOW, key=self.key)
        self.assertIsNotNone(r)
        r, why = statusline.backup_reading(self.failing(), self.capture_doc(age_s=900), {}, NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("old", why)

    def test_capture_from_the_future_ignored(self):
        r, why = statusline.backup_reading(self.failing(), self.capture_doc(age_s=-3600), {}, NOW, key=self.key)
        self.assertIsNone(r)

    def test_percent_range_0_to_101(self):
        for pct, ok in ((0, True), (101, True), (101.1, False), (-0.1, False)):
            r, _ = statusline.backup_reading(self.failing(), self.capture_doc(five=pct), {}, NOW, key=self.key)
            self.assertEqual(r is not None, ok, pct)

    def test_per_model_weekly_not_covered(self):
        extra = {"seven_day_opus": {"used_percentage": 50, "resets_at": timeutil.iso(WEEK_RESET)}}
        r, _ = statusline.backup_reading(self.failing(), self.capture_doc(extra=extra), {}, NOW, key=self.key)
        self.assertEqual([m.id for m in r.meters], ["session", "weekly"])

    def test_no_weekly_in_capture_cannot_be_identity_checked(self):
        doc = self.capture_doc()
        del doc["rate_limits"]["seven_day"]
        r, why = statusline.backup_reading(self.failing(), doc, {}, NOW, key=self.key)
        self.assertIsNone(r)
        self.assertIn("weekly", why)

    def test_no_capture_at_all(self):
        r, why = statusline.backup_reading(self.failing(), None, {}, NOW, key=self.key)
        self.assertIsNone(r)

    def test_disagree_thresholds(self):
        def reading(pct, reset):
            return Reading(self.key, NOW, [Meter("session", "Session", pct, reset, constants.SESSION_S)], [], None,
                           "service")
        base = reading(40.0, SESSION_RESET)
        self.assertFalse(statusline.disagree(base, reading(45.0, SESSION_RESET)))
        self.assertTrue(statusline.disagree(base, reading(45.1, SESSION_RESET)))
        self.assertTrue(statusline.disagree(base, reading(34.9, SESSION_RESET)))
        self.assertFalse(statusline.disagree(base, reading(40.0, SESSION_RESET + timedelta(seconds=120))))
        self.assertTrue(statusline.disagree(base, reading(40.0, SESSION_RESET + timedelta(seconds=121))))
        self.assertFalse(statusline.disagree(base, reading(40.0, SESSION_RESET - timedelta(seconds=120))))
        self.assertTrue(statusline.disagree(base, reading(40.0, SESSION_RESET - timedelta(seconds=121))))

    def test_disagree_ignores_meters_missing_on_one_side(self):
        a = Reading(self.key, NOW, [Meter("weekly:opus", "x", 90.0, WEEK_RESET, constants.WEEK_S)], [], None, "service")
        b = Reading(self.key, NOW, [Meter("session", "Session", 1.0, SESSION_RESET, constants.SESSION_S)], [], None,
                    "statusline")
        self.assertFalse(statusline.disagree(a, b))


if __name__ == "__main__":
    unittest.main()
