import Foundation

/// S3: the staleness clock (R-48..R-50). Pure: the display clock is the `now` argument, so a number greys on
/// the app's 30 s tick even if the engine never answers. The 15 minutes come from the feed (`rules.stale_after_s`).
enum Freshness: Equatable {
    case fresh
    case stale(age: TimeInterval, reason: String)

    var isStale: Bool { if case .stale = self { return true }; return false }

    /// "Updated 23 min ago (14:05). <reason>" (R-49); nil when fresh.
    func hover(readAt: Date?, now: Date, hour12: Bool = Fmt.systemHour12, timeZone: TimeZone = .current) -> String? {
        guard case .stale(let age, let reason) = self else { return nil }
        guard let readAt, age.isFinite else { return reason }
        return "Updated \(Fmt.age(age)) (\(Fmt.clock(readAt, now: now, hour12: hour12, timeZone: timeZone))). \(reason)"
    }
}

enum Staleness {
    static let noReading = "No fresh reading since then."
    static let neverRead = "No reading yet."

    /// Plain text for each engine error kind (the closed set of SCORE section 1.6.2).
    static let reasons: [String: String] = [
        "login_expired": "The Claude Code login has expired.",
        "login_missing": "No Claude Code login was found.",
        "keychain_waiting": "Waiting for Keychain permission.",
        "keychain_denied": "Keychain access was denied.",
        "rate_limited": "Anthropic is rate-limiting usage reads.",
        "no_network": "No network connection.",
        "http": "Anthropic returned an error.",
        "reply_changed": "Anthropic changed its usage reply.",
        "identity_changed": "The login now belongs to a different account.",
        "identity_foreign_reset": "The reading matches another account's reset.",
    ]
}

/// Stale at age >= `staleAfter` (900 s: 899 fresh, 900 stale), or once the reset has passed, or with no reading.
/// `error` is the engine error kind (or its text); the reason comes from it.
func freshness(readAt: Date?, error: String?, resetsAt: Date?, now: Date, staleAfter: TimeInterval) -> Freshness {
    if let resetsAt, resetsAt <= now {
        return .stale(age: readAt.map { now.timeIntervalSince($0) } ?? .infinity, reason: Fmt.resetDueText)
    }
    guard let readAt else { return .stale(age: .infinity, reason: Staleness.neverRead) }
    let age = now.timeIntervalSince(readAt)
    guard age >= staleAfter else { return .fresh }
    let reason = error.map { Staleness.reasons[$0] ?? $0 } ?? Staleness.noReading
    return .stale(age: age, reason: reason)
}

extension Feed {
    /// Freshness of one meter on the display clock.
    func freshness(of meter: Meter, in account: Account, now: Date) -> Freshness {
        Jusage.freshness(readAt: meter.readAt, error: account.error?.kind, resetsAt: meter.resetsAt,
                         now: now, staleAfter: rules.staleAfterS)
    }
}
