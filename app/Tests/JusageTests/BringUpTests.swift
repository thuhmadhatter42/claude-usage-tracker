import XCTest
@testable import Jusage

/// S9: the app's rigid bring-up (R-144, R-156, R-192) and the dev build's separation (SCORE §6).
final class BringUpTests: XCTestCase {
    /// A fake Python: which paths exist and which pass `import sys,sqlite3; assert >= 3.9`.
    final class FakePython: PythonChecking {
        var existing: Set<String> = []
        var passing: Set<String> = []
        var developerTools = true
        private(set) var probed: [String] = []
        func exists(_ path: String) -> Bool { existing.contains(path) }
        func passes(_ path: String) -> Bool { probed.append(path); return passing.contains(path) }
        func developerToolsPresent() -> Bool { developerTools }
    }

    private var temp: URL!

    override func setUpWithError() throws {
        temp = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-bringup-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: temp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: temp) }

    func makeBundle(version: String = "0.2.0", engineVersion: String? = "0.2.0", engine: Bool = true,
                    themes: String? = FakeAppBundle.goodThemes, bundleID: String = "com.goatdguild.jusage",
                    stateDir: String? = nil) throws -> Bundle {
        try FakeAppBundle.make(in: temp, version: version, engineVersion: engineVersion, engine: engine,
                               themes: themes, bundleID: bundleID, stateDir: stateDir)
    }

    func makeStore(python: String? = nil) -> SettingsStore {
        let store = SettingsStore(defaults: UserDefaults(suiteName: "jusage-bringup-\(UUID().uuidString)")!)
        if let python { store.update { $0.pythonPath = python } }
        return store
    }

    func refusal(_ r: Result<Engine, Refusal>) -> String? {
        if case .failure(let f) = r { return f.line }
        return nil
    }

    // MARK: SCORE-named tests

    func testVersionMismatchRefused() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let b = try makeBundle(version: "0.2.0", engineVersion: "0.1.39")
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: b, settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: the app is 0.2.0 but its engine is 0.1.39. Reinstall jusage.")
    }

    func testRecordedPythonGoneRefused() throws {
        let py = FakePython()
        py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing    // a probe WOULD find one
        let store = makeStore(python: "/usr/local/bin/python3")
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: try makeBundle(), settings: store, python: py)))
        XCTAssertEqual(line, "jusage: the recorded Python /usr/local/bin/python3 is gone. Run install.sh again, or open jusage with --set-python <path>.")
        XCTAssertEqual(store.settings.pythonPath, "/usr/local/bin/python3", "no silent re-probe, nothing re-recorded")
        XCTAssertEqual(py.probed, [], "a recorded Python that is gone is never replaced by a probe")
    }

    func testEngineMissingRefused() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: try makeBundle(engine: false), settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: the engine is missing from the app (Contents/Resources/engine/claude_usage.py). Reinstall jusage.")
    }

    // MARK: more of S9

    func testGoodBundleGivesEngine() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let b = try makeBundle()
        let r = BringUp.check(bundle: b, settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)
        let e = try r.get()
        XCTAssertEqual(e.python.path, "/opt/homebrew/bin/python3")
        XCTAssertEqual(e.script.lastPathComponent, "claude_usage.py")
        XCTAssertTrue(FileManager.default.fileExists(atPath: e.script.path))
        XCTAssertEqual(e.version, "0.2.0")
        XCTAssertNil(e.stateDir, "the release build uses the engine's default state dir")
        XCTAssertFalse(e.pythonRecordedNow)
    }

    func testVersionFileMissingRefused() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: try makeBundle(engineVersion: nil), settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: Contents/Resources/VERSION is missing from the app. Reinstall jusage.")
    }

    func testRecordedPythonTooOldRefused() throws {
        let py = FakePython(); py.existing = ["/usr/local/bin/python3"]   // exists, fails the 3.9 + sqlite3 check
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: try makeBundle(), settings: makeStore(python: "/usr/local/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: the recorded Python /usr/local/bin/python3 is not Python 3.9+ with sqlite3. Open jusage with --set-python <path>.")
    }

    func testProbeRecordsFirstPassingInOrder() throws {
        let py = FakePython()
        py.existing = ["/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
        py.passing = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
        let store = makeStore()
        let e = try BringUp.check(bundle: try makeBundle(), settings: store, python: py).get()
        XCTAssertEqual(e.python.path, "/opt/homebrew/bin/python3")
        XCTAssertTrue(e.pythonRecordedNow, "the caller also records it with `config python`")
        XCTAssertEqual(store.settings.pythonPath, "/opt/homebrew/bin/python3")
        XCTAssertEqual(py.probed, ["/usr/bin/python3", "/opt/homebrew/bin/python3"])
    }

    func testSystemStubSkippedWithoutDeveloperTools() throws {
        let py = FakePython()
        py.developerTools = false                 // /usr/bin/python3 would pop the Command Line Tools installer
        py.existing = ["/usr/bin/python3", "/usr/local/bin/python3"]
        py.passing = py.existing
        let e = try BringUp.check(bundle: try makeBundle(), settings: makeStore(), python: py).get()
        XCTAssertEqual(e.python.path, "/usr/local/bin/python3")
        XCTAssertFalse(py.probed.contains("/usr/bin/python3"))
    }

    func testNoPythonAnywhereRefused() throws {
        let store = makeStore()
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: try makeBundle(), settings: store, python: FakePython())))
        XCTAssertEqual(line, "jusage: no Python 3.9+ with sqlite3 found in /usr/bin/python3, /opt/homebrew/bin/python3, /usr/local/bin/python3. Install the Command Line Tools (xcode-select --install), then reopen jusage.")
        XCTAssertNil(store.settings.pythonPath)
    }

    func testBadThemesRefusedNamingKey() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let themes = #"{"order": ["system", "ink"], "themes": {"system": {}, "ink": {"scheme": "dark"}}}"#
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: try makeBundle(themes: themes), settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: themes.json: theme 'ink' lacks 'bg'")
    }

    func testThemesLoadedIntoThemeBook() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        var loaded: ThemeBook?
        _ = try BringUp.check(bundle: try makeBundle(), settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py,
                              useThemes: { loaded = $0 }).get()
        XCTAssertEqual(loaded?.themes.map(\.id), ["system"])
    }

    func testRefusalWritesNothing() throws {
        // A refusal earlier in the order never records a Python.
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let store = makeStore()
        XCTAssertNotNil(refusal(BringUp.check(bundle: try makeBundle(engine: false), settings: store, python: py)))
        XCTAssertNil(store.settings.pythonPath)
    }

    // MARK: dev build separation (SCORE §6)

    func testDevBuildUsesItsOwnStateDir() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let b = try makeBundle(bundleID: "com.goatdguild.jusage.dev", stateDir: "~/.claude-usage-dev")
        let e = try BringUp.check(bundle: b, settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py, home: "/h").get()
        XCTAssertEqual(e.stateDir, "/h/.claude-usage-dev")
    }

    func testDevBuildWithoutStateDirRefused() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let b = try makeBundle(bundleID: "com.goatdguild.jusage.dev", stateDir: nil)
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: b, settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: this dev build (com.goatdguild.jusage.dev) has no JusageStateDir; it would share the release build's data. Rebuild with app/build-app.sh --dev.")
    }

    func testReleaseBuildWithStateDirRefused() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let b = try makeBundle(bundleID: "com.goatdguild.jusage", stateDir: "~/.claude-usage-dev")
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: b, settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: this release build (com.goatdguild.jusage) carries JusageStateDir; only a dev build may. Rebuild without --dev.")
    }

    func testStateDirMustBeAbsoluteOrHome() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let b = try makeBundle(bundleID: "com.goatdguild.jusage.dev", stateDir: "relative/dir")
        let line = try XCTUnwrap(refusal(BringUp.check(bundle: b, settings: makeStore(python: "/opt/homebrew/bin/python3"), python: py)))
        XCTAssertEqual(line, "jusage: JusageStateDir must start with / or ~/, not 'relative/dir'.")
    }

    // MARK: --set-python

    func testSetPythonRecordsAGoodOne() throws {
        let py = FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let store = makeStore(python: "/usr/local/bin/python3")
        XCTAssertNil(BringUp.setPython("/opt/homebrew/bin/python3", settings: store, python: py))
        XCTAssertEqual(store.settings.pythonPath, "/opt/homebrew/bin/python3")
    }

    func testSetPythonRefusesABadOne() throws {
        let py = FakePython(); py.existing = ["/usr/bin/python2"]
        let store = makeStore(python: "/opt/homebrew/bin/python3")
        XCTAssertEqual(BringUp.setPython("/usr/bin/python2", settings: store, python: py)?.line,
                       "jusage: --set-python /usr/bin/python2 is not Python 3.9+ with sqlite3; kept /opt/homebrew/bin/python3.")
        XCTAssertEqual(store.settings.pythonPath, "/opt/homebrew/bin/python3")
        XCTAssertEqual(BringUp.setPython("python3", settings: store, python: py)?.line,
                       "jusage: --set-python needs an absolute path, not 'python3'.")
    }
}

/// A bundle on disk shaped like the built app (shared by the Host tests). Each call makes a new path, because
/// Bundle caches by URL.
enum FakeAppBundle {
    static let goodThemes = #"{"order": ["system"], "themes": {"system": {"name": "System"}}}"#

    static func make(in temp: URL, version: String = "0.2.0", engineVersion: String? = "0.2.0", engine: Bool = true,
                     themes: String? = goodThemes, bundleID: String = "com.goatdguild.jusage",
                     stateDir: String? = nil) throws -> Bundle {
        let app = temp.appendingPathComponent("b-\(UUID().uuidString)/jusage.app")
        let res = app.appendingPathComponent("Contents/Resources")
        try FileManager.default.createDirectory(at: res.appendingPathComponent("engine"), withIntermediateDirectories: true)
        var plist: [String: Any] = ["CFBundleIdentifier": bundleID, "CFBundleShortVersionString": version,
                                    "CFBundleExecutable": "Jusage", "CFBundlePackageType": "APPL"]
        if let stateDir { plist["JusageStateDir"] = stateDir }
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: app.appendingPathComponent("Contents/Info.plist"))
        if engine { try "print('engine')\n".write(to: res.appendingPathComponent("engine/claude_usage.py"), atomically: true, encoding: .utf8) }
        if let engineVersion { try "\(engineVersion)\n".write(to: res.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8) }
        if let themes { try themes.write(to: res.appendingPathComponent("themes.json"), atomically: true, encoding: .utf8) }
        guard let b = Bundle(url: app) else { throw CocoaError(.fileReadCorruptFile) }
        return b
    }
}
