import SwiftUI

/// S18: one plain line per failure (R-78, R-153): the last run's failure, each shown account's engine error by
/// kind, and a shared report that did not write. Full detail in hover; the engine and the host log it.
struct Banner: Identifiable, Equatable {
    let id: String
    let text: String
    let hover: String
}

enum BannerText {
    /// "<label>: <plain sentence>", with "until <clock>" when the engine gave a time. The sentences are the
    /// staleness reasons (S3), so a banner and a grey number's hover say the same thing.
    static func line(kind: String, until: Date?, label: String, time: DisplayTime) -> String {
        var sentence = Staleness.reasons[kind] ?? kind
        if let until {
            if sentence.hasSuffix(".") { sentence.removeLast() }
            sentence += " until \(time.clock(until))."
        }
        return "\(label): \(sentence)"
    }

    static let engineTextKinds: Set<String> = ["identity_foreign_reset"]

    static let shareFailed = "Shared report not updated."

    static func banners(feed: Feed?, runError: String?, settings: AppSettings, time: DisplayTime) -> [Banner] {
        var out: [Banner] = []
        if let runError { out.append(Banner(id: "run", text: runError, hover: runError)) }
        guard let feed else { return out }
        for a in TodayModel.accounts(feed: feed, settings: settings) {
            guard let e = a.error else { continue }
            let label = a.label
            // The engine's own text carries the specifics for kinds that name something (the other account's
            // label for identity_foreign_reset); the fixed sentence would drop it.
            let text = Staleness.reasons[e.kind] == nil || Self.engineTextKinds.contains(e.kind)
                ? "\(label): \(e.text)"
                : line(kind: e.kind, until: e.until, label: label, time: time)
            out.append(Banner(id: a.key, text: text, hover: e.text))
        }
        if let shareError = feed.share.error {
            out.append(Banner(id: "share", text: shareFailed, hover: shareError))
        }
        return out
    }

    /// Error kinds outside the closed set (SCORE 1.6.2): the engine is newer than this app. Logged once per feed.
    static func unknownKinds(in feed: Feed) -> [String] {
        feed.accounts.compactMap { $0.error?.kind }.filter { Staleness.reasons[$0] == nil }
    }
}

struct ErrorBanner: View {
    let banner: Banner
    let palette: Palette

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(TitleModel.warning).foregroundColor(palette.state(.amber))
            Text(banner.text).foregroundColor(palette.ink).lineLimit(2).fixedSize(horizontal: false, vertical: true)
        }
        .font(Layout.small)
        .frame(maxWidth: .infinity, alignment: .leading)
        .help(banner.hover)
    }
}
