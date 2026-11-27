import AppKit
import SwiftUI

// HOW THE HOST USES THE HOUSE KIT. jusage has no Dock icon and no menu bar of its own, so the SOP's SwiftUI
// `.commands` blocks have nothing to attach to. The AppKit path of the SOP is used instead:
//   - `JusageHouse.sheetHost()`: ONE invisible SwiftUI view, added to the panel, that owns the kit's sheets
//     (Quick Keys, Report, Uninstall, Doctor).
//   - `JusageHouseMenu.items(...)`: the status item's right-click menu (and the panel's "…" menu), whose items
//     open those sheets through the kit's centers.
//   - `JusageHouse.launch(...)`: once at launch, in this order: log line, tips check, Doctor checks, uninstall prep.
// Everything else (the table, the sheets, the updater) is the kit's own code, unchanged.

enum JusageHouse {
    /// Zero-size, invisible, in the panel's view tree: the sheets present from here. The accent environment is
    /// set at its root, so every sheet picks up the user's colour.
    @MainActor static func sheetHost() -> NSView {
        let root = AnyView(Color.clear.frame(width: 0, height: 0)
            .accentRoot().quickKeysSheet().reportSheet().uninstallSheet().doctorSheet())
        let host = NSHostingView(rootView: root)
        host.frame = .zero
        return host
    }

    /// `engineDoctor`: runs the engine's `doctor` and returns its verdict lines (`JusageDoctor.engineRunner`).
    /// `statusLineOff`: runs `statusline off --all` and returns (exit, output).
    @MainActor static func launch(engineDoctor: @escaping () throws -> [String],
                                  statusLineOff: @escaping () -> (Int32, String),
                                  loginItemOff: @escaping () -> Void = JusageUninstall.unregisterLoginItem,
                                  home: URL = URL(fileURLWithPath: NSHomeDirectory()),
                                  bundle: Bundle = .main) {
        AppLog.shared.launchLine()
        HouseTips.checkAtLaunch()
        JusageDoctor.install(engine: engineDoctor, bundle: bundle)
        JusageUninstall.install(statusLineOff: statusLineOff, loginItemOff: loginItemOff, home: home)
    }
}

/// A menu item that runs a closure. NSMenuItem wants a target and a selector; this is both.
@MainActor
final class HouseMenuItem: NSMenuItem {
    private let handler: @MainActor () -> Void

    init(_ title: String, id: String? = nil, _ handler: @escaping @MainActor () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
        if let id { identifier = NSUserInterfaceItemIdentifier("qk.\(id)") }
    }

    required init(coder: NSCoder) { fatalError("not from a nib") }

    @objc private func fire() { handler() }
}

enum JusageHouseMenu {
    /// The status item's right-click menu, in the SPEC's order. `quit` is the host's (it owns shutdown).
    /// After building (and after every Quick Keys change) the host calls `QuickKeys.shared.apply(to: menu)` so
    /// the Quick Keys item shows its chord.
    /// `presentSheet` runs its argument once a window that can show the kit's sheets is up: the host shows the
    /// panel (the sheets hang off it) and activates the app, then the item opens its sheet.
    @MainActor static func items(updater: AppUpdater, quit: @escaping @MainActor () -> Void,
                                 presentSheet: @escaping @MainActor (@escaping @MainActor () -> Void) -> Void = { $0() }) -> [NSMenuItem] {
        [
            HouseMenuItem("About \(House.appName)") { HouseAbout.show() },
            HouseMenuItem("Check for Updates…") { updater.checkForUpdates() },
            .separator(),
            HouseMenuItem("Quick Keys", id: QuickKeys.quickKeysID) { presentSheet { QuickKeys.shared.open() } },
            HouseMenuItem("Send Report…") { presentSheet { ReportCenter.shared.open() } },
            .separator(),
            HouseMenuItem("\(House.appName) Manual") { HouseHelp.open(HouseHelp.manual) },
            HouseMenuItem("What's New") { HouseHelp.open(HouseHelp.whatsNew) },
            HouseMenuItem("Run Doctor") { presentSheet { DoctorCenter.shared.run() } },
            HouseMenuItem("Licenses") { HouseHelp.open(HouseHelp.licenses) },
            .separator(),
            HouseMenuItem("Uninstall \(House.appName)…") { presentSheet { UninstallCenter.shared.open() } },
            .separator(),
            HouseMenuItem("Quit \(House.appName)") { quit() },
        ]
    }
}
