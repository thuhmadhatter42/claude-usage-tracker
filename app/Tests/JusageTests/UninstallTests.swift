import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

/// Runs the REAL uninstall.sh, but only ever against a fixture tree in a temp folder (--root / --home).
/// The script comes from the test bundle's resources (the app target bundles the same file).
final class UninstallTests: XCTestCase {
    private var root: URL!
    private var home: URL!
    private var manifest: URL!
    private var realRunner: ((URL, [String], Bool) -> (Int32, String))!

    /// uninstall.sh as the tests find it: UNINSTALL_SH (set by a runner), else the test bundle's resource, else the
    /// copy in the source tree (walking up from this file: Resources/, uninstall/, scripts/).
    private func script() throws -> URL {
        let fm = FileManager.default
        if let p = ProcessInfo.processInfo.environment["UNINSTALL_SH"], !p.isEmpty { return URL(fileURLWithPath: p) }
        for b in [Bundle(for: UninstallTests.self), Bundle.main] {
            if let u = b.url(forResource: "uninstall", withExtension: "sh"), fm.fileExists(atPath: u.path) { return u }
        }
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<6 {
            for rel in ["uninstall.sh", "Resources/uninstall.sh", "uninstall/uninstall.sh", "scripts/uninstall.sh"] {
                let u = dir.appendingPathComponent(rel)
                if fm.fileExists(atPath: u.path) { return u }
            }
            dir = dir.deletingLastPathComponent()
        }
        throw XCTSkip("uninstall.sh not found: set UNINSTALL_SH or add it to the test target's resources")
    }

    override func setUpWithError() throws {
        realRunner = HouseUninstall.runner
        let fm = FileManager.default
        root = fm.temporaryDirectory.appendingPathComponent("UninstallTests-\(UUID().uuidString)")
        home = root.appendingPathComponent("Users/u")
        for d in ["Applications/Fixture.app/Contents", "Applications/Other.app", "Users/u/Library/Application Support/Fixture",
                  "Users/u/Library/Logs/Fixture", "Users/u/.Trash"] {
            try fm.createDirectory(at: root.appendingPathComponent(d), withIntermediateDirectories: true)
        }
        try "s".write(to: home.appendingPathComponent("Library/Application Support/Fixture/settings.json"), atomically: true, encoding: .utf8)
        manifest = root.appendingPathComponent("uninstall.manifest")
        try """
        name     Fixture
        data     Library/Application Support/Fixture
        user     Library/Logs/Fixture
        app      /Applications/Fixture.app
        note     Restart to finish.
        """.write(to: manifest, atomically: true, encoding: .utf8)
    }

    override func tearDown() {
        HouseUninstall.runner = realRunner
        try? FileManager.default.removeItem(at: root)
    }

    private func exists(_ rel: String) -> Bool { FileManager.default.fileExists(atPath: root.appendingPathComponent(rel).path) }

    func testArgumentsAlwaysCarryHomeAndUser() {
        let m = URL(fileURLWithPath: "/x/uninstall.manifest")
        XCTAssertEqual(HouseUninstall.arguments(keepSettings: true, manifest: m, home: "/Users/j", user: "j"),
                       ["--manifest", "/x/uninstall.manifest", "--home", "/Users/j", "--user", "j", "--keep-data"])
        XCTAssertFalse(HouseUninstall.arguments(keepSettings: false, manifest: m, home: "/Users/j", user: "j").contains("--keep-data"))
    }

    /// The admin run hands every argument to AppleScript as argv; no path is ever written into AppleScript source.
    func testAdminArgvNeverSplicesAPathIntoTheScriptSource() throws {
        let evil = URL(fileURLWithPath: "/Apps/My \"App\"; rm -rf ~/uninstall.sh")
        let argv = HouseUninstall.adminArgv(script: evil, args: ["--home", "/Users/a b"])
        let source = zip(argv, argv.dropFirst()).filter { $0.0 == "-e" }.map(\.1)
        XCTAssertFalse(source.contains { $0.contains("rm -rf") || $0.contains("/Users/a b") }, "\(source)")
        XCTAssertTrue(source.contains("do shell script c with administrator privileges"))
        XCTAssertTrue(source.contains("set c to c & \" \" & quoted form of (a as text)"))
        let dashes = try XCTUnwrap(argv.firstIndex(of: "--"))
        XCTAssertEqual(Array(argv[(dashes + 1)...]), [evil.path, "--home", "/Users/a b"])
    }

    func testAFixtureTheUserOwnsNeedsNoAdminAndGoesToTheTrash() throws {
        let s = try script()
        var ranAsAdmin: Bool?
        let real = realRunner!
        HouseUninstall.runner = { script, args, admin in ranAsAdmin = admin; return real(script, args, false) }
        let outcome = HouseUninstall.perform(keepSettings: false, script: s, manifest: manifest, home: home.path,
                                             user: NSUserName(), extra: ["--root", root.path])
        XCTAssertEqual(outcome, .done(note: "Restart to finish."))
        XCTAssertEqual(ranAsAdmin, false, "no password box when nothing needs one")
        XCTAssertFalse(exists("Applications/Fixture.app"))
        XCTAssertFalse(exists("Users/u/Library/Application Support/Fixture"))
        XCTAssertTrue(exists("Users/u/.Trash/Fixture.app"), "moved to the Trash, not deleted")
        XCTAssertTrue(exists("Users/u/.Trash/Fixture/settings.json"))
        XCTAssertTrue(exists("Applications/Other.app"), "nothing else is touched")
    }

    func testKeepMySettingsLeavesTheData() throws {
        let s = try script()
        let outcome = HouseUninstall.perform(keepSettings: true, script: s, manifest: manifest, home: home.path,
                                             user: NSUserName(), extra: ["--root", root.path])
        XCTAssertEqual(outcome, .done(note: "Restart to finish."))
        XCTAssertTrue(exists("Users/u/Library/Application Support/Fixture/settings.json"))
        XCTAssertFalse(exists("Applications/Fixture.app"))
        XCTAssertFalse(exists("Users/u/Library/Logs/Fixture"), "logs are not settings")
    }

    /// Something the user cannot move -> the run is handed to the admin path (here a stub: no password box in a test).
    func testSomethingOutsideTheUsersReachIsRunAsAdmin() throws {
        let s = try script()
        let apps = root.appendingPathComponent("Applications")
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: apps.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: apps.path) }
        var ranAsAdmin: Bool?
        HouseUninstall.runner = { _, _, admin in ranAsAdmin = admin; return (1, "execution error: User canceled. (-128)") }
        let outcome = HouseUninstall.perform(keepSettings: true, script: s, manifest: manifest, home: home.path,
                                             user: NSUserName(), extra: ["--root", root.path])
        XCTAssertEqual(ranAsAdmin, true)
        XCTAssertEqual(outcome, .cancelled, "a dismissed password box is not a failure")
        XCTAssertTrue(exists("Applications/Fixture.app"), "nothing moved")
    }

    func testAFailedRunSaysSoAndABadManifestNeverRuns() throws {
        let s = try script()
        HouseUninstall.runner = { _, _, _ in (1, "FAILED to move /Applications/Fixture.app") }
        let failed = HouseUninstall.perform(keepSettings: true, script: s, manifest: manifest, home: home.path,
                                            user: NSUserName(), extra: ["--root", root.path])
        guard case .failed(let why) = failed else { return XCTFail("\(failed)") }
        XCTAssertTrue(why.contains("still installed"), why)

        var ran = false
        HouseUninstall.runner = { _, _, _ in ran = true; return (0, "DONE") }
        try "name Fixture\nuser Library/Preferences\n".write(to: manifest, atomically: true, encoding: .utf8)
        let refused = HouseUninstall.perform(keepSettings: true, script: s, manifest: manifest, home: home.path,
                                             user: NSUserName(), extra: ["--root", root.path])
        guard case .failed = refused else { return XCTFail("\(refused)") }
        XCTAssertFalse(ran, "a manifest the script refuses is never run")
        XCTAssertEqual(HouseUninstall.perform(keepSettings: true, script: nil, manifest: nil),
                       .failed("The uninstaller is missing from this copy of \(House.appName)."))
    }

    @MainActor
    func testTheSheetsStages() {
        let c = UninstallCenter()
        c.keepSettings = false
        c.open()
        XCTAssertTrue(c.showing); XCTAssertEqual(c.stage, .ask)
        XCTAssertTrue(c.keepSettings, "Keep my settings is ticked every time the sheet opens")
        render(UninstallSheet(center: c))
        var asked: Bool?
        c.keepSettings = false
        c.confirm { keep, done in asked = keep; done(.cancelled) }
        XCTAssertEqual(asked, false)
        XCTAssertEqual(c.stage, .ask, "password box dismissed: back to the question")
        c.confirm { _, done in done(.failed("nope")) }
        XCTAssertEqual(c.stage, .failed("nope"))
        render(UninstallSheet(center: c))
        c.confirm { _, done in done(.done(note: nil)) }
        XCTAssertEqual(c.stage, .done(nil))
        render(UninstallSheet(center: c))
    }

    /// Lay the view out in an off-screen hosting view (no window): its body really runs.
    @MainActor private func render<V: View>(_ view: V) {
        let host = NSHostingView(rootView: view.accentRoot())
        host.frame = NSRect(x: 0, y: 0, width: 600, height: 600)
        host.layoutSubtreeIfNeeded()
    }
}
