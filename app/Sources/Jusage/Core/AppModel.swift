import Foundation
import Combine

/// S7: the app's model. Keeps the last good feed on screen when a run fails (R-153); the numbers grey on their
/// own at 15 minutes because every view reads `clock`, which `reevaluateStaleness` advances on the 30 s tick.
enum Tab: String, Equatable { case today, month, settings }

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var lastGood: Feed?
    @Published private(set) var lastSuccessAt: Date?
    @Published private(set) var running = false
    /// One plain line about the last run, nil after a good one.
    @Published private(set) var runError: String?
    @Published var tab: Tab = .today
    @Published var monthIndex = 0
    /// The display clock. Views and the title compute staleness against this, never against the feed's own time.
    @Published private(set) var clock: Date

    init(now: Date = Date()) { clock = now }

    /// What is on screen: the last feed that decoded.
    var feed: Feed? { lastGood }

    func runStarted() { running = true }

    func apply(_ r: RunResult, now: Date) {
        running = false
        clock = now
        switch r {
        case .ok(let data):
            do {
                let f = try Feed.decode(data)
                lastGood = f
                lastSuccessAt = now
                runError = nil
                monthIndex = Self.clamp(monthIndex, count: f.months.count)
            } catch {
                runError = (error as? FeedError)?.errorDescription ?? error.localizedDescription
            }
        case .failed(let line, _):
            runError = line
        case .timedOut:
            runError = FeedRunner.timeoutText
        case .crashed(let signal):
            runError = "The engine stopped unexpectedly (signal \(signal))."
        case .launchFailed(let why):
            runError = "The engine could not start: \(why)"
        }
    }

    /// The 30 s tick: moves the display clock so every number re-checks its age, with no engine call.
    func reevaluateStaleness(now: Date = Date()) { clock = now }

    private static func clamp(_ i: Int, count: Int) -> Int { max(0, min(i, max(count - 1, 0))) }
}
