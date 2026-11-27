import SwiftUI

/// S18: "Today 1.4M tokens · $412 API-rate   7d $1,889" (R-77, R-109). Hover: what API-rate means, the unpriced
/// calls when any, and the age when grey (R-48).
struct SpendLineModel: Equatable {
    let text: String
    let hover: String
    let stale: Bool

    static let apiRate = "API-rate equivalent: what these tokens would cost at Anthropic's API prices. Not your bill."

    static func make(today: Feed.Spend, week: Feed.Spend, asOf: Date, staleAfter: TimeInterval, time: DisplayTime) -> SpendLineModel {
        let text = "Today \(Fmt.tokens(today.tokens ?? 0)) tokens · \(Fmt.usd(today.usd ?? 0)) API-rate   7d \(Fmt.usd(week.usd ?? 0))"
        var hover = [apiRate]
        let unpriced = (today.unpriced ?? 0)
        if unpriced > 0 { hover.append("\(unpriced) calls on unpriced models, not in $") }
        let fresh = freshness(readAt: asOf, error: nil, resetsAt: nil, now: time.now, staleAfter: staleAfter)
        if let why = time.staleHover(fresh, readAt: asOf) { hover.append(why) }
        return SpendLineModel(text: text, hover: hover.joined(separator: "\n"), stale: fresh.isStale)
    }
}

struct SpendLine: View {
    let model: SpendLineModel
    let palette: Palette

    var body: some View {
        Text(model.text).font(Layout.small.monospacedDigit())
            .foregroundColor(model.stale ? palette.grey : palette.muted)
            .lineLimit(1).minimumScaleFactor(0.8)
            .help(model.hover)
    }
}
