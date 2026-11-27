import Foundation

/// S16: the app's arguments. `--smoke-test <file>` (house kit release check), `--set-python <path>` (the installer
/// records the Python it checked), `--check-for-updates` (Sparkle's window at once), `--login-item-off` (`jusage
/// uninstall` switches the login item off with no window, then the app exits). `--window` is dropped.
/// AppKit's own `-NS… <value>` pairs and LaunchServices' `-psn_…` pass untouched; anything else is a problem the
/// app logs.
struct LaunchArgs: Equatable {
    var smokeOutput: URL?
    var setPython: String?
    var checkForUpdates = false
    var loginItemOff = false
    var problems: [String] = []

    init(_ argv: [String]) {
        var i = 1
        while i < argv.count {
            let a = argv[i]
            func value() -> String? {
                guard i + 1 < argv.count, !argv[i + 1].hasPrefix("-") else { problems.append("\(a) needs a \(a == "--smoke-test" ? "file" : "path")"); return nil }
                i += 1
                return argv[i]
            }
            switch a {
            case "--smoke-test": smokeOutput = value().map { URL(fileURLWithPath: $0) }
            case "--set-python": setPython = value()
            case "--check-for-updates": checkForUpdates = true
            case "--login-item-off": loginItemOff = true
            default:
                if a.hasPrefix("-psn_") { break }
                if a.hasPrefix("-NS") || a.hasPrefix("-Apple") { i += 1; break }     // AppKit default overrides
                problems.append("unknown argument \(a)")
            }
            i += 1
        }
    }
}
