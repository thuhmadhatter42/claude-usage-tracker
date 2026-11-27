import XCTest
@testable import Jusage

/// W3-C: the Doctor checks jusage adds (engine verdicts, the update keys).
final class HouseJusageDoctorTests: XCTestCase {
    func testEngineOkLinesPass() {
        let r = JusageDoctor.engineCheck(run: { ["OK #1 (claude): login, identity and gate are healthy", "OK #2 (work): login, identity and gate are healthy"] }).run()
        XCTAssertEqual(r.state, .ok)
    }

    func testEngineWarnShowsTheFirstWarning() {
        let r = JusageDoctor.engineCheck(run: { ["OK a: fine", "WARN b: legacy keychain item", "WARN c: stale"] }).run()
        XCTAssertEqual(r.state, .warn)
        XCTAssertTrue(r.detail.contains("legacy keychain item"), r.detail)
    }

    func testEngineFailBeatsWarn() {
        let r = JusageDoctor.engineCheck(run: { ["WARN b: x", "FAIL c: no login", "OK a: fine"] }).run()
        XCTAssertEqual(r.state, .fail)
        XCTAssertTrue(r.detail.contains("no login"), r.detail)
    }

    func testEngineThatSaysNothingFails() {
        XCTAssertEqual(JusageDoctor.engineCheck(run: { [] }).run().state, .fail)
        XCTAssertEqual(JusageDoctor.engineCheck(run: { ["", "  "] }).run().state, .fail)
    }

    func testEngineThatCannotRunFails() {
        let r = JusageDoctor.engineCheck(run: { () throws -> [String] in throw JusageDoctor.EngineError.message("python not found") }).run()
        XCTAssertEqual(r.state, .fail)
        XCTAssertTrue(r.detail.contains("python not found"), r.detail)
    }

    func testEngineCheckNeverPrintsASecretLookalike() {
        // the engine never prints one; Doctor shows verdict lines only, truncated to one line
        let long = "FAIL x: " + String(repeating: "y", count: 400)
        XCTAssertLessThanOrEqual(JusageDoctor.engineCheck(run: { [long] }).run().detail.count, 200)
    }

    private func bundle(plist: [String: Any], ext: String = "app") throws -> Bundle {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("w3c-\(UUID().uuidString).\(ext)")
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("Contents"), withIntermediateDirectories: true)
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: dir.appendingPathComponent("Contents/Info.plist"))
        return try XCTUnwrap(Bundle(url: dir))
    }

    func testUpdatesOkWithFeedAndRealKey() throws {
        let b = try bundle(plist: ["CFBundleIdentifier": "x.y", "SUFeedURL": "https://example.com/appcast.xml", "SUPublicEDKey": "abcdEFGH0123456789abcdEFGH0123456789abcdEF="])
        XCTAssertEqual(JusageDoctor.updatesCheck(bundle: b).run().state, .ok)
    }

    func testUpdatesFailsOnPlaceholderKey() throws {
        let b = try bundle(plist: ["CFBundleIdentifier": "x.y", "SUFeedURL": "https://example.com/appcast.xml", "SUPublicEDKey": "__SU_PUBLIC_ED_KEY__"])
        let r = JusageDoctor.updatesCheck(bundle: b).run()
        XCTAssertEqual(r.state, .fail)
        XCTAssertTrue(r.detail.contains("SUPublicEDKey"), r.detail)
    }

    func testUpdatesFailsWithoutFeed() throws {
        let b = try bundle(plist: ["CFBundleIdentifier": "x.y", "SUPublicEDKey": "abcdEFGH0123456789abcdEFGH0123456789abcdEF="])
        XCTAssertEqual(JusageDoctor.updatesCheck(bundle: b).run().state, .fail)
    }

    func testUpdatesOnlyWarnsOutsideABundledApp() {
        XCTAssertEqual(JusageDoctor.updatesCheck(bundle: Bundle(for: HouseJusageDoctorTests.self)).run().state, .warn)
    }

    func testInstallSetsTheAppChecks() {
        let saved = HouseDoctor.appChecks
        defer { HouseDoctor.appChecks = saved }
        JusageDoctor.install(engine: { ["OK a: fine"] })
        XCTAssertEqual(HouseDoctor.appChecks.map(\.name), ["Engine", "Updates"])
    }
}
