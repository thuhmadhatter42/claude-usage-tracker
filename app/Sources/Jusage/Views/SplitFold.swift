import SwiftUI

/// S20: "Split, window, models" (R-80): per-meter split, me first; open 5-hour windows; the top 4 models.
/// Row ids are folder key + meter, so two accounts whose names share a prefix never collide.
struct SplitRow: Identifiable, Equatable {
    let id: String
    let title: String
    let text: String
}

struct ValueRow: Identifiable, Equatable {
    let id: String
    let name: String
    let text: String
}

enum SplitModel {
    static func rows(feed: Feed, settings: AppSettings) -> [SplitRow] {
        TodayModel.accounts(feed: feed, settings: settings).flatMap { a in
            MeterRowModel.ordered(a.meters).compactMap { m -> SplitRow? in
                guard let split = m.split else { return nil }
                return SplitRow(id: "\(a.key)|\(m.id)", title: "\(AccountBlockModel.localPart(a.email)) · \(m.name)",
                                text: text(split.parts, me: feed.me.installId))
            }
        }
    }

    /// "Sam 71% · Lee 29%", me first, the others in the engine's order.
    static func text(_ parts: [Feed.SplitPart], me: String) -> String {
        let ordered = parts.filter { $0.installId == me } + parts.filter { $0.installId != me }
        return ordered.map { "\($0.user) \(Int(($0.share * 100).rounded()))%" }.joined(separator: " · ")
    }
}

enum DetailModel {
    static let title = "Split, window, models"

    /// Open 5-hour windows on shown accounts: "sam 19:00–00:00  6.2M · $21.40".
    static func windows(feed: Feed, settings: AppSettings, time: DisplayTime) -> [ValueRow] {
        guard settings.isSectionShown(.windows) else { return [] }     // "Open 5-hour windows" off (R-84)
        return feed.windows.filter(\.open).compactMap { w in
            let owner = feed.accounts.first { $0.key == w.account || $0.folders.contains(w.account) }
            if let owner, !settings.isAccountShown(owner.key) { return nil }
            let name = owner.map { AccountBlockModel.localPart($0.email) } ?? w.account
            return ValueRow(id: "\(w.account)|\(w.start.timeIntervalSince1970)",
                            name: "\(name) \(time.clock(w.start))–\(time.clock(w.end))",
                            text: "\(Fmt.tokens(w.tokens)) · \(Fmt.usd(w.usd))")
        }
    }

    static func models(feed: Feed) -> [ValueRow] {
        feed.models.prefix(4).map { ValueRow(id: $0.id, name: $0.name, text: "\(Fmt.tokens($0.tokens)) · \(Fmt.usd($0.usd))") }
    }
}

extension ValueRow {
    /// One string for a window row (tests, VoiceOver).
    var line: String { "\(name)  \(text)" }
}

struct SplitFold: View {
    let feed: Feed
    let settings: AppSettings
    let time: DisplayTime
    let palette: Palette
    let setFold: (FoldId, Bool) -> Void

    var body: some View {
        Fold(id: FoldID.detail, title: DetailModel.title, trailing: nil, stale: false,
             open: settings.isFoldOpen(FoldID.detail), palette: palette, setFold: setFold) {
            VStack(alignment: .leading, spacing: Layout.lineGap) {
                ForEach(SplitModel.rows(feed: feed, settings: settings)) { FoldRow(title: $0.title, value: $0.text, palette: palette) }
                ForEach(DetailModel.windows(feed: feed, settings: settings, time: time)) {
                    FoldRow(title: $0.name, value: $0.text, palette: palette)
                }
                ForEach(DetailModel.models(feed: feed)) { FoldRow(title: $0.name, value: $0.text, palette: palette) }
            }
        }
    }
}
