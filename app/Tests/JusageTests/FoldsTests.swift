import XCTest
@testable import Jusage

final class FoldsTests: XCTestCase {
    private func people(_ f: Feed, _ s: AppSettings = AppSettings(), now: Date = Fixture.now) -> [PersonFoldModel] {
        PeopleModel.make(feed: f, settings: s, time: ViewFixture.time(now))
    }

    func testPeopleKeyedByInstallId() throws {
        let f = try Fixture.feed { j in
            var p = j["people"] as! [[String: Any]]
            var twin = p[0]
            twin["install_id"] = "e5f6"
            twin["host"] = "studio"
            p.append(twin)
            j["people"] = p
        }
        let folds = people(f)
        XCTAssertEqual(folds.map(\.id), ["c3d4", "e5f6"], "two people named Lee never collide")
        XCTAssertEqual(folds.map(\.foldId), ["person:c3d4", "person:e5f6"])
        XCTAssertEqual(folds[0].title, "Lee")
        XCTAssertEqual(folds[0].trailing, "5.2M · $18.00")
        var s = AppSettings()
        s.hiddenPeople = ["e5f6"]
        XCTAssertEqual(people(f, s).map(\.id), ["c3d4"])
    }

    func testPeerWithEmptyFoldersAndAcctKeyStillShowsItsAccounts() throws {
        let f = try Fixture.feed { j in
            var p = j["people"] as! [[String: Any]]
            var accounts = p[0]["accounts"] as! [[String: Any]]
            accounts[0]["key"] = "acct:11111111-2222-3333-4444-555555555555"
            accounts[0]["folders"] = [String]()
            p[0]["accounts"] = accounts
            j["people"] = p
        }
        let accounts = people(f)[0].accounts
        XCTAssertFalse(accounts.isEmpty)
        XCTAssertEqual(accounts[0].id, "acct:11111111-2222-3333-4444-555555555555")
    }

    func testPersonGreysAt15Minutes() throws {
        let fresh = people(try Fixture.feed(), now: Fixture.date("2026-11-26T21:52:59-08:00"))[0]
        XCTAssertFalse(fresh.stale)
        XCTAssertNil(fresh.staleLine)
        let old = people(try Fixture.feed(), now: Fixture.date("2026-11-26T21:53:00-08:00"))[0]
        XCTAssertTrue(old.stale)
        XCTAssertEqual(old.staleLine, "Last report 21:38 from mac")
    }

    func testPersonBodyHasNoRunsOutLine() throws {
        let f = try Fixture.feed { j in
            var p = j["people"] as! [[String: Any]]
            var a = p[0]["accounts"] as! [[String: Any]]
            var m = a[0]["meters"] as! [[String: Any]]
            var pace = m[1]["pace"] as! [String: Any]
            pace["runs_out"] = "2026-11-28T08:00:00-08:00"
            m[1]["pace"] = pace
            a[0]["meters"] = m
            p[0]["accounts"] = a
            j["people"] = p
        }
        XCTAssertTrue(people(f)[0].accounts[0].meters.allSatisfy { $0.runsOut == nil })
    }

    func testAccountTogglesApplyToPeople() throws {
        // Lee also uses account #1 (same uuid as /abs/.claude).
        let f = try Fixture.feed { j in
            var p = j["people"] as! [[String: Any]]
            var a = p[0]["accounts"] as! [[String: Any]]
            var shared = a[0]
            shared["uuid"] = "11111111-1111-4111-8111-111111111111"
            shared["key"] = "/abs/lee/.claude-shared"
            a.append(shared)
            p[0]["accounts"] = a
            j["people"] = p
        }
        XCTAssertEqual(people(f)[0].accounts.count, 2)
        var s = AppSettings()
        s.hiddenAccounts = ["/abs/.claude"]
        XCTAssertEqual(people(f, s)[0].accounts.map(\.id), ["/abs/lee/.claude"])
    }

    func testSplitRowIdsUnique() throws {
        // Two accounts whose names share a prefix, each with a weekly split.
        let f = try ViewFixture.feed { a in
            a[1]["email"] = "sam@other.example.com"
            var m = a[1]["meters"] as! [[String: Any]]
            m[1]["split"] = (a[0]["meters"] as! [[String: Any]])[1]["split"]
            a[1]["meters"] = m
        }
        let rows = SplitModel.rows(feed: f, settings: AppSettings())
        XCTAssertEqual(rows.map(\.id), ["/abs/.claude|weekly", "/abs/.claude-newaccount|weekly"])
        XCTAssertEqual(Set(rows.map(\.id)).count, rows.count)
        XCTAssertEqual(rows[0].text, "Sam 71% · Lee 29%")
    }

    func testSplitTextMeFirst() throws {
        let f = try Fixture.feed()
        let parts = f.accounts[0].meters[1].split!.parts
        XCTAssertEqual(SplitModel.text(Array(parts.reversed()), me: "a1b2"), "Sam 71% · Lee 29%")
    }

    func testOpenWindowsModelsProjects() throws {
        let f = try Fixture.feed { j in
            j["models"] = (0..<6).map { ["id": "m\($0)", "name": "m\($0)", "tokens": 100 - $0, "usd": 1.0] }
            j["projects_today"] = (0..<7).map { ["name": "p\($0)", "share": 0.1, "tokens": 10] }
        }
        let t = ViewFixture.time()
        XCTAssertEqual(DetailModel.windows(feed: f, settings: AppSettings(), time: t).map(\.line), ["sam 19:00–Fri 00:00  6.2M · $21.40"])
        XCTAssertEqual(DetailModel.models(feed: f).map(\.name), ["m0", "m1", "m2", "m3"])
        XCTAssertEqual(ProjectsModel.rows(feed: f).count, 5)
        XCTAssertEqual(ProjectsModel.rows(feed: try Fixture.feed()).map(\.text), ["75%", "25%"], "today's list, not the week's")
    }

    func testMetersAndWindowsSectionsGate() throws {
        // RULINGS 48a / R-84: "Claude's meters" and "Open 5-hour windows" are Show toggles like the rest.
        let f = try Fixture.feed()
        var s = AppSettings()
        let t = ViewFixture.time()
        XCTAssertFalse(TodayModel.meterBlocks(feed: f, settings: s).isEmpty)
        XCTAssertFalse(DetailModel.windows(feed: f, settings: s, time: t).isEmpty)
        s.hiddenSections = [.meters, .windows]
        XCTAssertTrue(TodayModel.meterBlocks(feed: f, settings: s).isEmpty)
        XCTAssertTrue(DetailModel.windows(feed: f, settings: s, time: t).isEmpty)
        XCTAssertEqual(SplitModel.rows(feed: f, settings: s), SplitModel.rows(feed: f, settings: AppSettings()),
                       "the split rows do not follow the meters toggle")
        XCTAssertEqual(Section.allCases, [.meters, .windows, .today, .people, .models, .projects])
    }

    func testFoldStateIsOpenFolds() {
        var s = AppSettings()
        XCTAssertFalse(s.isFoldOpen(FoldID.detail), "closed by default")
        s.setFold(FoldID.detail, open: true)
        XCTAssertEqual(s.openFolds, ["detail"])
        XCTAssertTrue(s.hiddenSections.isEmpty, "never mixed into the visibility toggles")
        XCTAssertEqual(FoldID.person("c3d4"), "person:c3d4")
    }
}
