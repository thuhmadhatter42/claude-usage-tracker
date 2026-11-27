import XCTest
@testable import Jusage

/// S10: the one clock (SCORE §1.2, SPEC §5, R-50, R-149, R-152).
@MainActor
final class SchedulerTests: XCTestCase {
    final class FakeTimer: SchedulerTimer {
        private(set) var armedAt: Date?
        private(set) var fire: (() -> Void)?
        private(set) var arms = 0
        func arm(at date: Date, _ fire: @escaping () -> Void) { armedAt = date; self.fire = fire; arms += 1 }
    }

    /// Records each engine run; the test ends a run by calling its `done`.
    final class Runs {
        var forced: [Bool] = []
        var pending: [(Date?) -> Void] = []
        func run(_ force: Bool, _ done: @escaping (Date?) -> Void) { forced.append(force); pending.append(done) }
        func finish(nextDue: Date?) { pending.removeFirst()(nextDue) }
    }

    var now = Date(timeIntervalSince1970: 1_800_000_000)
    var timer: FakeTimer!
    var runs: Runs!
    var ticks: [Date] = []
    var scheduler: Scheduler!

    override func setUp() {
        timer = FakeTimer(); runs = Runs(); ticks = []
        scheduler = Scheduler(now: { [unowned self] in self.now }, timer: timer,
                              run: { [unowned self] f, d in self.runs.run(f, d) },
                              onTick: { [unowned self] t in self.ticks.append(t) })
    }

    func advance(_ s: TimeInterval) { now = now.addingTimeInterval(s) }

    /// Moves the clock to the armed time and fires the timer, as the run loop would.
    func fireTimer() {
        guard let at = timer.armedAt, let f = timer.fire else { return XCTFail("nothing armed") }
        if at > now { now = at }
        f()
    }

    // MARK: SCORE-named tests

    func testOneRunAtATimeManualWins() {
        scheduler.start()
        XCTAssertEqual(runs.forced, [false], "start runs once")
        scheduler.request(.panelOpen)
        scheduler.request(.manual)
        scheduler.request(.wake)
        XCTAssertEqual(runs.forced.count, 1, "requests during a run are merged, never a second run")
        runs.finish(nextDue: now.addingTimeInterval(60))
        XCTAssertEqual(runs.forced, [false, true], "one merged run follows, and the forced request wins")
        runs.finish(nextDue: now.addingTimeInterval(60))
        XCTAssertEqual(runs.forced.count, 2, "nothing else was pending")
        XCTAssertFalse(scheduler.running)
    }

    func testRearmUsesNextDue() {
        scheduler.start()
        runs.finish(nextDue: now.addingTimeInterval(12))
        XCTAssertEqual(timer.armedAt, now.addingTimeInterval(12), "next_due sooner than the 30 s tick wins")
        fireTimer()
        XCTAssertEqual(runs.forced.count, 2, "due time reached: a run starts")
        runs.finish(nextDue: now.addingTimeInterval(100))
        XCTAssertEqual(timer.armedAt, now.addingTimeInterval(30), "the 30 s tick wins over a later next_due")
        fireTimer()
        XCTAssertEqual(runs.forced.count, 2, "a tick before next_due only re-checks staleness")
        XCTAssertEqual(ticks.last, now)
    }

    func testCeiling300() {
        scheduler.start()
        let end = now
        runs.finish(nextDue: nil)                 // a failed run reports no due time
        var fires = 0
        while runs.forced.count == 1 && fires < 20 {
            XCTAssertLessThanOrEqual(timer.armedAt!, end.addingTimeInterval(300))
            fireTimer(); fires += 1
        }
        XCTAssertEqual(runs.forced.count, 2)
        XCTAssertEqual(now, end.addingTimeInterval(300), "the 300 s ceiling starts a run, not before")
        XCTAssertEqual(fires, 10, "ten 30 s ticks")
    }

    func testWakeAfterResetRunsNow() {
        scheduler.start()
        runs.finish(nextDue: now.addingTimeInterval(60))
        advance(9 * 3600)                          // slept 9 h across a weekly reset; the timer did not fire
        scheduler.request(.wake)
        XCTAssertEqual(ticks.last, now, "staleness re-checked at once on wake (grey + Reset due)")
        XCTAssertEqual(runs.forced, [false, false], "the wake run starts now, not forced")
    }

    // MARK: more of S10

    func testEveryTickReevaluatesStaleness() {
        scheduler.start()
        runs.finish(nextDue: now.addingTimeInterval(600))
        let before = ticks.count
        fireTimer(); fireTimer(); fireTimer()
        XCTAssertEqual(ticks.count, before + 3)
        XCTAssertEqual(runs.forced.count, 1, "no engine run on a plain tick")
    }

    func testTicksContinueDuringARun() {
        scheduler.start()
        XCTAssertEqual(timer.armedAt, now.addingTimeInterval(30), "the staleness clock keeps ticking while a run is out")
        fireTimer()
        XCTAssertEqual(runs.forced.count, 1, "a tick never starts a second run")
    }

    func testPastNextDueWaitsForTheTick() {
        scheduler.start()
        runs.finish(nextDue: now.addingTimeInterval(-5))   // the engine says "due already"
        XCTAssertEqual(timer.armedAt, now.addingTimeInterval(30), "never a tight loop: the next chance is the tick")
    }

    func testPanelOpenAndSessionActiveRunUnforced() {
        scheduler.start(); runs.finish(nextDue: now.addingTimeInterval(600))
        scheduler.request(.panelOpen); runs.finish(nextDue: now.addingTimeInterval(600))
        scheduler.request(.sessionActive); runs.finish(nextDue: now.addingTimeInterval(600))
        scheduler.request(.manual)
        XCTAssertEqual(runs.forced, [false, false, false, true])
    }

    func testOneTimerOnly() {
        // Re-arming replaces the single timer; there is never a second one.
        scheduler.start()
        runs.finish(nextDue: now.addingTimeInterval(45))
        let arms = timer.arms
        scheduler.request(.tick)
        XCTAssertEqual(timer.arms, arms + 1)
    }
}
