"""E3 files: private, atomic writes and never-overwrite corrupt move-aside."""
import json
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from ju import fsutil
from tests.engine import support


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class FsutilTests(unittest.TestCase):
    def setUp(self):
        self.th = support.temp_home()
        self.addCleanup(self.th.cleanup)
        self.dir = self.th.home / "work"
        self.dir.mkdir()

    def test_ensure_state_dir_creates_0700(self):
        p = self.dir / "a" / "state"
        old = os.umask(0)
        try:
            fsutil.ensure_state_dir(p)
        finally:
            os.umask(old)
        self.assertTrue(p.is_dir())
        self.assertEqual(mode(p), 0o700)

    def test_ensure_state_dir_tightens_existing(self):
        p = self.dir / "loose"
        p.mkdir()
        os.chmod(p, 0o755)
        fsutil.ensure_state_dir(p)
        self.assertEqual(mode(p), 0o700)

    def test_ensure_state_dir_refuses_a_file(self):
        p = self.dir / "afile"
        p.write_text("x")
        with self.assertRaises(NotADirectoryError):
            fsutil.ensure_state_dir(p)

    def test_write_private_atomic_and_0600(self):
        p = self.dir / "live.json"
        old = os.umask(0)
        try:
            fsutil.write_private(p, b'{"a": 1}')
        finally:
            os.umask(old)
        self.assertEqual(p.read_bytes(), b'{"a": 1}')
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["live.json"])

    def test_write_private_tightens_existing_loose_file(self):
        p = self.dir / "c.json"
        p.write_text("old")
        os.chmod(p, 0o644)
        fsutil.write_private(p, b"new")
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(p.read_bytes(), b"new")

    def test_failed_write_keeps_old_content_and_leaves_no_temp(self):
        p = self.dir / "live.json"
        fsutil.write_private(p, b"good")
        with mock.patch("os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                fsutil.write_private(p, b"bad")
        self.assertEqual(p.read_bytes(), b"good")
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["live.json"])

    def test_write_private_refuses_text(self):
        with self.assertRaises(TypeError):
            fsutil.write_private(self.dir / "x", "text")

    def test_move_aside_never_overwrites_earlier_corrupt(self):
        p = self.dir / "live.json"
        names = []
        for i in range(3):
            p.write_text(f"bad{i}")
            names.append(fsutil.move_aside_corrupt(p).name)
            self.assertFalse(p.exists())
        self.assertEqual(names, ["live.json.corrupt", "live.json.corrupt.1", "live.json.corrupt.2"])
        self.assertEqual((self.dir / "live.json.corrupt").read_text(), "bad0")
        self.assertEqual((self.dir / "live.json.corrupt.1").read_text(), "bad1")
        self.assertEqual((self.dir / "live.json.corrupt.2").read_text(), "bad2")

    def test_move_aside_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            fsutil.move_aside_corrupt(self.dir / "nope.json")

    def test_read_json_returns_dict(self):
        p = self.dir / "a.json"
        p.write_text(json.dumps({"k": [1, 2]}))
        self.assertEqual(fsutil.read_json(p), {"k": [1, 2]})

    def test_read_json_corrupt_cases(self):
        p = self.dir / "a.json"
        for body in (b"{not json", b"", b"[1, 2]", b'"str"', b"\xff\xfe\x00"):
            with self.subTest(body=body):
                p.write_bytes(body)
                with self.assertRaises(fsutil.CorruptFile) as cm:
                    fsutil.read_json(p)
                self.assertEqual(Path(cm.exception.path), p)

    def test_read_json_missing_is_not_corrupt(self):
        with self.assertRaises(FileNotFoundError):
            fsutil.read_json(self.dir / "nope.json")


if __name__ == "__main__":
    unittest.main()
