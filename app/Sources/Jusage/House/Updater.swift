import Combine
import Foundation
import Sparkle
import SwiftUI

// UPDATE CHECK: Sparkle 2, the standard window. App menu > Check for Updates…, one silent check per launch.
// Lifted from Beat Key (app/Sources/Orion/Updater.swift); The Kitchen does the same from ObjC (kshell.m).
// The feed, the public key and the daily automatic check are in Info.plist (README.md has the four keys):
//   SUFeedURL, SUPublicEDKey, SUEnableAutomaticChecks, SUScheduledCheckInterval
// Needs the Sparkle package (https://github.com/sparkle-project/Sparkle, 2.x) and Sparkle.framework in
// Contents/Frameworks, signed by the release script.

final class AppUpdater: ObservableObject {
    @Published private(set) var canCheck = false
    private let controller: SPUStandardUpdaterController?

    /// No updater when the process is not a bundled app that carries a feed (`swift run`, tests):
    /// Sparkle would only show an error there.
    init(bundle: Bundle = .main) {
        guard AppUpdater.isUpdatable(bundle: bundle) else {
            controller = nil
            AppLog.shared.info("update", "no updater: not a bundled app with SUFeedURL")
            return
        }
        let c = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
        controller = c
        c.updater.publisher(for: \.canCheckForUpdates).receive(on: DispatchQueue.main).assign(to: &$canCheck)
        AppLog.shared.info("update", "Sparkle, feed \(c.updater.feedURL?.absoluteString ?? "none"), automatic checks \(c.updater.automaticallyChecksForUpdates ? "on" : "off")")
    }

    static func isUpdatable(bundle: Bundle) -> Bool {
        bundle.bundleURL.pathExtension == "app" && bundle.object(forInfoDictionaryKey: "SUFeedURL") is String
    }

    private var checkedAtLaunch = false

    /// Once per launch: Sparkle's silent check (its own scheduled check waits out the 24 h interval, so an app
    /// opened an hour after the last check would not look). Shows a window only when a newer version exists;
    /// "up to date" and errors stay silent. Same updater as the menu item.
    func checkInBackgroundAtLaunch() {
        guard !checkedAtLaunch else { return }
        checkedAtLaunch = true
        guard let c = controller else {
            AppLog.shared.info("update", "launch check skipped (no updater)")
            return
        }
        AppLog.shared.info("update", "launch check (Sparkle, in the background)")
        c.updater.checkForUpdatesInBackground()
    }

    /// A user-initiated check: Sparkle shows "up to date", the update offer, or the error.
    func checkForUpdates() {
        DispatchQueue.main.async {
            guard let c = self.controller, c.updater.canCheckForUpdates else {
                AppLog.shared.warn("update", "Check for Updates pressed but the updater cannot check right now")
                return
            }
            AppLog.shared.info("update", "user asked to check for updates")
            c.checkForUpdates(nil)
        }
    }
}

/// The menu item; its own view so the disabled state follows the updater.
struct CheckForUpdatesButton: View {
    @ObservedObject var updater: AppUpdater
    var body: some View {
        Button("Check for Updates…") { updater.checkForUpdates() }
            .disabled(!updater.canCheck)
    }
}

/// App menu > Check for Updates… (after About). The app owns ONE AppUpdater and hands it in:
///   @StateObject private var updater = AppUpdater()
///   .commands { UpdateCommands(updater: updater) }      and   .onAppear { updater.checkInBackgroundAtLaunch() }
struct UpdateCommands: Commands {
    @ObservedObject var updater: AppUpdater
    var body: some Commands {
        CommandGroup(after: .appInfo) { CheckForUpdatesButton(updater: updater) }
    }
}
