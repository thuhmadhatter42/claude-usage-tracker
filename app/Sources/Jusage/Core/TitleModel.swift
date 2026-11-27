import Foundation

/// S7 (pure part): the menu bar title as data. The status item (S12) turns it into an attributed string.
struct TitleItem: Equatable {
    let text: String
    /// The account number or the user's short name, only when "Menu bar labels" is on.
    let tag: String?
    /// The pace colour, nil when grey or uncoloured.
    let colour: PaceState?
    /// Grey: the number's reading is 15 minutes old or more (R-48), each number on its own.
    let stale: Bool
    let hover: String
}

enum TitleModel {
    static let waiting = "…"
    static let warning = "⚠︎"
    static let missing = "—"

    static func make(feed: Feed?, settings: AppSettings, now: Date, runError: String? = nil,
                     hour12: Bool = Fmt.systemHour12, timeZone: TimeZone = .current) -> [TitleItem] {
        guard let feed else {
            if let runError {
                return [TitleItem(text: warning, tag: nil, colour: nil, stale: false, hover: runError)]
            }
            return [TitleItem(text: waiting, tag: nil, colour: nil, stale: false, hover: "Waiting for the first reading.")]
        }

        if settings.menuBarMode == .tokensToday {
            let age = now.timeIntervalSince(feed.totals.asOf)
            let stale = age >= feed.rules.staleAfterS
            var hover = "Tokens today"
            if stale { hover += ". Updated \(Fmt.age(age)) (\(Fmt.clock(feed.totals.asOf, now: now, hour12: hour12, timeZone: timeZone)))." }
            return [TitleItem(text: Fmt.tokens(feed.totals.today.tokens ?? 0), tag: nil, colour: nil, stale: stale, hover: hover)]
        }
        guard case .meter(let meterId) = settings.menuBarMode else { return [] }

        // Order comes from the account number, never from the labels: #1 first, #2 second, the rest by number.
        let accounts = feed.accounts
            .filter { settings.isAccountShown($0.key) }
            .sorted { $0.number < $1.number }
        return accounts.map { item(for: $0, meterId: meterId, feed: feed, settings: settings, now: now, hour12: hour12, timeZone: timeZone) }
    }

    private static func item(for a: Feed.Account, meterId: String, feed: Feed, settings: AppSettings, now: Date,
                             hour12: Bool, timeZone: TimeZone) -> TitleItem {
        let tag: String? = settings.menuBarLabels ? (settings.menuBarTags[a.key] ?? String(a.number)) : nil
        guard let m = a.meters.first(where: { $0.id == meterId }) else {
            return TitleItem(text: missing, tag: tag, colour: nil, stale: false,
                             hover: "\(a.email)\nNo \(meterId) reading for this account.")
        }
        let fresh = feed.freshness(of: m, in: a, now: now)
        let stale = fresh.isStale

        var colour: PaceState? = nil
        if !stale {
            if settings.colourByWorstMeter {
                // Worst of this account's meters that are fresh and carry a colour (stale ones never count).
                colour = a.meters
                    .filter { !feed.freshness(of: $0, in: a, now: now).isStale }
                    .compactMap { $0.pace.state }
                    .max()
            } else {
                colour = m.pace.state
            }
        }

        var lines = [a.email]
        lines.append("\(m.name) \(pctText(m.pct)) · resets \(Fmt.clock(m.resetsAt, now: now, hour12: hour12, timeZone: timeZone)) · \(m.source) · read \(Fmt.age(now.timeIntervalSince(m.readAt)))")
        if let why = fresh.hover(readAt: m.readAt, now: now, hour12: hour12, timeZone: timeZone) { lines.append(why) }
        return TitleItem(text: pctText(m.pct), tag: tag, colour: colour, stale: stale, hover: lines.joined(separator: "\n"))
    }

    static func pctText(_ pct: Double) -> String { String(format: "%.0f%%", pct) }
}
