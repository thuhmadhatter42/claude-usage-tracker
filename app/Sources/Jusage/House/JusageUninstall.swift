import Foundation
import ServiceManagement

// UNINSTALL, jusage's part. The kit's uninstall.sh moves the manifest's paths to the Trash and nothing else, so
// the three things a plain manifest cannot say are done here, BEFORE the script runs:
//   1. `jusage statusline off --all`: Claude Code's settings get their original status-line command back. It
//      goes first: if it fails (the user changed the setting since), nothing else is touched, because removing
//      the wrapper script would leave Claude Code pointing at a file that is gone.
//   2. the login item is switched off.
//   3. the `alias jusage=` lines in ~/.zshrc and ~/.bashrc go. The file is copied to the Trash first (never
//      deleted outright), then rewritten without those lines.
// `jusage uninstall` (the CLI) does all three itself (step 2 through `Jusage --login-item-off`) before it calls
// the same script (RULING 39).

enum JusageUninstall {
    /// The two rc files install.sh writes the alias into.
    static let rcFiles = [".zshrc", ".bashrc"]

    /// A line that defines the jusage alias (not `jusager`, not a comment).
    static func isAliasLine(_ line: Substring) -> Bool {
        line.trimmingCharacters(in: .whitespaces).hasPrefix("alias jusage=")
    }

    /// Removes the alias lines from each rc file; returns the names of the files it rewrote.
    /// A rewritten file's original is copied into `<home>/.Trash` first, under a name that cannot collide.
    @discardableResult
    static func removeAliasLines(home: URL) throws -> [String] {
        let fm = FileManager.default
        var changed: [String] = []
        for name in rcFiles {
            let url = home.appendingPathComponent(name)
            guard fm.fileExists(atPath: url.path) else { continue }
            let original = try String(contentsOf: url, encoding: .utf8)
            var kept: [Substring] = []
            var dropped = false
            for line in original.split(separator: "\n", omittingEmptySubsequences: false) {
                if isAliasLine(line) { dropped = true } else { kept.append(line) }
            }
            guard dropped else { continue }
            let trash = home.appendingPathComponent(".Trash")
            try fm.createDirectory(at: trash, withIntermediateDirectories: true)
            var dest = trash.appendingPathComponent(name), n = 0
            while fm.fileExists(atPath: dest.path) { n += 1; dest = trash.appendingPathComponent("\(name) \(n)") }
            try fm.copyItem(at: url, to: dest)
            try kept.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
            AppLog.shared.info("uninstall", "removed the jusage alias from \(name) (original in the Trash as \(dest.lastPathComponent))")
            changed.append(name)
        }
        return changed
    }

    /// What "restore the status line" means when the engine cannot start (bring-up refused): read the engine's
    /// own record (`config.json` in the state folder; the release default is `~/.claude-usage`). No wrapped entry
    /// (none ever, or all switched off) = nothing to restore, exit 0. A wrapped one, or a record that cannot be
    /// read, = exit 2 with the by-hand step, so a status line is never left pointing at a removed app.
    static func statusLineOffWithoutEngine(stateDir: String?, home: String) -> (Int32, String) {
        let dir = stateDir ?? (home as NSString).appendingPathComponent(".claude-usage")
        let url = URL(fileURLWithPath: dir).appendingPathComponent("config.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return (0, "no status line was ever wrapped") }
        let byHand = "Put each wrapped statusLine back from its \"original\" entry in \(url.path), then uninstall again."
        guard let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return (2, "The engine is not running and \(url.path) could not be read. \(byHand)")
        }
        let records = (json["statusline"] as? [String: Any])?.values.compactMap { $0 as? [String: Any] } ?? []
        if records.contains(where: { ($0["enabled"] as? Bool) == true }) {
            return (2, "The engine is not running and the Claude Code status line is still wrapped. \(byHand)")
        }
        return (0, "no status line is wrapped")
    }

    struct Prep {
        /// Runs `jusage statusline off --all`; returns (exit code, output).
        var statusLineOff: () -> (Int32, String)
        var loginItemOff: () -> Void
        var home: URL

        /// nil = done; else the sentence for the "was not uninstalled" box.
        func run() -> String? {
            let (rc, out) = statusLineOff()
            if rc != 0 {
                let line = out.split(whereSeparator: \.isNewline).last.map(String.init) ?? "no reason given"
                AppLog.shared.error("uninstall", "status line was not restored (exit \(rc)): \(out)")
                return "The Claude Code status line was not restored, so nothing was removed. \(line)"
            }
            loginItemOff()
            do { try JusageUninstall.removeAliasLines(home: home) }
            catch {
                AppLog.shared.error("uninstall", "could not clean the shell alias: \(error.localizedDescription)")
                return "The jusage alias in your shell files could not be removed: \(error.localizedDescription)"
            }
            return nil
        }
    }

    /// Puts the prep in front of the kit's runner. The kit calls the runner only for the real run (the
    /// `--needs-admin` question goes round it), so a Cancel at the password box leaves the status line restored
    /// and the app still installed: turn the backup back on in Settings.
    static func wrap(runner: @escaping (URL, [String], Bool) -> (Int32, String), prep: Prep) -> (URL, [String], Bool) -> (Int32, String) {
        { script, args, asAdmin in
            if let why = prep.run() { return (1, why) }
            return runner(script, args, asAdmin)
        }
    }

    /// The default login-item switch: SMAppService (macOS 13+). Not registered is not an error.
    static func unregisterLoginItem() {
        let s = SMAppService.mainApp
        guard s.status == .enabled || s.status == .requiresApproval else { return }
        do { try s.unregister() }
        catch { AppLog.shared.error("uninstall", "could not switch off the login item: \(error.localizedDescription)") }
    }

    /// Once at launch, after the host knows how to run the engine: `statusLineOff` runs
    /// `claude_usage.py statusline off --all` and returns (exit, output).
    @MainActor static func install(statusLineOff: @escaping () -> (Int32, String),
                                   loginItemOff: @escaping () -> Void = JusageUninstall.unregisterLoginItem,
                                   home: URL = URL(fileURLWithPath: NSHomeDirectory())) {
        HouseUninstall.runner = wrap(runner: HouseUninstall.runner, prep: Prep(statusLineOff: statusLineOff, loginItemOff: loginItemOff, home: home))
    }
}
