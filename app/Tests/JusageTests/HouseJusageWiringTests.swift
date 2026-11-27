import XCTest
import AppKit
import SwiftUI
@testable import Jusage

/// W3-C: what the host (W3-A) takes from House: the status item's right-click menu items and the invisible
/// SwiftUI view that hosts the kit's sheets inside the panel.
@MainActor
final class HouseJusageWiringTests: XCTestCase {
    func testStatusItemMenuIsTheSpecsOrder() {
        let items = JusageHouseMenu.items(updater: AppUpdater(), quit: {})
        XCTAssertEqual(items.map { $0.isSeparatorItem ? "-" : $0.title }, [
            "About jusage", "Check for Updates…", "-",
            "Quick Keys", "Send Report…", "-",
            "jusage Manual", "What's New", "Run Doctor", "Licenses", "-",
            "Uninstall jusage…", "-", "Quit jusage",
        ])
    }

    func testEveryItemDoesSomething() {
        for i in JusageHouseMenu.items(updater: AppUpdater(), quit: {}) where !i.isSeparatorItem {
            XCTAssertNotNil(i.action, i.title)
            XCTAssertNotNil(i.target, i.title)
        }
    }

    func testActionsReachTheKitsCenters() {
        var quit = 0
        let items = JusageHouseMenu.items(updater: AppUpdater(), quit: { quit += 1 })
        func fire(_ t: String) { let i = items.first { $0.title == t }!; _ = i.target!.perform(i.action!, with: i) }
        UninstallCenter.shared.showing = false; ReportCenter.shared.showing = false; DoctorCenter.shared.showing = false
        fire("Uninstall jusage…"); XCTAssertTrue(UninstallCenter.shared.showing)
        fire("Send Report…"); XCTAssertTrue(ReportCenter.shared.showing)
        let savedChecks = HouseDoctor.appChecks; HouseDoctor.appChecks = []
        defer { HouseDoctor.appChecks = savedChecks; UninstallCenter.shared.showing = false; ReportCenter.shared.showing = false; DoctorCenter.shared.showing = false }
        fire("Run Doctor"); XCTAssertTrue(DoctorCenter.shared.showing)
        fire("Quit jusage"); XCTAssertEqual(quit, 1)
        QuickKeys.shared.windowOpen = false
        fire("Quick Keys"); XCTAssertTrue(QuickKeys.shared.windowOpen)
        QuickKeys.shared.windowOpen = false
    }

    /// The sheets hang off the panel window, so a right-click item must show the panel BEFORE it opens its sheet.
    func testSheetItemsShowThePanelBeforeOpeningTheirSheet() {
        var order: [String] = []
        let items = JusageHouseMenu.items(updater: AppUpdater(), quit: {}, presentSheet: { open in
            order.append("panel"); open()
        })
        func fire(_ t: String) { let i = items.first { $0.title == t }!; _ = i.target!.perform(i.action!, with: i) }
        let savedChecks = HouseDoctor.appChecks; HouseDoctor.appChecks = []
        defer {
            HouseDoctor.appChecks = savedChecks
            UninstallCenter.shared.showing = false; ReportCenter.shared.showing = false
            DoctorCenter.shared.showing = false; QuickKeys.shared.windowOpen = false
        }
        for t in ["Quick Keys", "Send Report…", "Run Doctor", "Uninstall jusage…"] {
            order = []
            UninstallCenter.shared.showing = false; ReportCenter.shared.showing = false
            DoctorCenter.shared.showing = false; QuickKeys.shared.windowOpen = false
            fire(t)
            XCTAssertEqual(order, ["panel"], "\(t) must present the panel first")
        }
        order = []
        fire("Jusage Manual".replacingOccurrences(of: "J", with: "j"))
        XCTAssertEqual(order, [], "items that open their own window do not need the panel")
    }

    func testQuickKeysItemCarriesItsChordFromTheTable() {
        let item = JusageHouseMenu.items(updater: AppUpdater(), quit: {}).first { $0.title == "Quick Keys" }!
        XCTAssertEqual(item.identifier?.rawValue, "qk.quickKeys")
        let menu = NSMenu(); menu.items = [item]
        QuickKeys.shared.apply(to: menu)
        XCTAssertEqual(item.keyEquivalent, "k")
        XCTAssertEqual(item.keyEquivalentModifierMask.intersection(QuickChord.mask), [.control, .shift])
    }

    func testSheetHostIsInvisibleAndHostsTheSheets() {
        let v = JusageHouse.sheetHost()
        XCTAssertEqual(v.frame.size, .zero)
        XCTAssertTrue(v is NSHostingView<AnyView>)
    }

    func testLaunchWiresLogTipsDoctorAndUninstall() {
        let savedChecks = HouseDoctor.appChecks, savedRunner = HouseUninstall.runner
        defer { HouseDoctor.appChecks = savedChecks; HouseUninstall.runner = savedRunner }
        var prepRan = false
        JusageHouse.launch(engineDoctor: { ["OK a: fine"] }, statusLineOff: { prepRan = true; return (0, "") }, loginItemOff: {},
                           home: FileManager.default.temporaryDirectory)
        XCTAssertEqual(HouseDoctor.appChecks.map(\.name), ["Engine", "Updates"])
        _ = HouseUninstall.runner(URL(fileURLWithPath: "/usr/bin/true"), [], false)
        XCTAssertTrue(prepRan, "the uninstall runner now restores the status line first")
    }
}
