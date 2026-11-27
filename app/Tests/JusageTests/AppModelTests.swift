import XCTest
@testable import Jusage

@MainActor
final class AppModelTests: XCTestCase {
    private func good() throws -> Data { try Fixture.data("tests/fixtures/feed-v2.json") }

    func testFailureKeepsLastGood() throws {
        let m = AppModel(now: Fixture.now)
        m.apply(.ok(try good()), now: Fixture.now)
        XCTAssertNotNil(m.feed)
        XCTAssertNil(m.runError)
        let t1 = Fixture.now.addingTimeInterval(60)
        m.runStarted()
        XCTAssertTrue(m.running)
        m.apply(.failed(line: "Engine failed: boom", exit: 1), now: t1)
        XCTAssertEqual(m.feed?.accounts.count, 2, "last good stays on screen")
        XCTAssertEqual(m.runError, "Engine failed: boom")
        XCTAssertEqual(m.lastSuccessAt, Fixture.now, "header time is the last SUCCESS, not the failed attempt")
        XCTAssertFalse(m.running)
    }

    func testEveryFailureKindHasPlainText() throws {
        let m = AppModel(now: Fixture.now)
        m.apply(.timedOut, now: Fixture.now)
        XCTAssertEqual(m.runError, "Refresh took over 90 s and was stopped. Is a Keychain prompt waiting?")
        m.apply(.crashed(signal: 11), now: Fixture.now)
        XCTAssertEqual(m.runError, "The engine stopped unexpectedly (signal 11).")
        m.apply(.launchFailed("no such file"), now: Fixture.now)
        XCTAssertEqual(m.runError, "The engine could not start: no such file")
        XCTAssertNil(m.feed)
    }

    func testOneCopyOfFailureText() {
        XCTAssertNil(AppModel.failureText(.ok(Data())))
        XCTAssertEqual(AppModel.failureText(.failed(line: "jusage: x", exit: 1)), "jusage: x")
        XCTAssertEqual(AppModel.failureText(.timedOut), FeedRunner.timeoutText)
    }

    func testWrongSchemaKeepsLastGoodAndSaysSo() throws {
        let m = AppModel(now: Fixture.now)
        m.apply(.ok(try good()), now: Fixture.now)
        m.apply(.ok(try Fixture.bytes { $0["schema"] = 1 }), now: Fixture.now.addingTimeInterval(60))
        XCTAssertEqual(m.runError, "Engine sent feed schema 1; this app reads 2")
        XCTAssertNotNil(m.feed)
        XCTAssertEqual(m.lastSuccessAt, Fixture.now)
    }

    func testGoodRunClearsTheError() throws {
        let m = AppModel(now: Fixture.now)
        m.apply(.failed(line: "x", exit: 1), now: Fixture.now)
        m.apply(.ok(try good()), now: Fixture.now.addingTimeInterval(60))
        XCTAssertNil(m.runError)
        XCTAssertEqual(m.lastSuccessAt, Fixture.now.addingTimeInterval(60))
    }

    func testMonthIndexClampedWhenFeedShrinks() throws {
        let m = AppModel(now: Fixture.now)
        m.monthIndex = 2
        m.apply(.ok(try good()), now: Fixture.now)     // the fixture has one month
        XCTAssertEqual(m.monthIndex, 0)
        let none = try Fixture.bytes { $0["months"] = [] }
        m.monthIndex = 1
        m.apply(.ok(none), now: Fixture.now)
        XCTAssertEqual(m.monthIndex, 0)
    }
}
