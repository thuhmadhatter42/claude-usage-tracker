import XCTest
@testable import Jusage

/// Shared by every Core test: the golden feed and helpers to bend it.
enum Fixture {
    static let root: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

    static func data(_ rel: String) throws -> Data { try Data(contentsOf: root.appendingPathComponent(rel)) }

    static func json() throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: data("tests/fixtures/feed-v2.json")) as! [String: Any]
    }

    static func feed() throws -> Feed { try Feed.decode(data("tests/fixtures/feed-v2.json")) }

    /// Decode the golden feed after `bend` has changed its JSON.
    static func feed(bending bend: (inout [String: Any]) -> Void) throws -> Feed {
        var j = try json()
        bend(&j)
        return try Feed.decode(JSONSerialization.data(withJSONObject: j))
    }

    static func bytes(bending bend: (inout [String: Any]) -> Void) throws -> Data {
        var j = try json()
        bend(&j)
        return try JSONSerialization.data(withJSONObject: j)
    }

    static func date(_ s: String) -> Date { try! ISO.parse(s) }

    /// The golden feed's own moment: 21:40 on 2026-11-26 (-08:00).
    static let now = date("2026-11-26T21:40:00-08:00")
}

final class FeedTests: XCTestCase {
    func testGoldenDecodes() throws {
        let f = try Fixture.feed()
        XCTAssertEqual(f.schema, 2)
        XCTAssertEqual(f.version, "0.2.0")
        XCTAssertEqual(f.rules.staleAfterS, 900)
        XCTAssertEqual(f.rules.redAt, 97)
        XCTAssertEqual(f.rules.readEveryS, 60)
        XCTAssertEqual(f.me.installId, "a1b2")
        XCTAssertEqual(f.accounts.map(\.number), [1, 2])
        let a1 = f.accounts[0]
        XCTAssertEqual(a1.meters.map(\.id), ["session", "weekly", "weekly:fable"])
        XCTAssertEqual(a1.meters[0].pace.state, .green)
        XCTAssertEqual(a1.meters[1].pace.state, .amber)
        XCTAssertNotNil(a1.meters[1].pace.runsOut)
        XCTAssertNil(a1.meters[0].pace.runsOut)
        XCTAssertEqual(a1.meters[1].split?.parts.map(\.share), [0.71, 0.29])
        XCTAssertEqual(a1.sessionShare?.median7, 9.0)
        XCTAssertEqual(a1.flags, ["using_credits", "reset_available"])
        let a2 = f.accounts[1]
        XCTAssertEqual(a2.error?.kind, "rate_limited")
        XCTAssertNotNil(a2.error?.until)
        XCTAssertNil(a2.sessionShare)
        XCTAssertEqual(a2.missingPeople, ["Lee"])
        XCTAssertEqual(f.totals.today.tokens, 18_400_000)
        XCTAssertEqual(f.windows.count, 2)
        XCTAssertEqual(f.models.map(\.name), ["opus-5", "sonnet-5"])
        XCTAssertEqual(f.months.first?.types.cacheRead, 240_000_000)
        XCTAssertEqual(f.people.map(\.user), ["Lee"])
        XCTAssertNil(f.people[0].accounts[0].meters[0].pace.runsOut)
        XCTAssertEqual(f.statusline["/abs/.claude-newaccount"]?.enabled, true)
        XCTAssertEqual(f.dashboard, "/abs/dashboard.html")
        XCTAssertNil(f.share.error)
        XCTAssertEqual(f.notes.count, 1)
        XCTAssertEqual(f.droppedPeople, 0)
    }

    func testTimesKeepTheirInstant() throws {
        let f = try Fixture.feed()
        XCTAssertEqual(f.generated, Fixture.date("2026-11-27T05:40:00Z"))
    }

    func testSchema1Refused() throws {
        let data = try Fixture.bytes { $0["schema"] = 1 }
        XCTAssertThrowsError(try Feed.decode(data)) { e in
            XCTAssertEqual(e as? FeedError, .schemaMismatch(found: 1))
            XCTAssertEqual((e as? FeedError)?.errorDescription, "Engine sent feed schema 1; this app reads 2")
        }
    }

    func testSchemaRefusedBeforeAnythingElseIsRead() throws {
        // Everything else is garbage; the schema alone decides.
        let data = Data(#"{"schema": 3, "accounts": "nonsense"}"#.utf8)
        XCTAssertThrowsError(try Feed.decode(data)) { XCTAssertEqual($0 as? FeedError, .schemaMismatch(found: 3)) }
    }

    func testMissingSchemaNamed() {
        XCTAssertThrowsError(try Feed.decode(Data("{}".utf8))) { XCTAssertEqual($0 as? FeedError, .badField("schema")) }
        XCTAssertThrowsError(try Feed.decode(Data("not json".utf8))) { XCTAssertEqual($0 as? FeedError, .badField("schema")) }
    }

    func testBadPeerDropped() throws {
        let f = try Fixture.feed { j in
            var people = j["people"] as! [[String: Any]]
            var broken = people[0]
            broken["install_id"] = "e5f6"
            broken["generated"] = 12345            // wrong type
            var naive = people[0]
            naive["install_id"] = "g7h8"
            naive["generated"] = "2026-11-26T21:38:00"   // no offset
            people.append(broken)
            people.append(naive)
            j["people"] = people
        }
        XCTAssertEqual(f.people.map(\.installId), ["c3d4"])
        XCTAssertEqual(f.droppedPeople, 2)
        XCTAssertEqual(f.accounts.count, 2, "the rest of the feed is kept")
    }

    func testNaiveTimeRefused() throws {
        let data = try Fixture.bytes { j in
            var accounts = j["accounts"] as! [[String: Any]]
            var meters = accounts[0]["meters"] as! [[String: Any]]
            meters[0]["read_at"] = "2026-11-26T21:39:00"
            accounts[0]["meters"] = meters
            j["accounts"] = accounts
        }
        XCTAssertThrowsError(try Feed.decode(data)) { e in
            XCTAssertEqual(e as? FeedError, .naiveTime("2026-11-26T21:39:00"))
        }
    }

    func testMissingFieldNamed() throws {
        let data = try Fixture.bytes { j in
            var rules = j["rules"] as! [String: Any]
            rules.removeValue(forKey: "stale_after_s")
            j["rules"] = rules
        }
        XCTAssertThrowsError(try Feed.decode(data)) { e in
            XCTAssertEqual(e as? FeedError, .badField("rules.stale_after_s"))
        }
    }

    func testUnknownPaceStateRefused() throws {
        let data = try Fixture.bytes { j in
            var accounts = j["accounts"] as! [[String: Any]]
            var meters = accounts[0]["meters"] as! [[String: Any]]
            var pace = meters[0]["pace"] as! [String: Any]
            pace["state"] = "purple"
            meters[0]["pace"] = pace
            accounts[0]["meters"] = meters
            j["accounts"] = accounts
        }
        XCTAssertThrowsError(try Feed.decode(data)) { XCTAssertTrue($0 is FeedError) }
    }

    func testNullPaceStateIsNoColour() throws {
        let f = try Fixture.feed { j in
            var accounts = j["accounts"] as! [[String: Any]]
            var meters = accounts[0]["meters"] as! [[String: Any]]
            var pace = meters[0]["pace"] as! [String: Any]
            pace["state"] = NSNull()
            pace["reset_due"] = true
            meters[0]["pace"] = pace
            accounts[0]["meters"] = meters
            j["accounts"] = accounts
        }
        XCTAssertNil(f.accounts[0].meters[0].pace.state)
        XCTAssertTrue(f.accounts[0].meters[0].pace.resetDue)
    }

    func testZAndOffsetAreTheSameInstant() throws {
        XCTAssertEqual(try ISO.parse("2026-11-27T05:40:00Z"), try ISO.parse("2026-11-26T21:40:00-08:00"))
    }
}
