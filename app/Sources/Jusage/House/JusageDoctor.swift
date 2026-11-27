import Foundation

// RUN DOCTOR, jusage's own checks. The kit's standard checks (version, settings file, log, Report, uninstaller,
// pages, location) run as they are; these two are added through HouseDoctor.appChecks:
//   Engine   the engine's own `doctor` verdict lines (one OK / WARN / FAIL line per account)
//   Updates  Info.plist carries the Sparkle feed and a real public key (a placeholder key is a FAIL)
// The engine runner is injected (the host passes the recorded Python and the bundled claude_usage.py), so a
// test never starts a process.

enum JusageDoctor {
    enum EngineError: Error, Equatable { case message(String) }

    /// The most a Doctor line shows; the full text is in the log.
    static let maxDetail = 160

    private static func oneLine(_ s: String) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "\n", with: " ")
        return t.count <= maxDetail ? t : String(t.prefix(maxDetail - 1)) + "…"
    }

    /// `run` returns the engine's verdict lines (`OK <what>`, `WARN <what>: <why>`, `FAIL <what>: <why>`).
    /// Any FAIL = FAIL; else any WARN = CHECK; else OK. No lines at all is a FAIL: a silent engine proves nothing.
    static func engineCheck(run: @escaping () throws -> [String]) -> DoctorCheck {
        DoctorCheck(name: "Engine") {
            let lines: [String]
            do { lines = try run().map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
            catch let EngineError.message(m) { return .init(state: .fail, detail: oneLine(m)) }
            catch { return .init(state: .fail, detail: oneLine(error.localizedDescription)) }
            guard !lines.isEmpty else { return .init(state: .fail, detail: "The engine's doctor printed nothing.") }
            for l in lines { AppLog.shared.log(l.hasPrefix("FAIL") ? .error : l.hasPrefix("WARN") ? .warn : .info, "doctor", "engine: \(l)") }
            if let f = lines.first(where: { $0.hasPrefix("FAIL") }) { return .init(state: .fail, detail: oneLine(f)) }
            if let w = lines.first(where: { $0.hasPrefix("WARN") }) { return .init(state: .warn, detail: oneLine(w)) }
            return .init(state: .ok, detail: oneLine(lines.count == 1 ? lines[0] : "\(lines.count) accounts healthy"))
        }
    }

    /// The key is a placeholder (the build stamps the real one) or empty.
    static func isPlaceholder(_ key: String) -> Bool {
        let t = key.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty || t.contains("__") || t.contains("PUBLIC_KEY")
    }

    static func updatesCheck(bundle: Bundle = .main) -> DoctorCheck {
        DoctorCheck(name: "Updates") {
            guard bundle.bundleURL.pathExtension == "app" else {
                return .init(state: .warn, detail: "Not a bundled app, so there is no update feed to check.")
            }
            guard AppUpdater.isUpdatable(bundle: bundle) else {
                return .init(state: .fail, detail: "SUFeedURL is missing from Info.plist. The app cannot check for updates.")
            }
            let key = (bundle.object(forInfoDictionaryKey: "SUPublicEDKey") as? String) ?? ""
            if isPlaceholder(key) { return .init(state: .fail, detail: "SUPublicEDKey is missing or a placeholder. Updates would be refused.") }
            let feed = (bundle.object(forInfoDictionaryKey: "SUFeedURL") as? String) ?? ""
            return .init(state: .ok, detail: feed)
        }
    }

    /// Once at launch: Help > Run Doctor now shows the engine and the update keys.
    static func install(engine: @escaping () throws -> [String], bundle: Bundle = .main) {
        HouseDoctor.appChecks = [engineCheck(run: engine), updatesCheck(bundle: bundle)]
    }

    /// The real engine runner: `python -B -E -s <script> doctor` through FeedRunner (90 s cap shortened to 30 s),
    /// the verdict lines of its output. Blocking: Doctor's checks run in a row on the main thread.
    static func engineRunner(python: URL, script: URL, stateDir: String?, timeout: TimeInterval = 30) -> () throws -> [String] {
        {
            let box = ResultBox()
            let done = DispatchSemaphore(value: 0)
            Task.detached {
                box.value = await FeedRunner.run(python: python, script: script, args: ["doctor"], stateDir: stateDir, timeout: timeout, killGrace: 2)
                done.signal()
            }
            done.wait()
            switch box.value {
            case .ok(let data)?: return String(decoding: data, as: UTF8.self).split(whereSeparator: \.isNewline).map(String.init)
            case .failed(let line, let code)?: throw EngineError.message("\(line) (exit \(code))")
            case .timedOut?: throw EngineError.message("The engine's doctor took over \(Int(timeout)) s.")
            case .crashed(let sig)?: throw EngineError.message("The engine crashed (signal \(sig)).")
            case .launchFailed(let why)?: throw EngineError.message("Could not start Python: \(why)")
            case nil: throw EngineError.message("The engine gave no answer.")
            }
        }
    }

    private final class ResultBox: @unchecked Sendable { var value: RunResult? }
}
