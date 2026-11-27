import XCTest
@testable import Jusage

final class ErrorBannerTests: XCTestCase {
    static let kinds = ["login_expired", "login_missing", "keychain_waiting", "keychain_denied", "rate_limited",
                        "no_network", "http", "reply_changed", "identity_changed", "identity_foreign_reset"]

    func testEveryKindHasPlainText() {
        for kind in Self.kinds {
            let t = BannerText.line(kind: kind, until: nil, label: "sam", time: ViewFixture.time())
            XCTAssertTrue(t.hasPrefix("sam: "), t)
            XCTAssertFalse(t.contains("_"), "no error codes on screen: \(t)")
            XCTAssertGreaterThan(t.count, 12, t)
        }
        XCTAssertEqual(BannerText.line(kind: "rate_limited", until: Fixture.date("2026-11-26T21:57:00-08:00"),
                                       label: "pat", time: ViewFixture.time()),
                       "pat: Anthropic is rate-limiting usage reads until 21:57.")
    }

    func testBannersFromFeedRunAndShare() throws {
        let f = try Fixture.feed()
        var b = BannerText.banners(feed: f, runError: nil, settings: AppSettings(), time: ViewFixture.time())
        XCTAssertEqual(b.map(\.text), ["pat: Anthropic is rate-limiting usage reads until 21:57."])
        XCTAssertEqual(b[0].hover, "Anthropic is rate-limiting usage reads; next try 21:57", "full detail in hover")

        b = BannerText.banners(feed: f, runError: "The engine stopped unexpectedly (signal 11).", settings: AppSettings(),
                               time: ViewFixture.time())
        XCTAssertEqual(b.first?.text, "The engine stopped unexpectedly (signal 11).")

        var s = AppSettings()
        s.hiddenAccounts = ["/abs/.claude-newaccount"]
        XCTAssertEqual(BannerText.banners(feed: f, runError: nil, settings: s, time: ViewFixture.time()), [],
                       "a hidden account is silent")

        let shareBroken = try Fixture.feed { j in j["share"] = ["written_at": NSNull(), "error": "folder not writable"] }
        let sb = BannerText.banners(feed: shareBroken, runError: nil, settings: s, time: ViewFixture.time())
        XCTAssertEqual(sb.map(\.text), ["Shared report not updated."])
        XCTAssertEqual(sb[0].hover, "folder not writable")
    }

    func testUnknownKindShowsEngineText() throws {
        let f = try ViewFixture.feed { a in a[0]["error"] = ["kind": "new_kind", "text": "Something new.", "until": NSNull()] }
        let b = BannerText.banners(feed: f, runError: nil, settings: AppSettings(), time: ViewFixture.time())
        XCTAssertEqual(b.first?.text, "sam: Something new.")
        XCTAssertEqual(BannerText.unknownKinds(in: f), ["new_kind"])
        XCTAssertEqual(BannerText.unknownKinds(in: try Fixture.feed()), [])
    }
}
