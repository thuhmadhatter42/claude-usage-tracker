// THE APP'S TIPS, all in one place to edit and test. The per-app file of the Board Talk module: one line per
// control that needs a tip. Title = the control's legend, as printed. Body = what a click does, 3-8 words,
// 60 characters at most, the shortcut last in parentheses and read from its Quick Keys row (never typed twice).
extension HouseTips {
    static let refresh = HouseTip("REFRESH", "Read usage from Anthropic now.")
    static let dashboard = HouseTip("DASHBOARD", "Open the full usage page.")
    static let hoverBar = HouseTip("BARS", "Hover a bar to see what each part means.")
    static let report = HouseTip("REPORT", "Send a report, log attached.")
    static let accent = HouseTip("COLOR", "Pick the accent colour.")
    static let highContrast = HouseTip("HIGH CONTRAST", "Brighter text, no glow.")

    /// Every tip above, for the launch check and the test. A tip left out of this list is not checked.
    static let all: [HouseTip] = [refresh, dashboard, hoverBar, report, accent, highContrast]
}
