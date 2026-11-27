import ServiceManagement

/// S15: Open at login through the system login-item service (R-85). A failure comes back as the words shown under
/// the checkbox, never a checkbox that silently flips back (N21), and is logged.
protocol LoginService {
    var status: SMAppService.Status { get }
    func register() throws
    func unregister() throws
}

extension SMAppService: LoginService {}

struct LoginItem {
    let service: LoginService
    let log: HostLog

    init(service: LoginService = SMAppService.mainApp, log: HostLog) {
        self.service = service; self.log = log
    }

    var isOn: Bool { service.status == .enabled }

    /// nil when it worked; otherwise the reason, in words.
    func set(_ on: Bool) -> String? {
        do {
            if on { try service.register() } else { try service.unregister() }
        } catch {
            let why = "Open at login could not be turned \(on ? "on" : "off"): \(error.localizedDescription)"
            log.error("login-item", why)
            return why
        }
        if on && service.status == .requiresApproval {
            let why = "Allow jusage in System Settings → General → Login Items to open it at login."
            log.warn("login-item", "registered but waiting for approval in System Settings")
            return why
        }
        log.info("login-item", on ? "on" : "off")
        return nil
    }

    struct Outcome: Equatable {
        let code: Int32
        let line: String
    }

    /// `Jusage --login-item-off`: what `jusage uninstall` runs, since only the app itself can unregister its login
    /// item (RULING 39). Not registered is not an error.
    func switchOff() -> Outcome {
        guard service.status == .enabled || service.status == .requiresApproval else {
            return Outcome(code: 0, line: "Open at login was already off.")
        }
        if let why = set(false) { return Outcome(code: 1, line: why) }
        return Outcome(code: 0, line: "Open at login is off.")
    }
}
