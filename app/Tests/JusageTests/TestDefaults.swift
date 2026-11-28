import Foundation

/// Every UserDefaults suite a test makes goes through here. A suite name that is an absolute path is stored
/// at that path, so test suites live in a temp folder and never land in ~/Library/Preferences.
/// (Before this, every run left jusage-*.plist files there: about 3,700 by 2026-11-27. Deleting them after the
/// run does not work: cfprefsd writes them back after the test process exits.)
enum TestDefaults {
    static let folder: URL = {
        let u = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-test-defaults", isDirectory: true)
        try? FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
        return u
    }()

    static func make(_ name: String) -> UserDefaults? {
        let safe = name.replacingOccurrences(of: "/", with: "_")
        return UserDefaults(suiteName: folder.appendingPathComponent(safe).path)
    }
}
