import AppKit
import XCTest
@testable import Jusage

/// S11: wake, session, screen and Space observers (R-149, R-150, B11).
final class ObserversTests: XCTestCase {
    func testEachNotificationRoutes() {
        let workspace = NotificationCenter(), app = NotificationCenter()
        var requests: [Trigger] = []
        var layouts = 0
        let o = Observers(workspace: workspace, app: app, request: { requests.append($0) }, layoutChanged: { layouts += 1 })
        o.start()

        workspace.post(name: NSWorkspace.didWakeNotification, object: nil)
        workspace.post(name: NSWorkspace.sessionDidBecomeActiveNotification, object: nil)
        XCTAssertEqual(requests, [.wake, .sessionActive])

        app.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        workspace.post(name: NSWorkspace.activeSpaceDidChangeNotification, object: nil)
        XCTAssertEqual(layouts, 2, "screens and Spaces both re-place the panel and check the status item")
        XCTAssertEqual(requests.count, 2, "a layout change never starts a run")

        o.stop()
        workspace.post(name: NSWorkspace.didWakeNotification, object: nil)
        app.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        XCTAssertEqual(requests.count, 2)
        XCTAssertEqual(layouts, 2)
    }

    func testStartTwiceDoesNotDoubleRoute() {
        let workspace = NotificationCenter(), app = NotificationCenter()
        var requests: [Trigger] = []
        let o = Observers(workspace: workspace, app: app, request: { requests.append($0) }, layoutChanged: {})
        o.start(); o.start()
        workspace.post(name: NSWorkspace.didWakeNotification, object: nil)
        XCTAssertEqual(requests, [.wake])
    }
}
