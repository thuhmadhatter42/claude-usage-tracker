import SwiftUI

/// What the panel's views need from outside the Views folder, handed in by the host (S13/S16). The views never
/// reach for a house-kit type or the engine directly, so they build and test on their own; the host wires:
///   log           the house-kit log (`AppLog.shared` conforms to `HouseLog` with no extra code)
///   shortcuts     `QuickKeys.shared.shortcut(for:)` for every `QuickKeyID`, re-published when the user rebinds
///   refresh       `Scheduler.request(.manual)`
///   houseMenu     the same items, in the same order, as the status item's right-click menu (S12, R-169)
///   engine        `FeedRunner.run` with the recorded Python, the bundled engine and the state dir
///   loginItem     `LoginItem` (S15)
///   accentPicker  the house kit's `AccentPickerRow()`
///   glow          the house kit's glow modifier (lit = true)
///   foldAnimationDone  `PanelController.replaceIfShown()` (R-64)

/// The house-kit log as the views use it. Every view-level failure goes through here (a feed that would not
/// decode, a setting the engine refused).
protocol HouseLog {
    func error(_ category: String, _ message: String)
}

/// Quick Keys row ids the panel uses. W3-C's `QuickKeyTable.swift` defines refresh, dashboard, previousMonth and
/// nextMonth; settings, quit and quickKeys are the kit's standard rows.
enum QuickKeyID {
    static let refresh = "refresh"
    static let dashboard = "dashboard"
    static let settings = "settings"
    static let previousMonth = "previousMonth"
    static let nextMonth = "nextMonth"
    static let quit = "quit"
    static let quickKeys = "quickKeys"
}

struct PanelMenuItem: Identifiable {
    let id: String
    let title: String
    /// A divider goes above this item.
    let dividerBefore: Bool
    let action: () -> Void

    init(id: String, title: String, dividerBefore: Bool = false, action: @escaping () -> Void) {
        self.id = id; self.title = title; self.dividerBefore = dividerBefore; self.action = action
    }
}

/// The system login item (S15): current state, and a change that returns the reason when it fails.
struct LoginItemControl {
    let isOn: () -> Bool
    let set: (Bool) -> String?
}

/// Runs one engine command (`config read-every 60`, `statusline on --folder <key>`).
typealias EngineCommand = ([String]) async -> RunResult

@MainActor
final class PanelServices: ObservableObject {
    /// Quick Keys id -> its current chord (nil = the user cleared it).
    @Published var shortcuts: [String: KeyboardShortcut]
    let log: HouseLog
    let refresh: () -> Void
    let openDashboard: (String) -> Void
    let houseMenu: [PanelMenuItem]
    let engine: EngineCommand
    let loginItem: LoginItemControl
    let accentPicker: () -> AnyView
    let glow: (AnyView, Bool) -> AnyView
    let quit: () -> Void
    let foldAnimationDone: () -> Void

    init(shortcuts: [String: KeyboardShortcut], log: HouseLog, refresh: @escaping () -> Void,
         openDashboard: @escaping (String) -> Void, houseMenu: [PanelMenuItem], engine: @escaping EngineCommand,
         loginItem: LoginItemControl, accentPicker: @escaping () -> AnyView,
         glow: @escaping (AnyView, Bool) -> AnyView, quit: @escaping () -> Void,
         foldAnimationDone: @escaping () -> Void) {
        self.shortcuts = shortcuts; self.log = log; self.refresh = refresh; self.openDashboard = openDashboard
        self.houseMenu = houseMenu; self.engine = engine; self.loginItem = loginItem; self.accentPicker = accentPicker
        self.glow = glow; self.quit = quit; self.foldAnimationDone = foldAnimationDone
    }

    func shortcut(_ id: String) -> KeyboardShortcut? { shortcuts[id] }
}

/// The display clock and the Mac's 12/24-hour setting, passed together to every model that prints a time.
struct DisplayTime {
    let now: Date
    let hour12: Bool
    let timeZone: TimeZone

    init(now: Date, hour12: Bool = Fmt.systemHour12, timeZone: TimeZone = .current) {
        self.now = now; self.hour12 = hour12; self.timeZone = timeZone
    }

    func clock(_ d: Date) -> String { Fmt.clock(d, now: now, hour12: hour12, timeZone: timeZone) }
    func age(of d: Date) -> TimeInterval { now.timeIntervalSince(d) }

    /// "Updated 23 min ago (14:05). <reason>" for a stale number, nil for a fresh one (R-49).
    func staleHover(_ f: Freshness, readAt: Date?) -> String? {
        f.hover(readAt: readAt, now: now, hour12: hour12, timeZone: timeZone)
    }
}
