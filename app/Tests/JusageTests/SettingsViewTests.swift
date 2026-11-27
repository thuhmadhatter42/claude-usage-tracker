import XCTest
@testable import Jusage

@MainActor
final class SettingsViewTests: XCTestCase {
    private func store() -> SettingsStore { SettingsStore(defaults: UserDefaults(suiteName: "jusage-settings-\(UUID().uuidString)")!) }

    func testOrderMatchesSpec() {
        XCTAssertEqual(SettingsRow.allCases, [
            .theme, .accent, .people, .accounts,
            .showLabels, .showMenuBarLabels, .showWarnRunsOut, .showToday, .showModels, .showProjects, .showPeople,
            .statusLineBackup, .checkEvery, .menuBarShows, .colourByWorstMeter, .openAtLogin, .footer, .quit,
        ])
        XCTAssertEqual(SettingsRow.footerText, "Settings live on this Mac only.")
    }

    func testDefaultsOff() {
        let s = AppSettings()
        XCTAssertFalse(s.labels)
        XCTAssertFalse(s.menuBarLabels)
        XCTAssertFalse(s.colourByWorstMeter)
        XCTAssertEqual(s.theme, "system")
        XCTAssertEqual(SettingsRow.cadenceChoices.first, 15)
        XCTAssertEqual(SettingsRow.cadenceChoices.last, 600)
        XCTAssertTrue(SettingsRow.cadenceChoices.contains(60))
        XCTAssertEqual(SettingsRow.cadenceLabel(15), "15 s")
        XCTAssertEqual(SettingsRow.cadenceLabel(60), "1 min")
        XCTAssertEqual(SettingsRow.cadenceLabel(600), "10 min")
    }

    func testCadenceWritesThroughEngine() async throws {
        let engine = ViewEngineStub(), log = ViewLogStub(), st = store()
        let w = EngineSettingsWriter(run: { await engine.run($0) }, log: log, settings: st)
        await w.setReadEvery(120)
        XCTAssertEqual(engine.calls, [["config", "read-every", "120"]])
        XCTAssertEqual(st.settings.readEveryS, 120, "the mirror follows a write the engine took")
        XCTAssertNil(w.cadenceError)

        engine.reply = .failed(line: "read-every must be 15..600", exit: 1)
        await w.setReadEvery(30)
        XCTAssertEqual(w.cadenceError, "read-every must be 15..600")
        XCTAssertEqual(st.settings.readEveryS, 120, "a refused write leaves the mirror alone")
        XCTAssertEqual(log.lines, ["settings: config read-every 30 refused: read-every must be 15..600"])

        await w.setReadEvery(5)
        XCTAssertEqual(engine.calls.last, ["config", "read-every", "15"], "never sends a value outside 15..600")
    }

    func testCadenceShownIsTheFeedsValue() throws {
        let f = try Fixture.feed { j in var r = j["rules"] as! [String: Any]; r["read_every_s"] = 300; j["rules"] = r }
        XCTAssertEqual(SettingsRow.cadenceShown(feed: f, settings: AppSettings()), 300)
        XCTAssertEqual(SettingsRow.cadenceShown(feed: nil, settings: AppSettings()), 60)
    }

    func testStatusLineToggleRunsEngineAndShowsRefusal() async {
        let engine = ViewEngineStub(), log = ViewLogStub()
        let w = EngineSettingsWriter(run: { await engine.run($0) }, log: log, settings: store())
        await w.setStatusline(true, folder: "/abs/.claude")
        XCTAssertEqual(engine.calls, [["statusline", "on", "--folder", "/abs/.claude"]])
        XCTAssertNil(w.statuslineErrors["/abs/.claude"])
        engine.reply = .failed(line: "statusLine was changed by someone else; left as is", exit: 1)
        await w.setStatusline(false, folder: "/abs/.claude")
        XCTAssertEqual(engine.calls.last, ["statusline", "off", "--folder", "/abs/.claude"])
        XCTAssertEqual(w.statuslineErrors["/abs/.claude"], "statusLine was changed by someone else; left as is")
        XCTAssertEqual(log.lines.count, 1)
    }

    func testLoginItemFailureShowsReason() {
        let log = ViewLogStub()
        let w = LoginItemWriter(control: LoginItemControl(isOn: { false }, set: { _ in "Operation not permitted" }), log: log)
        w.set(true)
        XCTAssertEqual(w.error, "Operation not permitted")
        XCTAssertFalse(w.isOn)
        XCTAssertEqual(log.lines, ["settings: open at login refused: Operation not permitted"])
    }

    func testMenuBarChoicesFromFeed() throws {
        let c = SettingsRow.menuBarChoices(feed: try Fixture.feed())
        XCTAssertEqual(c.map(\.title), ["Session", "Weekly", "Weekly · Fable", "Tokens today"])
        XCTAssertEqual(c.last?.mode, .tokensToday)
        XCTAssertEqual(c.first?.mode, .meter("session"))
    }

    func testThemePickerHasSystem() {
        XCTAssertEqual(SettingsRow.themes().first?.id, "system")
    }
}
