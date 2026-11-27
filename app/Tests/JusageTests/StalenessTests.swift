import XCTest
@testable import Jusage

final class StalenessTests: XCTestCase {
    let now = Fixture.now
    func ago(_ s: TimeInterval) -> Date { now.addingTimeInterval(-s) }
    let future = Fixture.date("2026-11-27T00:10:00-08:00")

    func test899Fresh900Stale() {
        XCTAssertEqual(freshness(readAt: ago(899), error: nil, resetsAt: future, now: now, staleAfter: 900), .fresh)
        let f = freshness(readAt: ago(900), error: nil, resetsAt: future, now: now, staleAfter: 900)
        XCTAssertEqual(f, .stale(age: 900, reason: Staleness.noReading))
    }

    func testReasonComesFromErrorKind() {
        let f = freshness(readAt: ago(1500), error: "rate_limited", resetsAt: future, now: now, staleAfter: 900)
        XCTAssertEqual(f, .stale(age: 1500, reason: "Anthropic is rate-limiting usage reads."))
        XCTAssertEqual(freshness(readAt: ago(10), error: "rate_limited", resetsAt: future, now: now, staleAfter: 900), .fresh,
                       "an error alone does not grey a fresh reading")
    }

    func testEveryEngineErrorKindHasText() {
        for kind in ["login_expired", "login_missing", "keychain_waiting", "keychain_denied", "rate_limited",
                     "no_network", "http", "reply_changed", "identity_changed", "identity_foreign_reset"] {
            XCTAssertNotNil(Staleness.reasons[kind], kind)
        }
    }

    func testResetPassedIsStale() {
        let f = freshness(readAt: ago(10), error: nil, resetsAt: ago(1), now: now, staleAfter: 900)
        XCTAssertEqual(f, .stale(age: 10, reason: "Reset due, waiting for a fresh reading"))
    }

    func testNoReadingIsStale() {
        XCTAssertTrue(freshness(readAt: nil, error: nil, resetsAt: nil, now: now, staleAfter: 900).isStale)
    }

    func testHoverNamesAgeClockAndReason() {
        let tz = TimeZone(secondsFromGMT: -8 * 3600)!
        let read = now.addingTimeInterval(-23 * 60)
        let f = freshness(readAt: read, error: "no_network", resetsAt: future, now: now, staleAfter: 900)
        XCTAssertEqual(f.hover(readAt: read, now: now, hour12: false, timeZone: tz), "Updated 23 min ago (21:17). No network connection.")
        XCTAssertNil(Freshness.fresh.hover(readAt: read, now: now))
    }

    /// R-50: a number greys with no engine run at all. Only the display clock moves.
    func testGreysWithNoRun() throws {
        let feed = try Fixture.feed()
        let a = feed.accounts[0], m = a.meters[0]            // read 21:39
        let settings = AppSettings()
        XCTAssertFalse(feed.freshness(of: m, in: a, now: Fixture.now).isStale)
        let at14 = Fixture.date("2026-11-26T21:39:00-08:00").addingTimeInterval(14 * 60 + 59)
        let at15 = Fixture.date("2026-11-26T21:39:00-08:00").addingTimeInterval(15 * 60)
        XCTAssertFalse(feed.freshness(of: m, in: a, now: at14).isStale)
        XCTAssertTrue(feed.freshness(of: m, in: a, now: at15).isStale)
        let title = TitleModel.make(feed: feed, settings: settings, now: at15)
        XCTAssertTrue(title[0].stale)
        XCTAssertNil(title[0].colour)
    }

    @MainActor func testTickAdvancesTheClockOnly() throws {
        let model = AppModel(now: Fixture.now)
        model.apply(.ok(try Fixture.data("tests/fixtures/feed-v2.json")), now: Fixture.now)
        let later = Fixture.now.addingTimeInterval(1800)
        model.reevaluateStaleness(now: later)
        XCTAssertEqual(model.clock, later)
        XCTAssertEqual(model.lastSuccessAt, Fixture.now, "no new run was made")
    }
}
