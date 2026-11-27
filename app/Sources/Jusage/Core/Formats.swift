import Foundation

/// S2: number and time formats (R-112, R-205). One fixture table (`tests/fixtures/formats.json`) judges this,
/// the engine's formatter and the dashboard's. Nothing here depends on the locale except the Mac's 12/24-hour
/// setting, which `clock` reads through `systemHour12`.
enum Fmt {
    /// 1.23B / 4.5M / 450k / 12.
    static func tokens(_ n: Int) -> String {
        if n < 1000 { return String(n) }
        let k = (Double(n) / 1000).rounded()
        if k < 1000 { return "\(Int(k))k" }
        let m = (Double(n) / 1_000_000 * 10).rounded() / 10
        if m < 1000 { return String(format: "%.1fM", m) }
        return String(format: "%.2fB", Double(n) / 1_000_000_000)
    }

    /// At $100 and up no cents, with a thousands separator; below, two decimals.
    static func usd(_ v: Double) -> String {
        let cents = (v * 100).rounded() / 100
        if cents < 100 { return String(format: "$%.2f", cents) }
        let whole = String(Int(v.rounded()))
        var out = ""
        for (i, ch) in whole.reversed().enumerated() {
            if i > 0 && i % 3 == 0 { out.append(",") }
            out.append(ch)
        }
        return "$" + String(out.reversed())
    }

    /// Whole minutes, floored: "42m", "2h 05m", "5d 21h"; zero or less is "due".
    static func countdown(_ seconds: TimeInterval) -> String {
        if seconds <= 0 { return "due" }
        let s = Int(seconds)
        if s < 3600 { return "\(s / 60)m" }
        if s < 86400 { return String(format: "%dh %02dm", s / 3600, (s % 3600) / 60) }
        return "\(s / 86400)d \((s % 86400) / 3600)h"
    }

    /// "40 s ago", "23 min ago", "2h 05m ago", "1d 3h ago" (hover text; seconds below a minute, RULINGS 48g).
    static func age(_ seconds: TimeInterval) -> String {
        let s = max(0, Int(seconds))
        if s < 60 { return "\(s) s ago" }
        if s < 3600 { return "\(s / 60) min ago" }
        return countdown(TimeInterval(s)) + " ago"
    }

    /// The Mac's 12/24-hour setting (the "j" skeleton follows the user's override as well as the locale).
    static var systemHour12: Bool {
        (DateFormatter.dateFormat(fromTemplate: "j", options: 0, locale: Locale.current) ?? "").contains("a")
    }

    private static let weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    /// "14:05" / "2:05 PM" when on the same calendar day as `now`, else "Fri 14:05" / "Fri 2:05 PM".
    static func clock(_ date: Date, now: Date, hour12: Bool = Fmt.systemHour12, timeZone: TimeZone = .current) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.hour, .minute, .weekday], from: date)
        let h = c.hour ?? 0, m = c.minute ?? 0
        let time: String
        if hour12 {
            let h12 = h % 12 == 0 ? 12 : h % 12
            time = String(format: "%d:%02d %@", h12, m, h < 12 ? "AM" : "PM")
        } else {
            time = String(format: "%02d:%02d", h, m)
        }
        if cal.isDate(date, inSameDayAs: now) { return time }
        return "\(weekdays[(c.weekday ?? 1) - 1]) \(time)"
    }

    static let resetDueText = "Reset due, waiting for a fresh reading"

    /// The reset countdown for a meter; once the reset has passed, the R-42 wording, never "0h 00m".
    static func resetText(meter: Feed.Meter, now: Date) -> String {
        let left = meter.resetsAt.timeIntervalSince(now)
        return left <= 0 ? resetDueText : countdown(left)
    }

    /// Fills the engine's `{runs_out}` / `{resets}` placeholders with clock times (RULINGS 4).
    static func words(_ words: String, runsOut: Date?, resetsAt: Date?, now: Date,
                      hour12: Bool = Fmt.systemHour12, timeZone: TimeZone = .current) -> String {
        var out = words
        if let r = runsOut {
            out = out.replacingOccurrences(of: "{runs_out}", with: clock(r, now: now, hour12: hour12, timeZone: timeZone))
        }
        if let r = resetsAt {
            out = out.replacingOccurrences(of: "{resets}", with: clock(r, now: now, hour12: hour12, timeZone: timeZone))
        }
        return out
    }
}
