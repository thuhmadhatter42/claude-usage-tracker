import Foundation

/// `Jusage --smoke-test <file>` (R-167, house-kit release): the BUILT app checks itself with no window, records
/// nothing (no settings, no Python, no engine run) and writes OK or FAIL plus one line per check into <file>;
/// exit 0 / 1. `release.sh` launches it this way once per CPU.
struct SmokeCheck: Equatable {
    let name: String
    let ok: Bool
    let detail: String
    init(_ name: String, _ ok: Bool, _ detail: String) { self.name = name; self.ok = ok; self.detail = detail }
}

enum Smoke {
    /// jusage's own checks: bring-up checks 1–4 (engine, VERSION, dev separation, themes) and a read-only probe for
    /// a Python the app could record.
    static func bundleChecks(bundle: Bundle, python: PythonChecking) -> [SmokeCheck] {
        var out: [SmokeCheck] = []
        switch BringUp.bundleFacts(bundle) {
        case .success(let f):
            out.append(SmokeCheck("bring-up", true, "engine \(f.version), \(f.themes.themes.count) themes" + (f.stateDir.map { ", dev state \($0)" } ?? "")))
        case .failure(let r):
            out.append(SmokeCheck("bring-up", false, r.line))
        }
        if let p = BringUp.probe(python) { out.append(SmokeCheck("python", true, p)) }
        else { out.append(SmokeCheck("python", false, "no Python 3.9+ with sqlite3 in \(BringUp.probeOrder.joined(separator: ", "))")) }
        return out
    }

    /// The house kit's checks (the pattern of the kit's sample Smoke): config filled, version, log written, Doctor,
    /// Quick Keys table, tips, the bundled uninstaller's dry run, a Report folder in a temp folder.
    @MainActor static func houseChecks() -> [SmokeCheck] {
        var out: [SmokeCheck] = []
        out.append(SmokeCheck("config", House.unfilled.isEmpty, House.unfilled.isEmpty ? House.bundleID : "unfilled \(House.unfilled)"))
        out.append(SmokeCheck("version", House.version != "0.0.0", House.version))
        out.append(SmokeCheck("log written", FileManager.default.fileExists(atPath: AppLog.shared.fileURL.path), AppLog.shared.fileURL.path))
        for r in HouseDoctor.run(HouseDoctor.standardChecks()) where r.name != "Location" && r.name != "Report" {
            out.append(SmokeCheck("doctor \(r.name)", r.result.state != .fail, r.result.detail))
        }
        let qk = QuickKeys.problems(QuickKeys.appRows + QuickKeys.standardRows)
        out.append(SmokeCheck("quick keys table", qk.isEmpty, qk.isEmpty ? "\(QuickKeys.appRows.count + QuickKeys.standardRows.count) rows" : qk.joined(separator: "; ")))
        let tips = HouseTips.problems(HouseTips.all)
        out.append(SmokeCheck("tips", tips.isEmpty, tips.isEmpty ? "\(HouseTips.all.count) tips" : tips.joined(separator: "; ")))
        if let s = HouseUninstall.scriptURL, let m = HouseUninstall.manifestURL {
            let args = HouseUninstall.arguments(keepSettings: true, manifest: m)
            let (rc, text) = HouseUninstall.runPlain(script: s, args: args + ["--dry-run"])
            out.append(SmokeCheck("uninstall dry run", rc == 0 && text.contains("DONE") && text.contains("\(House.appName).app"),
                                  "rc=\(rc), \(text.split(separator: "\n").count) lines"))
        } else {
            out.append(SmokeCheck("uninstall bundled", false, "uninstall.sh or uninstall.manifest missing"))
        }
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-smoke-\(UUID().uuidString)")
        let (logs, sizes) = HouseReport.currentLogs()
        let (folder, problems) = HouseReport.writeFolder(body: HouseReport.body(text: "smoke"),
                                                         logs: HouseReport.logSelection(logs, sizes: sizes).chosen, parent: tmp)
        out.append(SmokeCheck("report folder", folder != nil && problems.isEmpty, folder?.lastPathComponent ?? problems.joined(separator: "; ")))
        try? FileManager.default.removeItem(at: tmp)
        return out
    }

    static func write(_ checks: [SmokeCheck], version: String, to output: URL, log: HostLog) -> Int32 {
        let failed = checks.filter { !$0.ok }
        for c in failed { log.error("smoke", "\(c.name): \(c.detail)") }
        let lines = checks.map { "\($0.ok ? "ok  " : "FAIL") \($0.name): \($0.detail)" }
        let text = ([failed.isEmpty ? "OK" : "FAIL", "jusage \(version)"] + lines).joined(separator: "\n") + "\n"
        do { try text.write(to: output, atomically: true, encoding: .utf8) }
        catch { log.error("smoke", "could not write \(output.path): \(error.localizedDescription)"); return 1 }
        return failed.isEmpty ? 0 : 1
    }

    @MainActor static func run(output: URL) -> Int32 {
        AppLog.shared.launchLine()
        let checks = houseChecks() + bundleChecks(bundle: .main, python: SystemPython())
        return write(checks, version: House.version, to: output, log: AppLog.shared)
    }
}
