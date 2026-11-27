import AppKit
import SwiftUI

/// S13: the panel (R-60…R-64). An NSStatusItem plus a borderless panel we place ourselves: no popover, never
/// off-screen, MenuBarExtra not used.
///   - under the item on that item's own screen, 8 pt inside the visible frame, 6 pt gap; height capped at the
///     visible height − 16, the rest scrolls
///   - showing activates the app; the panel shows tooltips while the app is inactive (R-62)
///   - losing key hides it; the status-item click that caused that loss does not reopen it (150 ms debounce)
///   - `replaceIfShown()` re-places it (screens/Space changed, fold animation finished, content resized)

enum PanelPlacement {
    static let inset: CGFloat = 8
    static let gap: CGFloat = 6

    static func frame(anchor: PanelAnchor, width: CGFloat, contentHeight: CGFloat) -> (frame: NSRect, scrolls: Bool) {
        let v = anchor.visible
        let cap = v.height - 2 * inset
        let height = min(contentHeight, cap)
        var x = anchor.item.midX - width / 2
        x = min(max(x, v.minX + inset), v.maxX - inset - width)
        let top = min(anchor.item.minY - gap, v.maxY)   // the item sits in the menu bar, above the visible frame
        let y = max(top - height, v.minY + inset)
        return (NSRect(x: x, y: y, width: width, height: height), contentHeight > cap)
    }
}

/// The window the controller drives. The app's is `Panel`; tests use a fake.
protocol PanelWindow: AnyObject {
    var isShown: Bool { get }
    /// A kit sheet (Report, Doctor, Quick Keys, Uninstall) is up: it took key, the panel must stay.
    var hasSheet: Bool { get }
    var contentHeight: CGFloat { get }
    var contentWidth: CGFloat { get }
    func place(frame: NSRect, scrolls: Bool)
    /// Order front and make key, with tooltips allowed while inactive.
    func present()
    func dismiss()
}

@MainActor
final class PanelController {
    static let clickDebounce: TimeInterval = 0.15

    private let window: PanelWindow
    private let anchor: () -> PanelAnchor?
    private let recoverAnchor: () -> Void
    private let activate: () -> Void
    private let onShow: () -> Void
    private let log: HostLog
    private let now: () -> Date
    private var lastResignHide: Date?

    init(window: PanelWindow, anchor: @escaping () -> PanelAnchor?, recoverAnchor: @escaping () -> Void,
         activate: @escaping () -> Void, onShow: @escaping () -> Void, log: HostLog, now: @escaping () -> Date = Date.init) {
        self.window = window; self.anchor = anchor; self.recoverAnchor = recoverAnchor
        self.activate = activate; self.onShow = onShow; self.log = log; self.now = now
    }

    var isShown: Bool { window.isShown }

    /// The status item was clicked.
    func toggle() {
        if window.isShown { hide(); return }
        if let t = lastResignHide, now().timeIntervalSince(t) < PanelController.clickDebounce { return }
        show()
    }

    func show() {
        guard place() else { return }
        activate()
        window.present()
        onShow()
    }

    func hide() { window.dismiss() }

    /// The panel lost key: hide it, unless one of the kit's sheets took key.
    func didResignKey() {
        guard window.isShown, !window.hasSheet else { return }
        lastResignHide = now()
        hide()
    }

    func replaceIfShown() {
        guard window.isShown else { return }
        _ = place()
    }

    /// False (logged, panel not shown) when the status item is on no screen even after recreating it once.
    @discardableResult
    private func place() -> Bool {
        var a = anchor()
        if a == nil { recoverAnchor(); a = anchor() }
        guard let a else {
            log.error("panel", "the status item is on no screen; the panel was not shown")
            return false
        }
        let p = PanelPlacement.frame(anchor: a, width: window.contentWidth, contentHeight: window.contentHeight)
        window.place(frame: p.frame, scrolls: p.scrolls)
        return true
    }
}

/// The real panel: borderless, can become key (or shortcuts and focus never arrive, R-60), SwiftUI content in a
/// scroll view, plus the kit's invisible sheet host (RULINGS 41).
final class Panel: NSPanel, PanelWindow, NSWindowDelegate {
    private let hosting: NSHostingView<AnyView>
    private let scroll = NSScrollView()
    var onResignKey: () -> Void = {}

    init(root: AnyView, sheetHost: NSView) {
        hosting = NSHostingView(rootView: root)
        super.init(contentRect: NSRect(x: 0, y: 0, width: 312, height: 300),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        level = .statusBar
        isFloatingPanel = true
        hidesOnDeactivate = false
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        allowsToolTipsWhenApplicationIsInactive = true
        delegate = self

        let effect = NSVisualEffectView()
        effect.material = .popover
        effect.state = .active
        effect.wantsLayer = true
        effect.layer?.cornerRadius = 10
        effect.layer?.masksToBounds = true

        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.documentView = hosting
        scroll.translatesAutoresizingMaskIntoConstraints = false
        effect.addSubview(scroll)
        effect.addSubview(sheetHost)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: effect.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: effect.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: effect.topAnchor),
            scroll.bottomAnchor.constraint(equalTo: effect.bottomAnchor),
        ])
        contentView = effect
    }

    override var canBecomeKey: Bool { true }

    var isShown: Bool { isVisible }
    var hasSheet: Bool { attachedSheet != nil }
    var contentHeight: CGFloat { hosting.fittingSize.height }
    var contentWidth: CGFloat { max(hosting.fittingSize.width, 1) }

    func place(frame: NSRect, scrolls: Bool) {
        hosting.frame = NSRect(x: 0, y: 0, width: frame.width, height: max(frame.height, contentHeight))
        setFrame(frame, display: true)
        scroll.verticalScrollElasticity = scrolls ? .automatic : .none
    }

    func present() {
        makeKeyAndOrderFront(nil)
    }

    func dismiss() { orderOut(nil) }

    func windowDidResignKey(_ notification: Notification) { onResignKey() }
}
