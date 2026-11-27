import Foundation

/// S6: runs the engine once and reports how it ended (R-144 use, R-151). Drains stdout and stderr concurrently
/// (a child blocked on a full 64 KB pipe never exits), stops a run at 90 s (SIGTERM, then SIGKILL 5 s later), and
/// names a crash by its signal. The environment is `HOME` and `LANG` only.
enum RunResult: Equatable {
    case ok(Data)
    case failed(line: String, exit: Int32)
    case timedOut
    case crashed(signal: Int32)
    case launchFailed(String)
}

enum FeedRunner {
    static let timeoutText = "Refresh took over 90 s and was stopped. Is a Keychain prompt waiting?"

    /// `python -B -E -s <script> [--state-dir D] <args>` (-B: no .pyc inside the sealed app, RULINGS 51). `timeout` and `killGrace` are 90 s and 5 s in the app;
    /// tests shorten them.
    static func run(python: URL, script: URL, args: [String], stateDir: String?,
                    timeout: TimeInterval = 90, killGrace: TimeInterval = 5) async -> RunResult {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                cont.resume(returning: runBlocking(python: python, script: script, args: args, stateDir: stateDir,
                                                   timeout: timeout, killGrace: killGrace))
            }
        }
    }

    private final class Buffer: @unchecked Sendable {
        private let lock = NSLock()
        private var data = Data()
        func append(_ d: Data) { lock.lock(); data.append(d); lock.unlock() }
        func snapshot() -> Data { lock.lock(); defer { lock.unlock() }; return data }
    }

    private static func runBlocking(python: URL, script: URL, args: [String], stateDir: String?,
                                    timeout: TimeInterval, killGrace: TimeInterval) -> RunResult {
        let p = Process()
        p.executableURL = python
        p.arguments = ["-B", "-E", "-s", script.path] + (stateDir.map { ["--state-dir", $0] } ?? []) + args
        var env = ["LANG": "en_US.UTF-8"]
        env["HOME"] = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        p.environment = env
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        p.standardInput = FileHandle.nullDevice

        let done = DispatchSemaphore(value: 0)
        p.terminationHandler = { _ in done.signal() }
        do { try p.run() } catch { return .launchFailed(error.localizedDescription) }

        // Both pipes are read at once on their own threads.
        let outBuf = Buffer(), errBuf = Buffer()
        let readers = DispatchGroup()
        func drain(_ pipe: Pipe, into buf: Buffer) {
            readers.enter()
            Thread.detachNewThread {
                let h = pipe.fileHandleForReading
                while true {
                    let chunk = h.availableData
                    if chunk.isEmpty { break }
                    buf.append(chunk)
                }
                readers.leave()
            }
        }
        drain(out, into: outBuf)
        drain(err, into: errBuf)

        var timedOut = false
        if done.wait(timeout: .now() + timeout) == .timedOut {
            timedOut = true
            p.terminate()                                   // SIGTERM
            if done.wait(timeout: .now() + killGrace) == .timedOut {
                kill(p.processIdentifier, SIGKILL)
                done.wait()
            }
        }
        // A grandchild that kept a pipe open must not hold the app: take what arrived after a short grace.
        // The reader threads keep their pipes and end when the grandchild closes them.
        _ = readers.wait(timeout: .now() + 2)

        if timedOut { return .timedOut }
        if p.terminationReason == .uncaughtSignal { return .crashed(signal: p.terminationStatus) }
        if p.terminationStatus == 0 { return .ok(outBuf.snapshot()) }
        let line = String(decoding: errBuf.snapshot(), as: UTF8.self)
            .split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .last { !$0.isEmpty } ?? "The engine exited with code \(p.terminationStatus)."
        return .failed(line: line, exit: p.terminationStatus)
    }
}
