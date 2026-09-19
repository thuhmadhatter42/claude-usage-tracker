// jusage menu bar — a status item + popover over `claude_usage.py menu-json`.
// Built by install.sh with swiftc (Command Line Tools), no Xcode needed.
import Cocoa
import SwiftUI
import ServiceManagement
import UserNotifications

// MARK: - data

struct Forecast: Decodable { let ready: Bool; let samples: Int?; let left_usd: Double?; let hours_to_wall: Double?; let wall_at: String?; let per_pct_usd: Double? }
/// Who spent what inside this meter's period. Every field optional: a feed from an older jusage
/// carries no split at all, and must still decode.
struct Part: Decodable, Identifiable { var id: String { user }; let user: String; let cost: Double?; let share: Double? }
struct Split: Decodable { let period_start: String?; let parts: [Part]?; let missing: [String]? }
struct Meter: Decodable, Identifiable { var id: String { name }; let name: String; let pct: Double; let resets_at: String?; let forecast: Forecast?; let split: Split? }

func forecastText(_ f: Forecast?) -> String {
    guard let f, f.ready, let left = f.left_usd else { return "" }
    var s = String(format: "≈ $%.0f of room left", left)
    if let w = f.wall_at, let t = ISO8601DateFormatter.minutes.date(from: w) {
        let df = DateFormatter(); df.dateFormat = Calendar.current.isDateInToday(t) ? "HH:mm" : "EEE HH:mm"
        s += " · wall \(df.string(from: t)) at this pace"
    }
    return s
}
/// "this period: Sam 71% · Lee 29%" — shares of api-equivalent spend inside the meter's period,
/// never of the percentage itself. `me` reads first: this is my meter on my Mac.
func splitText(_ s: Split?, me: String?) -> String {
    guard let parts = s?.parts, !parts.isEmpty else { return "" }
    let ordered = parts.sorted { a, b in
        if let me, (a.user == me) != (b.user == me) { return a.user == me }
        return (a.share ?? 0) > (b.share ?? 0)
    }
    return "this period: " + ordered.map { "\($0.user) \(Int(((($0.share ?? 0) * 100)).rounded()))%" }.joined(separator: " · ")
}
func missingText(_ s: Split?) -> String {
    (s?.missing ?? []).map { "\($0): not reported yet (update jusage)" }.joined(separator: " · ")
}
extension ISO8601DateFormatter {
    static let minutes: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime]; return f }()
}
struct Live: Decodable { let fetched: String?; let stale: Bool?; let error: String?; let meters: [Meter]? }
struct Tokens: Decodable { let tokens: Double; let cost: Double; let calls: Int?; let unpriced: Int? }
/// Decodes a JSON array element-by-element, dropping any that fail instead of failing the whole
/// array (and with it every field decoded alongside it) — one malformed peer report must not
/// blank the whole app. `Empty` is a JSON-object-shaped catch-all: decoding an element as `Empty`
/// after `T` fails still consumes it, so the unkeyed container's cursor advances past it.
struct LossyArray<T: Decodable>: Decodable {
    let elements: [T]
    struct Empty: Decodable {}
    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var result: [T] = []
        while !container.isAtEnd {
            if let value = try? container.decode(T.self) {
                result.append(value)
            } else {
                _ = try? container.decode(Empty.self)
            }
        }
        self.elements = result
    }
}
struct Window: Decodable, Identifiable { var id: String { account }; let account: String; let start: String; let end: String; let tokens: Double; let cost: Double; let open: Bool }
struct ModelRow: Decodable, Identifiable { var id: String { model }; let model: String; let tokens: Double; let cost: Double }
struct ProjectRow: Decodable, Identifiable { var id: String { project }; let project: String; let tokens: Double; let cost: Double }
struct Month: Decodable, Identifiable {
    var id: String { key }
    let key: String; let label: String; let tokens: Double; let cost: Double; let calls: Int
    let types: [String: Double]; let models: [ModelRow]; let projects: [ProjectRow]
}
struct Person: Decodable, Identifiable {
    var id: String { "\(user)@\(host)" }
    let user: String; let host: String; let updated: String?; let age_hours: Double?
    let today: Tokens; let week: Tokens; let month: Tokens; let live: [String: Live]
    let account_ids: [String: String]?
}
struct Feed: Decodable {
    let version: String; let user: String?; let generated: String
    let today: Tokens; let week: Tokens; let windows: [Window]?
    let live: [String: Live]; let accounts: [String: Tokens]; let models: [ModelRow]; let dashboard: String?
    let months: [Month]?; let people: LossyArray<Person>?; let projects: [ProjectRow]?
    let share_error: String?; let shared_at: String?
}

// MARK: - themes (themes.json next to claude_usage.py; shared with the dashboard)

struct ThemeDef: Decodable {
    let name: String; let scheme: String?
    let bg: String?; let sf: String?; let sf2: String?; let ink: String?; let ink2: String?; let mut: String?
    let line: String?; let ok: String?; let warn: String?; let hot: String?; let accent: String?
}
struct ThemeFile: Decodable { let order: [String]; let themes: [String: ThemeDef] }
extension Color {
    init(hex: String) {
        var h = hex; if h.hasPrefix("#") { h.removeFirst() }
        let v = UInt64(h, radix: 16) ?? 0
        self.init(red: Double((v >> 16) & 0xff) / 255, green: Double((v >> 8) & 0xff) / 255, blue: Double(v & 0xff) / 255)
    }
}
extension NSColor { convenience init(hex: String) { self.init(Color(hex: hex)) } }
/// Resolved colours for the current theme. System = nil colours, macOS decides.
struct Theme {
    let id: String; let def: ThemeDef?
    var isSystem: Bool { def?.scheme == nil }
    var bg: Color? { def?.bg.map { Color(hex: $0) } }
    var ink: Color? { def?.ink.map { Color(hex: $0) } }
    var ink2: Color? { def?.ink2.map { Color(hex: $0) } }
    var mut: Color? { def?.mut.map { Color(hex: $0) } }
    var line: Color { def?.line.map { Color(hex: $0) } ?? Color.primary.opacity(0.12) }
    var ok: Color { def?.ok.map { Color(hex: $0) } ?? .green }
    var warn: Color { def?.warn.map { Color(hex: $0) } ?? .yellow }
    var hot: Color { def?.hot.map { Color(hex: $0) } ?? .red }
    var accent: Color { def?.accent.map { Color(hex: $0) } ?? .accentColor }
    var appearance: NSAppearance? {
        switch def?.scheme { case "dark": return NSAppearance(named: .darkAqua); case "light": return NSAppearance(named: .aqua); default: return nil }
    }
}

func fmt(_ n: Double) -> String {
    if n >= 1e9 { return String(format: "%.2fB", n / 1e9) }
    if n >= 1e6 { return String(format: "%.1fM", n / 1e6) }
    if n >= 1e3 { return String(format: "%.0fk", n / 1e3) }
    return String(format: "%.0f", n)
}
func money(_ c: Double) -> String { c >= 100 ? String(format: "$%.0f", c) : String(format: "$%.2f", c) }
func shortModel(_ m: String) -> String { m.replacingOccurrences(of: "claude-", with: "").replacingOccurrences(of: "-20251001", with: "") }
func meterLabel(_ n: String) -> String {
    ["five_hour": "5 hours", "seven_day": "7 days", "seven_day_opus": "7 days, Opus",
     "seven_day_sonnet": "7 days, Sonnet", "seven_day_fable": "7 days, Fable"][n] ?? n.replacingOccurrences(of: "_", with: " ")
}
/// Never "0h 00m": once the reset time is behind us the percentage on screen belongs to the
/// finished period, and saying so is the only honest thing until the next reading lands.
func resetsText(_ iso: String?) -> String {
    guard let iso, let t = ISO8601DateFormatter.minutes.date(from: iso) else { return "" }
    let secs = t.timeIntervalSinceNow
    if secs <= 0 { return "reset due, waiting for a fresh reading" }
    let left = Int(secs) / 60
    let f = DateFormatter(); f.dateFormat = Calendar.current.isDateInToday(t) ? "HH:mm" : "EEE HH:mm"
    return "resets \(f.string(from: t)), in \(left / 60)h \(String(format: "%02d", left % 60))m"
}
/// "as of 17:06", or "as of Sat 17:06" when the other person's report is not from today.
func asOfText(_ iso: String?) -> String {
    guard let iso, let t = ISO8601DateFormatter.minutes.date(from: iso) else { return "" }
    let f = DateFormatter(); f.dateFormat = Calendar.current.isDateInToday(t) ? "HH:mm" : "EEE HH:mm"
    return "as of \(f.string(from: t))"
}

// MARK: - model

final class Model: ObservableObject {
    @Published var feed: Feed?
    @Published var error: String?
    @Published var loading = false
    @Published var lastRefresh: Date?
    @Published var atLogin: Bool = (SMAppService.mainApp.status == .enabled) {
        didSet {
            guard atLogin != oldValue else { return }
            do { if atLogin { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() } }
            catch { DispatchQueue.main.async { self.atLogin = oldValue } }
        }
    }
    @Published var tab = 0          // 0 today, 1 month, 2 settings
    @Published var monthIndex = 0   // 0 = this month, 1 = last month, ...
    let scriptDir: URL
    var timer: Timer?
    let themeFile: ThemeFile?

    // Settings, remembered across launches.
    @Published var hidden: Set<String> = Set(UserDefaults.standard.stringArray(forKey: "hidden") ?? []) {
        didSet { UserDefaults.standard.set(Array(hidden).sorted(), forKey: "hidden") }
    }
    func shown(_ key: String) -> Bool { !hidden.contains(key) }
    func binding(_ key: String) -> Binding<Bool> {
        Binding(get: { !self.hidden.contains(key) }, set: { on in if on { self.hidden.remove(key) } else { self.hidden.insert(key) } })
    }
    @Published var themeID: String = UserDefaults.standard.string(forKey: "theme") ?? "system" {
        didSet { UserDefaults.standard.set(themeID, forKey: "theme"); (NSApp.delegate as? AppDelegate)?.applyTheme() }
    }
    var theme: Theme { Theme(id: themeID, def: themeFile?.themes[themeID]) }
    var themeChoices: [(String, String)] { (themeFile?.order ?? ["system"]).compactMap { id in themeFile?.themes[id].map { (id, $0.name) } } }

    init() {
        // The .app is built inside the install folder next to claude_usage.py; build.sh also stamps that
        // folder into Info.plist so the app keeps working after someone drags it to /Applications.
        let beside = Bundle.main.bundleURL.deletingLastPathComponent()
        let stamped = (Bundle.main.object(forInfoDictionaryKey: "JusageScriptDir") as? String).map { URL(fileURLWithPath: $0) }
        scriptDir = [beside, stamped].compactMap { $0 }.first { FileManager.default.fileExists(atPath: $0.appendingPathComponent("claude_usage.py").path) } ?? beside
        themeFile = (try? Data(contentsOf: scriptDir.appendingPathComponent("themes.json"))).flatMap { try? JSONDecoder().decode(ThemeFile.self, from: $0) }
    }
    func start() { refresh(); timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in self?.refresh() } }

    func refresh(force: Bool = false) {
        guard !loading else { return }
        loading = true
        let script = scriptDir.appendingPathComponent("claude_usage.py").path
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", script, "menu-json"] + (force ? ["--force"] : [])
            let out = Pipe(), err = Pipe(); p.standardOutput = out; p.standardError = err
            var result: Feed?; var msg: String?
            do {
                try p.run()
                // A Keychain consent dialog or a hung network call must not wedge refresh forever.
                DispatchQueue.global().asyncAfter(deadline: .now() + 90) { if p.isRunning { p.terminate() } }
                // Drain both pipes before waiting: a child blocked on a full 64 KB pipe never exits.
                let data = out.fileHandleForReading.readDataToEndOfFile()
                let errData = err.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                if p.terminationStatus == 0 { result = try JSONDecoder().decode(Feed.self, from: data) }
                else if p.terminationReason == .uncaughtSignal { msg = "Refresh took over 90 s and was stopped. Is a Keychain prompt waiting?" }
                else { msg = String(data: errData, encoding: .utf8)?.split(separator: "\n").last.map(String.init) ?? "exit \(p.terminationStatus)" }
            } catch { msg = error.localizedDescription }
            DispatchQueue.main.async {
                self.loading = false; self.lastRefresh = Date()
                let previous = self.feed
                if let result { self.feed = result; self.error = nil; Alerts.check(previous: previous, now: result) } else { self.error = msg }
                (NSApp.delegate as? AppDelegate)?.updateTitle()
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { (NSApp.delegate as? AppDelegate)?.updateTitle() }
            }
        }
    }

    /// What the menu bar shows. Keys: "today" or "<account>|<meter name>". Remembered across launches.
    @Published var barMode: String = UserDefaults.standard.string(forKey: "barMode") ?? "" {
        didSet { UserDefaults.standard.set(barMode, forKey: "barMode"); (NSApp.delegate as? AppDelegate)?.updateTitle() }
    }
    struct BarChoice: Identifiable { let id: String; let label: String }
    var barChoices: [BarChoice] {
        guard let f = feed else { return [] }
        var out: [BarChoice] = []
        let multi = f.live.count > 1
        for (acct, lv) in f.live.sorted(by: { $0.key < $1.key }) where shown("acct:" + acct) {
            for m in (lv.meters ?? []) { out.append(.init(id: "\(acct)|\(m.name)", label: (multi ? "\(acct) · " : "") + meterLabel(m.name))) }
        }
        out.append(.init(id: "today", label: "tokens today"))
        return out
    }
    /// Default: the 5-hour meter of the first account that has one; else today's tokens.
    var effectiveBarMode: String {
        let ids = barChoices.map(\.id)
        if ids.contains(barMode) { return barMode }
        return ids.first(where: { $0.hasSuffix("|five_hour") }) ?? "today"
    }
    var barText: String {
        guard let f = feed else { return "…" }
        let mode = effectiveBarMode
        if mode == "today" { return fmt(f.today.tokens) }
        let parts = mode.split(separator: "|", maxSplits: 1).map(String.init)
        if parts.count == 2, let m = f.live[parts[0]]?.meters?.first(where: { $0.name == parts[1] }) { return String(format: "%.0f%%", m.pct) }
        return fmt(f.today.tokens)
    }
}

// MARK: - alerts (macOS notifications at 80 / 95, and when a window resets)

enum Alerts {
    static var asked = false
    static func setup() {
        guard !asked else { return }; asked = true
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }
    static func post(_ title: String, _ body: String, id: String) {
        let c = UNMutableNotificationContent(); c.title = title; c.body = body; c.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: c, trigger: nil))
    }
    static func check(previous: Feed?, now: Feed) {
        guard let previous else { return }
        setup()
        for (acct, lv) in now.live where lv.stale != true {
            for m in (lv.meters ?? []) {
                guard let old = previous.live[acct]?.meters?.first(where: { $0.name == m.name }) else { continue }
                let label = "\(acct) · \(meterLabel(m.name))"
                for line in [80.0, 95.0] where old.pct < line && m.pct >= line {
                    post("\(label) at \(Int(m.pct))%", line == 95 ? "Almost at the wall. \(resetsText(m.resets_at))" : "Past 80%. \(resetsText(m.resets_at))",
                         id: "\(acct)-\(m.name)-\(Int(line))")
                }
                if old.pct >= 50 && m.pct < old.pct - 30 {
                    post("\(label) reset", "Back to \(Int(m.pct))%. Fresh window.", id: "\(acct)-\(m.name)-reset")
                }
            }
        }
    }
}

// MARK: - views

struct ChannelMeter: View {
    let pct: Double; var th: Theme = Theme(id: "system", def: nil); let segments = 24
    var body: some View {
        GeometryReader { g in
            let gap: CGFloat = 2
            let w = (g.size.width - gap * CGFloat(segments - 1)) / CGFloat(segments)
            HStack(spacing: gap) {
                ForEach(0..<segments, id: \.self) { i in
                    let lit = Double(i) < (pct / 100) * Double(segments)
                    let frac = Double(i + 1) / Double(segments)
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(lit ? (frac > 0.9 ? th.hot : frac > 0.7 ? th.warn : th.ok) : th.line)
                        .frame(width: w)
                }
            }
        }
        .frame(height: 10)
        .accessibilityLabel(String(format: "%.0f percent", pct))
    }
}

struct MeterRow: View {
    let meter: Meter; let tag: String; var th: Theme = Theme(id: "system", def: nil); var forecast = true; var me: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline) {
                Text(String(format: "%.0f%%", meter.pct)).font(.system(.body, design: .default).weight(.semibold).monospacedDigit())
                Text(tag + meterLabel(meter.name)).foregroundStyle(.secondary).font(.callout)
                Spacer()
                Text(resetsText(meter.resets_at)).foregroundStyle(.tertiary).font(.caption).monospacedDigit()
            }
            ChannelMeter(pct: meter.pct, th: th)
            let ft = forecast ? forecastText(meter.forecast) : ""
            if !ft.isEmpty { Text(ft).font(.caption).foregroundStyle(.secondary).monospacedDigit() }
            let sp = splitText(meter.split, me: me)
            if !sp.isEmpty { Text(sp).font(.caption).foregroundStyle(.secondary).monospacedDigit() }
        }
    }
}

/// One line naming the account above its meters, so each row can say just "5 hours" / "7 days".
/// Whoever has not reported into this account yet is said once here, not under every meter.
struct AccountHeader: View {
    let acct: String; let meters: [Meter]; var th: Theme = Theme(id: "system", def: nil)
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(acct).font(.callout.weight(.semibold)).lineLimit(1).truncationMode(.middle)
            let miss = missingText(meters.first(where: { $0.split?.missing?.isEmpty == false })?.split)
            if !miss.isEmpty { Text(miss).font(.caption).foregroundStyle(th.warn) }
        }
    }
}

struct Stat: View {
    let value: String; let label: String; var big = false
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value).font(big ? .title2.weight(.semibold) : .body.weight(.semibold)).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// A labelled share bar: name, tokens, cost, and a thin proportional bar underneath.
struct ShareRow: View {
    let name: String; let tokens: Double; let cost: Double; let total: Double; var tint: Color = .accentColor
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(name).font(.callout).lineLimit(1)
                Spacer()
                Text(fmt(tokens)).font(.callout).monospacedDigit().foregroundStyle(.secondary)
                Text(cost > 0 ? money(cost) : "").font(.callout).monospacedDigit().foregroundStyle(.tertiary).frame(width: 56, alignment: .trailing)
                Text(String(format: "%.0f%%", total > 0 ? tokens / total * 100 : 0)).font(.caption).monospacedDigit().foregroundStyle(.tertiary).frame(width: 32, alignment: .trailing)
            }
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 1.5).fill(Color.primary.opacity(0.08))
                    RoundedRectangle(cornerRadius: 1.5).fill(tint.opacity(0.85)).frame(width: max(2, g.size.width * (total > 0 ? tokens / total : 0)))
                }
            }.frame(height: 4)
        }
    }
}

struct MonthView: View {
    @ObservedObject var model: Model
    let months: [Month]
    let typeOrder: [(String, String, Color)] = [("input", "input", .blue), ("cache_w", "cache write", .orange), ("cache_read", "cache read", .teal), ("output", "output", .yellow)]
    var body: some View {
        let i = min(model.monthIndex, months.count - 1)
        let m = months[i]
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Button { model.monthIndex = min(i + 1, months.count - 1) } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.borderless).disabled(i >= months.count - 1).keyboardShortcut(.leftArrow, modifiers: [])
                Text(m.label).font(.headline).frame(maxWidth: .infinity)
                Button { model.monthIndex = max(i - 1, 0) } label: { Image(systemName: "chevron.right") }
                    .buttonStyle(.borderless).disabled(i == 0).keyboardShortcut(.rightArrow, modifiers: [])
            }
            HStack(alignment: .top, spacing: 22) {
                Stat(value: fmt(m.tokens), label: "tokens", big: true)
                Stat(value: money(m.cost), label: "api-equivalent", big: true)
                Stat(value: "\(m.calls)", label: "calls")
            }
            if m.tokens > 0 {
                Divider()
                Text("By type").font(.caption).foregroundStyle(.secondary)
                ForEach(typeOrder, id: \.0) { key, label, tint in
                    ShareRow(name: label, tokens: m.types[key] ?? 0, cost: 0, total: m.tokens, tint: tint)
                }
                if model.shown("sec:models") {
                    Divider()
                    Text("By model").font(.caption).foregroundStyle(.secondary)
                    ForEach(m.models.prefix(6)) { ShareRow(name: shortModel($0.model), tokens: $0.tokens, cost: $0.cost, total: m.tokens) }
                }
                if !m.projects.isEmpty, model.shown("sec:projects") {
                    Divider()
                    Text("By project").font(.caption).foregroundStyle(.secondary)
                    ForEach(m.projects.prefix(6)) { ShareRow(name: $0.project, tokens: $0.tokens, cost: $0.cost, total: m.tokens, tint: .purple) }
                }
            } else {
                Text("Nothing recorded this month.").font(.callout).foregroundStyle(.secondary)
            }
        }
    }
}

struct SettingsView: View {
    @ObservedObject var model: Model
    var body: some View {
        let me = model.feed?.user ?? "me"
        let others = model.feed?.people?.elements ?? []
        let accts = (model.feed?.live.keys.map { $0 } ?? []).sorted()
        VStack(alignment: .leading, spacing: 10) {
            Picker("Theme", selection: $model.themeID) {
                ForEach(model.themeChoices, id: \.0) { id, name in Text(name).tag(id) }
            }.pickerStyle(.menu).font(.callout)
            Divider()
            Text("People").font(.caption).foregroundStyle(.secondary)
            Toggle("\(me) (me)", isOn: model.binding("person:" + me))
            ForEach(others) { p in Toggle("\(p.user) · \(p.host)", isOn: model.binding("person:" + p.user)) }
            if others.isEmpty { Text("Nobody else shares into this folder yet.").font(.caption).foregroundStyle(.tertiary) }
            if accts.count > 1 {
                Divider()
                Text("Accounts").font(.caption).foregroundStyle(.secondary)
                ForEach(accts, id: \.self) { a in Toggle(a, isOn: model.binding("acct:" + a)) }
            }
            Divider()
            Text("Show").font(.caption).foregroundStyle(.secondary)
            Toggle("Claude's meters", isOn: model.binding("sec:meters"))
            Toggle("Room-left forecast under each meter", isOn: model.binding("sec:forecast"))
            Toggle("Today's totals", isOn: model.binding("sec:today"))
            Toggle("Current 5-hour window", isOn: model.binding("sec:window"))
            Toggle("Busiest models", isOn: model.binding("sec:models"))
            Toggle("Busiest projects", isOn: model.binding("sec:projects"))
            Toggle("Other people", isOn: model.binding("sec:people"))
            Divider()
            Toggle("Open at login", isOn: $model.atLogin)
            Text("Settings live on this Mac only. The dashboard has its own Customize button.").font(.caption).foregroundStyle(.tertiary)
        }
        .toggleStyle(.checkbox).font(.callout)
    }
}

struct ContentView: View {
    @ObservedObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("jusage").font(.headline)
                if let u = model.feed?.user { Text(u).foregroundStyle(.secondary) }
                Spacer()
                if let t = model.lastRefresh { Text(t, style: .time).font(.caption).foregroundStyle(.tertiary) }
                Button { model.refresh(force: true) } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless).help("Refresh now (⌘R)").keyboardShortcut("r").disabled(model.loading)
                Button { model.tab = model.tab == 2 ? 0 : 2 } label: { Image(systemName: model.tab == 2 ? "xmark.circle" : "slider.horizontal.3") }
                    .buttonStyle(.borderless).help("Customize (⌘,)").keyboardShortcut(",")
            }
            if model.tab != 2, model.feed?.months?.isEmpty == false {
                Picker("", selection: $model.tab) { Text("Today").tag(0); Text("Month").tag(1) }
                    .pickerStyle(.segmented).labelsHidden().controlSize(.small)
            }

            if model.tab == 2 {
                SettingsView(model: model)
            } else if let f = model.feed, model.tab == 1, let months = f.months, !months.isEmpty {
                MonthView(model: model, months: months)
            } else if let f = model.feed {
                let th = model.theme
                let me = f.user ?? "me"
                // A refresh that failed after a feed exists keeps the numbers on screen: they are the
                // last good ones and still worth reading. The reason goes above them until one succeeds.
                if let e = model.error {
                    Label(e, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(th.warn).lineLimit(3)
                }
                if let se = f.share_error {
                    Label("shared report not updated: " + se, systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(th.warn).lineLimit(3)
                }
                // my name at header size, the others below at the same size: one block per person, seen at a glance
                if model.shown("person:" + me) { Text(me).font(.title3.weight(.bold)) }
                // meters
                let lives = f.live.filter { model.shown("acct:" + $0.key) }.sorted { $0.key < $1.key }
                let anyMeters = lives.contains { !($0.value.meters ?? []).isEmpty }
                if model.shown("person:" + me), model.shown("sec:meters") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(lives, id: \.key) { acct, lv in
                            AccountHeader(acct: acct, meters: lv.meters ?? [], th: th)
                            ForEach(lv.meters ?? []) { MeterRow(meter: $0, tag: "", th: th, forecast: model.shown("sec:forecast"), me: f.user) }
                            if let e = lv.error {
                                // meters present + error = the last good reading; say when it is from and why it stopped
                                let when = (lv.meters ?? []).isEmpty ? "" : "Stale since \((lv.fetched ?? "").dropFirst(11).prefix(5)): "
                                Label(when + e, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.secondary).lineLimit(3)
                            }
                        }
                        if !anyMeters && lives.isEmpty {
                            Text("Run `jusage live` once in Terminal to show Claude's own limits here.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Divider()
                }
                // today
                if model.shown("person:" + me), model.shown("sec:today") {
                    HStack(alignment: .top, spacing: 22) {
                        Stat(value: fmt(f.today.tokens), label: "tokens today", big: true)
                        Stat(value: money(f.today.cost), label: "api-equivalent", big: true)
                        Stat(value: "\(f.today.calls ?? 0)", label: "calls")
                    }
                    if (f.today.unpriced ?? 0) > 0 {
                        Label("\(f.today.unpriced ?? 0) calls on models not in pricing.json — cost shown without them", systemImage: "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    }
                    let wins = (f.windows ?? []).filter { $0.open && model.shown("acct:" + $0.account) }
                    if !wins.isEmpty, model.shown("sec:window") {
                        HStack(alignment: .top, spacing: 22) {
                            ForEach(wins) { w in Stat(value: fmt(w.tokens), label: (wins.count > 1 ? "\(w.account) · " : "") + "5-hour window, since \(w.start.suffix(5))") }
                        }
                    }
                    HStack(spacing: 22) {
                        Stat(value: fmt(f.week.tokens), label: "7 days · \(money(f.week.cost))")
                        if f.accounts.count > 1 {
                            ForEach(f.accounts.filter { model.shown("acct:" + $0.key) }.sorted { $0.key < $1.key }, id: \.key) { k, v in Stat(value: fmt(v.tokens), label: k) }
                        }
                    }
                }
                // everyone else who shares into the same folder
                let others = (f.people?.elements ?? []).filter { model.shown("person:" + $0.user) }
                if !others.isEmpty, model.shown("sec:people") {
                    Divider()
                    ForEach(others) { p in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(p.user).font(.title3.weight(.bold))
                                Text(p.host).font(.caption).foregroundStyle(.tertiary)
                                Spacer()
                                let asOf = asOfText(p.updated)
                                if !asOf.isEmpty { Text(asOf).font(.caption).foregroundStyle(.tertiary) }
                                if let a = p.age_hours, a > 6 {
                                    Text("report \(Int(a.rounded())) h old").font(.caption).foregroundStyle(th.warn)
                                }
                            }
                            HStack(spacing: 22) {
                                Stat(value: fmt(p.today.tokens), label: "today · \(money(p.today.cost))")
                                Stat(value: fmt(p.week.tokens), label: "7 days · \(money(p.week.cost))")
                                Stat(value: fmt(p.month.tokens), label: "30 days · \(money(p.month.cost))")
                            }
                            if model.shown("sec:meters") {
                                ForEach(p.live.sorted { $0.key < $1.key }, id: \.key) { acct, lv in
                                    AccountHeader(acct: acct, meters: lv.meters ?? [], th: th)
                                    ForEach(lv.meters ?? []) { MeterRow(meter: $0, tag: "", th: th, forecast: model.shown("sec:forecast")) }
                                }
                            }
                        }
                    }
                }
                if !f.models.isEmpty, model.shown("person:" + me), model.shown("sec:models") {
                    Divider()
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(f.models.prefix(4)) { m in
                            HStack {
                                Text(shortModel(m.model)).font(.callout)
                                Spacer()
                                Text(fmt(m.tokens)).font(.callout).monospacedDigit().foregroundStyle(.secondary)
                                Text(money(m.cost)).font(.callout).monospacedDigit().foregroundStyle(.tertiary).frame(width: 52, alignment: .trailing)
                            }
                        }
                    }
                }
                if let pj = f.projects, !pj.isEmpty, model.shown("person:" + me), model.shown("sec:projects") {
                    Divider()
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Busiest projects today").font(.caption).foregroundStyle(.secondary)
                        ForEach(pj.prefix(6)) { ShareRow(name: $0.project, tokens: $0.tokens, cost: $0.cost, total: f.today.tokens, tint: th.accent) }
                    }
                }
            } else if let e = model.error {
                Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.secondary).font(.callout)
            } else {
                HStack { ProgressView().controlSize(.small); Text("Reading transcripts…").foregroundStyle(.secondary) }
            }

            Divider()
            if !model.barChoices.isEmpty {
                Picker("Menu bar shows", selection: Binding(get: { model.effectiveBarMode }, set: { model.barMode = $0 })) {
                    ForEach(model.barChoices) { Text($0.label).tag($0.id) }
                }
                .pickerStyle(.menu).font(.callout).controlSize(.small)
            }
            HStack {
                if let d = model.feed?.dashboard {
                    Button("Open dashboard") { NSWorkspace.shared.open(URL(fileURLWithPath: d)) }.keyboardShortcut("d")
                }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut("q")
            }
            .controlSize(.small)
        }
        .padding(14)
        .frame(width: 360)
        .background(model.theme.bg ?? Color.clear)
        .tint(model.theme.accent)
    }
}

// MARK: - app

final class Panel: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = Model()
    var item: NSStatusItem!
    var panel: Panel!
    var host: NSHostingView<ContentView>!

    func applicationDidFinishLaunching(_ n: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = item.button {
            b.font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .medium)
            b.title = "…"; b.toolTip = "jusage"
            b.action = #selector(toggle); b.target = self
        }
        // A borderless panel we place ourselves: no popover animation, never off-screen.
        host = NSHostingView(rootView: ContentView(model: model))
        let effect = NSVisualEffectView()
        effect.material = .popover; effect.state = .active; effect.blendingMode = .behindWindow
        effect.wantsLayer = true; effect.layer?.cornerRadius = 12; effect.layer?.masksToBounds = true
        host.translatesAutoresizingMaskIntoConstraints = false
        // Content scrolls vertically when it's taller than place() can fit on screen
        // (see place()); host keeps its full natural height so fittingSize still
        // reports true content size for the small (non-scrolling) case.
        let scroll = NSScrollView()
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = host
        effect.addSubview(scroll)
        NSLayoutConstraint.activate([scroll.leadingAnchor.constraint(equalTo: effect.leadingAnchor), scroll.trailingAnchor.constraint(equalTo: effect.trailingAnchor),
                                     scroll.topAnchor.constraint(equalTo: effect.topAnchor), scroll.bottomAnchor.constraint(equalTo: effect.bottomAnchor),
                                     host.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor), host.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor),
                                     host.topAnchor.constraint(equalTo: scroll.contentView.topAnchor)])
        panel = Panel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.contentView = effect
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = true
        panel.level = .popUpMenu; panel.hidesOnDeactivate = false; panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.moveToActiveSpace, .transient]
        panel.delegate = self
        applyTheme()
        model.start()
        if CommandLine.arguments.contains("--window") { showPanel() }
    }

    func applyTheme() {
        panel?.appearance = model.theme.appearance
        if panel?.isVisible == true { place() }
    }

    func updateTitle() {
        guard let b = item.button else { return }
        // Colour is the warning: red past 95 on any meter, amber when Fable's weekly runs 20+ points
        // ahead of the general weekly (Fable locks first) or any meter passes 80.
        var color: NSColor? = nil
        if let f = model.feed {
            var worst = 0.0, fableGap = 0.0
            for lv in f.live.values {
                for m in (lv.meters ?? []) { worst = max(worst, m.pct) }
                if let fab = lv.meters?.first(where: { $0.name == "seven_day_fable" }), let all = lv.meters?.first(where: { $0.name == "seven_day" }) {
                    fableGap = max(fableGap, fab.pct - all.pct)
                }
            }
            if worst >= 95 { color = .systemRed } else if worst >= 80 || fableGap >= 20 { color = .systemOrange }
        }
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .medium),
                                                    .foregroundColor: color ?? NSColor.labelColor, .baselineOffset: 0.5]
        b.attributedTitle = NSAttributedString(string: model.barText, attributes: attrs)
        if panel.isVisible { place() }
    }

    func place() {
        host.layoutSubtreeIfNeeded()
        let size = host.fittingSize
        guard let b = item.button, let bw = b.window else { return }
        let anchor = bw.convertToScreen(b.convert(b.bounds, to: nil))
        // bw.screen (not NSScreen.main) is the display the status item actually lives on,
        // so this already does the right thing when the menu bar is on a second display.
        let screen = bw.screen ?? NSScreen.main
        let vis = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let margin: CGFloat = 8
        // Never taller than the screen's visible area (below the menu bar / notch,
        // above the dock); host.fittingSize above is still the true, unclipped content
        // height for the small case — the NSScrollView added around host in
        // applicationDidFinishLaunching makes the excess scroll instead of clipping off-screen.
        let maxHeight = vis.height - margin * 2
        let height = min(size.height, maxHeight)
        var x = anchor.midX - size.width / 2
        x = min(max(x, vis.minX + margin), vis.maxX - size.width - margin)
        var y: CGFloat
        if size.height > maxHeight {
            // Content doesn't fit on screen: pin the top just under the menu bar
            // and let the scroll view carry the rest, instead of pinning the
            // bottom and letting the top run off the top of the screen.
            y = vis.minY + margin
        } else {
            y = anchor.minY - 6 - size.height
            y = max(y, vis.minY + margin)
        }
        panel.setFrame(NSRect(x: x, y: y, width: size.width, height: height), display: true)
    }

    func showPanel() {
        model.refresh()
        place()
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func toggle() {
        if panel.isVisible { panel.orderOut(nil) } else { showPanel() }
    }

    func windowDidResignKey(_ n: Notification) { panel.orderOut(nil) }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
