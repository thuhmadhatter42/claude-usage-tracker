import AppKit
import SwiftUI

// THE HELP MENU SET every app carries: Manual, What's New, Run Doctor, Licenses.
// (Send Report… is added to the same menu by the Report module.)
// From Artemis (Help: Setup Guide / Manual / What's New, bundled pages opened offline) and Beat Key (Run Doctor).
// The pages are files in Contents/Resources; a missing one is an ERROR in the log and a line in Doctor, never
// a menu item that silently does nothing.

enum HouseHelp {
    /// One bundled page: the first of `names` that exists in Contents/Resources wins.
    struct Page: Equatable {
        let title: String
        let names: [String]
    }

    static let manual = Page(title: "Manual", names: ["Manual.pdf", "Manual.html"])
    static let whatsNew = Page(title: "What's New", names: ["WhatsNew.html", "WhatsNew.txt"])
    static let licenses = Page(title: "Licenses", names: ["Licenses.html", "Licenses.txt"])
    static let pages = [manual, whatsNew, licenses]

    static func url(for page: Page, in bundle: Bundle = .main) -> URL? {
        for name in page.names {
            let ns = name as NSString
            if let u = bundle.url(forResource: ns.deletingPathExtension, withExtension: ns.pathExtension) { return u }
        }
        return nil
    }

    /// THE SEAM: how a page is shown (a test swaps it; nothing opens).
    @MainActor static var opener: (URL) -> Bool = { NSWorkspace.shared.open($0) }

    /// Open a bundled page in its default app (a PDF in Preview, HTML in the browser): it works offline.
    @MainActor @discardableResult
    static func open(_ page: Page, in bundle: Bundle = .main) -> Bool {
        guard let u = url(for: page, in: bundle) else {
            AppLog.shared.error("help", "\(page.title): none of \(page.names) is in the bundle")
            return false
        }
        let ok = opener(u)
        if ok { AppLog.shared.info("help", "opened \(u.lastPathComponent)") }
        else { AppLog.shared.error("help", "macOS would not open \(u.path)") }
        return ok
    }
}

/// Replaces the system Help menu's items with the house set.
struct HelpCommands: Commands {
    var body: some Commands {
        CommandGroup(replacing: .help) {
            Button("\(House.appName) Manual") { HouseHelp.open(HouseHelp.manual) }
            Button("What's New") { HouseHelp.open(HouseHelp.whatsNew) }
            Divider()
            Button("Run Doctor") { DoctorCenter.shared.run() }
            Button("Licenses") { HouseHelp.open(HouseHelp.licenses) }
        }
    }
}

// MARK: - Doctor

/// One thing Doctor checks. `run` is quick and never throws: it answers ok / warn / fail and one short line.
struct DoctorCheck: Identifiable {
    enum State: String, Equatable { case ok = "OK", warn = "CHECK", fail = "FAIL" }
    struct Result: Equatable {
        let state: State
        let detail: String
    }

    let name: String
    let run: @Sendable () -> Result
    var id: String { name }
}

enum HouseDoctor {
    /// The app's own checks (its helper tools, its audio device, its server...). Set once at launch.   CHANGE
    static var appChecks: [DoctorCheck] = []

    /// The checks every app runs: the house kit itself is all there and filled in.
    static func standardChecks(bundle: Bundle = .main, log: AppLog = .shared) -> [DoctorCheck] {
        var checks: [DoctorCheck] = [
            DoctorCheck(name: "Version") {
                let v = House.version(in: bundle)
                return v == "0.0.0" ? .init(state: .fail, detail: "No version in the app.") : .init(state: .ok, detail: v)
            },
            DoctorCheck(name: "App settings file") {
                House.unfilled.isEmpty ? .init(state: .ok, detail: House.bundleID)
                                       : .init(state: .fail, detail: "Not filled in: \(House.unfilled.joined(separator: ", "))")
            },
            DoctorCheck(name: "Log") {
                let probe = log.directory.appendingPathComponent(".doctor-\(UUID().uuidString)")
                do {
                    try FileManager.default.createDirectory(at: log.directory, withIntermediateDirectories: true)
                    try Data("ok".utf8).write(to: probe)
                    try FileManager.default.removeItem(at: probe)
                    return .init(state: .ok, detail: log.fileURL.path)
                } catch {
                    return .init(state: .fail, detail: "Cannot write \(log.directory.path): \(error.localizedDescription)")
                }
            },
            DoctorCheck(name: "Report") {
                .init(state: NSSharingService(named: .composeEmail) != nil ? .ok : .warn,
                      detail: NSSharingService(named: .composeEmail) != nil ? "Mail to \(House.reportTo)" : "No Mail here. Reports save to the Desktop.")
            },
            DoctorCheck(name: "Uninstaller") {
                let s = bundle.url(forResource: "uninstall", withExtension: "sh"), m = bundle.url(forResource: "uninstall", withExtension: "manifest")
                return s != nil && m != nil ? .init(state: .ok, detail: "In the app.")
                                            : .init(state: .fail, detail: "uninstall.sh or uninstall.manifest is missing.")
            },
        ]
        for page in HouseHelp.pages {
            checks.append(DoctorCheck(name: page.title) {
                if let u = HouseHelp.url(for: page, in: bundle) { return .init(state: .ok, detail: u.lastPathComponent) }
                return .init(state: .fail, detail: "\(page.names.joined(separator: " or ")) is missing.")
            })
        }
        checks.append(DoctorCheck(name: "Location") {
            let path = bundle.bundleURL.path
            if path.hasPrefix("/Applications/") || path.hasPrefix(NSHomeDirectory() + "/Applications/") { return .init(state: .ok, detail: path) }
            return .init(state: .warn, detail: "Not in Applications: \(path)")
        })
        return checks
    }

    /// Run every check, log every answer (a FAIL as an error), return them in order.
    static func run(_ checks: [DoctorCheck]) -> [(name: String, result: DoctorCheck.Result)] {
        var out: [(String, DoctorCheck.Result)] = []
        for c in checks {
            let r = c.run()
            AppLog.shared.log(r.state == .fail ? .error : r.state == .warn ? .warn : .info, "doctor", "\(c.name): \(r.state.rawValue) \(r.detail)")
            out.append((c.name, r))
        }
        return out.map { (name: $0.0, result: $0.1) }
    }
}

/// Help > Run Doctor: runs the checks and shows the answers in a sheet.
@MainActor
final class DoctorCenter: ObservableObject {
    static let shared = DoctorCenter()
    struct Line: Identifiable, Equatable {
        let name: String
        let result: DoctorCheck.Result
        var id: String { name }
    }

    @Published var showing = false
    @Published private(set) var lines: [Line] = []
    /// True while the checks are running off the main thread.
    @Published private(set) var running = false

    var failures: Int { lines.filter { $0.result.state == .fail }.count }

    /// Shows the sheet at once and runs the checks off the main thread (the engine's doctor can wait 30 s on a
    /// Keychain prompt); the answers are published on the main actor when they are all in.
    @discardableResult
    func run(_ checks: [DoctorCheck] = HouseDoctor.standardChecks() + HouseDoctor.appChecks) -> Task<Void, Never> {
        lines = []
        running = true
        showing = true
        return Task { [weak self] in
            let answers = await Task.detached { HouseDoctor.run(checks) }.value
            self?.lines = answers.map { Line(name: $0.name, result: $0.result) }
            self?.running = false
        }
    }
}

struct DoctorSheet: View {
    @ObservedObject var center: DoctorCenter
    @Environment(\.accent) private var theme

    @MainActor init(center: DoctorCenter? = nil) { self.center = center ?? .shared }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(center.running ? "Doctor: checking…" : center.failures == 0 ? "Doctor: all good" : "Doctor: \(center.failures) to fix").font(.headline)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(center.lines) { line in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(line.result.state.rawValue)
                            .font(.system(size: 10, weight: .bold, design: .monospaced))
                            .foregroundColor(line.result.state == .ok ? theme.color : line.result.state == .warn ? .yellow : .red)
                            .frame(width: 44, alignment: .leading)
                        Text(line.name).font(.body.weight(.semibold)).frame(width: 110, alignment: .leading)
                        Text(line.result.detail).font(.callout).foregroundColor(.secondary)
                            .lineLimit(2).truncationMode(.middle).textSelection(.enabled)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            HStack {
                Button("Report") { center.showing = false; ReportCenter.shared.open() }.buttonStyle(.crisp)
                    .help("Send these results, log attached.")
                Spacer()
                Button("Done") { center.showing = false }.buttonStyle(.accent).keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 560)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

extension View {
    /// Put this on the main window's root view: Doctor's answers are its sheet.
    func doctorSheet() -> some View { modifier(DoctorSheetModifier()) }
}

private struct DoctorSheetModifier: ViewModifier {
    @ObservedObject private var center = DoctorCenter.shared
    func body(content: Content) -> some View {
        content.sheet(isPresented: $center.showing) { DoctorSheet(center: center).accentRoot() }
    }
}
