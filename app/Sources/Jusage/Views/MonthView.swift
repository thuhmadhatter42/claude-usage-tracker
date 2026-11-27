import SwiftUI

/// S21: the Month tab (R-83). The engine sends the last 3 months, this one first; ‹ walks back, › forward, both
/// Quick Keys rows (← / →). AppModel clamps the index when a new feed has fewer months; the buttons never step
/// past either end.
enum MonthNav {
    static func older(_ i: Int, count: Int) -> Int { max(0, min(i + 1, count - 1)) }
    static func newer(_ i: Int, count: Int) -> Int { max(0, min(i - 1, count - 1)) }
}

struct TypeShare: Identifiable, Equatable {
    var id: String { name }
    let name: String
    let tokens: Int
    /// Theme palette key for this type's colour (R-83: never fixed system colours).
    let colourKey: String
}

struct MonthModel: Equatable {
    static let nothing = "Nothing recorded this month."

    let label: String
    let summary: String
    let types: [TypeShare]
    let models: [ValueRow]
    let projects: [ValueRow]
    let empty: Bool

    /// nil when `index` is not one of the feed's months: an out-of-range index is refused, never read.
    static func make(feed: Feed, index: Int) -> MonthModel? {
        guard feed.months.indices.contains(index) else { return nil }
        let m = feed.months[index]
        let t = m.types
        return MonthModel(
            label: m.label,
            summary: "\(Fmt.tokens(m.tokens)) tokens · \(Fmt.usd(m.usd)) API-rate · \(m.calls) calls",
            types: [TypeShare(name: "Input", tokens: t.input, colourKey: "ink"),
                    TypeShare(name: "Cache write", tokens: t.cacheW, colourKey: "ink2"),
                    TypeShare(name: "Cache read", tokens: t.cacheRead, colourKey: "mut"),
                    TypeShare(name: "Output", tokens: t.output, colourKey: "ok")],
            models: m.models.prefix(6).map { ValueRow(id: $0.id, name: $0.name, text: "\(Fmt.tokens($0.tokens)) · \(Fmt.usd($0.usd))") },
            projects: m.projects.prefix(6).map { ValueRow(id: $0.name, name: $0.name, text: "\(Int(($0.share * 100).rounded()))%") },
            empty: m.tokens == 0 && m.calls == 0)
    }
}

struct MonthView: View {
    let feed: Feed
    @Binding var index: Int
    let palette: Palette
    @EnvironmentObject private var services: PanelServices

    var body: some View {
        let count = feed.months.count
        VStack(alignment: .leading, spacing: Layout.blockGap) {
            HStack {
                Button { index = MonthNav.older(index, count: count) } label: { Image(systemName: "chevron.left") }
                    .keyboardShortcut(services.shortcut(QuickKeyID.previousMonth))
                    .disabled(index >= count - 1)
                Spacer()
                Text(MonthModel.make(feed: feed, index: index)?.label ?? "").font(Layout.body.weight(.semibold))
                Spacer()
                Button { index = MonthNav.newer(index, count: count) } label: { Image(systemName: "chevron.right") }
                    .keyboardShortcut(services.shortcut(QuickKeyID.nextMonth))
                    .disabled(index <= 0)
            }
            .buttonStyle(.plain)
            .foregroundColor(palette.ink)

            if let m = MonthModel.make(feed: feed, index: index) {
                if m.empty {
                    Text(MonthModel.nothing).font(Layout.small).foregroundColor(palette.muted)
                } else {
                    Text(m.summary).font(Layout.small.monospacedDigit()).foregroundColor(palette.muted)
                        .help(SpendLineModel.apiRate)
                    TypeBar(types: m.types, palette: palette)
                    ForEach(m.models) { FoldRow(title: $0.name, value: $0.text, palette: palette) }
                    Divider()
                    ForEach(m.projects) { FoldRow(title: $0.name, value: $0.text, palette: palette) }
                }
            }
        }
    }
}

/// Tokens by type as one stacked bar with a legend; each part's count is in its hover.
struct TypeBar: View {
    let types: [TypeShare]
    let palette: Palette

    var body: some View {
        let total = max(1, types.reduce(0) { $0 + $1.tokens })
        VStack(alignment: .leading, spacing: Layout.lineGap) {
            GeometryReader { g in
                HStack(spacing: 1) {
                    ForEach(types) { t in
                        Rectangle().fill(palette.colour(t.colourKey))
                            .frame(width: g.size.width * CGFloat(t.tokens) / CGFloat(total))
                            .help("\(t.name): \(Fmt.tokens(t.tokens)) tokens")
                    }
                }
            }
            .frame(height: Layout.barHeight)
            .clipShape(RoundedRectangle(cornerRadius: Layout.barHeight / 2))
            HStack(spacing: 8) {
                ForEach(types) { t in
                    HStack(spacing: 3) {
                        Circle().fill(palette.colour(t.colourKey)).frame(width: 6, height: 6)
                        Text(t.name).font(Layout.small).foregroundColor(palette.muted)
                    }
                    .help("\(t.name): \(Fmt.tokens(t.tokens)) tokens")
                }
            }
        }
    }
}
