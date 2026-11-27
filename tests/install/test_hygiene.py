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


if __name__ == "__main__":
    unittest.main()
