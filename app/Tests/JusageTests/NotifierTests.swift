import XCTest
import UserNotifications
@testable import Jusage

/// S14: permission at launch, posts S8's notes under a stable id so a repeat replaces (R-90, R-93).
final class NotifierTests: XCTestCase {
    final class FakeCenter: NotificationPosting {
        var grant = true
        var grantError: String?
        var postError: String?
        private(set) var asked = 0
        private(set) var posted: [(id: String, title: String, body: String)] = []
        func requestAuthorization(_ done: @escaping (Bool, String?) -> Void) { asked += 1; done(grant, grantError) }
        func post(id: String, title: String, body: String, _ done: @escaping (String?) -> Void) {
            posted.append((id, title, body)); done(postError)
        }
    }

    func note(_ kind: NotifyKind = .warn80, folder: String = "/h/.claude", meter: String = "session") -> Note {
        Note(id: "\(folder)-\(meter)-\(kind.rawValue)", kind: kind, folderKey: folder, meterId: meter,
             title: "Session is at 81%", body: "x@y: resets 2:05 PM.")
    }

    func testIdStable() {
        let c = FakeCenter()
        let n = Notifier(center: c, log: RecordingLog())
        n.post([note()])
        n.post([note()])
        XCTAssertEqual(c.posted.map(\.id), ["/h/.claude-session-warn80", "/h/.claude-session-warn80"],
                       "the same account + meter + kind posts under one id, so the banner is replaced")
        n.post([note(.critical)])
        XCTAssertEqual(c.posted.last?.id, "/h/.claude-session-critical")
        XCTAssertEqual(c.posted.last?.title, "Session is at 81%")
    }

    func testPermissionAskedOnceAtLaunch() {
        let c = FakeCenter()
        let n = Notifier(center: c, log: RecordingLog())
        n.requestPermission()
        XCTAssertEqual(c.asked, 1)
    }

    func testDeniedIsLogged() {
        let c = FakeCenter(); c.grant = false
        let log = RecordingLog()
        Notifier(center: c, log: log).requestPermission()
        XCTAssertEqual(log.warns, ["[notify] notifications are off for jusage in System Settings; meters still colour, nothing will pop up"])
    }

    func testPermissionErrorIsLogged() {
        let c = FakeCenter(); c.grant = false; c.grantError = "not allowed"
        let log = RecordingLog()
        Notifier(center: c, log: log).requestPermission()
        XCTAssertEqual(log.errors, ["[notify] permission request failed: not allowed"])
    }

    func testPostFailureIsLogged() {
        let c = FakeCenter(); c.postError = "denied"
        let log = RecordingLog()
        Notifier(center: c, log: log).post([note()])
        XCTAssertEqual(log.errors, ["[notify] could not post /h/.claude-session-warn80: denied"])
    }

    func testPostReportsOnlyTheIdsActuallyDelivered() {
        let ok = FakeCenter()
        var got: [String]?
        Notifier(center: ok, log: RecordingLog()).post([note(), note(.critical)]) { got = $0 }
        XCTAssertEqual(Set(got ?? []), ["/h/.claude-session-warn80", "/h/.claude-session-critical"])
        let bad = FakeCenter(); bad.postError = "denied"
        var none: [String]?
        Notifier(center: bad, log: RecordingLog()).post([note()]) { none = $0 }
        XCTAssertEqual(none, [])
    }

    func testAlertsShowWhileJusageIsTheActiveApp() {
        // willPresent must ask for a banner and sound, or macOS drops the alert while jusage is frontmost.
        XCTAssertEqual(NotificationPresenter.options, [.banner, .sound])
        var asked: UNNotificationPresentationOptions?
        NotificationPresenter.present { asked = $0 }
        XCTAssertEqual(asked, [.banner, .sound])
    }
}
