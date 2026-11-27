import AppKit
import SwiftUI

// REPORT: one button on screen + Help > Send Report…, one sheet with a text box, one email with the logs
// attached. Generalised from Artemis (app/src/shell/shell.m "THE ONE DOOR TO THE REPORT SHEET", the report
// section around art_shell_report_folder_write / artShellRealReportDeliver).
//   Send                 -> the macOS Mail compose sheet, addressed to House.reportTo, logs attached
//   no Mail on this Mac  -> the same report as a folder on the Desktop + a mailto: asking to attach it
//   Save Log to Desktop  -> the folder only
// The destination is ONE constant: House.reportTo (HouseConfig.swift).

enum HouseReport {
    /// Attached logs stop at this many bytes (Mail refuses large messages); this launch's log always goes.
    static let logCapBytes: UInt64 = 15 * 1024 * 1024

    /// What a delivery did, for the log and the sheet.
    enum Outcome: Equatable {
        case composed(logs: Int)
        case savedToDesktop(folder: URL, problems: Int)
        case fallbackFolder(folder: URL)
        case failed(String)
    }

    static func subject(version: String = House.version) -> String {
        "\(House.appName) \(version) report"
    }

    /// The email body: the user's words first, then what a reader needs to place the report.
    static func body(text: String, version: String = House.version,
                     macOS: String = ProcessInfo.processInfo.operatingSystemVersionString,
                     extra: [String] = [], when: Date = Date()) -> String {
        let f = DateFormatter()
        f.dateStyle = .medium; f.timeStyle = .medium
        let said = text.trimmingCharacters(in: .whitespacesAndNewlines)
        var lines = [said.isEmpty ? "(no description given)" : said, "", "\(House.appName) \(version)", "macOS \(macOS)"]
        lines += extra                        // app facts worth having in every report (interface, sample rate, ...)
        lines.append(f.string(from: when))
        return lines.joined(separator: "\n") + "\n"
    }

    /// Which logs ride along: the newest (this launch's) always, then the rest newest-first while they fit the cap.
    /// `sizes` = path -> bytes, `newestFirst` = the order to consider them in. Returns the chosen paths and how
    /// many were left out.
    static func logSelection(_ newestFirst: [URL], sizes: [URL: UInt64], cap: UInt64 = logCapBytes) -> (chosen: [URL], leftOut: Int) {
        var chosen: [URL] = [], total: UInt64 = 0, leftOut = 0
        for (i, url) in newestFirst.enumerated() {
            let size = sizes[url] ?? 0
            if i == 0 || total + size <= cap { chosen.append(url); total += size } else { leftOut += 1 }
        }
        return (chosen, leftOut)
    }

    /// The app's log files with their sizes, newest first (AppLog.logFiles: live file, then .1, .2, .3).
    static func currentLogs(_ log: AppLog = .shared) -> (urls: [URL], sizes: [URL: UInt64]) {
        var sizes: [URL: UInt64] = [:]
        let urls = log.logFiles()
        for u in urls {
            do { sizes[u] = (try FileManager.default.attributesOfItem(atPath: u.path)[.size] as? UInt64) ?? 0 }
            catch { AppLog.shared.error("report", "could not size \(u.lastPathComponent): \(error.localizedDescription)") }
        }
        return (urls, sizes)
    }

    /// THE FOLDER: "<App> report <yyyy-MM-dd HH-mm-ss>" under `parent` (nil = the Desktop, else the home folder),
    /// holding report.txt and a copy of each chosen log. EVERY write and copy is checked: each failure is logged,
    /// returned in `problems`, and written into the folder as "NOT SAVED - read me.txt". nil = it could not be made.
    static func writeFolder(body: String, logs: [URL], parent: URL? = nil, now: Date = Date()) -> (folder: URL?, problems: [String]) {
        let fm = FileManager.default
        let stamp = DateFormatter()
        stamp.locale = Locale(identifier: "en_US_POSIX")
        stamp.dateFormat = "yyyy-MM-dd HH-mm-ss"
        let base = parent ?? fm.urls(for: .desktopDirectory, in: .userDomainMask).first ?? fm.homeDirectoryForCurrentUser
        let folder = base.appendingPathComponent("\(House.appName) report \(stamp.string(from: now))", isDirectory: true)
        do { try fm.createDirectory(at: folder, withIntermediateDirectories: true) }
        catch {
            AppLog.shared.error("report", "could not create \(folder.path): \(error.localizedDescription)")
            return (nil, ["the report folder could not be made (\(error.localizedDescription))"])
        }
        var bad: [String] = []
        do { try body.write(to: folder.appendingPathComponent("report.txt"), atomically: true, encoding: .utf8) }
        catch { bad.append("report.txt was not written (\(error.localizedDescription))") }
        for log in logs {
            do { try fm.copyItem(at: log, to: folder.appendingPathComponent(log.lastPathComponent)) }
            catch { bad.append("\(log.lastPathComponent) was not copied (\(error.localizedDescription))") }
        }
        for line in bad { AppLog.shared.error("report", "folder \(folder.path): \(line)") }
        if !bad.isEmpty {
            let note = "Part of this report could not be saved:\n\n" + bad.joined(separator: "\n")
            do { try note.write(to: folder.appendingPathComponent("NOT SAVED - read me.txt"), atomically: true, encoding: .utf8) }
            catch { AppLog.shared.error("report", "folder \(folder.path): the note about the missing files could not be written either (\(error.localizedDescription))") }
        }
        return (folder, bad)
    }

    /// THE SEAM. Every side effect (Mail, Finder, the Desktop) is behind this, so a test swaps it and nothing
    /// real happens. Arguments: subject, body, log URLs, toDesktop.
    @MainActor static var deliver: (String, String, [URL], Bool) -> Outcome = realDeliver

    /// The one entry: the sheet's Send and Save Log to Desktop both come here.
    @MainActor @discardableResult
    static func send(text: String, toDesktop: Bool, extra: [String] = []) -> Outcome {
        let (urls, sizes) = currentLogs()
        let (chosen, leftOut) = logSelection(urls, sizes: sizes)
        AppLog.shared.info("report", "attaches \(chosen.count) log file(s); \(leftOut) older left out by the \(logCapBytes / (1024 * 1024)) MB cap")
        let outcome = deliver(subject(), body(text: text, extra: extra), chosen, toDesktop)
        switch outcome {
        case .composed(let n): AppLog.shared.info("report", "composed to \(House.reportTo) (\(n) log file(s) attached)")
        case .savedToDesktop(let folder, let problems):
            if problems == 0 { AppLog.shared.info("report", "saved to \(folder.path)") }
            else { AppLog.shared.error("report", "folder \(folder.path) is INCOMPLETE: \(problems) file(s) could not be saved (the folder says which)") }
        case .fallbackFolder(let folder): AppLog.shared.info("report", "no Mail compose on this Mac; folder \(folder.path) + mailto")
        case .failed(let why): AppLog.shared.error("report", "not delivered: \(why)")
        }
        return outcome
    }

    @MainActor
    private static func realDeliver(subject: String, body: String, logs: [URL], toDesktop: Bool) -> Outcome {
        func folderOnDesktop() -> (URL, Int)? {
            let (folder, bad) = writeFolder(body: body, logs: logs)
            guard let folder else { return nil }
            let note = folder.appendingPathComponent("NOT SAVED - read me.txt")
            // the reveal selects the note about anything missing, so the reveal itself says so
            NSWorkspace.shared.activateFileViewerSelecting([!bad.isEmpty && FileManager.default.fileExists(atPath: note.path) ? note : folder])
            return (folder, bad.count)
        }
        if toDesktop {
            guard let (folder, problems) = folderOnDesktop() else { return .failed("the report folder could not be made") }
            return .savedToDesktop(folder: folder, problems: problems)
        }
        let items: [Any] = [body] + logs
        if let service = NSSharingService(named: .composeEmail), service.canPerform(withItems: items) {
            service.recipients = [House.reportTo]
            service.subject = subject
            service.perform(withItems: items)
            return .composed(logs: logs.count)
        }
        guard let (folder, _) = folderOnDesktop() else { return .failed("no Mail on this Mac and the report folder could not be made") }
        var c = URLComponents()
        c.scheme = "mailto"; c.path = House.reportTo
        c.queryItems = [URLQueryItem(name: "subject", value: subject),
                        URLQueryItem(name: "body", value: "Please attach the folder \"\(folder.lastPathComponent)\" from your Desktop.")]
        if let url = c.url { NSWorkspace.shared.open(url) }
        else { AppLog.shared.error("report", "could not build the mailto: link for \(House.reportTo)") }
        return .fallbackFolder(folder: folder)
    }
}

/// Opens the sheet from anywhere (the on-screen button, the Help menu).
@MainActor
final class ReportCenter: ObservableObject {
    static let shared = ReportCenter()
    @Published var showing = false
    /// What the user has typed; kept while the sheet is closed so a slip of the hand loses nothing.
    @Published var draft = ""
    func open() { showing = true }
}

/// The sheet: a headline, one line, the text box, three buttons.
struct ReportSheet: View {
    @ObservedObject var center: ReportCenter
    /// App facts for every report ("Interface: Apollo x8", "Sample rate: 48000 Hz"). Optional.
    var extra: () -> [String] = { [] }
    @Environment(\.accent) private var theme

    @MainActor init(center: ReportCenter? = nil, extra: @escaping () -> [String] = { [] }) {
        self.center = center ?? .shared; self.extra = extra
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Send a Report").font(.headline)
            Text("What happened? The log is attached.").font(.callout).foregroundColor(.secondary)
            TextEditor(text: $center.draft)
                .font(.body)
                .frame(minHeight: 140)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.color(0.5), lineWidth: 1))
                .accessibilityLabel("What happened")
            HStack {
                Button("Save Log to Desktop") { finish(toDesktop: true) }.buttonStyle(.crisp)
                    .help("Save the report as a folder.")
                Spacer()
                Button("Cancel") { center.showing = false }.buttonStyle(.crisp).keyboardShortcut(.cancelAction)
                Button("Send") { finish(toDesktop: false) }.buttonStyle(.accent).keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 440)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    func finish(toDesktop: Bool) {
        let outcome = HouseReport.send(text: center.draft, toDesktop: toDesktop, extra: extra())
        if case .failed = outcome { return }          // the sheet stays with the words in it; the log says why
        center.draft = ""
        center.showing = false
    }
}

/// The on-screen button. Put one where the user's eye already is (a header, a footer).
struct ReportButton: View {
    var body: some View {
        Button("Report") { ReportCenter.shared.open() }
            .buttonStyle(.crisp)
            .help("Send a report, log attached.")
    }
}

/// Help > Send Report… (added after the Help menu's own items).
struct ReportCommands: Commands {
    var body: some Commands {
        CommandGroup(after: .help) {
            Button("Send Report…") { ReportCenter.shared.open() }
        }
    }
}

extension View {
    /// Put this on the main window's root view: the Report sheet is its sheet.
    func reportSheet(extra: @escaping () -> [String] = { [] }) -> some View { modifier(ReportSheetModifier(extra: extra)) }
}

private struct ReportSheetModifier: ViewModifier {
    @ObservedObject private var center = ReportCenter.shared
    let extra: () -> [String]
    func body(content: Content) -> some View {
        content.sheet(isPresented: $center.showing) { ReportSheet(center: center, extra: extra).accentRoot() }
    }
}
