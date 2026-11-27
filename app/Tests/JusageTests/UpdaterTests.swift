import XCTest
@testable import Jusage   // CHANGE: the app's module name

final class UpdaterTests: XCTestCase {
    /// Under tests (not a bundled .app with a feed) there is no updater: nothing phones home, nothing crashes.
    func testNoUpdaterOutsideABundledAppWithAFeed() {
        XCTAssertFalse(AppUpdater.isUpdatable(bundle: Bundle(for: UpdaterTests.self)))
        let u = AppUpdater(bundle: Bundle(for: UpdaterTests.self))
        XCTAssertFalse(u.canCheck)
        u.checkInBackgroundAtLaunch()        // a no-op, logged
        u.checkForUpdates()                  // a no-op, logged
    }

    private func bundle(id: String, automatic: Bool?) throws -> Bundle {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("upd-\(UUID().uuidString).app")
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("Contents"), withIntermediateDirectories: true)
        var plist: [String: Any] = ["CFBundleIdentifier": id]
        if let automatic { plist["SUEnableAutomaticChecks"] = automatic }
        try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            .write(to: dir.appendingPathComponent("Contents/Info.plist"))
        return try XCTUnwrap(Bundle(url: dir))
    }

    /// Review 14: a dev build must never offer to replace itself with the release.
    func testDevBuildsSkipTheLaunchCheck() throws {
        XCTAssertFalse(AppUpdater.launchCheckAllowed(bundle: try bundle(id: "com.example.jusage.dev", automatic: true)))
        XCTAssertFalse(AppUpdater.launchCheckAllowed(bundle: try bundle(id: "com.example.jusage", automatic: false)),
                       "automatic checks off in the plist also means no launch check")
        XCTAssertTrue(AppUpdater.launchCheckAllowed(bundle: try bundle(id: "com.example.jusage", automatic: true)))
        XCTAssertTrue(AppUpdater.launchCheckAllowed(bundle: try bundle(id: "com.example.jusage", automatic: nil)))
    }

    /// The shipped app must carry the four Sparkle keys. Runs against the built app when APP_INFO_PLIST points at it
    /// (the release script sets it); skipped in a plain unit-test run.
    func testTheBuiltAppCarriesTheSparkleKeys() throws {
        guard let path = ProcessInfo.processInfo.environment["APP_INFO_PLIST"], !path.isEmpty else {
            throw XCTSkip("set APP_INFO_PLIST=<App>.app/Contents/Info.plist to check the built app")
        }
        let plist = try XCTUnwrap(NSDictionary(contentsOfFile: path) as? [String: Any], "unreadable \(path)")
        let feed = try XCTUnwrap(plist["SUFeedURL"] as? String, "SUFeedURL missing")
        XCTAssertTrue(feed.hasPrefix("https://"), "the feed is https: \(feed)")
        let key = try XCTUnwrap(plist["SUPublicEDKey"] as? String, "SUPublicEDKey missing")
        XCTAssertEqual(Data(base64Encoded: key)?.count, 32, "an Ed25519 public key is 32 bytes")
        XCTAssertEqual(plist["SUEnableAutomaticChecks"] as? Bool, true)
        XCTAssertEqual(plist["SUScheduledCheckInterval"] as? Int, 86400)
    }
}
