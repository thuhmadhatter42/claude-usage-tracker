import SwiftUI

/// S19: one meter line (R-71..R-75). The model holds every number and sentence; the view only draws it.
enum FillTone: Equatable {
    case state(PaceState)
    /// Stale, reset passed, or no pace colour from the engine (RULINGS 3, 9): fills grey, shadow hidden.
    case grey
}

struct MeterRowModel: Equatable, Identifiable {
    let id: String
    /// The meter's name in Anthropic's words, only when Labels is on (R-72).
    let label: String?
    /// Fractions of the bar, 0...1.
    let used: Double
    /// The dark fill to the forecast landing; nil = not drawn.
    let shadow: Double?
    /// The white line at time gone; nil once the reset has passed (R-42).
    let line: Double?
    let tone: FillTone
    let pctText: String
    let countdown: String
    /// "Runs out Sat 09:06", amber, own meters only (R-73).
    let runsOut: String?
    let hover: String
    let voiceOver: String
    /// A red % glows (house kit glow, R-160).
    let glows: Bool

    /// Session first, then Weekly, then the per-model weeklies in the feed's order (R-71).
    static func ordered(_ meters: [Feed.Meter]) -> [Feed.Meter] {
        func rank(_ m: Feed.Meter) -> Int { m.id == "session" ? 0 : m.id == "weekly" ? 1 : 2 }
        return meters.enumerated().sorted { (rank($0.element), $0.offset) < (rank($1.element), $1.offset) }.map(\.element)
    }

    static func sourceName(_ source: String) -> String {
        switch source {
        case "service": return "Anthropic"
        case "statusline": return "Claude Code"
        case "last_good": return "the last good reading"
        default: return source
        }
    }

    private static func whole(_ v: Double) -> Int { Int(v.rounded()) }

    static func make(meter m: Feed.Meter, account a: Feed.Account, feed: Feed, settings: AppSettings,
                     time: DisplayTime, own: Bool) -> MeterRowModel {
        let fresh = feed.freshness(of: m, in: a, now: time.now)
        let resetPassed = m.resetsAt <= time.now || m.pace.resetDue
        let tone: FillTone = (fresh.isStale ? nil : m.pace.state).map { .state($0) } ?? .grey
        let coloured = tone != .grey

        let used = min(max(m.pct / 100, 0), 1)
        var shadow: Double? = nil
        if coloured, m.pace.forecast, let landing = m.pace.landing, landing > m.pct { shadow = min(landing, 100) / 100 }
        let line: Double? = resetPassed ? nil : min(max(m.pace.e, 0), 1)

        var runsOut: String? = nil
        if own, coloured, settings.warnRunsOut, let r = m.pace.runsOut, r < m.resetsAt { runsOut = "Runs out \(time.clock(r))" }

        let pct = TitleModel.pctText(m.pct)
        let period = m.id == "session" ? "session" : "week"
        var hover = [m.name]
        if resetPassed {
            hover.append("Reset passed; resetting…")
        } else {
            hover.append(Fmt.words(m.pace.words, runsOut: m.pace.runsOut, resetsAt: m.resetsAt, now: time.now,
                                   hour12: time.hour12, timeZone: time.timeZone))
        }
        hover.append("Bright fill: \(pct) used.")
        if let landing = m.pace.landing, shadow != nil { hover.append("Dark fill: about \(whole(landing))% by the reset at this pace.") }
        if let e = line { hover.append("White line: \(whole(e * 100))% of the \(period) gone.") }
        hover.append(resetPassed ? "\(Fmt.resetDueText)." : "Resets \(time.clock(m.resetsAt)).")
        hover.append("From \(sourceName(m.source)), \(Fmt.age(time.age(of: m.readAt))).")
        switch tone {
        case .grey:
            hover.append(time.staleHover(fresh, readAt: m.readAt) ?? "Grey: no current pace for this reading.")
        case .state(.green):
            hover.append("Green: on or behind the white line.")
        case .state(.amber):
            hover.append("Amber: ahead of the white line (\(pct) used, \(whole(m.pace.e * 100))% of the \(period) gone).")
        case .state(.red):
            hover.append("Red: \(whole(feed.rules.redAt))% or more used. Wrap up sessions and save state.")
        }

        let stateWord: String
        switch tone {
        case .state(let s): stateWord = s.rawValue
        case .grey: stateWord = "grey, not current"
        }
        let resetWord = resetPassed ? "reset due" : "resets \(time.clock(m.resetsAt))"
        let voiceOver = "\(m.name), \(whole(m.pct)) percent used, \(stateWord), \(resetWord)."

        return MeterRowModel(id: m.id, label: settings.labels ? m.name : nil, used: used, shadow: shadow, line: line,
                             tone: tone, pctText: pct, countdown: Fmt.resetText(meter: m, now: time.now),
                             runsOut: runsOut, hover: hover.joined(separator: "\n"), voiceOver: voiceOver,
                             glows: tone == .state(.red))
    }
}

struct MeterRow: View {
    let model: MeterRowModel
    let palette: Palette
    @EnvironmentObject private var services: PanelServices

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                if let label = model.label {
                    Text(label).font(Layout.small).foregroundColor(palette.muted)
                        .lineLimit(1).truncationMode(.tail)
                        .frame(width: Layout.labelColumn, alignment: .leading)
                }
                PaceBar(model: model, palette: palette)
                services.glow(AnyView(
                    Text(model.pctText).font(Layout.pct).foregroundColor(palette.fill(model.tone))
                ), model.glows)
                .frame(width: Layout.pctColumn, alignment: .trailing)
                Text(model.countdown).font(Layout.small.monospacedDigit()).foregroundColor(palette.muted)
                    .lineLimit(1).minimumScaleFactor(0.7)
                    .frame(width: Layout.countdownColumn, alignment: .trailing)
            }
            if let runsOut = model.runsOut {
                Text(runsOut).font(Layout.small).foregroundColor(palette.state(.amber))
            }
        }
        .contentShape(Rectangle())
        .help(model.hover)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(model.voiceOver)
    }
}
