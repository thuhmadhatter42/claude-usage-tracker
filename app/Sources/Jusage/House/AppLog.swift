import Foundation
import os

/// App-wide log: `~/Library/Logs/<House.logFolder>/<stem>.log` (rotating, 5 MB x 3) + OSLog subsystem `House.bundleID`.
/// Lifted from Jlipper (JlipperCore/Logging/AppLog.swift). House rule: everything we build logs its errors;
/// a `catch` or a failed write that says nothing is a bug.
///
///     AppLog.shared.error("export", "could not write \(url.path): \(error.localizedDescription)")
final class AppLog: @unchecked Sendable {
    static let shared = AppLog()
    static var subsystem: String { House.bundleID }

    enum Level: String, Codable, Sendable { case debug = "DEBUG", info = "INFO", warn = "WARN", error = "ERROR" }

    private let lock = NSLock()
    private let osLog: Logger
    let directory: URL
    /// File stem: the log folder's name in lower case, no spaces ("Beat Key" -> "beat-key.log").
    let stem: String
    var fileURL: URL { directory.appendingPathComponent("\(stem).log") }
    private let maxBytes: UInt64
    private let keep = 3

    /// `directory` wins, then the `House.logDirEnv` environment variable, then (under XCTest) a temp folder so a
    /// test run never writes into the user's real log, then ~/Library/Logs/<House.logFolder>.
    init(directory: URL? = nil, maxBytes: UInt64 = 5 * 1024 * 1024) {
        self.maxBytes = maxBytes
        self.osLog = Logger(subsystem: House.bundleID, category: "app")
        self.stem = House.logFolder.lowercased().replacingOccurrences(of: " ", with: "-")
        if let directory { self.directory = directory }
        else if let override = ProcessInfo.processInfo.environment[House.logDirEnv], !override.isEmpty {
            self.directory = URL(fileURLWithPath: override)
        } else if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil || NSClassFromString("XCTestCase") != nil {
            self.directory = FileManager.default.temporaryDirectory.appendingPathComponent("\(House.logFolder)-test-logs", isDirectory: true)
        } else {
            self.directory = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Logs/\(House.logFolder)", isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }

    /// `fileToo: false` = unified log only (used once the log folder itself has been uninstalled).
    func log(_ level: Level, _ category: String, _ message: String, fileToo: Bool = true) {
        let clean = Redactor.redact(message)
        switch level {
        case .debug: osLog.debug("[\(category, privacy: .public)] \(clean, privacy: .public)")
        case .info: osLog.info("[\(category, privacy: .public)] \(clean, privacy: .public)")
        case .warn: osLog.warning("[\(category, privacy: .public)] \(clean, privacy: .public)")
        case .error: osLog.error("[\(category, privacy: .public)] \(clean, privacy: .public)")
        }
        guard fileToo else { return }
        let line = "\(LocalTime.now()) \(level.rawValue) [\(category)] \(clean)\n"
        lock.lock(); defer { lock.unlock() }
        rotateIfNeeded()
        append(line)
    }

    func debug(_ category: String, _ message: String) { log(.debug, category, message) }
    func info(_ category: String, _ message: String) { log(.info, category, message) }
    func warn(_ category: String, _ message: String) { log(.warn, category, message) }
    func error(_ category: String, _ message: String) { log(.error, category, message) }

    /// The first line of every launch: what is running, on what. A Report then always says which build it was.
    func launchLine() {
        let os = ProcessInfo.processInfo.operatingSystemVersionString
        #if arch(arm64)
        let cpu = "arm64"
        #else
        let cpu = "x86_64"
        #endif
        info("launch", "\(House.appName) \(House.version) on macOS \(os) (\(cpu))")
    }

    /// Every log file, newest first: the live one, then the rotated copies. The Report attaches these.
    func logFiles() -> [URL] {
        let fm = FileManager.default
        let names = (try? fm.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.filter { $0.hasPrefix(stem) && $0.hasSuffix(".log") }.sorted().map { directory.appendingPathComponent($0) }
    }

    private func append(_ line: String) {
        let data = Data(line.utf8)
        let fm = FileManager.default
        // The folder can vanish under a running app (a cleaner, Uninstall with a failed step): put it back.
        if !fm.fileExists(atPath: directory.path) {
            do { try fm.createDirectory(at: directory, withIntermediateDirectories: true) }
            catch { osLog.error("log folder \(self.directory.path, privacy: .public) could not be made: \(error.localizedDescription, privacy: .public)"); return }
        }
        if let h = try? FileHandle(forWritingTo: fileURL) {
            defer { try? h.close() }
            do { _ = try h.seekToEnd(); try h.write(contentsOf: data) }
            catch { osLog.error("log write failed: \(error.localizedDescription, privacy: .public)") }
        } else {
            do { try data.write(to: fileURL) }
            catch { osLog.error("log file \(self.fileURL.path, privacy: .public) could not be created: \(error.localizedDescription, privacy: .public)") }
        }
    }

    private func rotateIfNeeded() {
        let fm = FileManager.default
        guard let size = (try? fm.attributesOfItem(atPath: fileURL.path)[.size] as? UInt64), size > maxBytes else { return }
        for i in stride(from: keep - 1, through: 1, by: -1) {
            let src = directory.appendingPathComponent("\(stem).\(i).log")
            let dst = directory.appendingPathComponent("\(stem).\(i + 1).log")
            try? fm.removeItem(at: dst)
            try? fm.moveItem(at: src, to: dst)
        }
        let first = directory.appendingPathComponent("\(stem).1.log")
        try? fm.removeItem(at: first)
        try? fm.moveItem(at: fileURL, to: first)
    }
}

/// Local-time ISO-8601 timestamps with offset (J's rule: local, never UTC).
enum LocalTime {
    static func now() -> String { string(Date()) }
    static func string(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.timeZone = .current
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }
}

/// Strips secrets (API keys, auth headers, cookies) from anything that gets logged.
enum Redactor {
    private static let patterns: [(NSRegularExpression, String)] = {
        let specs: [(String, String)] = [
            (#"sk-ant-[A-Za-z0-9_\-]{8,}"#, "sk-ant-***"),
            (#"am_[a-z]{2}_[A-Za-z0-9_\-]{8,}"#, "am_***"),
            (#"(?i)(bearer\s+)[A-Za-z0-9._\-]+"#, "$1***"),
            (#"(?i)(oauth\s+)[A-Za-z0-9._\-]{8,}"#, "$1***"),
            (#"(?i)(basic\s+)[A-Za-z0-9+/=]{8,}"#, "$1***"),
            (#"(?i)((?:access|refresh|id|fb_exchange|input)_token|client_secret|code_verifier|secret|password|app_secret|aws_secret_access_key)(["']?\s*[:=]\s*["']?)[^"'\s,&}]+"#, "$1$2***"),
            (#"(?i)(x-api-key|authorization|cookie)(["']?\s*[:=]\s*["']?)[^"'\s,}]+"#, "$1$2***"),
            (#"(?i)([?&](?:code|X-Amz-Signature|X-Amz-Credential|upload_id)=)[^&\s"']+"#, "$1***"),
        ]
        return specs.compactMap { spec in
            guard let re = try? NSRegularExpression(pattern: spec.0) else { return nil }
            return (re, spec.1)
        }
    }()

    static func redact(_ s: String) -> String {
        var out = s
        for (re, template) in patterns {
            out = re.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: template)
        }
        return out
    }
}
