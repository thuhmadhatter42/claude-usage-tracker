import AppKit

// S16: the entry. `--smoke-test <file>` checks the built app with no window and exits; otherwise jusage runs as a
// menu bar accessory (no Dock icon, no app menu: the house items live on the status item, R-169).
let launchArgs = LaunchArgs(CommandLine.arguments)

if CommandLine.arguments.contains("--smoke-test") {
    guard let out = launchArgs.smokeOutput else {
        FileHandle.standardError.write(Data("jusage: --smoke-test needs a file\n".utf8))
        exit(2)
    }
    exit(MainActor.assumeIsolated { Smoke.run(output: out) })
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let delegate = AppDelegate(args: launchArgs)
    app.delegate = delegate
    withExtendedLifetime(delegate) { app.run() }
}
