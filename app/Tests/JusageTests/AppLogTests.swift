import XCTest
@testable import Jusage   // CHANGE: the app's module name

final class AppLogTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("AppLogTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() { try? FileManager.default.removeItem(at: dir) }

    private func text(_ log: AppLog) -> String { (try? String(contentsOf: log.fileURL, encoding: .utf8)) ?? "" }

    func testAnErrorLandsInTheFileWithLevelCategoryAndLocalTime() {
        let log = AppLog(directory: dir)
        log.error("export", "could not write /tmp/x: disk full")
        let line = text(log)
        XCTAssertTrue(line.contains(" ERROR [export] could not write /tmp/x: disk full\n"), line)
        // local time with an offset, never a bare Z-less stamp: 2026-11-18T20:52:01.123-08:00
        XCTAssertNotNil(line.range(of: #"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}([+-]\d\d:\d\d|Z) "#, options: .regularExpression), line)
    }

    func testSecretsNeverReachTheFile() {
        let log = AppLog(directory: dir)
        log.info("net", "Authorization: Bearer abc.def.ghi and password=hunter2 and sk-ant-api03-ABCDEFGH1234")
        let line = text(log)
        XCTAssertFalse(line.contains("abc.def.ghi"), line)
        XCTAssertFalse(line.contains("hunter2"), line)
        XCTAssertFalse(line.contains("ABCDEFGH1234"), line)
        XCTAssertTrue(line.contains("***"), line)
    }

    func testRotationKeepsThreeOldFiles() {
        let log = AppLog(directory: dir, maxBytes: 200)
        for i in 0..<60 { log.info("fill", "line \(i) " + String(repeating: "x", count: 40)) }
        let names = log.logFiles().map(\.lastPathComponent)
        XCTAssertTrue(names.contains("\(log.stem).log"), "\(names)")
        XCTAssertTrue(names.contains("\(log.stem).1.log"), "\(names)")
        XCTAssertLessThanOrEqual(names.count, 4, "the live file + 3 rotated: \(names)")
        XCTAssertTrue(text(log).contains("line 59"), "the newest line is in the live file")
    }

    func testTheFolderComesBackIfItVanishes() throws {
        let log = AppLog(directory: dir)
        log.info("a", "one")
        try FileManager.default.removeItem(at: dir)
        log.error("a", "two")
        XCTAssertTrue(text(log).contains("two"))
    }

    func testUnifiedLogOnlyWritesNoFile() {
        let log = AppLog(directory: dir)
        log.log(.info, "uninstall", "done", fileToo: false)
        XCTAssertFalse(FileManager.default.fileExists(atPath: log.fileURL.path))
    }

    func testStemIsTheFolderNameLowerCasedWithoutSpaces() {
        let log = AppLog(directory: dir)
        XCTAssertEqual(log.stem, House.logFolder.lowercased().replacingOccurrences(of: " ", with: "-"))
        XCTAssertFalse(log.stem.contains(" "))
    }
}

/// RULINGS 46: the log folder follows the running bundle id, so a `.dev` build never writes the release log.
final class DevLogFolderTests: XCTestCase {
    func testDevBuildLogsToItsOwnFolder() {
        XCTAssertEqual(House.logFolder(for: "com.goatdguild.jusage.dev"), "jusage-dev")
        XCTAssertEqual(House.logFolder(for: "com.goatdguild.jusage"), "jusage")
        XCTAssertEqual(House.logFolder(for: nil), "jusage")
    }
}
