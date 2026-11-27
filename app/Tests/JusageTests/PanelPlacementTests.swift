import AppKit
import XCTest
@testable import Jusage

/// S13: the panel, placed by us under the status item (R-60…R-64). Tests use a fake window: no real panel opens.
@MainActor
final class PanelPlacementTests: XCTestCase {
    let screen = NSRect(x: 0, y: 0, width: 1440, height: 875)          // visible frame (menu bar and Dock removed)

    // MARK: SCORE-named tests

    func testClampsInsideVisibleFrame() {
        // Item near the right edge: the panel slides left to stay 8 pt inside.
        let item = NSRect(x: 1400, y: 875, width: 40, height: 24)
        let (f, scrolls) = PanelPlacement.frame(anchor: PanelAnchor(item: item, visible: screen), width: 312, contentHeight: 400)
        XCTAssertEqual(f.maxX, screen.maxX - 8)
        XCTAssertEqual(f.maxY, item.minY - 6, "6 pt gap under the item")
        XCTAssertEqual(f.height, 400)
        XCTAssertFalse(scrolls)

        // Item near the left edge.
        let left = PanelPlacement.frame(anchor: PanelAnchor(item: NSRect(x: 2, y: 875, width: 30, height: 24), visible: screen),
                                        width: 312, contentHeight: 200).frame
        XCTAssertEqual(left.minX, 8)

        // In the middle: centred under the item.
        let mid = PanelPlacement.frame(anchor: PanelAnchor(item: NSRect(x: 700, y: 875, width: 40, height: 24), visible: screen),
                                       width: 312, contentHeight: 200).frame
        XCTAssertEqual(mid.midX, 720)

        // A second screen to the right: placed on THAT screen's visible frame.
        let second = NSRect(x: 1440, y: 0, width: 1920, height: 1055)
        let f2 = PanelPlacement.frame(anchor: PanelAnchor(item: NSRect(x: 3300, y: 1055, width: 50, height: 24), visible: second),
                                      width: 312, contentHeight: 200).frame
        XCTAssertTrue(second.contains(f2))
        XCTAssertGreaterThanOrEqual(f2.minX, second.minX + 8)
        XCTAssertLessThanOrEqual(f2.maxX, second.maxX - 8)
        XCTAssertEqual(f2.maxY, 1055 - 6)
    }

    func testTallContentCapsAndScrolls() {
        let item = NSRect(x: 700, y: 875, width: 40, height: 24)
        let (f, scrolls) = PanelPlacement.frame(anchor: PanelAnchor(item: item, visible: screen), width: 312, contentHeight: 2000)
        XCTAssertEqual(f.height, screen.height - 16, "capped at the visible height − 16")
        XCTAssertTrue(scrolls, "the rest scrolls")
        XCTAssertGreaterThanOrEqual(f.minY, screen.minY + 8)
    }

    func testClickWhileOpenCloses() {
        let (c, w, clock) = makeController()
        c.toggle()
        XCTAssertTrue(w.shown)
        XCTAssertEqual(w.presents, 1)

        // Clicking the item while open: the panel loses key first (and hides), then the click arrives.
        clock.now = clock.now.addingTimeInterval(1)
        c.didResignKey()
        XCTAssertFalse(w.shown)
        clock.now = clock.now.addingTimeInterval(0.05)
        c.toggle()
        XCTAssertFalse(w.shown, "the click that caused the hide does not reopen it")
        XCTAssertEqual(w.presents, 1, "no flicker: never shown again in between")

        // A later click opens it again.
        clock.now = clock.now.addingTimeInterval(0.5)
        c.toggle()
        XCTAssertTrue(w.shown)
    }

    // MARK: more of S13

    func testToggleWhileShownHides() {
        let (c, w, _) = makeController()
        c.toggle(); c.toggle()
        XCTAssertFalse(w.shown)
    }

    func testShowActivatesPlacesAndAsksForARun() {
        let (c, w, _) = makeController()
        c.show()
        XCTAssertEqual(activations, 1, "showing activates the app so hover works (R-62)")
        XCTAssertEqual(opens, 1, "panel open asks the scheduler for a run")
        XCTAssertEqual(w.placed?.frame.width, 312)
        XCTAssertTrue(w.tooltipsWhenInactive, "tooltips show even when the app is inactive")
    }

    func testSheetKeepsPanelOpen() {
        let (c, w, _) = makeController()
        c.show()
        w.hasSheet = true                          // a Report/Doctor/Quick Keys sheet took key
        c.didResignKey()
        XCTAssertTrue(w.shown)
    }

    func testReplaceIfShownOnlyWhenShown() {
        let (c, w, _) = makeController()
        c.replaceIfShown()
        XCTAssertNil(w.placed)
        c.show()
        w.contentHeight = 500
        c.replaceIfShown()
        XCTAssertEqual(w.placed?.frame.height, 500, "re-placed for the new content height (fold animation end)")
    }

    func testNoScreenRecoversThenRefusesAndLogs() {
        anchorValue = nil
        let (c, w, _) = makeController()
        c.show()
        XCTAssertEqual(recovers, 1, "the status item is recreated once")
        XCTAssertFalse(w.shown, "never shown off-screen")
        XCTAssertEqual(log.errors, ["[panel] the status item is on no screen; the panel was not shown"])
    }

    func testNoScreenRecoveredShows() {
        anchorValue = nil
        let (c, w, _) = makeController()
        recoverAction = { [unowned self] in self.anchorValue = PanelAnchor(item: NSRect(x: 700, y: 875, width: 40, height: 24), visible: self.screen) }
        c.show()
        XCTAssertTrue(w.shown)
        XCTAssertTrue(log.errors.isEmpty)
    }

    // MARK: fixtures

    final class FakeWindow: PanelWindow {
        var shown = false
        var hasSheet = false
        var contentHeight: CGFloat = 300
        var contentWidth: CGFloat = 312
        var tooltipsWhenInactive = false
        private(set) var presents = 0
        private(set) var placed: (frame: NSRect, scrolls: Bool)?
        var isShown: Bool { shown }
        func place(frame: NSRect, scrolls: Bool) { placed = (frame, scrolls) }
        func present() { shown = true; presents += 1; tooltipsWhenInactive = true }
        func dismiss() { shown = false }
    }

    final class Clock { var now = Date(timeIntervalSince1970: 1_800_000_000) }

    var activations = 0, opens = 0, recovers = 0
    var log = RecordingLog()
    lazy var anchorValue: PanelAnchor? = PanelAnchor(item: NSRect(x: 700, y: 875, width: 40, height: 24), visible: screen)
    var recoverAction: () -> Void = {}

    func makeController() -> (PanelController, FakeWindow, Clock) {
        let w = FakeWindow(), clock = Clock()
        let c = PanelController(window: w, anchor: { [unowned self] in self.anchorValue },
                                recoverAnchor: { [unowned self] in self.recovers += 1; self.recoverAction() },
                                activate: { [unowned self] in self.activations += 1 },
                                onShow: { [unowned self] in self.opens += 1 },
                                log: log, now: { clock.now })
        return (c, w, clock)
    }
}
