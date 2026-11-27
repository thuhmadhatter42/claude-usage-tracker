import AppKit
import SwiftUI

// UNINSTALL, "like Artemis": the app menu item "Uninstall <App>…" raises ONE sheet (a headline, one sentence, a
// single tick "Keep my settings", Cancel / Uninstall). Uninstall runs the bundled script, which moves everything in
// the app's manifest to the Trash (never deletes) and forgets its installer receipts. The script is run as
// administrator ONLY when it says it needs to be (something outside the home folder, or a receipt).
// Generalised from Artemis (shell.m artShellUninstallRun / openUninstallSheet / answerUninstall).
// Needs in Contents/Resources: uninstall.sh + uninstall.manifest (modules/uninstall).

enum HouseUninstall {
    enum Outcome: Equatable {
        case done(note: String?)
        /// The password box was cancelled: nothing moved.
        case cancelled
        case failed(String)
    }

    /// The bundled script and manifest. A test points these at a fixture.
    static var scriptURL: URL? { Bundle.main.url(forResource: "uninstall", withExtension: "sh") }
    static var manifestURL: URL? { Bundle.main.url(forResource: "uninstall", withExtension: "manifest") }

    /// The script's arguments. `--home` and `--user` are always passed: as administrator the script runs as
    /// root, whose $HOME is not the user's.
    static func arguments(keepSettings: Bool, manifest: URL, home: String = NSHomeDirectory(), user: String = NSUserName(),
                          extra: [String] = []) -> [String] {
        var a = ["--manifest", manifest.path, "--home", home, "--user", user]
        if keepSettings { a.append("--keep-data") }
        return a + extra
    }

    /// Run /bin/sh <script> <args> as this user. Returns (exit code, combined output).
    static func runPlain(script: URL, args: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = [script.path] + args
        let pipe = Pipe()
        p.standardOutput = pipe; p.standardError = pipe
        do { try p.run() } catch { return (127, "could not start /bin/sh: \(error.localizedDescription)") }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }

    /// The osascript argv that runs the script as administrator. `do shell script ... with administrator
    /// privileges` is AppleScript's documented way to ask macOS for the admin password box; the argv form hands
    /// each argument over untouched and AppleScript quotes it (`quoted form of`), so no path is ever spliced
    /// into AppleScript source.
    static func adminArgv(script: URL, args: [String]) -> [String] {
        ["-e", "on run argv",
         "-e", "set c to \"/bin/sh\"",
         "-e", "repeat with a in argv",
         "-e", "set c to c & \" \" & quoted form of (a as text)",
         "-e", "end repeat",
         "-e", "do shell script c with administrator privileges",
         "-e", "end run", "--", script.path] + args
    }

    static func runAsAdmin(script: URL, args: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = adminArgv(script: script, args: args)
        let pipe = Pipe()
        p.standardOutput = pipe; p.standardError = pipe
        do { try p.run() } catch { return (127, "could not start osascript: \(error.localizedDescription)") }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }

    /// Ask the script whether a real run needs administrator rights. nil = it could not say (the output is the reason).
    static func needsAdmin(script: URL, args: [String]) -> (Bool?, String) {
        let (rc, out) = runPlain(script: script, args: args + ["--needs-admin"])
        let answer = out.trimmingCharacters(in: .whitespacesAndNewlines)
        if rc == 0 && answer == "yes" { return (true, out) }
        if rc == 0 && answer == "no" { return (false, out) }
        return (nil, out)
    }

    /// THE SEAM for the two things a test must never do for real: the admin run and the plain run on a real Mac.
    /// Arguments: script, args, asAdmin. Returns (exit code, output).
    static var runner: (URL, [String], Bool) -> (Int32, String) = { script, args, asAdmin in
        asAdmin ? runAsAdmin(script: script, args: args) : runPlain(script: script, args: args)
    }

    /// The whole uninstall. Blocking: call it off the main thread (`uninstall(keepSettings:completion:)` does).
    static func perform(keepSettings: Bool, script: URL? = scriptURL, manifest: URL? = manifestURL,
                        home: String = NSHomeDirectory(), user: String = NSUserName(), extra: [String] = []) -> Outcome {
        guard let script, let manifest else {
            AppLog.shared.error("uninstall", "uninstall.sh or uninstall.manifest is not in the bundle")
            return .failed("The uninstaller is missing from this copy of \(House.appName).")
        }
        let args = arguments(keepSettings: keepSettings, manifest: manifest, home: home, user: user, extra: extra)
        AppLog.shared.info("uninstall", "requested (\(keepSettings ? "keeping settings" : "removing everything"))")
        let (admin, why) = needsAdmin(script: script, args: args)
        guard let admin else {
            AppLog.shared.error("uninstall", "the script could not say whether it needs administrator rights: \(why)")
            return .failed("The uninstaller could not read its list.")
        }
        AppLog.shared.info("uninstall", admin ? "running as administrator (something outside the home folder, or a receipt)" : "running as the user (nothing needs administrator rights)")
        let (rc, out) = runner(script, args, admin)
        // Every line the script printed is one fact: keep them all. The log folder itself may now be in the
        // Trash, so after a clean run these go to the unified log only (a file write would recreate the folder).
        let clean = rc == 0
        for line in out.split(separator: "\n") {
            AppLog.shared.log(clean ? .info : .error, "uninstall", String(line), fileToo: !clean)
        }
        AppLog.shared.log(clean ? .info : .error, "uninstall", "script ended rc=\(rc)", fileToo: !clean)
        if clean {
            // the manifest's note lines are whatever the script printed between its last move and DONE
            let lines = out.split(separator: "\n").map(String.init)
            let notes = lines.filter { l in
                !["move ", "absent ", "keep ", "forget ", "would "].contains { l.hasPrefix($0) } && l != "DONE"
            }
            return .done(note: notes.isEmpty ? nil : notes.joined(separator: " "))
        }
        // osascript: "User canceled." (-128) when the password box is dismissed
        if admin && (out.contains("(-128)") || out.localizedCaseInsensitiveContains("user canceled")) { return .cancelled }
        return .failed("Some of \(House.appName) is still installed. Try again, or see Uninstalling in the manual.")
    }

    /// Off the main thread; `completion` on the main thread.
    static func uninstall(keepSettings: Bool, completion: @escaping @MainActor (Outcome) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = perform(keepSettings: keepSettings)
            DispatchQueue.main.async { MainActor.assumeIsolated { completion(outcome) } }
        }
    }
}

/// Opens the sheet (the app menu item) and holds its state.
@MainActor
final class UninstallCenter: ObservableObject {
    static let shared = UninstallCenter()
    enum Stage: Equatable { case ask, working, done(String?), failed(String) }
    @Published var showing = false
    @Published var stage: Stage = .ask
    /// "Keep my settings": ticked by default, as Artemis.
    @Published var keepSettings = true

    func open() { stage = .ask; keepSettings = true; showing = true }

    func confirm(run: @escaping (Bool, @escaping @MainActor (HouseUninstall.Outcome) -> Void) -> Void = HouseUninstall.uninstall) {
        stage = .working
        run(keepSettings) { [weak self] outcome in
            guard let self else { return }
            switch outcome {
            case .done(let note): self.stage = .done(note)
            case .cancelled: self.stage = .ask                 // the password box was dismissed: back to the question
            case .failed(let why): self.stage = .failed(why)
            }
        }
    }
}

/// The sheet: a headline, one sentence, the tick, two buttons. Then "is uninstalled" (Quit) or "was not uninstalled".
struct UninstallSheet: View {
    @ObservedObject var center: UninstallCenter
    /// What the sentence says goes to the Trash. Name the big pieces ("Artemis, its driver and Recall plugin").
    var what: String = House.appName

    @MainActor init(center: UninstallCenter? = nil, what: String = House.appName) { self.center = center ?? .shared; self.what = what }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch center.stage {
            case .ask, .working:
                Text("Uninstall \(House.appName)?").font(.headline)
                Text("\(what) goes to the Trash. You may be asked for your password.")
                    .font(.callout).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                Toggle("Keep my settings", isOn: $center.keepSettings).disabled(center.stage == .working)
                HStack {
                    if center.stage == .working { ProgressView().controlSize(.small) }
                    Spacer()
                    Button("Cancel") { center.showing = false }.buttonStyle(.crisp).keyboardShortcut(.cancelAction)
                        .disabled(center.stage == .working)
                    Button("Uninstall") { center.confirm() }.buttonStyle(.accent)
                        .disabled(center.stage == .working)
                }
            case .done(let note):
                Text("\(House.appName) is uninstalled").font(.headline)
                Text(note ?? "It is in the Trash.").font(.callout).foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack { Spacer(); Button("Quit") { NSApp.terminate(nil) }.buttonStyle(.accent).keyboardShortcut(.defaultAction) }
            case .failed(let why):
                Text("\(House.appName) was not uninstalled").font(.headline)
                Text(why).font(.callout).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack { Spacer(); Button("OK") { center.showing = false }.buttonStyle(.accent).keyboardShortcut(.defaultAction) }
            }
        }
        .padding(18)
        .frame(width: 400)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

/// The app menu item: "Uninstall <App>…", after Settings (Artemis: under Check for Updates).
struct UninstallCommands: Commands {
    var body: some Commands {
        CommandGroup(after: .appSettings) {
            Button("Uninstall \(House.appName)…") { UninstallCenter.shared.open() }
        }
    }
}

extension View {
    /// Put this on the main window's root view: the Uninstall sheet is its sheet.
    func uninstallSheet(what: String = House.appName) -> some View { modifier(UninstallSheetModifier(what: what)) }
}

private struct UninstallSheetModifier: ViewModifier {
    @ObservedObject private var center = UninstallCenter.shared
    let what: String
    func body(content: Content) -> some View {
        content.sheet(isPresented: $center.showing) { UninstallSheet(center: center, what: what).accentRoot() }
    }
}
