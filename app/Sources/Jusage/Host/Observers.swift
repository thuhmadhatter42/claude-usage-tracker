import AppKit

/// S11: the system events the host listens to (R-149, R-150, B11).
///   wake from sleep, session became active   → `request(.wake / .sessionActive)` (a run now)
///   screens changed, active Space changed    → `layoutChanged()` (re-place an open panel, check the status item
///                                              is on a screen)
/// The app passes `NSWorkspace.shared.notificationCenter` and `NotificationCenter.default`; tests pass their own.
final class Observers {
    private let workspace: NotificationCenter
    private let app: NotificationCenter
    private let request: (Trigger) -> Void
    private let layoutChanged: () -> Void
    private var tokens: [(NotificationCenter, NSObjectProtocol)] = []

    init(workspace: NotificationCenter, app: NotificationCenter,
         request: @escaping (Trigger) -> Void, layoutChanged: @escaping () -> Void) {
        self.workspace = workspace; self.app = app; self.request = request; self.layoutChanged = layoutChanged
    }

    func start() {
        guard tokens.isEmpty else { return }
        func on(_ center: NotificationCenter, _ name: Notification.Name, _ act: @escaping () -> Void) {
            tokens.append((center, center.addObserver(forName: name, object: nil, queue: nil) { _ in act() }))
        }
        let request = self.request, layoutChanged = self.layoutChanged
        on(workspace, NSWorkspace.didWakeNotification) { request(.wake) }
        on(workspace, NSWorkspace.sessionDidBecomeActiveNotification) { request(.sessionActive) }
        on(app, NSApplication.didChangeScreenParametersNotification) { layoutChanged() }
        on(workspace, NSWorkspace.activeSpaceDidChangeNotification) { layoutChanged() }
    }

    func stop() {
        for (center, token) in tokens { center.removeObserver(token) }
        tokens = []
    }

    deinit { stop() }
}
