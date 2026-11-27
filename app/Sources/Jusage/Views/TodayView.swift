import SwiftUI

/// S18: the Today tab: banners, one block per shown account (by number), the spend line, then the folds.
enum TodayModel {
    static let noAccounts = "Run `jusage live` once in Terminal to show Claude's limits here."

    /// Shown accounts in number order (#1, #2, then the rest), never by label (R-53).
    static func accounts(feed: Feed, settings: AppSettings) -> [Feed.Account] {
        feed.accounts.filter { settings.isAccountShown($0.key) }.sorted { $0.number < $1.number }
    }
}

struct TodayView: View {
    let feed: Feed
    let banners: [Banner]
    let settings: AppSettings
    let time: DisplayTime
    let palette: Palette
    let setFold: (FoldId, Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.blockGap) {
            ForEach(banners) { ErrorBanner(banner: $0, palette: palette) }
            let accounts = TodayModel.accounts(feed: feed, settings: settings)
            if feed.accounts.isEmpty {
                Text(TodayModel.noAccounts).font(Layout.small).foregroundColor(palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(accounts, id: \.key) { a in
                AccountBlock(model: .make(account: a, feed: feed, settings: settings, time: time, own: true), palette: palette)
            }
            if settings.isSectionShown(.today) {
                SpendLine(model: .make(today: feed.totals.today, week: feed.totals.week, asOf: feed.totals.asOf,
                                       staleAfter: feed.rules.staleAfterS, time: time), palette: palette)
            }
            if settings.isSectionShown(.people) {
                PeopleFolds(folds: PeopleModel.make(feed: feed, settings: settings, time: time),
                            settings: settings, palette: palette, setFold: setFold)
            }
            if settings.isSectionShown(.models) {
                SplitFold(feed: feed, settings: settings, time: time, palette: palette, setFold: setFold)
            }
            if settings.isSectionShown(.projects) && !feed.projects.isEmpty {
                ProjectsFold(feed: feed, settings: settings, palette: palette, setFold: setFold)
            }
        }
    }
}
