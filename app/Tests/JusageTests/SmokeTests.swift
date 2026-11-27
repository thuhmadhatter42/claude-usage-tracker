import XCTest
@testable import Jusage

/// S16 `--smoke-test <file>` (R-167): the built app checks itself with no window and writes OK or FAIL.
final class SmokeTests: XCTestCase {
    private var temp: URL!

    override func setUpWithError() throws {
        temp = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-smoke-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: temp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: temp) }

    func testSmokeWritesOK() throws {
        let out = temp.appendingPathComponent("smoke.txt")
        let rc = Smoke.write([SmokeCheck("engine", true, "claude_usage.py"), SmokeCheck("version", true, "0.2.0")],
                             version: "0.2.0", to: out, log: RecordingLog())
        XCTAssertEqual(rc, 0)
        XCTAssertEqual(try String(contentsOf: out, encoding: .utf8),
                       "OK\njusage 0.2.0\nok   engine: claude_usage.py\nok   version: 0.2.0\n")
    }

    func testSmokeWritesFailAndLogs() throws {
        let out = temp.appendingPathComponent("smoke.txt")
        let log = RecordingLog()
        let rc = Smoke.write([SmokeCheck("engine", false, "missing")], version: "0.2.0", to: out, log: log)
        XCTAssertEqual(rc, 1)
        XCTAssertTrue(try String(contentsOf: out, encoding: .utf8).hasPrefix("FAIL\njusage 0.2.0\nFAIL engine: missing\n"))
        XCTAssertEqual(log.errors, ["[smoke] engine: missing"])
    }

    func testBundleChecksPassOnAGoodBundle() throws {
        let py = BringUpTests.FakePython(); py.existing = ["/opt/homebrew/bin/python3"]; py.passing = py.existing
        let checks = Smoke.bundleChecks(bundle: try FakeAppBundle.make(in: temp), python: py)
        XCTAssertEqual(checks.map(\.name), ["bring-up", "python"])
        XCTAssertTrue(checks.allSatisfy(\.ok), "\(checks)")
        XCTAssertEqual(py.probed, ["/opt/homebrew/bin/python3"], "probes, but records nothing anywhere")
    }

    func testBundleChecksNameTheRefusal() throws {
        let checks = Smoke.bundleChecks(bundle: try FakeAppBundle.make(in: temp, engineVersion: "0.1.39"), python: BringUpTests.FakePython())
        XCTAssertEqual(checks.first?.ok, false)
        XCTAssertEqual(checks.first?.detail, "jusage: the app is 0.2.0 but its engine is 0.1.39. Reinstall jusage.")
        XCTAssertEqual(checks.last?.ok, false, "no Python found is a smoke failure too")
    }

    func testDevBundleChecksItsStateDir() throws {
        let checks = Smoke.bundleChecks(bundle: try FakeAppBundle.make(in: temp, bundleID: "com.goatdguild.jusage.dev"),
                                        python: BringUpTests.FakePython())
        XCTAssertEqual(checks.first?.ok, false)
        XCTAssertTrue(checks.first?.detail.contains("has no JusageStateDir") ?? false)
    }

    // MARK: S16 arguments

    func testLaunchArgs() {
        let a = LaunchArgs(["/x/Jusage", "--set-python", "/opt/homebrew/bin/python3", "--check-for-updates"])
        XCTAssertNil(a.smokeOutput)
        XCTAssertEqual(a.setPython, "/opt/homebrew/bin/python3")
        XCTAssertTrue(a.checkForUpdates)
        XCTAssertEqual(LaunchArgs(["/x/Jusage", "--smoke-test", "/tmp/s.txt"]).smokeOutput?.path, "/tmp/s.txt")
        let bare = LaunchArgs(["/x/Jusage", "--smoke-test"])
        XCTAssertNil(bare.smokeOutput)
        XCTAssertEqual(bare.problems, ["--smoke-test needs a file"])
        XCTAssertTrue(LaunchArgs(["/x/Jusage", "--login-item-off"]).loginItemOff)
        XCTAssertFalse(a.loginItemOff)
        XCTAssertEqual(LaunchArgs(["/x/Jusage", "--window"]).problems, ["unknown argument --window"], "--window is dropped")
        XCTAssertEqual(LaunchArgs(["/x/Jusage", "-NSDocumentRevisionsDebugMode", "YES"]).problems, [], "AppKit's own -NS… pairs pass")
    }
}
