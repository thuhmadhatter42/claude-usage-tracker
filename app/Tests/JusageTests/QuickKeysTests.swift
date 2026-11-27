import AppKit
import SwiftUI
import XCTest
@testable import Jusage   // CHANGE: the app's module name

@MainActor
final class QuickKeysTests: XCTestCase {
    private var suite: String!
    private var defaults: UserDefaults!

    private let table: [QuickKeyDef] = [
        QuickKeyDef("export", "Export…", group: "File", key: "e", mods: [.command, .shift]),
        QuickKeyDef("newItem", "New Item", group: "File", key: "n", mods: .command),
        QuickKeyDef("solo", "Solo Selected", group: "Main Window", kind: .key, key: "s", mods: .shift),
    ] + QuickKeys.standardRows

    override func setUp() async throws {
        suite = "QuickKeysTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() async throws { defaults.removePersistentDomain(forName: suite) }

    /// A table with nothing reserved by macOS unless the test says so: the tests never depend on this Mac's settings.
    private func make(reserved: [QuickChord] = []) -> QuickKeys {
        QuickKeys(defs: table, defaults: defaults, osReserved: { reserved })
    }

    // MARK: the table

    func testTheAppsRealTableHasNoProblems() {
        XCTAssertEqual(QuickKeys.problems(QuickKeys.appRows + QuickKeys.standardRows), [], "fix QuickKeyTable.swift")
        let ids = (QuickKeys.appRows + QuickKeys.standardRows).map(\.id)
        XCTAssertEqual(Set(ids).count, ids.count, "row ids are unique")
    }

    func testQuickKeysItselfIsShiftControlK() {
        let k = make()
        XCTAssertEqual(k.chord(for: QuickKeys.quickKeysID), QuickChord(key: "k", mods: [.control, .shift]))
        XCTAssertEqual(k.display(for: QuickKeys.quickKeysID), "⌃⇧K")
    }

    func testProblemsCatchesTwoRowsOnOneChordAndAMenuRowWithoutAModifier() {
        let bad = [QuickKeyDef("a", "A", group: "G", key: "k", mods: .command),
                   QuickKeyDef("b", "B", group: "G", key: "k", mods: .command),
                   QuickKeyDef("c", "C", group: "G", key: "j", mods: .shift)]
        let p = QuickKeys.problems(bad)
        XCTAssertEqual(p.count, 2, "\(p)")
        XCTAssertTrue(p[0].contains("both default to ⌘K"), p[0])
        XCTAssertTrue(p[1].contains("needs Command, Control or Option"), p[1])
    }

    func testDisplayStrings() {
        XCTAssertEqual(QuickChord(key: "k", mods: [.control, .shift]).display, "⌃⇧K")
        XCTAssertEqual(QuickChord(key: "s", mods: [.command, .option, .shift, .control]).display, "⌃⌥⇧⌘S")
        XCTAssertEqual(QuickChord(key: "delete", mods: .command).display, "⌘⌫")
        XCTAssertEqual(QuickChord(key: "space", mods: .option).display, "⌥Space")
        XCTAssertEqual(QuickChord.none.display, "Unassigned")
        XCTAssertEqual(QuickChord(key: "K", mods: [.command, .capsLock]), QuickChord(key: "k", mods: .command), "lower case, four modifier bits only")
    }

    func testGroupsKeepTableOrder() {
        XCTAssertEqual(make().groups.map(\.name), ["File", "Main Window", "App", "Edit", "Window"])
    }

    // MARK: rebinding

    func testRebindSavesAndSurvivesARelaunch() {
        let k = make()
        XCTAssertEqual(k.set("export", to: QuickChord(key: "x", mods: [.command, .option])), .ok)
        XCTAssertEqual(k.display(for: "export"), "⌥⌘X")
        XCTAssertTrue(k.rows.first { $0.id == "export" }!.custom)
        let next = make()                                   // the next launch
        XCTAssertEqual(next.chord(for: "export"), QuickChord(key: "x", mods: [.command, .option]))
        XCTAssertEqual(next.chord(for: "newItem"), QuickChord(key: "n", mods: .command), "untouched rows keep their default")
    }

    func testAClashIsRefusedUntilReassigned() {
        let k = make()
        let taken = QuickChord(key: "n", mods: .command)
        XCTAssertEqual(k.set("export", to: taken),
                       .clash(id: "newItem", title: "New Item", reason: "New Item already uses that shortcut."))
        XCTAssertEqual(k.display(for: "export"), "⇧⌘E", "nothing changed")
        XCTAssertEqual(k.set("export", to: taken, reassign: true), .ok)
        XCTAssertEqual(k.chord(for: "export"), taken)
        XCTAssertFalse(k.chord(for: "newItem").isAssigned, "the other action is left unassigned")
        XCTAssertEqual(k.display(for: "newItem"), "Unassigned")
    }

    func testALockedRowIsNeverChangedAndNeverTaken() {
        let k = make()
        XCTAssertEqual(k.set("quit", to: QuickChord(key: "y", mods: .command)),
                       .refused("That shortcut belongs to macOS and cannot be changed."))
        let quit = QuickChord(key: "q", mods: .command)
        XCTAssertEqual(k.set("export", to: quit, reassign: true),
                       .clash(id: "quit", title: "Quit", reason: "Quit is a macOS shortcut and cannot be reassigned."))
        XCTAssertEqual(k.chord(for: "quit"), quit)
        k.clear("quit")
        XCTAssertEqual(k.chord(for: "quit"), quit, "clear does nothing to a locked row")
    }

    func testAChordMacOSHoldsOnThisMacIsRefused() {
        let spotlight = QuickChord(key: "space", mods: .command)
        let k = make(reserved: [spotlight])
        XCTAssertEqual(k.set("export", to: spotlight),
                       .refused("⌘Space is a macOS shortcut on this Mac and cannot be assigned."))
        XCTAssertEqual(k.set("export", to: QuickChord(key: "space", mods: [.command, .option])), .ok, "only the exact chord")
    }

    func testModifierRules() {
        let k = make()
        XCTAssertEqual(k.set("export", to: QuickChord(key: "e", mods: .shift)), .refused("A menu shortcut needs Command, Control or Option."))
        XCTAssertEqual(k.set("export", to: QuickChord(key: "e", mods: [])), .refused("A menu shortcut needs Command, Control or Option."))
        XCTAssertEqual(k.set("solo", to: QuickChord(key: "d", mods: [])), .refused("A shortcut needs a modifier key."))
        XCTAssertEqual(k.set("solo", to: QuickChord(key: "d", mods: .shift)), .ok, "a window key may be Shift-only")
        XCTAssertEqual(k.set("export", to: .none), .refused("Press a key."))
        XCTAssertEqual(k.set("nope", to: QuickChord(key: "e", mods: .command)), .refused("There is no such shortcut."))
    }

    func testClearAndResetAll() {
        let k = make()
        k.clear("export")
        XCTAssertFalse(k.chord(for: "export").isAssigned)
        XCTAssertNil(k.shortcut(for: "export"), "an unassigned row gives its menu item no shortcut")
        XCTAssertFalse(make().chord(for: "export").isAssigned, "cleared survives a relaunch")
        k.resetAll()
        XCTAssertEqual(k.display(for: "export"), "⇧⌘E")
        XCTAssertNil(defaults.object(forKey: QuickKeys.storageKey))
    }

    func testAnUnreadableStoreFallsBackToDefaults() {
        defaults.set("garbage", forKey: QuickKeys.storageKey)
        XCTAssertEqual(make().display(for: "export"), "⇧⌘E")
        defaults.set(["export": ["key": 5, "mods": "x"], "gone": ["key": "g", "mods": 1 << 20]], forKey: QuickKeys.storageKey)
        let k = make()
        XCTAssertEqual(k.display(for: "export"), "⇧⌘E")
        XCTAssertTrue(k.overrides.isEmpty, "a bad entry and an unknown row are both dropped")
    }

    // MARK: the window's flow

    func testListenThenOfferRunsTheWholeFlow() {
        let k = make()
        k.listen("export")
        XCTAssertEqual(k.listening, "export")
        k.offer("export", QuickChord(key: "n", mods: .command))            // New Item has it
        XCTAssertNil(k.listening)
        XCTAssertEqual(k.pendingClash?.otherTitle, "New Item")
        k.cancelClash()
        XCTAssertNil(k.pendingClash)
        XCTAssertEqual(k.display(for: "export"), "⇧⌘E")
        k.offer("export", QuickChord(key: "n", mods: .command))
        k.reassignClash()
        XCTAssertEqual(k.display(for: "export"), "⌘N")
        XCTAssertEqual(k.display(for: "newItem"), "Unassigned")
        k.offer("newItem", QuickChord(key: "q", mods: .command))           // a locked row has it
        XCTAssertNil(k.pendingClash, "no Reassign offer against macOS")
        XCTAssertEqual(k.message, "Quit is a macOS shortcut and cannot be reassigned.")
        k.listen("quit")
        XCTAssertNil(k.listening, "a locked row never listens")
        k.stopListening()
    }

    // MARK: handing chords to the UI

    func testShortcutsForSwiftUIAndAppKit() {
        let k = make()
        let s = k.shortcut(for: "export")
        XCTAssertEqual(s?.key, KeyEquivalent("e"))
        XCTAssertEqual(s?.modifiers, [.command, .shift])
        XCTAssertEqual(QuickChord(key: "delete", mods: .command).keyboardShortcut?.key, .delete)
        XCTAssertNil(QuickChord(key: "f5", mods: .command).keyboardShortcut, "no SwiftUI equivalent: handled as a window key")

        let menu = NSMenu(), sub = NSMenu()
        let item = NSMenuItem(title: "Export…", action: nil, keyEquivalent: "")
        item.identifier = NSUserInterfaceItemIdentifier("qk.export")
        let parent = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        parent.submenu = sub; sub.addItem(item); menu.addItem(parent)
        k.apply(to: menu)
        XCTAssertEqual(item.keyEquivalent, "e")
        XCTAssertEqual(item.keyEquivalentModifierMask, [.command, .shift])
        k.clear("export"); k.apply(to: menu)
        XCTAssertEqual(item.keyEquivalent, "")
    }

    func testAKeyEventMatchesItsRow() throws {
        let k = make()
        // kVK_ANSI_S = 1
        let e = try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .shift, timestamp: 0, windowNumber: 0,
                                               context: nil, characters: "S", charactersIgnoringModifiers: "s", isARepeat: false, keyCode: 1))
        XCTAssertTrue(k.matches(e, "solo"))
        XCTAssertFalse(k.matches(e, "export"))
        k.clear("solo")
        XCTAssertFalse(k.matches(e, "solo"), "an unassigned row never fires")
    }

    func testKeyNamesByPhysicalCap() {
        XCTAssertEqual(QuickKeys.keyName(keyCode: 40), "k")      // kVK_ANSI_K
        XCTAssertEqual(QuickKeys.keyName(keyCode: 20), "3")      // kVK_ANSI_3
        XCTAssertEqual(QuickKeys.keyName(keyCode: 51), "delete") // kVK_Delete
        XCTAssertEqual(QuickKeys.keyName(keyCode: 49), "space")  // kVK_Space
    }

    /// The real read from macOS: it must not crash, and every chord it returns is well formed.
    func testTheRealMacOSReadIsWellFormed() {
        for c in QuickKeys.macOSReservedChords() {
            XCTAssertTrue(c.isAssigned)
            XCTAssertEqual(c.mods, c.mods.intersection(QuickChord.mask))
        }
    }

    func testTheWindowBuilds() {
        let k = make()
        render(QuickKeysView(keys: k))
        k.listen("export"); render(QuickKeysView(keys: k)); k.stopListening()
        k.offer("export", QuickChord(key: "n", mods: .command)); render(QuickKeysView(keys: k))
        render(QuickKeyButton("export", "Export…", keys: k) {})
    }

    /// Lay the view out in an off-screen hosting view (no window): its body really runs.
    private func render<V: View>(_ view: V) {
        let host = NSHostingView(rootView: view.accentRoot())
        host.frame = NSRect(x: 0, y: 0, width: 600, height: 600)
        host.layoutSubtreeIfNeeded()
    }
}
