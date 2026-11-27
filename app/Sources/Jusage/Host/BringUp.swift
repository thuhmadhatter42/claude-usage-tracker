import Foundation

/// S9: the app's rigid bring-up (R-144, R-156, R-192; SCORE §3 "App"). Every check refuses loudly with one plain
/// line; the caller shows it in the title hover and the panel and never runs the engine.
///
/// Order (a refusal earlier in the list writes nothing):
///   1. the engine is in the bundle (`Contents/Resources/engine/claude_usage.py`)
///   2. `Contents/Resources/VERSION` equals `CFBundleShortVersionString`
///   3. dev separation (SCORE §6): a `.dev` bundle id carries `JusageStateDir`, a release one never does
///   4. `themes.json` is valid (S4), then it becomes `ThemeBook.current`
///   4b. the saved settings are readable (a damaged record refuses and is kept, never reset)
///   5. Python: the recorded path must exist and pass the 3.9 + sqlite3 check; none recorded → probe once and
///      record the first that passes; a recorded one that is gone refuses (no re-probe)

/// What a good bring-up hands the feed runner.
struct Engine: Equatable {
    let python: URL
    let script: URL
    /// `--state-dir` for the engine: nil for the release build (the engine's default), the dev build's own folder.
    let stateDir: String?
    let version: String
    /// True when this check probed and recorded the Python; the caller then records it in the engine's config too
    /// (`config python`), so the `jusage` command runs the same one.
    let pythonRecordedNow: Bool
}

struct Refusal: Error, Equatable {
    let line: String
}

/// A refusal or a failed run is never silent (SCORE §3): one HostLog line and the panel's one plain line (the
/// model's `runError`); the host then runs no engine. The host's only path for both.
@MainActor
enum HostFailure {
    static func refuse(_ r: Refusal, log: HostLog, model: AppModel, now: Date = Date()) {
        log.error("bringup", r.line)
        model.apply(.failed(line: r.line, exit: 2), now: now)
    }

    /// After `model.apply(run)`: true for a good feed; otherwise the failure (including a feed the app refused
    /// to decode, e.g. a wrong schema) is logged once, here (RULINGS 48d).
    static func ranOK(_ r: RunResult, model: AppModel, log: HostLog) -> Bool {
        if case .ok = r, model.runError == nil, model.feed != nil { return true }
        log.error("engine", "run failed: \(model.runError ?? AppModel.failureText(r) ?? "no reason given")")
        return false
    }
}

/// How bring-up asks about a Python. The app uses `SystemPython`; tests use a fake.
protocol PythonChecking {
    func exists(_ path: String) -> Bool
    /// Runs `<path> -c 'import sys,sqlite3;assert sys.version_info>=(3,9)'`; true on exit 0.
    func passes(_ path: String) -> Bool
    /// `xcode-select -p` succeeds. Without it `/usr/bin/python3` is a stub that pops the tools installer.
    func developerToolsPresent() -> Bool
}

struct SystemPython: PythonChecking {
    static let check = "import sys,sqlite3;assert sys.version_info>=(3,9)"

    func exists(_ path: String) -> Bool { FileManager.default.isExecutableFile(atPath: path) }

    func passes(_ path: String) -> Bool { exitCode(path, ["-c", SystemPython.check]) == 0 }

    func developerToolsPresent() -> Bool { exitCode("/usr/bin/xcode-select", ["-p"]) == 0 }

    private func exitCode(_ exe: String, _ args: [String]) -> Int32? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.standardInput = FileHandle.nullDevice
        do { try p.run() } catch { return nil }
        p.waitUntilExit()
        return p.terminationStatus
    }
}

enum BringUp {
    static let probeOrder = ["/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
    static let systemStub = "/usr/bin/python3"
    static let stateDirKey = "JusageStateDir"
    static let devSuffix = ".dev"
    static let engineRelPath = "engine/claude_usage.py"

    /// The checks that need no settings (1–4): shared with `--smoke-test`, which must never record anything.
    struct BundleFacts: Equatable {
        let script: URL
        let version: String
        let stateDir: String?
        let themes: ThemeBook
    }

    static func bundleFacts(_ bundle: Bundle, home: String = NSHomeDirectory()) -> Result<BundleFacts, Refusal> {
        func refuse(_ s: String) -> Result<BundleFacts, Refusal> { .failure(Refusal(line: "jusage: " + s)) }
        guard let res = bundle.resourceURL else { return refuse("the app has no Resources folder. Reinstall jusage.") }

        let script = res.appendingPathComponent(engineRelPath)
        guard FileManager.default.fileExists(atPath: script.path) else {
            return refuse("the engine is missing from the app (Contents/Resources/\(engineRelPath)). Reinstall jusage.")
        }

        let appVersion = (bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? ""
        guard let raw = try? String(contentsOf: res.appendingPathComponent("VERSION"), encoding: .utf8) else {
            return refuse("Contents/Resources/VERSION is missing from the app. Reinstall jusage.")
        }
        let engineVersion = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard engineVersion == appVersion else {
            return refuse("the app is \(appVersion.isEmpty ? "unversioned" : appVersion) but its engine is \(engineVersion). Reinstall jusage.")
        }

        let id = bundle.bundleIdentifier ?? ""
        let stateDirRaw = bundle.object(forInfoDictionaryKey: stateDirKey) as? String
        var stateDir: String? = nil
        if id.hasSuffix(devSuffix) {
            guard let s = stateDirRaw, !s.isEmpty else {
                return refuse("this dev build (\(id)) has no \(stateDirKey); it would share the release build's data. Rebuild with app/build-app.sh --dev.")
            }
            if s.hasPrefix("/") { stateDir = s }
            else if s.hasPrefix("~/") { stateDir = (home as NSString).appendingPathComponent(String(s.dropFirst(2))) }
            else { return refuse("\(stateDirKey) must start with / or ~/, not '\(s)'.") }
        } else if stateDirRaw != nil {
            return refuse("this release build (\(id)) carries \(stateDirKey); only a dev build may. Rebuild without --dev.")
        }

        let themes: ThemeBook
        do { themes = try ThemeBook.load(url: res.appendingPathComponent("themes.json")) }
        catch let e as ThemeError { return refuse(e.message) }
        catch { return refuse("themes.json: \(error.localizedDescription)") }

        return .success(BundleFacts(script: script, version: engineVersion, stateDir: stateDir, themes: themes))
    }

    /// The first Python in `probeOrder` that passes, skipping the system stub without the developer tools.
    static func probe(_ python: PythonChecking) -> String? {
        let tools = python.developerToolsPresent()
        for path in probeOrder {
            if path == systemStub && !tools { continue }
            if python.exists(path) && python.passes(path) { return path }
        }
        return nil
    }

    static func check(bundle: Bundle, settings: SettingsStore, python: PythonChecking = SystemPython(),
                      home: String = NSHomeDirectory(),
                      useThemes: (ThemeBook) -> Void = { ThemeBook.current = $0 }) -> Result<Engine, Refusal> {
        let facts: BundleFacts
        switch bundleFacts(bundle, home: home) {
        case .failure(let r): return .failure(r)
        case .success(let f): facts = f
        }

        if let problem = settings.loadProblem {
            return .failure(Refusal(line: "jusage: the saved settings could not be read (\(problem)). Nothing was changed. "
                + "Quit jusage and run `defaults delete \(bundle.bundleIdentifier ?? "<bundle id>") \(SettingsStore.key)` to start fresh, or reopen the jusage that wrote them."))
        }

        let path: String
        var recordedNow = false
        if let recorded = settings.settings.pythonPath {
            guard python.exists(recorded) else {
                return .failure(Refusal(line: "jusage: the recorded Python \(recorded) is gone. Run install.sh again, or open jusage with --set-python <path>."))
            }
            guard python.passes(recorded) else {
                return .failure(Refusal(line: "jusage: the recorded Python \(recorded) is not Python 3.9+ with sqlite3. Open jusage with --set-python <path>."))
            }
            path = recorded
        } else {
            guard let found = probe(python) else {
                return .failure(Refusal(line: "jusage: no Python 3.9+ with sqlite3 found in \(probeOrder.joined(separator: ", ")). Install the Command Line Tools (xcode-select --install), then reopen jusage."))
            }
            settings.update { $0.pythonPath = found }
            path = found
            recordedNow = true
        }

        useThemes(facts.themes)
        return .success(Engine(python: URL(fileURLWithPath: path), script: facts.script, stateDir: facts.stateDir,
                               version: facts.version, pythonRecordedNow: recordedNow))
    }

    /// `--set-python <path>` (the installer passes it): records a good one; a bad one is refused and the old kept.
    static func setPython(_ path: String, settings: SettingsStore, python: PythonChecking = SystemPython()) -> Refusal? {
        if let problem = settings.loadProblem {
            return Refusal(line: "jusage: --set-python was not recorded: the saved settings could not be read (\(problem)).")
        }
        guard path.hasPrefix("/") else { return Refusal(line: "jusage: --set-python needs an absolute path, not '\(path)'.") }
        guard python.exists(path), python.passes(path) else {
            let kept = settings.settings.pythonPath.map { "kept \($0)" } ?? "none recorded"
            return Refusal(line: "jusage: --set-python \(path) is not Python 3.9+ with sqlite3; \(kept).")
        }
        settings.update { $0.pythonPath = path }
        return nil
    }
}
