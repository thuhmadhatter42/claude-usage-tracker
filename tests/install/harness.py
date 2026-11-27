"""Shared helpers for the install, build and release tests (W3-D).

Everything here is hermetic: a temp HOME, PATH stubs for the system tools that would touch the real
Mac (swift, spctl, open, osascript, xcode-select, security, codesign, xcrun, gh, trash), a temp copy of
the build inputs, a locally built universal test binary. No test signs, notarizes, uploads, opens an app,
or reads a real Keychain, Claude folder or /Applications.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "VERSION").read_text().strip()
APP_ID = "com.goatdguild.jusage"

_SCRATCH = []


def scratch() -> Path:
    d = Path(tempfile.mkdtemp(prefix="jusage-w3d-")).resolve()
    _SCRATCH.append(d)
    return d


def cleanup_all() -> None:
    for d in _SCRATCH:
        shutil.rmtree(d, ignore_errors=True)
    _SCRATCH.clear()


def write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def clean_env(home: Path, *path_front: Path, extra=None) -> dict:
    """The environment a script runs in: temp HOME, no Claude folder variables (RULING 32), stubs first on PATH."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE") and not k.startswith("JUSAGE") and k not in ("SIGN_IDENTITY", "TEAM_ID", "NOTARY_PROFILE")}
    env["HOME"] = str(home)
    env["PATH"] = ":".join([str(p) for p in path_front] + [env.get("PATH", "/usr/bin:/bin")])
    env["TMPDIR"] = str(home / "tmp")
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    env.update(extra or {})
    return env


def run(cmd, env=None, cwd=None, timeout=240):
    r = subprocess.run([str(c) for c in cmd], env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


# ------------------------------------------------------------------ test binaries

_BIN_CACHE = {}


def test_binary(archs=("arm64", "x86_64")) -> Path:
    """A tiny Mach-O built with clang for the given architectures (stands in for swift's product)."""
    key = tuple(archs)
    if key in _BIN_CACHE and _BIN_CACHE[key].exists():
        return _BIN_CACHE[key]
    d = scratch()
    src = d / "main.c"
    src.write_text("int main(int c, char**v){return 0;}\n")
    out = d / ("bin-" + "-".join(archs))
    cmd = ["clang", "-mmacosx-version-min=13.0", "-Wl,-headerpad_max_install_names", "-o", str(out), str(src)]
    for a in archs:
        cmd += ["-arch", a]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("clang failed: " + r.stderr)
    _BIN_CACHE[key] = out
    return out


# ------------------------------------------------------------------ a temp copy of the build inputs

PLIST_IN = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>jusage</string>
  <key>CFBundleDisplayName</key><string>jusage</string>
  <key>CFBundleIdentifier</key><string>com.goatdguild.jusage</string>
  <key>CFBundleExecutable</key><string>Jusage</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.0.0</string>
  <key>CFBundleVersion</key><string>0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>SUFeedURL</key><string>https://example.invalid/appcast.xml</string>
  <key>SUPublicEDKey</key><string>__SU_PUBLIC_ED_KEY__</string>
  <key>SUEnableAutomaticChecks</key><true/>
  <key>SUScheduledCheckInterval</key><integer>86400</integer>
</dict></plist>
"""


def make_repo(tmp: Path) -> Path:
    """A temp repo root holding the REAL build scripts, engine and VERSION, plus fixture bundle resources
    for any file wave 3's other chairs (H2) have not delivered to this worktree yet."""
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    shutil.copy2(ROOT / "app" / "build-app.sh", repo / "app" / "build-app.sh")
    shutil.copytree(ROOT / "app" / "icon", repo / "app" / "icon")
    shutil.copytree(ROOT / "scripts", repo / "scripts")
    shutil.copytree(ROOT / "engine", repo / "engine", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "VERSION", repo / "VERSION")
    res = repo / "app" / "Resources"
    res.mkdir()
    real = ROOT / "app" / "Resources"
    if real.is_dir():
        for f in real.iterdir():
            if f.is_file() and f.name != "AppIcon.icns":
                shutil.copy2(f, res / f.name)
    if not (res / "Info.plist.in").exists():
        (res / "Info.plist.in").write_text(PLIST_IN)
    for name, body in (("uninstall.sh", "#!/bin/sh\nexit 0\n"),
                       ("uninstall.manifest", "name jusage\napp /Applications/jusage.app\n"),
                       ("Manual.html", "<html></html>\n"), ("WhatsNew.html", "<html></html>\n"),
                       ("Licenses.txt", "Sparkle: MIT\n")):
        if not (res / name).exists():
            (res / name).write_text(body)
    return repo


def stub_swift(stubs: Path, out: Path, binary: Path, log: Path) -> None:
    """A `swift` on PATH that answers `build` and `build --show-bin-path` without compiling anything."""
    bindir = out / "bin"
    write_exe(stubs / "swift", f"""#!/bin/bash
echo "swift $*" >> '{log}'
case " $* " in *" --show-bin-path "*) echo '{bindir}'; exit 0 ;; esac
mkdir -p '{bindir}/Sparkle.framework/Versions/B'
cp '{binary}' '{bindir}/Jusage'
cp '{binary}' '{bindir}/Sparkle.framework/Versions/B/Sparkle'
mkdir -p '{bindir}/Sparkle.framework/Versions/B/Resources'
cat > '{bindir}/Sparkle.framework/Versions/B/Resources/Info.plist' <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>CFBundleExecutable</key><string>Sparkle</string><key>CFBundleIdentifier</key><string>org.example.sparkle-stub</string><key>CFBundlePackageType</key><string>FMWK</string><key>CFBundleVersion</key><string>1</string></dict></plist>
PL
ln -sfn B '{bindir}/Sparkle.framework/Versions/Current'
ln -sfn Versions/Current/Resources '{bindir}/Sparkle.framework/Resources'
ln -sfn Versions/Current/Sparkle '{bindir}/Sparkle.framework/Sparkle'
exit 0
""")


def stub_trash(stubs: Path, bin_dir: Path) -> Path:
    """A trash command that moves into a temp folder (the tests never touch the real Trash)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    write_exe(stubs / "trash", f"""#!/bin/bash
for p in "$@"; do mv "$p" '{bin_dir}/'"$(basename "$p").$$.$RANDOM"; done
""")
    return stubs / "trash"


def tree(path: Path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))
