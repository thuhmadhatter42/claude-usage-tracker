import Foundation
import Combine

/// S5: the app's settings, one typed record (R-145, R-146, R-148). Keys are folder keys and install ids, never
/// emails or first names. Stored as JSON under `settings.v2` in the app's UserDefaults (bundle id unchanged).

/// The panel's Show toggles that hide a whole part (R-84, RULINGS 48a): Claude's meters (the account blocks),
/// open 5-hour windows, today's totals, other people, busiest models, busiest projects.
enum Section: String, Codable, CaseIterable {
    case meters, windows, today, people, models, projects
}

typealias FoldId = String

enum MenuBarMode: Codable, Equatable {
    case meter(String)
    case tokensToday

    init(from decoder: Decoder) throws {
        let s = try decoder.singleValueContainer().decode(String.self)
        if s == "tokensToday" { self = .tokensToday; return }
        if s.hasPrefix("meter:"), s.count > 6 { self = .meter(String(s.dropFirst(6))); return }
        throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "bad menu bar mode \(s)"))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .tokensToday: try c.encode("tokensToday")
        case .meter(let id): try c.encode("meter:\(id)")
        }
    }
}

enum NotifyKind: String, Codable, CaseIterable {
    case warn80, critical, runsOut, reset
}

/// One latch per account + meter + kind. Encodes as a JSON object key.
struct LatchKey: Hashable, Codable, RawRepresentable, CodingKeyRepresentable {
    let folderKey: String
    let meterId: String
    let kind: NotifyKind

    private static let sep: Character = "\u{1F}"

    var rawValue: String { [folderKey, meterId, kind.rawValue].joined(separator: String(LatchKey.sep)) }

    init(folderKey: String, meterId: String, kind: NotifyKind) {
        self.folderKey = folderKey; self.meterId = meterId; self.kind = kind
    }

    init?(rawValue: String) {
        let p = rawValue.split(separator: LatchKey.sep, omittingEmptySubsequences: false).map(String.init)
        guard p.count == 3, let k = NotifyKind(rawValue: p[2]) else { return nil }
        self.init(folderKey: p[0], meterId: p[1], kind: k)
    }

    var codingKey: CodingKey { LatchCodingKey(stringValue: rawValue) }
    init?<T: CodingKey>(codingKey: T) { self.init(rawValue: codingKey.stringValue) }

    init(from decoder: Decoder) throws {
        let s = try decoder.singleValueContainer().decode(String.self)
        guard let k = LatchKey(rawValue: s) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "bad latch key"))
        }
        self = k
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

private struct LatchCodingKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

struct LatchState: Codable, Equatable {
    /// The meter's reset time when this latch was last set; a different cycle starts when it moves far enough.
    var cycleResetsAt: Date?
    /// True while the condition has fired and not yet recovered.
    var fired: Bool
    /// True once anything fired in this cycle (decides whether a reset is worth a notification).
    var firedThisCycle: Bool
}

struct AppSettings: Codable, Equatable {
    static let readEveryRange = 15...600
    static let readEveryDefault = 60
    static let maxTag = 4

    var hiddenAccounts: [String] = []
    var hiddenPeople: [String] = []
    var hiddenSections: [Section] = []
    /// Open folds only; its own set, not mixed into the visibility toggles and not stored inverted (R-82).
    var openFolds: [FoldId] = []
    var theme: String = "system"
    var menuBarMode: MenuBarMode = .meter("session")
    /// "Labels" (meter row labels). Off by default.
    var labels: Bool = false
    /// "Menu bar labels": a tag before each menu bar number. Off by default.
    var menuBarLabels: Bool = false
    /// Folder key -> tag of up to 4 characters.
    var menuBarTags: [String: String] = [:]
    /// Colour each menu bar number by the account's worst meter instead of the shown meter (Amendment). Off by default.
    var colourByWorstMeter: Bool = false
    var warnRunsOut: Bool = true
    var notificationLatch: [LatchKey: LatchState] = [:]
    var pythonPath: String? = nil
    var accent: String = "#ffb020"
    /// Check-every cadence in seconds, 60 by default, always within 15...600. The engine's config owns the live
    /// value (`config read-every`); this mirrors what the user last set so the field is never blank.
    private var _readEveryS: Int = AppSettings.readEveryDefault
    var readEveryS: Int {
        get { _readEveryS }
        set { _readEveryS = min(max(newValue, AppSettings.readEveryRange.lowerBound), AppSettings.readEveryRange.upperBound) }
    }

    init() {}

    enum CodingKeys: String, CodingKey {
        case hiddenAccounts, hiddenPeople, hiddenSections, openFolds, theme, menuBarMode, labels, menuBarLabels
        case menuBarTags, colourByWorstMeter, warnRunsOut, notificationLatch, pythonPath, accent
        case _readEveryS = "readEveryS"
    }

    /// Every key is optional on read, so a record written by a later 0.2.x still loads.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func get<T: Decodable>(_ k: CodingKeys, _ d: T) throws -> T { try c.decodeIfPresent(T.self, forKey: k) ?? d }
        hiddenAccounts = try get(.hiddenAccounts, [])
        hiddenPeople = try get(.hiddenPeople, [])
        hiddenSections = try get(.hiddenSections, [])
        openFolds = try get(.openFolds, [])
        theme = try get(.theme, "system")
        menuBarMode = try get(.menuBarMode, .meter("session"))
        labels = try get(.labels, false)
        menuBarLabels = try get(.menuBarLabels, false)
        menuBarTags = try get(.menuBarTags, [:])
        colourByWorstMeter = try get(.colourByWorstMeter, false)
        warnRunsOut = try get(.warnRunsOut, true)
        notificationLatch = try get(.notificationLatch, [:])
        pythonPath = try c.decodeIfPresent(String.self, forKey: .pythonPath)
        accent = try get(.accent, "#ffb020")
        readEveryS = try get(._readEveryS, AppSettings.readEveryDefault)
    }

    /// A tag is at most 4 characters; an empty one removes the tag (the account number shows instead).
    mutating func setTag(_ text: String, for folderKey: String) {
        let t = String(text.trimmingCharacters(in: .whitespaces).prefix(AppSettings.maxTag))
        if t.isEmpty { menuBarTags.removeValue(forKey: folderKey) } else { menuBarTags[folderKey] = t }
    }

    func isAccountShown(_ key: String) -> Bool { !hiddenAccounts.contains(key) }
    func isPersonShown(_ installId: String) -> Bool { !hiddenPeople.contains(installId) }
    func isSectionShown(_ s: Section) -> Bool { !hiddenSections.contains(s) }
    func isFoldOpen(_ id: FoldId) -> Bool { openFolds.contains(id) }

    mutating func setFold(_ id: FoldId, open: Bool) {
        if open { if !openFolds.contains(id) { openFolds.append(id) } } else { openFolds.removeAll { $0 == id } }
    }
}

final class SettingsStore: ObservableObject {
    static let key = "settings.v2"
    static let migratedKey = "settings.v2.migratedFrom01"

    @Published private(set) var settings = AppSettings()
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        load()
    }

    /// Reads the stored record; none or an unreadable one gives the defaults.
    func load() {
        guard let data = defaults.data(forKey: SettingsStore.key),
              let s = try? JSONDecoder().decode(AppSettings.self, from: data) else {
            settings = AppSettings()
            return
        }
        settings = s
    }

    func save() {
        if let data = try? JSONEncoder().encode(settings) { defaults.set(data, forKey: SettingsStore.key) }
    }

    /// Change, then save: the one write path for views.
    func update(_ change: (inout AppSettings) -> Void) {
        var s = settings
        change(&s)
        settings = s
        save()
    }

    var migrated: Bool { defaults.bool(forKey: SettingsStore.migratedKey) }

    /// Once, from the 0.1.x keys `hidden`, `theme`, `barMode` (left in place for rollback). Emails and names in
    /// `hidden` map through the first feed's accounts and people; unmatched ones are dropped with one log line.
    /// Values the user already changed in 0.2 are not overwritten. Returns false when it had already run.
    @discardableResult
    func migrateFrom01(defaults old: UserDefaults, feed: Feed, log: (String) -> Void = { _ in }) -> Bool {
        guard !migrated else { return false }
        var s = settings
        var dropped: [String] = []

        let byEmail = Dictionary(feed.accounts.compactMap { a in a.email.map { ($0, a.key) } }, uniquingKeysWith: { a, _ in a })
        let allPeople = feed.people.map { ($0.user, $0.installId) } + [(feed.me.user, feed.me.installId)]
        let byName = Dictionary(allPeople, uniquingKeysWith: { a, _ in a })

        for entry in old.stringArray(forKey: "hidden") ?? [] {
            if entry.hasPrefix("acct:") {
                if let k = byEmail[String(entry.dropFirst(5))] { if !s.hiddenAccounts.contains(k) { s.hiddenAccounts.append(k) } }
                else { dropped.append(entry) }
            } else if entry.hasPrefix("person:") {
                if let id = byName[String(entry.dropFirst(7))] { if !s.hiddenPeople.contains(id) { s.hiddenPeople.append(id) } }
                else { dropped.append(entry) }
            } else if entry.hasPrefix("sec:") {
                if let sec = Section(rawValue: String(entry.dropFirst(4))) {
                    if !s.hiddenSections.contains(sec) { s.hiddenSections.append(sec) }
                } else { dropped.append(entry) }
            } else if entry.hasPrefix("fold:") {
                // 0.1.x stored OPEN folds in the hidden set (inverted); they become openFolds.
                let rest = String(entry.dropFirst(5))
                if rest.hasPrefix("person:") {
                    if let id = byName[String(rest.dropFirst(7))] { s.setFold("person:\(id)", open: true) } else { dropped.append(entry) }
                } else if rest == "detail" || rest == "projects" { s.setFold(rest, open: true) }
                else { dropped.append(entry) }
            } else { dropped.append(entry) }
        }
        if !dropped.isEmpty { log("settings migration: dropped \(dropped.count) unmatched entries from 0.1.x 'hidden': \(dropped.joined(separator: ", "))") }

        if s.theme == "system", let t = old.string(forKey: "theme"), !t.isEmpty { s.theme = t }
        if s.menuBarMode == .meter("session"), let raw = old.string(forKey: "barMode"), !raw.isEmpty {
            s.menuBarMode = SettingsStore.mode(fromOld: raw)
        }
        settings = s
        save()
        defaults.set(true, forKey: SettingsStore.migratedKey)
        return true
    }

    /// 0.1.x bar mode: "today", a meter name, or `account|meter` (the account part is dropped).
    static func mode(fromOld raw: String) -> MenuBarMode {
        let name = raw.split(separator: "|", maxSplits: 1).map(String.init).last ?? raw
        switch name {
        case "today": return .tokensToday
        case "five_hour": return .meter("session")
        case "seven_day": return .meter("weekly")
        default:
            if name.hasPrefix("seven_day_") { return .meter("weekly:" + name.dropFirst("seven_day_".count)) }
            return .meter("session")
        }
    }
}
