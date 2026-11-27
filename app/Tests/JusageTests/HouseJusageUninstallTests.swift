import XCTest
@testable import Jusage

/// W3-C: what jusage does around the kit's uninstall script: restore the status line first, drop the login
/// item, remove its alias lines (the rc files go to the Trash first, never deleted).
final class HouseJusageUninstallTests: XCTestCase {
    private var home: URL!
    override func setUpWithError() throws {
        home = FileManager.default.temporaryDirectory.appendingPathComponent("w3c-un-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home.appendingPathComponent(".Trash"), withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: home) }

    private func write(_ name: String, _ s: String) throws { try s.write(to: home.appendingPathComponent(name), atomically: true, encoding: .utf8) }
    private func read(_ name: String) throws -> String { try String(contentsOf: home.appendingPathComponent(name), encoding: .utf8) }
    private func trashed() throws -> [String] { try FileManager.default.contentsOfDirectory(atPath: home.appendingPathComponent(".Trash").path).sorted() }

    func testAliasLinesGoAndTheOriginalGoesToTheTrash() throws {
        try write(".zshrc", "export A=1\nalias jusage='\"/opt/x/jusage/jusage\"'\nexport B=2\n")
        try write(".bashrc", "alias jusage='\"/x/jusage\"'\n")
        let removed = try JusageUninstall.removeAliasLines(home: home)
        XCTAssertEqual(removed, [".zshrc", ".bashrc"])
        XCTAssertEqual(try read(".zshrc"), "export A=1\nexport B=2\n")
        XCTAssertEqual(try read(".bashrc"), "")
        XCTAssertEqual(try trashed().count, 2, "one Trash copy per rewritten file")
        let copy = try trashed().first { $0.hasPrefix(".zshrc") }
        XCTAssertNotNil(copy)
        XCTAssertTrue(try String(contentsOf: home.appendingPathComponent(".Trash/\(copy!)")).contains("alias jusage="))
    }

    func testFileWithoutAnAliasIsLeftAlone() throws {
        try write(".zshrc", "export A=1\n# alias jusage is mentioned in a comment\nalias jusager='x'\n")
        XCTAssertEqual(try JusageUninstall.removeAliasLines(home: home), [])
        XCTAssertEqual(try read(".zshrc"), "export A=1\n# alias jusage is mentioned in a comment\nalias jusager='x'\n")
        XCTAssertEqual(try trashed(), [])
    }

    func testMissingRcFilesAreFine() throws {
        XCTAssertEqual(try JusageUninstall.removeAliasLines(home: home), [])
    }

    func testPrepRunsStatusLineThenLoginItemThenAliasesInOrder() throws {
        try write(".zshrc", "alias jusage='x'\n")
        var order: [String] = []
        let prep = JusageUninstall.Prep(statusLineOff: { order.append("statusline"); return (0, "") },
                                        loginItemOff: { order.append("loginitem") },
                                        home: home)
        XCTAssertNil(prep.run())
        order.append("aliases-done:" + ((try? read(".zshrc")) ?? "?").replacingOccurrences(of: "\n", with: ""))
        XCTAssertEqual(order, ["statusline", "loginitem", "aliases-done:"])
    }

    func testStatusLineFailureStopsEverythingBeforeAnyChange() throws {
        try write(".zshrc", "alias jusage='x'\n")
        var loginItemTouched = false
        let prep = JusageUninstall.Prep(statusLineOff: { (1, "statusline off: the statusLine in x was changed since jusage wrapped it") },
                                        loginItemOff: { loginItemTouched = true }, home: home)
        let why = try XCTUnwrap(prep.run())
        XCTAssertTrue(why.contains("status line"), why)
        XCTAssertTrue(why.contains("was changed since"), why)
        XCTAssertFalse(loginItemTouched)
        XCTAssertEqual(try read(".zshrc"), "alias jusage='x'\n", "nothing changed")
    }

    func testRunnerWrapRunsPrepOnlyForARealRun() throws {
        var prepRan = 0
        var scriptRan = 0
        let prep = JusageUninstall.Prep(statusLineOff: { prepRan += 1; return (0, "") }, loginItemOff: {}, home: home)
        let wrapped = JusageUninstall.wrap(runner: { _, _, _ in scriptRan += 1; return (0, "DONE") }, prep: prep)
        let (rc, out) = wrapped(URL(fileURLWithPath: "/x"), [], false)
        XCTAssertEqual(rc, 0); XCTAssertEqual(out, "DONE")
        XCTAssertEqual(prepRan, 1); XCTAssertEqual(scriptRan, 1)
    }

    func testPrepFailureIsAFailedRunAndTheScriptNeverRuns() throws {
        var scriptRan = 0
        let prep = JusageUninstall.Prep(statusLineOff: { (1, "boom") }, loginItemOff: {}, home: home)
        let wrapped = JusageUninstall.wrap(runner: { _, _, _ in scriptRan += 1; return (0, "DONE") }, prep: prep)
        let (rc, out) = wrapped(URL(fileURLWithPath: "/x"), [], true)
        XCTAssertNotEqual(rc, 0)
        XCTAssertTrue(out.contains("boom"), out)
        XCTAssertEqual(scriptRan, 0)
    }

    // MARK: uninstall with no engine (review 11): the common reason to uninstall is that bring-up failed.

    private func writeConfig(_ json: String) throws -> String {
        let dir = home.appendingPathComponent("state")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try json.write(to: dir.appendingPathComponent("config.json"), atomically: true, encoding: .utf8)
        return dir.path
    }

    func testNoEngineAndNoWrappedStatusLineIsNothingToRestore() throws {
        let dir = try writeConfig(#"{"statusline": {"/h/.claude": {"enabled": false, "original": null, "wrapped": "x"}}}"#)
        XCTAssertEqual(JusageUninstall.statusLineOffWithoutEngine(stateDir: dir, home: home.path).0, 0)
        let none = home.appendingPathComponent("never-made").path
        XCTAssertEqual(JusageUninstall.statusLineOffWithoutEngine(stateDir: none, home: home.path).0, 0, "no config at all: never wrapped")
        let empty = try writeConfig("{}")
        XCTAssertEqual(JusageUninstall.statusLineOffWithoutEngine(stateDir: empty, home: home.path).0, 0)
    }

    func testNoEngineButAWrappedStatusLineSaysWhatToDoByHand() throws {
        let dir = try writeConfig(#"{"statusline": {"/h/.claude": {"enabled": true, "original": "echo hi", "wrapped": "x"}}}"#)
        let (rc, out) = JusageUninstall.statusLineOffWithoutEngine(stateDir: dir, home: home.path)
        XCTAssertNotEqual(rc, 0)
        XCTAssertTrue(out.contains("config.json"), out)
        XCTAssertTrue(out.contains("original"), out)
    }

    func testUnreadableConfigIsNotAssumedClean() throws {
        let dir = try writeConfig("not json")
        XCTAssertNotEqual(JusageUninstall.statusLineOffWithoutEngine(stateDir: dir, home: home.path).0, 0)
    }

    func testUninstallPrepProceedsWhenThereIsNothingToRestore() throws {
        try write(".zshrc", "alias jusage='x'\n")
        let dir = try writeConfig("{}")
        let prep = JusageUninstall.Prep(statusLineOff: { [home = home!] in JusageUninstall.statusLineOffWithoutEngine(stateDir: dir, home: home.path) },
                                        loginItemOff: {}, home: home)
        XCTAssertNil(prep.run())
        XCTAssertEqual(try read(".zshrc"), "")
    }
}
