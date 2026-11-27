"""I6: repo hygiene. Nothing tracked may carry a home path, a person's name, an email or a vault path
(R-182), and the build folders stay out of git. The sweep patterns are assembled from pieces so this
file does not match its own sweep."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = ["Users" + "/", "j" + "shriver", "dandev" + "itto", "goat" + "-arc", "Al" + "ex"]
SWEEP = re.compile("|".join(re.escape(p) for p in PATTERNS))
# RULINGS 40: pasted house-kit code holds generic fixture home paths. Exempt this one file by
# exact path, nothing else.
EXEMPT = frozenset({"app/Tests/JusageTests/UninstallTests.swift"})


def sweep(repo: Path):
    """Every `file:line` in a git-tracked text file that matches the hygiene patterns."""
    names = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True,
                           capture_output=True).stdout.split(b"\0")
    hits = []
    for raw in names:
        if not raw or raw.decode() in EXEMPT:
            continue
        path = repo / raw.decode()
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue                                # binary: the shell sweep skips it too
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if SWEEP.search(line):
                hits.append(f"{raw.decode()}:{n}")
    return hits


class Hygiene(unittest.TestCase):
    def test_sweep_empty(self):
        self.assertEqual(sweep(ROOT), [])

    def test_sweep_detects_a_planted_hit(self):
        """A sweep that cannot fail proves nothing: plant each pattern in a scratch repo."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for i, p in enumerate(PATTERNS):
                (repo / f"f{i}.txt").write_text(f"clean line\nsee {p}x here\n")
            (repo / "clean.txt").write_text("nothing here\n")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            hits = sweep(repo)
            self.assertEqual(sorted(hits), sorted(f"f{i}.txt:2" for i in range(len(PATTERNS))))

    def test_exemption_is_one_exact_path(self):
        """The exempt file passes; the same text anywhere else, even beside it, still fails."""
        fixture = f"let home = \"/{PATTERNS[0]}j\"\n"
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for rel in (*EXEMPT, "app/Tests/JusageTests/OtherTests.swift", "README.md",
                        "app/Tests/UninstallTests.swift"):
                f = repo / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(fixture)
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            self.assertEqual(sorted(sweep(repo)),
                             ["README.md:1", "app/Tests/JusageTests/OtherTests.swift:1",
                              "app/Tests/UninstallTests.swift:1"])

    def test_untracked_files_are_not_swept(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "notes.txt").write_text(f"{PATTERNS[1]}\n")
            self.assertEqual(sweep(repo), [])

    def test_gitignore_keeps_build_output_out(self):
        lines = {l.strip() for l in (ROOT / ".gitignore").read_text().splitlines()}
        for entry in ("app/.build/", "dist/", "__pycache__/", ".DS_Store"):
            self.assertIn(entry, lines, f".gitignore must list {entry}")

    def test_no_build_output_is_tracked(self):
        names = subprocess.run(["git", "-C", str(ROOT), "ls-files"], check=True,
                               capture_output=True, text=True).stdout.splitlines()
        bad = [n for n in names if n.startswith(("app/.build/", "dist/")) or "/__pycache__/" in n]
        self.assertEqual(bad, [])



class Legacy(unittest.TestCase):
    """Review-security 5: the 0.1.39 engine, dashboard and menu bar app (replaced by engine/,
    engine/ju/dash/ and app/ per SCORE) are gone, so a 0.1.x `jusage upgrade` finds no menubar/
    to build and copy over /Applications. Root `jusage` stays only as a shim to the 0.2.0 command,
    so the `alias jusage=` an 0.1.x install wrote keeps working."""

    def test_replaced_0_1_files_are_not_tracked(self):
        names = subprocess.run(["git", "-C", str(ROOT), "ls-files"], check=True,
                               capture_output=True, text=True).stdout.splitlines()
        gone = ("claude_usage.py", "dashboard.py", "pricing.json", "themes.json")
        self.assertEqual([n for n in names if n in gone or n.startswith("menubar/")], [])

    def test_root_jusage_is_a_shim_to_the_engine_command(self):
        text = (ROOT / "jusage").read_text()
        code = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(code, ['exec "$(cd -P "$(/usr/bin/dirname "${BASH_SOURCE[0]}")" && pwd)/engine/bin/jusage" "$@"'])
        for word in ("ditto", "menubar", "/Applications", "build.sh", "git"):
            self.assertNotIn(word, "\n".join(code))
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "PATH": "/usr/bin:/bin"}
            via_shim = subprocess.run(["bash", str(ROOT / "jusage"), "help"], capture_output=True, text=True, env=env)
            direct = subprocess.run(["bash", str(ROOT / "engine" / "bin" / "jusage"), "help"], capture_output=True,
                                    text=True, env=env)
        self.assertEqual(via_shim.stdout.splitlines()[:3], direct.stdout.splitlines()[:3])
        self.assertTrue(via_shim.stdout.startswith("jusage: Claude Code usage"), via_shim.stdout + via_shim.stderr)

if __name__ == "__main__":
    unittest.main()
