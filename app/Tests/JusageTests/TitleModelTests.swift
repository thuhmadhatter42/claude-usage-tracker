import XCTest
@testable import Jusage

final class TitleModelTests: XCTestCase {
    let now = Fixture.now
    let tz = TimeZone(secondsFromGMT: -8 * 3600)!

    private func make(_ feed: Feed?, _ s: AppSettings = AppSettings(), runError: String? = nil, at: Date? = nil) -> [TitleItem] {
        TitleModel.make(feed: feed, settings: s, now: at ?? now, runError: runError, hour12: false, timeZone: tz)
    }

    /// Bend one meter of account `i` (by index in the JSON).
    private func feed(accounts edit: @escaping (inout [[String: Any]]) -> Void) throws -> Feed {
        try Fixture.feed { j in
            var a = j["accounts"] as! [[String: Any]]
            edit(&a)
            j["accounts"] = a
        }
    }

    func testOrder1Then2WhateverEmails() throws {
        // JSON order reversed and emails that sort the other way: still #1 then #2.
        let f = try feed { a in
            a.reverse()
            a[0]["email"] = "aaa@example.com"      // number 2
            a[1]["email"] = "zzz@example.com"      // number 1
        }
        let items = make(f)
        XCTAssertEqual(items.map(\.text), ["37%", "98%"])
        XCTAssertTrue(items[0].hover.hasPrefix("zzz@example.com"))
    }

    func testTagsOnOff() throws {
        let f = try Fixture.feed()
        XCTAssertEqual(make(f).map(\.tag), [nil, nil], "off by default")
        var s = AppSettings()
        s.menuBarLabels = true
        XCTAssertEqual(make(f, s).map(\.tag), ["1", "2"])
        s.setTag("Work", for: "/abs/.claude-newaccount")
        XCTAssertEqual(make(f, s).map(\.tag), ["1", "Work"])
    }

    func testPerNumberGrey() throws {
        // Account 1 read 21:39 (fresh); account 2 read 21:12 (28 min old).
        let items = make(try Fixture.feed())
        XCTAssertEqual(items.map(\.stale), [false, true])
        XCTAssertEqual(items[0].colour, .green)
        XCTAssertNil(items[1].colour, "a grey number carries no pace colour")
        XCTAssertTrue(items[1].hover.contains("Updated 28 min ago (21:12). Anthropic is rate-limiting usage reads."))
    }

    func testResetPassedGreys() throws {
        let f = try Fixture.feed()
        let after = Fixture.date("2026-11-27T00:11:00-08:00")      // account 1 session reset 00:10
        XCTAssertTrue(make(f, at: after)[0].stale)
    }

    func testWorstMeterOption() throws {
        let f = try Fixture.feed()
        var s = AppSettings()
        XCTAssertEqual(make(f, s)[0].colour, .green, "shown meter (session) is green")
        s.colourByWorstMeter = true
        XCTAssertEqual(make(f, s)[0].colour, .amber, "the weekly meter is amber")
        XCTAssertEqual(make(f, s)[0].text, "37%", "the text still shows the chosen meter")
    }

    func testWorstMeterIgnoresStaleMeters() throws {
        // Weekly goes stale (old read_at) while session stays fresh: the amber weekly no longer counts.
        let f = try feed { a in
            var meters = a[0]["meters"] as! [[String: Any]]
            meters[1]["read_at"] = "2026-11-26T21:00:00-08:00"
            a[0]["meters"] = meters
        }
        var s = AppSettings()
        s.colourByWorstMeter = true
        XCTAssertEqual(make(f, s)[0].colour, .green)
    }

    func testMissingMeterDash() throws {
        var s = AppSettings()
        s.menuBarMode = .meter("weekly:fable")
        let items = make(try Fixture.feed(), s)
        XCTAssertEqual(items.map(\.text), ["18%", "—"], "account 2 has no Fable meter: a dash, not another meter")
        XCTAssertFalse(items[1].stale)
        XCTAssertNil(items[1].colour)
    }

    func testFirstRunFailureShowsWarning() {
        let waiting = make(nil)
        XCTAssertEqual(waiting.map(\.text), ["…"])
        let failed = make(nil, runError: "Engine refused: VERSION missing")
        XCTAssertEqual(failed.map(\.text), [TitleModel.warning])
        XCTAssertEqual(failed[0].hover, "Engine refused: VERSION missing")
    }

    func testHiddenAccountLeavesTheTitle() throws {
        var s = AppSettings()
        s.hiddenAccounts = ["/abs/.claude"]
        XCTAssertEqual(make(try Fixture.feed(), s).map(\.text), ["98%"])
    }

    func testTokensTodayMode() throws {
        var s = AppSettings()
        s.menuBarMode = .tokensToday
        let items = make(try Fixture.feed(), s)
        XCTAssertEqual(items.map(\.text), ["18.4M"])
        XCTAssertFalse(items[0].stale)
        let late = make(try Fixture.feed(), s, at: now.addingTimeInterval(20 * 60))
        XCTAssertTrue(late[0].stale)
    }
}
