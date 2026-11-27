import XCTest
@testable import Jusage

final class AccountBlockTests: XCTestCase {
    private func block(_ f: Feed, _ i: Int, settings: AppSettings = AppSettings(), now: Date = Fixture.now) -> AccountBlockModel {
        AccountBlockModel.make(account: f.accounts[i], feed: f, settings: settings, time: ViewFixture.time(now), own: true)
    }

    func testResetOnRightAlways() throws {
        let f = try Fixture.feed()
        XCTAssertEqual(block(f, 0).resetText, "Resets Mon 17:00")
        XCTAssertEqual(block(f, 1).resetText, "Resets Wed 10:00", "a stale account still shows its reset")
        var s = AppSettings()
        s.labels = true
        XCTAssertEqual(block(f, 0, settings: s).resetText, "Resets Mon 17:00", "Labels never moves it")
        let unknown = try ViewFixture.feed { a in a[0]["weekly_reset"] = NSNull() }
        XCTAssertEqual(block(unknown, 0).resetText, "Resets —")
    }

    func testHeaderNameIsLocalPartWithEmailOnHover() throws {
        let b = block(try Fixture.feed(), 0)
        XCTAssertEqual(b.name, "sam")
        XCTAssertTrue(b.headerHover.hasPrefix("sam@example.com"))
        XCTAssertEqual(b.flagWords, ["credits", "reset ready"])
        XCTAssertTrue(b.headerHover.contains("Using credits"))
        XCTAssertTrue(b.headerHover.contains("Limit reset available"))
        XCTAssertTrue(block(try Fixture.feed(), 1).headerHover.contains("legacy Keychain item"), "the warning lives in hover")
    }

    func testMetersInFixedOrder() throws {
        let f = try ViewFixture.feed { a in
            var m = a[0]["meters"] as! [[String: Any]]
            m.reverse()
            a[0]["meters"] = m
        }
        XCTAssertEqual(block(f, 0).meters.map(\.id), ["session", "weekly", "weekly:fable"])
    }

    func testStaleKeepsNumbersGrey() throws {
        let b = block(try Fixture.feed(), 1)
        XCTAssertEqual(b.meters.map(\.pctText), ["98%", "35%"])
        XCTAssertEqual(b.meters.map(\.tone), [.grey, .grey])
        XCTAssertTrue(b.meters.allSatisfy { $0.hover.contains("Updated 28 min ago (21:12).") })
        XCTAssertEqual(block(try Fixture.feed(), 0).meters.map(\.tone), [.state(.green), .state(.amber), .state(.green)])
    }

    func testMissingPeopleLineOnce() throws {
        let f = try ViewFixture.feed { a in a[1]["missing_people"] = ["Lee", "Kim", "Lee"] }
        XCTAssertEqual(block(f, 1).missingLines, ["Lee: not reported yet (update jusage)", "Kim: not reported yet (update jusage)"])
        XCTAssertEqual(block(f, 0).missingLines, [])
    }

    func testSessionShareLine() throws {
        let s = block(try Fixture.feed(), 0).share!
        XCTAssertEqual(s.text, "This session: 4% of the week · last: 11%")
        XCTAssertTrue(s.hover.contains("Median of the last 7 sessions: 9%"), s.hover)
        XCTAssertTrue(s.hover.contains("14:00  11%"), s.hover)
        XCTAssertFalse(s.stale)
        XCTAssertNil(block(try Fixture.feed(), 1).share)
        let later = block(try Fixture.feed(), 0, now: Fixture.date("2026-11-26T21:54:00-08:00")).share!
        XCTAssertTrue(later.stale, "as_of 21:39 is 15 min old at 21:54")
        let noneYet = try ViewFixture.feed { a in
            var ss = a[0]["session_share"] as! [String: Any]; ss["current"] = NSNull(); a[0]["session_share"] = ss
        }
        XCTAssertEqual(block(noneYet, 0).share!.text, "This session: — · last: 11%")
    }

    func testSpendLine() throws {
        let f = try Fixture.feed()
        let s = SpendLineModel.make(today: f.totals.today, week: f.totals.week, asOf: f.totals.asOf,
                                    staleAfter: f.rules.staleAfterS, time: ViewFixture.time())
        XCTAssertEqual(s.text, "Today 18.4M tokens · $63.20 API-rate   7d $412")
        XCTAssertTrue(s.hover.hasPrefix("API-rate equivalent: what these tokens would cost at Anthropic's API prices. Not your bill."))
        XCTAssertFalse(s.hover.contains("unpriced"))
        XCTAssertFalse(s.stale)
        var today = f.totals.today
        today = Feed.Spend(tokens: today.tokens, usd: 1889.4, calls: today.calls, unpriced: 3)
        let u = SpendLineModel.make(today: today, week: f.totals.week, asOf: f.totals.asOf,
                                    staleAfter: f.rules.staleAfterS, time: ViewFixture.time(Fixture.date("2026-11-26T21:55:00-08:00")))
        XCTAssertEqual(u.text, "Today 18.4M tokens · $1,889 API-rate   7d $412")
        XCTAssertTrue(u.hover.contains("3 calls on unpriced models, not in $"))
        XCTAssertTrue(u.stale)
        XCTAssertTrue(u.hover.contains("Updated 15 min ago (21:40)."), u.hover)
    }

    func testEmptyStateText() {
        XCTAssertEqual(TodayModel.noAccounts, "Run `jusage live` once in Terminal to show Claude's limits here.")
    }

    func testHiddenAccountsLeaveTheTodayList() throws {
        let f = try Fixture.feed()
        var s = AppSettings()
        s.hiddenAccounts = ["/abs/.claude"]
        XCTAssertEqual(TodayModel.accounts(feed: f, settings: s).map(\.key), ["/abs/.claude-newaccount"])
        let reversed = try ViewFixture.feed { a in a.reverse() }
        XCTAssertEqual(TodayModel.accounts(feed: reversed, settings: AppSettings()).map(\.number), [1, 2])
    }
}
