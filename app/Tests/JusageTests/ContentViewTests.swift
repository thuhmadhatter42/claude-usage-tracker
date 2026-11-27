import XCTest
import SwiftUI
@testable import Jusage

@MainActor
final class ContentViewTests: XCTestCase {
    func testHeaderShowsLastSuccessNotFailure() throws {
        let model = AppModel(now: Fixture.now)
        model.apply(.ok(try Fixture.data("tests/fixtures/feed-v2.json")), now: Fixture.date("2026-11-26T21:40:00-08:00"))
        model.apply(.failed(line: "Keychain access was denied.", exit: 1), now: Fixture.date("2026-11-26T21:45:00-08:00"))
        let h = HeaderModel.make(model: model, time: ViewFixture.time(Fixture.date("2026-11-26T21:46:00-08:00")))
        XCTAssertEqual(h.trailing, .time("21:40"), "the failed run's time never shows")
        XCTAssertEqual(h.warning, "Keychain access was denied.")
        XCTAssertTrue(h.tabs)
        XCTAssertTrue(h.dashboard)

        model.runStarted()
        XCTAssertEqual(HeaderModel.make(model: model, time: ViewFixture.time()).trailing, .spinner)
    }

    func testBeforeAnyFeed() {
        let h = HeaderModel.make(model: AppModel(now: Fixture.now), time: ViewFixture.time())
        XCTAssertEqual(h.trailing, .none)
        XCTAssertFalse(h.tabs, "no months: the title says jusage")
        XCTAssertFalse(h.dashboard, "⌘D only when a dashboard exists")
        XCTAssertNil(h.warning)
    }

    func testNoMonthsNoTabs() throws {
        let model = AppModel(now: Fixture.now)
        model.apply(.ok(try Fixture.bytes { j in j["months"] = []; j["dashboard"] = NSNull() }), now: Fixture.now)
        let h = HeaderModel.make(model: model, time: ViewFixture.time())
        XCTAssertFalse(h.tabs)
        XCTAssertFalse(h.dashboard)
        XCTAssertEqual(TabNav.shown(.month, hasMonths: false), .today)
        XCTAssertEqual(TabNav.shown(.settings, hasMonths: false), .settings)
    }

    func testSettingsReturnsToPreviousTab() {
        var nav = TabNav()
        var tab = Tab.month
        nav.toggleSettings(&tab)
        XCTAssertEqual(tab, .settings)
        nav.toggleSettings(&tab)
        XCTAssertEqual(tab, .month)
        tab = .today
        nav.toggleSettings(&tab)
        nav.select(.month, &tab)
        XCTAssertEqual(tab, .month, "a tab chosen from settings wins")
        nav.toggleSettings(&tab)
        nav.toggleSettings(&tab)
        XCTAssertEqual(tab, .month)
    }

    func testFailuresLoggedOncePerChange() throws {
        let log = ViewLogStub()
        PanelFailureLog.runError(from: nil, to: "Engine sent feed schema 3; this app reads 2", log: log)
        PanelFailureLog.runError(from: "Engine sent feed schema 3; this app reads 2", to: "Engine sent feed schema 3; this app reads 2", log: log)
        PanelFailureLog.runError(from: "Engine sent feed schema 3; this app reads 2", to: nil, log: log)
        XCTAssertEqual(log.lines, ["feed: Engine sent feed schema 3; this app reads 2"])
        let f = try ViewFixture.feed { a in a[0]["error"] = ["kind": "new_kind", "text": "x", "until": NSNull()] }
        PanelFailureLog.feedArrived(f, log: log)
        XCTAssertEqual(log.lines.last, "feed: unknown error kind new_kind")
    }

    func testRendersEveryTab() throws {
        let model = AppModel(now: Fixture.now)
        model.apply(.ok(try Fixture.data("tests/fixtures/feed-v2.json")), now: Fixture.now)
        let store = SettingsStore(defaults: UserDefaults(suiteName: "jusage-views-\(UUID().uuidString)")!)
        store.update { $0.openFolds = [FoldID.detail, FoldID.projects, FoldID.person("c3d4")] }
        let services = ViewFixture.services()
        for tab in [Tab.today, .month, .settings] {
            model.tab = tab
            let host = NSHostingView(rootView: ContentView(model: model, settings: store, services: services))
            host.layoutSubtreeIfNeeded()
            XCTAssertEqual(host.fittingSize.width, Layout.width, accuracy: 0.5, "\(tab)")
            XCTAssertGreaterThan(host.fittingSize.height, 40, "\(tab)")
        }
    }
}
