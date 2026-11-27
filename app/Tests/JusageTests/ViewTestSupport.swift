import XCTest
import SwiftUI
@testable import Jusage

/// Stand-ins for the house kit and the host, so the views are tested without them (W3-C pastes the real log,
/// W3-A wires the real engine runner).
final class ViewLogStub: HostLog {
    private(set) var lines: [String] = []
    func info(_ category: String, _ message: String) {}
    func warn(_ category: String, _ message: String) {}
    func error(_ category: String, _ message: String) { lines.append("\(category): \(message)") }
}

final class ViewEngineStub {
    private(set) var calls: [[String]] = []
    var reply: RunResult = .ok(Data())
    func run(_ args: [String]) async -> RunResult { calls.append(args); return reply }
}

enum ViewFixture {
    static let tz = TimeZone(secondsFromGMT: -8 * 3600)!
    static func time(_ now: Date = Fixture.now) -> DisplayTime { DisplayTime(now: now, hour12: false, timeZone: tz) }

    /// Bend the golden feed's accounts array.
    static func feed(accounts edit: @escaping (inout [[String: Any]]) -> Void) throws -> Feed {
        try Fixture.feed { j in
            var a = j["accounts"] as! [[String: Any]]
            edit(&a)
            j["accounts"] = a
        }
    }

    /// Bend one meter: account index, meter index.
    static func feed(account i: Int, meter m: Int, _ edit: @escaping (inout [String: Any]) -> Void) throws -> Feed {
        try feed { a in
            var meters = a[i]["meters"] as! [[String: Any]]
            edit(&meters[m])
            a[i]["meters"] = meters
        }
    }

    @MainActor
    static func services(log: HostLog = ViewLogStub(), engine: ViewEngineStub = ViewEngineStub(),
                         loginError: String? = nil) -> PanelServices {
        PanelServices(
            shortcuts: [:], log: log, refresh: {}, openDashboard: { _ in }, houseMenu: [],
            engine: { await engine.run($0) },
            loginItem: LoginItemControl(isOn: { false }, set: { _ in loginError }),
            accentPicker: { AnyView(EmptyView()) }, glow: { view, _ in view }, quit: {}, foldAnimationDone: {})
    }
}
