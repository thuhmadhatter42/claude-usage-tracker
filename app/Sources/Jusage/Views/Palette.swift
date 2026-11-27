import SwiftUI

/// The theme's colours as SwiftUI colours (R-47). A named theme supplies hex values for every key; System has
/// none and follows the Mac's appearance with system colours. The accent never colours a meter (R-159): the host
/// sets it as the panel's tint, and only controls read it.
struct Palette {
    let theme: Theme

    init(theme: Theme) { self.theme = theme }
    init(settings: AppSettings) { theme = Theme.resolve(id: settings.theme) }

    /// The colour for a theme key (`ok`, `warn`, `hot`, `ink`, `ink2`, `mut`, `line`, `sf`, `bg`, ...).
    func colour(_ key: String) -> Color {
        if theme.scheme != nil, let rgb = theme.rgb(key) {
            return Color(.sRGB, red: rgb.r, green: rgb.g, blue: rgb.b, opacity: 1)
        }
        return Palette.system[key] ?? .primary
    }

    private static let system: [String: Color] = [
        "ok": Color(nsColor: .systemGreen), "warn": Color(nsColor: .systemOrange), "hot": Color(nsColor: .systemRed),
        "ink": .primary, "ink2": .secondary, "mut": Color(nsColor: .tertiaryLabelColor),
        "line": Color(nsColor: .separatorColor), "bg": Color(nsColor: .windowBackgroundColor),
        "sf": Color(nsColor: .controlBackgroundColor), "sf2": Color(nsColor: .underPageBackgroundColor),
        "accent": .accentColor,
    ]

    func state(_ s: PaceState) -> Color {
        switch s { case .green: return colour("ok"); case .amber: return colour("warn"); case .red: return colour("hot") }
    }

    /// A meter's fill: its state colour, or the stale grey.
    func fill(_ tone: FillTone) -> Color {
        switch tone { case .state(let s): return state(s); case .grey: return colour("mut") }
    }

    var ink: Color { colour("ink") }
    var muted: Color { colour("ink2") }
    var grey: Color { colour("mut") }
    var track: Color { colour("line") }
    /// The white line at time gone: theme ink at 70 %, never state-coloured (R-47).
    var paceLine: Color { colour("ink").opacity(0.7) }
}
