import Foundation

/// What the host needs from the house-kit log (`AppLog`, H1): one line per failure, so Help → Report attaches it
/// and `jusage doctor` prints it. Host types take this protocol so their tests can read what was logged; the app
/// passes `AppLog.shared`.
protocol HostLog: AnyObject {
    func info(_ category: String, _ message: String)
    func warn(_ category: String, _ message: String)
    func error(_ category: String, _ message: String)
}

extension AppLog: HostLog {}
