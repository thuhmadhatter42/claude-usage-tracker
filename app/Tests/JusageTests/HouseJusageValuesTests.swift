import XCTest
@testable import Jusage

/// W3-C: jusage's own values in the house-kit per-app files, and the bundle resources H2 ships.
/// Paths are read from the source tree (this file sits in app/Tests/JusageTests).
final class HouseJusageValuesTests: XCTestCase {
    private var app: URL { URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent() }
    private var resources: URL { app.appendingPathComponent("Resources") }
    private func text(_ name: String) throws -> String { try String(contentsOf: resources.appendingPathComponent(name), encoding: .utf8) }

    // MARK: HouseConfig
    func testConfigValues() {
        XCTAssertEqual(House.appName, "jusage")
        XCTAssertEqual(House.bundleID, "com.goatdguild.jusage")
        XCTAssertEqual(House.reportTo, "contact@fourletterrecords.com")
        XCTAssertEqual(House.logFolder, "jusage")
        XCTAssertEqual(House.logDirEnv, "JUSAGE_LOG_DIR")
    }

    // MARK: Quick Keys
    @MainActor func testQuickKeyRowsAreSpecsTable() throws {
        let keys = QuickKeys(defs: QuickKeys.appRows + QuickKeys.standardRows, defaults: UserDefaults(suiteName: "w3c-\(UUID().uuidString)")!, osReserved: { [] })
        func chord(_ id: String) -> QuickChord { keys.chord(for: id) }
        XCTAssertEqual(chord("refresh"), QuickChord(key: "r", mods: .command))
        XCTAssertEqual(chord("dashboard"), QuickChord(key: "d", mods: .command))
        XCTAssertEqual(chord("previousMonth"), QuickChord(key: "left", mods: .option))
        XCTAssertEqual(chord("nextMonth"), QuickChord(key: "right", mods: .option))
        XCTAssertEqual(chord("quickKeys"), QuickChord(key: "k", mods: [.control, .shift]))
        XCTAssertEqual(chord("settings"), QuickChord(key: ",", mods: .command))
        XCTAssertEqual(chord("quit"), QuickChord(key: "q", mods: .command))
        XCTAssertEqual(keys.def("refresh")?.kind, .menu)
        XCTAssertEqual(keys.def("dashboard")?.kind, .menu)
        XCTAssertEqual(keys.def("previousMonth")?.kind, .key)
        XCTAssertEqual(keys.def("settings")?.kind, .locked)
        XCTAssertEqual(keys.def("quit")?.kind, .locked)
        XCTAssertEqual(QuickKeys.appRows.map(\.id), ["refresh", "dashboard", "previousMonth", "nextMonth"], "the standard rows are not repeated")
    }

    // MARK: Tips
    func testTipsAreBoardTalkAndNameTheControls() {
        XCTAssertEqual(HouseTips.problems(HouseTips.all), [])
        XCTAssertEqual(HouseTips.hoverBar.body, "Hover a bar to see what each part means.")
        XCTAssertEqual(HouseTips.refresh.title, "REFRESH")
        XCTAssertTrue(HouseTips.all.contains(HouseTips.refresh))
        XCTAssertTrue(HouseTips.all.contains(HouseTips.hoverBar))
        XCTAssertTrue(HouseTips.all.contains(HouseTips.report))
        XCTAssertTrue(HouseTips.all.contains(HouseTips.accent))
        for t in HouseTips.all { XCTAssertLessThanOrEqual(t.body.count, 60, t.title) }
    }

    // MARK: Info.plist.in
    func testInfoPlistTemplate() throws {
        let data = try Data(contentsOf: resources.appendingPathComponent("Info.plist.in"))
        let p = try XCTUnwrap(PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        XCTAssertEqual(p["CFBundleIdentifier"] as? String, "com.goatdguild.jusage")
        XCTAssertEqual(p["CFBundleName"] as? String, "jusage")
        XCTAssertEqual(p["CFBundleExecutable"] as? String, "Jusage")
        XCTAssertEqual(p["CFBundlePackageType"] as? String, "APPL")
        XCTAssertEqual(p["CFBundleIconFile"] as? String, "AppIcon")
        XCTAssertEqual(p["LSUIElement"] as? Bool, true)
        XCTAssertEqual(p["LSMinimumSystemVersion"] as? String, "13.0")
        XCTAssertEqual(p["SUFeedURL"] as? String, "https://github.com/thuhmadhatter42/claude-usage-tracker/releases/latest/download/appcast.xml")
        XCTAssertEqual(p["SUEnableAutomaticChecks"] as? Bool, true)
        XCTAssertEqual(p["SUScheduledCheckInterval"] as? Int, 86400)
        XCTAssertNotNil(p["SUPublicEDKey"] as? String)
        XCTAssertNotNil(p["CFBundleShortVersionString"], "version.sh plist stamps it")
        XCTAssertNotNil(p["CFBundleVersion"], "version.sh plist stamps it")
    }

    // MARK: uninstall.manifest
    func testManifestListsWhatJusagePutsOnAMac() throws {
        let m = try text("uninstall.manifest")
        let lines = m.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.hasPrefix("#") && !$0.isEmpty }
        func has(_ l: String) -> Bool { lines.contains { $0.split(separator: " ", omittingEmptySubsequences: true).joined(separator: " ") == l } }
        XCTAssertTrue(has("name jusage"))
        XCTAssertTrue(has("app /Applications/jusage.app"))
        XCTAssertTrue(has("user Applications/jusage.app"), "the per-user copy: home-relative")
        XCTAssertTrue(has("data .claude-usage"), "the state folder is kept with Keep my settings")
        XCTAssertTrue(has("data Library/Preferences/com.goatdguild.jusage.plist"))
        XCTAssertTrue(has("user Library/Logs/jusage"))
        XCTAssertTrue(has("user Library/Caches/com.goatdguild.jusage"))
        XCTAssertFalse(m.contains("APP_NAME") || m.contains("BUNDLE_ID"))
    }

    func testManifestAndScriptWorkOnAFixtureHome() throws {
        // run the real script, dry, against a fixture home that has jusage's folders
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("w3c-home-\(UUID().uuidString)")
        let root = home.appendingPathComponent("root")
        defer { try? FileManager.default.removeItem(at: home) }
        for d in [".claude-usage", "Library/Logs/jusage", "Library/Caches/com.goatdguild.jusage"] {
            try FileManager.default.createDirectory(at: home.appendingPathComponent(d), withIntermediateDirectories: true)
        }
        try FileManager.default.createDirectory(at: root.appendingPathComponent("Applications/jusage.app"), withIntermediateDirectories: true)
        let (rc, out) = HouseUninstall.runPlain(script: resources.appendingPathComponent("uninstall.sh"),
            args: ["--manifest", resources.appendingPathComponent("uninstall.manifest").path, "--home", home.path, "--root", root.path, "--dry-run", "--keep-data"])
        XCTAssertEqual(rc, 0, out)
        XCTAssertTrue(out.contains("keep \(home.path)/.claude-usage"), out)
        XCTAssertTrue(out.contains("would move \(home.path)/Library/Logs/jusage"), out)
        XCTAssertTrue(out.contains("would move \(root.path)/Applications/jusage.app"), out)
        XCTAssertTrue(out.contains("DONE"), out)
    }

    func testBundledScriptIsTheKitsScriptUnchanged() throws {
        let kit = URL(fileURLWithPath: NSHomeDirectory() + "/dev/house-kit/modules/uninstall/uninstall.sh")
        try XCTSkipUnless(FileManager.default.fileExists(atPath: kit.path), "house-kit not on this Mac")
        XCTAssertEqual(try text("uninstall.sh"), try String(contentsOf: kit, encoding: .utf8))
    }

    // MARK: pages
    func testHelpPagesAreBundledAndHonest() throws {
        let version = try String(contentsOf: app.deletingLastPathComponent().appendingPathComponent("VERSION"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        let manual = try text("Manual.html")
        XCTAssertTrue(manual.contains("Uninstalling"))
        XCTAssertTrue(manual.contains("sh \"/Applications/jusage.app/Contents/Resources/uninstall.sh\""))
        XCTAssertTrue(manual.contains("jusage uninstall"))
        XCTAssertTrue(manual.contains("Quick Keys"))
        let new = try text("WhatsNew.html")
        XCTAssertTrue(new.contains("<h2>\(version)</h2>"), "What's New leads with \(version)")
        let lic = try text("Licenses.txt")
        XCTAssertTrue(lic.contains("Sparkle"))
        XCTAssertTrue(lic.contains("MIT"))
        for f in [manual, new, lic] { XCTAssertFalse(f.contains("House Sample") || f.contains("APP_NAME")) }
    }
}
