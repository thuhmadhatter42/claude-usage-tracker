import XCTest
@testable import Jusage

/// Wave 4 end to end, app side: the REAL engine's `menu-json` decodes in the app, and the app greys a number at
/// exactly the feed's own `rules.stale_after_s` (900 s; RULING 33). The engine runs in a temp HOME with a fake
/// `security` first on PATH and no Claude account, so it never reaches the real Keychain, endpoint, ~/.claude*
/// or install. (tests/engine/test_end_to_end.py checks the same engine output against the golden feed's shape.)
final class EndToEndDecodeTests: XCTestCase {
    private var home: URL!

    override func setUpWithError() throws {
        home = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-e2e-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home.appendingPathComponent("bin"), withIntermediateDirectories: true)
        let security = home.appendingPathComponent("bin/security")
        try "#!/bin/sh\necho \"fake security: refused\" >&2\nexit 64\n".write(to: security, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: security.path)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: home) }

    /// The first python3 on PATH that is Python 3.9+ (the same check the app's bring-up makes).
    private func python() throws -> URL {
        let path = ProcessInfo.processInfo.environment["PATH"] ?? "/usr/bin:/bin"
        for dir in path.split(separator: ":") {
            let p = URL(fileURLWithPath: String(dir)).appendingPathComponent("python3")
            if SystemPython().exists(p.path) && SystemPython().passes(p.path) { return p }
        }
        throw XCTSkip("no Python 3.9+ on PATH")
    }

    private func engine(_ args: [String]) throws -> (status: Int32, out: Data, err: String) {
        let p = Process()
        p.executableURL = try python()
        p.arguments = ["-B", "-E", "-s", Fixture.root.appendingPathComponent("engine/claude_usage.py").path] + args
        p.environment = ["HOME": home.path, "LANG": "en_US.UTF-8", "PATH": home.appendingPathComponent("bin").path + ":/usr/bin:/bin"]
        let out = Pipe(), err = Pipe()
        p.standardOutput = out; p.standardError = err; p.standardInput = FileHandle.nullDevice
        try p.run()
        let o = out.fileHandleForReading.readDataToEndOfFile()
        let e = err.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, o, String(decoding: e, as: UTF8.self))
    }

    func testRealEngineFeedDecodes() throws {
        let r = try engine(["menu-json"])
        XCTAssertEqual(r.status, 0, r.err)
        let f = try Feed.decode(r.out)
        XCTAssertEqual(f.rules.staleAfterS, 900)
        XCTAssertTrue(f.accounts.isEmpty, "a temp HOME has no Claude account")
        XCTAssertEqual(f.version, try String(contentsOf: Fixture.root.appendingPathComponent("VERSION"), encoding: .utf8)
                        .trimmingCharacters(in: .whitespacesAndNewlines))
        // the app model takes it as a good run
        let model = MainActor.assumeIsolated { AppModel(now: Date()) }
        MainActor.assumeIsolated { model.apply(.ok(r.out), now: Date()) }
        XCTAssertNil(MainActor.assumeIsolated { model.runError })
    }

    func testGoldenFeedDecodesAndGreysAtItsOwnRule() throws {
        let f = try Fixture.feed()
        let account = f.accounts[0]
        let meter = try XCTUnwrap(account.meters.first { $0.id == "session" })
        let rule = f.rules.staleAfterS
        XCTAssertEqual(rule, 900)
        XCTAssertEqual(f.freshness(of: meter, in: account, now: meter.readAt.addingTimeInterval(rule - 1)), .fresh)
        guard case .stale = f.freshness(of: meter, in: account, now: meter.readAt.addingTimeInterval(rule)) else {
            return XCTFail("900 s old must be stale, in the app as in the engine and the page")
        }
    }

    func testWrongEngineSchemaRefusedLoudly() throws {
        // The real engine's feed with its schema bumped: refused, last good kept, the reason in one line.
        let r = try engine(["menu-json"])
        var j = try JSONSerialization.jsonObject(with: r.out) as! [String: Any]
        j["schema"] = 3
        let model = MainActor.assumeIsolated { AppModel(now: Date()) }
        MainActor.assumeIsolated {
            model.apply(.ok(r.out), now: Date())
            model.apply(.ok(try! JSONSerialization.data(withJSONObject: j)), now: Date())
        }
        XCTAssertEqual(MainActor.assumeIsolated { model.runError }, "Engine sent feed schema 3; this app reads 2")
        XCTAssertNotNil(MainActor.assumeIsolated { model.feed }, "the last good feed stays on screen")
    }
}
