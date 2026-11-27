import SwiftUI

// BOARD TALK: the house voice for hover tips and one-line help (BOARD-TALK.md beside this file).
// Named for the scribble strip on a console: a few words, in the user's own language, readable at a glance.
// A tip is a title (the control's legend) and ONE short body: a phrase, 60 characters at most.
// The rules are enforced, not hoped for: `HouseTips.problems` runs in the tests and again at every launch.
// Lifted from Beat Key (app/Sources/Orion/Help.swift HelpTip / HelpText.problems, docs/BOARD-TALK.md).

struct HouseTip: Equatable, Identifiable {
    let title: String
    let body: String
    var id: String { title }

    /// The longest body Board Talk allows.
    static let maxBody = 60

    init(_ title: String, _ body: String) { self.title = title; self.body = body }
}

enum HouseTips {
    /// Words Board Talk never uses (rule 5): they explain or sell instead of saying what the click does.
    static let banned = ["please", "you can", "allows you to", "lets you", "simply", "easily", "in order to", "this will"]

    /// Tips that break the rules: empty, over 60 characters, a banned phrase, a title used twice.
    static func problems(_ tips: [HouseTip]) -> [String] {
        var out: [String] = []
        var seen = Set<String>()
        for t in tips {
            let body = t.body.trimmingCharacters(in: .whitespaces)
            if t.title.isEmpty || body.isEmpty { out.append("empty: \(t.title)") }
            if t.body.count > HouseTip.maxBody { out.append("too long (\(t.body.count)): \(t.title)") }
            // whole words only: punctuation becomes a space, so "Please." is caught and "Displeased" is not
            let lower = " " + String(t.body.lowercased().map { $0.isLetter || $0.isNumber ? $0 : " " }) + " "
            for word in banned where lower.contains(" " + word + " ") {
                out.append("uses \"\(word)\": \(t.title)")
            }
            if !seen.insert(t.id).inserted { out.append("title used twice: \(t.title)") }
        }
        return out
    }

    /// Call once at launch with the app's table: any tip that breaks the rules is in the log.
    static func checkAtLaunch(_ tips: [HouseTip] = HouseTips.all) {
        for p in problems(tips) { AppLog.shared.error("tips", p) }
    }
}

extension View {
    /// The hover tip: the body only (the title is already printed on the control). VoiceOver reads it as the hint.
    func tip(_ t: HouseTip) -> some View {
        help(t.body).accessibilityHint(Text(t.body))
    }
}
