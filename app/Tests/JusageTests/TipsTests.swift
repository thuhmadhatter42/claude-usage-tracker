import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

final class TipsTests: XCTestCase {
    /// THE test that holds the app to Board Talk: every tip in the app's table passes every rule.
    func testEveryTipInTheAppsTableIsBoardTalk() {
        XCTAssertEqual(HouseTips.problems(HouseTips.all), [], "see BOARD-TALK.md")
        XCTAssertFalse(HouseTips.all.isEmpty, "the table lists the app's tips")
    }

    func testTheCapIsSixtyCharacters() {
        XCTAssertEqual(HouseTip.maxBody, 60)
        let sixty = String(repeating: "a", count: 60)
        XCTAssertEqual(HouseTips.problems([HouseTip("OK", sixty)]), [])
        XCTAssertEqual(HouseTips.problems([HouseTip("LONG", sixty + "a")]), ["too long (61): LONG"])
    }

    func testTheRulesCatchWhatTheyShould() {
        let p = HouseTips.problems([
            HouseTip("BPM", "Click: normal, double or half time."),
            HouseTip("OPEN", "You can also drop files on the window."),
            HouseTip("SEND", "This will simply send the key."),
            HouseTip("BPM", "Tempo."),
            HouseTip("EMPTY", "  "),
        ])
        XCTAssertEqual(p, ["uses \"you can\": OPEN", "uses \"simply\": SEND", "uses \"this will\": SEND",
                           "title used twice: BPM", "empty: EMPTY"])
        XCTAssertEqual(HouseTips.problems([HouseTip("PIN", "Displeased? Click to unpin.")]), [], "a banned word inside another word is fine")
    }

    @MainActor
    func testTheModifierBuilds() {
        let host = NSHostingView(rootView: Text("x").tip(HouseTip("X", "Click to pin.")))
        host.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(host.fittingSize.width, 0)
    }
}
