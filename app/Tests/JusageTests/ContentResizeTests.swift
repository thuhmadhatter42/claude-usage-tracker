import Combine
import XCTest
@testable import Jusage

/// The panel is re-placed whenever its content may have changed height (Today / Month / Settings, a new feed, a
/// refusal line), not only on show, on screen change and after a fold animation.
@MainActor
final class ContentResizeTests: XCTestCase {
    func testAChangeReplacesThePanelOnTheNextTurnOfTheRunLoop() {
        let changes = PassthroughSubject<Void, Never>()
        var replaced = 0
        let watcher = ContentResizeWatcher(changes: changes.eraseToAnyPublisher(), replace: { replaced += 1 })
        changes.send()
        XCTAssertEqual(replaced, 0, "the new height is only laid out after the change lands")
        let e = expectation(description: "replaced")
        DispatchQueue.main.async { DispatchQueue.main.async { e.fulfill() } }
        wait(for: [e], timeout: 2)
        XCTAssertEqual(replaced, 1)
        _ = watcher
    }

    func testBurstsCoalesceIntoOneReplace() {
        let changes = PassthroughSubject<Void, Never>()
        var replaced = 0
        let watcher = ContentResizeWatcher(changes: changes.eraseToAnyPublisher(), replace: { replaced += 1 })
        for _ in 0..<5 { changes.send() }
        let e = expectation(description: "settled")
        DispatchQueue.main.async { DispatchQueue.main.async { e.fulfill() } }
        wait(for: [e], timeout: 2)
        XCTAssertEqual(replaced, 1)
        _ = watcher
    }
}
