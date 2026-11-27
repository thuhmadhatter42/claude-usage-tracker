import AppKit

/// S12: the status item (R-53…R-59, R-150, R-169, R-192 display).
///   - title: `TitleModel`'s items as one attributed string (monospaced digits); its width is set in the same call
///     (R-59, no second pass); hover = every account's lines
///   - left click → toggle the panel; right click → the house menu (`JusageHouseMenu.items`, RULINGS 41) with the
///     Quick Keys chords applied
///   - `ensureOnScreen()` recreates the item when its window is on no screen (the macOS 26/27 item-loss bug)
///   - a bring-up refusal arrives as `TitleModel`'s warning mark with the reason as hover

/// Where the panel goes: the item's frame and its own screen's visible frame, both in screen coordinates.
struct PanelAnchor: Equatable {
    let item: NSRect
    let visible: NSRect
}

/// One status item. The app's is `SystemStatusSlot`; tests use a fake (a real one would appear in the menu bar).
protocol StatusSlot: AnyObject {
    var isOnScreen: Bool { get }
    var anchor: PanelAnchor? { get }
    func show(title: NSAttributedString, length: CGFloat, tooltip: String)
    func popUp(_ menu: NSMenu)
    func remove()
}

protocol StatusSlotMaking {
    /// `click(true)` for a right click (or ⌃-click), `click(false)` for a left click.
    func make(click: @escaping (Bool) -> Void) -> StatusSlot
}

/// How the title looks. Menu bar colours are the system's (the menu bar follows the Mac's appearance, not the
/// panel theme).
enum TitleRender {
    static let grey = NSColor.tertiaryLabelColor
    static let plain = NSColor.labelColor
    static let tagColour = NSColor.secondaryLabelColor
    static let font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
    static let tagFont = NSFont.monospacedDigitSystemFont(ofSize: NSFont.smallSystemFontSize, weight: .medium)
    static let gap = "  "
    /// The button's own inset on each side of its title.
    static let padding: CGFloat = 8

    static func colour(_ s: PaceState) -> NSColor {
        switch s {
        case .green: return .systemGreen
        case .amber: return .systemOrange
        case .red: return .systemRed
        }
    }

    static func attributed(_ items: [TitleItem]) -> NSAttributedString {
        let out = NSMutableAttributedString()
        for (i, item) in items.enumerated() {
            if i > 0 { out.append(NSAttributedString(string: gap, attributes: [.font: font])) }
            if let tag = item.tag {
                out.append(NSAttributedString(string: tag + " ", attributes: [.font: tagFont, .foregroundColor: tagColour]))
            }
            let c = item.stale ? grey : (item.colour.map(colour) ?? plain)
            out.append(NSAttributedString(string: item.text, attributes: [.font: font, .foregroundColor: c]))
        }
        return out
    }

    static func length(of title: NSAttributedString) -> CGFloat { ceil(title.size().width) + 2 * padding }

    static func tooltip(_ items: [TitleItem]) -> String { items.map(\.hover).joined(separator: "\n\n") }
}

@MainActor
final class StatusItemController {
    private let maker: StatusSlotMaking
    private let menuItems: @MainActor () -> [NSMenuItem]
    private let leftClick: () -> Void
    private let log: HostLog
    private var slot: StatusSlot!
    private var last: (title: NSAttributedString, length: CGFloat, tooltip: String)?

    init(maker: StatusSlotMaking, menuItems: @escaping @MainActor () -> [NSMenuItem],
         leftClick: @escaping () -> Void, log: HostLog) {
        self.maker = maker; self.menuItems = menuItems; self.leftClick = leftClick; self.log = log
        slot = makeSlot()
    }

    var anchor: PanelAnchor? { slot.anchor }

    func render(_ items: [TitleItem]) {
        let title = TitleRender.attributed(items)
        let shown = (title, TitleRender.length(of: title), TitleRender.tooltip(items))
        last = shown
        slot.show(title: shown.0, length: shown.1, tooltip: shown.2)
    }

    /// True when the item was lost and has been recreated.
    @discardableResult
    func ensureOnScreen() -> Bool {
        guard !slot.isOnScreen else { return false }
        log.warn("status-item", "the status item was on no screen; recreated it")
        slot.remove()
        slot = makeSlot()
        if let last { slot.show(title: last.title, length: last.length, tooltip: last.tooltip) }
        return true
    }

    /// The right-click menu, built fresh each time so the Quick Keys chords are current.
    func makeMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false
        for item in menuItems() { menu.addItem(item) }
        QuickKeys.shared.apply(to: menu)
        return menu
    }

    /// The panel's "⋯" menu from the same list as the right-click menu (R-169): one source, same order, same
    /// actions. A separator becomes `dividerBefore` on the next item.
    static func panelMenu(from items: [NSMenuItem]) -> [PanelMenuItem] {
        var out: [PanelMenuItem] = []
        var divider = false
        for item in items {
            if item.isSeparatorItem { divider = !out.isEmpty; continue }
            // AppKit fills `identifier` with the action's name ("fire") when none is set; only a Quick Keys id ("qk.…")
            // is a real one. Titles are unique in the list.
            let qk = item.identifier?.rawValue ?? ""
            let id = qk.hasPrefix("qk.") ? qk : item.title
            out.append(PanelMenuItem(id: id, title: item.title, dividerBefore: divider) {
                _ = (item.target as AnyObject?)?.perform(item.action, with: item)
            })
            divider = false
        }
        return out
    }

    private func makeSlot() -> StatusSlot {
        maker.make { [weak self] right in
            guard let self else { return }
            MainActor.assumeIsolated {
                if right { self.slot.popUp(self.makeMenu()) } else { self.leftClick() }
            }
        }
    }
}

/// The real NSStatusItem.
final class SystemStatusSlot: NSObject, StatusSlot {
    private let item: NSStatusItem
    private let click: (Bool) -> Void

    init(click: @escaping (Bool) -> Void) {
        self.click = click
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()
        item.button?.target = self
        item.button?.action = #selector(clicked(_:))
        item.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
    }

    var isOnScreen: Bool { item.button?.window?.screen != nil }

    var anchor: PanelAnchor? {
        guard let button = item.button, let window = button.window, let screen = window.screen else { return nil }
        let frame = window.convertToScreen(button.convert(button.bounds, to: nil))
        return PanelAnchor(item: frame, visible: screen.visibleFrame)
    }

    func show(title: NSAttributedString, length: CGFloat, tooltip: String) {
        item.length = length
        item.button?.attributedTitle = title
        item.button?.toolTip = tooltip
    }

    func popUp(_ menu: NSMenu) {
        guard let button = item.button else { return }
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: button.bounds.height + 4), in: button)
    }

    func remove() { NSStatusBar.system.removeStatusItem(item) }

    @objc private func clicked(_ sender: Any?) {
        let e = NSApp.currentEvent
        let right = e?.type == .rightMouseUp || (e?.modifierFlags.contains(.control) ?? false)
        click(right)
    }
}

struct SystemStatusSlots: StatusSlotMaking {
    func make(click: @escaping (Bool) -> Void) -> StatusSlot { SystemStatusSlot(click: click) }
}
