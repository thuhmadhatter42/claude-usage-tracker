import AppKit
import Carbon.HIToolbox
import SwiftUI

// QUICK KEYS (⇧⌃K): every shortcut the app has, in one table, listed in one window, each one rebindable by
// clicking it and pressing the new keys. Generalised from Artemis (app/src/shell/shell.m "Quick Keys: ONE table",
// app/src/faceplate/faceplate-quickkeys.js). The rules that came with it:
//   - ONE table. A shortcut exists only as a row; menus and key handlers read their chord from the row, so a
//     rebind moves them all and there is no second copy to drift.
//   - A chord macOS itself holds on this Mac (read from the OS, never a hard-coded list) cannot be assigned.
//   - A chord another row uses is a clash: Cancel, or Reassign (the other row is left unassigned).
//   - Locked rows (Quit, Copy, ...) are shown so the list is complete, and can never be changed or taken.
// The app's own rows live in QuickKeyTable.swift (the per-app file). This file is the same in every app.

/// A key + modifiers. `key` is lower case: a letter or digit by its physical cap ("k", "3"), punctuation by the
/// character the layout types (","), or a name: delete, return, tab, space, escape, left, right, up, down, f1...f12.
/// An empty key = unassigned.
struct QuickChord: Hashable, Sendable {
    var key: String
    var mods: NSEvent.ModifierFlags

    /// The only modifier bits a chord carries.
    static let mask: NSEvent.ModifierFlags = [.shift, .control, .option, .command]
    static let none = QuickChord(key: "", mods: [])

    init(key: String, mods: NSEvent.ModifierFlags) {
        self.key = key.lowercased()
        self.mods = mods.intersection(QuickChord.mask)
    }

    func hash(into h: inout Hasher) { h.combine(key); h.combine(mods.rawValue) }
    static func == (a: QuickChord, b: QuickChord) -> Bool { a.key == b.key && a.mods.rawValue == b.mods.rawValue }

    var isAssigned: Bool { !key.isEmpty }

    /// "⌃⇧K", in the order macOS menus print modifiers. "Unassigned" when there is no key.
    var display: String {
        guard isAssigned else { return QuickKeys.unassigned }
        var d = ""
        if mods.contains(.control) { d += "⌃" }
        if mods.contains(.option) { d += "⌥" }
        if mods.contains(.shift) { d += "⇧" }
        if mods.contains(.command) { d += "⌘" }
        switch key {
        case "delete": d += "⌫"
        case "return": d += "↩"
        case "tab": d += "⇥"
        case "space": d += "Space"
        case "escape": d += "⎋"
        case "left": d += "←"
        case "right": d += "→"
        case "up": d += "↑"
        case "down": d += "↓"
        default: d += key.uppercased()
        }
        return d
    }

    /// For a SwiftUI menu item or button; nil when unassigned or the key has no SwiftUI equivalent.
    var keyboardShortcut: KeyboardShortcut? {
        guard isAssigned else { return nil }
        let eq: KeyEquivalent
        switch key {
        case "delete": eq = .delete
        case "return": eq = .return
        case "tab": eq = .tab
        case "space": eq = .space
        case "escape": eq = .escape
        case "left": eq = .leftArrow
        case "right": eq = .rightArrow
        case "up": eq = .upArrow
        case "down": eq = .downArrow
        default:
            guard key.count == 1, let c = key.first else { return nil }
            eq = KeyEquivalent(c)
        }
        var m: SwiftUI.EventModifiers = []
        if mods.contains(.shift) { m.insert(.shift) }
        if mods.contains(.control) { m.insert(.control) }
        if mods.contains(.option) { m.insert(.option) }
        if mods.contains(.command) { m.insert(.command) }
        return KeyboardShortcut(eq, modifiers: m)
    }

    /// For an AppKit NSMenuItem.keyEquivalent.
    var menuKeyEquivalent: String {
        switch key {
        case "delete": return "\u{8}"
        case "return": return "\r"
        case "tab": return "\t"
        case "space": return " "
        case "escape": return "\u{1b}"
        default: return key.count == 1 ? key : ""
        }
    }
}

/// One row of the table.
struct QuickKeyDef: Identifiable, Hashable, Sendable {
    enum Kind: String, Sendable {
        /// A menu command. Rebindable. Needs Command, Control or Option (a bare or Shift-only key would be typed
        /// into a text field instead).
        case menu
        /// A key the window handles itself (not a menu item). Rebindable. Needs at least one modifier.
        case key
        /// Belongs to macOS or to every Mac app (Quit, Copy, Settings). Shown, never changed, never taken.
        case locked
    }

    let id: String
    let title: String
    let group: String
    let kind: Kind
    let chord: QuickChord

    init(_ id: String, _ title: String, group: String, kind: Kind = .menu, key: String, mods: NSEvent.ModifierFlags) {
        self.id = id; self.title = title; self.group = group; self.kind = kind
        self.chord = QuickChord(key: key, mods: mods)
    }
}

/// A row as the window shows it: the definition plus the chord in force.
struct QuickKeyRow: Identifiable, Hashable {
    let def: QuickKeyDef
    let chord: QuickChord
    /// The user changed it from the default.
    let custom: Bool
    var id: String { def.id }
    var locked: Bool { def.kind == .locked }
}

/// The answer to a rebind.
enum QuickKeyResult: Equatable {
    case ok
    /// Not allowed; the sentence says why.
    case refused(String)
    /// Another row has that chord. Offer Cancel / Reassign.
    case clash(id: String, title: String, reason: String)
}

@MainActor
final class QuickKeys: ObservableObject {
    static let storageKey = "quickKeys"
    nonisolated static let unassigned = "Unassigned"
    /// The row that opens this window. Every app has it: ⇧⌃K.
    nonisolated static let quickKeysID = "quickKeys"

    /// The rows every app has, whatever it does. An app's table = its own rows + these.
    nonisolated static let standardRows: [QuickKeyDef] = [
        QuickKeyDef(quickKeysID, "Quick Keys", group: "App", key: "k", mods: [.control, .shift]),
        QuickKeyDef("settings", "Settings", group: "App", kind: .locked, key: ",", mods: .command),
        QuickKeyDef("hide", "Hide", group: "App", kind: .locked, key: "h", mods: .command),
        QuickKeyDef("hideOthers", "Hide Others", group: "App", kind: .locked, key: "h", mods: [.command, .option]),
        QuickKeyDef("quit", "Quit", group: "App", kind: .locked, key: "q", mods: .command),
        QuickKeyDef("undo", "Undo", group: "Edit", kind: .locked, key: "z", mods: .command),
        QuickKeyDef("redo", "Redo", group: "Edit", kind: .locked, key: "z", mods: [.command, .shift]),
        QuickKeyDef("cut", "Cut", group: "Edit", kind: .locked, key: "x", mods: .command),
        QuickKeyDef("copy", "Copy", group: "Edit", kind: .locked, key: "c", mods: .command),
        QuickKeyDef("paste", "Paste", group: "Edit", kind: .locked, key: "v", mods: .command),
        QuickKeyDef("selectAll", "Select All", group: "Edit", kind: .locked, key: "a", mods: .command),
        QuickKeyDef("minimize", "Minimize", group: "Window", kind: .locked, key: "m", mods: .command),
        QuickKeyDef("closeWindow", "Close Window", group: "Window", kind: .locked, key: "w", mods: .command),
    ]

    /// The app's one table: its own rows (QuickKeyTable.swift) + the standard rows.
    static let shared = QuickKeys(defs: QuickKeys.appRows + QuickKeys.standardRows)

    let defs: [QuickKeyDef]
    /// The user's changes: id -> chord (an unassigned chord = the user cleared it).
    @Published private(set) var overrides: [String: QuickChord] = [:]
    /// The window is open.
    @Published var windowOpen = false
    /// The row waiting for a chord.
    @Published private(set) var listening: String?
    /// A refused chord's sentence, shown in the window until the next action.
    @Published private(set) var message: String?
    /// A chord that clashed, waiting for Cancel / Reassign.
    @Published private(set) var pendingClash: (id: String, chord: QuickChord, otherTitle: String, reason: String)?

    private let defaults: UserDefaults
    private let osReserved: () -> [QuickChord]
    private var monitor: Any?

    init(defs: [QuickKeyDef], defaults: UserDefaults = .standard,
         osReserved: @escaping () -> [QuickChord] = { QuickKeys.macOSReservedChords() }) {
        self.defaults = defaults
        self.osReserved = osReserved
        // A table with the same id twice is a programming error: keep the first, log the rest.
        var seen = Set<String>(), unique: [QuickKeyDef] = []
        for d in defs {
            if seen.insert(d.id).inserted { unique.append(d) }
            else { AppLog.shared.error("quickkeys", "row id '\(d.id)' is in the table twice; the second is ignored") }
        }
        self.defs = unique
        self.overrides = QuickKeys.load(defaults, known: seen)
        for p in QuickKeys.problems(unique) { AppLog.shared.error("quickkeys", "table: \(p)") }
    }

    // MARK: reading

    /// Defaults that collide (two rows on one chord) or rebindable rows whose default breaks the modifier rule.
    /// Logged at launch; QuickKeysTests fails on any.
    nonisolated static func problems(_ defs: [QuickKeyDef]) -> [String] {
        var out: [String] = [], byChord: [QuickChord: String] = [:]
        for d in defs where d.chord.isAssigned {
            if let other = byChord[d.chord] { out.append("\(d.id) and \(other) both default to \(d.chord.display)") }
            else { byChord[d.chord] = d.id }
            if d.kind == .menu && d.chord.mods.isDisjoint(with: [.command, .control, .option]) {
                out.append("\(d.id): a menu shortcut needs Command, Control or Option")
            }
            if d.kind == .key && d.chord.mods.isEmpty { out.append("\(d.id): a shortcut needs a modifier key") }
        }
        return out
    }

    func def(_ id: String) -> QuickKeyDef? { defs.first { $0.id == id } }

    /// The chord in force for a row: the user's override, else the default.
    func chord(for id: String) -> QuickChord {
        guard let d = def(id) else { return .none }
        if d.kind != .locked, let o = overrides[id] { return o }
        return d.chord
    }

    var rows: [QuickKeyRow] {
        defs.map { d in
            let c = chord(for: d.id)
            return QuickKeyRow(def: d, chord: c, custom: d.kind != .locked && overrides[d.id] != nil)
        }
    }

    /// The groups in table order, each with its rows.
    var groups: [(name: String, rows: [QuickKeyRow])] {
        var order: [String] = [], by: [String: [QuickKeyRow]] = [:]
        for r in rows {
            if by[r.def.group] == nil { order.append(r.def.group) }
            by[r.def.group, default: []].append(r)
        }
        return order.map { ($0, by[$0] ?? []) }
    }

    /// The SwiftUI shortcut for a row (nil = unassigned): `.keyboardShortcut(keys.shortcut(for: "export"))`.
    func shortcut(for id: String) -> KeyboardShortcut? { chord(for: id).keyboardShortcut }

    /// "⌃⇧K" for a tooltip or a label, read from the row so it is never typed twice (Board Talk rule 7).
    func display(for id: String) -> String { chord(for: id).display }

    /// Does this key event fire that row? For `.key` rows the window handles itself.
    func matches(_ event: NSEvent, _ id: String) -> Bool {
        let c = chord(for: id)
        return c.isAssigned && QuickKeys.chord(from: event) == c
    }

    // MARK: changing

    /// Rebind a row. `reassign: true` takes the chord from the row that has it (that row is left unassigned).
    @discardableResult
    func set(_ id: String, to chord: QuickChord, reassign: Bool = false) -> QuickKeyResult {
        guard let d = def(id) else { return .refused("There is no such shortcut.") }
        if d.kind == .locked { return .refused("That shortcut belongs to macOS and cannot be changed.") }
        guard chord.isAssigned else { return .refused("Press a key.") }
        if osReserved().contains(chord) {
            return .refused("\(chord.display) is a macOS shortcut on this Mac and cannot be assigned.")
        }
        if d.kind == .menu && chord.mods.isDisjoint(with: [.command, .control, .option]) {
            return .refused("A menu shortcut needs Command, Control or Option.")
        }
        if chord.mods.isEmpty { return .refused("A shortcut needs a modifier key.") }
        var next = overrides
        for o in defs where o.id != id && self.chord(for: o.id) == chord {
            if o.kind == .locked {
                return .clash(id: o.id, title: o.title, reason: "\(o.title) is a macOS shortcut and cannot be reassigned.")
            }
            if !reassign {
                return .clash(id: o.id, title: o.title, reason: "\(o.title) already uses that shortcut.")
            }
            next[o.id] = QuickChord.none                       // the other action is left unassigned
            AppLog.shared.info("quickkeys", "\(o.id) lost its shortcut to \(id)")
        }
        next[id] = chord
        overrides = next
        save()
        AppLog.shared.info("quickkeys", "\(id) is now \(chord.display)")
        return .ok
    }

    /// Leave a row without a shortcut.
    func clear(_ id: String) {
        guard let d = def(id), d.kind != .locked else { return }
        overrides[id] = QuickChord.none
        save()
        AppLog.shared.info("quickkeys", "\(id) is now unassigned")
    }

    /// Every shortcut back to its default.
    func resetAll() {
        overrides = [:]
        defaults.removeObject(forKey: QuickKeys.storageKey)
        message = nil; pendingClash = nil
        AppLog.shared.info("quickkeys", "every shortcut back to its default")
    }

    // MARK: the window's listening state

    /// A chip was clicked: the next key press is that row's new chord. While listening, every key press is
    /// swallowed before the menus see it (so pressing ⌘Q to assign it does not quit the app).
    func listen(_ id: String) {
        guard let d = def(id), d.kind != .locked else { return }
        message = nil; pendingClash = nil
        listening = id
        if monitor == nil {
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                // local monitors are called on the main thread
                let chord = QuickKeys.chord(from: event)
                let swallowed = MainActor.assumeIsolated { self?.heard(chord) ?? false }
                return swallowed ? nil : event
            }
        }
    }

    func stopListening() {
        listening = nil
        if let m = monitor { NSEvent.removeMonitor(m); monitor = nil }
    }

    /// A key press while the window is up. True = swallowed (a row was listening).
    private func heard(_ chord: QuickChord) -> Bool {
        guard let id = listening else { return false }
        if chord.key == "escape" && chord.mods.isEmpty { stopListening(); return true }
        offer(id, chord)
        return true
    }

    /// A chord arrives for a listening row (from the key monitor; tests call it directly).
    func offer(_ id: String, _ chord: QuickChord) {
        stopListening()
        switch set(id, to: chord) {
        case .ok: message = nil; pendingClash = nil
        case .refused(let why): message = why; pendingClash = nil
        case .clash(let otherID, let title, let reason):
            if def(otherID)?.kind == .locked { message = reason; pendingClash = nil }
            else { pendingClash = (id, chord, title, reason); message = nil }
        }
    }

    func cancelClash() { pendingClash = nil }

    func reassignClash() {
        guard let p = pendingClash else { return }
        pendingClash = nil
        if case .refused(let why) = set(p.id, to: p.chord, reassign: true) { message = why }
    }

    /// Open the window (the ⇧⌃K menu item).
    func open() { message = nil; pendingClash = nil; windowOpen = true }

    func close() { stopListening(); message = nil; pendingClash = nil; windowOpen = false }

    // MARK: storage

    private func save() {
        var out: [String: [String: Any]] = [:]
        for (id, c) in overrides { out[id] = ["key": c.key, "mods": Int(c.mods.rawValue)] }
        defaults.set(out, forKey: QuickKeys.storageKey)
        if defaults.dictionary(forKey: QuickKeys.storageKey)?.count != out.count {
            AppLog.shared.error("quickkeys", "save failed: \(QuickKeys.storageKey) did not read back")
        }
    }

    static func load(_ d: UserDefaults, known: Set<String>) -> [String: QuickChord] {
        guard let raw = d.object(forKey: storageKey) else { return [:] }
        guard let dict = raw as? [String: Any] else {
            AppLog.shared.error("quickkeys", "stored \(storageKey) unreadable, using the defaults")
            return [:]
        }
        var out: [String: QuickChord] = [:]
        for (id, v) in dict {
            guard known.contains(id) else { continue }          // a row a newer or older build had
            guard let e = v as? [String: Any], let key = e["key"] as? String, let mods = e["mods"] as? Int, mods >= 0 else {
                AppLog.shared.error("quickkeys", "stored shortcut for \(id) unreadable, using its default")
                continue
            }
            out[id] = QuickChord(key: key, mods: NSEvent.ModifierFlags(rawValue: UInt(mods)))
        }
        return out
    }

    // MARK: keys

    /// The chord a key event is.
    nonisolated static func chord(from event: NSEvent) -> QuickChord {
        let key = keyName(keyCode: event.keyCode) ?? (event.charactersIgnoringModifiers ?? "").lowercased()
        return QuickChord(key: key, mods: event.modifierFlags)
    }

    nonisolated private static let namedKeys: [Int: String] = [
        kVK_Delete: "delete", kVK_Return: "return", kVK_ANSI_KeypadEnter: "return", kVK_Tab: "tab", kVK_Space: "space",
        kVK_Escape: "escape", kVK_LeftArrow: "left", kVK_RightArrow: "right", kVK_UpArrow: "up", kVK_DownArrow: "down",
        kVK_F1: "f1", kVK_F2: "f2", kVK_F3: "f3", kVK_F4: "f4", kVK_F5: "f5", kVK_F6: "f6", kVK_F7: "f7", kVK_F8: "f8",
        kVK_F9: "f9", kVK_F10: "f10", kVK_F11: "f11", kVK_F12: "f12",
        // A LETTER OR DIGIT BY THE CAP'S PHYSICAL NAME: with Option held a letter types another character ("ß"),
        // and ⇧⌘3 on an AZERTY Mac must be the same chord the OS reports.
        kVK_ANSI_A: "a", kVK_ANSI_B: "b", kVK_ANSI_C: "c", kVK_ANSI_D: "d", kVK_ANSI_E: "e", kVK_ANSI_F: "f",
        kVK_ANSI_G: "g", kVK_ANSI_H: "h", kVK_ANSI_I: "i", kVK_ANSI_J: "j", kVK_ANSI_K: "k", kVK_ANSI_L: "l",
        kVK_ANSI_M: "m", kVK_ANSI_N: "n", kVK_ANSI_O: "o", kVK_ANSI_P: "p", kVK_ANSI_Q: "q", kVK_ANSI_R: "r",
        kVK_ANSI_S: "s", kVK_ANSI_T: "t", kVK_ANSI_U: "u", kVK_ANSI_V: "v", kVK_ANSI_W: "w", kVK_ANSI_X: "x",
        kVK_ANSI_Y: "y", kVK_ANSI_Z: "z",
        kVK_ANSI_0: "0", kVK_ANSI_1: "1", kVK_ANSI_2: "2", kVK_ANSI_3: "3", kVK_ANSI_4: "4", kVK_ANSI_5: "5",
        kVK_ANSI_6: "6", kVK_ANSI_7: "7", kVK_ANSI_8: "8", kVK_ANSI_9: "9",
    ]

    /// A virtual key code spelled the way a chord spells a key; punctuation through the current keyboard layout
    /// (a Text Input Sources read: call it on the main thread).
    nonisolated static func keyName(keyCode: UInt16) -> String? {
        if let n = namedKeys[Int(keyCode)] { return n }
        guard let src = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
              let ptr = TISGetInputSourceProperty(src, kTISPropertyUnicodeKeyLayoutData) else { return nil }
        let data = Unmanaged<CFData>.fromOpaque(ptr).takeUnretainedValue() as Data
        var dead: UInt32 = 0
        var chars = [UniChar](repeating: 0, count: 4)
        var n = 0
        let status: OSStatus = data.withUnsafeBytes { raw in
            guard let layout = raw.bindMemory(to: UCKeyboardLayout.self).baseAddress else { return OSStatus(paramErr) }
            return UCKeyTranslate(layout, keyCode, UInt16(kUCKeyActionDisplay), 0, UInt32(LMGetKbdType()),
                                  OptionBits(kUCKeyTranslateNoDeadKeysMask), &dead, chars.count, &n, &chars)
        }
        guard status == noErr, n > 0 else { return nil }
        let s = String(utf16CodeUnits: chars, count: n).lowercased()
        return s.trimmingCharacters(in: .controlCharacters).isEmpty ? nil : s
    }

    /// THE OS'S OWN SHORTCUTS, READ FROM THE OS: every enabled symbolic hotkey on this Mac (Spotlight, Mission
    /// Control, screenshots, input-source switching, whatever the user set in System Settings > Keyboard).
    /// Call it on the main thread (it reads the keyboard layout).
    nonisolated static func macOSReservedChords() -> [QuickChord] {
        var list: Unmanaged<CFArray>?
        guard CopySymbolicHotKeys(&list) == noErr, let array = list?.takeRetainedValue() as? [[String: Any]] else {
            AppLog.shared.error("quickkeys", "macOS did not hand over its own shortcuts (CopySymbolicHotKeys failed); no chord is refused as the system's")
            return []
        }
        var out: [QuickChord] = []
        for hk in array {
            guard (hk["kHISymbolicHotKeyEnabled"] as? Bool) == true,
                  let code = hk["kHISymbolicHotKeyCode"] as? Int, code >= 0, code <= Int(UInt16.max),
                  let carbon = hk["kHISymbolicHotKeyModifiers"] as? Int,
                  let key = keyName(keyCode: UInt16(code)) else { continue }
            var mods: NSEvent.ModifierFlags = []
            if carbon & cmdKey != 0 { mods.insert(.command) }
            if carbon & shiftKey != 0 { mods.insert(.shift) }
            if carbon & optionKey != 0 { mods.insert(.option) }
            if carbon & controlKey != 0 { mods.insert(.control) }
            out.append(QuickChord(key: key, mods: mods))
        }
        return out
    }

    // MARK: AppKit menus

    /// For an AppKit menu bar: every NSMenuItem whose identifier is "qk.<row id>" takes its row's chord.
    /// Call after building the menus and after every change (observe `objectWillChange`).
    func apply(to menu: NSMenu) {
        for item in menu.items {
            if let ident = item.identifier?.rawValue, ident.hasPrefix("qk.") {
                let c = chord(for: String(ident.dropFirst(3)))
                item.keyEquivalent = c.menuKeyEquivalent
                item.keyEquivalentModifierMask = c.mods
            }
            if let sub = item.submenu { apply(to: sub) }
        }
    }
}
