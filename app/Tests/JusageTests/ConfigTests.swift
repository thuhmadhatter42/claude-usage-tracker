import XCTest
@testable import Jusage   // CHANGE: the app's module name

final class ConfigTests: XCTestCase {
    func testNoPlaceholderLeft() {
        XCTAssertEqual(House.unfilled, [], "fill in HouseConfig.swift")
    }

    func testReportGoesToOneOfTheTwoHouseAddresses() {
        XCTAssertTrue(["contact@fourletterrecords.com", "js-claude@agentmail.to"].contains(House.reportTo))
    }

    func testVersionIsThreeNumbers() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("ConfigTests-\(UUID().uuidString).bundle")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try "1.2.3\n".write(to: dir.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
        let b = try XCTUnwrap(Bundle(url: dir))
        XCTAssertEqual(House.version(in: b), "1.2.3", "falls back to the bundled VERSION file")
        XCTAssertFalse(House.version.isEmpty)
    }
}
