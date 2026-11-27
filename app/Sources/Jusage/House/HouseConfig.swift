import Foundation

/// The ONE file of per-app values. Every house-kit module reads its app-specific values from here,
/// so pasting a module never means hunting for strings inside it.
/// Copy this file into the app, then change every line marked CHANGE.
enum House {
    /// The app's name as the menus and sheets print it.                                   CHANGE
    static let appName = "jusage"
    /// The bundle identifier (also the OSLog subsystem and the UserDefaults domain).     CHANGE
    static let bundleID = "com.goatdguild.jusage"
    /// Where a Report goes. One constant, two legal values:                               CHANGE
    ///   an app other people use  -> "contact@fourletterrecords.com"
    ///   an internal app (J only) -> "js-claude@agentmail.to"
    static let reportTo = "contact@fourletterrecords.com"
    /// The folder name under ~/Library/Logs (and the log file's stem, lower-cased).      CHANGE if not appName
    /// It follows the running bundle id: a `.dev` build logs to `<appName>-dev`, never the release log (RULINGS 46).
    static var logFolder: String { logFolder(for: Bundle.main.bundleIdentifier) }

    static func logFolder(for bundleID: String?) -> String {
        (bundleID ?? "").hasSuffix(".dev") ? appName + "-dev" : appName
    }
    /// Environment variable that points the log somewhere else (tests, a second copy).   CHANGE
    static let logDirEnv = "JUSAGE_LOG_DIR"
    /// Who made it, on the About panel.
    static let maker = "Four Letter Records"

    /// The version on screen: Info.plist (a built app), else the bundled VERSION file, else "0.0.0".
    /// The build writes VERSION into Info.plist; nothing else in the app carries a version string.
    static var version: String { version(in: .main) }

    static func version(in bundle: Bundle) -> String {
        if let v = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String, !v.isEmpty { return v }
        if let u = bundle.url(forResource: "VERSION", withExtension: nil),
           let s = try? String(contentsOf: u, encoding: .utf8) {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { return t }
        }
        return "0.0.0"
    }

    /// The values above that are still placeholders. Doctor and ConfigTests fail on any.
    /// (The placeholder words are built from two halves so a search-and-replace over this file cannot break the check.)
    static var unfilled: [String] {
        let name = "APP" + "_NAME", id = "BUNDLE" + "_ID"
        var out: [String] = []
        if appName.contains(name) { out.append("House.appName") }
        if bundleID.contains(id) { out.append("House.bundleID") }
        if logDirEnv.contains(name) { out.append("House.logDirEnv") }
        if !["contact@fourletterrecords.com", "js-claude@agentmail.to"].contains(reportTo) { out.append("House.reportTo") }
        return out
    }
}
