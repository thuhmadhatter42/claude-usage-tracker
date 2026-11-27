import XCTest
@testable import Jusage

/// Wave 4 T2, app side of SCORE §3 "rigid bring-up": at each app layer a deliberate mismatch REFUSES loudly. Every
/// refusal is one plain `jusage: …` line, goes to HostLog once, lands in the panel as the run error, and no engine
/// run is attempted (no Engine comes back). Layers: engine present, bundle VERSION == app version, recorded
/// Python, themes, and the feed schema. (The engine's own layers are tests/engine/test_refusals.py.)
@MainActor
final class BringUpRefusalTests: XCTestCase {
    private var temp: URL!

    override func setUpWithError() throws {
        temp = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-refusal-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: temp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: temp) }

    private func goodPython() -> BringUpTests.FakePython {
        let py = BringUpTests.FakePython()
        py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        return py
    }

    private func store(python: String? = "/opt/homebrew/bin/python3") -> SettingsStore {
        let s = SettingsStore(defaults: UserDefaults(suiteName: "jusage-refusal-\(UUID().uuidString)")!)
        if let python { s.update { $0.pythonPath = python } }
        return s
    }

    /// Runs bring-up and the host's refusal path; returns the line, after checking it was said loudly.
    private func refused(_ bundle: Bundle, settings: SettingsStore? = nil, python: PythonChecking? = nil,
                         file: StaticString = #filePath, line: UInt = #line) -> String? {
        var themed = false
        let r = BringUp.check(bundle: bundle, settings: settings ?? store(), python: python ?? goodPython(),
                              useThemes: { _ in themed = true })
        guard case .failure(let refusal) = r else { XCTFail("bring-up did not refuse", file: file, line: line); return nil }
        let log = RecordingLog()
        let model = AppModel(now: Fixture.now)
        HostFailure.refuse(refusal, log: log, model: model, now: Fixture.now)
        XCTAssertTrue(refusal.line.hasPrefix("jusage: "), refusal.line, file: file, line: line)
        XCTAssertFalse(refusal.line.contains("\n"), "one line", file: file, line: line)
        XCTAssertEqual(log.errors, ["[bringup] \(refusal.line)"], "logged once", file: file, line: line)
        XCTAssertEqual(model.runError, refusal.line, "the panel shows it", file: file, line: line)
        XCTAssertFalse(themed, "a refused bring-up changes nothing", file: file, line: line)
        return refusal.line
    }

    func testEngineMissingRefuses() throws {
        let line = refused(try FakeAppBundle.make(in: temp, engine: false))
        XCTAssertEqual(line, "jusage: the engine is missing from the app (Contents/Resources/engine/claude_usage.py). Reinstall jusage.")
    }

    func testBundleVersionMismatchRefuses() throws {
        let line = refused(try FakeAppBundle.make(in: temp, version: "0.2.0", engineVersion: "0.2.1"))
        XCTAssertEqual(line, "jusage: the app is 0.2.0 but its engine is 0.2.1. Reinstall jusage.")
        XCTAssertNotNil(refused(try FakeAppBundle.make(in: temp, engineVersion: nil)), "no VERSION at all")
    }

    func testRecordedPythonGoneRefuses() throws {
        let line = refused(try FakeAppBundle.make(in: temp), settings: store(python: "/usr/local/bin/python3"))
        XCTAssertEqual(line, "jusage: the recorded Python /usr/local/bin/python3 is gone. Run install.sh again, or open jusage with --set-python <path>.")
    }

    func testDamagedSettingsRefuseAndKeepTheRecord() throws {
        let d = UserDefaults(suiteName: "jusage-refusal-\(UUID().uuidString)")!
        let damaged = Data(#"{"menuBarMode":"meter:","pythonPath":"/opt/homebrew/bin/python3"}"#.utf8)
        d.set(damaged, forKey: SettingsStore.key)
        let line = try XCTUnwrap(refused(try FakeAppBundle.make(in: temp), settings: SettingsStore(defaults: d)))
        XCTAssertTrue(line.contains("settings"), line)
        XCTAssertTrue(line.contains(SettingsStore.corruptKeyPrefix), "names where the copy is: \(line)")
        XCTAssertEqual(d.data(forKey: SettingsStore.key), damaged)
        XCTAssertNotNil(BringUp.setPython("/opt/homebrew/bin/python3", settings: SettingsStore(defaults: d), python: goodPython()),
                        "--set-python refuses too, it would overwrite the record")
        XCTAssertEqual(d.data(forKey: SettingsStore.key), damaged)
    }

    func testBadThemesRefuse() throws {
        let line = try XCTUnwrap(refused(try FakeAppBundle.make(in: temp, themes: #"{"order": ["nope"], "themes": {}}"#)))
        XCTAssertTrue(line.contains("themes.json") || line.contains("nope"), line)
        XCTAssertNotNil(refused(try FakeAppBundle.make(in: temp, themes: nil)), "no themes.json at all")
    }

    func testFeedSchemaMismatchRefusesAndLogsOnce() throws {
        let model = AppModel(now: Fixture.now)
        let log = RecordingLog()
        let good = try Fixture.data("tests/fixtures/feed-v2.json")
        model.apply(.ok(good), now: Fixture.now)
        XCTAssertTrue(HostFailure.ranOK(.ok(good), model: model, log: log))
        let bad = RunResult.ok(try Fixture.bytes { $0["schema"] = 1 })
        model.apply(bad, now: Fixture.now)
        XCTAssertFalse(HostFailure.ranOK(bad, model: model, log: log))
        XCTAssertEqual(model.runError, "Engine sent feed schema 1; this app reads 2")
        XCTAssertNotNil(model.feed, "the last good feed stays")
        XCTAssertEqual(log.errors, ["[engine] run failed: Engine sent feed schema 1; this app reads 2"])
    }
}
