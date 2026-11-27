import XCTest
@testable import Jusage

final class FeedRunnerTests: XCTestCase {
    var dir: URL!
    var python: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("jusage-runner-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        // A stand-in for `python -B -E -s <script> ...` (RULINGS 51: no .pyc inside the sealed app): it refuses
        // any other flags (exit 9), drops the three and runs the script with sh.
        python = dir.appendingPathComponent("fake-python")
        try "#!/bin/sh\n[ \"$1 $2 $3\" = \"-B -E -s\" ] || { echo \"flags: $1 $2 $3\" >&2; exit 9; }\nshift 3\ns=\"$1\"; shift\nexec /bin/sh \"$s\" \"$@\"\n".write(to: python, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: python.path)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    private func script(_ body: String) throws -> URL {
        let u = dir.appendingPathComponent("child-\(UUID().uuidString).sh")
        try body.write(to: u, atomically: true, encoding: .utf8)
        return u
    }

    func testLargeOutputNoDeadlock() async throws {
        // 200 KB on stdout and 200 KB on stderr at the same time: a reader that waits on one pipe would hang.
        let s = try script("""
        head -c 200000 /dev/zero | tr '\\0' 'e' >&2 &
        head -c 200000 /dev/zero | tr '\\0' 'x'
        wait
        """)
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 20)
        guard case .ok(let data) = r else { return XCTFail("\(r)") }
        XCTAssertEqual(data.count, 200_000)
    }

    func testPassesStateDirAndArgsInOrder() async throws {
        let s = try script("printf '%s|' \"$@\"")
        let r = await FeedRunner.run(python: python, script: s, args: ["menu-json", "--force"], stateDir: "/tmp/state", timeout: 20)
        XCTAssertEqual(r, .ok(Data("--state-dir|/tmp/state|menu-json|--force|".utf8)))
    }

    func testEnvironmentIsHomeAndLangOnly() async throws {
        let s = try script("env | sort")
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 20)
        guard case .ok(let data) = r else { return XCTFail("\(r)") }
        let names = String(decoding: data, as: UTF8.self).split(separator: "\n").compactMap { $0.split(separator: "=").first.map(String.init) }
        // sh itself adds PWD, SHLVL and _; nothing of ours beyond HOME and LANG.
        XCTAssertTrue(names.contains("HOME") && names.contains("LANG"))
        XCTAssertFalse(names.contains("PATH"))
        XCTAssertFalse(names.contains("USER"))
    }

    func testTimeoutKills() async throws {
        let s = try script("exec sleep 30")
        let t0 = Date()
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 0.5, killGrace: 0.5)
        XCTAssertEqual(r, .timedOut)
        XCTAssertLessThan(Date().timeIntervalSince(t0), 10)
    }

    func testTimeoutEscalatesToKillWhenSigtermIgnored() async throws {
        let s = try script("trap '' TERM\nwhile true; do sleep 1; done")
        let t0 = Date()
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 0.5, killGrace: 0.5)
        XCTAssertEqual(r, .timedOut)
        XCTAssertLessThan(Date().timeIntervalSince(t0), 15)
    }

    func testCrashNamesSignal() async throws {
        let s = try script("kill -SEGV $$")
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 20)
        XCTAssertEqual(r, .crashed(signal: SIGSEGV))
    }

    func testFailureGivesLastStderrLine() async throws {
        let s = try script("echo noise >&2\necho 'Engine refused: VERSION missing' >&2\nexit 2")
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 20)
        XCTAssertEqual(r, .failed(line: "Engine refused: VERSION missing", exit: 2))
    }

    func testFailureWithNoStderrStillSaysSomething() async throws {
        let s = try script("exit 1")
        let r = await FeedRunner.run(python: python, script: s, args: [], stateDir: nil, timeout: 20)
        XCTAssertEqual(r, .failed(line: "The engine exited with code 1.", exit: 1))
    }

    func testLaunchFailure() async {
        let r = await FeedRunner.run(python: URL(fileURLWithPath: "/nonexistent/python"), script: dir, args: [], stateDir: nil)
        guard case .launchFailed(let why) = r else { return XCTFail("\(r)") }
        XCTAssertFalse(why.isEmpty)
    }

    func testTimeoutTextOnlyForTimeout() {
        XCTAssertEqual(FeedRunner.timeoutText, "Refresh took over 90 s and was stopped. Is a Keychain prompt waiting?")
    }
}
