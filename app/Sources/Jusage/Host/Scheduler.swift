import Foundation

/// S10: the app's one clock (SCORE §1.2, SPEC §5). One one-shot timer, re-armed after every fire to
/// `min(now + 30 s, next_due, lastRunEnd + 300 s)`. Each fire re-checks staleness (no engine call) and starts an
/// engine run when `next_due` or the 300 s ceiling has passed. Wake, session-active, panel-open and ⌘R ask for a
/// run through `request`. One run at a time; requests during a run merge into one follow-up run, forced if any
/// of them was ⌘R (R-152).
enum Trigger: Equatable {
    case tick, wake, sessionActive, panelOpen, manual
    /// An engine setting was written: re-read the feed, never force a fetch (only ⌘R forces).
    case settingsChanged
}

/// The single timer. Arming replaces whatever was armed before.
protocol SchedulerTimer: AnyObject {
    func arm(at date: Date, _ fire: @escaping () -> Void)
}

/// The app's timer: one `Timer` in the run loop's `.common` modes (it keeps firing while a menu is open).
final class RunLoopTimer: SchedulerTimer {
    private var timer: Timer?

    func arm(at date: Date, _ fire: @escaping () -> Void) {
        timer?.invalidate()
        let t = Timer(fire: date, interval: 0, repeats: false) { _ in fire() }
        t.tolerance = 1
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }
}

@MainActor
final class Scheduler {
    static let tick: TimeInterval = 30
    static let ceiling: TimeInterval = 300
    /// A wake request this soon after a run started adds nothing.
    static let wakeDebounce: TimeInterval = 5

    /// Starts one engine run; `done(nextDue)` is called on the main thread when it ends, with the feed's
    /// `next_due` after a good run and nil after a failed one (then only the ceiling and the triggers apply).
    typealias Run = (_ force: Bool, _ done: @escaping (Date?) -> Void) -> Void

    private let now: () -> Date
    private let timer: SchedulerTimer
    private let run: Run
    private let onTick: (Date) -> Void

    private(set) var running = false
    private var pending: Bool?            // a merged request during a run; true when any was forced
    private var nextDue: Date?
    private var lastRunEnd: Date?
    private var lastRunStart: Date?

    init(now: @escaping () -> Date = Date.init, timer: SchedulerTimer = RunLoopTimer(),
         run: @escaping Run, onTick: @escaping (Date) -> Void) {
        self.now = now; self.timer = timer; self.run = run; self.onTick = onTick
    }

    /// The first run, at launch.
    func start() { begin(force: false) }

    func request(_ why: Trigger) {
        switch why {
        case .tick:
            fire()
        case .wake, .sessionActive, .panelOpen, .manual, .settingsChanged:
            onTick(now())                 // numbers re-check their age at once (wake after a reset greys now)
            // At wake the overdue timer and the wake notification both ask: one run, not two.
            if why == .wake, let t = lastRunStart, now().timeIntervalSince(t) < Scheduler.wakeDebounce { return }
            begin(force: why == .manual)
        }
    }

    private func fire() {
        let n = now()
        onTick(n)
        if !running && isDue(n) { begin(force: false) } else { rearm() }
    }

    private func isDue(_ n: Date) -> Bool {
        guard let end = lastRunEnd else { return true }
        if let due = nextDue, n >= due { return true }
        return n >= end.addingTimeInterval(Scheduler.ceiling)
    }

    private func begin(force: Bool) {
        if running {
            pending = (pending ?? false) || force
            return
        }
        running = true
        lastRunStart = now()
        rearm()                           // the staleness tick keeps going while the run is out
        run(force) { [weak self] due in self?.finish(nextDue: due) }
    }

    private func finish(nextDue due: Date?) {
        running = false
        lastRunEnd = now()
        nextDue = due
        if let p = pending {
            pending = nil
            begin(force: p)
        } else {
            rearm()
        }
    }

    private func rearm() {
        let n = now()
        var at = n.addingTimeInterval(Scheduler.tick)
        if !running {
            if let due = nextDue, due > n { at = min(at, due) }
            if let end = lastRunEnd { at = min(at, max(n, end.addingTimeInterval(Scheduler.ceiling))) }
        }
        timer.arm(at: at) { [weak self] in
            MainActor.assumeIsolated { self?.fire() }
        }
    }
}
