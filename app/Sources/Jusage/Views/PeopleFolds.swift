import SwiftUI

/// S20: other people, one fold per person keyed by install id (R-79, R-121). My account toggles hide their
/// copies of my accounts (matched by uuid). A report 15 minutes old greys with "Last report <time> from <host>".
struct PersonFoldModel: Identifiable, Equatable {
    let id: String
    let foldId: FoldId
    let title: String
    let trailing: String
    let stale: Bool
    let staleLine: String?
    let spend: SpendLineModel
    let accounts: [AccountBlockModel]
}

enum PeopleModel {
    static func make(feed: Feed, settings: AppSettings, time: DisplayTime) -> [PersonFoldModel] {
        let hiddenUUIDs = Set(feed.accounts.filter { !settings.isAccountShown($0.key) }.compactMap(\.uuid))
        return feed.people.filter { settings.isPersonShown($0.installId) }.map { p in
            let fresh = freshness(readAt: p.generated, error: nil, resetsAt: nil, now: time.now, staleAfter: feed.rules.staleAfterS)
            let accounts = p.accounts.filter { !($0.uuid.map(hiddenUUIDs.contains) ?? false) }.sorted { $0.number < $1.number }
            return PersonFoldModel(
                id: p.installId, foldId: FoldID.person(p.installId), title: p.user,
                trailing: "\(Fmt.tokens(p.today.tokens ?? 0)) · \(Fmt.usd(p.today.usd ?? 0))",
                stale: fresh.isStale,
                staleLine: fresh.isStale ? "Last report \(time.clock(p.generated)) from \(p.host)" : nil,
                spend: .make(today: p.today, week: p.week, asOf: p.generated, staleAfter: feed.rules.staleAfterS, time: time),
                accounts: accounts.map { .make(account: $0, feed: feed, settings: settings, time: time, own: false) })
        }
    }
}

struct PeopleFolds: View {
    let folds: [PersonFoldModel]
    let settings: AppSettings
    let palette: Palette
    let setFold: (FoldId, Bool) -> Void

    var body: some View {
        ForEach(folds) { p in
            Fold(id: p.foldId, title: p.title, trailing: p.trailing, stale: p.stale,
                 open: settings.isFoldOpen(p.foldId), palette: palette, setFold: setFold) {
                VStack(alignment: .leading, spacing: Layout.blockGap) {
                    SpendLine(model: p.spend, palette: palette)
                    ForEach(p.accounts) { AccountBlock(model: $0, palette: palette) }
                }
            }
            if let line = p.staleLine {
                Text(line).font(Layout.small).foregroundColor(palette.grey).padding(.leading, 14)
            }
        }
    }
}
