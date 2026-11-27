import AppKit
import XCTest
@testable import Jusage

/// S12: the status item (R-53…R-59, R-150, R-169, R-192 display). Tests never create a real NSStatusItem (it
/// would appear in the menu bar); they use a fake slot.
@MainActor
final class StatusItemTests: XCTestCase {
    final class FakeSlot: StatusSlot {
        var isOnScreen = true
        var anchor: PanelAnchor? = PanelAnchor(item: NSRect(x: 900, y: 1000, width: 60, height: 24),
                                               visible: NSRect(x: 0, y: 0, width: 1440, height: 1000))
        private(set) var title: NSAttributedString?
        private(set) var length: CGFloat = 0
        private(set) var tooltip = ""
        private(set) var removed = false
        private(set) var popped: NSMenu?
        let click: (Bool) -> Void
        init(click: @escaping (Bool) -> Void) { self.click = click }
        func show(title: NSAttributedString, length: CGFloat, tooltip: String) {
            self.title = title; self.length = length; self.tooltip = tooltip
        }
        func popUp(_ menu: NSMenu) { popped = menu }
        func remove() { removed = true }
    }

    final class FakeMaker: StatusSlotMaking {
        private(set) var made: [FakeSlot] = []
        func make(click: @escaping (Bool) -> Void) -> StatusSlot {
            let s = FakeSlot(click: click); made.append(s); return s
        }
    }

    var maker: FakeMaker!
    var leftClicks = 0
    var log: RecordingLog!

    func controller(menu: @escaping @MainActor () -> [NSMenuItem] = { [] }) -> StatusItemController {
        maker = FakeMaker(); leftClicks = 0; log = RecordingLog()
        return StatusItemController(maker: maker, menuItems: menu, leftClick: { [unowned self] in self.leftClicks += 1 }, log: log)
    }

    let items = [
        TitleItem(text: "37%", tag: "1", colour: .green, stale: false, hover: "a@x\nSession 37%"),
        TitleItem(text: "98%", tag: "2", colour: .red, stale: false, hover: "b@x\nSession 98%"),
        TitleItem(text: "12%", tag: nil, colour: .amber, stale: true, hover: "c@x\nSession 12%"),
    ]

    // MARK: SCORE-named tests

    func testMenuHasEveryHouseItem() {
        var quit = 0
        let updater = AppUpdater(bundle: Bundle(for: StatusItemTests.self))     // not an app: no Sparkle
        let c = controller(menu: { JusageHouseMenu.items(updater: updater, quit: { quit += 1 }) })
        let titles = c.makeMenu().items.filter { !$0.isSeparatorItem }.map(\.title)
        XCTAssertEqual(titles, ["About jusage", "Check for Updates…", "Quick Keys", "Send Report…", "jusage Manual",
                                "What's New", "Run Doctor", "Licenses", "Uninstall jusage…", "Quit jusage"],
                       "every house item of R-169, in order")
        let menu = c.makeMenu()
        let q = menu.items.first { $0.title == "Quit jusage" }!
        _ = (q.target as AnyObject).perform(q.action, with: q)
        XCTAssertEqual(quit, 1, "Quit is the host's own action")
    }

    func testRecreateWhenOffScreen() {
        let c = controller()
        c.render(items)
        XCTAssertFalse(c.ensureOnScreen(), "on a screen: nothing to do")
        XCTAssertEqual(maker.made.count, 1)

        maker.made[0].isOnScreen = false            // the macOS 26/27 item-loss bug
        XCTAssertTrue(c.ensureOnScreen())
        XCTAssertEqual(maker.made.count, 2, "a new status item")
        XCTAssertTrue(maker.made[0].removed, "the lost one is taken out")
        XCTAssertEqual(maker.made[1].title?.string, maker.made[0].title?.string, "the new item shows the same title at once")
        XCTAssertEqual(log.warns.count, 1, "the recreation is logged")
    }

    // MARK: more of S12

    func testTitleTagsNumbersAndTooltip() {
        let c = controller()
        c.render(items)
        let slot = maker.made[0]
        XCTAssertEqual(slot.title?.string, "1 37%  2 98%  12%")
        XCTAssertEqual(slot.tooltip, "a@x\nSession 37%\n\nb@x\nSession 98%\n\nc@x\nSession 12%")
        XCTAssertGreaterThan(slot.length, 0)
    }

    func testWidthFollowsTitleInOnePass() {
        let c = controller()
        c.render([TitleItem(text: "5%", tag: nil, colour: nil, stale: false, hover: "")])
        let narrow = maker.made[0].length
        c.render(items)
        XCTAssertGreaterThan(maker.made[0].length, narrow, "the width is set with the title, in the same call")
        XCTAssertEqual(maker.made[0].length, TitleRender.length(of: maker.made[0].title!))
    }

    func testColoursAndGrey() {
        let s = TitleRender.attributed(items)
        func colour(of text: String) -> NSColor? {
            let r = (s.string as NSString).range(of: text)
            return s.attribute(.foregroundColor, at: r.location, effectiveRange: nil) as? NSColor
        }
        XCTAssertEqual(colour(of: "37%"), TitleRender.colour(.green))
        XCTAssertEqual(colour(of: "98%"), TitleRender.colour(.red))
        XCTAssertEqual(colour(of: "12%"), TitleRender.grey, "a stale number is grey whatever its pace")
        XCTAssertEqual(colour(of: "1 "), TitleRender.tagColour)
    }

    func testRefusalShowsWarningMarkAndReason() {
        // Bring-up refusal: AppModel holds the line, TitleModel gives the mark, the item shows the reason on hover.
        let c = controller()
        let refusal = "jusage: the engine is missing from the app (Contents/Resources/engine/claude_usage.py). Reinstall jusage."
        c.render(TitleModel.make(feed: nil, settings: AppSettings(), now: Date(), runError: refusal))
        XCTAssertEqual(maker.made[0].title?.string, "⚠︎")
        XCTAssertEqual(maker.made[0].tooltip, refusal)
    }

    func testLeftClickTogglesRightClickMenu() {
        let c = controller(menu: { [NSMenuItem(title: "Quit jusage", action: nil, keyEquivalent: "")] })
        c.render(items)
        maker.made[0].click(false)
        XCTAssertEqual(leftClicks, 1)
        XCTAssertNil(maker.made[0].popped)
        maker.made[0].click(true)
        XCTAssertEqual(leftClicks, 1)
        XCTAssertEqual(maker.made[0].popped?.items.map(\.title), ["Quit jusage"])
    }

    func testAnchorComesFromTheLiveSlot() {
        let c = controller()
        XCTAssertEqual(c.anchor, maker.made[0].anchor)
        maker.made[0].anchor = nil
        XCTAssertNil(c.anchor)
    }

    func testPanelMenuMirrorsTheStatusItemMenu() {
        var fired: [String] = []
        let updater = AppUpdater(bundle: Bundle(for: StatusItemTests.self))
        let house = JusageHouseMenu.items(updater: updater, quit: { fired.append("quit") })
        let panel = StatusItemController.panelMenu(from: house)
        XCTAssertEqual(panel.map(\.title), house.filter { !$0.isSeparatorItem }.map(\.title), "same items, same order (R-169)")
        XCTAssertEqual(panel.filter(\.dividerBefore).map(\.title), ["Quick Keys", "jusage Manual", "Uninstall jusage…", "Quit jusage"])
        XCTAssertEqual(Set(panel.map(\.id)).count, panel.count, "ids are unique: \(panel.map(\.id))")
        panel.last!.action()
        XCTAssertEqual(fired, ["quit"], "the panel item runs the same action")
    }
}
