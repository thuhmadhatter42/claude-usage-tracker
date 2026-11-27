import AppKit
import SwiftUI

// VERSION + ABOUT. One VERSION file per project; the build writes it into Info.plist; the app shows it in two
// places: the About panel and a small label on screen. Nothing else carries a version string.

enum HouseAbout {
    /// The build number beside the version (CFBundleVersion: the commit count, set by the build). "" when absent.
    static func build(in bundle: Bundle = .main) -> String {
        (bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? ""
    }

    /// What the About panel shows.
    static func options(bundle: Bundle = .main) -> [NSApplication.AboutPanelOptionKey: Any] {
        let credits = NSAttributedString(string: House.maker, attributes: [
            .font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor,
        ])
        return [
            .applicationName: House.appName,
            .applicationVersion: House.version(in: bundle),
            .version: build(in: bundle),
            .credits: credits,
        ]
    }

    @MainActor static func show() {
        NSApp.orderFrontStandardAboutPanel(options: options())
        NSApp.activate(ignoringOtherApps: true)
        AppLog.shared.info("about", "\(House.appName) \(House.version)")
    }
}

/// App menu > About <App>.
struct AboutCommands: Commands {
    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            Button("About \(House.appName)") { HouseAbout.show() }
        }
    }
}

/// The version on screen: small, dim, in a corner or a footer. A Report and a screenshot then always say which build.
struct VersionLabel: View {
    var body: some View {
        Text("v\(House.version)")
            .font(.system(size: 10, weight: .medium, design: .monospaced))
            .foregroundColor(.secondary)
            .textSelection(.enabled)
            .accessibilityLabel("Version \(House.version)")
    }
}
