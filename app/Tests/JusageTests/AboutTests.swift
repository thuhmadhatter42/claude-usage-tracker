import AppKit
import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

final class AboutTests: XCTestCase {
    func testTheAboutPanelShowsTheNameVersionAndMaker() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("AboutTests-\(UUID().uuidString).bundle")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try "0.4.7\n".write(to: dir.appendingPathComponent("VERSION"), atomically: true, encoding: .utf8)
        let o = HouseAbout.options(bundle: try XCTUnwrap(Bundle(url: dir)))
        XCTAssertEqual(o[.applicationName] as? String, House.appName)
        XCTAssertEqual(o[.applicationVersion] as? String, "0.4.7")
        XCTAssertEqual((o[.credits] as? NSAttributedString)?.string, "Four Letter Records")
    }

    @MainActor
    func testTheVersionLabelBuilds() {
        let host = NSHostingView(rootView: VersionLabel())
        host.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(host.fittingSize.width, 0)
    }
}
