import XCTest
@testable import Jusage

final class ThemeTests: XCTestCase {
    private func theme(_ extra: [String: Any] = [:], drop: String? = nil) -> [String: Any] {
        var t: [String: Any] = ["name": "T", "scheme": "dark", "bg": "#000000", "sf": "#111111", "sf2": "#222222",
                                "ink": "#ffffff", "ink2": "#eeeeee", "mut": "#999999", "line": "#333333",
                                "ok": "#00ff00", "warn": "#ffff00", "hot": "#ff0000", "accent": "#0000ff"]
        for (k, v) in extra { t[k] = v }
        if let drop { t.removeValue(forKey: drop) }
        return t
    }

    private func book(order: [String] = ["system", "t"], themes: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: ["order": order, "themes": themes])
    }

    func testShippedFileLoadsWithSevenThemes() throws {
        let b = try ThemeBook.load(url: Fixture.root.appendingPathComponent("engine/themes.json"))
        XCTAssertEqual(b.themes.map(\.id), ["system", "paper", "graphite", "console", "midnight", "sand", "mono"])
        XCTAssertEqual(Theme.resolve(id: "graphite", book: b).scheme, .dark)
        XCTAssertNotNil(Theme.resolve(id: "paper", book: b).rgb("ok"))
    }

    func testMissingKeyRefused() {
        let data = book(themes: ["system": ["name": "System", "scheme": NSNull()], "t": theme(drop: "warn")])
        XCTAssertThrowsError(try ThemeBook.parse(data)) {
            XCTAssertEqual(($0 as? ThemeError)?.message, "themes.json: theme 't' lacks 'warn'")
        }
    }

    func testBadHexRefused() {
        let data = book(themes: ["system": ["name": "System"], "t": theme(["hot": "red"])])
        XCTAssertThrowsError(try ThemeBook.parse(data)) {
            XCTAssertEqual(($0 as? ThemeError)?.message, "themes.json: theme 't' key 'hot' is not a #RRGGBB colour")
        }
    }

    func testOrderNamingAMissingThemeRefused() {
        let data = book(order: ["system", "ghost"], themes: ["system": ["name": "System"]])
        XCTAssertThrowsError(try ThemeBook.parse(data)) {
            XCTAssertEqual(($0 as? ThemeError)?.message, "themes.json: order lists 'ghost' but there is no such theme")
        }
    }

    func testSystemAlwaysPresent() throws {
        XCTAssertEqual(ThemeBook.current.themes.map(\.id), ["system"], "the built-in book is never empty")
        XCTAssertEqual(Theme.resolve(id: "nonsense").id, "system")
        // A file whose order leaves System out still offers it.
        let b = try ThemeBook.parse(book(order: ["t"], themes: ["system": ["name": "System"], "t": theme()]))
        XCTAssertEqual(b.themes.map(\.id), ["system", "t"])
        // A missing file refuses and names itself.
        XCTAssertThrowsError(try ThemeBook.load(url: URL(fileURLWithPath: "/nonexistent/themes.json"))) {
            XCTAssertEqual(($0 as? ThemeError)?.message, "themes.json: file is missing")
        }
    }

    func testBadSchemeRefused() {
        let data = book(themes: ["system": ["name": "System"], "t": theme(["scheme": "sepia"])])
        XCTAssertThrowsError(try ThemeBook.parse(data))
    }

    func testHexParses() {
        XCTAssertEqual(Theme.parseHex("#ff8000"), RGB(r: 1, g: Double(0x80) / 255, b: 0))
        XCTAssertNil(Theme.parseHex("ff8000"))
    }
}
