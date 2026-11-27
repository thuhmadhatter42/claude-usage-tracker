import XCTest
@testable import Jusage

@MainActor
final class MonthViewTests: XCTestCase {
    private func feed(months n: Int) throws -> Data {
        try Fixture.bytes { j in
            let m = (j["months"] as! [[String: Any]])[0]
            j["months"] = (0..<n).map { i -> [String: Any] in
                var x = m; x["key"] = "2026-\(11 - i)"; x["label"] = ["November", "October", "September"][i]; return x
            }
        }
    }

    func testIndexClamped() throws {
        XCTAssertEqual(MonthNav.older(0, count: 3), 1)
        XCTAssertEqual(MonthNav.older(2, count: 3), 2, "no month older than the last one")
        XCTAssertEqual(MonthNav.newer(0, count: 3), 0)
        XCTAssertEqual(MonthNav.newer(2, count: 3), 1)

        // The feed shrinks from 3 months to 1 while month 3 is shown.
        let model = AppModel(now: Fixture.now)
        model.apply(.ok(try feed(months: 3)), now: Fixture.now)
        model.monthIndex = MonthNav.older(MonthNav.older(model.monthIndex, count: 3), count: 3)
        XCTAssertEqual(model.monthIndex, 2)
        model.apply(.ok(try feed(months: 1)), now: Fixture.now)
        XCTAssertEqual(MonthModel.make(feed: model.feed!, index: model.monthIndex)?.label, "November")
        XCTAssertNil(MonthModel.make(feed: model.feed!, index: 4), "an index past the months is refused, never read")
    }

    func testMonthContent() throws {
        let m = MonthModel.make(feed: try Fixture.feed(), index: 0)!
        XCTAssertEqual(m.summary, "310.0M tokens · $1,480 API-rate · 9120 calls")
        XCTAssertEqual(m.types.map(\.name), ["Input", "Cache write", "Cache read", "Output"])
        XCTAssertEqual(m.types.map(\.colourKey), ["ink", "ink2", "mut", "ok"])
        XCTAssertFalse(m.empty)
        let empty = MonthModel.make(feed: try Fixture.feed { j in
            var m = (j["months"] as! [[String: Any]])[0]
            m["tokens"] = 0; m["calls"] = 0; m["usd"] = 0.0
            j["months"] = [m]
        }, index: 0)!
        XCTAssertTrue(empty.empty)
        XCTAssertEqual(MonthModel.nothing, "Nothing recorded this month.")
    }
}
