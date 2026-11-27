import Foundation

/// S8: which notifications are due (R-90, R-91, R-92). Pure: feed + settings + the saved latch in, notes + the new
/// latch out. The notifier (S14) posts the notes; the caller saves the latch into settings.
///
/// Rules:
/// - Kinds: crossing 80 % used, crossing the red line (`rules.red_at`, 97), "runs out before the reset", and reset.
/// - Once per account + meter + cycle. A reset time that moves by less than max(half the period, 5 min) is the
///   same cycle. A condition that stops holding re-arms its latch.
/// - A meter whose reading is stale, or a status-line reading with a failed identity check, never fires; hidden
///   accounts are silent.
/// - A crossing that happened while the app was off fires once on the first feed. When both 80 and the red line
///   are crossed at once (a jump, or the first feed), only the red-line note fires and 80 is latched silently.
/// - A reset note fires when a new cycle starts for a meter that had fired anything in the previous cycle.
struct Note: Equatable {
    let id: String            // "<folderKey>-<meterId>-<kind>": a repeat replaces the earlier one
    let kind: NotifyKind
    let folderKey: String
    let meterId: String
    let title: String
    let body: String
}

typealias Latch = [LatchKey: LatchState]

enum NotifyLatch {
    static let warnAt = 80.0
    static let minSameCycle: TimeInterval = 300
    private static let identityKinds: Set<String> = ["identity_changed", "identity_foreign_reset"]

    static func evaluate(feed: Feed, settings: AppSettings, latch: Latch, now: Date,
                         hour12: Bool = Fmt.systemHour12, timeZone: TimeZone = .current) -> ([Note], Latch) {
        var next = latch
        var notes: [Note] = []

        for a in feed.accounts.sorted(by: { $0.number < $1.number }) where settings.isAccountShown(a.key) {
            let rejected = identityKinds.contains(a.error?.kind ?? "")
            for m in a.meters {
                if feed.freshness(of: m, in: a, now: now).isStale { continue }
                if rejected && m.source == "statusline" { continue }

                let tolerance = max(m.periodS / 2, minSameCycle)
                func key(_ k: NotifyKind) -> LatchKey { LatchKey(folderKey: a.key, meterId: m.id, kind: k) }

                // New cycle? Compare with the cycle the latch last saw.
                let known = NotifyKind.allCases.compactMap { next[key($0)] }
                let newCycle = known.first.map { abs(m.resetsAt.timeIntervalSince($0.cycleResetsAt)) >= tolerance } ?? false
                var firedBefore = false
                if newCycle {
                    firedBefore = known.contains { $0.firedThisCycle }
                    for k in NotifyKind.allCases { next[key(k)] = nil }
                }

                func state(_ k: NotifyKind) -> LatchState {
                    next[key(k)] ?? LatchState(cycleResetsAt: m.resetsAt, fired: false, firedThisCycle: false)
                }
                func note(_ k: NotifyKind, _ title: String, _ body: String) {
                    notes.append(Note(id: "\(a.key)-\(m.id)-\(k.rawValue)", kind: k, folderKey: a.key, meterId: m.id,
                                      title: title, body: body))
                }
                let resets = Fmt.clock(m.resetsAt, now: now, hour12: hour12, timeZone: timeZone)
                let pct = TitleModel.pctText(m.pct)

                // Conditions now.
                let red = m.pct >= feed.rules.redAt
                let warn = m.pct >= warnAt
                let runsOut = m.pace.state != nil && m.pace.runsOut != nil

                // Re-arm anything that recovered; a held condition keeps its latch.
                func settle(_ k: NotifyKind, holds: Bool) {
                    var s = state(k)
                    s.cycleResetsAt = m.resetsAt
                    if !holds { s.fired = false }
                    else if !s.fired { s.fired = true; s.firedThisCycle = true }
                    next[key(k)] = s
                }
                let warnWasFired = state(.warn80).fired
                let redWasFired = state(.critical).fired
                let runsWasFired = state(.runsOut).fired

                if red && !redWasFired {
                    note(.critical, "\(m.name) is at \(pct)", "\(a.email): wrap up your sessions and save state. Resets \(resets).")
                }
                if warn && !red && !warnWasFired {
                    note(.warn80, "\(m.name) is at \(pct)", "\(a.email): resets \(resets).")
                }
                if runsOut && !runsWasFired, let at = m.pace.runsOut {
                    note(.runsOut, "\(m.name) will run out before the reset",
                         "\(a.email): at this pace it runs out \(Fmt.clock(at, now: now, hour12: hour12, timeZone: timeZone)); it resets \(resets).")
                }
                settle(.critical, holds: red)
                settle(.warn80, holds: warn)   // latched silently when the red line already spoke
                settle(.runsOut, holds: runsOut)
                if newCycle && firedBefore {
                    note(.reset, "\(m.name) has reset", "\(a.email): back to \(pct). Next reset \(resets).")
                }
                var rs = state(.reset)
                rs.cycleResetsAt = m.resetsAt
                next[key(.reset)] = rs
            }
        }
        return (notes, next)
    }
}
