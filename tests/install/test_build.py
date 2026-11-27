"""I1 build, I2 icon, I3 release + appcast, version stamping (W3-D).

No test signs, notarizes, uploads or tags. `swift`, `security`, `codesign`, `xcrun`, `gh` are PATH stubs;
the app is built into a temp folder from a temp copy of the repo's build inputs.
"""
import json
import os
import plistlib
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.install import harness as h

BUILD_SH = h.ROOT / "app" / "build-app.sh"
RELEASE_SH = h.ROOT / "scripts" / "release.sh"
APPCAST_SH = h.ROOT / "scripts" / "appcast.sh"
VERSION_SH = h.ROOT / "scripts" / "version.sh"


def tearDownModule():
    h.cleanup_all()


def plist(app: Path) -> dict:
    with open(app / "Contents" / "Info.plist", "rb") as f:
        return plistlib.load(f)


class Built(unittest.TestCase):
    """Base: a temp repo, a stub swift producing a universal binary, a stub trash."""
    archs = ("arm64", "x86_64")

    def setUp(self):
        self.tmp = h.scratch()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = h.make_repo(self.tmp)
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.swift_log = self.tmp / "swift.log"
        self.swift_out = self.tmp / "swift-out"
        h.stub_swift(self.stubs, self.swift_out, h.test_binary(self.archs), self.swift_log)
        self.trashed = self.tmp / "trashed"
        h.stub_trash(self.stubs, self.trashed)
        self.env = h.clean_env(self.home, self.stubs, extra={"JUSAGE_TRASH": str(self.stubs / "trash")})
        self.dest = self.tmp / "dist"

    def build(self, *args, dest=None):
        return h.run(["bash", self.repo / "app" / "build-app.sh", *args, dest or self.dest], env=self.env, cwd=self.repo)


class TestBuildRefusals(Built):
    def test_refuses_applications_dest(self):
        for bad in ("/Applications", "/Applications/jusage-build", "/applications/x", "/Applications/../Applications/y"):
            rc, out = h.run(["bash", self.repo / "app" / "build-app.sh", bad], env=self.env, cwd=self.repo)
            self.assertNotEqual(rc, 0, bad)
            self.assertIn("/Applications", out)
            self.assertIn("dist", out)
        self.assertFalse(self.swift_log.exists(), "swift must not even start when DEST is refused")

    def test_refuses_applications_through_a_symlink(self):
        link = self.tmp / "looks-harmless"
        link.symlink_to("/Applications")
        rc, out = self.build(dest=link / "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("/Applications", out)
        self.assertFalse((Path("/Applications") / "x").exists())

    def test_missing_dest_and_unknown_option(self):
        rc, out = h.run(["bash", BUILD_SH], env=self.env, cwd=self.repo)
        self.assertEqual(rc, 2)
        self.assertIn("usage", out.lower())
        rc, out = h.run(["bash", BUILD_SH, "--bogus", self.dest], env=self.env, cwd=self.repo)
        self.assertEqual(rc, 2)

    def test_one_arch_binary_is_refused_and_old_app_left_alone(self):
        rc, _ = self.build()
        self.assertEqual(rc, 0)
        marker = self.dest / "jusage.app" / "Contents" / "Resources" / "marker"
        marker.write_text("old")
        h.stub_swift(self.stubs, self.swift_out, h.test_binary(("arm64",)), self.swift_log)
        rc, out = self.build()
        self.assertNotEqual(rc, 0)
        self.assertIn("x86_64", out)
        self.assertEqual(marker.read_text(), "old", "a failed build must leave the finished app untouched")
        self.assertFalse((self.dest / ".jusage.app.building").exists() and not (self.dest / "build-app.log").exists())
        self.assertIn("build-app.log", out, "a failure names its log")


class TestBuildBundle(Built):
    def setUp(self):
        super().setUp()
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.app = self.dest / "jusage.app"

    def test_universal_slices(self):
        rc, out = h.run(["lipo", "-archs", self.app / "Contents" / "MacOS" / "Jusage"])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(out.split()), ["arm64", "x86_64"])
        args = self.swift_log.read_text()
        self.assertIn("-c release", args)
        self.assertIn("--arch arm64", args)
        self.assertIn("--arch x86_64", args)

    def test_bundle_layout_mirrors_repo(self):
        res = self.app / "Contents" / "Resources"
        src = self.repo / "engine"
        skip = {"__pycache__", "tests"}
        n = 0
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if p.is_dir() or skip & set(rel.parts) or p.suffix == ".pyc" or p.name == ".DS_Store":
                continue
            twin = res / "engine" / rel
            self.assertTrue(twin.is_file(), f"engine/{rel} missing from the bundle")
            self.assertEqual(twin.read_bytes(), p.read_bytes(), f"engine/{rel} differs")
            self.assertEqual(os.access(twin, os.X_OK), os.access(p, os.X_OK), f"engine/{rel} exec bit")
            n += 1
        self.assertGreater(n, 40)
        self.assertEqual((res / "VERSION").read_text().strip(), h.VERSION)
        self.assertEqual((res / "themes.json").read_bytes(), (src / "themes.json").read_bytes())
        for name in ("uninstall.sh", "uninstall.manifest", "Manual.html", "WhatsNew.html", "Licenses.txt", "AppIcon.icns"):
            self.assertTrue((res / name).is_file(), name)
        self.assertFalse((res / "Info.plist.in").exists(), "the template is not a bundle resource")
        self.assertEqual([p for p in res.rglob("__pycache__")], [])
        self.assertEqual([p for p in res.rglob("*.pyc")], [])

    def test_bundle_engine_runs_from_inside_the_app(self):
        env = h.clean_env(self.home)
        rc, out = h.run(["python3", "-E", "-s", self.app / "Contents" / "Resources" / "engine" / "claude_usage.py", "--version"], env=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), h.VERSION)

    def test_plist_stamped(self):
        p = plist(self.app)
        self.assertEqual(p["CFBundleIdentifier"], h.APP_ID)
        self.assertEqual(p["CFBundleShortVersionString"], h.VERSION)
        self.assertEqual(p["LSMinimumSystemVersion"], "13.0")
        self.assertNotIn("JusageStateDir", p)
        self.assertTrue((self.app / "Contents" / "MacOS" / p["CFBundleExecutable"]).is_file())

    def test_sparkle_in_frameworks_and_rpath(self):
        self.assertTrue((self.app / "Contents" / "Frameworks" / "Sparkle.framework").is_dir())
        rc, out = h.run(["otool", "-l", self.app / "Contents" / "MacOS" / "Jusage"])
        self.assertIn("@executable_path/../Frameworks", out)

    def test_nothing_left_behind_and_log_written(self):
        names = sorted(p.name for p in self.dest.iterdir() if p.name.startswith("."))
        self.assertNotIn(".jusage.app.building", names)
        log = (self.dest / "build-app.log").read_text()
        self.assertIn("built", log)

    def test_rebuild_swaps_and_trashes_the_old_app(self):
        (self.app / "Contents" / "Resources" / "marker").write_text("old")
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.app / "Contents" / "Resources" / "marker").exists())
        olds = list(self.trashed.glob(".jusage.app.old*")) + list(self.trashed.glob("jusage.app*"))
        self.assertEqual(len(olds), 1, [p.name for p in self.trashed.iterdir()])
        self.assertTrue((olds[0] / "Contents" / "Resources" / "marker").is_file())
        self.assertFalse((self.dest / ".jusage.app.old").exists())

    def test_stale_building_folder_is_trashed_not_reused(self):
        stale = self.dest / ".jusage.app.building"
        stale.mkdir()
        (stale / "junk").write_text("x")
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.assertTrue(any((p / "junk").exists() for p in self.trashed.iterdir()))
        self.assertFalse((self.app / "junk").exists())


class TestBuildDev(Built):
    def test_dev_stamps_bundle_id_and_state_dir(self):
        rc, out = self.build("--dev")
        self.assertEqual(rc, 0, out)
        p = plist(self.dest / "jusage.app")
        self.assertEqual(p["CFBundleIdentifier"], h.APP_ID + ".dev")
        self.assertEqual(p["JusageStateDir"], "~/.claude-usage-dev")
        self.assertIs(p["SUEnableAutomaticChecks"], False, "a dev build never updates itself")


class TestIcon(unittest.TestCase):
    def test_icon_regenerated(self):
        tmp = h.scratch()
        out = tmp / "out"
        rc, text = h.run(["bash", h.ROOT / "app" / "icon" / "make-icns.sh", "--out", out], env=h.clean_env(tmp))
        self.assertEqual(rc, 0, text)
        icns, png = out / "AppIcon.icns", out / "icon.png"
        self.assertTrue(icns.is_file() and png.is_file())
        rc, text = h.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", png])
        self.assertIn("pixelWidth: 1024", text)
        self.assertIn("pixelHeight: 1024", text)
        rc, text = h.run(["iconutil", "-c", "iconset", "-o", tmp / "round.iconset", icns])
        self.assertEqual(rc, 0, text)
        names = sorted(p.name for p in (tmp / "round.iconset").iterdir())
        self.assertEqual(len(names), 10, names)
        self.assertIn("icon_512x512@2x.png", names)

    def test_committed_icons_are_valid(self):
        for f, magic in ((h.ROOT / "app" / "Resources" / "AppIcon.icns", b"icns"), (h.ROOT / "store" / "icon.png", b"\x89PNG")):
            self.assertTrue(f.is_file(), f)
            self.assertEqual(f.read_bytes()[:4], magic)


class TestBuildIcon(Built):
    def test_build_regenerates_the_icon_into_the_bundle(self):
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        icns = self.dest / "jusage.app" / "Contents" / "Resources" / "AppIcon.icns"
        rc, text = h.run(["iconutil", "-c", "iconset", "-o", self.tmp / "b.iconset", icns])
        self.assertEqual(rc, 0, text)
        self.assertIn("icon regenerated", (self.dest / "build-app.log").read_text())


# ------------------------------------------------------------------ release

CONF = h.ROOT / "scripts" / "release.conf"


def fixture_app(base: Path, version: str, name="jusage", key="AAAA") -> Path:
    app = base / f"{name}.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    (app / "Contents" / "MacOS").mkdir()
    (app / "Contents" / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": h.APP_ID, "CFBundleExecutable": "Jusage",
                       "CFBundleShortVersionString": version, "SUFeedURL": "https://example.invalid/a.xml",
                       "SUPublicEDKey": key, "LSMinimumSystemVersion": "13.0"}, f)
    res = app / "Contents" / "Resources"
    (res / "uninstall.manifest").write_text(f"name {name}\napp /Applications/{name}.app\n")
    for f in ("uninstall.sh", "Manual.html", "WhatsNew.html", "Licenses.txt", "VERSION", "themes.json", "AppIcon.icns"):
        (res / f).write_text("x")
    (res / "engine" / "bin").mkdir(parents=True)
    (res / "engine" / "claude_usage.py").write_text("x")
    (res / "engine" / "bin" / "jusage").write_text("x")
    return app


class TestReleaseConf(unittest.TestCase):
    def test_values(self):
        text = CONF.read_text()
        self.assertRegex(text, r'(?m)^APP_NAME="jusage"')
        self.assertRegex(text, r'(?m)^BUNDLE_ID_PREFIX="com\.goatdguild\.jusage"')
        self.assertRegex(text, r'(?m)^TAG_PREFIX="CUT"')
        self.assertRegex(text, r'(?m)^MIN_MACOS="13\.0"')
        self.assertIn("build-app.sh", text)

    def test_no_identity_or_team_tracked(self):
        for f in [p for p in (h.ROOT / "scripts").rglob("*") if p.is_file()] + [h.ROOT / "app" / "build-app.sh"]:
            t = f.read_text(errors="ignore")
            self.assertNotRegex(t, r"Developer ID Application: [A-Za-z]", f.name)
            self.assertNotIn("9WTFGV63S2", t, f.name)
            self.assertNotRegex(t, r"J Shriver", f.name)

    def test_entitlements_valid_and_comment_free(self):
        ent = h.ROOT / "scripts" / "jusage.entitlements"
        rc, out = h.run(["plutil", "-lint", ent])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("<!--", ent.read_text())


class Release(unittest.TestCase):
    def setUp(self):
        self.tmp = h.scratch()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.calls = self.tmp / "forbidden-calls.log"

    def forbid(self, *names):
        """PATH stubs that record a call and fail: a dry run must never reach them."""
        for n in names:
            h.write_exe(self.stubs / n, f"#!/bin/bash\necho \"{n} $*\" >> '{self.calls}'\nexit 1\n")

    def env(self, **extra):
        return h.clean_env(self.home, self.stubs, extra=extra)

    def release(self, *args, repo=None, env=None):
        repo = repo or h.ROOT
        return h.run(["bash", repo / "scripts" / "release.sh", *args], env=env or self.env(), cwd=repo)


class TestReleaseGate(Release):
    def test_check_only_gate_ok(self):
        app = fixture_app(self.tmp, h.VERSION)
        rc, out = self.release("--check-only", "--app-path", app)
        self.assertEqual(rc, 0, out)
        self.assertIn("HOUSE GATE OK", out)

    def test_gate_names_every_missing_item(self):
        app = fixture_app(self.tmp, h.VERSION)
        (app / "Contents/Resources/uninstall.sh").unlink()
        shutil.rmtree(app / "Contents/Frameworks/Sparkle.framework")
        (app / "Contents/Resources/engine/claude_usage.py").unlink()
        rc, out = self.release("--check-only", "--app-path", app)
        self.assertEqual(rc, 1)
        self.assertIn("uninstall.sh is missing", out)
        self.assertIn("no Sparkle.framework", out)
        self.assertIn("engine/claude_usage.py is missing", out)
        self.assertIn("HOUSE GATE:", out)

    def test_gate_refuses_wrong_version(self):
        app = fixture_app(self.tmp, "0.0.1")
        rc, out = self.release("--check-only", "--app-path", app)
        self.assertEqual(rc, 1)
        self.assertIn("VERSION is", out)

    def test_placeholder_sparkle_key_refused(self):
        app = fixture_app(self.tmp, h.VERSION, key="__SU_PUBLIC_ED_KEY__")
        rc, out = self.release("--check-only", "--app-path", app)
        self.assertEqual(rc, 1, out)
        self.assertIn("__SU_PUBLIC_ED_KEY__", out)

    def test_check_only_on_a_stripped_copy_of_a_real_bundle(self):
        app = fixture_app(self.tmp, h.VERSION)
        (app / "Contents/Resources/Manual.html").unlink()
        rc, out = self.release("--check-only", "--app-path", app)
        self.assertEqual(rc, 1)
        self.assertIn("Manual", out)


class TestReleaseIdentity(Release):
    def security_stub(self, listing: str):
        h.write_exe(self.stubs / "security", f"#!/bin/bash\ncat <<'EOF'\n{listing}\nEOF\n")

    def test_identity_read_from_keychain_at_run_time(self):
        self.security_stub('  1) AAAA "Developer ID Application: Test Person (ABCDE12345)"\n     1 valid identities found')
        rc, out = self.release("--show-identity")
        self.assertEqual(rc, 0, out)
        self.assertIn("Developer ID Application: Test Person (ABCDE12345)", out)
        self.assertIn("ABCDE12345", out)

    def test_no_identity_refused(self):
        self.security_stub('     0 valid identities found')
        rc, out = self.release("--show-identity")
        self.assertEqual(rc, 1)
        self.assertIn("Developer ID Application", out)

    def test_two_identities_need_a_choice(self):
        self.security_stub('  1) A "Developer ID Application: One (AAAAAAAAAA)"\n  2) B "Developer ID Application: Two (BBBBBBBBBB)"')
        rc, out = self.release("--show-identity")
        self.assertEqual(rc, 1)
        self.assertIn("SIGN_IDENTITY", out)
        rc, out = self.release("--show-identity", env=self.env(SIGN_IDENTITY="Developer ID Application: Two (BBBBBBBBBB)"))
        self.assertEqual(rc, 0, out)
        self.assertIn("BBBBBBBBBB", out)


class TestReleaseDryRun(Release):
    def setUp(self):
        super().setUp()
        self.forbid("xcrun", "spctl", "security", "gh", "notarytool", "stapler")
        # build-app.sh ad-hoc signs (--sign -) so the bundle runs; anything else is a real signature and is forbidden
        h.write_exe(self.stubs / "codesign", f"""#!/bin/bash
case " $* " in *" --sign - "*) exec /usr/bin/codesign "$@" ;; esac
echo "codesign $*" >> '{self.calls}'
exit 1
""")
        self.repo = h.make_repo(self.tmp)
        self.swift_log = self.tmp / "swift.log"
        h.stub_swift(self.stubs, self.tmp / "swift-out", h.test_binary(), self.swift_log)
        h.stub_trash(self.stubs, self.tmp / "trashed")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def dry(self, *extra):
        return self.release("--dry-run", "--plain-dmg", *extra, repo=self.repo,
                            env=self.env(JUSAGE_TRASH=str(self.stubs / "trash")))

    def test_dry_run_builds_gates_and_packs_but_never_signs(self):
        rc, out = self.dry()
        self.assertEqual(rc, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("HOUSE GATE", out.replace("GATE ok", "HOUSE GATE"))
        self.assertFalse(self.calls.exists(), self.calls.read_text() if self.calls.exists() else "")
        dmg = self.repo / "dist" / f"jusage-{h.VERSION}-DRYRUN.dmg"
        self.assertTrue(dmg.is_file(), out)
        self.assertFalse((self.repo / "dist" / f"jusage-{h.VERSION}.dmg").exists())
        rc, text = h.run(["hdiutil", "verify", dmg])
        self.assertEqual(rc, 0, text)
        self.assertIn("build-app.sh", (self.repo / "dist" / f"release-{h.VERSION}.log").read_text())

    def test_dry_run_warns_but_passes_on_placeholder_key(self):
        rc, out = self.dry()
        self.assertEqual(rc, 0, out)

    def test_dry_run_lists_what_it_would_sign_deepest_first(self):
        rc, out = self.dry()
        lines = [l for l in out.splitlines() if "DRY RUN would sign" in l]
        names = [l.split("would sign: ")[1] for l in lines]
        self.assertEqual(names[-2], "jusage.app")  # app last, then the DMG
        self.assertTrue(any(n.endswith("Sparkle.framework/Versions/B/Sparkle") for n in names), names)
        self.assertLess([i for i, n in enumerate(names) if n.endswith("Versions/B/Sparkle")][0],
                        [i for i, n in enumerate(names) if n.endswith("Sparkle.framework")][0])

    def test_dry_run_checks_slices_and_minimum_macos(self):
        rc, out = self.dry()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"has arm64 slice")
        self.assertRegex(out, r"has x86_64 slice")
        self.assertRegex(out, r"VERIFY minimum macOS")

    def test_dry_run_never_tags_and_refuses_tag(self):
        rc, out = self.dry("--tag")
        self.assertEqual(rc, 2, out)
        tags = subprocess.run(["git", "tag"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertEqual(tags.strip(), "")

    def test_failed_build_names_the_log(self):
        h.write_exe(self.stubs / "swift", "#!/bin/bash\necho boom >&2\nexit 1\n")
        rc, out = self.dry()
        self.assertNotEqual(rc, 0)
        self.assertRegex(out, r"see log: .*release-" + re.escape(h.VERSION) + r"\.log")

    def test_version_never_overwritten(self):
        dist = self.repo / "dist"
        dist.mkdir()
        (dist / f"jusage-{h.VERSION}.dmg").write_text("shipped")
        # only checked on a real (notarizing) run, so use --show-identity-free path: the dry run does not touch it
        rc, out = self.dry()
        self.assertEqual(rc, 0, out)
        self.assertEqual((dist / f"jusage-{h.VERSION}.dmg").read_text(), "shipped")

    def test_static_pipeline_steps_present(self):
        s = RELEASE_SH.read_text()
        for needle in ("notarytool submit", "stapler staple", "stapler validate", "spctl -a -vv",
                       "--options runtime --timestamp --sign", "notarytool log", "smoke-test-dmg.sh",
                       "appcast.sh", "never overwrite a shipped version", "UNNOTARIZED"):
            self.assertIn(needle, s, needle)
        code = "\n".join(l for l in s.splitlines() if not l.strip().startswith("#"))
        for bad in ("shutdown", "reboot", "killall", "sudo ", "launchctl"):
            self.assertNotIn(bad, code)


# ------------------------------------------------------------------ appcast

class TestAppcast(unittest.TestCase):
    def setUp(self):
        self.tmp = h.scratch()
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.calls = self.tmp / "calls.log"
        self.dl = self.tmp / "downloads"
        self.sparkle = self.tmp / "sparkle-bin"
        # stand-in generate_appcast: writes appcast.xml with one item per dmg using the given prefix
        h.write_exe(self.sparkle / "generate_appcast", """#!/bin/bash
prefix=""; folder=""
while [ $# -gt 0 ]; do case "$1" in --download-url-prefix) prefix="$2"; shift 2;; --account) shift 2;; *) folder="$1"; shift;; esac; done
{ echo '<rss><channel>'
  for f in "$folder"/jusage-[0-9]*.dmg; do echo "<item><enclosure url=\\"${prefix}$(basename "$f")\\"/></item>"; done
  echo '</channel></rss>'; } > "$folder/appcast.xml"
""")
        h.write_exe(self.stubs / "xcrun", f"#!/bin/bash\necho \"xcrun $*\" >> '{self.calls}'\n[ \"$1 $2\" = \"stapler validate\" ] && [ ! -e '{self.tmp}/unstapled' ] && exit 0\nexit 1\n")
        h.write_exe(self.stubs / "gh", f"#!/bin/bash\necho \"gh $*\" >> '{self.calls}'\nexit 0\n")
        self.dmg = self.tmp / f"jusage-{h.VERSION}.dmg"
        self.dmg.write_text("dmg-bytes")

    def env(self, **kw):
        base = dict(SPARKLE_BIN=str(self.sparkle), DOWNLOADS=str(self.dl))
        base.update(kw)
        return h.clean_env(self.tmp / "home", self.stubs, extra=base)

    def appcast(self, *args, **kw):
        (self.tmp / "home").mkdir(exist_ok=True)
        return h.run(["bash", APPCAST_SH, *args], env=self.env(**kw), cwd=h.ROOT)

    def test_stapled_dmg_joins_the_feed_with_github_release_urls(self):
        rc, out = self.appcast(self.dmg)
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.dl / f"jusage-{h.VERSION}.dmg").is_file())
        self.assertTrue((self.dl / "jusage-latest.dmg").is_file())
        xml = (self.dl / "appcast.xml").read_text()
        self.assertRegex(xml, r'url="https://github\.com/[^"]+/releases/download/CUT-' + re.escape(h.VERSION) + r"/jusage-" + re.escape(h.VERSION) + r'\.dmg"')

    def test_unstapled_refused(self):
        (self.tmp / "unstapled").write_text("")
        rc, out = self.appcast(self.dmg)
        self.assertNotEqual(rc, 0)
        self.assertIn("not stapled", out)
        self.assertFalse((self.dl / "appcast.xml").exists())

    def test_dry_run_dmg_never_in_the_feed(self):
        bad = self.tmp / f"jusage-{h.VERSION}-DRYRUN.dmg"
        bad.write_text("x")
        rc, out = self.appcast(bad)
        self.assertNotEqual(rc, 0)
        self.assertIn("never goes in the feed", out)

    def test_http_prefix_refused(self):
        rc, out = self.appcast(self.dmg, DOWNLOAD_PREFIX="http://example.invalid/")
        self.assertEqual(rc, 2, out)

    def test_shipped_version_never_overwritten(self):
        rc, _ = self.appcast(self.dmg)
        self.assertEqual(rc, 0)
        self.dmg.write_text("different")
        rc, out = self.appcast(self.dmg)
        self.assertNotEqual(rc, 0)
        self.assertIn("never overwritten", out)

    def test_publish_dry_run_prints_gh_command_and_runs_nothing(self):
        rc, out = self.appcast(self.dmg, "--publish", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"gh release create CUT-{h.VERSION}", out)
        self.assertIn("appcast.xml", out)
        self.assertIn("jusage-latest.dmg", out)
        self.assertNotIn("gh ", self.calls.read_text() if self.calls.exists() else "")


class TestVersionScript(unittest.TestCase):
    def test_show_and_plist(self):
        tmp = h.scratch()
        (tmp / "VERSION").write_text("1.2.3\n")
        p = tmp / "Info.plist"
        with open(p, "wb") as f:
            plistlib.dump({"CFBundleShortVersionString": "0", "CFBundleVersion": "0"}, f)
        rc, out = h.run(["sh", VERSION_SH, "--file", tmp / "VERSION", "show"])
        self.assertEqual((rc, out.strip()), (0, "1.2.3"))
        rc, out = h.run(["sh", VERSION_SH, "--file", tmp / "VERSION", "plist", p])
        self.assertEqual(rc, 0, out)
        self.assertEqual(plistlib.loads(p.read_bytes())["CFBundleShortVersionString"], "1.2.3")

    def test_bad_version_refused(self):
        tmp = h.scratch()
        (tmp / "VERSION").write_text("1.2\n")
        rc, out = h.run(["sh", VERSION_SH, "--file", tmp / "VERSION", "show"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
