import Foundation

/// S1: feed schema 2 (engine -> app), SCORE section 1.7. Rigid: a wrong schema, a malformed field or a time
/// without an offset refuses the whole feed (the app keeps its last good one). Other people's reports are the
/// one exception: they decode one by one and a bad one is dropped (R-154).

enum FeedError: Error, Equatable, LocalizedError {
    case schemaMismatch(found: Int)
    case badField(String)
    case naiveTime(String)

    var errorDescription: String? {
        switch self {
        case .schemaMismatch(let n): return "Engine sent feed schema \(n); this app reads 2"
        case .badField(let f): return "Engine sent a feed with a bad field: \(f)"
        case .naiveTime(let s): return "Engine sent a time without an offset: \(s)"
        }
    }
}

/// The one ISO parser (R-155): internet date-time, an offset is required.
enum ISO {
    static let parser: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parse(_ s: String) throws -> Date {
        let hasOffset: Bool = {
            if s.hasSuffix("Z") { return true }
            let tail = Array(s.suffix(6))
            return tail.count == 6 && (tail[0] == "+" || tail[0] == "-") && tail[3] == ":"
        }()
        guard hasOffset else { throw FeedError.naiveTime(s) }
        guard let d = parser.date(from: s) else { throw FeedError.badField("time \(s)") }
        return d
    }
}

/// Decodes what it can; an element that fails is skipped and counted.
struct LossyArray<Element: Decodable>: Decodable {
    let elements: [Element]
    let dropped: Int

    private struct Skip: Decodable {}

    init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        var out: [Element] = []
        var bad = 0
        while !c.isAtEnd {
            do { out.append(try c.decode(Element.self)) }
            catch {
                bad += 1
                _ = try c.decode(Skip.self)   // advance past the bad element
            }
        }
        elements = out
        dropped = bad
    }
}

enum PaceState: String, Decodable, Comparable {
    case green, amber, red

    private var rank: Int { switch self { case .green: return 0; case .amber: return 1; case .red: return 2 } }
    static func < (a: PaceState, b: PaceState) -> Bool { a.rank < b.rank }
}

struct Feed: Decodable {
    struct Rules: Decodable {
        let staleAfterS: TimeInterval
        let redAt: Double
        let warmupE: Double
        let readEveryS: Int
        enum CodingKeys: String, CodingKey {
            case staleAfterS = "stale_after_s", redAt = "red_at", warmupE = "warmup_e", readEveryS = "read_every_s"
        }
    }
    struct Me: Decodable {
        let user: String
        let installId: String
        enum CodingKeys: String, CodingKey { case user, installId = "install_id" }
    }
    struct FeedErrorInfo: Decodable {
        let kind: String
        let text: String
        let until: Date?
    }
    struct Pace: Decodable {
        let e: Double
        let landing: Double?
        /// nil = no pace colour (reset due or stale): the app draws it grey (RULINGS 3).
        let state: PaceState?
        let forecast: Bool
        let runsOut: Date?
        let resetDue: Bool
        let words: String
        enum CodingKeys: String, CodingKey {
            case e, landing, state, forecast, runsOut = "runs_out", resetDue = "reset_due", words
        }
    }
    struct SplitPart: Decodable {
        let user: String
        let installId: String
        let usd: Double
        let share: Double
        enum CodingKeys: String, CodingKey { case user, installId = "install_id", usd, share }
    }
    struct Split: Decodable {
        let periodStart: Date
        let parts: [SplitPart]
        enum CodingKeys: String, CodingKey { case periodStart = "period_start", parts }
    }
    struct Meter: Decodable {
        let id: String
        let name: String
        let pct: Double
        /// nil when Anthropic sent no reset time (engine emits null): no reset text, pace guarded.
        let resetsAt: Date?
        let readAt: Date
        let source: String
        let periodS: TimeInterval
        let disagree: Bool
        let pace: Pace
        let split: Split?
        enum CodingKeys: String, CodingKey {
            case id, name, pct, resetsAt = "resets_at", readAt = "read_at", source, periodS = "period_s", disagree, pace, split
        }
    }
    struct SessionShareEntry: Decodable { let start: Date; let pct: Double }
    struct SessionShare: Decodable {
        let current: Double?
        let last: Double?
        let median7: Double?
        let list: [SessionShareEntry]
        let asOf: Date
        enum CodingKeys: String, CodingKey { case current, last, median7, list, asOf = "as_of" }
    }
    struct Account: Decodable {
        let key: String
        let number: Int
        /// nil when the folder has no login identity (engine emits null).
        let email: String?
        let uuid: String?
        let folders: [String]
        let weeklyReset: Date?
        let flags: [String]
        let warning: String?
        let error: FeedErrorInfo?
        let noSessionMeter: Bool
        let meters: [Meter]
        let sessionShare: SessionShare?
        let missingPeople: [String]
        enum CodingKeys: String, CodingKey {
            case key, number, email, uuid, folders, weeklyReset = "weekly_reset", flags, warning, error
            case noSessionMeter = "no_session_meter", meters, sessionShare = "session_share", missingPeople = "missing_people"
        }
    }
    struct Spend: Decodable {
        let tokens: Int?
        let usd: Double?
        let calls: Int?
        let unpriced: Int?
    }
    struct Totals: Decodable {
        let asOf: Date
        let today: Spend
        let week: Spend
        enum CodingKeys: String, CodingKey { case asOf = "as_of", today, week }
    }
    struct Window: Decodable {
        let account: String
        let start: Date
        let end: Date
        let tokens: Int
        let usd: Double
        let open: Bool
    }
    struct ModelUse: Decodable { let id: String; let name: String; let tokens: Int; let usd: Double }
    struct ProjectUse: Decodable { let name: String; let share: Double; let tokens: Int }
    struct TokenTypes: Decodable {
        let input: Int
        let cacheW: Int
        let cacheRead: Int
        let output: Int
        enum CodingKeys: String, CodingKey { case input, cacheW = "cache_w", cacheRead = "cache_read", output }
    }
    struct Month: Decodable {
        let key: String
        let label: String
        let tokens: Int
        let usd: Double
        let calls: Int
        let types: TokenTypes
        let models: [ModelUse]
        let projects: [ProjectUse]
    }
    struct Person: Decodable {
        let installId: String
        let user: String
        let host: String
        let generated: Date
        let today: Spend
        let week: Spend
        let accounts: [Account]
        enum CodingKeys: String, CodingKey { case installId = "install_id", user, host, generated, today, week, accounts }
    }
    struct StatusLine: Decodable {
        let enabled: Bool
        let captureAt: Date?
        enum CodingKeys: String, CodingKey { case enabled, captureAt = "capture_at" }
    }
    struct Share: Decodable {
        let writtenAt: Date?
        let error: String?
        enum CodingKeys: String, CodingKey { case writtenAt = "written_at", error }
    }

    let schema: Int
    let version: String
    let generated: Date
    let nextDue: Date
    let rules: Rules
    let me: Me
    let accounts: [Account]
    let totals: Totals
    let windows: [Window]
    let models: [ModelUse]
    /// The last 7 days' projects; `projectsToday` is today's, for the panel's "Projects today" (RULINGS 48h).
    let projects: [ProjectUse]
    let projectsToday: [ProjectUse]
    let months: [Month]
    private let peopleBox: LossyArray<Person>
    let statusline: [String: StatusLine]
    let dashboard: String?
    let share: Share
    let notes: [String]

    /// Peers whose report decoded; a bad one is dropped (R-154).
    var people: [Person] { peopleBox.elements }
    var droppedPeople: Int { peopleBox.dropped }

    enum CodingKeys: String, CodingKey {
        case schema, version, generated, nextDue = "next_due", rules, me, accounts, totals, windows, models, projects
        case projectsToday = "projects_today", months
        case peopleBox = "people", statusline, dashboard, share, notes
    }

    private struct Probe: Decodable { let schema: Int? }

    /// Refuses `schema != 2` before decoding anything else, naming the field.
    static func decode(_ data: Data) throws -> Feed {
        let probe: Probe
        do { probe = try JSONDecoder().decode(Probe.self, from: data) }
        catch { throw FeedError.badField("schema") }
        guard let found = probe.schema else { throw FeedError.badField("schema") }
        guard found == 2 else { throw FeedError.schemaMismatch(found: found) }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { d in
            let s = try d.singleValueContainer().decode(String.self)
            do { return try ISO.parse(s) }
            catch FeedError.badField(let what) {
                let path = d.codingPath.map(\.stringValue).joined(separator: ".")
                throw FeedError.badField("\(path): \(what)")
            }
        }
        do { return try decoder.decode(Feed.self, from: data) }
        catch let e as FeedError { throw e }
        catch let e as DecodingError { throw FeedError.badField(describe(e)) }
    }

    private static func describe(_ e: DecodingError) -> String {
        func path(_ c: DecodingError.Context) -> String { c.codingPath.map(\.stringValue).joined(separator: ".") }
        switch e {
        case .keyNotFound(let k, let c): return (c.codingPath.map(\.stringValue) + [k.stringValue]).joined(separator: ".")
        case .typeMismatch(_, let c), .valueNotFound(_, let c), .dataCorrupted(let c): return path(c)
        @unknown default: return "feed"
        }
    }
}

extension Feed.Account {
    /// The short name to show: the email's local part, else the config folder's name without its leading dot
    /// and "claude" prefix, else "Account N". An account with no login identity still renders.
    var label: String {
        if let email, !email.isEmpty { return email.split(separator: "@", maxSplits: 1).first.map(String.init) ?? email }
        if let f = folders.first {
            let name = (f as NSString).lastPathComponent
            let trimmed = name.drop(while: { $0 == "." })
            if !trimmed.isEmpty { return String(trimmed) }
        }
        return "Account \(number)"
    }

    /// The full line for hovers and notifications: the email, or the label when there is none.
    var identity: String { email.flatMap { $0.isEmpty ? nil : $0 } ?? label }
}
