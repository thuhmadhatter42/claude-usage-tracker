import XCTest
@testable import Jusage

final class SettingsTests: XCTestCase {
    private func suite(_ name: String = #function) -> UserDefaults {
        let n = "jusage-test-\(name)-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: n)!
        d.removePersistentDomain(forName: n)
        return d
    }

    func testDefaults() {
        let s = AppSettings()
        XCTAssertFalse(s.labels)
        XCTAssertFalse(s.menuBarLabels)
        XCTAssertFalse(s.colourByWorstMeter)
        XCTAssertTrue(s.warnRunsOut)
        XCTAssertEqual(s.readEveryS, 60)
        XCTAssertEqual(s.theme, "system")
        XCTAssertEqual(s.accent, "#ffb020")
        XCTAssertEqual(s.menuBarMode, .meter("session"))
        XCTAssertNil(s.pythonPath)
    }

    func testCadenceStaysWithin15And600() {
        var s = AppSettings()
        s.readEveryS = 5
        XCTAssertEqual(s.readEveryS, 15)
        s.readEveryS = 9999
        XCTAssertEqual(s.readEveryS, 600)
        s.readEveryS = 120
        XCTAssertEqual(s.readEveryS, 120)
    }

    func testRoundTripThroughDefaults() throws {
        let d = suite()
        let store = SettingsStore(defaults: d)
        let key = LatchKey(folderKey: "/abs/.claude", meterId: "weekly:fable", kind: .critical)
        store.update {
            $0.labels = true
            $0.menuBarMode = .tokensToday
            $0.readEveryS = 300
            $0.hiddenAccounts = ["/abs/.claude-newaccount"]
            $0.notificationLatch[key] = LatchState(cycleResetsAt: Fixture.now, fired: true, firedThisCycle: true)
            $0.pythonPath = "/usr/bin/python3"
        }
        let again = SettingsStore(defaults: d)
        XCTAssertEqual(again.settings, store.settings)
        XCTAssertEqual(again.settings.notificationLatch[key]?.fired, true)
        XCTAssertEqual(again.settings.readEveryS, 300)
    }

    func testLatchEncodesAsAnObject() throws {
        var s = AppSettings()
        s.notificationLatch[LatchKey(folderKey: "/a", meterId: "session", kind: .warn80)] =
            LatchState(cycleResetsAt: Fixture.now, fired: true, firedThisCycle: true)
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(s)) as! [String: Any]
        XCTAssertTrue(json["notificationLatch"] is [String: Any])
    }

    func testUnreadableRecordGivesDefaults() {
        let d = suite()
        d.set(Data("garbage".utf8), forKey: SettingsStore.key)
        XCTAssertEqual(SettingsStore(defaults: d).settings, AppSettings())
    }

    func testRecordMissingKeysStillLoads() throws {
        let d = suite()
        d.set(Data(#"{"theme":"mono"}"#.utf8), forKey: SettingsStore.key)
        let s = SettingsStore(defaults: d).settings
        XCTAssertEqual(s.theme, "mono")
        XCTAssertEqual(s.readEveryS, 60)
    }

    func testTagMax4() {
        var s = AppSettings()
        s.setTag("Work-laptop", for: "/abs/.claude")
        XCTAssertEqual(s.menuBarTags["/abs/.claude"], "Work")
        s.setTag("  ", for: "/abs/.claude")
        XCTAssertNil(s.menuBarTags["/abs/.claude"])
    }

    func testOpenFoldsSeparate() {
        var s = AppSettings()
        s.setFold("detail", open: true)
        s.setFold("detail", open: true)
        s.setFold("projects", open: true)
        XCTAssertEqual(s.openFolds, ["detail", "projects"])
        XCTAssertTrue(s.hiddenAccounts.isEmpty && s.hiddenPeople.isEmpty && s.hiddenSections.isEmpty,
                      "folds never touch the visibility sets")
        s.setFold("detail", open: false)
        XCTAssertFalse(s.isFoldOpen("detail"))
        XCTAssertTrue(s.isFoldOpen("projects"))
    }

    func testMigratesHiddenThemeBarMode() throws {
        let old = suite("old"), new = suite("new")
        old.set(["acct:pat@example.com", "person:Lee", "sec:models", "fold:detail", "fold:person:Lee",
                 "acct:nobody@example.com", "person:Stranger"], forKey: "hidden")
        old.set("graphite", forKey: "theme")
        old.set("seven_day_fable", forKey: "barMode")
        let store = SettingsStore(defaults: new)
        var lines: [String] = []
        XCTAssertTrue(store.migrateFrom01(defaults: old, feed: try Fixture.feed(), log: { lines.append($0) }))
        let s = store.settings
        XCTAssertEqual(s.hiddenAccounts, ["/abs/.claude-newaccount"], "email mapped to the folder key")
        XCTAssertEqual(s.hiddenPeople, ["c3d4"], "name mapped to the install id")
        XCTAssertEqual(s.hiddenSections, [.models])
        XCTAssertEqual(Set(s.openFolds), ["detail", "person:c3d4"])
        XCTAssertEqual(s.theme, "graphite")
        XCTAssertEqual(s.menuBarMode, .meter("weekly:fable"))
        XCTAssertEqual(lines.count, 1, "one log line for everything dropped")
        XCTAssertTrue(lines[0].contains("acct:nobody@example.com") && lines[0].contains("person:Stranger"))
        XCTAssertNotNil(old.stringArray(forKey: "hidden"), "old keys stay for rollback")
        // Once only.
        XCTAssertFalse(store.migrateFrom01(defaults: old, feed: try Fixture.feed()))
        XCTAssertTrue(SettingsStore(defaults: new).migrated)
    }

    func testOldBarModes() {
        XCTAssertEqual(SettingsStore.mode(fromOld: "today"), .tokensToday)
        XCTAssertEqual(SettingsStore.mode(fromOld: "five_hour"), .meter("session"))
        XCTAssertEqual(SettingsStore.mode(fromOld: "seven_day"), .meter("weekly"))
        XCTAssertEqual(SettingsStore.mode(fromOld: "sam@x.com|seven_day"), .meter("weekly"))
    }

    func testMigrationKeepsWhatTheUserAlreadyChanged() throws {
        let old = suite("old2"), new = suite("new2")
        old.set("graphite", forKey: "theme")
        let store = SettingsStore(defaults: new)
        store.update { $0.theme = "sand" }
        store.migrateFrom01(defaults: old, feed: try Fixture.feed())
        XCTAssertEqual(store.settings.theme, "sand")
    }
}
