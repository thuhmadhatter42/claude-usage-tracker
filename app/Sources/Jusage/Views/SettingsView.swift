import SwiftUI

/// S22: the Settings tab (R-84, R-85, R-22 UI, R-54, R-159, Amendments). The rows draw in `SettingsRow` order,
/// so the order the test checks is the order on screen. App settings write through `SettingsStore.update`;
/// engine settings (cadence, status-line backup) run the engine and show the value the next feed reports.
enum SettingsRow: String, CaseIterable {
    case theme, accent, people, accounts
    case showLabels, showMenuBarLabels, showWarnRunsOut, showToday, showModels, showProjects, showPeople
    case statusLineBackup, checkEvery, menuBarShows, colourByWorstMeter, openAtLogin, footer, quit

    static let footerText = "Settings live on this Mac only."
    static let statusLineText = "Use Claude Code's status line as a backup"

    /// "Check every": 15 s to 10 min (Amendment).
    static let cadenceChoices = [15, 30, 60, 120, 300, 600]

    static func cadenceLabel(_ s: Int) -> String { s < 60 ? "\(s) s" : "\(s / 60) min" }

    /// The engine's live value from the feed; before the first feed, the value last written (never blank).
    static func cadenceShown(feed: Feed?, settings: AppSettings) -> Int { feed?.rules.readEveryS ?? settings.readEveryS }

    struct MenuBarChoice: Identifiable, Equatable {
        var id: String { SettingsRow.key(mode) }
        let title: String
        let mode: MenuBarMode
    }

    /// A picker tag for a menu bar mode (the mode itself is not Hashable).
    static func key(_ mode: MenuBarMode) -> String {
        switch mode { case .tokensToday: return "tokensToday"; case .meter(let id): return "meter:\(id)" }
    }

    /// Every meter the feed carries (session, weekly, per-model, by name) plus tokens today.
    static func menuBarChoices(feed: Feed?) -> [MenuBarChoice] {
        var seen = Set<String>(), out: [MenuBarChoice] = []
        for a in feed?.accounts ?? [] {
            for m in MeterRowModel.ordered(a.meters) where seen.insert(m.id).inserted {
                out.append(MenuBarChoice(title: m.name, mode: .meter(m.id)))
            }
        }
        let ranked = out.enumerated().sorted { (MeterRowModel.rank($0.element.mode), $0.offset) < (MeterRowModel.rank($1.element.mode), $1.offset) }
        return ranked.map(\.element) + [MenuBarChoice(title: "Tokens today", mode: .tokensToday)]
    }

    static func themes() -> [Theme] { ThemeBook.current.themes }
}

extension MeterRowModel {
    static func rank(_ mode: MenuBarMode) -> Int {
        guard case .meter(let id) = mode else { return 3 }
        return id == "session" ? 0 : id == "weekly" ? 1 : 2
    }
}

extension RunResult {
    /// One plain line for a run that did not succeed; nil for `.ok`.
    var failureText: String? {
        switch self {
        case .ok: return nil
        case .failed(let line, _): return line
        case .timedOut: return FeedRunner.timeoutText
        case .crashed(let signal): return "The engine stopped unexpectedly (signal \(signal))."
        case .launchFailed(let why): return "The engine could not start: \(why)"
        }
    }
}

/// Engine settings: written only by running the engine (SCORE 1.3, RULINGS 26). A refusal shows its line under
/// the control and goes to the house log. What the panel shows after a write it took is held until the next
/// feed reports the engine's value.
@MainActor
final class EngineSettingsWriter: ObservableObject {
    @Published private(set) var cadenceError: String?
    @Published private(set) var statuslineErrors: [String: String] = [:]
    @Published private(set) var cadenceSet: Int?
    @Published private(set) var statuslineSet: [String: Bool] = [:]

    private let run: EngineCommand
    private let log: HouseLog
    private let settings: SettingsStore

    init(run: @escaping EngineCommand, log: HouseLog, settings: SettingsStore) {
        self.run = run; self.log = log; self.settings = settings
    }

    func setReadEvery(_ seconds: Int) async {
        let r = AppSettings.readEveryRange
        let s = min(max(seconds, r.lowerBound), r.upperBound)
        let args = ["config", "read-every", String(s)]
        if let why = await run(args).failureText {
            cadenceError = why
            log.error("settings", "\(args.joined(separator: " ")) refused: \(why)")
        } else {
            cadenceError = nil
            cadenceSet = s
            settings.update { $0.readEveryS = s }
        }
    }

    func setStatusline(_ on: Bool, folder: String) async {
        let args = ["statusline", on ? "on" : "off", "--folder", folder]
        if let why = await run(args).failureText {
            statuslineErrors[folder] = why
            log.error("settings", "\(args.joined(separator: " ")) refused: \(why)")
        } else {
            statuslineErrors[folder] = nil
            statuslineSet[folder] = on
        }
    }

    /// A new feed carries the engine's own values: drop what was held since the write.
    func feedArrived() {
        cadenceSet = nil
        statuslineSet = [:]
    }
}

/// Open at login (R-85): a failure shows its reason in words, and the checkbox shows the real state.
@MainActor
final class LoginItemWriter: ObservableObject {
    @Published private(set) var isOn: Bool
    @Published private(set) var error: String?
    private let control: LoginItemControl
    private let log: HouseLog

    init(control: LoginItemControl, log: HouseLog) {
        self.control = control; self.log = log
        isOn = control.isOn()
    }

    func set(_ on: Bool) {
        if let why = control.set(on) {
            error = why
            log.error("settings", "open at login refused: \(why)")
        } else {
            error = nil
        }
        isOn = control.isOn()
    }
}

struct SettingsView: View {
    let feed: Feed?
    @ObservedObject var store: SettingsStore
    @ObservedObject var engine: EngineSettingsWriter
    @ObservedObject var login: LoginItemWriter
    let palette: Palette
    @EnvironmentObject private var services: PanelServices

    private var s: AppSettings { store.settings }

    private func bind<T>(_ key: WritableKeyPath<AppSettings, T>) -> Binding<T> {
        Binding(get: { store.settings[keyPath: key] }, set: { v in store.update { $0[keyPath: key] = v } })
    }

    private func section(_ sec: Section) -> Binding<Bool> {
        Binding(get: { store.settings.isSectionShown(sec) }, set: { on in
            store.update { st in
                st.hiddenSections.removeAll { $0 == sec }
                if !on { st.hiddenSections.append(sec) }
            }
        })
    }

    private func caption(_ t: String) -> some View {
        Text(t).font(Layout.small.weight(.semibold)).foregroundColor(palette.muted).padding(.top, 4)
    }

    private func refusal(_ t: String?) -> some View {
        Group { if let t { Text(t).font(Layout.small).foregroundColor(palette.state(.red)).fixedSize(horizontal: false, vertical: true) } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(SettingsRow.allCases, id: \.self) { row($0) }
        }
        .font(Layout.body)
        .toggleStyle(.checkbox)
    }

    @ViewBuilder private func row(_ r: SettingsRow) -> some View {
        switch r {
        case .theme:
            Picker("Theme", selection: bind(\.theme)) {
                ForEach(SettingsRow.themes(), id: \.id) { Text($0.name).tag($0.id) }
            }
        case .accent:
            services.accentPicker()
        case .people:
            if let people = feed?.people, !people.isEmpty {
                caption("People")
                ForEach(people, id: \.installId) { p in
                    Toggle("\(p.user) · \(p.host)", isOn: Binding(
                        get: { store.settings.isPersonShown(p.installId) },
                        set: { on in store.update { st in
                            st.hiddenPeople.removeAll { $0 == p.installId }
                            if !on { st.hiddenPeople.append(p.installId) }
                        } }))
                }
            }
        case .accounts:
            if let accounts = feed?.accounts, !accounts.isEmpty {
                caption("Accounts")
                ForEach(accounts.sorted { $0.number < $1.number }, id: \.key) { a in
                    HStack {
                        Toggle(AccountBlockModel.localPart(a.email), isOn: Binding(
                            get: { store.settings.isAccountShown(a.key) },
                            set: { on in store.update { st in
                                st.hiddenAccounts.removeAll { $0 == a.key }
                                if !on { st.hiddenAccounts.append(a.key) }
                            } }))
                        .help(a.email)
                        Spacer()
                        TextField(String(a.number), text: Binding(
                            get: { store.settings.menuBarTags[a.key] ?? "" },
                            set: { t in store.update { $0.setTag(t, for: a.key) } }))
                            .frame(width: 48)
                            .help("Menu bar tag, up to \(AppSettings.maxTag) characters")
                    }
                }
            }
        case .showLabels:
            caption("Show")
            Toggle("Labels", isOn: bind(\.labels))
        case .showMenuBarLabels:
            Toggle("Menu bar labels", isOn: bind(\.menuBarLabels))
        case .showWarnRunsOut:
            Toggle("Warn when the pace runs out before the reset", isOn: bind(\.warnRunsOut))
        case .showToday:
            Toggle("Today's totals", isOn: section(.today))
        case .showModels:
            Toggle("Busiest models", isOn: section(.models))
        case .showProjects:
            Toggle("Busiest projects", isOn: section(.projects))
        case .showPeople:
            Toggle("Other people", isOn: section(.people))
        case .statusLineBackup:
            if let accounts = feed?.accounts, !accounts.isEmpty {
                caption(SettingsRow.statusLineText)
                ForEach(accounts.sorted { $0.number < $1.number }, id: \.key) { a in
                    Toggle(AccountBlockModel.localPart(a.email), isOn: Binding(
                        get: { engine.statuslineSet[a.key] ?? feed?.statusline[a.key]?.enabled ?? false },
                        set: { on in Task { await engine.setStatusline(on, folder: a.key) } }))
                    refusal(engine.statuslineErrors[a.key])
                }
            }
        case .checkEvery:
            Picker("Check every", selection: Binding(
                get: { engine.cadenceSet ?? SettingsRow.cadenceShown(feed: feed, settings: s) },
                set: { v in Task { await engine.setReadEvery(v) } })) {
                ForEach(cadenceOptions, id: \.self) { Text(SettingsRow.cadenceLabel($0)).tag($0) }
            }
            refusal(engine.cadenceError)
        case .menuBarShows:
            Picker("Menu bar shows", selection: Binding(
                get: { SettingsRow.key(store.settings.menuBarMode) },
                set: { k in
                    guard let choice = menuBarOptions.first(where: { $0.id == k }) else { return }
                    store.update { $0.menuBarMode = choice.mode }
                })) {
                ForEach(menuBarOptions) { Text($0.title).tag($0.id) }
            }
        case .colourByWorstMeter:
            Toggle("Colour menu bar by the worst meter", isOn: bind(\.colourByWorstMeter))
        case .openAtLogin:
            Toggle("Open at login", isOn: Binding(get: { login.isOn }, set: { login.set($0) }))
            refusal(login.error)
        case .footer:
            Text(SettingsRow.footerText).font(Layout.small).foregroundColor(palette.muted).padding(.top, 4)
        case .quit:
            Button("Quit jusage", action: services.quit)
                .keyboardShortcut(services.shortcut(QuickKeyID.quit))
        }
    }

    /// The choices, plus the shown value if the engine reports one between them (a value set from the CLI).
    private var cadenceOptions: [Int] {
        let shown = engine.cadenceSet ?? SettingsRow.cadenceShown(feed: feed, settings: s)
        return Array(Set(SettingsRow.cadenceChoices + [shown])).sorted()
    }

    /// The choices, plus the stored mode if this feed has no such meter (it stays selectable, not lost).
    private var menuBarOptions: [SettingsRow.MenuBarChoice] {
        var c = SettingsRow.menuBarChoices(feed: feed)
        if !c.contains(where: { $0.mode == s.menuBarMode }), case .meter(let id) = s.menuBarMode {
            c.insert(SettingsRow.MenuBarChoice(title: id, mode: s.menuBarMode), at: 0)
        }
        return c
    }
}
