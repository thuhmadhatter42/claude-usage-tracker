import XCTest
@testable import Jusage

final class NotifyLatchTests: XCTestCase {
    let now = Fixture.now
    let tz = TimeZone(secondsFromGMT: -8 * 3600)!

    /// Account 1's weekly meter, fresh, with the given used % and optional runs-out time.
    private func feed(pct: Double, runsOut: String? = nil, resetsAt: String = "2026-11-30T17:00:00-08:00",
                      readAt: String = "2026-11-26T21:39:00-08:00", source: String = "service",
                      errorKind: String? = nil) throws -> Feed {
        try Fixture.feed { j in
            var accounts = j["accounts"] as! [[String: Any]]
            var meters = accounts[0]["meters"] as! [[String: Any]]
            meters[1]["pct"] = pct
            meters[1]["resets_at"] = resetsAt
            meters[1]["read_at"] = readAt
            meters[1]["source"] = source
            var pace = meters[1]["pace"] as! [String: Any]
            pace["runs_out"] = runsOut.map { $0 as Any } ?? NSNull()
            pace["state"] = "green"
            meters[1]["pace"] = pace
            accounts[0]["meters"] = meters
            if let errorKind { accounts[0]["error"] = ["kind": errorKind, "text": "x", "until": NSNull()] }
            j["accounts"] = accounts
        }
    }

    private func eval(_ f: Feed, _ latch: Latch, _ s: AppSettings = AppSettings(), at: Date? = nil) -> ([Note], Latch) {
        NotifyLatch.evaluate(feed: f, settings: s, latch: latch, now: at ?? now, hour12: false, timeZone: tz)
    }

    /// Only account 1's weekly meter matters in these tests: account 2 is stale in the fixture, session/fable stay low.
    func testFiresOncePerCycle() throws {
        var latch = Latch()
        var (notes, l) = eval(try feed(pct: 50), latch)
        XCTAssertTrue(notes.isEmpty)
        latch = l
        (notes, l) = eval(try feed(pct: 81), latch)
        XCTAssertEqual(notes.map(\.kind), [.warn80])
        XCTAssertEqual(notes[0].id, "/abs/.claude-weekly-warn80")
        latch = l
        (notes, l) = eval(try feed(pct: 83), latch)
        XCTAssertTrue(notes.isEmpty, "still the same cycle")
        latch = l
        (notes, l) = eval(try feed(pct: 97.5), latch)
        XCTAssertEqual(notes.map(\.kind), [.critical])
        latch = l
        (notes, _) = eval(try feed(pct: 99), latch)
        XCTAssertTrue(notes.isEmpty)
    }

    func testRearmsOnRecovery() throws {
        var (_, latch) = eval(try feed(pct: 82), Latch())
        var (notes, l) = eval(try feed(pct: 60), latch)
        XCTAssertTrue(notes.isEmpty)
        latch = l
        (notes, l) = eval(try feed(pct: 85), latch)
        XCTAssertEqual(notes.map(\.kind), [.warn80], "fires again after recovering below 80")
    }

    func testRunsOutFiresOnce() throws {
        let at = "2026-11-29T20:26:09-08:00"
        var (notes, latch) = eval(try feed(pct: 52, runsOut: at), Latch())
        XCTAssertEqual(notes.map(\.kind), [.runsOut])
        XCTAssertTrue(notes[0].body.contains("Sun 20:26"))
        (notes, latch) = eval(try feed(pct: 53, runsOut: at), latch)
        XCTAssertTrue(notes.isEmpty)
        (notes, _) = eval(try feed(pct: 54, runsOut: nil), latch)
        XCTAssertTrue(notes.isEmpty, "recovery is silent")
    }

    func testStaleNeverFires() throws {
        let old = "2026-11-26T21:20:00-08:00"       // 20 min old at 21:40
        let (notes, latch) = eval(try feed(pct: 99, readAt: old), Latch())
        XCTAssertTrue(notes.isEmpty)
        XCTAssertNil(latch[LatchKey(folderKey: "/abs/.claude", meterId: "weekly", kind: .critical)])
    }

    func testStatuslineRejectedNeverFires() throws {
        var (notes, _) = eval(try feed(pct: 99, source: "statusline", errorKind: "identity_changed"), Latch())
        XCTAssertTrue(notes.isEmpty)
        // A statusline reading with no identity problem is fine.
        (notes, _) = eval(try feed(pct: 99, source: "statusline"), Latch())
        XCTAssertEqual(notes.map(\.kind), [.critical])
    }

    func testHiddenSilent() throws {
        var s = AppSettings()
        s.hiddenAccounts = ["/abs/.claude"]
        let (notes, latch) = eval(try feed(pct: 99), Latch(), s)
        XCTAssertTrue(notes.isEmpty)
        XCTAssertTrue(latch.keys.allSatisfy { $0.folderKey != "/abs/.claude" })
    }

    func testCrossedWhileOffFiresOnce() throws {
        // First feed ever, already past 80 and past the red line: one critical note, no 80 note.
        var (notes, latch) = eval(try feed(pct: 98), Latch())
        XCTAssertEqual(notes.map(\.kind), [.critical])
        (notes, latch) = eval(try feed(pct: 98), latch)
        XCTAssertTrue(notes.isEmpty, "and not again")
        // Past 80 only: one 80 note.
        (notes, _) = eval(try feed(pct: 85), Latch())
        XCTAssertEqual(notes.map(\.kind), [.warn80])
    }

    func testJumpOverBothThresholdsFiresOnlyTheRedNote() throws {
        let (_, latch) = eval(try feed(pct: 50), Latch())
        let (notes, l) = eval(try feed(pct: 98), latch)
        XCTAssertEqual(notes.map(\.kind), [.critical])
        XCTAssertEqual(l[LatchKey(folderKey: "/abs/.claude", meterId: "weekly", kind: .warn80)]?.fired, true)
    }

    func testResetJitterSameCycle() throws {
        var (_, latch) = eval(try feed(pct: 85), Latch())
        // The reset time drifts by a minute: same cycle, so no second note and no reset note.
        let (notes, _) = eval(try feed(pct: 86, resetsAt: "2026-11-30T17:01:00-08:00"), latch)
        XCTAssertTrue(notes.isEmpty)
        latch = [:]
    }

    func testNewCycleFiresResetThenWarnsAgain() throws {
        var (notes, latch) = eval(try feed(pct: 90), Latch())
        XCTAssertEqual(notes.map(\.kind), [.warn80])
        // A week later: new reset time, usage back at 4 %.
        let later = Fixture.date("2026-12-01T09:00:00-08:00")
        let next = try feed(pct: 4, resetsAt: "2026-12-07T17:00:00-08:00", readAt: "2026-12-01T08:59:00-08:00")
        (notes, latch) = eval(next, latch, at: later)
        XCTAssertEqual(notes.map(\.kind), [.reset])
        XCTAssertEqual(notes[0].id, "/abs/.claude-weekly-reset")
        // And it can warn in the new cycle.
        let hot = try feed(pct: 82, resetsAt: "2026-12-07T17:00:00-08:00", readAt: "2026-12-02T08:59:00-08:00")
        (notes, _) = eval(hot, latch, at: Fixture.date("2026-12-02T09:00:00-08:00"))
        XCTAssertEqual(notes.map(\.kind), [.warn80])
    }

    func testResetOfAQuietMeterIsSilent() throws {
        var (_, latch) = eval(try feed(pct: 10), Latch())
        let later = Fixture.date("2026-12-01T09:00:00-08:00")
        let (notes, _) = eval(try feed(pct: 2, resetsAt: "2026-12-07T17:00:00-08:00", readAt: "2026-12-01T08:59:00-08:00"), latch, at: later)
        XCTAssertTrue(notes.isEmpty)
        latch = [:]
    }

    func testMeterWithNoResetTimeStillWarnsOnceAndNeverNotesAReset() throws {
        func nilReset(_ pct: Double) throws -> Feed {
            try Fixture.feed { j in
                var accounts = j["accounts"] as! [[String: Any]]
                var meters = accounts[0]["meters"] as! [[String: Any]]
                meters[1]["pct"] = pct
                meters[1]["resets_at"] = NSNull()
                meters[1]["read_at"] = "2026-11-26T21:39:00-08:00"
                var pace = meters[1]["pace"] as! [String: Any]
                pace["runs_out"] = NSNull(); pace["state"] = "green"
                meters[1]["pace"] = pace
                accounts[0]["meters"] = meters
                j["accounts"] = accounts
            }
        }
        var (notes, latch) = eval(try nilReset(81), Latch())
        XCTAssertEqual(notes.map(\.kind), [.warn80])
        XCTAssertFalse(notes[0].body.contains("resets"), notes[0].body)
        (notes, latch) = eval(try nilReset(82), latch)
        XCTAssertTrue(notes.isEmpty, "once per cycle")
        (notes, _) = eval(try nilReset(10), latch)
        XCTAssertTrue(notes.isEmpty, "no cycle key, so no reset note")
    }

    func testCrossingIsRecordedOnlyForNotesThatWereDelivered() throws {
        let start = Latch()
        let (notes, after) = eval(try feed(pct: 98), start)
        XCTAssertEqual(notes.map(\.kind), [.critical])
        // Delivery failed: the latch goes back to what it was for that note, so the next run fires it again.
        let kept = NotifyLatch.committed(old: start, new: after, notes: notes, delivered: [])
        let key = LatchKey(folderKey: notes[0].folderKey, meterId: notes[0].meterId, kind: .critical)
        XCTAssertNil(kept[key])
        XCTAssertEqual(eval(try feed(pct: 98), kept).0.map(\.kind), [.critical])
        // Delivered: recorded, no repeat.
        let done = NotifyLatch.committed(old: start, new: after, notes: notes, delivered: [notes[0].id])
        XCTAssertEqual(done, after)
        XCTAssertTrue(eval(try feed(pct: 98), done).0.isEmpty)
    }
}
