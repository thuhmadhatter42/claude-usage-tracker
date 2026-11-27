import SwiftUI

/// S20: "Projects today" (R-81): the top 5 projects by share.
enum ProjectsModel {
    static let title = "Projects today"

    static func rows(feed: Feed) -> [ValueRow] {
        feed.projectsToday.prefix(5).map { ValueRow(id: $0.name, name: $0.name, text: "\(Int(($0.share * 100).rounded()))%") }
    }
}

struct ProjectsFold: View {
    let feed: Feed
    let settings: AppSettings
    let palette: Palette
    let setFold: (FoldId, Bool) -> Void

    var body: some View {
        Fold(id: FoldID.projects, title: ProjectsModel.title, trailing: nil, stale: false,
             open: settings.isFoldOpen(FoldID.projects), palette: palette, setFold: setFold) {
            VStack(alignment: .leading, spacing: Layout.lineGap) {
                ForEach(ProjectsModel.rows(feed: feed)) { FoldRow(title: $0.name, value: $0.text, palette: palette) }
            }
        }
    }
}
