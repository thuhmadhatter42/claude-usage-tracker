import SwiftUI

/// S18: one account (R-5, R-67..R-70, R-76, R-88). Header: the email's local part on the left (full email on
/// hover), the weekly reset on the right, always. Then the meters, the "not reported yet" lines, the session
/// share line. A stale or errored account keeps its numbers, greyed by each meter's own freshness.
struct ShareLineModel: Equatable {
    let text: String
    let hover: String
    let stale: Bool
}

struct AccountBlockModel: Identifiable, Equatable {
    let id: String
    let name: String
    let resetText: String
    /// Short words for `flags`, shown before the reset (R-5).
    let flagWords: [String]
    let headerHover: String
    let missingLines: [String]
    let meters: [MeterRowModel]
    let share: ShareLineModel?

    static let flagText: [String: (word: String, hover: String)] = [
        "using_credits": ("credits", "Using credits"),
        "reset_available": ("reset ready", "Limit reset available"),
    ]

    static func localPart(_ email: String) -> String {
        email.split(separator: "@", maxSplits: 1).first.map(String.init) ?? email
    }

    static func make(account a: Feed.Account, feed: Feed, settings: AppSettings, time: DisplayTime, own: Bool) -> AccountBlockModel {
        let flags = a.flags.compactMap { flagText[$0] }
        var hover = [a.email] + flags.map(\.hover)
        if let w = a.warning { hover.append(w) }

        var seen = Set<String>()
        let missing = a.missingPeople.filter { seen.insert($0).inserted }.map { "\($0): not reported yet (update jusage)" }

        return AccountBlockModel(
            id: a.key, name: localPart(a.email),
            resetText: "Resets \(a.weeklyReset.map(time.clock) ?? "—")",
            flagWords: flags.map(\.word), headerHover: hover.joined(separator: "\n"), missingLines: missing,
            meters: MeterRowModel.ordered(a.meters).map {
                MeterRowModel.make(meter: $0, account: a, feed: feed, settings: settings, time: time, own: own)
            },
            share: a.sessionShare.map { share($0, staleAfter: feed.rules.staleAfterS, time: time) })
    }

    /// "This session: 4% of the week · last: 11%"; hover: the 7-session median and the list (R-88, R-89).
    static func share(_ s: Feed.SessionShare, staleAfter: TimeInterval, time: DisplayTime) -> ShareLineModel {
        func pct(_ v: Double?) -> String { v.map { "\(Int($0.rounded()))%" } ?? "—" }
        let now = s.current.map { "\(pct($0)) of the week" } ?? "—"
        let text = "This session: \(now) · last: \(pct(s.last))"
        var hover = ["Median of the last 7 sessions: \(pct(s.median7))"]
        hover += s.list.map { "\(time.clock($0.start))  \(pct($0.pct))" }
        let fresh = freshness(readAt: s.asOf, error: nil, resetsAt: nil, now: time.now, staleAfter: staleAfter)
        if let why = time.staleHover(fresh, readAt: s.asOf) { hover.append(why) }
        return ShareLineModel(text: text, hover: hover.joined(separator: "\n"), stale: fresh.isStale)
    }
}

struct AccountBlock: View {
    let model: AccountBlockModel
    let palette: Palette

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.lineGap) {
            HStack(spacing: 6) {
                Text(model.name).font(Layout.body.weight(.semibold)).foregroundColor(palette.ink)
                    .lineLimit(1).truncationMode(.middle)
                    .help(model.headerHover)
                Spacer(minLength: 6)
                ForEach(model.flagWords, id: \.self) { w in
                    Text(w).font(Layout.small).foregroundColor(palette.state(.amber)).help(model.headerHover)
                }
                Text(model.resetText).font(Layout.small.monospacedDigit()).foregroundColor(palette.muted)
                    .lineLimit(1).fixedSize()
            }
            ForEach(model.meters) { MeterRow(model: $0, palette: palette) }
            ForEach(model.missingLines, id: \.self) { line in
                Text(line).font(Layout.small).foregroundColor(palette.muted)
            }
            if let share = model.share {
                Text(share.text).font(Layout.small).foregroundColor(share.stale ? palette.grey : palette.muted)
                    .help(share.hover)
            }
        }
    }
}
