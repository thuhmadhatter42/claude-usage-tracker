import ServiceManagement
import XCTest
@testable import Jusage

/// S15: Open at login through SMAppService; a failure is said in words (R-85, N21).
final class LoginItemTests: XCTestCase {
    struct Boom: LocalizedError { var errorDescription: String? { "Operation not permitted" } }

    final class FakeService: LoginService {
        var status: SMAppService.Status = .notRegistered
        var registerError: Error?
        var unregisterError: Error?
        var statusAfterRegister: SMAppService.Status = .enabled
        func register() throws {
            if let registerError { throw registerError }
            status = statusAfterRegister
        }
        func unregister() throws {
            if let unregisterError { throw unregisterError }
            status = .notRegistered
        }
    }

    func testFailureReturnsReason() {
        let s = FakeService(); s.registerError = Boom()
        let log = RecordingLog()
        let item = LoginItem(service: s, log: log)
        XCTAssertEqual(item.set(true), "Open at login could not be turned on: Operation not permitted")
        XCTAssertFalse(item.isOn)
        XCTAssertEqual(log.errors.count, 1, "every failure is logged")
    }

    func testSuccessReturnsNil() {
        let s = FakeService()
        let item = LoginItem(service: s, log: RecordingLog())
        XCTAssertNil(item.set(true))
        XCTAssertTrue(item.isOn)
        XCTAssertNil(item.set(false))
        XCTAssertFalse(item.isOn)
    }

    func testNeedsApprovalIsExplained() {
        let s = FakeService(); s.statusAfterRegister = .requiresApproval
        let item = LoginItem(service: s, log: RecordingLog())
        XCTAssertEqual(item.set(true), "Allow jusage in System Settings → General → Login Items to open it at login.")
    }

    func testTurnOffFailureReturnsReason() {
        let s = FakeService(); s.status = .enabled; s.unregisterError = Boom()
        let item = LoginItem(service: s, log: RecordingLog())
        XCTAssertEqual(item.set(false), "Open at login could not be turned off: Operation not permitted")
    }
}

/// A HostLog that keeps every line (shared by the Host tests).
final class RecordingLog: HostLog {
    private(set) var infos: [String] = []
    private(set) var warns: [String] = []
    private(set) var errors: [String] = []
    func info(_ category: String, _ message: String) { infos.append("[\(category)] \(message)") }
    func warn(_ category: String, _ message: String) { warns.append("[\(category)] \(message)") }
    func error(_ category: String, _ message: String) { errors.append("[\(category)] \(message)") }
}
