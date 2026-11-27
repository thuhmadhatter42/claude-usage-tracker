import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

@MainActor
final class ReportTests: XCTestCase {
    private var dir: URL!
    private var realDeliver: ((String, String, [URL], Bool) -> HouseReport.Outcome)!

    override func setUp() async throws {
        realDeliver = HouseReport.deliver
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("ReportTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: dir)
        HouseReport.deliver = realDeliver
    }

    func testTheDestinationIsTheOneHouseConstant() {
        XCTAssertTrue(["contact@fourletterrecords.com", "js-claude@agentmail.to"].contains(House.reportTo), House.reportTo)
    }

    func testSubjectAndBodyCarryWhatAReaderNeeds() {
        XCTAssertEqual(HouseReport.subject(version: "1.2.3"), "\(House.appName) 1.2.3 report")
        let body = HouseReport.body(text: "  It froze on export.  ", version: "1.2.3", macOS: "Version 12.7.6", extra: ["Interface: Apollo x8"])
        let lines = body.components(separatedBy: "\n")
        XCTAssertEqual(lines[0], "It froze on export.", "the user's words first")
        XCTAssertTrue(body.contains("\(House.appName) 1.2.3\nmacOS Version 12.7.6\nInterface: Apollo x8\n"), body)
        XCTAssertTrue(HouseReport.body(text: " \n", version: "1", macOS: "x").hasPrefix("(no description given)\n"))
    }

    func testThisLaunchsLogAlwaysGoesAndOlderOnesStopAtTheCap() {
        let a = URL(fileURLWithPath: "/l/app.log"), b = URL(fileURLWithPath: "/l/app.1.log"),
            c = URL(fileURLWithPath: "/l/app.2.log"), d = URL(fileURLWithPath: "/l/app.3.log")
        let sizes: [URL: UInt64] = [a: 900, b: 300, c: 800, d: 100]
        let (chosen, leftOut) = HouseReport.logSelection([a, b, c, d], sizes: sizes, cap: 1000)
        XCTAssertEqual(chosen, [a, d], "the newest even though it is nearly the whole cap; then whatever still fits")
        XCTAssertEqual(leftOut, 2)
        XCTAssertEqual(HouseReport.logSelection([a], sizes: [a: 5000], cap: 1000).chosen, [a], "never an empty report")
        XCTAssertEqual(HouseReport.logSelection([], sizes: [:]).chosen, [])
    }

    func testTheFolderHoldsTheBodyAndACopyOfEachLog() throws {
        let log = dir.appendingPathComponent("app.log")
        try "line\n".write(to: log, atomically: true, encoding: .utf8)
        let (folder, problems) = HouseReport.writeFolder(body: "hello", logs: [log], parent: dir)
        let f = try XCTUnwrap(folder)
        XCTAssertEqual(problems, [])
        XCTAssertTrue(f.lastPathComponent.hasPrefix("\(House.appName) report 20"), f.lastPathComponent)
        XCTAssertEqual(try String(contentsOf: f.appendingPathComponent("report.txt"), encoding: .utf8), "hello")
        XCTAssertEqual(try String(contentsOf: f.appendingPathComponent("app.log"), encoding: .utf8), "line\n")
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.appendingPathComponent("NOT SAVED - read me.txt").path))
    }

    /// A log that cannot be copied is never silent: the folder itself says what is missing.
    func testAMissingLogIsNamedInTheFolder() throws {
        let gone = dir.appendingPathComponent("gone.log")
        let (folder, problems) = HouseReport.writeFolder(body: "hello", logs: [gone], parent: dir)
        let f = try XCTUnwrap(folder)
        XCTAssertEqual(problems.count, 1)
        XCTAssertTrue(problems[0].hasPrefix("gone.log was not copied"), problems[0])
        let note = try String(contentsOf: f.appendingPathComponent("NOT SAVED - read me.txt"), encoding: .utf8)
        XCTAssertTrue(note.contains("gone.log was not copied"), note)
        XCTAssertTrue(FileManager.default.fileExists(atPath: f.appendingPathComponent("report.txt").path), "what could be saved, is")
    }

    func testAFolderThatCannotBeMadeReturnsNil() throws {
        let file = dir.appendingPathComponent("not-a-folder")
        try "x".write(to: file, atomically: true, encoding: .utf8)
        let (folder, problems) = HouseReport.writeFolder(body: "hello", logs: [], parent: file)
        XCTAssertNil(folder)
        XCTAssertEqual(problems.count, 1)
    }

    /// Send goes through the seam with the subject, the body and the logs; nothing real is opened.
    func testSendHandsEverythingToTheSeam() {
        var got: (String, String, [URL], Bool)?
        HouseReport.deliver = { s, b, l, d in got = (s, b, l, d); return .composed(logs: l.count) }
        let outcome = HouseReport.send(text: "the meter stuck", toDesktop: false, extra: ["Buffer: 64"])
        XCTAssertEqual(got?.0, HouseReport.subject())
        XCTAssertTrue(got?.1.hasPrefix("the meter stuck\n") ?? false)
        XCTAssertTrue(got?.1.contains("Buffer: 64") ?? false)
        XCTAssertEqual(got?.3, false)
        if case .composed = outcome {} else { XCTFail("\(outcome)") }
        HouseReport.deliver = { _, _, _, d in XCTAssertTrue(d); return .failed("disk full") }
        XCTAssertEqual(HouseReport.send(text: "x", toDesktop: true), .failed("disk full"))
    }

    func testTheSheetKeepsTheDraftWhenDeliveryFailsAndClearsItWhenItWorks() {
        let center = ReportCenter()
        center.open()
        XCTAssertTrue(center.showing)
        center.draft = "words"
        let sheet = ReportSheet(center: center)
        render(sheet)
        render(ReportButton())
        HouseReport.deliver = { _, _, _, _ in .failed("no disk") }
        sheet.finish(toDesktop: true)
        XCTAssertEqual(center.draft, "words", "a failed delivery loses nothing")
        XCTAssertTrue(center.showing)
        HouseReport.deliver = { _, _, l, _ in .composed(logs: l.count) }
        sheet.finish(toDesktop: false)
        XCTAssertEqual(center.draft, "")
        XCTAssertFalse(center.showing)
    }

    /// Lay the view out in an off-screen hosting view (no window): its body really runs.
    private func render<V: View>(_ view: V) {
        let host = NSHostingView(rootView: view.accentRoot())
        host.frame = NSRect(x: 0, y: 0, width: 600, height: 600)
        host.layoutSubtreeIfNeeded()
    }
}
