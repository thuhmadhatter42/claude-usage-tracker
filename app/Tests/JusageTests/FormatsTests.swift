import XCTest
@testable import Jusage

final class FormatsTests: XCTestCase {
    func testFixtureTable() throws {
        let table = try JSONSerialization.jsonObject(with: Fixture.data("tests/fixtures/formats.json")) as! [String: Any]

        for row in table["tokens"] as! [[String: Any]] {
            XCTAssertEqual(Fmt.tokens((row["n"] as! NSNumber).intValue), row["text"] as? String, "tokens \(row)")
        }
        for row in table["usd"] as! [[String: Any]] {
            XCTAssertEqual(Fmt.usd((row["usd"] as! NSNumber).doubleValue), row["text"] as? String, "usd \(row)")
        }
        for row in table["countdown"] as! [[String: Any]] {
            XCTAssertEqual(Fmt.countdown((row["seconds"] as! NSNumber).doubleValue), row["text"] as? String, "countdown \(row)")
        }
        let clocks = table["clock"] as! [[String: Any]]
        XCTAssertFalse(clocks.isEmpty)
        for row in clocks {
            let tz = TimeZone(secondsFromGMT: (row["tz_offset_min"] as! NSNumber).intValue * 60)!
            let text = Fmt.clock(Fixture.date(row["at"] as! String), now: Fixture.date(row["now"] as! String),
                                 hour12: row["hour12"] as! Bool, timeZone: tz)
            XCTAssertEqual(text, row["text"] as? String, "clock \(row)")
        }
    }

    func testClockFollowsTheMacsSetting() {
        let tz = TimeZone(secondsFromGMT: -8 * 3600)!
        let at = Fixture.date("2026-11-27T14:05:00-08:00")
        XCTAssertEqual(Fmt.clock(at, now: Fixture.now, hour12: true, timeZone: tz), "Fri 2:05 PM")
        XCTAssertEqual(Fmt.clock(at, now: Fixture.now, hour12: false, timeZone: tz), "Fri 14:05")
        // With no explicit choice it reads the system setting.
        XCTAssertEqual(Fmt.clock(at, now: Fixture.now, timeZone: tz),
                       Fmt.clock(at, now: Fixture.now, hour12: Fmt.systemHour12, timeZone: tz))
    }

    func testResetTextNeverSaysZeroHours() throws {
        let f = try Fixture.feed()
        let m = f.accounts[0].meters[0]    // resets 00:10 next day
        XCTAssertEqual(Fmt.resetText(meter: m, now: Fixture.now), "2h 30m")
        let after = m.resetsAt!.addingTimeInterval(5)
        XCTAssertEqual(Fmt.resetText(meter: m, now: after), "Reset due, waiting for a fresh reading")
    }

    func testAgeWording() {
        XCTAssertEqual(Fmt.age(40), "40 s ago")             // RULINGS 48g: seconds below a minute (J's example)
        XCTAssertEqual(Fmt.age(0), "0 s ago")
        XCTAssertEqual(Fmt.age(59.9), "59 s ago")
        XCTAssertEqual(Fmt.age(60), "1 min ago")
        XCTAssertEqual(Fmt.age(23 * 60), "23 min ago")
        XCTAssertEqual(Fmt.age(7500), "2h 05m ago")
    }

    func testWordsFillClockPlaceholders() {
        let at = Fixture.date("2026-11-27T14:05:00-08:00")
        let tz = TimeZone(secondsFromGMT: -8 * 3600)!
        let out = Fmt.words("Hit the limit {runs_out}; resets {resets}.", runsOut: at, resetsAt: at, now: Fixture.now,
                            hour12: true, timeZone: tz)
        XCTAssertEqual(out, "Hit the limit Fri 2:05 PM; resets Fri 2:05 PM.")
        XCTAssertEqual(Fmt.words("No tokens here.", runsOut: nil, resetsAt: nil, now: Fixture.now), "No tokens here.")
    }
}
