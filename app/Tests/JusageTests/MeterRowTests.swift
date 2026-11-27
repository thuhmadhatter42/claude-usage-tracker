import XCTest
@testable import Jusage

final class MeterRowTests: XCTestCase {
    private func row(_ f: Feed, account i: Int = 0, meter id: String, own: Bool = true,
                     settings: AppSettings = AppSettings(), now: Date = Fixture.now) -> MeterRowModel {
        let a = f.accounts[i]
        let m = a.meters.first { $0.id == id }!
        return MeterRowModel.make(meter: m, account: a, feed: f, settings: settings, time: ViewFixture.time(now), own: own)
    }

    func testFillsFromTheMetersOwnNumbers() throws {
        let r = row(try Fixture.feed(), meter: "session")
        XCTAssertEqual(r.used, 0.37, accuracy: 1e-9)
        XCTAssertEqual(r.shadow!, 0.74, accuracy: 1e-9)
        XCTAssertEqual(r.line!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(r.tone, .state(.green))
        XCTAssertEqual(r.pctText, "37%")
        XCTAssertEqual(r.countdown, "2h 30m")
        XCTAssertNil(r.label, "Labels is off by default")
        XCTAssertEqual(row(try Fixture.feed(), meter: "weekly").shadow!, 1.0, accuracy: 1e-9, "landing capped at 100")
    }

    func testShadowOnlyWhenForecastAboveUsed() throws {
        let below = try ViewFixture.feed(account: 0, meter: 0) { m in
            var p = m["pace"] as! [String: Any]; p["landing"] = 30.0; m["pace"] = p
        }
        XCTAssertNil(row(below, meter: "session").shadow, "landing under used: no shadow")
        let noForecast = try ViewFixture.feed(account: 0, meter: 0) { m in
            var p = m["pace"] as! [String: Any]; p["forecast"] = false; m["pace"] = p
        }
        XCTAssertNil(row(noForecast, meter: "session").shadow)
        let noLanding = try ViewFixture.feed(account: 0, meter: 0) { m in
            var p = m["pace"] as! [String: Any]; p["landing"] = NSNull(); m["pace"] = p
        }
        XCTAssertNil(row(noLanding, meter: "session").shadow)
        XCTAssertNotNil(row(try Fixture.feed(), meter: "session").shadow)
    }

    func testStaleHidesShadowAndColour() throws {
        // Account 2 was read at 21:12: 28 minutes old at 21:40.
        let r = row(try Fixture.feed(), account: 1, meter: "session")
        XCTAssertEqual(r.tone, .grey)
        XCTAssertNil(r.shadow)
        XCTAssertNil(r.runsOut)
        XCTAssertFalse(r.glows, "a grey number never glows red")
        XCTAssertEqual(r.pctText, "98%", "the last value stays visible")
        XCTAssertTrue(r.hover.contains("Updated 28 min ago (21:12). Anthropic is rate-limiting usage reads."), r.hover)
        // The same meter at 21:26 (14 min old) is still coloured.
        let fresh = row(try Fixture.feed(), account: 1, meter: "session", now: Fixture.date("2026-11-26T21:26:59-08:00"))
        XCTAssertEqual(fresh.tone, .state(.red))
        XCTAssertTrue(fresh.glows)
    }

    func testResetPassedHidesLineAndForecast() throws {
        let after = Fixture.date("2026-11-27T00:11:00-08:00")
        let r = row(try Fixture.feed(), meter: "session", now: after)
        XCTAssertEqual(r.tone, .grey)
        XCTAssertNil(r.line)
        XCTAssertNil(r.shadow)
        XCTAssertEqual(r.countdown, Fmt.resetDueText)
        XCTAssertTrue(r.hover.contains("resetting"), r.hover)
    }

    func testRunsOutLineOwnMetersOnlyAndSetting() throws {
        let f = try Fixture.feed()
        XCTAssertEqual(row(f, meter: "weekly").runsOut, "Runs out Sun 20:26")
        XCTAssertNil(row(f, meter: "weekly", own: false).runsOut, "never on other people's meters")
        var s = AppSettings()
        s.warnRunsOut = false
        XCTAssertNil(row(f, meter: "weekly", settings: s).runsOut)
        XCTAssertNil(row(f, meter: "session").runsOut)
        let afterReset = try ViewFixture.feed(account: 0, meter: 1) { m in
            var p = m["pace"] as! [String: Any]; p["runs_out"] = "2026-12-01T00:00:00-08:00"; m["pace"] = p
        }
        XCTAssertNil(row(afterReset, meter: "weekly").runsOut, "runs out after the reset: no line")
    }

    func testLabelsUseAnthropicsWords() throws {
        var s = AppSettings()
        s.labels = true
        XCTAssertEqual(row(try Fixture.feed(), meter: "weekly:fable", settings: s).label, "Weekly · Fable")
    }

    func testHoverNamesSourceAndAge() throws {
        let r = row(try Fixture.feed(), meter: "session")
        XCTAssertTrue(r.hover.hasPrefix("Session\n"), "hover names the meter even with Labels off")
        XCTAssertTrue(r.hover.contains("On pace: about 75% by the reset."))
        XCTAssertTrue(r.hover.contains("Bright fill: 37% used."), r.hover)
        XCTAssertTrue(r.hover.contains("Dark fill: about 74% by the reset at this pace."), r.hover)
        XCTAssertTrue(r.hover.contains("White line: 50% of the session gone."), r.hover)
        XCTAssertTrue(r.hover.contains("Resets Fri 00:10."), r.hover)
        XCTAssertTrue(r.hover.contains("From Anthropic, 1 min ago."), r.hover)
        XCTAssertTrue(r.hover.contains("Green: on or behind the white line."), r.hover)

        let cc = try ViewFixture.feed(account: 0, meter: 0) { m in
            m["source"] = "statusline"; m["read_at"] = "2026-11-26T21:37:00-08:00"
        }
        XCTAssertTrue(row(cc, meter: "session").hover.contains("From Claude Code, 3 min ago."))
        let lastGood = try ViewFixture.feed(account: 0, meter: 0) { m in m["source"] = "last_good" }
        XCTAssertTrue(row(lastGood, meter: "session").hover.contains("From the last good reading, 1 min ago."))

        let w = row(try Fixture.feed(), meter: "weekly")
        XCTAssertTrue(w.hover.contains("At this pace you hit the limit Sun 20:26"), w.hover)
        XCTAssertTrue(w.hover.contains("Amber: ahead of the white line (52% used, 46% of the week gone)."), w.hover)
        let red = row(try Fixture.feed(), account: 1, meter: "session", now: Fixture.date("2026-11-26T21:20:00-08:00"))
        XCTAssertTrue(red.hover.contains("Red: 97% or more used."), red.hover)
    }

    func testRunsOutPlaceholderFilled() throws {
        let f = try ViewFixture.feed(account: 0, meter: 1) { m in
            var p = m["pace"] as! [String: Any]; p["words"] = "At this pace you hit the limit {runs_out}."; m["pace"] = p
        }
        XCTAssertTrue(row(f, meter: "weekly").hover.contains("At this pace you hit the limit Sun 20:26."))
    }

    func testVoiceOverSentence() throws {
        XCTAssertEqual(row(try Fixture.feed(), meter: "session").voiceOver,
                       "Session, 37 percent used, green, resets Fri 00:10.")
        XCTAssertEqual(row(try Fixture.feed(), account: 1, meter: "session").voiceOver,
                       "Session, 98 percent used, grey, not current, resets 23:00.")
    }
}
