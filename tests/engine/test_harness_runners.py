"""T1: the two runners (tests/js/run.js, tests/run_all.sh) fail loudly and pass quietly."""
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_JS = REPO / "tests" / "js" / "run.js"
RUN_ALL = REPO / "tests" / "run_all.sh"
HAVE_OSASCRIPT = shutil.which("osascript") is not None


def write(p: Path, text: str, mode=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))
    if mode:
        p.chmod(mode)
    return p


@unittest.skipUnless(HAVE_OSASCRIPT, "osascript (JavaScriptCore) needed")
class RunJsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jusage-runjs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_js(self, *args):
        r = subprocess.run(["osascript", "-l", "JavaScript", str(RUN_JS), *args],
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.strip().splitlines(), r.stderr

    def test_passing_tests_end_with_all_pass(self):
        write(self.tmp / "test_a.js", 'test("one", function () { assertEqual(1 + 1, 2); });')
        rc, lines, _ = self.run_js("--tests", str(self.tmp), "--dash", str(self.tmp / "none.js"))
        self.assertEqual(rc, 0)
        self.assertEqual(lines[-1], "ALL PASS")

    def test_a_failing_test_ends_with_fail_and_exits_1(self):
        write(self.tmp / "test_a.js", '''
            test("good", function () {});
            test("bad", function () { assertEqual(1, 2); });''')
        rc, lines, _ = self.run_js("--tests", str(self.tmp), "--dash", str(self.tmp / "none.js"))
        self.assertEqual(rc, 1)
        self.assertEqual(lines[-1], "FAIL")
        self.assertTrue(any(l.startswith("FAIL test_a.js :: bad") for l in lines))
        self.assertTrue(any(l.startswith("ok   test_a.js :: good") for l in lines))

    def test_a_syntax_error_in_a_test_file_fails(self):
        write(self.tmp / "test_a.js", "this is not javascript (")
        rc, lines, _ = self.run_js("--tests", str(self.tmp), "--dash", str(self.tmp / "none.js"))
        self.assertEqual(rc, 1)
        self.assertEqual(lines[-1], "FAIL")

    def test_dash_js_is_loaded_into_the_global_scope(self):
        write(self.tmp / "dash.js", "function fmtDouble(x) { return x * 2; }")
        write(self.tmp / "t" / "test_a.js", 'test("uses dash", function () { assertEqual(fmtDouble(21), 42); });')
        rc, lines, _ = self.run_js("--tests", str(self.tmp / "t"), "--dash", str(self.tmp / "dash.js"))
        self.assertEqual((rc, lines[-1]), (0, "ALL PASS"))

    def test_a_dash_js_that_throws_fails(self):
        write(self.tmp / "dash.js", 'throw new Error("boom");')
        write(self.tmp / "t" / "test_a.js", 'test("x", function () {});')
        rc, lines, _ = self.run_js("--tests", str(self.tmp / "t"), "--dash", str(self.tmp / "dash.js"))
        self.assertEqual((rc, lines[-1]), (1, "FAIL"))

    def test_reading_a_missing_file_fails_the_test(self):
        write(self.tmp / "test_a.js", 'test("x", function () { readText("tests/fixtures/nope.json"); });')
        rc, lines, _ = self.run_js("--tests", str(self.tmp), "--dash", str(self.tmp / "none.js"))
        self.assertEqual((rc, lines[-1]), (1, "FAIL"))

    def test_empty_tests_dir_is_all_pass_with_a_count(self):
        (self.tmp / "t").mkdir()
        rc, lines, _ = self.run_js("--tests", str(self.tmp / "t"), "--dash", str(self.tmp / "none.js"))
        self.assertEqual((rc, lines[-1]), (0, "ALL PASS"))
        self.assertIn("js: 0 passed, 0 failed, 0 tests in 0 files", lines)

    def test_unknown_argument_fails(self):
        rc, lines, _ = self.run_js("--bogus")
        self.assertEqual((rc, lines[-1]), (1, "FAIL"))

    def test_real_suite_passes(self):
        rc, lines, _ = self.run_js()
        self.assertEqual((rc, lines[-1]), (0, "ALL PASS"))


class RunAllTests(unittest.TestCase):
    """run_all.sh with JUSAGE_ROOT pointing at a throwaway tree, so each suite can be made to pass or fail."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jusage-runall-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        write(self.root / "tests" / "__init__.py", "")
        write(self.root / "tests" / "engine" / "__init__.py", "")
        self.bin = self.tmp / "bin"
        self.swift_log = self.tmp / "swift.log"
        write(self.bin / "swift", f'''#!/bin/bash
            echo "$@" >> {self.swift_log}
            exit "${{FAKE_SWIFT_RC:-0}}"
            ''', 0o755)

    def run_all(self, **env):
        e = dict(os.environ, JUSAGE_ROOT=str(self.root), PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", **env)
        r = subprocess.run(["bash", str(RUN_ALL)], capture_output=True, text=True, env=e, timeout=300)
        return r.returncode, r.stdout.strip().splitlines(), r.stdout + r.stderr

    def engine_test(self, name, body):
        write(self.root / "tests" / "engine" / f"test_{name}.py", f'''
            import unittest
            class T(unittest.TestCase):
                def test_it(self):
                    {body}
            ''')

    def test_all_suites_passing_ends_with_exactly_all_pass(self):
        self.engine_test("ok", "self.assertTrue(True)")
        rc, lines, _ = self.run_all()
        self.assertEqual(rc, 0)
        self.assertEqual(lines[-1], "ALL PASS")
        self.assertEqual(lines.count("ALL PASS"), 1)

    def test_failing_python_test_ends_with_exactly_fail(self):
        self.engine_test("bad", "self.assertTrue(False)")
        rc, lines, text = self.run_all()
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-1], "FAIL")
        self.assertNotIn("ALL PASS", text)
        self.assertIn("engine", " ".join(lines[-4:]))        # the failing suite is named

    def test_a_later_wave_install_test_is_picked_up_automatically(self):
        self.engine_test("ok", "pass")
        write(self.root / "tests" / "install" / "__init__.py", "")
        write(self.root / "tests" / "install" / "test_install.py", '''
            import unittest
            class T(unittest.TestCase):
                def test_it(self): self.fail("install broke")
            ''')
        rc, lines, _ = self.run_all()
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-1], "FAIL")

    def test_bash_install_tests_are_picked_up(self):
        self.engine_test("ok", "pass")
        write(self.root / "tests" / "install" / "test_uninstall.sh", "#!/bin/bash\nexit 3\n", 0o755)
        rc, lines, _ = self.run_all()
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-1], "FAIL")
        write(self.root / "tests" / "install" / "test_uninstall.sh", "#!/bin/bash\nexit 0\n", 0o755)
        rc, lines, _ = self.run_all()
        self.assertEqual((rc, lines[-1]), (0, "ALL PASS"))

    @unittest.skipUnless(HAVE_OSASCRIPT, "osascript needed")
    def test_failing_js_suite_fails_the_run(self):
        self.engine_test("ok", "pass")
        (self.root / "tests" / "js").mkdir(parents=True)
        shutil.copy(RUN_JS, self.root / "tests" / "js" / "run.js")
        write(self.root / "tests" / "js" / "test_x.js", 'test("bad", function () { assert(false, "nope"); });')
        rc, lines, _ = self.run_all()
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-1], "FAIL")

    def test_swift_is_skipped_when_sources_are_only_keep(self):
        self.engine_test("ok", "pass")
        write(self.root / "app" / "Sources" / "Jusage" / ".keep", "")
        write(self.root / "app" / "Package.swift", "// manifest")
        rc, lines, text = self.run_all(FAKE_SWIFT_RC="1")
        self.assertEqual((rc, lines[-1]), (0, "ALL PASS"))
        self.assertFalse(self.swift_log.exists())
        self.assertIn("swift: skipped", text)

    def test_swift_runs_when_sources_exist_and_its_failure_fails_the_run(self):
        self.engine_test("ok", "pass")
        write(self.root / "app" / "Sources" / "Jusage" / ".keep", "")
        write(self.root / "app" / "Sources" / "Jusage" / "main.swift", "print(1)")
        write(self.root / "app" / "Package.swift", "// manifest")
        rc, lines, _ = self.run_all()
        self.assertEqual((rc, lines[-1]), (0, "ALL PASS"))
        self.assertIn("test --scratch-path", self.swift_log.read_text())
        rc, lines, _ = self.run_all(FAKE_SWIFT_RC="1")
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-1], "FAIL")

    def test_swift_sources_but_no_swift_binary_fails_not_skips(self):
        self.engine_test("ok", "pass")
        write(self.root / "app" / "Sources" / "Jusage" / "main.swift", "print(1)")
        minimal = self.tmp / "minbin"
        minimal.mkdir()
        for tool in ("python3", "find", "bash", "dirname", "basename"):
            found = shutil.which(tool)
            self.assertIsNotNone(found, tool)
            (minimal / tool).symlink_to(found)
        e = dict(os.environ, JUSAGE_ROOT=str(self.root), PATH=str(minimal))
        r = subprocess.run([str(minimal / "bash"), str(RUN_ALL)], capture_output=True, text=True, env=e, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "FAIL")
        self.assertIn("swift not found", r.stdout + r.stderr)

    def test_no_engine_tests_at_all_fails(self):
        # a repo with no tests must never read as ALL PASS
        rc, lines, _ = self.run_all()
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-1], "FAIL")


if __name__ == "__main__":
    unittest.main()
