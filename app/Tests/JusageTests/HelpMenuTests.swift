import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

@MainActor
final class HelpMenuTests: XCTestCase {
    private var dir: URL!
    private var realOpener: ((URL) -> Bool)!

    override func setUp() async throws {
        realOpener = HouseHelp.opener
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("HelpMenuTests-\(UUID().uuidString).bundle")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        HouseHelp.opener = realOpener
        try? FileManager.default.removeItem(at: dir)
    }

    /// A fresh folder per bundle: Bundle caches what a folder held the first time it looked.
    private func bundle(_ files: [String], version: String? = nil) throws -> Bundle {
        let d = dir.appendingPathComponent("\(UUID().uuidString).bundle")
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        for f in files { try "x".write(to: d.appendingPathComponent(f), atomically: true, encoding: .utf8) }
        if let version { try "\(version)\n".write(to: d.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8) }
        return try XCTUnwrap(Bundle(url: d))
    }

    func testAPageOpensTheFirstNameThatIsBundled() throws {
        let b = try bundle(["Manual.html", "WhatsNew.html"])
        var opened: [String] = []
        HouseHelp.opener = { opened.append($0.lastPathComponent); return true }
        XCTAssertTrue(HouseHelp.open(HouseHelp.manual, in: b))
        XCTAssertTrue(HouseHelp.open(HouseHelp.whatsNew, in: b))
        XCTAssertEqual(opened, ["Manual.html", "WhatsNew.html"])
    }

    /// A page that is not in the bundle opens nothing and says so (it is logged as an error).
    func testAMissingPageIsReportedNotSilent() throws {
        let b = try bundle(["Manual.pdf"])
        var opened = 0
        HouseHelp.opener = { _ in opened += 1; return true }
        XCTAssertFalse(HouseHelp.open(HouseHelp.licenses, in: b))
        XCTAssertEqual(opened, 0)
        XCTAssertNil(HouseHelp.url(for: HouseHelp.licenses, in: b))
        XCTAssertEqual(HouseHelp.url(for: HouseHelp.manual, in: b)?.lastPathComponent, "Manual.pdf")
    }

    func testTheHouseSetIsManualWhatsNewLicenses() {
        XCTAssertEqual(HouseHelp.pages.map(\.title), ["Manual", "What's New", "Licenses"])
    }

    func testDoctorFailsOnAnEmptyBundleAndPassesOnAFullOne() throws {
        let empty = try bundle([])
        let before = HouseDoctor.run(HouseDoctor.standardChecks(bundle: empty, log: AppLog(directory: dir.appendingPathComponent("logs"))))
        let failed = before.filter { $0.result.state == .fail }.map(\.name)
        XCTAssertTrue(failed.contains("Uninstaller"), "\(failed)")
        XCTAssertTrue(failed.contains("Manual") && failed.contains("What's New") && failed.contains("Licenses"), "\(failed)")
        XCTAssertTrue(failed.contains("Version"), "\(failed)")

        let full = try bundle(["Manual.pdf", "WhatsNew.html", "Licenses.txt", "uninstall.sh", "uninstall.manifest"], version: "1.2.3")
        let after = HouseDoctor.run(HouseDoctor.standardChecks(bundle: full, log: AppLog(directory: dir.appendingPathComponent("logs"))))
        let bad = after.filter { $0.result.state == .fail }.map { "\($0.name): \($0.result.detail)" }
        XCTAssertEqual(bad, [], "everything the kit needs is there")
        XCTAssertEqual(after.first { $0.name == "Version" }?.result.detail, "1.2.3")
        XCTAssertEqual(after.first { $0.name == "Log" }?.result.state, .ok)
    }

    func testAnAppCheckRunsWithTheStandardOnes() {
        let c = DoctorCenter()
        c.run([DoctorCheck(name: "Helper tool") { .init(state: .fail, detail: "ffmpeg is missing.") },
               DoctorCheck(name: "Server") { .init(state: .ok, detail: "Reachable.") }])
        XCTAssertTrue(c.showing)
        XCTAssertEqual(c.lines.map(\.name), ["Helper tool", "Server"])
        XCTAssertEqual(c.failures, 1)
        render(DoctorSheet(center: c))
    }

    /// Lay the view out in an off-screen hosting view (no window): its body really runs.
    private func render<V: View>(_ view: V) {
        let host = NSHostingView(rootView: view.accentRoot())
        host.frame = NSRect(x: 0, y: 0, width: 600, height: 600)
        host.layoutSubtreeIfNeeded()
    }
}
